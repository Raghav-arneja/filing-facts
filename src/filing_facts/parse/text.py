"""Render a filing to the plain text a reader would see, with every XBRL tag removed.

This is the LLM's input in Stage 3. It must contain exactly what a human sees and nothing
the tags add: ix:header (hidden facts, references, contexts) is dropped entirely, and the
ix:* fact wrappers are transparent, leaving only their displayed text.
"""

from __future__ import annotations

# lxml stubs mark _Element private; it is the only element type there is.
# pyright: reportPrivateUsage=false
from lxml import etree

from filing_facts.parse.ixbrl import IX_NAMESPACES
from filing_facts.parse.xml import parse_tree

_BLOCK = frozenset(
    {
        "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
        "table", "tr", "thead", "tbody", "tfoot", "section", "article", "header",
        "footer", "hr", "blockquote", "pre", "dl", "dt", "dd", "caption",
    }
)  # fmt: skip
_CELL = frozenset({"td", "th"})
_DROP = frozenset({"script", "style", "head", "title"})


def render_text(data: bytes) -> str:
    return render_text_tree(parse_tree(data))


def render_text_tree(root: etree._Element) -> str:
    parts: list[str] = []
    _walk(root, parts)
    text = "".join(parts)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip() + "\n"


def _walk(el: etree._Element, parts: list[str]) -> None:
    if not isinstance(el.tag, str):  # comments, processing instructions
        if el.tail:
            parts.append(el.tail)
        return
    qname = etree.QName(el.tag)
    local, ns = qname.localname, qname.namespace
    if (ns in IX_NAMESPACES and local in {"header", "exclude"}) or local in _DROP:
        if el.tail:
            parts.append(el.tail)
        return
    if local == "tr":
        _row(el, parts)
        if el.tail:
            parts.append(el.tail)
        return
    if local in _BLOCK:
        parts.append("\n")
    if el.text:
        parts.append(el.text)
    for child in el:
        _walk(child, parts)
    if local in _BLOCK:
        parts.append("\n")
    if el.tail:
        parts.append(el.tail)


def _row(tr: etree._Element, parts: list[str]) -> None:
    """One table row becomes one line; empty cells vanish; cells are joined with ' | '."""
    cells: list[str] = []
    for child in tr:
        if not isinstance(child.tag, str):
            continue
        if etree.QName(child.tag).localname in _CELL:
            buf: list[str] = []
            _walk(child, buf)
            text = " ".join("".join(buf).split())
            if text:
                cells.append(text)
        else:
            _walk(child, parts)
    parts.append("\n" + " | ".join(cells) + "\n")
