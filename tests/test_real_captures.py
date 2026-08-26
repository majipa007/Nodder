"""Tests against snapshots captured from a live Claude Code session.

These three fixtures are verbatim `herdr agent read --source detection` output
taken on 26/08/2026 from Claude Code 2.1.241 under herdr 0.8.0. They are the
ground truth the synthetic fixtures imitate; if Claude Code changes its prompt
rendering, these are the tests that should fail first.
"""

import pathlib
import unittest

from nodder.prompts import Action, classify, parse_options

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class RealBashPermissionTest(unittest.TestCase):
    """A real Bash permission prompt: `touch /tmp/...`."""

    def setUp(self):
        self.snapshot = fixture("real_bash_permission.txt")

    def test_is_accepted(self):
        decision = classify(self.snapshot)
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(decision.label, "Yes")

    def test_reads_all_three_options(self):
        labels = [o.label for o in parse_options(self.snapshot)]
        self.assertEqual(labels[0], "Yes")
        self.assertEqual(
            labels[1], "Yes, and always allow access to /tmp from this project"
        )
        self.assertEqual(labels[2], "No")

    def test_the_echoed_user_message_is_not_mistaken_for_an_option(self):
        # This capture contains a real input-box line -- "❯ Run exactly this
        # shell command..." -- above the menu. It must not win over "❯ 1. Yes".
        labels = [o.label for o in parse_options(self.snapshot)]
        self.assertTrue(all("Run exactly this shell command" not in l for l in labels))


class RealQuestionPromptTest(unittest.TestCase):
    """A real AskUserQuestion dialog: tabs vs spaces."""

    def setUp(self):
        self.snapshot = fixture("real_question_prompt.txt")

    def test_is_skipped(self):
        decision = classify(self.snapshot)
        self.assertEqual(decision.action, Action.SKIP)
        self.assertEqual(decision.label, "Spaces")

    def test_multi_line_option_descriptions_do_not_become_options(self):
        labels = [o.label for o in parse_options(self.snapshot)]
        self.assertEqual(labels[:3], ["Spaces", "Tabs", "Type something."])

    def test_a_rule_between_options_does_not_break_numbered_parsing(self):
        # Option 4 sits below a horizontal rule in this capture.
        numbers = [o.number for o in parse_options(self.snapshot)]
        self.assertEqual(numbers, [1, 2, 3, 4])


class RealTypedMessageTest(unittest.TestCase):
    """A capture of a pane where the user had typed a two-line message.

    herdr reported this pane Blocked repeatedly during the session it was
    taken from: its low-priority fallback rule matches "do you want to" plus a
    "❯" anywhere in the buffer, and the transcript contained both. The message
    sitting in the input box then parsed as a two-option unnumbered menu, and
    one beginning with "Yes" would have been submitted on the user's behalf.

    This particular capture replays as `working` rather than `blocked`,
    because it caught a spinner that outranks that fallback rule. What it
    preserves is the input-box shape that fooled the parser, which is what
    these tests are about.
    """

    def setUp(self):
        self.snapshot = fixture("real_typed_message_not_a_menu.txt")

    def test_a_typed_message_is_not_a_menu(self):
        self.assertEqual(parse_options(self.snapshot), [])

    def test_it_is_skipped_for_want_of_a_menu(self):
        decision = classify(self.snapshot)
        self.assertEqual(decision.action, Action.SKIP)
        self.assertIsNone(decision.label)

    def test_it_would_still_be_skipped_if_the_message_began_with_yes(self):
        # The guard must not depend on the text happening not to say "Yes".
        hostile = self.snapshot.replace(
            "❯ okay what is this SKIP thing?", "❯ Yes please do the thing"
        )
        self.assertEqual(classify(hostile).action, Action.SKIP)


class RealWorkingOutputTest(unittest.TestCase):
    def test_a_busy_agent_offers_nothing_to_accept(self):
        decision = classify(fixture("real_working_not_a_prompt.txt"))
        self.assertEqual(decision.action, Action.SKIP)
        self.assertIsNone(decision.label)


if __name__ == "__main__":
    unittest.main()
