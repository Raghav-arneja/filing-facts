from __future__ import annotations

from pathlib import Path

import pytest

from filing_facts.extract.prompt import PROMPTS_DIR, available_versions, load_prompt


def test_v1_loads_and_renders() -> None:
    p = load_prompt("v1")
    assert p.version == "v1"
    assert len(p.sha256) == 12
    assert p.id == f"v1-{p.sha256}"
    assert "UK company accounts" in p.system
    assert "{text}" not in p.render("BODY")
    assert "<accounts>\nBODY\n</accounts>" in p.render("BODY")


def test_prompt_id_changes_when_the_file_changes(tmp_path: Path) -> None:
    src = (PROMPTS_DIR / "v1.md").read_text()
    (tmp_path / "v9.md").write_text(src)
    a = load_prompt("v9", tmp_path)
    (tmp_path / "v9.md").write_text(src + "\n- One more rule.\n")
    b = load_prompt("v9", tmp_path)
    assert a.id != b.id


def test_malformed_prompt_files_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "v2.md").write_text("# System\nno user section")
    with pytest.raises(ValueError, match="User"):
        load_prompt("v2", tmp_path)
    (tmp_path / "v3.md").write_text("# System\nx\n# User\nno placeholder")
    with pytest.raises(ValueError, match="text"):
        load_prompt("v3", tmp_path)


def test_available_versions_lists_repo_prompts() -> None:
    assert "v1" in available_versions()


def test_v2_differs_from_v1_only_in_the_rules_it_claims() -> None:
    v1, v2 = load_prompt("v1"), load_prompt("v2")
    assert v1.id != v2.id
    assert v2.user_template == v1.user_template
    assert "Creditors are amounts owed and are" in v2.system
    assert "Figures in brackets are negative" not in v2.system
