# nodder

**Nods yes to your coding agents' permission prompts — in every pane,
including the ones you aren't looking at. Pauses on the real decisions.**

You run several Claude Code sessions across a [herdr](https://herdr.dev)
workspace. Each one stops and waits for you to press Enter on
*"Do you want to proceed?"*. `nodder` watches all of them and presses it for
you.

```
$ nodder run

⚡ nodder                                  3 agents  ·  59 yes  ·  1 need you
ACCEPTED  last 60 min                                              peak 14
 ⠀⠀⠀⣀⣀⡀⠀⠀⠀⠀⠀⣠⣴⣶⣶⣆⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣶⣿⣷⣦⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
 ⠀⢀⣴⣿⣿⣷⣦⣤⣀⣤⣶⣿⣿⣿⣿⣿⣧⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣰⣿⣿⣿⣿⣿⣿⣤⣀⣀⣀⣤⣶⣶⣦⣀⠀⠀⠀⠀⠀⠀⠀⢀⣠⣤⣀⡀
 ⣤⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣤⠀⠀⠀⠀⠀⠀⣀⣀⡀⠀⠀⢀⣠⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⠀⠀⠀⠀⠀⢀⣼⣿⣿⣿⣿⣿
 ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣤⣠⣤⣴⣶⣦⣤⠀⠀⣠⣶⣿⣿⣷⣶⣶⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⡀⠀⢀⣼⣿⣿⣿⣿⣿⣿
 └ 60m ago ───────────────────────────────────────────────────────── now┘

WHERE                 STATE        YES  PAUSED            LAST 60m
  sluice/p2           blocked        4       1            ·············▃▁·▁ NEEDS YOU
▸ autoAccept/p1       working       43       5            ▁·▄▆▃▅▇▄▂·▃█▆▄▂·▂▃
  sluice/p4           idle          12       0            ··▂▁··▁▂▁··▂▁··▁▂▁

RECENT (last 10)
  20:54:25  autoAccept/p1      ACCEPT Yes
  20:48:43  sluice/p2          PAUSE  Spaces
  20:48:34  autoAccept/p1      ACCEPT Yes, run it
  ...

 q quit    r refresh
```

The chart is braille — eight addressable dots per character cell, so it has
four times the vertical resolution of a block-character chart. It grows into
whatever space the table and the recent list don't need.

Panes are named by workspace and pane number, `▸` marks the one you're focused
on, and `YES` / `PAUSED` are that pane's running totals. Blocked panes sort to
the top.

---

## Install

```bash
git clone https://github.com/majipa007/Nodder.git nodder
cd nodder
make install
```

That runs the tests, builds a single 40 KB self-contained executable, and drops
it at `~/.local/bin/nodder`. There are no dependencies to resolve — the whole
project is Python standard library.

No `make`? `./install.sh` does the same thing.

<details>
<summary>Other install options</summary>

```bash
make install PREFIX=/usr/local   # somewhere else
make service                     # install + run as a systemd user service
pipx install .                   # if you prefer pipx
make uninstall                   # remove it again
```

Run `make` on its own to list every target.
</details>

### Requirements

| | |
|---|---|
| Python | 3.10+ (standard library only) |
| [herdr](https://herdr.dev) | 0.8.0+, with a server running |
| OS | Linux — developed on Arch, tested on Debian/Ubuntu |

`make check` verifies these before installing.

---

## Use

```bash
nodder run               # ← the one you want: daemon + live dashboard
```

Everything else:

```bash
nodder --status          # which agents would be watched
nodder --waiting         # what needs a decision from you right now
nodder --dry-run -v      # decide and record, but press nothing
nodder run --plain       # daemon with a plain stderr log, no dashboard
```

Quit the dashboard with `q`, or stop a plain run with Ctrl-C. To keep it
running in the background instead, `make service`.

**Run `--dry-run` first.** It exercises the whole pipeline and writes to the
same database, but never touches your agents. Read the decisions back, and if
you agree with them, drop the flag.

| Command / flag | Effect |
|---|---|
| **`run`** | **Start the daemon with the live dashboard** |
| `--plain` | With `run`, log to stderr instead of drawing the dashboard |
| `--dry-run` | Classify and record, never send Enter |
| `--verbose`, `-v` | Log every decision to stderr |
| `--status` | List agents and whether each is watched, then exit |
| `--waiting`, `-w` | Show the decisions currently waiting for you, then exit |
| `--once TARGET` | One watch cycle against one agent, then exit |
| `--log PATH` | Use a different database |

---

## What it does with a prompt

Three outcomes, and the split between them is the whole point of the tool.

### A menu offering a "Yes" → **accepted**, in every pane

Focused or not, watched or not. That rule is deliberately broad: Claude Code has
30+ options beginning with "Yes" and nodder takes all of them.

```
Yes, buy usage credits                            → spends money
Yes, I trust this folder                          → security boundary
Yes, trust and add server                         → adds an MCP server
Yes, install <plugin>                             → installs software
Yes, I accept                                     → accepts terms
Yes, set auto mode as my default permission mode  → rewrites settings.json
Yes, and don't ask again for <x>                  → writes to permissions.allow
```

### A real decision → **paused**, in every pane

If no "Yes" is on offer, it isn't a permission prompt — it's a choice that is
yours to make:

```
Which indentation style?
 ❯ 1. Spaces
   2. Tabs
```

nodder sends nothing. The prompt stays exactly where it is and waits for you.
A pause is a hand-back, not a refusal. It's also paused when a "Yes" exists but
the cursor is sitting on something else, because pressing Enter there would
answer the wrong thing.

See everything currently waiting on you:

```bash
$ nodder --waiting

w2:p4  refactor the auth module
     ❯ 1. Spaces
       2. Tabs
     → a decision with no yes on offer

1 decision(s) waiting.
```

### Not a menu → **ignored**, and not recorded

herdr reports a pane `blocked` whenever the words "do you want to" and a `❯`
appear anywhere in its buffer, so it fires on ordinary transcripts. There is
nothing to decide, so nodder says nothing and records nothing — otherwise the
noise buries the pauses that actually need you.

### It never touches its own pane

`nodder` skips the pane it is running in (`$HERDR_PANE_ID`), so a daemon started
from inside a Claude session cannot answer its own prompts. Every *other* pane
is fair game — there is no opt-in.

---

## The record

Because every "Yes" is accepted, the decision log is the only account of what
was pressed. It lives in SQLite at `~/.local/state/nodder/decisions.db`, and
stores the whole menu, not just the chosen option — a pause is only readable
after the fact if you can see what was on offer.

```
26/08/2026 18:12:37  w16:p3  ACCEPT  "Yes"
26/08/2026 18:14:02  w16:p3  PAUSE   "Spaces"
```

```bash
sqlite3 ~/.local/state/nodder/decisions.db \
  "SELECT at, target, outcome, label FROM decisions ORDER BY id DESC LIMIT 20;"
```

---

## How it works

herdr already owns the terminal, recognises agents, tracks their lifecycle, and
exposes a plain-text snapshot of what each pane is showing. So `nodder` spawns
no PTY, emulates no terminal, and parses no ANSI. It is a supervisor around four
herdr commands:

```
discover   herdr agent list                                  polled every 2s
watch      herdr agent wait <a> --until blocked              one thread per agent
inspect    herdr agent read <a> --source detection           plain text, already rendered
act        herdr agent send-keys <a> enter
```

`blocked` means, in herdr's own words, *"an approval **or question** UI"* — it
does not say which. Telling those apart is `nodder`'s entire job, and the only
Claude-specific code in the project is
[`nodder/prompts.py`](./nodder/prompts.py). herdr recognises 21 agent kinds and
`blocked`/`send-keys` are kind-agnostic, so supporting codex or gemini means
teaching that one module their menus.

Two things that are easy to get wrong, both learned the hard way:

- **`herdr agent wait` is level-triggered.** An unanswered prompt keeps
  reporting `blocked`, so a naive loop re-presses and re-records it forever.
  Each watcher carries a signature of the prompt it last handled — cleared the
  moment the prompt leaves the screen, so a repeated command is still two
  decisions.
- **A dead pane is not a timeout.** `agent_not_found` returns in ~6 ms; treating
  it as a timeout spins a watcher at full CPU.

[`spec.md`](./spec.md) has the full design and [`CONTEXT.md`](./CONTEXT.md)
defines the vocabulary.

---

## Why this exists

Claude Code already ships `--permission-mode bypassPermissions`. This does not
replace it — it is a deliberate reimplementation, built Claude-first as a step
toward something tool-agnostic, and it reaches sessions you started normally,
across every pane, without relaunching anything.

---

## Development

```bash
make test     # 170 tests, standard library unittest
make run      # run from the source tree
make clean
```

Fixtures in `tests/fixtures/` include verbatim captures from a live Claude Code
session. `tests/validate_fixtures.py` replays each one through herdr's *own*
detection rules (`herdr agent explain --file`) so the synthetic fixtures cannot
drift from what herdr really classifies. It needs a running herdr server and is
not part of the suite:

```bash
python3 tests/validate_fixtures.py
```

---

## License

MIT — see [LICENSE](./LICENSE).
