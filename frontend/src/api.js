const BASE = "http://localhost:8000";

async function req(path, options) {
  const r = await fetch(BASE + path, options);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch {}
    throw new Error(msg);
  }
  if (r.status === 204) return null;
  return r.json();
}

export const getRfp = () => req("/rfp");
export const getVendors = () => req("/vendors");
export const getStatus = (id) => req(`/evaluations/${id}`);

export function uploadRfp(file) {
  const f = new FormData();
  f.append("file", file);
  return req("/rfp", { method: "POST", body: f });
}

export function uploadVendorFiles(name, files) {
  const f = new FormData();
  for (const file of files) f.append("files", file);
  return req(`/vendors/${encodeURIComponent(name)}/files`, { method: "POST", body: f });
}

export function startEvaluation(mock, targets) {
  return req("/evaluations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mock, targets }),
  });
}

export function getRanking(id, minScore, shortlistN) {
  const q = new URLSearchParams({ min_score: minScore, shortlist_n: shortlistN });
  return req(`/evaluations/${id}/ranking?${q}`);
}

export const getVendor = (id, name) => req(`/evaluations/${id}/vendors/${encodeURIComponent(name)}`);

export const downloadUrl = (id, format, minScore, shortlistN) =>
  `${BASE}/evaluations/${id}/download?` + new URLSearchParams({ format, min_score: minScore, shortlist_n: shortlistN });

export const sendChat = (id, question, history) =>
  req(`/evaluations/${id}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history }),
  });

export const listEvaluations = () => req("/evaluations");

export const overrideScore = (id, itemId, vendor, score, reason) =>
  req(`/evaluations/${id}/scores/${encodeURIComponent(itemId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vendor, score, reason }),
  });

export const deleteVendor = (name) => req(`/vendors/${encodeURIComponent(name)}`, { method: "DELETE" });
export const deleteVendorFile = (name, file) =>
  req(`/vendors/${encodeURIComponent(name)}/files/${encodeURIComponent(file)}`, { method: "DELETE" });


export const previewCosts = (currency, mock) =>
  req("/cost-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ currency, mock }),
  });
