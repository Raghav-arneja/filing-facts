"""Stable document id derived from the ZIP member name.

Members are named Prod223_4298_<company>_<period-end>.html (or _CIC.zip for community
interest companies). Company number plus period end is unique per set of accounts and
survives re-publication, so it is the document id for every later stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from filing_facts.parse.errors import UnknownDocumentNameError

_NAME = re.compile(
    r"^Prod(?P<product>\d+)_(?P<batch>\d+)_(?P<company>[A-Z0-9]{8})_(?P<period>\d{8})"
    r"(?P<cic>_CIC)?\.(?P<ext>html|xhtml|xml|zip)$"
)


@dataclass(frozen=True)
class DocumentKey:
    document_id: str
    company_number: str
    period_end: date
    is_cic_archive: bool
    extension: str


def document_key(member_name: str) -> DocumentKey:
    base = member_name.rsplit("/", 1)[-1]
    m = _NAME.match(base)
    if m is None:
        raise UnknownDocumentNameError(base)
    p = m["period"]
    try:
        period_end = date(int(p[:4]), int(p[4:6]), int(p[6:8]))
    except ValueError as exc:
        raise UnknownDocumentNameError(f"{base}: bad period {p}") from exc
    return DocumentKey(
        document_id=f"{m['company']}_{p}",
        company_number=m["company"],
        period_end=period_end,
        is_cic_archive=m["cic"] is not None,
        extension=m["ext"],
    )
