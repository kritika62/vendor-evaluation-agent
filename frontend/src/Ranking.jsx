import { useEffect, useState } from "react";
import { getRanking } from "./api";
import VendorDetail from "./VendorDetail";
import Downloads from "./Downloads";
import Chat from "./Chat";

const cell = { border: "1px solid #888", padding: "6px 10px", textAlign: "left" };

export default function Ranking({ evalId }) {
  const [minScore, setMinScore] = useState(3);
  const [shortlistN, setShortlistN] = useState(2);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    getRanking(evalId, minScore, shortlistN)
      .then((d) => { setData(d); setError(""); })
      .catch((e) => setError(e.message));
  }, [evalId, minScore, shortlistN, tick]);

  if (error) return <p style={{ color: "crimson" }}>Ranking error: {error}</p>;
  if (!data) return <p>Loading ranking...</p>;

  const cats = Object.keys(data.categories);
  const vendors = data.ranking.map((r) => r.vendor);

  return (
    <div>
      <h2>4. Ranking</h2>
      <p style={{ color: "var(--muted)", fontSize: 13 }}>Scores never change with the slider. Only Pass/Fail, rank and row order do.</p>
      <p>
        Minimum score for mandatory criteria: <b>{minScore}</b>{" "}
        <input type="range" min="0" max="5" step="0.5" value={minScore}
               onChange={(e) => setMinScore(Number(e.target.value))} />
        {"  "}Shortlist size:{" "}
        <input type="number" min="1" value={shortlistN} style={{ width: 50 }}
               onChange={(e) => setShortlistN(Math.max(1, Number(e.target.value) || 1))} />
      </p>

      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>{["Rank", "Vendor", "Score /5", "Score %", "Status", "Recommendation", "Failed mandatory"].map((h) => <th key={h} style={cell}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {data.ranking.map((r) => (
            <tr key={r.vendor}>
              <td style={cell}>{r.rank ?? "-"}</td>
              <td style={cell}>{r.vendor}</td>
              <td style={cell}>{r.weighted_score}</td>
              <td style={cell}>{r.score_pct}</td>
              <td style={{ ...cell, color: r.status === "Pass" ? "limegreen" : "tomato" }}>{r.status}</td>
              <td style={cell}>{r.recommendation}</td>
              <td style={cell}>{r.failed_mandatory || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Weighted score</h3>
      {data.ranking.map((r) => (
        <div key={r.vendor} style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>
          <div style={{ width: 90 }}>{r.vendor}</div>
          <div style={{ flex: 1, background: "#444", height: 18 }}>
            <div style={{ width: `${(r.weighted_score / 5) * 100}%`, height: 18, background: r.status === "Pass" ? "limegreen" : "tomato" }} />
          </div>
          <div style={{ width: 50, textAlign: "right" }}>{r.weighted_score}</div>
        </div>
      ))}

      <h3>Category scores (out of 5)</h3>
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr><th style={cell}>Category</th>{vendors.map((v) => <th key={v} style={cell}>{v}</th>)}</tr>
        </thead>
        <tbody>
          {cats.map((c) => (
            <tr key={c}>
              <td style={cell}>{c}</td>
              {vendors.map((v) => <td key={v} style={cell}>{data.categories[c][v]}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <p>Items flagged for review: {data.flagged_count}</p>
      <VendorDetail evalId={evalId} vendors={vendors} onChanged={() => setTick((n) => n + 1)} />
      <Downloads evalId={evalId} minScore={minScore} shortlistN={shortlistN} />
      <Chat evalId={evalId} />
    </div>
  );
}





