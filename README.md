# IIT-JEE
old Question Papers

## Question bank extraction

`extractor/` turns every PDF in this repo into rows of **`output/JEE_Questions.xlsx`**
(one row per question: subject, chapter, topic, question, options A–D, answer, type, diagram yes/no, year, …).

### How it works
All 26 papers in `IIT JEE 2021/` (2,340 questions) were read page by page from rendered page images
(`python -m extractor.manual render <paper>`), and the readings were saved as YAML in `output/raw/<paper>/`.
`python -m extractor.manual ingest` then validates them and builds the Excel file.
- Maths and chemistry notation is written as LaTeX (`$...$`). Drawn structures and graphs are described in `[figure: ...]` lines and cropped as images.
- Answers: the official NTA answer printed with each question, or, for the scanned July papers, the "KEYS" answer-key pages (`answers.yaml`).
  Where a printed key is demonstrably wrong, the correct answer is stored and the row carries a note in `Issues`.
- Chapter comes from the fixed list in `extractor/syllabus.yaml`. Each question also gets a topic and an estimated difficulty.
- Diagrams are cropped to `output/figures/<paper>/<SUBJ>_<sec><nn>_k.png` and linked from the Excel file.
- **Validation:** checks for 4 options per MCQ, numeric answers for numerical questions, valid option labels, gaps in
  question numbering (25 or 30 questions per subject), agreement with the PDF's own text layer, and a chapter within the subject.
  Each question gets a **Confidence** score (0–1). Anything below 0.8 goes to the **Needs_Review** sheet.

Progress per paper: `output/PROGRESS.md` (`python -m extractor.manual status`).

### Explanations
Worked explanations are stored in `output/raw/<paper>/expl_P.yaml`, `expl_C.yaml` and `expl_M.yaml`, keyed like `P A 1`.
Each explanation lists the concept, the given data, numbered steps and ends with an `Answer:` line.
When building the Excel file, that `Answer:` line is compared with `Correct_Answer`:
- `Explanation_Check` = `ok` when they match.
- A mismatch goes to `Needs_Review`.
- `missing` means the question has no explanation yet.

Coverage per paper is shown by `python -m extractor.manual status` and in `output/PROGRESS.md`.

### Excel sheets
- `Questions`: all questions
- `Needs_Review`: low-confidence rows, with the reason in the `Issues` column
- `Papers_Summary`: per paper: question counts per subject, diagrams, missing answers, average confidence
- `Chapter_Counts`: number of questions per chapter

### Automatic path (Gemini, optional)
The original automated pipeline is still available. It needs a Gemini API key, and the free tier is too small to process a full paper.

### Run it
```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...            # never commit the key
python -m extractor run              # only new or changed PDFs are processed
python -m extractor run --only "July 2021"   # a subset
python -m extractor excel            # rebuild the Excel file from output/json only
```
To add papers, drop new PDFs into a folder (for example `IIT JEE 2022/`) and run again.
Every Gemini response is cached in `output/cache/`, so re-runs and interrupted runs don't repeat API calls.
If the free-tier quota runs out, run the same command again later and it picks up where it stopped.

Optional environment variables: `GEMINI_MODEL` (default `gemini-3.5-flash`), `GEMINI_RPM` (requests per minute, default 10).
