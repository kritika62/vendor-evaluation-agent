"""Vendor evaluation dashboard.   Run:  streamlit run app.py"""
import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.io as pio
import streamlit as st
from vea.dashboard_data import compute, load_results
from vea.chat import ChatError, chat
from vea.exports import FORMATS, export
from vea.proposal_index import ProposalSearch, chunks_path_for, parse_chunks

st.set_page_config(
    page_title="Vendor Evaluation Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

PURPLE, PINK, GREY = "#c77dff", "#ff7eb6", "#6b6b80"
STATUS_COLOURS = {"Pass": PURPLE, "Fail": PINK, "Incomplete": GREY}

pio.templates.default = "plotly_dark"


def txt(v):
    """Safe text for display: empty cells (None / NaN / blank) become '-'."""
    return "-" if v is None or v != v or str(v).strip() == "" else str(v)


# ---------------- styling ----------------
st.markdown(
    """
    <style>
    #MainMenu, footer {visibility: hidden;}
    .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1300px;}
    .hero {
        background: linear-gradient(120deg, #1a0b2e 0%, #4a1d8a 60%, #7b2cbf 100%); border: 1px solid #2d2340;
        border-radius: 16px; padding: 1.6rem 2rem; margin-bottom: 1.4rem; color: #fff;
    }
    .hero h1 {margin: 0; font-size: 1.9rem; font-weight: 700; color: #fff;}
    .hero p {margin: .35rem 0 0 0; opacity: .88; font-size: .98rem;}
    div[data-testid="stMetric"] {
        background: #16161d; border: 1px solid #2a2a36; border-left: 5px solid #c77dff;
        border-radius: 12px; padding: 1rem 1.2rem; box-shadow: 0 2px 12px rgba(0,0,0,.5);
    }
    div[data-testid="stMetricLabel"] {color: #a9a3ba;}
    div[data-testid="stMetricValue"] {color: #f2eefa; font-weight: 700;}
    button[data-baseweb="tab"] {font-weight: 600; padding: .6rem 1.1rem;}
    .shortlist {
        background: #1d1530; border: 1px solid #5a3a8a; border-radius: 12px;
        padding: .9rem 1.2rem; margin-bottom: 1rem; color: #f2eefa;
    }
    .pill {display:inline-block; background:#9d4edd; color:#fff; border-radius:999px;
           padding:.15rem .8rem; margin:.15rem .3rem .15rem 0; font-size:.88rem; font-weight:600;}
    section[data-testid="stSidebar"] {border-right: 1px solid #2a2a36;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <h1>📊 Vendor Technical Evaluation</h1>
      <p>Compare vendor proposals, explore evidence behind every score, and test what-if scenarios.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------- data source ----------------
with st.sidebar:
    st.header("📁 Data")
    uploaded = st.file_uploader("Upload scorecard workbook (.xlsx)", type=["xlsx"])
    path = st.text_input("…or file path", value="output/scorecard.xlsx",
                         help="Used only when no file is uploaded.")
with st.sidebar:
    with st.expander("Proposal search (for the chat)"):
        chunks_up = st.file_uploader("Proposal index (.chunks.json)", type=["json"],
                                     help="Created next to the scorecard by main.py or build_index.py. "
                                          "Loaded automatically when you use the file path above.")
source = uploaded if uploaded is not None else path
try:
    res = load_results(source)
except Exception as e:  # missing file, wrong workbook, etc.
    st.markdown("### 👋 Welcome")
    st.write("Upload your **scorecard.xlsx** in the sidebar to get started.")
    st.caption("Generate it first with `python main.py`, then upload `output/scorecard.xlsx`.")
    with st.expander("Technical details"):
        st.code(str(e))
    st.stop()

# ---------------- what-if controls ----------------
with st.sidebar:
    st.header("🎛️ What-if")
    min_score = st.slider("Mandatory minimum score", 0.0, 5.0, float(res["min_score"]), 0.5)
    n_short = st.number_input("Shortlist size", min_value=1, max_value=10,
                              value=int(res["shortlist_n"]), step=1)
    with st.expander("Criterion weights (%)"):
        weights = {}
        for c in res["criteria"]:
            weights[c.id] = st.number_input(f"{c.id} {c.name}", min_value=0.0, max_value=100.0,
                                            value=round(c.weight * 100, 1), step=1.0, key=f"w_{c.id}")
    total_w = sum(weights.values())
    st.caption(f"Weights total {total_w:.0f}% (rescaled to 100% automatically)")
if total_w <= 0:
    st.error("At least one criterion weight must be above 0.")
    st.stop()

out = compute(res, weights={k: v / 100 for k, v in weights.items()},
              min_score=min_score, shortlist_n=int(n_short))
summary, flagged = out["summary"], out["flagged"]
default_w = {c.id: round(c.weight * 100, 1) for c in res["criteria"]}
if (min_score != res["min_score"] or int(n_short) != res["shortlist_n"]
        or any(abs(weights[k] - default_w[k]) > 0.05 for k in weights)):
    st.info("⚠️ What-if settings are active, so these results differ from the workbook.")

# ---------------- KPIs ----------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Vendors evaluated", len(summary))
k2.metric("Shortlisted", int((summary["Recommendation"] == "Shortlist").sum()))
k3.metric("Rejected (mandatory not met)", int((summary["Status"] == "Fail").sum()))
k4.metric("Scores flagged for review", len(flagged))
st.write("")


def heatmap(df):
    fig = px.imshow(
        df.astype(float), text_auto=".1f", aspect="auto",
        color_continuous_scale=[[0, "#2a0f2e"], [0.4, "#7a2a6e"], [0.75, "#9d4edd"], [1, "#e0aaff"]],
    )
    fig.update_traces(textfont=dict(size=15))
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), coloraxis_colorbar=dict(title="Score"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


tab_rank, tab_cat, tab_crit, tab_review, tab_sum, tab_tg, tab_chat = st.tabs(
    ["🏆 Ranking", "🗂️ Categories", "🔍 Criteria detail", "🚩 Review queue", "📝 Summaries", "🎯 Targets",
     "💬 Ask the assistant"])

# ---------------- Ranking ----------------
with tab_rank:
    short = summary[summary["Recommendation"] == "Shortlist"]["Vendor"].tolist()
    if short:
        pills = "".join(f'<span class="pill">{v}</span>' for v in short)
        st.markdown(f'<div class="shortlist"><b>Shortlist</b><br>{pills}</div>', unsafe_allow_html=True)
    else:
        st.warning("No vendor qualifies for the shortlist under the current rules.")
    chart = summary.dropna(subset=["Weighted score"])
    if not chart.empty:
        fig = px.bar(chart, x="Weighted score", y="Vendor", orientation="h", color="Status",
                     color_discrete_map=STATUS_COLOURS, range_x=[0, 5], text="Weighted score")
        fig.update_traces(texttemplate="%{text:.2f}", textposition="outside")
        fig.update_layout(yaxis=dict(autorange="reversed", title=None), xaxis_title="Weighted score (out of 5)",
                          margin=dict(l=10, r=10, t=10, b=10), legend_title_text="",
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
    table = summary.copy()
    # str(...) so the column is all text (mixing ints and "-" breaks Streamlit's Arrow conversion)
    table["Rank"] = table["Rank"].apply(lambda r: "-" if r is None or r != r else str(int(r)))
    st.dataframe(table, hide_index=True, width="stretch")
    st.markdown("**Download**")
    f1, f2, f3 = st.columns([1.2, 1.4, 1.4])
    fmt = f1.selectbox("Format", list(FORMATS), format_func=lambda k: FORMATS[k][0], label_visibility="collapsed")
    data, fname, mime = export(res, out, fmt)      # uses the current what-if settings
    f2.download_button(f"⬇️ Download {FORMATS[fmt][0]}", data, file_name=fname, mime=mime)
    wb_bytes = source.getvalue() if hasattr(source, "getvalue") else open(source, "rb").read()
    f3.download_button("⬇️ Original workbook (.xlsx)", wb_bytes, file_name="scorecard.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("Weighted score = sum of (weight x score), out of 5. "
               "A vendor scoring below the minimum on any mandatory criterion is rejected.")

# ---------------- Categories ----------------
with tab_cat:
    st.subheader("Average score by category")
    st.plotly_chart(heatmap(out["categories"]), width="stretch")

# ---------------- Criteria detail ----------------
with tab_crit:
    st.subheader("All criteria")
    st.plotly_chart(heatmap(out["criteria_scores"]), width="stretch")
    st.divider()
    st.subheader("Look at one score")
    d1, d2 = st.columns(2)
    vendor = d1.selectbox("Vendor", list(res["scores"].keys()))
    crit_labels = {f"{c.id} {c.name}": c.id for c in out["criteria"]}
    crit_label = d2.selectbox("Criterion", list(crit_labels.keys()))
    cid = crit_labels[crit_label]
    score = res["scores"][vendor][cid]
    st.metric("Score (out of 5)", "-" if score is None else f"{score:g}")
    log = res["log"]
    row = log[(log["Vendor"] == vendor) & (log["ID"] == cid)] if not log.empty else log
    if len(row):
        r = row.iloc[0]
        with st.container(border=True):
            st.write(f"**Justification:** {txt(r['Justification'])}")
            st.write(f"**Evidence quote:** {txt(r['Evidence quote'])}  (source: {txt(r['Sources'])})")
            st.write(f"**Confidence:** {txt(r['Confidence'])}   |   **Needs review:** {txt(r['Needs review'])}")
            if txt(r["Review reason"]) != "-":
                st.write(f"**Review reason:** {txt(r['Review reason'])}")
    else:
        st.info("No agent log entry for this score.")
    crit_obj = next(c for c in out["criteria"] if c.id == cid)
    if crit_obj.subs:
        st.markdown("**Sub-criteria**")
        sub_rows = []
        for sc_ in crit_obj.subs:
            lr = log[(log["Vendor"] == vendor) & (log["ID"] == sc_.id)] if not log.empty else log
            lr = lr.iloc[0] if len(lr) else None
            sub_rows.append({"Sub-criterion": f"{sc_.id} {sc_.name}", "Weight": f"{sc_.weight:.0%}",
                             "Score": res["sub_scores"][vendor].get(sc_.id),
                             "Needs review": txt(lr["Needs review"]) if lr is not None else "-",
                             "Sources": txt(lr["Sources"]) if lr is not None else "-",
                             "Evidence quote": txt(lr["Evidence quote"]) if lr is not None else "-",
                             "Justification": txt(lr["Justification"]) if lr is not None else "-"})
        st.dataframe(pd.DataFrame(sub_rows), hide_index=True, width="stretch")

# ---------------- Review queue ----------------
with tab_review:
    st.subheader("Scores the agent flagged for a human check")
    if flagged.empty:
        st.success("Nothing flagged. 🎉")
    else:
        pick = st.multiselect("Vendors", sorted(flagged["Vendor"].unique()),
                              default=sorted(flagged["Vendor"].unique()))
        fl = flagged[flagged["Vendor"].isin(pick)].copy()
        fl["Current score"] = [res["scores"][v].get(i, res["sub_scores"][v].get(i)) for v, i in zip(fl["Vendor"], fl["ID"])]
        fl["Overridden"] = ["Yes" if cur != orig else "" for cur, orig in zip(fl["Current score"], fl["Score"])]
        st.dataframe(fl, hide_index=True, width="stretch")
        st.caption("To override a score, change the yellow cell on the Scorecard sheet in Excel, "
                   "save, and re-upload the file.")

# ---------------- Summaries ----------------
with tab_sum:
    sm = res["summaries"]
    if sm.empty:
        st.info("No vendor summaries in this workbook.")
    else:
        for _, r in sm.iterrows():
            with st.expander(str(r["Vendor"]), expanded=True):
                fl = res["files"]
                if not fl.empty:
                    names = fl[fl["Vendor"] == r["Vendor"]]["File"].tolist()
                    if names:
                        st.caption("Files read: " + ", ".join(names))
                st.write(txt(r["Overview"]))
                a, b, c = st.columns(3)
                a.markdown("**✅ Strengths**\n\n" + txt(r["Strengths"]))
                b.markdown("**⚠️ Gaps**\n\n" + txt(r["Gaps"]))
                c.markdown("**🛑 Risks**\n\n" + txt(r["Risks"]))

# ---------------- Targets ----------------
with tab_tg:
    tg = res["targets"]
    if tg.empty:
        st.info("No numeric criteria were used, so there are no targets.")
    else:
        st.dataframe(tg, hide_index=True, width="stretch")
        st.caption("Targets come from the RFP (or the Criteria sheet) and were confirmed before scoring.")

# ---------------- Chat assistant ----------------
@st.cache_resource(show_spinner="Loading proposal search (first time only)…")
def get_search(key, _data):
    """key identifies the index file (path + modified time); _data is not hashed."""
    return ProposalSearch(parse_chunks(_data))


def find_index():
    """-> (key, parsed json) for the proposal index, or (None, None)."""
    if chunks_up is not None:
        return f"upload:{chunks_up.name}:{chunks_up.size}", json.loads(chunks_up.getvalue())
    if uploaded is None:
        p = chunks_path_for(path)
        if p.exists():
            return f"{p.resolve()}:{p.stat().st_mtime}", json.loads(p.read_text(encoding="utf-8"))
    return None, None


with tab_chat:
    st.subheader("Ask about the evaluation")
    idx_key, idx_data = find_index()
    if idx_data:
        n_chunks = sum(len(v) for v in idx_data.values())
        st.caption(f"✅ Proposal search is on ({n_chunks} passages from {len(idx_data)} vendors), so you can ask "
                   "what the proposals say. Try: *Does ey mention ISO 27001?* · *Who offers 24x7 support?* · "
                   "*Why did the top vendor rank first?* · *What if the minimum score is 2.5?*")
    else:
        st.caption("Answers come from the scorecard only (scores, evidence, flags, what-if). To also search the full "
                   "proposals, run `python build_index.py --proposals data/proposals --out output/scorecard.xlsx`, "
                   "then reload this page.")
    if "chat_msgs" not in st.session_state:
        st.session_state.chat_msgs = []
    if st.session_state.chat_msgs and st.button("🗑️ Clear chat"):
        st.session_state.chat_msgs = []
        st.rerun()
    for m in st.session_state.chat_msgs:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    q = st.chat_input("Ask a question about the vendors, scores or evidence…")
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    search = get_search(idx_key, idx_data) if idx_data else None
                    ans = chat(q, st.session_state.chat_msgs, res, out, compute_fn=compute, search=search)
                except ChatError as e:
                    ans = f"⚠️ {e}"
                except Exception as e:
                    ans = f"⚠️ Something went wrong: {e}"
            st.markdown(ans)
        st.session_state.chat_msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]
