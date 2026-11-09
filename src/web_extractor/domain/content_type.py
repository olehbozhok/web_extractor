"""Which response content types the service can turn into text.

HTML and PDF are extracted; anything that is clearly something else (images,
archives, media) is rejected as ``unsupported_type`` before the body is
downloaded. A missing or generic type (``application/octet-stream``) is
allowed through: servers often mislabel PDFs, and the extractor sniffs the
body itself.
"""

from collections.abc import Mapping
from typing import Final, Literal

type DocumentKind = Literal["html", "pdf"]
"""What the body actually is, sniffed from its bytes (never from headers or URL)."""

PDF_SIGNATURE: Final = b"%PDF-"
SNIFF_BYTES: Final = 1024
"""The PDF spec tolerates junk before the signature within the first kilobyte."""

_SUPPORTED_EXACT: Final = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
        "text/plain",
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
        "binary/octet-stream",
    }
)


def media_type(header: str | None) -> str:
    """The lowercase ``type/subtype`` of a Content-Type header, parameters dropped."""
    if header is None:
        return ""
    return header.split(";", 1)[0].strip().lower()


def is_supported_content_type(header: str | None) -> bool:
    """True when the declared type is extractable or unknown (then sniffed)."""
    declared = media_type(header)
    return declared == "" or declared in _SUPPORTED_EXACT


def sniff_document(body: bytes) -> DocumentKind:
    """``pdf`` when the body carries the PDF signature near its start, else ``html``.

    A body that starts with markup (``<``, after whitespace) is HTML even if the
    signature appears in its first kilobyte: a page may mention ``%PDF-`` in text.
    """
    head = body[:SNIFF_BYTES]
    if head.lstrip().startswith(b"<"):
        return "html"
    return "pdf" if PDF_SIGNATURE in head else "html"


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    """A header by case-insensitive name (fetchers do not normalize key case)."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None
