"""In-memory backends for tests. Same contract as GCS and BigQuery, no network."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from filing_facts.extract.rows import DocumentText, ExtractRunRecord, extract_run_key
from filing_facts.parse.rows import ParseRunRecord, parse_run_key
from filing_facts.parse.spool import Spool
from filing_facts.storage.protocols import AlreadyExistsError, RunRecord


class MemoryRawStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def uri_for(self, key: str) -> str:
        return f"memory://{key}"

    def fetch(self, key: str, dest: Path) -> None:
        if key not in self.objects:
            raise KeyError(key)
        dest.write_bytes(self.objects[key][0])

    def put(self, key: str, path: Path, sha256: str) -> str:
        if key in self.objects:
            raise AlreadyExistsError(key, self.objects[key][1])
        self.objects[key] = (path.read_bytes(), sha256)
        return self.uri_for(key)


class MemoryRunLog:
    def __init__(self) -> None:
        self.records: list[RunRecord] = []
        self._dedupe: set[str] = set()

    def has_succeeded(self, source_key: str) -> bool:
        return any(r.source_key == source_key and r.status == "succeeded" for r in self.records)

    def succeeded_keys(self) -> list[str]:
        return [r.source_key for r in self.records if r.status == "succeeded"]

    def record(self, record: RunRecord) -> bool:
        key = dedupe_key(record)
        if key in self._dedupe:
            return False
        self._dedupe.add(key)
        self.records.append(record)
        return True


def dedupe_key(record: RunRecord) -> str:
    """One successful record per source_key; every other outcome is per run.

    BigQuery uses this as the load job id, which it enforces as unique per project.
    """
    if record.status == "succeeded":
        return f"ingest-ok-{record.source_key}"
    return f"ingest-{record.status}-{record.run_id}"


class MemoryParseSink:
    """Rows are kept as the JSON-safe dicts the spool produced, exactly what BigQuery sees."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.facts: list[dict[str, Any]] = []
        self.quarantine: list[dict[str, Any]] = []
        self.runs: list[ParseRunRecord] = []
        self._batches: set[str] = set()

    def processed_members(self, source_key: str) -> set[str]:
        rows = self.documents + self.quarantine
        return {str(r["member_name"]) for r in rows if r["source_key"] == source_key}

    def open_batch(self, source_key: str) -> tuple[str, list[str]] | None:
        finished = {r.batch_id for r in self.runs if r.status == "succeeded"}
        started = [
            r
            for r in self.runs
            if r.source_key == source_key and r.status == "started" and r.batch_id not in finished
        ]
        if not started:
            return None
        last = max(started, key=lambda r: r.started_at)
        return last.batch_id or "", list(last.members)

    def succeeded_caps(self) -> dict[str, int]:
        caps: dict[str, int] = {}
        for r in self.runs:
            if r.status in ("succeeded", "skipped_existing"):
                caps[r.source_key] = max(caps.get(r.source_key, -1), r.cap)
        return caps

    def _once(self, key: str) -> bool:
        if key in self._batches:
            return False
        self._batches.add(key)
        return True

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool:
        if not self._once(f"quarantine:{batch_id}"):
            return False
        self.quarantine.extend(spool.rows())
        return True

    def write_facts(self, batch_id: str, spool: Spool) -> bool:
        if not self._once(f"facts:{batch_id}"):
            return False
        self.facts.extend(spool.rows())
        return True

    def write_documents(self, batch_id: str, spool: Spool) -> bool:
        if not self._once(f"documents:{batch_id}"):
            return False
        self.documents.extend(spool.rows())
        return True

    def record_run(self, record: ParseRunRecord) -> bool:
        if not self._once(f"runs:{parse_run_key(record)}"):
            return False
        self.runs.append(record)
        return True


def stable_order(document_id: str) -> str:
    return hashlib.sha256(document_id.encode()).hexdigest()


class MemoryExtractSink:
    def __init__(self, documents: list[DocumentText] | None = None) -> None:
        self.source_documents: list[DocumentText] = list(documents or [])
        self.extractions: list[dict[str, Any]] = []
        self.quarantine: list[dict[str, Any]] = []
        self.runs: list[ExtractRunRecord] = []
        self._batches: set[str] = set()

    def processed_ids(self, model: str, prompt_id: str) -> set[str]:
        ids = {
            str(r["document_id"])
            for r in self.extractions
            if r["model"] == model and r["prompt_id"] == prompt_id
        }
        ids |= {
            str(r["document_id"])
            for r in self.quarantine
            if r["stage"] == "extract" and r["model"] == model and r["prompt_id"] == prompt_id
        }
        return ids

    def pending_documents(self, model: str, prompt_id: str, cap: int) -> list[DocumentText]:
        done = self.processed_ids(model, prompt_id)
        todo = [d for d in self.source_documents if d.document_id not in done]
        todo.sort(key=lambda d: stable_order(d.document_id))
        return todo[:cap]

    def documents_by_id(self, ids: list[str]) -> list[DocumentText]:
        wanted = set(ids)
        return [d for d in self.source_documents if d.document_id in wanted]

    def open_batch(self, model: str, prompt_id: str) -> tuple[str, list[str]] | None:
        finished = {r.batch_id for r in self.runs if r.status == "succeeded"}
        started = [
            r
            for r in self.runs
            if r.model == model
            and r.prompt_id == prompt_id
            and r.status == "started"
            and r.batch_id not in finished
        ]
        if not started:
            return None
        last = max(started, key=lambda r: r.started_at)
        return last.batch_id or "", list(last.document_ids)

    def _once(self, key: str) -> bool:
        if key in self._batches:
            return False
        self._batches.add(key)
        return True

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool:
        if not self._once(f"xq:{batch_id}:{spool.attempt}"):
            return False
        self.quarantine.extend(spool.rows())
        return True

    def write_extractions(self, batch_id: str, spool: Spool) -> bool:
        if not self._once(f"xe:{batch_id}:{spool.attempt}"):
            return False
        self.extractions.extend(spool.rows())
        return True

    def record_run(self, record: ExtractRunRecord) -> bool:
        if not self._once(f"xr:{extract_run_key(record)}"):
            return False
        self.runs.append(record)
        return True
