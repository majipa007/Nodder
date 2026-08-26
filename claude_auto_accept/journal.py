"""Append-only record of every Acceptance and Skip.

The daemon presses buttons in panes nobody is looking at, and it accepts every
Affirmative Option including consequential ones, so this file is the only way
to answer "what did it approve, and when?".
"""

from __future__ import annotations

import datetime
import pathlib

from .prompts import Decision

DEFAULT_PATH = (
    pathlib.Path.home() / ".local" / "state" / "claude_auto_accept" / "log"
)

#: Day/month/year, matching local convention.
TIMESTAMP = "%d/%m/%Y %H:%M:%S"


class Journal:
    """Writes one line per decision, appending."""

    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = pathlib.Path(path) if path else DEFAULT_PATH

    def record(
        self,
        target: str,
        decision: Decision,
        at: datetime.datetime | None = None,
    ) -> str:
        """Append one decision and return the line written."""
        when = (at or datetime.datetime.now()).strftime(TIMESTAMP)
        label = "-" if decision.label is None else f'"{_flatten(decision.label)}"'
        line = f"{when}  {target}  {decision.action.name:<6}  {label}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return line


def _flatten(label: str) -> str:
    """Keep a record to a single line whatever the terminal rendered."""
    return " ".join(label.split())
