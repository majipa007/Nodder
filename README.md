<h1 align="center">nodder</h1>

<p align="center">
  <em>It presses Yes. It leaves the real decisions to you.</em>
</p>

<p align="center">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-black">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-black">
  <img alt="zero dependencies" src="https://img.shields.io/badge/dependencies-0-black">
  <img alt="herdr 0.8+" src="https://img.shields.io/badge/herdr-0.8%2B-black">
</p>

Run six Claude Code sessions across a [herdr](https://herdr.dev) workspace and
five of them are sitting on *"Do you want to proceed?"* while you look at the
sixth. nodder watches all of them.

```
$ nodder run

⚡ nodder                        3 agents  ·  312 yes all-time  ·  1 need you
ACCEPTED  last 60 min                                              peak 14
 ⠀⠀⠀⣀⣀⡀⠀⠀⠀⠀⠀⣠⣴⣶⣶⣆⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣶⣿⣷⣦⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
 ⠀⢀⣴⣿⣿⣷⣦⣤⣀⣤⣶⣿⣿⣿⣿⣿⣧⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣰⣿⣿⣿⣿⣿⣿⣤⣀⣀⣀⣤⣶⣶⣦⣀⠀⠀⠀⠀⠀⠀⠀⢀⣠⣤⣀⡀
 ⣤⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣤⠀⠀⠀⠀⠀⠀⣀⣀⡀⠀⠀⢀⣠⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⠀⠀⠀⠀⠀⢀⣼⣿⣿⣿⣿⣿
 ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣤⣠⣤⣴⣶⣦⣤⠀⠀⣠⣶⣿⣿⣷⣶⣶⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⡀⠀⢀⣼⣿⣿⣿⣿⣿⣿
 └ 60m ago ───────────────────────────────────────────────────────── now┘

PER PANE  ·  last 60 min
WHERE                 STATE        YES  PAUSED            ACTIVITY
  sluice/p2           blocked        4       1            ·············▃▁·▁ NEEDS YOU
▸ autoAccept/p1       working       43       0            ▁·▄▆▃▅▇▄▂·▃█▆▄▂·▂▃
  sluice/p4           idle          12       0            ··▂▁··▁▂▁··▂▁··▁▂▁

RECENT (last 10)
  20:54:25  autoAccept/p1      ACCEPT Yes
  20:48:43  sluice/p2          PAUSE  Spaces

 q quit    r refresh
```

## Install

```bash
git clone https://github.com/majipa007/Nodder.git nodder
cd nodder
make install
```

Tests, then a single 44 KB executable at `~/.local/bin/nodder`. Nothing to
resolve — it is standard library all the way down. No `make`? `./install.sh`.

## Use

```bash
nodder run            # daemon + dashboard
nodder --waiting      # what needs you right now
nodder --dry-run -v   # decide everything, press nothing
```

| | |
|---|---|
| `run` | Daemon with the live dashboard |
| `--waiting` `-w` | Decisions currently waiting on you |
| `--status` | Which agents would be watched |
| `--dry-run` | Classify and record, never press |
| `--plain` | With `run`, log to stderr instead of drawing |
| `--once TARGET` | One cycle against one agent |

`make service` runs it under systemd instead.

## What it does

| On screen | nodder |
|---|---|
| A menu offering a **"Yes"** | Presses Enter. Every pane, focused or not. |
| A real decision, **no "Yes"** | Leaves it up, says `NEEDS YOU` |
| Not a menu | Nothing, and records nothing |

A pause is a hand-back, not a refusal — the prompt stays exactly where it is.
It never touches the pane it runs in.

## It accepts every Yes

Including the ones you might not want:

```
Yes, buy usage credits                            spends money
Yes, I trust this folder                          security boundary
Yes, trust and add server                         adds an MCP server
Yes, install <plugin>                             installs software
Yes, set auto mode as my default permission mode  rewrites settings.json
```

The rule is *"the selected option begins with Yes"*, not *"this is a permission
prompt"*. A model-authored question whose first option reads `Yes, drop it`
**will** be answered. Narrow it in [`prompts.py`](./nodder/prompts.py) — it's
one regex.

So the record matters. It is the only account of what was pressed:

```bash
sqlite3 ~/.local/state/nodder/decisions.db \
  "SELECT at, target, outcome, label FROM decisions ORDER BY id DESC LIMIT 20;"
```

Whole menus are stored, not just the chosen option — a pause is unreadable
after the fact if you can't see what it declined.

## How it works

herdr already owns the terminal, recognises agents, and hands out a rendered
plain-text snapshot of any pane. So there is no PTY here, no terminal
emulation, no ANSI parsing — four commands and a supervisor:

```
herdr agent list                          discover, every 2s
herdr agent wait <a> --until blocked      one thread per agent
herdr agent read <a> --source detection
herdr agent send-keys <a> enter
```

`blocked` means, in herdr's words, *"an approval **or question** UI"*. Telling
those two apart is nodder's whole job, and the only Claude-specific code is
[`prompts.py`](./nodder/prompts.py). herdr knows 21 agent kinds and
`blocked`/`send-keys` are kind-agnostic, so codex support is one module.

[`spec.md`](./spec.md) has the design, including the two things that cost most
to get right: `agent wait` is level-triggered, and a dead pane is not a timeout.

## Why

Claude Code ships `--permission-mode bypassPermissions`. This is a deliberate
reimplementation, built Claude-first toward something tool-agnostic, that
reaches sessions you started normally, across every pane, without relaunching
anything.

## Development

```bash
make test    # 172 tests, stdlib unittest
make run
```

Fixtures include verbatim captures from a live session.
`tests/validate_fixtures.py` replays each through herdr's *own* detection rules
so the synthetic ones can't drift. Needs a running herdr server; not part of
the suite.

## License

MIT
