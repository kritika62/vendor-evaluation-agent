"""Cost preview: read each vendor's price, work out the total over the contract, and convert it into one currency.
Information only: nothing here changes a score. The AI only READS the amount, the currency, how the price is quoted
(total / per year / per month) and the contract length, each with a quote. Code does every calculation."""
import re

from .config import Settings
from .currency import MONEY_RE, CurrencyError, convert_currency, norm_currency, rates_date
from .ingest import group_vendors, ingest_files
from .numeric import search_hints
from .retriever import Retriever
from .scorer import LLMClient, _excerpt_label, _norm, _refs

_MULT = {"lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7, "k": 1e3, "m": 1e6, "mn": 1e6,
         "million": 1e6, "bn": 1e9, "billion": 1e9}

BASES = ("total", "per_year", "per_month", "other")
BASIS_LABEL = {"total": "total", "per_year": "per year", "per_month": "per month", "other": "other basis"}
MAX_TERM_YEARS = 30

COST_PROMPT = """You read the PRICE from a vendor proposal. Use ONLY the excerpts. Never estimate, never convert \
currencies, never calculate.
Return ONLY JSON: {"found": bool, "value": number, "unit": str, "basis": "total"|"per_year"|"per_month"|"other", \
"term_value": number or null, "term_unit": "years"|"months"|"", "evidence_quote": str (verbatim, max 25 words), \
"term_quote": str (verbatim, max 25 words, "" if no term), "pages": [int]}
Rules:
- "value" is the full amount as a plain number: 900000, not "9 lakh", "900K" or "0.9M". 1 lakh = 100000 and \
1 crore = 10000000.
- "unit" is the currency exactly as the text writes it: a code such as INR, USD, EUR or JPY, or a symbol or word. If \
the text only shows a dollar sign or a yen/yuan sign with no country, return that sign as written. NEVER guess.
- "basis": "total" if the amount covers the whole contract; "per_year" if it is yearly or annual; "per_month" if it is \
monthly; "other" for per-user, per-transaction, per-unit or anything unclear.
- If several prices are given, choose the overall price of the main offer, not optional extras.
- "term_value" and "term_unit": the contract or subscription length if the proposal states it (for example 3 years \
or 36 months). If it is not stated, use null and "". Do not work it out from other numbers.
- If no price is stated, set "found": false."""


def parse_money(text):
    """Offline reading (no AI): the first amount with a currency in the text, or None."""
    m = MONEY_RE.search(text)
    if not m:
        return None
    s = m.group(0)
    num = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    if not num:
        return None
    value = float(num.group(0).replace(",", ""))
    mult = re.search(r"\b(lakhs?|crores?|mn|bn|million|billion|k|m)\b", s[num.end():], re.I)
    if mult:
        value *= _MULT[mult.group(1).lower()]
    cur = re.search(r"US\$|\u20b9|\u20ac|\u00a3|\u00a5|\$|Rs\.?|INR|USD|EUR|GBP|JPY|CAD|AUD|CNY|SGD|rupees|dollars|euros|pounds|yen", s, re.I)
    if not cur:
        return None
    return {"value": value, "unit": cur.group(0), "quote": s}


_YEARLY = re.compile(r"\bper\s+(?:year|annum)\b|\ba\s+year\b|\bannual(?:ly)?\b|\byearly\b|/\s*(?:yr|year)\b", re.I)
_MONTHLY = re.compile(r"\bper\s+month\b|\ba\s+month\b|\bmonthly\b|/\s*(?:mo|month)\b", re.I)
_TERM = re.compile(r"(\d+(?:\.\d+)?)[\s-]*(years?|yrs?|months?)\b", re.I)


def _mock_read(chunks):
    """Offline reading with simple patterns: first amount, its basis from nearby words, a numeric contract length."""
    for c in chunks:
        m = MONEY_RE.search(c.text)
        p = parse_money(c.text)
        if not (m and p):
            continue
        window = c.text[max(0, m.start() - 40): m.end() + 60]
        basis = "per_year" if _YEARLY.search(window) else "per_month" if _MONTHLY.search(window) else "total"
        t = _TERM.search(c.text)
        term_value, term_unit, term_quote = None, "", ""
        if t:
            term_value = float(t.group(1))
            term_unit = "years" if t.group(2).lower().startswith(("y")) else "months"
            term_quote = t.group(0)
        return {"found": True, "value": p["value"], "unit": p["unit"], "basis": basis, "term_value": term_value,
                "term_unit": term_unit, "evidence_quote": p["quote"], "term_quote": term_quote, "pages": [c.page]}
    return {"found": False, "value": 0, "unit": "", "basis": "other", "term_value": None, "term_unit": "",
            "evidence_quote": "", "term_quote": "", "pages": []}


def validate_cost(p):
    """Check the AI's answer. Missing or odd fields are repaired to a safe value; a bad core field raises ValueError."""
    for k in ("found", "value", "unit", "basis", "evidence_quote", "pages"):
        if k not in p:
            raise ValueError(f"missing key: {k}")
    if not isinstance(p["found"], bool):
        raise ValueError("'found' must be true or false")
    if p["found"]:
        if isinstance(p["value"], bool) or not isinstance(p["value"], (int, float)):
            raise ValueError("'value' must be a number")
        if not isinstance(p["unit"], str) or not p["unit"].strip():
            raise ValueError("'unit' must be a currency such as INR")
    p["basis"] = p["basis"] if p["basis"] in BASES else "other"
    tv = p.get("term_value")
    tu = str(p.get("term_unit") or "").strip().lower()
    tu = "years" if tu.startswith(("year", "yr")) else "months" if tu.startswith("month") else ""
    if isinstance(tv, bool) or not isinstance(tv, (int, float)) or tv <= 0 or not tu:
        tv, tu = None, ""
    p["term_value"], p["term_unit"] = (float(tv) if tv is not None else None), tu
    p["term_quote"] = str(p.get("term_quote") or "") if tv is not None else ""
    p["pages"] = [x for x in p["pages"] if isinstance(x, int)] if isinstance(p["pages"], list) else []
    p["evidence_quote"] = str(p["evidence_quote"] or "")
    return p


def contract_total(amount, basis, term_value, term_unit):
    """The total over the contract, worked out in code. Returns (total or None, term in years or None, reason or '')."""
    if basis == "total":
        return float(amount), None, ""
    if basis == "other":
        return None, None, "the price basis is unclear (per user, per unit or similar), so no total was calculated"
    if term_value is None:
        return None, None, "contract length not stated, so the total over the contract was not calculated"
    years = term_value if term_unit == "years" else term_value / 12.0
    if years <= 0 or years > MAX_TERM_YEARS:
        return None, years, f"contract length of {years:g} years looks wrong, so no total was calculated"
    if basis == "per_year":
        return float(amount) * years, years, ""
    return float(amount) * years * 12.0, years, ""          # per_month


def _candidate_chunks(retriever):
    """Passages most likely to state the price and the contract length: several searches, then any amount."""
    out = []
    for q in ("Total price, cost, fees, licence, subscription, commercials", search_hints("USD"),
              "Pricing and commercial terms", "Contract term, duration, subscription period, years, months"):
        for c in retriever.search(q, k=3):
            if c not in out:
                out.append(c)
    for c in retriever.chunks:
        if len(out) >= 10:
            break
        if c not in out and MONEY_RE.search(c.text):
            out.append(c)
    return out[:10]


def _extract(chunks, llm):
    user = ("VALUE WANTED: the price of the proposal, how it is quoted (total, per year or per month) and the contract "
            "length if stated\n\n" + "\n\n".join(f"{_excerpt_label(i, c)}\n{c.text}" for i, c in enumerate(chunks, 1)))
    return validate_cost(llm.complete_json(COST_PROMPT, user))


def _blank_row(vendor, code):
    return {"vendor": vendor, "found": False, "amount": None, "written_currency": "", "currency": None,
            "converted": None, "target_currency": code, "basis": "", "term_years": None, "total": None,
            "converted_total": None, "quote": "", "term_quote": "", "source": "", "status": "needs review", "note": ""}


def _one(vendor, paths, code, s, llm, mock):
    row = _blank_row(vendor, code)
    try:
        chunks = ingest_files(vendor, paths, s.max_chunk_chars, s.overlap_chars)
    except ValueError as e:
        row["note"] = f"could not read the files: {e}"
        return row
    if not chunks:
        row["note"] = "no text could be read from the files (a scanned PDF needs OCR first)"
        return row
    prefix, notes = "", []
    if mock:
        prefix = "offline test reading (no AI, first amount found): "
        shown, ex = chunks, _mock_read(chunks)
    else:
        shown = _candidate_chunks(Retriever(chunks, s))
        try:
            ex = _extract(shown, llm)
        except (ValueError, RuntimeError) as e:
            row["note"] = f"could not read a price: {e}"
            return row
    if not ex["found"]:
        row["note"] = prefix + ("No price found in the proposal. Check the proposal by hand.")
        return row
    row.update(found=True, amount=ex["value"], written_currency=ex["unit"], quote=ex["evidence_quote"],
               basis=ex["basis"], term_quote=ex["term_quote"])
    refs = _refs(shown, ex["evidence_quote"], ex["pages"])
    if ex["term_quote"]:
        refs = list(refs) + [r for r in _refs(shown, ex["term_quote"], ex["pages"]) if r not in refs]
    row["source"] = ", ".join(str(r) for r in refs)
    ok = True
    total, years, why = contract_total(ex["value"], ex["basis"], ex["term_value"], ex["term_unit"])
    row["total"], row["term_years"] = total, years
    if why:
        notes.append(why)
        ok = False
    try:
        written = norm_currency(ex["unit"])
        if written is None:
            notes.append(f"'{ex['unit']}' is not a recognised currency")
            ok = False
        else:
            row["currency"] = written
            row["converted"] = convert_currency(ex["value"], written, code)
            if total is not None:
                row["converted_total"] = convert_currency(total, written, code)
    except CurrencyError as e:
        notes.append(str(e))
        ok = False
    shown_text = _norm(" ".join(c.text for c in shown))
    for q in (ex["evidence_quote"], ex["term_quote"]):
        if q and _norm(q) not in shown_text:
            notes.append("quote not found verbatim in the retrieved text")
            ok = False
            break
    row["status"] = "ok" if ok and not mock else "needs review"
    row["note"] = prefix + "; ".join(notes)
    return row


def preview_costs(proposals, currency, mock=False, llm=None, settings=None, per_file=False):
    """One row per vendor: price as written, total over the contract, both converted into `currency`."""
    code = norm_currency(currency)                      # CurrencyError if ambiguous
    if code is None:
        raise CurrencyError(f"'{currency}' is not a currency; use a code such as INR, USD, EUR, GBP or JPY")
    s = settings or Settings(mock=mock)
    if not mock and llm is None:
        llm = LLMClient(s)
    try:
        date = rates_date()
    except CurrencyError:
        date = ""
    rows = [_one(vendor, paths, code, s, llm, mock) for vendor, paths in group_vendors(proposals, per_file=per_file).items()]
    return {"currency": code, "rates_date": date, "mock": bool(mock), "vendors": rows}
