from __future__ import annotations

from typing import Any


def amount(value: str | None, confidence: float = 0.9) -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "evidence": "Line | x | y"}


def pair(current: str | None, prior: str | None) -> dict[str, Any]:
    return {"current": amount(current), "prior": amount(prior)}


def text(value: str | None) -> dict[str, Any]:
    return {"value": value, "confidence": 0.95, "evidence": "hdr"}


def good_answer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company_name": text("HR INFLUENCE LIMITED"),
        "company_number": text("09469075"),
        "period_start": text("2025-03-01"),
        "period_end": text("2026-02-28"),
        "equity": pair("51718", "49096"),
        "net_assets": pair("51718", "49096"),
        "net_current_assets": pair("30000", "28000"),
        "total_assets_less_current_liabilities": pair("51718", "49096"),
        "current_assets": pair("36802", "39765"),
        "fixed_assets": pair("2204", "1500"),
        "creditors": pair("6802", "11669"),
        "cash": pair("30000", "25000"),
        "average_employees": pair("2", "2"),
        "overall_confidence": 0.9,
    }
    base.update(overrides)
    return base
