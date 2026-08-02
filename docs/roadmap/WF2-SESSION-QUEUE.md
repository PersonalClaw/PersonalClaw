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
| 11 | **Slice 6a** — `mcp_workflows.py`: the 19 chat tools incl. `workflow_observe`/`run_from`/`audit`/`manifest`; wire into `_AGGREGATED_CATEGORY_MODULES`; validation schemas | G5 | ✅ DONE (#147) |
| 12 | **Slice 6b** — spec ingestion: strict mode + repromptable errors, dry-run-before-save, provenance actor, run-start preflight (`can_resolve_use_case`), generated manifest + CI drift test | G5 | ✅ DONE (#148) |
| 13 | **Slice 6c** — staged-turn contract for mutation tools; `[ACTIVE WORKFLOWS]` context block (never-break-a-turn); blocking-mode handler | G5 | ✅ DONE (#149) |
| 14 | **Slice 7a** — `handlers.py` REST routes for defs + runs; register in `dashboard/server.py`; per-run SSE stream endpoint | G6 | ✅ DONE (#150) |
| 15 | **Slice 7b** — FE `pages/workflows/`: list page, def detail, run detail (snapshot-then-subscribe); `lib/api.ts` methods + nav entry | G6 | ✅ DONE (#152) |
| 16 | **Slice 8a** — `WorkflowProgressCard.tsx`; event pipeline: dedup keys, deterministic ids, event-fold law, epoch-tagged supersede-drop, node-keyed patches | G7 | ✅ DONE (#153) |
| 17 | **Slice 8b** — per-observer debounced coalescing (~25ms), schema-validated snapshot projection, `result_omitted` spill boundary; FE lifecycle-union registration + backend⊆FE test | G7 | ✅ DONE (#154) |
| 18 | **Slice 8c** — typed ask renderer (approval/choice/text/form) in the attention banner + needs-input inbox projection; blocking-mode rendering; two-step delete; foreach progress rows; degraded rendering | G7 | ✅ DONE (#155) |
| 19 | **Slice 9a** — author 6 bundled templates incl. `produce-and-audit`; macros (`judge_panel`, `verify_panel`, `route`, `research_sweep`) | G8 | ✅ DONE (#156) |
| 20 | **Slice 9b** — conventions pack (triage-first, Finding record, baseline capture, `bundled/shared/`, template-lint, steering_examples); `artifact_update` provider; bundled-sync; FE template picker | G8 | ✅ DONE (#157) |
| 21 | **Slice 10a** — `foreach pipeline=true` streaming handoff; `loop until_dry`; `subworkflow` nesting (depth ≤3, namespaced, `child_run_attach`) | G9 | ✅ DONE (#158) |
| 22 | **Slice 10b** — context lifecycle: `session: fresh` resets + journaled handoffs, typed carryover buckets, decision records, output offloading, two-layer compaction; run-level budget end-to-end; FE collapsible containers | G9 | ✅ DONE (#159) |
| 23 | **Slice 11a** — end-to-end lifecycle test (create→run→edit→rewind→run_from→fork→complete); adversarial property tests (concurrent mutations, crash-during-execution, deep nesting, double-resume) | G10 | ✅ DONE (#160) |
| 24 | **Slice 11b** — timeout-fires pair; active-edge pair; journal-replay harness CI-gated vs baseline; performance (50+ nodes <100ms, 1000-entry replay, coalesced widget); security (binding sandbox, RedactingSink coverage, write-scope escapes); architecture doc + template guide | G10 | ✅ DONE (#161) |

## B. Learning Flywheel steps 1-4 (`-LEARNING-FLYWHEEL.md`) — engine-independent, may front-run

| # | Session | PR group | Status |
|---|---|---|---|
| 25 | Capture: three cadences, one gate, one hygiene policy, one staging log | G11 | ✅ DONE (#163) |
| 26 | Propose: one queue, four kinds, decision memory, fingerprint anti-refile | G11 | ✅ DONE (#164) |
| 27 | Curate: one usage store, one decay kernel, hardened curator | G12 | ✅ DONE (#165) |
| 28 | Inject: two surfacing engines → one ranked slot allocator | G12 | ✅ DONE (#166) |

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
- 2026-08-01 — session 11 (Slice 6a, the chat tool surface) DONE → **PR #147**
  (stack #141→…→#147). `workflows/service.py` (the ONE implementation Slice 7a's REST routes
  will also call), `mcp_workflows.py` (19 tools, coded never-raising results),
  `MCP_WORKFLOW_SCHEMAS` in validation.py, all 19 `manifest_meta` entries. All FOUR
  registration points wired incl. the regenerated offline reference. 10424 tests (+63).
  Validated live end to end: manifest generated from the engine's enums; plan → dry-run
  author (nothing written) → save → list; a `ghp_` literal REFUSED; a blocking run through
  the real engine completed with correct output; fork named its 3 shared axes; 4 error paths
  coded and readable; audit defaulted to dry-run.
  DEVIATIONS/DISCOVERIES: (a) **`_run` used bare `asyncio.run`**, which raises "cannot be
  called from a running event loop" when the caller is async. Production is safe (the native
  runtime uses a thread executor) but a surface contracted to never raise must not depend on
  its caller — it now detects a running loop and completes on a worker thread. FOUND BY
  DRIVING IT FROM AN ASYNC SCRIPT, not by a unit test (which called it from sync context and
  never hit it). Pinned by an async-context test.
  (b) `error_codes` in manifest_meta stay EMPTY: that registry is the `ERR_`-prefixed
  AgentError ENVELOPE, a different channel from these `WF_*` result-body codes. Declaring
  them there would put one failure in two vocabularies (the drift test enforces the
  registry, which is append-only).
  (c) Two Phase-1 assertions that `mcp_workflows` is ABSENT were correct then, obsolete now.
  Retargeted to the surviving invariant: `prompt_render` does not live there and every tool
  is `workflow_`-prefixed.
  (d) This category does NOT go over HTTP like its siblings — the engine is in-process, and
  a gateway round-trip to reach an object in the same process buys nothing.
- 2026-08-01 — session 12 (Slice 6b, preflight + drift gate) DONE → **PR #148**
  (stack #141→…→#148). `preflight.py`: credentials (declared ∪ REFERENCED `{{secret:}}`),
  binaries, models (via the SAME `can_resolve_use_case` probe onboarding uses), action
  providers. `start_run` gated with a `skip_preflight` override; agent-authored defs get a
  dry-run report attached at save; nine manifest-drift assertions against the engine's enums.
  10458 tests (+34).
  Validated live against REAL infrastructure: the real probe refused an LLM workflow in a
  home with no model; the real PATH flagged an absent binary and passed `sh`; the real
  registry rejected `no-such-provider`; the real credential store flagged an
  undeclared-but-referenced `{{secret:}}`; a pure-transform workflow demanded nothing.
  KEY DECISIONS: (a) **missing is an ERROR, unverifiable is a WARNING** — refusing a run
  because the CHECKER was offline is its own outage, so the two never collapse (tested both
  ways). (b) The model check reuses onboarding's probe deliberately; a private capability
  check would drift from what the bridge really resolves and greenlight unrunnable runs.
  (c) A BOUND provider name is SKIPPED, not guessed at — guessing a binding's future value
  produces a false failure. (d) A judge gate is checked on the `reasoning` tier because that
  is what `dispatch_gate` actually uses, not what the node declares.
  NOTE: 11 Slice-6a tool tests now pass `skip_preflight=True` — they inject a fake completion
  to test the ENGINE, while preflight tests the ENVIRONMENT (correctly model-less in a test
  home). Two new tests cover the ungated path so the gate is not merely skipped everywhere.
- 2026-08-01 — session 13 (Slice 6c) DONE → **PR #149** (stack #147→#148→#149).
  **Slice 6 is complete.** `workflows/context_block.py`: the `[ACTIVE WORKFLOWS]` block
  (urgency-ordered, ask inline, run+length capped, names its own tools) and the staged-turn
  spec echo (tree + source + `expect_version` + live node states, credentials stripped).
  `controller.wait_for_terminal()` + `progress_snapshot()` for blocking mode. Wired into
  `context.py:build_message`. 10493 tests (+35).
  Validated live: a real gated run returned `needs_input` from blocking mode instead of
  hanging; the block rendered as a chat turn sees it with the ask inline; the echo on a real
  `workflow_status` carried `expect_version=1` and `#approve [waiting]`; a broken store
  yielded "" not an exception.
  KEY DECISIONS: (a) **blocking mode needed a NEW wait.** `run_to_completion` waits for the
  tick loop to exit, which DEADLOCKS on needs_input — the tool holds the turn that would
  render the ask, so the ask can never be answered. `wait_for_terminal` stops there and
  returns the resume token in the same response.
  (b) The block is NOT project-filtered: `resolve_project_id` AUTO-CREATES a project when
  none resolves, and a read-only context block must not have side effects.
  (c) The echo ships tree AND source together — tree-only invites invented field names,
  JSON-only makes structure unreadable. Mutation tools do not re-echo (the model already
  knows what it changed).
  (d) never-break-a-turn is asserted on the CALL SITE in context.py, not just the helper.
- 2026-08-01 — session 14 (Slice 7a, the REST API) DONE → **PR #150** (stack #149→#150).
  `workflows/handlers.py`: 19 routes for defs + runs over the SAME `workflows.service` the
  chat tools use, §2.2 error envelope with a one-place `_STATUS_MAP`, restricted-session
  guard + SEL audit on every mutation, snapshot-then-subscribe per-run SSE that CLOSES for a
  terminal run. Mounted in `dashboard/server.py`. 10543 tests (+50).
  Validated live against a REAL aiohttp server over HTTP: save 201 → list → blocking run to
  complete → status with both nodes done → `output='got 7'` → SSE emitting
  `event: workflow_snapshot` then closing → fork 201 → manifest → a 404 in the exact §2.2
  envelope → `/runs` returning a run list rather than a def named "runs".
  DEVIATIONS/DISCOVERIES: (a) **TWO WIRING GAPS CLOSED.** The supervisor was built at boot
  but never published, so `state.workflows` AND `ActionServices.workflows` were both None —
  the routes would have created runs nobody drives and the `run-workflow` trigger provider
  would have kept reporting "no supervisor available". Both now get it.
  (b) **A pre-existing legibility gap fixed here because it hid this slice's work:** the
  offline reference's AST walk was rooted at `dashboard/`, so entity families registered from
  their own package were INVISIBLE. Widening it to the package surfaced **43 routes** — this
  slice's 19 plus artifacts' and tasks' 24, which had NEVER been documented. Asserted both
  ways so a future narrowing is caught.
  (c) An unmapped service code returns 400, never 500 — a 500 tells a client to retry
  something that can never succeed.
  (d) mypy caught two real bugs pre-run: `_is_restricted_session` takes `(state, request)`,
  and `dashboard_state` is Optional at that point.
  (e) TEST LANDMINE: `make_mocked_request`'s default app is a MagicMock, so
  `app.get("state")` returns a mock and the handler 500s on an awaited mock — masking what it
  actually did. Pass a REAL `web.Application`.
- 2026-08-01 — session 15 (Slice 7b, the workflows UI) DONE → **PR #152** (stack #150→#152).
  **Slice 7 is complete.** `pages/workflows/`: list (runs-first, needs_input sorted top),
  def detail (tree + inputs + requirements), live run detail (snapshot-then-subscribe),
  `WorkflowAsk` (ONE renderer for all 4 ask kinds), `useWorkflowStream` (full 10-event
  union), `workflowMeta` (status presentation, degraded≠failure). api.ts types + 18 methods.
  Nav entry under Capabilities. 10547 py tests · 329 web tests (+22) · typecheck + build clean.
  VALIDATED AS A USER in a real browser: nav renders; list showed "1 waiting on you"; the run
  view rendered the typed approval with its handoff and live states; clicking Approve drove
  the run to Complete via SSE with `deliver` appearing, the header swapping to Fork, and
  elapsed filling in; 0 console errors.
  DEVIATIONS/DISCOVERIES:
  (a) **BLOCKER CLEARED — the native def provider did not exist.** `defs.py` is only a
  registry SEAM, so with no app installed NOTHING writable was registered and every save
  failed `WF_DEF_NO_WRITABLE_PROVIDER` — a dead end for the whole feature. Slice 0 deferred
  it ("a loader with no executor can't be validated end to end"); the executor exists now, so
  `native_defs.py` landed here. Version advances on EVERY save (a run pins the version it
  started from; reusing one makes `expect_version` meaningless).
  (b) **A REAL BUG THE UI FOUND THAT UNIT TESTS MASKED.** Answering a gate wrote the node DONE
  but never restarted the tick loop — already exited when the run went needs_input — so the
  run sat forever with downstream nodes unlaunched. Every earlier test called
  `run_to_completion` BY HAND after resuming, and that manual call WAS the missing restart.
  `resume()` now clears needs_input + relaunches; 3 regression tests fail without the fix.
  **Lesson: a test that hand-drives the thing under test can hide the absence of the very
  mechanism it is testing.**
  (c) Added the workflow SSE drift guard to `test_transport_doctrine.py` (backend publishes ⊆
  FE listens), verified by deleting an event and watching it fail.
  (d) Three font sizes were off the documented ramp (0.875/0.6875rem) — snapped to real rungs.
  (e) The preflight model probe is now PINNED in its test rather than relying on ambient
  environment state.
- 2026-08-01 — session 16 (Slice 8a) DONE → **PR #153** (stack #152→#153). The event
  envelope stamped at the ONE `_publish` seam (`event_id` deterministic, `seq` monotonic,
  `epoch` = the RUN's max, plus `node_epoch` on node events); `workflowFold.ts` (pure,
  unit-locked, 4 guards); `WorkflowProgressCard` folding SSE through the SAME fold the run
  page uses. 10559 py · 368 web (+39) · typecheck + build clean.
  Validated live: captured 7 REAL frames off the wire (evt-9…evt-14) carrying the full
  envelope; browser showed the resumed run Complete 3/3; `foldRealFrames.test.ts` asserts the
  fold law against those exact frames.
  DEVIATIONS/DISCOVERIES:
  (a) **`workflow_node_started` overrode the run epoch** — it passed the NODE's epoch as
  `epoch`, so `started` and `done` for one node reported DIFFERENT run epochs; a consumer
  folding the lower one would treat the next real event as superseded and go permanently
  silent. Node epoch now goes under `node_epoch`, and a STRUCTURAL test fails if any call
  site re-introduces a bare `epoch` in a payload (the dicts are spread into the envelope, so
  a regression would otherwise be invisible).
  (b) **`seq` was in the envelope but the fold never used it.** Folding REAL captured frames
  OUT OF ORDER regressed a node from Done back to Running. Added a PER-NODE seq floor —
  per-node, not global, because two different nodes' events are independent and a global
  floor would drop a legitimate sibling. **Found by replaying real frames, not fixtures.**
  (c) `window.location.hash` in the card violated the url-navigation doctrine test; replaced
  with a plain `<a href>` matching SdlcProgressCard.
- 2026-08-01 — session 17 (Slice 8b) DONE → **PR #154** (stack #152→#153→#154). New
  `coalescer.py` (per-observer debounced batching, ~25ms window, `workflow_batch` frame) wired
  into the watchdog's publisher; new `projection.py` (explicit field-table validation before
  transmission) behind the per-run SSE snapshot; `store_output` spill extended from size-only
  to content-based binary detection; FE `unwrapBatch` + batch listener; transport-doctrine test
  extended. 10616 py · 380 web (+12) · typecheck + build clean.
  Validated live: a 12-item foreach delivered **26 logical events in 3 wire frames**
  (run_update / 24-member batch / terminal run_update), every member carrying the full envelope
  with seq dense 1-26 and the batch flushed BEFORE the terminal status. Those exact frames are
  checked in as `__fixtures__/realBatchFrames.json`. In a real browser a 30s fan-out went
  Running/Waiting → Complete/Done live, zero console errors.
  DEVIATIONS/DISCOVERIES:
  (a) **The binary check's first version detected nothing.** A PNG's leading `\x89`
  utf-8-encodes to TWO bytes (`\xc2\x89`), so a utf-8 round-trip matched no magic number at
  all. Recovered with latin-1 instead (codepoints 0-255 map back to the identical bytes). Found
  by the test failing, not by review.
  (b) **Base64 added as a second carrier** beyond the plan's "magic-prefix binary detection": a
  node output is JSON, and JSON cannot hold arbitrary bytes, so in practice a screenshot or
  fetched asset arrives base64'd — a raw-bytes check alone would miss every realistic case.
  (c) **Coalescing keyed by `(event, instance_path)`, not path alone.** Path-only would let a
  node's `started` be eaten by the `done` that follows in the same window, so the node would
  never render as active — a fan-out would look like it teleported from pending to finished.
  (d) A pass-through event FLUSHES the pending batch (not in the plan's wording, but required):
  otherwise a status flip overtakes the node events that logically precede it.
  (e) Two existing tests were pinned to the un-coalesced shape and were updated to unwrap
  (`test_workflows_watchdog.py::test_events_reach_the_per_run_sse_key`) or to the new projection
  seam (`test_workflows_api.py::test_it_is_snapshot_then_subscribe`) — the CLAIM each makes is
  unchanged.
  (f) `workflow_node_progress` was NOT added to the FE union or the allowlist: no call site
  publishes it yet (it arrives with Slice 8c's foreach progress rows), and registering it now
  would be a dead path.
- 2026-08-01 — session 18 (Slice 8c) DONE → **PR #155** (stack #152→#153→#154→#155). New
  `attention.py` (a waiting gate raises a durable inbox row + ONE notification via
  `emit_attention_item`, deduped per run/path/EPOCH, resolved on answer scoped to the node, and
  cleared when a run ends); `WorkflowGateActions` answering the gate IN the inbox through the
  same `WorkflowAsk` the run view uses; `service.delete_run` + `DELETE /api/workflows/runs/{id}`
  + two-step armed delete in the run list; per-item foreach rows (`[3/12] auth.py`) with the
  label persisted on the instance. 10669 py · 387 web · typecheck + build clean.
  Validated live: a gate raised a `pending` row carrying the real question; approving FROM THE
  INBOX completed the run and flipped the row to `handled`; the two-step delete 404'd the run and
  removed its directory; deleting a `needs_input` run was refused 409 naming the fix; a named
  fan-out rendered `[1/3] auth.py` … `[3/3] handlers.py`.
  DEVIATIONS/DISCOVERIES:
  (a) **The plan's "typed ask renderer" and "degraded rendering" were already done** in Session
  15 (`WorkflowAsk.tsx`, `nodeLook`'s degraded-as-success-adjacent tone). This session built what
  was genuinely missing — and the watchdog's comment claiming user-facing moments "go through
  notify separately" described work that had NEVER been done: a gate reached nobody who wasn't
  already watching the run view.
  (b) **EVERY attention row was written to a detached store.** `emit_attention_item` built a
  fresh `InboxStore()`, but the running service holds items in MEMORY and never re-reads the
  file — so the row hit disk and `/api/inbox` (which serves the service's instance) stayed empty,
  and the service's next save would overwrite it. Affected every attention kind (loop gates,
  proposals, mirrored approvals), not just workflows. Fixed with a shared `inbox.live_store(state)`,
  **isinstance-checked** because a test's `MagicMock` answers every getattr and would swallow real
  writes silently.
  (c) **`message=body or title` discarded the title** whenever a body existed — a gate's row read
  "Waiting for your approval." and LOST the actual question. Now joined title-then-body.
  (d) **The resolve side had (b) all over again**: closing a row in a detached copy left it open
  after its gate was answered. Found by clicking Approve in a REAL BROWSER and watching the row
  survive. `resolve_gate_item`/`resolve_run_items` now take the state.
  (e) `NodeInstance.item_label` added (persisted): the items list is re-resolved from a binding,
  so after an upstream output changes the label would be unrecoverable, and a retry must show the
  item it originally got rather than whatever now sits at that index.
  (f) `WorkflowWatchdog.forget(run_id)` added — nothing may hold a controller handle to a run whose
  row is about to disappear.
  (g) The offline reference needed regenerating for the new DELETE route (its drift guard caught it).
- 2026-08-01 — session 19 (Slice 9a) DONE → **PR #156** (stack #152→#153→#154→#155→#156).
  New `macros.py` (4 macros expanding at DEFINITION time in `author_def`, before validation and
  before the write — so the journal/resume-cache/rewind never learn macros exist and a user can
  hand-edit an expansion); new `bundled/` with all six templates + `bundled_defs.py` (read-only
  provider served straight from the package, registered at gateway boot). 10796 py · 387 web.
  Validated live: all six served as `source=bundled`; `produce-and-audit` triaged `standard`,
  took the `look_around` leg and SKIPPED the two untaken entry subgraphs with macro-expanded nodes
  rendering as ordinary rows; two templates parked correctly on approval gates.
  DEVIATIONS/DISCOVERIES:
  (a) **Flat action arguments deadlock a run.** `code-implementation` wrote bash args flat beside
  `provider`, but the engine reads them from `config.with` (`dispatch_action`). Bash got an EMPTY
  config and reported "missing 'command' field" for a command visibly in the spec — then every
  downstream binding failed and the run died as "deadlocked". Three cascading symptoms, none
  naming the cause. Fixed + the validator now refuses the shape at authoring time
  (`WF_ACTION_ARGS_NOT_NESTED`, naming which keys to move; argument-less = warning only). One
  existing validator test's fixture used the broken shape and was corrected.
  (b) **The wheel would have shipped an EMPTY template library.** `pyproject.toml` declared
  `workflows/bundled/*/WORKFLOW.md` — a filename nothing has ever produced. Editable installs
  looked perfect; only a real `pip install` would have shown it. Corrected to `workflow.json` and
  pinned by a test, since `pyproject.toml` is the only place it is observable.
  (c) A boolean `branch` enum does NOT work: `[true,false]` stringifies to Python's `True`/`False`
  and will never match `"true"`/`"false"` case keys. `audit-sweep`'s fix toggle uses an expression
  GATE instead, which is the honest construct for a boolean.
  (d) No `bundled/shared/` prompt-block library yet and no template-lint — both are Slice 9b's
  explicit scope, so they were left rather than half-built here.
- 2026-08-01 — session 20 (Slice 9b) DONE → **PR #157** (stack #152→…→#156→#157). New
  `bundled/shared/` (3 conventions blocks) + `blocks.py` (resolved at definition time, AFTER macros
  since a macro emits references); `template_lint.py` (advises on a user's spec via `author_def`,
  GATES the shipped library at zero findings incl. warnings); `artifact_update_provider.py`
  (zero-token upsert, registered in registry AND `ALLOWED_HOOK_PROVIDERS` in one commit); the FE run
  dialog (`templateStart.ts`); `workflow_plan` now honours its `template` arg. 10862 py · 406 web.
  DEVIATIONS/DISCOVERIES:
  (a) **Declared input defaults were validated and then NEVER APPLIED.** A template declaring
  `acceptance` with a default, run without it, failed three nodes deep on `binding failed:
  unresolved reference at 'acceptance'` — so every optional input was a landmine and a template
  could only run if the caller passed every key it declared. Found by starting a bundled template
  from the UI with the optional field blank. Now `_with_declared_defaults` at run start, recorded on
  the run so the record explains its own behaviour.
  (b) **`event_type` is a fixed enum** (`ALLOWED_EVENT_TYPES`): the invented `workflow_refresh` made
  every artifact UPDATE fail while creates succeeded. Now `iterated`.
  (c) A stray `{{block:…}}` surfaced as `WF_UNKNOWN_BINDING_ROOT`, sending an author after a node
  that was never the problem. Own code now (`WF_UNRESOLVED_BLOCK`) — blocks and bindings resolve at
  different TIMES.
  (d) **`workflow_plan`'s `template` arg was accepted and IGNORED** — a model passing it got a
  generic scaffold and no signal its request was dropped, which makes the library look useless.
  (e) The FE picker was the real "template picker" work: every bundled template declares a required
  input and the Run button passed none, so all six were unstartable from the UI.
  (f) No mtime bundled-SYNC was built, deliberately: serving read-only from the package means an
  upgrade ships new templates with no "did the user edit it?" reconciliation. The plan's "bundled-sync
  (mtime, no-overwrite)" is satisfied by not needing one. DEVIATION recorded.
  (g) **CI FIX amended onto #156**: Slice 9a's tests registered the bundled provider into the
  process-global `defs._providers` and never unregistered, so `test_workflows_tools.py` saw 7 defs
  where it asserts 1 — three failures visible only in a full-suite run. Both template test modules
  now snapshot-and-restore the registry. (`test_workflows_scope.py`'s failure was the same shard's
  collateral.) The 2 `test_subagent.py` timeouts in that run are the PRE-EXISTING flake.
- 2026-08-01 — session 21 (Slice 10a) DONE → **PR #158** (stack #152→…→#157→#158). `subworkflow`
  nesting implemented (`dispatch_subworkflow`): a real CHILD RUN with its own journal/state/terminal
  writer, inputs resolved against the parent before creation, child outputs flowed back, genealogy
  threaded (`parent_run_id` + `root_run_id`), `child_run_attach` emitted with the SPAWNING NODE id,
  depth capped at 3 and checked BEFORE creation. `max_concurrency` on `foreach` implemented.
  10893 py · 406 web. Validated live: parent completed, `{{nodes.nested.output.child_run_id}}`
  resolved in a downstream node, the child is independently inspectable via the API with correct
  genealogy, and self-recursion bounded at 4 runs with an actionable refusal.
  DEVIATIONS/DISCOVERIES:
  (a) **`loop until_dry` was ALREADY DONE** (`tick.loop_should_continue` + the controller's
  `_dry_streaks` feed). No work needed; verified by reading the code rather than assumed.
  (b) **`pipeline=true` needs NO implementation, and that is a measured finding, not an assumption.**
  The plan describes it as "no barrier between stages". Instrumented against the real engine: with a
  slow first item, a fast item's stage 2 launched at 0.07s rather than waiting — each item's body is
  an independent subtree and the frontier is re-derived every tick, so streaming handoff is already
  the engine's ONLY behaviour. A `pipeline: false` that imposed a barrier would be NEW machinery
  whose only effect is making fan-outs slower. The flag is accepted/documented/validated and
  non-semantic; two tests pin the streaming property so a future barrier fails there.
  (c) The knob that DOES govern a fan-out's shape was missing: **`max_concurrency` was declared in
  the plan's node table and used by my Slice 9a templates but ignored by the engine.** Implemented
  per-container (independent of the lane caps) — measured at run time, peak concurrency ≤ cap.
  (d) **BUG: an untouched foreach item derives as RUNNING**, not PENDING (`container_outcome` maps
  "children all pending" → RUNNING). My first cap implementation used `_derive` to decide "has this
  item started?", so every item looked in-flight and the cap admitted everything. Now decided by
  whether the item's subtree has any RECORDED state.
  (e) **BUG: `max_concurrency: 1.5` truncated to 1** and `true` became 1 via `int()` — a spec typo
  would silently serialize a fan-out, the most expensive misreading and invisible because the run
  still succeeds. Now requires a true int (bool excluded).
  (f) **BUG: a FAILED node's output was always discarded**, so a failed subworkflow's
  `child_run_id` was lost — the user is told a nested run failed with no way to find it. Failed
  results now persist an output when they carry one (on the instance only, NOT into `_outputs`, so a
  downstream binding still sees the node as having produced nothing).
  (g) **BUG (self-inflicted, caught by tests): `ref, _preview = …` shadowed the module-level
  `_preview` function** used a few lines below, crashing the tick for EVERY failing node. 13 tests
  went red at once, which is what surfaced it.
  (h) `test_workflows_engine.py::test_subworkflow_refuses_explicitly_rather_than_silently_skipping`
  asserted "not executable yet" — the exact thing this session removed. Re-pointed at the
  no-supervisor INTERNAL failure; the behaviour it used to guard is now covered by
  `test_workflows_nesting.py`.
  (i) NOT DONE, deliberately: the run-view node row has no link to a spawned child run, because
  `child_run_id` is not on the node PROJECTION (only in the node's output). That is Slice-8-family
  widget work, not 10a engine scope. Recorded rather than half-built.
- 2026-08-01 — session 22 (Slice 10b) DONE → **PR #159** (stack #152→…→#158→#159). New
  `context.py` (Handoff / Carryover / Decision, all bounded + deduped) + three journal record kinds
  in `LEDGER_KINDS`; `session: fresh|continuous` honoured; the carried context PREPENDED to a fresh
  iteration's prompt; rehydration from the ledger on start/resume. New FE `nodeTree.ts` +
  collapsible containers in the run view. 10930 py · 427 web (+21).
  Validated live: a 13-node run with an untaken branch rendered as **3 rows** (`gather` showing
  "9 skipped · 1 done"), the running node stayed visible, and expanding restored the hidden rows.
  DEVIATIONS/DISCOVERIES:
  (a) **The run BUDGET was already enforced end-to-end** — `_budget_exceeded()` pauses the run at
  the cap and `_check_budget_warning()` emits the 80% notice once. Measured: a 1500-token cap paused
  a run at 4/8 nodes. My first read of `_check_budget_warning` alone suggested warn-only; the stop
  lives in the tick loop. No work needed.
  (b) Handoff/carryover/decision are read from the node's OWN OUTPUT, not inferred from prose. A
  node that says nothing hands over nothing — a FABRICATED handoff is worse than none, because the
  next iteration would trust it.
  (c) The journaled carryover is the MERGED state at each write, so a resume takes the last record
  and is complete rather than replaying and re-merging every one.
  (d) `session: continuous` deliberately injects NOTHING: a continuous session already holds the
  previous iteration in its transcript, and prepending a handoff would say everything twice.
  (e) Collapse seeding is ONE-SHOT per run id (a `seeded` ref), not derived on every poll — re-
  deriving would slam a subtree shut the moment it finished, exactly as the user was reading it.
  (f) A subtree collapses only when every member is TERMINAL and none FAILED. An unfinished subtree
  holds the live work; a failed one holds the thing the user must read, and hiding a failure behind a
  disclosure control is how a run "silently" fails from the user's point of view. `degraded` DOES
  collapse — it is a success with a reason.
  (g) NOT DONE, and recorded rather than half-built: **output offloading to artifacts +
  `artifact_inspect`** and the **two-layer compaction ladder**. Both are real scope from the plan's
  §2. Offloading already exists in a partial form (`journal.store_output` spills oversize/binary to
  a stub with an `output_ref`, Slice 8b), but the `{{nodes.x.artifact}}` binding form and the
  `artifact_inspect` action are not built; the compaction ladder needs a summarizer seam that does
  not exist yet. Deferred to a follow-up rather than stubbed.
- 2026-08-01 — session 23 (Slice 11a) DONE → **PR #160** (stack #152→…→#159→#160). New
  `test_workflows_lifecycle_e2e.py`: 22 tests driving the whole engine through the REAL supervisor —
  lifecycle composition, crash recovery, double-resume, deep nesting, fork isolation, performance
  budgets, security boundaries. 10952 py.
  DEVIATIONS/DISCOVERIES — this session was mostly about learning the engine's REAL contracts, and
  every one of these was a wrong assumption in my tests, not a bug in the engine:
  (a) **`edit_run`/`rewind_run`/`run_from` all require a LIVE controller** (`WF_RUN_NOT_LIVE`). A
  mutation is only safe at the controller's drain point, so the suite launches through a real
  `WorkflowWatchdog` rather than a hand-rolled registry — which would have drifted from the contract
  exactly where the suite should be checking it.
  (b) **Mutations are QUEUED, not applied.** `edit_run` returns `queued: True`; the tick applies it.
  A test that does not drive the loop observes nothing and (worse) sees a stale version pass a
  `expect_version` check that should have failed.
  (c) **The spec version lives on `run.spec_version`, not in the spec file** — `read_spec()` has no
  `version` key at all, so `spec.get("version", 1)` was silently always 1.
  (d) **The frozen-region invariant refuses editing a COMPLETED node.** The user's order is REWIND
  then edit; my first lifecycle test had it backwards and got `WF_MUT_FROZEN_NODE`, which is the
  engine being right.
  (e) **A rewind whose cascade would re-run completed work reports `needs_confirmation` and applies
  nothing** until `force=True`. That gate is the point of the preview.
  (f) **A rewind produces NO `step_cached` event** — it bumps the node's epoch, and the epoch is part
  of the cache key, so the rewound region correctly MISSES the cache. The cache serves a RESUME;
  invalidation is what serves a rewind. Measured by execution count per node instead.
  (g) The real mutation op grammar is `{"op": "update_node", "node_id": …, "fields": {…}}` — not the
  `target`/`value` shape I assumed.
  (h) Three of my cases duplicated existing coverage (47 mutation tests + 35 fork tests already
  assert version-mismatch, frozen-node, cascade and isolation directly). Dropped rather than kept as
  a second, weaker copy; what remains is what only an e2e layer reaches — the seam between "the
  service accepted it" and "the controller applied it", which every unit test stubs.
  (i) The security assertion for `compile(` needed a word-boundary regex: `re.compile` is ubiquitous
  and legitimate, and a test that cried wolf there would be turned off.
- 2026-08-01 — session 24 (Slice 11b) DONE → **PR #161** (stack #152→…→#160→#161). New
  `test_workflows_hardening.py` (31 tests: timeout pair, active-edge pair, journal-replay
  properties, write-scope escapes, doc-accuracy gates); new `docs/architecture/workflows.md` +
  `docs/guides/workflow-templates.md`, both indexed. 10983 py.
  DEVIATIONS/DISCOVERIES:
  (a) **BUG (real, shipped): `note_progress` had NO caller.** The stall clock was never fed, so
  `timeout_stall` fired on any node slower than its window regardless of progress — the two timeout
  knobs were ONE knob, and a visibly-working node was killed as wedged. Measured: a node working
  steadily for 3s died at 1s with "no progress for 1s". Fixed by threading an `on_progress` callback
  through `dispatch` and feeding it on a 0.5s heartbeat while a nested run is alive. Both halves of
  the plan's pair now hold: a 2s working node survives a 1s window, a silent node is still killed.
  (b) **The active-edge pair ALREADY EXISTED** (`test_workflows_tick.py` lines 225/237). Verified by
  reading rather than assumed; this session added the end-to-end form (driven through a real run) and
  the "join fires once the async leg settles" complement.
  (c) **`STEP_STARTED` is deliberately NOT a LEDGER kind** — it lands in `journal.jsonl`, not
  `events.jsonl`. The replay pairing had to be asserted over the journal, which is the file a resume
  actually replays.
  (d) `scope.diff()` returns a `ScopeReport` (`created`/`modified`/`deleted`/`violations`), not an
  iterable of paths. `created` lists ALL writes; `violations` is the classification — asserting on
  the latter is the point.
  (e) `allowed_write_paths` does NOT resolve `..` — the DIFF resolves both sides at comparison time.
  So the safety property was re-asserted at the comparison layer; testing the declared list for `..`
  would have pinned an implementation detail that is not the property.
  (f) The guide originally documented a `personalclaw workflow author` CLI that DOES NOT EXIST.
  Corrected to the real surfaces (HTTP `save: false`, `workflow_author`, `workflow_plan --template`)
  and verified against `--help`.
  (g) Added **doc-accuracy CI gates**: every module named in the arch doc must exist AND every module
  on disk must be documented; every lint code, macro and block the guide lists must be real; the
  documented constants must match the code; the guide's `config.with` example is run through the
  validator. The bidirectional module check immediately caught `preflight.py` and `audit.py` as
  undocumented.
  (h) **CI FIX (#160 was red, now fixed here): a def-registry leak reproducible ONLY at CI's worker
  count.** `test_workflows_tools.py` asserts exact def counts ("1 def, from my provider") but
  inherited whatever a neighbour left registered → `7 == 1`. Local `-n auto` (18 workers) never
  scheduled the files together; CI's 4 did. Reproduced with `-n 4`, fixed by making that module start
  from an EMPTY registry rather than adding a restore to every possible leaker — the invariant it
  needs is "nothing but what I registered". Verified green at `-n 4` AND `-n auto`.

### Session 25 — Learning Flywheel step 1: Capture (`feature-wf2-flywheel-capture`, PR #163)

New `learning/` package: `gate.py` (LearningGate), `hygiene.py` (one capture policy),
`staging.py` (append-only log + flush outcome records). Config: 3 knobs through all four
wiring points. `context_management.py` split; `plan_memory.py` extracted. 11060 tests
(+77), lint clean at 599 files, green at `-n 4` AND `-n auto`.

**Deviations and findings:**

(a) **DELETED `after_turn_review.should_review`** rather than leaving it beside the gate.
  Zero production callers remained after the rewire, and clean-break doctrine says the
  replaced mechanism goes in the same change. Its six test cases migrated to
  `test_learning_gate.py`, which also covers the two things the old boolean structurally
  could not express (permitted-vs-worthwhile, registry-sourced suppression).

(b) **Permission and worthwhileness are SEPARATE fields, not one boolean.** The old
  single `should_review` is exactly why preference-facet capture ran UNGATED at
  chat_runner.py:150 — a cheap heuristic could not express "allowed, but not worth an
  LLM", so the carve-out bypassed the gate entirely. `GateDecision.permitted` /
  `.worthwhile` keeps the carve-out's intent while routing it through one gate;
  `__bool__` returns the STRICT answer so an `if decision:` cannot accidentally
  authorize an expensive pass.

(c) **MEASURED BUG in my own first implementation:** `security.fence_untrusted` emits
  `<untrusted_content source=web>`, so matching the bare `UNTRUSTED_OPEN` literal found
  NOTHING on exactly the spans that carry provenance. A planted injection passed straight
  through the filter. Every fence in production names a source. Fixed with a tag-aware
  regex derived FROM the constant, and covered for all four real sources.

(d) **System-marker matching is a PREFIX test, not a search.** A 200-char window flagged a
  user *quoting* `[Subagent completion event]` while asking about it — silently disabling
  learning for that turn. Every emitter (`gateway.py`, `subagent.py`, `chat_runner.py`)
  builds `f"[marker]\n{body}"`, so a prefix test matches exactly what the platform
  produces. Markers were read from the emitting code, never guessed.

(e) **Ordering is a privacy property:** permission must be settled WITHOUT reading the
  message. Classifying the text of a restricted session — even with a free regex — reads
  content its memory_mode promised was out of scope. Caught by an existing
  `test_temporary_chat` assertion; now pinned by
  `test_a_denied_session_is_never_classified`.

(f) **`_ChatSession` defines `__slots__`,** so stashing the turn's decision on the session
  raised `AttributeError`. The decision is threaded as an argument instead — which also
  makes the sharing visible at the call site rather than implicit in object state.

(g) **`context_management.py` split on the seam that already existed.** It held two
  unrelated subjects: resource-growth limits, and a plan-format parser + plan-memory
  journal. `build_stage_context` (zero callers) and `rephrase_plan` moved to the plan side
  — `rephrase_plan` reads generic but its whole job is restating *this* format. Step 5's
  plan-memory absorption is now a file deletion, not surgery on a live module.

(h) **learning.db is COPIED, never merged,** on import. Merging two capture logs would
  double-count evidence occurrences, and `learning.min_evidence` is what decides whether a
  pattern is real — a merge would manufacture proposals out of a restore rather than out of
  the user's behaviour.

(i) **`MIN_EVIDENCE_DEFAULT` is asserted equal to the config default.** The plan's claim is
  "ONE shared number"; two consumers reading different sources is the failure that claim
  exists to prevent, so a test pins them together.

### Session 26 — Learning Flywheel step 3: Propose (`feature-wf2-flywheel-propose`, PR #164)

`learning/proposals.py`: the generalized queue (6 kinds), decision memory with
fingerprint anti-refile + escalating cooldowns, the deterministic 4-verdict resolve
cascade, change manifests, per-run quota, SEL-audited accepts. `learning.propose_quota_per_run`
through all four wiring points. 11112 tests (+52), lint clean at 600 files.

**Deviations and findings:**

(a) **A literal NUL byte in the fingerprint separator made the module unimportable.**
  `f"{kind}\x00{target}..."` written as a raw NUL — Python refuses source containing
  null bytes, so `import` failed outright. Replaced with `\x1f` (unit separator).
  Pinned by a test, because the failure mode is a crash at import rather than a wrong
  answer.

(b) **The subject guard defeated the very contradiction detection it was meant to
  support.** `_subject_span` took the first two words, so "always use uv …" and
  "always never use uv …" had DIFFERENT subjects and the pair resolved as NEW —
  leaving two opposite instructions both pending. Fixed by stripping polarity/modal
  stopwords before taking the span. Measured, not reasoned.

(c) **Contradiction must be checked on SUBJECT match, not on similarity rank.**
  "always use uv for installs …" vs "always avoid uv …" scores **0.80** by token
  overlap — below `SIM_NEW` — so a similarity-gated check filed the opposite
  instruction as a new proposal. Negation barely moves the tokens but completely
  changes the meaning, which is what makes overlap the wrong gate.

(d) **The number-conflict detector collapsed the entire queue.** Four distinct lessons
  that merely contained different digits scored 0.6 similarity, were all judged
  contradictory, and each superseded the last — **1 row survived out of 4**. Number
  conflicts now require ≥0.75 similarity; polarity deliberately keeps no such guard.

(e) **The cascade ignored `target`,** so the same advice about two different templates
  reinforced into one row. A different target is a different change to make.

(f) **Found by driving the REAL dev home: a superseded proposal left a PENDING inbox
  row forever.** It can never be acted on — it no longer appears in the queue — so the
  row claimed attention for a decision unreachable from any surface. Now resolved on
  supersede.

(g) **Also from the dev home: superseded records accumulated with no path that ever
  removed them.** Added `prune_superseded` (keep 50): lineage is worth keeping, an
  unbounded pile is not.

(h) **Two negatives AGREE.** Contradiction is a polarity *difference*, not the presence
  of a negation — "avoid X" and "never X" say the same thing. I got this wrong in a
  test first; it is now pinned as its own property.

(i) **A failed install records NO decision.** Recording before the installer runs would
  mean a transient failure permanently suppresses its own retry.

(j) **`accept()` takes an INJECTED installer** rather than dispatching per-kind. This
  module owns the queue and the decision memory; making it also know how to write a
  skill, a template and a tier migration is the coupling that made the single-kind
  queue impossible to generalize.

(k) **`skills/proposals.py` is NOT deleted yet** — deliberately, and this is the one
  place this session leaves a dual path. It has live consumers (`dashboard/handlers/skills.py`,
  `after_turn_review`, `history`, 3 test files) and its own accept path that writes into
  the `auto/` skill namespace. Migrating those is step 3's Proposal-Inbox work (the FE
  surface lands there); doing it here would mean rewriting the skills page against a
  queue whose UI does not exist yet. Recorded as a DEVIATION from clean-break, with the
  retirement owned by the next session.

### Session 27 — Learning Flywheel step 4a: Curate (`feature-wf2-flywheel-curate`, PR #165)

`learning/decay.py` (one kernel, per-kind profiles, active-days clock),
`learning/usage.py` (one store in learning.db, per-entity semantics, multi-gate promotion),
`learning/curator.py` (bounded/reversible/refusing aging + review proposals + optimizer
battery). Wired into `history.py`'s verified consolidation cadence. `learning.curator_enabled`
through all four points. 11210 tests (+98), lint clean at 603 files.

**Deviations and findings:**

(a) **`DecayVerdict.__bool__` sabotaged its own consumer.** `_mutation` wrote
  `round(verdict.strength, 4) if verdict else None` — and `__bool__` is False for a
  PRUNED entity, so every archival journaled `strength: None`, losing the evidence for
  exactly the mutations most likely to need undoing. `is not None` is now explicit and
  commented. A convenience `__bool__` on a value object is a trap when the object is
  also checked for presence.

(b) **The kernel is pinned to the facet store's existing half-life.** `BASE_LAMBDA =
  ln2/30` so 30 active days still means 0.5 strength — verified equal to the old
  `0.5**(age/30)` to 1e-6. Without that, replacing `preference_facets.decay` would
  silently rewrite the meaning of every stored facet on upgrade.

(c) **Over-deletion refusal is measured against ELIGIBLE entities, not the batch.**
  Eight archivals from a batch of eight is normal for a large library; refusing that
  would make the curator unable to work at all. Pinned/user rows are excluded from the
  denominator — including them would let 10-of-30 read as a third and permit the cut.

(d) **A `MIN_SET_FOR_REFUSAL` floor of 8 is required.** A fraction of a tiny set means
  nothing: refusing "2 of 2" makes a small library un-groomable. I initially wrote a
  test asserting refusal on a 5-eligible set — the test was wrong, not the code.

(e) **Lessons are EXEMPT from the usage store, deliberately.** Their "surfaced" count
  degenerates to session count — a number measuring how much the user talks. Recording
  it would produce something that looks like evidence and means nothing. A caller that
  tries gets a no-op, not an exception: a policy that crashes callers gets worked around.

(f) **`success_rate` returns None, not 0.0, when nothing has run.** 0.0 means "ran and
  always failed". Collapsing them makes an unused template look broken and archives it
  for the wrong reason.

(g) **The scheduling gap the plan flagged was real:** `skills/curator.run_aging` had NO
  scheduled caller — a whole grooming pass existed and never ran. Now wired into
  `history.py`'s maintenance cadence (the tick that already runs `expire_by_category`
  / `promote_by_heat` / `synthesize_failures`). No new scheduler: a daemon for
  janitorial work is a new thing to monitor and a new thing to fail silently.

(h) **The curator DECIDES; the owner APPLIES.** `run_aging` takes plain `Candidate`
  values and returns a report — it does not reach into a skills loader or a template
  store. That split is what makes it testable without three subsystems, and what let
  it generalize past skills at all.

(i) **Reinforcement damping is shared with the kernel** (`reinforcement_weight`), so
  the usage store and the decay math cannot disagree about what a burst is worth.
  Measured: 11 raw events in one burst record as 6.

(j) **NOT DONE (deliberately):** `skills/usage.py` and `skills/curator.py` still exist
  with live consumers (the skills page, `loader.py`); `import_skill_sidecar` is the
  idempotent bridge and the sidecar is left on disk rather than deleted — removing the
  old source before the new one is verified in real use trades a recoverable state for
  an unrecoverable one. The LLM umbrella-consolidation pass, `compress_summary`'s
  siblings (downgrade_detail / merge_candidates / archive_unused as filed proposals),
  and the memory-heat migration onto the kernel are step 4b/9 work — each needs the
  surfacing merge that owns rank, and building them against the current two-engine
  surfacing would mean writing to a contract step 4b redefines.

### Session 28 — Learning Flywheel step 4b: Inject (`feature-wf2-flywheel-inject`, PR #166)

`learning/surfacing.py`: the ranked slot allocator — per-entity entry gates, one salience
pool, RRF fusion + diversification, slot priorities with ONE sacrificial slot, tiered
rendering with degrade-before-drop, the near-miss catalogue, the authority preamble,
intent-adaptive weights. Plus the live fix in `context.py`. `learning.context_budget_tokens`
through all four points. 11255 tests (+45), lint clean at 604 files.

**Deviations and findings:**

(a) **PREMISE MISMATCH (E1-adjacent, resolved by building the real scope).** The plan says
  "merge `skills/surfacing.py` + `workflows/surfacing.py`". **`workflows/surfacing.py` does
  not exist** — there is no workflow-surfacing module and no `[SUGGESTED WORKFLOW]` string
  anywhere in the tree. Likewise the "SOP `match_text` 0.62" is actually
  `agents_routing.min_confidence`. So there are not two engines to merge: there is ONE
  (`skills/surfacing.py`) plus an ambient render assembled inline in `context.py`. Built the
  allocator as the new owner of that render rather than inventing the second engine to
  merge. `skills/surfacing.py` is untouched and still live — it owns the embedding cache and
  keyword fallback, which the allocator does not duplicate.

(b) **THE REAL BUG THE PLAN PREDICTED, found and fixed:** `context.py` did
  `lessons_ctx[:_LESSONS_CAP]`, character-truncating the lesson block mid-sentence.
  Measured on a real 42489-char block against the 35000 cap: the old path cut a lesson at
  "…never deploy without running the full test suit". Lessons are the user's own
  corrections — the most authoritative content in the prompt — and "never deploy without"
  reads as an instruction that is not the one the user gave. `_fit_lessons` now drops WHOLE
  lessons and reports the count withheld. Verified: 494 kept, **0 partial**, at every cap
  including an impossible one.

(c) **Found by driving the real dev home: `MAX_PER_SOURCE` rationed LESSONS.** A 4th lesson
  was silently dropped while **3588 of 4000 tokens sat unused**. Diversification exists to
  stop a rich source crowding out a sparse one; it was never meant to quota the thing the
  whole slot policy protects. `UNCAPPED_KINDS = {"lesson"}` — lessons are bounded by the
  budget and their slot, not by a per-source count.

(d) **The authority preamble is only rendered IF IT FITS.** Measured: at 73 tokens it blew a
  50-token budget before a single item was considered — and a preamble with nothing under it
  is pure overhead asserting authority over an empty block.

(e) **Dropped items are catalogued from EVERY slot, not just the sacrificial one.** The
  first cut only recorded near-misses when trimming retrieved context; a skipped lesson
  vanished with no trace. A dropped item the model never hears about is a silent gap it
  cannot ask about — equally true for both.

(f) **Thresholds stay per-entity, with the calibration carried over.** 0.55 (short skill
  descriptions) and 0.62 (longer routing match text) are read from the existing code, and a
  test asserts the skill number equals `skills/surfacing.DEFAULT_SEMANTIC_THRESHOLD` — if it
  drifts, every existing skill's surfacing changes meaning.

(g) **char/4 is the LIVE token path** — tiktoken is not a dependency here (verified: import
  fails). So the fallback is the one that has to be right, and it over-estimates slightly,
  which is the safe direction for a budget.

(h) **A test premise of mine was wrong, not the code:** I asserted a near-miss catalogue at a
  200-token budget, but the diversification cap admitted only 3 context items so everything
  fit. The test now uses a budget tight enough that even L0 does not fit for the tail.

(i) **NOT DONE (deliberately):** the allocator is not yet wired as the sole owner of
  `build_session_context`'s assembly. Its policy is applied where the corruption was
  measurable (the lesson block), and the eight-part ambient render is a separate,
  behaviour-visible migration that needs the §2.5 measurement floor to prove no regression
  in what surfaces. Wiring it blind would swap a working render for an unproven one. Also
  deferred: the L0/L1/L2 tiers are consumed by the allocator but not yet PERSISTED per
  entity (that is a store change across four entity types), and `skills/surfacing.py`'s
  embedding cache generalization waits on that.
