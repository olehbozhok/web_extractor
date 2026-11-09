"""A deterministic rule-based agent used to validate the harness.

It is not a model: it follows fixed strategies per task kind, which makes
its outcomes fully reproducible. Its purpose is to prove the harness and
the real tool layer behave as the corpus expects (a broken tool or a wrong
expectation fails here before any LLM run burns tokens).
"""

import re

from evals.harness import AgentResult, ToolClient, ToolOutcome
from evals.tasks import TaskSpec

_CALL_LIMIT_KIND_DEFAULT = 3


class BaselineAgent:
    """Rule-based solver; one fixed strategy per task kind."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_errors = 0

    async def solve(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        self.calls = 0
        self.tool_errors = 0
        match task.kind:
            case "read_extract":
                return await self._read_extract(task, tools)
            case "quote_verify":
                return await self._quote_verify(task, tools)
            case "links_check":
                return await self._links_check(task, tools)
            case "pagination":
                return await self._pagination(task, tools)
            case "blocked_source":
                return await self._blocked_source(task, tools)

    async def _call(
        self, tools: ToolClient, tool: str, arguments: dict[str, object]
    ) -> ToolOutcome:
        self.calls += 1
        outcome = await tools.call(tool, arguments)
        if outcome.is_error:
            self.tool_errors += 1
        return outcome

    async def _read_extract(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        if task.url is None:
            msg = f"corpus bug: {task.id} needs url"
            raise ValueError(msg)
        outcome = await self._call(tools, "fetch_page", {"url": task.url, "format": "text"})
        return AgentResult(
            answer=outcome.text,
            meta=dict(outcome.meta),
            calls=self.calls,
            tool_errors=self.tool_errors,
        )

    async def _quote_verify(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        if task.url is None or task.query is None:
            msg = f"corpus bug: {task.id} needs url and query"
            raise ValueError(msg)
        outcome = await self._call(
            tools, "find_in_page", {"url": task.url, "query": task.query, "ignore_case": False}
        )
        return AgentResult(
            answer=outcome.text,
            meta=dict(outcome.meta),
            calls=self.calls,
            tool_errors=self.tool_errors,
        )

    async def _links_check(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        outcome = await self._call(tools, "check_urls", {"urls": list(task.urls)})
        return AgentResult(
            answer=outcome.text,
            meta=dict(outcome.meta),
            calls=self.calls,
            tool_errors=self.tool_errors,
        )

    async def _pagination(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        if task.url is None or task.needle is None:
            msg = f"corpus bug: {task.id} needs url and needle"
            raise ValueError(msg)
        outcome = await self._call(
            tools, "fetch_page", {"url": task.url, "max_chars": 1000, "format": "text"}
        )
        collected = _chunk_text(outcome.text)
        while task.needle not in collected:
            cursor = outcome.meta.get("cursor")
            if not isinstance(cursor, str) or outcome.is_error:
                break
            outcome = await self._call(tools, "read_more", {"cursor": cursor})
            collected += _chunk_text(outcome.text)
        sentence = _sentence_with(collected, task.needle) or collected[-500:]
        return AgentResult(
            answer=sentence, meta=dict(outcome.meta), calls=self.calls, tool_errors=self.tool_errors
        )

    async def _blocked_source(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        if task.url is None:
            msg = f"corpus bug: {task.id} needs url"
            raise ValueError(msg)
        outcome = await self._call(tools, "fetch_page", {"url": task.url})
        return AgentResult(
            answer=outcome.text,
            meta=dict(outcome.meta),
            calls=self.calls,
            tool_errors=self.tool_errors,
        )


def _chunk_text(response_text: str) -> str:
    """The body between the untrusted-content markers, if present."""
    begin = "--- BEGIN UNTRUSTED PAGE CONTENT ---\n"
    end = "\n--- END UNTRUSTED PAGE CONTENT ---"
    if begin in response_text and end in response_text:
        return response_text.split(begin, 1)[1].split(end, 1)[0]
    return response_text


def _sentence_with(text: str, needle: str) -> str | None:
    """The first sentence containing the needle, for a focused answer."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if needle in sentence:
            return sentence
    return None
