from __future__ import annotations

from decimal import Decimal

import pytest

from filing_facts.parse.errors import UnsupportedFormatError
from filing_facts.parse.transforms import transform_numeric


@pytest.mark.parametrize(
    ("text", "fmt", "expected"),
    [
        ("1,234", "ixt:numcommadot", Decimal("1234")),
        ("1,234.56", "ixt2:numdotdecimal", Decimal("1234.56")),
        ("1.234,56", "ixt2:numdotcomma", Decimal("1234.56")),
        ("1\u2009234", "ixt:numcommadot", Decimal("1234")),  # thin space thousands
        ("1\u00a0234", "ixt:numcommadot", Decimal("1234")),  # no-break space
        ("-", "ixt2:zerodash", Decimal("0")),
        ("-", "ixt:numdash", Decimal("0")),
        ("2.00", None, Decimal("2.00")),
        ("825,393", None, Decimal("825393")),
    ],
)
def test_formats(text: str, fmt: str | None, expected: Decimal) -> None:
    assert transform_numeric(text, fmt=fmt, scale=None, sign=None) == expected


def test_scale_and_sign() -> None:
    assert transform_numeric("1,256", fmt="ixt:numcommadot", scale=None, sign="-") == Decimal(
        "-1256"
    )
    assert transform_numeric("12", fmt="ixt:numcommadot", scale=3, sign=None) == Decimal("12000")
    assert transform_numeric("12", fmt="ixt:numcommadot", scale=0, sign=None) == Decimal("12")
    assert transform_numeric("5", fmt=None, scale=-2, sign="-") == Decimal("-0.05")


def test_empty_is_nil_not_zero() -> None:
    assert transform_numeric("", fmt=None, scale=None, sign=None) is None
    assert transform_numeric("   ", fmt="ixt:numcommadot", scale=None, sign=None) is None


def test_unknown_format_is_refused_not_guessed() -> None:
    with pytest.raises(UnsupportedFormatError, match="numwordsen"):
        transform_numeric("twelve", fmt="ixt:numwordsen", scale=None, sign=None)


def test_garbage_under_a_known_format_is_refused() -> None:
    with pytest.raises(UnsupportedFormatError, match="cannot parse"):
        transform_numeric("n/a", fmt="ixt:numcommadot", scale=None, sign=None)
