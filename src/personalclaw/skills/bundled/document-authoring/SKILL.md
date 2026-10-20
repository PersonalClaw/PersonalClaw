---
name: document-authoring
description: Author a real document — a report, proposal, slide deck, spec, or paper — by settling audience, claim, and structure before drafting prose, then writing to that structure. Use when asked to write or draft a document rather than answer a question.
always: false
triggers: write a document, draft a document, draft a report, write a report, slide deck, build a deck, presentation outline, write a proposal, write a spec, whitepaper, outline a paper, structured document, executive summary, author a doc, one pager, !commit message, !write a test, !write a script
---

# Document authoring

A document that starts as prose becomes a document nobody can edit, because the
structure is buried in the sentences. Settle the frame first; the prose is then
mechanical.

## Settle four things before writing a sentence

1. **Audience and their decision.** Who reads this, and what do they do differently
   afterwards? "Inform the team" is not a decision. "Approve the migration budget"
   is. If there is no decision, the document is reference material — say so, and
   structure it for lookup instead of persuasion.
2. **The claim.** One sentence that could be wrong. If your thesis cannot be
   disagreed with, you have a topic, not a claim.
3. **The shape.** Pick one and commit:
   - **Decision memo** — claim, options, recommendation, risks, ask.
   - **Report** — question, method, findings, limitations, implications.
   - **Proposal** — problem, cost of inaction, approach, plan, resources.
   - **Spec** — contract, behaviour, edge cases, out of scope.
   - **Deck** — one idea per slide, headline is the takeaway not the topic.
4. **The evidence you actually have.** List it before drafting. A structure your
   evidence cannot fill is where invented specifics come from.

## Then draft

- **Front-load.** Conclusion first. A reader who stops after the first paragraph
  should still have the answer.
- **Headline the takeaway.** "Latency regressed 40% after the cache change" beats
  "Latency analysis". This applies to every section heading and every slide title.
- **One idea per unit** — per paragraph, per slide, per bullet.
- **Name the limitations in the document**, not in a follow-up conversation. An
  unstated limitation reads as a claim you did not make.
- **Cut the frame.** "This document will discuss…" and "In conclusion…" are
  scaffolding. Remove them once the structure holds on its own.

## Slides differ from prose

A slide is a visual argument. If a slide needs a paragraph, it is two slides or it
belongs in an appendix. Speaker notes carry the nuance; the slide carries the claim
and the one piece of evidence that supports it.

## Worked example

Asked for "a document about our test flakiness".

**Settling it first:**

| | |
|---|---|
| Audience / decision | Eng leads — decide whether to fund a two-week stabilization sprint |
| Claim | Flaky tests cost us more engineering time than the sprint would, so the sprint pays back inside a quarter |
| Shape | Decision memo |
| Evidence I have | 6 weeks of CI reruns, 3 named flaky suites, the rerun-minutes total. **No** per-engineer interruption data — so I will not claim a focus cost |

**The resulting skeleton:**

```
Recommendation: fund the two-week stabilization sprint  (the ask, first)
What it costs us now: 41 rerun-hours over 6 weeks, concentrated in 3 suites
Why it compounds: a suite people rerun by reflex stops being a signal
The plan: quarantine, root-cause the 3, add a vacuity check to each rail
What I cannot show: per-engineer interruption cost — no data collected
Risk: two weeks not spent on the roadmap; mitigated by taking it after the release
```

Only now write prose, and only to fill that skeleton. Note the fourth row of the
table doing real work: it forced a limitation into the document instead of an
overclaim into the argument.
