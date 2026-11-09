"""Offline stand-ins for the Gemini embedding and text models."""

import hashlib
import re

import numpy as np
from app.memory import Memory, VectorStore
from app.memory.embeddings import normalize
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

DIM = 64


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


class FakeEmbedder:
    """Bag of words hashed into DIM buckets: texts that share words are close."""

    dim = DIM

    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        self.calls.append(list(texts))
        rows = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in words(text):
                rows[i, int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1
        return normalize(rows)


def fake_memory(path=":memory:", chunk_size: int = 400, overlap: int = 80) -> Memory:
    return Memory(VectorStore(path, DIM), FakeEmbedder(), chunk_size, overlap)


class ScriptedLlm(BaseLlm):
    """Plays a fixed conversation: each turn is a list of function calls (searches)
    or a final text. `{0}` in the text is replaced with the first retrieved id."""

    model: str = "scripted"
    turns: list = []
    requests: list = []

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self.requests.append(llm_request)
        step = self.turns[len(self.requests) - 1]
        if isinstance(step, list):
            parts = [
                types.Part(function_call=types.FunctionCall(name=name, args=args))
                for name, args in step
            ]
        else:
            first = ""
            for content in llm_request.contents:
                for part in content.parts or []:
                    resp = part.function_response
                    if resp and resp.response and resp.response.get("results"):
                        first = first or resp.response["results"][0]["id"]
            parts = [types.Part(text=step.replace("{0}", first))]
        yield LlmResponse(content=types.Content(role="model", parts=parts))
