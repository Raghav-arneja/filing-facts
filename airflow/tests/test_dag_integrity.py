"""Runs inside the Airflow container: every DAG file imports, and the graphs are as designed.

Pytest-compatible, but the official image ships no pytest, so it also runs as a script.
"""

from __future__ import annotations

from airflow.models import DagBag


def _bag() -> DagBag:
    bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)
    assert not bag.import_errors, bag.import_errors
    return bag


def test_all_dags_import() -> None:
    assert set(_bag().dag_ids) == {"filing_facts_daily", "filing_facts_backfill"}


def test_daily_chain_is_ingest_parse_extract() -> None:
    dag = _bag().dags["filing_facts_daily"]
    assert dag.schedule is None, "Cloud Scheduler owns the daily trigger"
    assert [t.task_id for t in dag.topological_sort()] == ["ingest", "parse", "extract"]
    assert dag.get_task("extract").upstream_task_ids == {"parse"}


def test_backfill_releases_then_branches_by_stage() -> None:
    dag = _bag().dags["filing_facts_backfill"]
    assert dag.get_task("release_quarantined").upstream_task_ids == {"handle_dead_letters"}
    assert dag.get_task("which_stage").upstream_task_ids == {"release_quarantined"}
    assert dag.params["dead_letters"] == "none"
    assert dag.get_task("rerun_extract").upstream_task_ids == {"which_stage"}
    assert dag.get_task("released_parse_sources").upstream_task_ids == {"which_stage"}
    assert dag.get_task("rerun_parse").upstream_task_ids == {"released_parse_sources"}
    assert dag.params["stage"] == "extract"


if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
    sys.exit(0)
