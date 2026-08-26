# nodder — Technical Spec

_Last revised: 26/08/2026_

Terminology in this document is defined in [CONTEXT.md](./CONTEXT.md).

---

## 1. Overview

**nodder** is a Python 3 daemon that watches every Agent in a
[Herdr](https://herdr.dev) session and answers Approval Prompts on the user's
behalf — including in Panes the user is not currently looking at.

It requires no modification to Claude Code and no modification to Herdr. It
drives the `herdr` CLI only.

---

## 2. Goal

Remove repetitive permission prompts during Claude Code sessions, across all
Panes at once.

---

## 3. Why this exists

Claude Code already ships equivalent functionality:

```bash
claude --permission-mode bypassPermissions
claude --dangerously-skip-permissions
```

plus `permissions.allow` in `settings.json` and `PreToolUse` hooks.

This tool is built anyway, deliberately, as a **Claude-specific first step
toward a tool-agnostic auto-accepter**. Herdr recognises multiple agent kinds
(`herdr agent start --kind ...`) and its `blocked` state and `send-keys` are
kind-agnostic, so the generalisation is expected to be close to free.

**Design consequence:** the prompt-inspection step is a replaceable seam, not
the product. Everything Claude-specific lives behind one function.

---

## 4. Architecture

Herdr owns the terminal. This tool never spawns a PTY.

```text
herdr session
     │
     ├── w1:p1  claude   ─┐
     ├── w1:p2  claude    ├── herdr tracks lifecycle: idle / working / blocked / done
     └── w1:p3  codex    ─┘
             │
             ▼
         nodder (daemon)
             │
   ┌─────────┴─────────┐
   │                   │
discover            watch (one thread per Agent)
`herdr agent list`  `herdr agent wait <a> --until blocked`
every ~2s                   │
                            ▼
                    `herdr agent read <a> --source detection`
                            │
                            ▼
                 Is a "Yes" offered and selected?
                    │                      │
                   yes                     no
                    │                      │
                    ▼                      ▼
        `herdr agent send-keys      Pause — leave it up for
              <a> enter`            the user; Herdr already
                    │               notifies on blocked
                    └──────────┬───────────┘
                               ▼
                          append to log
```

---

## 5. Herdr CLI surface used

| Purpose | Command |
|---|---|
| Discover Agents | `herdr agent list` |
| Wait for a Prompt | `herdr agent wait <target> --until blocked [--timeout <ms>]` |
| Inspect the Prompt | `herdr agent read <target> --source detection --lines 40` |
| Accept | `herdr agent send-keys <target> enter` |

`--source detection` returns Herdr's plain-text bottom-buffer snapshot. There is
no ANSI stripping, no rolling byte buffer, and no VT emulation in this tool.

Verified against **herdr 0.8.0** and **Claude Code 2.1.241**.

---

## 6. Detection

Herdr performs detection. This tool performs **classification**.

`blocked` means, per Herdr's own documentation, *"Herdr recognized an approval
**or question** UI"*. It does not distinguish the two. So on every `blocked`:

1. Read the detection snapshot.
2. Extract the **Selected Option** — the entry marked with `❯` (`❯`), or
   failing that, option `1.`, which Claude Code always pre-selects.
3. If no menu is on screen → **Ignore**.
4. If its label begins with `Yes` → **Acceptance**.
5. Otherwise → **Pause**.

### Decision: any Affirmative Option is accepted

Claude Code emits 30+ distinct Options beginning with "Yes". This tool accepts
**all** of them. That explicitly includes:

```text
Yes, buy usage credits                              → spends money
Yes, I trust this folder                            → security boundary
Yes, trust and add server                           → adds an MCP server
Yes, trust this gateway                             → security boundary
Yes, install <plugin>                               → installs software
Yes, I accept                                       → accepts terms
Yes, set auto mode as my default permission mode    → rewrites settings.json permanently
Yes, and don't ask again for <x>                    → writes to permissions.allow
```

This is a deliberate choice, not an oversight. An allowlist of specific labels
was considered and rejected in favour of the simpler rule. **The log (§8) is
therefore the only record of what was pressed.**

### Question Prompts are Paused, not Skipped

An earlier draft treated "not a Yes" and "not a menu" as one outcome called
Skip. That was wrong twice over: it read as though the Prompt had been thrown
away, and it buried real decisions under Herdr's fallback-rule noise.

There are three outcomes:

| Outcome | When | What happens |
|---|---|---|
| **Accept** | The menu offers a "Yes" and it is selected | Enter is pressed, in any Pane |
| **Pause** | A real decision — no "Yes" on offer, or a "Yes" that is not selected | Nothing is sent; the Prompt stays up and is reported loudly |
| **Ignore** | No menu on screen at all | Nothing sent, nothing recorded |

A Pause is a hand-back. The Prompt is untouched, the Agent stays `blocked`,
Herdr's own notification still fires, and `nodder --waiting` lists every Pane
currently holding one.

Ignore exists because Herdr's `legacy_no_prompt_blocker` rule reports `blocked`
whenever the words "do you want to" and a `❯` appear anywhere in a Pane's
buffer. That fires constantly on ordinary transcripts. Recording those would
make the record useless for its one job — showing what needs the human.

---

## 7. Concurrency

- One **discovery** loop polling `herdr agent list` every ~2s, to notice Agents
  appearing and exiting.
- One **watcher** thread per live Agent, each parked in a blocking
  `herdr agent wait --until blocked`.
- Watchers are reaped when their Agent leaves `herdr agent list`.

Discovery is polled; acceptance is event-driven.

### Duplicate protection is needed after all

An earlier draft of this spec claimed `agent wait` was edge-triggered and that
no duplicate protection was required. **That is wrong.** Measured against herdr
0.8.0:

```bash
$ time herdr agent wait <idle-agent> --until idle --timeout 5000
rc=0 in 0.010s          # already idle -> returns immediately
```

`agent wait` is **level-triggered**. An unanswered Prompt keeps reporting
`blocked`, so a watcher that simply re-armed would re-read, re-press and
re-journal the same Prompt every settle interval until a human answered it.

So each watcher carries the **signature** of the last Prompt it handled — a
hash of the Option labels and which one is selected. A cycle that sees the same
signature does nothing. The signature is cleared whenever the agent leaves
`blocked`, so Claude asking to run the same command twice is correctly treated
as two decisions rather than a duplicate.

### Failures must be told apart from timeouts

`herdr agent wait` exits non-zero for both, but the `error.code` on stderr
distinguishes them:

| `error.code` | Meaning | Handling |
|---|---|---|
| `timeout` | No Prompt appeared | Ordinary; loop again |
| `agent_not_found` | Pane is gone — returns in ~6 ms | Raise; end the watcher |

Conflating the two spins a watcher at full CPU against a closed pane. Watchers
are also retired by the discovery loop when their agent leaves
`herdr agent list`.

---

## 8. Logging

Every decision appends one line to:

```text
~/.local/state/nodder/log
```

Format:

```text
26/08/2026 14:32:07  w1:p2  ACCEPT  "Yes, run it"
26/08/2026 14:35:51  w1:p1  PAUSE   "Add a test"
```

Fields: timestamp (DD/MM/YYYY HH:MM:SS, local time), Agent target, outcome,
Selected Option label.

---

## 8a. Dashboard

`nodder run` starts the daemon with a curses dashboard on the same terminal.
It exists because the plain log answers "what happened" but not "what is the
state of my session":

- Panes are named `workspace-label/pane`, not by opaque id. `w17:p2` says
  nothing; `sluice/p2` says where to look.
- Each row carries that Pane's running Acceptance and Pause totals.
- Blocked Panes sort to the top, and the focused Pane is marked.
- The last 10 decisions scroll beneath.

Curses is standard library, so the dependency-free install still holds. The
dashboard owns the terminal, so logging is silenced while it runs; `run
--plain` keeps the stderr log instead and is what the systemd unit uses.

---

## 9. Scope

**Panes:** all of them. Every Agent in the session is watched. There is no
opt-in and no per-Pane arming.

**Platforms:** Omarchy (dev machine) and Debian / Ubuntu. Python 3 only — no
third-party packages. Python 3 is already a hard dependency of the installed
Herdr Claude integration.

---

## 10. Non-Goals

* GUI
* PTY spawning or terminal emulation
* ANSI parsing
* Claude API integration
* Parsing Claude's conversation or understanding what it wants to run
* Modifying Claude Code or Herdr
* LLM-based prompt detection
* Working outside a Herdr session
* Deny-patterns, read-only modes, or per-command policy

---

## 11. Build order

1. **Empirical spike (blocking).** Bring up a scratch session
   (`herdr --session aa-test`), run Claude in it, trigger a permission prompt,
   dump `herdr agent read --source detection` raw. Confirm whether `❯`, the
   `1.`/`2.` numbering, and the highlight survive the plain-text snapshot. If
   not, retry with `--format ansi`. **The matcher is written against this
   output, not against assumption.**
2. Selected-Option extractor + `Yes` test, with unit tests over captured
   snapshots.
3. Single-Agent watcher: wait → read → classify → send-keys → log.
4. Discovery loop and per-Agent watcher threads.
5. Daemon lifecycle: start, stop, status.

---

## 12. Definition of Done

With the daemon running and seven Claude Agents across a Herdr session:

* Every Approval Prompt whose Selected Option begins with "Yes" is accepted
  within ~1s, in focused and unfocused Panes alike.
* Question Prompts whose Selected Option is not a "Yes" are left untouched, and
  Herdr's existing notification tells the user one is waiting.
* Each Prompt produces exactly one log line however long it stays on screen.
* Every Acceptance and Pause appears in the record; Ignores do not.
* Killing the daemon returns every Pane to normal interactive behaviour with no
  residual state.

---

## 13. Open

* ADR recording *Herdr over PTY, and why not `--permission-mode
  bypassPermissions`* — offered, not yet written.
* Generalisation to other Agent kinds (`codex`, etc.) — expected to need no new
  classification code, unverified.
