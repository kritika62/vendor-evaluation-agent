"""Resolve the target value for each numeric criterion: Criteria-sheet override, else the RFP."""
from .scorer import extract_value, _norm
from .retriever import Retriever


class TargetError(ValueError):
    """A numeric target is missing and cannot be asked for (non-interactive run)."""


def resolve_targets(criteria, rfp_chunks, llm, settings, confirm=True, interactive=True):
    """Returns {criterion_id: {"value", "unit", "source", "quote"}} for numeric criteria."""
    numeric = [c for c in criteria if c.method != "Judgement"]
    targets = {}
    if not numeric:
        return targets
    retriever = Retriever(rfp_chunks, settings) if rfp_chunks else None
    print("\n== Targets for numeric criteria")
    for c in numeric:
        t = None
        if c.target is not None:
            t = {"value": c.target, "unit": c.unit, "source": "Criteria sheet (manual)", "quote": ""}
        elif retriever is not None:
            chunks = retriever.search(f"{c.name}. {c.description}. requirement deadline")
            ex = extract_value("rfp", c, chunks, llm, settings)
            if ex["found"]:
                from .numeric import convert
                try:
                    v = convert(ex["value"], ex["unit"], c.unit)
                    page = ",".join(map(str, ex["pages"])) or "-"
                    t = {"value": v, "unit": c.unit, "source": f"RFP page {page}", "quote": ex["evidence_quote"]}
                except ValueError:
                    pass
        if t is None:
            print(f"  {c.id} {c.name}: no target found in the RFP.")
            if not interactive:
                raise TargetError(f"No target found for {c.id} {c.name} ({c.unit}). Upload an RFP that states it, "
                                  f"type a number in the Target column of the Criteria sheet, or pass it as a "
                                  f"target override.")
            t = _ask(c, None)
        elif confirm and interactive:
            t = _ask(c, t)
        else:
            print(f"  {c.id} {c.name}: target {t['value']:g} {t['unit']}  ({t['source']})")
        targets[c.id] = t
    return targets


def _ask(c, t):
    if t:
        print(f"  {c.id} {c.name}: target {t['value']:g} {t['unit']}  ({t['source']}) \"{t['quote']}\"")
        ans = input(f"     Press Enter to accept, or type the correct number of {c.unit}: ").strip()
        if not ans:
            return t
    else:
        ans = input(f"     Type the target in {c.unit} (or set it in the Criteria sheet): ").strip()
    try:
        return {"value": float(ans), "unit": c.unit, "source": "entered by user", "quote": ""}
    except ValueError:
        raise SystemExit("A numeric target is required. Re-run with --rfp, or fill the Target column on the Criteria sheet.")
