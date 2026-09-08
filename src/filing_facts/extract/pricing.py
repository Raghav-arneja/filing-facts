"""List prices per million tokens, USD, for the models this project calls on Vertex AI.

Prices are inputs to a measured cost: token counts come from each response, and cost is
tokens times these rates. Thinking tokens are billed as output. Update PRICE_DATE when
these change; docs/cost.md cites it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

PRICE_DATE = date(2026, 9, 8)


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float


PRICES: dict[str, Price] = {
    "gemini-3.1-flash-lite": Price(0.25, 1.50),
    "gemini-3.8-flash": Price(0.75, 3.75),  # introductory; doubles 2027-01-01
    "gemini-2.5-flash-lite": Price(0.10, 0.40),  # retires 2026-10-16; not used
    "gemini-embedding-2": Price(0.20, 0.0),  # embeddings: input tokens only
}


def base_model(model_id: str) -> str:
    """'gemini-3.8-flash@t0' -> 'gemini-3.8-flash'. Variants share the base model's price."""
    return model_id.split("@", 1)[0]


def is_priced(model_id: str) -> bool:
    return base_model(model_id) in PRICES


def cost_usd(model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float:
    p = PRICES[base_model(model)]
    return (
        input_tokens * p.input_per_million
        + (output_tokens + thinking_tokens) * p.output_per_million
    ) / 1_000_000
