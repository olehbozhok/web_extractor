"""Evaluation harness: runs agents over the corpus and verifies outcomes.

The harness is model-agnostic: any :class:`EvalAgent` solves tasks through a
:class:`ToolClient`. Two ToolClient implementations exist: the real one
(``mcp_client``) drives the actual service over MCP; tests and the baseline
run use it too, so harness numbers always describe the shipped system.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from evals.tasks import EvalReport, TaskResult, TaskSpec


class ToolClient(Protocol):
    """What an agent may call: the four MCP tools through one method."""

    async def call(self, tool: str, arguments: Mapping[str, object]) -> "ToolOutcome":
        """Invoke one tool; never raises for expected tool errors."""
        ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """One tool result as the agent sees it."""

    text: str
    meta: Mapping[str, object]
    is_error: bool


@dataclass(frozen=True, slots=True)
class AgentResult:
    """What the agent hands back for verification."""

    answer: str
    meta: Mapping[str, object] = field(default_factory=dict)
    calls: int = 0
    tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class EvalAgent(Protocol):
    """A task solver: turns one task into an answer using the tools."""

    async def solve(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        """Solve ``task``; must respect ``task.max_calls``."""
        ...  # pragma: no cover


async def run_corpus(
    tasks: tuple[TaskSpec, ...], agent: EvalAgent, tools: ToolClient
) -> EvalReport:
    """Run every task, verify its outcome, and collect metrics."""
    results: list[TaskResult] = []
    for task in tasks:
        started = time.perf_counter()
        agent_result = await agent.solve(task, tools)
        duration = time.perf_counter() - started
        passed, reason = verify(task, agent_result)
        results.append(
            TaskResult(
                task_id=task.id,
                passed=passed,
                calls=agent_result.calls,
                tool_errors=agent_result.tool_errors,
                input_tokens=agent_result.input_tokens,
                output_tokens=agent_result.output_tokens,
                duration_s=round(duration, 3),
                reason=reason,
            )
        )
    return EvalReport(results=tuple(results))


def verify(task: TaskSpec, result: AgentResult) -> tuple[bool, str]:
    """Check one agent result against the task's expectations.

    Returns (passed, reason); reason explains the first failed check.
    """
    if result.calls > task.max_calls:
        return False, f"too many tool calls: {result.calls} > {task.max_calls}"

    match task.kind:
        case "read_extract" | "pagination":
            return _verify_contains(task, result)
        case "quote_verify":
            return _verify_quote(task, result)
        case "links_check":
            return _verify_links(task, result)
        case "blocked_source":
            return _verify_status(task, result)


def _verify_contains(task: TaskSpec, result: AgentResult) -> tuple[bool, str]:
    missing = [fragment for fragment in task.expect_contains if fragment not in result.answer]
    if missing:
        return False, f"answer misses {missing!r}"
    return True, "ok"


def _verify_quote(task: TaskSpec, result: AgentResult) -> tuple[bool, str]:
    matches = result.meta.get("matches")
    if task.expect_contains:
        return _verify_contains(task, result)
    if not isinstance(matches, list):
        return False, "no matches list in structured metadata"
    if not matches:
        return False, "expected a match but got none"
    first = matches[0]
    verbatim = first.get("verbatim") if isinstance(first, dict) else None
    if verbatim != task.expect_verbatim:
        return False, f"expected verbatim={task.expect_verbatim}, got {verbatim}"
    return True, "ok"


def _verify_links(task: TaskSpec, result: AgentResult) -> tuple[bool, str]:
    rows = result.meta.get("url_rows")
    if not isinstance(rows, list):
        return False, "no url_rows in structured metadata"
    statuses = {row.get("url"): row.get("status") for row in rows if isinstance(row, dict)}
    expected = task.expect_url_status or {}
    for url, status in expected.items():
        if statuses.get(url) != status:
            return False, f"expected {url} -> {status}, got {statuses.get(url)}"
    return True, "ok"


def _verify_status(task: TaskSpec, result: AgentResult) -> tuple[bool, str]:
    status = result.meta.get("error_kind") or result.meta.get("status")
    if status != task.expect_status:
        return False, f"expected status {task.expect_status!r}, got {status!r}"
    return True, "ok"
