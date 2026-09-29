"""PDF discovery, metadata from file names, page rendering and text layer."""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


@dataclass
class Paper:
    path: Path
    sha: str
    exam: str
    date: str        # YYYY-MM-DD or ""
    year: int | None
    shift: str       # Morning / Evening / ""
    slug: str

    @property
    def shift_code(self):
        return self.shift[:1].upper() if self.shift else "X"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_meta(path: Path):
    name = path.stem
    exam = "JEE Advanced" if re.search(r"advanced", name, re.I) else "JEE Main"
    date, year, shift = "", None, ""
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", name)
    if m and m.group(2).lower() in MONTHS:
        d = datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        date, year = d.strftime("%Y-%m-%d"), d.year
    else:
        y = re.search(r"(19|20)\d{2}", name) or re.search(r"(19|20)\d{2}", str(path.parent))
        year = int(y.group(0)) if y else None
    s = re.search(r"(morning|evening|first|second)\s+shift", name, re.I)
    if s:
        shift = {"first": "Morning", "second": "Evening"}.get(s.group(1).lower(), s.group(1).title())
    slug = "_".join(x for x in ["JEEM" if exam == "JEE Main" else "JEEA", date or str(year or ""), shift[:1] or ""] if x)
    if not date:
        slug += "_" + re.sub(r"[^A-Za-z0-9]+", "-", name)[:40]
    return exam, date, year, shift, slug


def discover(root: Path):
    papers = []
    for p in sorted(root.glob("**/*.pdf")):
        if any(part.startswith(".") or part == "output" for part in p.relative_to(root).parts):
            continue
        exam, date, year, shift, slug = parse_meta(p)
        papers.append(Paper(p, sha256(p), exam, date, year, shift, slug))
    return papers


def render_page(doc, i, dpi):
    return doc[i].get_pixmap(dpi=dpi).tobytes("png")


def page_text(doc, i):
    return doc[i].get_text()


def has_text_layer(doc):
    n = min(len(doc), 6)
    return sum(len(doc[i].get_text().strip()) for i in range(n)) > 300 * n


def crop_box(doc, page_index, box_2d, dpi, pad=12):
    """box_2d = [ymin, xmin, ymax, xmax] normalised to 0-1000 (Gemini convention)."""
    page = doc[page_index]
    r = page.rect
    y0, x0, y1, x1 = [max(0, min(1000, v)) for v in box_2d]
    if y1 <= y0 or x1 <= x0:
        return None
    clip = pymupdf.Rect(r.x0 + x0 / 1000 * r.width, r.y0 + y0 / 1000 * r.height,
                        r.x0 + x1 / 1000 * r.width, r.y0 + y1 / 1000 * r.height)
    clip = pymupdf.Rect(clip.x0 - pad, clip.y0 - pad, clip.x1 + pad, clip.y1 + pad) & r
    if clip.width < 10 or clip.height < 10:
        return None
    return page.get_pixmap(dpi=dpi, clip=clip).tobytes("png")
