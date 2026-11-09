#!/usr/bin/env bash
# Rebuilds and restarts the whole stack (core + browser) from the current
# working tree. Cache and journal survive: the core-data volume is kept
# (pass --wipe-data to drop it as well).
#
# Usage: ./reinstall.sh [--wipe-data]
set -euo pipefail

cd "$(dirname "$0")"

readonly READY_URL="http://127.0.0.1:8321/readyz"
readonly READY_TIMEOUT_S=90

wipe_data=false
for arg in "$@"; do
    case "$arg" in
        --wipe-data) wipe_data=true ;;
        -h | --help)
            sed -n '2,6p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $arg (see --help)" >&2
            exit 2
            ;;
    esac
done

# The shared secret between the two containers; generated once, never printed.
if [[ ! -f .env ]] || ! grep -q '^CRAWL4AI_API_TOKEN=.\+' .env; then
    echo "==> .env has no CRAWL4AI_API_TOKEN; generating a random one"
    if command -v python >/dev/null 2>&1; then
        token="$(python -c 'import secrets; print(secrets.token_hex(24))')"
    else
        token="$(openssl rand -hex 24)"
    fi
    grep -v '^CRAWL4AI_API_TOKEN=' .env 2>/dev/null > .env.tmp || true
    echo "CRAWL4AI_API_TOKEN=${token}" >> .env.tmp
    mv .env.tmp .env
    unset token
fi

echo "==> docker compose down"
if [[ "$wipe_data" == true ]]; then
    docker compose down --remove-orphans --volumes
else
    docker compose down --remove-orphans
fi

echo "==> docker compose build (core image)"
docker compose build --pull

echo "==> docker compose up"
docker compose up -d --remove-orphans

echo "==> waiting for the core to become ready (${READY_TIMEOUT_S}s max)"
deadline=$((SECONDS + READY_TIMEOUT_S))
until curl -fsS "$READY_URL" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
        echo "core did not become ready; recent logs:" >&2
        docker compose logs --tail 50 core >&2
        exit 1
    fi
    sleep 2
done

echo "==> ready: $(curl -fsS "$READY_URL")"
docker compose ps
echo
echo "MCP endpoint: http://127.0.0.1:8321/mcp   Web UI: http://127.0.0.1:8321/"
echo "The browser container reports healthy after its first real crawl (up to ~60s)."
