"""Tests for the command line entry point."""

import pathlib
import tempfile
import unittest

from nodder.cli import single_instance


class SingleInstanceTest(unittest.TestCase):
    """Every daemon watches every pane, so a second one only adds a second
    Enter per Prompt, which lands unread on whatever Prompt is next."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "daemon.lock"

    def test_a_second_daemon_is_refused(self):
        first = single_instance(self.path)
        self.addCleanup(first.close)
        self.assertIsNotNone(first)
        self.assertIsNone(single_instance(self.path))

    def test_the_lock_is_free_again_once_the_daemon_exits(self):
        single_instance(self.path).close()
        again = single_instance(self.path)
        self.addCleanup(again.close)
        self.assertIsNotNone(again)


if __name__ == "__main__":
    unittest.main()
