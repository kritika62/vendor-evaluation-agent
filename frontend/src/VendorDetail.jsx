import { useEffect, useState } from "react";
import { getVendor, overrideScore } from "./api";

const box = { border: "1px solid #888", padding: 8, marginBottom: 8 };

function Item({ c, indent, evalId, vendor, onSaved }) {
  const [open, setOpen] = useState(false);
  const [score, setScore] = useState(c.score);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const save = async () => {
    setBusy(true); setErr("");
    try {
      await overrideScore(evalId, c.id, vendor, Number(score), reason);
      setOpen(false); setReason("");
      onSaved();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ marginLeft: indent ? 20 : 0, marginTop: 6 }}>
      <b>{c.id}</b> {c.name} - <b>{c.score}</b>/5 (weight {Math.round(c.weight * 100)}%)
      {c.overridden && <span style={{ color: "orange" }}> overridden (agent: {c.agent_score})</span>}
      {c.needs_review && <span style={{ color: "orange" }}> - review</span>}{" "}
      <button onClick={() => { setOpen(!open); setScore(c.score); }}
              style={{ padding: "2px 10px", fontSize: 12 }}>Override</button>
      {open && (
        <div style={{ margin: "8px 0", display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input type="number" min="0" max="5" step="0.5" value={score} style={{ width: 70 }}
                 onChange={(e) => setScore(e.target.value)} />
          <input placeholder="Reason (e.g. proposal says 16 weeks on p.3)" value={reason} style={{ flex: 1, minWidth: 220 }}
                 maxLength={500} onChange={(e) => setReason(e.target.value)} />
          <button onClick={save} disabled={busy || score === "" || Number(score) < 0 || Number(score) > 5}>
            {busy ? "Saving..." : "Save"}
          </button>
        </div>
      )}
      {err && <div style={{ color: "crimson" }}>Override error: {err}</div>}
      <div style={{ fontSize: 13, opacity: 0.85 }}>
        {c.review_reason && <div>Review reason: {c.review_reason}</div>}
        {c.justification && <div>Why: {c.justification}</div>}
        {c.evidence_quote && <div>Evidence: "{c.evidence_quote}"</div>}
        {c.sources && <div>Source: {c.sources}</div>}
        <div>Confidence: {c.confidence}</div>
      </div>
    </div>
  );
}

export default function VendorDetail({ evalId, vendors, onChanged }) {
  const [name, setName] = useState(vendors[0]);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    getVendor(evalId, name)
      .then((d) => { setData(d); setError(""); })
      .catch((e) => setError(e.message));
  }, [evalId, name, reload]);

  const saved = () => { setReload((n) => n + 1); if (onChanged) onChanged(); };

  return (
    <div>
      <h2>5. Vendor detail</h2>
      <select value={name} onChange={(e) => { setData(null); setName(e.target.value); }}>
        {vendors.map((v) => <option key={v} value={v}>{v}</option>)}
      </select>
      {error && <p style={{ color: "crimson" }}>Error: {error}</p>}
      {!data && !error && <p>Loading...</p>}
      {data && (
        <div>
          <p>
            <b>{data.vendor}</b>: {data.weighted_score}/5 ({data.score_pct}%) - {data.status}
            {data.rank != null && ` - rank ${data.rank}`} - {data.recommendation}
            {data.failed_mandatory && ` (failed: ${data.failed_mandatory})`}
          </p>
          <p>Files: {data.files.join(", ")}</p>
          {data.summary && (
            <div style={box}>
              <p>{data.summary.overview}</p>
              <div style={{ whiteSpace: "pre-line" }}><b>Strengths</b>{"\n"}{data.summary.strengths}</div>
              <div style={{ whiteSpace: "pre-line", marginTop: 6 }}><b>Gaps</b>{"\n"}{data.summary.gaps}</div>
              <div style={{ whiteSpace: "pre-line", marginTop: 6 }}><b>Risks</b>{"\n"}{data.summary.risks}</div>
            </div>
          )}
          <h3>Criteria (click to expand)</h3>
          <p style={{ fontSize: 13, opacity: 0.8 }}>
            Tip: if a criterion has sub-criteria, override the sub-criterion. Overriding the criterion itself replaces its average.
          </p>
          {data.criteria.map((c) => (
            <details key={c.id} style={box}>
              <summary style={{ cursor: "pointer" }}>
                <b>{c.id}</b> {c.name} - <b>{c.score}</b>/5
                {c.mandatory && " (mandatory)"}{c.overridden && " - overridden"}{c.needs_review && " - review"}
              </summary>
              <Item c={c} evalId={evalId} vendor={data.vendor} onSaved={saved} />
              {c.sub_criteria.map((s) => <Item key={s.id} c={s} indent evalId={evalId} vendor={data.vendor} onSaved={saved} />)}
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
