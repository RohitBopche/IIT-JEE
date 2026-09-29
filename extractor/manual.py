"""Manual (human/Claude) reading workflow.

  python -m extractor.manual render <slug> [--dpi 130]   page PNGs with a 0-1000 coordinate ruler -> output/pages/<slug>/ (not committed)
  python -m extractor.manual ingest [<slug> ...]          raw readings output/raw/<slug>/*.yaml -> output/json/<slug>.json -> Excel
  python -m extractor.manual status                       which papers are done

Raw YAML format (one list of questions per file; files are read in name order):

- n: 1                  # question number as printed
  s: P                  # subject  P / C / M
  sec: A                # section as printed ('' if none)
  p: 1                  # PDF page (1-based) where the question starts
  t: S                  # S = MCQ-Single, M = MCQ-Multiple, N = Numerical
  st: Match             # optional style: Match, AR, Stmt, MultiStmt, Graph, Fill, Comp  (default Standard)
  q: |-
    Question text, maths in $LaTeX$
  o: ['$a$', '$b$', '$c$', '$d$']     # omit for numerical
  a: D                  # answer: option letter(s) A-D, numeric value, or Bonus ('' if not printed)
  as: key               # optional answer source: key (printed with question, default) / sol (from solution pages)
  ch: Rotational Motion # chapter (close spelling is matched to syllabus.yaml)
  tp: Moment of inertia # short topic
  df: M                 # difficulty E / M / H
  fig: [[1, 70, 574, 300, 810]]   # optional diagrams: [page, ymin, xmin, ymax, xmax] on the 0-1000 ruler
  c: h                  # optional reading confidence h / m / l (default h)
  nt: ''                # optional note on anything unclear
"""
import argparse
import difflib
import json
import sys
from pathlib import Path

import pymupdf
import yaml

from . import excel
from .papers import discover
from .pipeline import Extractor
from .prompts import SYLLABUS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
RAW = OUT / "raw"
PAGES = OUT / "pages"
SOURCE = "claude (manual page reading)"

SUBJ = {"P": "Physics", "C": "Chemistry", "M": "Mathematics"}
TYPES = {"S": "MCQ-Single", "M": "MCQ-Multiple", "N": "Numerical"}
STYLES = {"Standard": "Standard", "Match": "Match the Following", "AR": "Assertion-Reason", "Stmt": "Statement I/II",
          "MultiStmt": "Multiple Statements", "Graph": "Graph/Diagram Based", "Fill": "Fill in the Blank",
          "Comp": "Comprehension"}
DIFF = {"E": "Easy", "M": "Medium", "H": "Hard"}
CONF = {"h": "high", "m": "medium", "l": "low"}
ANS_SRC = {"key": "printed with question", "sol": "solution", "keypage": "answer key page"}


def papers_by_slug():
    return {p.slug: p for p in discover(ROOT)}


def render(slug, dpi, first=None, last=None):
    p = papers_by_slug()[slug]
    doc = pymupdf.open(p.path)
    d = PAGES / slug
    d.mkdir(parents=True, exist_ok=True)
    for i in range((first or 1) - 1, min(last or len(doc), len(doc))):
        page = doc[i]
        r = page.rect
        blue = (0.2, 0.5, 1.0)
        for k in range(0, 1001, 50):
            x = r.x0 + r.width * k / 1000
            y = r.y0 + r.height * k / 1000
            major = k % 100 == 0
            ln = 9 if major else 5
            page.draw_line((x, r.y0), (x, r.y0 + ln), color=blue, width=0.6)
            page.draw_line((x, r.y1), (x, r.y1 - ln), color=blue, width=0.6)
            page.draw_line((r.x0, y), (r.x0 + ln, y), color=blue, width=0.6)
            page.draw_line((r.x1, y), (r.x1 - ln, y), color=blue, width=0.6)
            if major and 0 < k < 1000:
                page.insert_text((x - 6, r.y0 + 16), str(k // 100), fontsize=6, color=blue)
                page.insert_text((r.x0 + 10, y + 2), str(k // 100), fontsize=6, color=blue)
        page.insert_text((r.x1 - 60, r.y1 - 12), f"page {i + 1}/{len(doc)}", fontsize=8, color=(1, 0, 0))
        page.get_pixmap(dpi=dpi).save(d / f"p{i + 1:03d}.png")
    print(f"{p.path.name}: {len(doc)} pages -> {d.relative_to(ROOT)}  (text layer: "
          f"{'yes' if sum(len(doc[i].get_text()) for i in range(min(4, len(doc)))) > 1200 else 'no'})")


def _chapter(subject, ch):
    options = SYLLABUS[subject]
    if ch in options:
        return ch
    m = difflib.get_close_matches(ch, options, n=1, cutoff=0.5)
    if m:
        return m[0]
    low = ch.lower()
    for o in options:
        if low in o.lower():
            return o
    return ch


def to_raw(item, fname):
    subj = SUBJ[item["s"]]
    page = int(item["p"])
    qtype = TYPES[item.get("t", "S")]
    opts = [str(o) for o in (item.get("o") or [])]
    ans = item.get("a", "")
    ans = "" if ans is None else str(ans)
    q = {
        "subject": subj, "section": str(item.get("sec", "")), "q_no": int(item["n"]),
        "question_text": str(item["q"]).strip(), "options": opts, "question_type": qtype,
        "question_style": STYLES.get(item.get("st", "Standard"), item.get("st", "Standard")),
        "answer": ans, "has_diagram": bool(item.get("fig")) or bool(item.get("dg")),
        "chapter": _chapter(subj, str(item.get("ch", ""))), "topic": str(item.get("tp", "")),
        "difficulty": DIFF.get(item.get("df", "M"), "Medium"),
        "confidence": CONF.get(item.get("c", "h"), "high"), "notes": str(item.get("nt", "") or ""),
        "page_index": page - 1, "page_indices": [page - 1, page],
        "diagram_boxes": [{"page_index": int(f[0]) - 1, "box_2d": [int(v) for v in f[1:5]]} for f in item.get("fig") or []],
    }
    if item.get("as"):
        q["answer_source"] = ANS_SRC.get(item["as"], item["as"])
    return q


def ingest(slugs):
    byslug = papers_by_slug()
    ex = Extractor(OUT, gemini=None, verify=False)
    targets = slugs or sorted(d.name for d in RAW.iterdir() if d.is_dir())
    ok = True
    for slug in targets:
        paper = byslug[slug]
        files = sorted((RAW / slug).glob("*.yaml"))
        raw_qs = []
        # answers.yaml: {"P A 1": "D", ...} for papers whose answers are only in separate solution pages
        ans_file = RAW / slug / "answers.yaml"
        sol_answers = yaml.safe_load(ans_file.read_text()) if ans_file.exists() else {}
        ans_src = sol_answers.pop("_source", "sol")
        files = [f for f in files if f.name != "answers.yaml"]
        for f in files:
            try:
                items = yaml.safe_load(f.read_text()) or []
            except yaml.YAMLError as e:
                print(f"[{slug}] YAML error in {f.name}: {e}")
                ok = False
                continue
            for it in items:
                try:
                    key = f"{it['s']} {it.get('sec', '')} {it['n']}"
                    if not str(it.get("a", "") or "").strip() and key in sol_answers:
                        it["a"], it["as"] = sol_answers[key], ans_src
                    raw_qs.append(to_raw(it, f.name))
                except Exception as e:
                    print(f"[{slug}] bad item in {f.name}: {e}: {str(it)[:120]}")
                    ok = False
        seen = {}
        for q in raw_qs:
            k = (q["subject"], q["section"], q["q_no"])
            if k in seen:
                print(f"[{slug}] duplicate {k}")
            seen[k] = 1
        doc = pymupdf.open(paper.path)
        res = ex.finish(paper, doc, raw_qs, [], SOURCE)
        qs = res["questions"]
        low = [q for q in qs if q["confidence"] < excel.REVIEW_BELOW]
        print(f"[{slug}] {len(qs)} questions | review {len(low)} | paper issues: {res['issues'] or 'none'}")
        for q in low:
            print(f"    {q['q_id']} {q['confidence']}: {'; '.join(q['issues'])}")
    n, review = excel.build(OUT, OUT / "JEE_Questions.xlsx")
    print(f"Excel: {n} questions, {review} need review")
    return ok


def status():
    lines = ["# Extraction progress", "", "| Paper | Status | Questions | Needs review | Paper issues |", "|---|---|---|---|---|"]
    for slug, p in sorted(papers_by_slug().items()):
        j = OUT / "json" / f"{slug}.json"
        d = json.loads(j.read_text()) if j.exists() else None
        qs = d["questions"] if d else []
        review = sum(q["confidence"] < excel.REVIEW_BELOW for q in qs)
        state = "done" if d and not d["issues"] else ("partial" if d else "todo")
        lines.append(f"| {p.path.name} | {state} | {len(qs)} | {review} | {'; '.join(d['issues']) if d else ''} |")
        print(f"{slug:22s} {state:8s} {len(qs):3d}  {p.path.name}")
    (OUT / "PROGRESS.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["render", "ingest", "status"])
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--dpi", type=int, default=130)
    ap.add_argument("--first", type=int)
    ap.add_argument("--last", type=int)
    a = ap.parse_args()
    if a.cmd == "render":
        for s in a.slugs:
            render(s, a.dpi, a.first, a.last)
    elif a.cmd == "ingest":
        sys.exit(0 if ingest(a.slugs) else 1)
    else:
        status()
