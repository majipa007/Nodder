"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

from . import herdr
from .daemon import Supervisor, selectable_targets, watch_cycle
from .herdr import HerdrError
from .journal import DEFAULT_PATH, Journal

BANNER = "⚡ nodder: ON"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nodder",
        description="Answer Claude Code's approval prompts across every agent "
                    "in a herdr session, including panes you are not watching.",
        epilog="Every option beginning with \"Yes\" is accepted, including ones "
               "that spend money or grant trust. Run --dry-run first.",
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
        "--once", metavar="TARGET",
        help="run a single watch cycle against one agent, then exit",
    )
    return parser


def _self_pane() -> str | None:
    return os.environ.get("HERDR_PANE_ID")


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
            print("no prompt appeared", file=sys.stderr)
            return 1
        print(f"{decision.action.name} {decision.label!r} ({decision.reason})")
        return 0

    print(f"{BANNER}{' (dry run)' if args.dry_run else ''} — "
          f"{len(targets)} agent(s), log: {journal.path}", file=sys.stderr)

    supervisor = Supervisor(
        journal=journal, dry_run=args.dry_run, self_pane=self_pane
    )
    try:
        supervisor.run()
    except KeyboardInterrupt:
        supervisor.stop.set()
        print("stopped", file=sys.stderr)
    return 0
