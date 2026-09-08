"""Embedding boundary. Vertex's Gemini embedding model in production, a deterministic fake
in tests. Documents and queries use different task types, which matters for retrieval."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from google.genai import types

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


@dataclass(frozen=True)
class Embedding:
    vector: list[float]
    tokens: int


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str], task: TaskType) -> list[Embedding]: ...


class FakeEmbedder:
    """Hash-based unit vectors: deterministic, and texts sharing words land closer together."""

    def __init__(self, dimensions: int = 256) -> None:
        self._dims = dimensions

    @property
    def model_id(self) -> str:
        return "fake-embedding"

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed(self, texts: Sequence[str], task: TaskType) -> list[Embedding]:
        out: list[Embedding] = []
        for text in texts:
            vec = [0.0] * self._dims
            for word in text.lower().split():
                h = int(hashlib.sha256(word.encode()).hexdigest(), 16)
                vec[h % self._dims] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append(Embedding([v / norm for v in vec], tokens=max(1, len(text) // 4)))
        return out


class VertexEmbedder:
    """One text per request. gemini-embedding-2 treats a list of contents as the parts of a
    single multimodal input and returns one vector for all of them, silently; the older
    gemini-embedding-001 batched. Requests run on a small thread pool instead."""

    def __init__(
        self,
        model_id: str,
        *,
        project: str,
        location: str = "global",
        dimensions: int = 768,
        threads: int = 4,
        max_attempts: int = 6,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        from google import genai

        self._model_id = model_id
        self._dims = dimensions
        self._threads = threads
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._client = genai.Client(vertexai=True, project=project, location=location)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dims

    def _call(self, text: str, task: TaskType) -> types.EmbedContentResponse:
        """Rate limits (429) and transient 5xx back off and retry; anything else raises."""
        from google.genai import errors, types

        config = types.EmbedContentConfig(task_type=task, output_dimensionality=self._dims)
        delay = 1.0
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._client.models.embed_content(  # pyright: ignore[reportUnknownMemberType]
                    model=self._model_id, contents=text, config=config
                )
            except errors.APIError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self._max_attempts:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise
        raise AssertionError("unreachable")

    def _one(self, text: str, task: TaskType) -> Embedding:
        response = self._call(text, task)
        embeddings: list[types.ContentEmbedding] = list(response.embeddings or [])
        if len(embeddings) != 1:
            raise RuntimeError(f"expected one embedding, got {len(embeddings)}")
        e = embeddings[0]
        vector = list(e.values or [])
        if len(vector) != self._dims:
            raise RuntimeError(f"expected {self._dims} dimensions, got {len(vector)}")
        tokens = int(e.statistics.token_count or 0) if e.statistics else 0
        return Embedding(vector, tokens)

    def embed(self, texts: Sequence[str], task: TaskType) -> list[Embedding]:
        from concurrent.futures import ThreadPoolExecutor

        def one(text: str) -> Embedding:
            return self._one(text, task)

        with ThreadPoolExecutor(max_workers=self._threads) as pool:
            return list(pool.map(one, texts))
