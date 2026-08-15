---
name: best-of-n
description: Sample several candidate answers in parallel, judge them against stated criteria, and present the winner with the runners-up kept one click away. Confirms the count and the criteria first because each candidate is a separate model call. Use when the user wants options rather than one answer. Triggers include "give me N options", "give me 3 versions", "best of", "try a few versions", "sample and pick", "generate a few and choose", "which of these is best", "draft some variations".
triggers: best of, give me options, give me versions, versions and pick, few versions, sample and pick, generate a few, pick the best, which is best, draft variations, some variations, few options
---

## Activation Behavior

**Explicit triggers** (the user has already asked for several candidates):

- "give me 3 versions and pick the best", "best of 5", "sample and pick", "generate a
  few options and choose"
- Activate immediately, then run the confirmation gate below for the two things you
  cannot guess: the count and the criteria.

**Ambiguous triggers** (the user might want one good answer, not a slate):

- "try a few versions", "draft some variations", "which of these is best", "give me
  options"
- Offer the choice BEFORE spending anything:
  > Want me to sample a few candidates and judge them (each candidate is its own model
  > call), or just write you one answer?
- If they pick one answer: answer normally. No skill mode, no sampling.

## The confirmation gate (never skip the cost line)

Best-of-N costs **N model calls plus one judge pass per surviving candidate**. That is
the whole trade, so say it out loud and get both inputs:

> Sampling 3 candidates — that's **3 model calls** (plus judging), not one. Judging on:
> *specific, under 60 characters, no hype*. Want a different count (max 5) or different
> criteria?

- **N is capped at 5.** If the user asks for more, say the cap and sample 5.
- **Criteria are what "best" means for this task.** If the user has not said, propose
  criteria in the gate rather than inventing them silently — a slate judged on an
  unstated bar is a coin flip with extra steps.
- One round trip is enough. Do not interrogate; propose sensible defaults and let the
  user correct them.

## Running it

Call the `best_of_n` tool once with `prompt` (the ask, identical for every candidate),
`n`, and `criteria`. The tool fans the N calls out in parallel — one slate costs roughly
one call's wall time, N calls' spend — and returns JSON:

```
{"winner": "...", "winner_idx": 1, "candidates": [{"idx", "temperature", "text", "error"}],
 "judgments": [{"idx", "score", "reason"}], "judged": true, "n": 3, "note": ""}
```

Do NOT re-sample to "double-check", and do not run it twice for the same ask. The slate
you already have is the slate.

## Presenting the result

Lead with the winner as a normal answer — the user asked for a good answer, not a
report. Then keep the slate one click away:

```markdown
<winner text, presented as the answer>

<details>
<summary>Runners-up (2) — say "use #2" to switch</summary>

**#1** — score 3.5 · *reason from the judge*

<candidate text>

**#3** — score 4.0 · *reason from the judge*

<candidate text>

</details>
```

Numbering rules, because "use #2" has to actually work:

- Number candidates **#1…#N in slate order**: `#k` is the candidate whose `idx` is
  `k - 1`. Never renumber by score — the user's "#2" must mean the same candidate in
  your list and in the tool result.
- Mark the winner in the visible answer (e.g. "picked #2 of 3"), so the numbers the
  user sees are the numbers they can choose from.
- When the user says **"use #2"** (or "go with the second one"), reply with that
  candidate's text **verbatim from the slate you already have**. Do not re-sample, do
  not paraphrase, do not re-judge. If they then ask you to edit it, edit that text.

## Degraded cases — report them, never paper over them

- **`judged: false`** — the judge was unavailable. Say so: "the judge wasn't available,
  so this is the first candidate, unranked" and still show the slate.
- **Some candidates carry an `error`** — the slate is narrower than N. Say how many
  returned ("2 of 3 came back") and judge only those. Never invent a missing candidate.
- **`winner: null`** — every sample failed. Say that plainly and offer to answer
  directly or retry. Do not present one of the errors as an answer.

## When NOT to use this

- Anything with one correct answer (a fact, a calculation, a file's contents). N samples
  of a lookup is N× the cost for the same answer.
- Long-running or tool-using work — this is one-shot text sampling, not delegation. Use
  the delegation skill and subagents for work that needs tools.
- Inside a loop over many items. One slate per user ask; a slate per item multiplies the
  spend by N without anyone noticing.
