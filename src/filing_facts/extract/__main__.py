"""CLI entrypoint for the extract job. Same in the container, on Cloud Run, and locally.

python -m filing_facts.extract                                  # settings' model and prompt
python -m filing_facts.extract --model gemini-3.8-flash --cap 50
python -m filing_facts.extract --dry-run --fake --cap 5          # local files, no model calls
"""

from __future__ import annotations

import argparse
import sys

import structlog

from filing_facts.config import Settings
from filing_facts.extract.job import run
from filing_facts.extract.model import ExtractionModel
from filing_facts.extract.prompt import load_prompt
from filing_facts.logging import configure_logging
from filing_facts.storage.factory import build_backends


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="filing_facts.extract", description=__doc__)
    parser.add_argument("--model", help="Vertex model id. Default: FF_EXTRACT_MODEL.")
    parser.add_argument("--prompt", help="Prompt version, e.g. v1. Default: FF_PROMPT_VERSION.")
    parser.add_argument("--cap", type=int, help="Documents this run. Default: FF_EXTRACT_CAP.")
    parser.add_argument("--min-confidence", type=float)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Local filesystem backends.")
    parser.add_argument("--fake", action="store_true", help="Fake model; no calls, no cost.")
    return parser.parse_args(argv)


def _model(settings: Settings, model_id: str, fake: bool) -> ExtractionModel:
    if fake:
        from tests.extract.conftest import good_answer

        from filing_facts.extract.model import FakeModel

        return FakeModel(model_id=f"fake-{model_id}", answer=lambda _t: good_answer())
    from filing_facts.extract.model import GeminiModel

    return GeminiModel(
        model_id,
        project=settings.gcp_project,
        location=settings.vertex_location,
        labels={"app": "filing-facts", "stage": "3"},
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    settings = Settings()
    model_id = args.model or settings.extract_model
    prompt = load_prompt(args.prompt or settings.prompt_version)
    b = build_backends(settings, dry_run=args.dry_run)
    model = _model(settings, model_id, args.fake)
    log = structlog.get_logger(__name__)
    log.info("extract_start", model=model.model_id, prompt=prompt.id, dry_run=args.dry_run)
    outcome = run(
        settings,
        sink=b.extract_sink,
        model=model,
        prompt=prompt,
        cap=args.cap,
        min_confidence=args.min_confidence,
        threads=args.threads,
    )
    log.info("extract_end", status=outcome.status, cost_usd=outcome.record.cost_usd)
    return 1 if outcome.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
