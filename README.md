# Vendor technical evaluation agent

Reads vendor proposals, scores them against a technical rubric with page-level evidence,
and fills an Excel scorecard (ranking, mandatory knockout, summaries, audit log).

## What's new
- **Any number of vendors.** The Scorecard and Summary sheets are built for however many vendors you run (no 3-vendor limit).
- **Any mix of formats, several files per vendor.** PDF, Word, PowerPoint, Excel and TXT. A sub-folder in `data/proposals/` is one vendor:
  `data/proposals/Orion/` can hold Orion's PDF, PPT and Excel together. Loose files sharing a prefix before `__`
  (`Orion__tech.pdf`, `Orion__support.docx`) are also one vendor; any other loose file is its own vendor.
  `--per-file` makes every file its own vendor (to check that all formats are read the same way).
- **Sub-criteria.** The `SubCriteria` sheet splits a criterion into narrower questions (e.g. Security -> access control,
  encryption, certifications, audit logging). Each sub-criterion is scored on its own evidence, and the criterion score is the
  weighted average (plain code, not AI). Evidence is cited as `file p.N`.

## Pipeline
Parse (PyMuPDF / python-docx) -> chunk (page-tagged) -> embed (bi-encoder) -> vector index (FAISS)
-> per criterion: retrieve top-k -> cross-encoder rerank -> LLM scores (JSON) -> schema validation
+ quote verification -> deterministic aggregation -> summary (LLM) -> Excel export.

## Setup (Windows PowerShell)
    python -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    copy .env.example .env      # then put your API key in .env

## Run
    python main.py --mock --yes --rfp data/rfp/RFP_Parts_Tracking.txt   # offline test, no key, no downloads
    python main.py --rfp data/rfp/RFP_Parts_Tracking.txt --proposals data/proposals --out output/scorecard.xlsx
    python main.py --samples 3                              # 3 runs per criterion, flags disagreement
    python -m unittest tests.test_pipeline tests.test_dashboard_data   # tests

## Scoring methods (set per criterion on the Criteria sheet, column "Method")
- **Judgement** - the AI compares the evidence with the 1/3/5 anchors.
- **Lower is better** (timeline, response time): `Score = 5 x (2 x Target - Proposed) / Target`, capped 0-5.
  On or under target = 5; at 2x the target or later = 0.
- **Higher is better** (uptime, warranty years): `Score = 5 x (Proposed - Floor) / (Target - Floor)`, capped 0-5.
- For numeric methods the AI only extracts the vendor's number and unit (with a quote); plain code converts units
  (days/weeks/months/years) and applies the formula. The calculation is written in the Agent_Log justification.
- The **Target** comes from the RFP: run with `--rfp data/rfp/YourRFP.pdf`. The agent shows what it found and you
  press Enter to accept or type a correction (`--yes` skips the question). A number typed in the Criteria sheet's
  "Target" column overrides the RFP. The targets used are saved on the `Targets` sheet.

## Dashboard
    streamlit run app.py
Reads `output/scorecard.xlsx` (or upload it in the sidebar). Tabs: Ranking, Categories, Criteria detail,
Review queue, Summaries, Targets. The sidebar has what-if controls (mandatory minimum, shortlist size, weights).
Scores you override in Excel are picked up after you save the file and reload the page.

## Sub-criteria (SubCriteria sheet)
One row per sub-criterion: Sub-ID, Criterion ID, name, "What to look for", weight within the criterion (a criterion's
weights must total 100%), and optional 1/3/5 anchors. A criterion with no rows is scored as a whole. Numeric criteria
(Lower/Higher is better) cannot have sub-criteria. The shipped rows are drafts taken from your "What to look for" text: edit them.
In Excel, criterion scores that have sub-criteria are formulas; override the sub-criterion (yellow cell), or type a number
over the criterion's cell to override it as a whole.

## Inputs
- `template/scorecard_template.xlsx`: the rubric lives on its **Criteria** sheet (edit criteria,
  weights, mandatory flags, anchors there). If you add or remove criteria, add or remove the matching rows on **Scorecard**.
- `data/proposals/`: sub-folder per vendor (or one file per vendor); .pdf, .docx, .pptx, .xlsx, .txt. No vendor limit.

## Outputs
- `output/scorecard.xlsx`: Scorecard (scores + evidence, sub-criteria rows), Summary (rank, shortlist), Agent_Log (audit trail),
  Vendor_Summaries, Targets, Vendor_Files. Orange score = needs human review. Edit any yellow cell to override; totals recalculate.
- `output/scorecard.log.json`: raw per-criterion results.

## What gets flagged for human review
Not addressed in proposal, low model confidence, quote not found verbatim in the retrieved text,
cited page not in the retrieved evidence, samples disagree, mandatory criterion below minimum.

## Notes
- `--mock` uses keyword matching, NOT real scoring. It only proves the plumbing works.
- Any OpenAI-compatible API works via `LLM_BASE_URL` (Groq default). Check with your mentor which tools are
  approved before using real company proposals; keep confidential data off personal accounts.
- Scanned PDFs need OCR first (the parser warns when it finds no text).
