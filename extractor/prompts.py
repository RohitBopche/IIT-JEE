"""Prompts and JSON schemas sent to Gemini."""
from pathlib import Path

import yaml

PROMPT_VERSION = "v1"
SYLLABUS = yaml.safe_load((Path(__file__).parent / "syllabus.yaml").read_text())
SUBJECTS = list(SYLLABUS)
ALL_CHAPTERS = sorted({c for chs in SYLLABUS.values() for c in chs})

QUESTION_TYPES = ["MCQ-Single", "MCQ-Multiple", "Numerical"]
QUESTION_STYLES = [
    "Standard", "Match the Following", "Assertion-Reason", "Statement I/II",
    "Multiple Statements", "Graph/Diagram Based", "Fill in the Blank", "Comprehension",
]

SYSTEM = """You are a meticulous data-entry expert digitising Indian JEE exam papers (Physics, Chemistry, Mathematics).
You receive page images of one paper and return strict JSON. Accuracy matters more than speed:
copy question text and options EXACTLY as printed (no paraphrasing, no fixing, no solving), preserving every number, unit and sign.

Formatting rules:
- Write all mathematics, chemical formulas with sub/superscripts, and symbols in LaTeX inside $...$ (e.g. $3.62\\,\\text{mm}$, $\\mathrm{H_2SO_4}$, $\\vec{A}\\times\\vec{B}$, $\\frac{3r^2}{2a^2}$).
- Keep plain words as plain text. Use \\n for line breaks inside lists (e.g. List-I/List-II, statements (a),(b),(c)).
- Tables inside a question: render as a compact markdown table inside question_text.
- Options: list them in printed order WITHOUT the label "(1)"/"(A)". MCQ always has the printed options (normally 4). Numerical/integer questions have an empty options list.
- If an option is only a picture (e.g. a graph or structure), write "[figure]" plus a brief description, and set has_diagram=true.
- Never include the solution text, hints, or answer inside question_text or options.

Diagrams: has_diagram=true when the QUESTION or its OPTIONS contain a figure, graph, circuit, ray diagram, chemical structure drawing, table-like figure or any picture needed to answer.
Figures that belong only to the solution do NOT count. For each such figure give diagram_boxes with the image number (1-based, in the order images are given) and box_2d=[ymin,xmin,ymax,xmax] normalised 0-1000 tightly around the figure.

Answer: copy the official answer printed near the question (e.g. "Official Ans. by NTA (4)" -> "4"; "Ans. (B)" -> "B"; "Sol. (1)" at start of solution -> "1"; numerical -> the printed value). If the paper shows several keys, prefer the official NTA one. If marked bonus/dropped write "Bonus". If no answer is printed for that question on these pages, write "".

Question type: MCQ-Single (one correct option), MCQ-Multiple (more than one correct), Numerical (integer/decimal answer, no options).
Question style: pick the best match from the allowed list; "Standard" if none applies.
Chapter: pick exactly one chapter from the list for the question's subject. Topic: a short (2-6 words) specific sub-topic.
Difficulty: your estimate for a JEE Main aspirant.
confidence: "high" only if every character of the question and options is clearly legible and you are sure of the question boundaries."""

CHAPTER_LIST_TEXT = "\n".join(f"{s}: " + "; ".join(chs) for s, chs in SYLLABUS.items())


def chunk_prompt(n_main, has_lookahead, state):
    look = (f"Image {n_main + 1} is the NEXT page, given only so you can finish a question that runs past image {n_main}. "
            f"Do NOT extract questions that START on image {n_main + 1}.") if has_lookahead else "There is no next page."
    ctx = "This is the start of the paper." if not state else (
        f"Context from the previous pages: current subject = {state.get('subject') or 'unknown'}, "
        f"section = {state.get('section') or 'unknown'}, last question number seen = {state.get('last_q_no') or 'unknown'}, "
        f"previous pages were {'solutions' if state.get('in_solutions') else 'questions'}.")
    return f"""{ctx}

You are given {n_main + (1 if has_lookahead else 0)} page image(s). Images 1..{n_main} are the pages to process. {look}
Ignore any text at the top of image 1 that is the tail of a question (or solution) that started on an earlier page.

Tasks:
1. questions: extract EVERY question whose number/start appears on images 1..{n_main}, in order. Subject comes from the page/section headings (carry over from context if no heading).
   q_no is the number printed on the paper for that question.
2. solution_answers: some papers print all questions first and the solutions later (e.g. "Sol1.", "Sol2." under "Solutions" headings). For every solution on images 1..{n_main} that belongs to a question NOT on these pages,
   give subject, q_no and the final answer exactly as the solution concludes it (option label if stated, else the final value/expression in LaTeX).
3. end_state: the subject, section and last question number at the end of image {n_main}, and whether image {n_main} is in a solutions-only part.
Instruction/cover pages have no questions: return empty lists.

Allowed chapters:
{CHAPTER_LIST_TEXT}"""


def chunk_schema():
    q = {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "enum": SUBJECTS},
            "section": {"type": "string", "description": "Section label as printed, e.g. A or B; '' if none"},
            "q_no": {"type": "integer"},
            "image": {"type": "integer", "description": "1-based image where the question starts"},
            "question_text": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "question_type": {"type": "string", "enum": QUESTION_TYPES},
            "question_style": {"type": "string", "enum": QUESTION_STYLES},
            "answer": {"type": "string"},
            "has_diagram": {"type": "boolean"},
            "diagram_boxes": {"type": "array", "items": {
                "type": "object",
                "properties": {"image": {"type": "integer"},
                               "box_2d": {"type": "array", "items": {"type": "integer"}}},
                "required": ["image", "box_2d"]}},
            "chapter": {"type": "string", "enum": ALL_CHAPTERS},
            "topic": {"type": "string"},
            "difficulty": {"type": "string", "enum": ["Easy", "Medium", "Hard"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "notes": {"type": "string", "description": "anything unclear/illegible; '' if none"},
        },
        "required": ["subject", "section", "q_no", "image", "question_text", "options", "question_type",
                     "question_style", "answer", "has_diagram", "diagram_boxes", "chapter", "topic",
                     "difficulty", "confidence", "notes"],
    }
    return {
        "type": "object",
        "properties": {
            "questions": {"type": "array", "items": q},
            "solution_answers": {"type": "array", "items": {
                "type": "object",
                "properties": {"subject": {"type": "string", "enum": SUBJECTS}, "q_no": {"type": "integer"},
                               "answer": {"type": "string"}},
                "required": ["subject", "q_no", "answer"]}},
            "end_state": {"type": "object", "properties": {
                "subject": {"type": "string"}, "section": {"type": "string"},
                "last_q_no": {"type": "integer"}, "in_solutions": {"type": "boolean"}},
                "required": ["subject", "section", "last_q_no", "in_solutions"]},
        },
        "required": ["questions", "solution_answers", "end_state"],
    }


def verify_prompt(subject, q_no, n_images):
    return f"""Re-extract ONLY {subject} question number {q_no} from these {n_images} consecutive page image(s) (it starts on image 1 or 2 and may continue onto the next image).
Be extremely careful: copy question text and every option exactly. Return it as the single element of "questions" (empty list if the question is not visible).
Return empty solution_answers. end_state may be filled with the question's subject/section.

Allowed chapters:
{CHAPTER_LIST_TEXT}"""


RESOLVE_SYSTEM = """You map a stated final answer of a JEE solution to the correct option of the question.
Return the option label (A, B, C or D) whose content equals the stated final answer. If none clearly matches, return "".
Do not solve the question yourself unless the stated answer is ambiguous; never guess."""


def resolve_schema():
    return {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "option": {"type": "string", "enum": ["A", "B", "C", "D", ""]}},
        "required": ["id", "option"]}}}, "required": ["items"]}
