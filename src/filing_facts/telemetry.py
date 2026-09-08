# The Cloud Trace exporter is marked deprecated in favour of OTLP; it works without a
# collector, which suits jobs that must exit promptly. Revisit when OTLP needs no sidecar.
# pyright: reportDeprecated=false
"""OpenTelemetry tracing for the jobs, exported to Cloud Trace, correlated with the logs.

One span per job run, one per document inside it. Every structlog line inside a span carries
the Cloud Logging trace field, so a trace and its log lines open together in the console.
Tracing is on whenever a project is configured and off in dry runs and tests, which use an
in-memory exporter through `capture_spans`.
"""

from __future__ import annotations

from collections.abc import Generator, MutableMapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_provider: TracerProvider | None = None
_project: str = ""


def configure_tracing(service: str, project: str, *, enabled: bool) -> None:
    """Install a tracer provider once. With enabled=False spans are created but not exported,
    which keeps the job code identical between the cloud and a dry run."""
    global _provider, _project
    if _provider is not None:
        return
    _project = project
    _provider = TracerProvider(resource=Resource.create({"service.name": service}))
    if enabled and project:
        # The exporter is marked deprecated in favour of OTLP to Cloud Trace; it still works
        # and needs no collector, which suits a job that must exit promptly.
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        exporter = CloudTraceSpanExporter(project_id=project)
        _provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)


def flush() -> None:
    """Cloud Run jobs exit as soon as main returns; the batch exporter needs a final push."""
    if _provider is not None:
        _provider.force_flush(timeout_millis=5000)


def add_trace_context(
    _logger: object, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: attach the current trace so Cloud Logging correlates log and span."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        trace_id = format(ctx.trace_id, "032x")
        event_dict["logging.googleapis.com/trace"] = (
            f"projects/{_project}/traces/{trace_id}" if _project else trace_id
        )
        event_dict["logging.googleapis.com/spanId"] = format(ctx.span_id, "016x")
    return event_dict


@contextmanager
def capture_spans() -> Generator[InMemorySpanExporter, None, None]:
    """Tests: a fresh provider with an in-memory exporter, torn down afterwards."""
    global _provider
    previous = _provider
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _provider = provider
    # The global tracer provider can only be set once per process; use the provider directly.
    token = _ActiveProvider.swap(provider)
    try:
        yield exporter
    finally:
        _ActiveProvider.restore(token)
        _provider = previous


class _ActiveProvider:
    """Test-time override: `tracer()` reads from here so tests never touch the global."""

    override: TracerProvider | None = None

    @classmethod
    def swap(cls, provider: TracerProvider) -> TracerProvider | None:
        previous, cls.override = cls.override, provider
        return previous

    @classmethod
    def restore(cls, previous: TracerProvider | None) -> None:
        cls.override = previous


def tracer(name: str) -> trace.Tracer:
    if _ActiveProvider.override is not None:
        return _ActiveProvider.override.get_tracer(name)
    return trace.get_tracer(name)
