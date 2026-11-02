"""Evidence-grounded LLM scoring of one criterion for one vendor."""
import json
import re
import statistics
import time
from dataclasses import dataclass, field, asdict

from .numeric import convert, score_higher_is_better, score_lower_is_better, search_hints, unit_pattern

SYSTEM_PROMPT = """You are a rigorous technical evaluator scoring a vendor proposal.
Rules:
1. Use ONLY the numbered excerpts provided. Never use outside knowledge about the vendor.
2. If the excerpts do not address the criterion, set "addressed": false and "score": 0. Do not guess.
3. Score 0-5 using the anchors: 1 = weak, 3 = meets requirement, 5 = exceeds. 0 = not addressed.
   Marketing claims without specifics score lower than concrete, verifiable commitments.
4. "evidence_quote" must be copied VERBATIM from an excerpt (max 25 words).
5. "pages" lists the page numbers of the excerpts you relied on.
6. If an RFP REQUIREMENTS section is given, it defines what "requirements" and "must-haves" mean in the anchors.
   Judge ONLY the RFP requirements that belong to the CRITERION being scored. Other requirements are scored
   under other criteria, so do not mention them or penalise the vendor for them here.
   Do not penalise a vendor for features the RFP does not ask for.
   The RFP is context only: evidence and quotes must still come from the proposal excerpts.
7. Excerpts may come from several files of the same vendor (PDF, Word, PowerPoint, Excel). If files contradict each
   other, score the weaker commitment and say so in the justification.
8. If a SUB-CRITERION is given, score ONLY that sub-criterion. The parent criterion is context, not the question.
Return ONLY a JSON object with exactly these keys:
{"addressed": bool, "score": int 0-5, "confidence": "high"|"medium"|"low",
 "justification": str (max 2 sentences), "evidence_quote": str, "pages": [int]}"""


@dataclass
class CriterionScore:
    vendor: str
    criterion_id: str
    criterion: str
    score: float
    confidence: str
    justification: str
    evidence_quote: str
    pages: list = field(default_factory=list)
    needs_review: bool = False
    review_reason: str = ""
    sources: list = field(default_factory=list)     # e.g. ["Orion.pdf p.2", "Orion.pptx p.5"]
    subscores: list = field(default_factory=list)   # CriterionScore per sub-criterion (parent scores only)
    parent_id: str = ""                             # set on sub-criterion scores

    def as_dict(self):
        return asdict(self)


def _excerpt_label(i, c):
    src = f" | File: {c.source}" if getattr(c, "source", "") else ""
    return f"[Excerpt {i}{src} | Page {c.page}]"


def build_user_prompt(criterion, chunks, rfp_text="", parent=None):
    ex = "\n\n".join(f"{_excerpt_label(i, c)}\n{c.text}" for i, c in enumerate(chunks, 1))
    rfp = f"RFP REQUIREMENTS (context only):\n{rfp_text}\n\n" if rfp_text else ""
    if parent is not None:
        head = (f"PARENT CRITERION (context only): {parent.name} - {parent.description}\n"
                f"SUB-CRITERION TO SCORE: {criterion.name}\nWHAT TO LOOK FOR: {criterion.description}\n")
        a1 = criterion.anchor_1 or "weak, vague or marketing-only for this sub-criterion"
        a3 = criterion.anchor_3 or "meets the requirement for this sub-criterion with specifics"
        a5 = criterion.anchor_5 or "clearly exceeds the requirement, with verifiable detail"
    else:
        head = f"CRITERION: {criterion.name}\nWHAT TO LOOK FOR: {criterion.description}\n"
        a1, a3, a5 = criterion.anchor_1, criterion.anchor_3, criterion.anchor_5
    return (f"{rfp}{head}ANCHORS:\n  1 (weak): {a1}\n  3 (meets): {a3}\n  5 (exceeds): {a5}\n\n"
            f"EXCERPTS:\n{ex if ex else '(none retrieved)'}")


def _refs(chunks, quote, pages):
    """Source references ('file p.N') for the chunks that contain the quote; else for the cited pages."""
    def ref(c):
        return f"{c.source} p.{c.page}" if getattr(c, "source", "") else f"p.{c.page}"
    q = _norm(quote or "")
    hit = [c for c in chunks if q and q in _norm(c.text)] if q else []
    if not hit:
        hit = [c for c in chunks if c.page in set(pages or [])]
    return sorted(dict.fromkeys(ref(c) for c in hit))


def validate(payload: dict) -> dict:
    """Schema check on the model's JSON. Raises ValueError with a message the model can act on."""
    required = {"addressed", "score", "confidence", "justification", "evidence_quote", "pages"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")
    if not isinstance(payload["addressed"], bool):
        raise ValueError("'addressed' must be true or false")
    if isinstance(payload["score"], bool) or not isinstance(payload["score"], int) or not 0 <= payload["score"] <= 5:
        raise ValueError("'score' must be an integer from 0 to 5")
    if payload["confidence"] not in ("high", "medium", "low"):
        raise ValueError("'confidence' must be high, medium or low")
    if not isinstance(payload["pages"], list) or not all(isinstance(p, int) for p in payload["pages"]):
        raise ValueError("'pages' must be a list of integers")
    payload["justification"] = str(payload["justification"])
    payload["evidence_quote"] = str(payload["evidence_quote"] or "")
    return payload


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object found")
    return json.loads(m.group(0))


def _norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


class LLMClient:
    """Any OpenAI-compatible chat API (Groq, OpenAI, Azure OpenAI via base_url, Ollama...)."""

    def __init__(self, settings):
        from openai import OpenAI
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY is not set. Copy .env.example to .env and fill it in, or run with --mock.")
        self.client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
        self.model = settings.llm_model

    def _call(self, messages, temperature, max_waits=12):
        """One API call. On a 429 rate-limit error, wait the time Groq asks for, then retry."""
        for attempt in range(max_waits):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=temperature,
                    response_format={"type": "json_object"})
                return resp.choices[0].message.content
            except Exception as e:
                if getattr(e, "status_code", None) != 429 or attempt == max_waits - 1:
                    raise
                m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", str(e))
                wait = 10.0
                if m:
                    wait = int(m.group(1) or 0) * 60 + float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
                wait += 2
                print(f"    rate limit reached, waiting {wait:.0f}s ...")
                time.sleep(wait)

    def complete_json(self, system, user, temperature=0.0, retries=3):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err = None
        for _ in range(retries):
            raw = self._call(messages, temperature)
            try:
                return parse_json(raw)
            except Exception as e:  # malformed JSON: tell the model and retry
                last_err = e
                messages += [{"role": "assistant", "content": raw},
                             {"role": "user", "content": f"Invalid JSON ({e}). Return only the JSON object."}]
        raise RuntimeError(f"LLM did not return valid JSON: {last_err}")


EXTRACT_VENDOR_PROMPT = """You extract ONE committed numeric value from a vendor proposal.
Use ONLY the excerpts. If the proposal states several phases, give the TOTAL end-to-end value.
Never estimate; if no value is stated, set "found": false.
Return ONLY JSON: {"found": bool, "value": number, "unit": str, "evidence_quote": str (verbatim, max 25 words), "pages": [int]}"""

EXTRACT_RFP_PROMPT = """You extract ONE requirement value (the target) from a Request for Proposal (RFP).
Use ONLY the excerpts. If no explicit requirement is stated, set "found": false. Never guess.
Return ONLY JSON: {"found": bool, "value": number, "unit": str, "evidence_quote": str (verbatim, max 25 words), "pages": [int]}"""


def validate_extract(p: dict) -> dict:
    for k in ("found", "value", "unit", "evidence_quote", "pages"):
        if k not in p:
            raise ValueError(f"missing key: {k}")
    if not isinstance(p["found"], bool):
        raise ValueError("'found' must be true or false")
    if p["found"]:
        if isinstance(p["value"], bool) or not isinstance(p["value"], (int, float)):
            raise ValueError("'value' must be a number")
        if not isinstance(p["unit"], str) or not p["unit"].strip():
            raise ValueError("'unit' must be text such as months")
    p["pages"] = [x for x in p["pages"] if isinstance(x, int)] if isinstance(p["pages"], list) else []
    p["evidence_quote"] = str(p["evidence_quote"] or "")
    return p


def extract_value(kind, criterion, chunks, llm, settings):
    """Ask the LLM (or mock) to pull one number + unit out of the excerpts."""
    if settings.mock:
        return validate_extract(llm.extract(criterion, chunks))
    system = EXTRACT_VENDOR_PROMPT if kind == "vendor" else EXTRACT_RFP_PROMPT
    user = (f"VALUE WANTED: {criterion.name} - {criterion.description} (unit expected: {criterion.unit})\n\n"
            + "\n\n".join(f"{_excerpt_label(i, c)}\n{c.text}" for i, c in enumerate(chunks, 1)))
    return validate_extract(llm.complete_json(system, user))


class MockLLM:
    """Offline stand-in so the pipeline can be tested without an API key. NOT real scoring:
    the score is just the share of criterion keywords found in the retrieved text."""
    STOP = set("and the of to in for a an with on or is are be as by at from that this".split())

    def extract(self, criterion, chunks):
        """Mock: first 'N weeks/months/years/days' found in the retrieved text."""
        text = " ".join(c.text for c in chunks)
        m = re.search(r"(\d+(?:\.\d+)?)[\s-]*(days?|weeks?|months?|years?)\b", text, flags=re.I)
        if not m:
            return {"found": False, "value": 0, "unit": "", "evidence_quote": "", "pages": []}
        page = next((c.page for c in chunks if m.group(0) in c.text), chunks[0].page)
        return {"found": True, "value": float(m.group(1)), "unit": m.group(2).lower(),
                "evidence_quote": m.group(0), "pages": [page]}

    def score(self, criterion, chunks):
        words = {w for w in _norm(criterion.name + " " + criterion.description).split()
                 if w not in self.STOP and len(w) > 3}
        text = _norm(" ".join(c.text for c in chunks))
        hit = sum(1 for w in words if w in text)
        ratio = hit / max(len(words), 1)
        score = 0 if not chunks or hit == 0 else max(1, min(5, round(ratio * 6)))
        pages = sorted({c.page for c in chunks[:2]})
        quote = " ".join(chunks[0].text.split()[:20]) if chunks else ""
        return {"addressed": score > 0, "score": score, "confidence": "low",
                "justification": f"MOCK: {hit}/{len(words)} criterion keywords found in retrieved text.",
                "evidence_quote": quote, "pages": pages}


def score_criterion(vendor, criterion, retriever, llm, settings, min_mandatory=3, rfp_text="", parent=None):
    query = f"{criterion.name}. {criterion.description}"
    if parent is not None:
        query = f"{parent.name}. {query}"
    chunks = retriever.search(query)
    allowed_pages = {c.page for c in chunks}
    evidence_text = _norm(" ".join(c.text for c in chunks))
    results = []
    for i in range(settings.n_samples):
        if settings.mock:
            payload = llm.score(criterion, chunks)
        else:
            temp = 0.0 if settings.n_samples == 1 else 0.4
            payload = llm.complete_json(SYSTEM_PROMPT, build_user_prompt(criterion, chunks, rfp_text, parent), temperature=temp)
        results.append(validate(payload))

    scores = [r["score"] for r in results]
    final = int(round(statistics.median(scores)))
    rep = min(results, key=lambda r: abs(r["score"] - final))
    reasons = []
    if not rep["addressed"] or final == 0:
        final = 0
        reasons.append("not addressed in proposal")
    if rep["confidence"] == "low" and not settings.mock:
        reasons.append("low model confidence")
    if rep["addressed"] and rep["evidence_quote"] and _norm(rep["evidence_quote"]) not in evidence_text:
        reasons.append("quote not found verbatim in retrieved text")
    if rep["addressed"] and not (set(rep["pages"]) & allowed_pages):
        reasons.append("cited page not in retrieved evidence")
    if max(scores) - min(scores) >= 2:
        reasons.append(f"samples disagree ({min(scores)}-{max(scores)})")
    if criterion.mandatory and final < min_mandatory:
        reasons.append("mandatory criterion below minimum")
    if settings.mock:
        reasons.append("mock scorer (offline test)")
    return CriterionScore(vendor, criterion.id, criterion.name, final, rep["confidence"],
                          rep["justification"], rep["evidence_quote"], sorted(set(rep["pages"])),
                          bool(reasons), "; ".join(reasons),
                          _refs(chunks, rep["evidence_quote"], rep["pages"]), [], parent.id if parent else "")


def _numeric_chunks(criterion, retriever):
    """Passages likely to hold the number: several differently-worded searches, merged (max 8)."""
    hints = search_hints(criterion.unit)
    queries = [f"{criterion.name}. {criterion.description}"]
    queries += [f"{criterion.name} {hints}", hints] if hints else [f"{criterion.name} {criterion.unit} target commitment"]
    out = []
    for q in queries:
        for c in retriever.search(q, k=3):
            if c not in out:
                out.append(c)
    return out[:8]


def score_numeric(vendor, criterion, retriever, llm, settings, target, min_mandatory=3):
    """Numeric criterion: the AI only extracts the vendor's value; code applies the formula.
    If the first search finds no value, every passage that contains a number with the right kind of unit
    (e.g. '16 weeks', '99.9%') is added and the extraction is tried once more before giving up."""
    chunks = _numeric_chunks(criterion, retriever)
    ex = extract_value("vendor", criterion, chunks, llm, settings)
    if not ex["found"]:
        pat = unit_pattern(criterion.unit)
        extra = [c for c in retriever.chunks if pat and pat.search(c.text) and c not in chunks][:6] if pat else []
        if extra:
            chunks = chunks + extra
            ex = extract_value("vendor", criterion, chunks, llm, settings)
        if not ex["found"] and pat:
            focused = [c for c in chunks if pat.search(c.text)][:6]
            if focused:
                ex = extract_value("vendor", criterion, focused, llm, settings)
    evidence_text = _norm(" ".join(c.text for c in chunks))
    reasons, score, calc, conf = [], 0.0, "", "high"
    if not ex["found"]:
        reasons.append(f"value not stated in proposal (searched {len(chunks)} passages; check the proposal text "
                       "by hand before trusting this 0)")
        calc, conf = "No committed value found in the proposal, so the score is 0.", "low"
    else:
        try:
            x = convert(ex["value"], ex["unit"], criterion.unit)
            T = target["value"]
            if criterion.method == "Lower is better":
                score = score_lower_is_better(x, T)
                calc = (f"Proposed {ex['value']:g} {ex['unit']} = {x:.1f} {criterion.unit}; target {T:g} {criterion.unit}. "
                        f"Score = 5 x (2 x {T:g} - {x:.1f}) / {T:g} = {score:.2f}")
            else:
                score = score_higher_is_better(x, T, criterion.floor)
                calc = (f"Proposed {ex['value']:g} {ex['unit']}; target {T:g}, floor {criterion.floor:g}. "
                        f"Score = 5 x ({x:g} - {criterion.floor:g}) / ({T:g} - {criterion.floor:g}) = {score:.2f}")
        except ValueError as e:
            reasons.append(f"unit problem: {e}")
            calc, conf = f"Could not convert '{ex['unit']}' to {criterion.unit}.", "low"
        if ex["evidence_quote"] and _norm(ex["evidence_quote"]) not in evidence_text:
            reasons.append("quote not found verbatim in retrieved text")
    score = round(score, 1)
    if criterion.mandatory and score < min_mandatory:
        reasons.append("mandatory criterion below minimum")
    if settings.mock:
        reasons.append("mock extraction (offline test)")
    return CriterionScore(vendor, criterion.id, criterion.name, score, conf, calc, ex["evidence_quote"],
                          sorted(set(ex["pages"])), bool(reasons), "; ".join(reasons),
                          _refs(chunks, ex["evidence_quote"], ex["pages"]))


_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def score_with_subs(vendor, criterion, retriever, llm, settings, min_mandatory=3, rfp_text=""):
    """Score a Judgement criterion. With sub-criteria, each one is scored on its own evidence and the criterion
    score is their weighted average (plain code, no AI). Without sub-criteria it is scored as a whole."""
    if not criterion.subs:
        return score_criterion(vendor, criterion, retriever, llm, settings, min_mandatory, rfp_text)
    subs = [score_criterion(vendor, sc, retriever, llm, settings, min_mandatory, rfp_text, parent=criterion)
            for sc in criterion.subs]
    total = round(sum(sc.weight * r.score for sc, r in zip(criterion.subs, subs)), 2)
    reasons = []
    flagged = [r.criterion_id for r in subs if r.needs_review]
    if flagged:
        reasons.append("sub-criteria to check: " + ", ".join(flagged))
    if criterion.mandatory and total < min_mandatory:
        reasons.append("mandatory criterion below minimum")
    parts = [f"{r.criterion} {r.score:g}/5 (weight {sc.weight:.0%}): {r.justification}"
             for sc, r in zip(criterion.subs, subs)]
    conf = min((r.confidence for r in subs), key=lambda c: _CONF_RANK[c])
    return CriterionScore(vendor, criterion.id, criterion.name, total, conf, " | ".join(parts), "",
                          sorted({p for r in subs for p in r.pages}), bool(reasons), "; ".join(reasons),
                          sorted({x for r in subs for x in r.sources}), subs)

