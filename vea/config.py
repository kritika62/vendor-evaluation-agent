import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    embed_model: str = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    rerank_model: str = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    k_retrieve: int = 12      # candidates from the vector index
    k_final: int = 4          # chunks kept after reranking
    n_samples: int = 1        # >1 = self-consistency (median score, spread check)
    max_chunk_chars: int = 900
    overlap_chars: int = 150
    mock: bool = False        # offline mode: TF-IDF retrieval + heuristic scorer
