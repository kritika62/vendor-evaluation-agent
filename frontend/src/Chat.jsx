import { useEffect, useRef, useState } from "react";
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
  "Which vendors hold ISO 27001?",
  "Which vendors failed and why?",
];

export default function Chat({ evalId }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    if (open && endRef.current) endRef.current.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, open]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Open the assistant"
          style={{
            position: "fixed", right: 24, bottom: 24, zIndex: 1000,
            borderRadius: 999, padding: "14px 22px", fontSize: 15,
            boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
          }}
        >
          Ask AI
        </button>
      )}

      {open && (
        <div
          style={{
            position: "fixed", top: 0, right: 0, bottom: 0,
            width: "min(420px, 100vw)", zIndex: 1001,
            background: "var(--panel)", borderLeft: "1px solid var(--line)",
            boxShadow: "-8px 0 30px rgba(0,0,0,0.45)",
            display: "flex", flexDirection: "column",
          }}
        >
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "12px 16px", borderBottom: "1px solid var(--line)",
          }}>
            <b>Assistant</b>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={() => { setMessages([]); setError(""); }}
                disabled={busy}
                style={{ background: "var(--panel2)", padding: "4px 12px", fontSize: 13 }}
              >Clear</button>
              <button
                onClick={() => setOpen(false)}
                aria-label="Close the assistant"
                style={{ background: "var(--panel2)", padding: "4px 12px", fontSize: 13 }}
              >Close</button>
            </div>
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            {messages.length === 0 && (
              <div style={{ color: "var(--muted)" }}>
                <p style={{ marginTop: 0 }}>Ask about scores, evidence or the ranking. Try:</p>
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    disabled={busy}
                    style={{ display: "block", margin: "0 0 8px", padding: "6px 12px", fontSize: 13, textAlign: "left", background: "var(--panel2)" }}
                  >{s}</button>
                ))}
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} style={{
                alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                maxWidth: "90%", whiteSpace: "pre-wrap", wordBreak: "break-word",
                padding: "10px 14px", borderRadius: 14, fontSize: 14,
                background: m.role === "user" ? "var(--accent)" : "var(--panel2)",
                color: m.role === "user" ? "#fff" : "var(--text)",
              }}>{m.content}</div>
            ))}
            {busy && <div style={{ color: "var(--muted)" }}>Thinking... (can take a while)</div>}
            {error && <div style={{ color: "crimson", fontSize: 13 }}>Chat error: {error}</div>}
            <div ref={endRef} />
          </div>

          <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid var(--line)" }}>
            <input
              style={{ flex: 1, minWidth: 0 }}
              placeholder="Ask a question..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") ask(); }}
            />
            <button onClick={() => ask()} disabled={busy || !input.trim()}>Send</button>
          </div>
        </div>
      )}
    </>
  );
}
