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

Exemplars are triple-duty: **regression anchors** (a future `exemplars` profile runs their
smoke scripts), **recorded-trace sources** for the replay scenarios in `../traces/`, and
**tutorials** for future coding agents (the easy-agent `step/` pattern).

## Status

**Empty until the per-slice exemplar backfill (SV-8).** The runnable exemplars exercise
engine slice mechanisms (the WF2 journal/run-ledger/required-artifacts machinery). This
directory + its contract shipped in Self-Verification Session 4 so that each WF2 slice's
exemplar has a home and the same-PR obligation is already in force; the backfill of the
landed slices is tracked as atom `SV-8`.

The two WF2-gated **replay scenarios** (`workflow-journal-projection`, `rewind-during-stream`)
landed in `SV-5` and live under [`../traces/`](../traces/) — the location the `replay` gate
reads — each with a checked-in baseline that pins the event-fold law (the pre-migration
journal-format gate). They are not runnable exemplars, so they are not under this dir; a
future exemplar that exercises the same slice can reuse them as its recorded-trace source.
