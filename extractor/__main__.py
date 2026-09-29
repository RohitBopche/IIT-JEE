"""CLI:  python -m extractor [run|excel] [--only TEXT] [--force] [--no-verify] [--workers N]

run   : extract every PDF that is new or changed since the last run, then rebuild the Excel file.
excel : only rebuild output/JEE_Questions.xlsx from output/json.
"""
import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import excel, prompts
from .gemini import Gemini, GeminiError
from .papers import discover
from .pipeline import Extractor

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
XLSX = OUT / "JEE_Questions.xlsx"
MANIFEST = OUT / "manifest.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "excel"])
    ap.add_argument("--only", action="append", help="process only PDFs whose path contains this text (repeatable)")
    ap.add_argument("--force", action="store_true", help="re-process even if unchanged (API cache still used)")
    ap.add_argument("--no-verify", action="store_true", help="skip the second-read verification pass")
    ap.add_argument("--workers", type=int, default=2, help="papers processed in parallel")
    a = ap.parse_args()

    if a.cmd == "run":
        manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
        papers = discover(ROOT)
        if a.only:
            papers = [p for p in papers if any(o.lower() in str(p.path).lower() for o in a.only)]
        todo = [p for p in papers if a.force or manifest.get(str(p.path.relative_to(ROOT)), {}).get("sha256") != p.sha
                or manifest[str(p.path.relative_to(ROOT))].get("prompt_version") != prompts.PROMPT_VERSION]
        slugs = [p.slug for p in papers]
        dup = {s for s in slugs if slugs.count(s) > 1}
        if dup:
            sys.exit(f"duplicate paper ids {dup}: rename the PDFs so date/shift differ")
        print(f"{len(papers)} PDFs found, {len(todo)} to process")
        g = Gemini()
        ex = Extractor(OUT, g, verify=not a.no_verify)
        failed = []

        def work(p):
            log = lambda m: print(f"[{p.slug}] {m}", flush=True)
            log(f"start {p.path.name}")
            res = ex.run(p, log)
            qs = res["questions"]
            log(f"done: {len(qs)} questions, {sum(q['confidence'] < excel.REVIEW_BELOW for q in qs)} need review; "
                f"issues: {res['issues'] or 'none'}")
            return p, res

        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futs = {pool.submit(work, p): p for p in todo}
            for f in as_completed(futs):
                p = futs[f]
                try:
                    _, res = f.result()
                    manifest[str(p.path.relative_to(ROOT))] = {
                        "sha256": p.sha, "slug": p.slug, "prompt_version": prompts.PROMPT_VERSION,
                        "questions": len(res["questions"])}
                    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
                    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True))
                except GeminiError as e:
                    failed.append(p)
                    print(f"[{p.slug}] FAILED: {e}", flush=True)
                except Exception:
                    failed.append(p)
                    print(f"[{p.slug}] FAILED:\n{traceback.format_exc()}", flush=True)
        if failed:
            print(f"{len(failed)} paper(s) failed; re-run later to resume (finished pages are cached).")

    n, review = excel.build(OUT, XLSX)
    print(f"Excel written: {XLSX.relative_to(ROOT)} ({n} questions, {review} need review)")


if __name__ == "__main__":
    main()
