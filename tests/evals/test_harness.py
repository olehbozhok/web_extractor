"""Evaluation harness tests: baseline over the real tools, corpus integrity."""

import tempfile
from pathlib import Path

import pytest
from evals.baseline import BaselineAgent
from evals.harness import AgentResult, run_corpus, verify
from evals.mcp_client import evaluation_server
from evals.tasks import TaskSpec, load_corpus


@pytest.mark.asyncio
async def test_baseline_passes_full_corpus() -> None:
    """The deterministic agent proves harness + tools + corpus agree."""
    tasks = load_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        async with (
            evaluation_server(Path(tmp)) as (_url, factory),
            factory.client() as tools,
        ):
            report = await run_corpus(tasks, BaselineAgent(), tools)
    assert report.passed == report.total
    assert report.tool_errors == 3  # blocked/not_found answers are expected errors
    # Pagination drives the cursor loop; everything else is one call.
    assert report.avg_calls > 1


@pytest.mark.asyncio
async def test_harness_fails_a_broken_expectation() -> None:
    """A wrong expectation must fail, not pass (guards the corpus itself)."""
    tasks = load_corpus()
    broken = TaskSpec(
        id="broken-check",
        kind="quote_verify",
        prompt="expect a match where none exists",
        max_calls=3,
        url=tasks[0].url,
        query="definitely absent phrase xyz",
        expect_verbatim=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        async with (
            evaluation_server(Path(tmp)) as (_url, factory),
            factory.client() as tools,
        ):
            report = await run_corpus((broken,), BaselineAgent(), tools)
    assert report.passed == 0
    assert report.results[0].reason != "ok"


def test_verify_rejects_too_many_calls() -> None:
    task = TaskSpec(
        id="budget",
        kind="read_extract",
        prompt="p",
        max_calls=2,
        url=None,
        expect_contains=("fact",),
    )
    result = AgentResult(answer="fact", calls=3)
    passed, reason = verify(task, result)
    assert passed is False
    assert "too many tool calls" in reason


def test_corpus_loads_and_covers_all_kinds() -> None:
    tasks = load_corpus()
    kinds = {task.kind for task in tasks}
    assert kinds == {"read_extract", "quote_verify", "links_check", "pagination", "blocked_source"}
    assert len(tasks) >= 15  # the plan's corpus size floor (docs/04, M10)
    ids = [task.id for task in tasks]
    assert len(ids) == len(set(ids))
