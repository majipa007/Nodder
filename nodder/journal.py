"""Append-only record of every Acceptance and Pause, kept in SQLite.

The daemon presses buttons in panes nobody is looking at, and it accepts every
Affirmative Option including consequential ones, so this database is the only
way to answer "what did it approve, and when?".

Every Option the Prompt offered is stored alongside the one that was chosen, so
a Pause can be read back as "here is what it is waiting on", not just as an
absence. Rows imported from the plain-text log that preceded this database
carry only the four fields that log held; their `source` is `legacy-log`.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import pathlib
import re
import sqlite3
import threading
from dataclasses import dataclass

from .prompts import Decision, Option

STATE_DIR = pathlib.Path.home() / ".local" / "state" / "nodder"
DEFAULT_PATH = STATE_DIR / "decisions.db"

#: The plain-text log this database replaced. Imported once, then left alone.
LEGACY_LOG_PATH = STATE_DIR / "log"

#: Day/month/year, matching local convention. Display only.
TIMESTAMP = "%d/%m/%Y %H:%M:%S"

#: How timestamps sit in the database: lexical order is chronological order,
#: which is what makes plain `ORDER BY at` and `at >= ?` correct.
STORED = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    target    TEXT NOT NULL,
    outcome   TEXT NOT NULL,
    label     TEXT,
    reason    TEXT,
    signature TEXT,
    options   TEXT,
    source    TEXT NOT NULL DEFAULT 'daemon'
);
CREATE INDEX IF NOT EXISTS decisions_at ON decisions(at);
CREATE INDEX IF NOT EXISTS decisions_target ON decisions(target);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_WRITTEN = "at, target, outcome, label, reason, signature, options, source"
_COLUMNS = f"id, {_WRITTEN}"
_INSERT = f"INSERT INTO decisions ({_WRITTEN}) VALUES (?,?,?,?,?,?,?,?)"

# One line of the plain-text log: timestamp, target, outcome, quoted label
# (or "-" where the Prompt had no Option list).
_LEGACY_LINE = re.compile(
    r"^(?P<at>\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<target>\S+)\s+"
    # SKIP is the outcome name the plain-text log used before Pause and
    # Ignore were told apart. Historical rows keep it.
    r"(?P<outcome>ACCEPT|SKIP|PAUSE)\s+"
    r"(?P<label>.*)$"
)


@dataclass(frozen=True)
class Record:
    """One recorded decision, read back out."""

    id: int
    at: datetime.datetime
    target: str
    outcome: str
    label: str | None
    reason: str | None
    signature: str | None
    options: list[Option]
    source: str

    def format(self) -> str:
        """The one-line form, unchanged from the log this database replaced."""
        return format_line(self.at, self.target, self.outcome, self.label)


def format_line(
    at: datetime.datetime, target: str, outcome: str, label: str | None
) -> str:
    """Render one decision the way the plain-text log always rendered it."""
    quoted = "-" if label is None else f'"{_flatten(label)}"'
    return f"{at.strftime(TIMESTAMP)}  {target}  {outcome:<6}  {quoted}"


class Journal:
    """Records decisions, and reads them back."""

    def __init__(
        self,
        path: pathlib.Path | None = None,
        legacy_log: pathlib.Path | None = LEGACY_LOG_PATH,
    ) -> None:
        self.path = pathlib.Path(path) if path else DEFAULT_PATH
        # Explicit, so that a database opened somewhere else -- a test, or
        # `--log /tmp/scratch.db` -- does not silently absorb the real log.
        self.legacy_log = legacy_log
        self._lock = threading.Lock()
        self._ready = False

    def record(
        self,
        target: str,
        decision: Decision,
        at: datetime.datetime | None = None,
        signature: str | None = None,
        options: list[Option] | None = None,
    ) -> str:
        """Store one decision and return the line describing it."""
        when = at or datetime.datetime.now()
        label = None if decision.label is None else _flatten(decision.label)
        with self._write() as conn:
            conn.execute(
                _INSERT,
                (
                    when.strftime(STORED),
                    target,
                    decision.action.name,
                    label,
                    decision.reason,
                    signature,
                    _dump_options(options),
                    "daemon",
                ),
            )
        return format_line(when, target, decision.action.name, label)

    def query(
        self,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        target: str | None = None,
        outcome: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> list[Record]:
        """Recorded decisions, oldest first.

        `limit` keeps the *most recent* matches, the way `tail` does, and the
        result is still returned in chronological order. `after_id` is what
        `--follow` uses to ask only for rows it has not printed yet.
        """
        where, params = [], []
        if since is not None:
            where.append("at >= ?")
            params.append(since.strftime(STORED))
        if until is not None:
            where.append("at <= ?")
            params.append(until.strftime(STORED))
        if target is not None:
            where.append("target = ?")
            params.append(target)
        if outcome is not None:
            where.append("outcome = ?")
            params.append(outcome.upper())
        if after_id is not None:
            where.append("id > ?")
            params.append(after_id)

        sql = f"SELECT {_COLUMNS} FROM decisions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        # Newest-first with a LIMIT is how "the last N" is expressed in SQL;
        # the rows are flipped back into reading order below.
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_record(row) for row in reversed(rows)]

    def tally(
        self,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
    ) -> dict[str, dict[str, int]]:
        """How many of each outcome per target: {"w16:p1": {"ACCEPT": 12}}.

        Bounded by `since`/`until` when given, so a caller can ask about a
        window rather than all of history. Counted in SQL rather than by
        reading rows back, so it stays cheap however long the record grows.
        """
        where, params = [], []
        if since is not None:
            where.append("at >= ?")
            params.append(since.strftime(STORED))
        if until is not None:
            where.append("at <= ?")
            params.append(until.strftime(STORED))

        sql = "SELECT target, outcome, COUNT(*) AS n FROM decisions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY target, outcome"

        counts: dict[str, dict[str, int]] = {}
        with self._read() as conn:
            for row in conn.execute(sql, params).fetchall():
                counts.setdefault(row["target"], {})[row["outcome"]] = row["n"]
        return counts

    def activity(
        self,
        since: datetime.datetime,
        until: datetime.datetime,
        buckets: int,
        target: str | None = None,
        outcome: str | None = "ACCEPT",
    ) -> list[int]:
        """Decisions per equal time bucket across [since, until].

        Feeds the dashboard charts. Bucketing happens here rather than in SQL
        because the boundaries have to line up exactly with the chart's
        columns, and sqlite's date arithmetic makes that awkward to express.
        """
        if buckets <= 0:
            return []
        span = (until - since).total_seconds()
        if span <= 0:
            return [0] * buckets

        counts = [0] * buckets
        for record in self.query(since=since, until=until, outcome=outcome,
                                 target=target):
            offset = (record.at - since).total_seconds()
            index = int(offset / span * buckets)
            counts[min(max(index, 0), buckets - 1)] += 1
        return counts

    def last_id(self) -> int:
        """The highest row id, or 0 when nothing has been recorded yet."""
        with self._read() as conn:
            row = conn.execute("SELECT MAX(id) FROM decisions").fetchone()
        return row[0] or 0

    @contextlib.contextmanager
    def _read(self):
        with self._lock, contextlib.closing(self._connect()) as conn:
            yield conn

    @contextlib.contextmanager
    def _write(self):
        with self._lock, contextlib.closing(self._connect()) as conn, conn:
            yield conn

    def _connect(self) -> sqlite3.Connection:
        """A fresh connection, schema assured.

        One connection per operation, rather than one held open: watchers write
        from a thread each, and sqlite3 connections are not shared across
        threads. Decisions arrive at human speed, so the cost is irrelevant.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            conn.executescript(SCHEMA)
            if self.legacy_log is not None:
                import_legacy_log(conn, self.legacy_log)
            self._ready = True
        return conn


def import_legacy_log(conn: sqlite3.Connection, path: pathlib.Path) -> int:
    """Copy the plain-text log into the database, once, and say how many rows.

    The file is read, never written or removed — it stays on disk as the
    artefact it is. The marker is written even when there is no file to import,
    so a log restored from a backup years later cannot reappear as new history.
    """
    if conn.execute("SELECT 1 FROM meta WHERE key = 'legacy_import'").fetchone():
        return 0

    rows = []
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            match = _LEGACY_LINE.match(line.strip())
            if not match:
                continue
            raw = match["label"].strip()
            label = None if raw == "-" else raw.strip('"')
            at = datetime.datetime.strptime(match["at"], TIMESTAMP)
            rows.append(
                (
                    at.strftime(STORED),
                    match["target"],
                    match["outcome"],
                    label,
                    None,
                    None,
                    None,
                    "legacy-log",
                )
            )

    conn.executemany(_INSERT, rows)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('legacy_import', ?)",
        (datetime.datetime.now().strftime(STORED),),
    )
    conn.commit()
    return len(rows)


def _record(row: sqlite3.Row) -> Record:
    return Record(
        id=row["id"],
        at=datetime.datetime.strptime(row["at"], STORED),
        target=row["target"],
        outcome=row["outcome"],
        label=row["label"],
        reason=row["reason"],
        signature=row["signature"],
        options=_load_options(row["options"]),
        source=row["source"],
    )


def _dump_options(options: list[Option] | None) -> str | None:
    if not options:
        return None
    return json.dumps(
        [
            {
                "number": option.number,
                "label": _flatten(option.label),
                "selected": option.selected,
            }
            for option in options
        ]
    )


def _load_options(payload: str | None) -> list[Option]:
    if not payload:
        return []
    return [
        Option(
            label=entry["label"],
            number=entry.get("number"),
            selected=entry.get("selected", False),
        )
        for entry in json.loads(payload)
    ]


def _flatten(label: str) -> str:
    """Keep a record to a single line whatever the terminal rendered."""
    return " ".join(label.split())
