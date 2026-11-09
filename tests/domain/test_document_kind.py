"""Document sniffing and case-insensitive header lookup."""

from web_extractor.domain.content_type import header_value, sniff_document


def test_sniff_pdf_signature_within_first_kilobyte() -> None:
    assert sniff_document(b"%PDF-1.7\n...") == "pdf"
    assert sniff_document(b"\x00" * 100 + b"%PDF-1.4") == "pdf"
    assert sniff_document(b"\x00" * 2000 + b"%PDF-1.4") == "html"


def test_markup_is_html_even_if_it_mentions_the_signature() -> None:
    assert sniff_document(b"<html><body>%PDF- in text later</body></html>") == "html"
    assert sniff_document(b"  \n<!doctype html>%PDF-") == "html"


def test_header_lookup_ignores_key_case() -> None:
    headers = {"Content-Type": "application/pdf"}
    assert header_value(headers, "content-type") == "application/pdf"
    assert header_value({"content-type": "text/html"}, "Content-Type") == "text/html"
    assert header_value({}, "content-type") is None
