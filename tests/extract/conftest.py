from __future__ import annotations

from typing import Any

from filing_facts.extract.model import sample_answer


def amount(value: str | None, confidence: float = 0.9) -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "evidence": "Line | x | y"}


def good_answer(**overrides: Any) -> dict[str, Any]:
    return sample_answer(**overrides)
