"""Tests for the decision log.

Because every Affirmative Option is accepted (spec section 6), this log is the
only record of what the daemon pressed. Its format is therefore pinned.
"""

import datetime
import pathlib
import tempfile
import unittest

from claude_auto_accept.journal import Journal
from claude_auto_accept.prompts import Action, Decision

AT = datetime.datetime(2026, 8, 26, 14, 32, 7)


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "nested" / "log"
        self.journal = Journal(self.path)
        self.addCleanup(self.tmp.cleanup)

    def test_creates_the_log_and_its_parent_directory_on_first_write(self):
        self.assertFalse(self.path.exists())
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "why"), at=AT)
        self.assertTrue(self.path.exists())

    def test_uses_day_month_year_ordering(self):
        self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "why"), at=AT)
        self.assertIn("26/08/2026 14:32:07", self.path.read_text())

    def test_records_target_outcome_and_label(self):
        self.journal.record(
            "w16:p1",
            Decision(Action.ACCEPT, "Yes, I trust this folder", "why"),
            at=AT,
        )
        line = self.path.read_text().strip()
        self.assertIn("w16:p1", line)
        self.assertIn("ACCEPT", line)
        self.assertIn('"Yes, I trust this folder"', line)

    def test_a_skip_records_the_option_it_declined_to_press(self):
        self.journal.record(
            "w16:p1", Decision(Action.SKIP, "Run migrations", "why"), at=AT
        )
        line = self.path.read_text()
        self.assertIn("SKIP", line)
        self.assertIn('"Run migrations"', line)

    def test_a_decision_with_no_option_still_records(self):
        self.journal.record("w16:p1", Decision(Action.SKIP, None, "no menu"), at=AT)
        self.assertIn("SKIP", self.path.read_text())

    def test_appends_rather_than_truncating(self):
        for _ in range(3):
            self.journal.record("w16:p1", Decision(Action.ACCEPT, "Yes", "w"), at=AT)
        self.assertEqual(len(self.path.read_text().strip().splitlines()), 3)

    def test_newlines_in_a_label_cannot_break_the_one_line_per_record_format(self):
        self.journal.record(
            "w16:p1", Decision(Action.ACCEPT, "Yes\nInjected line", "w"), at=AT
        )
        self.assertEqual(len(self.path.read_text().strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
