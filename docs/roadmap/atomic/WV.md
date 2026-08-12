# WORKFLOWS-V2 — atomic plans

**Source plan:** [`WORKFLOWS-V2`](../plans/WORKFLOWS-V2.md)  
**Code:** `WV`  
**Source status:** in_progress



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WV-1` | ✅ | Phase 0+1 clean break: relocate shared code, delete old SOP feature, repoint workflow _TypeHandler, archive legacy SOPs | — | prompt_render→mcp_prompts + resolve_agent_id→agents/identity relocated; whole old workflows/ package + mcp_workflows + run_workflow_provider + 3 native apps + FE dir + 8 tests deleted; workflow _TypeHandler repointed at v2 workflows/defs.py so PROVIDER_TYPES↔handler parity holds; legacy SOPs archived to _legacy_sops/; make test green (9698 passed) |
| `WV-2` | ✅ | Slice 0 — data model + store + bindings + validator + config wiring | `WV-1` | models.py (Node taxonomy incl. infer/branch, outcome model), store.py (SQLite WAL + (root_run_id,status) index), bindings.py (two resolution paths, closed pipes, typed BindingError, untrusted-origin lint), validator.py (never-throw, stable codes, Kahn levels, case-coverage); WorkflowsConfig wired through all 4 points; make test 9808 passed, 110 new tests |
| `WV-3` | ✅ | Slice 1 — pure frontier core + engine + journal + watchdog + resume/crash recovery | `WV-2` | tick.py frontier (lanes + declined-edge join gating), controller.py (single-writer terminal-write ownership), engine.py dispatchers, journal.py (epoch/inputs-hash cache + Run Ledger + step_cached), watchdog.py 5s poll + orphan reap + retention; __wf_depth max-3 enforcement; validated against real Bedrock provider; make test 9973 passed |
| `WV-4` | ✅ | Slices 2+3 — outcome model + engine-owned completion + resilience/budgets/timeouts + effect ledger + write-scope + termination + secrets | `WV-3` | extended outcome states, verification ladder + required_artifacts + fresh-judge + closed verdict enum, mutation-hint retries + circuit breaker + soft budgets + baseline_check, two-knob timeouts; effect ledger idempotency keys + BYOI teardown, v2 run-workflow provider in ALLOWED_HOOK_PROVIDERS, fs-diff scope_violation, sticky CANCEL + workflow_audit, {{secret:KEY}} + RedactingSink |
| `WV-5` | ✅ | Slices 4+5 — mid-flight mutation + checkpoints + fork + human-input contract + gates | `WV-3` | mutations.py typed ops incl. run_from + binding-dependency cascade preview + rollback/revert + TOCTOU re-verify + grammar hardening; checkpoints + fork isolation; typed ask payload + mode-dependent gate timeouts + continuation records/durable resume tokens + atomic answers + gate{kind:event} transient-hold |
| `WV-6` | ✅ | Slices 6+7 — 19 chat tools + spec ingestion + [ACTIVE WORKFLOWS] injection + HTTP API + FE list/detail pages | `WV-4`, `WV-5` | mcp_workflows.py all 19 tools wired into _AGGREGATED_CATEGORY_MODULES + validation schemas; strict-mode repromptable ingestion + dry-run + preflight + generated manifest w/ CI drift test; handlers.py REST routes registered + per-run SseRegistry; FE workflows pages + api.ts methods + nav entry |
| `WV-7` | ✅ | Slice 8 — live chat widget + event pipeline (dedup/fold/coalesce) incl. WF2-A1 step_cached emission | `WV-6` | WorkflowProgressCard mirrors SdlcProgressCard; dedup keys + deterministic event ids + event-fold law + epoch-tagged supersede + ~25ms coalescing + result_omitted spill; FE lifecycle-union registration + backend⊆FE-union test; typed ask-payload renderer; step_cached ledger event + cached flag on workflow_node_done |
| `WV-8` | ✅ | Slices 9+10+11 — templates + conventions pack + advanced constructs + context-lifecycle (partial) + validation/hardening | `WV-7` | 6 bundled templates + macros + Finding record + shared prompt blocks + template-lint; foreach pipeline/loop until_dry/subworkflow nesting; session:fresh handoffs + carryover buckets + decision records (offloading+compaction deferred); timeout-fires + active-edge + journal-replay regression harnesses green; S147/S148 stall-window + allow_failure template-config fixes landed |
| `WV-9` | ⬜ | WF2-A2 — node inspection endpoint returning resolved prompt/inputs/output/attempts/ledger slice | `WV-6`, `WV-7` | GET /api/workflows/runs/{id}/nodes/{node_id}/inspect returns the §5 reconstructability set (resolved_prompt\|ref, resolved_inputs, output\|artifact_ref, attempts, ledger_events, cached) for any terminal node with secrets absent (RedactingSink fixture); api.ts method added |
| `WV-10` | ⬜ | WF2-A3 — FE inspector drawer (run detail + widget node rows) + cached-badge rendering | `WV-9`, `WV-7` | a user can open any node in WorkflowRunDetail + WorkflowProgressCard and read its exact resolved prompt/inputs/output; cached nodes render a visually distinct badge (workflowFold.ts cached? finally read) |
| `WV-11` | ⬜ | Output-offloading writer + {{nodes.x.artifact}} population + artifact_inspect action provider | `WV-3`, `WV-8` | node outputs over threshold keep head/tail in journal and write body to runs/<id>/artifacts/, populating bindings.node_artifacts so {{nodes.x.artifact}} resolves to a live pointer; artifact_inspect action provider registered (registry + ALLOWED_HOOK_PROVIDERS + validation schema) pulls artifact content on demand |
| `WV-12` | ✅ | Two-layer context-compaction ladder for LLM-backed nodes | `WV-3`, `WV-8`, `EXT:CONTEXT-ECONOMY:cheap-summarizer/compaction seam (queue records it does not exist yet)` | proactive compaction at ~80% of the bound model window via a cheap summarizer, then error-triggered aggressive re-compaction before failing the node, degrade-to-drop-with-placeholder if the summarizer fails — driven end to end on a long-horizon template |
| `WV-13` | ✅ | Give `on_item_error: collect` an executor + an exhaustiveness ratchet over `ItemErrorPolicy` | `WV-4` | `tick.foreach_outcome` branches on every `ItemErrorPolicy` member and RAISES on an unmapped one; `collect` runs every item then fails the container, and its per-item failures land in the ledger as one `items_collected` record; the three policies produce three DIFFERENT run-level observables for one seeded failing item, driven through the real controller; `enum:ItemErrorPolicy.COLLECT` leaves `inert-surface-baseline.json` |

## Atom scopes

### `WV-1` — Phase 0+1 clean break: relocate shared code, delete old SOP feature, repoint workflow _TypeHandler, archive legacy SOPs

**Status:** done

§7 Phase 0 (relocate) + Phase 1 (delete)

**Done when:** prompt_render→mcp_prompts + resolve_agent_id→agents/identity relocated; whole old workflows/ package + mcp_workflows + run_workflow_provider + 3 native apps + FE dir + 8 tests deleted; workflow _TypeHandler repointed at v2 workflows/defs.py so PROVIDER_TYPES↔handler parity holds; legacy SOPs archived to _legacy_sops/; make test green (9698 passed)

### `WV-2` — Slice 0 — data model + store + bindings + validator + config wiring

**Status:** done

Slice 0

**Done when:** models.py (Node taxonomy incl. infer/branch, outcome model), store.py (SQLite WAL + (root_run_id,status) index), bindings.py (two resolution paths, closed pipes, typed BindingError, untrusted-origin lint), validator.py (never-throw, stable codes, Kahn levels, case-coverage); WorkflowsConfig wired through all 4 points; make test 9808 passed, 110 new tests

### `WV-3` — Slice 1 — pure frontier core + engine + journal + watchdog + resume/crash recovery

**Status:** done

Slice 1

**Done when:** tick.py frontier (lanes + declined-edge join gating), controller.py (single-writer terminal-write ownership), engine.py dispatchers, journal.py (epoch/inputs-hash cache + Run Ledger + step_cached), watchdog.py 5s poll + orphan reap + retention; __wf_depth max-3 enforcement; validated against real Bedrock provider; make test 9973 passed

### `WV-4` — Slices 2+3 — outcome model + engine-owned completion + resilience/budgets/timeouts + effect ledger + write-scope + termination + secrets

**Status:** done

Slice 2 + Slice 3

**Done when:** extended outcome states, verification ladder + required_artifacts + fresh-judge + closed verdict enum, mutation-hint retries + circuit breaker + soft budgets + baseline_check, two-knob timeouts; effect ledger idempotency keys + BYOI teardown, v2 run-workflow provider in ALLOWED_HOOK_PROVIDERS, fs-diff scope_violation, sticky CANCEL + workflow_audit, {{secret:KEY}} + RedactingSink

### `WV-5` — Slices 4+5 — mid-flight mutation + checkpoints + fork + human-input contract + gates

**Status:** done

Slice 4 + Slice 5

**Done when:** mutations.py typed ops incl. run_from + binding-dependency cascade preview + rollback/revert + TOCTOU re-verify + grammar hardening; checkpoints + fork isolation; typed ask payload + mode-dependent gate timeouts + continuation records/durable resume tokens + atomic answers + gate{kind:event} transient-hold

### `WV-6` — Slices 6+7 — 19 chat tools + spec ingestion + [ACTIVE WORKFLOWS] injection + HTTP API + FE list/detail pages

**Status:** done

Slice 6 + Slice 7

**Done when:** mcp_workflows.py all 19 tools wired into _AGGREGATED_CATEGORY_MODULES + validation schemas; strict-mode repromptable ingestion + dry-run + preflight + generated manifest w/ CI drift test; handlers.py REST routes registered + per-run SseRegistry; FE workflows pages + api.ts methods + nav entry

### `WV-7` — Slice 8 — live chat widget + event pipeline (dedup/fold/coalesce) incl. WF2-A1 step_cached emission

**Status:** done

Slice 8 + Amendment WF2-A1

**Done when:** WorkflowProgressCard mirrors SdlcProgressCard; dedup keys + deterministic event ids + event-fold law + epoch-tagged supersede + ~25ms coalescing + result_omitted spill; FE lifecycle-union registration + backend⊆FE-union test; typed ask-payload renderer; step_cached ledger event + cached flag on workflow_node_done

### `WV-8` — Slices 9+10+11 — templates + conventions pack + advanced constructs + context-lifecycle (partial) + validation/hardening

**Status:** done

Slice 9 + Slice 10 + Slice 11

**Done when:** 6 bundled templates + macros + Finding record + shared prompt blocks + template-lint; foreach pipeline/loop until_dry/subworkflow nesting; session:fresh handoffs + carryover buckets + decision records (offloading+compaction deferred); timeout-fires + active-edge + journal-replay regression harnesses green; S147/S148 stall-window + allow_failure template-config fixes landed

### `WV-9` — WF2-A2 — node inspection endpoint returning resolved prompt/inputs/output/attempts/ledger slice

**Status:** todo

Amendment (2026-07-26) WF2-A2

**Done when:** GET /api/workflows/runs/{id}/nodes/{node_id}/inspect returns the §5 reconstructability set (resolved_prompt|ref, resolved_inputs, output|artifact_ref, attempts, ledger_events, cached) for any terminal node with secrets absent (RedactingSink fixture); api.ts method added

### `WV-10` — WF2-A3 — FE inspector drawer (run detail + widget node rows) + cached-badge rendering

**Status:** todo

Amendment (2026-07-26) WF2-A3

**Done when:** a user can open any node in WorkflowRunDetail + WorkflowProgressCard and read its exact resolved prompt/inputs/output; cached nodes render a visually distinct badge (workflowFold.ts cached? finally read)

### `WV-11` — Output-offloading writer + {{nodes.x.artifact}} population + artifact_inspect action provider

**Status:** todo

§2 Context Lifecycle (output offloading) + §1 binding {{nodes.x.artifact}}

**Done when:** node outputs over threshold keep head/tail in journal and write body to runs/<id>/artifacts/, populating bindings.node_artifacts so {{nodes.x.artifact}} resolves to a live pointer; artifact_inspect action provider registered (registry + ALLOWED_HOOK_PROVIDERS + validation schema) pulls artifact content on demand

### `WV-12` — Two-layer context-compaction ladder for LLM-backed nodes

**Status:** done

§2 Context Lifecycle (two-layer context ladder)

**Done when:** proactive compaction at ~80% of the bound model window via a cheap summarizer, then error-triggered aggressive re-compaction before failing the node, degrade-to-drop-with-placeholder if the summarizer fails — driven end to end on a long-horizon template


### `WV-13` — Give `on_item_error: collect` an executor + an exhaustiveness ratchet over `ItemErrorPolicy`

**Status:** done

§2 `foreach` item-error policy (Slice 2c's `on_item_error`, completed)

**Done when:** `tick.foreach_outcome` branches on every `ItemErrorPolicy` member and RAISES on an unmapped one; `collect` runs every item then fails the container, and its per-item failures land in the ledger as one `items_collected` record; the three policies produce three DIFFERENT run-level observables for one seeded failing item, driven through the real controller; `enum:ItemErrorPolicy.COLLECT` leaves `inert-surface-baseline.json`

**Design**

`COLLECT` was a declared strategy with no executor, and — unlike most inert surfaces — it was
*advertised*: `validator.py` accepted `on_item_error: collect` and `service.capabilities`
published `item_error_policies=[p.value for p in ItemErrorPolicy]`, which is the catalog an
authoring model reads. Meanwhile `tick.py` compared against `HALT` (stop scheduling) and `SKIP`
(tolerate → DEGRADED) only, and `collect` fell through to `container_outcome`. So an author who
took the catalog at its word got behaviour nothing had ever specified.

The semantics, chosen and now implemented: **`COLLECT` = run every item to a terminal state
(never halt early, like SKIP), then FAIL the container if any item failed (unlike SKIP's
DEGRADED), with the failures recorded as data.** SKIP means "I do not care about the failures";
COLLECT means "run everything, then hand me the failures". That is the only reading under which
the member earns its place beside the other two.

**FAILED, not DEGRADED.** `controller._ROOT_TO_RUN` maps `DEGRADED → COMPLETE` and
`FAILED → FAILED`. A DEGRADED terminal would make COLLECT's run-level observable *identical* to
SKIP's, so the one policy whose entire point is that the failures matter would report success —
the silent-drop shape. The branch returns `container_outcome(item_states)` rather than a
hard-coded `FAILED`, so a CANCELLED or BLOCKED item still reports the more severe verdict off
`_worst`'s severity order instead of being flattened into "the fan-out failed".

**Errors-as-data: journal, not a container output.** There is no container output surface to put
them in. `controller._outputs` is keyed by node id and written only where a LEAF completes (a
dispatch result, a resolved `wait`, an answered gate), the frontier only ever yields leaves, and
a container deliberately has NO stored instance — its state is always derived so a rewind cannot
leave a stale verdict. Publishing under the foreach's node id would make
`{{nodes.<foreach>.output}}` resolve from memory and then resolve to nothing after a restart
(rehydration reads `inst.output_ref`; a container has none) — a live reader of an unwritten key.
Inventing that surface is out of this atom's scope, so the failures go where the run already
keeps per-node truth: one `items_collected` ledger record per fan-out per epoch, carrying
`item_index`, `item_label`, `instance_path`, `node_id`, `failure_class` and `cause` per failed
instance.

**Follow-up for the data half** (deliberately not built here): a downstream node is already
*reachable* — a sequence continues past a terminal failed child unless it declares
`on_error: fail_run`, so the node after a collect fan-out runs. What is missing is a way to BIND
the collected set into it. That needs (1) a durable container-output surface — a container
instance (or an equivalent persisted container-outputs map) written at the derivation moment and
restored on rehydrate, so `{{nodes.<fan>.output.failures}}` survives a restart; or (2) a
read-only ledger action provider, which is the cheaper of the two and reuses an existing shape.
Either is its own atom, with its own rewind semantics to get right.

**Exhaustiveness.** The decision moves into one named function, `tick.foreach_outcome(policy,
item_states)`, which enumerates all three members and raises on an unmapped one. Three tests
hold it: every member driven through the function, the raise asserted, and an AST read of
`foreach_outcome` asserting it names every member (a behavioural test alone would pass a
fallthrough shared by two members).

**Implementation plan**

1. `models.py` — document each `ItemErrorPolicy` member (the enum was three bare values); state
   the two axes and point at the one function that decides.
2. `tick.py` — add `foreach_outcome(policy, item_states)`: exhaustive over the enum, unreachable
   tail raises. Promote `_item_error_policy` → `item_error_policy` (the controller needs it).
   `_derive`'s FOREACH branch becomes one delegating line; `advance_foreach` keeps the
   scheduling half and says so.
3. `journal.py` — `ITEMS_COLLECTED` ledger kind + `items_collected(...)` writer, payload shape
   documented as the contract a later binding would surface.
4. `controller.py` — `_journal_collected_items(states)` off `_frontier`, mirroring
   `_journal_wip_holds`: walk collect fan-outs, derive, and on terminal write the record once,
   deduped by `path@epoch` and seeded from the ledger so a resume cannot double-count.
   `_item_failures(path)` reads the failed instances under `<path>.body#N`.
5. `tests/test_workflows_item_error_policy.py` — the three-way behavioural test (one seeded
   failing item, `max_concurrency: 1` so HALT is observable at all), the ledger-record
   assertions, and the exhaustiveness ratchet.
6. Regenerate `inert-surface-baseline.json` (152 → 151; `enum` 25 → 24).
