"""The storage boundary reads back every Literal member the domain can write.

These tests pin the typed tables in ``storage/rows.py`` against the domain
Literals: a kind added to a Literal but missed in its table writes fine and
then breaks every read of that row or event (regression for ``empty_render``,
which made cached records and journal events unreadable).
"""

from typing import get_args

import pytest

from web_extractor.domain.types import ErrorKind, FetcherName, PageFormat, Status, ToolName
from web_extractor.storage.rows import (
    as_fetcher,
    as_opt_error_kind,
    as_opt_page_format,
    as_status,
    as_tool,
)


@pytest.mark.parametrize("kind", get_args(ErrorKind.__value__))
def test_every_error_kind_reads_back(kind: ErrorKind) -> None:
    assert as_opt_error_kind(kind, "error_kind") == kind


@pytest.mark.parametrize("status", get_args(Status.__value__))
def test_every_status_reads_back(status: Status) -> None:
    assert as_status(status, "status") == status


@pytest.mark.parametrize("fetcher", get_args(FetcherName.__value__))
def test_every_fetcher_reads_back(fetcher: FetcherName) -> None:
    assert as_fetcher(fetcher, "fetcher") == fetcher


@pytest.mark.parametrize("tool", get_args(ToolName.__value__))
def test_every_tool_reads_back(tool: ToolName) -> None:
    assert as_tool(tool, "tool") == tool


@pytest.mark.parametrize("page_format", get_args(PageFormat.__value__))
def test_every_page_format_reads_back(page_format: PageFormat) -> None:
    assert as_opt_page_format(page_format, "text_format") == page_format
