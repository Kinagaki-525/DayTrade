"""Deterministic decode policy for stored raw source pages.

Order (first match wins):

1. Byte-order mark (UTF-8 / UTF-16 LE / UTF-16 BE)
2. ``charset=`` of the transport ``Content-Type`` header
3. ``charset=`` of an HTML ``<meta>`` declaration in the first 4 KiB
4. UTF-8, strict

Any failure to decode under the selected codec is ``PARSE_FAILED``. There is
no lenient/replacement decoding and no chardet-style guessing: a page we
cannot decode exactly is not a page we are allowed to read numbers from.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass


class DecodeError(ValueError):
    """Raised with the stable ``PARSE_FAILED`` code when decoding fails."""

    def __init__(self, message: str) -> None:
        super().__init__(f"PARSE_FAILED: {message}")
        self.code = "PARSE_FAILED"
        self.message = message


_META_SCAN_BYTES = 4096
_CHARSET_PATTERN = re.compile(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-.:]+)", re.IGNORECASE)

_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


@dataclass(frozen=True)
class DecodedPage:
    text: str
    encoding: str
    encoding_source: str


def _charset_from_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    match = _CHARSET_PATTERN.search(content_type.encode("ascii", "ignore"))
    if match is None:
        return None
    return match.group(1).decode("ascii").lower()


def _charset_from_meta(data: bytes) -> str | None:
    match = _CHARSET_PATTERN.search(data[:_META_SCAN_BYTES])
    if match is None:
        return None
    return match.group(1).decode("ascii", "ignore").lower() or None


def decode_source_page(data: bytes, content_type: str | None = None) -> DecodedPage:
    if not isinstance(data, bytes):
        raise DecodeError("raw source page must be bytes")

    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return _decode_with(data, encoding, "BOM")

    header_charset = _charset_from_content_type(content_type)
    if header_charset:
        return _decode_with(data, header_charset, "CONTENT_TYPE")

    meta_charset = _charset_from_meta(data)
    if meta_charset:
        return _decode_with(data, meta_charset, "META")

    return _decode_with(data, "utf-8", "DEFAULT_UTF8")


def _decode_with(data: bytes, encoding: str, source: str) -> DecodedPage:
    try:
        text = data.decode(encoding, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise DecodeError(
            f"could not decode raw source page as {encoding} ({source}): {exc}"
        ) from exc
    return DecodedPage(text=text, encoding=encoding, encoding_source=source)
