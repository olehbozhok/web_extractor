"""Which response content types the service can turn into text.

HTML and PDF are extracted; anything that is clearly something else (images,
archives, media) is rejected as ``unsupported_type`` before the body is
downloaded. A missing or generic type (``application/octet-stream``) is
allowed through: servers often mislabel PDFs, and the extractor sniffs the
body itself.
"""

from typing import Final

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
