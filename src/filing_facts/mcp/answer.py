# The google-genai SDK is partially typed; strict elsewhere.
# pyright: reportUnknownMemberType=false
"""Grounded answers: retrieve passages, hand them to Gemini Flash-Lite with the versioned
prompt in prompts/ask/, return the answer with the citation keys it used and what it cost."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from filing_facts.extract.model import label_value
from filing_facts.extract.pricing import cost_usd
from filing_facts.extract.prompt import Prompt, load_prompt
from filing_facts.index.search import Hit, Searcher

ASK_PROMPTS = Path(__file__).resolve().parents[3] / "prompts" / "ask"
_CITATION = re.compile(r"\[(\d{8}_\d{8}#\d+)\]")


@dataclass(frozen=True)
class Generation:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class Generator(Protocol):
    @property
    def model_id(self) -> str: ...

    def generate(self, system: str, user: str) -> Generation: ...


class FakeGenerator:
    """Echoes the first citation key it is given, so tests can check grounding end to end."""

    model_id = "fake-generator"

    def generate(self, system: str, user: str) -> Generation:
        keys = _CITATION.findall(user)
        text = f"Fake answer citing [{keys[0]}]." if keys else "The passages do not say."
        return Generation(text, input_tokens=len(user) // 4, output_tokens=8, latency_ms=1)


class GeminiGenerator:
    def __init__(
        self,
        model_id: str,
        *,
        project: str,
        location: str,
        labels: dict[str, str],
        attempts: int = 4,
    ) -> None:
        from google import genai

        self._model_id = model_id
        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._labels = {k: label_value(v) for k, v in {**labels, "model": model_id}.items()}
        self._attempts = attempts

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, system: str, user: str) -> Generation:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system, temperature=0, labels=self._labels
        )
        delay = 2.0
        for attempt in range(1, self._attempts + 1):
            started = time.perf_counter()
            try:
                response = self._client.models.generate_content(
                    model=self._model_id, contents=user, config=config
                )
            except errors.APIError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self._attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            usage = response.usage_metadata
            return Generation(
                text=response.text or "",
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class Answer:
    question: str
    answer: str
    citations: list[dict[str, object]]
    passages_used: int
    model: str
    prompt_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    unsupported_citations: list[str] = field(default_factory=list[str])


def citation_key(hit: Hit) -> str:
    return f"{hit.document_id}#{hit.chunk_index}"


def render_passages(hits: list[Hit]) -> str:
    parts: list[str] = []
    for hit in hits:
        who = hit.company_name or hit.company_number
        parts.append(f"[{citation_key(hit)}] {who}, period ending {hit.period_end}\n{hit.text}")
    return "\n\n".join(parts)


class Asker:
    def __init__(
        self, searcher: Searcher, generator: Generator, prompt: Prompt | None = None
    ) -> None:
        self._searcher = searcher
        self._generator = generator
        self._prompt = prompt or load_prompt("v1", ASK_PROMPTS)

    def ask(self, question: str, k: int = 6) -> Answer:
        hits = self._searcher.search(question, k=k)
        user = self._prompt.render(render_passages(hits)).replace("{question}", question)
        gen = self._generator.generate(self._prompt.system, user)
        by_key = {citation_key(h): h for h in hits}
        cited = list(dict.fromkeys(_CITATION.findall(gen.text)))
        citations: list[dict[str, object]] = [
            {
                "key": key,
                "document_id": by_key[key].document_id,
                "company_number": by_key[key].company_number,
                "company_name": by_key[key].company_name,
                "period_end": by_key[key].period_end,
                "text": by_key[key].text,
            }
            for key in cited
            if key in by_key
        ]
        model = self._generator.model_id
        return Answer(
            question=question,
            answer=gen.text.strip(),
            citations=citations,
            passages_used=len(hits),
            model=model,
            prompt_id=self._prompt.id,
            input_tokens=gen.input_tokens,
            output_tokens=gen.output_tokens,
            cost_usd=round(cost_usd(model, gen.input_tokens, gen.output_tokens), 6)
            if model != "fake-generator"
            else 0.0,
            latency_ms=gen.latency_ms,
            unsupported_citations=[key for key in cited if key not in by_key],
        )
