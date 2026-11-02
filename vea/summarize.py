"""Per-vendor evaluation summary (strengths, gaps, risks)."""
from .scorer import parse_json

PROMPT = """You are writing a vendor evaluation summary for engineering and procurement.
Use ONLY the scored criteria below. Be factual and concise.
Return ONLY JSON: {"overview": str (max 2 sentences), "strengths": [str], "gaps": [str], "risks": [str]}
Each list has at most 3 short items. Mention criterion names, not IDs."""


def _facts(scores):
    return "\n".join(f"- {s.criterion}: {s.score}/5. {s.justification}" for s in scores)


def summarize_vendor(vendor, scores, agg_row, llm, mock):
    if mock:
        ordered = sorted(scores, key=lambda s: -s.score)
        return {"overview": f"{vendor}: weighted score {agg_row['weighted_score']:.2f}/5, status {agg_row['status']}. (MOCK summary)",
                "strengths": [f"{s.criterion} ({s.score}/5)" for s in ordered[:3] if s.score >= 3],
                "gaps": [f"{s.criterion} ({s.score}/5)" for s in ordered[::-1][:3] if s.score < 3],
                "risks": [f"Review needed: {s.criterion}" for s in scores if s.needs_review and s.score < 3][:3]}
    user = (f"Vendor: {vendor}\nWeighted score: {agg_row['weighted_score']:.2f}/5\n"
            f"Mandatory status: {agg_row['status']}\nScores:\n{_facts(scores)}")
    out = llm.complete_json(PROMPT, user)
    return {k: out.get(k, [] if k != "overview" else "") for k in ("overview", "strengths", "gaps", "risks")}
