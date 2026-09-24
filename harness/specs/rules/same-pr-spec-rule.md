---
id: same-pr-spec-rule
type: ai-coding-rule
statement: >
  A fix-shaped change (commit subject matching fix/bug/regression/hotfix) should add or
  update a rule or scenario spec under `harness/specs/` in the SAME change, so the fixed
  bug becomes a permanent machine-checked invariant rather than a one-time patch.
appliesTo:
  - harness/specs/**
source: >
  The project's existing "every fixed bug becomes permanent" habit lived only in private
  auto-memory — invisible to other agents and lost on memory reset. Moving it into the
  versioned, greppable repo is the whole point of the harness.
expiry_condition: never (this is the harness's self-governing process rule).
---

# Every fix updates a spec, in the same change

When you fix a bug or close a recurring constraint, encode it as a harness spec **in the
same commit** as the fix:

- A new architectural invariant → a **rule** spec (`harness/specs/rules/`), ideally with a
  `scanner:` check-id so it's caught at diff time, and a `requiredTests` node-id so the
  proof is executable.
- A recurring *diagnosis* (a symptom you've now chased more than once) → a **scenario**
  spec (`harness/specs/scenarios/`) with the probe order and mitigation.

This moves institutional knowledge from private, decaying, per-agent memory into the
versioned repo shared with every coding agent.

## What compliance looks like

`python -m harness run --diff` warns when the diff's commit subject looks like a fix
(`fix`/`bug`/`regression`/`hotfix`) but touches nothing under `harness/specs/`. The warning
is advisory — some fixes genuinely don't generalize to an invariant — but the default
expectation is that a fix leaves the harness smarter than it found it.
