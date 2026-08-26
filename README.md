# Claude Auto Accept

Answers Claude Code's approval prompts across **every** agent in a
[herdr](https://herdr.dev) session — including panes you are not looking at.

Requires Python 3.10+ (stdlib only), herdr 0.8.0+, and a running herdr server.

See [spec.md](./spec.md) for the design and [CONTEXT.md](./CONTEXT.md) for the
vocabulary.

## Usage

```bash
# Show which agents would be watched
python3 -m claude_auto_accept --status

# Decide and log, but never press anything — run this first
python3 -m claude_auto_accept --dry-run --verbose

# Run for real
python3 -m claude_auto_accept --verbose

# Single cycle against one agent, for debugging
python3 -m claude_auto_accept --once w16:p3 --dry-run
```

Stop with Ctrl-C.

| Flag | Effect |
|---|---|
| `--dry-run` | Classify and log, never send Enter |
| `--verbose` | Log every decision to stderr |
| `--status` | List agents and whether each is watched, then exit |
| `--once TARGET` | One watch cycle against one agent, then exit |
| `--log PATH` | Decision log location |

## What it accepts

Enter is pressed whenever the **selected option begins with "Yes"**.

This is deliberately broad. It includes options that spend money, grant trust,
install software, and permanently rewrite `~/.claude/settings.json`:

```
Yes, buy usage credits
Yes, I trust this folder
Yes, trust and add server
Yes, install <plugin>
Yes, set auto mode as my default permission mode
Yes, and don't ask again for <x>
```

Prompts whose selected option is not a "Yes" — most multiple-choice questions,
plan reviews — are left alone for you to answer. herdr already notifies you
that an agent is waiting.

The rule is "the selected option begins with Yes", not "this is a permission
prompt". A model-authored question whose first option happens to read
`Yes, drop it` **will** be answered.

Because every "Yes" is accepted, **the log is the only record of what was
pressed**:

```
~/.local/state/claude_auto_accept/log

26/08/2026 18:12:37  w16:p3  ACCEPT  "Yes"
26/08/2026 18:14:02  w16:p3  SKIP    "Spaces"
```

The daemon never acts on its own pane (`$HERDR_PANE_ID`).

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

Fixtures under `tests/fixtures/` include verbatim captures from a live Claude
Code session. `tests/validate_fixtures.py` checks each fixture against herdr's
own detection rules, so the synthetic ones stay faithful to real output — it
needs a running herdr server and is not part of the suite.

## Why this exists

Claude Code already ships `--permission-mode bypassPermissions`. This tool is a
deliberate reimplementation, built Claude-first as a step toward a
tool-agnostic version: herdr recognises 21 agent kinds and its `blocked` state
and `send-keys` are kind-agnostic, so only `prompts.py` is Claude-specific.
