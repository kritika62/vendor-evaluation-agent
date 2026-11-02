"""Numeric scoring: unit conversion and the scoring formulas (no AI involved)."""
import re

# conversion factors to MONTHS
_TIME = {"day": 12 / 365, "week": 12 / 52, "month": 1.0, "year": 12.0}
_ALIASES = {"days": "day", "d": "day", "weeks": "week", "wk": "week", "wks": "week", "w": "week",
            "months": "month", "mo": "month", "mos": "month", "m": "month",
            "years": "year", "yr": "year", "yrs": "year", "y": "year",
            "%": "percent", "pct": "percent", "percentage": "percent"}


def norm_unit(u):
    u = (u or "").strip().lower().rstrip(".")
    return _ALIASES.get(u, u)


def convert(value, from_unit, to_unit):
    """Convert between time units (day/week/month/year). Other units must match exactly."""
    f, t = norm_unit(from_unit), norm_unit(to_unit)
    if f == t:
        return float(value)
    if f in _TIME and t in _TIME:
        return float(value) * _TIME[f] / _TIME[t]
    raise ValueError(f"cannot convert '{from_unit}' to '{to_unit}'")


def _clamp(x):
    return max(0.0, min(5.0, x))


def score_lower_is_better(actual, target, limit_factor=2.0):
    """Score = 5 x (2 x Target - Actual) / Target, capped to 0-5.
    On/under target -> 5; at 2x target or later -> 0."""
    if target <= 0:
        raise ValueError("target must be greater than 0")
    return _clamp(5 * (limit_factor * target - actual) / target)


def score_higher_is_better(actual, target, floor=0.0):
    """Score = 5 x (Actual - Floor) / (Target - Floor), capped to 0-5."""
    if target <= floor:
        raise ValueError("target must be greater than the floor")
    return _clamp(5 * (actual - floor) / (target - floor))


# ---- finding candidate numbers in text (used when the first search misses a value)
_NUM_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
              "eighteen|twenty|twenty[- ]four|thirty|thirty[- ]six|forty[- ]eight|sixty|ninety")
_NUM = rf"(?:\d+(?:[.,]\d+)?|{_NUM_WORDS})"
_TIME_RE = re.compile(rf"\b{_NUM}[\s-]*(?:(?:business|working|calendar)\s+)?(?:days?|weeks?|wks?|months?|mos?|years?|yrs?)\b", re.I)
_PCT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|per\s?cent\b|percent\b)", re.I)
_HINTS = {"time": "timeline schedule phases duration weeks months go-live delivery implementation plan milestones",
          "percent": "uptime availability SLA percentage guaranteed 99 disaster recovery RTO RPO"}


def unit_pattern(unit):
    """Regex for 'a number with this kind of unit' (e.g. '16 weeks', 'sixteen weeks', '99.9%'), or None."""
    u = norm_unit(unit)
    if u in _TIME:
        return _TIME_RE
    if u == "percent":
        return _PCT_RE
    return None


def search_hints(unit):
    """Extra search words that tend to sit next to this kind of number in a proposal."""
    u = norm_unit(unit)
    return _HINTS["time"] if u in _TIME else _HINTS["percent"] if u == "percent" else ""
