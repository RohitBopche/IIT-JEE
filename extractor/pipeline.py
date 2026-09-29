"""Per-paper extraction: chunked page images -> Gemini -> merge -> answers -> figures -> validation."""
import json
import re
from pathlib import Path

import pymupdf

from . import prompts, validate
from .gemini import Gemini
from .papers import Paper, crop_box, has_text_layer, page_text, render_page

LLM_DPI = 150
FIG_DPI = 200
CHUNK = 3          # pages processed per call (+1 look-ahead page)
VERIFY_BELOW = 0.8  # re-extract questions scoring below this


class Extractor:
    def __init__(self, out_dir: Path, gemini: Gemini, verify=True):
        self.out = out_dir
        self.g = gemini
        self.verify = verify

    # ---------- caching ----------
    def _cache_path(self, paper, name):
        p = self.out / "cache" / paper.slug / f"{paper.sha[:12]}_{prompts.PROMPT_VERSION}_{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _cached_call(self, paper, name, parts, schema, system):
        p = self._cache_path(paper, name)
        if p.exists():
            return json.loads(p.read_text())
        data, finish, usage = self.g.generate_json(parts, schema, system)
        rec = {"model": self.g.model, "finish": finish, "usage": usage, "response": data}
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        return rec

    # ---------- main ----------
    def run(self, paper: Paper, log=print):
        doc = pymupdf.open(paper.path)
        n = len(doc)
        text_pdf = has_text_layer(doc)
        raw_qs, sol_answers, state = [], [], None
        for start in range(0, n, CHUNK):
            main = list(range(start, min(start + CHUNK, n)))
            look = start + CHUNK if start + CHUNK < n else None
            imgs = [render_page(doc, i, LLM_DPI) for i in main + ([look] if look is not None else [])]
            prompt = prompts.chunk_prompt(len(main), look is not None, state)
            rec = self._cached_call(paper, f"p{start + 1:03d}", [prompt] + imgs, prompts.chunk_schema(), prompts.SYSTEM)
            resp = rec["response"]
            if rec.get("finish") not in (None, "STOP"):
                log(f"    ! pages {start + 1}-{main[-1] + 1}: finish={rec.get('finish')}")
            pages = main + ([look] if look is not None else [])
            for q in resp.get("questions", []):
                img = min(max(q.get("image", 1), 1), len(main))
                q["page_index"] = pages[img - 1]
                q["page_indices"] = pages
                for b in q.get("diagram_boxes", []):
                    bi = b.get("image", img)
                    b["page_index"] = pages[bi - 1] if 1 <= bi <= len(pages) else q["page_index"]
                raw_qs.append(q)
            sol_answers += resp.get("solution_answers", [])
            state = resp.get("end_state") or state
            log(f"    pages {start + 1}-{main[-1] + 1}/{n}: {len(resp.get('questions', []))} q, "
                f"{len(resp.get('solution_answers', []))} sol-answers")

        return self.finish(paper, doc, raw_qs, sol_answers, self.g.model, log)

    def finish(self, paper, doc, raw_qs, sol_answers, source, log=print):
        """Shared post-processing for any extraction source (Gemini or manual reading)."""
        n = len(doc)
        text_pdf = has_text_layer(doc)
        questions = self._merge(raw_qs)
        self._apply_solution_answers(paper, questions, sol_answers)
        for q in questions:
            self._finalise(paper, doc, q, text_pdf)
        paper_issues = validate.check_numbering(questions)
        for q in questions:
            q["confidence"] = validate.score(q)

        if self.verify and self.g:
            self._verify(paper, doc, questions, text_pdf, log)

        questions.sort(key=lambda q: (list(validate.SUBJ_CODE).index(q["subject"]), validate.sec(q), q["q_no"]))
        result = {
            "paper": {"file": str(paper.path), "sha256": paper.sha, "exam": paper.exam, "date": paper.date,
                      "year": paper.year, "shift": paper.shift, "slug": paper.slug, "pages": n,
                      "text_layer": text_pdf, "model": source, "prompt_version": prompts.PROMPT_VERSION},
            "issues": paper_issues,
            "questions": questions,
        }
        (self.out / "json").mkdir(parents=True, exist_ok=True)
        (self.out / "json" / f"{paper.slug}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
        return result

    # ---------- steps ----------
    @staticmethod
    def _merge(raw_qs):
        best = {}
        for q in raw_qs:
            key = (q["subject"], validate.sec(q), q["q_no"])
            rank = ({"high": 2, "medium": 1, "low": 0}[q["confidence"]], len(q["question_text"]))
            if key not in best or rank > best[key][0]:
                best[key] = (rank, q)
        return [v[1] for v in best.values()]

    def _apply_solution_answers(self, paper, questions, sol_answers):
        idx = {(q["subject"], q["q_no"]): q for q in questions}
        to_resolve = []
        for sa in sol_answers:
            q = idx.get((sa["subject"], sa["q_no"]))
            if not q or q["answer"].strip() or not sa["answer"].strip():
                continue
            q["answer_source"] = "solution"
            a = sa["answer"].strip()
            if q["question_type"] == "Numerical" or re.fullmatch(r"\(?[1-4A-Da-d]\)?", a):
                q["answer"] = a
            else:
                q["answer_stated"] = a
                to_resolve.append(q)
        if not to_resolve:
            return
        items = [{"id": f"{q['subject']}-{q['q_no']}", "question": q["question_text"],
                  "options": {l: o for l, o in zip("ABCD", q["options"])}, "stated_final_answer": q["answer_stated"]}
                 for q in to_resolve]
        rec = self._cached_call(paper, "resolve", [json.dumps(items, ensure_ascii=False)],
                                prompts.resolve_schema(), prompts.RESOLVE_SYSTEM)
        got = {it["id"]: it["option"] for it in rec["response"].get("items", [])}
        for q in to_resolve:
            opt = got.get(f"{q['subject']}-{q['q_no']}", "")
            if opt:
                q["answer"] = opt
                q["answer_source"] = "solution (matched to option)"

    def _finalise(self, paper, doc, q, text_pdf):
        q.setdefault("answer_source", "printed with question" if q["answer"].strip() else "")
        q["model_confidence"] = q.pop("confidence", "medium")
        q["answer"] = validate.norm_answer(q["answer"], q["question_type"], len(q["options"]))
        code = validate.SUBJ_CODE[q["subject"]]
        q["q_id"] = f"{paper.slug}_{code}_{validate.sec(q)}{q['q_no']:02d}"
        # figures
        q["diagram_files"] = []
        fig_dir = self.out / "figures" / paper.slug
        for k, b in enumerate(q.get("diagram_boxes") or [], 1):
            if len(b.get("box_2d", [])) != 4:
                continue
            png = crop_box(doc, b["page_index"], b["box_2d"], FIG_DPI)
            if png:
                fig_dir.mkdir(parents=True, exist_ok=True)
                f = fig_dir / f"{code}_Q{q['q_no']:02d}_{k}.png"
                f.write_bytes(png)
                q["diagram_files"].append(str(f.relative_to(self.out)))
        # cross-check with PDF text layer
        q["text_recall"] = None
        if text_pdf:
            pt = "".join(page_text(doc, i) for i in q["page_indices"] if 0 <= i < len(doc))
            q["text_recall"] = validate.text_layer_recall(q["question_text"], pt)
        q["issues"] = validate.check_question(q)

    def _verify(self, paper, doc, questions, text_pdf, log):
        weak = [q for q in questions if q["confidence"] < VERIFY_BELOW]
        if weak:
            log(f"    verifying {len(weak)} low-confidence question(s)")
        for q in weak:
            p0 = q["page_index"]
            pages = [p for p in (p0, p0 + 1) if p < len(doc)]
            imgs = [render_page(doc, i, LLM_DPI) for i in pages]
            rec = self._cached_call(paper, f"verify_{validate.SUBJ_CODE[q['subject']]}{validate.sec(q)}{q['q_no']:02d}",
                                    [prompts.verify_prompt(q["subject"], q["q_no"], len(imgs))] + imgs,
                                    prompts.chunk_schema(), prompts.SYSTEM)
            alts = rec["response"].get("questions") or []
            if not alts:
                q["issues"].append("verification disagrees: question not found on re-read")
                q["confidence"] = validate.score(q)
                continue
            alt = alts[0]
            alt["page_index"] = p0
            alt["page_indices"] = pages
            for b in alt.get("diagram_boxes", []):
                bi = b.get("image", 1)
                b["page_index"] = pages[bi - 1] if 1 <= bi <= len(pages) else p0
            if not alt.get("answer", "").strip() and q["answer"]:
                alt["answer"] = q["answer"]
                alt["answer_source"] = q.get("answer_source", "")
            self._finalise(paper, doc, alt, text_pdf)
            sim = validate.similarity(q["question_text"], alt["question_text"])
            opt_same = [o.strip() for o in q["options"]] == [o.strip() for o in alt["options"]]
            same = sim > 0.9 and opt_same and (q["answer"] == alt["answer"] or not alt["answer"])
            numbering = [i for i in q["issues"] if i.startswith("numbering")]
            if same:
                q["verified"] = True
                q["issues"] = [i for i in q["issues"] if not i.startswith(("model reported low", "model note"))]
            elif len(validate.check_question(alt)) < len([i for i in q["issues"] if not i.startswith("numbering")]):
                alt["issues"] = validate.check_question(alt) + numbering
                alt["issues"].append(f"verification disagrees with first read (text similarity {sim:.2f}); second read kept")
                keep = {k: q[k] for k in ("q_id",)}
                q.clear()
                q.update(alt, **keep)
            else:
                q["issues"].append(f"verification disagrees (text similarity {sim:.2f}, options same={opt_same})")
            q["confidence"] = validate.score(q)
