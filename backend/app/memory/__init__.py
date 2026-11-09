"""Long-term memory and retrieval (RAG) for Mario. See service.py."""

from google import genai
from google.genai import types

from ..config import Settings
from .citations import Checked, check_citations
from .embeddings import Embedder, GeminiEmbedder
from .service import IndexReport, Memory
from .store import Hit, Record, VectorStore

__all__ = [
    "Checked",
    "Embedder",
    "GeminiEmbedder",
    "Hit",
    "IndexReport",
    "Memory",
    "Record",
    "VectorStore",
    "check_citations",
    "gemini_client",
    "memory_from_settings",
]


def gemini_client(settings: Settings) -> genai.Client:
    # Retries 429/5xx with backoff: the free tier and "high demand" 503s are common.
    retry = types.HttpRetryOptions(attempts=5, initial_delay=2.0, max_delay=30.0)
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(retry_options=retry),
    )


def memory_from_settings(settings: Settings, client: genai.Client | None = None) -> Memory:
    embedder = GeminiEmbedder(
        client or gemini_client(settings), settings.embedding_model, settings.embedding_dim
    )
    store = VectorStore(settings.memory_db, settings.embedding_dim)
    return Memory(store, embedder, settings.chunk_size, settings.chunk_overlap)
