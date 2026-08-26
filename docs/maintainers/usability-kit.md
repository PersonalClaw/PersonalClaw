# Usability kit — stranger validation

**Status:** the kit is ready to run. The three sessions themselves are the owner's
(ONBOARDING-UX Owner task 1 — recruit and host three people who have never seen the
product). A dry-run against the real product is recorded in
[§7](#7-dry-run-on-self-2026-08-25), including the four things the first draft of this
kit got wrong.

Everything a facilitator needs is on this page. You do **not** need to have read the
codebase or the roadmap, and you should not read the roadmap before a session — knowing
what we hoped to build is exactly the bias these sessions exist to remove. The one
roadmap link below is a citation for a number, not required reading.

---

## 1. What you are running

One participant, ~45 minutes, think-aloud. They drive; you watch and write. You are
measuring **where a stranger's model of the product diverges from ours** — not whether
they can complete tasks. A participant who finishes every task in silence is a wasted
session; a participant who gets stuck twice and narrates it is a good one.

Four tasks, in this order (they build on each other):

| # | Goal | Budget | Needs a model provider? |
|---|---|---|---|
| 1 | Get it running on their own machine | 15 min | no |
| 2 | Get it to do one thing they consider useful | 10 min | no |
| 3 | Let it touch their machine, and decide whether to allow it | 10 min | **yes** |
| 4 | Describe the thing that runs on its own, in their own words | 10 min | no |

Budgets are for your clock, not theirs. Never tell a participant they are running long.

## 2. Before the session

| Check | Why it bites |
|---|---|
| Python **3.12+** on their machine | the install fails late and cryptically on older versions |
| They can install software on it | a locked-down work laptop ends the session at task 1 |
| Screen-share or in person | you must see hesitation, not just outcomes |
| Paper + pen for the sheet in [§5](#5-observation-sheet) | typing during think-aloud makes participants stop talking |
| **A model provider they can actually use** — read the next paragraph | task 3 is impossible without it |

### The credential problem — decide this before you invite anyone

The setup flow makes a model provider a **required** step, and the four provider cards
it shows by default all need either a paid API key or an AWS account. A keyless local
option (Ollama) exists but is the twelfth of sixteen providers, behind a **Show all 16
model provider apps** control. So a stranger with no API key hits a required step they
cannot satisfy, roughly ninety seconds in. Pick one of these in advance:

- **A — you supply a key (preferred).** Bring a throwaway, low-limit key for one
  provider. Hand it over *only when the participant asks for one*, and let them enter
  it themselves. Revoke it after the session. You are testing whether they can find
  where a key goes, so do not pre-enter it.
- **B — they install a local model first.** Cleanest privacy story, but it removes the
  hardest step from the session. If you choose B, you have not tested provider setup —
  write that on the sheet.
- **C — they skip the provider step.** The flow allows it (**Set up later**). Tasks 1,
  2 and 4 still run and still produce findings. **Task 3 does not run at all** — nothing
  in the product asks for approval when no model is bound. Do not improvise a
  substitute; record task 3 as not run.

Reference material, for you and never for them: [getting
started](../guides/getting-started.md), the [README](../../README.md), and — if the
participant asks what it is allowed to do to their machine — [the threat
model](../security/threat-model.md).

## 3. Consent note — read this out loud, near enough to verbatim

> Before we start.
>
> This is a test of the software, not of you. If something here is confusing, that is a
> defect, and you finding it is the entire point of this session.
>
> Please think out loud — say what you are looking for, what you expect to happen, and
> what surprises you. Silence is the only thing that does not help me.
>
> **What I am recording:** written notes, by hand, on this sheet. What you do, and
> sometimes the exact words you use — your words are the most useful thing I get today.
>
> **What I am not recording:** no screen recording, no audio, no video, nothing
> automatic. The product itself has no usage tracking of any kind, so nothing about
> this session is logged by the software.
>
> **Where the notes go:** they stay on my machine. If something you run into becomes a
> public bug report, it goes in as a paraphrase or a short quote, with no name and
> nothing that identifies you. Tell me now — or at any point later, including after we
> finish — if you would rather nothing you say is quoted, and I will keep it out.
>
> **You can stop at any time.** Take a break, skip a task, or ask me to strike a note
> you would rather I did not keep. You do not need a reason.
>
> **One request:** at some point you may be asked for an API key. Use a throwaway, or
> the one I give you — never a credential you care about. If a key of yours lands on my
> notes, I will destroy that page in front of you.
>
> Any questions before we start?

If you want a screen or audio recording, you must **ask for it explicitly and get a
yes**, and then say where the file will live and when you will delete it. Never record
silently, and never treat "sure, whatever" as consent. The project has no
data-retention policy — it has never collected participant data — so the rule is the
simple one: **nothing is recorded unless the participant agrees, and the facilitator's
notes stay local.**

## 4. Facilitator script

Read the task, then stop talking. The tasks are deliberately written as **goals, not
steps** — the moment you name a button, you have destroyed the measurement that button
existed to provide.

### Task 1 — Get it running

> "You have heard about a self-hosted AI assistant called PersonalClaw and you want to
> try it on your own machine. Go ahead and get it running. Use whatever you would
> normally use to work that out — a search engine, the project's site, its README,
> anything."

Watch for: where they look first; whether they find an install path at all; whether
they pick the one we would have recommended; the Python version wall; whether they know
the thing is now running and where to look at it.

### Task 2 — Get it to do one useful thing

> "It is running. Now get it to do something you would consider actually useful. Tell
> me when you believe it worked — and tell me if you are not sure."

Do not say "first success", do not mention setup, do not point at any card. **Start the
first-success clock the moment the dashboard first paints** (see [§6](#6-after-the-session)).
Stop it when *they* say it worked. If they reach a step the product marks as done but
they do not believe it, the clock keeps running — that gap is the finding.

### Task 3 — Let it touch your machine

*(Requires a bound model — see [§2](#2-before-the-session). If you chose option C, skip
this and record it as not run.)*

> "Now ask it to do something that involves your actual computer, or the internet —
> read a file, look something up, whatever you would want. Narrate everything it shows
> you before it acts. Then tell me what you decide, and why."

Watch for: whether they read what they are approving or click through it; whether they
can tell a harmless action from a dangerous one; whether they understand what "remember
this" would commit them to; whether they feel safe.

### Task 4 — The vocabulary probe

Ask these **in order**, and do not skip ahead. This task exists to catch naming bugs, so
the words must come from them first.

> a. "Earlier, something got started that runs on its own. In your own words: what is
>    that thing, what is it for, and where would you go right now to see how it is
>    doing?"
>
> b. *(after they answer)* "What would you call it?"
>
> c. *(only now)* Show them the word the product uses, and ask: "does that match what
>    you just described?"

**Do not say the product's word for it before step (c).** The first draft of this kit
told the facilitator to ask "what does *Loops* mean, from the UI alone" — and a dry-run
measured that the word **Loops appears nowhere in the shipped navigation**. A
participant sent hunting for that label would have been hunting for something that does
not exist, and you would have written down a finding the kit invented. See
[§7](#7-dry-run-on-self-2026-08-25).

### What you may say

A short, fixed list. Anything not on it, do not say.

- "What are you thinking?"
- "What did you expect to happen?"
- "What are you looking for right now?"
- "Say that again — what did you call it?"
- "Go ahead and try it." *(when they ask you for permission to click something)*
- "There is no wrong move here."
- "Take as long as you like."

### What you must never say

- The name of any button, page, menu or setting they have not found yet.
- "It's under Settings." / "Just click X." / "Scroll down."
- "Did you notice the ___?" — a leading question wearing a curiosity costume.
- "That's a known bug." / "Yeah, that's broken." — this teaches them to stop reporting.
- Any answer the product itself should have given them.

If they are stuck for two minutes and visibly frustrated, unblock them with the
**smallest possible hint**, then write the hint down verbatim in the unblock log. An
unblock is not a failure of the session; it is the highest-value line on the sheet,
because it names the exact sentence the product should have said itself.

## 5. Observation sheet

One sheet per participant. Copy this block per session.

```
Participant: P__      Date: ______      Facilitator: ______
OS / Python: ______________________     Provider option (A/B/C): ___
Consent read and agreed: [ ]   Recording agreed (if any): [ ] none  [ ] screen  [ ] audio
```

Per task, all four columns. Blank columns are the sign of a session that was watched
instead of observed.

```
TASK __   start __:__   stop __:__   elapsed ____
Outcome:  [ ] reached unaided   [ ] reached after an unblock   [ ] abandoned

Hesitation log        mm:ss | where they were | what they were hunting for
                      ─────────────────────────────────────────────────────
                      ____ | ______________ | _____________________________
                      ____ | ______________ | _____________________________

Expected vs happened  they expected ______________________________________
                      it actually  ______________________________________

Their exact words     "________________________________________________"
(verbatim — do not     "________________________________________________"
 tidy it up)

Dead ends             what they tried that went nowhere: ________________
```

**Vocabulary table** — the naming-bug detector. Fill the middle column with *their*
word the first time they reach for the concept, before you have said ours.

| Our word | Their word (verbatim) | Did they connect the two? |
|---|---|---|
| Loops | | |
| Projects | | |
| Inbox | | |
| Approvals | | |
| Triggers | | |
| Skills | | |
| Agents | | |
| Artifacts | | |
| Store / Apps | | |
| Knowledge | | |
| Workflows | | |

**First success**

```
Dashboard first painted at __:__      Participant said "it worked" at __:__
Elapsed: ______      of which waiting on the machine: ______
Did the product mark something done BEFORE they believed it?  [ ] no  [ ] yes → when: ____
```

**Unblock log** — every hint you gave, word for word.

```
mm:ss  "_______________________________________________________________"
mm:ss  "_______________________________________________________________"
```

**Closing questions** — ask all five, write the answers long.

1. "If you had to explain to a friend what this is, what would you say?"
2. "What was the worst moment?"
3. "What did you expect it to be able to do that it turned out it couldn't?"
4. "Would you keep it installed? Why, or why not?"
5. "Anything you were thinking but didn't say out loud at the time?"

## 6. After the session

**Within 30 minutes**, while it is fresh, sort every finding into exactly one bucket:

- **fix-now** — one change, well under a day, no design decision needed. Wrong label,
  confusing sentence, missing empty state, a button that goes somewhere unhelpful.
- **`ux-finding` issue** — needs a design decision, or is more than a day's work.

The fix-now budget is **≤1 day across all three sessions**, not per session. If
session 1 spends it, sessions 2 and 3 file everything they find. (The label may need
creating — the repo defines its labels in the GitHub UI, not in a tracked file.)

Issue body shape — no participant names, ever:

```
What they were trying to do:
What they actually did:
What they expected:
What happened:
Their words:            "..."
Session:                P1 / P2 / P3
Severity:               blocked / worked around / annoyed
```

### The timing number, and why you cannot compute a delta from session 1

Log the split between **hands-on time** and **waiting on the machine**. The machine part
is small — the dry-run measured 112 ms and 235 ms from click to visible outcome on two
of the three try-one cards — so anything large is deliberation, and deliberation is the
finding, not the latency.

**Do not compare a session against the existing recorded figure as if it were a human
baseline.** The only pre-existing number is **9.3 s of scripted-browser interaction**
from an automated drive with the provider step skipped entirely, recorded in the OU-4
entry of 2026-08-16 in
[the ONBOARDING-UX plan](../roadmap/plans/ONBOARDING-UX.md). That is a machine floor for
the click path — it is not a person, it does not include reading, deciding, or setting
up a provider, and a stranger's time will be one to two orders of magnitude larger with
no defect implied. So:

- **Session 1 establishes the human baseline.** Write it down as the baseline.
- The delta the plan asks for is measurable from the **re-run after the fix-now list is
  cleared** — same tasks, a fresh participant, and say plainly in the record that it is
  a different person, because it is.

## 7. Dry-run on self (2026-08-25)

Run against the real product from a source checkout: gateway on an isolated
`PERSONALCLAW_HOME`, loopback-only, SPA built from the same tree and confirmed serving
(a 2.6 MB bundle, HTTP 200) before anything was observed. Zero console errors and zero
warnings across the whole run. The purpose was to find the kit's bugs before a stranger
did, and it found four.

**What the kit got wrong**

1. **Task 4 named a label that does not exist.** The draft task read "tell us what
   *Loops* means from the UI alone." Measured on the finished dashboard: the word
   "Loops" appears in **neither** the starter navigation **nor** the expanded
   eighteen-item navigation, and the Projects page — the only rail item that mentions
   loops at all, and only in its accessible description — renders the substring "loop"
   **zero** times. The task would have sent a participant hunting for a word the
   product does not use, and a facilitator would have logged that as a finding the kit
   itself manufactured. Task 4 is now a three-step probe that takes the participant's
   word first.
2. **Task 3 had an unstated prerequisite.** Approving a tool call requires a bound
   model. With no provider configured the dashboard reports eleven surfaces degraded
   "without a model" and zero approvals are reachable, so task 3 is not merely harder —
   it cannot run. The credential problem is now a decision in
   [§2](#2-before-the-session) rather than a surprise at minute thirty.
3. **The kit assumed a stranger can satisfy the required provider step.** They often
   cannot: the four provider cards shown by default all need a paid key or an AWS
   account, and the keyless local option is twelfth of sixteen behind a **Show all 16**
   control. That is now stated as the session's main logistical risk, with three
   explicit options.
4. **My own timing harness produced a number I had to throw away.** The first card's
   measurement came back as 29 s because the polling loop's own deadline dominated the
   result; re-measured tightly, the next two cards were 112 ms and 235 ms. Hence the
   instruction in [§6](#6-after-the-session) to record the hands-on and machine splits
   separately, and to trust the participant's declaration rather than any instrument.

**What the run also established, for the facilitator's benefit**

- The flow is name → essential apps → try one → all set. Skipping the provider step is
  one click (**Set up later**) and does not block the rest.
- All three try-one cards do real work with no provider bound: a real note in Knowledge
  answered by a real retrieval, a real reminder created and fired once, a real one-cycle
  loop started. Backend state confirmed all three.
- The step-2 apps screen is a wall — thirteen apps across four categories, each with a
  paragraph — arriving immediately after the participant has typed their name. Expect
  hesitation there and log it; it is the most likely place a session stalls.
- **Wall clock for the whole dry-run was 4m 04s**, but essentially all of it was the
  operator reading and deciding, which is exactly the quantity a stranger session exists
  to measure properly. Treat this figure as a lower bound on the operator's own path, not
  as a target.

**Product defects the dry-run found** (reported, not fixed by this kit; if a session
reproduces one, note it and move on rather than re-filing):

- The **"Set a reminder" card contradicts itself in a single view**: it displays
  "Cadence: At 09:00 AM" alongside "Next time: 8/26/2026, 2:00:00 AM". The trigger is
  stored as `0 9 * * *` with no timezone, evaluated as UTC, and rendered in local time —
  so the first reminder a new user creates fires at their UTC offset, and the card shows
  the discrepancy to them. The built-in notification digest has the same shape.
- The **Projects rail item advertises "1 active loop" but the Projects page shows no
  loops** — only the two built-in projects. The running loop is reachable from the
  dashboard, and opening it lands on a **chat session** URL rather than anything
  loop-shaped. The concept is named four different ways across four surfaces.
- The try-one step opens with **two consecutive sentences that both say "runs for
  real"** — redundant copy in the highest-attention position in the flow.

## 8. What this kit cannot tell you

- **Three participants find severe problems, not rates.** If two of three hit the same
  wall, fix it. Do not compute percentages from n=3.
- **Technical-adjacent recruits are the wrong sample for the install path.** They will
  route around a Python version problem that would end a non-developer's session.
- **A screen-share hides the machine.** Slow disks, corporate proxies and permission
  dialogs are invisible to you and very visible to them.
- **You cannot test first run on a machine that has already run it.** If a participant
  has the product installed for any reason, tasks 1 and 2 are void for them.
- **No telemetry means no funnel.** There is deliberately no usage tracking, so these
  sessions are the only instrument. That is the trade: three careful hours instead of a
  dashboard.

---

Related: [release runbook](./release-runbook.md) for how a fix reaches users;
[configuration reference](../reference/configuration.md) if a participant asks what can
be changed.
