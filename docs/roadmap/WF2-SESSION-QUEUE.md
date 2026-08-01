# Workflows-V2 — session execution queue

**The single source of truth for autonomous execution order.** A nudge reads this file,
finds the first session whose status is not DONE, and executes it.

This file exists because 77 sessions do not fit in one context window. Every session ends
with its status written here, so the thread survives compaction: the queue is on disk, not
in anyone's head.

## Working rules (do not renegotiate these per session)

1. **Branch from the PREVIOUS session's branch, never from `main`.** Name it
   `feature-wf2-<slug>`. The stack merges in order with no conflicts.
2. **One atomic commit per PR.** Fixes to the same PR AMEND that commit and
   `git push --force-with-lease`. Never a second commit on the same PR — with one commit a
   squash-merge produces an identical tree, which is what keeps the next rebase trivial.
3. **A PR covers 2-3 related sessions** (the group is marked below). Commit each session's
   work into the group's single commit by amending.
4. **Gate before every commit:** `make lint` + `make test` green. No exceptions.
5. **Real-model validation every session** against the tiered Bedrock bindings
   (see below). If a session produces nothing observable yet, validate the seam it exposes
   — never claim validation that did not happen.
6. **`git commit -s`**, owner identity, no agent trailers.
7. Record DONE here + deviations in the owning plan's `## Execution log`.
8. **On a blocker:** if the work can proceed correctly without it, proceed and record a
   DEVIATION. If proceeding would break architecture or force a poor implementation, mark
   BLOCKED here with the reasoning and move to the next unblocked session. Never guess at
   an architectural decision to preserve momentum.

## Model tiers (bound + verified 2026-08-01)

| Workflow `model_tier` | Use case | Model |
|---|---|---|
| `fast` | `background` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `standard` | `orchestration` | `global.anthropic.claude-sonnet-5` |
| `reasoning` | `reasoning` | `global.anthropic.claude-opus-5` |

Also bound: `chat`/`code_tools` → sonnet-5, `loops` → opus-5. Embeddings available via
`global.cohere.embed-v4:0`. Dev home `.dev-home-bedrock`, `AWS_PROFILE=golani`.
Bare model ids are REFUSED by Bedrock — the `global.`/`us.` inference-profile prefix is
mandatory.

---

## A. The engine (`WORKFLOWS-V2.md`) — the contract owner, everything else waits on it

| # | Session | PR group | Status |
|---|---|---|---|
| — | Phase 0: relocate trapped shared code | — | ✅ DONE (#134) |
| — | Phase 1: delete the old feature | — | ✅ DONE (#135) |
| — | Slice 0: data model, store, bindings, validator (3 sess) | — | ✅ DONE (#136) |
| — | Slice 1: pure frontier, dispatchers, journal, watchdog (4 sess) | — | ✅ DONE (#137) |
| 1 | **Slice 2a** — extended outcome states through state/journal/ledger; typed attempt records + mutation-hint retries + structured retry payloads | G1 | ✅ DONE |
| 2 | **Slice 2b** — engine-owned completion: verification ladder, `required_artifacts`, `gate.verify{script}`, fresh-judge invariant, closed verdict enum | G1 | ✅ DONE |
| 3 | **Slice 2c** — deterministic circuit breaker in frontier; escalation artifact; budgets (node soft caps, extend gates, baseline_check, topology estimate); two-knob timeout warm-up split; foreach `on_item_error` + per-item checkpointing | G1 | ✅ DONE |
| 4 | **Slice 3a** — effect ledger: idempotency keys, effect_status, redo_effects gate, caller idempotency dedupe, BYOI teardown | G2 | ✅ DONE (#139) |
| 5 | **Slice 3b** — v2 `run-workflow` action provider + `ALLOWED_HOOK_PROVIDERS`; write-scope enforcement (pre/post fs diff, scope_violation); termination (sticky cancel, protocol-violation, `workflow_audit`); secrets (`{{secret:KEY}}`, `_has*` stripping, RedactingSink) | G2 | ✅ DONE (#140) |
| 6 | **Slice 4a** — `mutations.py`: op types incl. `run_from`, batch validator, spec-history writer, epoch/inputs-hash logic | G3 | ✅ DONE (#141) |
| 7 | **Slice 4b** — binding-dependency cascade closure, engine-computed preview, `inputs_stale`, rollback-vs-revert, TOCTOU re-verify; mutation queue in the controller; grammar hardening a-f | G3 | ✅ DONE (#142) |
| 8 | **Slice 4c** — rewind (archive outputs + journal region, epoch bump, memoized replay); checkpoints + `fork`; property tests (rewind idempotence, cascade = binding closure, fork isolation) | G3 | ✅ DONE (#143) |
| 9 | **Slice 5a** — typed ask payload, mode-dependent gate timeouts, `timed_out_unattended`; continuation records + durable resume tokens + expiry | G4 | ✅ DONE (#144) |
| 10 | **Slice 5b** — action-node clarification → needs_input; auto-approve for trigger-origin; owner binding + default-DENY for remote gates; `gate{kind: event}` transient hold | G4 | ✅ DONE (#145) |
| 11 | **Slice 6a** — `mcp_workflows.py`: the 19 chat tools incl. `workflow_observe`/`run_from`/`audit`/`manifest`; wire into `_AGGREGATED_CATEGORY_MODULES`; validation schemas | G5 | TODO |
| 12 | **Slice 6b** — spec ingestion: strict mode + repromptable errors, dry-run-before-save, provenance actor, run-start preflight (`can_resolve_use_case`), generated manifest + CI drift test | G5 | TODO |
| 13 | **Slice 6c** — staged-turn contract for mutation tools; `[ACTIVE WORKFLOWS]` context block (never-break-a-turn); blocking-mode handler | G5 | TODO |
| 14 | **Slice 7a** — `handlers.py` REST routes for defs + runs; register in `dashboard/server.py`; per-run SSE stream endpoint | G6 | TODO |
| 15 | **Slice 7b** — FE `pages/workflows/`: list page, def detail, run detail (snapshot-then-subscribe); `lib/api.ts` methods + nav entry | G6 | TODO |
| 16 | **Slice 8a** — `WorkflowProgressCard.tsx`; event pipeline: dedup keys, deterministic ids, event-fold law, epoch-tagged supersede-drop, node-keyed patches | G7 | TODO |
| 17 | **Slice 8b** — per-observer debounced coalescing (~25ms), schema-validated snapshot projection, `result_omitted` spill boundary; FE lifecycle-union registration + backend⊆FE test | G7 | TODO |
| 18 | **Slice 8c** — typed ask renderer (approval/choice/text/form) in the attention banner + needs-input inbox projection; blocking-mode rendering; two-step delete; foreach progress rows; degraded rendering | G7 | TODO |
| 19 | **Slice 9a** — author 6 bundled templates incl. `produce-and-audit`; macros (`judge_panel`, `verify_panel`, `route`, `research_sweep`) | G8 | TODO |
| 20 | **Slice 9b** — conventions pack (triage-first, Finding record, baseline capture, `bundled/shared/`, template-lint, steering_examples); `artifact_update` provider; bundled-sync; FE template picker | G8 | TODO |
| 21 | **Slice 10a** — `foreach pipeline=true` streaming handoff; `loop until_dry`; `subworkflow` nesting (depth ≤3, namespaced, `child_run_attach`) | G9 | TODO |
| 22 | **Slice 10b** — context lifecycle: `session: fresh` resets + journaled handoffs, typed carryover buckets, decision records, output offloading, two-layer compaction; run-level budget end-to-end; FE collapsible containers | G9 | TODO |
| 23 | **Slice 11a** — end-to-end lifecycle test (create→run→edit→rewind→run_from→fork→complete); adversarial property tests (concurrent mutations, crash-during-execution, deep nesting, double-resume) | G10 | TODO |
| 24 | **Slice 11b** — timeout-fires pair; active-edge pair; journal-replay harness CI-gated vs baseline; performance (50+ nodes <100ms, 1000-entry replay, coalesced widget); security (binding sandbox, RedactingSink coverage, write-scope escapes); architecture doc + template guide | G10 | TODO |

## B. Learning Flywheel steps 1-4 (`-LEARNING-FLYWHEEL.md`) — engine-independent, may front-run

| # | Session | PR group | Status |
|---|---|---|---|
| 25 | Capture: three cadences, one gate, one hygiene policy, one staging log | G11 | TODO |
| 26 | Propose: one queue, four kinds, decision memory, fingerprint anti-refile | G11 | TODO |
| 27 | Curate: one usage store, one decay kernel, hardened curator | G12 | TODO |
| 28 | Inject: two surfacing engines → one ranked slot allocator | G12 | TODO |

## C. Loops Evolution (`-LOOPS-EVOLUTION.md`) — needs engine Slices 0-5

| # | Session | PR group | Status |
|---|---|---|---|
| 29 | Judge contract + `runtime_hints` spec; typed verdict enum; judge isolation; deterministic pre-tier + `fallback_check` | G13 | TODO |
| 30 | Engine loop-node middleware: breaker + fingerprinting + escalation ladder + failure-class routing; fresh-session protocol; interrupt queue | G13 | TODO |
| 31 | Author the 8 template YAML specs + integration tests through the engine | G14 | TODO |
| 32 | Calibration + acceptance instrumentation: rubric contract, verdict ledger, divergence events, template lint, nodding-loop detector | G14 | TODO |
| 33 | FE + coexistence: template picker, cockpit live-follow, interrupt-queue UI, legacy alias layer, as-a-user validation of all 8 | G15 | TODO |

## D. Knowledge Synthesis (`-KNOWLEDGE-SYNTHESIS.md`) — needs engine Slices 0-2

| # | Session | PR group | Status |
|---|---|---|---|
| 34 | Store semantics: `kind`/`logical_key`/`last_verified`/`expires_at`, `item_relations`, hashing, `KnowledgeConfig` four-point wiring, `schema.md` | G16 | TODO |
| 35 | The provider pair: `knowledge_persist` + `knowledge_retrieve`, allowlist, native `search()`, three-node pattern end-to-end | G16 | TODO |
| 36 | Engine additions: `until_cancelled` loop mode + seen-set, `{{siblings.*}}`/`{{previous.output}}`, buffer-seal wait, adaptive delay clamp | G17 | TODO |
| 37 | Consolidation + maintenance: reflect mechanics, `knowledge-health`/`lint`/`gap-healing` templates, proposal routing, differential refresh | G17 | TODO |
| 38 | Contradiction + retrieval polish: persist-time conflict pass, typed-edge inference, contradiction UI, Session Brief, fencing filter | G18 | TODO |
| 39 | Template slate + long-run validation (idempotent re-runs, bounded cycle cost, seen-set across restart) | G18 | TODO |

## E. Universal Planning (`-UNIVERSAL-PLANNING.md`) — needs engine + Loops templates

| # | Session | PR group | Status |
|---|---|---|---|
| 40 | Matching + classification: intent classifier, tiered `match_template()` T1-T5, metadata extensions, CI routing fixtures; delete dead chat plan-mode | G19 | TODO |
| 41 | Grounded generation: grounding bundle from live registries, pattern-shape registry, schema-constrained `oneOf`, repair-not-regenerate, brownfield pass | G19 | TODO |
| 42 | Contracts + parameterization: done-means contracts + lint + preflight, `resolve_unfilled_inputs()`, triage-first, blocking/open decision typing | G20 | TODO |
| 43 | Review + revision: streaming multi-view review, typed merge-by-id, TTL'd sketches, plan-as-artifact, `revise{step_ref, comment}` | G20 | TODO |
| 44 | Autonomy + risk: risk-signal registry, autonomy floors, HITL/AFK typing, confirmation matrix, earned trust, planner read-only posture | G21 | TODO |
| 45 | Grill + entry surfaces + template pipeline: `rigor: deep` protocol, rigor:fast, session mining, suggest_template, per-template eval specs | G21 | TODO |

## F. Work Containers (`-WORK-CONTAINERS.md`) — needs engine + Tasks

| # | Session | PR group | Status |
|---|---|---|---|
| 46 | Project umbrella extensions + run→project binding; hub Work tab state-grouped board (incl. queued/suspended/claimed, truthful across a kill) | G22 | TODO |
| 47 | Artifacts reuse: `publish: {artifact}`, version-on-material-change + change_note, typed lineage deep links | G22 | TODO |
| 48 | Subagent batch hardening: isolation, schema-validated typed output, secret-filtered env, leases (no double-execution) | G23 | TODO |
| 49 | Run workspace + environment: provisioning block, folder contracts, per-project run-env secrets | G23 | TODO |
| 50 | Session ownership + truthful run lifecycle; incognito enforcement (`session_restrictions` + `memory_mode`, durable across restart) | G24 | TODO |
| 51 | Needs-input inbox: NeedsInputItem cards (blocker, attempted, evidence, recommendation, one decision), resume_token wiring, >24h re-notify | G24 | TODO |
| 52 | Code-kind worktrees: preserve_patterns, idempotent setup, resume-safe, teardown before deletion, cockpit diff + Apply Locally/Checkout | G25 | TODO |
| 53 | Introspection checklist: RunStats strip, template p50/p95 cards, Proof section, fake-check warning badge | G25 | TODO |
| 54 | Project export/import: brief/overview/ledgers/templates/artifact metadata/run digests, sha256-verified, zero secrets, `projects` snapshot component | G26 | TODO |

## G. Tasks & SOPs (`-TASKS-SOPS.md`) — needs engine + Work Containers

| # | Session | PR group | Status |
|---|---|---|---|
| 55 | Projection core: `workflow_binding` + new Task fields, `TaskStatus.SKIPPED`, auto-materialization + fingerprint dedup + fan-out caps, typed state projection | G27 | TODO |
| 56 | Verified done + enforcement: engine-owned criterion execution, pass-state gating, three-actor matrix, managed-write rejection, cascade-fail, stuck-work sweep | G27 | TODO |
| 57 | ConfirmationRequest + gates: durable record + atomic single-use resolution + auto-resume, `require_hitl`, DagView Approve/Deny, per-stage mute, tool profiles | G28 | TODO |
| 58 | Surfacing core: `surface_mode` enum, trigger-phrase `match_text` + collision check, metadata split + lints, one-source-two-wrappers injection, SOP migration | G28 | TODO |
| 59 | Surfacing channels + resolution: cadence channel + overdue escalation, fingerprint channel + packs, layered scope resolution, parameter pre-fill, reachability doctor | G29 | TODO |
| 60 | Pool + templates: frontier/next projections, evented unblock, TTL'd leases, write-time acyclicity, lifecycle events, seed template library | G29 | TODO |
| 61 | UX + validation: composer chips, validated deep-links, checklist edit UX, config four-point wiring, end-to-end as-a-user sweep | G30 | TODO |

## H. Automation Substrate (`-AUTOMATION-SUBSTRATE.md`) — final step needs Loops Phase 4

| # | Session | PR group | Status |
|---|---|---|---|
| 62 | Trigger entity + per-kind specs + fire/run records with typed outcomes | G31 | TODO |
| 63 | Disposition table + TriggerService (one scheduler) + crash-safe scheduling discipline | G31 | TODO |
| 64 | Dispatch architecture (inbox + wakeup) + event-bus delivery contract | G32 | TODO |
| 65 | Missed fires: review-don't-storm; catch_up exactly-once staggered | G32 | TODO |
| 66 | Cron migration: lossless from the real store, identical firing incl. jitter/tz/skip_dates | G33 | TODO |
| 67 | Event-kind API parity (toggle/update/run/test/history) + the 8 dormant lifecycle events | G33 | TODO |
| 68 | Autopause after 5 true failures (typed exits park instead) + Runs-inbox surfacing | G34 | TODO |
| 69 | Injection fencing + frozen capability set (adversarially verified); budget/triage typed ledger rows, zero silent drops | G34 | TODO |
| 70 | Calendar-aware scheduling: quiet-hours semantics, duty-gate hook, week-grid view; `automation doctor` | G35 | TODO |

## I. Learning Flywheel steps 5-8 (`-LEARNING-FLYWHEEL.md`) — needs the Run Ledger + everything above

| # | Session | PR group | Status |
|---|---|---|---|
| 71 | Measure: per-arm surfaced-vs-used precision, tunable threshold profiles, trust posterior | G36 | TODO |
| 72 | Self-model: capped, reinforcement-promoted, propose-don't-write | G36 | TODO |
| 73 | Run outcomes → template refinement (the flagship): typed ledger evidence, median-of-3 critic, held-out replay gate | G37 | TODO |
| 74 | Repeated ad-hoc work → suggested templates; failed stages → lessons + procedural priors | G37 | TODO |
| 75 | Proposal Inbox: six kinds, provenance, evidence manifests, risk tiers; model cannot accept its own proposals | G38 | TODO |
| 76 | Staging tier observability: FLUSH_OK/ERROR/proposal-id outcome records, week-at-a-glance panel | G38 | TODO |
| 77 | Accountability: EFFECTIVE…HARMFUL verdicts from ledger outcomes, auto-filed revert proposals; incognito capture gate closed + regression-tested | G39 | TODO |

---

## Execution log (one line per session; newest last)

- 2026-08-01 — queue created. Sessions 1-77 defined. Model tiers bound + verified
  (haiku/sonnet/opus → fast/standard/reasoning). Engine Slices 0-1 already DONE.
- 2026-08-01 — sessions 1-3 (G1, Slice 2) DONE. `resilience.py` + `verify.py`, wired into
  the controller and gate dispatcher. 10049 tests (+76). Real-model validated: mutation-hint
  retry (haiku recovered on attempt 2 with the correction in-prompt), breaker stopped a
  20-iteration thrash at 3 with zero model calls, opus-5 judge PASSed a sound claim and
  REJECTed an absurd one via the closed enum, ladder refused to average away a hard failure,
  artifact gate rejected claimed-but-absent files and refused a traversal pattern.
  DEVIATIONS: (a) `check_budget` now sets `warn` even when already over — a single large node
  can jump past the cap, and treating over as "no warning needed" left the user with a paused
  run and no notice; (b) `model_tier_standard` defaults to `orchestration` not `background`,
  or `standard` and `fast` collapse to one model and the three tiers are decorative;
  (c) added `GateKind.LADDER` + `GateKind.JUDGE` rather than overloading `verify_command`.
- 2026-08-01 — session 4 (Slice 3a, the effect ledger) DONE → **PR #139**. `effects.py`:
  `sha256(run_id+instance_path+epoch)` identity, the 5-state effect lifecycle in
  `events.jsonl`, the committed-effect redo boundary, teardown-before-redo, strict
  one-object BYOI stdout parsing, and `CallerDedupe` for the Slice-6 tool surface.
  10075 tests (+26). Validated live against the REAL bash provider: fired 1x and committed
  `output_id=res-42`; an epoch bump without `redo_effects` blocked (`committed_effect`) with
  the provider never dispatched — still 1x; with `redo_effects` the teardown received
  `res-42` and the node re-fired exactly once.
  DEVIATIONS: (a) **`BLOCKED` added to `TERMINAL_STATES`** — it means "the engine refused,
  a human must decide", so leaving it schedulable made the frontier relaunch-and-refuse
  forever (the silent hang the state exists to prevent); its absence also made
  `_ROOT_TO_RUN[BLOCKED]` unreachable. 351 workflow tests confirm no regression.
  (b) PR base is `main`, not `feature-wf2-slice2`: #138 squash-merged mid-session, so the
  branch was rebased `--onto origin/main` to shed the duplicated pre-squash slice-2 content.
  **Stacking lesson: when the predecessor PR merges mid-session, rebase onto the squashed
  main rather than opening against a deleted branch.**
  (c) **DCO landmine, cost one CI failure:** `git commit --amend -F -` REPLACES the message
  and silently drops the `Signed-off-by` trailer. Always `--amend -s`. Verify with
  `git log -1 --format='%(trailers:key=Signed-off-by,valueonly)'` before pushing.
- 2026-08-01 — session 5 (Slice 3b) DONE → **PR #140**. `scope.py` (snapshot/diff write-scope
  with symlink resolution, warn-vs-reject, opt-in), `audit.py` (`workflow_audit` diagnose/heal,
  dry-run-default, live-controller protection, `blocked{protocol_violation}`), `secrets.py`
  (`_has*` strip / node-id-keyed re-inject / inline-secret lint), and the v2 `run-workflow`
  provider re-registered + re-allowlisted in one commit. 10148 tests (+73).
  Validated live on real disk: a provider clobbering a real file above the workspace was
  caught by name in warn (complete + ledgered) and reject (scope_violation); a real symlink
  escape resolved out of scope; an 8h-stale node healed to blocked/protocol_violation while
  dry-run changed nothing.
  DEVIATIONS/DISCOVERIES: (a) **`_epoch` used `time.mktime` on UTC `...Z` stamps** in BOTH
  `audit.py` and `controller.py` — mktime reads the struct as LOCAL time, so the offset
  cancelled the measured age and the stale-running check was permanently inert; in the
  controller it only showed as an hour-wrong duration across a DST boundary. Both now
  `calendar.timegm`, regression-pinned. (b) **Consolidated the duplicate credential-shape
  list**: `validator.py` had its own prefix tuple, so two vendor-shape lists would drift
  until one stopped catching a provider. It now reads `secrets.py`; the stale validator
  keeps-entry was removed and `secrets.py` added, carrying over `gho_`/`hf_` (a test caught
  that the consolidation would have dropped them). (c) `watch_roots` is necessarily WIDER
  than `allowed_write_paths` — snapshotting only allowed paths makes a violation
  undetectable by construction; it reaches one level above the workspace, not `$HOME`, and
  the limitation is stated in the module rather than papered over.
- 2026-08-01 — session 6 (Slice 4a, `mutations.py`) DONE → **PR #141** (stacked on #140,
  which was still open — normal stacking, no rebase needed). Typed op vocabulary, the
  binding-dependency cascade, transactional batch (parse → validate → apply-to-copy →
  re-validate), grammar hardening a-d, spec-history writer, force-only epoch bump.
  10199 tests (+51). Validated live on a real completed 4-node run: **tree subtree of
  `gather` = ['gather'] vs BINDING closure = ['analyze','gather','report']** — a tree reset
  would have missed both consumers (the WF2-R2 stale-input bug, demonstrated rather than
  asserted); editing a done node rejected `WF_MUT_FROZEN_NODE`; a spec-breaking delete
  wrote nothing.
  NOTES: `rewind`/`run_from`/`fork` are spec-level no-ops here by design — they change
  INSTANCE STATE and run identity, which Slice 4c owns; `inline_subworkflow` is a typed
  refusal until Slice 10. Both are explicit, not silent.
- 2026-08-01 — session 7 (Slice 4b, the mutation queue) DONE → **PR #142** (stack:
  #140→#141→#142). `submit_mutation` (validate + queue + synchronous preview + confirm gate
  + `expect_version`), `_drain_mutations` at the documented safe point with the **TOCTOU
  re-verify**, rewind/run_from state reset, `store.archive_output` (attic, not delete),
  `inputs_stale` journaling, spec-history writing. 10224 tests (+25).
  Validated live: unconfirmed rewind refused with nothing queued; confirmed reset exactly
  ['analyze','report'] leaving gather/unrelated done; 2 outputs archived; re-run cost
  exactly 2 model calls; `spec_history/v002.json` + 1 journaled edit.
  DEVIATION/BUGFIX: **`_terminal` stayed set after a loop exited**, so a controller
  restarted in place after a rewind returned the PREVIOUS run's status instantly without
  waiting for the new work. `start()` now clears it. Found by the end-to-end rewind test —
  not by inspection, which is the argument for driving the real thing.
  NOTE: rollback-vs-revert is only half-landed. `rewind` (rollback: hard reset with
  preserved forward refs) is done; `revert` (inverse-patch ONE node's effects, 409 with the
  conflict named on overlapping later state) needs the checkpoint machinery from 4c to
  identify what "later state" is, so it moves to session 8. Recorded rather than faked.
- 2026-08-01 — session 8 (Slice 4c) DONE → **PR #143** (stack #140→#141→#142→#143).
  **Slice 4 is complete.** `checkpoints.py`: checkpoints (instance map + spec version, NOT
  outputs), `fork_run` (own run dir, `root_run_id` propagation, copied journal prefix +
  outputs, `fork_axis` disambiguator, SHARED_AXES surfaced), `prune_fork` (traversal-refusing),
  `revert_node`/`revert_paths` (409-style refusal with dependents NAMED). The `fork` op is
  wired through the mutation queue; the child lands in DRAFT deliberately (a fork exists to
  be edited before it runs — auto-starting would race that edit). All six plan-named
  property tests landed. 10267 tests (+43).
  Validated live: **a fork of a complete run resumed with ZERO model calls** (the cache-key
  claim proven, not asserted); parent byte-identical after fork; `root_run_id` stable across
  a fork-of-a-fork; revert of a consumed node refused with `dependents=['b']` while the leaf
  was allowed; `prune_fork('../../etc')` refused.
  NOTE: session 7's carried-over `revert` is now landed here, as planned. The remaining
  Slice-4 gap is the journal-region attic (`journal/attic/v<NNN>/`): outputs are archived and
  the in-memory cache is invalidated, but the journal FILE stays append-only by contract, so
  region archival belongs with the retention sweep rather than here. Recorded, not faked.
- 2026-08-01 — session 9 (Slice 5a, the human-input contract) DONE → **PR #144**
  (stack #140→#141→#142→#143→#144). `human_input.py`: typed Ask (4 kinds, per-field types +
  defaults + validation), continuation records with `resolved_inputs` + handoff bundle,
  ATOMIC single-use consumption via unlink, typed `resume_expired`, mode-dependent gate
  timeouts. `controller.resume()` is the out-of-band entry point for widget/inbox/HTTP/chat.
  10313 tests (+46).
  Validated live: a real run blocked with a handoff naming what had already run; an invalid
  answer was refused with the token INTACT; a **fresh controller** (standing in for a gateway
  restart between question and answer) approved it and the immediate second resume was
  refused; the run completed with `deployed=true`; an expired token returned a typed item.
  DEVIATIONS/DISCOVERIES: (a) **surfacing and terminating had to be split.** Giving
  background gates a default deadline meant an unanswered gate had a wake time and stopped
  reporting `needs_input` — it waited silently. `_surface_needs_input` publishes without
  ending the run, so a gate is visible NOW and can still time out later. A regression pair
  pins both, because an earlier cut broke whichever behaviour was written last.
  (b) `wait` nodes are excluded from needs-input surfacing — parked on the clock, they
  resolve themselves. (c) Rewind drops tokens PREFIX-SCOPED, not globally: dropping every
  token on any rewind would cancel approvals a rewind cannot affect. Both directions tested.
- 2026-08-01 — session 10 (Slice 5b) DONE → **PR #145** (stack #140→…→#145).
  **Slice 5 is complete.** `gate_policy.py`: risk-scoped auto-approve (reusing
  `tool_providers.RiskLevel`, NOT a new vocabulary), owner binding + default-DENY for remote
  channels, `AllowMemory` (run-scoped, cleared on rewind), event-gate transient hold with
  event preservation + bounded give-up, and `clarification_from_output` so ANY action node
  can ask without a pre-placed gate. 10360 tests (+47).
  Validated live: schedule-fired runs sailed through safe/caution and STOPPED at destructive
  AND undeclared; an intruder's channel reply was refused with the token surviving for the
  real owner; rewind cleared the remembered allow; an absent prerequisite held with
  `preserve_event=True` while an invalid payload gave up; remote timeout denied.
  KEY DECISION: **an undeclared gate defaults to DESTRUCTIVE.** Deny-by-default toward
  higher risk means forgetting to classify a gate makes it ASK; the opposite default would
  turn every unclassified gate in an unattended run into an unreviewed action.
