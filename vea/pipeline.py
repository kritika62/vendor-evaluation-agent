"""The whole scoring pipeline as one function, used by both the command line (main.py) and the API.

    result = run_evaluation("data/proposals", "output/scorecard.xlsx", rfp="data/rfp/RFP.txt",
                            progress=lambda message, done, total: print(message))

`progress(message, done, total)` is called as work advances (done/total are step counts for a progress bar).
Problems the user can fix (no proposals, bad rubric, missing target...) raise EvaluationError with a plain message.
"""
import json
from pathlib import Path

from .aggregate import aggregate
from .config import Settings
from .ingest import group_vendors, ingest_files, ingest_vendor
from .proposal_index import chunks_path_for, save_chunks
from .report import write_workbook
from .retriever import Retriever
from .rubric import load_rubric
from .scorer import LLMClient, MockLLM, score_numeric, score_with_subs
from .summarize import summarize_vendor
from .targets import TargetError, resolve_targets


class EvaluationError(Exception):
    pass


def costs_path_for_out(out):
    """Where the costs of an evaluation are saved: next to its scorecard."""
    return Path(out).with_name("costs.json")


def read_costs(proposals, code, per_file, mock, llm, settings, vendors):
    """Read each scored vendor's price and contract total in `code`. Never raises: a failure is saved as 'error'."""
    from .costs import preview_costs
    try:
        data = preview_costs(proposals, code, mock=mock, llm=None if mock else llm, settings=settings, per_file=per_file)
        data["vendors"] = [r for r in data["vendors"] if r["vendor"] in vendors]
        data["error"] = ""
    except Exception as e:                                # information only: never fail the evaluation over costs
        data = {"currency": code, "rates_date": "", "mock": bool(mock), "vendors": [],
                "error": f"{type(e).__name__}: {e}"}
    return data


def save_costs(out, costs):
    path = costs_path_for_out(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(costs, indent=2), encoding="utf-8")
    return path


def load_costs(out):
    """The saved costs of an evaluation, or None if it has none (older evaluations)."""
    path = costs_path_for_out(out)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_evaluation(proposals, out, rfp=None, template="template/scorecard_template.xlsx", samples=1,
                   per_file=False, mock=False, confirm=False, interactive=True, target_overrides=None, currency=None,
                   cost_currency=None, progress=None):
    """Score every vendor in `proposals` and write the scorecard workbook to `out`.

    confirm / interactive: ask the user at the keyboard about numeric targets (command line only).
    target_overrides: {criterion_id: number} that replaces the target read from the RFP.
    cost_currency: if set, each vendor's price, contract length and total are read and saved next to the scorecard
    (costs.json) in this currency. Information only: it never changes a score, and a failure never fails the run.
    Returns {"out", "rows" (ranking rows), "vendors", "files", "log_path", "chunks_path"}.
    """
    s = Settings(n_samples=samples, mock=mock)

    def say(msg, step=0):
        say.done += step
        if progress:
            progress(msg, say.done, say.total)
    say.done, say.total = 0, 1

    try:
        criteria, min_mand, shortlist_n = load_rubric(template)
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise EvaluationError(f"Problem with the criteria template: {e}")
    try:
        vendors = group_vendors(proposals, per_file=per_file)
    except FileNotFoundError:
        raise EvaluationError(f"Proposals folder not found: {proposals}")
    if not vendors:
        raise EvaluationError(f"No proposals found in {proposals}")
    for c in criteria:
        if target_overrides and c.id in target_overrides:
            c.target = float(target_overrides[c.id])
    if currency:
        from .currency import CurrencyError, is_currency, norm_currency
        try:
            code = norm_currency(currency)
        except CurrencyError as e:
            raise EvaluationError(str(e))
        if code is None:
            raise EvaluationError("Unknown costing currency '" + str(currency) + "'. Use a code such as INR, USD, EUR, GBP or JPY.")
        for c in criteria:
            if is_currency(c.unit):
                c.unit = code
    cost_code = None
    if cost_currency:
        from .currency import CurrencyError, norm_currency
        try:
            cost_code = norm_currency(cost_currency)
        except CurrencyError as e:
            raise EvaluationError(str(e))
        if cost_code is None:
            raise EvaluationError("Unknown costing currency '" + str(cost_currency) + "'. Use a code such as INR, USD, EUR, GBP or JPY.")
    say.total = 1 + len(vendors) * (len(criteria) + 1) + 1 + (1 if cost_code else 0)
    n_sub = sum(len(c.subs) for c in criteria)
    say(f"{len(vendors)} vendor(s); {len(criteria)} criteria, {n_sub} sub-criteria")
    try:
        llm = MockLLM() if s.mock else LLMClient(s)
    except RuntimeError as e:
        raise EvaluationError(str(e))

    rfp_chunks = []
    if rfp:
        try:
            _, rfp_chunks = ingest_vendor(Path(rfp), s.max_chunk_chars, s.overlap_chars)
        except (ValueError, FileNotFoundError) as e:
            raise EvaluationError(f"Could not read the RFP: {e}")
    try:
        targets = resolve_targets(criteria, rfp_chunks, llm, s, confirm=confirm, interactive=interactive)
    except TargetError as e:
        raise EvaluationError(str(e))
    rfp_text = "\n".join(f"[RFP page {c.page}] {c.text}" for c in rfp_chunks)
    say("Targets resolved", 1)

    scores_by_vendor, files_by_vendor, chunks_by_vendor = {}, {}, {}
    for vendor, paths in vendors.items():
        say(f"\n== {vendor}  ({len(paths)} file{'s' if len(paths) != 1 else ''})")
        try:
            chunks = ingest_files(vendor, paths, s.max_chunk_chars, s.overlap_chars)
        except ValueError as e:
            raise EvaluationError(f"{vendor}: {e}")
        if not chunks:
            say(f"  WARNING: no text could be read from {vendor}'s files; skipping.", len(criteria) + 1)
            continue
        files_by_vendor[vendor] = [p.name for p in paths]
        chunks_by_vendor[vendor] = chunks
        retriever = Retriever(chunks, s)
        scores_by_vendor[vendor] = []
        for c in criteria:
            if c.method == "Judgement":
                others = ", ".join(f"{x.id} {x.name}" for x in criteria if x.id != c.id)
                ctx = rfp_text + f"\n\nOTHER CRITERIA (scored separately, do not judge their topics here): {others}"
                r = score_with_subs(vendor, c, retriever, llm, s, min_mand, rfp_text=ctx)
            else:
                r = score_numeric(vendor, c, retriever, llm, s, targets[c.id], min_mand)
            scores_by_vendor[vendor].append(r)
            lines = [f"  {c.id} {c.name[:34]:<34} {r.score:>4.1f}/5{'  <-- review' if r.needs_review else ''}"]
            lines += [f"      {sub.criterion_id:<5} {sub.criterion[:30]:<30} {sub.score:g}/5"
                      f"{'  <-- review' if sub.needs_review else ''}" for sub in r.subscores]
            say("\n".join(lines), 1)
    if not scores_by_vendor:
        raise EvaluationError("No vendor had readable text.")

    rows = aggregate(criteria, scores_by_vendor, min_mand, shortlist_n)
    summaries = {}
    for v in scores_by_vendor:
        summaries[v] = summarize_vendor(v, scores_by_vendor[v], next(r for r in rows if r["vendor"] == v), llm, s.mock)
        say(f"Summary written for {v}", 1)

    costs, costs_path = None, None
    if cost_code:
        costs = read_costs(proposals, cost_code, per_file, s.mock, llm, s, list(scores_by_vendor))
        say("Costs read" if not costs.get("error") else "Costs could not be read (the evaluation continues)", 1)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if costs is not None:
        costs_path = save_costs(out, costs)
    write_workbook(template, str(out), criteria, scores_by_vendor, summaries, targets, files_by_vendor, costs)
    log_path = out.with_suffix(".log.json")
    log_path.write_text(json.dumps({v: [x.as_dict() for x in sc] for v, sc in scores_by_vendor.items()}, indent=2),
                        encoding="utf-8")
    chunks_path = chunks_path_for(out)
    save_chunks(chunks_path, {v: chunks_by_vendor[v] for v in scores_by_vendor})   # for proposal search in chat
    say("Scorecard saved", 1)
    return {"out": str(out), "rows": rows, "vendors": list(scores_by_vendor), "files": files_by_vendor,
            "log_path": str(log_path), "chunks_path": str(chunks_path),
            "costs": costs, "costs_path": str(costs_path) if costs_path else None}
