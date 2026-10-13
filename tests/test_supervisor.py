"""Tests for watcher bookkeeping.

The threading itself is left to the standard library; what is tested here is
that the Supervisor keeps exactly one watcher per agent, retires watchers whose
agent has gone, and stops cleanly.
"""

import threading
import unittest

from nodder.daemon import Supervisor
from nodder.herdr import Agent, HerdrError
from nodder.journal import Journal


class FakeClient:
    def __init__(self, agents=(), fail=False):
        self.agents = list(agents)
        self.fail = fail
        self.listed = threading.Event()
        self.read: set[str] = set()
        self.first_read = threading.Event()

    def list_agents(self):
        self.listed.set()
        if self.fail:
            raise HerdrError("no server")
        return self.agents

    # No menu on screen, so watchers loop without doing anything and exit as
    # soon as they are asked to.
    def read_detection(self, target, lines=40):
        self.read.add(target)
        self.first_read.set()
        return "● Working on it…\n"


def claude(pane_id, status="idle"):
    return Agent(pane_id=pane_id, kind="claude", status=status)


class SupervisorTest(unittest.TestCase):
    def supervisor(self, client, **kwargs):
        sup = Supervisor(
            client=client,
            journal=Journal("/dev/null"),
            discovery_interval=0.01,
            scan_interval=0.01,
            **kwargs,
        )
        self.addCleanup(sup.stop.set)
        return sup

    def running(self, sup, client):
        thread = threading.Thread(target=sup.run)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(sup.stop.set)
        client.listed.wait(timeout=5)
        return thread

    def test_one_watcher_is_started_per_agent(self):
        sup = self.supervisor(FakeClient())
        sup._spawn("w1:p1")
        sup._spawn("w1:p2")
        self.assertEqual(set(sup._watchers), {"w1:p1", "w1:p2"})

    def test_dead_watchers_are_reaped(self):
        sup = self.supervisor(FakeClient())
        sup._spawn("w1:p1")
        sup._watchers["w1:p1"].retire()
        sup._watchers["w1:p1"].thread.join(timeout=5)
        sup._reap()
        self.assertEqual(sup._watchers, {})

    def test_a_watcher_whose_agent_disappears_is_retired(self):
        # Otherwise it keeps polling a pane that no longer exists.
        sup = self.supervisor(FakeClient())
        sup._spawn("w1:p1")
        watcher = sup._watchers["w1:p1"]
        sup._retire(targets=set())
        self.assertTrue(watcher.retired.is_set())
        watcher.thread.join(timeout=5)
        self.assertFalse(watcher.thread.is_alive())

    def test_a_watcher_whose_agent_is_still_present_is_left_alone(self):
        sup = self.supervisor(FakeClient())
        sup._spawn("w1:p1")
        sup._retire(targets={"w1:p1"})
        self.assertFalse(sup._watchers["w1:p1"].retired.is_set())

    def test_run_stops_when_asked(self):
        client = FakeClient([claude("w1:p1")])
        sup = self.supervisor(client)
        thread = self.running(sup, client)
        sup.stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_discovery_failure_does_not_stop_the_supervisor(self):
        client = FakeClient(fail=True)
        sup = self.supervisor(client)
        thread = self.running(sup, client)
        sup.stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_discovery_failure_does_not_retire_existing_watchers(self):
        # A momentary herdr hiccup is not evidence that every agent has gone.
        client = FakeClient(fail=True)
        sup = self.supervisor(client)
        sup._spawn("w1:p1")
        watcher = sup._watchers["w1:p1"]
        thread = self.running(sup, client)
        # Checked while the loop is still running: shutdown retires every
        # watcher, which would mask the thing under test.
        self.assertFalse(watcher.retired.is_set())
        sup.stop.set()
        thread.join(timeout=5)

    def test_an_agent_that_comes_back_is_watched_again(self):
        # Claude exiting and restarting in the same pane is the ordinary case
        # after a crash or a /quit. The daemon must pick it up again rather
        # than leaving that pane unwatched for the rest of the session.
        sup = self.supervisor(FakeClient())
        sup._spawn("w1:p1")
        first = sup._watchers["w1:p1"]

        sup._retire(targets=set())          # agent went away
        first.thread.join(timeout=5)
        sup._reap()
        self.assertEqual(sup._watchers, {})

        sup._spawn("w1:p1")                 # agent came back
        self.assertIsNot(sup._watchers["w1:p1"], first)
        self.assertFalse(sup._watchers["w1:p1"].retired.is_set())

    def test_working_agents_are_scanned_and_idle_ones_are_not(self):
        # herdr can call an agent working while a Prompt is on screen, so
        # waiting for blocked missed it. An idle pane is left alone: a picker
        # the human opened there (/model, /resume) is drawn like a menu.
        client = FakeClient([claude("w1:p1", "working"), claude("w1:p2", "idle")])
        sup = self.supervisor(client)
        thread = self.running(sup, client)
        self.assertTrue(client.first_read.wait(timeout=5))
        sup.stop.wait(0.1)                  # ten scans' worth
        sup.stop.set()
        thread.join(timeout=5)
        self.assertEqual(client.read, {"w1:p1"})

    def test_the_daemons_own_pane_is_never_watched(self):
        client = FakeClient([claude("w1:p1"), claude("w1:p2")])
        sup = self.supervisor(client, self_pane="w1:p1")
        thread = self.running(sup, client)
        sup.stop.set()
        thread.join(timeout=5)
        self.assertNotIn("w1:p1", sup._watchers)


if __name__ == "__main__":
    unittest.main()
