"""Row shapes written by the extract job. Mirrored by the BigQuery schemas in infra/."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ExtractStatus = Literal["started", "succeeded", "skipped_existing", "failed"]
RowStatus = Literal["ok", "low_confidence"]


@dataclass(frozen=True)
class DocumentText:
    document_id: str
    source_key: str
    text: str


@dataclass(frozen=True)
class ExtractionRow:
    """One model answer for one document under one prompt. The JSON is the validated answer."""

    document_id: str
    source_key: str
    model: str
    prompt_id: str
    prompt_version: str
    status: RowStatus
    overall_confidence: float
    extraction_json: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cost_usd: float
    latency_ms: int
    finish_reason: str | None
    batch_id: str
    extracted_at: datetime


@dataclass(frozen=True)
class ExtractRunRecord:
    run_id: str
    model: str
    prompt_id: str
    batch_id: str | None
    status: ExtractStatus
    cap: int
    selected: int
    extracted: int
    quarantined: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    started_at: datetime
    finished_at: datetime
    error: str | None = None
    document_ids: list[str] = field(default_factory=list[str])  # started rows only


def extract_run_key(record: ExtractRunRecord) -> str:
    if record.status == "succeeded":
        return f"extract-ok-{record.model}-{record.prompt_id}-{record.batch_id}"
    return f"extract-{record.status}-{record.run_id}"
