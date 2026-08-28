# WORKFLOWS-V2-LEARNING-FLYWHEEL

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/WF2LEA.md`](../atomic/WF2LEA.md) as 14 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Learning Flywheel — One Lifecycle for Lessons, Skills, Memory, and Templates

**Status:** DONE — steps 1-8 shipped (PRs #163-#166, #227-#233) plus criteria 1/4/5 closed by
S78-S80 (#234-#236); the S69-S80 stacked-merge recovery landed as #239. Capture/propose/curate/
inject/measure/self-model/refiner/inbox are live-wired through `context.py`, `after_turn_review.py`,
`chat_runner.py`, `history.py`, the five `/api/learning/*` routes and the Learning page.
✅ **The 2026-08-04 "REMAINING" block was CLOSED and is corrected here (measured 2026-08-22 against
`origin/main` = `05bba66e`).** All three of its claims are now false, and both owning atoms
(`WF2LEA-5`, `WF2LEA-7`) are `done` in `dag.json` — the header was contradicting its own rows and its
own execution log, which is the first thing a session reads:
· `learning/accountability.py` has **1** production importer — `learning/attribution.py` imports it
  and calls `accountability.attribute` / `Outcome` / `MIN_RUNS`, which IS criterion 9's verdict path
  (logged DONE 2026-08-09 under `WF2LEA-5`).
· `learning/detectors.py` has **6** — `after_turn_review.py`, `learning/attribution.py`,
  `learning/loop_end.py`, `learning/run_end.py`, `learning/template_gate.py`, `mcp_core.py`
  (`WF2LEA-7`).
· `Cadence.SESSION_END` is live at `dashboard/chat_utils.py:705`; `Cadence.RUN_END` at
  `workflows/controller.py:4235` and `loop/watchdog.py:483`. `assert_gate_covers_cadences()` returns
  `[]` — run, not read — so NO declared cadence is uncovered. That guard being called only from
  tests is its design, not a gap: it is a source scan whose whole job is to be asserted by a test
  (`tests/test_learning_accountability.py` pins both the empty gap set and a seeded phantom).
Importer counts came from an AST index, not a grep: `detectors.py` shares its stem with ordinary
prose and with an unrelated `web_source.DETECTOR_ORDER`, both of which a text scan reports as
importers. (rev 2 — research-integrated 2026-07-12)

---

## Research Integration (2026-07-12)

Approved recommendation IDs folded into this revision (ID → landing section):

- **LEARN-R1** (proposal decision memory: fingerprints, rejection exemplars, quota, resolve cascade, extract→decide) → §2.2
- **LEARN-R2** (refiner acceptance statistics: median-of-3 critic, held-out replay/GateOK, frozen region, canary revert, harvested regression suite) → §3.1
- **LEARN-R3** (refiner trust + emission: fenced inputs, deterministic tier, typed-op diffs, evidence manifests, risk tiers, skill sidecar overlays) → §3.1
- **LEARN-R4** (Measure un-demoted: surfacing_events, mechanical "used", per-arm confidence, Beta-Binomial trust) → §2.5
- **LEARN-R5** (capture hygiene: system-injection filter, grounding gate, session scoring, notability rules, pre-compaction flush) → §2.1
- **LEARN-R6** (curator hardening: provenance scoping, demote-never-delete WAL, over-deletion guards, decay formulas, mode-scoped sweeps) → §2.3
- **LEARN-R7** (context budget as ranked slot allocator with authority doctrine and tiered rendering) → §2.4
- **LEARN-R8** (typed failure signatures, executable lesson checks, failure capsules) → §3.3
- **LEARN-R9** (ratchet invariant, scaffolding-retirement proposal kind, expiry metadata, provenance-weighted lifecycle) → §2.2 / §1
- **LEARN-R10** (judge health pass: nodding-loop detector, judge-divergence events) → §3.1
- **LEARN-R11** (template maturity levels L0-L3 from ledger-derived health) → §3.1
- **LEARN-R12** (invocation axis, precondition gates, glob/pack auto-attach, trigger-shaped description lint) → §1 / §2.4
- **LEARN-R13** (§3.2 detectors as deterministic gate chain with reasoned-skip observability + 4 signal sources) → §3.2
- **LEARN-R14** (surfaced-entity chips with mute-as-signal + flywheel observability panel) → §6
- **LEARN-R15** (near-miss surfacing ledger + nudge_threshold detector) → §2.5
- **LEARN-R16** (change-manifest attribution: predict-then-verify, 5-way verdicts, HARMFUL auto-revert) → §2.2 / §3.1
- **LEARN-R17** (trajectory-variance tier-migration detector: agentic↔fixed) → §3.5
- **LEARN-R18** (pending→resolved outcome-grounded lesson lifecycle) → §3.3
- **LEARN-R19** (explicit staging tier with outcome records and cost metering) → §2.1
- **LEARN-R20** (voice-aspect capture: directives/preferences/beliefs stored whole, never decomposed) → §1 (re-pointed here from KNOWLEDGE-SYNTHESIS — this is memory-side work)
- **LEARN-R21** (capped self-model: reinforcement-promoted behavioral principles) → §2.6 (re-pointed here from KNOWLEDGE-SYNTHESIS)

**Recon corrections applied in this revision** (verified against code 2026-07-12):
1. There is NO `LearningGate` class today. Eligibility is the free function `after_turn_review.should_review()` (:88), recomputed independently in BOTH `chat_runner._maybe_after_turn_review` (:158) and `_maybe_skill_ladder_review` (:217) — and `capture_preference_facet` runs UNGATED on every non-ephemeral turn (chat_runner.py:150). §2.1's "one gate, computed once" is the FIX for this verified duplication + gap, not a description of the present.
2. Lessons ALREADY live primarily in memory.db as `lesson.*` semantic rows via `vector_memory.write_lesson` (L1858) with dedup + supersession + the contradiction judge; `lessons.jsonl` (`learn.py` LessonStore) is only the no-embedder WRITE fallback + `/api/lessons` dashboard backing and no longer feeds prompts (context.py L920). Step 2's "migration" is a consumer reroute + residual-file import, NOT a data migration of the live lesson corpus.
3. The skills lifecycle sidecars are exactly `~/.personalclaw/skills/.proposals/`, `.usage.json`, and `.skill_embeddings.json`; the curator ladder (`skills/curator.py`, STALE_AFTER_DAYS=30 / ARCHIVE_AFTER_DAYS=90, `auto/` namespace only, `pinned: true` exempt) is verified — but `run_aging()` has NO verified scheduled caller. The plan previously said the generalized curator "rides the existing heartbeat prune tick"; corrected: it is wired into `history.py`'s consolidation maintenance cadence (the post-steps that already run `expire_by_category`/`promote_by_heat`), which IS a real, verified tick.
4. The 0.55 skills surfacing threshold is REAL (`skills/surfacing.py surface_skills(..., semantic_threshold=0.55)`) — kept, alongside the 0.62 SOP match_text profile.
5. `plan_memory/` deletion must also remove it from `portability.py`'s export tree list (it is currently exported).

---

## Overview

PersonalClaw already has every organ of a learning flywheel — it has grown five of some of them. This plan is NOT a new learning system; it is (a) a crisp four-entity taxonomy, (b) ONE shared lifecycle machine (staging log, proposal queue with decision memory, usage/measurement store, surfacing allocator, curator, judge) that all four entities ride, and (c) the new workflow-native learning spokes that v2 runs enable.

**Soul guard:** learning must feel like *the assistant getting to know you*, never like an MLOps console. Single-user, on-disk, human-reviewable markdown/JSON, propose-don't-write. The UX north star: the user occasionally opens one inbox, taps accept a few times, and their assistant is visibly better at *their* recurring life. Rev 2 adds the operational hardening that keeps that inbox worth opening: the flywheel never re-files a rejected proposal, never accepts a diff on judge noise, and can answer "is it actually capturing?" at a glance.

**Boundary (user directive):** MEMORY is the harness's own internal mechanics — facts, facets, episodic, procedural, lessons, the self-model — living in memory.db and this plan. KNOWLEDGE is the user's personal items (documents, files, photos, notes) in knowledge.db, owned by KNOWLEDGE-SYNTHESIS. Nothing in this plan reads or writes knowledge.db; `knowledge_*` names never appear in flywheel code.

---

## Architecture Fit — where each piece plugs into the provider system

The flywheel is harness-core (like memory and skills), NOT a new provider family — deliberately: learning about the user is the harness's own job. But every touchpoint follows the existing extension seams:

- **Config:** all new knobs extend the existing `LearningConfig` dataclass (config/loader.py:934) and MUST be wired through the FOUR points (recon-verified gotcha): (a) dataclass field with `_meta(label, help)`; (b) `AppConfig.load()`'s explicit field-by-field mapping (loader.py:1638-1802 — omission = silently dropped); (c) `to_dict()` (:1930); (d) `_EDITABLE_CONFIG` (dashboard/handlers/core.py:363) + FE panel for anything runtime-editable. New knobs: `learning.propose_quota_per_run`, `learning.min_evidence` (the shared ≥3 constant, §2.1e), `learning.context_budget_tokens`, `learning.staging_enabled`, `learning.self_model_enabled`.
- **Action providers:** the refiner (§3.1) runs as a trigger-fired workflow via the existing `run-workflow` action provider — already in `ALLOWED_HOOK_PROVIDERS` (src/personalclaw/validation.py), so NO allowlist change is needed. If any future slice ships a dedicated learning action provider, it must be added to that frozenset or `hook_create` rejects it.
- **Stores:** the new lifecycle tables (proposals, decisions, usage, surfacing_events, staging log — §2) live in ONE new SQLite file `~/.personalclaw/learning.db` (WAL, 0600, `atomic_write` conventions for JSON siblings). It must be added to `snapshot.py` `CORE_FILES`/`VALID_COMPONENTS` and `portability.py`'s export set — recon confirms current snapshot/export coverage is partial and a new store is invisible to backup unless explicitly listed.
- **Memory writes:** all memory-side artifacts (lessons, facets, voice aspects, self-model) go through `MemoryService`/`VectorMemoryStore` — the `MemoryProvider` seam (memory_providers/registry.py) is preserved; new key prefixes (`user.voice.*`, `user.selfmodel.*`) must be added to `_BUILTIN_PREFIXES` (vector_memory.py L204) and to the injection-exclusion clause `_NON_FACT_KEY_CLAUSE` (L383) so they don't leak into fact blocks.
- **Skills:** accepted skill proposals continue to land via `SkillsLoader.create_auto_skill` into the `auto/` namespace; marketplace-installed skills keep `install_guarded`'s lock/verify pipeline untouched — the flywheel's sidecar overlays (§3.1) never mutate locked files, so `verify_skill_integrity` stays green.
- **Model resolution:** every background LLM call (refiner, critic, resolvers, detectors' boundary pass) goes through `one_shot_completion(use_case=…)` — "background" for capture/refine, `eval_judge` for the critic (the LLMJudge's own binding). No provider is ever hardcoded.
- **Apps:** third-party apps can feed the flywheel only through the existing seams (skills marketplaces, `sdk.security.fence_untrusted`-fenced content, workflow templates); they cannot write proposals or memory directly.

---

## 1. Entity Taxonomy — Boundary Rules

| Entity | Answers | Shape | Injection | Executable? |
|---|---|---|---|---|
| **Memory record** (facts, facets, voice aspects, episodic, procedural priors, self-model) | "What is true about the user/world — and how does the user think?" | typed record in memory.db | ambient blocks + recall | no |
| **Lesson** | "What must I always/never do?" | one corrective rule, ≤2 sentences; optional machine-checkable form (§3.3c) | `[Learned corrections]` block, every session | no — a constraint (unless promoted to an executable check) |
| **Skill** | "How do I do this CLASS of task well?" | markdown know-how, unordered | relevance-surfaced, progressive disclosure | by agent judgment |
| **Workflow template** | "What are the exact STEPS, gates, order?" | v2 graph spec, versioned | relevance-surfaced + runnable | yes — by engine, journaled |

**Routing rules (encoded verbatim in capture prompts):** declarative → memory; constraint → lesson; technique → skill; ordered procedure with checkable steps or side effects → template. A veto ("never pip, use uv") is a lesson even if phrased as preference (the facet-veto seam stays).

**Voice aspects (LEARN-R20):** user-voiced statements classified into aspects (Directive, Preference, Habit, Belief, Goal) are stored as COMPLETE verbatim statements with an aspect tag — never decomposed into normalized facts — as a memory sub-kind under new `user.voice.<aspect>.<slug>` semantic keys (prefix added to `_BUILTIN_PREFIXES`; excluded from fact blocks via `_NON_FACT_KEY_CLAUSE`, rendered by their own adapter). Observed/world content continues to decompose into typed records. Retrieval gains an explicit axis: "how the user thinks/wants" (whole statements, injected verbatim) vs "what is true" (decomposed facts). This extends §2.1's verbatim-capture rule from lessons to the whole voice class; same-aspect same-subject statements with divergent content become the curator's supersession candidates. The facet-veto seam is unchanged. Preference facets stay a memory sub-kind (they already ride memory.db with the right decay math) — voice aspects are a sibling sub-kind, not a fifth entity.

**Universal entity metadata (LEARN-R9c, R12):** all four entities carry `source` (why created — user | agent | run-inferred), `applicability` (when to surface: semantic | always | context-glob), and `expiry` (when to retire) metadata the curator audits — instruction bloat with no expiry is the measured failure mode of merged surfacing. Skills additionally gain (R12): `model_invoked: bool` (default true; false = excluded from surfacing embeddings and the INDEX entirely, listed only via a router entry — zero context-budget cost for command-like skills; the two populations get different metadata and different indexes, with an 80-char hint cap on the agent-side index); deterministic `requires_tools`/`platform` precondition gates evaluated before any threshold scoring; and a third activation mode — context/glob auto-attach (inject when workspace files or stage kind match a declared pattern), generalizable to confidence-scored "enable this pack?" proposals from project-fingerprint detection (never silent auto-apply).

**Promotion ladder (first-class):** ≥3 lessons clustering on one topic → skill proposal. A skill whose body has become an ordered checklist → template proposal. A template whose step keeps getting skipped → reverse lesson proposal ("step 3 is dead — delete it?"). This is GPTs' instruction-accretion done right: accretion into versioned per-entity artifacts with a graduation ladder, not one bloating blob. NOTE: this ladder is about entity KIND; execution-TIER migration (agentic↔deterministic within the template entity) is a separate, two-way proposal class — §3.5.

---

## 2. One Pipeline: Capture → Stage → Propose → Curate → Inject → Measure

### 2.1 Capture — three cadences, ONE gate, ONE hygiene policy, ONE staging log

Keep exactly three cadences (they observe different signals):
1. **Per-turn** (`after_turn_review.py`): corrections → lessons, facets, voice aspects, procedural priors, skill-ladder.
2. **Session-end** (consolidation in `history.py`): the batch envelope.
3. **NEW — Run-end**: the workflow-run outcome learner (§3), firing on WorkflowRun terminal state.

Unified plumbing:

- **One `LearningGate` module (NEW — corrected claim):** today there is no such module. Eligibility is `after_turn_review.should_review()` recomputed independently at chat_runner.py:158 and :217, and **preference-facet capture runs UNGATED** at :150 (only the expensive review is gated). The new module computes eligibility (enabled, non-ephemeral, non-restricted, sensitivity) ONCE per event and ALL capture paths — including facet/voice capture and the run-end cadence — consume the one result. Incognito/temporary suppression today is a process-global registry (`session_restrictions.py`) consulted at N scattered sites; the LearningGate becomes the single learning-side chokepoint consulting it (other consumers — history consolidation's `memory_mode` check, mcp_memory — are unchanged). Runs originating from temporary/incognito sessions inherit write-suppression through this gate.
- **One `capture_hygiene.py`:** content inside `fence_untrusted` (inbox bodies, web fetches, MCP payloads, webhook text) is INVISIBLE to all three cadences (the Codex `disable_on_external_context` pattern), plus the env-failure deny-filter (`is_environment_failure_claim`), redaction, and sensitive-path filter — one auditable policy at the gate instead of scattered implicit filters. The skill-ladder's current hand-built inline fence (after_turn_review.py:260) is normalized onto `security.fence_untrusted`. **Stated boundary:** user-PASTED text in the user's own message is user-trusted by single-user doctrine and CAN direct-write a lesson via the correction heuristic — accepted risk, documented. Rev-2 additions (LEARN-R5):
  - **System-injection filter:** cron/autonudge/orchestrator/heartbeat preamble prefixes are invisible to all capture cadences — at PClaw's cron density, platform scaffolding is the larger pollution volume, not untrusted content.
  - **Grounding filter** for per-turn capture: decision-evidence AND outcome-evidence regexes plus minimum substance on both sides.
  - **Session scoring** as the consolidation gate: sessions below a weighted depth/decisions/recall/engagement threshold are skipped entirely.
  - **Three capture rules verbatim in the prompts:** the notability gate ("when in doubt, DON'T create" — junk degrades recall; missing can be added later), verbatim capture of user phrasing for lessons AND voice aspects, and a one-line "Learned: N signals (…)" log per cadence.
  - **One `min_evidence` constant** (≥3 occurrences, `learning.min_evidence`) shared between the §1 promotion ladder, all pattern synthesis, and R1's inferred-proposal floor.
  - **Pre-compaction flush:** a silent turn (optionally on a cheap local model via the `background` use-case) persists unsaved context through the LearningGate before summarization destroys it.
- **One staging tier (LEARN-R19)** between raw capture and the proposal queue: (a) cheap immediate extraction (per-turn and session-end) appends to an immutable per-day capture log in learning.db (append-only; never edited by consolidation); (b) the expensive consolidation/curator pass runs batched over accumulated staging entries, triggered by activity + time-window gate + **input-hash idempotence** (no new daemon — piggybacks on session activity and the consolidation cadence); (c) compiled proposals keep `sources:` pointers back to staging entries for auditable provenance; (d) every extraction pass persists an explicit outcome record — `FLUSH_OK` (nothing worth proposing), `FLUSH_ERROR` (type/message), or proposal IDs — so absence of output is observable and a week of all-FLUSH_OK on an active system can alarm (this is the observability floor that prevents the S05 dead-transcript-read bug class from recurring undetected); (e) every flywheel op (extract/consolidate/curate/decay) meters its LLM cost into the ledger, aggregated in the observability panel (§6).
- **Extraction is two-phase (LEARN-R1 batch-5):** fact extraction and the ADD/UPDATE/DELETE decision are two DISTINCT steps with a candidate-gathering query between them (search existing entities relevant to each extracted item) so the decide pass always sees what already exists — preventing drift/duplicate accumulation by construction. Per-turn capture reads the last messages PLUS existing stored facts and emits a structured `{create:[], delete:[]}` output — an explicit delete/supersede channel at extraction time (inferred deletes route through the proposal queue per §2.2's write policy; explicit user corrections may supersede directly).

### 2.2 Propose — one queue, four kinds, decision memory

Generalize `skills/proposals.py` (today: `.proposals/<id>.json`, `_MAX_PENDING=100`, fenced `source_excerpt` at :105) → `learning/proposals.py` with `kind: skill | lesson_batch | template | template_diff | retirement | tier_migration`. **The invariant is the flywheel's trust anchor: autonomous synthesis proposes; the human installs.**

Per-kind write policy:
- Facets, voice aspects, procedural priors, episodic/semantic memory: **direct write** (reversible, decaying, low blast radius) — through the LearningGate.
- Lessons from *explicit user correction*: direct write (via `write_lesson`'s existing dedup + contradiction judge). Lessons from consolidation/run-failure inference: **proposal** — a change from today (consolidation lessons currently write live) that closes a real prompt-injection→standing-instruction hole.
- Skills: proposal (unchanged). Ephemeral taught skills: direct session-scoped (unchanged).
- Templates and template diffs: **always proposal**; template diffs carry a rendered before/after graph diff.

**Decision memory (LEARN-R1) — the queue's anti-nag machinery:**
- Every proposal carries a **content fingerprint** (order-independent hash of kind + target entity + normalized diff/body). Every proposer (refiner, §3.2 detectors, curator, self-model observer) consults the decision store before filing: a fingerprint matching a prior ACCEPTED or REJECTED decision is silently skipped; "later" preserves DRAFT for the next pass.
- **Rejected proposals are KEPT as negative exemplars** (a `rejected` store) with an embedding-similarity prior-rejection check at propose-time in addition to exact fingerprints; declined promotions get escalating re-propose cooldowns.
- **Exact duplicates REINFORCE** the pending row (reinforcements counter; duplicate proposals refresh the existing row — bump updated_at, merge tags) instead of inserting; variants get a `specializes` parent link, never a merge; supersedes lineage + soft-delete semantics throughout.
- **Deterministic 4-verdict resolve cascade** as pre-LLM triage on every proposal/memory write: new (cosine <0.85) | reinforce (≥0.92) | replace-on-contradiction | entity-append merge — with a subject guard (embedded first-2-word span cosine ≥0.60) and polarity/negation/number contradiction detectors (spaCy-only, zero LLM cost) running BEFORE the reinforce shortcut. Near-identical similarity (≥0.92) with opposite polarity must REPLACE, not reinforce. This extends `write_lesson`'s existing dedup/supersession into a shared policy for all proposal kinds.
- **Per-run proposal quota** (~3-5 high-signal, `learning.propose_quota_per_run`) alongside the 100-pending cap, oldest-auto-expire.
- **Confidence discipline:** inferred proposals need ≥`min_evidence` occurrences to exist; confidence scales with sample size; correlational evidence is labeled "correlated", never "causal".

**Ratchet + retirement (LEARN-R9):**
- **Ratchet invariant:** inferred lesson/template-diff/rule proposals are generated ONLY from Run-Ledger-evidenced failures ("ratchet, don't brainstorm"), each carrying run id + failure event — provenance is a hard generation precondition, not an annotation.
- **`retirement` proposal kind:** when a rule/hint/gate/template-step has never triggered across N consecutive runs on a newer model, propose its removal WITH ablation-grade evidence — applied strictly one at a time (batched removal demonstrably fails). Retiring a template/trigger auto-drafts a lesson proposal from its ledger history.
- **Provenance-weighted lifecycle:** human-originated corrections (gold) decay slower and outrank agent-inferred patterns in proposal scoring and the decay kernel.

**Change manifests (LEARN-R16):** every template-diff/skill-edit proposal carries a `change_manifest`: {component, files, failure_pattern, evidence_refs (Run Ledger event ids), root_cause, targeted_fix, predicted_fixes[], risk_tasks[]}. Validation is lenient-but-recording: missing/invalid manifests yield warnings + `manifest_valid=false` on the record (surfaced in the Proposal Inbox), never a hard reject. Post-acceptance attribution closes the loop in §3.1.

Every proposal carries provenance: source cadence, session/run id, fenced evidence excerpt, motivating ledger/staging pointers. Accepts/rejects are SEL-audited (`sel.py`) like skill installs.

### 2.3 Curate — one usage store, one decay kernel, one hardened curator

- **One usage store** (`learning/usage.py`, tables in learning.db — the JSON sidecars `.usage.json` were a skills-ism) with **per-entity semantics** (review resolution — a naive shared store degenerates):
  | Entity | Recorded events |
  |---|---|
  | Skills | surfaced_at, loaded_at |
  | Templates | surfaced_at, run_at, run outcome (success/failure) |
  | Lessons | **EXEMPT** — always-on caps-bounded blocks make "surfaced" degenerate to session count; their lifecycle signal is the contradiction judge + capsule replay (§3.3d) + explicit forget only |
  Reinforcement updates flush once per session (idle watchdog), not per retrieval — prevents heat inflation distorting decay.
- **One curator** (`skills/curator.py` generalized): ages auto-captured skills AND templates `active→stale(30d)→archived(90d)` (the verified `STALE_AFTER_DAYS`/`ARCHIVE_AFTER_DAYS` ladder); pinned bypass. **Scheduling corrected:** `run_aging()` has no verified scheduled caller today — the generalized curator is explicitly wired into `history.py`'s consolidation maintenance cadence (the post-steps that already run `expire_by_category`/`promote_by_heat`/`synthesize_failures`), which is a real, verified tick. No new scheduler.
- **Curator hardening (LEARN-R6):**
  - **Provenance scoping:** all 4 entities carry `created_by`/`source_type` (user | agent | run-inferred); the curator may age/consolidate/patch ONLY agent-created entities — user-authored content is archive-only, exempt from auto-dedup/eviction; merge-conflict priority human > procedural > ai.
  - **Demote-never-delete:** curator/decay mutations are WAL-logged with undo (operation, before/after, undone_at) — extending `vector_memory`'s existing reversible event WAL (`undo_event` L1243) pattern to learning.db; dry-run mode + a per-run report; contradictions resolve via supersession versioning (the existing v4 `superseded_by` chain), never delete; append-only dated changelog semantics exposed in the curator UI.
  - **Guards:** bounded batches per tick (~8, oldest-audited first); fingerprint short-circuit (no-op audits cost zero LLM calls); over-deletion refusal (reject any pass cutting >50% of ≥8 entries); the LLM-curator pass cadenced by mutation count (every N writes) while the deterministic decay/structural kernel runs every tick free.
  - **Decayed-but-high-stability entities become a REVIEW proposal** in the unified queue instead of silent archival.
  - **Auto-repair vs proposal policy line:** deterministic, reversible, low-stakes link-writes (string-similarity ≥0.92) may bypass the queue; semantic/destructive mutations always go through. Speculative claims decay faster (claim hedging).
  - **Mode-scoped sweeps + windowed checkpoints (batch-5):** the review-and-merge pass takes an explicit mode (per entity type, or a combined "experience" mode) so a sweep can be scoped; the last consolidation time is persisted as a first-class record and each pass processes only the window since — idempotent-by-construction, corroborating R19's input-hash idempotence from the curation side. Maintenance runs in a fixed order (cleanup → per-type dedup → pattern analysis LAST), and the final phase mines episodic entries for routines/pattern changes and writes those insights back THROUGH the proposal queue — a generative output on top of the janitorial one.
  - **Optimizer detector battery** as named curator proposal kinds: compress_summary (>500-token), downgrade_detail, promote_importance (served ≥5×), merge_candidates (same kind + >60% tag overlap), archive_unused — each with `estimated_token_saving` as comparable currency.
  - **Pinned shared/imported templates** get skills-lock-style `{source, computedHash}` so template-diff proposals distinguish upstream drift from local evolution.
- **Heat-earned promotion — hardened:** a session-scoped auto-captured template gets a *promotion suggestion* only on multi-gate evidence (usage count AND recency AND context diversity — not the bare "≥2× surfaced"); never auto-promote (scope widening is a trust decision). Skills' ephemeral ladder adopts the same policy — four "prove narrow, graduate wide" mechanisms become one policy with per-entity thresholds.
- **Decay: one kernel, three profiles.** Facet stability + engagement weight already share `decay()`; memory heat has its own math (`memory_record.heat()` L259: 0.7·log1p(visits)/ln10 + 0.5·e^(−days/30); episodic: cos·(0.7+0.3·imp)·e^(−0.03·days)) — its migration to the kernel is a REAL (small) change, not a rename. Kernel form (R6f): strength = exp(−baseλ × entityMultiplier × daysSinceUse) with per-kind half-lives (strategies endure, failures go stale fast); importance is a decay-immune second axis modulating λ ×(1−imp·0.8), NOT an exemption; prune only when both low; reinforcement boosts halved within 1h and journaled; an active-days clock (vacation-proof for a single user); chain-aware sparing (skip eviction when a strongly-linked neighbor exists). **Doctrine: strength never enters surfacing rank — it gates eviction and review only.**
- **Judge sequencing (review resolution):** the canonical harness is `eval/judge.py`'s LLMJudge (verified: `provider_factory("eval_judge")`, pass_threshold 3.0, rejects tool-permission requests, parse-failure → score 0 — the reject-by-default property §3.1 relies on); the lesson-contradiction judge becomes a thin wrapper NOW (it also runs over template diffs: "does this diff contradict an existing lesson?"). `loop/judge.py`'s ground-truth wrapper is NOT unified pre-emptively — loops converge onto v2 per LOOPS-EVOLUTION, and its ground-truth contract survives as `gate{verify_command}` + judge-prompt doctrine there. Unifying it first would be wasted motion.

### 2.4 Inject — two surfacing engines become one ranked slot allocator

Merge `skills/surfacing.py` + `workflows/surfacing.py` into `learning/surfacing.py`: one embedding-cache format (generalizing the `.skill_embeddings.json` path+mtime+model-keyed cache), one keyword fallback, one specificity tie-break, per-entity render adapters (skill INDEX / `[SUGGESTED WORKFLOW]` block / lesson block / voice-aspect block).

**Review resolution — thresholds stay per-entity:** the current 0.55 (skills, verified in `surface_skills`) vs 0.62 (SOP match_text) split was DELIBERATELY calibrated for different text profiles (the code comments document it). The merged engine takes named threshold profiles per entity kind, carrying the calibration rationale over. Joint recalibration only when §2.5's measurement shows the split unjustified — empirically, not by taste.

**One context budget = a ranking algorithm, not a token counter (LEARN-R7):**
- Per-entity thresholds stay as ENTRY gates; post-threshold candidates from all four entities enter ONE salience pool scored (0.55·query_overlap + 0.45·score) × 0.85^rank × entity_prior (priors near 1.0 — relevance must dominate source identity), with cross-source fusion via RRF (k=60) and per-source diversification (max ~3 items per run/session) applied BEFORE trimming.
- **Slot-based allocation:** priority-ordered named slots (system/constraints/lessons/skills/memory/retrieved-context) with tiktoken-exact counting (char/4 fallback); truncation applies only to the designated sacrificial slot (retrieved context) — instructions and lessons are never crowded out; oversized items skip, not truncate. This closes the same bug class as the whisper bias-prompt overflow.
- **Position policy:** hard-constraint entities (lessons) inject at context edges; detail demoted to on-demand references (measured 60%→95% compliance lift from position alone); curator-maintained compressed digests by default with full spec on demand.
- **Tiered rendering:** every entity persists L0 (one-liner) / L1 (operational summary) / L2 (full body); the allocator degrades tier before dropping items; L2 only at ≥0.9·top_score with a hard cap ~3 full-detail items; the render ends with an L0 catalog of unloaded near-misses plus a request-on-demand affordance. Degradation sequence: full → evenly-shrunk descriptions → names-only. Concrete budgets: ~500-byte manifest entries, 500-2000-byte full text on demand, dedup returns "already loaded".
- **Same-subject/cross-subject tiers:** N most-recent same-subject entries FULL + M cross-subject entries text-only under one header; pending-outcome entries (§3.3) exempt from eviction.
- **Intent-adaptive weight profiles:** lexical classification into debug/ideation/default modulates the salience formula's lexical/recency/importance mix; path/file-touch match is a deterministic surfacing signal.
- **Authority doctrine:** a 3-line preamble on the rendered block — injected lessons/memory are authoritative over model priors, explicit conflict rules, never treat a question as novel when the answer is already injected (counters the measured "perfect injection, agent re-searches everything" failure).
- **Keep the total aggressive:** LLM-generated context files HURT at +20% cost (ETH Zurich); fewer, human-approved entries win.

The allocator owns the ambient render currently assembled in `context.py build_session_context` (~L846-940: memory context → working memory → persona → USER PROFILE facets → skills → ephemeral skills → capped lessons) — that ordering becomes the slot order, and four entities can no longer independently accrete prompt weight.

### 2.5 Measure — UN-DEMOTED to a v2-independent floor (LEARN-R4, R15)

The plan previously deferred all measurement to the contingent §3.4; that left "visibly better at their recurring life" uncheckable. The floor is near-zero-cost and rides the merged engine:

- **`surfacing_events` table:** the §2.4 engine logs every surfacing event (entity kind, matching arm, confidence, session/turn). **"Used" is derived MECHANICALLY** — skill body loaded after surfacing, template run started from a suggestion, run outcome success/failure, lesson referenced by after_turn_review — never a voluntary model feedback call (unenforced "helpful" scores stay ornamental forever). Per-arm precision reports tune the per-entity threshold profiles (0.55/0.62) from data. Events prune at 90d on the curator tick.
- **Per-arm confidence semantics:** distinct base confidences per match path (exact name/alias ~0.9, exact title ~0.8, embedding ~0.6, +0.05 recency bonus), gated on the fused score — a single scalar can't be calibrated per-arm.
- **Bayesian trust:** helpful/surfaced ratio with a trust prior (start 0.50); Beta-Binomial usefulness posteriors per entity with per-arm citation rates feed curator aging and the surfacing tie-break. Self-similar retrieval dedup (cron-clone filtering) keeps the counts honest. New retrieval arms dark-ship and are judged by citation data before enablement.
- **Outcome-derived effectiveness** per skill/template (run success ratio, loaded-to-outcome correlation) blends into surfacing rank as sim·(floor+(1−floor)·eff).
- **Ablation-delta rule:** every surfacing heuristic ships with a measured delta and is removed if ~0 — honest reporting of null results is a feature.
- **Stale-candidate rule:** never-recalled AND importance ≤ floor AND age ≥ threshold feeds the curator.
- **Response provenance:** when a surfaced entity influences a reply, tag the reply with which entity informed it — visible trust plus a free click-signal stream (renders as §6's chips).
- **Near-miss ledger (LEARN-R15):** persist `not_surfaced(entity_id, final_score, reason)` alongside served-entity records on every assembly — data the engine already computes then discards. A `nudge_threshold` detector over it: any entity scoring near-threshold but not loaded in ≥3 of the last 10 assemblies emits an importance-bump proposal into the unified queue (entities that systematically score just below their kind's threshold likely have stale importance metadata, not irrelevant content). Near-miss patterns surface in the observability panel as evidence of undertriggered entities.

### 2.6 Self-model — capped, reinforcement-promoted, propose-don't-write (LEARN-R21)

A compact, hard-capped artifact the flywheel maintains about its OWN observed working patterns with this user — harness-internal by definition, so it lives in memory.db under `user.selfmodel.*` keys (prefix allowlisted; excluded from fact blocks; adjacent to the existing `user.persona.*` seam, self_persona kind):

- After significant turns, an observer records (route taken, tools used, success/failure, user reaction) into the staging log.
- Repeated useful habits become behavioral-principle PROPOSALS once they cross reinforcement thresholds (seenCount ≥ 2 AND confidence ≥ 0.72), landing in the unified queue like any lesson — **never self-installed**. Accepted principles are lessons-shaped (constraint-like, always-on) but sourced from observation-reinforcement rather than explicit correction, carrying reinforcement evidence as provenance.
- **Bounded by construction:** max ~6 active principles, ~4 working theories, ~4 current-focus entries, a small retrospection ring buffer — promotion beyond a full cap requires displacing (demoting) an existing entry, making bloat structurally impossible at the schema level.
- Only a compact snapshot injects into planning/recovery prompts — one budgeted slot in §2.4's allocator, never the full history.
- Declined promotions feed §2.2's rejection exemplars with escalating re-propose cooldowns.

This is the flywheel's only mechanism that learns from what quietly WORKS (the capture cadences only learn from corrections and failures). `learning.self_model_enabled` gates it (four-point config wiring).

---

## 3. Workflow-Native Learning Spokes

### 3.1 Run outcomes → template refinement (the flagship)

**Mechanism = Anthropic's evaluator-optimizer, run COLD over the journal, never hot in the loop:**
- The engine's **Run Ledger** events (`step_completed/failed/skipped`, `gate_rejected{user_comment}`, `user_edited_mid_flight{ops}`, `run_abandoned`) are an ACCEPTANCE CRITERION on WORKFLOWS-V2.md §5 — filed there, not assumed here. The refiner is starved without them.
- **Refiner (optimizer):** after every failed run and every N=5 completed runs per template, a background pass reads the ledger + current template and emits `template_diff` proposals: prompt rewording, added retry/on_error, a gate where users keep intervening, step deletion where users keep skipping. Mid-flight `workflow_edit` ops are gold — repeated identical hand-fixes become "make this permanent?". **Substrate (LEARN-R3e):** the refiner is a trigger-fired workflow run via the existing `run-workflow` action provider (already in `ALLOWED_HOOK_PROVIDERS`), per AUTOMATION-SUBSTRATE's own doctrine — not a bespoke background one-shot.
- **Critic (evaluator):** LLMJudge scores each diff against ledger evidence + the contradiction check; sub-threshold diffs are dropped silently — the user only sees defensible proposals.
- **Accept → new template VERSION:** monotonic versions, runs pin the version they executed, diff view is version-to-version, rollback = re-pin. Append-only by construction.
- This absorbs and deletes the dead `plan_memory` silo (its only writer already has zero live callers); one final consolidation of `plan_lessons.md` seeds a small global planning-lessons skill. Deletion includes removing `plan_memory/` from `portability.py`'s export tree list (it is exported today).

**Refiner trust + tiers + emission shape (LEARN-R3):**
- **TRUST:** run transcripts, `gate_rejected{user_comment}`, and `run_feedback` text are wrapped in `fence_untrusted` before the refiner LLM sees them (recon: fencing is caller responsibility — only 4 call sites exist today, and the refiner becomes the 5th), and success criterion 4's adversarial test explicitly covers the refiner path: injection in a run transcript must not become an accepted diff. **Tool-set scoping makes propose-don't-write structurally unbreakable:** the refiner agent gets only `propose_*` tools; only the human-facing review surface holds apply tools.
- **TIERS:** a zero-cost regex/statistics pass over the ledger (failure-signature counts, skip counts, repeated identical `workflow_edit` ops) runs BEFORE any LLM call. **Failure clustering is the front half:** cold pass over Run Ledger failures into structured records, clustered by shared mechanism, ranked by frequency × unresolvedness; the refiner proposes against the top cluster. The LLM tier runs on a cheap model (`one_shot_completion(use_case="background")`) over digest replay with a mandatory NO_PROPOSAL decline path. **Evidence input is two-layer:** mechanical `render_run_trajectory(ledger_events)` clipped to tens of KB + a cached causal summary, with conservative-editing constraints and a 3-way failure-attribution rubric (skill/agent/environment) baked into refiner+critic prompts. The refiner also gets an **experience directory** — raw filesystem access to prior proposals' diffs, verdicts, and run journals (measured +7.7pts vs compressed-feedback optimizers), with Pareto (score, context-cost) secondary selection via TokenJuice projection. Optionally a **teacher/student split**: a frontier model reads local run traces READ-ONLY, diagnoses 2-5 failure clusters (student_failure_rate, teacher_success_rate, skill_gap), and proposes typed edits — while execution stays local-first.
- **SHAPE:** `template_diff` proposals are a schema-constrained list of the engine's own typed mutation ops (add/remove/reorder node, adapt-parameter, add-gate, add-retry — the edit-op format extends to memory/lesson upsert/delete + key + reason with anti-bloat doctrine), validated against past successful ledger outputs before surfacing, so accepted diffs are machine-applicable and the Versions diff view renders structured diffs against version-tagged snapshots. **Risk tiers (batch-5):** deterministic risk-tier assignment by edit TYPE (routing/params/tools = low-risk; prompts/few-shot = review-worthy; anything destructive = manual-only) stamped on every typed-op proposal — used ONLY as Proposal Inbox metadata for ordering/filtering/bulk-accept ergonomics, NEVER as an auto-apply lane (any "auto" tier is guardrail-violating; the human-installs invariant is absolute). **Skill application substrate (batch-5):** accepted SKILL proposals apply as sidecar overlays — a separate fault-tolerant overlay file (metadata + few-shot exemplars) overlays the base skill at load time and never mutates it; revert = delete one file. This gives skills the same trivial-rollback property templates get from version pinning, and keeps `install_guarded`'s `.pclaw-lock.json` hash locks intact.
- **EVIDENCE:** every proposal carries an evidence manifest ({value, metric, measured_at, run_ids} + evaluating model + confidence); full decision provenance for judged steps (prompt, raw response, parse status, tokens, latency) persists in the ledger so diffs are falsifiable. Templates may embed `eval_prompts`/`output_contract` metadata so the critic scores against the template's own rubric.

**Acceptance discipline (LEARN-R2) — what keeps the refiner from random-walking templates under judge noise:**
- **Critic scoring = median of 3 LLMJudge runs with an epsilon margin** (accept only if the median beats the current version by >0.05) — single-run judge acceptance is provably indistinguishable from noise. Four named check scores per diff (grounded_in_evidence, preserves_existing_value, specificity_and_reusability, safe_to_publish) with **reject-by-default on LLM failure** (LLMJudge's parse-failure→0 already gives this property).
- **Held-out replay gate / GateOK:** keep a small set of past successful runs per template; refuse any diff whose replay/judge score regresses on them. Machine-checkable form (batch-5): an accepted edit must improve its target failure cluster AND every other cluster may regress at most eps (default 1%), scored on a held-out subsample; session-level stop rules (gate-score stagnation k=5/eps=0.001, cost-budget exhaustion) and a minimum-improvement accept/reject floor (0.02) per learning cycle. GateOK runs as a pre-surfacing gate like the critic — sub-threshold diffs dropped silently; it never bypasses human accept. **This is the lightweight replacement that lets §3.4's eval-CI stay deferred safely.** The **recipes pattern** feeds it: one template artifact compiles via pure functions into BOTH the runnable spec and its own eval suite, giving the gate a template-specific benchmark without CI machinery.
- **Frozen-region invariant:** the refiner may mutate step prompts/retries/gates but NEVER template id, triggers, or surfacing metadata (routing-drift prevention).
- **Power discipline:** no template-diff proposal from fewer than N runs' evidence; diffs judged on consistency improvement over k runs (RP@k-style), not single-run pass. Triad generation (conservative/moderate/wild revisions on a rolling leaderboard) is an optional widening once the floor works; feed each optimization round the LATEST eval report, not the baseline (stale-feedback fix).
- **Artifacts:** append-only history.json/rejected.json beside template versions; **per-version evidence records** (motivating run ids, sections preserved vs changed, retrospective on prior versions) the refiner must read before proposing. Every applied diff is individually revertible (per-edit commit-or-rollback — versions are the checkpoint store).
- **Canary auto-revert:** compare the next N runs against the prior version; on quality regression, auto-FILE a demotion proposal (through the queue — never auto-revert silently). A **harvested regression suite** grows organically from previously-failing-now-passing runs — the organic answer to the golden-run objection.
- **Bootstrap sentinel:** any refiner/detector-GENERATED evaluation artifact (held-out fixtures, §3.2 spec-union drafts) carries a PENDING_REVIEW sentinel the human must clear on accept — prevents self-referential benchmark gaming.

**Change-manifest attribution (LEARN-R16) — predict-then-verify:** after N post-acceptance runs, the curator computes fixed[]/regressed[] deltas from Run Ledger outcomes and scores each accepted change with a 5-way verdict: EFFECTIVE / PARTIALLY_EFFECTIVE / INEFFECTIVE / MIXED / HARMFUL — plus `unattributed_regressions` (regressions nobody predicted, the scariest class, surfaced loudly). HARMFUL verdicts auto-generate revert proposals through the queue, making version-pin rollback mechanical instead of requiring user vigilance. Verdict history per proposal source (refiner / §3.2 detectors / user) becomes the trust signal feeding template maturity and refiner calibration — the flywheel learns which of its own proposers to believe.

**Judge health pass (LEARN-R10):** (a) flag any gate, judge, or verify step with an anomalous 100% pass rate over N runs (a check that has never rejected is not a check) and emit calibration template-diff proposals (stance tightening, model swap, act-don't-read verify grants); audit verify-step QUALITY, not just pass/fail (green-but-empty verification); (b) bias refiner proposal energy toward judge/gate improvements over generator prompt tweaks — a modest generator with a sharp judge is what compounds; (c) log a first-class **judge-divergence event** whenever the user overrides or rejects a judge-passed deliverable; divergence accumulation becomes proposed judge-prompt diffs through the queue; (d) record (predicted judge confidence, ground-truth outcome) pairs per template in the ledger NOW; report MAE per bucket in the flywheel health view, applying correction only once volume justifies.

**Template maturity L0-L3 (LEARN-R11):** computed from (a) static spec signals — has verifier/gate node, escalation path, budget block, attempt caps, stop conditions — and (b) demonstrated ledger activity: clean-run count, consistency over k runs, first-try-valid parameterization rate, per-(template × executor/model) outcome aggregates, and "the evaluator has rejected at least one real bad run." Stored as template metadata the planner reads to gate which autonomy modes it may OFFER (new template defaults to report-only/per-stage; unattended requires L3) — **the flywheel computes the level; UNIVERSAL-PLANNING owns mode-switching.** Refiner proposals that add a missing signal raise the level, giving the evaluator-optimizer a concrete numeric target; a >30% gate false-positive rate auto-proposes a tightening diff.

### 3.2 Repeated ad-hoc work → suggested templates

The three detectors, now run through ONE deterministic gate chain (LEARN-R13) instead of pure LLM-prompt branches:

1. **Hard pre-gates:** plan ≥2 steps; no existing template already surfaced for the run; budget burn ≤80% (near-death plans make bad templates).
2. **Deterministic structural score:** action-verb diversity, inter-step deps, parameterizable slots, −1 per hardcoded entity.
3. **LLM consulted ONLY at the score boundary**, on a cheap model; high scores auto-FILE a proposal with zero LLM calls (filing, not installing — the human accept invariant is untouched).
4. **`skipped(reason)` ledger events for every negative decision** — the flywheel's negative space is how thresholds get tuned.

Signal sources feeding the chain:
- **Session-shape detector:** the skill-ladder review (`run_skill_ladder_review`) gains a fifth branch — if the detected reusable procedure is ordered/multi-stage/side-effecting, propose a TEMPLATE (v2 spec skeleton) instead of a skill. One prompt change + one proposal kind; cheapest win.
- **Plan-similarity detector:** every planner-produced spec gets an embedding; ≥0.85 cosine to ≥2 prior ad-hoc specs in 30 days → "you've built this three times — save as template?" (draft = union of the specs, PENDING_REVIEW-sentineled per §3.1). Batch `subagent_run` compiles (WORK-CONTAINERS) feed the same detector.
- **Registry-miss events:** when an agent queries for a matching skill/template, finds none, and executes ad-hoc, log the miss — a higher-precision "gap in the library" signal.
- **Intent mining:** grep/embed over run `intent` fields as the cheap corpus for the repeated-plan detector; plus intent-inversion — after each WorkflowRun, a cheap pass synthesizes a canonical 120-200-word user-register intent from (goal + node names + summary), embeds and clusters it against prior run-intents; ≥k near-duplicates without a matching template emits a template-suggestion proposal carrying the synthesized intent as match/description text.
- **Positive-path trace mining (batch-5):** scan the Run Ledger for recurring SUCCESSFUL tool sequences, gated by min_frequency AND min_outcome quality — mining what already WORKS, complementing the gap-shaped signals; zero LLM cost, tunable via the same `skipped(reason)` observability; candidates land as session-scoped drafts feeding the queue.
- **Repeated-query branch:** same-shape one-shot questions recurring across days propose "convert to a standing view/template?" through the same chain.
- **Grill → template:** grill's dormant `SaveFn` wired — tree output IS a template skeleton (session-scoped draft + proposal); settled flat decisions → lessons. (Also referenced in UNIVERSAL-PLANNING; implemented once, here.)

Auto-captured templates land session-scoped (the existing capture ladder is the landing zone), sweep with the session unless promoted (§2.3's multi-gate).

### 3.3 Failed stages → lessons + procedural priors — typed, checkable, outcome-grounded

On `step_failed` (post retry-exhaustion):
- `write_lesson(source="workflow_run")` **through the proposal queue** and the env-failure deny-filter (`is_environment_failure_claim` — a flaky network is not a lesson).
- `record_procedural(tool="workflow:<template>/<step>", outcome=failed)` — the existing ≥3-failure synthesis works over template steps for free; the prior surfaces next time the template is planned.
- Project scoping (Jules pattern): captured lessons/facets default to the run's project scope, graduating to global via the existing heat gate (`promote_by_heat` — the only path to scope=global, verified).

**Typed failure data (LEARN-R8):**
- (a) A **failure-mode enum** (schema_violation / constraint_violation / env / timeout / spec-mismatch + the RCA taxonomy seed code/config/data/infra/dependency/process) becomes a first-class Run Ledger dimension; the refiner computes `failure_distribution()` per template and targets the dominant mode. Rubric ruling: work that would be reverted counts as FAILED, not rework — splitting failure post-mortems from rework lessons.
- (b) Failed-stage lessons are stored keyed by (template, failure_mode) with a collapsed deduplicated signature — and are **re-INJECTED as correction notes on future runs of that template** (a lesson IS a persistent mutation hint).
- (c) The lesson entity gains an optional **machine-checkable form**: `applies_to` scope, invariant statement, `check_command`/required_tests — with the curator proposing promotion to an executable gate/lint after N recurrences ("capture taste once, enforce continuously"). User-stated rules become a named-rule subtype cited BY NAME when a proposed action violates them. A negative-result outcome kind ("tried X, measured no effect") preserves measurements.
- (d) Reproducible stage failures propose a **failure CAPSULE** (repro command + failure_signature + forbidden_success_modes + bounded evidence) instead of prose; later replays verify the lesson still applies — replay outcome is the lesson's decay signal (and the lessons-exempt usage-store gap from §2.3 gets its lifecycle signal here).
- (e) Where a lesson amends a known skill/template, the proposal offers "append to the owner's `<common_mistakes>` section" as a merge action, not only a floating lesson entity.
- (f) `run_feedback` with a defect report generates BOTH a lesson proposal AND a template-diff appending a verify check to the originating template.

**Pending→resolved outcome lifecycle (LEARN-R18):** decision-producing workflow runs journal a `pending_outcome` entry {subject, metric, horizon, baseline} into the Run Ledger at decision time. A resolver (background one-shot on the curator tick, after the horizon elapses) measures ground truth against the baseline, computes a benchmark-relative score, and only THEN invokes the lesson-writer with the strict format: 2-4 sentences citing the measured figure and the run that produced the decision — outcome-grounded reflection beats at-decision-time self-assessment, which is systematically overconfident. Pending entries are exempt from retention eviction (open questions). Surfacing uses §2.4's same-subject/cross-subject tiers under one "Lessons from prior decisions and outcomes" header. Failed-to-measure outcomes (metric unavailable after horizon) become a specific "inconclusive" resolution that decays faster than measured lessons.

### 3.4 Eval regression gate — CONTINGENT (review demotion, now safely so)

Golden runs + materialized eval scenarios + async acceptance is CI/CD machinery sized for a team. **Build only if accepted refiner diffs regress templates in practice** — and §3.1's held-out replay gate + canary auto-revert + harvested regression suite + change-manifest attribution now cover the risk that made this demotion feel hopeful rather than safe. Precondition if built: `eval/runner.py`'s process-global `PERSONALCLAW_WORKSPACE` env mutation (verified, :216 — not concurrency-safe) must move to subprocess isolation first (a live-gateway hazard). Until then, template-diff acceptance relies on the critic + GateOK + the version-pin rollback + HARMFUL auto-revert proposals.

### 3.5 Trajectory-variance tier migration — agentic ↔ fixed (LEARN-R17)

A two-way `tier_migration` proposal class over the Run Ledger, distinct from §1's promotion ladder (which is about entity KIND; this is execution TIER within the template entity):

- **Agentic → fixed (distill):** low-variance agentic templates — the agent follows the same steps in the same order across N runs with negligible branching — trigger a proposal to DISTILL into a fixed deterministic workflow template (cheaper execution tier, no LLM cost for those steps).
- **Fixed → agentic (promote):** repeatedly-failing deterministic steps (≥M failures on the same step across K runs) trigger a proposal to PROMOTE that step to an agentic stage (the rigid step cannot handle the domain's variance).
- Tier-migration proposals carry **cost estimates as evidence** (projected LLM savings from distillation, projected reliability gain from promotion) — addressing the measured ~5× cost difference between agentic and deterministic execution. Detection is pure ledger statistics (zero LLM); drafts are PENDING_REVIEW-sentineled; the human installs.

---

## 4. Disposition Table

| Item | Verdict |
|---|---|
| `skills/proposals.py` | **GENERALIZE** → `learning/proposals.py` (6 kinds) + decision memory/fingerprints/rejection exemplars/resolve cascade (R1) + ratchet/retirement (R9) + change manifests (R16) |
| Two surfacing engines | **MERGE** → `learning/surfacing.py` with per-entity threshold profiles + the R7 slot allocator/authority doctrine |
| `skills/usage.py` sidecar (`.usage.json`) + curator | **GENERALIZE** (learning.db; per-entity semantics; lessons exempt) + R6 hardening (provenance scoping, WAL undo, guards, mode-scoped windowed sweeps); curator explicitly wired to the consolidation maintenance cadence (recon: `run_aging` has no scheduled caller today) |
| `after_turn_review.should_review` double-compute + ungated facet capture | **UNIFY** into the new LearningGate module (recon: no LearningGate class exists — this is the fix, both chat_runner sites + the :150 facet gap) |
| Lesson-contradiction judge | **WRAP** over eval LLMJudge now (reject-by-default on parse failure is already its behavior) |
| `loop/judge.py` | **DO NOT touch here** — its contract survives via LOOPS-EVOLUTION's gates |
| Three decay models | **ONE kernel, three profiles** with R6f's concrete formulas (memory-heat migration is a real small change; strength gates eviction only, never surfacing rank) |
| `plan_memory/` silo + `plan_lessons.md` | **ABSORB** into Run Ledger + seed skill; delete + remove from `portability.py` export list |
| `lessons.jsonl` LessonStore | **REROUTE CONSUMERS — step 2, not step 1** (recon-corrected: lessons are ALREADY primary in memory.db `lesson.*` via `write_lesson`; the live box has no lessons.jsonl and memory.db holds the corpus). Work = reroute the `/api/lessons` contract's three consumers (`mcp_memory` tools over HTTP, the dashboard backing in handlers/schedule.py, the no-embedder write fallback in context.py) onto memory.db + import any residual JSONL + verify embedder-less writes; a real consumer migration with regression risk, not a data migration |
| Consolidation lessons live-write | **CHANGE to proposal** (closes the injection hole) |
| Measure (§2.5) | **UN-DEMOTED** from a bare pointer to a v2-independent floor (R4/R15) — surfacing_events + mechanical "used" + near-miss ledger |
| Stats approve/deny counters → procedural priors | **RE-SPEC'd**: today's counters carry NO tool identity and no persistence. The wire is "add per-tool identity to approval stats, THEN feed" — a real change, priced into step 9, or dropped if not worth it |
| Preference facets, ephemeral skills, consolidation envelope, engagement signals, memory core, contradiction-supersede | **KEEP as-is** (the healthy organs); voice aspects (R20) and the self-model (R21) land as new memory sub-kinds beside them, never in knowledge.db |

---

## 5. Chat Tools

Keep the explicit-capture trio unchanged — `memory_remember` (the actual tool name; lessons ride it over HTTP `/api/lessons`), `skill_remember`, and template capture via `workflow_author(save: true)` — routing rules (including the R20 voice-aspect split) go in their descriptions. Add three:

| Tool | Description |
|---|---|
| `learning_review(action?, ids?)` | List/accept/reject pending proposals in-chat. **INVARIANT: accept/reject is ALWAYS human-elicitation-gated — never auto-approvable, exempt from trust mode and allowlists.** This is what keeps "the model never installs its own proposals" true; without it, propose-don't-write is theater. R3's risk tiers order the list; rejects feed R1's exemplar store |
| `run_feedback(run_id, comment)` | Attach a user comment to a run's ledger — the richest refiner input (fenced before the refiner reads it, §3.1); a defect report also triggers §3.3f's dual proposal |
| `template_save_from_session()` | Explicit "turn what we just did into a template": renders the session's tool/stage trace into a draft spec, opens as a proposal |

Deliberately NOT added: a `learn` mega-tool, template auto-run-on-accept, any model-driven proposal installation, any auto-apply risk tier.

---

## 6. FE — One Learning Page + Extensions

1. **Learning page** (evolves the Skills page's proposal section): unified Proposal Inbox across all six kinds — provenance excerpt, evidence manifest, reinforcement count, `manifest_valid` flag (R16), risk-tier metadata for ordering/filter/bulk-accept (R3, metadata only — no auto lane), one-tap accept/reject, filter by kind/source. Below: the artifact ledger (every lesson/skill/auto-template with usage sparkline where meaningful, age state, pin/forget/edit — markdown-editable, artifacts are files).
2. **Flywheel observability panel (LEARN-R14b)** on the Learning page: capture/consolidation pipeline counts (candidates, grounded, promoted today) backed by R19's staging outcome records; per-cadence schedules with next-run; recent signals with provenance; confirm-gated maintenance verbs (dedupe / repair / undo-last-sweep) surfacing R6's WAL; staleness as a fresh→amber→red elapsed-time gradient with per-entity usage stats (information, not guilt); near-miss patterns (R15); per-op LLM cost aggregates (R19e); the flywheel health composite (0-100, 50-80% budget-utilization ideal band) with judge-calibration MAE buckets (R10d) and attribution verdict history (R16).
3. **Surfaced-entity chips (LEARN-R14a):** the surfacing engine's composer widget is a count-badged popover of toggleable chips — each surfaced skill/lesson/template/facet/voice block is a chip with hover-card preview and per-item on/off; toggling off writes a mute/not-helpful event into the usage store (feeding §2.5's mechanical Measure), and repeated mutes become curator input. Response-provenance tags (R4d) render here.
4. **Template detail** additions: Versions tab (structured typed-op diff view, re-pin/rollback, per-version evidence records), Run Ledger tab, maturity level badge (R11), "Refine now" button. (Golden-run star + regression toggle only if §3.4 is ever built.)
5. **Run detail:** "Learned from this run" chips linking to generated proposals; gate-rejection comments visibly feed the ledger; pending-outcome entries (R18) shown as awaiting-measurement.
6. **Chat:** "Learned: …" activity chips extended to run-end captures; proposal-count badge.

---

## 7. Migration Order (risk-ascending; 1-4 are v2-independent)

1. **Hygiene + gate + staging:** extract the LearningGate module (unify the two `should_review` computations at chat_runner.py:158/:217 AND route the currently-ungated facet capture at :150 through it); `capture_hygiene.py` (R5: system-injection filter, grounding gate, session scoring, notability/verbatim/log rules, `min_evidence` constant, pre-compaction flush; normalize the :260 inline fence); the R19 staging tier + outcome records + learning.db bootstrap (with snapshot/portability coverage); delete dead chat plan-mode (with UNIVERSAL-PLANNING); `context_management.py` split. *(No lessons.jsonl deletion here — see step 2.)*
2. **Lesson-store consumer reroute** (the re-tiered, recon-corrected step): reroute the `/api/lessons` consumers (`mcp_memory` tools, dashboard backing in handlers/schedule.py, no-embedder fallback in context.py) onto memory.db `lesson.*`; import residual JSONL where present; verify embedder-less writes; THEN delete `lessons.jsonl`.
3. **Proposal queue generalization** (R1 decision memory: fingerprints, rejection exemplars, reinforce/specializes, resolve cascade, quota; R9 ratchet + retirement kind + provenance weighting; R16 change-manifest schema; extract→decide two-phase capture; R20 voice-aspect routing + `user.voice.*` allowlisting) + consolidation-lessons→proposal policy + Proposal Inbox FE + SEL audit of accepts.
4. **Surfacing merge + Measure floor:** the R7 slot allocator (per-entity profiles, tiered rendering, position/authority doctrine, one budget) + R12 schema axes (invocation axis, precondition gates, glob auto-attach, description lint at proposal-acceptance and write time) + usage store + R4 surfacing_events/mechanical-used/Beta-Binomial trust + R15 near-miss ledger + R14a chips + R6-hardened curator over templates (wired to the consolidation maintenance cadence).
5. **Run-end capture** (needs v2 Slices 0-3): Run Ledger consumption through the LearningGate, R8 typed failure signatures + capsules + checkable lessons, failed-stage→lesson/procedural, R18 pending-outcome lifecycle, plan-memory absorption + deletion (+ portability list).
6. **Template refiner** (evaluator-optimizer as a trigger-fired workflow): R3 trust/tiers/typed-op shape/evidence manifests/experience directory + R2 acceptance discipline (median-of-3, GateOK held-out replay, frozen region, canary revert proposals, harvested suite, bootstrap sentinels) + versioning + R16 attribution verdicts + R10 judge health pass + R11 maturity levels + Versions/Ledger FE tabs + skill sidecar overlays.
7. **Ad-hoc→template detectors** (R13 gate chain + all signal sources incl. positive-path trace mining) + grill wiring + `template_save_from_session` + R17 tier-migration detector.
8. **Self-model (R21):** observer, reinforcement thresholds, capped artifact under `user.selfmodel.*`, allocator slot, queue integration.
9. **Polish tier:** heat-earned promotion multi-gate; decay-kernel consolidation (R6f formulas); R14b observability panel completion (health composite, MAE buckets, cost aggregates); per-tool approval identity + procedural wire (or drop); intent-adaptive weight profiles + ablation-delta sweeps. Eval regression gate: contingent, only on demonstrated need (with the eval-runner env-isolation precondition).

## Implementation Effort

- **11 sessions** (1-4: ~4.5 sessions, v2-independent — the rev-2 scope lands mostly here and in step 6; 5-9: ~6.5 sessions, v2-coupled). Was 6 sessions in rev 1; the added ~5 are decision memory + acceptance discipline + Measure floor + curator hardening + attribution/maturity/tier-migration + self-model/voice — each individually small-to-medium, all riding the same lifecycle machine rather than adding new systems.

## Success Criteria

1. One Proposal Inbox shows all six proposal kinds with provenance, evidence manifests, and risk-tier metadata; accept installs, reject dismisses — and the model cannot accept its own proposals under any trust mode (tool-set scoping enforced).
2. A rejected proposal is never re-filed: refiling the same content is a silent skip (fingerprint) or a reinforcement of a pending row — verified by replaying a rejected diff's inputs.
3. A template that fails the same stage twice generates a defensible template-diff proposal citing typed ledger evidence — and an accepted diff must pass the median-of-3 critic AND the held-out replay gate; a diff that regresses held-out runs is dropped silently.
4. Content inside `fence_untrusted` provably never becomes a lesson/skill/template — and the adversarial test covers the REFINER path: injection planted in a run transcript or `run_feedback` comment must not surface as a proposal (let alone an accepted diff).
5. The lesson block, skill INDEX, template suggestion, voice/facet blocks, and self-model snapshot fit one per-turn slot-allocated token budget; lessons are never crowded out (sacrificial-slot truncation only); the authority preamble renders.
6. The `/api/lessons` consumers (MCP tools, dashboard, no-embedder path) work identically after the consumer reroute onto memory.db.
7. Measure answers "is the flywheel working" without §3.4: per-arm surfaced-vs-used precision is reportable per entity kind, threshold profiles are tunable from data, and a muted chip visibly lowers an entity's trust posterior.
8. The staging tier makes silent capture failure impossible: every extraction pass leaves a FLUSH_OK / FLUSH_ERROR / proposal-id outcome record, and the observability panel shows a week of pipeline activity at a glance.
9. An accepted change is accountable: after N runs it carries an EFFECTIVE…HARMFUL verdict computed from ledger outcomes, and a HARMFUL verdict has auto-filed a revert proposal.
10. Preference-facet/voice capture is gated: an incognito or temporary session writes NO memory-side artifact through any cadence (the chat_runner:150 gap is closed and regression-tested).

---

## Amendment (2026-07-29 — owner-approved: skill resource tier, agentic authoring, and approval-gated write-back)

**Provenance.** A gap analysis (2026-07-28/29) plus a code audit surfaced three items the owner approved for planning. **Two of the three are narrower than the framing implied**, and this amendment says so rather than restating the pitch — §2.4 already owns a more sophisticated version of one of them.

### (a) Skills progressive disclosure — we have 2 of 3 levels; only the RESOURCE tier is missing

**A known three-level load model** (documented in the wider ecosystem): L1 metadata (name+description, always loaded, ~100 tokens/skill), L2 instructions (the `SKILL.md` body, loaded on slash-command trigger, <5k tokens), L3 resources (scripts, reference files, assets, loaded only when referenced). It builds on **Anthropic's Agent Skills open standard** (`SKILL.md` + YAML frontmatter) rather than inventing a format.

**What PersonalClaw already has — the framing "we have 1 of 3" was wrong:**
- **L1 exists.** `SkillsLoader.get_context()` documents it in its own docstring: "Always-loaded skills: full content included. Other skills: summary with instruction to load via bash when needed." Surfacing is embedding-ranked with a keyword-union fallback (`skills/surfacing.py`, threshold 0.55, mtime+model-keyed embedding cache in `.skill_embeddings.json`), capped by `skills.max_triggered`.
- **L2 exists.** `skill_invoke` loads the full body on demand (`mcp_core.py:575` → `loader.load_skill(name)`, frontmatter stripped, usage recorded so the curator sees on-demand invocations), and its tool description explicitly steers the model to prefer it over reading the file.
- **L3 does NOT exist.** A skill's bundled scripts/reference files/assets have no addressable, on-demand load path — the agent must discover and read them as ordinary files, which means they are either absent from consideration or pulled in wholesale.
- **§2.4 of this plan already designs something stronger than that fixed three-level model:** persisted L0/L1/L2 tiers per entity, a *ranked slot allocator* that degrades tier before dropping items, L2 gated at ≥0.9·top_score with a hard cap of ~3 full-detail items, and an L0 catalog of unloaded near-misses. **Do not replace that design with the simpler one.** This amendment adds the missing resource tier *underneath* it.

**The contract addition** (an executor must implement exactly this, not a parallel scheme):

```
A skill directory may carry resources beside SKILL.md:
  ~/.personalclaw/skills/<name>/SKILL.md
                              /scripts/*        # executable helpers
                              /reference/*      # docs the skill cites
Frontmatter gains an optional declaration so resources are addressable without a directory walk:
  resources:
    - path: reference/api-notes.md
      description: field-by-field notes on the vendor payload
```
- `skill_invoke` returns the body **plus an L0 catalog of declared resources** (path + one-line description) — never their contents.
- A new `skill_resource(skill, path)` tool loads ONE declared resource on demand. It resolves **only** against the declared list (no arbitrary path read), rejects traversal, and is size-capped with a truncate-with-notice — the same discipline `investigate.py`'s snapshot cap uses.
- Resource loads are **usage-recorded** like `skill_invoke`, so the curator (§2.3) can age unused resources.
- **Security note (load-bearing):** resources are agent- and third-party-authored content. A resource load routes through the same fencing discipline as any untrusted read, and a *script* resource is never executed by this tool — `skill_resource` reads, it does not run. Execution stays on the normal, denylist-screened, sandbox-wrapped command path.

**Format alignment (owner decision, §Owner tasks):** the wider ecosystem has converged on Anthropic's `SKILL.md` + YAML-frontmatter standard, and PersonalClaw already uses `SKILL.md` with YAML frontmatter (`skills/loader.py:65` serializes frontmatter lines; `_parse_frontmatter` reads them). Worth an explicit conformance check so imported third-party skills work unmodified — cheap interoperability on a format we already substantially share.

### (b) Agent-authored + retroactively-promoted skills — check before building

**What exists:** `skill_remember` (`mcp_core.py`) already captures a user-taught skill as a **session-live draft** — active immediately for the rest of the chat, with an end-of-chat prompt to save permanently (to this agent or all agents) or forget. `skills/proposals.py` provides the full propose/accept/reject queue (`enqueue`/`list_pending`/`accept`/`reject`, with `kind="new"` and a `refine_target` for refinements). `skills/curator.py` ages `auto/` skills active→stale→archived by last use. `skills/ephemeral.py` exists for the session-live tier.

**So the gap is not "agent can't author a skill" — it is narrower:**
1. **Retroactive promotion of a completed run/conversation** into a skill. This is a common pattern ("save this process as a Skill" after a successful task); PersonalClaw captures at the moment the user *teaches*, not retrospectively from *what worked*. §3.2 of this plan ("repeated ad-hoc work → suggested templates") is the adjacent mechanism and the natural home — promotion should feed **its** proposal queue rather than a second path.
2. **Agent-initiated authoring** (the agent proposing a skill unprompted, having noticed it repeated itself) — must land as a **proposal**, never a silent write, per this plan's propose-don't-write doctrine.
3. **GitHub-repo import** of a skill (a community-distribution path). Note the existing install rail is not bypassable: skills install through the supply-chain scanner at the source's trust tier (`skills/marketplace.py:117` — "payload to quarantine, scans it at this marketplace's trust tier, and commits the exact scanned bytes"), with a **non-overridable DANGEROUS floor**. Any import path must ride that rail, unchanged.

### (c) Self-updating project context with an approval gate

**A known pattern:** you ask the assistant to review a conversation; it proposes updates to the project's instructions, files, and skills, explains what and why, and **project context is not updated without authorization.** Prompt-triggered, not automatic — notably, this pattern has no automatic cross-session personal memory that learns unasked.

**Why it fits here cleanly:** this is exactly this plan's `Capture → Stage → Propose → Curate` pipeline (§2), with the "project" as the target entity. It also matches the platform's existing discipline elsewhere: `memory_lint.py` has **exactly one** auto-fix (purging superseded rows past 90 days) and everything judgmental is **flagged, not changed**; `memory_service` uses **supersession-by-pointer** rather than lossy deletion. The mechanism is already the house style — what's missing is the project-scoped target and the review-this-conversation entry point.

**Contract:** a `project_context_review` capability that (1) reads a conversation or run, (2) emits proposals of kind `project_instruction` / `project_file` / `project_skill` into the existing §2.2 queue with a rationale per item, (3) **writes nothing** until accepted, and (4) records the decision in decision memory (§2.2) so the same rejected suggestion doesn't re-surface. Prompt-triggered by default; if a cadence is added later it must remain propose-only.

### Amendment task table (extends this plan; run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| E1.1 | Skill resource tier: optional `resources:` frontmatter declaration; `skill_invoke` returns body + an L0 resource catalog (never contents); new `skill_resource(skill, path)` tool resolving ONLY declared paths, traversal-rejecting, size-capped with truncate-notice, usage-recorded, read-never-execute | `src/personalclaw/skills/loader.py`, `src/personalclaw/mcp_core.py`, tests | a skill with resources exposes a catalog at ~one line each; loading one returns just that file; an undeclared or traversal path is refused; an oversized resource truncates with a visible notice; a script resource is read, never run |
| E1.2 | `SKILL.md` frontmatter conformance check against the Agent Skills standard (per the owner's ruling in Owner tasks): document the delta, and accept a conformant third-party skill unmodified through the EXISTING scanner rail | `skills/loader.py`, `docs/reference/`, tests | a standard-conformant skill imports and runs unmodified; the scanner rail is unchanged (DANGEROUS floor still non-overridable — test it) |
| E1.3 | Verify-then-build promotion: audit what `skill_remember` + `proposals.py` + §3.2 already cover, then add ONLY the missing retroactive path — "promote this completed run/conversation into a skill" feeding §3.2's existing proposal queue (no second queue) | `skills/proposals.py`, the §3.2 seam, tests | a successful run can be promoted to a skill proposal; the agent may propose unprompted but never writes; a rejected proposal is remembered in decision memory and does not re-surface |
| E1.4 | `project_context_review`: read a conversation/run → typed proposals (`project_instruction`/`project_file`/`project_skill`) with per-item rationale into the §2.2 queue; nothing written without acceptance; decision recorded | the learning pipeline modules, tests | reviewing a conversation yields reviewable proposals; declining changes nothing; accepting applies exactly the accepted items; a second review does not re-propose a rejected item |
| VE | Validation as a user: author a skill with a script + reference resource, confirm the catalog appears and one resource loads on demand while the other does not; import a third-party standard-format skill and confirm the scanner gate still applies; complete a real task, promote it to a skill proposal, accept it, and confirm it surfaces on a later relevant turn; run a project-context review, decline it (verify no change), then accept it (verify exactly the accepted items applied); full local gate | — | holds |

### Owner tasks (real world)
1. **Rule on `SKILL.md` standard conformance** (blocks E1.2): adopt the Agent Skills standard's frontmatter keys exactly where they differ from ours, or document the delta and stay divergent. The wider ecosystem has adopted it; the interoperability upside is that third-party skills work unmodified, and `anthropics/skills` is where the community publishes.
2. **Confirm prompt-triggered-only for (c).** The plan makes project-context review manual. A cadence could be added later, but must stay propose-only.

### Risks
- **Duplicating §2.4.** The single largest risk in this amendment. §2.4's ranked slot allocator with L0/L1/L2 degradation is the authority for *how much of an entity* reaches a prompt; E1.1 adds only a **new, deeper tier for skill resources**. A task that reimplements tiering in `skills/` is out of scope and should be refused (escalation E6).
- **Resource sprawl.** A skill with many resources re-creates the tool-explosion problem one level down. Mitigated by the L0 catalog being one line per resource and by curator aging of unused resources — and worth watching in real use.
- **Promotion noise.** Retroactive promotion could flood the proposal queue. §2.2's decision memory and §2.3's curator already bound this; the amendment deliberately reuses them rather than adding a second gate.

---

## Execution log

- **2026-08-01 — DONE — Migration step 1 (Capture): hygiene + gate + staging.**
  Branch `feature-wf2-flywheel-capture`, PR #163. New `learning/` package with the three
  §2.1 unifications: `gate.py` (one eligibility decision per event, consumed by every
  cadence), `hygiene.py` (untrusted-invisible + system-injection + env-failure +
  grounding + session scoring + the shared `min_evidence` constant), `staging.py` (the
  R19 append-only log with FLUSH_OK/FLUSH_PRODUCED/FLUSH_ERROR outcome records, batch
  gate with input-hash idempotence, cost metering, provenance pointers, health/prune).
  Config: `learning.min_evidence`, `learning.staging_enabled`, `learning.min_session_score`
  wired through all four points and verified live. `learning.db` added to
  snapshot/portability (copy-never-merge). `context_management.py` split; the plan-format
  and plan-memory half extracted to `plan_memory.py`. 11060 tests (+77), lint clean,
  green at `-n 4` and `-n auto`, seam validated against the real dev home.

- **DEVIATION — `should_review` deleted, not deprecated.** The plan says "unify"; after the
  rewire it had zero production callers, and clean-break doctrine says the replaced
  mechanism goes in the same change. Coverage migrated to `test_learning_gate.py`.

- **DEVIATION — permission and worthwhileness are two fields.** The plan describes ONE
  eligibility result. Implementing it as one boolean would have forced the same
  carve-out that made facet capture ungated (a cheap path cannot express "allowed but not
  worth an LLM"). `GateDecision` carries both; `__bool__` is the strict answer.

- **DISCOVERY — the fence filter's first implementation was wrong, and measurement caught
  it.** `fence_untrusted` emits `<untrusted_content source=web>`; a bare-literal match
  found nothing on exactly the spans carrying provenance, so a planted injection survived.
  Now matched with a tag-aware regex derived from the constant. This is the plan's success
  criterion 4 — it would have shipped false.

- **DISCOVERY — ordering is a privacy property.** Permission must be settled before the
  message is read at all; classifying a restricted session's text is already a read of
  content its memory_mode excluded. Pinned by a test.

- **2026-08-01 — DONE — Migration step 3 (Propose): the generalized queue + decision memory.**
  Branch `feature-wf2-flywheel-propose`, PR #164. `learning/proposals.py` with the six
  kinds, content fingerprints, the rejected-exemplar store with escalating cooldowns,
  reinforce-on-duplicate, `specializes` for variants, supersession lineage, the
  deterministic 4-verdict resolve cascade (contradiction BEFORE reinforce), change
  manifests (lenient-but-recording), the evidence floor for inferred proposals only,
  per-run quota from config, and SEL-audited accept/reject. 11112 tests (+52).

- **DISCOVERY — three cascade bugs, all found by measuring rather than reasoning.**
  (1) The subject guard shifted under negation, defeating contradiction detection.
  (2) A genuine contradiction scores 0.80 by token overlap — below the NEW threshold —
  so contradiction had to move off the similarity gate entirely. (3) The number-conflict
  rule judged four unrelated lessons contradictory and collapsed the queue to one row.
  Each of these would have shipped as silent data loss.

- **DISCOVERY — two inbox/store leaks found on the real dev home,** neither visible from
  tests: a superseded proposal kept a PENDING inbox row that could never be acted on,
  and superseded records had no pruning path.

- **DEVIATION — `skills/proposals.py` survives this session.** Its consumers include the
  skills page's approval tab and its accept path writes the `auto/` namespace; the
  replacement UI is step 3's Proposal Inbox. Retiring it here would mean rewriting a live
  surface against a queue with no frontend. The retirement is owned by the Proposal-Inbox
  session, and this is the only dual path this step leaves.

- **NOT DONE (out of scope, needs later steps):** the embedding-similarity prior-rejection
  check (needs the embedder-backed store from step 4's usage tier — the token-overlap
  cascade is the zero-dependency floor and works without an embedder configured), the
  Proposal Inbox FE, and the consolidation-lessons→proposal rerouting (needs the
  `/api/lessons` consumer reroute, which the plan orders as step 2).

- **2026-08-01 — DONE — Migration step 4a (Curate): usage store + decay kernel + hardened curator.**
  Branch `feature-wf2-flywheel-curate`, PR #165. `learning/decay.py` (one kernel with
  per-kind half-lives, importance as a second axis, both-signals pruning, chain sparing,
  the active-days clock), `learning/usage.py` (one store in learning.db, per-entity event
  vocabularies, lessons exempt, reinforcement damping, multi-gate promotion suggestions),
  `learning/curator.py` (bounded batches oldest-audited-first, demote-never-delete with a
  WAL undo journal + dated changelog, provenance scoping, over-deletion refusal, mode
  scoping, decayed-but-stable → REVIEW proposal, the optimizer battery's saving estimates).
  Wired into `history.py`'s consolidation maintenance cadence. 11210 tests (+98).

- **DISCOVERY — the scheduling gap in §2.3 was real and worse than stated.**
  `skills/curator.run_aging` had no scheduled caller anywhere: an entire grooming pass
  existed and had never run. It is now on the verified maintenance tick.

- **DISCOVERY — `DecayVerdict.__bool__` broke its own audit trail.** `if verdict` asked
  "is this healthy?" where the code meant "did I get a verdict?", so every archival
  journaled a null strength — losing the evidence for exactly the mutations most likely
  to be undone. Found by measuring the journal, not by reading the code.

- **DEVIATION — the kernel's base λ is pinned to the facet store's existing 30-day
  half-life** rather than chosen freshly. This kernel replaces `preference_facets.decay`,
  so any other constant would silently rewrite the meaning of every stored facet.

- **DEVIATION — `run_aging` decides, the caller applies.** The plan describes a curator
  that ages entities; implementing it as one that also WRITES them would have coupled it
  to the skills loader again (the exact reason the old one could not generalize). It takes
  value objects and returns a report.

- **NOT DONE (needs step 4b, which owns rank):** the LLM umbrella-consolidation pass, the
  remaining optimizer detectors as filed proposals, and the memory-heat migration onto the
  kernel. `skills/usage.py` / `skills/curator.py` survive with live consumers;
  `import_skill_sidecar` is the idempotent bridge and the sidecar is deliberately left on
  disk until the new store is verified in real use.

- **2026-08-01 — DONE — Migration step 4b (Inject): the ranked slot allocator.**
  Branch `feature-wf2-flywheel-inject`, PR #166. `learning/surfacing.py` with per-entity
  entry gates (calibration preserved), one salience pool, RRF fusion + pre-trim
  diversification, priority slots with exactly one sacrificial slot, tiered rendering that
  degrades before dropping, the L0 near-miss catalogue, the authority preamble, and
  intent-adaptive weight profiles. 11255 tests (+45).

- **PREMISE MISMATCH — `workflows/surfacing.py` does not exist.** Neither does any
  `[SUGGESTED WORKFLOW]` render; the "SOP match_text 0.62" is `agents_routing.min_confidence`.
  There was ONE surfacing engine plus an inline ambient render in `context.py`, not two
  engines. Built the allocator as the owner of that render's policy instead of inventing the
  second engine. `skills/surfacing.py` stays live and unduplicated.

- **DISCOVERY — the crowd-out bug §2.4 predicts was real and measurable.**
  `lessons_ctx[:_LESSONS_CAP]` cut the user's own corrections mid-sentence. Fixed by dropping
  whole lessons with an explicit withheld count; verified 0 partial lessons at every cap.

- **DISCOVERY — the diversification cap rationed lessons** (a 4th dropped with 3588 tokens
  unused), and the authority preamble was added without checking that it fit. Both found by
  driving the real dev home, not by tests.

- **NOT DONE — the allocator does not yet own `build_session_context`'s eight-part assembly.**
  Its policy is applied where corruption was measurable. Replacing the whole render is
  behaviour-visible and needs §2.5's measurement floor to prove nothing stops surfacing;
  doing it blind would swap a working render for an unproven one. L0/L1/L2 persistence per
  entity is likewise deferred — it is a store change across four entity types.

- **NOT DONE (deliberately, out of step-1 scope):** the extract/decide two-phase split and
  the pre-compaction flush both need the proposal queue (step 3) to route into — building
  them now would mean writing against a contract step 3 defines. `lessons.jsonl` deletion
  is step 2 by the plan's own re-tiering. The staging tier is wired to the per-turn cadence;
  session-end and run-end consume the same gate/hygiene/staging API when their steps land.

### S71 — Per-arm precision, threshold tuning from data, Beta-Binomial trust (43 tests) — DONE

§7 criterion 7 has three clauses and all three are now measurements: per-arm surfaced-vs-used precision
is reportable per entity kind, threshold profiles are tunable from data, and a muted chip visibly
lowers an entity's trust posterior. The plan's own reasoning drove the shape — "unenforced 'helpful'
scores stay ornamental forever", so `used` is only ever a mechanically-observed fact.

**Measured before writing.** Two of the three pieces already existed: `learning/surfacing.py` carries
`THRESHOLD_PROFILES` with the deliberate 0.55/0.62 split, and `learning/usage.py` already persists
`surfaced`/`used`/`successes`/`failures` per entity. The gap was the middle — `Candidate` had no `arm`,
so nothing could attribute a surfacing to the path that produced it, and nothing computed a posterior
from counts already on disk. So this session added attribution + statistics and deliberately did NOT
add a second threshold table or a second usage store.

**🔴 A DIVERGENCE I SHIPPED AND THEN CAUGHT BY COMPARING.** `memory_push.ARM_CONFIDENCE` already
existed (`alias`/`exact_name`/`suffix`), with a docstring recording that "how the name was recognised
IS the evidence". My first draft wrote a second table — and it disagreed immediately: `exact_name` at
0.90 where the shipped table says 0.80. Two confidence scales for one arm name is precisely the drift
this program keeps finding. The shipped table is now IMPORTED and the retrieval-only arms
(`exact_title`/`path`/`keyword`/`embedding`) extend it, pinned by
`test_the_shipped_arm_table_is_imported_not_restated`. **DEVIATION:** the plan says `exact_name ~0.9`;
the shipped calibration says 0.8 and wins — a plan number does not override a value already in use.

**🔴 A DEFECT FOUND BY MEASURING `fuse`, NOT BY READING IT.** With two sources finding the SAME entity
at the same rank, RRF ties — and the survivor was whichever source dict happened to be iterated FIRST.
So an entity matched by both exact-name and embedding was attributed to an arm chosen by insertion
order, and the per-arm precision report would credit the wrong path. Fixed by carrying the STRONGEST
arm across the dedup, which is the rule `memory_push` already applies ("being named explicitly once is
not undone by also being a vector neighbour"), compared through `arm_confidence` rather than a second
precedence list.

**A second finding the probe surfaced.** `skill` had a 90%-precision `exact_name` arm and a 16%
`embedding` arm, which average to a healthy-looking 49% — so no threshold moved and the report read as
fine. A kind's threshold genuinely IS one number (it gates the fused score), so the aggregate is the
right input for it; the fix is that the reason now NAMES the spread and points at the weak arm's own
confidence as the actual remedy. `test_a_kind_whose_arms_disagree_sharply_says_so` pins it, and a
one-sample arm cannot trigger the alert (it would otherwise fire forever at 100%).

Decisions, each with the failure it prevents:

- **Beta-Binomial, not a ratio.** A raw used/surfaced ratio says 1.0 after one lucky hit, so a
  brand-new entity would outrank a proven one and one bad turn would condemn a good lesson. Ranking is
  on the LOWER BOUND, so ignorance costs something: a 1-of-1 entity ranks below a 27-of-30 one despite
  a better naive ratio. `precision_ratio()` exposes the naive number deliberately — having it visible
  is what makes the difference legible.
- **`LOWER_BOUND_Z = 1.0`, not 1.96.** At 95% a promising entity with 3 uses ranks below one with 30
  mediocre ones, which stalls the flywheel this is supposed to steer.
- **A mute is a full negative observation**, not a display flag — §7's clause says it must VISIBLY
  lower trust, and anything less makes muting a gesture the numbers ignore. `apply_mute` returns a NEW
  posterior so a caller cannot drop the result and silently lose the change.
- **`INSUFFICIENT` is a distinct verdict from `POOR`.** They demand opposite responses (tighten vs
  collect more), and collapsing them is how a threshold gets tuned on noise. Nothing is tuned below
  `MIN_SAMPLES_FOR_TUNING = 20`.
- **Proposals only, never applied.** §2.5 says recalibration happens "empirically, not by taste" — and
  the corollary is that it also does not happen automatically: the 0.55/0.62 split was calibrated, so
  overwriting it from a week of data would discard a real decision. A test asserts the live table is
  unchanged after a proposal runs. A single proposal moves a threshold at most `MAX_THRESHOLD_STEP`.
- **An unattributed surfacing is charged to the WEAKEST arm, never dropped.** Dropping would make the
  report describe only the instrumented paths while claiming to describe surfacing as a whole.
- **Counts are clamped**, so a `used` larger than `surfaced` (possible for an entity surfaced before
  events existed) cannot produce a negative beta and a nonsense posterior.

- **NOT DONE (by scope):** the `surfacing_events` TABLE and its 90d prune on the curator tick.
  `per_arm_precision`/`build_report` take events as a list precisely so the store is a separate
  concern — the same functions then serve the live report, a backfill over pruned history, and a test.
  The arm is now on `Candidate` for producers to set; wiring each retrieval path to name its own arm
  is per-path work in the modules that own those paths.

### S72 — The capped self-model: reinforcement-promoted, propose-don't-write (42 tests) — DONE

The flywheel's ONLY mechanism that learns from what quietly WORKS — every other cadence learns from
corrections and failures, so this is the one that can notice a habit that keeps succeeding. That
asymmetry is also what makes it worth constraining, and §2.6's three constraints are enforced
MECHANICALLY here rather than by convention.

**🔴 A LEAK MEASURED BEFORE THE MODULE EXISTED.** `user.selfmodel.*` was NOT in
`vector_memory._NON_FACT_KEY_CLAUSE`, so a behavioural principle the harness observed about its OWN
working patterns would have rendered in the user-FACT block — a category error (it is a statement about
the system, not the user) and a leak into a surface it was never meant to reach. The exclusion landed
with this session; `test_the_selfmodel_prefix_is_excluded_from_fact_blocks` is the regression.

Conversely, `user.*` was ALREADY in `_BUILTIN_PREFIXES`, so the plan's "prefix allowlisted" step needed
no change. Measuring both is what kept the session from inventing an allowlist entry it did not need.

The three constraints, and how each is enforced:

1. **Propose, never install.** `build_proposal` returns None for a refused plan, so the cap and the
   thresholds sit ON THE PATH to the queue rather than after it — a caller cannot file by ignoring the
   plan. `test_nothing_in_this_module_writes_memory` asserts the ABSENCE of every write primitive
   against the source, because "never self-installed" is a property no behavioural test can see.
2. **Bounded by construction.** A full tier does not refuse — it names the weakest entry as a
   DISPLACEMENT. Refusing outright would freeze the self-model at its first six principles, so the cap
   would prevent bloat by preventing learning. A newcomer must BEAT the weakest, not tie it, or the
   tier churns between two similar principles forever. `over_cap` catches hand-edited data that
   predates the caps.
3. **Only a compact snapshot injects.** Retrospections are excluded from it entirely (evidence for
   promotion, not guidance for a turn), theories render under an explicit "Unproven" heading, and
   truncation drops whole ENTRIES — half a principle is an instruction whose reader cannot tell it is
   half, the same rule `learning/surfacing.py` applies to lessons. A dangling heading is dropped rather
   than rendered.

Decisions, each with the failure it prevents:

- **Both thresholds are a CONJUNCTION** (`seen_count ≥ 2` AND `confidence ≥ 0.72`). Repetition alone
  promotes a coincidence that happened twice; confidence alone promotes one strongly-felt observation.
- **An ACCEPTED reaction after a FAILED turn is not reinforcement.** The user may have accepted a
  partial answer and moved on; reading that as reinforcement is how a broken habit gets promoted.
- **A correction outweighs an acceptance 2:1.** Being told you were wrong is stronger evidence than not
  being told you were wrong, and a symmetric scale would let a habit that fails a third of the time
  still promote.
- **A NEUTRAL observation counts toward repetition but not confidence.** The pattern did recur;
  pretending otherwise would let an unobservable outcome erase half the threshold.
- **Four facets, not one bag.** They have different lifetimes and different AUTHORITY — collapsing them
  would let a working theory inject with the weight of a rule.
- **`lesson_batch` is reused rather than a new proposal kind minted.** §2.6 says an accepted principle
  is "lessons-shaped", and the existing kind already carries the review UI, the fingerprint dedup, and
  the decision store; a new kind would be a second review surface for one shape of thing. The
  `observed-reinforcement` provenance marker is what distinguishes it at a glance.
- **The fingerprint goes through the shared `proposals.content_fingerprint`.** A second hashing scheme
  would make the self-model the one proposer able to re-file something the user already declined.
- **Evidence on a proposal is bounded to 3 lines.** A proposal a reviewer will not read is not evidence.

`learning.self_model_enabled` is wired through all four config points (dataclass + `_meta`, `load()`,
`to_dict`, `_EDITABLE_CONFIG`) and is live-editable: it is the one learning path that acts on what
WORKED, so a user who finds that presumptuous should be able to stop it without a restart.

- **NOT DONE (by scope):** the observer CALL SITE (what records a turn's route/tools/outcome/reaction
  into the staging log) and the memory read/write of live entries. Both belong to the capture cadence
  and `MemoryService` respectively — this session owns the decisions those call sites will apply, which
  is why every function here is pure. Declined promotions feeding §2.2's rejection exemplars with
  escalating cooldowns needs the rejection-exemplar store from a later step.

### S73 — The refiner's acceptance discipline: cluster → median-of-3 → GateOK (71 tests) — DONE

The flagship spoke, and the one with the most ways to go wrong. §3.1's "acceptance discipline" section
is longer than its mechanism section for a reason: an optimizer editing templates from run outcomes
random-walks them under judge noise unless every gate is strict. This session is that discipline as
pure decisions, so the pipeline that calls a model can be tested without one.

**Measured first — every prerequisite was already in place.** `journal.LEDGER_KINDS` carries all the
events the refiner reads INCLUDING `user_edited_mid_flight`, §3.1's "gold" signal (a repeated identical
hand-fix is the user saying what the template should say). And `mutations.OpKind` is a CLOSED ten-op
vocabulary, so a `template_diff` is expressed in the ENGINE'S OWN terms rather than in a second edit
language that would need its own validator. `test_evidence_kinds_exist_in_the_real_ledger` keeps that
true — a renamed event would starve the refiner silently, and zero failures is indistinguishable from a
healthy template.

The four gates, and the failure each prevents:

- **Failure clustering first, LLM second.** A zero-cost pass groups failures by MECHANISM and ranks by
  frequency × unresolvedness — the product, not the sum, because a frequent failure that self-heals is
  not worth an edit and neither is a permanent one that happened once. Noise-stripping is what makes it
  work: without it every failure carries its own run id, path and duration, so 100 instances of one bug
  cluster into 100 clusters of one and the refiner proposes against a cluster of size 1.
- **The power floor is enforced BEFORE the model tier**, and counts DISTINCT runs — three failures in
  one run is one run's evidence. A proposal built from two runs would be rejected downstream anyway,
  after paying for it.
- **Median of 3 critic runs with an epsilon margin.** A single enthusiastic outlier cannot carry
  acceptance (the whole reason for a median rather than a mean), a short critic pass REJECTS rather than
  falling back to a mean, and a parse failure scores 0 — an LLM with no parseable score has endorsed
  nothing.
- **GateOK.** The target cluster must improve by ≥ 0.02 AND every other cluster may regress by at most
  1%. Both halves are load-bearing: requiring target improvement stops a diff being accepted for a
  coincidental gain elsewhere, and bounding the others stops an edit that fixes one failure by breaking
  two. An UNMEASURED target FAILS rather than scoring 0 — "no evidence" must not read as "no
  regression" — and a cluster the replay stopped scoring counts as a regression, because silence is not
  a pass.
- **The frozen region.** Prompts, retries and gates are editable; `id`/`triggers`/surfacing metadata
  never are — they decide WHEN a template runs, and a self-editing system that can change its own
  trigger conditions drifts without anyone approving the drift. Frozen fields are detected in every
  container (`fields`/`config`/`set`/`patch`/`field`), the run-control ops
  (`rewind`/`run_from`/`fork`/`skip`) are refused as CATEGORY ERRORS rather than risky edits (they act
  on a live run, not a stored template), and an unrecognized op name is checked against the engine's
  own vocabulary so a typo cannot become a silently-ignored no-op inside an accepted diff.

Further decisions:

- **One illegal op rejects the WHOLE diff.** A partially applied diff is a template nobody authored:
  neither what the refiner proposed nor what the user reviewed.
- **`evaluate_diff` runs the gates cheapest-and-most-decisive first**, so a frozen-region op never pays
  for three critic runs. A dropped diff still RECORDS why for the log while staying invisible to the
  user — a review queue full of rejected machine guesses trains people to stop reading it.
- **Risk tier = the RISKIEST op in the diff**, not the average: a destructive delete bundled with four
  parameter tweaks is a destructive diff. There is deliberately **no `AUTO` member** on the enum for a
  caller to reach for — §3.1 says any auto tier is guardrail-violating and the human-installs invariant
  is absolute.
- **A manifest that cannot be checked is an assertion.** `falsifiable` requires run ids + metric +
  `measured_at`; run ids are deduped and bounded to 20. `confidence` is DERIVED from the critic margin
  that actually gated the diff, never self-reported — a self-reported confidence is the same ornamental
  signal §2.5 rejects for helpfulness.
- **`canary_verdict` returns PENDING under 3 runs.** Declaring a diff effective after one run is how a
  lucky run becomes a permanent change. HARMFUL is reachable because §3.1 auto-FILES a revert proposal
  for it — through the queue, never silently.

- **NOT DONE (by scope):** the refiner AGENT itself (the trigger-fired workflow that calls a model over
  the digest, with its `propose_*`-only tool set) and the version store. §3.1 puts the agent on the
  `run-workflow` action provider per AUTOMATION-SUBSTRATE's doctrine, so it is a template plus a
  trigger rather than Python — and it consumes exactly these decisions. Fencing run transcripts before
  the refiner LLM sees them is that call site's responsibility (S69 built the screen it will use). The
  teacher/student split, triad generation, and the experience directory are §3.1's explicit "optional
  widening once the floor works".

### S74 — Detectors (§3.2) + typed failure data (§3.3) (73 tests) — DONE

Two spokes, one discipline: a DETERMINISTIC chain decides and a model is consulted only at the score
boundary. §3.2 replaced "pure LLM-prompt branches" because a model asked "is this template-worthy" costs
a call per candidate and answers unstably.

**🔴 THE GUARDRAIL §3.3 DEPENDS ON WAS CATCHING 1 OF 4.** `is_environment_failure_claim` is the
deny-filter that keeps a flaky network from becoming a durable lesson — and measured against real
failure text it caught **1 of 4**: "connection refused", `ECONNRESET`, and rate-limit noise all passed
straight through. §3.3 routes EVERY `step_failed` through it, so landing that spoke on the shipped
filter would have turned transport noise into permanent lessons that teach the agent to refuse valid
actions. Widened to **12/12** with the false-positive direction checked at **0/9** — a bare `429` had
been filtering the legitimate lesson "the 429 rate limiter config lives in settings.py", so a status
code now only counts with failure context, and `rate limited` (past tense) is a report while "rate
limiter" is ordinary vocabulary. Both directions are pinned by parameterized tests.

**§3.2 — the gate chain.** Free at both extremes, paid only in the middle:

- **Hard pre-gates run first**, cheapest and most decisive: <2 steps (a command, not a procedure), a
  template already surfaced (no library gap), >80% budget burn (§3.2's "near-death plans make bad
  templates" — a run that flailed to its answer teaches the expensive path). A one-step plan never gets
  scored at all.
- **The structural score is deterministic and reproducible** — verb diversity measured against STEP
  COUNT (three verbs across three steps is structure; three across twelve is repetition), back-reference
  dependencies, slot density weighted highest (a plan with no parameterizable slot cannot be reused
  however well-structured it is), −1 per hardcoded entity. Components stay visible because a scalar
  cannot say WHICH signal was weak, and §3.2 tunes thresholds from data.
- **A high score auto-FILES with zero model calls**; a low score is dropped free; only the band between
  costs anything. Filing, not installing — the human-accept invariant is what makes a free auto-file
  safe.
- **Every negative decision names a TYPED skip reason.** §3.2: "the flywheel's negative space is how
  thresholds get tuned". Prose reasons are unfilterable; the counts per reason are what say which gate
  earns its place. `test_every_negative_decision_names_a_typed_reason` asserts the whole set.
- **Plan similarity needs threshold AND window.** A below-threshold match is a different plan; an
  out-of-window match is the same plan from a project that ended — and they get DIFFERENT skip reasons,
  because counting either would propose a template for work nobody does any more.

**§3.3 — typed failure data.**

- **A closed `FailureMode` enum** (schema/constraint/spec-mismatch/timeout/environment + the RCA seed
  code/config/data/infra/dependency/process) makes `failure_distribution()` computable. Prose failure
  text cannot be counted, and a refiner that cannot count cannot choose a target.
- **The environment check wins OUTRIGHT.** A traceback containing `ECONNRESET` classifies as
  environment, not code — a message that is both is still the world's fault, and classifying it as code
  would route it to a lesson.
- **Unmatched text is `UNKNOWN`, never a guess.** Otherwise the distribution silently attributes it to
  whichever mode the pattern list leans toward, and the refiner targets a dominant mode that does not
  exist.
- **`dominant_mode` EXCLUDES what a refiner cannot fix.** Measured on a corpus where environment is the
  biggest bucket (8 of 20): the target is `schema_violation` (5), and an all-environment corpus returns
  `""` rather than the raw top mode. A refiner cannot fix the network, and proposing against it would
  be a diff that cannot work.
- **Lessons are keyed `(template, failure_mode, signature)`** so §3.3b's re-injection can find them —
  §3.3 calls a lesson "a persistent mutation hint", and a hint nobody can look up by template is a note
  in a drawer. The dedupe signature REUSES the refiner's `failure_signature`: two schemes would make a
  clustered failure and its lesson un-joinable, so the refiner would target a cluster whose lesson it
  cannot find.

- **NOT DONE (by scope):** the detector CALL SITES — the fifth `run_skill_ladder_review` branch, the
  per-spec embedding + registry-miss logging, intent inversion, positive-path trace mining, and grill's
  dormant `SaveFn`. Each is a hook in a module that owns its own signal, and all consume these
  decisions. The `skipped(reason)` LEDGER WRITE is likewise the caller's: `Skip` is the vocabulary it
  will use. §3.3's machine-checkable lesson form (`check_command`, failure capsules) needs the lesson
  entity extension, which is a later step's schema change.

### S75 — The Proposal Inbox and the accept gate (48 tests) — DONE

§7's criterion 1 has two halves: one inbox across all six kinds with provenance, evidence manifests and
risk-tier metadata — **and the model cannot accept its own proposals under any trust mode**. The second
is load-bearing, and this session is where it became a control rather than a coincidence.

**🔴 THE HARD INVARIANT WAS HOLDING BY ABSENCE.** `proposals.accept()` takes `(pid, installer=...)` and
NOTHING in it knew who was accepting — no actor, no caller, no trust check. Grepped: no agent tool calls
it today, which is the only reason the invariant held. **One new MCP tool would have removed it
silently, with no test failing.** `require_human` is now called from inside the real `accept()` and
`reject()`, and `test_the_real_accept_refuses_an_agent` drives the actual path rather than the decision
in isolation.

Gate decisions, each with the failure it prevents:

- **The ENGINE is refused too**, not just the agent. S56 gives the engine authority to record a task
  outcome because it OBSERVED the work — that is not authority to install autonomously-authored
  behaviour, and an engine that could accept would make every gate upstream of it decorative.
- **An UNKNOWN actor is denied, never assumed human.** The failure directions are not symmetric: denying
  a human costs one click, while admitting an unrecognized caller is the hole this closes.
- **There is deliberately NO trust parameter.** §7 says "under ANY trust mode", and a gate a mode could
  relax is a gate whose invariant is a default. A test asserts the signature has no
  `trust`/`yolo`/`force`/`override`.
- **Reject is gated too**, for a subtler reason: an agent that could reject would clear its own bad
  proposals from the queue before a human read them, and §2.2's rejection exemplars would silently stop
  accumulating.
- **Filing stays open to all three actors.** The whole design depends on non-human proposers; only the
  DECISION is human-only, and separating `can_file` from `require_human` makes that asymmetry explicit
  rather than implied.
- **`actor` defaults to `user`**, so every existing human-facing caller is unaffected (746 existing tests
  confirm) — a required parameter would have broken them all.
- **A refused decision leaves the row PENDING** and writes a SEL `blocked` row. A blocked self-accept is
  the signal that something calls the wrong path, and it would be invisible if only successes were
  logged. The `Denial` enum keeps `self_accept` distinct from a generic permission denial: the first is
  an incident, the second is a bug.
- **The actor vocabulary is REUSED from S56's verified-done matrix**, which already carries the doctrine
  ("the AGENT is a worker whose self-report is exactly what needs checking"). Two actor enums on one
  machine would eventually disagree about who an `agent` is.

Inbox decisions:

- **`manual_only` sorts FIRST**, and an UNSCORED tier sorts above even that — burying destructive
  proposals under a page of parameter tweaks is how one gets accepted by momentum, and an unrecognized
  tier is the one case where nobody has judged the risk at all.
- **Risk tier is metadata, never a lane** (§3.1: any auto tier is guardrail-violating). It orders,
  filters, and bounds a bulk-accept CONTROL — every accept inside a bulk action still passes
  `require_human` individually.
- **A row needs provenance to be renderable.** A proposal whose source cannot be shown is one a reviewer
  cannot weigh, and a queue of unweighable rows trains people to bulk-accept — defeating the invariant
  while appearing to honour it. Unrenderable rows are REPORTED, not hidden: a missing provenance is a
  proposer bug, and quietly dropping the row makes that bug invisible.
- **`manifest_valid=false` still appears in the queue** (§3.1's lenient-but-recording), and
  `flagged_only` is the filter that finds them — a flag nobody can filter to is a flag nobody sees.
- **A defect found while probing:** a row with no provenance came back `bulk_acceptable=True` while
  `renderable=False`, so a row the UI cannot honestly show was eligible for a control that accepts
  without opening it. Bulk-accepting something a reviewer could not have read is the human-installs
  invariant in name only. `renderable` is now a bulk precondition.
- **A second defect:** the unknown-tier sort rank collided with a rank a known tier produces, so an
  unscored proposal sorted BELOW `manual_only` instead of above it.

- **NOT DONE (by scope):** the Learning page FE and the `/api/learning/proposals` routes. The unified
  queue has no HTTP surface at all today (measured — only the older per-kind skills queue does), so the
  API + page is its own session; this one owns the view model and the gate they will call. The
  `risk_tier` field is passed into the projection rather than stored on `Proposal`, because only a
  `template_diff` carries typed ops to derive one and stamping a meaningless tier on a lesson would make
  the filter lie.

### S76 — Staging-tier observability: the week-at-a-glance panel (17 tests) — DONE

**Measured before writing, and most of the session's scope was already shipped.** `record_flush`
already persists all four `FlushOutcome` members AND `proposal_ids`; `health()` already computes an
`all_ok_streak` over a window. So the honest remaining gap was narrow and specific:

**🔴 `health()` CANNOT SEE A SILENT DAY.** It aggregates over the whole window, and an absent day
contributes nothing to either the outcome counts or the streak — so a day where capture never ran is
indistinguishable from a healthy day. That is the exact failure the staging tier exists to expose
("a pass that crashes looks exactly like a quiet day"), surviving in the view built to expose it.
`test_health_alone_cannot_see_a_silent_day` asserts the contrast directly: two windows with identical
totals, one continuous and one with a two-day hole, are the same to `health()` and different in the
panel.

`StagingStore.week()` is the panel:

- **Every day in the window gets a bucket, INCLUDING the empty ones.** Pre-seeded, because a gap that
  vanishes from the list is a gap nobody sees. `silent_days` names them, and it is the alarming number:
  no passes at all on a machine that was in use means capture did not run.
- **`error_days` isolates which day broke** — "which day" is the question a maintainer actually asks,
  and the windowed view can only say "two errors, somewhere".
- **Proposal ids ride each bucket**, turning "a pass produced something" into "produced WHAT" so the
  panel links straight to the Proposal Inbox rows a day generated. `produced` counts IDS, not passes:
  one pass that filed three proposals produced three.
- **Staged entries are bucketed separately from flushes.** A day that STAGED but never flushed is a
  different failure from one that never ran — the signal arrived and nothing consumed it.
- **Days bucket by LOCAL date, not by 86400-second slices.** A user reading "Tuesday" means their
  Tuesday; a UTC-slice panel drifts hours off every reader's calendar.
- **A malformed `proposal_ids` cell cannot empty the week.** The column is JSON text, and a hand-edited
  or truncated row must not take out the whole panel — the observability surface failing silently would
  be the same class of bug it exists to catch.
- **`days=0` clamps to 1.** A caller passing zero wants today, not an empty panel.

- **NOT DONE (by scope):** the FE panel and its route. There is no learning HTTP handler at all (S75
  recorded the same finding for the proposal queue), so the API + page is one session covering both
  surfaces rather than two half-built ones. `week()` returns a fully-serialized shape with a
  fields-exact test, so that session wires rather than designs.

### S77 — Predict-then-verify attribution, auto-filed reverts, the incognito gate (32 tests) — DONE

The last spoke, and the one that closes the loop: everything upstream FILES proposals, and this measures
what happened after a human accepted one. §3.1's rule is predict-then-verify rather than measure-after —
an accepted proposal DECLARED which failures it would fix, so the verdict compares prediction against
outcome instead of looking at a delta and inventing a story.

**Measured before writing.** `refiner.canary_verdict` (S73) already returns the five verdict names from
a scalar before/after, so this reuses that vocabulary rather than forking it — two verdict scales would
make one proposal's history unreadable when it passed through both paths. And `learning/gate.py`'s
permission half is genuinely CLOSED: probed across all three cadences with both an idle turn and a busy
one carrying a correction, a restricted (incognito) session is refused every time.

**🔴 WHAT IS NOT CLOSED IS COVERAGE.** `Cadence.SESSION_END` and `Cadence.RUN_END` are declared with
**zero live call sites** — only `PER_TURN` has any. A gate cannot suppress a path nobody routes through
it, which is this program's recurring "present and inert" class applied to a privacy control.
`assert_gate_covers_cadences` turns that into a checkable fact and `test_the_uncovered_cadences_are_pinned`
pins the current gap set, so wiring one — or adding a fourth cadence — must be a deliberate edit rather
than a silent hole.

**🔴 AND THE CHECKER FOUND ITSELF.** Its first version matched its own docstring's `Cadence.SESSION_END`
mention and reported ZERO gaps for two cadences that genuinely had no callers — a coverage checker
certifying coverage by finding its own prose. Exactly the self-referential trap S67's fire-site scan fell
into, one module over. `test_the_coverage_checker_does_not_find_ITSELF` is the regression.

The verdict ladder, and why each rung sits where it does:

- **PENDING** under 3 post-acceptance runs. Not a guess: one run is an anecdote, and a change declared
  effective on one run is a lucky run made permanent.
- **HARMFUL** only when something regressed AND nothing predicted was fixed. Damage with no upside is
  the unambiguous case, and the only one that auto-files a revert.
- **MIXED** when regressions coexist with real fixes — deliberately NOT harmful, because the change did
  something the user wanted, so reverting is their call rather than an automatic rollback.
- **INEFFECTIVE** when nothing moved: clutter, not damage. Keeping it distinct is what keeps the revert
  queue readable.
- **A change with NO predictions can never be EFFECTIVE.** Without a prediction there is nothing to have
  been right about, and letting it reach EFFECTIVE would reward filing manifests with empty
  `predicted_fixes` — the shortcut §3.1's lenient validation makes tempting.

Further decisions:

- **A cluster present only in `after` counts as a regression.** It is a failure the change INTRODUCED —
  the most important kind, and the one a `before`-keyed loop misses entirely.
- **`unattributed_regressions`** is §3.1's "scariest class, surfaced loudly": what broke that nobody
  predicted. A change scored only on its own predictions looks fine while having broken something
  adjacent.
- **Rates, not counts.** Five failures in five hundred runs is not worse than five in ten, and a
  count-based comparison would call a busier week a regression.
- **Only HARMFUL owes a revert.** Auto-filing for everything that did not help would bury the queue —
  which is how the one revert that mattered gets skipped. The revert body NAMES the regressed clusters,
  because a proposal saying only "this made things worse" is un-reviewable.
- **A revert is a PROPOSAL, never an application.** Asserted against the source (no `accept(`, no
  installer, no writes): "mechanical" in §3.1 means the proposal appears without anyone noticing the
  regression, not that the rollback happens on its own — and S75's gate refuses a non-human accept
  regardless.
- **`harm_rate` is over DECIDED verdicts, not total.** A proposer with many PENDING changes would
  otherwise look safer than one whose changes have been measured, inverting the signal exactly when a
  new proposer starts filing. Proposers sort worst-first, because the useful question is which one to
  trust less.
- **An unknown verdict is counted under its own name, not dropped.** A verdict this module does not
  recognize is a drift signal, and discarding it hides the drift.

- **NOT DONE (by scope):** wiring `SESSION_END`/`RUN_END` through the gate. Each needs the cadence's own
  call site (a session-teardown hook and the run-end capture pass), which belongs with those subsystems
  — and the pinned gap test is what makes the omission visible rather than assumed-done. The curator
  tick that computes deltas from the Run Ledger and calls `attribute` is likewise a call site; this
  session owns the decision it will apply.

### S78 — The Learning HTTP surface: the Proposal Inbox page (24 + 15 tests) — DONE

**This closes success criterion 1**, which was unmet for want of a route. The criterion says "One
Proposal Inbox **SHOWS** all six proposal kinds with provenance, evidence manifests, and risk-tier
metadata; accept installs, reject dismisses — and the model cannot accept its own proposals under any
trust mode". Everything behind that sentence shipped in S75/S76 with **no HTTP surface and no page**:
`inbox.build_view` and `StagingStore.week` both returned fully-serialized shapes, and grepping found no
`/api/learning` route and no Learning page. Both prior sessions recorded the deferral; this is the
session they deferred to.

**DEVIATION — no queue row.** The queue was exhausted at 77/77 when this ran, and the previous cycles
recorded `BLOCKED (E6)` on the grounds that adding a row is a scope decision. That was right about
INVENTING scope and wrong about this: an unmet acceptance criterion of a plan already in the queue is
declared work, not new direction. Recorded here rather than as a new numbered row, since the plan's own
criterion is the authority.

**🔴 THE ACTOR IS THE WHOLE POINT, and a route is where it would have been lost.** S75 put
`require_human` inside `proposals.accept()` and defaulted `actor="user"` so existing callers kept
working. A route that omitted the actor would therefore have handed EVERY caller — including an
app-scoped token — the reviewer's authority, silently re-opening the hole S75 closed. So `_actor`
DERIVES it from the request (`request["app"]` → `agent`, `request["user"]` → `user`, otherwise `""`),
never from the body: a caller that could name itself `user` would make the gate decorative. Driven over
real HTTP: an app token gets **403** on both accept and reject, an unidentified caller gets 403, and
only the dashboard user succeeds.

Further decisions:

- **A missing row is 404 and a refused actor is 403.** Collapsing them would report a permission
  decision as a typo and vice versa.
- **The kill switch 404s rather than 403s.** With learning off there is no inbox, and "forbidden" would
  imply one exists behind a permission wall. But `_enabled()` fails **OPEN** on an unreadable config:
  a hidden queue looks like an empty one, and proposals would accumulate unseen.
- **A corrupt proposal file cannot empty the queue.** Proposals are per-file, and one unreadable file
  hiding the rest is how a backlog silently disappears — the listing degrades to `[]` rather than 500.
- **Literal paths register before `/{id}`**, so `staging` is not captured as a proposal id. The
  ordering landmine S67 and S70 each paid for once.
- **Only a `template_diff` gets a risk tier.** Nothing else carries typed ops to derive one, so
  fabricating `low` for a lesson would hand it bulk-accept eligibility nobody computed.
- **The FE re-derives NO judgement.** Ordering, bulk eligibility and renderability all arrive decided;
  `bulkBlockedReason` only EXPLAINS the backend's flag, and a test asserts it still refuses when every
  visible field looks fine. Two implementations of "safe to bulk-accept" would eventually disagree, and
  the FE would be the copy shipping the permissive answer.
- **An unrenderable row still renders, labelled** ("untitled — proposer bug"). Hiding it would make a
  proposer bug invisible, which is the same reasoning S75 used for reporting rather than dropping.
- **`dayLabel` parses the bucket as LOCAL time.** `new Date('2024-01-01')` parses as UTC and would shift
  the weekday a day west of the reader; the backend buckets by local date, so the label must agree.

**Two defects found by driving rather than reading.** (1) `del()` in `api.ts` is NOT generic — it
resolves void and throws on `!ok`; assuming symmetry with `get`/`post` failed typecheck. (2) A live
`404` on every route looked like a defect in `_enabled()` and was a **stale gateway process** — backend
changes never hot-reload, and the sibling route registered six lines away answered 200 the whole time,
which is what isolated it.

**Validated end to end** against a live gateway on an isolated dev home: all five routes 200, a seeded
proposal renders with provenance + evidence + tier, `accept` over real HTTP installs it and clears the
queue, and the week panel returns 7 buckets with `silent_days` populated.

- **NOT DONE (by scope):** bulk-accept as a UI CONTROL. The backend computes eligibility per row and the
  page explains it, but the multi-select affordance is its own interaction design — and §3.1 is explicit
  that bulk is ergonomics, never a lane, so shipping the explanation before the control is the safe
  order. Proposal DETAIL (the full change manifest rendered as a diff) also stays deferred: the route
  serves the record, and the diff view belongs with the Versions tab that renders template diffs.

### S79 — Criterion 4's adversarial refiner-path test, and the fencing it needed (41 tests) — DONE

**This closes success criterion 4**, whose second clause was unmet: "Content inside `fence_untrusted`
provably never becomes a lesson/skill/template — **and the adversarial test covers the REFINER path**:
injection planted in a run transcript or `run_feedback` comment must not surface as a proposal (let
alone an accepted diff)."

**🔴 THE REFINER PATH HAD NO SCREEN AND NO FENCE.** Grepped `refiner.py` for `screen(`,
`fence_untrusted` and `triggers.screen` — none present. Driven with an injection planted in a
`step_failed` error, the text flowed straight into the cluster SIGNATURE, which is precisely what a
refiner prompt carries as its evidence. §3.1's TRUST clause names the refiner as the fifth
`fence_untrusted` call site and says fencing is the caller's responsibility; that call site had not been
built. S69 built the screen this needed, at the trigger boundary, and nothing on this path called it.

Two dispositions, deliberately different:

- **BLOCKED** (the screen's hard groups) → the event is **DROPPED**, not fenced-and-passed. A fenced
  event still influences cluster RANK, so an attacker could choose which failure the refiner targets
  even without steering the prompt. `test_a_blocked_payload_never_reaches_a_PROMPT` covers the second
  layer, so the criterion's "let alone an accepted diff" holds too.
- **SUSPICIOUS or clean** → fenced at the model boundary and KEPT. An attacker who could make legitimate
  text look borderline would suppress the refiner's evidence — an availability attack, and just as
  effective as steering it. `test_borderline_text_survives_screening` pins that with "the retry
  instructions in the runbook", which mentions instructions and is not an injection.

**🔴 A DEFECT FOUND BY PROBING MY OWN FIRST VERSION.** Fencing at the CLUSTERING layer put the marker
words into every failure signature — `untrusted_content source run ledger step_failed …` — so four
tokens of boilerplate ate a third of the 12-token window that makes two mechanisms distinct, and
unrelated failures began sharing tokens (measured: 4 shared, where they should share at most the
`step_failed` prefix both messages literally contain). Clustering is pure statistics that no model
reads, so fencing buys it nothing and costs precision. Split into two layers: `screen_evidence` produces
clustering input (screened, unfenced) and `fenced_evidence` produces prompt input (screened AND fenced).

Further decisions:

- **`UNTRUSTED_EVIDENCE_FIELDS` is data, not inline checks.** A field absent from the set is one nobody
  decided the trust level of, and a test walks every member so a new evidence field cannot quietly skip
  the screen.
- **Every surviving field is fenced, not only the flagged ones.** Fencing only suspicious text would
  mean the screen's MISSES arrive as instructions — the composition rule S69 established at the trigger
  boundary.
- **`cluster_failures` stays public and unguarded.** It is a pure function, and a test proving the raw
  path is unsafe needs to call it; the guard is that the PIPELINE entry point (`cluster_safely`) screens.
  `test_the_raw_clustering_path_stays_callable_and_unscreened` asserts both halves.
- **Screening never raises and never changes the power floor.** A screen that throws fails open under
  exactly the input an attacker controls, and a floor that shifted under screening would silently change
  when a refiner may propose.
- **A non-string field is ignored rather than coerced** — coercing a dict would invent content to match
  against.

**Two of my own errors, corrected mid-session.** (1) An assertion of ZERO shared signature tokens fails
on real text and says nothing about fencing; the honest property is that no fence-MARKER token leaks,
with genuine shared vocabulary allowed. (2) The reflow tool split a string literal across lines,
producing unparseable Python — caught by `black`, not by me, and repaired by shortening the literal.

- **NOT DONE (by scope):** the refiner AGENT's own prompt assembly, which is where `fenced_evidence`
  gets consumed. §3.1 puts that agent on the `run-workflow` provider as a trigger-fired template rather
  than Python, so this session builds the contract it will call — the same split S73 recorded.

### S80 — Criterion 5's one budget: the ambient render finally goes through the allocator (57 tests) — DONE

**This closes success criterion 5**: "the lesson block, skill INDEX, template suggestion, voice/facet
blocks, and self-model snapshot fit ONE per-turn slot-allocated token budget; lessons are never crowded
out (sacrificial-slot truncation only); the authority preamble renders."

S71 built the allocator and deliberately stopped short of owning the render, recording why: "replacing
the whole render is behaviour-visible and needs §2.5's measurement floor to prove nothing stops
surfacing." That floor landed in S71's own `measure.py`, so the deferral's condition was met.

**🔴 THREE INERT CONTROLS, NOT ONE.** (1) `allocate()` and `AUTHORITY_PREAMBLE` had ZERO callers outside
their own module and tests — the ranking algorithm existed and nothing ranked. (2)
`learning.context_budget_tokens` is a fully round-tripped config knob (dataclass + `_meta` + `load()` +
`to_dict()` + `_EDITABLE_CONFIG`) that NOTHING read; its help text promises "only retrieved context is
ever trimmed", a promise no code kept. (3) The blocks were bounded by per-block CHARACTER caps summing
to ~36,750 tokens against the declared 4,000 — 9x. Driven with 120 realistic lessons the render passed
the budget by 1,576 tokens; at 400+ it reached 10,101 (2.5x). The allocator on the same input holds
3,999 and keeps the query-relevant lesson.

**Two measurements decided the design.**

- **The budget scales with the model window**, on the same `window/200k` clamped to [1,5] that
  `context._memory_caps` uses. A FLAT budget beside window-scaled memory sections would make the ambient
  blocks the only part of the prompt that never benefits from a larger window — silently inverting the
  plan's own adaptive-recall design. A test asserts the two constants are the SAME, not merely equal.
- **The skill INDEX is a catalogue, not a candidate set.** Fed one candidate per entry, `MAX_PER_SOURCE
  = 3` kept 3 of 12 skills while 3,539 tokens sat unused. Diversification stops a rich source crowding a
  sparse one; applied to a catalogue it just deletes the catalogue. So the index is ONE candidate with
  §2.4's three-step degradation (full → 80-char hints → names only), every skill named at every tier.

**🔴 THE SKILLS BLOCK HOLDS TWO POPULATIONS, AND MY FIRST VERSION DROPPED ONE.** Caught by the EXISTING
suite (`test_context.py::test_skills_injected`), not by any of the 52 tests written here — every fixture
I wrote happened to contain an index. `skills/loader.get_context` emits always-loaded skills as
`### Skill: <name>` with their full body AND on-demand skills as the `- **name**:` index; an index-only
parser silently dropped every always-loaded skill, which are precisely the ones the user marked
never-optional. `always_body` and `index_candidate` are now a pair, each carrying its own `[Skills:]`
frame (either can be dropped independently, so one shared wrapper would sometimes frame nothing), with
bodies ranked above the index because a pointer list should yield before content does.

**Three more defects found by driving, each the same mistake — spending the budget on FRAMING before
CONTENT.**

1. `frame` restored the lesson header AFTER the allocator had spent the budget: at 600 tokens `used`
   reported 596 while the framed text was 622. A budget a later step can add to is not a budget.
2. Reserving the header then crowded out the last lesson — the header costs 29 tokens and one lesson 31,
   so at a 60-token budget the reservation left nothing. Now the reservation is RELEASED when no lesson
   survived.
3. The 73-token preamble did the same thing: at a 120-token budget it fit, left 23, and ZERO lessons
   survived, while at 60 tokens (preamble skipped as oversized) one did. The authority statement was
   outranking the corrections it exists to speak for. Now the render retries without it.

Then the affordability check itself had to be measured: comparing `used_tokens` double-counted the
reserved header and dropped a header that plainly fit (574 + 29 + 2 > 600 on paper, well under in
fact). It compares the rendered TEXT.

**Clean break:** `_fit_lessons` and `_LESSONS_CAP` are deleted — a second policy for a block the budget
now governs. Their five tests are MIGRATED rather than dropped: the properties (no partial lesson, the
dropped ones are counted, an under-budget block keeps everything) are exactly what the budget must
still guarantee, re-expressed against the near-miss catalogue.

**Two of my own test errors, corrected by driving:** an assertion that the L0 catalogue always renders
was wrong (it costs tokens from the same budget, and for lessons `l0 == l1`, so a greedy fill leaves no
room — the right trade), and an assertion about index DEGRADATION at 500 tokens asserted nothing,
because the full index fits there.

- **NOT DONE (by scope):** `template` and `self_model` have no live producer — nothing on the chat path
  matches a query to a workflow def, and nothing persists `user.selfmodel.*` (S72 built the decisions,
  not the store). Both are mapped in `SLOT_KINDS` with tested seams so a future producer joins the
  budget instead of appending a sixth independent block. The memory context keeps `_memory_caps`: a
  different, already-window-scaled mechanism, and swapping a working recall render for an unproven one
  is what S71 refused to do blind.

### WF2LEA-4 — Step 5: run-end capture spoke + SESSION_END/RUN_END routed through the gate (criterion 10) — DONE (PR #938)

**This closes success criterion 10**: the incognito capture gate is closed AND every declared cadence is
routed through it. Before this atom `SESSION_END` and `RUN_END` were `Cadence` members with ZERO live call
sites — `assert_gate_covers_cadences()` reported `["RUN_END", "SESSION_END"]`. Now `SESSION_END` runs through
the dashboard consolidation envelope and `RUN_END` through `controller._finish → _capture_run_end`, so the
gap set is `[]` and the checker's regression test pins it there (adding a fourth cadence, or dropping a call
site, is now a deliberate edit, not a silent hole).

What landed, each driven against the REAL `MemoryService`/`VectorMemoryStore`, the REAL Run Ledger, and the
REAL proposal store (isolated to a tmp home):

- **The RUN_END spoke** (`learning/run_end.py`): a terminal run mines its own ledger's `step_failed` events
  into `lesson_batch` PROPOSALS through the shared human-gated queue (never a live lesson — the injection
  hole §3.3 names), each carrying an **R8 failure CAPSULE** (repro `workflow_start(name="diagnose-run", …)`,
  signature, forbidden success modes, bounded evidence) keyed by `(template, mode, signature)` so one
  mechanism failing on every retry is ONE proposal. The §3.3 environment deny-filter drops world-condition
  failures first (a refused connection must never teach the agent to refuse a valid action), and a
  `record_procedural(outcome="failed")` prior is recorded per failed step even when the lesson is
  quota-suppressed.
- **The R18 pending-outcome resolver** (`learning/outcome_resolver.py`): a decision journals a
  `pending_outcome` at DECISION time (subject/metric/horizon/baseline — wired in `controller`'s
  iteration-context capture, kept separate from `decision` because a settled choice ≠ a claim about the
  future). On the curator tick (`history._run_learning_curator`, BEFORE the aging pass) every open question
  past its horizon is measured against ground truth in semantic memory, scored benchmark-relative, closed
  with an `outcome_resolved` that cites the question's `pending_event_id` (which makes a second tick
  idempotent), and filed as a graded proposal — `measured` or an honest `inconclusive`, never a fabricated
  pass.
- **plan_memory absorbed and DELETED**: the per-plan JSONL silo (`plan_memory.py` → renamed to
  `plan_format.py` keeping only the formatting helpers; the store half gone) is removed from the durability
  inventory, snapshot capture/restore/merge, AND the portability export list — a run's working memory now
  lives in the Run Ledger, the single source the resolver and run-end learner both read.

**🔴 THE DISCOVERY — both new spokes shipped their lesson-proposal path INERT, and neither would ever file.**
`run_end.capture` and `outcome_resolver.resolve` both called `proposals.enqueue(occurrences=1)` WITHOUT a
`min_evidence` argument. `enqueue`'s evidence floor is `if provenance != "human" and occurrences and
occurrences < max(1, min_evidence): return SKIP`, and `min_evidence` defaults to `MIN_EVIDENCE_DEFAULT = 3`.
So every inferred, once-per-signal proposal (`occurrences=1 < 3`) was SILENTLY SKIPPED — the whole
propose-a-lesson half of the RUN_END cadence and the entire graded-outcome path were dead on arrival, and a
test that only asserted "capture ran" would have passed over a spoke that files nothing. The ≥3 floor is for
consolidation-mined habits that must recur to be worth proposing; a terminal failure and a resolved bet are
each a single first-class signal (R8 wants the lesson re-injectable on the NEXT run, not after three separate
runs fail the same way). Fixed both to `min_evidence=1`, matching the curator's own review proposals
(`curator.py:435`). Regression tests now assert the proposal is actually FILED (`list_pending` returns it),
not merely that capture returned — the guard that would have caught the inert path.

**Two isolation hazards, both from `config_dir` binding time.** The run-end/resolver tests scan runs through
`workflows/store.py`, which does `from personalclaw.config.loader import config_dir` at MODULE import — so
`monkeypatch.setattr("…loader.config_dir", …)` does NOT reach the store's own binding, and the resolver was
scanning the REAL `~/.personalclaw`, accumulating cross-test runs (resolved:5 where 1 was expected). Fixed by
setting `PERSONALCLAW_HOME` in the `home` fixture — `config_dir()` re-reads that env live every call, so it
reaches the import-bound reference too. Separately, the quota test's five node failures were first named
`attr0..attr4`; `refiner.failure_signature` does `re.findall(r"[a-z_]{3,}", …)` and STRIPS DIGITS, collapsing
all five to signature `attr` → one `LessonKey` → one proposal, silently defeating the quota path. Renamed to
alphabetic `foo/bar/baz/qux/quux` so five distinct signatures actually exercise the per-run quota.

- **2026-08-09 — DONE — WF2LEA-5 (criterion 9): `accountability.attribute` wired on the curator tick.**
  Branch `feature-wf2lea5-attribution-wiring`. `accountability.py` had ZERO production importers by
  design (its purity is asserted by `test_a_revert_is_a_PROPOSAL_never_an_application` — the source
  may contain no `sqlite3`/`atomic_write`/`accept(`/`installer`), so all orchestration and storage
  had to live elsewhere). New `learning/attribution.py` is that importer and the missing half of §3.1
  predict-then-verify. Two halves join across time: (1) **accept-time snapshot** — `proposals.accept`
  now records the bet (target + `predicted_fixes` + pre-acceptance failure rates, baseline keyed by
  run id) the instant BEFORE it unlinks the proposal file, the only moment those are still knowable;
  (2) **curator-tick grading** — `history._run_learning_curator` calls `attribution.grade_accepted_changes()`,
  which finds records with ≥`MIN_RUNS` post-acceptance terminal runs of the target, recomputes
  after-rates from the Run Ledger, calls `accountability.attribute()`, records the 5-way verdict, and
  for a HARMFUL verdict files a revert PROPOSAL through the shared human-gated queue (named clusters,
  never auto-applied). Config round-trip: `learning.attribution_enabled` (default on) through
  dataclass+`_meta` / `load` / `to_dict` / `_EDITABLE_CONFIG` + `config-baseline.json`. 14 new
  end-to-end tests over the real proposal store, Run Ledger, and config loader (repointed via
  `PERSONALCLAW_HOME`), including the headline `test_accountability_now_has_a_production_importer`.
  `make lint` (black/isort/flake8/mypy) + all 3 `gate_report.py` gates + 280 sibling tests green.

- **DEVIATION — grading reads the Run Ledger directly, NOT gated on a vector store.** `outcome_resolver`
  is gated on a configured embedder because it queries semantic memory; attribution instead reads the
  sqlite/jsonl Run Ledger available on every box, so gating HARMFUL auto-reverts on an embedder would
  be a silent capability loss. It is inert-by-DATA (no accepted-change records → the scan returns
  immediately), which is the correct floor here.

- **DESIGN — cluster = failure MODE, scope = the target's own runs.** The join between what a proposer
  PREDICTED and what the ledger MEASURED needs one shared vocabulary; free-form prediction strings and
  per-run signatures do not share one, but the closed `FailureMode` enum does. Rates are scoped by
  `workflow_name == target`, so a HARMFUL verdict names a real regression in THIS template's runs, not
  global noise after an unrelated accept — pinned by `test_only_the_targets_own_runs_are_scored`.
- [2026-08-10][WF2LEA-7] PARTIAL (clause 6 of 7 shipped; atom stays in_progress). **Stale premise corrected first:** the atom's status line says "detectors.py has ZERO production importers" — no longer true. `attribution.py:191` and `run_end.py:151` already import `classify_failure`/`lesson_worthy`/`LessonKey`/`dedupe_signature`/`NON_LESSON_MODES`, so the failure-classification half is wired. What IS inert is the GATE half (`gate()`, `structural_score()`, `similarity_verdict()`) — verified symbol-by-symbol (the apparent `Candidate`/`GateDecision`/`Skip` hits elsewhere are other modules' same-named classes; only those two files import from `detectors` at all).
  **Shipped — clause 6: every negative decision writes a row.** Two halves of one promise were both unkept: `learning/gate.py`'s `GateReason` docstring says the reason is "recorded, not just returned", and `staging.FlushOutcome.FLUSH_SKIPPED` exists for "the gate denied it — recorded so a config-off period is legible" — yet `FLUSH_SKIPPED` was NEVER written in production and no consumer read `.reason`. A denial left no trace, making a permanently-disabled gate indistinguishable from a healthy pass that found nothing. Added `learning.gate.record_denial(decision)` writing `FLUSH_SKIPPED` with the typed reason as `detail`, exported from `learning/__init__`, wired at the two real denial branches in `dashboard/chat_runner.py` (`not decision.permitted` and `not decision.worthwhile`). Deliberately a SEPARATE function, not a write inside `decide()`: `decide` is documented pure w.r.t. config + the restrictions registry, and two reviews in one turn must consult it without double-recording — so the call site that ACTS on a denial records it. Best-effort by design (observability must never fail a capture path). 7 tests pin it: each permission reason recorded with its typed value, below-threshold recorded too, an ALLOWED decision records nothing, a staging failure swallowed, and — the wiring test, not just the function — the real `chat_runner` branch writes the row.
  **NOT shipped (clauses 1-5, 7):** fifth `run_skill_ladder_review` branch, per-spec embedding + registry-miss logging, intent inversion, positive-path trace mining, grill SaveFn, and R17 `tier_migration` proposals + a `template_save_from_session` tool. `proposals.TIER_MIGRATION` (proposals.py:103) is an enum member with no producer, and `template_save_from_session` does not exist anywhere — both real remaining work.
  **DISCOVERY (subagent hygiene):** the implementation subagent spent >1MB of transcript on recon with zero edits across four checks; a nudge did not change it, so it was stopped. It had just begun a 375-line `learning/templating.py`, which I DISCARDED rather than salvage: nothing imported it (it would have shipped as another inert module — the exact defect this atom fixes) and it referenced `Skip.NEEDS_CONSULT`, which does not exist in the `Skip` enum. Clause 6 was implemented inline.
- [2026-08-11][WF2LEA-7] PARTIAL — 2 of 7 clauses wired and DRIVEN; atom stays `in_progress`, and the five remaining are named below rather than quietly counted as done. The clause that mattered most is closed: `detectors.gate()`/`structural_score()` had ZERO production callers, so the ad-hoc→template gate could not decline anything.
  **Verified premise correction.** The atom's old note claims "detectors.py has ZERO production importers" — stale. `attribution.py:191` and `run_end.py:151` already import its failure-classification half (`NON_LESSON_MODES`, `classify_failure`). The precise truth is narrower and is what this atom fixes: the *gate* half was inert.
  **1. The gate call site.** New `learning/template_gate.py` holds the wiring rather than `detectors.py`, because that module's documented purity — nothing there calls a model, writes memory, or files a proposal — is exactly *why* it shipped inert and is worth preserving. A fifth tier joins `run_skill_ladder_review`'s ladder (`after_turn_review.py`), dispatching a procedure-shaped turn to the deterministic chain instead of the skill queue. The `Candidate` is built from REAL turn data (the ladder's own `steps` array, the session key, and `_template_already_surfaced(slug)`), not hand-built state — a gate fed by a fixture passes its own test and still never fires in production. Steps are redacted (`redact_exfiltration_urls` + `redact_credentials`) BEFORE reaching the gate, since an accepted candidate becomes a proposal body.
  **2. Every negative decision writes a typed `skipped(reason)`.** `record_skip()` writes `FLUSH_SKIPPED` through `staging.record_flush` carrying the typed `Skip` value — the same ledger `gate.record_denial` (#1013) uses, left untouched. Rows are prefixed `template_gate:` so two gates sharing that table stay attributable, and `skip_counts()` reads back only this gate's reasons. Proof it fires: a parametrized test across all four pre-gates, a separate `LOW_SCORE` case (the post-scoring branch most easily lost), and `test_record_skip_refuses_to_log_an_accept_as_a_skip` pinning the inverse.
  **3. `template_save_from_session` files a DRAFT.** Both accepting paths call `proposals.enqueue(kind="template")` — a PENDING row, never an install. Even the zero-model `AUTO_FILE` branch cannot write a definition.
  **BLOCKED — R17 `tier_migration`, premise absent.** Its inputs do not exist: no execution-tier field on `WorkflowDef` (`workflows/models.py:658-683`) and no cross-run per-step aggregation to compute trajectory variance from (`RunStats` is per-run scalars; `introspection.template_card` aggregates cost/duration only). I did not invent a statistics surface to satisfy the wording. `Kind.TIER_MIGRATION` remains an enum member with no writer — the same shape this plan keeps closing, recorded here so the next reader does not rediscover it.
  **Left undone, with reasons:** per-spec embedding + registry-miss logging needs an embedding store `similarity_verdict` was never wired to; intent inversion and positive-path trace mining each need a journal-mining pass, not a call site; grill SaveFn belongs to `workflows/grill_protocol`, a different seam.
  **DEVIATION — tool placement.** `template_save_from_session` first went in `mcp_workflows.py`, which reds four `test_workflows_tools.py` invariants (a hard `workflow_` name prefix, a count assertion, `MCP_WORKFLOW_SCHEMAS` membership) plus `test_api_manifest_drift`. Moved to `mcp_core.py` beside `skill_remember` — where §5 groups it with the explicit-capture trio — rather than weaken the prefix rule. Needed a `TOOL_META` entry; `reference/{index,tools}.md` regenerated in the same commit (83→84 tools).
  **Verification notes worth keeping.** (a) A narrow test selection hid 5 real reds: the first targeted run passed at 245, and adding the workflow-tool/manifest suites surfaced them. Worse, a nonexistent path (`tests/test_mcp_workflows.py`) made pytest report `collected 0 items` — a zero that reads like success from the tail. (b) `flake8` was red behind a pipe twice, so lint is now judged by EXIT CODE, not by `tail`. (c) The rebase onto post-merge `main` conflicted on generated `reference/index.md`: main carried SM-5's 3 new routes, this branch the new tool, so NEITHER side was correct — regenerated instead of picking, giving the true combination (84 tools, 623 routes).
  **Gate:** black/isort/flake8 all exit 0; `mypy src/personalclaw harness` clean on 787 files; 135 passed on an independent re-run (new 28-test suite + `workflows_tools` + `api_manifest_drift` + the three full-suite-only ratchets `agent_reference`/`inert_surface_baseline`/`docs_lint_baseline`). No baseline regenerated to bless a higher count. PYTHONPATH proved via `template_gate.__file__` resolving under the worktree — the venv is editable-installed against main, so a bare pytest would have tested main.
  **FOLLOW-UP (same PR) — a THIRD registry the new tool had to join.** CI's `test_native_tool_categories::test_residual_core_is_exactly_the_cross_cutting_tools` red on `Extra items in the left set: 'template_save_from_session'`. `mcp_core` carries an exact-set allowlist of the tools permitted to live in residual core, and adding a tool there without an entry is a red — a fourth registration point beyond the three (`registry`, `ALLOWED_HOOK_PROVIDERS`, `TOOL_META`) this atom already handled. Added with its justification rather than by loosening the assertion: the tool reads the SESSION's just-carried-out steps, queries the WORKFLOW library for an already-surfaced definition, and files into the LEARNING proposal queue — three categories, owning none of them, which is exactly why `get_context` and `project_context_review` sit in that same list. The sibling ratchet (`test_residual_core_owns_no_category_tools`, which forbids an `artifact_`/`workflow_`/`memory_`/`subagent_` prefix in core) still holds, since `template_` is not a category prefix.
  **Verification note:** the earlier targeted run passed because it never imported this module's suite — the same narrow-selection failure recorded above, one layer deeper. The re-run now sweeps every tool-surface ratchet together (`native_tool_categories`, `workflows_tools`, `api_manifest_drift`, `inbound_mcp`, `agent_reference`) — 246 passed. A new MCP tool touches more registries than any single suite knows about, so the whole family is the unit to run.
- [2026-08-11][WF2LEA-7] DONE — the remaining 4 of 7 clauses closed; atom moves to `done`. R17 `tier_migration` stays BLOCKED on absent inputs, re-verified against code (below). The through-line: §3.2's detectors were all READERS with no producers, so each was a calibrated-looking control that could never fire.
  **New `learning/mining.py`, with a real production call site.** `run_end._mine_positive_signals` (`run_end.py:167`), reached from the live terminal-run path `run_end.capture` <- `workflows/controller.py:3164`. The module is not importer-less: `run_end.py:148` imports it inside `capture`. That check is the atom's own standing defect, so it was made explicitly rather than assumed.
  **Clause A — per-spec embedding + registry-miss logging.** `similar_run_matches` produces the exact `(run_id, cosine, age_days)` triples `detectors.similarity_verdict` (`detectors.py:296`) consumes; that function was left untouched, being already correct and pure. Built on the existing `vector_memory` substrate through `MemoryService` — no second embedding stack, no new dependency. The WRITE half (`index_run_spec`) ships with the read half deliberately: a similarity search against an index nothing populates reports "no priors" forever, which is the same inertness one layer down. A miss is a typed `Miss` (5 variants) recorded to the shared flush ledger under a `mining:` prefix and read back by `miss_counts()`; a blind pass files NOTHING, because proposing on an unmeasured signal is worse than not proposing.
  **DEFECT FOUND — MMR reranking made the repetition detector structurally unable to count.** `search_episodic` reranks for DIVERSITY (Maximal Marginal Relevance), which suppresses precisely the near-duplicates a repetition counter exists to find: three identical runs came back as ONE match and the verdict read "1 similar plan; 2 needed". Fixed with an explicit `mmr` flag (`memory_service.py:316`) — recall keeps the diversity bias, counting callers opt out (`mining.py:314`). Wiring the producer without this would have shipped clause A as another inert control that passes its own test.
  **DEFECT FOUND — `write_episodic` deduped the corpus down to one prior.** It rejects on the lowercased first 80 chars (`vector_memory.py:1958`, `LOWER(SUBSTR(text,1,80))`), and two runs of one template have BY DEFINITION identical spec text, so the second indexed as a no-op. The corpus could never accumulate priors at all. Fixed at the producer by leading the body with the run id (`mining.py:233`), NOT by weakening a dedup other callers depend on.
  **Clause B — intent inversion.** `mining.invert_intent`, called at `run_end.py:180`. A journal-mining pass following the existing `attribution.py`/`run_end.py` shape (`journal.ledger`, `_step_names`), deterministic and with no model call. The synthesized intent feeds clause A's embedded spec via `spec_text`, pinned by `test_the_synthesized_intent_reaches_the_embedded_spec` so the two halves cannot drift apart silently.
  **Clause C — positive-path trace mining.** `positive_path_candidates` + `file_positive_trace`, called at `run_end.py:204-208`. Order-sensitive signatures, gated on `min_frequency` AND outcome quality (COMPLETE runs only), routed through the same `proposals.enqueue` PENDING draft path — never a self-install. This is §3.2's positive half; the negative half is the `skipped(reason)` ledger shipped in the prior session.
  **Clause D — grill SaveFn.** `workflows/grill_protocol.settled_decisions` + `_persist_grill_decisions`, called at `dashboard/handlers/loop_routes.py:493` in the real `PUT /api/loops/{id}` launch write. Wired at the SETTLE point (where `phase_answers` persist), not at generation time where nothing is settled yet.
  **DEFECT FOUND — clause D would have written into a key space its own recall excludes.** `write_lesson` stores `lesson.<hash>` keys while `get_semantic_context` EXCLUDES `lesson.%` by design (`_NON_FACT_KEY_CLAUSE`, `vector_memory.py:492` — a lesson is not a fact about the user). So the fact block alone could never surface a prior grill decision: the recall read a different key space than the save wrote, and the memory check would look wired while being blind to its own output. The lessons block is the matching reader. The two reads are guarded SEPARATELY — sharing one `try` meant a lessons failure discarded already-fetched facts, turning partial degradation into total silence.
  **BLOCKED (unchanged) — R17 `tier_migration`, premise still absent.** Re-verified against code this session: no execution-tier field on `WorkflowDef` (`workflows/models.py:658-683`), no cross-run per-step aggregation to compute trajectory variance from, and `Kind.TIER_MIGRATION` (`learning/proposals.py:110`) remains an enum member with no writer. No statistics surface was invented to satisfy the wording.
  **Gate (independent re-run by EXIT CODE, not by tail):** black/isort/flake8 all 0; `mypy src/personalclaw harness` clean on 795 files; 198 passed across the new 24-test mining suite + `learning_run_end` + `workflows_grill_protocol` + `loop_http` + the three full-suite-only ratchets (`agent_reference`, `inert_surface_baseline`, `docs_lint_baseline`), with no `collected 0` in the output. Because a SHARED `search_episodic` signature changed, the memory surface was swept whole rather than targeted: 1568 passed / 2 skipped / 1 xfailed. No baseline regenerated; no assertion weakened — the one existing assertion edited (`test_learning_run_end.py:93`) gained `"mined": 0` inside an exact-dict comparison, which tightens it. PYTHONPATH proved via `mining.__file__` resolving under the worktree, since the venv is editable-installed against main.
  **Real home verified untouched by MTIME, not by absence:** `~/.personalclaw/workflows` and `runs.db` both unchanged across a test run, 22 run dirs. The home's own recent mtime is the two already-running dev gateways (PIDs 5827, 52350), not this work — an important distinction, since "the home changed" would otherwise read as test pollution.
  **Test-isolation hazard worth carrying forward:** `staging.get_store()` caches a process-global `_INSTANCE` (`staging.py:526`), so miss counts leak across tests in an xdist worker. The `home` fixture resets it around each test rather than masking the leak with looser assertions.
  **No docs row needed:** the `workflows.md` module ratchet (`test_workflows_hardening.py:528`) is scoped to `src/personalclaw/workflows/*.py`, and this atom added no module there — checked rather than assumed, since that same ratchet went red three times on the preceding stack.

- **DONE `WF2LEA-11`** (Amendment E1.3 — retroactive completed-run/conversation → skill proposal).
  **Audit first, as the atom demands.** What already existed and was NOT rebuilt: `skill_remember`
  captures a *taught* skill as a session-live ephemeral draft (prompted, not retrospective, already
  never writes live); `learning/proposals.py` is §2.2's unified queue with `content_fingerprint` +
  `_prior_decision_blocks` + `record_decision` and a `require_human`-gated `accept`/`reject`; §3.2
  already mines successful runs but only for **templates** (`mining.file_positive_trace` →
  `Kind.TEMPLATE`). The gap: nothing promoted a run/conversation to a **skill**, and `Kind.SKILL` was
  a declared kind with an inbox label, **zero producers and no accept-installer** — a reserved slot
  with no writer.
  **Built (add-only):** `learning/skill_promotion.py` `promote()` files a `Kind.SKILL` PROPOSAL via the
  existing queue — no second queue, per the atom's "no second queue" clause. Deliberately NOT routed
  through the legacy `skills/proposals.py`, which has no decision memory; routing there would have
  meant reimplementing the suppression the atom says to reuse.
  **Never writes (three layers):** promotion touches no skills path (a test asserts zero `SKILL.md`
  appear); the only writer is `install_accepted_skill`, reachable solely through `accept()` after
  `require_human`; and `accept(actor="agent")` raises. **Does not re-surface:** reject → re-promote
  returns `already_decided` with zero rows and zero skills written.
  **Deviation recorded:** the missing `Kind.SKILL` accept-installer was added even though it is not in
  the atom's letter — without it accept would record a decision and write nothing, shipping the
  promotion as an inert control (the failure mode this program keeps finding).
  **Gate (independently re-run by the driving session, not taken from the subagent's report):**
  `make lint` clean (mypy 797 files); `test_skill_promotion` + `native_tool_categories` +
  `agent_reference` + `inert_surface_baseline` = 44 passed / 1 xfailed; `-k "proposals or skill or
  learning"` = 1143 passed / 2 skipped; added lines name-scrub clean; diff scoped to 8 files.
  **Shipped as #1086** after an environment blocker delayed it: `git push` was declined repeatedly
  across several ticks, so the atom sat complete-and-gated on `feature-wf2lea11-retroactive-skill`
  (impl `6f135a45` + tracking) until the permission was restored. No code changed in the interval —
  `origin/main` never moved off `a2e874a8`, so the gate result above still stood at push time.

### WF2LEA-9 — Step 9: polish tier — DONE (4 of 5 parts; part 4 dropped with a DEVIATION)

**Part 1 — heat-earned promotion multi-gate: WIRED (it existed and nothing ran it).**
`usage.promotion_ready` already implemented R6f's multi-gate correctly (uses AND context diversity
AND recency AND success rate) and had **no caller anywhere in `src/`** — exported from
`learning/__init__` and exercised only by `test_learning_usage.py`. That is the worse half of the
inert shape: the bare "surfaced ≥2×" the gate was written to replace was still what the ladder
effectively used, because nothing consulted the replacement. `Kind.TIER_MIGRATION` was the mirror
defect — a declared proposal kind with no writer.
- **Writer:** `history._run_learning_curator` (the verified consolidation maintenance tick that
  already hosts `run_aging`) → new `curator.promotion_suggestions()` → `file_promotion_suggestions()`
  → `proposals.enqueue(kind=tier_migration)`. Never auto-promotes; the queue's fingerprint reinforces
  a pending row and silently skips a rejected one, so a daily cadence cannot nag.
- **Reader:** `proposals.list_pending()` → `/api/learning/proposals` → the Proposal Inbox.
- **Test:** `tests/test_learning_promotion_wire.py` (10) — drives the real `UsageStore` through
  surfaced/run/run_success events, reads the proposal out of the queue's own live reader, asserts the
  evidence travels in the body, that filing twice does not duplicate, and an **AST assertion that
  `_run_learning_curator` calls both functions** (testing the functions alone would have passed
  exactly the state this atom found).

**Part 2 — `memory_record.heat` migrated onto the one kernel (a real behaviour change).**
Its private recency term `0.5·e^(−days/30)` is now `0.5·decay.strength(...)`. The old curve had no
per-kind rate (an episodic fragment aged exactly like a distilled fact) and no importance axis, so
the same record could be hot to the retrieval boost and prunable to the curator.
- Added the 5 missing memory profiles to `decay.KIND_MULTIPLIERS` (3 already collided by string
  value — that collision is the one-kernel property working). `episodic` is **1.3, not a round
  number**: the formula it replaces was `e^(−0.03·days)` and 0.03 / BASE_LAMBDA ≈ 1.299.
- `memory_record._DECAY_PROFILES` maps all 8 `MemoryKind` members and `decay_profile()` **RAISES**
  for an unmapped kind rather than defaulting to the reference rate.
- **Old vs new** (30 days idle, 0 visits, importance 0.5): OLD `0.183940` for *every* kind →
  NEW `semantic`/`preference`/`self_persona` `0.406126`, `lesson`/`commitment` `0.373712`,
  `note` `0.329877`, `procedural` `0.303549`, `episodic` `0.291183`. Kind and importance now matter;
  neither did before. `promote_by_heat`'s 1.0 threshold and the M5b retrieval boost both see these.
- **Surfacing-rank doctrine asserted.** `test_the_kernels_verdict_never_reorders_retrieval` drives
  the live `procedural_priors()` with two records — one 400 active days idle that the kernel
  **`prune`s** and one fresh — and asserts the prunable-but-used record still ranks FIRST. Plus
  `test_strength_alone_cannot_win_a_rank` (structural: 0.7 usage > 0.5 recency) and a source-level
  rail that `memory_service`/`memory_vault` never import `DecayVerdict`. `heat` may use `strength`;
  the eviction VERDICT may not reach rank.
- **Test:** `tests/test_learning_decay_heat.py` (18).

**Part 3 — the observability panel: `GET /api/learning/health` + `HealthPanel` on the existing
Learning page** (no new route — §6.2 says the panel lives there). Each of the four additions traced
writer → DOM:
- **Health composite (0-100) with the 50-80% band** — `measure.health_composite`, weighted
  precision/capture/utilization/judge. Budget utilization **had no persisted writer at all**:
  `ambient.report()` computed it and sent it to a debug log, so a panel reading it would have
  rendered from a key nothing wrote. New writer `context._record_ambient_measurements` →
  `allocation_samples` (rolling 500) → `StagingStore.utilization()` → the composite → the DOM.
- **Judge MAE buckets (R10d)** — `judge_calibration.mae_buckets`. **Premise correction:** R10d says
  "predicted judge confidence", and nothing writes `overall` or `scores` to the ledger (the
  controller journals verdict/status/**evidence**), so an MAE over `overall` would have averaged
  parse defaults. Predicted confidence is therefore **sample agreement** from
  `judge_evidence.samples`, which `engine.dispatch_gate` genuinely writes; `VerdictRecord.samples` +
  `.agreement` were added and parsed from where the writer puts them. Ground truth is a **human
  divergence label only** — silence is not agreement, or the MAE would improve as the user stopped
  looking, so buckets report `mae: null` with an `unlabelled` count until a real label lands.
- **Attribution verdict history (R16)** — `attribution.verdict_history()` +
  `proposer_trust_report()`, both of which existed with no reader outside their own module.
- **Per-op LLM cost (R19e)** — new `StagingStore.cost_by_op()` over `flush_records.cadence`, the
  op identity every flush already carries.
- **Unmeasured is rendered as unmeasured.** Every score is `number | null`; a component with no data
  is EXCLUDED from the composite and reweighted, and says so. A fresh install reads "not measured
  yet", never 0/100. The FE **reads `error`** and renders the `LoadError` primitive.
- **Tests:** `tests/test_learning_health_panel.py` (31), 5 new route tests in
  `tests/test_learning_routes.py` (incl. one that drives two live writers and asserts the response),
  `web/src/pages/learning/HealthPanel.test.tsx` (10).

**Part 4 — per-tool approval identity → procedural priors: DROPPED, DEVIATION recorded.**
§4's row explicitly authorizes dropping it "if not worth it". The evidence says it is not, and that
building it would ADD an inert layer rather than close one:
1. The counters the RE-SPEC targets **no longer exist**. `stats.py:118-127` records that the previous
   `summary()` led with "tools approved 0 denied 0 auto 0" — "six writerless counters presented as
   measurements" — and they were deleted for exactly that reason.
2. The per-tool identity the wire needs **already exists and is live**:
   `MemoryService.record_procedural(tool=…, task_shape=…, outcome=…)`, called from
   `after_turn_review.py:132` and `learning/run_end.py:299`, persisting per-tool rows that the heat
   gate promotes to global priors.
3. The **consumer is inert**. `MemoryService.procedural_priors()` — the "procedural priors" the row
   names — has **zero readers outside its own tests** (repo-wide grep: 1 definition, 2 test
   references, no call site). Feeding it more rows would deepen an inert path, and giving it a live
   reader means adding a sixth ambient injection block — a real design change that `ambient.py:76`
   warns against ("vocabulary rather than extended") and that belongs to the allocator, not here.
**Left undone deliberately, and worth its own atom:** `procedural_priors()` is an inert reader, and
`record_procedural`'s declared `denied` outcome is an enum member no writer produces. Closing both
together is a coherent piece of work; half-wiring either here was the alternative and is worse.

**Part 5 — intent-adaptive weight profiles + ablation-delta sweep.**
**Premise correction:** the intent profiles were **already live** — `surfacing.classify_intent` +
`INTENT_WEIGHTS` are consumed by `allocate()` at the real call site. The missing half was §2.5's
ablation-delta rule ("every surfacing heuristic ships with a measured delta and is removed if ~0"),
which had no implementation anywhere.
- New `surfacing.ablation_deltas()` over a closed `ABLATABLE` set (intent, path_bonus, entity_prior,
  rank_decay, diversification), threaded as an `ablate` **parameter** rather than patched onto module
  globals — the sweep runs beside live traffic and a patched global would reorder a turn in flight.
  An unknown name **raises**: a typo would report delta 0.0, the same reading as a useless heuristic.
- **Writer:** `context._record_ambient_measurements`, cadence-gated by
  `StagingStore.ablation_due()` (daily) — five extra allocations per turn to learn something that
  changes monthly is a cost with no matching benefit. Runs on the SAME candidate pool as the live
  render via the extracted `ambient.sources_for()`, so it cannot measure a drifting second assembly.
- **Reader:** `latest_ablation()` → `/api/learning/health` → the panel, which names a `no_effect`
  heuristic as "a candidate for removal". Reporting the null result is the feature.

**Deviations / premise corrections (all recorded above):** part 4 dropped on evidence; R10d's
predicted confidence re-sourced from `judge_evidence.samples` because `overall` has no writer;
part 5's intent half found already shipped; part 1's gate found already written but unwired.
`ambient.render`'s source-building was extracted to `sources_for()` — mechanical, semantics
preserved, 381 ambient/surfacing tests unchanged and green.

**Gate:** `make lint` clean (black/isort/flake8 + mypy 800 files). Targeted
`-k "learning or decay or heat or calibration or attribution or memory"` = **1638 passed, 2 skipped,
1 xfailed**. Full-suite-only gates (`inert_surface_baseline`, `agent_reference`, `docs_lint_baseline`,
`config_roundtrip`) = **35 passed**. Web: `typecheck` clean, **FULL** `npm test` = **151 files /
1497 tests passed** (the global `tokenLint`/`inertUtilities`/`primitiveAdoption`/`uiDocs.drift`/
`consistencyAudit` ratchets included), `npm run build` clean.
**Two generated baselines legitimately changed and are regenerated in this same commit:**
`src/personalclaw/reference/{index,routes}.md` (+1 row for the new route: 631→632 agent-callable) and
`docs/design/consistency-audit.json` (`filesScanned` 439→442 for the new FE files — `driftHits` 7 and
`filesWithDrift` 6 are UNCHANGED, so no new drift was blessed).
**Known pre-existing reds, verified not mine.** The full suite ran **18269 passed / 5 failed**. All 5
are documented flakes: `test_workflows_controller.py::TestResumeCache` (2) failed under CPU
contention (`Timeout (>120s)`, `RuntimeError: Runner is closed`) and passed **85/85 re-run serially
with `-n 0`**; `test_harness_validate.py` (3) fails in ANY worktree — root cause read from the
assertion, `[Errno 2] No such file or directory: '.venv/bin/python'`, because the validator shells out
to a relative venv path the worktree has no copy of. The same file is **11/11 green in the main
checkout**, and this diff touches nothing under `harness/`.
**SEL:** the three new test files and the 5 new route tests add **0 bytes** to
`~/.personalclaw/security_events.jsonl` (measured against a verified 0-byte idle rate). The 8961-byte
delta from `test_learning_routes.py` as a whole is the file's PRE-EXISTING leak — its accept/reject
tests call `_audit` → `sel().log_api_access` without home isolation. Every test added here isolates
the home (`PERSONALCLAW_HOME` + `config_dir`).

### WF2LEA-13 — Close the procedural-memory loop (`procedural_priors` reader + the outcome contract) — DONE

Picks up exactly what WF2LEA-9's Part 4 recorded as "left undone deliberately, and worth its own
atom". Atom row + scope: `docs/roadmap/atomic/WF2LEA.md`.

**The finding: capture without readback, and two members nobody wrote.**
`MemoryService.record_procedural` had TWO live writers — `after_turn_review.record_procedural_outcomes`
(dashboard turn path, via `chat_runner`) and `learning/run_end.py`'s terminal-failure pass — while
`MemoryService.procedural_priors()` had **zero production callers** (repo-wide grep: 1 definition, 2
test references, no call site), and its own docstring named a consumer that did not exist ("for
recall-gated injection"). So every significant turn paid to capture how-to-work priors and used none
of them. The same docstring declared `Outcome ∈ {success, denied, corrected, failed}` while only
`success` and `failed` were ever written.

**Premise correction — how a procedural record actually reaches GLOBAL.** The reader returns global
records only, so the loop needs a promotion. That promotion is **not** the human-approved
`Kind.TIER_MIGRATION` path: `curator.file_promotion_suggestions` files those for learned-library
*entities*, and `dashboard/handlers/learning.py::_installer_for` dispatches only project-context,
skill-promotion and self-model proposals — there is **no tier_migration branch**, so accepting one
records a decision and moves no record's scope. The live promoters for a memory RECORD are
`MemoryService.promote_by_heat()` (heat ≥ 1.0 and recall_count ≥ 2, run from
`history._maybe_consolidate`'s maintenance tick) and `synthesize_failures()`, which writes its
collapsed prior at GLOBAL directly. Both are driven in the test; nothing is hand-promoted.

**Design, and what was rejected.** The reader joins the EXISTING allocator pool: `SLOT_KINDS` gains
`procedural → lesson`, because a how-to-work prior IS a learned lesson (only the teacher differs) and
because `ambient.py`'s own note is explicit that "a sixth kind would need a sixth slot, which is how
'one budget' becomes six again". Rejected: a sixth kind (that warning); the `memory` family, which
would have dodged the crowd-out question by ranking priors under the skills index rather than
answering it; and one candidate per prior, because `lesson` is the one kind EXEMPT from the
diversification cap, so N prior candidates would enter a non-sacrificial slot unrationed. It enters
as ONE all-or-nothing candidate at score 0.8 against a stored lesson's 1.0.

**Sharing the lesson kind broke two rails, and both are fixed rather than tolerated.**
(a) `render`'s "NOTHING MAY CROWD OUT A LESSON" retry tested `kind == "lesson"`. Measured: one
100-token lesson plus a short prior block at a 60-token budget drops the lesson as oversized and keeps
the priors — a kind-only check reads that as "a lesson survived", never retries, and `frame` then
prints "[Learned corrections — ALWAYS follow these]" over machine-observed priors, which is precisely
the "header asserting rules the model cannot find" that rail exists to prevent. It now discriminates by
KEY (`lesson:*`) via `_kept_a_lesson`. (b) The allocator renders a whole slot as ONE newline-joined
chunk, so a prior block outranking the second lesson makes the chunk OPEN with the priors' header:
`_is_lesson_block` now recognises the lessons chunk by "contains a bullet line" (it is still the first
bulleted chunk — priority 2, preamble has no bullets) instead of by its first line, and the block
carries an explicit `[End of how-to-work priors]` footer, the same device `[End of skills]` already is.

**What may be surfaced — the anti-noise mechanism is honoured, not routed around.** Only `success`
priors and `failure_synthesis` rows. A raw `→ failed`/`→ denied` row is `synthesize_failures` INPUT:
below the cluster threshold one failure is not evidence, and above it the synthesized "prefer an
alternative" prior is the durable form — printing both would defeat the mechanism and contradict it in
the same render. The filter lives inside `procedural_priors` (one definition, so a second caller cannot
bypass it), maps the closed vocabulary EXHAUSTIVELY with no default branch, and applies
`is_environment_failure_claim` on the READ side too, because `record_procedural` accepts a `detail`
that lands in the prior text and a promoted world-condition would be durable guidance telling the agent
a working tool does not work. The block is capped at 5 lines by its producer.

**Per-member decision.**
- **`denied` — WIRED.** It was the worst inert shape available: `synthesize_failures` has always
  clustered on `"→ failed" in text or "→ denied" in text`, so this was a **live reader of a value no
  writer produced**. The clean seam is the native runtime's outcome record, which derived `failed`
  from `result_str.startswith("Error:")` — and all five denial paths (hard deny-list, task-mode gate,
  PreToolUse hook, the user's reject, the unattended auto-decline) return an observation authored by
  `security.classify_denial`. So that function got the recogniser beside it
  (`is_denial_observation`, over a table of its own four wording fragments) and the runtime asks it
  rather than re-authoring the strings; the accumulator became `(tool, outcome)`. This is a
  correctness fix: labelling the user's refusal `failed` is what let failure synthesis publish "this
  tool is unreliable — prefer an alternative" about a tool that works fine and is merely not allowed
  here. The failure breaker still sees `failed` — a denial IS a reason to stop repeating the call.
- **`corrected` — REMOVED from the contract.** `after_turn_review` does detect corrections, but its
  `correction` flag is this turn's user message read as a reaction to the **previous** turn's work
  (`chat_runner` says so where it feeds the self-model observer), and nothing carries the previous
  turn's tool set forward — so any writer here would attribute the correction to whichever tools
  happened to run this turn. Nothing anywhere reads `→ corrected`, and the correction itself is
  already captured as a lesson in the block that ranks ABOVE this one. A wrong prior is worse than a
  missing one, so the member is deleted rather than documented-and-unwritten. `PROCEDURAL_OUTCOMES` is
  now a closed frozenset: `record_procedural` RAISES on anything else and the drain drops+logs (a turn
  must not fail over a label).

**Doctrine.** Rank stays heat, which WF2LEA-9's kernel note permits (heat weights usage above
recency, so recency may break a tie but never create one); *strength* alone and the prune/review
VERDICT never enter rank. Asserted through the new block: a 400-day-idle prior with 9 uses renders
above a fresh unused one, and `memory_service.py` contains no `DecayVerdict`.

**The chain is proven end to end, not just the reader.**
`tests/test_learning_procedural_loop.py` (25 tests) drives
`record_procedural_outcomes` ×5 (the live writer) → `promote_by_heat()` (the live promoter) →
`procedural_priors()` → `procedural_block()` → **`ContextBuilder.build_session_context()`**, asserting
the prior text appears in the real assembled context; plus the synthesis leg (3 scattered failures →
nothing surfaced → `synthesize_failures` → the collapsed prior surfaces), the `denied` leg (3 denials
→ the same collapse), a real `NativeAgentRuntime` driven three times for `success`/`failed`/`denied`,
`classify_denial` recognised over every declared `DENY_KIND_*` constant, the exhaustive-vocabulary
rail, the measured non-vacuity case for the key-based crowd-out rail, and a budget sweep
(20→4000 tokens) proving `used_tokens` never exceeds the ceiling.

**Gate.** `make lint` clean (black/isort/flake8 + mypy 802 files). Targeted
`-k "procedural or ambient or memory or learning or after_turn"` = **1617 passed, 2 skipped, 1
xfailed**. Full-suite-only ratchets (`inert_surface_baseline`, `agent_reference`, `docs_lint_baseline`,
`config_roundtrip`) = **35 passed** — no baseline needed regeneration (no new inert surface, no new
route, no new config key: the block rides the budget knob that already exists). Full suite:
**18407 passed / 3 failed / 30 skipped / 12 xfailed in 122s**; the 3 are the known worktree-only
`test_harness_validate.py` failures (`.venv/bin/python` relative path, 11/11 green in the main
checkout; nothing here touches `harness/`). No `web/` change. **Real-home rail:
`/Users/golani/.personalclaw` unchanged by this run.**

---

**DONE — `WF2LEA-14` (2026-08-12): lesson SCOPE now survives the write path, and the read path
honors it.** Filed here rather than under a memory plan because this plan owns the lesson write
path end to end: `WF2LEA-3` owns the three `/api/lessons` consumers, `WF2LEA-13` owns how a lesson
reaches a prompt. `MEMORY-GRAPH-AND-VAULT` owns the graph/vault, not lesson reach;
`PLATFORM-HARDENING-FLOORS` only *catalogued* `enum:MemoryScope.WORKSPACE` as inert — it is the
census, not the owner.

**The finding (the whole chain).** `mcp_memory.py:41-48` advertises `scope: "global" |
"workspace"` with `workspace` documented as *"required when scope='workspace'"*, and enforces it:
line 106-110 returns `"Error: workspace name is required when scope='workspace'"` before POSTing
`{"rule","category","scope"[,"workspace"]}` to `/api/lessons`.
`dashboard/handlers/schedule.py::api_lessons_create` read `rule`, `category` and `negative` and
**never** `scope`/`workspace`, then called `svc.write_lesson(rule, category, negative)`.
`memory_service.write_lesson` and `vector_memory.write_lesson` had **no scope parameter at all**,
so the row landed at the `MemoryRecord` default — global — and the symptom was already sitting in
`inert-surface-baseline.json` as `enum:MemoryScope.WORKSPACE`. Read side: `get_records`/`query`
could filter `scope`/`scope_ref`, but `get_lessons()` returned every `lesson.%` row unfiltered. So
a caller that carefully asked for a workspace-scoped lesson silently got a global one and was told
`{"ok": true}`.

**Write path threaded.** `api_lessons_create` → `resolve_lesson_scope` →
`memory_service.write_lesson(scope=, scope_ref=)` → `vector_memory.write_lesson` → the row's
`scope`/`scope_ref` columns. The endpoint does not trust the MCP tool's validation: the tool is one
client of a route anyone can call, and it was the *only* thing enforcing a contract the server
ignored.

**Read-side decision, and the measurement behind it.** A grep for a "current workspace" in
`memory_service.py`/`context.py` finds nothing, as expected. What *does* exist is stronger: memory
is already partitioned by working directory (`config.loader.memory_dir_for_cwd` →
`_ext/<slug(realpath(cwd))>`), and the `workspace-identity` prompt block tells the agent *"You are
operating in workspace (working directory): {{ws_path}}"* and documents `scope=workspace` as *"only
visible in this working directory"* — a promise the code did not keep. So a workspace **is** a
working directory, `scope_ref` is its `realpath`, and **option (a)** applies at the one read path
that has a cwd: `context.build_session_context(cwd)` now calls `lessons_context(cwd)`. Two facts
made this worth doing rather than falling back to (b): that read path is the one that assembles the
prompt, and the cwd partitioning is COARSE — `ContextBuilder.__init__` registers the gateway's one
store under both the no-cwd `_default` key and the running-workspace key, so several working
directories share one `memory.db` in production and the scope filter is the only thing separating
them.

**What a workspace lesson is visible to:** a session whose cwd `realpath`-matches its `scope_ref`
(its injected lessons block); the lesson INVENTORY — unfiltered `GET /api/lessons`, the MemoryPanel
list, the count badge, the CLI listing, and `delete_lesson` (a lesson you cannot see is a lesson
you cannot delete); and `GET /api/lessons?workspace=<abs path>` for its own directory.
**What it is invisible to:** any session in a different working directory; every recall path with
no workspace identity — `build_session_context()` with no cwd, the dashboard grill-tree recall in
`loop_routes.py`, `get_context_preview`; and a `?workspace=` filter for another directory. Matching
is EXACT — no basename, prefix or case-insensitive comparison — because two unrelated checkouts are
routinely both named `web`, and a fuzzy match would leak one project's private rule into another. A
ref that matches nothing simply shows no lesson, which is the safe direction to fail.

**Refusal behaviour (three, not two).** `scope="workspace"` with no workspace → 400 `"workspace is
required when scope='workspace'"`. An unknown scope string → 400 naming the two writable values.
And the third, found while designing: a **non-absolute** workspace → 400, because a bare `alpha`
would `realpath` against the *gateway's* cwd and land under a ref nothing can ever match — storing
a lesson nobody will ever see is as dishonest as storing it at the wrong scope. `session`/`agent`
are real `MemoryScope` members with no lesson write path and are refused by name. Nothing is
written in any refusal case (asserted). Absent *or* blank scope means the documented default; `""`
and `" "` cannot mean different things because no caller can tell them apart. Every `MemoryScope`
member is mapped with no default branch, and a test iterates the enum so a fifth member reds
instead of quietly becoming global.

**Two rails so a narrower write never mutates a wider record.** (1) A workspace lesson keys as
`lesson.ws.<md5(ref + rule)>`; sharing the global `lesson.<md5(rule)>` would have made a second
write with the same text an UPSERT that silently re-scoped the global lesson — the same defect, one
layer down. (2) The dedup pass iterates `_lessons_in_bucket(scope, scope_ref)` instead of every
lesson, because its substring / topic-overlap / cosine branches all *supersede the loser*, so an
unscoped pass would let a project-local rule soft-delete a lesson every other workspace still
reads. A global write is therefore byte-identical to before.

**Additive.** `scope` arrived with migration v6 as `DEFAULT 'global'`, so all existing lessons are
already global and stay visible everywhere; every read `COALESCE`s NULL → `'global'`; the axis
UPDATE fires only for a non-global write (the same "skip plain global/durable" rule `_apply_axes`
follows). No migration, no backfill.

**Inventory vs visibility are separate reads on purpose.** `get_lessons()` keeps its exact
semantics (every scope) and `lessons_visible_in()` is the new fail-closed read that decides what may
enter a prompt. Collapsing them into one defaulted parameter was rejected: whichever default won,
one caller would be silently wrong — an undeletable workspace lesson, or a workspace lesson leaking
into every recall.

**DEVIATION (docs, not code): `WF2LEA-14` declares `WF2LEA-3` as its dependency, and that row's
`⬜` status line is stale.** The reroute landed — `context.py` says so in its own comment ("no
parallel JSONL store (WF2LEA-3 retired it)"), `LessonStore` is gone, and `/api/lessons` reads and
writes `memory.db` `lesson.*`. The code and this log win over the header, per the plan-header rule.
The dep is recorded honestly rather than re-pointed at a green row.

**Product surface, recorded not half-built.** The dashboard MemoryPanel gains no scope CHOOSER: the
Studio has no working-directory identity to offer, so a picker would have to invent one, and manual
dashboard entry stays global — which is what that surface can honestly promise. The list response
now carries `scope`/`workspace` per row, so a future FE badge is a display change over data that
already arrives. The MCP `memory_list` inventory DOES label a workspace lesson with its directory —
listing it unlabeled beside global rules would present a project-local rule as a universal one, the
same confusion one surface over. No `web/` files changed.

**Inert surface.** `enum:MemoryScope.WORKSPACE` left `inert-surface-baseline.json` as a *result*:
the member now decides the stored key, the persisted axis, the dedup bucket and the visibility
query, and is driven end to end by the new tests. Regenerated with
`scripts/generate_inert_surface_baseline.py` in the same commit: enum 13 → 12, total 140 → 139.

**One test fake had to be realigned.** `tests/test_triggers_lifecycle_fire.py::_fake_service`'s
`_VS.write_lesson` stub did not accept the new scope keywords, so the service's pass-through broke
it. The stub now mirrors the real signature — a fake that drops the parameter would only prove the
fake's own shape.

**Gate.** `make lint` clean (black/isort/flake8 + mypy 804 files).
`tests/test_lesson_scope.py` = 22 passed. Targeted
`-k "lesson or memory_scope or schedule or mcp_memory"` = **274 passed, 2 skipped**. Ratchets
(`roadmap_dag_derived`, `inert_surface_baseline`, `docs_lint_baseline`, `agent_reference`) = 45+
passed; `agent_reference` needed `python -m personalclaw.manifest_reference` (the MCP tool
description changed) and `inert-surface-baseline.json` was regenerated by its own script — both in
this commit. Full suite: **18651 passed / 3 failed / 30 skipped / 12 xfailed in 221s**; the 3 are
the known worktree-only `test_harness_validate.py` failures (nothing here touches `harness/`).
No `web/` change. **Real-home rail: `/Users/golani/.personalclaw` unchanged by this run.**

### WF2LEA-6 — Step 6: template refiner AGENT + version store + Versions/Ledger FE tabs + skill sidecar overlays — DONE

The largest atom, and the one whose "propose-only tool set" turned out to be a trap. Four pieces
landed, each checkpointed: the monotonic version store, the skill sidecar overlays, the refiner
agent, and the four FE surfaces.

- **Version store (`workflows/versions.py`).** Append-only per-version files (`v001.json`, …) beside
  a `pinned.json` pointer, keyed by the def's own monotonic `version` so
  `get_version(name, run.spec_version)` reproduces the exact spec a past run executed. `record_version`
  (called from `native_defs.save_def`, best-effort) never overwrites a snapshot; `repin` (== rollback)
  moves only the pointer, so history survives a rollback — driven by an accept→new-version→rollback
  test. Lives under the already-inventoried `workflows/` tree (JSON, not a `.db`), so `audit_home`/the
  inventory ratchet need no change — same reasoning as `workflows/runs/`.
- **Runs pin the executed version.** `run_workflow_provider` constructed a run WITHOUT `spec_version`,
  so every trigger-fired (i.e. refiner) run recorded version 1. Fixed to pass `spec.get("version")` —
  the same value `service.py:516` already pins on the chat path.
- **Accept → new version.** Accepting a `template_diff` applies its typed ops (carried on the change
  manifest's `targeted_fix`, the field the inbox's `_tier_for` already reads) via `mutations.apply_batch`
  and saves through the writable provider → a new `refiner`-sourced version. Done in the async accept
  handler post-accept, not in `proposals.accept`'s sync installer (save_def is async).
- **Refiner agent.** Bundled `refine-template` workflow (trigger-fireable via the `run-workflow` action
  provider) whose stage runs the reserved `personalclaw-template-refiner` agent, holding only
  `refiner_evidence` (read: `cluster_safely` + `fenced_evidence` + `top_cluster`, S73/S79) and
  `propose_template_diff` (files a `TEMPLATE_DIFF` proposal after `check_diff`'s frozen-region gate,
  never applies). Both tools live in `mcp_core`.
- **Skill sidecar overlays.** An accepted skill *refinement* now writes ONE `skills/.overlays/<name>.json`
  (outside the locked dir) merged onto the base body at load time, replacing the old in-body append.
  Revert = delete that one file; the base `SKILL.md` and its `.pclaw-lock.json` are never touched, so
  `verify_skill_integrity` stays green across apply and revert.
- **FE.** `WorkflowDefDetail` gains Segmented Steps/Versions/Run-Ledger tabs, a maturity badge (R11,
  L0–L3 from static spec signals + ledger activity), and a Refine-now button; five new
  `/api/workflows/{name}/…` endpoints back them.

- **DISCOVERY — `AgentProfile.tools` is NOT enforced by the native runtime.** A profile `tools`
  allow-list is dead for native agents (the runtime offers the full registry regardless). A test asserting
  the profile's declared tools would have been theater. The real, handler-level propose-only enforcement
  is the workflow **research-leaf capability posture** (`mcp_shared.leaf_tool_denial` + `batch_compile`):
  a research leaf denies every write tool and all orchestration tools (incl. `workflow_author`), while a
  `propose_*`-named tool survives. So the tool-set test asserts against the SAME classifiers the runtime
  enforces (`is_write_tool` + `ORCHESTRATION_TOOLS`) and that the stage runs read-only — adding a direct
  template-write tool reds it for a reason the runtime shares.
- **DEVIATION — the refiner tools live in `mcp_core`, not `mcp_workflows`.** The workflows tool category
  enforces a `workflow_` name prefix and a hard 19-tool count; `propose_template_diff`/`refiner_evidence`
  fit neither. `mcp_core` already houses the sibling learning/template tools (`skill_promote`,
  `template_save_from_session`, `suggest_template`), so they landed there with `TOOL_META` entries.
- **DISCOVERY — jsdom has no `ResizeObserver`.** The responsive `HeaderActions` cluster (which the
  header-adoption ratchet requires a 2-control right slot to use) observes its container on mount and
  crashed every test that rendered the def page. A no-op `ResizeObserver` polyfill went into the shared
  web test setup — the right fixture (it never fires, so the cluster stays at its widest tier).

**Falsifications (restored from `cp` backups):** (1) adding a direct template-write tool to
`REFINER_TOOL_NAMES` reds `test_the_refiner_tool_set_is_propose_only`; (2) making rollback truncate
history reds `test_rollback_moves_the_pointer_and_keeps_history_intact`; (3) making overlay-revert delete
more than one file reds `test_revert_removes_exactly_one_file_and_keeps_the_lock_intact`.

**Gate.** `make lint` clean (mypy 826 files). Targeted `test_learning_*`/`test_workflows_*`/`test_skills*`
green; full suite green but for the known worktree-only `test_harness_validate.py` failures. Web:
`typecheck:web` clean, `vitest` 2478 passed, `npm run build` clean.
- **2026-08-17 — DONE (`WF2LEA-10`): skill resource tier + SKILL.md conformance** (#1551). Optional
  `resources:` frontmatter; `skill_invoke` returns body + an **L0 catalog** (declared paths and
  descriptions, never contents, block absent entirely when a skill declares none); new
  `skill_resource(skill, path)` loads exactly one declared path. Three independent defence layers, each
  proven live rather than read: `_norm_declared_path` rejects `..`/absolute/`~`/drive/backslash at
  **declaration and request** time; allowlist membership against `resources_for()`; post-`realpath`
  containment. Removing the allowlist alone left the traversal tests **passing**, so the `..` rejection had
  to be mutated too — and the secret still never leaked, caught by the third layer. Reads never execute
  (an executable resource that would `touch` a sentinel left it absent). Content is
  `fence_untrusted(source_type=skill_resource, …)`; the truncation notice sits OUTSIDE the fence;
  `RESOURCE_MAX_BYTES = 32_768`. A conformant third-party `SKILL.md` imports **unmodified** with foreign
  keys preserved-but-unused, and the **DANGEROUS floor stays non-overridable** (defended twice — mutating
  it reds the new test and the pre-existing `test_dangerous_not_overridable_by_force`). §2.4 tiering was NOT
  reimplemented in `skills/`. All registration points moved together: `_list_tools`, `_call_tool_inner`,
  `validation.MCP_CORE_SCHEMAS`, `manifest_meta.TOOL_META`, regenerated `reference/{index,tools}.md`
  (91→92) and `_RESIDUAL_CORE_TOOLS` (exact-equality, so a miss reds). Gate: lint EXIT=0, mypy clean on
  902 files, 84 passed + 1 pre-existing xfail.
- **2026-08-17 — SCOPE CALL + DISCOVERY (`WF2LEA-10`).** **Usage is recorded at skill granularity, not per
  resource** — `record_use(skill)` fires on a resource load exactly as `skill_invoke` does. Per-resource
  counters were deliberately NOT added because nothing reads them, which would be a writer-without-reader
  inert surface. **Consequence: the amendment's prose about a curator ageing unused *resources* is not
  delivered** — recorded as a scope call rather than a silent omission. Separately, a measured reachability
  note: `skill_invoke` is in `tool_retrieval._CORE_NAMES` but **`skill_resource` is not**. Harmless today —
  `_select` returns the whole pool when `total <= DEFAULT_K` (48) and `mcp_core._list_tools` declares 17 —
  but the catalog text *instructs* calling `skill_resource`, so if the native catalog ever exceeds K and the
  tool scores out, that instruction becomes unfollowable for a turn (`tool_search` is the escape hatch and
  `reduced()` does warn). Pairing it with `skill_invoke` in `_CORE_NAMES` is a one-line change deliberately
  left out of #1551: it alters retrieval behaviour and deserves its own test.

### 2026-08-22 — the header's 🔴 REMAINING block was three false claims; corrected and pinned

**No atom flipped** — `WF2LEA-5` and `WF2LEA-7` were already `done`. This is a legibility fix to the
plan HEADER, which is the first thing a session reads and was directing work at three items that
ship.

**Measured against `origin/main` = `05bba66e`.** The 2026-08-04 audit block claimed:

| claim | measured |
|---|---|
| `learning/accountability.py` has ZERO production importers | **1** — `learning/attribution.py` imports it and calls `accountability.attribute` / `Outcome` / `MIN_RUNS`, i.e. criterion 9's verdict path |
| `learning/detectors.py` has ZERO production importers | **6** — `after_turn_review.py`, `learning/attribution.py`, `learning/loop_end.py`, `learning/run_end.py`, `learning/template_gate.py`, `mcp_core.py` |
| `Cadence.SESSION_END`/`RUN_END` have no live call sites | SESSION_END at `dashboard/chat_utils.py:705`; RUN_END at `workflows/controller.py:4235` and `loop/watchdog.py:483` |

`assert_gate_covers_cadences()` was **run**, not read: it returns `[]`, so no declared cadence is
uncovered. The header also treated that guard "being uncalled outside tests" as a gap — it is not.
The function is a source scan whose entire job is to be asserted by a test, and
`tests/test_learning_accountability.py` pins both the empty gap set and a seeded phantom cadence.

**Counted by AST, not grep, and that mattered.** A text scan for `detectors` returns six files —
`web_source.DETECTOR_ORDER`, a comment about "cheap detectors" in `agents/native/tool_retrieval.py`,
several docstrings — **none of which import the module**. The stem is an ordinary English word here.

**Pinned by `tests/test_learning_flywheel_wiring.py`.** Both modules shipped complete, well-tested and
orphaned once; the atoms wired them and nothing pinned that. Delete the last call site and each module
keeps passing its own unit tests forever while the behaviour it exists for stops — the same
"present but inert" shape the original audit found, and a healthy-looking suite either way.

**🪤 A general docs-lint check for this claim class was BUILT, MEASURED, and REJECTED.** Do not
rebuild it. Added as a fourth `docs-lint` kind (`stale_zero_importer`: a doc claiming a module has no
importers when it has some), it swept every tracked doc and reported 22 findings across 6 files —
then inspection showed it cannot be made precise:

* **a markdown table is one paragraph with no sentence-ending period**, so the claim's sentence scope
  swallowed every module named in the table — that is where `gateway.py`, `history.py` and
  `cli_commands.py` came from in `atomic/WF2LEA.md`;
* **`**Done when:** … (module no longer has zero importers)` is an aspirational negation** that reads
  identically to the claim it inverts;
* **execution logs are full of past-tense narrative** — "shipped 1,096 lines … with zero importers in
  `src/`" — describing gaps that were then closed. `WORKFLOWS-V2-WORK-CONTAINERS.md` even labels its
  own block *"partially superseded — see the Execution log, which wins"*.

Separating "has none" from "had none, then we fixed it" needs tense, not pattern matching, and a gate
that flags correct history teaches people to delete the history. The generator was reverted; the
narrow checkable fact is asserted for the two named modules instead.

**Falsification.** Remove **all three** of `attribution.py`'s `accountability` import sites → the rail
reds (`exit 1`, *"has NO production importer under src/personalclaw"*), then restores byte-identical.
🪤 The first attempt removed **one of three** sites: the applied-count check passed, two importers
remained, and the rail correctly stayed green — an incomplete mutation is not a falsification, and
reporting that green as "the rail is insensitive" would have been wrong. The vacuity floor also
earned itself organically: it failed on first run because `_importers` mapped `learning/__init__.py`
to `learning.__init__`, a name nobody imports, so a module dozens of files import read as orphaned.
The floor caught a real bug in the helper rather than in the code under test.

**Gate.** `tests/test_learning_flywheel_wiring.py` 3 passed · `tests/test_learning_accountability.py`
+ `tests/test_docs_lint_baseline.py` below · `make lint` clean. No `web/` change; `dag.json` untouched.

### 2026-08-24 — S73's refiner could not fire: every event attributed to node `""` AND to run `""` (bugfix, no atom flipped)

**No atom flipped.** S73 ships; this is a measured defect inside it, found while closing the adjacent
triage-skip surfacing gap in PR #1974 and deliberately left out of scope there.

**The mismatch.** `refiner.cluster_failures` read `event["node"] or event["path"]`. The ledger writer
stamps **`node_id`** and **`instance_path`** (`journal.step_skipped` and every sibling emitter), and
**no writer in the repo has ever stamped `node` or `path` on a ledger event.** Every clustered event
therefore fell through to the empty string.

**Measured at the production entry point** (`refiner_tools.gather_evidence`, four real runs, four real
`step_skipped` rows via `Journal.step_skipped`, two distinct nodes):

| | clusters |
|---|---|
| before | `1` — `{node: '', signature: 'skipped ', count: 4}` |
| after | `2` — `{node: 'summarize', count: 2}` + `{node: 'translate', count: 2}` |

So §3.1's documented mechanism — *"a repeatedly SKIPPED step is a failure of the template"*, which the
bundled `refine-template` agent prompt spells out as *"delete a step users keep skipping"* — could not
name the step. Every skip collapsed into one anonymous bucket.

**Reader census (both sides checked).** `refiner.py` was the **sole** reader of bare `node`/`path`
against a ledger event. Every sibling consumer already reads the writer's names: `evals/harvest.py:406`,
`resume_account.py:262`, `workflows/introspection.py:173`/`:284` and `routing/feedback.py` (documented
at its module head). The other `get("path")` hits in `workflows/` are spec/state dicts, not events.

**🪤 The suites were green because reader and fixtures agreed on a fiction.** Every refiner test
hand-built `{"kind": ..., "node": ...}` — the same key the reader used and the engine never emits — so
`test_a_repeatedly_skipped_step_is_its_own_mechanism` passed against a shape no run produces. Fixing
the reader turned `test_refiner_fencing.py::test_the_attack_cannot_hide_among_real_failures` red
(`assert 'good' in {''}`), which is the false green surfacing, not a new break. Ledger-event fixtures
now use the writer's names; mutation-op dicts keep `node`, a different vocabulary (`mutations.OpKind`
addresses a target by `node_id`/`id` and uses `node` for an inline node *dict*).

**Two distinct nodes are structurally required.** With ONE skipped node the buggy and correct reads
produce an identical single cluster of identical count — only the label differs — so a one-node test
cannot see this defect. Measured: `{('skipped ', ''): 3}` vs `{('skipped summarize', 'summarize'): 3}`.
The vacuity floor asserts the writer's rows carry neither `node` nor `path`, so the test cannot drift
back to passing on hand-built rows.

**🔴 Attribution was NECESSARY BUT NOT SUFFICIENT — the mechanism still could not fire, and that half
is now fixed too.** On the adjacent line, `run_id = event.get("run_id")` read a key **no writer stamps
and `gather_evidence` never injected**, so `distinct_runs` was permanently `1`, `top_cluster`
permanently `None` (`MIN_RUNS_FOR_EVIDENCE = 3`, not 2), and the `refine-template` agent always took
its step-2 branch *"If there is no top cluster, STOP and propose nothing."* The consumer chain is live
and named — `refiner_evidence` MCP tool (`mcp_core.py:649` declared, `:1376` dispatched) →
`gather_evidence` → `cluster_safely` / `top_cluster`, held by `agents/defaults.py:372`, driven by
`workflows/bundled/refine-template/workflow.json` — so this was an **inert mechanism behind a live
reader**, not dead code.

**Premise re-verified at the writer, not assumed.** A real `Journal.step_skipped` row is exactly
`{actor, epoch, event_id, instance_path, kind, node_id, seq, ts}`. There is no `run_id`; the run key
appears only inside the opaque `event_id` (`<run>-evt-<seq>`), because a ledger record is run-scoped
by DIRECTORY (`runs/<run_id>/events.jsonl`).

**Chose INJECT over STAMP.** `gather_evidence` already holds `run.id` in its own loop, so injection is
one additive line at the one consumer that needs it. Stamping at the writer would duplicate the row's
own storage key onto every row of ~40 emitters to serve one reader, and rewrite the whole
`tests/fixtures/ledger_golden/` pair. The decisive argument is precedent, not size: `consumer_liveness.py:303`
already fans in over this same ledger with `{**event, "run_id": str(run.id)}`, so injection IS the
established convention for a cross-run reader — stamping would have been the novel move. Injection is
placed LAST in the dict merge so the authoritative directory wins over any same-named field.

| through `mcp_core._call_tool("refiner_evidence", …)`, 4 real runs, real writer | `top_cluster` |
|---|---|
| before | `None` — clusters correct (`summarize`/`translate`) but `run_ids: ['']`, `distinct_runs: 1` |
| after | `{node: 'summarize', signature: 'skipped summarize', count: 3, distinct_runs: 3, run_ids: [3 real ids]}` |

**The `distinct_runs >= 3` floor still holds — asserted in the NEGATIVE direction.** §3.1's power
discipline, as `MIN_RUNS_FOR_EVIDENCE`'s own comment states it: *"Below this a 'pattern' is one bad
afternoon, and a template edited from it is a template edited from noise."* So injecting the run key
must not smuggle an anecdote past it. Three skips of one node inside a SINGLE run still yields
`top_cluster: None`, and that test's vacuity floor asserts the cluster EXISTS first
(`count == 3`, `rank > 0`, `distinct_runs == 1`) — otherwise `None` would pass for the wrong reason.
Note the floor counts distinct RUNS, not occurrences.

**`at_node_id` reconciled AT THE EMITTER (item 2).** `run_abandoned` stamped the ledger's only
divergent spelling of the node field, so an abandoned run clustered under `''` even after the
`node_id`/`instance_path` read landed. Renamed to `node_id`; WORKFLOWS-V2 §5's event table moved with
it. Not aliased in the reader — a second accepted spelling is the dual path the clean-break tenet
forbids, and it would leave the next cross-kind consumer (`evals/harvest`, `resume_account`,
`workflows/introspection`, all of which read `node_id`) to rediscover the trap. Nothing semantic
distinguished it: `step_failed` also means "the node this went wrong at". Free to rename — **zero
production callers**, and `test_ledger_golden` probes kinds through `write(kind, marker=…)`, not the
typed emitters, so no golden row churns.

**🔴 `user_edited_mid_flight` — RECORDED, NOT BUILT (item 3), and the earlier recording of it was
WRONG.** It stamps no node at all. The previous entry said this "needs the emitter's signature to
change"; measured, **a `node_id=` kwarg on the emitter is the wrong shape.** The sole call site is
`controller.py:1453`, `user_edited_mid_flight([o.to_dict() for o in result.ops])`, and `ops` is an
unbounded array at the user surface (`mcp_workflows.py:254`, `required: [run_id, ops]`), so ONE event
legitimately carries an N-node batch. `Op.to_dict()` also emits `node_id` only when non-empty — a
positional `insert` addresses its target by `parent_id + index` and carries no node id at all. The
honest fix is therefore reader-side per-op attribution, which is a clustering-SEMANTICS decision, not
a signature change: one event would become N cluster contributions, turning `Cluster.count` from an
event count into an op count, and double-counting one run's evidence toward the power floor when a
batch touches the same node twice. Out of scope here by the bounded-change rule; §3.1's "gold" signal
stays unattributed and this is the honest residual.

**Falsification** (six; every restore `cp` from a pre-mutation file copy, never `git checkout`, each
verified byte-identical). (1) Reverted the live read to `node`/`path` → the attribution test reds on
its load-bearing assertion, printing `Cluster(signature='skipped ', node='', count=3)`. (2) Rewrote
that test to hand-build rows carrying `node` → its **vacuity floor** reds, proving the floor is
load-bearing. (3) Dropped the `run_id` injection from `gather_evidence` → the end-to-end test reds
*"the mechanism did not fire"* AND the negative test reds on its own floor (`run_ids == ['']`), which
is the point: without the floor the negative would have kept passing for the wrong reason. (4) Weakened
`top_cluster`'s floor to `>= 1` → the negative test reds *"one run's evidence cleared the power floor"*.
(5) Restored `at_node_id` on the emitter → the abandoned-run test reds *"the third spelling is still
emitted"*. (6) Made `LedgerWriter._append` stamp `run_id` → the end-to-end test's vacuity floor reds
*"writer already stamps run_id"*, so a writer that starts stamping it can never silently mask a dropped
injection.

**Gate.** `make lint` clean (black 2007 files, isort, flake8, mypy 992 sources). Refiner suites
120 passed. `make test` below. No `web/` change; `dag.json` untouched.

- **2026-08-26 — DONE — §2.5 / LEARN-R4: the `surfacing_events` TABLE, its writer and its 90d
  prune.** Branch `feature-learnr4-surfacing-events`. Closes the one item S71 explicitly deferred
  ("**NOT DONE (by scope):** the `surfacing_events` TABLE and its 90d prune on the curator tick"),
  and with it the named blocker on **EVALUATION-SUBSTRATE `ES-3`**, which was `BLOCKED (E6,
  external)` because §5.2's source (a) needed a table that "appears in the entire tree exactly ONCE
  and only as prose — no table, no schema, no reader, no writer".

  **The gap was real and measured before designing.** `git grep surfacing_events` over `src/`
  returned two hits, both prose: `dashboard/handlers/learning.py` explaining why it could not read
  the table, and `evals/retrieval_bench.py` explaining that it shipped a substitute instead. Three
  consumers named a table nobody had created.

  New `learning/surfacing_events.py`: `SurfacingEvent` + `SurfacingEventStore` in `learning.db`,
  declaring its own table lazily with `CREATE TABLE IF NOT EXISTS` over `StagingStore._cursor()` —
  the house pattern `usage.UsageStore` already uses on the same connection. No migration machinery
  was written; the idempotent declaration is the whole story, per CONTRIBUTING §"the lifecycle
  mental model".

  **Every column is justified by a live reader, and the shape is deliberately narrow.** §2.5 named
  four fields ("entity kind, matching arm, confidence, session/turn"); `measure.per_arm_precision`
  pins `kind`/`arm`/`used` as the exact keys it destructures; `retrieval_bench.mine_knowledge_qrels`
  needs `query` + `entity` (its `intent_outcomes` substitute reads exactly that pair); the prune
  needs `created_ts`. Nothing else was added — a wide speculative table would have been worse than a
  narrow correct one, and `ES-3` can now switch its source over without a schema change.

  **The writer is reached from production, which was the point.** `allocate_skills` records one
  event per candidate it was OFFERED (not per candidate included — precision is used ÷ *surfaced*,
  and dropping the losers would make the denominator the numerator), and `used` comes from
  `SkillAllocation.loaded`, an existing MECHANICAL derivation: content that really reached the
  prompt, REFUSED excluded. That seam is the only point holding both halves at once, so no second
  marking pass can disagree with the allocator that made the judgement. `allocate_skills` gained
  one `session` kwarg, passed from `context.py`'s `build_message` call site. The 90d prune runs on
  `history._run_learning_curator`, the tick §2.5 assigns it.

  **🔴 DISCOVERY — `usage.UsageStore.record` has NO production caller.** The handler docstring this
  work replaced claimed the surfaced/used counters "have a live writer per session flush"; the only
  importers of `learning.usage` are two READERS (`handlers/learning.py`, `history.py`) and the
  package re-export. So `_precision_from_usage` returned `(None, 0, 0)` on every box and the health
  panel's surfacing row rendered an empty state — the exact failure its docstring was written to
  avoid, one store over. It is now `_precision_from_events`, reading the table that is actually
  written, windowed on the panel's own `days`. The per-arm breakdown is deliberately NOT published
  as a new response field: no surface reads it yet, and an unread field would red `inert-surface`.
  `UsageStore`'s missing writer is left as a separate finding — it is not this atom's scope.

  **DEVIATION — no config knob.** §2.5 states one retention number, so `DEFAULT_RETENTION_DAYS = 90`
  is a module constant. `config/loader.py` sits at 5900 lines against a 6000 ceiling with a `>= 100`
  headroom assertion, so a knob would also have forced an unrelated extraction; it is untouched at
  5900 and `test_structural_baseline.py` is green.

  **NOT DONE (by scope, and named so it is not mistaken for finished):** only the SKILLS arm is
  instrumented. §2.5's other three mechanical-used clauses (template run started, run outcome
  success/failure, lesson cited by `after_turn_review`) and the ambient render's lesson/memory/
  persona candidates write no events yet — `Allocation.included` carries `(kind, key, tier)` and no
  arm, so wiring those paths is per-path work in the modules that own them, exactly as S71 said of
  `Candidate.arm`. `ES-3` needs the knowledge arm, which this covers via `query`/`entity`/`used`.

  **Falsification** (two; each restore `cp` from a pre-mutation file copy, never `git checkout`,
  each verified byte-identical and `git status` clean). (1) Deleted the `_record_surfacing_events`
  call from `allocate_skills` — the definition stayed, so `git grep` went 2 hits → 1 — and the
  anchor test reds on its own message, *"a real surfacing wrote no row — the writer is not reached
  from production"*, plus two siblings: the pre-existing writerless state IS detectable. (2) Renamed
  the table in the `CREATE TABLE IF NOT EXISTS` declaration only — a DIFFERENT red, and an error
  rather than an assertion: `sqlite3.OperationalError: no such table: main.surfacing_events`.

  **Gate.** `make lint` clean (black 2147 files, isort, flake8, mypy 1060 sources). Targeted 209
  passed / 209 collected (new suite + staging/usage/measure + skill allocation + config round-trip +
  structural baseline + promotion wire); handler/surfacing sweep 242 passed / 242 collected; wider
  subsystem sweep 364 passed / 364 collected. `python scripts/gate_report.py`: all 6 gates PASS,
  `inert-surface` included — the new writer and reader are both reached. Real-home rail confirmed
  unchanged on every run; the new suite's `home` fixture patches both `config_dir` bindings plus
  `staging._default_home` and ASSERTS the redirect before creating the database. No `web/` change.
  `dag.json` untouched — `ES-3`'s `blocked_reason` is the owner's to update, and `LEARN-R4` has no
  atom row of its own to flip (the nearest, `WF2LEA-2`, is already ✅ and its `done_when` covers the
  per-arm report's pure functions, which is precisely what S71 shipped without the table).
