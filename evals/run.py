"""Entry point for evaluation runs.

Usage:
    uv run python -m evals.run --agent baseline

The baseline agent is deterministic; LLM agents plug in through the same
``EvalAgent`` protocol (see docs/evals/README.md). The run writes a
markdown report to stdout.
"""

import argparse
import asyncio
import tempfile
from pathlib import Path

from evals.baseline import BaselineAgent
from evals.harness import run_corpus
from evals.llm_agent import LlmAgent, make_client
from evals.mcp_client import evaluation_server
from evals.tasks import EvalReport, TaskSpec, load_corpus

DEFAULT_PAID_TASKS = (
    "read-extract-1",
    "quote-verify-1",
    "quote-verify-3",
    "links-check-1",
    "links-check-2",
    "blocked-source-1",
)
"""A cheap cross-section for paid runs: one task per kind except pagination
(which needs dozens of calls over a long page)."""


async def run_baseline() -> EvalReport:
    """Run the whole corpus with the baseline agent against the real tools."""
    tasks = load_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        async with (
            evaluation_server(Path(tmp)) as (_url, factory),
            factory.client() as tools,
        ):
            return await run_corpus(tasks, BaselineAgent(), tools)


async def run_llm(model: str, task_ids: tuple[str, ...]) -> EvalReport:
    """Run selected tasks with a paid LLM agent (DeepSeek) against the real tools."""
    by_id: dict[str, TaskSpec] = {task.id: task for task in load_corpus()}
    tasks = tuple(by_id[task_id] for task_id in task_ids)
    client = make_client()
    with tempfile.TemporaryDirectory() as tmp:
        async with (
            evaluation_server(Path(tmp)) as (_url, factory),
            factory.client() as tools,
        ):
            agent = LlmAgent(client, model, await tools.specs())
            return await run_corpus(tasks, agent, tools)


def render_report(report: EvalReport) -> str:
    """Markdown summary of a corpus run."""
    lines = [
        "# Evaluation run",
        "",
        f"- tasks: {report.total}",
        f"- passed: {report.passed} ({report.accuracy_pct}% accuracy)",
        f"- average tool calls per task: {report.avg_calls}",
        f"- tool errors: {report.tool_errors}",
        f"- tokens: {report.input_tokens} in / {report.output_tokens} out",
        f"- total duration: {report.total_duration_s}s",
        "",
        "| task | passed | calls | errors | tokens in/out | duration, s | reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in report.results:
        mark = "yes" if result.passed else "NO"
        lines.append(
            f"| {result.task_id} | {mark} | {result.calls} | {result.tool_errors} | "
            f"{result.input_tokens}/{result.output_tokens} | "
            f"{result.duration_s} | {result.reason} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tool evaluation")
    parser.add_argument("--agent", choices=["baseline", "deepseek"], default="baseline")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--tasks", nargs="+", default=list(DEFAULT_PAID_TASKS))
    parser.add_argument(
        "--confirm-paid",
        action="store_true",
        help="required for paid agents: the run spends API tokens",
    )
    args = parser.parse_args()
    if args.agent == "deepseek":
        if not args.confirm_paid:
            print("deepseek runs spend tokens; pass --confirm-paid to proceed")
            return 2
        report = asyncio.run(run_llm(args.model, tuple(args.tasks)))
    else:
        report = asyncio.run(run_baseline())
    print(render_report(report))
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
