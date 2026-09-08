# Route handlers are registered by decorator inside the factory; pyright cannot see the use.
# pyright: reportUnusedFunction=false
"""FastAPI service receiving Pub/Sub push deliveries for the lifecycle channel.

Contract with Pub/Sub:
  * 2xx acknowledges. 4xx and 5xx both cause redelivery; after the subscription's retry
    limit the message goes to the dead-letter topic. So: a message we can never handle
    (malformed, unknown version) is answered 400, a transient failure (job API down) 503.
  * Delivery is at least once. The dispatcher does not deduplicate: the jobs it starts are
    idempotent per source and batch, so a replayed event costs one no-op execution.

Authentication is Cloud Run IAM: the service is private and the push subscription's
service account holds run.invoker, so Cloud Run rejects anything else before it gets here.
"""

from __future__ import annotations

import base64
import binascii
import json

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from filing_facts.config import Settings
from filing_facts.dispatcher.runner import JobRunner
from filing_facts.events.messages import LifecycleEvent

log = structlog.get_logger(__name__)


class PushMessage(BaseModel):
    """The message inside a Pub/Sub push delivery. Unknown fields are ignored."""

    data: str = ""
    messageId: str = ""  # noqa: N815 - Pub/Sub's field name
    publishTime: str = ""  # noqa: N815


class PushEnvelope(BaseModel):
    """What Pub/Sub POSTs."""

    message: PushMessage
    subscription: str = ""


def decode_event(envelope: PushEnvelope) -> LifecycleEvent:
    if not envelope.message.data:
        raise ValueError("message has no data")
    try:
        raw = base64.b64decode(envelope.message.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"data is not base64: {exc}") from exc
    return LifecycleEvent.model_validate_json(raw)


def plan(event: LifecycleEvent, settings: Settings) -> tuple[str, list[str]] | None:
    """Which job to start for an event, or None when the event needs no action."""
    if event.event == "ingested" and event.source_key:
        return "parse", ["--source-key", event.source_key]
    if event.event == "parsed":
        if settings.extract_on_event:
            return "extract", ["--cap", str(settings.extract_cap)]
        return None
    return None


def create_app(settings: Settings, runner: JobRunner) -> FastAPI:
    app = FastAPI(title="filing-facts dispatcher", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/pubsub", status_code=204)
    async def pubsub(request: Request) -> Response:
        try:
            envelope = PushEnvelope.model_validate(json.loads(await request.body()))
            event = decode_event(envelope)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            # Permanent: retrying cannot help. Pub/Sub retries anyway, then dead-letters it.
            log.warning("event_rejected", error=str(exc)[:500])
            raise HTTPException(status_code=400, detail="malformed event") from exc
        bound = log.bind(
            kind=event.event, source_key=event.source_key, run_id=event.run_id,
            message_id=envelope.message.messageId,
        )  # fmt: skip
        action = plan(event, settings)
        if action is None:
            bound.info("event_acknowledged", action="none")
            return Response(status_code=204)
        job, args = action
        try:
            if runner.is_running(job):
                # One execution per job at a time. Pub/Sub retries with backoff, and the job
                # itself skips anything already done, so a redelivery never doubles work.
                bound.info("job_busy", job=job)
                raise HTTPException(status_code=503, detail="job busy; retry later")
            execution = runner.run_job(job, args)
        except HTTPException:
            raise
        except Exception as exc:
            bound.error("job_start_failed", job=job, error=str(exc))
            raise HTTPException(status_code=503, detail="job start failed") from exc
        bound.info("job_dispatched", job=job, args=args, execution=execution)
        return Response(status_code=204)

    return app


def app() -> FastAPI:
    """Uvicorn factory: `uvicorn filing_facts.dispatcher.app:app --factory`."""
    from filing_facts.dispatcher.runner import CloudRunJobRunner
    from filing_facts.logging import configure_logging

    configure_logging()
    settings = Settings()
    if not settings.gcp_project:
        raise SystemExit("FF_GCP_PROJECT is required for the dispatcher")
    return create_app(settings, CloudRunJobRunner(settings.gcp_project, settings.region))
