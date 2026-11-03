# Vendor Evaluation Agent

Reads vendor proposals, scores them against an RFP and a technical rubric using an LLM, and shows the ranking with page-level evidence. A person can review, override scores, download reports and ask questions about the results.

- **Scoring pipeline** (Python): parse, chunk, embed, retrieve, rerank, LLM scoring, aggregation, Excel scorecard.
- **Backend** (FastAPI): one endpoint per job, with background evaluations.
- **Frontend** (React + Vite): upload, run, ranking, evidence, overrides, downloads, chat.
- **Dashboard** (Streamlit): the original dashboard, still available.
<img width="685" height="399" alt="image" src="https://github.com/user-attachments/assets/624d57d8-ffd7-4edc-bf0e-e74e26d3abbd" />
<img width="611" height="209" alt="image" src="https://github.com/user-attachments/assets/8381fbb1-cd65-4317-8526-db9cf53aaa5e" />
<img width="653" height="289" alt="image" src="https://github.com/user-attachments/assets/f251e53b-781c-4c44-8450-f85d97492e4b" />
<img width="749" height="251" alt="image" src="https://github.com/user-attachments/assets/8d79b055-ed0c-46f9-bb0d-7d60e48c1e08" />
<img width="655" height="352" alt="image" src="https://github.com/user-attachments/assets/e475d34e-b819-4bc3-bcb2-3aff77968012" />
<img width="650" height="390" alt="image" src="https://github.com/user-attachments/assets/4247a801-86c7-4bbc-9567-01aa2b17a81a" />
<img width="692" height="387" alt="image" src="https://github.com/user-attachments/assets/f8f51269-58a5-4d9e-8c66-214342b2c16e" />

## Features

- Any number of vendors; several files per vendor; PDF, Word, PowerPoint, Excel and TXT proposals.
- 9 criteria with sub-criteria. Each sub-criterion is scored on its own evidence with a quote and a source (`file p.N`). Criterion scores are weighted averages computed by code, not by the AI.
- Numeric criteria (for example uptime, timeline): the AI only extracts the vendor's number and unit; code converts units and applies the formula against the RFP target.
- Mandatory criteria act as a pass/fail gate. A vendor below the minimum score (default 3 of 5) on any mandatory criterion is rejected and not ranked. The minimum and shortlist size are adjustable without re-running the AI.
- Weak or uncertain scores are flagged for human review. Scores can be overridden with a reason; the original AI score is kept.
- Downloads: PDF, Word, CSV, JSON, Markdown, Excel.
- Chat assistant that answers from the scorecard and proposal text. Numbers come from code, never from the model.

## How it works

```
Browser (React, :5173)  ->  FastAPI (:8000)  ->  api/service.py  ->  vea/ pipeline
                                  |                                      |
                            api_data/ (plain folders)          LLM API + local models
```

Pipeline: parse (PyMuPDF, python-docx and others) -> page-tagged chunks -> embeddings (`BAAI/bge-small-en-v1.5`) -> FAISS index -> per criterion: retrieve top 12 -> cross-encoder rerank (`ms-marco-MiniLM-L-6-v2`) to the best 4 -> LLM scores as JSON -> validation and quote check -> deterministic aggregation -> summaries -> Excel export.

The embedding and reranker models run locally. The LLM is any OpenAI-compatible API (Groq by default).

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-api.txt
copy .env.example .env
```

Open `.env` and set your own values. Never commit `.env`; it is listed in `.gitignore`.

| Variable | Purpose |
|---|---|
| `LLM_BASE_URL` | OpenAI-compatible endpoint (Groq by default) |
| `LLM_API_KEY` | Your API key |
| `LLM_MODEL` | Model name |
| `EMBED_MODEL` | Embedding model |
| `RERANK_MODEL` | Reranker model |

## Run the web app

Terminal 1, the backend (project folder, venv active):

```powershell
uvicorn api.main:app --port 8000
```

Terminal 2, the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Interactive API docs: http://localhost:8000/docs. See `API.md` for the endpoints.

Typical use:

1. Upload the RFP.
2. Add vendors and their files.
3. Choose mock or real mode, check the numeric targets, and start the evaluation.
4. Read the ranking, open a vendor for evidence, override scores if needed, download a report, or ask the chat.

Mock mode makes no LLM calls and gives meaningless scores. It only proves the plumbing works.

## Run from the command line

```powershell
python main.py --mock --yes --rfp data/rfp/RFP_Parts_Tracking.txt
python main.py --rfp data/rfp/RFP_Parts_Tracking.txt --proposals data/proposals --out output/scorecard.xlsx --yes
python main.py --samples 3          # 3 runs per criterion, flags disagreement
```

## Streamlit dashboard

```powershell
streamlit run app.py
```

Reads `output/scorecard.xlsx` (or upload it in the sidebar). Tabs: Ranking, Categories, Criteria detail, Review queue, Summaries, Targets, Ask the assistant.

## Inputs

- `template/scorecard_template.xlsx`: the rubric lives on its Criteria and SubCriteria sheets (criteria, weights, mandatory flags, anchors, sub-criteria). Weights inside one criterion must total 100%. The shipped sub-criteria are drafts; edit them to match your RFP.
- `data/proposals/`: sample proposals (fictional vendors A, B, C). A sub-folder is one vendor; loose files named `Vendor__anything.ext` share a vendor; any other loose file is its own vendor.
- `data/rfp/`: a sample RFP.

## Scoring methods (Criteria sheet, column "Method")

- **Judgement**: the AI compares the evidence with the 1/3/5 anchors.
- **Lower is better** (timeline): `Score = 5 x (2 x Target - Proposed) / Target`, capped 0 to 5.
- **Higher is better** (uptime): `Score = 5 x (Proposed - Floor) / (Target - Floor)`, capped 0 to 5.

Targets come from the RFP text, the Target column of the Criteria sheet, or values typed in the web app. A missing value is flagged, not silently scored as a real answer.

## What gets flagged for review

Not addressed in the proposal, low model confidence, quote not found verbatim in the retrieved text, cited page not in the retrieved evidence, samples disagree, mandatory criterion below the minimum, numeric value not found.

## Tests

```powershell
python -m unittest tests.test_pipeline tests.test_dashboard_data tests.test_api_service
```

## Known limitations

- LLM scores vary a little between runs (totals by roughly 0.3 to 0.5). Ranking order and pass/fail have been stable across the real runs so far.
- A value that is plainly stated in a proposal (for example a timeline) can occasionally be missed. Such items are flagged for review and can be overridden.
- Free-tier LLM rate limits make real runs slow (minutes), and chat uses the same quota.
- No login and one evaluation at a time, so it is meant for local use, not public deployment.
- Scanned PDFs need OCR first.

## Notes

- Check which AI tools are approved before using real company proposals, and keep confidential data off personal accounts.
- The sample vendors are fictional.

