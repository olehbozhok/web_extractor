"""Tests for validating value types and TTL logic."""

from datetime import UTC, datetime, timedelta

import pytest

from web_extractor.domain.ttl import cache_age, compute_expires_at, is_expired
from web_extractor.domain.types import (
    AgentId,
    HttpStatus,
    NonEmptyStr,
    PositiveInt,
    Sha256Hex,
    TaskId,
)

CASES_AGENT_INVALID = [
    "",
    "   ",
    ".starts-with-dot",
    "-starts-with-dash",
    "has space",
    "has/slash",
    "a" * 65,
]


@pytest.mark.parametrize("raw", CASES_AGENT_INVALID)
def test_agent_id_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError, match="agent id"):
        AgentId(raw)


def test_agent_id_accepts_valid_values() -> None:
    agent = AgentId(" verifier-1 ")
    assert agent.value == "verifier-1"


@pytest.mark.parametrize("raw", ["", "   ", "a" * 129])
def test_task_id_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError, match="task id"):
        TaskId(raw)


def test_task_id_accepts_valid_values() -> None:
    assert TaskId("check-citations-2026").value == "check-citations-2026"


def test_non_empty_str_strips_and_rejects_blank() -> None:
    assert NonEmptyStr(" content ").value == "content"
    with pytest.raises(ValueError, match="empty"):
        NonEmptyStr("  ")


def test_sha256_hex_normalizes_case() -> None:
    digest = Sha256Hex("A" * 64)
    assert digest.value == "a" * 64
    with pytest.raises(ValueError, match="sha256"):
        Sha256Hex("zz" * 32)
    with pytest.raises(ValueError, match="sha256"):
        Sha256Hex("a" * 63)


def test_http_status_bounds() -> None:
    assert HttpStatus(200).value == 200
    assert HttpStatus(599).value == 599
    for bad in (99, 600, 0):
        with pytest.raises(ValueError, match="HTTP status"):
            HttpStatus(bad)


def test_positive_int_rejects_zero_and_negatives() -> None:
    assert PositiveInt(1).value == 1
    for bad in (0, -1):
        with pytest.raises(ValueError, match="positive"):
            PositiveInt(bad)


def test_ttl_expiry_boundary_belongs_to_next_fetch() -> None:
    fetched_at = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
    expires_at = compute_expires_at(fetched_at, timedelta(hours=24))
    assert expires_at == datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    assert not is_expired(expires_at, datetime(2026, 11, 10, 11, 59, 59, tzinfo=UTC))
    assert is_expired(expires_at, datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC))


def test_cache_age_measures_elapsed_time() -> None:
    fetched_at = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
    now = fetched_at + timedelta(hours=3)
    assert cache_age(now, fetched_at).total_seconds() == 3 * 3600
