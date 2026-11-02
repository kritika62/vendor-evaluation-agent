"""Proposal search for the chat assistant.

main.py saves every vendor's chunks (the exact text the scorer saw) next to the scorecard as
`<scorecard>.chunks.json`. The dashboard loads that file and searches it with the project's own Retriever
(same embedding model, FAISS index and reranker as scoring), so chat answers and scores agree.

    save_chunks(chunks_path_for("output/scorecard.xlsx"), {"ey": [Chunk, ...]})
    search = ProposalSearch(load_chunks(path))
    search.search("does ey mention ISO 27001?", vendors=["ey"])  ->  [(vendor, Chunk), ...]
"""
import json
from pathlib import Path

from .config import Settings
from .ingest import Chunk
from .retriever import Retriever


def chunks_path_for(xlsx_path):
    return Path(xlsx_path).with_suffix(".chunks.json")


def save_chunks(path, chunks_by_vendor):
    data = {v: [{"page": c.page, "text": c.text, "source": c.source} for c in cs]
            for v, cs in chunks_by_vendor.items()}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def parse_chunks(data):
    return {v: [Chunk(v, int(d["page"]), d["text"], d.get("source", "")) for d in items]
            for v, items in data.items()}


def load_chunks(path):
    return parse_chunks(json.loads(Path(path).read_text(encoding="utf-8")))


class ProposalSearch:
    """One Retriever per vendor. Falls back to offline keyword (TF-IDF) search if the embedding model
    cannot be loaded, so the chat keeps working (just less smart)."""

    def __init__(self, chunks_by_vendor, settings=None):
        self.settings = settings or Settings()
        self.mode = "keyword" if self.settings.mock else "semantic"
        self.engines = {}
        for vendor, cs in chunks_by_vendor.items():
            if cs:
                self.engines[vendor] = self._build(cs)

    def _build(self, chunks):
        if self.mode == "semantic":
            try:
                return Retriever(chunks, self.settings)
            except Exception:                                    # no sentence-transformers / model not downloaded
                self.mode = "keyword"
                self.settings = Settings(mock=True)
        return Retriever(chunks, self.settings)

    @property
    def vendors(self):
        return list(self.engines)

    def search(self, question, vendors=None, max_chars=5000):
        """Top passages for the question. vendors: restrict to these (None / no match = all vendors).
        Returns [(vendor, Chunk)], interleaved across vendors so each gets a fair share."""
        names = [v for v in self.engines if vendors and v in vendors] or list(self.engines)
        per = 4 if len(names) == 1 else 3 if len(names) == 2 else 2
        hits = {v: self.engines[v].search(question)[:per] for v in names}
        out, used = [], 0
        for rnd in range(per):
            for v in names:
                if rnd < len(hits[v]):
                    c = hits[v][rnd]
                    if used + len(c.text) > max_chars:
                        return out
                    out.append((v, c)); used += len(c.text)
        return out
