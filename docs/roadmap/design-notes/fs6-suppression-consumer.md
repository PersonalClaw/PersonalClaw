# FS-6 — a live gated consumer of `suppressed_producers()`

**Atom:** re-add a live gated consumer of `feedback.suppressed_producers()` so a
persistently-wrong producer stops surfacing again. The only prior gated consumer
(`workflows.surfacing.eligible_workflows`) was deleted in WF2 Phase 1, leaving the
control inert — its only current callers are feedback.py's own retire logic and a
display-only dashboard handler (`dashboard/handlers/feedback.py`, which reports a
`"suppressed"` boolean in the accuracy table but withholds nothing).

## Host gate chosen: skill surfacing (`skills/surfacing.py::surface_skills`)

**Why not the workflow suggestion path.** The atom names
`workflows/surfacing.py::may_suggest` / `veto_reasons` as "the most natural" host.
It is not viable: **that entire path is itself inert on `origin/main`.** Verified —
`may_suggest`, `veto_reasons`, `render_suggest`, `render_passive` have **zero runtime
callers** in `src/`. The only live consumer of `workflows/` surfacing is
`workflows/service.py::list_defs_surfacing`, and it calls only the cadence / freshness /
doctor helpers in `surfacing_channels.py` (`cadence_from_def`, `freshness`, `overdue`,
`handoffs_from_def`, `doctor_entry`, `sort_key`, `doctor`) — never the suggestion-veto
functions. Wiring an `in suppressed_producers()` check into `veto_reasons` would satisfy
the letter of the atom while shipping **another declared-but-inert control** — the exact
failure FS-6 exists to fix. The done-when requires *a runtime path WITHHOLDS*, so the host
must be a gate a real turn actually executes.

**Why skill surfacing is the right host.**
- `surface_skills` is genuinely **live**: called every turn via
  `SkillsLoader.get_surfaced_skills` → `context.py` (skill injection for any
  non-custom-agent message). It is the one turn-time surfacing gate that runs.
- feedback.py already declares this as the intended consumer. `suppressed_producers()`'s
  own docstring: *"Consulted by workflow/skill surfacing as one membership check;
  everything else gets the proposal only."* And `_proposal_only_candidates` treats only
  `("prompt", "loop_judge")` as ungated (proposal-only), commenting that *"gated producers
  are covered by `suppressed_producers()`"* — the gated set includes `skill_synthesis`.
  So skill surfacing is not an improvised host; it is the consumer the substrate was
  written for.

## Producer-identity mapping

A surfaced skill maps to feedback's producer identity as:

```
(producer_kind, producer_id) = ("skill_synthesis", <skill key>)
```

- `skill_synthesis` is the skill-related member of `feedback.PRODUCER_KINDS`, and
  feedback.py already classifies it as a *gated* producer (not proposal-only).
- The skill's `key` (its stable path-relative identifier, the same value used as the
  ranking key throughout `surface_skills`) is the `producer_id` — mirroring the identity
  convention elsewhere (`loop_judge`/`loop.kind`, `prompt`/prompt-ref). A thumbs-down on a
  skill's judgment recorded as `producer_kind="skill_synthesis", producer_id="<key>"`
  accrues per-skill accuracy; once it falls below `retire_threshold` with `n >= min_n`,
  `(skill_synthesis, key)` enters the suppressed set and the skill is withheld.

The membership check is exact-tuple, matching how every other consumer reads the set.

## Where the check lives (seam shape)

- `surface_skills(..., suppressed: set[tuple[str, str]] = frozenset())` — a new keyword
  arg, default empty (suppress nothing). A matched candidate whose `(skill_synthesis, key)`
  is in `suppressed` is **withheld** (excluded from the ranked result; in `explain` mode it
  is surfaced as an excluded row with a `withheld (feedback suppression …)` reason, so the
  Doctor simulator never lies about why a skill did not surface). Keeping `surface_skills`
  a pure function over its inputs (rather than importing feedback itself) preserves its
  "pure functions over metadata" design and keeps the existing unit tests hermetic — no
  real-home read is introduced into tests that call it directly.
- `SkillsLoader.get_surfaced_skills` — **the live wiring point.** It fetches
  `feedback.suppressed_producers()` and passes it into `surface_skills`. This is where the
  control becomes live. The Doctor's surfacing simulator
  (`dashboard/handlers/doctor.py::_simulate`) is wired the same way so it reflects the real
  turn decision.

## Fail-open reasoning

Suppression must never crash or empty the surfacing path (a feedback fault must not silence
the assistant's skills). Layers:

1. `feedback.suppressed_producers()` already fail-opens internally: config-disabled →
   empty set; any exception → logged + empty set.
2. The fetch helper in `get_surfaced_skills` wraps the call and returns `set()` on any
   fault, so a fetch/import failure degrades to *suppress nothing* while the semantic ∪
   keyword surfacing continues unchanged.
3. If suppression somehow raised past that, `get_surfaced_skills`'s existing outer
   `except` still falls back to the pure keyword path — which surfaces normally. Every
   failure mode surfaces *more*, never less. Withholding only ever happens on an explicit,
   healthy membership hit.

## Clean break

One live consumer, no dual path, no dead code. `surface_skills` gains one argument and one
membership guard; `get_surfaced_skills` and the Doctor simulator supply the set. No config
field is added (the existing `feedback.enabled` kill-switch already zeroes the suppressed
set when off). The stale test note in `tests/test_feedback.py::TestSurfacingSuppression`
(which documents the FS-6 gap and asks the next lander to "re-add an end-to-end case") is
updated to reflect that skill surfacing is now that consumer.
