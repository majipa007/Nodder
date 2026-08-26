"""Tests for the decision record.

Because every Affirmative Option is accepted (spec section 6), this database is
the only record of what the daemon pressed, so its contents and its one-line
rendering are both pinned.
"""

import datetime
import pathlib
import sqlite3
import tempfile
import unittest

from claude_auto_accept.journal import Journal, format_line, import_legacy_log
from claude_auto_accept.prompts import Action, Decision, Option

AT = datetime.datetime(2026, 8, 26, 14, 32, 7)
LATER = datetime.datetime(2026, 8, 26, 18, 5, 0)

MENU = [
    Option(label="Yes", number=1, selected=True),
    Option(label="No", number=2),
]


class JournalTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        # legacy_log=None: a test database must never absorb the real log.
        self.journal = Journal(self.dir / "nested" / "decisions.db", legacy_log=None)


class RecordTest(JournalTestCase):
    def test_creates_the_database_and_its_parent_directory(self):
        self.assertFalse(self.journal.path.exists())
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "why"), at=AT)
        self.assertTrue(self.journal.path.exists())

    def test_stores_target_outcome_label_and_reason(self):
        self.journal.record(
            "w16:p1",
            Decision(Action.ACCEPT, "Yes, I trust this folder", "affirmative"),
            at=AT,
        )
        (row,) = self.journal.query()
        self.assertEqual(row.target, "w16:p1")
        self.assertEqual(row.outcome, "ACCEPT")
        self.assertEqual(row.label, "Yes, I trust this folder")
        self.assertEqual(row.reason, "affirmative")
        self.assertEqual(row.source, "daemon")

    def test_stores_the_whole_menu_not_just_the_chosen_option(self):
        # A Skip is only readable after the fact if you can see what it
        # declined to press.
        self.journal.record(
            "w16:p1", Decision(Action.SKIP, "No", "not affirmative"),
            at=AT, options=MENU,
        )
        (row,) = self.journal.query()
        self.assertEqual([o.label for o in row.options], ["Yes", "No"])
        self.assertEqual([o.number for o in row.options], [1, 2])
        self.assertTrue(row.options[0].selected)

    def test_stores_the_signature(self):
        self.journal.record(
            "w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=AT, signature="abc123"
        )
        self.assertEqual(self.journal.query()[0].signature, "abc123")

    def test_a_decision_with_no_option_still_records(self):
        self.journal.record("w16:p1", Decision(Action.SKIP, None, "no menu"), at=AT)
        (row,) = self.journal.query()
        self.assertEqual(row.outcome, "SKIP")
        self.assertIsNone(row.label)
        self.assertEqual(row.options, [])

    def test_appends_rather_than_replacing(self):
        for _ in range(3):
            self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=AT)
        self.assertEqual(len(self.journal.query()), 3)

    def test_newlines_in_a_label_are_flattened(self):
        line = self.journal.record(
            "w16:p1", Decision(Action.ACCEPT, "Yes\nInjected", "w"), at=AT
        )
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(self.journal.query()[0].label, "Yes Injected")

    def test_survives_being_reopened(self):
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=AT)
        reopened = Journal(self.journal.path, legacy_log=None)
        self.assertEqual(len(reopened.query()), 1)


class RenderingTest(JournalTestCase):
    def test_uses_day_month_year_ordering(self):
        line = self.journal.record(
            "w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=AT
        )
        self.assertIn("26/08/2026 14:32:07", line)

    def test_the_stored_form_sorts_chronologically(self):
        # Lexical order must equal chronological order, or `ORDER BY at` and
        # the `--since` filter are both wrong.
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "b", "w"), at=LATER)
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "a", "w"), at=AT)
        with sqlite3.connect(self.journal.path) as conn:
            stored = [r[0] for r in conn.execute("SELECT at FROM decisions")]
        self.assertEqual(sorted(stored), sorted(stored, key=str))

    def test_a_record_renders_the_same_line_it_was_written_with(self):
        line = self.journal.record(
            "w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=AT
        )
        self.assertEqual(self.journal.query()[0].format(), line)

    def test_a_missing_label_renders_as_a_dash(self):
        self.assertIn("-", format_line(AT, "w16:p1", "SKIP", None))


class QueryTest(JournalTestCase):
    def populate(self):
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=AT)
        self.journal.record("w17:p2", Decision(Action.SKIP, "No", "w"), at=LATER)
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=LATER)

    def test_returns_oldest_first(self):
        self.populate()
        self.assertEqual([r.at for r in self.journal.query()], [AT, LATER, LATER])

    def test_filters_by_target(self):
        self.populate()
        rows = self.journal.query(target="w16:p1")
        self.assertEqual(len(rows), 2)

    def test_filters_by_outcome_case_insensitively(self):
        self.populate()
        self.assertEqual(len(self.journal.query(outcome="skip")), 1)

    def test_filters_by_time_window(self):
        self.populate()
        self.assertEqual(len(self.journal.query(since=LATER)), 2)
        self.assertEqual(len(self.journal.query(until=AT)), 1)

    def test_limit_keeps_the_most_recent_but_still_reads_oldest_first(self):
        self.populate()
        rows = self.journal.query(limit=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.target for r in rows], ["w17:p2", "w16:p1"])

    def test_after_id_returns_only_newer_rows(self):
        self.populate()
        first = self.journal.query()[0]
        self.assertEqual(len(self.journal.query(after_id=first.id)), 2)

    def test_last_id_is_zero_when_empty(self):
        self.assertEqual(self.journal.last_id(), 0)

    def test_last_id_tracks_the_newest_row(self):
        self.populate()
        self.assertEqual(self.journal.last_id(), self.journal.query()[-1].id)


class LegacyImportTest(JournalTestCase):
    LOG = (
        '26/08/2026 19:00:57  w16:p5  ACCEPT  "Yes — new, ours"\n'
        '26/08/2026 19:03:00  w16:p5  SKIP    "No — show me first"\n'
        '26/08/2026 19:09:09  w16:p5  SKIP    -\n'
    )

    def write_log(self, text=None):
        path = self.dir / "log"
        path.write_text(self.LOG if text is None else text, encoding="utf-8")
        return path

    def test_imports_each_line(self):
        journal = Journal(self.dir / "d.db", legacy_log=self.write_log())
        rows = journal.query()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].label, "Yes — new, ours")
        self.assertEqual(rows[0].source, "legacy-log")

    def test_a_dash_label_becomes_null(self):
        journal = Journal(self.dir / "d.db", legacy_log=self.write_log())
        self.assertIsNone(journal.query()[2].label)

    def test_imports_only_once_even_across_reopens(self):
        path = self.write_log()
        Journal(self.dir / "d.db", legacy_log=path).query()
        reopened = Journal(self.dir / "d.db", legacy_log=path)
        self.assertEqual(len(reopened.query()), 3)

    def test_a_log_restored_later_cannot_reappear_as_new_history(self):
        journal = Journal(self.dir / "d.db", legacy_log=self.dir / "absent")
        journal.query()
        restored = Journal(self.dir / "d.db", legacy_log=self.write_log())
        self.assertEqual(restored.query(), [])

    def test_unparseable_lines_are_ignored(self):
        path = self.write_log("not a log line at all\n" + self.LOG)
        self.assertEqual(len(Journal(self.dir / "d.db", legacy_log=path).query()), 3)

    def test_the_log_file_is_left_on_disk(self):
        path = self.write_log()
        Journal(self.dir / "d.db", legacy_log=path).query()
        self.assertTrue(path.exists())

    def test_a_journal_with_no_legacy_log_imports_nothing(self):
        self.write_log()
        self.assertEqual(Journal(self.dir / "d.db", legacy_log=None).query(), [])

    def test_import_reports_how_many_rows_it_took(self):
        with sqlite3.connect(self.dir / "raw.db") as conn:
            conn.executescript(
                "CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " at TEXT, target TEXT, outcome TEXT, label TEXT, reason TEXT,"
                " signature TEXT, options TEXT, source TEXT);"
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
            )
            self.assertEqual(import_legacy_log(conn, self.write_log()), 3)


if __name__ == "__main__":
    unittest.main()
