"""Row shapes written by the parse job. Mirrored by the BigQuery schemas in infra/."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from filing_facts.parse.ixbrl import Context, Fact

ParseStatus = Literal["started", "succeeded", "skipped_existing", "failed"]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")  # never exponent notation: 1.000E+6 -> 1000000
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def to_row(obj: Any) -> dict[str, Any]:
    return {k: _json_safe(v) for k, v in asdict(obj).items()}


@dataclass(frozen=True)
class DocumentRow:
    document_id: str
    source_key: str
    member_name: str
    company_number: str
    period_end: date
    byte_count: int
    sha256: str
    ix_namespace: str
    entity_identifier: str | None
    fact_count: int
    text: str
    batch_id: str
    parsed_at: datetime


@dataclass(frozen=True)
class FactRow:
    document_id: str
    concept: str
    namespace: str
    context_id: str
    is_numeric: bool
    value: Decimal | None
    text: str
    unit: str | None
    decimals: str | None
    format: str | None
    scale: int | None
    sign: str | None
    in_hidden: bool
    period_start: date | None
    period_end: date | None
    instant: date | None
    dimensional: bool
    batch_id: str

    @classmethod
    def from_fact(cls, document_id: str, fact: Fact, ctx: Context | None, batch_id: str) -> FactRow:
        return cls(
            document_id=document_id,
            **asdict(fact),
            period_start=ctx.start_date if ctx else None,
            period_end=ctx.end_date if ctx else None,
            instant=ctx.instant if ctx else None,
            dimensional=ctx.dimensional if ctx else False,
            batch_id=batch_id,
        )


@dataclass(frozen=True)
class QuarantineRow:
    document_id: str | None  # None when the member name itself is unrecognised
    source_key: str
    member_name: str
    stage: str
    reason: str
    error: str
    batch_id: str
    quarantined_at: datetime
    model: str | None = None  # extract stage only
    prompt_id: str | None = None  # extract stage only


@dataclass(frozen=True)
class ParseRunRecord:
    """One row per job execution. A `started` row pins the batch so a replay resumes it."""

    run_id: str
    source_key: str
    batch_id: str | None
    status: ParseStatus
    cap: int
    selected: int
    parsed: int
    quarantined: int
    facts: int
    started_at: datetime
    finished_at: datetime
    error: str | None = None
    members: list[str] = field(default_factory=list[str])  # populated on `started` rows only


def parse_run_key(record: ParseRunRecord) -> str:
    """Idempotency key for a ledger row. One success per batch; everything else per run."""
    if record.status == "succeeded":
        return f"parse-ok-{record.source_key}-{record.batch_id}"
    return f"parse-{record.status}-{record.run_id}"
