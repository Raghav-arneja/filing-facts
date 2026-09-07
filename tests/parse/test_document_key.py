from __future__ import annotations

from datetime import date

import pytest

from filing_facts.parse import document_key
from filing_facts.parse.errors import UnknownDocumentNameError


def test_standard_html_member() -> None:
    k = document_key("Prod223_4298_09469075_20260228.html")
    assert k.document_id == "09469075_20260228"
    assert k.company_number == "09469075"
    assert k.period_end == date(2026, 2, 28)
    assert not k.is_cic_archive
    assert k.extension == "html"


def test_cic_archive_and_nested_path() -> None:
    k = document_key("some/dir/Prod223_4298_07114506_20251231_CIC.zip")
    assert k.document_id == "07114506_20251231"
    assert k.is_cic_archive
    assert k.extension == "zip"


def test_alphanumeric_company_numbers() -> None:
    assert document_key("Prod223_4298_SC123456_20251231.html").company_number == "SC123456"


@pytest.mark.parametrize(
    "name",
    ["notes.txt", "Prod223_4298_1234_20251231.html", "Prod223_4298_09469075_20261399.html"],
)
def test_unrecognised_names_raise(name: str) -> None:
    with pytest.raises(UnknownDocumentNameError):
        document_key(name)
