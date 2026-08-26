"""Tests for the dashboard.

Two halves: the snapshot it builds (pure, given fakes) and the drawing, which
is exercised against a stub window so the layout arithmetic is covered without
needing a real terminal.
"""

import curses
import datetime
import pathlib
import tempfile
import unittest
from unittest import mock

from nodder import ui
from nodder.herdr import Agent, HerdrError
from nodder.journal import Journal
from nodder.prompts import Action, Decision

WORKSPACES = {"w16": "autoAccept", "w17": "sluice"}


def agent(pane_id, status="idle", workspace="w17", focused=False):
    return Agent(pane_id=pane_id, kind="claude", status=status,
                 workspace_id=workspace, title="a title", focused=focused)


class WhereTest(unittest.TestCase):
    def test_names_a_pane_by_workspace_label(self):
        self.assertEqual(agent("w17:p2").where(WORKSPACES), "sluice/p2")

    def test_falls_back_to_the_workspace_id_when_unlabelled(self):
        self.assertEqual(agent("w99:p1", workspace="w99").where(WORKSPACES),
                         "w99/p1")

    def test_pane_is_just_the_pane_part(self):
        self.assertEqual(agent("w16:p11").pane, "p11")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal = Journal(pathlib.Path(tmp.name) / "d.db", legacy_log=None)
        self.board = ui.Dashboard(self.journal)

    def build(self, agents, workspaces=WORKSPACES, error=None):
        with mock.patch.object(ui.herdr, "list_agents",
                               side_effect=HerdrError(error) if error
                               else lambda: agents), \
             mock.patch.object(ui.herdr, "list_workspaces",
                               return_value=workspaces):
            self.board.refresh()
        return self.board.snapshot

    def record(self, target, action, label, at=None):
        self.journal.record(
            target, Decision(action, label, "why"),
            at=at or datetime.datetime(2026, 8, 26, 20, 0, 0),
        )

    def test_rows_are_named_by_workspace_and_pane(self):
        snap = self.build([agent("w17:p2")])
        self.assertEqual([r.where for r in snap.rows], ["sluice/p2"])

    def test_counts_acceptances_per_pane(self):
        for _ in range(3):
            self.record("w17:p2", Action.ACCEPT, "Yes")
        self.record("w16:p1", Action.ACCEPT, "Yes")
        snap = self.build([agent("w17:p2"), agent("w16:p1", workspace="w16")])
        counts = {r.where: r.accepted for r in snap.rows}
        self.assertEqual(counts, {"sluice/p2": 3, "autoAccept/p1": 1})

    def test_counts_pauses_separately(self):
        self.record("w17:p2", Action.ACCEPT, "Yes")
        self.record("w17:p2", Action.PAUSE, "Spaces")
        (row,) = self.build([agent("w17:p2")]).rows
        self.assertEqual((row.accepted, row.paused), (1, 1))

    def test_legacy_skip_rows_count_as_pauses(self):
        # Rows written before Pause and Ignore were told apart.
        self.journal.record("w17:p2", Decision(Action.PAUSE, "No", "why"))
        with mock.patch.object(ui.herdr, "list_agents",
                               return_value=[agent("w17:p2")]), \
             mock.patch.object(ui.herdr, "list_workspaces",
                               return_value=WORKSPACES):
            self.board.refresh()
        self.assertEqual(self.board.snapshot.rows[0].paused, 1)

    def test_blocked_agents_are_counted_as_waiting(self):
        snap = self.build([agent("w17:p2", status="blocked"),
                           agent("w17:p4")])
        self.assertEqual(snap.waiting, 1)

    def test_blocked_agents_sort_to_the_top(self):
        snap = self.build([
            agent("w17:p9"),
            agent("w17:p2", status="blocked"),
            agent("w17:p5", status="working"),
        ])
        self.assertEqual([r.status for r in snap.rows],
                         ["blocked", "working", "idle"])

    def test_the_daemons_own_pane_is_not_listed(self):
        board = ui.Dashboard(self.journal, self_pane="w17:p2")
        with mock.patch.object(ui.herdr, "list_agents",
                               return_value=[agent("w17:p2"), agent("w17:p4")]), \
             mock.patch.object(ui.herdr, "list_workspaces",
                               return_value=WORKSPACES):
            board.refresh()
        self.assertEqual([r.where for r in board.snapshot.rows], ["sluice/p4"])

    def test_recent_is_capped_and_newest_first(self):
        for minute in range(20):
            self.record("w17:p2", Action.ACCEPT, f"Yes {minute}",
                        at=datetime.datetime(2026, 8, 26, 20, minute, 0))
        snap = self.build([agent("w17:p2")])
        self.assertEqual(len(snap.recent), ui.RECENT)
        self.assertIn("Yes 19", snap.recent[0])

    def test_recent_names_panes_the_readable_way(self):
        self.record("w17:p2", Action.ACCEPT, "Yes")
        snap = self.build([agent("w17:p2")])
        self.assertIn("sluice/p2", snap.recent[0])

    def test_a_herdr_failure_is_reported_not_raised(self):
        snap = self.build([], error="no server")
        self.assertIn("no server", snap.error)
        self.assertEqual(snap.rows, [])

    def test_focus_is_carried_through(self):
        snap = self.build([agent("w17:p2", focused=True)])
        self.assertTrue(snap.rows[0].focused)


class FakeWindow:
    """Records what would have been drawn."""

    def __init__(self, height=24, width=80):
        self.size = (height, width)
        self.lines: list[str] = []

    def getmaxyx(self):
        return self.size

    def erase(self):
        self.lines = []

    def addnstr(self, y, x, text, n, attr=0):
        height, width = self.size
        if y >= height or x >= width:
            raise curses.error("out of bounds")
        self.lines.append(text[:n])

    def noutrefresh(self):
        pass


class DrawTest(unittest.TestCase):
    def snapshot(self, **kwargs):
        defaults = dict(
            rows=[ui.Row(where="sluice/p2", kind="claude", status="blocked",
                         accepted=4, paused=1, note="needs you?")],
            recent=["20:41:02  sluice/p2  PAUSE  Spaces"],
            waiting=1,
        )
        defaults.update(kwargs)
        return ui.Snapshot(**defaults)

    def draw(self, snap, height=24, width=80):
        win = FakeWindow(height, width)
        colour = {k: 0 for k in ("dim", "head", "ok", "warn", "accent")}
        with mock.patch.object(curses, "doupdate"):
            ui.draw(win, snap, colour)
        return "\n".join(win.lines)

    def test_shows_the_pane_by_name(self):
        self.assertIn("sluice/p2", self.draw(self.snapshot()))

    def test_shows_the_acceptance_count(self):
        self.assertIn("4", self.draw(self.snapshot()))

    def test_shows_recent_activity(self):
        self.assertIn("Spaces", self.draw(self.snapshot()))

    def test_marks_the_focused_pane(self):
        rows = [ui.Row(where="sluice/p2", kind="claude", status="idle",
                       accepted=0, paused=0, focused=True)]
        self.assertIn("▸", self.draw(self.snapshot(rows=rows)))

    def test_reports_a_herdr_error_on_screen(self):
        self.assertIn("no server",
                      self.draw(self.snapshot(error="no server")))

    def test_survives_a_tiny_terminal(self):
        # Must clip rather than raise: people resize things.
        for height, width in ((5, 20), (3, 10), (24, 12)):
            with self.subTest(size=(height, width)):
                self.draw(self.snapshot(), height=height, width=width)

    def test_survives_having_nothing_to_show(self):
        self.draw(ui.Snapshot())

    def test_survives_many_agents(self):
        rows = [ui.Row(where=f"sluice/p{n}", kind="claude", status="idle",
                       accepted=n, paused=0) for n in range(50)]
        self.draw(self.snapshot(rows=rows))

    def test_a_long_label_does_not_overflow_the_line(self):
        rows = [ui.Row(where="w" * 60, kind="claude", status="idle",
                       accepted=0, paused=0)]
        self.draw(self.snapshot(rows=rows), width=40)


if __name__ == "__main__":
    unittest.main()
