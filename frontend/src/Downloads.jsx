import { useState } from "react";
import { downloadUrl } from "./api";

const FORMATS = ["pdf", "docx", "csv", "json", "md", "xlsx"];

export default function Downloads({ evalId, minScore, shortlistN }) {
  const [format, setFormat] = useState("pdf");
  return (
    <div>
      <h2>6. Download report</h2>
      <select value={format} onChange={(e) => setFormat(e.target.value)}>
        {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
      </select>{" "}
      <a href={downloadUrl(evalId, format, minScore, shortlistN)}>
        <button>Download {format.toUpperCase()}</button>
      </a>
      <p style={{ fontSize: 13, opacity: 0.8 }}>
        Uses the current slider settings (minimum {minScore}, shortlist {shortlistN}).
      </p>
    </div>
  );
}
