# IIT-JEE
old Question Papers

## Question bank extraction

`extractor/` turns every PDF in this repo into rows of **`output/JEE_Questions.xlsx`**
(one row per question: subject, chapter, topic, question, options A–D, answer, type, diagram yes/no, year, …).

### How it works
1. Finds every `*.pdf` in the repo. Date, shift and year come from the file name
   (`... 26 August 2021 Morning Shift ...`), so keep that naming for new papers.
2. Renders pages as images and sends 3 pages at a time (plus the next page, so questions that run over a page break stay whole) to **Google Gemini** (`gemini-3.5-flash`).
   This works for text PDFs and scanned PDFs alike. Maths and chemistry notation is written as LaTeX (`$...$`).
3. Gemini returns structured JSON: question, options, official answer, type/style, chapter (from the fixed list in
   `extractor/syllabus.yaml`), topic, diagram boxes.
4. For papers that print solutions separately (for example the July 2021 papers), answers are read from the solution pages and matched to options.
5. Diagrams are cropped to `output/figures/<paper>/<SUBJ>_Qnn_k.png` and linked from the Excel file.
6. **Validation:** 4 options per MCQ, numeric answers for numerical questions, valid option labels, no gaps in question
   numbering, agreement with the PDF's own text layer, and chapter within the subject. Each question gets a
   **Confidence** score (0–1). Questions below 0.8 are read a second time and compared.
   Anything still below 0.8 goes to the **Needs_Review** sheet.

### Excel sheets
- `Questions`: all questions
- `Needs_Review`: low-confidence rows, with the reason in the `Issues` column
- `Papers_Summary`: per paper: question counts per subject, diagrams, missing answers, average confidence
- `Chapter_Counts`: number of questions per chapter

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
