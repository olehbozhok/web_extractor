"""Content-type policy: extractable and unknown types pass, the rest do not."""

import pytest

from web_extractor.domain.content_type import is_supported_content_type, media_type


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, True),
        ("", True),
        ("text/html", True),
        ("text/html; charset=utf-8", True),
        ("TEXT/HTML", True),
        ("application/pdf", True),
        ("application/octet-stream", True),
        ("application/xhtml+xml", True),
        ("text/plain", True),
        ("application/zip", False),
        ("image/png", False),
        ("video/mp4", False),
        ("application/json", False),
    ],
)
def test_supported(*, header: str | None, expected: bool) -> None:
    assert is_supported_content_type(header) is expected


def test_media_type_drops_parameters_and_case() -> None:
    assert media_type("Text/HTML; charset=UTF-8") == "text/html"
    assert media_type(None) == ""
