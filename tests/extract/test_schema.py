from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from filing_facts.extract.schema import CONCEPTS, Amount, Extraction, normalise_number
from tests.extract.conftest import amount, good_answer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15315", "15315"),
        ("15,315", "15315"),
        ("£1,256", "1256"),
        ("(1,256)", "-1256"),
        ("-1256", "-1256"),
        (" 2 ", "2"),
        ("1234.50", "1234.50"),
        ("1 234", "1234"),
        ("12,345,678", "12345678"),
    ],
)
def test_normalise_number(raw: str, expected: str) -> None:
    assert normalise_number(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "-", "n/a", "twelve", "1,2,3.4.5", "12k", "--5", "(-5)", "-(5)", "1,,2", "1 2 3"]
)
def test_bad_numbers_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match=r"not a number|sign inside brackets"):
        normalise_number(raw)


def test_amount_normalises_and_exposes_decimal() -> None:
    a = Amount.model_validate(amount("(1,256)"))
    assert a.value == "-1256"
    assert a.decimal == Decimal("-1256")
    assert Amount.model_validate(amount(None)).decimal is None


def test_extraction_rejects_extra_fields_and_bad_confidence() -> None:
    with pytest.raises(ValidationError):
        Extraction.model_validate({**good_answer(), "surprise": 1})
    with pytest.raises(ValidationError):
        Extraction.model_validate(good_answer(overall_confidence=1.5))


def test_every_concept_is_a_fact_pair_on_the_model() -> None:
    e = Extraction.model_validate(good_answer())
    assert set(e.pairs()) == set(CONCEPTS)
    assert e.pairs()["equity"].current.decimal == Decimal("51718")


def test_concept_names_exist_in_real_filings() -> None:
    """Every XBRL concept we extract must appear in at least one fixture's tags."""
    from pathlib import Path

    from filing_facts.parse import parse_ixbrl

    seen: set[str] = set()
    for p in (Path(__file__).parents[1] / "fixtures" / "ixbrl").glob("*.html"):
        seen |= {f.concept for f in parse_ixbrl(p.read_bytes()).facts}
    missing = set(CONCEPTS.values()) - seen
    assert not missing, missing
