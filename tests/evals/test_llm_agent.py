"""LlmAgent over the real openai SDK with a mocked HTTP transport (no network)."""

import json
from collections.abc import Mapping

import httpx2
import pytest
from evals.harness import ToolOutcome
from evals.llm_agent import LlmAgent, MissingApiKeyError, make_client
from evals.mcp_client import ToolSpec
from evals.tasks import TaskSpec
from openai import AsyncOpenAI

_SPECS = (ToolSpec("fetch_page", "Fetch a page.", {"type": "object", "properties": {}}),)


def _task(max_calls: int = 3) -> TaskSpec:
    return TaskSpec(
        id="t",
        kind="read_extract",
        prompt="Read the page.",
        max_calls=max_calls,
        url="http://fixtures.test/article",
    )


def _completion(
    *, content: str = "", tool_calls: list[dict[str, object]] | None = None
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _tool_call(arguments: str) -> dict[str, object]:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": "fetch_page", "arguments": arguments},
    }


class _Tools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    async def call(self, tool: str, arguments: Mapping[str, object]) -> ToolOutcome:
        self.calls.append((tool, arguments))
        return ToolOutcome(text="page text", meta={"status": "ok"}, is_error=False)


def _client(responses: list[dict[str, object]], seen: list[dict[str, object]]) -> AsyncOpenAI:
    queue = list(responses)

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=queue.pop(0))

    return AsyncOpenAI(
        api_key="test-key",
        base_url="https://llm.test/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_tool_call_then_final_answer() -> None:
    seen: list[dict[str, object]] = []
    client = _client(
        [
            _completion(tool_calls=[_tool_call('{"url": "http://fixtures.test/article"}')]),
            _completion(content="The year is 1986."),
        ],
        seen,
    )
    tools = _Tools()

    result = await LlmAgent(client, "m", _SPECS).solve(_task(), tools)

    assert tools.calls == [("fetch_page", {"url": "http://fixtures.test/article"})]
    assert result.answer == "The year is 1986."
    assert result.calls == 1
    assert result.tool_errors == 0
    assert result.meta == {"status": "ok"}
    assert (result.input_tokens, result.output_tokens) == (20, 10)
    assert "tools" in seen[0]
    # The task inputs (URL) are given to the model, not only the prompt.
    assert "http://fixtures.test/article" in json.dumps(seen[0]["messages"])


@pytest.mark.asyncio
async def test_invalid_arguments_count_as_tool_error_without_calling_tool() -> None:
    seen: list[dict[str, object]] = []
    client = _client(
        [_completion(tool_calls=[_tool_call("not json")]), _completion(content="gave up")],
        seen,
    )
    tools = _Tools()

    result = await LlmAgent(client, "m", _SPECS).solve(_task(), tools)

    assert tools.calls == []
    assert (result.calls, result.tool_errors) == (1, 1)


@pytest.mark.asyncio
async def test_tools_are_withdrawn_when_the_call_budget_is_spent() -> None:
    seen: list[dict[str, object]] = []
    client = _client(
        [
            _completion(tool_calls=[_tool_call('{"url": "http://fixtures.test/article"}')]),
            _completion(content="final"),
        ],
        seen,
    )

    result = await LlmAgent(client, "m", _SPECS).solve(_task(max_calls=1), _Tools())

    assert result.calls == 1
    assert seen[1]["tools"] == [] or "tools" not in seen[1]


@pytest.mark.asyncio
async def test_transport_failure_becomes_a_failed_answer_not_a_crash() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, json={"error": {"message": "boom"}})

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://llm.test/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    result = await LlmAgent(client, "m", _SPECS).solve(_task(), _Tools())

    assert result.answer.startswith("[model request failed")
    assert result.calls == 0


def test_missing_key_is_reported_without_touching_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        make_client()


class _TwoToolClient:
    async def call(self, tool: str, arguments: Mapping[str, object]) -> ToolOutcome:
        del arguments
        meta: dict[str, object] = (
            {"matches": [{"verbatim": True}]} if tool == "find_in_page" else {"status": "ok"}
        )
        return ToolOutcome(text="x", meta=meta, is_error=False)


@pytest.mark.asyncio
async def test_metadata_from_an_earlier_call_survives_a_later_one() -> None:
    """A follow-up fetch_page must not erase the matches find_in_page returned."""
    first: dict[str, object] = {
        "id": "a",
        "type": "function",
        "function": {"name": "find_in_page", "arguments": "{}"},
    }
    second: dict[str, object] = {
        "id": "b",
        "type": "function",
        "function": {"name": "fetch_page", "arguments": "{}"},
    }
    client = _client(
        [
            _completion(tool_calls=[first]),
            _completion(tool_calls=[second]),
            _completion(content="done"),
        ],
        [],
    )

    result = await LlmAgent(client, "m", _SPECS).solve(_task(), _TwoToolClient())

    assert result.meta == {"matches": [{"verbatim": True}], "status": "ok"}
