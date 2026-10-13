"""Tests for the watch cycle and agent discovery."""

import pathlib
import tempfile
import unittest

from nodder.daemon import (
    REPRESS_AFTER,
    pending_decisions,
    selectable_targets,
    watch_cycle,
)
from nodder.herdr import Agent, HerdrError
from nodder.journal import Journal
from nodder.prompts import Action, prompt_signature

ACCEPTABLE = "Do you want to proceed?\n ❯ 1. Yes\n   2. No\n esc to cancel · enter to confirm\n"
OTHER_PROMPT = "Do you want to proceed?\n ❯ 1. Yes, run it\n   2. No\n esc to cancel · enter to confirm\n"
QUESTION = "Which strategy?\n ❯ 1. Roll back on failure\n   2. Stop at first failure\n esc to cancel · enter to confirm\n"
NO_MENU = "● Working on it…\n"


class FakeClient:
    """Stands in for the herdr CLI: a pane's screens, and what was done to it.

    The first screen is what is showing. Enter answers it and reveals the
    next, if there is one; a single screen is a Prompt Enter did not dismiss.
    """

    def __init__(self, *screens):
        self.screens = list(screens)
        self.calls: list[tuple] = []

    def read_detection(self, target, lines=40):
        self.calls.append(("read", target))
        return self.screens[0]

    def send_enter(self, target):
        self.calls.append(("enter", target))
        if len(self.screens) > 1:
            self.screens.pop(0)

    def kinds(self, name):
        return [c for c in self.calls if c[0] == name]


class Cycles(unittest.TestCase):
    """Runs watch cycles against a journal of its own, on a clock it controls."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal = Journal(pathlib.Path(tmp.name) / "d.db", legacy_log=None)
        self.clock = 0.0

    def cycle(self, client, last=None, dry_run=False):
        return watch_cycle("w16:p1", client, self.journal, dry_run=dry_run,
                           last=last, now=lambda: self.clock)

    def scan(self, client, times, last=None, dry_run=False, every=1.0):
        """`times` cycles a second apart, threading what each one saw."""
        for _ in range(times):
            _, last = self.cycle(client, last=last, dry_run=dry_run)
            self.clock += every
        return last

    def lines(self):
        return self.journal.query()


class WatchCycleTest(Cycles):
    def test_affirmative_option_is_accepted_with_enter(self):
        client = FakeClient(ACCEPTABLE)
        decision, _ = self.cycle(client)
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(client.kinds("enter"), [("enter", "w16:p1")])

    def test_question_prompt_is_paused_not_answered(self):
        client = FakeClient(QUESTION)
        decision, _ = self.cycle(client)
        self.assertEqual(decision.action, Action.PAUSE)
        self.assertEqual(client.kinds("enter"), [])

    def test_dry_run_decides_but_never_presses(self):
        client = FakeClient(ACCEPTABLE)
        decision, _ = self.cycle(client, dry_run=True)
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(client.kinds("enter"), [])

    def test_every_decision_reaches_the_journal(self):
        self.cycle(FakeClient(ACCEPTABLE))
        self.cycle(FakeClient(QUESTION))
        self.assertEqual(len(self.lines()), 2)

    def test_a_screen_with_no_menu_is_not_a_decision(self):
        # herdr's fallback rule fires on ordinary transcripts. Recording those
        # would bury the Pauses that actually need the human.
        client = FakeClient(NO_MENU)
        decision, last = self.cycle(client)
        self.assertIsNone(decision)
        self.assertIsNone(last)
        self.assertEqual(client.kinds("enter"), [])
        self.assertEqual(self.lines(), [])


class DuplicateSuppressionTest(Cycles):
    """Every scan sees an unanswered Prompt again. It must be acted on and
    recorded once -- with one exception: a Yes that is still up long after
    Enter was pressed is pressed again, because that Enter did not land."""

    def test_the_same_prompt_is_not_pressed_on_every_scan(self):
        client = FakeClient(ACCEPTABLE)
        self.scan(client, times=int(REPRESS_AFTER) - 1)
        self.assertEqual(len(client.kinds("enter")), 1)
        self.assertEqual(len(self.lines()), 1)

    def test_a_yes_still_on_screen_is_pressed_again_after_a_while(self):
        # Enter did not land -- or an identical Prompt took its place. Either
        # way the Prompt wants answering; going quiet on it is the stall.
        client = FakeClient(ACCEPTABLE)
        self.scan(client, times=int(REPRESS_AFTER) + 1)
        self.assertEqual(len(client.kinds("enter")), 2)
        self.assertEqual(len(self.lines()), 2, "every press is recorded")

    def test_an_unanswered_pause_is_recorded_once_however_long_it_waits(self):
        client = FakeClient(QUESTION)
        self.scan(client, times=int(REPRESS_AFTER) * 5)
        self.assertEqual(client.kinds("enter"), [])
        self.assertEqual(len(self.lines()), 1)

    def test_dry_run_never_re_records_a_prompt_it_never_clears(self):
        client = FakeClient(ACCEPTABLE)
        self.scan(client, times=int(REPRESS_AFTER) * 3, dry_run=True)
        self.assertEqual(client.kinds("enter"), [])
        self.assertEqual(len(self.lines()), 1)

    def test_a_different_prompt_is_acted_on(self):
        client = FakeClient(ACCEPTABLE)
        last = self.scan(client, times=1)
        client.screens = [OTHER_PROMPT]
        decision, _ = self.cycle(client, last=last)
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(decision.label, "Yes, run it")
        self.assertEqual(len(self.lines()), 2)

    def test_an_identical_prompt_that_arrives_after_the_last_one_cleared(self):
        # The regression that made the daemon go silent: Claude asking to run
        # the same command twice is two decisions, not a duplicate.
        client = FakeClient()
        last = None
        for _ in range(5):
            client.screens = [ACCEPTABLE, NO_MENU]
            last = self.scan(client, times=2, last=last)
        self.assertEqual(len(client.kinds("enter")), 5)
        self.assertEqual(len(self.lines()), 5)

    def test_what_was_seen_is_forgotten_once_the_prompt_clears(self):
        client = FakeClient(ACCEPTABLE, NO_MENU)
        last = self.scan(client, times=2)
        self.assertIsNone(last, "a Prompt that cleared must not suppress the next one")

    def test_signature_distinguishes_prompts_by_selected_option(self):
        moved = "Do you want to proceed?\n   1. Yes\n ❯ 2. No\n esc to cancel · enter to confirm\n"
        self.assertNotEqual(prompt_signature(ACCEPTABLE), prompt_signature(moved))


def queued(position, total, prompt=ACCEPTABLE):
    """One of a workflow's queued Prompts, under the panel header that numbers it."""
    return f"{'─' * 20}\n Bash command · from a workflow  {position} of {total}\n{prompt}"


class QueuedPromptsTest(Cycles):
    """A workflow's subagents queue their Prompts in one pane. Answering one
    shows the next at once, and their Options are often identical."""

    def test_each_queued_prompt_is_pressed_on_the_next_scan(self):
        client = FakeClient(*[queued(n, 3) for n in (1, 2, 3)], NO_MENU)
        self.scan(client, times=3)
        self.assertEqual(len(client.kinds("enter")), 3)
        self.assertEqual(len(self.lines()), 3)

    def test_a_question_queued_behind_approvals_is_still_paused(self):
        client = FakeClient(queued(1, 2), queued(2, 2, QUESTION))
        self.scan(client, times=4)
        self.assertEqual(len(client.kinds("enter")), 1)
        self.assertEqual([r.outcome for r in self.lines()], ["ACCEPT", "PAUSE"])


class SelectableTargetsTest(unittest.TestCase):
    def agents(self, *specs):
        return [Agent(pane_id=p, kind=k, status="idle") for p, k in specs]

    def test_selects_claude_agents(self):
        agents = self.agents(("w1:p1", "claude"), ("w1:p2", "claude"))
        self.assertEqual(selectable_targets(agents), {"w1:p1", "w1:p2"})

    def test_ignores_agents_of_other_kinds(self):
        agents = self.agents(("w1:p1", "claude"), ("w1:p2", "codex"))
        self.assertEqual(selectable_targets(agents), {"w1:p1"})

    def test_excludes_the_pane_the_daemon_is_running_in(self):
        agents = self.agents(("w1:p1", "claude"), ("w1:p2", "claude"))
        self.assertEqual(selectable_targets(agents, self_pane="w1:p1"), {"w1:p2"})

    def test_no_agents_yields_no_targets(self):
        self.assertEqual(selectable_targets([]), set())


class WatchCycleErrorTest(unittest.TestCase):
    def test_a_herdr_failure_propagates_for_the_thread_to_handle(self):
        class Broken(FakeClient):
            def read_detection(self, target, lines=40):
                raise HerdrError("pane vanished")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(HerdrError):
            watch_cycle(
                "w16:p1", Broken(),
                Journal(pathlib.Path(tmp.name) / "d.db", legacy_log=None),
                dry_run=False,
            )


class PendingDecisionsTest(unittest.TestCase):
    """`nodder --waiting` answers "which panes need me?" from live state."""

    class Client:
        def __init__(self, agents, snapshots):
            self._agents = agents
            self._snapshots = snapshots

        def list_agents(self):
            return self._agents

        def read_detection(self, target, lines=40):
            return self._snapshots[target]

    def agent(self, pane_id, status="blocked", kind="claude"):
        return Agent(pane_id=pane_id, kind=kind, status=status)

    def test_lists_only_agents_paused_on_a_real_decision(self):
        client = self.Client(
            [self.agent("w1:p1"), self.agent("w1:p2"), self.agent("w1:p3")],
            {
                "w1:p1": QUESTION,     # a decision -> needs the human
                "w1:p2": ACCEPTABLE,   # a yes -> nodder handles it
                "w1:p3": NO_MENU,      # not a menu -> nothing to do
            },
        )
        waiting = pending_decisions(client)
        self.assertEqual([a.pane_id for a, _, _ in waiting], ["w1:p1"])

    def test_reports_the_options_so_the_user_can_see_the_choice(self):
        client = self.Client([self.agent("w1:p1")], {"w1:p1": QUESTION})
        (_, decision, options), = pending_decisions(client)
        self.assertEqual(decision.action, Action.PAUSE)
        self.assertEqual(len(options), 2)

    def test_a_question_counts_even_when_herdr_calls_the_agent_working(self):
        client = self.Client(
            [self.agent("w1:p1", status="working")], {"w1:p1": QUESTION}
        )
        self.assertEqual(len(pending_decisions(client)), 1)

    def test_idle_agents_are_not_waiting(self):
        client = self.Client(
            [self.agent("w1:p1", status="idle")], {"w1:p1": QUESTION}
        )
        self.assertEqual(pending_decisions(client), [])

    def test_excludes_the_daemons_own_pane(self):
        client = self.Client([self.agent("w1:p1")], {"w1:p1": QUESTION})
        self.assertEqual(pending_decisions(client, self_pane="w1:p1"), [])

    def test_a_pane_that_cannot_be_read_is_passed_over(self):
        class Broken(self.Client):
            def read_detection(self, target, lines=40):
                raise HerdrError("gone")

        client = Broken([self.agent("w1:p1")], {})
        self.assertEqual(pending_decisions(client), [])


if __name__ == "__main__":
    unittest.main()
