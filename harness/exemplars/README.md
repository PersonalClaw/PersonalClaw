# harness/exemplars/ — milestone exemplars (§4.1)

Per-slice runnable exemplars for the Workflows-v2 build-out. This is a **process obligation
on WORKFLOWS-V2 sessions**, enforced by this harness (the same-PR rule flags a slice merged
without its exemplar): each WF2 wave/slice landing adds one entry here —

```
exemplars/<slice>/
  exemplar.py     # a standalone runnable spec exercising that slice's mechanism
  smoke.sh        # run + assert (≤30s)
  RATIONALE.md    # what the slice added, what the exemplar proves
```

Exemplars are triple-duty: **regression anchors** (the `exemplars` profile runs their smoke
scripts — `python -m harness.exemplars`, or `harness run <task>` for a task that requires
it), **recorded-trace sources** for the replay scenarios in `../traces/`, and **tutorials**
for future coding agents (the easy-agent `step/` pattern).

Each `slice_*/` is a package: `exemplar.py` exposes a `main()` that drives the run and
self-asserts (exit 0 = the mechanism behaved), runnable as
`python -m harness.exemplars.slice_N.exemplar`. `smoke.sh` isolates a throwaway
`PERSONALCLAW_HOME` and invokes it. `discover_exemplars()` in this package's `__init__.py` is
the single enumeration the proving test (`tests/test_harness_exemplars.py`) and the
`exemplars` profile share, and `incomplete_slices()` is the mechanical same-PR check (a slice
merged without its full bundle is flagged, not silently skipped).

## Status

**Backfilled for the landed WF2 slices (SV-8).** Slices 0-5 each have an exemplar exercising
that slice's mechanism through the real engine with a fake model (no network, no LLM):

| dir | slice | mechanism it proves |
|---|---|---|
| `slice_0/` | Data model + bindings + validator | validator passes a sound spec / flags a dangling ref without throwing; binding type-preservation |
| `slice_1/` | Pure frontier + engine + journal | a 3-node sequence runs to COMPLETE in dependency order; ledger records a completion per node |
| `slice_2/` | Engine-owned completion | a 3-node run with a failing `required_artifacts` gate ends FAILED (the done-when's named example) |
| `slice_3/` | Secrets + RedactingSink | `{{secret:KEY}}` resolves only via the injected resolver; a leaked credential never reaches disk |
| `slice_4/` | Mid-flight mutation | an edit re-runs exactly the binding closure (sibling in, unrelated out); prefix served from the resume cache |
| `slice_5/` | Human-input contract | an unanswered gate surfaces `needs_input`; a timed-out unattended gate FAILs, never passes |

This directory + its contract shipped in Self-Verification Session 4 so each WF2 slice's
exemplar had a home and the same-PR obligation was in force; the backfill of the landed
slices is atom `SV-8`. New WF2 slices add their `slice_N/` here in the same PR.

The two WF2-gated **replay scenarios** (`workflow-journal-projection`, `rewind-during-stream`)
landed in `SV-5` and live under [`../traces/`](../traces/) — the location the `replay` gate
reads — each with a checked-in baseline that pins the event-fold law (the pre-migration
journal-format gate). They are not runnable exemplars, so they are not under this dir; a
future exemplar that exercises the same slice can reuse them as its recorded-trace source.
