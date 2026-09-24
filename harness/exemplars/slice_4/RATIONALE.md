# Slice 4 — Mid-flight mutation, checkpoints, fork

**What the slice added.** Editing a run in flight: the mutation op types (incl. `run_from`),
the binding-dependency cascade closure, the resume cache's epoch/inputs-hash logic, rewind,
checkpoints and `fork`. The correctness core (WF2-R2) is that the cascade follows BINDINGS,
not the tree — a later sibling that consumes an edited node's output is not a tree
descendant, and a subtree-only reset would leave it holding a stale input: a silently
inconsistent run, the worst failure mode because nothing looks broken.

**What this exemplar proves.** Editing a node re-runs exactly its binding closure and
nothing else — shown two ways:

- **Pure:** `binding_closure` of `n2` in a `n1 → n2 → n3 (+ n_unrelated)` spec is `{n2, n3}`
  — the downstream consumer is included, the unrelated node is excluded.
- **End-to-end:** a real controller runs the spec to completion, `n2`'s prompt is edited, the
  run is resumed — and the ledger shows exactly two nodes re-ran (`n2` with its new prompt,
  then `n3`), the untouched prefix served from the resume cache (`STEP_CACHED` records). This
  is the acceptance bar Slice 4 set: the targeted-re-run claim is answerable from the
  LEDGER, not from logs.

**Mechanism under test:** `personalclaw.workflows.mutations.binding_closure` /
`cascade_preview` + the `RunController` resume cache keyed on (path, epoch, inputs, spec)
(WF2-R2 / WF2-A1).
