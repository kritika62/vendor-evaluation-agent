"""Currency handling for cost scoring. No AI involved.

Names and symbols are normalised to ISO codes; amounts are converted through USD using a dated rate table
(rates.json, filled in by a person). Ambiguous inputs ($, the yen/yuan sign, 'dollars') are refused, not guessed."""
import json
import os
import re
from pathlib import Path

CURRENCIES = ("INR", "USD", "EUR", "GBP", "JPY")          # offered in the dropdown
_KNOWN = set(CURRENCIES) | {"CAD", "AUD", "CNY", "SGD", "CHF", "AED", "SAR", "KRW", "ZAR", "NZD"}
_NAMES = {
    "\u20b9": "INR", "rs": "INR", "rs.": "INR", "rupee": "INR", "rupees": "INR",
    "\u20ac": "EUR", "euro": "EUR", "euros": "EUR",
    "\u00a3": "GBP",
    "yen": "JPY", "japanese yen": "JPY",
    "us$": "USD", "us dollar": "USD", "us dollars": "USD",
}
_AMBIGUOUS = {"$", "\u00a5", "dollar", "dollars", "pound", "pounds"}


class CurrencyError(ValueError):
    """A currency problem a person should look at (ambiguous symbol, missing rate). Flagged, never guessed."""


def norm_currency(unit):
    """ISO code for a currency name, symbol or code; None if it is not a currency; CurrencyError if ambiguous."""
    u = (unit or "").strip().lower()
    if not u:
        return None
    if u.upper() in _KNOWN:
        return u.upper()
    if u in _AMBIGUOUS:
        raise CurrencyError(f"ambiguous currency '{unit}': ask the vendor, or use a code such as USD, CAD or JPY")
    return _NAMES.get(u)


def is_currency(unit):
    try:
        return norm_currency(unit) is not None
    except CurrencyError:
        return True


def rates_path():
    return Path(os.getenv("VEA_RATES_FILE") or Path(__file__).resolve().parent.parent / "rates.json")


def load_rates():
    try:
        with open(rates_path(), encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        raise CurrencyError("rates.json not found: create it with the exchange rates (units per 1 USD)")
    except json.JSONDecodeError as e:
        raise CurrencyError(f"rates.json is not valid JSON: {e}")


def rates_date(rates=None):
    return str((rates if rates is not None else load_rates()).get("date", ""))


def convert_currency(amount, from_unit, to_unit, rates=None):
    """Convert an amount between two currencies through USD. Raises CurrencyError if a rate is missing."""
    f, t = norm_currency(from_unit), norm_currency(to_unit)
    if f is None or t is None:
        raise CurrencyError(f"'{from_unit}' or '{to_unit}' is not a currency")
    if f == t:
        return float(amount)
    per_usd = (rates if rates is not None else load_rates()).get("per_usd", {})

    def rate(c):
        if c == "USD":
            return 1.0
        v = per_usd.get(c)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            raise CurrencyError(f"no exchange rate for {c}: fill it in rates.json (units of {c} per 1 USD) and set the date")
        return float(v)

    return float(amount) / rate(f) * rate(t)


# amounts written with a currency symbol/code before them, or a currency word after them
_SYM = r"(?:\u20b9|\u20ac|\u00a3|\u00a5|\$|US\$|Rs\.?|INR|USD|EUR|GBP|JPY|CAD|AUD|CNY|SGD)"
_AMT = r"\d[\d,.]*(?:\s?(?:lakhs?|crores?|million|billion|mn|bn|k|m)\b)?"
_WORD = r"(?:rupees|dollars|euros|pounds|yen|INR|USD|EUR|GBP|JPY)"
MONEY_RE = re.compile(rf"(?:(?<![A-Za-z]){_SYM}\s?{_AMT}|{_AMT}\s?{_WORD}\b)", re.I)
