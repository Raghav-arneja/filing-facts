"""Companies House free Accounts Data Product: one daily ZIP, named for its publish date.

Files land at download.companieshouse.gov.uk around 06:45 UTC, Tuesday to Saturday.
No registration, no API key.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

_CHUNK = 1 << 20  # 1 MiB


class SourceError(Exception):
    """Base class for download-side failures."""


class NotPublishedError(SourceError):
    """The source returned 404: no file exists for this date (weekend, holiday, not yet)."""


class DownloadFailedError(SourceError):
    """Non-success HTTP status or transport error."""


class TruncatedDownloadError(SourceError):
    """Bytes received did not match Content-Length, or the size cap was exceeded."""


class CorruptArchiveError(SourceError):
    """The bytes are complete but do not form a valid ZIP."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    byte_count: int
    sha256: str
    content_length: int | None


def source_key(day: date) -> str:
    """Stable natural key for one daily file. This is the document id for Stage 1."""
    return f"Accounts_Bulk_Data-{day.isoformat()}.zip"


def source_url(base_url: str, day: date) -> str:
    return f"{base_url.rstrip('/')}/{source_key(day)}"


def download(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    max_bytes: int,
) -> DownloadResult:
    """Stream the file to disk, hashing as we go. Never trusts the download blindly.

    Raises NotPublishedError on 404, DownloadFailedError on any other non-2xx or transport error,
    TruncatedDownloadError if the byte count disagrees with Content-Length or exceeds max_bytes.
    """
    digest = hashlib.sha256()
    received = 0
    try:
        with client.stream("GET", url) as response:
            if response.status_code == 404:
                raise NotPublishedError(url)
            if response.is_error:
                raise DownloadFailedError(f"{response.status_code} for {url}")
            raw_length = response.headers.get("content-length")
            content_length = int(raw_length) if raw_length is not None else None
            if content_length is not None and content_length > max_bytes:
                raise TruncatedDownloadError(
                    f"Content-Length {content_length} exceeds cap {max_bytes} for {url}"
                )
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes(_CHUNK):
                    received += len(chunk)
                    if received > max_bytes:
                        raise TruncatedDownloadError(f"exceeded cap {max_bytes} bytes for {url}")
                    digest.update(chunk)
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise DownloadFailedError(f"{type(exc).__name__}: {exc}") from exc

    if content_length is not None and received != content_length:
        raise TruncatedDownloadError(f"received {received} of {content_length} bytes for {url}")
    return DownloadResult(
        path=dest, byte_count=received, sha256=digest.hexdigest(), content_length=content_length
    )


def validate_zip(path: Path) -> int:
    """Check the ZIP central directory and every member CRC. Returns the member count.

    A truncated file with a correct Content-Length is the case this catches: the server
    can serve a partial object with a partial length and the byte check passes.
    """
    if not zipfile.is_zipfile(path):
        raise CorruptArchiveError(f"{path.name} is not a ZIP file")
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise CorruptArchiveError(f"{path.name}: CRC failure in member {bad}")
        return len(zf.infolist())
