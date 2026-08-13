"""Minimal, deterministic HTML shredding helpers.

Deliberately not a full HTML parser: these helpers only expose table rows,
definition-list pairs, and anchor hrefs, which is everything the deterministic
parsers need. Anything a helper cannot express exactly becomes ``PARSE_FAILED``
upstream rather than a heuristic guess.
"""

from __future__ import annotations

import html as html_module
import re

from src.source_parsers.numeric import normalize_text, strip_tags


_ROW_PATTERN = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_PATTERN = re.compile(r"<(td|th)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_DT_DD_PATTERN = re.compile(
    r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", re.IGNORECASE | re.DOTALL
)
_HREF_PATTERN = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


def clean(fragment: str) -> str:
    return normalize_text(html_module.unescape(strip_tags(fragment)))


def table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in _ROW_PATTERN.findall(text):
        cells = [clean(cell) for _, cell in _CELL_PATTERN.findall(row_html)]
        if cells:
            rows.append(cells)
    return rows


def definition_pairs(text: str) -> list[tuple[str, str]]:
    return [(clean(term), clean(value)) for term, value in _DT_DD_PATTERN.findall(text)]


def values_for_label(text: str, label: str) -> list[str]:
    """All ``<dd>`` values whose ``<dt>`` term equals ``label`` exactly."""
    return [value for term, value in definition_pairs(text) if term == label]


def hrefs(text: str) -> list[str]:
    return [html_module.unescape(href) for href in _HREF_PATTERN.findall(text)]
