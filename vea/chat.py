"""Chat assistant for the evaluation results ("ask anything about the scorecard").

    answer = chat("Why did vendor_a rank first?", history, res, out)

How it stays grounded (anti-hallucination):
  * The numbers (ranking, scores, flags, what-if outcomes) are computed by the dashboard code and pasted
    into the prompt as FACTS. The model is told to answer only from those facts and to say so when the
    answer is not there.
  * If a proposal index is available (see proposal_index.py), the best-matching passages from the vendors'
    documents are added too, so "does ey mention X?" can be answered from the full text.
  * Evidence quotes / justifications are only included for the vendors and criteria the question mentions,
    so the prompt stays small enough for the Groq free tier.
Uses the same .env settings as the rest of the project (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL) and plain
`requests`, so there is no new dependency. No Streamlit code in here: FastAPI can call chat() too.
"""
import os
import re
import time

import requests

try:                                    # the project already uses .env; load it if python-dotenv is present
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass

MAX_EVIDENCE_CHARS = 6000
MAX_HISTORY_TURNS = 6
STOP = set("the and for with what why how which does did are was were has have this that from about into "
           "than then them their they vendor vendors score scores criterion criteria rank ranked ranking".split())

SYSTEM_PROMPT = """You are the assistant inside a vendor technical evaluation dashboard. Users ask about the \
evaluation of vendor proposals against an RFP.

Rules:
- Answer ONLY from the FACTS block below. Never use outside knowledge about the vendors.
- Quote numbers exactly as they appear in FACTS. Do not do your own arithmetic on weights; the ranking is given.
- Cite the vendor and ID in brackets for each claim, e.g. [vendor_a, T4.2]. When you use evidence, say which source it came from.
- A score of 0 usually means the proposal did not mention the topic ("not found"), not that the vendor is bad. Say so when relevant.
- A score of 0 IS a score (the lowest, 0/5). Never describe it as "no score" or "unscored"; only "n/a" means a missing score.
- For "what if the minimum score is X" questions, use the WHAT-IF TABLE. For weight changes or other scenarios, \
tell the user to use the What-if controls in the sidebar.
- If the answer is not in FACTS, say what is missing instead of guessing.
- PROPOSAL PASSAGES (when present) are the most relevant extracts from the vendors' own documents. Use them for \
questions about what a proposal says, and cite them as [vendor, file p.N]. They are the best matches, not the whole \
proposal: if something is not in them, say "I did not find it in the most relevant passages", never a flat "No".
- Without PROPOSAL PASSAGES, start yes/no questions about proposal content with "Based on the scorecard evidence" and never give a flat "No".
- Be concise: a short answer first, then supporting points.
- FORMAT: reply in GitHub-flavoured markdown. Whenever you compare vendors, list scores, show a ranking, or show \
what-if results, use a markdown table, never a run-on sentence or a list of "vendor: score" lines.
  Table rules: one header row, a separator row (| --- | --- |), one row per vendor or criterion, one fact per cell, \
the same columns on every row. Put a blank line before and after the table. Keep cells short (no long quotes in cells).
  Good columns for a ranking: | Rank | Vendor | Total /5 | Mandatory | Result |. Use "Pass" or "Fail" for Result, \
and "n/a" for missing values. After the table, add at most 2-3 short sentences or bullets explaining the reason.
  Use **bold** for vendor names or key numbers when it helps. No HTML. No code fences around tables."""


# ---------------------------------------------------------------- building the FACTS block
def _fmt(v, nd=2):
    try:
        if v is None or v != v:
            return "n/a"
        t = f"{float(v):.{nd}f}"
        return t.rstrip("0").rstrip(".") if "." in t else t        # never strip the zero out of "0" or "10"
    except (TypeError, ValueError):
        return str(v)


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 3 and w not in STOP}


def _mentioned(question, res, out):
    """Which vendors / criterion-or-sub IDs does the question talk about?"""
    q = question.lower()
    vendors = [v for v in res["scores"] if str(v).lower() in q]
    qw = _words(question)
    ids = {m.upper() for m in re.findall(r"\bt\d+(?:\.\d+)?\b", q)}
    for c in out["criteria"]:
        if _words(c.name) & qw:
            ids.add(c.id)
        for s in c.subs:
            if _words(s.name) & qw:
                ids.add(s.id)
    return vendors, ids


def _base_facts(res, out):
    s = out["summary"]
    L = [f"CURRENT SETTINGS: mandatory minimum score {_fmt(out['min_score'])}, shortlist size {out['shortlist_n']}. "
         "Mandatory criteria scoring below the minimum reject a vendor.", "", "RANKING (best first):"]
    for _, r in s.iterrows():
        rank = "-" if r["Rank"] is None or r["Rank"] != r["Rank"] else int(r["Rank"])
        failed = f", failed mandatory: {r['Failed mandatory']}" if str(r["Failed mandatory"]).strip() else ""
        L.append(f"- {r['Vendor']}: weighted score {_fmt(r['Weighted score'])}/5 ({_fmt(r['Score %'], 1)}%), "
                 f"status {r['Status']}, rank {rank}, {r['Recommendation']}{failed}")
    L += ["", "CRITERIA (id name, weight):"]
    for c in out["criteria"]:
        L.append(f"- {c.id} {c.name}, weight {c.weight:.0%}" +
                 (": sub-criteria " + "; ".join(f"{x.id} {x.name} ({x.weight:.0%})" for x in c.subs) if c.subs else ""))
    L += ["", "CRITERION SCORES (0-5):"]
    for v, sc in res["scores"].items():
        L.append(f"- {v}: " + ", ".join(f"{c.id}={_fmt(sc.get(c.id), 1)}" for c in out["criteria"]))
    L += ["", "SUB-CRITERION SCORES (0-5):"]
    for v, sc in res["sub_scores"].items():
        L.append(f"- {v}: " + ", ".join(f"{k}={_fmt(x, 0)}" for k, x in sc.items()))
    fl = out["flagged"]
    if not fl.empty:
        L += ["", "FLAGGED FOR HUMAN REVIEW:"]
        for _, r in fl.iterrows():
            L.append(f"- {r['Vendor']} {r['ID']} {r['Criterion']}: score {_fmt(r['Score'], 0)}; {r['Review reason']}")
    tg = res["targets"]
    if not tg.empty:
        L += ["", "NUMERIC TARGETS:"]
        for _, r in tg.iterrows():
            L.append(f"- {r['ID']} {r['Criterion']}: target {r['Target']} {r['Unit']} (source: {r['Source']})")
    return "\n".join(L)


def _whatif_table(res, out, compute_fn):
    """Status / rank of every vendor at each mandatory-minimum value, using the dashboard's own compute()."""
    if compute_fn is None:
        return ""
    weights = {c.id: c.weight for c in out["criteria"]}
    steps = [x / 2 for x in range(0, 11)]
    per = {v: [] for v in res["scores"]}
    try:
        for m in steps:
            o = compute_fn(res, weights=weights, min_score=m, shortlist_n=out["shortlist_n"])
            for _, r in o["summary"].iterrows():
                rank = "-" if r["Rank"] is None or r["Rank"] != r["Rank"] else f"#{int(r['Rank'])}"
                per[r["Vendor"]].append(f"{m:g}:{r['Status']}{rank if rank != '-' else ''}")
    except Exception:
        return ""
    L = ["WHAT-IF TABLE (outcome at each mandatory minimum score, current weights; format minimum:status#rank):"]
    L += [f"- {v}: " + " ".join(x) for v, x in per.items()]
    return "\n".join(L)


def _evidence(res, vendors, ids, question):
    log = res["log"]
    if log is None or log.empty:
        return ""
    df = log
    if vendors:
        df = df[df["Vendor"].isin(vendors)]
    if ids:
        parent = {i.split(".")[0] for i in ids}                     # a criterion id also pulls in its sub-criteria
        df = df[df["ID"].astype(str).isin(ids) | df["ID"].astype(str).isin(parent) |
                df["ID"].astype(str).str.split(".").str[0].isin(parent)]
    elif not vendors:
        df = df[df["Level"] == "Criterion"]                         # broad question: criterion rows only
    L, used = ["EVIDENCE FROM THE AGENT LOG (vendor, id, score, justification, evidence quote, source):"], 0
    for _, r in df.iterrows():
        cut = lambda t, n: ("-" if t is None or t != t or not str(t).strip() else str(t).replace("\n", " ")[:n])
        line = (f"- [{r['Vendor']}, {r['ID']}] {r['Criterion']}: score {_fmt(r['Score'], 0)}, "
                f"review: {r['Needs review']}. Why: {cut(r['Justification'], 300)} "
                f"Quote: \"{cut(r['Evidence quote'], 200)}\" Source: {cut(r['Sources'], 80)}")
        if used + len(line) > MAX_EVIDENCE_CHARS:
            L.append("- (more evidence rows omitted; ask about a specific vendor or criterion for the rest)")
            break
        L.append(line); used += len(line)
    sm = res["summaries"]
    if not sm.empty:
        chosen = sm[sm["Vendor"].isin(vendors)] if vendors else sm
        L += ["", "VENDOR SUMMARIES:"]
        for _, r in chosen.iterrows():
            L.append(f"- {r['Vendor']}: {str(r['Overview'])[:400]} | Strengths: {str(r['Strengths'])[:300]} | "
                     f"Gaps: {str(r['Gaps'])[:300]} | Risks: {str(r['Risks'])[:300]}".replace("\n", " "))
    return "\n".join(L)


SKIP_SEARCH = re.compile(r"what[ -]?if|minimum|shortlist size|weights?\b|re-?weigh", re.I)


def _passages(search, question, vendors):
    """Best-matching proposal passages for the question (empty if no index or a pure scoring/what-if question)."""
    if search is None or SKIP_SEARCH.search(question):
        return ""
    try:
        hits = search.search(question, vendors=vendors)
    except Exception:
        return ""
    if not hits:
        return ""
    L = [f"PROPOSAL PASSAGES (top matches for the question, {search.mode} search; cite as [vendor, file p.N]):"]
    for v, c in hits:
        L.append(f"- [{v}, {c.source or 'file'} p.{c.page}] " + c.text.replace("\n", " ").strip())
    return "\n".join(L)


def build_facts(question, res, out, compute_fn=None, search=None):
    vendors, ids = _mentioned(question, res, out)
    parts = [_base_facts(res, out), _whatif_table(res, out, compute_fn), _evidence(res, vendors, ids, question),
             _passages(search, question, vendors)]
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------- calling the model
class ChatError(Exception):
    pass


def _call_llm(messages, retries=3):
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    key, model = os.getenv("LLM_API_KEY", ""), os.getenv("LLM_MODEL", "")
    if not (base and key and model):
        raise ChatError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set in your .env file.")
    for attempt in range(retries + 1):
        try:
            r = requests.post(f"{base}/chat/completions", timeout=90,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1000})
        except requests.exceptions.Timeout:
            raise ChatError("The model API took too long to answer. Try again in a moment.")
        except requests.exceptions.ConnectionError:
            raise ChatError("Can't reach the model API. Check your internet connection (and VPN or firewall), "
                            "then ask again.")
        if r.status_code == 429 and attempt < retries:               # Groq free tier rate limit
            time.sleep(min(float(r.headers.get("retry-after", 5)), 30))
            continue
        if r.status_code != 200:
            raise ChatError(f"The model API returned {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"].strip()
    raise ChatError("Rate limit reached. Wait a few seconds and ask again.")


def chat(question, history, res, out, compute_fn=None, search=None):
    """question: str. history: [{"role": "user"|"assistant", "content": str}] of earlier turns.
    res / out: the dicts from load_results() / compute(). compute_fn: dashboard_data.compute (enables what-if).
    search: optional proposal_index.ProposalSearch (enables questions about the full proposal text)."""
    if compute_fn is None:
        try:
            from .dashboard_data import compute as compute_fn
        except Exception:
            compute_fn = None
    facts = build_facts(question, res, out, compute_fn, search)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nFACTS:\n" + facts}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history[-2 * MAX_HISTORY_TURNS:]]
    msgs.append({"role": "user", "content": question})
    return _call_llm(msgs)

