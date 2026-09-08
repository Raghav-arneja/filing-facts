"""Embedding boundary. Vertex's Gemini embedding model in production, a deterministic fake
in tests. Documents and queries use different task types, which matters for retrieval."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

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
    def __init__(
        self,
        model_id: str,
        *,
        project: str,
        location: str = "global",
        dimensions: int = 768,
        batch_size: int = 32,
    ) -> None:
        from google import genai

        self._model_id = model_id
        self._dims = dimensions
        self._batch = batch_size
        self._client = genai.Client(vertexai=True, project=project, location=location)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed(self, texts: Sequence[str], task: TaskType) -> list[Embedding]:
        from google.genai import types

        out: list[Embedding] = []
        for i in range(0, len(texts), self._batch):
            batch = list(texts[i : i + self._batch])
            response = self._client.models.embed_content(  # pyright: ignore[reportUnknownMemberType]
                model=self._model_id,
                contents=batch,
                config=types.EmbedContentConfig(task_type=task, output_dimensionality=self._dims),
            )
            for e in response.embeddings or []:
                tokens = int(e.statistics.token_count or 0) if e.statistics else 0
                out.append(Embedding(list(e.values or []), tokens))
        if len(out) != len(texts):
            raise RuntimeError(f"embedded {len(out)} of {len(texts)} texts")
        return out
