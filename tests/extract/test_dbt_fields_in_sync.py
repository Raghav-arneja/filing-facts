"""The dbt model that flattens extraction JSON lists the fields by hand. It must match CONCEPTS."""

from __future__ import annotations

import re
from pathlib import Path

from filing_facts.extract.schema import CONCEPTS

SQL = Path(__file__).parents[2] / "dbt" / "models" / "staging" / "stg_extraction_values.sql"


def test_dbt_field_list_matches_the_extraction_schema() -> None:
    block = re.search(r"\{% set fields = \[(.*?)\] %\}", SQL.read_text(), re.S)
    assert block is not None, "fields block not found"
    pairs = dict(re.findall(r"\('([a-z_]+)', '([A-Za-z]+)'\)", block.group(1)))
    assert pairs == CONCEPTS
