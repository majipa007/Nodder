"""Thin client over the `herdr` CLI.

Herdr owns the terminal; this tool only drives its CLI. Control commands answer
with JSON on stdout, `agent read` answers with raw text, and server errors
arrive as JSON on stderr with exit status 1.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

HERDR = "herdr"


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
    focused: bool = False

    @property
    def pane(self) -> str:
        """Just the pane part of the id: "w16:p1" -> "p1"."""
        _, _, pane = self.pane_id.partition(":")
        return pane or self.pane_id

    def where(self, workspaces: dict[str, str] | None = None) -> str:
        """Human-readable location, e.g. "sluice/p2".

        Falls back to the opaque workspace id when no label is known, so this
        never hides which pane is meant.
        """
        label = (workspaces or {}).get(self.workspace_id) or self.workspace_id
        return f"{label}/{self.pane}"


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
            focused=bool(entry.get("focused", False)),
        )
        for entry in agents
    ]


def list_workspaces() -> dict[str, str]:
    """Workspace id -> its label, e.g. {"w16": "autoAccept"}.

    Used only for display: "sluice/p2" says far more than "w17:p2".
    """
    payload = _run_json([HERDR, "workspace", "list"], timeout=10)
    return {
        entry["workspace_id"]: entry.get("label") or entry["workspace_id"]
        for entry in payload.get("result", {}).get("workspaces", [])
    }


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


def send_enter(target: str) -> None:
    """Press Enter in a pane, accepting its Selected Option."""
    _run_json([HERDR, "agent", "send-keys", target, "enter"], timeout=10)
