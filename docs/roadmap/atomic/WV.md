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
| `WV-9` | ✅ | WF2-A2 — node inspection endpoint returning resolved prompt/inputs/output/attempts/ledger slice | `WV-6`, `WV-7` | GET /api/workflows/runs/{id}/nodes/{node_id}/inspect returns the §5 reconstructability set (resolved_prompt\|ref, resolved_inputs, output\|artifact_ref, attempts, ledger_events, cached) for any terminal node with secrets absent (RedactingSink fixture); api.ts method added |
| `WV-10` | ✅ | WF2-A3 — FE inspector drawer (run detail + widget node rows) + cached-badge rendering | `WV-9`, `WV-7` | a user can open any node in WorkflowRunDetail + WorkflowProgressCard and read its exact resolved prompt/inputs/output; cached nodes render a visually distinct badge (workflowFold.ts cached? finally read) |
| `WV-11` | ✅ | Output-offloading writer + {{nodes.x.artifact}} population + artifact_inspect action provider | `WV-3`, `WV-8` | node outputs over threshold keep head/tail in journal and write body to runs/<id>/artifacts/, populating bindings.node_artifacts so {{nodes.x.artifact}} resolves to a live pointer; artifact_inspect action provider registered (registry + ALLOWED_HOOK_PROVIDERS + validation schema) pulls artifact content on demand |
| `WV-12` | ✅ | Two-layer context-compaction ladder for LLM-backed nodes | `WV-3`, `WV-8`, `EXT:CONTEXT-ECONOMY:cheap-summarizer/compaction seam (queue records it does not exist yet)` | proactive compaction at ~80% of the bound model window via a cheap summarizer, then error-triggered aggressive re-compaction before failing the node, degrade-to-drop-with-placeholder if the summarizer fails — driven end to end on a long-horizon template |
| `WV-13` | ✅ | Give `on_item_error: collect` an executor + an exhaustiveness ratchet over `ItemErrorPolicy` | `WV-4` | `tick.foreach_outcome` branches on every `ItemErrorPolicy` member and RAISES on an unmapped one; `collect` runs every item then fails the container, and its per-item failures land in the ledger as one `items_collected` record; the three policies produce three DIFFERENT run-level observables for one seeded failing item, driven through the real controller; `enum:ItemErrorPolicy.COLLECT` leaves `inert-surface-baseline.json` |
| `WV-14` | ✅ | Make `on_overlap: queue` queue instead of starting a concurrent run + an exhaustiveness ratchet over `OverlapPolicy` | `WV-3`, `WV-4` | `overlap.decide` branches on every `OverlapPolicy` member and RAISES on an unmapped one; a `queue` start with a prior in flight PERSISTS an unlaunched run (DRAFT + a marker on `run.extra`) and returns `outcome: "queued"` naming it, instead of launching beside the prior; `overlap.drain` starts it from the controller's terminal write and from the watchdog poll, single-flight and idempotent; a hand-made DRAFT with no marker is never launched; the queue is capped at one and a dropped start names the cap in its outcome and the log; `enum:OverlapPolicy.QUEUE` leaves `inert-surface-baseline.json` |
| `WV-15` | ✅ | Map every status a fire writes, and an AST rail over the three status→outcome tables | `WV-14` | `HOOK_STATUS_TO_OUTCOME` names `launched` + `skipped_incident` and `SCHEDULE_STATUS_TO_OUTCOME` names `blocked_injection` + all six `INERT_OUTCOMES`, so no live status reaches a `.get()` fallback; both fallbacks LOG the status they could not classify and never return `ran`; a suppressed or screened row is `LEDGER` weight and lands in `feed_response`'s suppressed half; `tests/test_triggers_status_vocabulary.py` infers each writer's possible values from its own AST (conditional expressions, `in`/`not in` guards, local names) with per-writer vacuity floors and a pinned writer-file census, and reds on all nine statuses if the tables are reverted |

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

### `WV-14` — Make `on_overlap: queue` queue instead of starting a concurrent run + an exhaustiveness ratchet over `OverlapPolicy`

**Status:** done

§2 trigger-origin starts (`on_overlap`, owned by the run-workflow provider re-added in Slice 3)

**Done when:** `overlap.decide` branches on every `OverlapPolicy` member and RAISES on an unmapped one; a `queue` start with a prior in flight PERSISTS an unlaunched run (DRAFT + a marker on `run.extra`) and returns `outcome: "queued"` naming it, instead of launching beside the prior; `overlap.drain` starts it from the controller's terminal write and from the watchdog poll, single-flight and idempotent; a hand-made DRAFT with no marker is never launched; the queue is capped at one and a dropped start names the cap in its outcome and the log; `enum:OverlapPolicy.QUEUE` leaves `inert-surface-baseline.json`

**Design**

`OverlapPolicy.QUEUE` did the exact OPPOSITE of its name. `run_workflow_provider` compared
against `SKIP` (return early, nothing starts) and `CANCEL_PREVIOUS` (cancel the priors, then
start) and let `queue` **match neither branch and fall straight through to `store.create` +
`_launch`** — so the one policy whose name promises ordering started a CONCURRENT run beside the
still-running prior, silently. That directly violates the enum's own docstring
(`SKIP = "skip"  # default — a per-minute trigger must not stack runs`): a per-minute trigger with
`on_overlap: queue` against a slow workflow stacked runs without bound. It was reachable and
round-tripped, not theoretical — `models.py` parses and serializes `on_overlap`, `native_defs.py`
accepts it from a def payload, and `inert-surface-baseline.json` had listed
`enum:OverlapPolicy.QUEUE` as inert for the length of the program.

**Semantics, one line each.** SKIP (default) — a prior is in flight ⇒ nothing is created and
nothing starts. QUEUE — a prior is in flight ⇒ the start is PERSISTED as an unlaunched run and
started when that prior ends; ordering, not concurrency. CANCEL_PREVIOUS — a prior is in flight ⇒
cancel it, then start now; the newest fire wins. With a free def all three start immediately —
`queue` is not `always queue`, or a per-hour trigger against a one-minute workflow would never
start anything directly.

**The queue is a marked DRAFT run, not a new status.** `RunStatus.DRAFT` is where an unlaunched run
already lives, so no state-machine member and no frontend mapping changes (the FE renders it with
its existing `Draft` badge). But DRAFT is *also* where a user's deliberately-unstarted editor draft
sits, so a drain keyed on "DRAFT for this def" would start work the user never asked to start —
the worst available outcome of this atom. Queued-ness is therefore an explicit marker,
`run.extra["overlap_queued"]`, and `extra` is a persisted JSON column, so it survives a restart
with the row. Rejected: a new `RunStatus.QUEUED` (a state-machine change — `active_runs`,
`TERMINAL_RUN_STATUSES`, `_ROOT_TO_RUN`, `materialize`'s exhaustive state→status table and the FE
status union/badge `Record` all switch on it, and none of them need to); `RunOrigin` (it says WHO
started a run, not what it waits on); a journal fact (durable but unqueryable — "which drafts are
queued" would mean opening every draft's ledger).

**The drain is single-flight and idempotent, reusing `concurrency.single_flight`** — the same flock
the claim leases are built on — rather than a new lock. Three guards, cheapest first: the flock
(cross-process, and cross-coroutine because flock conflicts across separate open file descriptions
in one process); the supervisor's controller registry, since `watchdog.launch` already returns the
EXISTING controller for a run id it holds (which also covers the window before a new controller has
written RUNNING, when `active_runs()` still reads empty); and an active re-check *inside* the lock.
Two live call sites: `controller._finish` after the terminal status is written (the moment the def
stops being busy — awaited inline, since a floating task makes the handoff untestable, and fully
guarded because `_finish` is the single terminal writer and must not raise), and the watchdog's 5s
poll after adoption.

**Cap: one — coalesce-to-one.** Depth 1 keeps the promise the name makes (the fire is not dropped;
it runs next) while bounding the backlog. Unbounded depth does not: run N+2 does the same work as
run N+1 with staler inputs, and a workflow that once ran long would spend hours replaying trigger
fires whose reason has expired — one late run silently becoming a multi-hour backlog. A dropped
start is loud in both places: the returned outcome carries `dropped/reason: "queue_full"/
queue_depth/max_queue_depth/queued_run_id`, and the provider logs a WARNING. It stays
`outcome: "skip"` (nothing durable was created) rather than minting a fourth vocabulary member.

**Restart: the queue IS re-drained, with a ≤5s bound.** A queued start is a durable DRAFT row plus
its spec file, and the watchdog's poll calls `drain_all`, so the first poll after the gateway comes
back drains whatever was pending — no separate boot hook. The one thing deliberately NOT preserved:
if the prior was suspended to PAUSED by the boot sweep, PAUSED counts as active, so the queued run
waits for an explicit Resume rather than overtaking a run that is not finished. A queued run whose
spec directory vanished is FAILED rather than left queued — a queue head the drain can never launch
would be re-examined every poll forever and would block every start behind it.

**`outcome: "queued"` is a new vocabulary member, and an unmapped status is recorded as FAILED.**
`triggers.executor._record_fire_outcome` maps an unrecognized runner status to `Outcome.FAILED`, so
the member ships with every reader updated in the same change: `STATUS_TO_OUTCOME` and
`SCHEDULE_STATUS_TO_OUTCOME`/`HOOK_STATUS_TO_OUTCOME` → `Outcome.DEFERRED` (its "parked /
resource-busy" half) with their own reason string, `hooks.py` and the manual-fire handler pass it
through instead of folding it into `ok`/`success`, and `engine.dispatch_action` maps it to DEGRADED
(a DONE node would tell the frontier this action's work completed). `"skip"` would under-report a
real run record; `"launched"` would claim work that has not begun.

**Exhaustiveness.** The decision moves into one named function, `overlap.decide(policy, active=,
queued=)`, which names every member and raises on an unmapped one; the provider's call site also
refuses an `OverlapAction` it has no branch for, before anything is created, because the dangerous
default there is "fall through and launch". Four tests hold it: every member driven, the raise
asserted, an AST read of `decide` asserting it names every member, and a source check that every
`OverlapAction` member has a call site.

**Implementation plan**

1. `models.py` — document each `OverlapPolicy` member (three bare values, one carrying a comment)
   and point at the one function that decides.
2. `overlap.py` (new) — `decide()` exhaustive with a raising tail; `OverlapAction`; the marker
   (`QUEUED_KEY`/`queued_extra`/`is_queued`), `queued_runs`/`queued_depth`/`queued_names`;
   `drain(name, supervisor)` under `single_flight` and `drain_all(supervisor)`.
3. `run_workflow_provider.py` — replace the two-way comparison with `decide`; the QUEUE branch
   creates with the marker in the same INSERT and returns `outcome="queued"`; the DROP branch
   returns and logs the cap; `dry_run` moves ahead of the write paths and names the decided action.
4. `controller.py` — `_drain_overlap_queue()` off `_finish`, gated on a terminal status, awaited
   inline and fully guarded (WF2-R10).
5. `watchdog.py` — `overlap.drain_all(self)` at the end of `_poll_once`, after adoption.
6. The `"queued"` outcome vocabulary: `action_providers/base.py`, `triggers/executor.py`,
   `triggers/history.py`, `hooks.py`, `dashboard/handlers/triggers.py`, `workflows/engine.py`.
7. `tests/test_workflows_overlap_queue.py` — the three-policy observables, the cap, the drain on
   the real controller path, the fresh-watchdog restart drain, the hand-made-draft safety test, and
   the ratchet.
8. Regenerate `inert-surface-baseline.json` (151 → 150; `enum` 24 → 23) and add the `overlap.py`
   row to `docs/architecture/workflows.md`'s module table.

### `WV-15` — Map every status a fire writes, and an AST rail over the three status→outcome tables

**Status:** done

§7 criterion 4 (one run-history feed, typed outcomes) + criterion 8 (zero silent drops)

**Done when:** `HOOK_STATUS_TO_OUTCOME` names `launched` + `skipped_incident` and `SCHEDULE_STATUS_TO_OUTCOME` names `blocked_injection` + all six `INERT_OUTCOMES`, so no live status reaches a `.get()` fallback; both fallbacks LOG the status they could not classify and never return `ran`; a suppressed or screened row is `LEDGER` weight and lands in `feed_response`'s suppressed half; `tests/test_triggers_status_vocabulary.py` infers each writer's possible values from its own AST (conditional expressions, `in`/`not in` guards, local names) with per-writer vacuity floors and a pinned writer-file census, and reds on all nine statuses if the tables are reverted

**Design**

**The finding: a projection table is only as honest as the writers it was authored against.**
`triggers/history.py` translates each store's own status word into a `FIRE_OUTCOMES` member, and
both projections read their table with a `.get(status, <fallback>)`. WV-14 added `queued` to all
three tables because a NEW member's ripple is obvious. The statuses that were already being written
when the tables were authored are the ones nobody re-checked, and nine were live:

| writer | status | projected as | what a user saw |
|---|---|---|---|
| `hooks.py:590` | `skipped_incident` | `RAN if last_run` | "ran" — for a hook the incident kill switch stopped BEFORE dispatch |
| `hooks.py:653` | `launched` | `RAN if last_run` | "ran" — for a background turn nobody has seen |
| `gateway.py:1429` | `blocked_injection` | `FAILED` | a defended injection attempt as a broken automation |
| `service.py:665` | the six `INERT_OUTCOMES` | `FAILED` | a quiet-hours skip as a red failure, in the `did` half |

**A silent fallback is the defect, not the missing key.** Both fallbacks were individually
defensible — `hook_to_record`'s `RAN if last_run` reads "we know it ran, we just lack the verdict",
and `schedule_run_to_record`'s `FAILED` follows `FireRecord.from_dict`'s "unclassifiable must not
count as a success". Neither says anything, which is why four statuses (nine values) sat unmapped
across two WV sessions that edited these very tables. So the fallbacks now LOG the status they
could not classify, and the hook path splits into three explicit cases — mapped, absent (`""`, a row
written before the field existed, the one case that still reads `RAN`), and unmapped-and-loud
(`FAILED` + a warning). No branch swallows a value silently.

**The mappings, and what a user now sees instead of "ran".**

* `launched` → `DEFERRED` (hook table). Matches the other two tables exactly; T7's whole point is
  that started ≠ succeeded, and the background turn records its own outcome in its own run. The
  user sees `launched`-blue "deferred" with "outcome not yet known" instead of a green tick.
* `skipped_incident` → `SKIPPED_GATE`. The provider is never called, so nothing ran, nothing was
  spent, nothing changed — `SKIPPED_GATE`'s family (quiet-hours / cooldown / condition-false) and,
  through `INERT_OUTCOMES`, a ledger row that folds out of the default runs inbox. NOT `REFUSED`
  (which `blocked` already carries: a denylist refusal is a verdict on THIS action, while an
  incident suspends every automated action and lifts on its own) and NOT `DEFERRED` (deferred work
  still starts; this fire is dropped and never retried). The user sees a neutral grey suppression
  row naming the incident, in the archived half, instead of a green "ran".
* `blocked_injection` → `BLOCKED_INJECTION`. The member exists for exactly this row. The user sees
  the red-shield "blocked" badge `statusMeta` already renders, instead of "failed" — and it leaves
  `TRUE_FAILURE_OUTCOMES`, so a defence stops looking like a fault.
* the six `INERT_OUTCOMES` → themselves, by construction (`**{v: v for v in sorted(INERT_OUTCOMES)}`)
  rather than six hand-copied lines: the writer's own guard is `outcome not in INERT_OUTCOMES:
  return`, so that set IS the vocabulary and a seventh member needs no second edit. The user sees
  each suppression as the suppression it was, folded into `suppressed_ids`, with the reason
  `_record_suppression_row` already stored — instead of six kinds of red "failed" in the `did` half.

**No new vocabulary member, deliberately.** Every one of the nine landed on an existing `Outcome`;
WV-14 paid the cost of adding one (three tables plus `engine.dispatch_action` plus the FE union) and
nothing here needed it. `weight` moved instead: `LEDGER_WEIGHT_OUTCOMES` (deferred ∪ blocked ∪
inert) replaces the hook projection's hardcoded `FULL` and the schedule projection's
`DEFERRED`-only check, because `FULL` claims "earned a run directory and a journal" and a fire that
never reached a runner has no exit code to show.

**Autopause is untouched, and that is a finding worth recording.** `autopause
.consecutive_failures_from` reads the RAW store rows (`trigger` → `outcome` → `status`), not
`FireRecord.outcome`, and already skips inert exit types. So the mis-projection never spent the
failure budget — it was purely a history/UI lie, and the fix does not move a single autopause
decision.

**The rail is AST-based because a regex finds only the easy half.** A `grep` over `hooks.py` finds
`skipped_incident` and misses `launched`, whose write is
`hook.last_status = result.outcome if result.outcome in ("launched", "queued") else "ok"` — a NAME
whose values live in the condition's `in` tuple. Two of the four writers have that shape; a third
(`service.py`) writes a variable pinned by an early-return `not in` guard; a fourth (the manual-fire
handler) writes a local assigned in four branches and passed as a keyword. So the rail infers each
write's POSSIBLE VALUES: constants, both arms of a conditional expression, `str()`/`or` unwrapping,
local-name resolution, and `in`/`not in` guards — with the guard checked BEFORE the expression,
because `status=outcome` resolves to `{""}` through its assignment and to the right six through its
guard. The boolean handling is asymmetric on purpose: `A and B` holding implies B, and `not (A or
B)` implies `not B`; nothing else proves anything about one operand, so nothing else is guessed.
Anything unresolvable FAILS rather than counting as clean, since "I could not tell" is how these
got in.

**Vacuity and the second drift direction.** Each writer carries `min_sites`/`min_values` floors
measured against the real source (8/7, 2/3, 1/6, 1/4, 2/2), so a shape change that stops matching
reds instead of reading as clean. And `test_the_writer_file_census_is_pinned` enumerates every
module that assigns `.last_status` or constructs a `ScheduleRun`, because a NEW writer file is drift
no per-file scan can see. The executor table's writer set is honestly partial and says so: its
runner is injected, so an app provider or `_http_runner`'s HTTP body is out of static reach —
`classify`'s unrecognized→FAILED rule covers the open half, and the rail covers the one in-repo
runner that fires every clock trigger.

**Implementation plan**

1. `triggers/history.py` — the four table entries (each with a comment saying why that outcome and
   not the neighbouring one), `LEDGER_WEIGHT_OUTCOMES`, `_hook_reason` (reason strings shared
   word-for-word with the schedule path), the three-case hook fallback, and a warning on both
   unmapped paths.
2. `tests/test_triggers_status_vocabulary.py` (new) — the writer census, the inference, the two
   rails, the file-census pin, a self-test of the shape that hid `launched`, and the
   `INERT_OUTCOMES` name-resolution test.
3. `tests/test_triggers_history.py` — behavioural coverage per status: deferred not ran, incident
   skip inert + archived, unmapped is failed AND loud, absent status still reads `ran`, screened not
   failed, the six suppressions parametrised, and the end-to-end fold in `feed_response`.
4. No `web/` change: `scheduleMeta.statusMeta` already renders `deferred`, `blocked_injection` and
   the `skipped_` family, and no new `Outcome` member means the FE union is unchanged.
