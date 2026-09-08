"""Stage 3 extract job: documents in, model answers out, every outcome recorded.

Invariants, mirroring the parse job:
  * A document is processed at most once per (model, prompt): the extraction and quarantine
    tables define processed-ness, so a replay never pays for the same answer twice.
  * A batch is pinned in the ledger (a `started` row listing document ids) before any call.
    A resume processes only the pinned documents that have no row yet, so an answer is
    never paid for twice and a batch never mixes answers from two attempts. Loads are keyed
    on batch and attempt: a retried load of the same attempt is a no-op, a new attempt
    appends only rows that did not exist.
  * Every document in a batch ends in extractions, quarantine, or both (low confidence keeps
    the answer and files a quarantine row). A pinned id whose document has vanished is
    quarantined as MissingDocument. Nothing is dropped.
  * The job writes the validated JSON only. Flattened values are derived from it by dbt,
    so there is one source of truth and no dual write to fall out of step.
  * Cost is measured from the token counts each response returns. A real model without a
    price in the table is refused, never recorded as free.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from filing_facts.config import Settings
from filing_facts.extract.model import ExtractionError, ExtractionModel, ModelResponse
from filing_facts.extract.pricing import cost_usd, is_priced
from filing_facts.extract.prompt import Prompt
from filing_facts.extract.rows import DocumentText, ExtractionRow, ExtractRunRecord, ExtractStatus
from filing_facts.parse.rows import QuarantineRow
from filing_facts.parse.spool import Spool
from filing_facts.storage.protocols import ExtractSink

log = structlog.get_logger(__name__)
STAGE = "extract"


@dataclass(frozen=True)
class ExtractOutcome:
    status: ExtractStatus
    record: ExtractRunRecord


class UnpricedModelError(ExtractionError):
    """A real model with no entry in the price table. Cost must be measured, not zero."""


@dataclass
class _Counts:
    extracted: int = 0
    quarantined: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def batch_id_for(model: str, prompt_id: str, document_ids: list[str]) -> str:
    key = "\n".join([model, prompt_id, *sorted(document_ids)])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def run(
    settings: Settings,
    *,
    sink: ExtractSink,
    model: ExtractionModel,
    prompt: Prompt,
    cap: int | None = None,
    min_confidence: float | None = None,
    threads: int | None = None,
    now: datetime | None = None,
) -> ExtractOutcome:
    started = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())
    cap = settings.extract_cap if cap is None else cap
    min_conf = settings.extract_min_confidence if min_confidence is None else min_confidence
    workers = settings.extract_threads if threads is None else threads
    bound = log.bind(run_id=run_id, model=model.model_id, prompt=prompt.id, cap=cap)

    def record(
        status: ExtractStatus,
        batch_id: str | None,
        selected: int,
        counts: _Counts,
        error: str | None = None,
        document_ids: list[str] | None = None,
    ) -> ExtractRunRecord:
        rec = ExtractRunRecord(
            run_id=run_id,
            model=model.model_id,
            prompt_id=prompt.id,
            batch_id=batch_id,
            status=status,
            cap=cap,
            selected=selected,
            extracted=counts.extracted,
            quarantined=counts.quarantined,
            input_tokens=counts.input_tokens,
            output_tokens=counts.output_tokens,
            cost_usd=round(counts.cost_usd, 6),
            started_at=started,
            finished_at=datetime.now(UTC),
            error=error,
            document_ids=document_ids or [],
        )
        sink.record_run(rec)
        return rec

    batch_id: str | None = None
    docs: list[DocumentText] = []
    missing: list[str] = []
    try:
        if not is_fake(model.model_id) and not is_priced(model.model_id):
            raise UnpricedModelError(model.model_id)
        resumed = sink.open_batch(model.model_id, prompt.id)
        if resumed is not None:
            batch_id, ids = resumed
            done = sink.processed_ids(model.model_id, prompt.id)
            todo = [i for i in ids if i not in done]
            found = {d.document_id: d for d in sink.documents_by_id(todo)}
            docs = [found[i] for i in todo if i in found]
            missing = [i for i in todo if i not in found]
            bound.info(
                "resuming_batch",
                batch_id=batch_id,
                pinned=len(ids),
                todo=len(todo),
                missing=len(missing),
            )
        else:
            docs = sink.pending_documents(model.model_id, prompt.id, cap)
            if not docs:
                bound.info("nothing_pending")
                return ExtractOutcome(
                    "skipped_existing", record("skipped_existing", None, 0, _Counts())
                )
            batch_id = batch_id_for(model.model_id, prompt.id, [d.document_id for d in docs])
            record(
                "started",
                batch_id,
                len(docs),
                _Counts(),
                document_ids=[d.document_id for d in docs],
            )
        with tempfile.TemporaryDirectory(prefix="filing_facts_extract_") as tmp:
            counts = _process(
                sink,
                model,
                prompt,
                docs,
                missing,
                batch_id,
                run_id,
                min_conf,
                workers,
                Path(tmp),
                bound,
            )
        rec = record("succeeded", batch_id, len(docs) + len(missing), counts)
        bound.info("extract_succeeded", batch_id=batch_id, **counts.__dict__)
        return ExtractOutcome("succeeded", rec)
    except Exception as exc:
        bound.error("extract_failed", batch_id=batch_id, error=str(exc))
        rec = record("failed", batch_id, len(docs), _Counts(), error=f"{type(exc).__name__}: {exc}")
        return ExtractOutcome("failed", rec)


def is_fake(model_id: str) -> bool:
    return model_id.startswith("fake")


@dataclass(frozen=True)
class _Result:
    doc: DocumentText
    response: ModelResponse | None
    error: ExtractionError | None


def _call(model: ExtractionModel, prompt: Prompt, doc: DocumentText) -> _Result:
    try:
        return _Result(doc, model.extract(prompt, doc.text), None)
    except ExtractionError as exc:
        return _Result(doc, None, exc)
    except Exception as exc:
        return _Result(doc, None, ExtractionError(f"Unexpected:{type(exc).__name__}: {exc}"))


def _process(
    sink: ExtractSink,
    model: ExtractionModel,
    prompt: Prompt,
    docs: list[DocumentText],
    missing: list[str],
    batch_id: str,
    run_id: str,
    min_conf: float,
    workers: int,
    tmp: Path,
    bound: structlog.stdlib.BoundLogger,
) -> _Counts:
    now = datetime.now(UTC)
    meta = {"model": model.model_id, "prompt_id": prompt.id, "attempt": run_id}
    extractions = Spool(tmp / "extractions.ndjson", **meta)
    quarantine = Spool(tmp / "quarantine.ndjson", **meta)
    counts = _Counts()
    priced = is_priced(model.model_id)

    def quarantined(document_id: str, source_key: str, reason: str, error: str) -> None:
        quarantine.append(
            QuarantineRow(
                document_id=document_id,
                source_key=source_key,
                member_name=document_id,
                stage=STAGE,
                reason=reason,
                error=error[:1000],
                batch_id=batch_id,
                quarantined_at=now,
                model=model.model_id,
                prompt_id=prompt.id,
            )
        )
        counts.quarantined += 1

    for document_id in missing:
        quarantined(
            document_id, "", "MissingDocument", "pinned to the batch but absent from documents"
        )

    def call(doc: DocumentText) -> _Result:
        return _call(model, prompt, doc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for res in pool.map(call, docs):
            if res.error is not None or res.response is None:
                err = res.error or ExtractionError("no response")
                reason = type(err).__name__
                if reason == "ExtractionError":  # bare, from the unexpected-exception wrapper
                    reason = str(err).split(":", 1)[0]
                quarantined(res.doc.document_id, res.doc.source_key, reason, str(err))
                continue
            r = res.response
            cost = (
                cost_usd(model.model_id, r.input_tokens, r.output_tokens, r.thinking_tokens)
                if priced
                else 0.0
            )
            counts.input_tokens += r.input_tokens
            counts.output_tokens += r.output_tokens + r.thinking_tokens
            counts.cost_usd += cost
            if r.extraction is None:
                quarantined(
                    res.doc.document_id,
                    res.doc.source_key,
                    "SchemaValidationError",
                    f"{r.validation_error}\n---\n{r.raw_text[:500]}",
                )
                continue
            e = r.extraction
            status = "ok" if e.overall_confidence >= min_conf else "low_confidence"
            extractions.append(
                ExtractionRow(
                    document_id=res.doc.document_id,
                    source_key=res.doc.source_key,
                    model=model.model_id,
                    prompt_id=prompt.id,
                    prompt_version=prompt.version,
                    status=status,
                    overall_confidence=e.overall_confidence,
                    extraction_json=json.dumps(e.model_dump(), separators=(",", ":")),
                    input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens,
                    thinking_tokens=r.thinking_tokens,
                    cost_usd=round(cost, 8),
                    latency_ms=r.latency_ms,
                    finish_reason=r.finish_reason,
                    batch_id=batch_id,
                    extracted_at=now,
                )
            )
            counts.extracted += 1
            if status == "low_confidence":
                quarantined(
                    res.doc.document_id,
                    res.doc.source_key,
                    "LowConfidence",
                    f"overall_confidence={e.overall_confidence} < {min_conf}",
                )

    for spool in (extractions, quarantine):
        spool.close()
    bound.info("extracted_batch", batch_id=batch_id, documents=len(docs), **counts.__dict__)
    written = {
        "quarantine": sink.write_quarantine(batch_id, quarantine),
        "extractions": sink.write_extractions(batch_id, extractions),
    }
    bound.info("batch_written", batch_id=batch_id, **written)
    return counts
