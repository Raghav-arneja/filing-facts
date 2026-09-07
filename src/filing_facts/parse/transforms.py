"""Inline XBRL numeric transformations (the ixt registries), applied to fact text.

Only formats observed in Companies House filings are implemented. Anything else raises
UnsupportedFormatError, which quarantines the document rather than guessing a number.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from filing_facts.parse.errors import UnsupportedFormatError

_WS = str.maketrans({"\u00a0": " ", "\u2009": " ", "\u202f": " "})  # nbsp, thin, narrow nbsp


def _clean(text: str) -> str:
    return text.translate(_WS).strip()


def _dot_decimal(text: str) -> Decimal:
    """Thousands separated by comma or space, decimal point is '.'. e.g. 1,234.56"""
    return Decimal(_clean(text).replace(",", "").replace(" ", ""))


def _comma_decimal(text: str) -> Decimal:
    """Thousands separated by '.' or space, decimal point is ','. e.g. 1.234,56"""
    return Decimal(_clean(text).replace(".", "").replace(" ", "").replace(",", "."))


def _zero(text: str) -> Decimal:
    return Decimal(0)


def _plain(text: str) -> Decimal:
    return _dot_decimal(text)


_FORMATS: dict[str, Callable[[str], Decimal]] = {
    "numcommadot": _dot_decimal,  # ixt v1 name: comma thousands, dot decimal
    "numdotdecimal": _dot_decimal,  # ixt v2/v3 name for the same thing
    "numdotcomma": _comma_decimal,
    "numcommadecimal": _comma_decimal,
    "numdash": _zero,
    "zerodash": _zero,
    "fixedzero": _zero,
}


def transform_numeric(
    text: str,
    *,
    fmt: str | None,
    scale: int | None,
    sign: str | None,
) -> Decimal | None:
    """Return the numeric value of a nonFraction fact, or None if the fact is empty (nil)."""
    cleaned = _clean(text)
    if cleaned == "" and fmt is None:
        return None
    if fmt is None:
        func: Callable[[str], Decimal] | None = _plain
    else:
        # ixt v4 hyphenates names (num-dot-decimal); earlier registries do not (numdotdecimal).
        local = fmt.split(":", 1)[-1].replace("-", "")
        if local not in _FORMATS:
            raise UnsupportedFormatError(fmt)
        func = _FORMATS[local]
    if cleaned == "":
        return None
    try:
        value = func(cleaned)
    except InvalidOperation as exc:
        raise UnsupportedFormatError(f"{fmt or 'plain'}: cannot parse {text!r}") from exc
    if scale:
        value = value.scaleb(scale)
    if sign == "-":
        value = -value
    return value
