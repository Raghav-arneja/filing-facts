"""Stage 2: turn one iXBRL filing into (a) tagged facts, the answer key, and (b) plain text."""

from filing_facts.parse.document_key import DocumentKey, document_key
from filing_facts.parse.errors import (
    MalformedDocumentError,
    NotInlineXbrlError,
    ParseError,
    UnsupportedFormatError,
)
from filing_facts.parse.ixbrl import Context, Fact, ParsedDocument, parse_ixbrl
from filing_facts.parse.text import render_text

__all__ = [
    "Context",
    "DocumentKey",
    "Fact",
    "MalformedDocumentError",
    "NotInlineXbrlError",
    "ParseError",
    "ParsedDocument",
    "UnsupportedFormatError",
    "document_key",
    "parse_ixbrl",
    "render_text",
]
