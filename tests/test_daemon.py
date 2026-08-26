"""Tests for the watch cycle and agent discovery."""

import pathlib
import tempfile
import unittest

from claude_auto_accept.daemon import selectable_targets, watch_cycle
from claude_auto_accept.herdr import Agent, HerdrError, Wait
from claude_auto_accept.journal import Journal
from claude_auto_accept.prompts import Action, prompt_signature

ACCEPTABLE = "Do you want to proceed?\n ❯ 1. Yes\n   2. No\n"
OTHER_PROMPT = "Do you want to proceed?\n ❯ 1. Yes, run it\n   2. No\n"
QUESTION = "Which strategy?\n ❯ 1. Roll back on failure\n   2. Stop at first failure\n"
NO_MENU = "● Working on it…\n"


class FakeClient:
    """Stands in for the herdr CLI, recording what it was asked to do."""

    UNBLOCKED_STATES = ("idle", "working", "done", "unknown")

    def __init__(self, snapshot=ACCEPTABLE, blocked=True, clears=True):
        self.snapshot = snapshot
        self.blocked = blocked
        # Whether the Prompt goes away once it has been answered. False
        # models a Prompt that Enter did not dismiss.
        self.clears = clears
        self.calls: list[tuple] = []

    def wait_for(self, target, states, timeout_ms):
        self.calls.append(("wait", target, tuple(states)))
        if states == ("blocked",):
            return Wait.REACHED if self.blocked else Wait.TIMEOUT
        return Wait.REACHED if self.clears else Wait.TIMEOUT

    def read_detection(self, target, lines=40):
        self.calls.append(("read", target))
        return self.snapshot

    def send_enter(self, target):
        self.calls.append(("enter", target))

    def kinds(self, name):
        return [c for c in self.calls if c[0] == name]


class WatchCycleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal = Journal(pathlib.Path(tmp.name) / "d.db", legacy_log=None)

    def cycle(self, client, dry_run=False, last=None):
        return watch_cycle("w16:p1", client, self.journal, dry_run=dry_run,
                           last_signature=last)

    def lines(self):
        return self.journal.query()

    def test_affirmative_option_is_accepted_with_enter(self):
        client = FakeClient(ACCEPTABLE)
        decision, _ = self.cycle(client)
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(client.kinds("enter"), [("enter", "w16:p1")])

    def test_question_prompt_is_not_answered(self):
        client = FakeClient(QUESTION)
        decision, _ = self.cycle(client)
        self.assertEqual(decision.action, Action.SKIP)
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

    def test_nothing_happens_when_the_agent_never_blocks(self):
        client = FakeClient(ACCEPTABLE, blocked=False)
        decision, signature = self.cycle(client)
        self.assertIsNone(decision)
        self.assertIsNone(signature)
        self.assertEqual(client.kinds("read"), [])
        self.assertEqual(client.kinds("enter"), [])

    def test_waits_for_the_agent_to_leave_blocked_after_acting(self):
        client = FakeClient(ACCEPTABLE)
        self.cycle(client)
        waits = client.kinds("wait")
        self.assertEqual(waits[0][2], ("blocked",))
        self.assertEqual(waits[-1][2], FakeClient.UNBLOCKED_STATES)

    def test_blocked_on_something_that_is_not_a_menu_is_skipped(self):
        client = FakeClient(NO_MENU)
        decision, _ = self.cycle(client)
        self.assertEqual(decision.action, Action.SKIP)
        self.assertIsNone(decision.label)
        self.assertEqual(client.kinds("enter"), [])


class DuplicateSuppressionTest(unittest.TestCase):
    """`herdr agent wait` is level-triggered, so the same unanswered Prompt
    reports Blocked on every cycle. It must be acted on and recorded once."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal = Journal(pathlib.Path(tmp.name) / "d.db", legacy_log=None)

    def lines(self):
        return self.journal.query()

    def cycle(self, client, last=None, dry_run=False):
        return watch_cycle("w16:p1", client, self.journal, dry_run=dry_run,
                           last_signature=last)

    def test_the_same_prompt_is_not_pressed_twice(self):
        # Still on screen after Enter, so it must not be pressed again.
        client = FakeClient(ACCEPTABLE, clears=False)
        _, signature = self.cycle(client)
        self.cycle(client, last=signature)
        self.assertEqual(len(client.kinds("enter")), 1)

    def test_the_same_prompt_is_not_journalled_twice(self):
        client = FakeClient(ACCEPTABLE, clears=False)
        _, signature = self.cycle(client)
        self.cycle(client, last=signature)
        self.assertEqual(len(self.lines()), 1)

    def test_an_unanswered_skip_is_recorded_once_however_long_it_waits(self):
        # A Skip is never pressed, so the Prompt stays up until a human acts.
        client = FakeClient(QUESTION, clears=False)
        _, signature = self.cycle(client)
        for _ in range(5):
            _, signature = self.cycle(client, last=signature)
        self.assertEqual(len(self.lines()), 1)

    def test_dry_run_does_not_re_record_a_prompt_it_never_clears(self):
        client = FakeClient(ACCEPTABLE, clears=False)
        _, signature = self.cycle(client, dry_run=True)
        for _ in range(3):
            _, signature = self.cycle(client, last=signature, dry_run=True)
        self.assertEqual(len(self.lines()), 1)

    def test_a_different_prompt_is_acted_on(self):
        first = FakeClient(ACCEPTABLE)
        _, signature = self.cycle(first)
        second = FakeClient(OTHER_PROMPT)
        decision, _ = self.cycle(second, last=signature)
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(decision.label, "Yes, run it")
        self.assertEqual(len(self.lines()), 2)

    def test_an_identical_prompt_that_arrives_after_the_last_one_cleared(self):
        # The regression that made the daemon go silent: Claude asking to run
        # the same command twice is two decisions, not a duplicate. Suppression
        # must only apply while a Prompt is still on screen.
        client = FakeClient(ACCEPTABLE)
        signature = None
        for _ in range(5):
            _, signature = self.cycle(client, last=signature)
        self.assertEqual(len(client.kinds("enter")), 5)
        self.assertEqual(len(self.lines()), 5)

    def test_the_signature_is_forgotten_once_the_prompt_clears(self):
        client = FakeClient(ACCEPTABLE)
        _, signature = self.cycle(client)
        self.assertIsNone(
            signature,
            "a Prompt that cleared must not suppress the next one",
        )

    def test_the_signature_is_kept_while_the_prompt_is_still_on_screen(self):
        client = FakeClient(ACCEPTABLE, clears=False)
        _, signature = self.cycle(client)
        self.assertIsNotNone(signature)

    def test_a_prompt_that_never_clears_is_pressed_only_once(self):
        # The case the guard was added for: Enter did not dismiss it, so do
        # not sit there re-pressing and re-recording.
        client = FakeClient(ACCEPTABLE, clears=False)
        signature = None
        for _ in range(5):
            _, signature = self.cycle(client, last=signature)
        self.assertEqual(len(client.kinds("enter")), 1)
        self.assertEqual(len(self.lines()), 1)

    def test_an_unblocked_agent_clears_the_signature(self):
        client = FakeClient(ACCEPTABLE, blocked=False)
        _, signature = self.cycle(client)
        self.assertIsNone(signature)

    def test_signature_distinguishes_prompts_by_selected_option(self):
        moved = "Do you want to proceed?\n   1. Yes\n ❯ 2. No\n"
        self.assertNotEqual(prompt_signature(ACCEPTABLE), prompt_signature(moved))


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


if __name__ == "__main__":
    unittest.main()
