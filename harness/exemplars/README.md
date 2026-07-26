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

**Empty until Workflows-v2 lands.** The exemplars exercise engine slices that don't exist
yet (the WF2 journal/run-ledger/required-artifacts machinery). This directory + its
contract ship now (Self-Verification Session 4) so that when WF2 Slice 0 lands, its
exemplar has a home and the obligation is already in force. The two WF2-gated replay
scenarios (`workflow-journal-projection`, `rewind-during-stream`) are recorded here as
exemplars when those slices land — see the Self-Verification plan's S4 BLOCKED note.
