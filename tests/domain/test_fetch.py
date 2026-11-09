"""Tests for fetch-stage types: validation of failures and requests."""

import pytest

from web_extractor.domain.fetch import FetchFailure, FetchRequest
from web_extractor.domain.types import AgentId, HttpStatus
from web_extractor.domain.urls import NormalizedUrl, parse_normalized_url


def _example_url() -> NormalizedUrl:
    parsed = parse_normalized_url("https://example.com/")
    assert isinstance(parsed, NormalizedUrl)
    return parsed


def test_fetch_failure_blocked_requires_http_status() -> None:
    with pytest.raises(ValueError, match="http_status"):
        FetchFailure(kind="blocked", message="forbidden")


def test_fetch_failure_not_found_requires_http_status() -> None:
    with pytest.raises(ValueError, match="http_status"):
        FetchFailure(kind="not_found", message="missing")


def test_fetch_failure_message_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="message"):
        FetchFailure(kind="timeout", message="   ")


def test_fetch_failure_with_status_is_valid() -> None:
    failure = FetchFailure(
        kind="blocked",
        message="403 forbidden",
        http_status=HttpStatus(403),
        final_url=_example_url(),
    )
    assert failure.http_status is not None
    assert failure.http_status.value == 403


def test_fetch_request_holds_parsed_url() -> None:
    url = _example_url()
    request = FetchRequest(url=url, fresh=True, render="static", agent=AgentId("tester"))
    assert request.url == url
    assert request.task is None


def test_fetch_request_is_immutable() -> None:
    request = FetchRequest(url=_example_url(), fresh=False, render="auto", agent=AgentId("tester"))
    # Assign through a variable name: bandit's B010 rightly dislikes
    # setattr with a constant, and the runtime guard is what this test pins.
    attribute_name = "fresh"
    with pytest.raises(AttributeError):
        setattr(request, attribute_name, True)
