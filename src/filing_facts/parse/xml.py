"""One XML parse per filing. The fact extractor and the text renderer share the tree."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
from lxml import etree

from filing_facts.parse.errors import MalformedDocumentError


def parse_tree(data: bytes) -> etree._Element:
    try:
        return etree.fromstring(data, etree.XMLParser(huge_tree=True, resolve_entities=False))
    except etree.XMLSyntaxError as exc:
        raise MalformedDocumentError(str(exc)) from exc
