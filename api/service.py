"""All the logic behind the API, with no web-framework code, so it can be tested directly.

Storage is plain folders (no database), under VEA_DATA_DIR (default ./api_data):

  workspace/rfp/<file>                      the current RFP (one file)
  workspace/proposals/<vendor>/<files>      each vendor's files, any mix of formats
  evaluations/<id>/inputs/...               a snapshot of the RFP, proposals and criteria used for that run
  evaluations/<id>/output/scorecard.xlsx    the result (+ .log.json, .chunks.json)
  evaluations/<id>/job.json                 status, progress and log
  evaluations/<id>/overrides.json           audit trail of score overrides
"""
import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from vea.chat import ChatError, chat as run_chat
from vea.dashboard_data import compute, load_results
from vea.exports import FORMATS, export
from vea.ingest import SUPPORTED, vendor_name
from vea.pipeline import EvaluationError, run_evaluation
from vea.proposal_index import ProposalSearch, chunks_path_for, load_chunks
from vea.report import scorecard_rows, vendor_columns
from vea.rubric import load_rubric

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_SUBCRITERIA = 56                                  # rows 5-60 of the SubCriteria sheet
ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ServiceError(Exception):
    """A problem the caller can fix. `status` is the HTTP status the API should answer with."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- paths
def data_dir():
    return Path(os.getenv("VEA_DATA_DIR", "api_data")).resolve()


def template_path():
    return Path(os.getenv("VEA_TEMPLATE") or Path(__file__).resolve().parent.parent / "template" / "scorecard_template.xlsx")


def _rfp_dir():
    return data_dir() / "workspace" / "rfp"


def _proposals_dir():
    return data_dir() / "workspace" / "proposals"


def _evals_dir():
    return data_dir() / "evaluations"


# ---------------------------------------------------------------- name / file checks
def clean_vendor_name(name):
    name = (name or "").strip()
    if not name or len(name) > 80 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.&()-]*", name) or name.endswith("."):
        raise ServiceError(400, "Vendor name must be 1-80 characters: letters, digits, spaces and _ . - & ( ) only.")
    return name


def clean_filename(filename):
    name = Path((filename or "").replace("\\", "/")).name.strip()
    ext = Path(name).suffix.lower()
    if ext in (".doc", ".ppt", ".xls"):
        raise ServiceError(400, f"{name}: old Office format. Open it in Office and 'Save As' {ext}x first.")
    if not name or ext not in SUPPORTED:
        raise ServiceError(400, f"{name or '(no name)'}: unsupported file type. Use {', '.join(sorted(SUPPORTED))}.")
    if name.startswith(("~$", ".")):
        raise ServiceError(400, f"{name}: temporary or hidden files are not accepted.")
    return name


def _save_stream(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ServiceError(413, f"{dest.name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise ServiceError(400, f"{dest.name} is empty.")
    return size


# ---------------------------------------------------------------- RFP
def save_rfp(filename, fileobj):
    name = clean_filename(filename)
    d = _rfp_dir()
    if d.exists():
        shutil.rmtree(d)                                  # one RFP at a time
    size = _save_stream(fileobj, d / name)
    return {"file": name, "bytes": size}


def rfp_path():
    d = _rfp_dir()
    files = sorted(d.glob("*")) if d.exists() else []
    return files[0] if files else None


def rfp_info():
    p = rfp_path()
    return {"file": p.name, "bytes": p.stat().st_size} if p else None


def delete_rfp():
    if not rfp_path():
        raise ServiceError(404, "No RFP uploaded.")
    shutil.rmtree(_rfp_dir())


# ---------------------------------------------------------------- vendors and their files
def _norm(name):
    return re.sub(r"[\s_]+", " ", name).strip().lower()


def _find_vendor_dir(name):
    root = _proposals_dir()
    if root.exists():
        for d in root.iterdir():
            if d.is_dir() and _norm(d.name) == _norm(name):
                return d
    return None


def save_vendor_files(name, uploads):
    """uploads: [(filename, file-like)]. Adds to the vendor (created if new). Same file name replaces the old file."""
    name = clean_vendor_name(name)
    if not uploads:
        raise ServiceError(400, "No files were sent.")
    names = [clean_filename(fn) for fn, _ in uploads]      # validate everything before writing anything
    d = _find_vendor_dir(name) or (_proposals_dir() / name)
    saved = [{"file": n, "bytes": _save_stream(f, d / n)} for n, (_, f) in zip(names, uploads)]
    return {"vendor": vendor_name(d.name), "saved": saved, "files": sorted(p.name for p in d.iterdir())}


def list_vendors():
    root = _proposals_dir()
    out = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        if d.is_dir():
            files = sorted(p for p in d.iterdir() if p.is_file())
            out.append({"vendor": vendor_name(d.name),
                        "files": [{"file": p.name, "bytes": p.stat().st_size} for p in files]})
    return out


def delete_vendor(name):
    d = _find_vendor_dir(clean_vendor_name(name))
    if d is None:
        raise ServiceError(404, f"Vendor '{name}' not found.")
    shutil.rmtree(d)


def delete_vendor_file(name, filename):
    d = _find_vendor_dir(clean_vendor_name(name))
    if d is None:
        raise ServiceError(404, f"Vendor '{name}' not found.")
    f = d / Path(filename).name
    if not f.is_file():
        raise ServiceError(404, f"{filename} not found for {name}.")
    f.unlink()
    if not any(d.iterdir()):
        d.rmdir()


# ---------------------------------------------------------------- evaluations (background jobs)
_executor = ThreadPoolExecutor(max_workers=1)             # one evaluation at a time: protects the Groq rate limit
_locks, _locks_guard = {}, threading.Lock()
_search_guard = threading.Lock()                          # loading the embedding model can take a while; keep it
_results_cache, _search_cache = {}, {}                    # apart from the per-evaluation lock that status polls use


def _lock(eval_id):
    with _locks_guard:
        return _locks.setdefault(eval_id, threading.RLock())    # re-entrant: a locked function may call another


def _retry(fn, attempts=40, delay=0.05):
    """Windows refuses to open or replace a file another thread/program is using at that instant
    (PermissionError). It clears within milliseconds, so wait and try again instead of failing."""
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def _eval_dir(eval_id):
    if not ID_RE.match(eval_id or ""):
        raise ServiceError(404, "Evaluation not found.")
    d = _evals_dir() / eval_id
    if not d.is_dir():
        raise ServiceError(404, "Evaluation not found.")
    return d


def _read_job_file(folder):
    return _retry(lambda: json.loads((folder / "job.json").read_text(encoding="utf-8")))


def _read_job(eval_id):
    folder = _eval_dir(eval_id)
    with _lock(eval_id):                                  # never read while the job thread is replacing the file
        return _read_job_file(folder)


def _write_job(folder, job):
    tmp = folder / "job.json.tmp"
    _retry(lambda: tmp.write_text(json.dumps(job, indent=2), encoding="utf-8"))
    _retry(lambda: os.replace(tmp, folder / "job.json"))


def _update_job(eval_id, **changes):
    folder = _eval_dir(eval_id)
    with _lock(eval_id):
        job = _read_job_file(folder)
        log_line = changes.pop("log_line", None)
        job.update(changes)
        if log_line:
            job["log"] = (job.get("log", []) + [log_line])[-300:]
        _write_job(folder, job)


def _now():
    return datetime.now().isoformat(timespec="seconds")


def start_evaluation(mock=False, samples=1, per_file=False, targets=None, currency=None):
    vendors = list_vendors()
    if not vendors:
        raise ServiceError(400, "Upload at least one vendor's files first (POST /vendors/{name}/files).")
    if not mock and not os.getenv("LLM_API_KEY"):
        raise ServiceError(400, "LLM_API_KEY is not set on the server. Put it in .env, or start with mock=true.")
    if currency:
        from vea.currency import CurrencyError, norm_currency
        try:
            currency = norm_currency(currency)
        except CurrencyError as e:
            raise ServiceError(400, str(e))
        if currency is None:
            raise ServiceError(400, "Unknown costing currency. Use a code such as INR, USD, EUR, GBP or JPY.")
    eval_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    folder = _evals_dir() / eval_id
    inputs = folder / "inputs"
    shutil.copytree(_proposals_dir(), inputs / "proposals")
    rfp = rfp_path()
    if rfp:
        (inputs / "rfp").mkdir(parents=True)
        shutil.copy2(rfp, inputs / "rfp" / rfp.name)
    shutil.copy2(template_path(), inputs / "scorecard_template.xlsx")      # the criteria used for THIS run
    options = {"mock": bool(mock), "samples": int(samples), "per_file": bool(per_file), "targets": targets or {}, "currency": currency or None}
    _write_job(folder, {"id": eval_id, "status": "queued", "percent": 0, "message": "Waiting to start",
                        "created": _now(), "started": None, "finished": None, "options": options,
                        "vendors": [v["vendor"] for v in vendors], "error": None, "log": []})
    _executor.submit(_run, eval_id)
    return get_status(eval_id)


def _run(eval_id):
    folder = _eval_dir(eval_id)
    job = _read_job(eval_id)
    opts = job["options"]
    _update_job(eval_id, status="running", started=_now(), message="Starting")

    def progress(message, done, total):
        try:
            _update_job(eval_id, percent=int(100 * done / max(total, 1)),
                        message=message.strip().split("\n")[0][:160], log_line=message)
        except OSError:
            pass                                          # a missed progress update is harmless; losing the run is not
    try:
        inputs = folder / "inputs"
        rfp_files = sorted((inputs / "rfp").glob("*")) if (inputs / "rfp").exists() else []
        run_evaluation(str(inputs / "proposals"), str(folder / "output" / "scorecard.xlsx"),
                       rfp=str(rfp_files[0]) if rfp_files else None, template=str(inputs / "scorecard_template.xlsx"),
                       samples=opts["samples"], per_file=opts["per_file"], mock=opts["mock"], confirm=False,
                       interactive=False, target_overrides=opts.get("targets") or None, currency=opts.get("currency"), progress=progress)
        _update_job(eval_id, status="done", percent=100, message="Finished", finished=_now())
    except EvaluationError as e:
        _update_job(eval_id, status="failed", error=str(e), finished=_now())
    except Exception as e:                                # unexpected: keep the traceback for debugging
        (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        _update_job(eval_id, status="failed", error=f"{type(e).__name__}: {e}", finished=_now())


def recover():
    """Call once at server start: runs that were in progress when the server stopped can never finish."""
    root = _evals_dir()
    for d in root.iterdir() if root.exists() else []:
        f = d / "job.json"
        if f.is_file():
            job = _read_job_file(d)
            if job.get("status") in ("queued", "running"):
                job.update(status="failed", error="The server was restarted while this evaluation was running. Start it again.",
                           finished=_now())
                _write_job(d, job)


def get_status(eval_id, log_lines=40):
    job = _read_job(eval_id)
    job["log"] = job.get("log", [])[-log_lines:]
    return job


def list_evaluations():
    root = _evals_dir()
    out = []
    for d in sorted(root.iterdir(), reverse=True) if root.exists() else []:
        if (d / "job.json").is_file():
            j = _read_job_file(d)
            out.append({k: j.get(k) for k in ("id", "status", "percent", "created", "finished", "vendors", "error")})
    return out


# ---------------------------------------------------------------- results
def _xlsx(eval_id):
    job = _read_job(eval_id)
    if job["status"] != "done":
        raise ServiceError(409, f"Evaluation is {job['status']}; results are available when it is done.")
    return _eval_dir(eval_id) / "output" / "scorecard.xlsx"


def get_results(eval_id):
    path = _xlsx(eval_id)
    mtime = path.stat().st_mtime
    cached = _results_cache.get(eval_id)
    if cached and cached[0] == mtime:
        return cached[1]
    res = load_results(str(path))
    _results_cache[eval_id] = (mtime, res)
    return res


def _nz(v):
    """JSON-safe: NaN / None -> None, numpy numbers -> Python numbers."""
    if v is None or v != v:
        return None
    return v.item() if hasattr(v, "item") else v


def _check_settings(min_score, shortlist_n):
    if min_score is not None and not 0 <= min_score <= 5:
        raise ServiceError(400, "min_score must be between 0 and 5.")
    if shortlist_n is not None and shortlist_n < 1:
        raise ServiceError(400, "shortlist_n must be at least 1.")


def _summary_rows(out):
    return [{"vendor": r["Vendor"], "weighted_score": _nz(r["Weighted score"]), "score_pct": _nz(r["Score %"]),
             "status": r["Status"], "rank": None if _nz(r["Rank"]) is None else int(r["Rank"]),
             "recommendation": r["Recommendation"], "failed_mandatory": r["Failed mandatory"] or ""}
            for _, r in out["summary"].iterrows()]


def get_ranking(eval_id, min_score=None, shortlist_n=None):
    _check_settings(min_score, shortlist_n)
    res = get_results(eval_id)
    out = compute(res, min_score=min_score, shortlist_n=shortlist_n)
    cats = out["categories"]
    return {"settings": {"min_score": out["min_score"], "shortlist_n": out["shortlist_n"]},
            "ranking": _summary_rows(out),
            "categories": {str(c): {v: (None if _nz(cats.loc[c, v]) is None else round(float(cats.loc[c, v]), 2))
                                    for v in cats.columns} for c in cats.index},
            "flagged_count": int(len(out["flagged"]))}


def _find_vendor(res, name):
    for v in res["scores"]:
        if _norm(str(v)) == _norm(name or ""):
            return v
    raise ServiceError(404, f"Vendor '{name}' not found in this evaluation.")


def get_vendor(eval_id, name):
    res = get_results(eval_id)
    v = _find_vendor(res, name)
    out = compute(res)
    log = res["log"]
    row_of = {}
    if not log.empty:
        for _, r in log[log["Vendor"] == v].iterrows():
            row_of[str(r["ID"])] = r

    def item(item_id, label, weight, current):
        r = row_of.get(item_id)
        agent = _nz(r["Score"]) if r is not None else None
        d = {"id": item_id, "name": label, "weight": weight, "score": _nz(current), "agent_score": agent,
             "overridden": agent is not None and current is not None and abs(float(current) - float(agent)) > 1e-9}
        if r is not None:
            d.update(confidence=_nz(r["Confidence"]), needs_review=str(r["Needs review"]) == "Yes",
                     review_reason=_nz(r["Review reason"]) or "", sources=_nz(r["Sources"]) or "",
                     justification=_nz(r["Justification"]) or "", evidence_quote=_nz(r["Evidence quote"]) or "")
        return d

    criteria = []
    for c in out["criteria"]:
        d = item(c.id, c.name, round(c.weight, 4), res["scores"][v].get(c.id))
        d.update(category=c.category, mandatory=c.mandatory, method=c.method,
                 sub_criteria=[item(s.id, s.name, round(s.weight, 4), res["sub_scores"][v].get(s.id)) for s in c.subs])
        criteria.append(d)
    summary_row = next(r for r in _summary_rows(out) if r["vendor"] == v)
    sm = res["summaries"]
    summary = {}
    if not sm.empty and v in set(sm["Vendor"]):
        r = sm[sm["Vendor"] == v].iloc[0]
        summary = {k.lower(): _nz(r[k]) or "" for k in ("Overview", "Strengths", "Gaps", "Risks")}
    files = res["files"][res["files"]["Vendor"] == v]["File"].tolist() if not res["files"].empty else []
    return {"vendor": v, **{k: summary_row[k] for k in ("weighted_score", "score_pct", "status", "rank", "recommendation",
                                                         "failed_mandatory")},
            "files": files, "summary": summary, "criteria": criteria}


def override_score(eval_id, item_id, vendor, score, reason=""):
    """Replace one score in the workbook (what the dashboard and every download then use). The agent's original
    score stays in the Agent_Log sheet, and each override is recorded in overrides.json."""
    if not 0 <= score <= 5:
        raise ServiceError(400, "score must be between 0 and 5.")
    item_id = item_id.upper()
    with _lock(eval_id):
        path = _xlsx(eval_id)
        res = get_results(eval_id)
        v = _find_vendor(res, vendor)
        layout = scorecard_rows(res["criteria"])
        hit = next(((c, sub, r) for kind, c, sub, r in layout if (sub.id if sub else c.id) == item_id), None)
        if hit is None:
            raise ServiceError(404, f"Unknown criterion or sub-criterion '{item_id}'.")
        c, sub, row = hit
        old = res["sub_scores"][v].get(item_id) if sub else res["scores"][v].get(item_id)
        try:
            wb = load_workbook(path)
            ws = wb["Scorecard"]
            col = next(sc for n, sc, _ in vendor_columns(ws) if n == v)
            ws[f"{col}{row}"] = float(score)
            wb.save(path)
        except PermissionError:
            raise ServiceError(409, "The workbook is open in Excel. Close it and try again.")
        log_file = _eval_dir(eval_id) / "overrides.json"
        log = json.loads(log_file.read_text(encoding="utf-8")) if log_file.exists() else []
        log.append({"time": _now(), "vendor": v, "id": item_id, "old_score": _nz(old), "new_score": float(score),
                    "reason": reason})
        log_file.write_text(json.dumps(log, indent=2), encoding="utf-8")
    _results_cache.pop(eval_id, None)
    ranking = next(r for r in get_ranking(eval_id)["ranking"] if r["vendor"] == v)
    note = (f"{c.id} now uses your number instead of the average of its sub-criteria." if (not sub and c.subs) else "")
    return {"vendor": v, "id": item_id, "old_score": _nz(old), "new_score": float(score), "note": note,
            "vendor_result": ranking}


def get_overrides(eval_id):
    f = _eval_dir(eval_id) / "overrides.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def download(eval_id, fmt, min_score=None, shortlist_n=None):
    """Returns (bytes, file name, mime type). fmt: pdf, docx, csv, json, md or xlsx (the original workbook)."""
    fmt = (fmt or "").lower()
    if fmt != "xlsx" and fmt not in FORMATS:
        raise ServiceError(400, f"Unknown format '{fmt}'. Choose from: {', '.join([*FORMATS, 'xlsx'])}")
    _check_settings(min_score, shortlist_n)
    if fmt == "xlsx":
        return _xlsx(eval_id).read_bytes(), f"{eval_id}_scorecard.xlsx", XLSX_MIME
    res = get_results(eval_id)
    data, name, mime = export(res, compute(res, min_score=min_score, shortlist_n=shortlist_n), fmt)
    return data, f"{eval_id}_{name}", mime


# ---------------------------------------------------------------- chat
def _get_search(eval_id):
    path = chunks_path_for(_xlsx(eval_id))
    if not path.exists():
        return None
    with _search_guard:
        if eval_id not in _search_cache:                  # loads the embedding model: slow the first time only
            _search_cache[eval_id] = ProposalSearch(load_chunks(path))
        return _search_cache[eval_id]


def chat_about(eval_id, question, history=None):
    question = (question or "").strip()
    if not question:
        raise ServiceError(400, "Question is empty.")
    res = get_results(eval_id)
    out = compute(res)
    search = _get_search(eval_id)
    try:
        answer = run_chat(question, history or [], res, out, compute_fn=compute, search=search)
    except ChatError as e:
        raise ServiceError(502, str(e))
    return {"answer": answer, "proposal_search": search.mode if search else None}


# ---------------------------------------------------------------- criteria (view / edit the template)
_CRIT_COLS = {"category": 3, "name": 4, "description": 5, "weight": 6, "mandatory": 7, "anchor_1": 8,
              "anchor_3": 9, "anchor_5": 10, "method": 11, "unit": 12, "target": 13, "floor": 14}
_SUB_COLS = {"id": 2, "parent_id": 3, "name": 5, "description": 6, "weight": 7, "anchor_1": 8, "anchor_3": 9, "anchor_5": 10}


def _criteria_payload(path):
    criteria, min_s, short_n = load_rubric(str(path))
    return {"settings": {"min_score": min_s, "shortlist_n": short_n},
            "criteria": [{"id": c.id, "category": c.category, "name": c.name, "description": c.description,
                          "weight": c.weight, "mandatory": c.mandatory, "method": c.method, "unit": c.unit,
                          "target": c.target, "floor": c.floor, "anchor_1": c.anchor_1, "anchor_3": c.anchor_3,
                          "anchor_5": c.anchor_5,
                          "sub_criteria": [{"id": s.id, "parent_id": s.parent_id, "name": s.name,
                                            "description": s.description, "weight": s.weight,
                                            "anchor_1": s.anchor_1, "anchor_3": s.anchor_3, "anchor_5": s.anchor_5}
                                           for s in c.subs]} for c in criteria]}


def get_criteria():
    try:
        return _criteria_payload(template_path())
    except (ValueError, KeyError) as e:
        raise ServiceError(500, f"The criteria template is invalid: {e}")


def update_criteria(criteria=None, sub_criteria=None):
    """criteria: [{"id": "T4", + any of weight, mandatory, name, description, anchors...}] changes those fields.
    sub_criteria: the COMPLETE new list of sub-criteria (replaces the old one); None leaves them unchanged.
    The change is validated on a copy first; the live template is only replaced if it loads cleanly (old one kept
    as scorecard_template.bak.xlsx). Evaluations already started keep the criteria they began with."""
    src = template_path()
    tmp = src.with_name(src.stem + ".editing.xlsx")
    shutil.copy2(src, tmp)
    try:
        wb = load_workbook(tmp)
        ws = wb["Criteria"]
        rows = {str(ws.cell(r, 2).value): r for r in range(5, 41) if ws.cell(r, 2).value not in (None, "")}
        for ch in criteria or []:
            cid = str(ch.get("id", "")).strip()
            if cid not in rows:
                raise ServiceError(400, f"Unknown criterion id '{cid}'.")
            for key, val in ch.items():
                if key == "id":
                    continue
                if key not in _CRIT_COLS:
                    raise ServiceError(400, f"{cid}: cannot edit '{key}'. Editable: {', '.join(_CRIT_COLS)}.")
                if key == "mandatory":
                    val = "Yes" if val in (True, "Yes", "yes", "YES") else "No"
                ws.cell(rows[cid], _CRIT_COLS[key], val)
        if sub_criteria is not None:
            if len(sub_criteria) > MAX_SUBCRITERIA:
                raise ServiceError(400, f"At most {MAX_SUBCRITERIA} sub-criteria are supported.")
            ss = wb["SubCriteria"]
            for r in range(5, 5 + MAX_SUBCRITERIA):
                for col in _SUB_COLS.values():
                    ss.cell(r, col).value = None
            for i, s in enumerate(sub_criteria):
                unknown = set(s) - set(_SUB_COLS)
                if unknown:
                    raise ServiceError(400, f"Sub-criterion {s.get('id', i + 1)}: unknown field(s) {sorted(unknown)}.")
                for key, col in _SUB_COLS.items():
                    ss.cell(5 + i, col).value = s.get(key) or None if key != "weight" else s.get(key)
        wb.save(tmp)
        try:
            payload = _criteria_payload(tmp)             # validates: weights total 100%, ids exist, numeric rules...
        except ValueError as e:
            raise ServiceError(400, str(e))
        try:
            shutil.copy2(src, src.with_name(src.stem + ".bak.xlsx"))
            os.replace(tmp, src)
        except PermissionError:
            raise ServiceError(409, "The template is open in Excel. Close it and try again.")
        return payload
    finally:
        tmp.unlink(missing_ok=True)


def preview_costs(currency, mock=False):
    """Read each vendor's total price from the uploaded proposals and convert it into `currency` (information only)."""
    from vea.costs import preview_costs as _preview
    from vea.currency import CurrencyError, norm_currency
    try:
        code = norm_currency(currency)
    except CurrencyError as e:
        raise ServiceError(400, str(e))
    if code is None:
        raise ServiceError(400, "Choose a currency such as INR, USD, EUR, GBP or JPY.")
    if not list_vendors():
        raise ServiceError(400, "Upload at least one vendor's files first (POST /vendors/{name}/files).")
    if not mock and not os.getenv("LLM_API_KEY"):
        raise ServiceError(400, "LLM_API_KEY is not set on the server. Put it in .env, or use mock=true.")
    try:
        return _preview(str(_proposals_dir()), code, mock=bool(mock))
    except CurrencyError as e:
        raise ServiceError(400, str(e))
    except (RuntimeError, ValueError) as e:
        raise ServiceError(500, "Could not read the costs: " + str(e))
