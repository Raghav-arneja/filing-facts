"""Event schemas. Two channels at different granularity, per the architecture:

  * lifecycle: one message per job outcome that another stage may act on. Low volume,
    consumed by the dispatcher, dead-lettered when malformed.
  * documents: one message per parsed document. High volume, written to BigQuery by a
    subscription for visibility; no code consumer yet.

Every message carries a schema version so a consumer can refuse what it does not know.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LifecycleKind = Literal["ingested", "parsed", "extracted"]


class LifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    event: LifecycleKind
    source_key: str | None = Field(default=None, min_length=1)  # required unless extracted
    run_id: str = Field(min_length=1)
    batch_id: str | None = None
    occurred_at: datetime
    model: str | None = None  # extracted events only
    prompt_id: str | None = None  # extracted events only

    @model_validator(mode="after")
    def _source_key_rules(self) -> LifecycleEvent:
        if self.event in ("ingested", "parsed") and not self.source_key:
            raise ValueError(f"{self.event} events need a source_key")
        if self.event == "extracted" and not (self.batch_id and self.model and self.prompt_id):
            raise ValueError("extracted events need batch_id, model and prompt_id")
        return self


class DocumentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    document_id: str = Field(min_length=1)
    source_key: str
    batch_id: str
    fact_count: int
    text_chars: int
    parsed_at: datetime
