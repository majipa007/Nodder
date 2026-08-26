"""Tests for the herdr CLI client.

The client is a thin translation layer, so these tests pin the argv it builds
and how it reads herdr's two response shapes: JSON for control commands, raw
text for `agent read`.
"""

import json
import subprocess
import unittest
from unittest import mock

from claude_auto_accept import herdr

AGENT_LIST = {
    "id": "cli:agent:list",
    "result": {
        "type": "agent_list",
        "agents": [
            {
                "agent": "claude",
                "agent_status": "blocked",
                "pane_id": "w16:p1",
                "workspace_id": "w16",
                "terminal_title_stripped": "autoAccept",
            },
            {
                "agent": "codex",
                "agent_status": "idle",
                "pane_id": "w17:p2",
                "workspace_id": "w17",
                "terminal_title_stripped": "sluice",
            },
        ],
    },
}


def completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class ListAgentsTest(unittest.TestCase):
    def test_parses_every_agent(self):
        with mock.patch.object(
            herdr.subprocess, "run", return_value=completed(json.dumps(AGENT_LIST))
        ):
            agents = herdr.list_agents()
        self.assertEqual([a.pane_id for a in agents], ["w16:p1", "w17:p2"])
        self.assertEqual(agents[0].status, "blocked")
        self.assertEqual(agents[1].kind, "codex")

    def test_returns_empty_list_when_no_agents_are_running(self):
        payload = {"result": {"type": "agent_list", "agents": []}}
        with mock.patch.object(
            herdr.subprocess, "run", return_value=completed(json.dumps(payload))
        ):
            self.assertEqual(herdr.list_agents(), [])

    def test_server_error_raises(self):
        with mock.patch.object(
            herdr.subprocess,
            "run",
            return_value=completed(returncode=1, stderr='{"error":"no server"}'),
        ):
            with self.assertRaises(herdr.HerdrError):
                herdr.list_agents()

    def test_unparseable_output_raises(self):
        with mock.patch.object(
            herdr.subprocess, "run", return_value=completed("not json")
        ):
            with self.assertRaises(herdr.HerdrError):
                herdr.list_agents()


class ReadDetectionTest(unittest.TestCase):
    def test_returns_raw_text_not_json(self):
        with mock.patch.object(
            herdr.subprocess, "run", return_value=completed("❯ 1. Yes\n  2. No\n")
        ) as run:
            snapshot = herdr.read_detection("w16:p1", lines=40)
        self.assertEqual(snapshot, "❯ 1. Yes\n  2. No\n")
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            ["herdr", "agent", "read", "w16:p1",
             "--source", "detection", "--lines", "40"],
        )


class WaitTest(unittest.TestCase):
    def test_builds_a_repeated_until_flag_for_each_state(self):
        with mock.patch.object(
            herdr.subprocess, "run", return_value=completed("{}")
        ) as run:
            herdr.wait_for(
                "w16:p1", states=("idle", "working", "done"), timeout_ms=5000
            )
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            ["herdr", "agent", "wait", "w16:p1",
             "--until", "idle", "--until", "working", "--until", "done",
             "--timeout", "5000"],
        )

    def test_timeout_is_an_ordinary_outcome(self):
        # The agent simply did not reach the state. It must not crash the
        # watcher thread.
        stderr = '{"error":{"code":"timeout","message":"timed out"}}'
        with mock.patch.object(
            herdr.subprocess, "run",
            return_value=completed(returncode=1, stderr=stderr),
        ):
            self.assertIs(
                herdr.wait_for("w16:p1", states=("blocked",), timeout_ms=10),
                herdr.Wait.TIMEOUT,
            )

    def test_reaching_the_state_is_reported(self):
        with mock.patch.object(
            herdr.subprocess, "run", return_value=completed("{}")
        ):
            self.assertIs(
                herdr.wait_for("w16:p1", states=("blocked",), timeout_ms=10),
                herdr.Wait.REACHED,
            )

    def test_a_vanished_pane_raises_rather_than_looking_like_a_timeout(self):
        # herdr answers agent_not_found in milliseconds. Treating that as a
        # timeout would spin the watcher thread at full speed.
        stderr = '{"error":{"code":"agent_not_found","message":"not found"}}'
        with mock.patch.object(
            herdr.subprocess, "run",
            return_value=completed(returncode=1, stderr=stderr),
        ):
            with self.assertRaises(herdr.HerdrError):
                herdr.wait_for("w99:p9", states=("blocked",), timeout_ms=10)

    def test_an_unparseable_error_raises(self):
        with mock.patch.object(
            herdr.subprocess, "run",
            return_value=completed(returncode=1, stderr="server exploded"),
        ):
            with self.assertRaises(herdr.HerdrError):
                herdr.wait_for("w16:p1", states=("blocked",), timeout_ms=10)


class SendEnterTest(unittest.TestCase):
    def test_sends_the_logical_enter_key(self):
        with mock.patch.object(
            herdr.subprocess, "run", return_value=completed("{}")
        ) as run:
            herdr.send_enter("w16:p1")
        self.assertEqual(
            run.call_args.args[0],
            ["herdr", "agent", "send-keys", "w16:p1", "enter"],
        )


if __name__ == "__main__":
    unittest.main()
