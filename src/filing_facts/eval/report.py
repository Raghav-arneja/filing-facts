"""Render the evaluation views as Markdown and write it between markers in the README.

Every number in the README's results section comes through here. `--check` fails when the
README no longer matches the views, which is how CI enforces the "computed, not typed" rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

START = "<!-- eval:start -->"
END = "<!-- eval:end -->"
_BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)

CONCEPT_LABELS = {
    "Equity": "Equity",
    "NetAssetsLiabilities": "Net assets",
    "NetCurrentAssetsLiabilities": "Net current assets",
    "TotalAssetsLessCurrentLiabilities": "Total assets less current liabilities",
    "CurrentAssets": "Current assets",
    "FixedAssets": "Fixed assets",
    "Creditors": "Creditors due within one year",
    "CashBankOnHand": "Cash at bank",
    "AverageNumberEmployeesDuringPeriod": "Average employees",
}
ERROR_LABELS = {
    "sign_flipped": "sign flipped",
    "scale_1000": "off by a factor of 1,000",
    "period_swapped": "current and prior swapped",
    "near_miss": "within 1 percent",
    "other_error": "other",
}


@dataclass(frozen=True)
class Metrics:
    """Rows of eval_metrics and eval_metrics_by_size, as plain dicts."""

    by_concept: list[dict[str, Any]]
    by_size: list[dict[str, Any]]


def _find(
    rows: list[dict[str, Any]], model: str, prompt: str, **match: str
) -> dict[str, Any] | None:
    for r in rows:
        if (
            r["model"] == model
            and r["prompt_id"] == prompt
            and all(r[k] == v for k, v in match.items())
        ):
            return r
    return None


def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{float(x) * 100:.1f}%"


def _config(row: dict[str, Any]) -> str:
    return f"{row['model']} / {row['prompt_id'].split('-')[0]}"


def render(m: Metrics, generated_at: datetime | None = None) -> str:
    when = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d")
    overall = sorted(
        (r for r in m.by_concept if r["concept"] == "ALL"),
        key=lambda r: (-float(r["recall"] or 0), r["model"]),
    )
    lines = [
        START,
        f"_Computed from the evaluation views on {when} by `python -m filing_facts.eval`. "
        "Every figure below is generated; CI fails if this section is stale._",
        "",
        "**Headline: exact-match recall of extracted facts against the XBRL tags**",
        "",
        "| Model / prompt | Filings | Verifiable cells | Recall | Precision | Unsupported "
        "| Tag errors | USD per 1,000 | Mean latency |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in overall:
        lines.append(
            f"| {_config(r)} | {int(r['documents'])} | {int(r['verifiable'])} "
            f"| {_pct(r['recall'])} | {_pct(r['precision'])} | {int(r['unsupported'])} "
            f"| {int(r['tag_error'])} "
            f"| {float(r['usd_per_1000']):.2f} | {float(r['mean_latency_ms']) / 1000:.1f} s |"
        )
    lines += [
        "",
        "Verifiable cells are those where the filing's own tags give an answer. Unsupported cells "
        "are values the model gave where the tags are silent; they are reported, not scored. "
        "Tag errors are cells where the tag is demonstrably wrong and the model is not, such as "
        "employee counts filed with a scale of minus two.",
        "",
        "**Recall by concept**",
        "",
    ]
    configs = [(_config(r), r["model"], r["prompt_id"]) for r in overall]
    lines.append("| Concept | " + " | ".join(c[0] for c in configs) + " |")
    lines.append("|---|" + "---:|" * len(configs))
    for concept, label in CONCEPT_LABELS.items():
        cells: list[str] = []
        for _, model, prompt in configs:
            row = _find(m.by_concept, model, prompt, concept=concept)
            cells.append(f"{_pct(row['recall'])} (n={int(row['verifiable'])})" if row else "n/a")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "**How the wrong answers were wrong**", ""]
    lines.append("| Model / prompt | " + " | ".join(ERROR_LABELS.values()) + " |")
    lines.append("|---|" + "---:|" * len(ERROR_LABELS))
    for r in overall:
        lines.append(f"| {_config(r)} | " + " | ".join(str(int(r[k])) for k in ERROR_LABELS) + " |")
    lines += ["", "**Recall by filing length**", ""]
    bands = sorted({r["size_band"] for r in m.by_size})
    lines.append("| Model / prompt | " + " | ".join(b.split(": ", 1)[-1] for b in bands) + " |")
    lines.append("|---|" + "---:|" * len(bands))
    for _, model, prompt in configs:
        cells = []
        for b in bands:
            row = _find(m.by_size, model, prompt, size_band=b)
            cells.append(f"{_pct(row['recall'])} (n={int(row['documents'])})" if row else "n/a")
        lines.append(f"| {model} / {prompt.split('-')[0]} | " + " | ".join(cells) + " |")
    lines += ["", END]
    return "\n".join(lines)


def splice(readme: str, block: str) -> str:
    if not _BLOCK.search(readme):
        raise ValueError(f"README has no {START} ... {END} markers")
    return _BLOCK.sub(lambda _m: block, readme, count=1)


def current_block(readme: str) -> str | None:
    m = _BLOCK.search(readme)
    return m.group(0) if m else None


def strip_date(block: str) -> str:
    """The generation date is the one thing allowed to differ between two fresh renders."""
    return re.sub(r"on \d{4}-\d{2}-\d{2} by", "on DATE by", block)
