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

THIN_CHARS: Final = 800
"""Below this many characters a page is too small to count as a document."""

MARKER_SCAN_CHARS: Final = 3000
"""General challenge and login markers are only trusted on short texts: a
long article may legitimately discuss logins or captchas. Unambiguous
challenge signatures (:data:`_UNAMBIGUOUS_CHALLENGE_MARKERS`) are scanned at
any length and are not subject to this limit."""

_UNAMBIGUOUS_CHALLENGE_MARKERS: Final = (
    "verify you are human",
    "verify you are a human",
    "checking your browser",
    "just a moment",
    "enable javascript and cookies",
    "please wait for verification",
    "whoa there, pardner",
    "something went wrong, but don't fret",
)
"""Signatures of interstitial bot-check screens, scanned on texts of any length.

Each is a full phrase shown only by challenge pages (Cloudflare and friends);
a real document almost never quotes one verbatim, while a challenge page
without a match used to pass as ``ok`` — and a false ``ok`` gets cached, where
a false ``challenge`` is only a warning the verdict is allowed to give. Hence
no length limit for these."""

_GENERAL_CHALLENGE_MARKERS: Final = (
    "attention required",
    "are you a robot",
    "captcha",
    "blocked by network security",
    "javascript is not available",
    "javascript is disabled",
    "підтвердьте, що ви не робот",
    "я не робот",
)
"""Single words and short phrases a real document may legitimately quote
(an article *about* captchas, Cloudflare's own prose). Trusted only on texts
up to :data:`MARKER_SCAN_CHARS`, as before, to keep long legitimate articles
from being flagged."""
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
    lowered = stripped.lower()
    if any(marker in lowered for marker in _UNAMBIGUOUS_CHALLENGE_MARKERS):
        return "challenge"
    if len(stripped) <= MARKER_SCAN_CHARS:
        if any(marker in lowered for marker in _GENERAL_CHALLENGE_MARKERS):
            return "challenge"
        if any(marker in lowered for marker in _LOGIN_MARKERS):
            return "login_wall"
    if len(stripped) < THIN_CHARS:
        return "thin"
    return "ok"
