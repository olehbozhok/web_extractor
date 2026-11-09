"""Content verdict, separate from the HTTP outcome (docs/03, section 3.5).

An HTTP 200 does not mean the agent got the document: login walls,
bot-challenge pages and near-empty shells all arrive with status 200. The
verdict is derived from the extracted text, so it needs no storage and is
recomputed for cached pages. Markers are heuristics by design; the verdict
only ever *adds* a warning, it never turns a result into an error.
"""

from typing import Final, Literal

type ContentQuality = Literal["ok", "thin", "login_wall", "challenge"]
"""``ok``: looks like a real document; ``thin``: too little text to be one;
``login_wall``: a sign-in prompt instead of the content; ``challenge``: a
bot check (captcha, JS challenge)."""

THIN_CHARS: Final = 500
"""Below this many characters a page is too small to count as a document."""

MARKER_SCAN_CHARS: Final = 3000
"""Markers are only trusted on short texts: a long article may legitimately
discuss logins or captchas."""

_CHALLENGE_MARKERS: Final = (
    "verify you are human",
    "verify you are a human",
    "checking your browser",
    "just a moment",
    "enable javascript and cookies",
    "attention required",
    "are you a robot",
    "captcha",
    "підтвердьте, що ви не робот",
    "я не робот",
)
_LOGIN_MARKERS: Final = (
    "log in to continue",
    "sign in to continue",
    "log in to see",
    "log in or sign up",
    "login to view",
    "sign in to view",
    "you must be logged in",
    "create an account to",
    "увійдіть, щоб",
    "увійдіть щоб",
    "щоб продовжити, увійдіть",
)


def assess_content(text: str) -> ContentQuality:
    """Classify extracted ``text``; challenge and login markers win over thin."""
    stripped = text.strip()
    if len(stripped) <= MARKER_SCAN_CHARS:
        lowered = stripped.lower()
        if any(marker in lowered for marker in _CHALLENGE_MARKERS):
            return "challenge"
        if any(marker in lowered for marker in _LOGIN_MARKERS):
            return "login_wall"
    if len(stripped) < THIN_CHARS:
        return "thin"
    return "ok"
