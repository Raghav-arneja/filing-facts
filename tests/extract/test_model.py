from __future__ import annotations

import json

import pytest

from filing_facts.extract.model import FakeModel, ModelCallError, label_value
from filing_facts.extract.pricing import PRICES, cost_usd
from filing_facts.extract.prompt import load_prompt
from tests.extract.conftest import good_answer


def test_fake_model_validates_good_and_bad_answers() -> None:
    p = load_prompt("v1")
    m = FakeModel(canned=[good_answer(), {"nope": 1}, "not json"])
    ok = m.extract(p, "text")
    assert ok.extraction is not None
    assert ok.validation_error is None
    bad = m.extract(p, "text")
    assert bad.extraction is None
    assert bad.validation_error
    garbage = m.extract(p, "text")
    assert garbage.extraction is None
    assert "json" in (garbage.validation_error or "").lower()
    assert m.calls == ["text", "text", "text"]
    with pytest.raises(ModelCallError):
        m.extract(p, "text")


def test_fake_model_accepts_raw_json_strings() -> None:
    m = FakeModel(answer=lambda t: json.dumps(good_answer()))
    assert m.extract(load_prompt("v1"), "x").extraction is not None


def test_cost_arithmetic_bills_thinking_as_output() -> None:
    assert cost_usd("gemini-3.1-flash-lite", 1_000_000, 0) == pytest.approx(0.25)
    assert cost_usd("gemini-3.8-flash", 0, 1_000_000) == pytest.approx(3.75)
    assert cost_usd("gemini-3.8-flash", 0, 0, thinking_tokens=1_000_000) == pytest.approx(3.75)
    assert set(PRICES) >= {"gemini-3.1-flash-lite", "gemini-3.8-flash"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gemini-3.1-flash-lite", "gemini-3-1-flash-lite"),
        ("v1", "v1"),
        ("Filing Facts", "filing-facts"),
    ],
)
def test_billing_label_values_meet_gcp_rules(raw: str, expected: str) -> None:
    v = label_value(raw)
    assert v == expected
    assert len(v) <= 63
    assert all(c.islower() or c.isdigit() or c in "_-" for c in v)
