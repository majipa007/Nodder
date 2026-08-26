# nodder

A watcher that answers Claude Code's routine permission prompts on the user's
behalf, across every agent in a Herdr session, including agents the user is not
currently looking at.

## Language

### Herdr concepts

**Pane**:
A terminal location within a Herdr session, identified by an opaque handle such
as `w1:p1`. A pane exists whether or not something is running inside it.
_Avoid_: Tab, window, split, terminal

**Agent**:
A coding assistant that Herdr recognises as occupying a pane, addressed by a
unique live name or by its pane handle. Claude Code is one kind of agent; Herdr
recognises others.
_Avoid_: Session, process, claude, instance

**Blocked**:
The Herdr lifecycle state meaning an agent is displaying an approval or question
UI and cannot progress until it receives input. Blocked does not say _which_
kind of UI is showing.
_Avoid_: Waiting, stuck, needs input, paused

### Prompts

**Prompt**:
Any interactive UI an agent displays while Blocked. Always qualified as an
Approval Prompt or a Question Prompt — the bare word is too vague to act on.
_Avoid_: Confirmation, dialog, confirmation prompt

**Approval Prompt**:
A Prompt asking permission to perform an action the agent has already decided
on, where the affirmative answer is a plain yes. Claude Code's
`Do you want to proceed?` is the canonical example.
_Avoid_: Confirmation, permission dialog, Y/N prompt

**Question Prompt**:
A Prompt asking the user to choose between substantive alternatives, where no
option is meaningfully "the yes". Claude Code's multiple-choice questions and
plan-mode reviews are Question Prompts.
_Avoid_: Confirmation, choice dialog

**Option**:
One selectable entry in a Prompt's list, identified by its label text.
_Avoid_: Choice, item, answer

**Selected Option**:
The Option a Prompt would act on if Enter were pressed right now.
_Avoid_: Highlighted, default, current, active

### Behaviour

**Affirmative Option**:
A Selected Option whose label begins with "Yes". Every Affirmative Option is
treated alike, whatever follows the word — including Options that spend money,
grant trust, or change settings permanently.
_Avoid_: Allowlisted, safe, approved, positive

**Acceptance**:
Pressing Enter on a Blocked agent because a "Yes" was on offer and selected.
Happens in every Pane, whether or not the user is looking at it.
_Avoid_: Auto-accept, approval, confirming, clicking yes

**Pause**:
Leaving a Prompt on screen because it is a real decision — no "Yes" was on
offer, or the "Yes" is not the Selected Option. The Prompt stays up and the
user answers it. A Pause is the outcome that wants surfacing; it is a
hand-back, never a refusal.
_Avoid_: Skip, ignore, reject, deny, timeout, pass

**Ignore**:
Doing nothing about a Blocked agent that is showing no menu at all. Herdr's
fallback rule reports Blocked whenever the words "do you want to" and a "❯"
appear anywhere in a Pane's buffer, so this fires on ordinary transcripts.
Nothing is decided and nothing is recorded — an Ignore in the record would
bury the Pauses.
_Avoid_: Skip, noise, false positive, no-op
