"""Text → vector. The store only needs `Embedder`; tests pass a fake one."""

from typing import Protocol

import numpy as np
from google import genai
from google.genai import types

BATCH = 100  # embed_content's limit per request


class Embedder(Protocol):
    dim: int

    async def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        """Unit-length float32 rows, one per text."""
        ...


def normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


class GeminiEmbedder:
    """Gemini embeddings with retrieval task types: documents and queries are
    embedded asymmetrically, which ranks better than embedding both the same way.

    gemini-embedding-001 returns one vector per input; at less than its full
    3072 dimensions the vectors are not unit length, so they are normalised here."""

    def __init__(self, client: genai.Client, model: str, dim: int):
        self.client = client
        self.model = model
        self.dim = dim

    async def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        config = types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
            output_dimensionality=self.dim,
        )
        rows: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            res = await self.client.aio.models.embed_content(
                model=self.model, contents=batch, config=config
            )
            vectors = [e.values or [] for e in res.embeddings or []]
            if len(vectors) != len(batch):
                raise RuntimeError(f"{self.model} returned {len(vectors)} vectors for {len(batch)}")
            rows.extend(vectors)
        return normalize(np.array(rows))
