from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from filing_facts.eval.report import END, START, Metrics, current_block, render, splice, strip_date


def _row(model: str, prompt: str, concept: str, recall: float, n: int) -> dict[str, Any]:
    return {
        "model": model,
        "prompt_id": prompt,
        "concept": concept,
        "verifiable": n,
        "correct": int(n * recall),
        "wrong": n - int(n * recall),
        "missed": 0,
        "unsupported": 3,
        "tag_error": 1,
        "sign_flipped": 2,
        "scale_1000": 0,
        "period_swapped": 0,
        "near_miss": 1,
        "other_error": 0,
        "precision": recall,
        "recall": recall,
        "documents": 10,
        "mean_latency_ms": 3700.0,
        "usd_per_1000": 3.28,
    }


def _size(model: str, documents: int) -> dict[str, Any]:
    return {
        "model": model,
        "prompt_id": "v1-abc",
        "size_band": "1: under 2k chars",
        "documents": documents,
        "verifiable": 30,
        "correct": 27,
        "recall": 0.9,
    }


@pytest.fixture
def metrics() -> Metrics:
    by_concept = [
        _row("lite", "v1-abc", "ALL", 0.93, 100),
        _row("lite", "v1-abc", "Equity", 0.95, 50),
        _row("flash", "v1-abc", "ALL", 0.91, 40),
        _row("flash", "v1-abc", "Equity", 0.96, 20),
    ]
    by_size = [_size("lite", 4), _size("flash", 2)]
    return Metrics(by_concept, by_size)


def test_render_orders_by_recall_and_includes_every_section(metrics: Metrics) -> None:
    out = render(metrics, generated_at=datetime(2026, 9, 8, tzinfo=UTC))
    assert out.startswith(START)
    assert out.endswith(END)
    lite = out.index("| lite / v1 | 10 |")
    flash = out.index("| flash / v1 | 10 |")
    assert lite < flash, "best recall first"
    assert "| Equity | 95.0% (n=50) | 96.0% (n=20) |" in out
    assert "| Creditors due within one year | n/a | n/a |" in out
    assert "| lite / v1 | 2 | 0 | 0 | 1 | 0 |" in out
    assert "under 2k chars" in out
    assert "on 2026-09-08 by" in out


def test_splice_replaces_only_the_marked_block(metrics: Metrics) -> None:
    readme = f"# Title\n\nintro\n\n{START}\nold\n{END}\n\ntail\n"
    block = render(metrics)
    out = splice(readme, block)
    assert out.startswith("# Title\n\nintro\n\n")
    assert out.endswith("\n\ntail\n")
    assert "old" not in out
    assert current_block(out) == block


def test_splice_requires_markers() -> None:
    with pytest.raises(ValueError, match="markers"):
        splice("no markers here", "x")


def test_strip_date_makes_fresh_renders_comparable(metrics: Metrics) -> None:
    a = render(metrics, generated_at=datetime(2026, 9, 8, tzinfo=UTC))
    b = render(metrics, generated_at=datetime(2026, 9, 9, tzinfo=UTC))
    assert a != b
    assert strip_date(a) == strip_date(b)
