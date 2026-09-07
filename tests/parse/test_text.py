from __future__ import annotations

import re
from pathlib import Path

import pytest

from filing_facts.parse import MalformedDocumentError, parse_ixbrl, render_text
from tests.parse.conftest import fixture


def test_no_markup_survives(any_filing: Path) -> None:
    text = render_text(any_filing.read_bytes())
    assert "<" not in text
    assert "ix:" not in text
    assert "xbrli" not in text
    assert not re.search(r"^[| ]+$", text, re.M), "no pipe-only lines"
    assert text.endswith("\n")
    assert "\n\n\n" not in text


def test_visible_numeric_facts_appear_as_displayed(any_filing: Path) -> None:
    """Every non-hidden numeric fact's display text must be findable in the rendered text."""
    data = any_filing.read_bytes()
    text = render_text(data)
    visible = [f for f in parse_ixbrl(data).facts if f.is_numeric and not f.in_hidden and f.text]
    assert visible
    missing = [f.text for f in visible if f.text not in text]
    assert not missing, missing


def test_hidden_section_is_dropped() -> None:
    """ix:header holds contexts and hidden facts; its raw identifiers must not leak."""
    text = render_text(fixture("Prod223_4298_00425355_20251231.html"))
    assert "U-iso4217-GBP" not in text
    assert "http://www.companieshouse.gov.uk/" not in text


def test_table_rows_become_single_lines() -> None:
    text = render_text(fixture("Prod223_4298_02432229_20251231.html"))
    assert "Cash at bank and in hand | 2.00 | 2.00" in text
    assert "2025 | 2024" in text


def test_malformed_input_is_named() -> None:
    with pytest.raises(MalformedDocumentError):
        render_text(b"<html><p>")
