import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sendChat } from "./api";

function textOf(r) {
  if (typeof r === "string") return r;
  if (r && typeof r === "object") {
    for (const k of ["answer", "reply", "response", "content", "message"]) {
      if (typeof r[k] === "string") return r[k];
    }
    return JSON.stringify(r, null, 2);
  }
  return String(r);
}

const SUGGESTIONS = [
  "Which vendor ranks first and why?",
  "Does vendor_a have ISO 27001?",
  "Why did vendor_c fail?",
];

export default function Chat({ evalId }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const ask = async (q) => {
    const question = (q ?? input).trim();
    if (!question || busy) return;
    setError("");
    setBusy(true);
    setInput("");
    const history = messages.slice(-20);
    setMessages([...messages, { role: "user", content: question }]);
    try {
      const r = await sendChat(evalId, question, history);
      setMessages((m) => [...m, { role: "assistant", content: textOf(r) }]);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h2>7. Ask the assistant</h2>
      {messages.length === 0 && (
        <p style={{ color: "var(--muted)" }}>
          Ask about scores, evidence or the ranking. Try:{" "}
          {SUGGESTIONS.map((s) => (
            <button key={s} onClick={() => ask(s)} disabled={busy}
                    style={{ margin: "0 6px 6px 0", padding: "4px 10px", fontSize: 13 }}>{s}</button>
          ))}
        </p>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {messages.map((m, i) => (
          <div key={i} style={{
            alignSelf: m.role === "user" ? "flex-end" : "flex-start",
            maxWidth: "85%", whiteSpace: m.role === "user" ? "pre-wrap" : "normal",
            padding: "10px 14px", borderRadius: 14,
            background: m.role === "user" ? "var(--accent)" : "var(--panel)",
            border: m.role === "user" ? "0" : "1px solid var(--line)",
            color: m.role === "user" ? "#fff" : "var(--text)",
          }}>
            {m.role === "user" ? m.content : (
              <div className="md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content.replace(/\u3010/g, "[").replace(/\u3011/g, "]")}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}
        {busy && <div style={{ color: "var(--muted)" }}>Thinking... (the first answer can take a while)</div>}
      </div>
      {error && <p style={{ color: "crimson" }}>Chat error: {error}</p>}
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <input style={{ flex: 1 }} placeholder="Ask a question about this evaluation..."
               value={input} onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") ask(); }} />
        <button onClick={() => ask()} disabled={busy || !input.trim()}>Send</button>
        <button onClick={() => { setMessages([]); setError(""); }} disabled={busy}
                style={{ background: "var(--panel2)" }}>Clear</button>
      </div>
    </div>
  );
}

