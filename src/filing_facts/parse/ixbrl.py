"""Extract tagged facts from an inline XBRL (iXBRL) filing. This is the ground truth.

The identity of a concept is (namespace URI, local name), never the prefix: the same
concept appears as core:Equity, uk-core:Equity, frs-core:Equity and ns5:Equity across
filings, and prefixes are chosen per document. Namespace URIs also carry the taxonomy
version, which Stage 4 will want for slicing results.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# lxml stubs mark _Element private; it is the only element type there is.
# pyright: reportPrivateUsage=false
from lxml import etree

from filing_facts.parse.errors import NotInlineXbrlError
from filing_facts.parse.transforms import transform_numeric
from filing_facts.parse.xml import parse_tree

IX_NAMESPACES = frozenset(
    {"http://www.xbrl.org/2013/inlineXBRL", "http://www.xbrl.org/2008/inlineXBRL"}
)
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"


@dataclass(frozen=True)
class Context:
    id: str
    entity_scheme: str | None
    entity_identifier: str | None
    start_date: date | None
    end_date: date | None
    instant: date | None
    dimensional: bool  # has a segment or scenario; such facts are slices, not totals
    dimensions: str | None  # "Dimension=Member;..." local names, sorted; None when not dimensional


@dataclass(frozen=True)
class Fact:
    concept: str  # local name, e.g. Equity
    namespace: str  # taxonomy namespace URI
    context_id: str
    is_numeric: bool
    value: Decimal | None  # numeric facts only
    text: str  # text as shown, whitespace-collapsed
    unit: str | None
    decimals: str | None
    format: str | None
    scale: int | None
    sign: str | None
    in_hidden: bool  # inside ix:hidden, so invisible in the rendered text


@dataclass(frozen=True)
class ParsedDocument:
    ix_namespace: str
    entity_identifier: str | None
    contexts: dict[str, Context]
    units: dict[str, str]
    facts: tuple[Fact, ...]


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text.strip()) if text and text.strip() else None


def _collapse(text: str) -> str:
    return " ".join(text.split())


def parse_ixbrl(data: bytes) -> ParsedDocument:
    return parse_ixbrl_tree(parse_tree(data))


def parse_ixbrl_tree(root: etree._Element) -> ParsedDocument:
    ix_ns = next((ns for ns in root.nsmap.values() if ns in IX_NAMESPACES), None)
    if ix_ns is None:
        # The declaration may sit on a descendant rather than the root.
        for el in root.iter():
            found = next((ns for ns in el.nsmap.values() if ns in IX_NAMESPACES), None)
            if found:
                ix_ns = found
                break
    if ix_ns is None:
        raise NotInlineXbrlError("no inline XBRL namespace declared")

    contexts = _contexts(root)
    units = _units(root)
    hidden_tag = f"{{{ix_ns}}}hidden"

    facts: list[Fact] = []
    for el in root.iter(f"{{{ix_ns}}}nonFraction", f"{{{ix_ns}}}nonNumeric"):
        name = el.get("name")
        if name is None:
            continue
        prefix, _, local = name.rpartition(":")
        namespace = el.nsmap.get(prefix or None) or ""
        text = _collapse("".join(el.itertext()))
        numeric = etree.QName(el).localname == "nonFraction"
        fmt = el.get("format")
        scale_attr = el.get("scale")
        scale = int(scale_attr) if scale_attr else None
        sign = el.get("sign")
        nil = (
            el.get(f"{{{XBRLI}}}nil") == "true"
            or el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"
        )
        value = (
            None
            if (not numeric or nil)
            else transform_numeric(text, fmt=fmt, scale=scale, sign=sign)
        )
        facts.append(
            Fact(
                concept=local,
                namespace=namespace,
                context_id=el.get("contextRef", ""),
                is_numeric=numeric,
                value=value,
                text=text,
                unit=units.get(el.get("unitRef", ""), el.get("unitRef")) if numeric else None,
                decimals=el.get("decimals"),
                format=fmt,
                scale=scale,
                sign=sign,
                in_hidden=any(a.tag == hidden_tag for a in el.iterancestors()),
            )
        )

    entity = next((c.entity_identifier for c in contexts.values() if c.entity_identifier), None)
    return ParsedDocument(
        ix_namespace=ix_ns,
        entity_identifier=entity,
        contexts=contexts,
        units=units,
        facts=tuple(facts),
    )


def _contexts(root: etree._Element) -> dict[str, Context]:
    out: dict[str, Context] = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        cid = c.get("id")
        if cid is None:
            continue
        ident = c.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}identifier")
        period = c.find(f"{{{XBRLI}}}period")
        start = end = instant = None
        if period is not None:
            start = _date(period.findtext(f"{{{XBRLI}}}startDate"))
            end = _date(period.findtext(f"{{{XBRLI}}}endDate"))
            instant = _date(period.findtext(f"{{{XBRLI}}}instant"))
        dimensional = (
            c.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}segment") is not None
            or c.find(f"{{{XBRLI}}}scenario") is not None
        )
        members = sorted(
            f"{(m.get('dimension') or '').rpartition(':')[-1]}"
            f"={(m.text or '').strip().rpartition(':')[-1]}"
            for m in c.iter(f"{{{XBRLDI}}}explicitMember")
        )
        out[cid] = Context(
            id=cid,
            entity_scheme=ident.get("scheme") if ident is not None else None,
            entity_identifier=_collapse(ident.text or "") if ident is not None else None,
            start_date=start,
            end_date=end,
            instant=instant,
            dimensional=dimensional,
            dimensions=";".join(members) if members else None,
        )
    return out


def _units(root: etree._Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in root.iter(f"{{{XBRLI}}}unit"):
        uid = u.get("id")
        measures = [m.text.rpartition(":")[-1] for m in u.iter(f"{{{XBRLI}}}measure") if m.text]
        if uid and measures:
            out[uid] = "/".join(measures) if len(measures) > 1 else measures[0]
    return out
