# Plan: Learning Flywheel — One Lifecycle for Lessons, Skills, Memory, and Templates

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)
**Created:** 2026-07-11
**Revised:** 2026-07-12 — 21 approved research recommendations folded in; every load-bearing claim re-verified against code (recon 2026-07-12)
**Depends on:** Steps 1-4 are v2-INDEPENDENT (can front-run everything); steps 5-8 need WORKFLOWS-V2 Slices 0-3 (Run Ledger events are an engine acceptance criterion there)
**Companions:** WORKFLOWS-V2-TASKS-SOPS (SOP→template migration is the landing zone), WORKFLOWS-V2-LOOPS-EVOLUTION (judge sequencing), WORKFLOWS-V2-UNIVERSAL-PLANNING (maturity-gated autonomy modes)

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
- **Action providers:** the refiner (§3.1) runs as a trigger-fired workflow via the existing `run-workflow` action provider — already in `ALLOWED_HOOK_PROVIDERS` (validation.py:555), so NO allowlist change is needed. If any future slice ships a dedicated learning action provider, it must be added to that frozenset or `hook_create` rejects it.
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

**Provenance.** A competitive gap analysis (Genspark + Manus, 2026-07-28/29) plus a code audit surfaced three items the owner approved for planning. **Two of the three are narrower than the competitive framing implied**, and this amendment says so rather than restating the vendor pitch — §2.4 already owns a more sophisticated version of one of them.

### (a) Skills progressive disclosure — we have 2 of 3 levels; only the RESOURCE tier is missing

**What Manus ships** (verified from their docs): a three-level load model — L1 metadata (name+description, always loaded, ~100 tokens/skill), L2 instructions (the `SKILL.md` body, loaded on slash-command trigger, <5k tokens), L3 resources (scripts, reference files, assets, loaded only when referenced). They adopted **Anthropic's Agent Skills open standard** (`SKILL.md` + YAML frontmatter) rather than inventing a format.

**What PersonalClaw already has — the framing "we have 1 of 3" was wrong:**
- **L1 exists.** `SkillsLoader.get_context()` documents it in its own docstring: "Always-loaded skills: full content included. Other skills: summary with instruction to load via bash when needed." Surfacing is embedding-ranked with a keyword-union fallback (`skills/surfacing.py`, threshold 0.55, mtime+model-keyed embedding cache in `.skill_embeddings.json`), capped by `skills.max_triggered`.
- **L2 exists.** `skill_invoke` loads the full body on demand (`mcp_core.py:575` → `loader.load_skill(name)`, frontmatter stripped, usage recorded so the curator sees on-demand invocations), and its tool description explicitly steers the model to prefer it over reading the file.
- **L3 does NOT exist.** A skill's bundled scripts/reference files/assets have no addressable, on-demand load path — the agent must discover and read them as ordinary files, which means they are either absent from consideration or pulled in wholesale.
- **§2.4 of this plan already designs something stronger than Manus's fixed three levels:** persisted L0/L1/L2 tiers per entity, a *ranked slot allocator* that degrades tier before dropping items, L2 gated at ≥0.9·top_score with a hard cap of ~3 full-detail items, and an L0 catalog of unloaded near-misses. **Do not replace that design with Manus's simpler one.** This amendment adds the missing resource tier *underneath* it.

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

**Format alignment (owner decision, §Owner tasks):** both competitors converged on Anthropic's `SKILL.md` + YAML-frontmatter standard, and PersonalClaw already uses `SKILL.md` with YAML frontmatter (`skills/loader.py:65` serializes frontmatter lines; `_parse_frontmatter` reads them). Worth an explicit conformance check so imported third-party skills work unmodified — cheap interoperability on a format we already substantially share.

### (b) Agent-authored + retroactively-promoted skills — check before building

**What exists:** `skill_remember` (`mcp_core.py`) already captures a user-taught skill as a **session-live draft** — active immediately for the rest of the chat, with an end-of-chat prompt to save permanently (to this agent or all agents) or forget. `skills/proposals.py` provides the full propose/accept/reject queue (`enqueue`/`list_pending`/`accept`/`reject`, with `kind="new"` and a `refine_target` for refinements). `skills/curator.py` ages `auto/` skills active→stale→archived by last use. `skills/ephemeral.py` exists for the session-live tier.

**So the gap is not "agent can't author a skill" — it is narrower:**
1. **Retroactive promotion of a completed run/conversation** into a skill. Both competitors ship this ("save this process as a Skill" after a successful task); PersonalClaw captures at the moment the user *teaches*, not retrospectively from *what worked*. §3.2 of this plan ("repeated ad-hoc work → suggested templates") is the adjacent mechanism and the natural home — promotion should feed **its** proposal queue rather than a second path.
2. **Agent-initiated authoring** (the agent proposing a skill unprompted, having noticed it repeated itself) — must land as a **proposal**, never a silent write, per this plan's propose-don't-write doctrine.
3. **GitHub-repo import** of a skill (Manus's community-distribution path). Note the existing install rail is not bypassable: skills install through the supply-chain scanner at the source's trust tier (`skills/marketplace.py:117` — "payload to quarantine, scans it at this marketplace's trust tier, and commits the exact scanned bytes"), with a **non-overridable DANGEROUS floor**. Any import path must ride that rail, unchanged.

### (c) Self-updating project context with an approval gate

**What Manus ships:** you ask it to review a conversation; it proposes updates to the project's instructions, files, and skills, explains what and why, and **"Project context is not updated without authorization."** Prompt-triggered, not automatic — notably, their own docs show no evidence of an automatic cross-session personal memory that learns unasked.

**Why it fits here cleanly:** this is exactly this plan's `Capture → Stage → Propose → Curate` pipeline (§2), with the "project" as the target entity. It also matches the platform's existing discipline elsewhere: `memory_lint.py` has **exactly one** auto-fix (purging superseded rows past 90 days) and everything judgmental is **flagged, not changed**; `memory_service` uses **supersession-by-pointer** rather than lossy deletion. The mechanism is already the house style — what's missing is the project-scoped target and the review-this-conversation entry point.

**Contract:** a `project_context_review` capability that (1) reads a conversation or run, (2) emits proposals of kind `project_instruction` / `project_file` / `project_skill` into the existing §2.2 queue with a rationale per item, (3) **writes nothing** until accepted, and (4) records the decision in decision memory (§2.2) so the same rejected suggestion doesn't re-surface. Prompt-triggered by default; if a cadence is added later it must remain propose-only.

### Amendment task table (extends this plan; run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| E1.1 | Skill resource tier: optional `resources:` frontmatter declaration; `skill_invoke` returns body + an L0 resource catalog (never contents); new `skill_resource(skill, path)` tool resolving ONLY declared paths, traversal-rejecting, size-capped with truncate-notice, usage-recorded, read-never-execute | `src/personalclaw/skills/loader.py`, `src/personalclaw/mcp_core.py`, tests | a skill with resources exposes a catalog at ~one line each; loading one returns just that file; an undeclared or traversal path is refused; an oversized resource truncates with a visible notice; a script resource is read, never run |
| E1.2 | `SKILL.md` frontmatter conformance check against the Agent Skills standard (per the owner's ruling in Owner tasks): document the delta, and accept a conformant third-party skill unmodified through the EXISTING scanner rail | `skills/loader.py`, `docs/reference/`, tests | a standard-conformant skill imports and runs unmodified; the scanner rail is unchanged (DANGEROUS floor still non-overridable — test it) |
| E1.3 | Verify-then-build promotion: audit what `skill_remember` + `proposals.py` + §3.2 already cover, then add ONLY the missing retroactive path — "promote this completed run/conversation into a skill" feeding §3.2's existing proposal queue (no second queue) | `skills/proposals.py`, the §3.2 seam, tests | a successful run can be promoted to a skill proposal; the agent may propose unprompted but never writes; a rejected proposal is remembered in decision memory and does not re-surface |
| E1.4 | `project_context_review`: read a conversation/run → typed proposals (`project_instruction`/`project_file`/`project_skill`) with per-item rationale into the §2.2 queue; nothing written without acceptance; decision recorded | the learning pipeline modules, tests | reviewing a conversation yields reviewable proposals; declining changes nothing; accepting applies exactly the accepted items; a second review does not re-propose a rejected item |
| VE | Validation as a user: author a skill with a script + reference resource, confirm the catalog appears and one resource loads on demand while the other does not; import a third-party standard-format skill and confirm the scanner gate still applies; complete a real task, promote it to a skill proposal, accept it, and confirm it surfaces on a later relevant turn; run a project-context review, decline it (verify no change), then accept it (verify exactly the accepted items applied); full local gate | — | holds |

### Owner tasks (real world)
1. **Rule on `SKILL.md` standard conformance** (blocks E1.2): adopt the Agent Skills standard's frontmatter keys exactly where they differ from ours, or document the delta and stay divergent. Both competitors adopted it; the interoperability upside is that third-party skills work unmodified, and `anthropics/skills` is where the community publishes.
2. **Confirm prompt-triggered-only for (c).** The plan makes project-context review manual, matching Manus. A cadence could be added later, but must stay propose-only.

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
