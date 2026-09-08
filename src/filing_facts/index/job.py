"""Stage 6 index job: documents in, embedded chunks out. Same shape as the extract job:
pinned batches, resume without re-paying, loads keyed on batch and attempt, cost measured
from the token counts the embedding API returns."""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from filing_facts.config import Settings
from filing_facts.extract.pricing import cost_usd, is_priced
from filing_facts.extract.rows import DocumentText
from filing_facts.index.chunker import chunk_text
from filing_facts.index.embedder import Embedder
from filing_facts.index.rows import ChunkRow, IndexRunRecord, IndexStatus
from filing_facts.parse.spool import Spool
from filing_facts.storage.protocols import IndexSink
from filing_facts.telemetry import tracer

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IndexOutcome:
    status: IndexStatus
    record: IndexRunRecord


@dataclass
class _Counts:
    indexed: int = 0
    chunks: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


def batch_id_for(model: str, document_ids: list[str]) -> str:
    return hashlib.sha256("\n".join([model, *sorted(document_ids)]).encode()).hexdigest()[:16]


def run(
    settings: Settings,
    *,
    sink: IndexSink,
    embedder: Embedder,
    cap: int | None = None,
    now: datetime | None = None,
) -> IndexOutcome:
    started = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())
    cap = settings.index_cap if cap is None else cap
    model = embedder.model_id
    with tracer(__name__).start_as_current_span("index.run") as span:
        span.set_attributes({"run_id": run_id, "embedding_model": model, "cap": cap})
        outcome = _run(settings, sink, embedder, cap, started, run_id)
        span.set_attribute("status", outcome.status)
        return outcome


def _run(
    settings: Settings,
    sink: IndexSink,
    embedder: Embedder,
    cap: int,
    started: datetime,
    run_id: str,
) -> IndexOutcome:
    model = embedder.model_id
    bound = log.bind(run_id=run_id, embedding_model=model, cap=cap)

    def record(
        status: IndexStatus,
        batch_id: str | None,
        selected: int,
        counts: _Counts,
        error: str | None = None,
        document_ids: list[str] | None = None,
    ) -> IndexRunRecord:
        rec = IndexRunRecord(
            run_id=run_id,
            embedding_model=model,
            batch_id=batch_id,
            status=status,
            cap=cap,
            selected=selected,
            indexed=counts.indexed,
            chunks=counts.chunks,
            tokens=counts.tokens,
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
    counts = _Counts()
    try:
        if not model.startswith("fake") and not is_priced(model):
            raise ValueError(f"unpriced embedding model {model}")
        resumed = sink.open_batch(model)
        if resumed is not None:
            batch_id, ids = resumed
            done = sink.processed_ids(model)
            docs = sink.documents_by_id([i for i in ids if i not in done])
            bound.info("resuming_batch", batch_id=batch_id, pinned=len(ids), todo=len(docs))
        else:
            docs = sink.pending_documents(model, cap)
            if not docs:
                bound.info("nothing_pending")
                return IndexOutcome(
                    "skipped_existing", record("skipped_existing", None, 0, _Counts())
                )
            batch_id = batch_id_for(model, [d.document_id for d in docs])
            record(
                "started",
                batch_id,
                len(docs),
                _Counts(),
                document_ids=[d.document_id for d in docs],
            )
        with tempfile.TemporaryDirectory(prefix="filing_facts_index_") as tmp:
            _process(settings, sink, embedder, docs, batch_id, run_id, Path(tmp), bound, counts)
        rec = record("succeeded", batch_id, len(docs), counts)
        bound.info("index_succeeded", batch_id=batch_id, **counts.__dict__)
        return IndexOutcome("succeeded", rec)
    except Exception as exc:
        bound.error("index_failed", batch_id=batch_id, error=str(exc))
        # counts hold what was embedded (and paid for) before the failure
        rec = record("failed", batch_id, len(docs), counts, error=f"{type(exc).__name__}: {exc}")
        return IndexOutcome("failed", rec)


def _process(
    settings: Settings,
    sink: IndexSink,
    embedder: Embedder,
    docs: list[DocumentText],
    batch_id: str,
    run_id: str,
    tmp: Path,
    bound: structlog.stdlib.BoundLogger,
    counts: _Counts,
) -> None:
    """Chunks are loaded every `index_flush_docs` documents, each part keyed on batch, attempt
    and part number, so a crash keeps what was embedded and a resume pays only for the rest."""
    now = datetime.now(UTC)
    priced = is_priced(embedder.model_id)
    span_tracer = tracer(__name__)
    part = 0
    spool = _new_spool(tmp, embedder.model_id, run_id, part)
    since_flush = 0

    def flush() -> None:
        nonlocal spool, part, since_flush
        spool.close()
        if spool.count:
            written = sink.write_chunks(batch_id, spool)
            bound.info("part_written", batch_id=batch_id, part=part, rows=spool.count, new=written)
        part += 1
        since_flush = 0
        spool = _new_spool(tmp, embedder.model_id, run_id, part)

    for doc in docs:
        with span_tracer.start_as_current_span(
            "index.document", attributes={"document_id": doc.document_id}
        ):
            chunks = chunk_text(
                doc.text, max_chars=settings.chunk_chars, overlap_lines=settings.chunk_overlap_lines
            )
            if not chunks:
                continue
            embeddings = embedder.embed([c.text for c in chunks], "RETRIEVAL_DOCUMENT")
            for chunk, emb in zip(chunks, embeddings, strict=True):
                spool.append(
                    ChunkRow(
                        document_id=doc.document_id,
                        chunk_index=chunk.index,
                        embedding_model=embedder.model_id,
                        text=chunk.text,
                        chars=len(chunk.text),
                        tokens=emb.tokens,
                        embedding=emb.vector,
                        batch_id=batch_id,
                        indexed_at=now,
                    )
                )
                counts.chunks += 1
                counts.tokens += emb.tokens
            counts.indexed += 1
            since_flush += 1
            if priced:
                counts.cost_usd = cost_usd(embedder.model_id, counts.tokens, 0)
        if since_flush >= settings.index_flush_docs:
            flush()
    flush()
    bound.info("indexed_batch", batch_id=batch_id, parts=part, **counts.__dict__)


def _new_spool(tmp: Path, model: str, run_id: str, part: int) -> Spool:
    return Spool(tmp / f"chunks-{part}.ndjson", model=model, attempt=f"{run_id}-p{part}")
