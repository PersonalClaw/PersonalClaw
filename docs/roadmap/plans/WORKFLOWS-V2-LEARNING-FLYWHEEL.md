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
