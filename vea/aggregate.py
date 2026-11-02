"""Deterministic maths: weighted totals, mandatory knockout, ranking. No LLM involved."""


def aggregate(criteria, scores_by_vendor, min_mandatory=3, shortlist_n=2):
    rows = []
    for vendor, scores in scores_by_vendor.items():
        by_id = {s.criterion_id: s.score for s in scores}
        total = sum(c.weight * by_id[c.id] for c in criteria)
        failed = [c.id for c in criteria if c.mandatory and by_id[c.id] < min_mandatory]
        rows.append({"vendor": vendor, "weighted_score": total, "pct": total / 5,
                     "status": "Fail" if failed else "Pass", "failed": failed})
    passing = sorted([r for r in rows if r["status"] == "Pass"], key=lambda r: -r["weighted_score"])
    for r in rows:
        r["rank"] = passing.index(r) + 1 if r["status"] == "Pass" else None
        r["recommendation"] = ("Rejected - mandatory criterion not met" if r["status"] == "Fail"
                               else "Shortlist" if r["rank"] <= shortlist_n else "Not shortlisted")
    return rows
