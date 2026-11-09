"""find_matches: how each match relates to the query (kind)."""

import pytest

from web_extractor.service.search import MatchKind, find_matches


@pytest.mark.parametrize(
    ("text", "query", "expected"),
    [
        ("the Budget is final", "Budget", "exact"),
        ("the BUDGET is final", "Budget", "case_differs"),
        ("she said “hello”", '"hello"', "normalized"),
        ("a — b", "a - b", "normalized"),
        ("A — B", "a - b", "normalized"),
    ],
)
def test_match_kind(*, text: str, query: str, expected: MatchKind) -> None:
    (match,) = find_matches(text, query, ignore_case=True, context_chars=0, max_matches=1)

    assert match.kind == expected
    assert match.verbatim is (expected == "exact")


def test_case_sensitive_search_does_not_find_case_differences() -> None:
    assert find_matches("BUDGET", "Budget", ignore_case=False, context_chars=0, max_matches=1) == ()
