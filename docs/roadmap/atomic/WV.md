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
| `WV-12` | ⬜ | Two-layer context-compaction ladder for LLM-backed nodes | `WV-3`, `WV-8`, `EXT:CONTEXT-ECONOMY:cheap-summarizer/compaction seam (queue records it does not exist yet)` | proactive compaction at ~80% of the bound model window via a cheap summarizer, then error-triggered aggressive re-compaction before failing the node, degrade-to-drop-with-placeholder if the summarizer fails — driven end to end on a long-horizon template |

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

**Status:** todo

§2 Context Lifecycle (two-layer context ladder)

**Done when:** proactive compaction at ~80% of the bound model window via a cheap summarizer, then error-triggered aggressive re-compaction before failing the node, degrade-to-drop-with-placeholder if the summarizer fails — driven end to end on a long-horizon template

