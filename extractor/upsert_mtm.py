"""Upsert output/mocktestmitra/{tests,questions}.json into MockTestMitra's Supabase.

Order: figures -> Storage bucket `question-assets` (x-upsert), then `tests`
(on_conflict=id), then `questions` (on_conflict=test_id,position). A true upsert:
existing rows keep their uuid, so question analytics survive a re-run. Rows beyond a
test's current length are deleted so a shortened test cannot keep a stale tail.

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY, from the environment or from a .env file
(e.g. the MockTestMitra repo's): --env /path/to/mocktestmitra/.env

    python3 -m extractor.upsert_mtm --dry-run
    python3 -m extractor.upsert_mtm --env ../mocktestmitra/.env
    python3 -m extractor.upsert_mtm --env ../mocktestmitra/.env --skip-figures
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "output" / "mocktestmitra"
BUCKET = "question-assets"
BATCH = 200


def load_env(path: str | None) -> tuple[str, str]:
    env = {}
    if path:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = re.split(r"\s", v.strip().strip('"').strip("'"), maxsplit=1)[0]
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k].strip()
    if not env.get("SUPABASE_URL") or not env.get("SUPABASE_SERVICE_KEY"):
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_KEY not set (use --env or environment)")
    return env["SUPABASE_URL"].rstrip("/"), env["SUPABASE_SERVICE_KEY"]


def call(method, url, key, body=None, headers=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
    h = {"apikey": key, "Authorization": "Bearer " + key}
    if not isinstance(body, bytes):
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-figures", action="store_true")
    a = ap.parse_args()

    tests = json.loads((PKG / "tests.json").read_text(encoding="utf-8"))
    questions = json.loads((PKG / "questions.json").read_text(encoding="utf-8"))
    figures = sorted(p for p in (PKG / "figures").rglob("*") if p.is_file())
    sizes = Counter(q["test_id"] for q in questions)
    print(f"{len(tests)} tests, {len(questions)} questions, {len(figures)} figures")
    if a.dry_run:
        return

    url, key = load_env(a.env)
    upsert = {"Prefer": "resolution=merge-duplicates,return=minimal"}

    if not a.skip_figures:
        for i, p in enumerate(figures, 1):
            obj = urllib.parse.quote(p.relative_to(PKG / "figures").as_posix(), safe="/")
            call("POST", f"{url}/storage/v1/object/{BUCKET}/{obj}", key, p.read_bytes(),
                 {"Content-Type": "image/png", "x-upsert": "true", "cache-control": "3600"})
            if i % 50 == 0 or i == len(figures):
                print(f"  figures {i}/{len(figures)}")

    call("POST", f"{url}/rest/v1/tests?on_conflict=id", key, tests, upsert)
    print(f"  tests upserted: {len(tests)}")

    for i in range(0, len(questions), BATCH):
        call("POST", f"{url}/rest/v1/questions?on_conflict=test_id,position", key,
             questions[i:i + BATCH], upsert)
        print(f"  questions {min(i + BATCH, len(questions))}/{len(questions)}")

    for tid, n in sizes.items():
        call("DELETE", f"{url}/rest/v1/questions?test_id=eq.{urllib.parse.quote(tid)}"
                       f"&position=gte.{n}", key, None, {"Prefer": "return=minimal"})
    print("done")


if __name__ == "__main__":
    main()
