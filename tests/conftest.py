from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterator
from datetime import date

import httpx
import pytest

from filing_facts.config import Settings
from filing_facts.storage.memory import MemoryRawStore, MemoryRunLog

TARGET_DATE = date(2026, 9, 1)
BASE_URL = "https://example.test"


def make_zip(members: int = 3) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(members):
            zf.writestr(f"Prod223_1234_{i:08d}_20260831.html", f"<html>{i}</html>" * 50)
    return buf.getvalue()


@pytest.fixture
def settings() -> Settings:
    return Settings(source_base_url=BASE_URL, max_bytes=10_000_000)


@pytest.fixture
def store() -> MemoryRawStore:
    return MemoryRawStore()


@pytest.fixture
def runlog() -> MemoryRunLog:
    return MemoryRunLog()


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def client_for() -> Iterator[Callable[[Handler], httpx.Client]]:
    """Build an httpx client whose transport is the given handler. Closes clients on teardown."""
    clients: list[httpx.Client] = []

    def factory(handler: Handler) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.close()


def serve_bytes(body: bytes, *, status: int = 200, content_length: int | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"content-length": str(len(body) if content_length is None else content_length)}
        return httpx.Response(status, content=body, headers=headers)

    return handler
