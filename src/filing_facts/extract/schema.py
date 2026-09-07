"""The extraction contract. Gemini's structured output is validated against this, and every
field maps to an XBRL concept so Stage 4 can join extraction to ground truth by name.

Money values are strings, not numbers: JSON numbers are floats and accounts are not.
The validator normalises what models tend to emit ("15,315", "£1,256") and rejects anything
that is not a plain signed integer or decimal, which becomes a quarantine row.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NUMBER = re.compile(r"^\d+(\.\d+)?$")
_STRIP = re.compile(r"[£$€,\s]")
# Optional currency symbol, then either plain digits or thousands groups of exactly three.
_GROUPING_OK = re.compile(r"^[£$€]?\s?(?:\d+|\d{1,3}(?:[,\s]\d{3})+)(?:\.\d+)?$")

# Extraction field -> XBRL concept local name in stg_facts. Order matters for prompts.
CONCEPTS: dict[str, str] = {
    "equity": "Equity",
    "net_assets": "NetAssetsLiabilities",
    "net_current_assets": "NetCurrentAssetsLiabilities",
    "total_assets_less_current_liabilities": "TotalAssetsLessCurrentLiabilities",
    "current_assets": "CurrentAssets",
    "fixed_assets": "FixedAssets",
    "creditors": "Creditors",
    "cash": "CashBankOnHand",
    "average_employees": "AverageNumberEmployeesDuringPeriod",
}


def normalise_number(raw: str) -> str:
    """'(1,256)' -> '-1256'; '£15,315' -> '15315'. Anything ambiguous raises ValueError.

    Strict on purpose: a doubled sign, a sign inside brackets, or separators in odd places
    are model mistakes, and a mistake must become a quarantine row, not a silent guess.
    """
    s = raw.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    if s.startswith("-"):
        if negative:
            raise ValueError(f"sign inside brackets: {raw!r}")
        negative = True
        s = s[1:]
    if "-" in s or "(" in s or ")" in s:
        raise ValueError(f"not a number: {raw!r}")
    s = _STRIP.sub("", s)
    if not _NUMBER.match(s):
        raise ValueError(f"not a number: {raw!r}")
    if not _GROUPING_OK.match(raw.strip().strip("()").lstrip("-").strip()):
        raise ValueError(f"not a number: {raw!r}")
    return f"-{s}" if negative else s


class Amount(BaseModel):
    """A numeric fact as the model read it."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(
        description="The number as digits only, e.g. -1256 or 15315. null if not stated."
    )
    confidence: float = Field(ge=0, le=1, description="0 to 1: how sure the value is correct.")
    evidence: str = Field(description="A short verbatim quote of the line the value came from.")

    @field_validator("value")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalise_number(v)

    @property
    def decimal(self) -> Decimal | None:
        if self.value is None:
            return None
        try:
            return Decimal(self.value)
        except InvalidOperation as exc:  # pragma: no cover - validator already rejected this
            raise ValueError(self.value) from exc


class Text(BaseModel):
    """A non-numeric fact: a name, a company number, or an ISO date."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(description="The value, or null if not stated.")
    confidence: float = Field(ge=0, le=1)
    evidence: str


class FactPair(BaseModel):
    """The same fact for the current period and the prior (comparative) period."""

    model_config = ConfigDict(extra="forbid")

    current: Amount
    prior: Amount


class Extraction(BaseModel):
    """Everything asked of the model for one filing. Field names match CONCEPTS."""

    model_config = ConfigDict(extra="forbid")

    company_name: Text
    company_number: Text = Field(description="Companies House number, 8 characters.")
    period_start: Text = Field(description="ISO date YYYY-MM-DD.")
    period_end: Text = Field(description="ISO date YYYY-MM-DD, the balance sheet date.")
    equity: FactPair
    net_assets: FactPair
    net_current_assets: FactPair
    total_assets_less_current_liabilities: FactPair
    current_assets: FactPair
    fixed_assets: FactPair
    creditors: FactPair
    cash: FactPair
    average_employees: FactPair
    overall_confidence: float = Field(
        ge=0, le=1, description="0 to 1: how well the text supported the extraction overall."
    )

    def pairs(self) -> dict[str, FactPair]:
        return {name: getattr(self, name) for name in CONCEPTS}
