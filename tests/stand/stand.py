"""Docker stand management for isolation tests.

The stand (docker/stand.yaml) is brought up and down via the docker CLI;
tests skip only when Docker itself is unavailable (the stand is a
Docker-only artifact; its absence is an environment fact, documented in
AGENTS.md section 11 discussion and docs/04_plan.md M11).
"""

import subprocess
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_STAND_FILE = _REPO_ROOT / "docker" / "stand.yaml"
_PROJECT = "web_extractor_stand"
_STAND_UP_TIMEOUT_S = 90


class DockerError(RuntimeError):
    """A docker CLI command failed; includes the command and output."""

    def __init__(self, command: str, output: str) -> None:
        super().__init__(f"docker command failed: {command}\n{output}")


def docker_available() -> bool:
    """True when the docker CLI answers; the stand tests need it."""
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() != ""


def _run(command: list[str], timeout: int = 120) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise DockerError(" ".join(command), result.stdout + result.stderr)
    return result.stdout


def stand_up() -> None:
    """Start the stand and wait for both nginx healthchecks."""
    _run(["docker", "compose", "-f", str(_STAND_FILE), "-p", _PROJECT, "up", "-d"])
    deadline = time.monotonic() + _STAND_UP_TIMEOUT_S
    while time.monotonic() < deadline:
        health = _run(
            [
                "docker",
                "compose",
                "-f",
                str(_STAND_FILE),
                "-p",
                _PROJECT,
                "ps",
                "--format",
                "{{.Health}}",
            ]
        )
        if health.count("healthy") == 2:
            return
        time.sleep(1)
    msg = "stand did not become healthy in time"
    raise TimeoutError(msg)


def stand_down() -> None:
    """Stop the stand and remove networks/volumes."""
    _run(["docker", "compose", "-f", str(_STAND_FILE), "-p", _PROJECT, "down", "-v"])


def curl_from_pub_net(arguments: str) -> tuple[int, str]:
    """Run curl on the public-internet network; returns (exit code, output).

    Connection failures surface as a non-zero exit code - the expected
    outcome when the private target must be unreachable from the public
    network. Uses the curl image pinned by tag.
    """
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "web_extractor_stand_stand_pub_net",
            "curlimages/curl:8.17.0",
            "--silent",
            "--show-error",
            "--max-time",
            "10",
            *arguments.split(),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


_HEALTHCHECK_PROBE = "127.0.0.1"
"""The container's own healthcheck (wget to loopback); not a breach."""


def internal_target_hits() -> list[str]:
    """Access-log lines that came from outside the container.

    Any line is a breach of isolation. The container's own healthcheck
    probes loopback and is excluded by source address.
    """
    probe = _run(  # missing file is fine: no request has arrived yet
        [
            "docker",
            "compose",
            "-f",
            str(_STAND_FILE),
            "-p",
            _PROJECT,
            "exec",
            "-T",
            "internal-target",
            "sh",
            "-c",
            "test -f /tmp/stand-access.log && cat /tmp/stand-access.log || true",
        ],
        timeout=30,
    )
    return [
        line
        for line in probe.splitlines()
        if line.strip() and not line.startswith(_HEALTHCHECK_PROBE)
    ]
