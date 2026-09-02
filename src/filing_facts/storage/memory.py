"""In-memory backends for tests. Same contract as GCS and BigQuery, no network."""

from __future__ import annotations

from pathlib import Path

from filing_facts.storage.protocols import AlreadyExistsError, RunRecord


class MemoryRawStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def uri_for(self, key: str) -> str:
        return f"memory://{key}"

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
