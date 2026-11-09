"""Normalized text search for ``find_in_page`` (docs/03, section 3.2).

Comparison normalizes whitespace runs, non-breaking spaces, typographic
quotes and dashes so a quote matches even when the page uses different
characters. Every match reports whether it is verbatim (exact, unmodified
text) or matched only after normalization — the distinction matters when
agents verify quotations. Offsets always refer to the original text so
cursors stay valid.
"""

from dataclasses import dataclass
from typing import Literal

type MatchKind = Literal["exact", "case_differs", "normalized"]
"""``exact``: the span equals the query character for character; ``case_differs``:
the same characters, only letter case differs; ``normalized``: matched only after
whitespace, quote or dash normalization (possibly with case differences too)."""

_WHITESPACE_CHARS = frozenset(" \t\r\n\u00a0\u202f")
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)


@dataclass(frozen=True, slots=True)
class _Hit:
    """One hit in the normalized view: position and matched length."""

    index: int
    needle_length: int


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """One match in the original text with its context."""

    offset: int
    """Character offset of the match start in the original text."""
    before: str
    """Up to ``context_chars`` original characters before the match."""
    match: str
    """The matched span from the original text (may differ from the query)."""
    after: str
    """Up to ``context_chars`` original characters after the match."""
    verbatim: bool
    """True when the original span equals the query exactly (same as ``kind == 'exact'``)."""
    kind: MatchKind
    """How the span relates to the query; ``verbatim=False`` alone does not say
    whether the quote is merely differently cased or differently punctuated."""


def find_matches(
    text: str,
    query: str,
    *,
    ignore_case: bool,
    context_chars: int,
    max_matches: int,
) -> tuple[SearchMatch, ...]:
    """Find the first ``max_matches`` occurrences of ``query`` in ``text``.

    Searching runs on a normalized view of the text (whitespace runs,
    typographic quotes and dashes collapsed); each match is mapped back to
    its original-text offset. A match is ``verbatim`` when the original span
    equals the query without any normalization.
    """
    if not query:
        return ()
    haystack = _NormalizedView(text, ignore_case=ignore_case)
    needle = _normalize(query, ignore_case=ignore_case)
    if not needle:
        return ()

    matches: list[SearchMatch] = []
    position = 0
    while len(matches) < max_matches:
        index = haystack.normalized.find(needle, position)
        if index < 0:
            break
        hit = _Hit(index=index, needle_length=len(needle))
        matches.append(_to_match(text, query, haystack, hit, context_chars))
        position = index + 1
    return tuple(matches)


class _NormalizedView:
    """The normalized form of a text plus per-character original offsets."""

    def __init__(self, text: str, *, ignore_case: bool) -> None:
        normalized_chars: list[str] = []
        offsets: list[int] = []
        index = 0
        while index < len(text):
            char = text[index]
            if char in _WHITESPACE_CHARS:
                normalized_chars.append(" ")
                offsets.append(index)
                while index < len(text) and text[index] in _WHITESPACE_CHARS:
                    index += 1
                continue
            normalized_chars.append(_normalize_char(char, ignore_case=ignore_case))
            offsets.append(index)
            index += 1
        self.normalized = "".join(normalized_chars)
        self.offsets = offsets


def _normalize(text: str, *, ignore_case: bool) -> str:
    """Normalize a query the same way the haystack is normalized.

    Runs of whitespace inside the query collapse like runs in the text.
    """
    chars: list[str] = []
    previous_was_space = False
    for char in text:
        if char in _WHITESPACE_CHARS:
            if not previous_was_space:
                chars.append(" ")
                previous_was_space = True
            continue
        previous_was_space = False
        chars.append(_normalize_char(char, ignore_case=ignore_case))
    return "".join(chars)


def _normalize_char(char: str, *, ignore_case: bool) -> str:
    mapped = char.translate(_QUOTE_MAP)
    if ignore_case:
        return mapped.casefold()
    return mapped


def _to_match(
    text: str,
    query: str,
    haystack: _NormalizedView,
    hit: _Hit,
    context_chars: int,
) -> SearchMatch:
    """Map a normalized-text hit back onto the original text.

    A match is verbatim only when the original span equals the query
    exactly; case-folded matches and matches that relied on quote/dash or
    whitespace normalization are not verbatim.
    """
    start = haystack.offsets[hit.index]
    end = haystack.offsets[hit.index + hit.needle_length - 1] + 1
    span = text[start:end]
    return SearchMatch(
        offset=start,
        before=text[max(0, start - context_chars) : start],
        match=span,
        after=text[end : end + context_chars],
        verbatim=span == query,
        kind=_match_kind(span, query),
    )


def _match_kind(span: str, query: str) -> MatchKind:
    if span == query:
        return "exact"
    if span.casefold() == query.casefold():
        return "case_differs"
    return "normalized"
