"""Classify a Blocked agent's screen into an Acceptance or a Skip.

This module is the Claude-specific seam described in spec section 3. Herdr
tells us an agent is Blocked but not *what kind* of Prompt is showing, so the
job here is to read the Selected Option out of a detection snapshot and decide
whether it is an Affirmative Option.

Everything here is pure: text in, decision out. No subprocesses, no I/O.
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass

# Claude Code renders the Selected Option with a "❯" cursor. Options are
# usually numbered ("❯ 1. Yes") but not always ("❯ Yes"), so both shapes are
# recognised. Confirmed against herdr's own claude detection manifest.
CURSOR = "❯"

_NUMBERED = re.compile(rf"^\s*(?P<cursor>{CURSOR})?\s*(?P<number>\d+)\.\s+(?P<label>\S.*?)\s*$")
_CURSORED = re.compile(rf"^\s*{CURSOR}\s+(?P<label>\S.*?)\s*$")

# A line of box drawing or dashes separates regions of Claude Code's UI. It is
# never an Option, and it terminates an unnumbered Option block. ─-╿
# is the Box Drawing block.
_RULE = re.compile(r"^\s*[─-╿=_-]{3,}\s*$")

# "Yes" as a whole word. \b alone is not enough: it treats a hyphen as a
# boundary, which would make "Yes-man refactor" affirmative.
_AFFIRMATIVE = re.compile(r"^yes(?![\w'’-])", re.IGNORECASE)

# A menu has alternatives. Requiring two Options is a start, but not enough on
# its own: a two-line message typed into the input box also begins with "❯"
# and also has a second line under it.
_MINIMUM_OPTIONS = 2

# What actually separates a menu from anything else on screen is the
# affordance line Claude Code renders beneath every one of them --
# "Esc to cancel · Tab to amend", "Enter to select · ↑/↓ to navigate".
# herdr's own detection rules key on the same strings.
#
# Without this, a message the user is part-way through typing parses as a
# menu, and one beginning with "Yes" would be submitted on their behalf.
_AFFORDANCE = re.compile(
    r"esc to cancel|enter to confirm|enter to select|to navigate",
    re.IGNORECASE,
)


class Action(enum.Enum):
    ACCEPT = "accept"
    SKIP = "skip"


@dataclass(frozen=True)
class Option:
    """One selectable entry in a Prompt."""

    label: str
    number: int | None = None
    selected: bool = False


@dataclass(frozen=True)
class Decision:
    """What to do about a Blocked agent, and why."""

    action: Action
    label: str | None
    reason: str


@dataclass(frozen=True)
class _Block:
    """A candidate menu and where its last line sits on screen."""

    options: list[Option]
    bottom: int


def parse_options(snapshot: str) -> list[Option]:
    """Read the Prompt's Option list out of a detection snapshot.

    Claude Code's screen holds more than the menu: prose numbered lists, and
    the echoed user message, which is prefixed with the same "❯" cursor the
    menu uses. Both shapes are parsed and the one lowest on screen wins,
    because the menu is always the bottom-most thing drawn.

    Returns an empty list when the snapshot holds no menu, which includes the
    common case of an agent Blocked on something that is not a menu at all.
    """
    if not _AFFORDANCE.search(snapshot):
        return []
    lines = snapshot.splitlines()
    blocks = [
        block
        for block in (_numbered_block(lines), _unnumbered_block(lines))
        if block and len(block.options) >= _MINIMUM_OPTIONS
    ]
    if not blocks:
        return []
    return max(blocks, key=lambda block: block.bottom).options


def _numbered_block(lines: list[str]) -> _Block | None:
    """The numbered menu nearest the bottom of the screen.

    Claude Code prints numbered lists in prose too, so matching every "1." in
    the buffer would let a plan step masquerade as an Option. The menu is
    found by walking up from the bottom and keeping the run of numbers that
    counts down to 1; anything not continuing the run -- prose, wrapped option
    descriptions, horizontal rules -- is passed over.
    """
    matches = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := _NUMBERED.match(line)) and not _RULE.match(line)
    ]
    if not matches:
        return None

    options: list[Option] = []
    bottom = matches[-1][0]
    wanted = int(matches[-1][1]["number"])
    for _, match in reversed(matches):
        if wanted < 1:
            break
        if int(match["number"]) != wanted:
            continue
        options.append(
            Option(
                label=match["label"],
                number=wanted,
                selected=match["cursor"] is not None,
            )
        )
        wanted -= 1
    return _Block(list(reversed(options)), bottom)


def _unnumbered_block(lines: list[str]) -> _Block | None:
    """The cursor-marked block of unnumbered Options nearest the bottom.

    Unnumbered Options carry no marker of their own, so the block is the
    cursor line plus the contiguous non-blank lines beneath it; a blank line
    or a horizontal rule ends it. The search runs bottom-up so that the menu
    wins over the echoed user message higher up the screen. Numbered cursor
    lines are left to `_numbered_block`.
    """
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        match = _CURSORED.match(line)
        if not match or _RULE.match(line) or _NUMBERED.match(line):
            continue
        options = [Option(label=match["label"], selected=True)]
        bottom = index
        for offset, following in enumerate(lines[index + 1:], start=index + 1):
            if not following.strip() or _RULE.match(following):
                break
            options.append(Option(label=following.strip()))
            bottom = offset
        return _Block(options, bottom)
    return None


def selected_option(snapshot: str) -> str | None:
    """The label of the Option that Enter would act on, if there is a menu.

    Falls back to Option 1 when no cursor is rendered: Claude Code always
    pre-selects the first Option.
    """
    options = parse_options(snapshot)
    if not options:
        return None
    for option in options:
        if option.selected:
            return option.label
    for option in options:
        if option.number == 1:
            return option.label
    return None


def is_affirmative(label: str) -> bool:
    """Whether an Option label begins with the word "Yes"."""
    return bool(_AFFIRMATIVE.match(label.strip()))


def prompt_signature(snapshot: str) -> str | None:
    """A stable identifier for the Prompt currently on screen.

    `herdr agent wait` is level-triggered: an unanswered Prompt keeps
    reporting Blocked. Comparing signatures between cycles is what stops the
    same Prompt being decided, pressed and journalled over and over while it
    waits for a human. Returns None when there is no menu.
    """
    options = parse_options(snapshot)
    if not options:
        return None
    joined = "\x1f".join(
        f"{'>' if option.selected else ' '}{option.number}:{option.label}"
        for option in options
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def classify(snapshot: str) -> Decision:
    """Decide whether a Blocked agent's Prompt should be accepted.

    Every Affirmative Option is accepted, whatever follows the word "Yes" --
    including Options that spend money, grant trust, or change settings
    permanently. See spec section 6; this is deliberate.
    """
    label = selected_option(snapshot)
    if label is None:
        return Decision(Action.SKIP, None, "no option list found")
    if is_affirmative(label):
        return Decision(Action.ACCEPT, label, "selected option is affirmative")
    return Decision(Action.SKIP, label, "selected option is not affirmative")
