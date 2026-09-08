from __future__ import annotations

from pathlib import Path

from filing_facts.config import Settings
from filing_facts.parse.job import run as parse_run
from filing_facts.storage.memory import MemoryParseSink, MemoryRawStore
from filing_facts.telemetry import add_trace_context, capture_spans, tracer
from tests.parse.test_job import SOURCE, build_zip, seed


def test_parse_emits_one_run_span_and_one_span_per_member(tmp_path: Path) -> None:
    store, sink = MemoryRawStore(), MemoryParseSink()
    seed(store, build_zip(), tmp_path)
    with capture_spans() as exporter:
        out = parse_run(Settings(parse_cap=100), store=store, sink=sink, source_key=SOURCE)
    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert names.count("parse.run") == 1
    assert names.count("parse.document") == out.record.selected
    run = next(s for s in spans if s.name == "parse.run")
    assert run.attributes is not None
    assert run.attributes["status"] == "succeeded"
    assert run.attributes["source_key"] == SOURCE
    docs = [s for s in spans if s.name == "parse.document"]
    run_context = run.get_span_context()
    assert run_context is not None
    run_span_id = run_context.span_id
    for doc_span in docs:
        parent = doc_span.parent
        assert parent is not None
        assert parent.span_id == run_span_id


def test_log_lines_inside_a_span_carry_the_trace_field() -> None:
    with capture_spans():
        with tracer("t").start_as_current_span("x"):
            out = add_trace_context(None, "info", {"message": "hi"})
        outside = add_trace_context(None, "info", {"message": "bye"})
    assert "logging.googleapis.com/trace" in out
    assert "logging.googleapis.com/spanId" in out
    assert "logging.googleapis.com/trace" not in outside
