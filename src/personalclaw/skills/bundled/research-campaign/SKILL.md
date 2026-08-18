---
name: research-campaign
description: Run a multi-cycle research campaign — grill the question, decompose it into answerable sub-questions, investigate each against sources, then synthesize with confidence and gaps stated. Use for open questions that need real investigation rather than one lookup.
always: false
triggers: research campaign, do research on, deep dive into, investigate thoroughly, literature review, survey the landscape, decompose the question, multi cycle research, synthesize findings, research plan, competitive analysis, evaluate the options for, !quick answer, !just tell me
---

# Research campaign

One search answers a lookup. An open question needs a campaign: grill, decompose,
investigate, synthesize. The discipline is that each phase has an exit condition, so
you stop when the question is answered rather than when you get tired of searching.

## Phase 1 — Grill the question

Never start from the question as asked. Interrogate it first:

- **What decision does this serve?** The answer's required precision comes from the
  decision, not from the topic. "Which database" for a prototype and for a
  five-year platform bet are different questions.
- **What would change your mind?** Name it now. A campaign with no falsifier
  produces a conclusion that was decided before it started.
- **What is already known?** Restate the current belief explicitly, so the campaign
  can confirm or overturn something specific.
- **What is out of scope?** Write it down. Scope creep is what turns a campaign into
  a browsing session.

**Exit condition:** you can state the question in one sentence, plus the evidence
that would falsify your current belief.

## Phase 2 — Decompose

Break the question into sub-questions that are each *independently answerable*. A
good sub-question has a knowable answer and a source type that would hold it. If you
cannot name where the answer would live, the sub-question is still too abstract.

Mark each one: **load-bearing** (the conclusion changes if this changes) or
**supporting**. Investigate load-bearing ones first, and be willing to stop early —
if the first load-bearing answer settles the question, the rest is decoration.

**Exit condition:** a list of sub-questions, each with a source type and a
load-bearing flag.

## Phase 3 — Investigate

One sub-question at a time. For each:

- Prefer primary sources. A summary of a benchmark is not a benchmark.
- Record the answer, the source, and your confidence — and record **contradictions
  as contradictions**. Two sources disagreeing is a finding, not a problem to
  resolve by picking the more convenient one.
- Note what you looked for and did not find. An absent answer is evidence.

Run more cycles only while they change something. When a cycle adds sources but no
new answers, the campaign is saturated.

**Exit condition:** every load-bearing sub-question answered or explicitly marked
unanswerable.

## Phase 4 — Synthesize

- Lead with the answer to the original question.
- Attach confidence to each claim, and derive it from the evidence you actually
  gathered — not from how much searching you did.
- **State the gaps.** An unstated gap becomes a claim you did not make.
- Revisit the Phase 1 falsifier: did you find it? If you never looked for it, the
  campaign is incomplete regardless of how much you found.

## Worked example

Question as asked: *"Should we switch our queue to Kafka?"*

**Grilled:** the decision is whether to spend a quarter on a migration. Current
belief: the existing queue is the cause of our backlog incidents. Falsifier: if the
incidents trace to consumer throughput rather than the broker, the migration
addresses nothing. Out of scope: cost modelling, team training.

**Decomposed:**

| Sub-question | Source type | Load-bearing |
|---|---|---|
| What caused each of the last 6 backlog incidents? | Our own incident records | **Yes** |
| Does the current broker have a throughput ceiling we are near? | Our metrics + broker docs | **Yes** |
| What would the migration cost in engineer-weeks? | Comparable migrations, our code | No |

**Investigated:** the first sub-question showed 5 of 6 incidents were consumer-side
(slow handlers, no backpressure); one was a broker restart. The second showed
throughput at 12% of the documented ceiling.

**Synthesized:** *"No — and the campaign stopped after two sub-questions because the
first load-bearing answer settled it. 5 of 6 backlog incidents were consumer-side,
and the broker runs at 12% of its ceiling; a broker swap would not have prevented
any of them. The falsifier from Phase 1 was found, and it points at consumer
backpressure. Confidence: high on incident attribution (primary records), medium on
the ceiling figure (vendor documentation, not measured here). Gap: I did not cost the
migration, because the answer no longer depends on it."*

Note what the campaign did *not* do: it did not investigate the third sub-question,
and it says so. Finishing a list is not the goal; answering the question is.
