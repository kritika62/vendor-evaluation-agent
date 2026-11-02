# Backend API

Start it from the project folder (the one with `main.py`), with your `.env` in place:

    pip install -r requirements-api.txt
    uvicorn api.main:app --port 8000

Open **http://localhost:8000/docs**: every endpoint can be tried there (upload files, start, poll, download).
Data is stored under `api_data/` (change with `VEA_DATA_DIR`). Add `api_data/` to `.gitignore`.
The LLM key stays in `.env` on the server; nothing sends it to the browser.

## Typical order
1. `POST /rfp` - upload the RFP (replaces the previous one).
2. `POST /vendors/{name}/files` - upload one or more files (pdf, docx, pptx, xlsx, txt) for a vendor. Repeat per vendor.
3. `POST /evaluations` - start. Body (all optional): `{"mock": false, "samples": 1, "targets": {"T6": 99.9, "T7": 6}}`.
   Returns `202` with an `id`. The run happens in the background, one at a time.
4. `GET /evaluations/{id}` - poll until `status` is `done` (`percent`, `message`, last log lines; `failed` has `error`).
5. Read results (below).

## Endpoints
| Job | Endpoint |
|---|---|
| Upload / show / delete RFP | `POST /rfp`, `GET /rfp`, `DELETE /rfp` |
| Add files to a vendor | `POST /vendors/{name}/files` |
| List / delete vendors or one file | `GET /vendors`, `DELETE /vendors/{name}`, `DELETE /vendors/{name}/files/{filename}` |
| Start an evaluation | `POST /evaluations` |
| List evaluations / status | `GET /evaluations`, `GET /evaluations/{id}` |
| Ranking + category scores | `GET /evaluations/{id}/ranking?min_score=3&shortlist_n=2` (what-if optional) |
| One vendor: scores, sub-scores, evidence, sources, summary | `GET /evaluations/{id}/vendors/{name}` |
| Override a score (`T4` or `T4.2`) | `PATCH /evaluations/{id}/scores/{item_id}` body `{"vendor": "...", "score": 4, "reason": "..."}` |
| Override history | `GET /evaluations/{id}/overrides` |
| Download | `GET /evaluations/{id}/download?format=pdf` (pdf, docx, csv, json, md, xlsx) |
| Chat | `POST /evaluations/{id}/chat` body `{"question": "...", "history": []}` |
| View / edit criteria and sub-criteria | `GET /criteria`, `PUT /criteria` |

## Notes
- Vendor names: underscores become spaces (`Alpha_Systems` and `Alpha Systems` are the same vendor).
- An evaluation snapshots the proposals, RFP and criteria at the moment it starts, so later uploads or criteria edits
  never change an old result.
- Overriding a criterion that has sub-criteria replaces its average with your number; override the sub-criterion to keep
  the average. The agent's original score stays in the workbook's Agent_Log sheet.
- `PUT /criteria` validates a copy first (weights must total 100%, numeric criteria cannot have sub-criteria) and keeps
  the old template as `scorecard_template.bak.xlsx`. Weights are fractions (0.25 = 25%).
- If the workbook or template is open in Excel, writes return `409`: close it and retry.
- A server restart marks any run that was in progress as `failed`; start it again.
