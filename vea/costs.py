"""Cost preview: read each vendor's total price and convert it into one chosen currency.
Information only: nothing here changes a score. The AI only reads the amount and the currency exactly as written;
code does the conversion."""
import re

from .config import Settings
from .currency import MONEY_RE, CurrencyError, convert_currency, norm_currency, rates_date
from .ingest import group_vendors, ingest_files
from .numeric import search_hints
from .retriever import Retriever
from .scorer import EXTRACT_VENDOR_PROMPT, MONEY_NOTE, LLMClient, _excerpt_label, _norm, _refs, validate_extract

_MULT = {"lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7, "k": 1e3, "m": 1e6, "mn": 1e6,
         "million": 1e6, "bn": 1e9, "billion": 1e9}


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


def _candidate_chunks(retriever):
    """Passages most likely to state the price: several searches, then any passage that contains an amount."""
    out = []
    for q in ("Total price, cost, fees, licence, subscription, commercials", search_hints("USD"), "Pricing and commercial terms"):
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
    user = ("VALUE WANTED: Total price of the whole proposal (unit: the currency exactly as written in the text)\n\n"
            + "\n\n".join(f"{_excerpt_label(i, c)}\n{c.text}" for i, c in enumerate(chunks, 1)))
    return validate_extract(llm.complete_json(EXTRACT_VENDOR_PROMPT + MONEY_NOTE, user))


def _one(vendor, paths, code, s, llm, mock):
    row = {"vendor": vendor, "found": False, "amount": None, "written_currency": "", "currency": None,
           "converted": None, "target_currency": code, "quote": "", "source": "", "status": "needs review", "note": ""}
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
        shown = chunks
        ex = {"found": False, "value": 0, "unit": "", "evidence_quote": "", "pages": []}
        for c in chunks:
            p = parse_money(c.text)
            if p:
                ex = {"found": True, "value": p["value"], "unit": p["unit"], "evidence_quote": p["quote"], "pages": [c.page]}
                break
    else:
        shown = _candidate_chunks(Retriever(chunks, s))
        try:
            ex = _extract(shown, llm)
        except (ValueError, RuntimeError) as e:
            row["note"] = f"could not read a price: {e}"
            return row
    if not ex["found"]:
        row["note"] = prefix + ("No total price found in the proposal. A yearly or per-user price alone is not "
                                "treated as a total. Check the proposal by hand.")
        return row
    row.update(found=True, amount=ex["value"], written_currency=ex["unit"], quote=ex["evidence_quote"])
    row["source"] = ", ".join(str(r) for r in _refs(shown, ex["evidence_quote"], ex["pages"]))
    ok = True
    try:
        written = norm_currency(ex["unit"])
        if written is None:
            notes.append(f"'{ex['unit']}' is not a recognised currency")
            ok = False
        else:
            row["currency"] = written
            row["converted"] = convert_currency(ex["value"], written, code)
    except CurrencyError as e:
        notes.append(str(e))
        ok = False
    if ex["evidence_quote"] and _norm(ex["evidence_quote"]) not in _norm(" ".join(c.text for c in shown)):
        notes.append("quote not found verbatim in the retrieved text")
        ok = False
    row["status"] = "ok" if ok and not mock else "needs review"
    row["note"] = prefix + "; ".join(notes)
    return row


def preview_costs(proposals, currency, mock=False, llm=None, settings=None):
    """One row per vendor with its total price as written and converted into `currency`."""
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
    rows = [_one(vendor, paths, code, s, llm, mock) for vendor, paths in group_vendors(proposals).items()]
    return {"currency": code, "rates_date": date, "mock": bool(mock), "vendors": rows}
