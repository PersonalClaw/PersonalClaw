---
name: memory-discipline
description: When and how to persist durable memory — save genuine user preferences, facts, and corrections; NEVER persist transient environment failures (a failed command, a flaky network call, a one-off tool error) as lessons; recall before re-asking.
always: false
triggers: remember, memory, lesson, preference, note this, persist, save this for later, recall, forget, learned, you should know
---

# Memory Discipline

PersonalClaw is **persistent and self-learning** — across sessions it remembers
what the user prefers, durable facts about them and their work, and corrections
they've made. That value collapses if memory fills with noise. The discipline is
simple: **persist what's durable, recall before re-asking, and never store
transient failures as lessons.**

Tools: `memory_remember` (save), `memory_recall` / `memory_list` (retrieve),
`memory_forget` (remove).

## What IS worth remembering

Persist something only when it will still be true and useful **next week, in a
different session**. Three durable kinds:

- **Preference** — a standing choice about how the user wants things done.
  *"Prefers pytest over unittest." "Wants concise answers, no preamble." "Uses
  tabs, not spaces, in this repo."*
- **Fact** — durable truth about the user, their environment, or their projects.
  *"The prod database is `orders-prod` in us-east-1." "Their team's CI is
  GitHub Actions." "Deploys go out Tuesdays."*
- **Correction** — the user fixed something you got wrong; capture the corrected
  rule so you don't repeat the mistake.
  *"Don't call it 'the API' — it's specifically the Billing API." "I said X was
  fine; the user corrected that X is forbidden here."*

## What is NOT a lesson (the key guardrail)

**Never persist environment or transient failures as memory.** A failure that
belongs to *this moment* — not to the user's durable preferences or world — is
not a lesson:

- A bash command that failed (wrong flag, missing file, exit 1) — fix it and move
  on; don't remember "the command failed".
- A flaky or timed-out network call, a transient API 500, a rate-limit — retry or
  route around; it's not a fact about the user.
- A one-off tool error (bad argument, malformed path, a file that didn't exist
  yet) — correct the call; the error is not knowledge.
- A momentary state ("the server was down", "the test was red just now") — state
  changes; don't freeze a snapshot of it into permanent memory.

Litmus test before `memory_remember`: *"Will this still be true and actionable
in a fresh session next week?"* If it's about something that just broke or a
passing condition of the current run, the answer is no — **don't save it.** (If a
failure reveals a *durable* rule — e.g. "this build always needs Node ≥20" — save
the **rule**, not the incident.)

## Recall before re-asking

Before asking the user something they may have already told you — a preference, a
default, a name, an environment detail — **check memory first** with
`memory_recall` (semantic lookup) or `memory_list` (browse). If it's there, act
on it (or confirm — *"Last time you preferred X; still the case?"*) instead of
re-asking. Re-asking something already on record is exactly the friction
persistent memory exists to remove.

## How to write a good memory

- **One durable rule per entry**, phrased as a standing instruction or fact, not
  a narration of an event. ✅ *"Prefers TypeScript strict mode."* ❌ *"Today we
  turned on strict mode."*
- **Categorize** by kind (preference / fact / correction) and **scope** it
  appropriately — workspace/project-specific facts scoped to that project, global
  preferences scoped broadly — so recall surfaces the right thing in context.
- **Supersede, don't duplicate.** If a new preference contradicts an old one,
  update/forget the stale entry rather than leaving both — contradictory memory
  is worse than none.

## Don't

- Don't save secrets or credential contents, ever.
- Don't save throwaway working state (current branch, today's failing test, a
  transient error) — see the guardrail above.
- Don't save something the user didn't actually express as durable just because
  it came up once; ephemeral context stays in the conversation, not in memory.
