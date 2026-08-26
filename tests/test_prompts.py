"""Tests for Selected Option extraction and Acceptance/Skip classification.

Fixtures under tests/fixtures/ are validated against herdr's own rule engine
by tests/validate_fixtures.py, so they classify the way real terminal output
classifies.
"""

import pathlib
import unittest

from nodder.prompts import (
    Action,
    classify,
    parse_options,
    selected_option,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParseOptionsTest(unittest.TestCase):
    def test_numbered_menu_yields_every_option_in_order(self):
        options = parse_options(fixture("bash_permission_numbered.txt"))
        self.assertEqual([o.number for o in options], [1, 2, 3])
        self.assertEqual(options[0].label, "Yes")
        self.assertEqual(
            options[2].label,
            "No, and tell Claude what to do differently (esc)",
        )

    def test_cursor_marks_exactly_one_option_as_selected(self):
        options = parse_options(fixture("bash_permission_numbered.txt"))
        self.assertEqual([o.selected for o in options], [True, False, False])

    def test_unnumbered_menu_is_parsed_as_a_contiguous_block(self):
        options = parse_options(fixture("permission_unnumbered.txt"))
        self.assertEqual(
            [o.label for o in options],
            [
                "Yes",
                "Yes, and don't ask again this session",
                "No, and tell Claude what to do differently (esc)",
            ],
        )
        self.assertTrue(options[0].selected)
        self.assertIsNone(options[0].number)

    def test_output_with_no_menu_yields_no_options(self):
        self.assertEqual(parse_options(fixture("real_working_not_a_prompt.txt")), [])

    def test_lone_input_box_caret_is_not_an_option(self):
        # Claude Code's prompt box renders a bare "❯". It must never be read
        # as a one-option menu.
        self.assertEqual(parse_options("─────\n❯\n─────\n"), [])

    def test_typed_text_in_the_input_box_is_not_a_menu(self):
        # The dangerous case: the user has typed something starting with "Yes"
        # into the input box. A horizontal rule terminates the block, so this
        # is a single option and therefore not a menu.
        snapshot = "─────────────\n❯ Yes do it now\n─────────────\n  O5 1M | 42%\n"
        self.assertEqual(parse_options(snapshot), [])

    def test_a_numbered_list_in_prose_above_the_menu_is_ignored(self):
        # Claude often prints numbered plans. Only the menu at the bottom of
        # the screen is a menu.
        snapshot = (
            "● Here is my plan:\n"
            "  1. Yes-man refactor of the auth module\n"
            "  2. Update the tests\n"
            "\n"
            "Do you want to proceed?\n"
            "  1. Continue without changes\n"
            "  2. No\n"
            "\n esc to cancel · enter to confirm\n"
            "\n esc to cancel · enter to confirm\n"
        )
        self.assertEqual(
            [o.label for o in parse_options(snapshot)],
            ["Continue without changes", "No"],
        )

    def test_prose_cannot_supply_the_fallback_option_one(self):
        # Without this, a prose line beginning "1. Yes..." would be accepted
        # while the real menu's option 1 says something else entirely.
        snapshot = (
            "● Plan:\n"
            "  1. Yes, refactor first\n"
            "  2. Then test\n"
            "\n"
            "Do you want to proceed?\n"
            "  1. Continue without changes\n"
            "  2. No\n"
            "\n esc to cancel · enter to confirm\n"
        )
        self.assertEqual(selected_option(snapshot), "Continue without changes")
        self.assertEqual(classify(snapshot).action, Action.PAUSE)

    def test_horizontal_rules_are_never_options(self):
        options = parse_options(fixture("permission_unnumbered.txt"))
        self.assertTrue(all("───" not in o.label for o in options))

    def test_a_wrapped_echoed_message_above_an_unnumbered_menu_loses(self):
        # The echoed user message also begins with "❯". If it wraps onto a
        # second line it looks like a two-option block, so the menu lower down
        # the screen must win.
        snapshot = (
            "❯ Yes please refactor the config loader and also update the readme\n"
            "  when you are done with that\n"
            "\n"
            "Do you want to proceed?\n"
            " ❯ Yes\n"
            "   No, and tell Claude what to do differently (esc)\n"
            "\n esc to cancel · enter to confirm\n"
        )
        self.assertEqual(
            [o.label for o in parse_options(snapshot)],
            ["Yes", "No, and tell Claude what to do differently (esc)"],
        )

    def test_a_single_line_echoed_message_does_not_hide_the_menu_below_it(self):
        snapshot = (
            "❯ go ahead and do it\n"
            "\n"
            "Do you want to proceed?\n"
            " ❯ Yes\n"
            "   No\n"
            "\n esc to cancel · enter to confirm\n"
        )
        self.assertEqual([o.label for o in parse_options(snapshot)], ["Yes", "No"])

    def test_numbered_prose_does_not_pre_empt_an_unnumbered_menu_below_it(self):
        # The numbered block is higher on screen than the real menu, so the
        # real menu wins even though numbered parsing runs first.
        snapshot = (
            "● Plan:\n"
            "  1. Yes-and pattern for the retry wrapper\n"
            "  2. Add tests\n"
            "\n"
            "Do you want to proceed?\n"
            " ❯ No, and tell Claude what to do differently (esc)\n"
            "   Yes\n"
            "\n esc to cancel · enter to confirm\n"
        )
        decision = classify(snapshot)
        self.assertEqual(decision.action, Action.PAUSE)
        self.assertEqual(
            decision.label, "No, and tell Claude what to do differently (esc)"
        )


class SelectedOptionTest(unittest.TestCase):
    def test_returns_the_cursor_marked_option(self):
        self.assertEqual(
            selected_option(fixture("bash_permission_numbered.txt")), "Yes"
        )

    def test_falls_back_to_option_one_when_no_cursor_is_rendered(self):
        snapshot = (
            "Do you want to proceed?\n"
            "  1. Yes\n"
            "  2. No, and tell Claude what to do differently (esc)\n"
            "\n esc to cancel · enter to confirm\n"
        )
        self.assertEqual(selected_option(snapshot), "Yes")

    def test_returns_none_when_there_is_no_menu(self):
        self.assertIsNone(selected_option(fixture("real_working_not_a_prompt.txt")))


def menu(*options, question="Do you want to proceed?"):
    """A snapshot of a menu, with the footer Claude Code always draws."""
    body = "\n".join(
        f" {'❯' if selected else ' '} {n}. {label}"
        for n, (label, selected) in enumerate(options, start=1)
    )
    return f"{question}\n{body}\n esc to cancel · enter to confirm\n"


class YesOnOfferTest(unittest.TestCase):
    """A menu offering a "Yes" is a permission prompt and is accepted
    anywhere. A menu offering none is a decision and belongs to the human."""

    def test_a_menu_with_a_yes_selected_is_accepted(self):
        snapshot = menu(("Yes", True), ("No", False))
        self.assertEqual(classify(snapshot).action, Action.ACCEPT)

    def test_a_decision_with_no_yes_anywhere_pauses(self):
        snapshot = menu(
            ("Spaces", True), ("Tabs", False),
            question="Tabs or spaces?",
        )
        decision = classify(snapshot)
        self.assertEqual(decision.action, Action.PAUSE)
        self.assertEqual(decision.reason, "a decision with no yes on offer")

    def test_a_yes_offered_but_not_selected_pauses_rather_than_guessing(self):
        # Pressing Enter here would answer "No". Hand it over instead.
        snapshot = menu(("Yes", False), ("No", True))
        decision = classify(snapshot)
        self.assertEqual(decision.action, Action.PAUSE)
        self.assertEqual(decision.reason, "a yes was offered but not selected")

    def test_a_consequential_yes_is_still_accepted(self):
        snapshot = menu(("Yes, I trust this folder", True), ("No", False))
        self.assertEqual(classify(snapshot).action, Action.ACCEPT)

    def test_something_that_is_not_a_menu_is_ignored_not_paused(self):
        # The distinction that keeps the log readable: nothing to decide here,
        # so it must not appear as a decision waiting on the user.
        decision = classify("● Working on it…\n")
        self.assertEqual(decision.action, Action.IGNORE)
        self.assertIsNone(decision.label)


class ClassifyTest(unittest.TestCase):
    def test_plain_yes_is_accepted(self):
        decision = classify(fixture("bash_permission_numbered.txt"))
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(decision.label, "Yes")

    def test_unnumbered_yes_is_accepted(self):
        self.assertEqual(
            classify(fixture("permission_unnumbered.txt")).action, Action.ACCEPT
        )

    def test_consequential_yes_is_accepted_because_any_yes_is_accepted(self):
        # Deliberate, per spec section 6: every Affirmative Option is accepted,
        # including ones that grant trust. This test documents the decision so
        # that changing it is a visible change, not a silent one.
        decision = classify(fixture("permission_trust_folder.txt"))
        self.assertEqual(decision.action, Action.ACCEPT)
        self.assertEqual(decision.label, "Yes, I trust this folder")

    def test_question_prompt_is_paused_for_the_human(self):
        decision = classify(fixture("question_prompt.txt"))
        self.assertEqual(decision.action, Action.PAUSE)
        self.assertEqual(
            decision.label,
            "Run migrations in a transaction and roll back on failure",
        )

    def test_non_prompt_output_is_ignored(self):
        decision = classify(fixture("real_working_not_a_prompt.txt"))
        self.assertEqual(decision.action, Action.IGNORE)
        self.assertIsNone(decision.label)

    def test_yes_match_is_case_insensitive_and_ignores_surrounding_space(self):
        snapshot = "Do you want to proceed?\n ❯ 1.   yes, run it \n   2. No\n esc to cancel · enter to confirm\n"
        self.assertEqual(classify(snapshot).action, Action.ACCEPT)

    def test_a_no_selected_option_is_paused(self):
        snapshot = "Do you want to proceed?\n   1. Yes\n ❯ 2. No\n esc to cancel · enter to confirm\n"
        decision = classify(snapshot)
        self.assertEqual(decision.action, Action.PAUSE)
        self.assertEqual(decision.label, "No")

    def test_word_beginning_with_yes_is_not_an_affirmative(self):
        snapshot = "Do you want to proceed?\n ❯ 1. Yesterday's backup\n   2. No\n esc to cancel · enter to confirm\n"
        self.assertEqual(classify(snapshot).action, Action.PAUSE)

    def test_hyphenated_words_starting_with_yes_are_not_affirmative(self):
        for label in ("Yes-man refactor", "Yes-and pattern for the wrapper"):
            snapshot = f"Do you want to proceed?\n ❯ 1. {label}\n   2. No\n esc to cancel · enter to confirm\n"
            self.assertEqual(classify(snapshot).action, Action.PAUSE, label)

    def test_yes_followed_by_punctuation_is_affirmative(self):
        for label in ("Yes", "Yes, run it", "Yes: proceed"):
            snapshot = f"Do you want to proceed?\n ❯ 1. {label}\n   2. No\n esc to cancel · enter to confirm\n"
            self.assertEqual(classify(snapshot).action, Action.ACCEPT, label)

    def test_every_decision_carries_a_reason(self):
        for name in (
            "bash_permission_numbered.txt",
            "question_prompt.txt",
            "real_working_not_a_prompt.txt",
        ):
            self.assertTrue(classify(fixture(name)).reason, name)


if __name__ == "__main__":
    unittest.main()
