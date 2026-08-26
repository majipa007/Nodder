#!/usr/bin/env python3
"""Validate that fixtures are faithful by asking herdr's own rule engine.

A fixture is only useful as a test input if herdr classifies it the same way
it would classify the real terminal output it imitates. This script is a dev
tool, not part of the test suite: it requires a running herdr server.

Usage:  python3 tests/validate_fixtures.py
"""

import json
import pathlib
import subprocess
import sys

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# fixture filename -> herdr agent_status we expect herdr to report for it
EXPECTED = {
    "bash_permission_numbered.txt": "blocked",
    "permission_unnumbered.txt": "blocked",
    "permission_trust_folder.txt": "blocked",
    "question_prompt.txt": "blocked",
    # Verbatim captures from a live session; herdr must agree these are the
    # states they were captured in.
    "real_bash_permission.txt": "blocked",
    "real_question_prompt.txt": "blocked",
    "real_working_not_a_prompt.txt": "working",
}


def explain(path: pathlib.Path) -> dict:
    proc = subprocess.run(
        ["herdr", "agent", "explain", "--file", str(path),
         "--agent", "claude", "--json"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def main() -> int:
    failures = 0
    for name, want in sorted(EXPECTED.items()):
        path = FIXTURES / name
        if not path.exists():
            print(f"MISSING  {name}")
            failures += 1
            continue
        result = explain(path)
        got = result.get("state")
        matched = [r["id"] for r in result.get("evaluated_rules", [])
                   if r.get("matched")]
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: want={want} got={got} "
              f"rules={matched or ['-']}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
