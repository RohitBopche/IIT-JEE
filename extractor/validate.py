"""Normalisation, validation checks and confidence scoring."""
import re
from difflib import SequenceMatcher

from .prompts import SYLLABUS

LETTERS = "ABCD"
SUBJ_CODE = {"Physics": "PHY", "Chemistry": "CHE", "Mathematics": "MAT"}


def sec(q):
    """Normalised section letter ('A', 'B', ... or '')."""
    m = re.search(r"[A-Z]", (q.get("section") or "").upper().replace("SECTION", ""))
    return m.group(0) if m else ""


def norm_answer(ans, qtype, n_options):
    a = (ans or "").strip()
    if not a:
        return ""
    if re.fullmatch(r"(?i)bonus|dropped|all.*", a):
        return "Bonus"
    if qtype.startswith("MCQ"):
        labels = re.findall(r"\b([1-4A-Da-d])\b", re.sub(r"[()\[\]]", " ", a))
        if labels and len(re.sub(r"[\s(),\[\]&and1-4A-Da-d]", "", a)) == 0:
            out = []
            for l in labels:
                l = LETTERS[int(l) - 1] if l.isdigit() else l.upper()
                if l not in out:
                    out.append(l)
            return ",".join(out)
        return a
    return a.strip("() ")


def words(text):
    text = re.sub(r"\[figure[^\n]*", " ", text or "")  # our own figure descriptions are not printed text
    text = re.sub(r"\$[^$]*\$", " ", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    return [w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)]


def text_layer_recall(question_text, page_text):
    """Share of the question's plain words that also appear in the PDF text layer."""
    qw = words(question_text)
    if len(qw) < 4 or not page_text:
        return None
    pw = set(w.lower() for w in re.findall(r"[A-Za-z]{4,}", page_text))
    return sum(w in pw for w in qw) / len(qw)


def similarity(a, b):
    return SequenceMatcher(None, a or "", b or "").ratio()


def check_question(q):
    """Return list of issue strings for one question (already normalised)."""
    issues = []
    n = len(q["options"])
    if q["question_type"].startswith("MCQ"):
        if n != 4:
            issues.append(f"MCQ has {n} options")
        if any(not o.strip() for o in q["options"]):
            issues.append("empty option")
        if len(set(o.strip() for o in q["options"])) < n:
            issues.append("duplicate options")
        if q["answer"] and q["answer"] != "Bonus" and not re.fullmatch(r"[A-D](,[A-D])*", q["answer"]):
            issues.append(f"answer not an option label: {q['answer']}")
    else:
        if n:
            issues.append(f"numerical question has {n} options")
        if q["answer"] and q["answer"] != "Bonus" and not re.search(r"\d", q["answer"]):
            issues.append(f"numerical answer not numeric: {q['answer']}")
    if not q["answer"]:
        issues.append("answer missing")
    if any(ord(c) < 32 and c not in "\n\t" for c in q["question_text"] + "".join(q["options"])):
        issues.append("control character in text (bad escape)")
    if len(q["question_text"].strip()) < 15:
        issues.append("question text too short")
    if q["chapter"] not in SYLLABUS.get(q["subject"], []):
        issues.append(f"chapter '{q['chapter']}' not in {q['subject']} syllabus")
    if q["has_diagram"] and not q.get("diagram_files"):
        issues.append("diagram flagged but not cropped")
    if q.get("text_recall") is not None and q["text_recall"] < 0.7:
        issues.append(f"low agreement with PDF text layer ({q['text_recall']:.2f})")
    if q.get("model_confidence") == "low":
        issues.append("model reported low confidence")
    if q.get("notes"):
        issues.append("model note: " + q["notes"][:120])
    return issues


PENALTY = [
    (r"^MCQ has|^numerical question has", 0.45),
    (r"^empty option|^duplicate options", 0.3),
    (r"^question text too short", 0.4),
    (r"^control character", 0.5),
    (r"^answer not an option|^numerical answer not numeric", 0.15),
    (r"^answer missing", 0.1),
    (r"^chapter", 0.05),
    (r"^diagram flagged", 0.1),
    (r"^low agreement", 0.25),
    (r"^model reported low", 0.3),
    (r"^model note", 0.1),
    (r"^numbering", 0.15),
    (r"^verification disagrees", 0.3),
]


def score(q):
    s = 1.0
    if q.get("model_confidence") == "medium":
        s -= 0.1
    for issue in q["issues"]:
        for pat, pen in PENALTY:
            if re.search(pat, issue):
                s -= pen
                break
    if q.get("verified"):
        s = min(1.0, s + 0.1)
    return round(max(0.0, s), 2)


def check_numbering(questions):
    """Flag gaps/duplicates in printed numbering per subject. Returns paper-level issues."""
    issues = []
    by_subj, by_sec = {}, {}
    for q in questions:
        by_subj.setdefault(q["subject"], []).append(q)
        by_sec.setdefault((q["subject"], sec(q)), []).append(q)
    for subj in SYLLABUS:
        if not by_subj.get(subj):
            issues.append(f"{subj}: no questions found")
            continue
        n = len(by_subj[subj])
        if n not in (25, 30):
            issues.append(f"{subj}: {n} questions (expected 25 or 30)")
    for (subj, s), qs in sorted(by_sec.items()):
        nums = sorted(q["q_no"] for q in qs)
        label = f"{subj} {('section ' + s) if s else ''}".strip()
        missing = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
        if missing:
            issues.append(f"{label}: missing question numbers {missing}")
            for q in qs:
                if q["q_no"] - 1 in missing or q["q_no"] + 1 in missing:
                    q["issues"].append("numbering gap next to this question")
    return issues
