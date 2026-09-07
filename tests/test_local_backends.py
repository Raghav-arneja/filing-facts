from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from filing_facts.storage.local import JsonlRunLog, LocalRawStore
from filing_facts.storage.protocols import AlreadyExistsError, RunRecord


def test_local_store_is_create_only(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"abc")
    store = LocalRawStore(tmp_path / "raw")
    uri = store.put("k.zip", src, "hash1")
    assert uri.startswith("file://")
    assert store.exists("k.zip")
    with pytest.raises(AlreadyExistsError) as info:
        store.put("k.zip", src, "hash2")
    assert info.value.existing_sha256 == "hash1"


def test_jsonl_runlog_dedupes_success(tmp_path: Path) -> None:
    log = JsonlRunLog(tmp_path / "runs.jsonl")
    now = datetime.now(UTC)
    assert log.record(RunRecord("r1", "k.zip", "u", "succeeded", now, now)) is True
    assert log.record(RunRecord("r2", "k.zip", "u", "succeeded", now, now)) is False
    assert log.record(RunRecord("r3", "k.zip", "u", "failed", now, now)) is True
    assert log.has_succeeded("k.zip")
    assert not log.has_succeeded("other.zip")
