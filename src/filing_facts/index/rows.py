from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

IndexStatus = Literal["started", "succeeded", "skipped_existing", "failed"]


@dataclass(frozen=True)
class ChunkRow:
    document_id: str
    chunk_index: int
    embedding_model: str
    text: str
    chars: int
    tokens: int
    embedding: list[float]
    batch_id: str
    indexed_at: datetime


@dataclass(frozen=True)
class IndexRunRecord:
    run_id: str
    embedding_model: str
    batch_id: str | None
    status: IndexStatus
    cap: int
    selected: int
    indexed: int
    chunks: int
    tokens: int
    cost_usd: float
    started_at: datetime
    finished_at: datetime
    error: str | None = None
    document_ids: list[str] = field(default_factory=list[str])


def index_run_key(record: IndexRunRecord) -> str:
    if record.status == "succeeded":
        return f"index-ok-{record.embedding_model}-{record.batch_id}"
    return f"index-{record.status}-{record.run_id}"
