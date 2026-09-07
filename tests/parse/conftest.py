from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "ixbrl"
FIXTURE_FILES = sorted(FIXTURES.glob("*.html"))


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(params=FIXTURE_FILES, ids=lambda p: p.stem.split("_", 2)[-1])
def any_filing(request: pytest.FixtureRequest) -> Path:
    return request.param


IX13 = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI = "http://www.xbrl.org/2003/instance"


def synthetic(body: str, *, ix_ns: str | None = IX13) -> bytes:
    """Minimal XHTML with one GBP unit and one instant context, for edge-case tests."""
    ixdecl = f' xmlns:ix="{ix_ns}"' if ix_ns else ""
    header = (
        '<ix:header><ix:resources><xbrli:context id="c1"><xbrli:entity>'
        '<xbrli:identifier scheme="http://www.companieshouse.gov.uk/">01234567</xbrli:identifier>'
        "</xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>"
        '</xbrli:context><xbrli:unit id="GBP"><xbrli:measure>iso4217:GBP</xbrli:measure>'
        "</xbrli:unit></ix:resources></ix:header>"
        if ix_ns
        else ""
    )
    return (
        f'<html xmlns="http://www.w3.org/1999/xhtml"{ixdecl} xmlns:xbrli="{XBRLI}" '
        f'xmlns:core="http://example.test/core"><body>{header}{body}</body></html>'
    ).encode()
