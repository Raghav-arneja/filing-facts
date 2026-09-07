from __future__ import annotations

from datetime import date

from filing_facts.sources.companies_house import source_key, source_url


def test_source_key_is_named_for_publish_date() -> None:
    assert source_key(date(2026, 9, 1)) == "Accounts_Bulk_Data-2026-09-01.zip"


def test_source_url_joins_without_double_slash() -> None:
    url = source_url("https://download.companieshouse.gov.uk/", date(2026, 9, 1))
    assert url == "https://download.companieshouse.gov.uk/Accounts_Bulk_Data-2026-09-01.zip"
