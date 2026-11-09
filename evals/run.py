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
from evals.mcp_client import evaluation_server
from evals.tasks import EvalReport, load_corpus


async def run_baseline() -> EvalReport:
    """Run the whole corpus with the baseline agent against the real tools."""
    tasks = load_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        async with (
            evaluation_server(Path(tmp)) as (_url, factory),
            factory.client() as tools,
        ):
            return await run_corpus(tasks, BaselineAgent(), tools)


def render_report(report: EvalReport) -> str:
    """Markdown summary of a corpus run."""
    lines = [
        "# Evaluation run",
        "",
        f"- tasks: {report.total}",
        f"- passed: {report.passed} ({report.accuracy_pct}% accuracy)",
        f"- average tool calls per task: {report.avg_calls}",
        f"- tool errors: {report.tool_errors}",
        f"- total duration: {report.total_duration_s}s",
        "",
        "| task | passed | calls | errors | duration, s | reason |",
        "|---|---|---|---|---|---|",
    ]
    for result in report.results:
        mark = "yes" if result.passed else "NO"
        lines.append(
            f"| {result.task_id} | {mark} | {result.calls} | {result.tool_errors} | "
            f"{result.duration_s} | {result.reason} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tool evaluation")
    parser.add_argument("--agent", choices=["baseline"], default="baseline")
    args = parser.parse_args()
    if args.agent != "baseline":
        print("only the baseline agent ships; see docs/evals/README.md for LLM adapters")
        return 2
    report = asyncio.run(run_baseline())
    print(render_report(report))
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
