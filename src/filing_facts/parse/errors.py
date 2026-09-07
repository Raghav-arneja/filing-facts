"""Parse failures. Each maps to a quarantine reason; none is ever swallowed."""

from __future__ import annotations


class ParseError(Exception):
    """Base class. The class name is the quarantine reason."""


class MalformedDocumentError(ParseError):
    """Not well-formed XML. Companies House filings are XHTML, so this is rare and real."""


class NotInlineXbrlError(ParseError):
    """Well-formed, but declares no inline XBRL namespace, so there are no facts to read."""


class UnsupportedFormatError(ParseError):
    """A numeric fact uses an ixt transformation this parser does not implement yet."""


class UnknownDocumentNameError(ParseError):
    """The ZIP member name does not match the Companies House naming scheme."""
