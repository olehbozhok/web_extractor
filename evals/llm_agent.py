"""An LLM agent for the harness over an OpenAI-compatible chat API.

DeepSeek exposes an OpenAI-compatible endpoint, so the ``openai`` SDK is
used with a different ``base_url``. The API key is read from the
environment by :func:`make_client` and handed straight to the SDK; it is
never logged, printed or stored.

The agent's structured metadata (``AgentResult.meta``) is merged from the
tool results the *harness observed* (later calls override same-named keys),
not from anything the model claims: verification must not depend on the
model's self-report.
"""

import json
import os
from collections.abc import Sequence
from typing import Final

from openai import AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from evals.harness import AgentResult, ToolClient, ToolOutcome
from evals.mcp_client import ToolSpec
from evals.tasks import TaskSpec

DEEPSEEK_BASE_URL: Final = "https://api.deepseek.com"
API_KEY_ENV: Final = "DEEPSEEK_API_KEY"

_SYSTEM_PROMPT: Final = (
    "You are a research assistant with web-reading tools. Use the tools to answer. "
    "Be concise: finish with a short final answer that states the facts you found."
)


class MissingApiKeyError(Exception):
    """The API key environment variable is not set."""


def make_client() -> AsyncOpenAI:
    """Build a DeepSeek client; the key comes from the environment only."""
    key = os.environ.get(API_KEY_ENV)
    if not key:
        msg = f"environment variable {API_KEY_ENV} is not set"
        raise MissingApiKeyError(msg)
    return AsyncOpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL)


def _tool_params(specs: Sequence[ToolSpec]) -> list[ChatCompletionToolParam]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        }
        for spec in specs
    ]


def _task_message(task: TaskSpec) -> str:
    """The user turn: the task prompt plus the inputs a real caller supplies."""
    lines = [task.prompt]
    if task.url is not None:
        lines.append(f"URL: {task.url}")
    if task.urls:
        lines.append("URLs: " + ", ".join(task.urls))
    if task.query is not None:
        lines.append(f"Text to look for: {task.query}")
    return "\n".join(lines)


def _parse_arguments(raw: str) -> dict[str, object] | None:
    """Tool-call arguments as a dict, or None when the model sent bad JSON."""
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): value for key, value in parsed.items()}


class LlmAgent:
    """Drives a chat model through the tool-calling loop for one task."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        specs: Sequence[ToolSpec],
        *,
        max_completion_tokens: int = 400,
    ) -> None:
        self._client = client
        self._model = model
        self._tools = _tool_params(specs)
        self._max_completion_tokens = max_completion_tokens

    async def solve(self, task: TaskSpec, tools: ToolClient) -> AgentResult:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _task_message(task)},
        ]
        calls = 0
        tool_errors = 0
        input_tokens = 0
        output_tokens = 0
        observed_meta: dict[str, object] = {}
        answer = ""
        for _ in range(task.max_calls + 1):  # tool turns plus the final answer
            offer_tools = calls < task.max_calls
            try:
                completion = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=self._tools if offer_tools else [],
                    max_completion_tokens=self._max_completion_tokens,
                    temperature=0,
                )
            except OpenAIError as error:
                answer = f"[model request failed: {type(error).__name__}]"
                break
            if completion.usage is not None:
                input_tokens += completion.usage.prompt_tokens
                output_tokens += completion.usage.completion_tokens
            message = completion.choices[0].message
            answer = message.content or ""
            requested = [call for call in (message.tool_calls or []) if call.type == "function"]
            if not requested or not offer_tools:
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in requested
                    ],
                }
            )
            for call in requested:
                calls += 1
                arguments = _parse_arguments(call.function.arguments)
                if arguments is None:
                    outcome = ToolOutcome(
                        text="ERROR invalid_argument: arguments were not a JSON object.",
                        meta={},
                        is_error=True,
                    )
                else:
                    outcome = await tools.call(call.function.name, arguments)
                if outcome.is_error:
                    tool_errors += 1
                observed_meta.update(outcome.meta)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": outcome.text})
        return AgentResult(
            answer=answer,
            meta=observed_meta,
            calls=calls,
            tool_errors=tool_errors,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
