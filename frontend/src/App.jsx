import { useEffect, useState } from "react";
import { listEvaluations, getRfp, getVendors, getStatus, uploadRfp, uploadVendorFiles, startEvaluation, deleteVendor, deleteVendorFile } from "./api";
import Ranking from "./Ranking";
import CostPreview from "./CostPreview";

const kb = (b) => (b >= 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`);

export default function App() {
  const [rfp, setRfp] = useState(null);
  const [vendors, setVendors] = useState([]);
  const [vendorName, setVendorName] = useState("");
  const [vendorFiles, setVendorFiles] = useState([]);
  const [mock, setMock] = useState(true);
  const [t6, setT6] = useState("99.9");
  const [t7, setT7] = useState("6");
  const targets = () => { const o = {}; if (t6 !== "") o.T6 = Number(t6); if (t7 !== "") o.T7 = Number(t7); return o; };
  const [evalId, setEvalId] = useState(null);
  const [latest, setLatest] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");

  const run = async (fn) => {
    setError("");
    try { await fn(); } catch (e) { setError(e.message); }
  };

  const refresh = async () => {
    try { setRfp(await getRfp()); } catch { setRfp(null); }
    setVendors((await getVendors()) || []);
  };

  useEffect(() => { run(refresh); }, []);

  useEffect(() => {
    listEvaluations().then((d) => {
      const list = Array.isArray(d) ? d : (d.evaluations || d.items || []);
      const done = list.filter((e) => e.status === "done" && e.id).sort((a, b) => (a.id < b.id ? 1 : -1));
      if (done.length) setLatest(done[0].id);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!evalId) return;
    const timer = setInterval(async () => {
      try {
        const s = await getStatus(evalId);
        setStatus(s);
        if (s.status === "done" || s.status === "failed") clearInterval(timer);
      } catch (e) { setError(e.message); clearInterval(timer); }
    }, 2000);
    return () => clearInterval(timer);
  }, [evalId]);

  const card = { border: "1px solid var(--line)", borderRadius: 12, padding: "10px 14px", marginBottom: 10, background: "var(--panel)" };
  const small = { padding: "2px 10px", fontSize: 12, background: "var(--panel2)" };

  return (
    <div>
      <h1>Vendor Evaluation</h1>
      {error && <p style={{ color: "crimson" }}>Error: {error}</p>}

      <h2>1. RFP</h2>
      {rfp
        ? <p>On server: <b>{rfp.file}</b> ({kb(rfp.bytes)}). Choose a file below to replace it.</p>
        : <p>No RFP uploaded yet.</p>}
      <input type="file" onChange={(e) => run(async () => { await uploadRfp(e.target.files[0]); await refresh(); })} />

      <h2>2. Vendor proposals</h2>
      {vendors.length === 0 && <p>No vendors yet. Add one below.</p>}
      {vendors.map((v) => (
        <div key={v.vendor} style={card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <b>{v.vendor}</b>
            <button style={small} onClick={() => { if (window.confirm(`Delete vendor "${v.vendor}" and all its files?`)) run(async () => { await deleteVendor(v.vendor); await refresh(); }); }}>
              Delete vendor
            </button>
          </div>
          {v.files.map((f) => (
            <div key={f.file} style={{ display: "flex", justifyContent: "space-between", fontSize: 14, marginTop: 6 }}>
              <span>{f.file} <span style={{ color: "var(--muted)" }}>({kb(f.bytes)})</span></span>
              <button style={small} onClick={() => run(async () => { await deleteVendorFile(v.vendor, f.file); await refresh(); })}>Remove file</button>
            </div>
          ))}
        </div>
      ))}

      <p style={{ color: "var(--muted)", marginBottom: 6 }}>Add files to a vendor (new or existing name):</p>
      <input placeholder="Vendor name" value={vendorName} onChange={(e) => setVendorName(e.target.value)} />{" "}
      <input type="file" multiple onChange={(e) => setVendorFiles([...e.target.files])} />{" "}
      <button
        disabled={!vendorName || vendorFiles.length === 0}
        onClick={() => run(async () => { await uploadVendorFiles(vendorName, vendorFiles); setVendorName(""); setVendorFiles([]); await refresh(); })}
      >Upload</button>

      <h2>3. Run</h2>
      <p style={{ color: "var(--muted)" }}>An evaluation uses the RFP and the vendors listed above.</p>
      <label><input type="checkbox" checked={mock} onChange={(e) => setMock(e.target.checked)} /> Mock mode (no Groq, meaningless scores)</label>
      <p style={{ color: "var(--muted)", marginBottom: 6 }}>Numeric targets (used if the RFP does not state them): T6 uptime % <input style={{ width: 70 }} value={t6} onChange={(e) => setT6(e.target.value)} /> T7 timeline in months <input style={{ width: 70 }} value={t7} onChange={(e) => setT7(e.target.value)} /></p>
      <CostPreview mock={mock} />
      <br /><br />
      <button disabled={!rfp || vendors.length === 0} onClick={() => run(async () => { setStatus(null); const r = await startEvaluation(mock, targets()); setEvalId(r.id); })}>
        Start evaluation
      </button>{" "}
      {latest && !evalId && (
        <button style={{ background: "var(--panel2)" }} onClick={() => { setEvalId(latest); setStatus({ status: "done", percent: 100, message: "Loaded previous evaluation" }); }}>
          Open previous results
        </button>
      )}
      {evalId && <p>Evaluation id: {evalId}</p>}
      {status && <p>Status: {status.status} {status.percent != null && `(${status.percent}%)`} {status.message}</p>}
      {status && status.status === "failed" && <p style={{ color: "crimson" }}>{status.error}</p>}
      {status && status.status === "done" && <Ranking evalId={evalId} />}
    </div>
  );
}

