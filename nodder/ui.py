"""A live dashboard for a running nodder.

Answers, at a glance: which panes am I watching, how much has each of them had
accepted, and is anything waiting on me right now.

Panes are named by workspace label and pane number -- "sluice/p2" -- because
"w17:p2" tells a human nothing. Rendering is curses from the standard library,
so the dependency-free install still holds.

The screen is drawn from a snapshot refreshed on a background thread. herdr
calls are subprocesses and would otherwise stall the draw loop.
"""

from __future__ import annotations

import curses
import datetime
import threading
from dataclasses import dataclass, field

from . import herdr, viz
from .herdr import HerdrError
from .journal import Journal
from .prompts import Action, classify

#: How many recent decisions to keep on screen.
RECENT = 10

#: Seconds between refreshes of the agent table.
REFRESH = 1.5

_STATE_ORDER = {"blocked": 0, "working": 1, "idle": 2, "done": 3}


#: Minutes of history the header chart covers.
WINDOW_MINUTES = 60

#: Buckets behind each pane's sparkline. Kept small: it is a table cell.
TREND_BUCKETS = 24

#: Buckets behind the header chart: one per minute of the window. Finer
#: buckets than this render sparse real activity as isolated single-dot
#: needles; a minute's worth clumps into something with a readable shape,
#: and stretches to a few columns each on a wide terminal.
HISTORY_BUCKETS = WINDOW_MINUTES


@dataclass
class Row:
    where: str
    kind: str
    status: str
    accepted: int
    paused: int
    note: str = ""
    focused: bool = False
    trend: list[int] = field(default_factory=list)


@dataclass
class Snapshot:
    rows: list[Row] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)
    waiting: int = 0
    error: str = ""
    dry_run: bool = False
    #: Session-wide acceptances per bucket over the last WINDOW_MINUTES.
    history: list[int] = field(default_factory=list)


class Dashboard:
    """Collects the state the screen shows, off the drawing thread."""

    def __init__(self, journal: Journal, self_pane: str | None = None,
                 dry_run: bool = False) -> None:
        self.journal = journal
        self.self_pane = self_pane
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self._snapshot = Snapshot(dry_run=dry_run)
        self.stop = threading.Event()

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def refresh(self) -> None:
        """Rebuild the snapshot. Never raises: the screen must keep drawing."""
        try:
            agents = herdr.list_agents()
            workspaces = herdr.list_workspaces()
            error = ""
        except HerdrError as exc:
            agents, workspaces, error = [], {}, str(exc)

        tally = self.journal.tally()
        until = datetime.datetime.now()
        since = until - datetime.timedelta(minutes=WINDOW_MINUTES)
        history = self.journal.activity(since, until, HISTORY_BUCKETS)

        rows, waiting = [], 0
        for agent in agents:
            if agent.pane_id == self.self_pane:
                continue
            counts = tally.get(agent.pane_id, {})
            paused = counts.get("PAUSE", 0) + counts.get("SKIP", 0)
            note = ""
            if agent.status == "blocked":
                # Blocked alone does not mean the human is needed: most of
                # these are about to be accepted. Ask what is on screen.
                try:
                    decision = classify(herdr.read_detection(agent.pane_id))
                except HerdrError:
                    decision = None
                if decision is not None and decision.action is Action.PAUSE:
                    note = "NEEDS YOU"
                    waiting += 1
            rows.append(Row(
                where=agent.where(workspaces),
                kind=agent.kind,
                status=agent.status,
                accepted=counts.get("ACCEPT", 0),
                paused=paused,
                note=note,
                focused=agent.focused,
                trend=self.journal.activity(
                    since, until, TREND_BUCKETS, target=agent.pane_id
                ),
            ))

        rows.sort(key=lambda r: (_STATE_ORDER.get(r.status, 9), r.where))

        names = {a.pane_id: a.where(workspaces) for a in agents}
        recent = [
            f"{record.at.strftime('%H:%M:%S')}  "
            f"{names.get(record.target, record.target):<18} "
            f"{record.outcome:<6} {record.label or ''}"
            for record in self.journal.query(limit=RECENT)
        ]

        with self._lock:
            self._snapshot = Snapshot(
                rows=rows, recent=list(reversed(recent)),
                waiting=waiting, error=error, dry_run=self.dry_run,
                history=history,
            )

    def run(self) -> None:
        """Refresh until stopped."""
        while not self.stop.is_set():
            self.refresh()
            self.stop.wait(REFRESH)


# --- drawing ---------------------------------------------------------------

def _colours() -> dict[str, int]:
    if not curses.has_colors():
        return {name: curses.A_NORMAL for name in
                ("dim", "head", "ok", "warn", "accent")}
    curses.start_color()
    curses.use_default_colors()
    for index, colour in enumerate(
        (curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW,
         curses.COLOR_MAGENTA), start=1
    ):
        curses.init_pair(index, colour, -1)
    return {
        "dim": curses.A_DIM,
        "head": curses.color_pair(1) | curses.A_BOLD,
        "ok": curses.color_pair(2),
        "warn": curses.color_pair(3) | curses.A_BOLD,
        "accent": curses.color_pair(4),
    }


def _put(win, y: int, x: int, text: str, attr: int = curses.A_NORMAL) -> None:
    """Write text, clipped to the window. curses errors on the last cell."""
    height, width = win.getmaxyx()
    if y >= height or x >= width:
        return
    try:
        win.addnstr(y, x, text, max(0, width - x - 1), attr)
    except curses.error:
        pass


#: Bounds on the header chart's height in character cells. It grows into
#: whatever vertical space the table and the recent panel do not need, so a
#: tall terminal gets a bigger chart rather than a band of dead space.
CHART_MIN_HEIGHT = 3
CHART_MAX_HEIGHT = 10

#: Below this width the chart is dropped rather than drawn as a smear.
CHART_MIN_WIDTH = 24

#: Column layout for the agent table.
_TREND_AT = 58
_TREND_WIDTH = 18


def draw(win, snap: Snapshot, colour: dict[str, int]) -> None:
    win.erase()
    height, width = win.getmaxyx()

    title = "⚡ nodder" + ("  (dry run)" if snap.dry_run else "")
    _put(win, 0, 0, title, colour["head"])
    summary = (f"{len(snap.rows)} agents  ·  "
               f"{sum(r.accepted for r in snap.rows)} yes  ·  "
               f"{snap.waiting} need you")
    _put(win, 0, max(len(title) + 2, width - len(summary) - 1), summary,
         colour["dim"])

    row = 1
    if snap.error:
        _put(win, row, 0, f"herdr: {snap.error}", colour["warn"])
        row += 1

    row = _chart(win, snap, colour, row, width, height)

    _put(win, row, 0,
         f"{'WHERE':<22}{'STATE':<10}{'YES':>6}{'PAUSED':>8}", colour["dim"])
    if width > _TREND_AT + 8:
        _put(win, row, _TREND_AT, f"LAST {WINDOW_MINUTES}m", colour["dim"])
    row += 1

    # Leave room for the recent panel, its heading, and the key line.
    table_limit = max(1, height - RECENT - row - 3)
    peak = max((max(r.trend, default=0) for r in snap.rows), default=0)
    for entry in snap.rows[:table_limit]:
        mark = "▸" if entry.focused else " "
        state_attr = (colour["warn"] if entry.status == "blocked"
                      else colour["ok"] if entry.status == "working"
                      else colour["dim"])
        _put(win, row, 0, f"{mark} {entry.where:<20}", colour["accent"])
        _put(win, row, 22, f"{entry.status:<10}", state_attr)
        _put(win, row, 32, f"{entry.accepted:>6}", colour["ok"])
        _put(win, row, 38, f"{entry.paused:>8}",
             colour["warn"] if entry.paused else colour["dim"])
        if width > _TREND_AT + _TREND_WIDTH:
            _put(win, row, _TREND_AT,
                 viz.sparkline(entry.trend, _TREND_WIDTH, peak=peak or None),
                 colour["ok"])
        if entry.note:
            note_at = (_TREND_AT + _TREND_WIDTH + 1
                       if width > _TREND_AT + _TREND_WIDTH else 47)
            _put(win, row, note_at, entry.note, colour["warn"])
        row += 1

    row += 1
    _put(win, row, 0, f"RECENT (last {RECENT})", colour["dim"])
    row += 1
    for line in snap.recent[-max(0, height - row - 2):]:
        attr = colour["warn"] if " PAUSE " in line else curses.A_NORMAL
        _put(win, row, 2, line, attr)
        row += 1

    _put(win, height - 1, 0, " q quit    r refresh ", colour["dim"])
    win.noutrefresh()
    curses.doupdate()


def _chart(win, snap: Snapshot, colour: dict[str, int],
           row: int, width: int, height: int) -> int:
    """Draw the header chart, and return the next free row.

    Dropped entirely when the terminal is too small to carry it: a squashed
    chart is worse than none, and the table below it is what matters.
    """
    chart_width = width - 2
    # Everything the chart must not eat into: table rows, the recent panel,
    # their headings, the axis and the key line.
    reserved = row + len(snap.rows) + RECENT + 7
    chart_height = min(CHART_MAX_HEIGHT, height - reserved)
    if chart_width < CHART_MIN_WIDTH or chart_height < CHART_MIN_HEIGHT:
        return row + 1
    if not any(snap.history):
        _put(win, row + 1, 2, "no activity yet", colour["dim"])
        return row + 3

    peak = max(snap.history)
    _put(win, row, 0, f"ACCEPTED  last {WINDOW_MINUTES} min", colour["dim"])
    _put(win, row, max(20, width - 12), f"peak {peak}", colour["dim"])
    row += 1

    for line in viz.area(snap.history, chart_width, chart_height):
        _put(win, row, 1, line, colour["ok"])
        row += 1

    axis = f"└{'─' * (chart_width - 2)}┘"
    _put(win, row, 1, axis, colour["dim"])
    _put(win, row, 2, f" {WINDOW_MINUTES}m ago ", colour["dim"])
    _put(win, row, max(3, chart_width - 4), " now", colour["dim"])
    return row + 2


def run(journal: Journal, self_pane: str | None = None,
        dry_run: bool = False, stop: threading.Event | None = None) -> None:
    """Show the dashboard until the user quits or `stop` is set."""
    board = Dashboard(journal, self_pane=self_pane, dry_run=dry_run)
    board.refresh()
    collector = threading.Thread(target=board.run, name="dashboard", daemon=True)
    collector.start()

    def _loop(win):
        curses.curs_set(0)
        win.nodelay(True)
        colour = _colours()
        while not (stop and stop.is_set()):
            draw(win, board.snapshot, colour)
            key = win.getch()
            if key in (ord("q"), ord("Q")):
                break
            if key in (ord("r"), ord("R")):
                board.refresh()
            curses.napms(120)

    try:
        curses.wrapper(_loop)
    finally:
        board.stop.set()
