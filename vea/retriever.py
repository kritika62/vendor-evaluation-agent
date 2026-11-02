"""Evidence retrieval: dense bi-encoder search (+ optional cross-encoder rerank),
with an offline TF-IDF fallback used in --mock mode."""
import numpy as np

_MODEL_CACHE = {}


def _get(kind, name):
    key = (kind, name)
    if key not in _MODEL_CACHE:
        if kind == "embed":
            from sentence_transformers import SentenceTransformer
            _MODEL_CACHE[key] = SentenceTransformer(name)
        else:
            from sentence_transformers import CrossEncoder
            _MODEL_CACHE[key] = CrossEncoder(name)
    return _MODEL_CACHE[key]


class Retriever:
    def __init__(self, chunks, settings):
        self.chunks = chunks
        self.s = settings
        self.texts = [c.text for c in chunks]
        self.dense = not settings.mock
        if self.dense:
            self.embedder = _get("embed", settings.embed_model)
            vecs = self.embedder.encode(self.texts, normalize_embeddings=True,
                                        show_progress_bar=False).astype("float32")
            try:
                import faiss
                self.index = faiss.IndexFlatIP(vecs.shape[1])
                self.index.add(vecs)
                self._faiss = True
            except ImportError:
                self.vecs, self._faiss = vecs, False
            try:
                self.reranker = _get("rerank", settings.rerank_model)
            except Exception:
                self.reranker = None
        else:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.tfidf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
            self.matrix = self.tfidf.fit_transform(self.texts)

    def search(self, query, k=None):
        """Top chunks for the query. k overrides how many are returned (default settings.k_final)."""
        keep = k or self.s.k_final                       # how many chunks the caller gets back
        n = min(max(self.s.k_retrieve, keep), len(self.chunks))     # how many candidates to look at first
        if self.dense:
            q = self.embedder.encode([query], normalize_embeddings=True).astype("float32")
            if self._faiss:
                _, idx = self.index.search(q, n)
                idx = list(idx[0])
            else:
                idx = list(np.argsort(-(self.vecs @ q[0]))[:n])
            cands = [self.chunks[i] for i in idx if i >= 0]
            if self.reranker is not None and cands:
                scores = self.reranker.predict([(query, c.text) for c in cands])
                cands = [c for _, c in sorted(zip(scores, cands), key=lambda t: -t[0])]
        else:
            q = self.tfidf.transform([query])
            sims = (self.matrix @ q.T).toarray().ravel()
            cands = [self.chunks[i] for i in np.argsort(-sims)[:n] if sims[i] > 0]
        return cands[:keep]
