"""A short read, an over-cap file, and a byte-complete but corrupt ZIP all fail loudly."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from filing_facts.config import Settings
from filing_facts.ingest.job import run
from filing_facts.sources import companies_house as ch
from filing_facts.storage.memory import MemoryRawStore, MemoryRunLog
from tests.conftest import TARGET_DATE, Handler, make_zip, serve_bytes


def test_content_length_mismatch_is_truncated(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    body = make_zip()
    client = client_for(serve_bytes(body[: len(body) // 2], content_length=len(body)))
    outcome = run(settings, client=client, store=store, runlog=runlog, target_date=TARGET_DATE)

    assert outcome.status == "failed"
    assert outcome.record is not None
    assert "TruncatedDownloadError" in (outcome.record.error or "")
    assert store.objects == {}


def test_complete_but_corrupt_zip_is_rejected(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    """Content-Length matches the bytes served, but the bytes are not a ZIP."""
    body = make_zip()[: len(make_zip()) - 40]  # chop the central directory
    outcome = run(
        settings,
        client=client_for(serve_bytes(body)),
        store=store,
        runlog=runlog,
        target_date=TARGET_DATE,
    )

    assert outcome.status == "failed"
    assert outcome.record is not None
    assert "CorruptArchiveError" in (outcome.record.error or "")
    assert store.objects == {}


def test_oversized_content_length_is_refused_before_downloading(
    settings: Settings,
    client_for: Callable[[Handler], httpx.Client],
    tmp_path: Path,
) -> None:
    client = client_for(serve_bytes(b"x" * 100, content_length=settings.max_bytes + 1))
    with pytest.raises(ch.TruncatedDownloadError, match="exceeds cap"):
        ch.download(
            client, "https://example.test/f.zip", tmp_path / "f.zip", max_bytes=settings.max_bytes
        )


def test_body_over_cap_without_content_length_is_refused(
    client_for: Callable[[Handler], httpx.Client], tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # An iterable body is sent chunked, so no Content-Length header is present.
        return httpx.Response(200, content=iter([b"x" * 1024, b"x" * 1024]))

    with pytest.raises(ch.TruncatedDownloadError, match="exceeded cap"):
        ch.download(
            client_for(handler), "https://example.test/f.zip", tmp_path / "f.zip", max_bytes=1024
        )


def test_member_crc_failure_is_corrupt(tmp_path: Path) -> None:
    body = bytearray(make_zip(1))
    # Flip a byte inside the compressed member payload, leaving the central directory intact.
    body[40] ^= 0xFF
    path = tmp_path / "bad.zip"
    path.write_bytes(bytes(body))
    with pytest.raises(ch.CorruptArchiveError, match="CRC"):
        ch.validate_zip(path)
