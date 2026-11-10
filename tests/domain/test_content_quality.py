"""Content verdict: table-driven cases for each quality class."""

import pytest

from web_extractor.domain.content_quality import (
    MARKER_SCAN_CHARS,
    THIN_CHARS,
    ContentQuality,
    assess_content,
)

_LONG = "A reasonably long sentence about a real topic. " * 40


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_LONG, "ok"),
        ("x" * THIN_CHARS, "ok"),
        ("x" * (THIN_CHARS - 1), "thin"),
        ("Instagram. Log in to continue.", "login_wall"),
        ("Please verify you are human before proceeding.", "challenge"),
        ("Just a moment... Checking your browser before accessing the site.", "challenge"),
        ("Увійдіть, щоб переглянути цей допис.", "login_wall"),
        ("Підтвердьте, що ви не робот", "challenge"),
        ("A short note that mentions CAPTCHA once.", "challenge"),
        ("   \n  ", "thin"),
    ],
)
def test_verdict(text: str, expected: ContentQuality) -> None:
    assert assess_content(text) == expected


def test_long_article_mentioning_login_is_not_flagged() -> None:
    text = _LONG + " The report explains why users must log in to continue. " + _LONG * 20
    assert len(text) > MARKER_SCAN_CHARS
    assert assess_content(text) == "ok"


def test_long_challenge_page_with_strong_marker_is_flagged() -> None:
    """Unambiguous challenge signatures are scanned on texts of any length.

    Regression: a ~5900-char challenge page containing "just a moment" used to
    pass as ``ok`` because the marker scan skipped texts over the general-word
    limit, and the false ``ok`` was then cached.
    """
    sentence = (
        "Just a moment while we verify your connection. "
        "This process happens automatically once our servers finish the check. "
    )
    text = sentence * 50
    assert len(text) > MARKER_SCAN_CHARS
    assert assess_content(text) == "challenge"


def test_long_article_about_captchas_is_not_flagged() -> None:
    """General markers stay length-limited: a long article may discuss captchas."""
    body = (
        "The security report explains how websites present a captcha and why "
        "automated solvers fail against modern bot detection systems. "
    ) * 40
    assert len(body) > MARKER_SCAN_CHARS
    assert "captcha" in body.lower()
    assert assess_content(body) == "ok"


def test_challenge_wins_over_login_when_both_present() -> None:
    assert assess_content("Log in to continue. Verify you are human.") == "challenge"
