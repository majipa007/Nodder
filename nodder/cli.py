"""Command line entry point."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import pathlib
import sys
import threading

from . import herdr, ui
from .daemon import (
    Supervisor,
    pending_decisions,
    selectable_targets,
    watch_cycle,
)
from .herdr import HerdrError
from .journal import DEFAULT_PATH, STATE_DIR, Journal

BANNER = "⚡ nodder: ON"

#: Held by the running daemon for as long as it runs.
LOCK_PATH = STATE_DIR / "daemon.lock"


def single_instance(path: pathlib.Path = LOCK_PATH):
    """Take the daemon lock: the open file holding it, or None if it is taken.

    Every daemon watches every pane, so a second one adds nothing but a second
    Enter per Prompt -- and that Enter lands, unread, on whichever Prompt comes
    next, which may be a decision that should have been paused. The kernel
    drops the lock when the process exits, however it exits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nodder",
        description="Answer Claude Code's approval prompts across every agent "
                    "in a herdr session, including panes you are not watching.",
        epilog="Every option beginning with \"Yes\" is accepted, including ones "
               "that spend money or grant trust. Run --dry-run first.",
    )
    parser.add_argument(
        "command", nargs="?", choices=["run"], default=None,
        help="`run` starts the daemon with the live dashboard",
    )
    parser.add_argument(
        "--plain", action="store_true",
        help="with `run`, log to stderr instead of drawing the dashboard",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="decide and log, but never press Enter",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="log every decision to stderr",
    )
    parser.add_argument(
        "--log", type=pathlib.Path, default=DEFAULT_PATH,
        help=f"decision log path (default: {DEFAULT_PATH})",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="list the agents that would be watched, then exit",
    )
    parser.add_argument(
        "--waiting", "-w", action="store_true",
        help="show the decisions currently waiting for you, then exit",
    )
    parser.add_argument(
        "--once", metavar="TARGET",
        help="look at one agent's screen once, act on any Prompt, then exit",
    )
    return parser


def _self_pane() -> str | None:
    return os.environ.get("HERDR_PANE_ID")


def run() -> None:
    """Entry point for the built executable.

    zipapp calls its entry point and discards the result, so `main` alone
    exited 0 even when it failed.
    """
    sys.exit(main())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose or args.once else logging.WARNING,
        format="[auto-accept] %(message)s",
        stream=sys.stderr,
    )

    try:
        agents = herdr.list_agents()
    except HerdrError as exc:
        print(f"cannot reach herdr: {exc}", file=sys.stderr)
        return 1

    self_pane = _self_pane()
    targets = selectable_targets(agents, self_pane)

    if args.waiting:
        waiting = pending_decisions(herdr, self_pane)
        if not waiting:
            print("nothing waiting on you.")
            return 0
        for agent, decision, options in waiting:
            print(f"\n{agent.pane_id}  {agent.title}")
            for option in options:
                mark = "❯" if option.selected else " "
                number = f"{option.number}. " if option.number else ""
                print(f"    {mark} {number}{option.label}")
            print(f"    → {decision.reason}")
        print(f"\n{len(waiting)} decision(s) waiting. "
              f"Answer them in the pane, or `herdr agent focus <pane>`.")
        return 0

    if args.status:
        for agent in agents:
            mark = "watch " if agent.pane_id in targets else "skip  "
            print(f"{mark} {agent.pane_id:<10} {agent.kind:<8} "
                  f"{agent.status:<8} {agent.title}")
        return 0

    journal = Journal(args.log)

    if args.once:
        try:
            decision, _ = watch_cycle(args.once, herdr, journal, args.dry_run)
        except HerdrError as exc:
            print(f"herdr failed: {exc}", file=sys.stderr)
            return 1
        if decision is None:
            print("no prompt on screen", file=sys.stderr)
            return 1
        print(f"{decision.action.name} {decision.label!r} ({decision.reason})")
        return 0

    lock = single_instance()    # held, by this reference, until main returns
    if lock is None:
        print("nodder is already running; one daemon watches every pane.",
              file=sys.stderr)
        return 1

    supervisor = Supervisor(
        journal=journal, dry_run=args.dry_run, self_pane=self_pane
    )

    # The dashboard owns the screen, so the two cannot both be on. `run`
    # draws it; every other invocation keeps the plain stderr log.
    with_dashboard = args.command == "run" and not args.plain

    if not with_dashboard:
        print(f"{BANNER}{' (dry run)' if args.dry_run else ''} — "
              f"{len(targets)} agent(s), db: {journal.path}", file=sys.stderr)
        try:
            supervisor.run()
        except KeyboardInterrupt:
            supervisor.stop.set()
            print("stopped", file=sys.stderr)
        return 0

    logging.disable(logging.CRITICAL)   # nothing may write over the screen
    worker = threading.Thread(target=supervisor.run, name="daemon", daemon=True)
    worker.start()
    try:
        ui.run(journal, self_pane=self_pane, dry_run=args.dry_run)
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop.set()
        worker.join(timeout=3)
    return 0
