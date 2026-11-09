"""Healthcheck for the Crawl4AI container.

``/health`` does not reflect Chromium's state (docs/spikes/02, 3/14): a
real browser crawl with a wait_for marker is the only trustworthy probe.
On failure the script terminates PID 1 so compose's ``restart:
unless-stopped`` brings the container back - no docker socket needed.

Rewritten from the spike snippet to AGENTS.md requirements (typing, error
handling, no bare except).
"""

import json
import os
import signal
import sys
import urllib.error
import urllib.request

_HEALTHCHECK_MARKER = "healthcheck-ok-marker"
_CRAWL_URL = "http://127.0.0.1:11235/crawl"
_TIMEOUT_S = 15.0


def _crawl_marker_page() -> bool:
    """Crawl a tiny page and verify the browser rendered its marker."""
    body = {
        "urls": [f"raw:<html><body><p>{_HEALTHCHECK_MARKER} healthcheck padding</p></body></html>"],
        "browser_config": {"type": "BrowserConfig", "params": {"text_mode": True}},
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {"cache_mode": "bypass", "wait_for": "css:p"},
        },
    }
    request = urllib.request.Request(
        _CRAWL_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + os.environ.get("CRAWL4AI_API_TOKEN", ""),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        payload: object = json.loads(response.read())
    if not isinstance(payload, dict):
        return False
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return False
    first = results[0]
    if not isinstance(first, dict):
        return False
    html = first.get("html")
    return first.get("success") is True and isinstance(html, str) and _HEALTHCHECK_MARKER in html


def main() -> int:
    """Probe the browser; terminate PID 1 when it is not healthy."""
    try:
        healthy = _crawl_marker_page()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        print(f"healthcheck failed: {error!r}", file=sys.stderr)  # noqa: T201 - compose log
        healthy = False
    if not healthy:
        os.kill(1, signal.SIGTERM)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
