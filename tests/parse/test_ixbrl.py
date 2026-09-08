"""Values below were checked by hand against the raw HTML of each fixture."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from filing_facts.parse import (
    Fact,
    MalformedDocumentError,
    NotInlineXbrlError,
    UnsupportedFormatError,
    document_key,
    parse_ixbrl,
)
from tests.parse.conftest import fixture, synthetic


def _facts(doc_facts: tuple[Fact, ...], concept: str, context: str) -> Fact:
    matches = [f for f in doc_facts if f.concept == concept and f.context_id == context]
    assert len(matches) == 1, (concept, context, matches)
    return matches[0]


def test_every_fixture_is_internally_consistent(any_filing: Path) -> None:
    doc = parse_ixbrl(any_filing.read_bytes())
    key = document_key(any_filing.name)
    assert doc.entity_identifier == key.company_number
    assert doc.facts, "a filing with no facts is not a filing"
    for f in doc.facts:
        assert f.context_id in doc.contexts, f
        assert f.namespace.startswith("http"), f
        if f.is_numeric:
            assert f.unit is not None, f
            assert f.value is not None or f.text == "", f


def test_concept_identity_is_namespace_not_prefix() -> None:
    """One filing writes core:Equity, another frs-core:Equity. Same concept, same URI."""
    raw_a = fixture("Prod223_4298_09469075_20260228.html")
    raw_b = fixture("Prod223_4298_08963733_20260331.html")
    assert b'name="core:Equity"' in raw_a
    assert b'name="frs-core:Equity"' in raw_b
    a = _facts(parse_ixbrl(raw_a).facts, "Equity", "FY_END_20260228")
    b = _facts(parse_ixbrl(raw_b).facts, "Equity", "CURRENT_FY_END")
    assert a.concept == b.concept == "Equity"
    assert a.namespace == b.namespace
    assert a.namespace.endswith("/core")


def test_numcommadot_with_two_periods_and_2008_namespace() -> None:
    doc = parse_ixbrl(fixture("Prod223_4298_09469075_20260228.html"))
    assert doc.ix_namespace == "http://www.xbrl.org/2008/inlineXBRL"
    current = _facts(doc.facts, "Equity", "FY_END_20260228")
    prior = _facts(doc.facts, "Equity", "FY_END_20250228")
    assert (current.value, current.unit, current.format) == (
        Decimal("51718"),
        "GBP",
        "ixt:numcommadot",
    )
    assert prior.value == Decimal("49096")
    assert doc.contexts["FY_END_20260228"].instant.isoformat() == "2026-02-28"  # type: ignore[union-attr]
    assert not doc.contexts["FY_END_20260228"].dimensional


def test_dimensional_contexts_are_flagged() -> None:
    doc = parse_ixbrl(fixture("Prod223_4298_09469075_20260228.html"))
    ctx = "FY_END_20260228_CORE_MATURITIESOREXPIRATIONPERIODSDIMENSION_CORE_WITHINONEYEAR"
    assert doc.contexts[ctx].dimensional
    assert _facts(doc.facts, "Creditors", ctx).value == Decimal("6802")


def test_sign_attribute_negates() -> None:
    doc = parse_ixbrl(fixture("Prod223_4298_08963733_20260331.html"))
    f = _facts(doc.facts, "NetAssetsLiabilities", "CURRENT_FY_END")
    assert (f.text, f.sign, f.value) == ("1,256", "-", Decimal("-1256"))


def test_scale_zero_and_long_unit_ids() -> None:
    doc = parse_ixbrl(fixture("Prod223_4298_00425355_20251231.html"))
    f = _facts(doc.facts, "Creditors", "I4")
    assert (f.value, f.scale, f.unit) == (Decimal("825393"), 0, "GBP")
    assert (
        sum(x.in_hidden for x in doc.facts) == 18
    )  # counted by regex over the raw ix:hidden block


def test_dash_formats_are_zero_and_units_normalise() -> None:
    doc = parse_ixbrl(fixture("Prod223_4298_00700947_20251231.html"))
    numeric = [f for f in doc.facts if f.is_numeric]
    assert numeric
    assert all(f.value == 0 for f in numeric)
    assert all(f.format == "ixt:numdash" for f in numeric)
    assert doc.units["Shares"] == "shares"
    assert doc.units["Number"] == "pure"


def test_period_contexts_carry_start_and_end() -> None:
    doc = parse_ixbrl(fixture("Prod223_4298_02432229_20251231.html"))
    duration = [c for c in doc.contexts.values() if c.start_date and c.end_date]
    assert duration
    assert all(c.instant is None for c in duration)


def test_malformed_xml_is_a_named_failure() -> None:
    with pytest.raises(MalformedDocumentError):
        parse_ixbrl(b"<html><body><p>unclosed</body></html>")


def test_plain_xhtml_without_ixbrl_is_a_named_failure() -> None:
    with pytest.raises(NotInlineXbrlError):
        parse_ixbrl(synthetic("<p>just a page</p>", ix_ns=None))


def test_unsupported_format_propagates_from_a_fact() -> None:
    body = (
        '<ix:nonFraction name="core:Equity" contextRef="c1" unitRef="GBP" '
        'format="ixt:numwordsen">ten</ix:nonFraction>'
    )
    with pytest.raises(UnsupportedFormatError):
        parse_ixbrl(synthetic(body))


def test_nil_fact_has_no_value_but_is_kept() -> None:
    body = (
        '<ix:nonFraction name="core:Equity" contextRef="c1" unitRef="GBP" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/>'
    )
    doc = parse_ixbrl(synthetic(body))
    assert len(doc.facts) == 1
    assert doc.facts[0].value is None
    assert doc.facts[0].is_numeric


def test_dimension_members_are_captured_by_local_name() -> None:
    doc = parse_ixbrl(fixture("Prod223_4298_09469075_20260228.html"))
    ctx = "FY_END_20260228_CORE_MATURITIESOREXPIRATIONPERIODSDIMENSION_CORE_WITHINONEYEAR"
    assert doc.contexts[ctx].dimensions == "MaturitiesOrExpirationPeriodsDimension=WithinOneYear"
    assert doc.contexts["FY_END_20260228"].dimensions is None
    plain = [c for c in doc.contexts.values() if not c.dimensional]
    assert all(c.dimensions is None for c in plain)
