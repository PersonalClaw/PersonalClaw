# Slice 1 — Pure frontier core, engine, journal

**What the slice added.** The executable heart: the pure `frontier()` scheduler (which
nodes are ready, respecting data dependencies and lane caps), the node dispatchers
(transform / infer / branch / stage / wait / gate), the `RunController` lifecycle with
terminal-status ownership, and the journal (epoch + inputs-hash keyed resume cache; Run
Ledger emission). Slice 1's own acceptance criterion was "a simple sequence of 2 stages →
completion"; this exemplar runs a 3-node sequence with a binding leg between each.

**What this exemplar proves.** One `seed → think → final` sequence driven end to end against
a temp home with only the model call faked:

- the frontier scheduled the nodes in dependency order (a single `infer` call, `"double 7"`,
  proves `think` ran after `seed` and saw its bound value — not the raw `{{...}}` template);
- bindings threaded node to node (`final` consumed `think`'s output);
- every node reached `DONE` and the run reached `COMPLETE`;
- the run row carries terminal metadata (started/completed timestamps);
- the journal recorded one `STEP_COMPLETED` per node — the Run Ledger the flywheel and the
  UI read.

**Mechanism under test:** `personalclaw.workflows.controller.RunController` +
`engine` dispatchers + `journal` ledger emission.

**Recorded-trace reuse:** this clean 3-node run is the natural source for a
`workflow-journal-projection`-style replay scenario (a happy-path event fold).
