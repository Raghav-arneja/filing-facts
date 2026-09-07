# The google-genai SDK is partially typed; strict elsewhere.
# pyright: reportUnknownMemberType=false
"""The model boundary. The job talks to ExtractionModel; Gemini and a test fake implement it.

Every Gemini call carries billing labels (a locked decision) and asks for JSON constrained
to the Extraction schema. The response is returned raw plus parsed so a schema failure can
be quarantined with the text that caused it.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from filing_facts.extract.prompt import Prompt
from filing_facts.extract.schema import Extraction


class ExtractionError(Exception):
    """Base class; the class name is the quarantine reason."""


class SchemaValidationError(ExtractionError):
    """The model answered but not in the shape the schema demands."""


class EmptyResponseError(ExtractionError):
    """No candidate text came back (safety block, truncation, or an empty answer)."""


class ModelCallError(ExtractionError):
    """The API call itself failed after retries."""


class TruncatedResponseError(ExtractionError):
    """The model stopped before finishing (MAX_TOKENS, SAFETY, ...); the JSON is incomplete."""


_LABEL_BAD = re.compile(r"[^a-z0-9_-]")


def label_value(value: str) -> str:
    """GCP label values: lowercase letters, digits, underscore, dash, at most 63 characters."""
    return _LABEL_BAD.sub("-", value.lower())[:63]


@dataclass(frozen=True)
class ModelResponse:
    raw_text: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    latency_ms: int
    finish_reason: str | None
    extraction: Extraction | None = None
    validation_error: str | None = None


class ExtractionModel(Protocol):
    @property
    def model_id(self) -> str: ...

    def extract(self, prompt: Prompt, text: str) -> ModelResponse: ...


def _validate(raw_text: str) -> tuple[Extraction | None, str | None]:
    try:
        return Extraction.model_validate_json(raw_text), None
    except ValidationError as exc:
        return None, str(exc)[:2000]


@dataclass
class FakeModel:
    """Deterministic stand-in for tests. Answers come from a callable or a canned list."""

    model_id: str = "fake-model"
    answer: Callable[[str], dict[str, Any] | str] | None = None
    canned: list[dict[str, Any] | str] = field(default_factory=lambda: list[dict[str, Any] | str]())
    calls: list[str] = field(default_factory=list[str])
    input_tokens: int = 1000
    output_tokens: int = 200

    def extract(self, prompt: Prompt, text: str) -> ModelResponse:
        self.calls.append(text)
        if self.answer is not None:
            out = self.answer(text)
        elif self.canned:
            out = self.canned.pop(0)
        else:
            raise ModelCallError("fake model has no answer configured")
        raw = out if isinstance(out, str) else json.dumps(out)
        extraction, err = _validate(raw)
        return ModelResponse(
            raw_text=raw,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            thinking_tokens=0,
            latency_ms=1,
            finish_reason="STOP",
            extraction=extraction,
            validation_error=err,
        )


class GeminiModel:
    """Gemini on Vertex AI through the google-genai SDK, global endpoint."""

    def __init__(
        self,
        model_id: str,
        *,
        project: str,
        location: str = "global",
        labels: dict[str, str] | None = None,
        max_attempts: int = 4,
    ) -> None:
        from google import genai

        self._model_id = model_id
        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._labels = labels or {}
        self._max_attempts = max_attempts

    @property
    def model_id(self) -> str:
        return self._model_id

    def extract(self, prompt: Prompt, text: str) -> ModelResponse:
        import httpx
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=prompt.system,
            response_mime_type="application/json",
            response_schema=Extraction,
            temperature=0,
            labels={
                k: label_value(v)
                for k, v in {
                    **self._labels,
                    "prompt": prompt.version,
                    "model": self._model_id,
                }.items()
            },
        )
        delay = 2.0
        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            try:
                response = self._client.models.generate_content(
                    model=self._model_id, contents=prompt.render(text), config=config
                )
            except errors.APIError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self._max_attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise ModelCallError(f"{exc.code}: {exc.message}") from exc
            except (httpx.HTTPError, OSError) as exc:  # transport-level: retry like a 5xx
                if attempt < self._max_attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise ModelCallError(f"{type(exc).__name__}: {exc}") from exc
            except Exception as exc:
                raise ModelCallError(f"{type(exc).__name__}: {exc}") from exc
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = response.usage_metadata
            raw = response.text or ""
            finish = None
            if response.candidates:
                fr = response.candidates[0].finish_reason
                finish = fr.name if fr is not None else None
            if not raw.strip():
                raise EmptyResponseError(f"finish_reason={finish}")
            if finish not in (None, "STOP"):
                raise TruncatedResponseError(f"finish_reason={finish}")
            extraction, err = _validate(raw)
            return ModelResponse(
                raw_text=raw,
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
                thinking_tokens=(getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0,
                latency_ms=latency_ms,
                finish_reason=finish,
                extraction=extraction,
                validation_error=err,
            )
        raise ModelCallError("unreachable")  # pragma: no cover
