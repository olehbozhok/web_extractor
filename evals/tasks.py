"""Task corpus and result types for tool evaluation (docs/03, section 8).

Each task is a realistic agent job with a machine-checkable expected
outcome; the harness verifies the agent's answer and structured metadata
against ``expected``. The corpus lives in ``tasks.json`` so it can be
re-run and extended without code changes.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

TaskKind = Literal["read_extract", "quote_verify", "links_check", "pagination", "blocked_source"]

CORPUS_PATH = Path(__file__).parent / "tasks.json"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """One evaluation task with its verification expectations."""

    id: str
    kind: TaskKind
    prompt: str
    """The instruction an agent receives (as a model would see it)."""
    max_calls: int
    """Upper bound on tool calls; exceeding it fails the task."""
    url: str | None = None
    urls: tuple[str, ...] = ()
    query: str | None = None
    needle: str | None = None
    expect_verbatim: bool | None = None
    expect_contains: tuple[str, ...] = ()
    expect_status: str | None = None
    expect_url_status: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class TaskResult:
    """The harness's verdict for one task."""

    task_id: str
    passed: bool
    calls: int
    tool_errors: int
    duration_s: float
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Aggregate metrics over a corpus run (docs/03, section 2.11)."""

    results: tuple[TaskResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def accuracy_pct(self) -> float:
        return round(self.passed / self.total * 100, 1) if self.total else 0.0

    @property
    def avg_calls(self) -> float:
        return (
            round(sum(result.calls for result in self.results) / self.total, 2)
            if self.total
            else 0.0
        )

    @property
    def tool_errors(self) -> int:
        return sum(result.tool_errors for result in self.results)

    @property
    def input_tokens(self) -> int:
        return sum(result.input_tokens for result in self.results)

    @property
    def output_tokens(self) -> int:
        return sum(result.output_tokens for result in self.results)

    @property
    def total_duration_s(self) -> float:
        return round(sum(result.duration_s for result in self.results), 3)


def load_corpus(path: Path = CORPUS_PATH) -> tuple[TaskSpec, ...]:
    """Load the task corpus from JSON; the file is the single source."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        TaskSpec(
            id=item["id"],
            kind=item["kind"],
            prompt=item["prompt"],
            max_calls=item["max_calls"],
            url=item.get("url"),
            urls=tuple(item.get("urls", ())),
            query=item.get("query"),
            needle=item.get("needle"),
            expect_verbatim=item.get("expect_verbatim"),
            expect_contains=tuple(item.get("expect_contains", ())),
            expect_status=item.get("expect_status"),
            expect_url_status=item.get("expect_url_status"),
        )
        for item in raw["tasks"]
    )
