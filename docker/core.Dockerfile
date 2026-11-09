# Multi-stage build: dependencies and project installed with uv, runtime
# runs as a non-root user with no build tooling (01_requirements.md, 5).
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.8 /uv /uvx /usr/local/bin/

WORKDIR /build
COPY pyproject.toml uv.lock ./
# Dependencies first so this layer caches across source-only changes.
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY LICENSE README.md ./
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim

RUN useradd --create-home --uid 1000 appuser && mkdir -p /data && chown appuser /data

WORKDIR /app
COPY --from=builder /build/.venv ./.venv

ENV PATH="/app/.venv/bin:$PATH"
ENV WX_DB_PATH="/data/web_extractor.db"
ENV WX_HOST="0.0.0.0"
# Inside the container the server binds all interfaces so the compose port
# mapping can reach it; compose publishes the port on 127.0.0.1 only.

USER appuser
EXPOSE 8321

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8321/healthz', timeout=3)"]

ENTRYPOINT ["python", "-m", "web_extractor"]
