"""Tests for watcher bookkeeping.

The threading itself is left to the standard library; what is tested here is
that the Supervisor keeps exactly one watcher per agent, retires watchers whose
agent has gone, and stops cleanly.
"""

import threading
import unittest

from claude_auto_accept.daemon import Supervisor
from claude_auto_accept.herdr import Agent, HerdrError, Wait
from claude_auto_accept.journal import Journal


class FakeClient:
    UNBLOCKED_STATES = ("idle", "working", "done", "unknown")

    def __init__(self, agents=(), fail=False):
        self.agents = list(agents)
        self.fail = fail
        self.listed = threading.Event()

    def list_agents(self):
        self.listed.set()
        if self.fail:
            raise HerdrError("no server")
        return self.agents

    # Watchers park here; TIMEOUT means "no prompt appeared", so threads loop
    # without doing anything and exit as soon as they are asked to.
    def wait_for(self, target, states, timeout_ms):
        return Wait.TIMEOUT


def claude(pane_id):
    return Agent(pane_id=pane_id, kind="claude", status="idle")


class SupervisorTest(unittest.TestCase):
    def supervisor(self, client, **kwargs):
        sup = Supervisor(
            client=client,
            journal=Journal("/dev/null"),
            discovery_interval=0.01,
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

    def test_a_retired_watcher_does_not_block_its_replacement_forever(self):
        # A retired watcher still parked in a blocking wait keeps its slot in
        # `_watchers`, which stops a replacement being spawned. The block wait
        # is what bounds that window, so it must stay short.
        from claude_auto_accept.daemon import BLOCK_WAIT_MS
        self.assertLessEqual(BLOCK_WAIT_MS, 15_000)

    def test_the_daemons_own_pane_is_never_watched(self):
        client = FakeClient([claude("w1:p1"), claude("w1:p2")])
        sup = self.supervisor(client, self_pane="w1:p1")
        thread = self.running(sup, client)
        sup.stop.set()
        thread.join(timeout=5)
        self.assertNotIn("w1:p1", sup._watchers)


if __name__ == "__main__":
    unittest.main()
