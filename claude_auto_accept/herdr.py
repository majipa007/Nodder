"""Thin client over the `herdr` CLI.

Herdr owns the terminal; this tool only drives its CLI. Control commands answer
with JSON on stdout, `agent read` answers with raw text, and server errors
arrive as JSON on stderr with exit status 1.
"""

from __future__ import annotations

import enum
import json
import subprocess
from dataclasses import dataclass

HERDR = "herdr"

#: States that mean an agent is no longer showing a Prompt.
UNBLOCKED_STATES = ("idle", "working", "done", "unknown")


class Wait(enum.Enum):
    """Outcome of `wait_for`."""

    REACHED = "reached"
    TIMEOUT = "timeout"


class HerdrError(RuntimeError):
    """The herdr CLI failed, or answered with something unreadable."""


@dataclass(frozen=True)
class Agent:
    """A coding agent occupying a pane, as herdr reports it."""

    pane_id: str
    kind: str
    status: str
    workspace_id: str = ""
    title: str = ""


def _run(argv: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise HerdrError(f"herdr not found on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HerdrError(f"herdr timed out: {' '.join(argv)}") from exc


def _run_json(argv: list[str], timeout: float | None = None) -> dict:
    proc = _run(argv, timeout=timeout)
    if proc.returncode != 0:
        raise HerdrError(
            f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise HerdrError(
            f"{' '.join(argv)} returned unreadable output: {proc.stdout[:200]!r}"
        ) from exc


def list_agents() -> list[Agent]:
    """Every agent herdr currently recognises, in any workspace."""
    payload = _run_json([HERDR, "agent", "list"], timeout=10)
    agents = payload.get("result", {}).get("agents", [])
    return [
        Agent(
            pane_id=entry["pane_id"],
            kind=entry.get("agent", "unknown"),
            status=entry.get("agent_status", "unknown"),
            workspace_id=entry.get("workspace_id", ""),
            title=entry.get("terminal_title_stripped", ""),
        )
        for entry in agents
    ]


def read_detection(target: str, lines: int = 40) -> str:
    """Herdr's plain-text detection snapshot for a pane.

    This is the same bottom-buffer text herdr classifies against, already
    rendered and stripped of ANSI, which is why this tool needs no terminal
    emulation of its own.
    """
    argv = [HERDR, "agent", "read", target,
            "--source", "detection", "--lines", str(lines)]
    proc = _run(argv, timeout=10)
    if proc.returncode != 0:
        raise HerdrError(
            f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    return proc.stdout


def _error_code(stderr: str) -> str | None:
    """The `error.code` from a herdr failure, if it answered with JSON."""
    try:
        return json.loads(stderr).get("error", {}).get("code")
    except (json.JSONDecodeError, AttributeError):
        return None


def wait_for(target: str, states: tuple[str, ...], timeout_ms: int) -> Wait:
    """Block until an agent reaches one of `states`.

    A timeout is ordinary -- the agent simply did not get there -- and is
    reported rather than raised. Every other failure raises: herdr answers
    `agent_not_found` for a closed pane in milliseconds, and treating that as
    a timeout would spin the caller at full speed.
    """
    argv = [HERDR, "agent", "wait", target]
    for state in states:
        argv += ["--until", state]
    argv += ["--timeout", str(timeout_ms)]
    # Allow generous slack over herdr's own deadline before giving up on it.
    proc = _run(argv, timeout=timeout_ms / 1000 + 30)
    if proc.returncode == 0:
        return Wait.REACHED
    if _error_code(proc.stderr) == "timeout":
        return Wait.TIMEOUT
    raise HerdrError(
        f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}"
    )


def send_enter(target: str) -> None:
    """Press Enter in a pane, accepting its Selected Option."""
    _run_json([HERDR, "agent", "send-keys", target, "enter"], timeout=10)
