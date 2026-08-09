# Slice 0 — Scaffold, data model, store, bindings, validator

**What the slice added.** The foundation the rest of the engine stands on: the `Node` /
`WorkflowRun` data model, the SQLite run store, the binding grammar and resolver, and the
spec-ingestion validator (never-throw structural pass, typed issue accumulation, stable
error codes, Kahn level grouping). No frontier and no engine yet — those are Slice 1.

**What this exemplar proves.** The three Slice-0 mechanisms, exercised directly (there is no
run to drive at this slice):

- **Validator, positive:** a well-formed acyclic spec validates clean and the validator
  returns its Kahn concurrency levels.
- **Validator, negative:** a spec whose node binds a non-existent id is reported as a typed
  `WF_UNKNOWN_NODE_REF` issue — and the validator does not throw, because its output is
  handed straight back to an author for repair. A run would only discover this at
  ready-time as a `BindingError`; catching it at authoring is free.
- **Binding grammar:** `{{nodes.a.output.n}}` interpolates into a string, and a whole-value
  reference (`{{nodes.a.output.items}}`) preserves the source type (a list stays a list, not
  a stringified one) — the type-preservation contract every downstream node depends on.

**Mechanism under test:** `personalclaw.workflows.validator.validate_spec` +
`personalclaw.workflows.bindings.resolve` / `BindingContext`.
