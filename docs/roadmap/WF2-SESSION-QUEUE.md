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
| 29 | Judge contract + `runtime_hints` spec; typed verdict enum; judge isolation; deterministic pre-tier + `fallback_check` | G13 | ✅ DONE (#168) |
| 30 | Engine loop-node middleware: breaker + fingerprinting + escalation ladder + failure-class routing; fresh-session protocol; interrupt queue | G13 | ✅ DONE (#169) |
| 31 | Author the 8 template YAML specs + integration tests through the engine | G14 | ✅ DONE (#170) |
| 32 | Calibration + acceptance instrumentation: rubric contract, verdict ledger, divergence events, template lint, nodding-loop detector | G14 | ✅ DONE (#171) |
| 33 | FE + coexistence: template picker, cockpit live-follow, interrupt-queue UI, legacy alias layer, as-a-user validation of all 8 | G15 | ✅ DONE (#172) |

## D. Knowledge Synthesis (`-KNOWLEDGE-SYNTHESIS.md`) — needs engine Slices 0-2

| # | Session | PR group | Status |
|---|---|---|---|
| 34 | Store semantics: `kind`/`logical_key`/`last_verified`/`expires_at`, `item_relations`, hashing, `KnowledgeConfig` four-point wiring, `schema.md` | G16 | ✅ DONE (#173) |
| 35 | The provider pair: `knowledge_persist` + `knowledge_retrieve`, allowlist, native `search()`, three-node pattern end-to-end | G16 | ✅ DONE (#174) |
| 36 | Engine additions: `until_cancelled` loop mode + seen-set, `{{siblings.*}}`/`{{previous.output}}`, buffer-seal wait, adaptive delay clamp | G17 | ✅ DONE (#175) |
| 37 | Consolidation + maintenance: reflect mechanics, `knowledge-health`/`lint`/`gap-healing` templates, proposal routing, differential refresh | G17 | ✅ DONE (#175) |
| 38 | Contradiction + retrieval polish: persist-time conflict pass, typed-edge inference, contradiction UI, Session Brief, fencing filter | G18 | ✅ DONE (#176) |
| 39 | Template slate + long-run validation (idempotent re-runs, bounded cycle cost, seen-set across restart) | G18 | ✅ DONE (#177) |

## E. Universal Planning (`-UNIVERSAL-PLANNING.md`) — needs engine + Loops templates

| # | Session | PR group | Status |
|---|---|---|---|
| 40 | Matching + classification: intent classifier, tiered `match_template()` T1-T5, metadata extensions, CI routing fixtures; delete dead chat plan-mode | G19 | ✅ DONE (#178) |
| 41 | Grounded generation: grounding bundle from live registries, pattern-shape registry, schema-constrained `oneOf`, repair-not-regenerate, brownfield pass | G19 | ✅ DONE (#179) |
| 42 | Contracts + parameterization: done-means contracts + lint + preflight, `resolve_unfilled_inputs()`, triage-first, blocking/open decision typing | G20 | ✅ DONE (#181) |
| 43 | Review + revision: streaming multi-view review, typed merge-by-id, TTL'd sketches, plan-as-artifact, `revise{step_ref, comment}` | G20 | ✅ DONE (#182) |
| 44 | Autonomy + risk: risk-signal registry, autonomy floors, HITL/AFK typing, confirmation matrix, earned trust, planner read-only posture | G21 | ✅ DONE (#183) |
| 45 | Grill + entry surfaces + template pipeline: `rigor: deep` protocol, rigor:fast, session mining, suggest_template, per-template eval specs | G21 | ✅ DONE (#184) |

## F. Work Containers (`-WORK-CONTAINERS.md`) — needs engine + Tasks

| # | Session | PR group | Status |
|---|---|---|---|
| 46 | Project umbrella extensions + run→project binding; hub Work tab state-grouped board (incl. queued/suspended/claimed, truthful across a kill) | G22 | ✅ DONE (#185) |
| 47 | Artifacts reuse: `publish: {artifact}`, version-on-material-change + change_note, typed lineage deep links | G22 | ✅ DONE (#186) |
| 48 | Subagent batch hardening: isolation, schema-validated typed output, secret-filtered env, leases (no double-execution) | G23 | ✅ DONE (#187) |
| 49 | Run workspace + environment: provisioning block, folder contracts, per-project run-env secrets | G23 | ✅ DONE (#188) |
| 50 | Session ownership + truthful run lifecycle; incognito enforcement (`session_restrictions` + `memory_mode`, durable across restart) | G24 | ✅ DONE (#189) |
| 51 | Needs-input inbox: NeedsInputItem cards (blocker, attempted, evidence, recommendation, one decision), resume_token wiring, >24h re-notify | G24 | ✅ DONE (#190) |
| 52 | Code-kind worktrees: preserve_patterns, idempotent setup, resume-safe, teardown before deletion, cockpit diff + Apply Locally/Checkout | G25 | ✅ DONE (#191) |
| 53 | Introspection checklist: RunStats strip, template p50/p95 cards, Proof section, fake-check warning badge | G25 | ✅ DONE (#192) |
| 54 | Project export/import: brief/overview/ledgers/templates/artifact metadata/run digests, sha256-verified, zero secrets, `projects` snapshot component | G26 | ✅ DONE (#193) |

## G. Tasks & SOPs (`-TASKS-SOPS.md`) — needs engine + Work Containers

| # | Session | PR group | Status |
|---|---|---|---|
| 55 | Projection core: `workflow_binding` + new Task fields, `TaskStatus.SKIPPED`, auto-materialization + fingerprint dedup + fan-out caps, typed state projection | G27 | ✅ DONE (#195) |
| 56 | Verified done + enforcement: engine-owned criterion execution, pass-state gating, three-actor matrix, managed-write rejection, cascade-fail, stuck-work sweep | G27 | ✅ DONE (#196) |
| 57 | ConfirmationRequest + gates: durable record + atomic single-use resolution + auto-resume, `require_hitl`, DagView Approve/Deny, per-stage mute, tool profiles | G28 | ✅ DONE (#197) |
| 58 | Surfacing core: `surface_mode` enum, trigger-phrase `match_text` + collision check, metadata split + lints, one-source-two-wrappers injection, SOP migration | G28 | ✅ DONE (#198) |
| 59 | Surfacing channels + resolution: cadence channel + overdue escalation, fingerprint channel + packs, layered scope resolution, parameter pre-fill, reachability doctor | G29 | ✅ DONE (#199) |
| 60 | Pool + templates: frontier/next projections, evented unblock, TTL'd leases, write-time acyclicity, lifecycle events, seed template library | G29 | ✅ DONE (#200) |
| 61 | Def-side surfacing fields (`DefMetadata`: surface_mode, cadence_days, escalation, packs, hands_off_to, guided) + the def→record adapter — RE-SCOPED from UX, which was unbuildable: the fields S55-S60 read did not exist | G30 | ✅ DONE (#201) |
| 61b | Backend wiring: `metadata` write path on `author_def`, `GET /api/workflows/surfacing` (freshness + scope + packs + doctor findings), `TaskComplete` emission from `update_task` | G30 | ✅ DONE (#202) |
| 61c | FE surfacing surfaces: `surfacingMeta` presentation layer, composer-chip rule, allowlisted deep-link params, templates-list freshness/mode/pack/doctor rendering, validated as-a-user against a live gateway | G30 | ✅ DONE (#203) |
| 61d | Lease write path (flock'd CAS + sweep, 0/12 multi-winner across 8 processes) + `POST /runs/{id}/confirm` verb resolve + the FE node→token join | G30 | ✅ DONE (#204) |
| 61e | The 5 task-projection events on BOTH channels (ledger kinds + `_publish` + the FE union, guarded in both directions) — UNBLOCKS the stream unions | G30 | ✅ DONE (#205) |
| 61f | The projection CALL SITE: `RunController` projects settled leaves via `materialize` (+ container/opt-out/failure refusals, refresh dedup), verified on real runs | G30 | ✅ DONE (#206) |
| 61g | The Task WRITE through the provider (engine actor, drained at completion, shielded bound) + 3 measured defects + the def-registry leak fix | G30 | ✅ DONE (#207) |
| 61h | The VERIFICATION call site (criterion execution, tristate preserved, drained) | G30 | ✅ DONE (#210) |
| 61i | The confirmation-gate emission (pending on the continuation's idempotency, resolved after the claim) + the `master_fd=99` fixture bug behind the terminal flake | G30 | ✅ DONE (#211) |
| 61j | DagView composition: `runDag` layout + List/Graph toggle + the gate verbs wired to `/confirm`; fixed container-less depth and the clipped overlay | G30 | ✅ DONE (#214) |
| 61k | Config four-point wiring + the fifth point (`workflows/settings.py` resolvers, so the knobs are not inert) + checklist two-stage delete and a REAL checked-locks-drag | G30 | ✅ DONE (#215) |

## H. Automation Substrate (`-AUTOMATION-SUBSTRATE.md`) — final step needs Loops Phase 4

| # | Session | PR group | Status |
|---|---|---|---|
| 62 | Trigger entity + per-kind specs + fire/run records with typed outcomes | G31 | ✅ DONE (#216) |
| 63 | Disposition table AS CODE (14 rows, module-existence checked) + the crash-safe scheduling discipline (jitter parity with the shipped scheduler asserted) | G31 | ✅ DONE (#217) |
| 64 | Dispatch (inbox + wakeup, resume-never-droppable) + the delivery contract + the SPOOL that fixes the reproduced sync-context silent drop | G32 | ✅ DONE (#218) |
| 65 | Missed fires: bounded honest enumeration (budget bounds ROWS, not counts — measured), review decisions that always write a row, catch_up once + staggered | G32 | ✅ DONE (#219) |
| 66 | Cron migration: lossless against a store the REAL service wrote (0 unaccounted fields); `every`≠`at` (a one-shot would kill every interval job); dry-run report | G33 | ✅ DONE (#220) |
| 67 | Event-kind API parity (toggle/update/run/test/history: all 404'd or silently no-op'd) + dormancy surfaced for the **7** (not 8 — S60 wired TaskComplete) | G33 | ✅ DONE (#221) |
| 68 | Autopause after 5 TRUE failures (found: 5 denylist BLOCKS disabled a trigger) + typed exits park + Runs-inbox surfacing | G34 | ✅ DONE (#222) |
| 69 | Injection screen 5/18→18/18 caught + 0 false positives, wired into the fire path (found: payloads reached providers UNFENCED) + deny-by-default capability fence + zero-silent-drop rows | G34 | ✅ DONE (#223) |
| 70 | Quiet-hours semantics for a key RESERVED since S62 (wrap rule matched to the shipped matcher) + fail-open duty-gate provider seam (#47 rule) + week-grid endpoint + `automation doctor` (6 findings) | G35 | ✅ DONE (#224) |

## I. Learning Flywheel steps 5-8 (`-LEARNING-FLYWHEEL.md`) — needs the Run Ledger + everything above

| # | Session | PR group | Status |
|---|---|---|---|
| 71 | Measure: per-arm precision (found: `fuse` attributed by dict-insertion order) + data-driven threshold proposals + Beta-Binomial trust; reconciled a forked ARM_CONFIDENCE table | G36 | ✅ DONE (#227) |
| 72 | Self-model: capped (full tier DISPLACES, never appends) + propose-never-install + compact snapshot; found `user.selfmodel.*` leaking into user-FACT blocks | G36 | ✅ DONE (#228) |
| 73 | Refiner acceptance discipline: mechanism clustering (freq × unresolvedness) + power floor + median-of-3 critic + GateOK + frozen region, all as pure decisions | G37 | ✅ DONE (#229) |
| 74 | Deterministic detector chain (free at both extremes, typed skip reasons) + closed FailureMode enum; FOUND the env-failure deny-filter catching 1 of 4 → now 12/12, 0 false positives | G37 | ✅ DONE (#230) |
| 75 | Proposal Inbox view model + THE accept gate — found the "model cannot accept its own proposals" invariant holding only by ABSENCE of a caller; now enforced in the real accept()/reject() | G38 | ✅ DONE (#231) |
| 76 | Week-at-a-glance panel — outcome records + proposal ids were already shipped; the real gap was that `health()` CANNOT SEE A SILENT DAY (absent day == healthy day) | G38 | ✅ DONE (#232) |
| 77 | Predict-then-verify 5-way verdicts + HARMFUL-only auto-reverts + proposer trust; incognito gate is CLOSED but SESSION_END/RUN_END have zero callers (gap now pinned) | G39 | ✅ DONE (#233) |

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

### Session 29 — Loops Evolution: the judge contract (`feature-wf2-loops-judge`, PR NOT OPENED)

> **BLOCKED on publication only.** `git push` is denied at the permission layer (it succeeded for
> sessions 24-28 in the same run, so something changed mid-run). Commit `064ea34` is complete and
> fully gated; it is NOT on origin and no PR exists. The earlier `#167` in this file was a
> placeholder I should not have written — corrected. Code work continues on later sessions, which
> branch from the local `feature-wf2-loops-judge`; publication of the whole tail needs one push.

`workflows/judge_contract.py` (typed verdict enum, rubric ratchet, engine-computed overall,
forbidden-mode denylist, N-sample median), `judge_pretier.py` (the free rule tier + the
tristate `fallback_check`), `judge_actors.py` (the worker-never-completes invariant + judge
isolation). `runtime_hints` on `WorkflowDef`. 11379 tests (+124), lint clean at 607 files.

**Deviations and findings:**

(a) **The forbidden-mode denylist was INERT on realistic phrasing.** My first matcher required
  EVERY long word of a mode string to be present, so "the test was deleted" missed "test deleted
  OR skipped" — the phrase lists alternatives, not a conjunction. Measured against 6 real
  admissions: only 2 matched. This is the worst kind of control failure — present, plausible,
  and doing nothing. Fixed to require TWO distinct stemmed signals (one word appears in innocent
  prose constantly), and re-phrased the defaults to two-signal forms so "value hardcoded to
  satisfy assertion" is its own entry rather than a buried alternative. 6/6 caught, 4/4 clean
  after.

(b) **`DecayVerdict.__bool__`-style trap avoided here on purpose:** `JudgeVerdict` has `.valid`
  and `.passed` as SEPARATE properties, never a `__bool__`. "Well-formed" and "approved" are
  different questions, and after last session's `if verdict` bug I did not give this object a
  truthiness at all.

(c) **The worker's `done` is REDIRECTED to `review`, not rejected.** The work may genuinely be
  finished; erroring would strand the run, and silently accepting would defeat the invariant.
  An unknown actor, by contrast, falls to `failed` — defaulting an unrecognized actor to
  permissive would be the exact hole the invariant closes.

(d) **`cross_model` isolation refuses a same-FAMILY judge,** not just a same-model one —
  same-family judges share the blind spots they are meant to catch. Family is a crude prefix
  match on purpose: a registry of exact lineages would go stale toward falsely reporting
  independence.

(e) **`fallback_check` is TRISTATE and a standing cross-check.** None ("could not run") never
  reads as failure — reused from `loop/gates.run_verify_command`'s exit-127→None discipline. A
  judge PASS while the deterministic check FAILED auto-escalates; a judge that passes what
  `exit 1` failed is either wrong or being gamed.

(f) **The pre-tier can never issue a PASS** — only REJECT or pass-through. A cheap approval would
  recreate self-approval with extra steps. It proves work UNFINISHED (empty / gave-up / tool
  error / stub / missing artifact / no output), and only what survives is worth a model call.

(g) **The session-24 docs gate caught the three new modules undocumented** — worked exactly as
  built; added them plus a judge-contract section to `docs/architecture/workflows.md`.

(h) **Real-model validated through the LIVE dev gateway** (bare-process provider registry is
  empty — providers load gateway-side only). claude-sonnet-5 via Bedrock returned a correctly-
  shaped verdict and REJECTED thin evidence unprompted; the contract then caught the rubber-stamp
  (same zero-scored evidence flipped to PASS → rejected on the ratchet) and the deterministic
  contradiction (well-formed PASS + failed check → escalated).

(i) **Two test premises of MINE were wrong, not the code:** a tautological `== 1 or True`
  assertion, and an empty `stop_condition` expected to floor to 1 when `.get(k, 2)` correctly
  returns the default. Both corrected.

(j) **NOT DONE (this session is the contract + invariants only, per the queue split):** the
  engine loop-node MIDDLEWARE that consumes these (breaker, fingerprinting, escalation ladder,
  failure-class routing, fresh-session protocol, interrupt queue) is session 30; the 8 template
  YAML specs are session 31. This session builds the enforcement primitives those depend on.
  `loop/judge.py` is deliberately NOT unified yet — LOOPS-EVOLUTION converges loops onto v2 in
  its own slices, and unifying pre-emptively would be wasted motion.

### Session 30 — Loops Evolution: loop-node middleware (`feature-wf2-loops-middleware`, PR NOT OPENED — push blocked)

`workflows/loop_middleware.py`: failure classification (7 classes + unknown), tool-argument
fingerprinting, the Continue→Nudge→Escalate→Halt ladder, failure-class routing with per-class
entry rungs, recoverable-class headroom, the structured "never silence" brief, and the atomic
interrupt queue. 4 new ledger kinds wired into the FE `RUN_LIFECYCLE` union with a bidirectional
drift test. 11442 tests (+63), lint clean at 608 files, full FE gate green (typecheck + 427 tests
+ build).

**Deviations and findings:**

(a) **Built ON TOP of the existing `resilience.check_breaker`, not over it.** That breaker
  (Slice 2c) already catches max-iterations, error-streak, identical-output, and token cap. This
  adds only the tiers that need more than counters (call fingerprinting, failure-class routing,
  the nudge tier) — re-implementing the four it already has would have been duplication dressed as
  completeness.

(b) **`fresh_session`/`model_switch` ESCALATE, they do not HALT.** My first cut mapped every
  non-classified-retry rung to HALT, which made the entire middle of the ladder dead code — "try a
  clean session" became "ask the human". Added a distinct `Action.ESCALATE`; only SURFACE halts.

(c) **`attempt_cap` bounds attempts WITHIN a rung, not ladder position.** Measured: applied as a
  position cap, `restart_from_scratch` was unreachable under the plan's OWN declared values (cap 3,
  5-rung ladder) — a configured rung that can never be selected. Added `attempts_at_rung`; a
  reachability test now walks the whole ladder and asserts every rung is selected.

(d) **`MiddlewareVerdict.__bool__` RAISES.** After last session's `DecayVerdict.__bool__` /
  `if verdict` bug, this verdict object refuses a truth value outright — `if verdict` must be
  written `if verdict.action is ...`. A convenience truthiness on a verdict is a trap.

(e) **Recoverable classes (429, 5xx, timeouts) never consume a rung** and get 3× headroom before a
  halt — burning the escalation ladder on a rate limit surfaces a run that would have succeeded.
  Classification checks rate-limit/context-overflow FIRST, since their messages also contain the
  generic words a broader pattern would claim.

(f) **The nudge names the STALL SHAPE when the failure class is UNKNOWN,** and does so on EVERY
  nudge, not just the first (measured: cycles 4-5 fell back to generic text). "You ran the
  identical command three times" is precise advice; "change your approach" is not.

(g) **4 new ledger kinds (`breaker_trip`, `steering`, `judge_verdict`, `judge_divergence`) MUST be
  in the FE `RUN_LIFECYCLE` union** — EventSource silently drops unregistered types. A drift test
  reads `useRunStream.ts` and asserts every one is present; I verified it FAILS when a kind is
  removed, because an untested guard is no guard.

(h) **NOT DONE (needs live-engine wiring, out of this session's deterministic-primitives scope):**
  the middleware is not yet CALLED from the `RunController` tick — that is the seam where the
  loop-node execution path consumes it, and wiring it means touching the single-writer tick loop
  under a live run, which the interrupt-queue and fresh-session-retry integration (R7 lifecycle
  protocol: 5-field handoff, repair-vs-restart, executor_caps gating across the 3 ACP dialects)
  properly belong to. The R13 context-overflow recovery (reactive one-shot compaction) needs the
  summarizer seam the architecture doc already records as absent. This session is the pure
  decision layer those consume; it is fully unit- and sequence-validated in isolation.

### Session 31 — Loops Evolution: the loop-kind templates (`feature-wf2-loops-templates`, PR NOT OPENED — push blocked)

Five new bundled templates (`goal-pursuit-open-ended`, `goal-pursuit-verifiable`, `general-project`,
`design-project`, `diagnose-run`) + `tests/test_workflows_loop_templates.py` (132 tests).
11629 tests (+187), lint clean at 608 files, all 11 templates verified in a built wheel and
through the live API.

**Deviations and findings:**

(a) **FIVE new templates, not eight.** The plan's §"Per-Kind Template Designs" describes six
  families; `deep-research` (the research-loop descendant) ALREADY SHIPS from Slice 9a, and the
  `goal-pursuit-monitor` variant is **not buildable yet** — its design is a parked run driven by
  `set_onetime_task`/`set_recurring_task`, tools that belong to AUTOMATION-SUBSTRATE and do not
  exist (verified: no such symbol anywhere in the tree). Authoring it would mean shipping a
  template whose central mechanism silently no-ops. Recorded rather than faked.

(b) **`{{defaults.runtime_hints.judge.rubric}}` IS NOT A VALID BINDING.** The plan's YAML uses it
  throughout; the engine's binding roots are `inputs`/`nodes`/`item`/`iter`/`last` only
  (validator.py:346), and it was flagged as `WF_UNKNOWN_BINDING_ROOT`. Rubrics and forbidden-mode
  lists are therefore INLINED into judge prompts — also more legible to the judge than a rendered
  data structure. A test now asserts every declared rubric criterion and forbidden mode actually
  appears in a judge prompt, because a rubric the judge never sees scores nothing.

(c) **Two more spec-key mismatches in the plan's YAML,** both caught by the validator: a gate's
  kind is `config.kind`, not `gate_kind`; an `until` loop needs `config.condition`, not
  `config.until`.

(d) **`continue_on_error` does not exist** — I invented it for the baseline action. The bundled
  action-arg guard (a landmine recorded from an earlier session) caught it: the real key is
  `allow_failure` INSIDE `with`, as `code-implementation` already does.

(e) **`design-project` shipped with NO JUDGE in my first cut** — its refinement loop exited on a
  self-reported `issues_resolved`, which is exactly the "no agent certifies its own work" rule the
  plan calls the platform's oldest. My own structural test caught it; added a fresh-isolated
  read-only judge.

(f) **The terminal `accept` gate lacked the anti-leniency line.** It is the LAST check before the
  run reports success to the user, so it is the one place that most needs "do not talk yourself
  into approving".

(g) **A prose collision with a convention gate:** "Finding issues is the normal outcome" tripped
  `test_every_review_stage_uses_the_canonical_Finding_record`, which greps for the literal
  "Finding". Reworded to "Reporting real issues…" — the doctrine was worth keeping, the capital F
  was incidental.

(h) **Two of my own tests were wrong, not the templates:** `_judges()` classified any
  `tools_posture: verify` stage as a judge (diagnose-run's trace stage is read-only because it
  reads a LEDGER), and `required` + `default: null` is the LOADER's normalization rather than an
  authoring conflict.

(i) **`EXPECTED` in `test_workflows_bundled.py` grew 6 → 11,** so the pre-existing convention suite
  now holds the new templates to every gate it applies to the originals — strict validation,
  lint-clean-as-bundled, canonical Finding record, action-arg nesting, name/dir agreement.

(j) **NOT DONE:** the monitor variant (a), and the `code-project` SDLC template — the plan's design
  for it is the largest of the six (WIP=1 enforcement, four structural gates, an initializer stage,
  `tick.evaluate` porting) and it overlaps `code-implementation`, which already ships. Reconciling
  the two is a judgement about whether to replace a working template or add a second beside it,
  which is a product decision rather than a mechanical port.

### Session 32 — Loops Evolution: calibration + acceptance instrumentation (`feature-wf2-loops-calibration`, PR NOT OPENED — push blocked)

`workflows/judge_calibration.py` (verdict ledger, divergence records, the nodding-loop detector,
stuck detection, the judge canary, the hardening-loop exemplars) + six anti-pattern lint rules and
the five-moves audit in `template_lint.py`. 11697 tests (+68), lint clean at 609 files.

**Deviations and findings:**

(a) **FIVE false positives from my own first draft of the lint rules — all on the SHIPPED
  library, none of them real template defects.** In order: the amnesiac rule accepted only
  `{{last.}}`/`{{iter.}}` when `{{nodes.}}` inside a loop body is equally cross-iteration
  state; the nodding rule demanded a field literally named `verdict` when `refuted: boolean`
  routes just as well; it demanded `tools_posture: verify` from `infer` nodes, which have no
  tools BY DEFINITION; the tangled rule required `progress_field` when `streak` alone is a
  valid `until_dry` exit, and required a literal int `max_iterations` when a binding
  (`{{inputs.rounds}}`) is a perfectly good cap; and the blind rule only recognised verifiers
  with "judge" in the name, missing `verify_refute`, `completeness_critic` and `round_gaps`.

  Each was fixed **at the rule**, not exempted at the call site. `KNOWN_ANTI_PATTERNS` is
  empty and a test asserts it stays empty. A lint that cries wolf on the library it ships
  with is a lint authors learn to ignore, which is worse than no lint.

(b) **I nearly shipped one of those false positives as a "real finding".** I had written
  `audit-sweep`'s amnesiac flag into the queue as a genuine latent defect with a reasoned
  exemption — and the `test_the_known_finding_is_still_real` self-check I wrote alongside it is
  what proved the exemption wrong once the rule was fixed. Worth recording because the
  plausible-sounding write-up was the dangerous part, not the rule.

(c) **A verdict record keeps DISCARDED iterations** (`status="discard"`). A ledger of only the
  verdicts that stuck cannot answer "does this judge ever reject?", and excluding them would
  let a template look like a nodder precisely BECAUSE its judge was forcing rewinds.

(d) **`pass_rate` and `median_overall` return None, not 0.0, on no data** — 0.0 reads as
  "always fails", which is a different and alarming claim.

(e) **`false_pass_rate` is reported SEPARATELY from any accuracy figure.** An instrument that
  is 90% accurate but wrong in the dangerous direction every time is not 90% good, and one
  averaged number would hide exactly that.

(f) **A probe that could not RUN is not a blind judge.** `calibrated=None` is distinct from
  `False`; declaring a judge untrustworthy because the probe broke would halt runs for an
  infrastructure problem. The separation threshold is asserted equal to
  `loop/instrument._CANARY_MIN_SEPARATION` — a second threshold would make the same judge
  trustworthy to one caller and blind to another.

(g) **A malformed `scores` field raised `AttributeError`,** which my `except (TypeError,
  ValueError)` did not name — so the "degrades gracefully" docstring was false. Fixing it
  raised the better question: such a row is still USABLE (its verdict is what the detector
  counts), so it is now kept with empty scores rather than dropped. Dropping it would lose a
  real rejection over a secondary field.

(h) **`OBSTRUCTING` (never passes) does NOT block a template from becoming default,** while
  `NODDING` does. Both are broken, but obstruction fails work that should pass — visible and
  annoying — whereas nodding passes work that should fail, invisibly.

(i) **Validated against the REAL Bedrock verdict captured in session 29:** it journals, round-
  trips with its `prompt_version` intact, reads as `discriminating` inside a realistic 10-run
  history, and a simulated human override becomes a labelled `false_reject` exemplar with the
  user's reasoning verbatim.

(j) **NOT DONE:** the calibration is not yet CALLED from the run path — nothing emits
  `judge_verdict` / `judge_divergence` events yet (the kinds are registered and the FE union
  knows them, from session 30). Wiring the emit points means touching the controller tick and
  the human-override UI, which is session 33's FE work. The `probe_judge` template-save-time
  hook is likewise deferred: `assess_separation` is pure and tested, but the probe that FEEDS
  it needs a live model call on the save path, and putting a model call in a save is a latency
  decision worth making deliberately rather than in passing.

### Session 33 — Loops Evolution: FE + coexistence (`feature-wf2-loops-fe`, PR NOT OPENED — push blocked)

`workflows/loop_aliases.py` (read-time legacy-kind aliases + cockpit key equivalence), the same two
in `web/src/pages/workflows/containerKey.ts` with a backend↔FE drift test, the R14 steering endpoints
(`/steer`, `/steering`) + service functions + FE client methods, and the alias manifest surfaced
through `/api/workflows/manifest`. 11750 tests (+53 py, +13 ts), lint clean at 610 files, full FE gate
green.

**Deviations and findings:**

(a) **A test of mine LEAKED a run into the real `~/.personalclaw/workflows/runs.db`** — the exact
  thing the doctrine forbids. My `run_store` fixture patched `personalclaw.config.loader.config_dir`,
  but `store.py` does `from ... import config_dir` (a MODULE-LEVEL bind), so the patch never reached
  it and `store.save()` wrote to the developer's actual home. The leak surfaced THREE test files away
  as `test_custom_agent_gets_hook_transform` failing, because a live run makes `build_message`
  prepend an `[ACTIVE WORKFLOWS]` block to every message. Root-caused (not masked), the stray
  `r-steer` row deleted from the real db, and the fixture fixed to patch `store.config_dir` directly.
  The `[[personalclaw-test-isolation-hazards]]` memory gets a new entry.

(b) **The cockpit key-equivalence fix (R10c) is the whole point of coexistence, and it is a SILENT
  regression class:** `loop:<id>` (loop cockpit) vs a run-scoped key compared with `==` drops every
  event with no error — the stream connects, the cockpit renders, nothing updates. `base_container`
  strips any of four prefixes (longest-first, so `workflow:run:` beats `workflow:`) and compares the
  bare id. Implemented identically on both sides with a drift test I verified FAILS on divergence.

(c) **The alias layer is deliberately ONE-WAY.** A loop kind resolves to a template; a template does
  NOT resolve back. Reverse lookup would invite writing new references in the legacy vocabulary, and
  an alias layer that accepts new writes is a second API rather than a bridge. A test asserts no
  `template_to_*` / `*_to_kind` export exists.

(d) **An unknown kind resolves to "" , never a default.** Guessing a template for an unrecognised
  identifier would silently run the WRONG workflow, and "it ran something" is far harder to debug
  than "it ran nothing and said why".

(e) **`goal` + a verify command resolves to the VERIFIABLE variant.** A goal loop carrying a command
  that proves it was the verifiable variant in all but name; honouring that beats dropping the user
  into a template that ignores the command they already supplied.

(f) **Every alias points at a SHIPPED template** — asserted per-alias against `template_names()`,
  because an alias to a missing template is a dead reference that only fails when a user clicks it.
  `code` maps to `code-implementation` (which ships) rather than the deferred `code-project`.

(g) **Steering is QUEUED on the run, consumed at the iteration boundary** — same single-writer
  discipline as pause/cancel. Injecting mid-iteration would race the worker's state, and the pending
  queue is exposed via `/steering` because a queued instruction the user cannot see is
  indistinguishable from one that was dropped.

(h) **The routes-reference doc is GENERATED, not hand-edited.** My manual edit to `routes.md` went
  stale against `manifest_reference`; regenerating produced better entries from the handler
  docstrings. Regenerated and committed.

(i) **NOT DONE (deliberately, needs the run-path wiring these sit above):** the steering queue is
  stored and surfaced but the tick loop does not yet CONSUME it — that is the same controller-tick
  seam sessions 30 and 32 also stopped short of, and wiring all three at once against a live run is a
  single coherent change rather than three half-changes. Likewise the template-picker widget itself
  (the alias table + manifest are the data it needs; the React component is not built) and the
  cockpit's actual adoption of `keysEquivalent` (the function + its tests exist; swapping the
  cockpit's `===` comparisons over to it is a behaviour-visible edit to a live surface). The
  as-a-user validation of all 8 templates is bounded by the same run-path gap. These are honestly the
  back half of "FE + coexistence" and belong with the engine-integration session that consumes the
  three decision layers built in 30/32/33.

### Session 34 — Knowledge Synthesis: store semantics (`feature-wf2-knowledge-store`, PR NOT OPENED — push blocked)

`knowledge/semantics.py` (typed kinds, logical identity, content/chunk hashing, confidence
aggregation, claims/mentions, freshness, the idempotency decision, typed item relations),
`knowledge/schema_conventions.py` (the `schema.md` conventions contract), the five additive columns
+ `item_relations` table + logical-key index in `store.py`, and `KnowledgeConfig` through all four
wiring points. 11829 tests (+79), lint clean at 612 files, validated against the real dev-home store.

**Deviations and findings:**

(a) **`KnowledgeConfig` did not exist** — the plan says "four-point wiring" as though it were an
  existing dataclass gaining fields. Created it from scratch: dataclass + `_meta` on every field,
  the `AppConfig` field, the `load()` mapping AND its `knowledge_data` section read, the `to_dict`
  entry, and four entries in `_EDITABLE_CONFIG`. A test asserts all four points, because omitting
  any one makes a knob silently inert.

(b) **`report_budget_chars` would have been a knob that does nothing.** My first cut had
  `check_persist` read the module constant. Added `effective_budgets()` which consults config, and
  verified with a real config file that a 100-char budget actually rejects a 500-char report. A
  misconfigured `0` is treated as unset rather than as "nothing may be written".

(c) **The logical-key index needed to be created in `_migrate`, not the schema block.** The schema
  block runs BEFORE `_migrate` adds the column, so a `CREATE INDEX` there fails on a fresh db and
  silently no-ops on an upgraded one — I hit both. Creating it after the ALTERs is the only ordering
  that works for either.

(d) **My own index test was asserting nothing.** It matched `str(sqlite3.Row)` — an object repr —
  against "index", which can never contain it. Reading the `detail` column instead revealed the
  index genuinely was missing (plan: `SCAN items`), which is how the real gap in (c) surfaced. A
  test that cannot fail is worse than no test.

(e) **Confidence aggregation reached exactly 1.0 at ten sources,** contradicting my own docstring.
  A claim at 1.0 is unfalsifiable — no later contradiction can lower it — so `MAX_CONFIDENCE = 0.999`
  is now a hard ceiling, and a test asserts it across 1/2/10/50 sources.

(f) **Aggregation is `1 - ∏(1 - cᵢ)`, deliberately not a sum or a mean.** Three sources at 0.6 give
  0.936: more than any one, less than certainty. Summing exceeds 1.0; averaging makes corroboration
  WEAKEN a strong claim, which is the opposite of what agreement means.

(g) **The same source twice is not two confirmations.** Mentions dedupe on `source_ref`, or one loud
  source could manufacture consensus with itself.

(h) **Supersession sets `invalid_at` and never deletes** — "what was true when" stays queryable,
  which is the difference between a knowledge base and a cache.

(i) **`schema.md` is written once and never overwritten.** An owner's conventions are the one thing
  in the store the system has no business editing; a "helpful" refresh would discard the reasoning
  they encode. Loading is bounded at a LINE boundary (half a convention is worse than none), and an
  absent document returns "" rather than silently adopting the defaults.

(j) **Migration verified additive on a simulated pre-migration db:** columns dropped back out, then
  reopened through the store — every column returns, `item_relations` is recreated, and the existing
  row survives. Re-opening twice is safe.

(k) **NOT DONE (this session is the groundwork the next one consumes):** `knowledge_persist` /
  `knowledge_retrieve` are session 35 — the semantics module is pure and store-agnostic on purpose,
  so the providers can be written against it without re-deriving identity rules. The `ops` op-list
  payload, `also_artifact` dual-write, and the async enrichment/backfill loop all belong to that
  provider pair. Nothing yet WRITES `kind`/`logical_key`/`content_hash` on the live ingest path —
  the columns exist and are indexed, and populating them is the persist provider's job.

### Session 35 — Knowledge Synthesis: the provider pair (`feature-wf2-knowledge-providers`, PR NOT OPENED — push blocked)

`action_providers/knowledge_persist_provider.py` + `knowledge_retrieve_provider.py`, registered in
the action registry AND `ALLOWED_HOOK_PROVIDERS`, plus node provenance threaded through
`dispatch_action`. 11870 tests (+41), lint clean at 614 files. The three-node pattern was driven
end-to-end through the live engine, including an idempotent re-run.

**Deviations and findings — six defects, every one found by measuring:**

(a) **`items_fts` has NO triggers.** It is an EXTERNAL-CONTENT fts5 index over a view, so a row
  written with plain SQL is simply not searchable. Every retrieve fell through to
  `substring_fallback` until the persist provider synced the index — and a substring fallback looks
  identical in the output to a working hybrid search, so the whole retrieval tier would have been
  quietly useless. The store's own docstring warns that `'rebuild'` against a stale content target
  WIPES THE INDEX AND REPORTS SUCCESS, so this uses the delete-then-insert pattern the store's
  existing manual sync sites use.

(b) **The FTS delete has to read the OLD values BEFORE the row is rewritten.** `items_fts_src` is a
  VIEW over the live row, so reading it after the UPDATE returns the NEW content and the delete
  removes nothing. Measured: "aardvark" still matched after the body had been replaced with
  "buffalo". Fixed with `_fts_snapshot` taken pre-write.

(c) **The hybrid retriever's scores are RRF (~1/(60+rank)), not cosine.** A top hit scores about
  0.033. My 0.30 "relevance cliff", borrowed from cosine space, rejected EVERY hybrid result — the
  provider silently returned nothing on a store that plainly contained the answer. The cliff now
  applies only to tiers whose scores are similarities; create-safety uses RANK for fused results.

(d) **`HybridRetriever.search` does not return `kind`,** or any of the timestamp columns. So the
  kind filter matched nothing (every hit had `kind: None`) and every freshness reading was zero,
  both silently. Added `_enrich` to fill them from the store.

(e) **Both tag tables have NOT NULL timestamp columns** (`tags.created_at`, `item_tags.added_at`).
  Omitting them made every tag insert fail, and my broad `except` swallowed it so cleanly that a
  measurement run showed `tags: []` with no error anywhere. Now logged at WARNING with a returned
  count — best-effort has to be best-effort about something that works.

(f) **`ActionContext` carries only `event`/`context`/`payload`.** Reading `ctx.run_id` (as my first
  cut did) silently produced "workflow:unknown" for every persisted item. Provenance now comes from
  `ctx.payload`, and `dispatch_action` threads `node_id` in. The RUN id is genuinely unavailable at
  that seam — `dispatch_action` does not receive the run and `BindingContext` does not carry it — so
  the provider degrades to a node-scoped ref and says so rather than the engine growing a parameter
  nothing else needs yet.

(g) **"Always included" and "always first" are different guarantees.** The `overview` was inserted
  only when absent from the hits, so when it WAS a hit it stayed wherever ranking put it — measured,
  second, behind a plain fact. It is now promoted rather than inserted.

(h) **Create-safety is rank AND tier, not rank alone.** A substring hit that merely shared a common
  word ranked first for an unrelated query and was reported `probable`. A top-ranked substring match
  is a coincidence of characters; a top-ranked semantic or keyword match is evidence. The asymmetry
  is deliberate: `unknown` leaves a duplicate for the curator, while a wrong `probable` silently
  overwrites unrelated knowledge and no later pass can tell it happened.

(i) **NOT DONE:** the `ops` op-list payload, `also_artifact` dual-write, `read_when` MATCHING at
  retrieve time (the triggers are stored and returned; matching them against node task text is
  retrieval-side work that belongs with session 37's synthesizer), the `coverage_gap` run-journal
  EVENT (the flag is in the payload; journaling it needs the ledger seam), and the async
  enrichment/backfill loop (`_enqueue_enrichment` is a best-effort hook whose target
  `knowledge.ingest.enqueue_item` does not exist yet — it degrades silently by design, and the
  backfill that covers what the queue missed is session 37's).

### Session 36 — Knowledge Synthesis: long-run engine additions (`feature-wf2-engine-longrun`, PR NOT OPENED — push blocked)

`workflows/longrun.py` (item identity, persistent seen-set, bounded sibling views, convergence
guard, lineage caps, adaptive-delay clamp, buffer-seal, run-continuity, web hygiene), the
`until_cancelled` loop mode with a real reaper, `{{siblings.*}}` / `{{previous.output}}` with the
`window`/`unseen`/`significant`/`full`/`hygiene`/`clamp` pipes, buffer-seal `wait`, and four new
ledger kinds. 11957 tests (+87), lint clean at 615 files. Driven end-to-end through the live
engine: the sibling view grew 2→4→8 across cycles, `previous.output` chained each cycle to its
predecessor, the seen-set journaled one novel item per cycle, and the run COMPLETED rather than
hanging.

**PREMISE MISMATCH (E1) — resolved in place, not escalated.** The plan states a watcher is
cancelled by "a sibling completing in a `join: any` parallel". Measured, that does not happen:
`container_outcome` checks for non-terminal children BEFORE the ANY rule, so a parallel whose
watcher is still running reads RUNNING and the run never completes. That check is deliberate and
documented (tick.py's own header: a join must not fire early on a fan-out whose other legs are
still waiting), so changing join semantics would have broken the rule it was protecting. Instead
`reap_watchers` is a separate, narrower pure rule: inside a `join: any`/`quorum` parallel, an
`until_cancelled` loop is reaped once enough of its NON-watcher siblings have succeeded. Recorded
as a DEVIATION rather than a BLOCKED because the plan's *intent* — the watcher stops when its
work is done — was buildable exactly as written; only its stated mechanism was wrong.

**Three PRE-EXISTING engine defects found by running the plan's flagship shape.** All three are
about instance-path handling below an iteration marker, all three were live on `main`, and all
three made container-bodied loops unusable — a shape five SHIPPED templates already use:

(a) **`_base_path` truncated at the last marker** instead of removing the marker, so
  `root.children[0].body@0.children[0]` resolved to the body SEQUENCE. Live effect: a `wait`
  nested in a loop body was looked up as its parent sequence, `_wake_due_nodes` read it as a
  gate, and every cycle failed with "gate timed out with no answer" — for a template containing
  no gate at all.

(b) **`_loop_parent` required the path to END at `@N`.** It always does for a leaf body and never
  for a container one, so `int("0.children[2]")` raised, `_advance_loop` returned silently, the
  loop never advanced, and the run DEADLOCKED after exactly one iteration.

(c) **Instance paths were sorted as strings,** so `body@10` sorted before `body@2`. "Oldest
  first" silently became wrong at the tenth iteration — the window would keep the wrong items
  and `previous.output` would return the wrong cycle. Ten cycles in is later than any short test
  reaches, which is why it survived.

Fixing (b) required a fourth change: a container-bodied loop calls `_advance_loop` once per LEAF,
so the counter now advances only when the whole body is terminal, derived through the scheduler's
own `derive_state` (promoted from private) rather than a second notion of completeness that could
disagree with it.

**Other deviations and findings:**

- **The default sibling view is applied at RESOLUTION time, not in `as_root`.** Filtering in
  `as_root` made `| full` inert: the opt-out could only ever see items the default had already
  dropped — a control that looks present and does nothing.
- **`siblings.<id>.output` FLATTENS iteration envelopes.** Without it `| full` returned 1 item
  out of 60 and `| unseen` returned nothing at all, because an envelope carries no item identity.
- **`| full` means full** — no filter AND no window. The first cut still windowed at 20, so the
  opt-out silently didn't.
- **`previous` absent is the first cycle, not an unresolvable reference.** Every diff-aware
  template in the plan is written `{{previous.output.summary | default('None yet')}}`; raising
  would make each fail on its own first cycle. `nodes.typo.output` still raises.
- **`unseen` with no engine seen-set RAISES.** A silently inert `unseen` is the exact failure it
  exists to prevent: the watcher keeps working and costs grow every cycle with no indication why.
- **`_enclosing_parallel` walks OUTWARD.** A watcher's synthesize stage sits at
  `…children[1].body@3.children[0]`, whose nearest `.children[N]` prefix is the body SEQUENCE —
  stopping there returned no siblings for the one node in the template that needs them.
- **An empty buffer never seals, including on the stale path.** A stale-flush of nothing would
  pay for a synthesis of no new material every hour forever — the cost the volume trigger exists
  to avoid.
- **A garbage adaptive-delay proposal falls back to the CONFIGURED delay, not the floor** — "the
  model returned nonsense" must not make the loop faster.
- **Two new validator errors**, because both failures are silent hangs or silent spends:
  `WF_UNREAPABLE_WATCHER` (no reaper and no `max_iterations` — including a parallel of nothing
  but watchers, which can never satisfy its own join) and `WF_WATCHER_NO_WAIT` (a watcher with no
  wait cycles as fast as the model answers). Plus `WF_BAD_SEAL` / `WF_SEAL_NO_FLUSH`.
- **NOT DONE:** the `transform(hygiene|unseen)` preset as a node-level shorthand (the pipes ship;
  the preset is template sugar), LLM-summarize for oversized sibling payloads (deterministic
  truncate ships as the fallback that must always work — the model path belongs with the
  synthesizer), and the run-continuity injection SITE (`roll_continuity`/`continuity_header` ship
  and are tested; wiring them onto the trigger record is recurring-run work in session 37).

### Session 37 — Knowledge Synthesis: consolidation + maintenance (`feature-wf2-knowledge-maintenance`, PR NOT OPENED — push blocked)

`knowledge/consolidation.py` (gate stack, deterministic pre-dedup, injectable-metric clustering,
lineage caps, health checks, differential refresh, phantom hubs, lint cadence), three maintenance
action providers (`knowledge-health` / `knowledge-consolidate` / `knowledge-gaps`), the three
bundled templates, and four config knobs through all four wiring points. 12059 tests (+102), lint
clean at 617 files.

**Deviations and findings — every one measured, not read:**

(a) **The plan's 0.75 cluster threshold is an EMBEDDING number, and the default metric is token
  overlap.** Measured: six human paraphrases of one fact score 0.12-0.36 pairwise on token
  Jaccard, so a 0.75 cut clustered NOTHING — a pass that ran, reported success, and consolidated
  zero items every single time. This is the same defect class as session 35's cosine-cliff-vs-RRF
  bug: a threshold is meaningless without its number space. Fixed by making the metric injectable
  (cosine over the store's embeddings when one is configured) and making the DEFAULT THRESHOLD
  FOLLOW THE METRIC — `TOKEN_CLUSTER_SIMILARITY = 0.30` (cross-topic pairs measure below 0.10)
  versus the plan's 0.75 for cosine.

(b) **The plan's `<100-char body` stub rule over-fires on exactly the content the store is for.**
  "Cold start latency measured 4.2s on the M2 after a fresh boot" is 83 characters and a complete,
  useful fact; six of those reported as six stubs trains the reader to ignore the report, which
  costs more than the stubs do. A stub is now short AND unspecific: a body containing a number, a
  path, an identifier or a version is making a claim regardless of length.

(c) **`items_fts` is keyed by ROWID, not by the item's text id.** Comparing the two marked EVERY
  item unindexed — a report claiming seven problems on a healthy seven-item store. The reindex
  path had the mirror bug: inserting keyed on the text id writes an entry no search will ever
  match, which is worse than the gap it repaired because the report then says it is fixed.

(d) **`knowledge.content_hash()` is keyword-only, and `items.item_type` is NOT NULL.** My
  hand-rolled INSERT hit both. Fixed by routing the consolidated write THROUGH
  `knowledge-persist` instead — which also gets the FTS sync, the idempotency check and the
  provenance ref right. Two writers to one table means every fix to one has to be remembered for
  the other.

(e) **The persist provider silently DROPPED a `metadata=` argument.** It forwards a named
  allowlist, so the entire lineage the consolidation pass depends on never reached the row and
  nothing errored. Added `lineage` as an explicit named key rather than opening the passthrough:
  an open dict would let any caller clobber `claims` or `logical_key`, and a caller that silently
  wiped the claim ledger would be indistinguishable from one that never wrote it.

(f) **The first `gap-healing` draft passed `min_mentions` AS a `knowledge-retrieve` query** — it
  reads plausibly in a spec and searches the store for the string "3". Phantom-hub detection is
  not a search; it is a set difference between what items REFERENCE and what items EXIST, so it
  became its own zero-token provider. It also carries EXCERPTS: a model given a bare name writes
  what it already believes about that name, which is the invention the template exists to avoid.

(g) **An empty buffer / empty store is HEALTHY, and a declined pass is a SUCCESS.** Reporting
  problems on a fresh install would make the maintenance cadence start by crying wolf, and failing
  the node when there is nothing to do would make a healthy frequent schedule look broken every
  time it ran.

(h) **An embedder returning None must not be cached as an empty vector.** A cosine against `[]`
  is either a crash or a meaningless 0.0, and 0.0 silently means "unrelated" — so that item would
  never cluster with anything, forever. Falls back to the token metric for that pair only.

(i) **The health template's node label "Findings" collided with the library's canonical
  Finding-record convention check** (a substring test over the whole spec). Renamed to "Health
  report": this node reports store health, not review findings.

**Validated live** through the dev gateway on a seeded 6-item store: all three templates appear in
the Store; `knowledge-health` ran clean (`item_count: 6`, zero false stubs, zero false unindexed);
`knowledge-gaps` found the `[[Provisioned Concurrency]]` phantom hub with 6 referrers and grounded
excerpts; `knowledge-consolidate` clustered all six paraphrases with the doctrine in its prompt and
correctly refused to write (dry run by default). The apply path, archival with back-references, and
the second-pass gate were validated against a real store in-process.

**NOT DONE:** `gap-healing`'s LLM drafting stage was NOT observed to completion live (the Bedrock
subagent call was still running after ~5 minutes and was cancelled; the `stage` mechanics it uses
are validated by earlier sessions). Also not done: routing gap drafts into the LEARNING-FLYWHEEL
`proposals.enqueue` queue — that queue's `Kind` enum is CLOSED to six kinds and none of them is a
knowledge draft, so filing there would mean either a seventh kind or mislabelling one; the template
persists a TTL'd `probe` item tagged `proposal` instead, and the enum extension belongs with the
flywheel plan that owns it. Contradiction detection at persist time (§3.2) and the typed-edge
inference pass are session 38's.

### Session 38 — Knowledge Synthesis: contradiction + retrieval polish (`feature-wf2-contradiction`, PR NOT OPENED — push blocked)

`knowledge/contradiction.py` (two-tier conflict detection, source-precedence ladder, typed-edge
vocabulary, memoization) and `knowledge/session_brief.py` (bounded project digest), the persist-time
conflict pass with edge writes, the `fenced_sources` binding filter, two read-only API routes, a
`ConflictPanel` mounted as a Knowledge view, and two config knobs. 12123 tests (+64), lint clean at
619 files, frontend typecheck + 440 tests green.

**DISCOVERY — a PRE-EXISTING split-brain in the knowledge store path, and it invalidated part of
sessions 35-37's premise.** The dashboard's `AppState` has always opened
`<home>/workspace/knowledge/knowledge.db`. Every provider built in sessions 35-38 composed
`<home>/knowledge/knowledge.db` instead. So a workflow persisted knowledge into a SECOND database
the UI could never read: both writes "succeeded", both reads "worked", and the store the user
browsed simply never contained what their workflows wrote. Found only by driving the new conflicts
route through the live gateway after a workflow had written a conflict — the route returned an empty
list while the data sat in the other file. There is now ONE `knowledge_db_path()` helper in
`store.py`, all four call sites go through it, and a test asserts no module composes the path itself
(a second copy is how this happened).

**Deviations and findings:**

(a) **The numeric conflict branch skipped the similarity gate.** Measured: "The M2 has 8 cores" and
  "The M2 has 16 gigabytes of unified memory" were reported as a numeric conflict — two unrelated
  properties of one subject. The gate now applies to numeric objects too.

(b) **The prose-negation rule was UNREACHABLE.** It sat behind the SPO subject gate, which requires
  a successful decomposition — and the statements it exists for ("does not need", where the
  predicate set has `needs`) are exactly the ones decomposition fails on. Measured: "needs a
  restart" vs "does not need a restart" returned None. Moved ahead of the gate.

(c) **Plain Jaccard is the wrong instrument for a polarity comparison.** Negating a claim ADDS
  tokens and changes inflection, so the score is systematically depressed for exactly the pair the
  rule wants to catch — measured at 0.60 against a 0.75 floor. Added `core_similarity`, which
  strips negations and auxiliaries first, and verified it still refuses two DIFFERENT negated
  claims.

(d) **Conflict details were built from the normalized object,** which strips the decimal point, so
  a "4.2 vs 9.1" conflict rendered as "4 2 seconds vs 9 1 seconds" — it reads as a formatting bug
  and hides the actual claim.

(e) **The neighbour scan searched FTS for claim text, and claims are not in the FTS index.** It
  carries title/content/tags; claims live in `file_metadata`. So the scan found nothing whenever a
  claim did not echo its item's title — and my first manual test passed only because the titles
  happened to contain the claim's words, which is the worst kind of passing test. Added a
  claim-bearing recency scan alongside the FTS half.

(f) **The edge write used the claim's `source_ref`, which is RUN provenance
  ("workflow:node:b"), not a row id.** The foreign key silently wrote nothing while the conflict
  record looked correct, so the item metadata and the graph disagreed about whether the store knew
  about a contradiction. Edges are now written after the upsert, from the recorded conflicts.

(g) **`_fts_safe`:** a claim is prose and prose contains FTS5 operators, so an unquoted
  `4.2s (measured)` is a syntax error the broad except would swallow into "no neighbours" — which
  reads exactly like "no conflicts".

(h) **`KnowledgeRelation` already existed** for ENTITY-level relations with a different shape. Mine
  is `KnowledgeItemRelation`; shadowing the existing name broke three call sites in
  `KnowledgeDetailPage`.

(i) **mypy caught `load_config()`, which does not exist** (`AppConfig.load()` does) — a runtime
  crash in the brief path that no test would have reached, since the brief degrades silently by
  design.

(j) **Deliberately NO resolve endpoint.** Conflicts are flagged at ingest and BOTH claims are always
  kept. Deciding which source to trust is a judgement about the sources, which is the owner's; a
  "resolve" route would invite the system to discard evidence, and a discarded claim is
  unrecoverable. The precedence ladder returns "" for two same-tier sources rather than
  manufacturing authority out of arrival order.

**Validated live** through the dev gateway: a workflow persisted two contradicting claims, the
conflict was detected at ingest with `prefer: left` correctly favouring the user-origin decision,
`GET /api/knowledge/conflicts` returned it, `GET /api/knowledge/items/{id}/relations` showed the
`contradicts` edge from BOTH directions with resolved titles, and a second run read
`{{brief.count}} = 2` / `{{brief.text}}` with the decision ranked first and every item fenced.

**NOT DONE:** the model-tier conflict pass is BUILT and tested (prompt, memo key, verdict parsing,
confidence ceiling) but is NOT wired to a live model call — that needs a `stage` node in a template
rather than an action provider, since an LLM call inside an action is exactly what the action/stage
split exists to prevent; the deterministic tier ships wired. Background typed-edge INFERENCE beyond
`contradicts` is likewise parse-ready but unwired. The `coverage_gap` → persist-proposal loop is
still session 39's, along with the template slate.

### Session 39 — Knowledge Synthesis: template slate + long-run validation (`feature-wf2-knowledge-slate`, PR NOT OPENED — push blocked)

Four bundled templates (`knowledge-synthesis`, `rich-ingest`, `thesis-tracker`, `publish-article`)
and `tests/test_knowledge_longrun_validation.py` covering the plan's §8 success criteria. 12186
tests (+63), lint clean at 619 files. This closes the Knowledge Synthesis plan (sessions 34-39).

**DEVIATION — 4 of the 12 slate items built, and every omission is a missing PROVIDER, not a
missing template.** The plan's §7.1 slate assumes capabilities that do not exist yet:

- `trending-repo-digest` and the dual-sink watcher variant need **`net.fetch` as an ACTION
  provider**. There is none — `ALLOWED_HOOK_PROVIDERS` has no fetch entry, and the egress
  chokepoint is a library function, not a dispatchable action. Building the template anyway would
  ship a spec that validates and then fails at run time, which is the exact failure mode
  `ALLOWED_HOOK_PROVIDERS` exists to prevent.
- `meeting-prep` needs a calendar source. The plan itself notes the event is "a template input
  until one does", which makes the template a `knowledge_retrieve` with extra steps — the
  `knowledge-synthesis` template already covers that shape.
- `market-monitor` is the `until_cancelled` flagship, and its `wait` + seen-set + sibling-window
  mechanics are exactly what session 36 validated live (2→4→8 sibling growth, `watcher_reaped`,
  journaled seen-set). Shipping a second copy of a validated shape that also needs `net.fetch`
  adds a maintenance surface without adding a mechanism.
- `quality-document`, the raw→rolling→one-pager tiering, and `living-document` are artifact-centric
  variants of the compiled-truth pattern `thesis-tracker` demonstrates; `paper-ingest` is
  `rich-ingest` with a fetch step (same missing provider).

The four that DID land are the ones whose mechanisms ship end to end: the three-node one-model-call
pattern, classifier-then-dispatch multi-lens ingest, compiled-truth tracking, and the full
draft→dual-review→gate→persist artifact lifecycle with a decision record.

**Findings:**

(a) **`rich-ingest` persists each lens under its OWN kind.** A single merged persist would collapse
  four typed vocabularies into one item and lose exactly the typing the separate lenses exist to
  produce. Asserted structurally (`{decision, reference, fact, report}`).

(b) **Every lens carries `allow_failure`.** One lens tripping must not sink the pass — four of five
  vocabularies extracted is a good outcome, and losing all of them because the facts prompt failed
  is not.

(c) **The KNOW-R18 memory boundary is asserted, not trusted.** A test reads the template's action
  providers and requires exactly `{knowledge-persist, create-task}` — episodic and preference
  capture is the MEMORY subsystem's job, and a template that wrote both would put user-modeling in
  an ingest path nobody audits.

(d) **A test asserts every slate template FENCES what it retrieves.** It checks the raw
  `{{nodes.<id>.output.items}}` form is absent AND that `| fenced_sources` is present for each
  retrieve node — a template that interpolated raw knowledge into a prompt would bypass the
  platform's fencing doctrine, and reviewing that by eye does not scale to twelve templates.

(e) **The long-run assertions were mutation-tested.** Measured that removing the window makes the
  sibling view grow to 840 items over 168 cycles (the test caps at 20), and that removing the
  seen-set marking produces 95 duplicate re-processings over 20 cycles. Both assertions fail when
  their mechanism is removed, which is the only thing that makes them worth having.

(f) **Idempotency is tested at FIFTY calls, not two.** A duplicate-on-Nth bug (a counter in the
  key, a timestamp in the hash) survives a two-call test. Mention counts are held stable across 20
  retries for the same reason: a claim that looks corroborated by fifty sources when one source
  retried fifty times is the most dangerous possible artifact, because confidence is computed
  from it.

**Validated live:** all four templates appear in the Store; `knowledge-synthesis`'s zero-token
retrieve correctly reported a coverage gap on an empty store; and the `publish-article` tail was
driven against the REAL dev store — a `reference` item stored, the approval recorded as a separate
`kind: decision` citing it, a hybrid retrieve finding both, and `fenced_sources` rendering them as
two numbered fenced blocks.

**NOT DONE:** `knowledge-synthesis`'s SYNTHESIS STAGE was not observed to completion live — the
Bedrock subagent call was still running after ~7 minutes and was cancelled, the same environmental
latency session 37 hit with `gap-healing`. A minimal `infer` probe confirmed the model IS reachable
(it failed on an output-contract error from my own deliberately loose probe prompt, which proves the
call path works), so this is latency rather than a defect — but I have not seen these specific
templates' model nodes finish. Criterion #10 (heuristic-extraction fallback with no provider bound)
is also unverified: it needs a provider-less environment, and this dev home has Bedrock bound.

### Session 40 — Universal Planning: matching + classification (`feature-wf2-planning-match`, PR NOT OPENED — push blocked)

`workflows/intent.py` (the no-LLM 4-dimension classifier + rigor routing), `workflows/matcher.py`
(the T1-T5 tiered matcher), typed match metadata on `DefMetadata`, all 18 bundled templates
annotated, `tests/fixtures/planner_routing.json` as the CI deployment gate, and both wired into
`workflow_plan`. 12251 tests (+65), lint clean at 621 files.

**The routing accuracy story is the important part of this session.** The plan sets a >=85% bar on a
fixture suite, and a keyword classifier measured against the examples it was tuned on reports its own
training set back. So the fixtures were written FIRST from how a user actually types — lowercase,
abbreviated, missing the keywords the classifier hopes for — and then measured:

**68% on first contact.** Six misses, one root cause: a request with NO signal at all collapsed to
TRIVIAL. "Add a retry to the ingest queue" fires no keyword, and reading that silence as simplicity
sent ordinary work down the cheapest path. Absence of evidence is not evidence of simplicity — an
unremarkable request is ORDINARY WORK, which is what STANDARD means. Four more rounds of measure-fix
reached 100% on the fixtures, 4/4 on shape detection, and 13/13 against the REAL bundled library.

**Every fix was a measured defect, not a tuning pass:**

(a) **TRIVIAL was unreachable.** It required `uncertainty == LOW`, but uncertainty defaults to MEDIUM
  with no signal — and "rename the variable" HAS no uncertainty signal, which is exactly what makes
  it trivial. Requiring an explicit "exactly" made the branch dead.
(b) **Time pressure was checked AFTER stakes,** so "production is on fire, fix it now" routed DEEP —
  a deep grill is the right answer to the wrong question when the user needs a plan in a minute.
(c) **`quick` was a time-pressure word.** "Draft a quick note" is a SIZE, not a deadline, and it made
  every casual request read as urgent — which then vetoed the deep path for real work.
(d) **`delete`/`drop` were in BOTH the stakes and irreversible lists,** and HIGH wins a tie in
  `_dimension` — so "delete the scratch file" read as high-stakes and routed DEEP, with the `scratch`
  de-escalator unreachable for exactly the verbs that most need it. Irreversibility is now its own
  signal: stakes decide whether to GATE, irreversibility decides whether to THINK first.
(e) **A signal-less MEDIUM blocked the cheap paths** while ALSO being read as not-complex by
  `_is_complex` — the same ambiguity in two directions. MEDIUM now means two distinguishable things:
  "nothing fired" (ordinary work) versus "the length nudge moved it here" (scope).
(f) **High stakes short-circuited to FAST,** so "write the changelog entry for this release" got LESS
  planning than "add a retry". High stakes must never buy a request fewer steps than an unremarkable
  one.
(g) **Breadth was not a signal.** "Rename x to y" is trivial; "rename x to y everywhere it's used" is
  mechanical but scoped — same verb, different scope, and the simplicity word won.
(h) **A domain NOUN counted as a unit of scale.** "Refactor the ingestion pipeline" escalated to DEEP
  because `refactor` + `pipeline` read as two signals. Only action-scale verbs and breadth count now.
(i) **The uncertainty list had "why is"/"why does" but not the bare "why" or "look into",** so "look
  into why the sync job is slow" — a request entirely ABOUT not knowing — read as certain.

**Matcher findings:**

(j) **`DefMetadata` is a CLOSED dataclass and `from_dict` drops unnamed keys.** Annotating all 18
  bundled templates with `keywords[]` left the matcher reading 0/18 — it ran entirely on description
  overlap while still reporting matches at 0.02-0.22 confidence. The matchable surface is now TYPED
  FIELDS on `DefMetadata`, not smuggled through an untyped dict.
(k) **`TemplateProfile.from_def` called `.get()` on that dataclass** and silently got nothing. It now
  reads `to_dict()` when present.
(l) **Confidence saturated at the 0.95 ceiling for every clean match.** The gap component divided by
  the leader's OWN score, so an uncontested leader always scored a full 0.5, plus a free 0.2 floor.
  A number that never varies carries no information, and a review that always reads 95% trains the
  user to ignore it. Confidence is now earned from evidence and spreads 0.04-0.90.
(m) **The T3 shape penalty (0.6x) could not unseat a strong keyword hit,** so a monitor-shaped intent
  still matched a review template — the classifier said monitor and the router ignored it. Shape is
  now a HARD exclusion when the library serves that shape, and a soft preference when it does not.
(n) **Hard-excluding every candidate crashed** on `candidates[0]`. An all-excluded shape is a
  legitimate no-match.
(o) **Literal-phrase-only keyword matching was too brittle:** `"why did it fail"` missed "why did
  that run fail" — the same question with one word changed. All CONTENT words must be present now,
  in any order, which still refuses the "cold drink / race start" coincidence.
(p) **The matcher and the plan-tool loader read DIFFERENT SOURCES.** The matcher reads bundled
  templates from disk; `_plan_from_template` resolves through the service's registered providers.
  Outside a booted gateway they disagreed, so the router proposed a real template name the loader
  could not find and turned a working scaffold into `WF_PLAN_TEMPLATE_NOT_FOUND`. Guarded with
  `_def_resolvable` — a router that breaks the fallback is worse than one that never matched.
(q) **An INVALID `rigor` keeps the documented "standard" fallback** rather than deferring to the
  classifier: the caller asked for something and would otherwise get something else with no
  indication. Only an ABSENT rigor defers.

**PREMISE MISMATCH (E1) — the session's "delete dead chat plan-mode" task is not actionable as
written.** It says to delete "the whole plan-mode half incl. the dead chat_title wrappers" from
`context_management.py`. That split ALREADY HAPPENED: the plan-mode half lives in `plan_memory.py`,
whose own docstring says it becomes deletable once UNIVERSAL-PLANNING replaces the format and the
Flywheel absorbs the journal. It is not dead — `history.py` (2 call sites) and
`dashboard/chat_title.py` import it live, and `tests/test_plan_memory.py` covers it. Deleting it now
would break three live surfaces to front-run a replacement that does not exist until session 41's
grounded generation. Recorded as a DEVIATION and left for the session that actually replaces the
format.

**Validated end to end** through the real runtime (bundled provider registered): "audit the auth
module for security problems" → `audit-sweep` at 0.79 via T3, "why did that run fail" →
`diagnose-run` at 0.59 via T1, "write up what we know about cold start latency" →
`knowledge-synthesis` at 0.80 — each returning the template's expanded tree and its steering
examples. A monitor-shaped intent correctly reports "no template serves a monitor-shaped intent"
because session 39 deferred `market-monitor` (it needs a `net.fetch` action provider that does not
exist), and falls back to a scaffold rather than forcing a wrong shape.

**NOT DONE:** T4's embedding tie-break and T5's summarize-then-rematch are BUILT and unit-tested
(injection points, failure degradation, the re-entry contract that forbids a model-emitted template
id) but neither is wired to a live embedder or model in `workflow_plan` — T4 needs the embedder
registry threaded through the plan path, and T5 needs a `one_shot_completion` call, both of which
belong with session 41's grounding work where the model plumbing already lands. Hybrid COMPOSITION
returns the names to compose but does not yet build the subworkflow spec. `presets` and
`lighter_path` are surfaced in the match result and not yet acted on by the planner.

---

## POST-QUEUE WORK (criteria + acceptance bars the 91 rows did not cover)

The 91 rows are `✅ DONE`. Four sessions since then closed **acceptance criteria of plans already in
the queue** — declared work, not new scope (see the ruling in the exhausted record below).

| Session | What was unmet | Plan / bar | PR |
|---|---|---|---|
| S78 | One Proposal Inbox showing six kinds; the model cannot accept its own proposals | LEARNING-FLYWHEEL crit 1 | ✅ DONE (#234) |
| S79 | The adversarial test must cover the REFINER path | LEARNING-FLYWHEEL crit 4 | ✅ DONE (#235) |
| S80 | The named ambient blocks must fit ONE slot-allocated budget | LEARNING-FLYWHEEL crit 5 | ✅ DONE (#236) |
| S81 | The Automations **Week tab** (the endpoint's other half) | AUTOMATION-SUBSTRATE AUTO-A3 | ✅ DONE (#240) |
| S82 | The 7 **dormant lifecycle events** never fired (configurable + dead) | AUTOMATION-SUBSTRATE crit 5, clause 2 | ✅ DONE (#241) |
| S83 | The `file` kind's **watch runtime** — declared, and nothing watched a filesystem | AUTOMATION-SUBSTRATE crit 2 | ✅ DONE (#242, closed by #253 + #255) — status corrected 2026-08-03: the row said PARTIAL, but the plan's own S93 record says "S83 now FULLY closed (create + fire)". Re-verified by driving it: `automation_create` routes "when a file in ~/notes changes" to `kind=file` with `paths=['~/notes/**']`, boot wires `_file_watch_task`, and `_fire_file_trigger` delegates to the shared `_fire_store_trigger`. Stale bookkeeping, not open work |
| S84 | The cross-kind **run-history feed** was schedule-only; `FireRecord` was never constructed | AUTOMATION-SUBSTRATE crit 4 | ✅ DONE (#243) |
| S85 | **`statusUrl` did not exist anywhere** — the notification→journal dead end (R18) | AUTOMATION-SUBSTRATE crit 10 | ✅ DONE (#244) |
| S86 | **§3's fire path never existed** — 15 controls, zero live callers, no `service.py` | AUTOMATION-SUBSTRATE §3 (the order) | ✅ DONE (#245) |
| S87 | **`triggers.json`** — the one store + the cron migration; found the `interval` data-loss bug | AUTOMATION-SUBSTRATE §1 + §6 step 2 | ✅ DONE (#246) |
| S88 | **`TriggerService.tick`** — the loop's decisions; found the ISO-vs-epoch type seam | AUTOMATION-SUBSTRATE §3 + §3.1 | ✅ DONE (#247) |
| S89 | **WakeupDispatcher** — inbox + wakeup; found `enqueue` drops idle-session payloads | AUTOMATION-SUBSTRATE §3.2 | ✅ DONE (#248) |
| S90 | **The executor** — drain/run/classify; the substrate now runs END TO END | AUTOMATION-SUBSTRATE §3 + §1.3 | ✅ DONE (#249) |
| S91 | **`automation verify-migration`** — §7 step 2's named cutover prerequisite; found `lossless: true` beside two silently-paused real automations | AUTOMATION-SUBSTRATE §7 step 2 + §8 | ✅ DONE (#250) |
| S92 | **`automation_*` chat-tool namespace** — closes criterion 2 (a file-watch automation creatable in one message); S83's recorded blocker is gone since S87 shipped the store | AUTOMATION-SUBSTRATE §4 + crit 2 (S83 unblock) | ✅ DONE (#253) |
| S93 | **file-watch poll runtime, wired into gateway boot** — makes S92's file automations actually FIRE; disjoint from `ScheduleService` so no double-fire (the additive cutover, not the deferred clock switch-over) | AUTOMATION-SUBSTRATE §3 + crit 2 (S83 runtime) | ✅ DONE (#255) |
| S94 | **`/api/triggers` surfaces store-only kinds** (file/web_watch/idle/…) via a `store` namespace — closes the present-and-inert gap S92/S93 opened (created + fired but unlistable on the Automations page). List + toggle + run + delete route through S92's `tools.py`; legacy backends untouched | AUTOMATION-SUBSTRATE §6 (additive slice) | ✅ DONE (#256) |
| S95 | **Automations page shows store triggers** — the FE half of S94: an "Automations" filter tab lists file/web_watch/idle/… triggers, with a `StoreTriggerDetail` inspector for pause/run/dry-run/delete. Closes "implementation owns product too" for S92-S94 — the automation is now visible AND manageable in the UI, not just in chat + the API | AUTOMATION-SUBSTRATE §5 (FE) + crit 2 | ✅ DONE (#257) |

| S96 | **Arm the clock** — a spec→next-fire primitive for all four `CLOCK_KINDS`, wired into boot + the tick. Clears the REAL clock-cutover blocker: a migrated cron was permanently inert (`next_fire_at` empty ⇒ never due), and fixing that exposed a fire storm (a fired cron kept its ELAPSED slot and re-fired it every tick). One-shots now retire via `delete_after_run` instead of holding a past timestamp | AUTOMATION-SUBSTRATE §3.1 (clock cutover step 1) | ✅ DONE (#263) |
| S97 | **The claim store** — `overlap` was decorative (the tick never supplied `existing_claim`, nothing persisted a grant, the executor never released). All three closed; `is_running`/`running_since` now answer from a cross-process sidecar, which is what the API facade needs to re-point off `ScheduleService` | AUTOMATION-SUBSTRATE §3.1 (clock cutover step 2) | ✅ DONE (#264) |
| S98 | **Boot migration + the schedule projection** — `migrate_from_crons` was called by NOTHING outside tests, so `triggers.json` was EMPTY on a real machine (blocking the §6 API re-point and leaving the tick nothing to fire). Now runs at boot, arms the imports, and verifies. Found the migration was NOT idempotent for runtime state: every boot blanked the arm and the run history | AUTOMATION-SUBSTRATE §7 step 2 + §6 | ✅ DONE (#273) |
| S99 | **The schedule re-point** — `/api/triggers`' schedule LIST now reads the unified store through S98's projection, with a legacy fallback only while a home's migration has not run. Verified the store lists the same job ids as the legacy service first, so nothing vanishes from the page | AUTOMATION-SUBSTRATE §6 (the re-point) | ✅ DONE (#275) |
| S100 | **THE CLOCK CUTOVER** — the unified tick loop is now the SOLE clock engine (`triggers/loop.py` + `_clock_loop`), and the legacy timer is no longer armed (`load_without_timer`). Measured first: a store-only trigger had NO firing path, and running both loops would double-fire `j-at`+`j-cron` on the owner's real store | AUTOMATION-SUBSTRATE §3 + §6 (the cutover) | ✅ DONE (#282) |
| S101 | **The schedule WRITE re-point** — create/update/toggle/delete now persist to the unified store through `tools.py`. Found that `tools.create` never ARMED a clock trigger, so every cron created via chat (since S92) or the API would never fire; and a cadence edit silently dropped `timezone`/`skip_dates` | AUTOMATION-SUBSTRATE §6 (writes) | ✅ DONE (#286) |
| S102 | **The manual-run re-point** — `POST /api/triggers/{id}/run` fires a store-backed clock trigger through the same `_run_store` path every other store kind uses, and the already-running 409 now reads S97's cross-process CLAIM store instead of a process-local dict that was simply wrong in an API worker | AUTOMATION-SUBSTRATE §6 (run) | ✅ DONE (#289) |
| S103 | **Week grid + doctor re-point — and the week grid now PLOTS A CRON.** The old handler omitted every non-interval trigger (its own comment admitted it), so the forecast showed only half a user's automations; the doctor read `job.workflow`/`watch_glob`, fields a `ScheduleJob` does not have, so its orphan + broad-glob checks scanned blanks for every schedule trigger | AUTOMATION-SUBSTRATE §6 + AUTO-A3 | ✅ DONE (#293) |
| S104 | **Chat-injection + history re-point** — the last two facade `list_jobs` reads. The injection needs only id/name/agent_id (measured), and the last RESULT comes from `ScheduleRunStore` because `LEGACY_FIELD_MAP` drops `last_result` on purpose. The history name map now covers EVERY kind, so file/web_watch run rows stop rendering as deleted automations | AUTOMATION-SUBSTRATE §6 | ✅ DONE (#297) |
| S105 | **Run-record re-point** — the facade holds `ScheduleRunStore` DIRECTLY; all four service run methods were one-line passthroughs, so the coupling was pure indirection. Two history handlers no longer touch `state` at all. Hit a real same-name SHADOWING bug (`_run_store` already existed) | AUTOMATION-SUBSTRATE §6 (run records) | ✅ DONE (#305) |
| S106 | **The reaper cutover** — the cron reaper had been INERT since S100 (its sweep read a dict only the retired timer wrote; driven, 8 sweeps reaped nothing), so a 30-min deadline was enforcing nothing. Replaced by a claim-driven `triggers/reaper.py`. Also found every store-backed bash fire silently capped at 30s where the legacy path allowed 300s + `zt_timeout` | AUTOMATION-SUBSTRATE §3.1 + §8 | ✅ DONE (#313) |
| S107 | **Status + live refresh re-point** — three surfaces read a service the cutover emptied: `/api/status` reported `running:false, jobs:0` on a healthy machine with automations, the dashboard's "triggers" metric read 0, and a SCHEDULED fire pushed no live refresh so open views stayed stale until navigation. One shared `trigger_counts()`; `status`/`set_refresh_callback` retired | AUTOMATION-SUBSTRATE §6 | ✅ DONE (#318) |
| S108 | **No legacy writers left** — the CLI, app-cron reconcile, and digest reconcile all wrote `crons.json`, which the clock engine never reads: a cron created by `cron add` DID NOT FIRE until the next restart, an app's cron was one restart behind its manifest, and the digest never ran. Both reconcilers also ran BEFORE the migration. Found a cadence edit that never re-armed and a blank message column | AUTOMATION-SUBSTRATE §6 (writers) | ✅ DONE (#325) |
| S109 | **The `schedule_*` MCP aliases retire** — 9 aliases, the 716-line module, a bundled app, 4 validation schemas, the `defaults.json` grant + 7 more registration points. Found `schedule_remove_all` was enforcing a real ACCESS CONTROL nothing else had (carried over as scoped `automation_delete_all`), and that the R1 interval floor was declared but read by NO code — a 5-second LLM poll persisted with `ok:true`. The shipped PROMPTS still named the retired tools | AUTOMATION-SUBSTRATE §4 | ✅ DONE (#328) |
| S110 | **The facade's legacy CRUD fallbacks retire** — `state.crons.*` 15 → 0; driven with `state.crons = object()` (no methods) and every surface still answers. Found the one real gap first: a `crons.json` row with an empty/unknown `schedule.kind` LOADS in the legacy service but the migration DROPPED it, so deleting the fallbacks would have made that job vanish silently. Now imported disabled + visibly broken | AUTOMATION-SUBSTRATE §6 | ✅ DONE (#331) |
| S111 | **The last read-only callers re-point** — four surfaces still described the legacy file: a user with live automations read as NOT engaged with automation, a cron's `session='origin'` reply went NOWHERE, suggestions lost their scheduled context, and the whole investigate snapshot came back blank. Driving my own re-point found 3 more (legacy-only attributes, the `health_status` rename, a nested provider key) | AUTOMATION-SUBSTRATE §6 | ✅ DONE (#347) |
| S112 | **`ScheduleService` DELETED** — 779 lines of class + a 450-line dead dispatcher + 217 lines of orphaned methods, and the `cron_svc` thread through boot/shutdown/DashboardState. Re-scoped the E5 "blocker": the SDK gained a trigger surface and the slack app's `/cron` re-pointed (its commands were ALREADY broken — the service read a file nothing writes). Found `skip_dates` enforced NOWHERE in the new engine: a trigger armed to fire on a day the user struck out | AUTOMATION-SUBSTRATE §6 | ✅ DONE (#348) |
| S113 | **Snapshot carries the automations** (§7 step 9 — declared work, unblocked). Driven first: a snapshot of a home with 2 automations + an event trigger + run history captured **`config.json` ALONE**, so `personalclaw snapshot` silently lost every automation — while backing up the legacy `crons.json` nothing writes. Both export paths fixed; the round trip then found that import IGNORED the run ledger export carried, and that `.history.lock` travelled | AUTOMATION-SUBSTRATE §7 step 9 | ✅ DONE (#353) |
| S114 | **The inventory ⇄ snapshot drift guard** — asked whether the inventory is the authority and cross-checked it: **25 of 57 declared state entries travelled in NEITHER snapshot path** (knowledge store, sessions, subagents, every `active_*` binding, `mcp.json`, the audit log). The automation domain is now enforced COMPLETE; the 22 that belong to DURABILITY-AND-SYNC are pinned in a shrink-only list. Verified the guard bites both ways | AUTOMATION-SUBSTRATE §7 step 9 | ✅ DONE (#355) |
| S115 | **`{{secret:KEY}}` in a trigger action** (§7 item 6 / decision 11). Workflows have carried the form since R14 and three surfaces tell authors to use it — but a trigger action passed the LITERAL placeholder to the shell, so the only way to authenticate was pasting the credential into `triggers.json` (snapshotted, echoed into run records, rendered in the UI). Resolved at dispatch; an unresolved key refuses instead of substituting `""`. Second gap found: the store accepted a credential-shaped literal with zero issues while the workflow lint flags it | AUTOMATION-SUBSTRATE §7 item 6 | ✅ DONE (#357) |
| S116 | **Decision 7's frozen-capability fence, actually enforced** (§1.4 / R3). `FireContext.requested` defaulted to `{}` and **nothing in production populated it** — the only real construction (`service.tick`) omitted the field, so `if ctx.requested:` was always false and the fence had never run on a single real fire, while passing its own unit tests (which supply `requested` by hand). Exactly S97's `existing_claim` shape, in the gate directly below it. Second finding: wiring it ALONE would have refused every automation in existence — no writer set `capabilities` and each creates a write-capable action — so it lands with decision 7's read-only default, a save-time freeze on all four writers, and an idempotent boot backfill | AUTOMATION-SUBSTRATE §1.4 / R3 | ✅ DONE (#366) |
| S117 | **The global kill switch, on the unified trigger path** (decision 7 — declared REMAINING by S115/S116). `personalclaw incident on` did **NOT stop a clock trigger**: the CLI calls it "suspend all unattended work", the flag is SEL-audited, and hooks / subagent spawns / the legacy `event_triggers` path all honour it — but the unified engine, the sole path firing clock triggers since S100, never read it. Driven: switch thrown, `tick()` still returned `fires: ['clock:nightly']`. Second defect found while wiring it: `manual_gate_plan` PRINTED "gates enforced: incident, screen, budget, claim, yield, capability" and enforced none of them | AUTOMATION-SUBSTRATE decision 7 | ✅ DONE (#371) |
| S118 | **PathGuard — the `paths` capability compared as PATHS, not strings** (decision 7 — the last enforcement-chain item). `paths` has been a fail-closed `CAPABILITY_KEYS` member since S69 and is rendered as a fence in the UI, but `capability_allows` compared it with `_matches_entry` — prefix matching built for tool names. Measured against the real function: with `paths: ["/Users/me/notes/*"]` it **ALLOWED** `/Users/me/notes/../../.ssh/id_rsa`. A trigger fenced to a notes directory could read an SSH key and the ledger recorded the fire as permitted. Now canonicalized containment (`realpath` + `commonpath`, so traversals, symlink escapes and prefix siblings all refuse), plus `bypass_immune` for sensitive paths and a doctor finding for fences that bound nothing | AUTOMATION-SUBSTRATE decision 7 | ✅ DONE (#384) |
| S119 | **The webhook `token_ref` lint** (decision 12 — the token-at-rest half). Decision 12 says webhook bearer tokens are "SHA-256-hashed at rest" and R14 says "never verbatim in triggers.json". Driven: a `token_ref` of `sk-LITERAL-SECRET-abc123` was written **straight to disk** with `ok: True` and ZERO warnings. S115's inline-credential lint would have caught that string, but it scans the `workflow` only — so the one field on the one kind whose entire purpose is authentication was the field with no credential lint. Flagged as a warning (the row still loads) + a doctor finding that says ROTATE, because a lint cannot un-leak a token already on disk | AUTOMATION-SUBSTRATE decision 12 | ✅ DONE (#391) |
| S120 | **The provider-registration invariant** (§7 item 6 / R3 am.5 — "a test asserting no execution without a policy check"). Measured all five `get_action_provider` call sites: the four that EXECUTE are each policy-checked (three by `incident_active`, the manual one by `manual_refusal`) and the fifth reads catalog metadata only. So the invariant HOLDS — this pins it, including a staleness guard that greps the tree so a FIFTH execution site cannot be added unchecked (verified by deleting a site from the list and watching it fail). Deliberately did NOT add the per-provider `chokepoint` ATTRIBUTE the plan also describes: an attribute nothing reads is the inert-control defect, so a test pins its absence as a decision | AUTOMATION-SUBSTRATE §7 item 6 | ✅ DONE (#394) |
| S121 | **The `web_watch` RUNTIME** (§7 item 8 — new kinds wave 1). `web_watch` was a fully declared kind with NO firing path: in `KINDS`, accepted by `SPEC_KEYS`, routed by `nl_kind` for any URL, persisted, listed by `/api/triggers` (S94) and rendered on the Automations page (S95) — and **nothing polled it**. Driven: `T.create(... "watch https://…")` → `ok: True`, then `tick()` considered nothing (no `next_fire_at`) and the file poller only reads `file`. Same shape as S93's gap, one kind over. Ships `triggers/web_poll.py` + a boot loop: item-keyed novelty (the seen-set IS the storm guard — a page whose timestamp changes every fetch fires 0 times), a seeding pass that never fires, an enforced 5-min rate floor + daily request budget, and fetching ONLY through the `net.fetch` egress chokepoint | AUTOMATION-SUBSTRATE §7 item 8 | ✅ DONE (#398) |
| S122 | **The `run_completed` CHAIN runtime** (§7 item 8) — "when X finishes, run Y". Third declared-but-unpolled kind in a row: creatable, persisted, listed, rendered, and reached by NOTHING (driven — the tick considered only the clock source; neither poller saw it). Ships `triggers/chain.py` chained from `_fire_store_trigger`, the ONE point every store-backed run completes, so a chain inherits the same gates (kill switch, capability fence) instead of being a second place to forget them. Two controls: a depth cap AND cycle detection on the path, because reporting a loop as "too deep" sends the user to raise a limit that was never the problem. Three instances earned a **completeness test**: every kind in `KINDS` now needs a runtime or a stated reason — it caught a stale table entry on its first run | AUTOMATION-SUBSTRATE §7 item 8 | ✅ DONE (#400) |
| S123 | **The `webhook` fire endpoint** (`POST /api/triggers/{id}/fire`, decision 12's verification half) | AUTOMATION-SUBSTRATE §7 item 8 + decision 12 | 🛑 **BLOCKED (E4 — security-control ambiguity, owner decision).** The plan's kind table names the endpoint, and S119 left token VERIFICATION waiting on it. But `docs/security/threat-model.md` §3 assigns the inbound surface explicitly elsewhere: *"Inbound MCP and external remote access (fail-closed inbound, fencing at ingestion) are **owned by MCP-READONLY-INBOUND and EXTERNAL-ACCESS** — not yet landed; see the ASI07 row"*, and ASI07 is `in progress (plans 41, 24)`. The workspace brief's hard rules also sequence **MCP-Read-Only-Inbound before External-Access**, i.e. inbound surfaces are ordered deliberately. Building an externally-reachable POST that FIRES automations would create the exact surface those plans own, and choosing its auth model (per-trigger token vs the existing `internal_paths` secret vs the scoped owner/collaborator/viewer tokens decision 12 describes) plus its bind/exposure posture is an owner call about this machine's attack surface — not something to infer. **Not** a doctrine conflict and **not** a missing prerequisite I could re-scope: the at-rest half shipped in #391, and the remainder is one decision away. |
| S124 | **The `view` kind — pull-on-view refresh** (R10 / §7 item 8). FOURTH declared kind found with no runtime: `surface_binding` was referenced by **exactly one line in the tree** — its own declaration in `SPEC_KEYS`. Deliberately NOT a poll: §3's R10 "sidesteps the 1440-run-dirs critique by never firing unviewed", so the runtime is a function a RENDER calls, and a test asserts the gateway never polls it. TTL serves cache inside the window and refreshes past it; `persist=False` lets a freshness column ASK without consuming the window (otherwise reporting staleness would refresh the tile by asking) | AUTOMATION-SUBSTRATE §7 item 8 + R10 | ✅ DONE (#403) |
| S125 | **Fencing strips chat-template ROLE TOKENS** (§7/R4 rule b). The rule is explicit — "fencing strips chat-template special/role tokens so untrusted text can't forge role boundaries — essential with local model providers" — and it was unimplemented. Measured against the real `fence_untrusted`: ChatML's `<|im_start|>`, Llama-3's `<|start_header_id|>`, Llama-2's `[/INST]` + `<<SYS>>`, Mistral's `</s>` and the bare `<|endoftext|>` **all passed straight through**. The fence defended its OWN marker (fence-break) and nothing else, so a webhook body or watched file could forge a turn boundary one layer BELOW the XML fence's argument. Tokens are now BROKEN, not deleted (a summarizer must not silently lose a span), with no zero-width chars (the memory-write scanner flags those) and zero false positives on prose like `a/b` or `</div>` | AUTOMATION-SUBSTRATE §7 item 8 / R4 rule b | ✅ DONE (#407) |
| S126 | **The payload trust boundary — §3's declared "fence payload" step, which did not exist** (§7/R4). `GATE_ORDER` has no fence entry and `_fire_store_trigger` never calls `fence_untrusted`, so payload text was substituted STRAIGHT into a provider template. Driven end to end: a hostile `web_watch` item title reached an `invoke-agent` `task_template` (an agent task), a `send-message` `text_template` and a notification title **with forged role boundaries intact**. S125 could not cover it — that hardened `fence_untrusted`, and this path never calls it: the guard was one layer away from the sink, the same shape S119 found for `token_ref`. Fixed at the ONE renderer all four native providers share, with an **allowlist** of structural keys so a future kind's new payload key is untrusted by DEFAULT | AUTOMATION-SUBSTRATE §7 item 8 / R4 | ✅ DONE (#413) |
| S127 | **Provenance attributes on the fence tag** (§7/R4 rule c). Measured absent: the signature was `(text, *, source="")` and the tag rendered `<untrusted_content source=webhook>`. Now carries `source_type` / `source_id` / `transformation_path` — three claims, because "a web page said this" and "THIS page said it, and we truncated it on the way" are different, and only the second lets an audit tell whether the text the model acted on is the text that ARRIVED. Values are attribute-escaped: a crafted `source_id` containing `>` would otherwise close the tag early and reintroduce the fence-break through the LABEL. Backward-compatible byte-for-byte for the 13 `source=`-only call sites, and the `learning/hygiene.py` tag parser is asserted to still match | AUTOMATION-SUBSTRATE §7 item 8 / R4 rule c | ✅ DONE (#418) |
| S128 | **Rule (d) audited — it HOLDS — and the ReDoS the audit exposed** (§7/R4 rule d). Verified rather than assumed: the pattern always comes from `trigger.content_re`/`key_glob`, a value shaped like `.*` matches nothing extra, and `render_template` does not re-expand a substituted value, so a payload carrying `$SECRET_KEY` cannot reach another key. **No fix was needed and none was invented** — this ships the guard. 🔴 What the audit DID find: `matches` runs on the memory-WRITE path with an uncapped value, so an author regex of `(a+)+$` cost 0.66s at 24 chars → 10.2s at 28 → **40.7s at 30**. A length cap does NOT fix exponential backtracking (said so in the constant's own docstring rather than pretending); catastrophic shapes are now detected where they are AUTHORED, warned not refused, on both the create and update handlers | AUTOMATION-SUBSTRATE §7 item 8 / R4 rule d | ✅ DONE (#425) |
| S129 | **Rule (e) audited + a payload→env PATH HIJACK the audit found** (§7/R4 rule e). Rule (e)'s two clauses are inapplicable by construction today and that is stated, not padded: a trigger payload does NOT become structured workflow input (`run-workflow` reads `inputs` from the action config, never `ctx.payload` — driven), and no workflow/trigger module mints a bus event, so the forged-handoff attack has no path. 🔴 What the audit DID find: `bash_provider._payload_env` merges AFTER `os.environ`, so a payload KEY shadows the real var. Driven: payload `{"PATH": "<dir with a fake date>"}` + command `date` printed **HIJACKED**. Passing the payload as env stops a VALUE becoming code; nothing stopped a KEY changing which code runs. Latent (all shipped payload keys are literals — asserted) which is exactly when it is cheapest to close | AUTOMATION-SUBSTRATE §7 item 8 / R4 rule e | ✅ DONE (#436) |
| S130 | **The per-gate fail-open/closed classifier described a DIFFERENT VOCABULARY than the engine walks** (§1.4 decision 1 / R3 am.). Measured: `set(firepath.GATE_ORDER) & FAIL_OPEN_GATES` was **EMPTY**. The set held per-trigger CAP KEYS a person edits (`cost_cap`, `duty_gate`) while the fire path walks GATE names (`screen`, `duty`, `budget`, `incident`), so **every gate the engine actually runs read "closed"** — including `duty`, which §1.4 and `calendar.evaluate_duty` both require to fail OPEN and which correctly DOES. The gates were right; the classifier disagreed with the code it was written to describe, and nothing outside tests read it. Both spellings now resolve, a `FAIL_CLOSED_GATES` set makes the security fences explicit, and the tests assert the classification against REAL gate behaviour (reverting the fix turns 4 red) | AUTOMATION-SUBSTRATE §1.4 decision 1 | ✅ DONE (#442) |

**🔴 THE CLOCK CUTOVER'S REAL BLOCKER WAS NOT DOUBLE-FIRE — IT WAS THAT THE TICK COULD NOT ARM A
CLOCK.** Measured on a real migrated store: `boot()` reported `rearmed: []`, `next_fire_at` stayed
empty, and `due_ids` returned `[]` forever — a migrated cron reported lossless-and-enabled and could
never fire. `recompute_from_completion` handled intervals only; `boot_recovery` needs an existing
fire; `next_after_completion` returned 0.0 for every non-interval kind on the premise that a
"recurrence engine" owned them, and no such engine existed. S96 is that engine. **The cutover's
remaining steps (retire `ScheduleService`, re-point the schedule/event API backends, retire the
`schedule_*` aliases) are now unblocked and sequenced after it** — each a clean break, no dual paths.

**Criterion 2 is now closed create → fire → SEE → manage.** S92 create (chat), S93 fire (poll loop),
S94 API surface, S95 the Automations page. A user can create "when a file in ~/notes changes…" in
chat, watch it fire, and find/pause/run/delete it on the Triggers page under the Automations tab —
the full "implementation owns product too" loop. Read-only inspector by design: these are authored
in chat, so the page owns management (pause/run/delete), not a second create form.

**§6 partial — the ADDITIVE half, not the class-B re-point.** The full §6 ("re-point `/api/triggers`'
three backends at one store") is the deferred class-B switch-over. But S92/S93 opened a real
present-and-inert gap: a chat-created file/web_watch/idle automation was created, fired (S93 for
`file`), and **invisible** on the Automations page — `GET /api/triggers` read only the three legacy
backends. S94 adds a `store` namespace that lists the six store-only kinds and routes
toggle/run/delete through S92's `tools.py`, all read + safe-mutation with the legacy paths untouched
(no migration, no double-write). The schedule/event backend re-point and `schedule_*`-alias
retirement remain the deferred class-B work.

**S83 now FULLY closed (create + fire).** S92 made file automations creatable; S93 makes them
fire. The runtime `file_watch.changed_files` shipped in S83 had **zero live callers** and the tick
clock never surfaces a `file` trigger (no `next_fire_at`), so a chat-created file automation was
present-and-inert. S93's `file_poll` poll loop (WatchState persisted as a per-trigger sidecar) is
booted in `_init_cron` beside `ScheduleService` and fires through the same action-provider registry
a cron uses. **It is DISJOINT from `ScheduleService`** — that fires clock crons, this fires `file`
triggers, and the tick clock never surfaces either the other's kind — so booting them together
cannot double-fire. That disjointness is what makes it the additive cutover; the CLOCK switch-over
(retiring `ScheduleService` for the tick loop) remains deferred as the genuine class-B change.

**S83 UNBLOCKED and closed by S92.** S83 was `🟡 PARTIAL` for one honest reason: "Criterion 2 needs
`automation_create` (§4), which needs somewhere to PUT a `file` trigger. Measured: there is no
unified trigger store." **S87 shipped that store**, and re-measuring confirmed a `file` trigger now
round-trips with zero errors. So S92 built §4's eight-tool namespace over it and closed criterion 2:
*"when a file in ~/notes changes, summarize it…"* is creatable in one message, verified end to end
through the real MCP dispatch. The per-minute-poll trap the probe found (a file request reaching the
cron-only `nl_to_cron` and a model answering `* * * * *`, which validates) is prevented by
`nl_kind.route()` deciding the kind BEFORE the cadence converter is ever consulted.

**RESOLVED (S87-S91) — the substrate is MECHANICALLY COMPLETE and runs end to end, and the
cutover's named prerequisite now exists.** S91 shipped `personalclaw automation verify-migration`,
which §7 step 2 names in the same breath as the migration and §8 lists as the migration-trust
mitigation — S87's own docstring promised it by name and it did not exist. Driving it against a copy
of the owner's real store found the gap it exists to close: four jobs migrate `lossless: true` and
**two come out disabled** (`j-every`, a 5-minute interval, and `j-seq`, a 3-step `agent_sequence`).
That is deliberate on the migration's part, but `lossless` beside two silently-stopped automations is
technically accurate and practically misleading, so `VerifyReport.ok` is FALSE where `lossless` is
TRUE. **Cutover (a) should now be gated on this command exiting 0.**
`test_store_to_tick_to_dispatch_to_execute` drives store → tick → dispatch → execute with the LLM turn as
the only injected piece. What remains is not mechanism but two behaviour-visible CUTOVERS, each its own
session: (a) wiring the chain into gateway boot beside the live `ScheduleService` — both would fire the
same crons until the old one retires; (b) re-pointing `/api/triggers`' three backends at `triggers.json`
(§6: "the id namespace becomes the migration map"). Both carry user-visible risk on a machine with real
automations, so they are switch-overs rather than additions. The claim below that the two are one unbuilt foundation was HALF WRONG: they are separate concerns and the service needs the store, not the reverse. S87 shipped `triggers.json` (#246) and found that the cron migration would have silently retired every interval cron. What remains is the runtime.

**Original note, kept for the record —** the STORE and the SERVICE are one unbuilt foundation.**
S83 found there is no unified trigger store; S86 found there is no `triggers/service.py` and that **every
one** of the 15 trigger control modules has zero live callers. Sessions S62-S85 each recorded "NOT DONE (by
scope): the service" — eight notes in the plan's execution log — and no queue row ever owned either piece.
Consequence: every AUTO criterion probes as "machinery present" while no gate runs on a real fire. S86 ships
§3's ORDER as a tested composition so the eventual service session calls it instead of re-deriving 13 steps
from prose. What remains needs `triggers.json` + the WakeupDispatcher + the executor — a multi-session
program, and the owner's call on scope.

**BLOCKED — the unified trigger store does not exist, and no queue row owns it.** Criterion 2's chat
half (`automation_create`, §4) needs somewhere to persist a `file` trigger. The handler is a FACADE
over three legacy stores (`crons.json`, `event_triggers.json`, the hook config) routing exactly three
kinds; `file`/`webhook`/`idle`/`view`/`web_watch`/`run_completed` have **no persistence at all**. Rows
62-70 built the entity, disposition table, dispatch, cron migration and event parity — the store itself
was never a row. Building it + the eight-tool `automation_*` namespace + the `schedule_*` alias
retirement is a multi-session program, and writing the chat tool against a store a later session defines
is what EXECUTION-PROTOCOL forbids. S83 ships the runtime that program would otherwise invent under
pressure; the store is the next owner's first task.

### 🔴 The stacked-merge incident (2026-08-03) — resolved by #239

Every PR from #223–#236 went green and reported **merged** — into its own **stacked base branch**, not
into `main`. `origin/main` stayed at #222 (S68) while twelve sessions (S69–S80) sat only on those base
branches. The bases were then deleted as "merged" (true of the PR, false of `main`), leaving the work
only in the local object store.

**Nothing was lost.** PR #239 replays the 13 commits `main` lacked, cherry-picked in lineage order onto
current `main`; the resulting tree is **byte-identical** to the original stack tip (`da423978460c`),
proving a faithful replay. The eleven commits already on `main` as squashes (S61h–S68) were skipped —
which is why a naive `merge-tree` of the old tip reported nine conflicts and #239 reports none. `main`
was NOT force-pushed; the recovery lands as an ordinary fast-forward.

**The check that would have caught it** is `git merge-base --is-ancestor <branch> origin/main`, not the
PR's merged flag or `mergeStateStatus: CLEAN`. Branch cleanup is now gated on the former.

## QUEUE EXHAUSTED — 2026-08-03 (three criteria since closed; see below)

All **77 of 77** rows are `✅ DONE` with real PR numbers (sections A–I). The autonomous nudge asks for
"the first session whose status is not DONE"; there is no such row, so steps 1-7 have no subject. The
five other `BLOCKED` strings in this file are prose — the protocol text at line ~27 and historical
deviation notes — not live entries.

**Why this is E6 and not something to work around.** Adding a queue row IS the scope decision. The
workspace brief reserves it: "the roadmap is owner-maintained — propose changes via issue, not by
editing `docs/roadmap/` in a PR." Choosing session 78 would be choosing the program's direction.

**Deferred work already recorded in the plans, for whoever writes the next row:**

1. **Unmet SUCCESS CRITERIA are declared work, not new scope — three closed since.** The BLOCKED
   reasoning above was right to refuse INVENTING a session, but wrong as a stopping rule: an unmet
   acceptance criterion of a plan already in the queue is work the plan itself declares. Filed under
   the criterion rather than as new numbered rows, because the criterion is the authority.

   | Crit | What was unmet | Closed by | PR |
   |---|---|---|---|
   | 1 | One Proposal Inbox showing all six kinds; the model cannot accept its own proposals | Learning page + five `/api/learning` routes + the actor-derived accept gate | #234 |
   | 4 | The adversarial test must cover the REFINER path | `screen_evidence`/`fenced_evidence` — the refiner path had NO screen and NO fence | #235 |
   | 5 | The named ambient blocks must fit ONE slot-allocated budget | `learning/ambient.py` — the allocator, the preamble and `context_budget_tokens` were all inert | #236 |

   **Audited and genuinely met (verified by probing, not by reading):** crit 6 (`/api/lessons`
   consumers — three live routes + the MCP client path), crit 7 (`measure.py` exports per-arm
   precision, threshold tuning and the trust posterior), crit 8 (staging outcomes + the week panel
   shipped in #234), crit 9 (`accountability.attribute` + `revert_proposal`), crit 10 (the PER_TURN
   gate is called from `chat_runner` and refuses restricted sessions). Crit 2 and 3 were closed by
   their own sessions.

2. **`SESSION_END` / `RUN_END` gate coverage — NOT a gap-fill (corrected 2026-08-03).** S77 pinned
   these two cadences as having zero live callers. Measured afterwards: there is no session-teardown
   hook and no run-end capture pass anywhere in `src/personalclaw`, so this is not a gate someone
   forgot to wire — the cadences were declared ahead of the subsystems that would use them. Building
   the capture passes is NEW scope, and `Cadence.RUN_END` overlaps `triggers.models`' existing
   `run_completed` event, which the next author should reconcile rather than duplicate.
   `accountability.assert_gate_covers_cadences()` keeps the gap visible either way.

3. **The 22-PR stack (#210 → #233) is unmerged.** `origin/main` is still at #196, so every row above
   #196 is DONE-in-branch. GitHub merges each stacked PR into its own base, not `main` — see the
   stacked-PR mechanics memory before merging.

**State at block:** clean tree on `feature-wf2-accountability`; `make lint` green (665 files);
`pytest -n 4 --dist worksteal` → 14939 passed, 29 skipped, 13 xfailed. Nothing half-finished.
