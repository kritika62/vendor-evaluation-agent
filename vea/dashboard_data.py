"""Data layer for the dashboard. Reads the scorecard workbook the agent wrote
(so human overrides made in Excel are picked up) and recomputes totals in Python."""
from collections import namedtuple
from dataclasses import replace

import pandas as pd
from openpyxl import load_workbook

from .aggregate import aggregate
from .report import scorecard_rows, vendor_columns
from .rubric import load_rubric

_S = namedtuple("_S", "criterion_id score")


def load_results(xlsx):
    """xlsx: a path or file-like object. Returns a dict of everything the dashboard needs.
    Criterion scores that have sub-criteria are recomputed here from the sub-scores (so human overrides of a
    sub-score count even when the workbook has never been opened in Excel); a plain number typed over a
    criterion's formula cell is treated as an override of the whole criterion."""
    criteria, min_s, shortlist_n = load_rubric(xlsx)
    wb = load_workbook(xlsx)
    sc = wb["Scorecard"]
    layout = scorecard_rows(criteria)
    scores, sub_scores, evidence = {}, {}, {}
    for name, scol, ecol in vendor_columns(sc):
        scores[name], sub_scores[name], evidence[name] = {}, {}, {}
        for kind, c, sub, r in layout:
            val, ev = sc[f"{scol}{r}"].value, sc[f"{ecol}{r}"].value
            if kind == "S":
                sub_scores[name][sub.id] = val
                evidence[name][sub.id] = ev
                continue
            evidence[name][c.id] = ev
            if c.subs and (val is None or (isinstance(val, str) and val.startswith("="))):
                val = ("__subs__", c.id)
            scores[name][c.id] = val
        for c in criteria:                       # resolve criterion scores that come from their sub-scores
            if isinstance(scores[name].get(c.id), tuple):
                parts = [sub_scores[name].get(s.id) for s in c.subs]
                scores[name][c.id] = (None if any(p is None or isinstance(p, str) for p in parts)
                                      else round(sum(s.weight * float(p) for s, p in zip(c.subs, parts)), 2))

    def sheet_df(name):
        if name not in wb.sheetnames:
            return pd.DataFrame()
        rows = list(wb[name].iter_rows(values_only=True))
        return pd.DataFrame(rows[1:], columns=rows[0]) if rows else pd.DataFrame()

    return {"criteria": criteria, "min_score": min_s, "shortlist_n": shortlist_n, "scores": scores,
            "sub_scores": sub_scores, "evidence": evidence, "log": sheet_df("Agent_Log"),
            "summaries": sheet_df("Vendor_Summaries"), "targets": sheet_df("Targets"),
            "files": sheet_df("Vendor_Files")}


def compute(res, weights=None, min_score=None, shortlist_n=None):
    """Recompute ranking / category tables. weights: {criterion_id: weight} (rescaled to total 1)."""
    criteria = res["criteria"]
    if weights:
        total = sum(weights.values()) or 1.0
        criteria = [replace(c, weight=weights.get(c.id, c.weight) / total) for c in criteria]
    min_score = res["min_score"] if min_score is None else min_score
    shortlist_n = res["shortlist_n"] if shortlist_n is None else shortlist_n

    complete = {v: [_S(cid, float(s)) for cid, s in sc.items()]
                for v, sc in res["scores"].items() if all(s is not None for s in sc.values())}
    rows = aggregate(criteria, complete, min_score, shortlist_n) if complete else []
    out = []
    for v in res["scores"]:
        r = next((x for x in rows if x["vendor"] == v), None)
        if r is None:
            out.append({"Vendor": v, "Weighted score": None, "Score %": None, "Status": "Incomplete",
                        "Rank": None, "Recommendation": "Complete scoring first", "Failed mandatory": ""})
        else:
            out.append({"Vendor": v, "Weighted score": round(r["weighted_score"], 2), "Score %": round(r["pct"] * 100, 1),
                        "Status": r["status"], "Rank": r["rank"], "Recommendation": r["recommendation"],
                        "Failed mandatory": ", ".join(r["failed"])})
    summary = pd.DataFrame(out)
    summary = summary.sort_values(["Rank", "Weighted score"], ascending=[True, False], na_position="last").reset_index(drop=True)

    crit_df = pd.DataFrame({v: {f"{c.id} {c.name}": sc[c.id] for c in criteria} for v, sc in res["scores"].items()})
    cats = []
    for cat in dict.fromkeys(c.category for c in criteria):
        cc = [c for c in criteria if c.category == cat]
        w = sum(c.weight for c in cc)
        cats.append({"Category": cat, **{v: (sum(c.weight * sc[c.id] for c in cc) / w
                                              if all(sc[c.id] is not None for c in cc) else None)
                                         for v, sc in res["scores"].items()}})
    cat_df = pd.DataFrame(cats).set_index("Category")

    flagged = pd.DataFrame()
    if not res["log"].empty and "Needs review" in res["log"].columns:
        flagged = res["log"][res["log"]["Needs review"] == "Yes"][
            ["Vendor", "ID", "Criterion", "Level", "Score", "Review reason", "Justification"]].reset_index(drop=True)
    return {"summary": summary, "criteria_scores": crit_df, "categories": cat_df, "flagged": flagged,
            "criteria": criteria, "min_score": min_score, "shortlist_n": shortlist_n}
