"""Watch every Claude agent in a herdr session and answer its Approval Prompts.

Discovery is polled, because herdr has no "an agent appeared" event. Each agent
then gets a thread that reads its screen every second.

The screen is read whatever herdr says. Waiting for herdr's `blocked` missed
Prompts: it can call an agent `working` while a Prompt is up, and nothing then
looks at that Prompt until herdr changes its mind. Only idle and done agents
are skipped -- a picker the human opened there (/model, /resume) is drawn just
like a menu, and is theirs to answer.

Every look sees an unanswered Prompt again, so each watcher carries the one it
last acted on and leaves it alone while it is still on screen.

Three outcomes, and the difference between them is the point of the tool:

    ACCEPT  a menu offering a "Yes"  -> press Enter, in any pane
    PAUSE   a real decision, no Yes  -> leave it, and say so loudly
    IGNORE  not a menu at all        -> say nothing, record nothing
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from . import herdr
from .herdr import HerdrError
from .journal import Journal
from .prompts import Action, Decision, classify, parse_options, prompt_signature

log = logging.getLogger("nodder")

#: Agent kind this tool understands. `prompts.classify` is written against
#: Claude Code's UI; other kinds render their menus differently.
CLAUDE = "claude"

#: Seconds between looks at each watched agent's screen.
SCAN_INTERVAL = 1.0

#: herdr states whose screens are read. Not idle or done: see the module notes.
SCANNED_STATES = ("blocked", "working")

#: Seconds an accepted Prompt may stay on screen before Enter is pressed
#: again. Well above any redraw lag, so a slow screen is not taken for a lost
#: Enter.
REPRESS_AFTER = 10.0

#: Seconds between `herdr agent list` polls for appearing/leaving agents.
DISCOVERY_INTERVAL = 2.0


def selectable_targets(
    agents: list[herdr.Agent], self_pane: str | None = None
) -> set[str]:
    """Pane IDs this daemon should watch.

    Excludes the daemon's own pane, so a daemon started from inside a Claude
    session never answers its own Prompts.
    """
    return {
        agent.pane_id
        for agent in agents
        if agent.kind == CLAUDE and agent.pane_id != self_pane
    }


def pending_decisions(client, self_pane: str | None = None) -> list[tuple]:
    """Every agent sitting on a decision that needs the human, right now.

    Answers "which of my panes are waiting on me?" by looking at live state
    rather than at history, so it is correct even if the daemon is not
    running. Returns (Agent, Decision, options) newest state first.
    """
    waiting = []
    for agent in client.list_agents():
        if agent.kind != CLAUDE or agent.pane_id == self_pane:
            continue
        if agent.status not in SCANNED_STATES:
            continue
        try:
            snapshot = client.read_detection(agent.pane_id)
        except HerdrError:
            continue
        decision = classify(snapshot)
        if decision.action is Action.PAUSE:
            waiting.append((agent, decision, parse_options(snapshot)))
    return waiting


@dataclass(frozen=True)
class Seen:
    """The Prompt a watcher last acted on, and when."""

    signature: str
    action: Action
    at: float


def watch_cycle(
    target: str,
    client,
    journal: Journal,
    dry_run: bool,
    last: Seen | None = None,
    now=time.monotonic,
) -> tuple[Decision | None, Seen | None]:
    """Look at `target` once, and decide about any new Prompt on screen.

    Returns the decision, or None when there was nothing new to decide, and
    what to pass back in as `last` on the next look.

    Raises HerdrError if herdr fails; the caller decides whether that is fatal.
    """
    snapshot = client.read_detection(target)
    signature = prompt_signature(snapshot)
    if signature is None:
        # No menu, so whatever was last acted on has gone. The next Prompt --
        # even an identical one, because Claude re-runs commands -- is a new
        # decision.
        return None, None

    at = now()
    if last is not None and last.signature == signature:
        # Still on screen. A Pause waits for the human however long that
        # takes. A Yes still up REPRESS_AFTER after Enter means the Enter did
        # not land, or an identical Prompt took its place: press again.
        # ponytail: a UI frozen for longer than REPRESS_AFTER gets a second,
        # unread Enter. Raise REPRESS_AFTER if that is ever seen.
        enter_lost = (last.action is Action.ACCEPT and not dry_run
                      and at - last.at >= REPRESS_AFTER)
        if not enter_lost:
            return None, last

    decision = classify(snapshot)
    seen = Seen(signature, decision.action, at)

    if decision.action is Action.IGNORE:
        log.debug("%s ignored (%s)", target, decision.reason)
        return None, seen

    if decision.action is Action.ACCEPT and not dry_run:
        client.send_enter(target)

    # The whole menu is recorded, not just the Option that was chosen: a Pause
    # is only readable after the fact if you can see what was on offer.
    journal.record(
        target, decision, signature=signature, options=parse_options(snapshot)
    )

    if decision.action is Action.PAUSE:
        log.warning("%s NEEDS YOU — %s", target, decision.label)
    else:
        log.info("%s %-6s %s", target, decision.action.name, decision.label)

    return decision, seen


class _Watcher:
    """One agent's watcher thread and its private stop flag."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.retired = threading.Event()
        self.thread: threading.Thread | None = None

    def retire(self) -> None:
        self.retired.set()


class Supervisor:
    """Keeps one watcher thread alive per Claude agent."""

    def __init__(
        self,
        client=herdr,
        journal: Journal | None = None,
        dry_run: bool = False,
        self_pane: str | None = None,
        discovery_interval: float = DISCOVERY_INTERVAL,
        scan_interval: float = SCAN_INTERVAL,
    ) -> None:
        self.client = client
        self.journal = journal or Journal()
        self.dry_run = dry_run
        self.self_pane = self_pane
        self.discovery_interval = discovery_interval
        self.scan_interval = scan_interval
        self.stop = threading.Event()
        self._watchers: dict[str, _Watcher] = {}
        # Each agent's herdr state as of the last discovery. Replaced whole,
        # never mutated, so watchers can read it without a lock.
        self._statuses: dict[str, str] = {}

    def run(self) -> None:
        """Poll for agents until stopped, keeping watchers in step."""
        while not self.stop.is_set():
            try:
                agents = self.client.list_agents()
            except HerdrError as exc:
                # Keep existing watchers; a momentary discovery failure is not
                # evidence that every agent has gone.
                log.warning("discovery failed: %s", exc)
                self.stop.wait(self.discovery_interval)
                continue

            self._statuses = {agent.pane_id: agent.status for agent in agents}
            targets = selectable_targets(agents, self.self_pane)
            self._retire(targets)
            self._reap()
            for target in targets - self._watchers.keys():
                self._spawn(target)

            self.stop.wait(self.discovery_interval)

        for watcher in list(self._watchers.values()):
            watcher.retire()
        for watcher in list(self._watchers.values()):
            watcher.thread.join(timeout=2.0)

    def _spawn(self, target: str) -> None:
        watcher = _Watcher(target)
        watcher.thread = threading.Thread(
            target=self._watch, args=(watcher,), name=f"watch:{target}", daemon=True
        )
        self._watchers[target] = watcher
        log.info("watching %s", target)
        watcher.thread.start()

    def _retire(self, targets: set[str]) -> None:
        """Ask watchers whose agent has left to finish.

        One part-way through reading its screen finishes that read first.
        """
        for target, watcher in self._watchers.items():
            if target not in targets and not watcher.retired.is_set():
                log.info("%s has gone; retiring its watcher", target)
                watcher.retire()

    def _reap(self) -> None:
        for target, watcher in list(self._watchers.items()):
            if not watcher.thread.is_alive():
                del self._watchers[target]

    def _watch(self, watcher: "_Watcher") -> None:
        """Thread body: answer Prompts on one agent until it goes away."""
        last: Seen | None = None
        while not self.stop.is_set() and not watcher.retired.is_set():
            if self._statuses.get(watcher.target) in SCANNED_STATES:
                try:
                    _, last = watch_cycle(
                        watcher.target, self.client, self.journal, self.dry_run,
                        last=last,
                    )
                except HerdrError as exc:
                    # Usually the pane closed. End the thread; discovery
                    # re-adds the agent if it is still there.
                    log.info("stopped watching %s: %s", watcher.target, exc)
                    return
            watcher.retired.wait(self.scan_interval)
