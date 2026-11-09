#!/usr/bin/env bash
# Runs the full mandatory check suite (AGENTS.md section 3). All must exit 0.
set -euo pipefail

export UV_LINK_MODE=copy

cd "$(dirname "$0")"

uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
