"""Watch every Claude agent in a herdr session and answer its Approval Prompts.

Discovery is polled, because herdr has no "an agent appeared" event. Each agent
then gets a thread parked in a blocking `herdr agent wait --until blocked`.

That wait is *level*-triggered, not edge-triggered: an unanswered Prompt keeps
reporting Blocked, so each watcher also carries the signature of the Prompt it
last handled and does nothing while that Prompt is still on screen.

Three outcomes, and the difference between them is the point of the tool:

    ACCEPT  a menu offering a "Yes"  -> press Enter, in any pane
    PAUSE   a real decision, no Yes  -> leave it, and say so loudly
    IGNORE  not a menu at all        -> say nothing, record nothing
"""

from __future__ import annotations

import logging
import threading

from . import herdr
from .herdr import HerdrError, Wait
from .journal import Journal
from .prompts import Action, Decision, classify, parse_options, prompt_signature

log = logging.getLogger("nodder")

#: Agent kind this tool understands. `prompts.classify` is written against
#: Claude Code's UI; other kinds render their menus differently.
CLAUDE = "claude"

#: How long a watcher parks waiting for a Prompt before looping.
#:
#: This bounds how long the daemon stays blind after Claude restarts in a pane
#: it was already watching: the old watcher is retired but cannot be replaced
#: until it wakes from this wait and exits, because a target that still has an
#: entry in `_watchers` is not re-spawned. Keep it short.
BLOCK_WAIT_MS = 15_000

#: How long to wait for an answered Prompt to clear. On a Pause this
#: expires repeatedly while the Prompt waits for the user, as intended.
SETTLE_WAIT_MS = 30_000

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
        if agent.status != "blocked":
            continue
        try:
            snapshot = client.read_detection(agent.pane_id)
        except HerdrError:
            continue
        decision = classify(snapshot)
        if decision.action is Action.PAUSE:
            waiting.append((agent, decision, parse_options(snapshot)))
    return waiting


def watch_cycle(
    target: str,
    client,
    journal: Journal,
    dry_run: bool,
    last_signature: str | None = None,
) -> tuple[Decision | None, str | None]:
    """Wait for one Prompt on `target`, decide about it, and record it.

    Returns the decision and the signature of the Prompt that produced it, to
    be passed back in on the next call. Either may be None: the wait can
    expire with no Prompt, and a Prompt already handled is left alone.

    Raises HerdrError if herdr fails; the caller decides whether that is fatal.
    """
    if client.wait_for(target, ("blocked",), BLOCK_WAIT_MS) is not Wait.REACHED:
        # Not Blocked, so any Prompt we were suppressing has gone.
        return None, None

    snapshot = client.read_detection(target)
    signature = prompt_signature(snapshot)

    if signature is not None and signature == last_signature:
        # The same unanswered Prompt, still on screen. `herdr agent wait` is
        # level-triggered, so it keeps reporting Blocked until a human
        # answers; acting again would re-press Enter and fill the journal
        # with duplicates.
        return None, _remember(client, target, signature)

    decision = classify(snapshot)

    if decision.action is Action.IGNORE:
        # Not a menu, so there is no decision here to make or to record.
        # herdr's fallback rule fires on ordinary transcripts; recording those
        # would bury the Pauses that actually need the human.
        log.debug("%s ignored (%s)", target, decision.reason)
        return None, _remember(client, target, signature)

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

    return decision, _remember(client, target, signature)


def _remember(client, target: str, signature: str) -> str | None:
    """Wait for the Prompt to clear, and say whether to keep suppressing it.

    A signature suppresses only a Prompt that is *still on screen*. Once the
    agent leaves Blocked the Prompt is gone, and the next one -- even an
    identical-looking one, because Claude re-runs commands -- is a new
    decision that must be acted on.

    Forgetting this is what made the daemon accept one Prompt and then go
    silent for every later Prompt of the same shape.
    """
    cleared = client.wait_for(target, client.UNBLOCKED_STATES, SETTLE_WAIT_MS)
    return None if cleared is Wait.REACHED else signature


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
    ) -> None:
        self.client = client
        self.journal = journal or Journal()
        self.dry_run = dry_run
        self.self_pane = self_pane
        self.discovery_interval = discovery_interval
        self.stop = threading.Event()
        self._watchers: dict[str, _Watcher] = {}

    def run(self) -> None:
        """Poll for agents until stopped, keeping watchers in step."""
        while not self.stop.is_set():
            try:
                targets = selectable_targets(
                    self.client.list_agents(), self.self_pane
                )
            except HerdrError as exc:
                # Keep existing watchers; a momentary discovery failure is not
                # evidence that every agent has gone.
                log.warning("discovery failed: %s", exc)
                self.stop.wait(self.discovery_interval)
                continue

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

        They may be parked in a blocking wait, so they stop at the top of
        their next loop rather than immediately.
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
        signature: str | None = None
        while not self.stop.is_set() and not watcher.retired.is_set():
            try:
                _, signature = watch_cycle(
                    watcher.target, self.client, self.journal, self.dry_run,
                    last_signature=signature,
                )
            except HerdrError as exc:
                # Usually the pane closed while we were parked on it. End the
                # thread; discovery re-adds the agent if it is still there.
                log.info("stopped watching %s: %s", watcher.target, exc)
                return
