"""Load the evaluation rubric from the 'Criteria' sheet of the scorecard workbook."""
from dataclasses import dataclass, field
from openpyxl import load_workbook


@dataclass
class SubCriterion:
    """A finer-grained question under one criterion (e.g. T4 Security -> T4.2 Encryption)."""
    id: str
    parent_id: str
    name: str
    description: str
    weight: float                   # share of the parent criterion, 0-1
    anchor_1: str = ""
    anchor_3: str = ""
    anchor_5: str = ""
    mandatory: bool = False         # duck-types as a Criterion for the scorer
    row: int = 0                    # row on the SubCriteria sheet (for live formulas)


@dataclass
class Criterion:
    id: str
    category: str
    name: str
    description: str
    weight: float
    mandatory: bool
    anchor_1: str
    anchor_3: str
    anchor_5: str
    method: str = "Judgement"      # Judgement | Lower is better | Higher is better
    unit: str = ""
    target: float = None            # optional manual override (else read from the RFP)
    floor: float = 0.0              # higher-is-better only
    subs: list = field(default_factory=list)   # SubCriterion list (Judgement criteria only)


def load_rubric(xlsx_path: str):
    """Returns (criteria, min_mandatory_score, shortlist_size)."""
    wb = load_workbook(xlsx_path)  # formulas kept; we only read inputs
    ws = wb["Criteria"]
    criteria = []
    for row in ws.iter_rows(min_row=5, min_col=2, max_col=14, values_only=True):
        if row[0] is None:
            break
        cid, cat, name, desc, weight, mand, a1, a3, a5, method, unit, target, floor = row
        method = (method or "Judgement").strip()
        if method not in ("Judgement", "Lower is better", "Higher is better"):
            raise ValueError(f"{cid}: unknown Method '{method}'")
        if method != "Judgement" and not unit:
            raise ValueError(f"{cid}: numeric criteria need a Unit (e.g. months)")
        criteria.append(Criterion(str(cid), cat, name, desc or "", float(weight),
                                  str(mand).strip().lower() == "yes", a1 or "", a3 or "", a5 or "",
                                  method, str(unit or "").strip(),
                                  float(target) if target not in (None, "") else None,
                                  float(floor) if floor not in (None, "") else 0.0))
    total = sum(c.weight for c in criteria)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Criteria weights must total 100% (currently {total:.0%}). Fix the 'Criteria' sheet.")
    _load_subs(wb, criteria)
    summ = wb["Summary"]
    return criteria, int(summ["C5"].value), int(summ["C6"].value)


def _load_subs(wb, criteria):
    """Attach SubCriterion rows from the optional 'SubCriteria' sheet to their parent criteria."""
    if "SubCriteria" not in wb.sheetnames:
        return
    by_id = {c.id: c for c in criteria}
    ws = wb["SubCriteria"]
    seen = set()
    for r, row in enumerate(ws.iter_rows(min_row=5, min_col=2, max_col=10, values_only=True), start=5):
        sid, pid, _parent_name, name, desc, weight, a1, a3, a5 = row
        if sid in (None, "") and pid in (None, ""):
            continue                                   # blank row
        sid, pid = str(sid or "").strip(), str(pid or "").strip()
        if not sid or not pid or not name:
            raise ValueError(f"SubCriteria row '{sid or pid}': Sub-ID, Criterion ID and Sub-criterion are all required.")
        if sid in seen:
            raise ValueError(f"SubCriteria: duplicate Sub-ID '{sid}'.")
        seen.add(sid)
        if pid not in by_id:
            raise ValueError(f"SubCriteria {sid}: Criterion ID '{pid}' is not on the Criteria sheet.")
        parent = by_id[pid]
        if parent.method != "Judgement":
            raise ValueError(f"SubCriteria {sid}: {pid} uses '{parent.method}', so it cannot have sub-criteria "
                             "(its score comes from a formula on one number).")
        if weight in (None, ""):
            raise ValueError(f"SubCriteria {sid}: weight is required.")
        parent.subs.append(SubCriterion(sid, pid, str(name), str(desc or ""), float(weight),
                                        a1 or "", a3 or "", a5 or "", False, r))
    for c in criteria:
        if c.subs:
            total = sum(s.weight for s in c.subs)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"SubCriteria for {c.id} {c.name}: weights total {total:.0%}, must be 100%.")
