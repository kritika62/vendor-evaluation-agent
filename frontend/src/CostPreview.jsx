import { useState } from "react";
import { previewCosts } from "./api";

const CURRENCIES = ["INR", "USD", "EUR", "GBP", "JPY"];
const BASIS = { total: "Total", per_year: "Per year", per_month: "Per month", other: "Other" };
const num = (n) => (n == null ? "-" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }));

export default function CostPreview({ mock, currency, onCurrency }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      setData(await previewCosts(currency, mock));
    } catch (e) {
      setError(e.message);
      setData(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ margin: "14px 0" }}>
      <p style={{ color: "var(--muted)", marginBottom: 6 }}>
        Costing currency:{" "}
        <select value={currency} onChange={(e) => { onCurrency(e.target.value); setData(null); }}>
          {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>{" "}
        <button onClick={run} disabled={busy}>{busy ? "Reading costs..." : "Preview costs"}</button>
      </p>
      <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 8px" }}>
        Reads each vendor's price, how it is quoted (total, per year or per month) and the contract length, then works out the total over the contract and converts it. Information only: it does not change any score. The same currency is saved with the evaluation when you click Start evaluation.
        {mock ? " Mock mode reads the first amount with a simple pattern, no AI." : ""}
      </p>
      {error && <p style={{ color: "crimson" }}>Cost preview error: {error}</p>}
      {data && (
        <div>
          <p style={{ fontSize: 13 }}>
            All prices in <b>{data.currency}</b>. Exchange rates dated: {data.rates_date || "not set (fill in rates.json)"}.
          </p>
          <table>
            <thead>
              <tr>
                <th>Vendor</th><th>Price as written</th><th>Quoted as</th><th>Years</th>
                <th>Total over contract</th><th>Total in {data.currency}</th><th>In {data.currency} (as quoted)</th>
                <th>Source</th><th>Quote</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.vendors.map((r) => (
                <tr key={r.vendor}>
                  <td>{r.vendor}</td>
                  <td>{r.found ? `${num(r.amount)} ${r.written_currency}` : "-"}</td>
                  <td>{BASIS[r.basis] || "-"}</td>
                  <td title={r.term_quote || ""}>{num(r.term_years)}</td>
                  <td>{r.found && r.total != null ? `${num(r.total)} ${r.written_currency}` : "-"}</td>
                  <td><b>{num(r.converted_total)}</b></td>
                  <td>{num(r.converted)}</td>
                  <td>{r.source || "-"}</td>
                  <td style={{ maxWidth: 260 }}>
                    {r.quote || "-"}
                    {r.term_quote ? <div style={{ color: "var(--muted)", fontSize: 12 }}>Length: {r.term_quote}</div> : null}
                  </td>
                  <td style={{ color: r.status === "ok" ? "limegreen" : "orange" }}>
                    {r.status}{r.note ? `: ${r.note}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
