# LEARNING-VISIBILITY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/LV.md`](../atomic/LV.md) as 7 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Learning Visibility — Make the Flywheel Felt

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "let's plan for this gap closure")
**Created:** 2026-07-18
**Wave:** 1 (S1-2) + 2 (S3) + 3 (S4, with EVALUATION-SUBSTRATE)
**Depends on:** LEARNING-FLYWHEEL steps 1-4 (this plan complements the flywheel — it surfaces and pulls forward, never forks its lifecycle), INBOX-NOTIFICATIONS-UNIFICATION S4 (proposals as inbox items). EVALUATION-SUBSTRATE S1-2 for the S4 benchmark.
**Scope:** the market-visible half of learning: a pulled-forward end-to-end slice (run → proposed skill → approval → used-next-time-and-says-so), legibility surfaces, the refinement arm's UX, and a published benchmark. **Soul guardrail:** **propose-don't-write is inviolable** — every learned artifact passes the approval surface; no auto-write ships under any demo pressure (auto-written learning is OWASP ASI06 surface). If a slice can't be made visible without weakening a gate, the slice waits. Attribution shows *honest counts only* — no invented time-savings math before S4 measures it.

---

## Context (code recon, 2026-07-18 — more exists than the outline assumed)

- **Synthesis machinery exists:** `after_turn_review.py` — `run_after_turn_review`, **`run_skill_ladder_review`** (skill-ladder synthesis prompt + JSON parse), `record_procedural_outcomes`, and capture-hygiene primitives already in place (`is_correction_signal`, `is_environment_failure_claim` — environment failures are never learned).
- **The refinement shape exists:** `skills/proposals.py::enqueue(kind="new"|…, refine_target=…, source_excerpt=fenced)` — proposals already model refinement targets and **fence source excerpts** ("a poisoned trace can't direct any model that later renders it"). Queue is capped (`_MAX_PENDING`).
- **Attribution substrate exists:** `skills/usage.py::SkillUsageStore` (`record_use/record_uses`, per-skill counts/recency, prune) and `surface_skills`/`search_skills` matching (`skills/surfacing.py`); `learn.py::LessonStore` for lessons.
- **Gap, precisely:** the ladder review's *wiring* (where it fires — verify chat-after-turn vs loop end-of-run coverage), the *surfaces* (nothing shows "learned" or "used" to the user), the *refinement trigger* (nothing detects a stumble and enqueues `kind="refine"`), and *proof* (no benchmark). This is wiring + UX + measurement, not new learning machinery — exactly the right shape.

## Design

- **S1 slice:** verify + extend `run_skill_ladder_review` firing: chat after-turn (existing — confirm) AND loop end-of-run (add at the loop-complete seam); hygiene preserved (gate predicates already imported there); proposals land in the existing queue → surface as inbox `proposal` items (plan 42) or the skills approval inbox pre-42 → on accept, the skill enters the store → next matching run loads it via `surface_skills` → `record_uses` fires → **the run says so** (S2 chips).
- **S2 legibility:** (a) per-run attribution — chat turn/loop run panel shows "used N skills you approved" chips (names on hover/tap) fed by the loaded-skills list the runner already passes to the ladder prompt (`loaded_skills` param — confirm plumb-through to the frontend event stream); (b) session "learned" chips — what after-turn captured/proposed this session (facets, lessons, proposals) with tap-through to approve/edit/reject; (c) weekly digest section — new/refined skills, promoted facts, pending proposals (rides plan 42's digest rule).
- **S3 refinement arm:** stumble detection at the after-turn seam when a skill was loaded: correction signal (`is_correction_signal`) OR failure-then-retry pattern OR explicit user rejection → capture the delta → `enqueue(kind="refine", refine_target=<skill>, …)` with a **unified-diff body** against the current SKILL.md → proposal surface renders the diff (approve = versioned overwrite via the accept path + provenance line in the skill frontmatter; version history = the store's file history + a `provenance:` frontmatter list). Coordinates with LEARNING-FLYWHEEL's refiner (statistical gates arrive with its Wave-3 steps and slot behind the same surface — same queue, same kind, stronger acceptance logic; no fork).
- **S4 benchmark:** EVALUATION-SUBSTRATE template study — fixed task set (owner-curated, ~10 repeatable research/ops tasks), paired runs skills-on vs skills-off (fresh fixture homes, same model+config), metrics: completion, tool-call count, wall time; publish methodology + results (site, plan 36) *including modest results honestly*.
- **User model:** deliberately deferred to LEARNING-FLYWHEEL's self-model step; this plan reserves a "Your model" digest card slot only (one owner, one mechanism).

## Contracts & Interfaces (builds ON existing synthesis machinery — [AGENTS.md](../../../AGENTS.md) §3.6)

### C1 — Skill-draft synthesis (REUSES `after_turn_review.run_skill_ladder_review` + `skills/proposals.enqueue`, §3.6)
No new synthesis engine. The run-end hook builds an `enqueue()` call:
```python
enqueue(slug=…, description=<trigger-shaped>, triggers=…, procedure_md=…,
        session_key=…, created_at=…, kind="new", source_excerpt=<fenced>)
```
Then `emit_attention_item(source="skills", kind="proposal", refs={"skill_proposal": pid}, …)` (plan 42 C5) so it lands in the one attention store. Hygiene gates (`is_correction_signal`, `is_environment_failure_claim`) already imported there — reuse, never bypass. Budget: ≤1 synthesis call per run.

### C2 — Attribution (additive meta on existing turn/loop events — NO new event channel)
Runner already passes `loaded_skills` to the ladder prompt (verified `after_turn_review._build_ladder_prompt`). Plumb it to the frontend event meta: `{used_skills: ["slug", …]}`. `SkillUsageStore.record_uses(names)` (§3.6) fires on load. UI chip: "used N skills you approved" (names on hover). **Honest counts only** — no time-saved math until S4.

### C3 — Refine proposal (REUSES `enqueue(kind="refine", refine_target=<skill>)`, §3.6 — the field already exists)
Stumble detector at the after-turn seam (only when skills were loaded): correction/failure-retry/rejection → build a unified diff against the current SKILL.md → carry it in `procedure_md` with `kind="refine"`. On accept (existing `accept(pid, procedure_md=…)`), append `provenance:` frontmatter (date, run ref, pid). Cap: 1 refine/skill/day. The flywheel's statistical gates (its Wave-3 steps) slot behind this same surface unchanged.

### C4 — Benchmark (EVALUATION-SUBSTRATE template study)
`docs/roadmap/research/learning-benchmark-protocol.md` defines: task-set schema, paired skills-on/off design (fresh fixture homes, fixed model+seed), metrics {completion, tool_calls, wall_ms}, honesty rule (publish regardless). Implemented as an eval-substrate study; results → site (plan 36).

### Integration points
- **Calls:** `run_skill_ladder_review`, `proposals.enqueue/accept/reject`, `SkillUsageStore.record_uses`, `surface_skills`, `emit_attention_item` (plan 42), the loop-complete seam (T1.2 locates).
- **Called by:** the after-turn path (chat) + loop end-of-run.
- **Consumed by:** plan 42 (proposals ARE inbox `kind=proposal`); its digest builder renders the "What I learned" block.
- **Coordination:** LEARNING-FLYWHEEL owns the self-model (user "About you" doc) — this plan reserves a digest card slot only, builds no parallel mechanism (one owner per §1.3).
- **Inviolable:** propose-don't-write — every artifact passes the approval surface; zero auto-write paths.

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — The end-to-end visible slice

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Map the ladder-review wiring: where `run_skill_ladder_review` fires today (grep callers; read `should_review` gating), what it enqueues, and whether loop end-of-run is covered — record the map in the Execution log BEFORE changing anything | — (read-only recon task) | Execution log carries the caller map + gaps |
| T1.2 | Extend firing to loop end-of-run at the loop-complete seam (locate via `loop/` completion path), same hygiene predicates, same queue; budget: at most one synthesis call per run (no per-cycle spam) | the loop completion site, `after_turn_review.py` if a shared helper is extracted | a completed multi-step fixture loop enqueues ≤1 proposal; environment-failure fixture enqueues 0 |
| T1.3 | Confirm/complete the accept→surface→use loop: accepted proposal's skill is surfaced by `surface_skills` on the next matching prompt and `record_uses` fires (add the missing wiring if any — verify where record_uses is called today) | `skills/{surfacing,usage}.py` call sites | integration test: propose→accept→matching-prompt→usage count increments |
| V1 | Validation: fixture home → run a repeatable 3-step task → proposal appears → approve → repeat task → skill loads and usage records; all under gates (no auto-write anywhere — verify by store inspection between steps) | — | full loop observed; ledger written |

### Session 2 — "What I learned" legibility

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Plumb per-run loaded-skills + usage into the frontend event stream (additive meta on existing turn/run events — locate the tool-result meta path; no new event channel) | runner event emission site, `web` chat/loop panels | run panel shows "used N skills" chip with names; zero new WS/SSE channels |
| T2.2 | Session learned-chips: after-turn captures (facets/lessons/proposals) render as chips with tap-through to the relevant approve/edit surface | after-turn result plumb-through, `web/src/pages/chat/` components | a correction in chat yields a visible chip within the session; tap lands on the right surface |
| T2.3 | Digest section: learning summary block (new/refined/pending counts + names) registered with plan 42's digest builder (coordinate; if 42 S5 not landed, render the same block on the skills page header and file DISCOVERY) | digest builder extension or skills page header | weekly digest (or fallback header) shows the block with real counts |
| V2 | Validation: a week-compressed fixture (seeded history) produces a truthful digest block; chips verified across chat + loop surfaces; reduced-motion/theme checks on new UI | — | holds |

### Session 3 — Refinement arm (Wave 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Stumble detector at the after-turn seam (only when skills were loaded): correction signal OR failure-then-retry OR explicit rejection → delta capture; hygiene: environment failures excluded (existing predicate) | `after_turn_review.py` | unit tests per trigger; env-failure fixture never triggers |
| T3.2 | Refine proposal: build unified diff against current SKILL.md → `enqueue(kind="refine", refine_target=…, source_excerpt=…)`; cap: one refine proposal per skill per day | `after_turn_review.py`, `skills/proposals.py` (only if the diff body needs a field — prefer procedure_md carrying the diff + kind flag) | stumble fixture yields exactly one refine proposal with a valid diff |
| T3.3 | Diff rendering + versioned accept: proposal surface renders the diff (reuse the web Markdown/diff component if present — locate; else minimal diff view); accept applies via the existing accept path with `provenance:` frontmatter appended (date, run ref, proposal id) | proposal surface component, `skills/proposals.py::accept` | approve applies the diff; skill frontmatter carries provenance; reject leaves the skill untouched |
| V3 | Validation: seed a deliberately-flawed skill → run → stumble → refine proposal with sensible diff → approve → re-run succeeds with the refined skill | — | full arc observed |

### Session 4 — The public number (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Benchmark protocol doc: task set schema, pairing design (fresh homes, fixed model/config/seed), metrics, exclusions, and the honesty rules (publish regardless of magnitude) | `docs/roadmap/research/learning-benchmark-protocol.md` | protocol reviewable before any runs; owner sign-off (owner task 2) |
| T4.2 | Implement as an EVALUATION-SUBSTRATE template study (its S1-2 machinery); runner script producing a results table + raw logs | eval substrate study definition + `scripts/` runner | paired runs reproducible from one command against fixture homes |
| T4.3 | Publish: results page on the site (plan 36 sync path) + README one-liner if favorable, honest either way | site repo content | page live with methodology link |
| V4 | Validation: an independent re-run (owner or CI nightly variant) reproduces within stated variance | — | reproduction recorded |

## Owner tasks (real world)

1. **Curate the benchmark task set** (S4 — ~10 repeatable tasks that reflect YOUR real usage; 1-2 hours). The benchmark's credibility rests on tasks not being cherry-picked — the protocol doc asks you to freeze them before any measurement.
2. **Sign off the benchmark protocol + the publish decision** (including if results are modest — the honesty is the marketing).
3. During S1-S3 dogfooding, actually **review proposals in the approval surface** for a week — the queue cap and daily refine cap are tuned by your real tolerance; report friction.

## Risks & open questions

- **Risk — proposal spam:** caps exist (queue `_MAX_PENDING`, one-per-run, one-refine-per-skill-per-day); owner dogfood (task 3) tunes them; the flywheel's statistical gates strengthen acceptance later without UX change.
- **Risk — parallel-mechanism drift with LEARNING-FLYWHEEL:** structural guard — same queue, same kinds, same accept path; the flywheel plan's steps 5-9 upgrade internals behind the identical surface. A quarterly cross-check line item sits in that plan's coordination notes (add when its steps land).
- **Open:** whether refine diffs should be able to *split* a skill (one → two) — deferred; kind="new" from a refine context covers it manually.

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**What & why.** The **periodic identity report**: a scheduled (default monthly, configurable) background job composing a readable "how I've adapted to you" narrative from EXISTING learned artifacts — never a modal, never a memory write. Recon confirms every input already has a read seam: `learn.py::LessonStore.load_all()/get_context()`, `preference_facets.py::load_facets()` + `decayed_stability`/`facet_state` (typed facets with class half-lives — the report can honestly say "stable for 4 months" vs "fading"), promoted skills under `skills/auto/` with `SkillUsageStore.all_usage()` (counts + recency) and curator aging states, `skills/proposals.list_pending()`, and `memory_service.memory_stats()` (:1101). This is a pure composition over S2's legibility data — it extends the "What I learned" surfaces (S2's session chips and weekly digest block) with the long-horizon view neither covers: the weekly digest shows deltas; the identity report shows the accumulated shape.

**Design (contract level).**
- New `learning_report.py`: `compose_identity_report(*, window_days: int) -> IdentityReport{period, sections: {facets: [{text, class, stability, state}], lessons: [...], skills: [{name, uses, last_used, aging_state}], proposals_pending: int, memory: memory_stats subset}, markdown: str}`. Deterministic gather + ONE `one_shot_completion(use_case="background")` narrative pass over the gathered facts (facts fenced as data; the LLM narrates, it cannot invent counts — the numeric sections are attached verbatim). **Propose-don't-write:** the composer takes read-only snapshots; zero writes to memory/skills/facets; the honesty-ratchet lint (`test_resilience_degraded_lint.py::_CALL_SITE_SURFACES`) gains `learning_report.py → "assistant_reasoning"` (no-model floor: skip the narrative, deliver the deterministic sections).
- **Delivery:** persisted as an artifact (the existing `artifacts/` provider — versioned, linkable) + surfaced as an inbox item (pre-plan-42: a `source="learning"` InboxItem carrying the artifact link; post-42: `emit_attention_item(kind="report")`), routed through `DashboardState.notify` → `notification_allowed` so quiet hours apply. Never a modal.
- **Scheduling:** a `created_by: system` clock job on the existing `ScheduleService` (`system:learning:identity-report`, monthly default) — migrating to the unified trigger store automatically when AUTOMATION-SUBSTRATE lands (row-for-row, per its disposition table). Config: `learning.identity_report_enabled: bool = True`, `learning.identity_report_cadence: monthly|weekly|off` via the LearningConfig section (loader.py:990), 4-point wired.

**Lands in:** extends **Session 2** ("What I learned" legibility — this is its long-horizon sibling; T2.3's digest block already builds half the gather). Count stays **4 sessions**; S2 grows by ~half a session, recorded honestly.

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.4 | `compose_identity_report`: deterministic gather over LessonStore/facets/SkillUsageStore/curator-state/proposals/memory_stats + one fenced background narrative pass; degraded floor = sections without narrative; lint map entry added | `src/personalclaw/learning_report.py`, `tests/test_resilience_degraded_lint.py` map | fixture home with seeded lessons/facets/skills yields a truthful report; counts byte-match store contents; zero writes to any learning store (inspected before/after); no-model fixture still produces the sections |
| T2.5 | Delivery + schedule: monthly system clock job → artifact + inbox item (notify-gated); `learning.identity_report_*` config 4-point wired; FE: report renders from the inbox/artifact view (no modal) | `learning_report.py`, `gateway_services.py` (job registration), `config/loader.py`, inbox/artifact surfaces | job fires on a compressed-clock fixture; item lands in inbox linking the artifact; quiet-hours suppresses the ping but not the artifact; config round-trips; `off` disables cleanly |

---

## Execution log — LV-6 (S4 benchmark protocol doc)

- **LV-6 DONE.** `docs/roadmap/research/learning-benchmark-protocol.md` is frozen as **PROTOCOL v1**
  and owner-signed (owner task 2), with the ten-task register frozen (owner task 1) and its
  selection rule stated so it can be checked rather than trusted: **one task per bundled skill
  family whose procedure has a deterministic, non-judged observable**. The 14 skills under
  `src/personalclaw/skills/bundled/` were enumerated from a fresh home; four are excluded with
  reasons (`loop-worker` fires only inside a loop; `pclaw-api`/`pclaw-features` would test doc
  recall, not a procedure; `infographic-syntax` shares `visual-output`'s observable), leaving
  exactly ten. No task was chosen after seeing a result — no result exists. Task-set version is
  anchored mechanically on the library manifest's per-scenario `sha256`, which `RESULTS_COLUMNS`
  already carries as `scenario_sha256`, so a v2 row cannot be plotted as a v1 row.
- **The doc prescribes no tooling.** Metrics are specified against surfaces that exist:
  `evals/child.py::result_from_scenario` (completion + assertion-rate score),
  `ScenarioResult.elapsed_secs` (wall time), `TurnResult.tool_calls` (`eval/runner.py:448`),
  SEL `skill_surface` (`skills/loader.py:741`) for arm integrity, and
  `guardrails/audit.py::AttemptRecord` (`model_calls.jsonl`) for the token denominator. The verdict
  rule imports `harness/fanout_measure.py`'s three constants rather than restating them.
- **MEASURED — eight probes executed against `main` in throwaway homes** (never `~/.personalclaw`):
  (1) `SkillsConfig(max_triggered=0)` → `1` with a warning, `-5` → `1`; (2) a brand-new empty
  `PERSONALCLAW_HOME` + `SkillsLoader()` → **14 skills** present in `skills/`; (3) `install_library()`
  → 4 shipped scenarios, all `origin: shipped`, all `fixture_home: empty`; (4)
  `compute_pin("lesson_application")` → complete except `model_fingerprint`, `is_complete() == False`
  in an unbound home (so `append_result` would raise `PinRequiredError` — correct behaviour);
  (5) `harness fanout-measure` on arms named `skills_on`/`skills_off` → refused, exit 2;
  (6) the same observations renamed `fanout`/`single` → `fanout_wins` with delta 9.67, band 5.0,
  spread 4.00, token ratio 0.995; (7) `_expand_cells` with an `arm` axis at `trial_count=3` → 6 cells
  carrying distinct `arm` coords, and an AST census of `evals/child.py` → **exactly one** `coords.get`
  call, for `"model"`; (8) `result_from_scenario` over a scenario whose turn recorded three tool
  calls → no tool-call field in either the scenario summary or the cell result.
- **BLOCKING FINDING (doc §7 G1) — there is no way to run a skills-off arm today.** All three
  candidate levers fail, each verified: `skills.max_triggered` clamps below 1 to 1
  (`config/loader.py:1817-1819`); an empty fixture home is not skills-off because `SkillsLoader`
  force-syncs `skills/bundled/` into the home on construction (`skills/loader.py:293`, `:41`); and
  `feedback.suppressed_producers` is accuracy-derived and only reaches surfacing as
  `("skill_synthesis", key)` pairs (`skills/surfacing.py:304`), so it cannot suppress a bundled skill
  on request. No env override exists. Recorded in the doc as a named precondition on `LV-7`, not
  papered over with a procedure nobody can run.
- **DISCOVERY — the arm lever is already owned by EVALUATION-SUBSTRATE, and `LV-7`'s declared deps
  understate it.** ES §3.3 (atom `ES-7`) specifies replaying runs "with the skill surfaced vs
  suppressed (`arm_mask`)" — the exact lever this benchmark needs. `arm_mask` appears nowhere in
  `src/`, `harness/` or `tests/`: designed, unbuilt. `LV-7`'s row declares
  `EXT:EVALUATION-SUBSTRATE:S1-2` only, but §3 is a later ES session, so `LV-7` is gated on `ES-7`
  and not merely on S1-2. Left unedited here — the roadmap is owner-maintained and this atom's scope
  was the protocol doc; `LV-7` must consume `ES-7`'s `arm_mask` rather than grow a second toggle
  beside it (AGENTS.md §"Shared conventions", one owner per mechanism).
- **DISCOVERY — a declared axis that nothing reads is worse than a missing one** (doc §7 G2). Because
  `evals/child.py:162` reads only `coords["model"]`, an `arm` axis today produces N identical runs
  labelled two ways. Every artifact would look like a real comparison. This is the specific defect
  the protocol's arm-integrity rule exists to catch, and it is why the doc forbids the axis until the
  child honours it.
- **DISCOVERY — the metrics the plan's C4 declares are two-thirds unreachable from the matrix path**
  (doc §7 G3/G4). `tool_calls` is captured per turn and dropped by BOTH `ScenarioResult.summary()`
  and `evals/child.py::result_from_scenario`; `RESULTS_COLUMNS` has no column for it or for wall
  time. Worse and more fixable: the token denominator the honest verdict requires **already exists
  per-cell** — `AttemptRecord` carries `tokens_in`/`tokens_out`/`dollars_est`/`latency_ms` — but
  `model_calls.jsonl` and the SEL log are written into the cell's `PERSONALCLAW_HOME`, a
  `tempfile.TemporaryDirectory` destroyed on exit, while only the cell *artifact* dir survives under
  the real home. `LV-7` must fold both into the cell payload before the child exits.
- **DISCOVERY — `run_matrix` has no production caller** (doc §7 G5): only
  `tests/test_evals_pinning.py` and `tests/test_evals_matrix_runner.py`. No CLI, no handler, no
  script. `personalclaw eval` does not reach it, and must not be used for benchmark runs because it
  isolates `PERSONALCLAW_WORKSPACE` but **not** `PERSONALCLAW_HOME` — so a CLI arm would surface the
  operator's real skills. Separately, `evals.enabled` and `evals.study_default_k` sit in the PATCH
  allowlist (`dashboard/handlers/core.py:643-644`) and are read by nothing under `personalclaw/evals/`;
  the doc tells `LV-7` to read `study_default_k` rather than hardcode `k`, and deliberately does not
  instruct an operator to flip a switch that changes nothing.
- **DISCOVERY — `personalclaw eval` ignores `fixture_home`.** `eval/scenario.py::_parse_scenario`
  reads `name`/`description`/`dimensions`/`seed`/`sessions`/`judge_criteria` and neither `version` nor
  `fixture_home`; those two are read only by the library manifest and `RunPin`. A scenario's declared
  fixture home is therefore honoured by the matrix path alone.
- **DISCOVERY — `harness/fanout_measure.py` is arm-name-bound by construction.** `load_observations`
  requires arms literally named `fanout`/`single` and errors otherwise (probe 5). The statistical
  posture and the three constants are reusable; `compare()`/`load_observations()` are not. The doc
  states the two honest options for `LV-7` (generalise the vocabulary in that module — an owner call,
  since the names are deliberately fixed — or a thin sibling importing the same constants) and rules
  out the dishonest one (relabelling `skills_on` as `fanout` to get a green run).
- **NOT DONE deliberately, and reported rather than built:** no paired arm was executed, because
  G1 means there is no skills-off arm to run and executing one arm twice under two labels is the
  precise artifact §1 of the protocol exists to prevent. No model was called; no cost incurred; no
  provider-reported token count observed. The doc says all of this in §9 so a reader is not left to
  infer that a run happened.
- **DISCOVERY (adjacent, not acted on) — `ES-1`'s atom row reads `todo` while its named machinery is
  on `main`:** `MatrixSpec`/`run_matrix`, per-cell artifact retention under `evals/matrices/`, the
  three-state `PASSED|FAILED|VERIFIER_ABSENT` aggregate, and the `EvalsConfig` PATCH entries all
  exist, and `ES-2` (which depends on `ES-1`) is already `✅`. The genuinely missing piece is the
  production caller (G5), not the machinery. Flagged for the owner; the header/row was left alone
  because this atom does not own the ES plan.

---

## Execution log — LV-1 (S1 end-to-end visible slice)

- **T1.1 CALLER MAP (recorded before any change, measured on `8b4ca7b0`).**
  `run_skill_ladder_review` (`after_turn_review.py:509`) had **exactly one production caller**:
  `dashboard/chat_runner.py:360`, inside `_maybe_skill_ladder_review`, gated on
  `learning_decision_for_turn(...)` plus its own `cfg.skill_ladder` flag and scheduled off
  `state._background_tasks`. **Loop end-of-run was NOT covered.** The seam is
  `loop/watchdog.py:401 _complete`, which already calls `_capture_loop_end` (`:464`, PP-5) — that
  mines lessons through `LearningGate(Cadence.RUN_END)` and never runs the ladder or enqueues a
  skill proposal. So every unattended loop — the runs that do the most work and re-derive the most
  reusable procedure — finished without proposing anything. Use counting: `context.py:1768`
  `SkillUsageStore().record_uses(skill_alloc.loaded)` in `build_message`'s `if skill_requests:`
  branch (a REFUSED skill is deliberately uncounted), plus `mcp_core.py:910`/`:961` `record_use`
  for skill tool reads. Install path: `skills/proposals.py:382 accept()` → `create_auto_skill` for
  `kind="new"`, sidecar overlay when a `refine_target` is still live.
- **T1.2 DONE.** `_complete` now calls `_schedule_loop_end_ladder(loop_id)` immediately after
  `_capture_loop_end` and **before** the `"complete"` publish — a sibling of the PP-5 hook, not a
  change to it. The sync scheduler owns the gate, the once-guard and the candidate-skill list and
  hands only the awaitable body to `state._background_tasks`, so the terminal status write and the
  publish never wait on a model call (falsified: converting the schedule to an `await` made the
  hanging-review fixture hit `Timeout (>30s)`). Gate order mirrors the chat path — `decision.allowed`
  and `cfg.skill_ladder` are answered before any run text is composed, and the strongest observable
  form of that ordering is asserted: `_loop_outcome_text` is never called when the gate denies.
  `loop.task` becomes `user_message`, the deliverable document (else `loop.summary`) becomes
  `assistant_text`, both capped at `_LADDER_TEXT_LIMIT = 6000` — matching `loop.finding_content`'s
  existing ceiling rather than inventing a number, because a monitor loop's `MONITOR_LOG.md` is
  unbounded and this text becomes a prompt. Env-failure hygiene needed no new predicate: passing the
  loop's real texts routes through `_ladder_pass`'s existing `is_environment_failure_claim` guard, and
  the env-failure fixture is asserted on the **queue** (a verdict marker reading `env_failure_claim`
  while a proposal sat in the queue would pass a verdict-only assertion).
  `_deliverable_file(loop)` was extracted from `_register_deliverable_artifact` so the workspace-first
  deliverable resolution exists once and both consumers get the same answer.
- **MEASURED — the once-per-run guard is load-bearing, not defensive.** `store.update_status`'s
  transition guard is `if current in TERMINAL_STATUSES and new_status != current`
  (`loop/store.py:445`), so `COMPLETE → COMPLETE` is **permitted** and `_complete` genuinely re-runs
  end to end. The guard is a per-instance `self._ladder_done: set[str]`, and a test pins the premise
  (`COMPLETE → COMPLETE` is reachable) so the guard cannot quietly become dead code; deleting the
  guard reds with `assert 2 == 1`.
- **T1.3 DONE — and the finding is that nothing was missing.** The accept→surface→use loop already
  closed on `main`: `accept()` → `create_auto_skill` writes `auto/<slug>/SKILL.md`;
  `ContextBuilder.build_message` → `skills.get_surfaced_skills(text)` (`loader.py:1004` →
  `surface_skills`) returns it on a matching prompt; `load_skill` per hit → `allocate_skills` inlines
  the body; `context.py:1768` counts the use. The test passed on its FIRST run with no production
  edit — that is the measurement. What was genuinely missing was a test *crossing* the four links:
  proposals, surfacing and usage each had their own suite, but nothing proved they compose. It drives
  `build_message` rather than `surface_skills`/`get_surfaced_skills` in isolation because
  `record_uses` is written **only** on that path — a surfacing-only test would pass with the counter
  entirely unwired. One long-lived `ContextBuilder` is built *before* the proposal and used for both
  turns (how the gateway holds it), which makes the pre-accept turn a real vacuity floor rather than a
  comparison between two loaders, and `progressive_disclosure_threshold` is pinned to 8: above the
  threshold the turn injects an index only and deliberately records no use, so a test drifting over it
  would have gone silently vacuous on the counter leg.
- **V1 OBSERVED — the full arc, in a throwaway fixture home, no model called.**
  `run ends → ladder fires → exactly one proposal (verdict `filed`) → approve → repeat the task →
  `[Skill: auto/release-flow]` in the assembled prompt with its procedure inlined → use recorded`,
  with the live stores inspected between every step. **Propose-don't-write held at each inspection:**
  after the ladder filed its proposal the `auto/` dir was still absent, the loader still did not know
  the skill, the same prompt still surfaced nothing and no use was recorded — only the accept installed
  anything, and exactly one `auto/` skill existed at the end. The second matching turn incremented the
  counter to 2, so it accumulates rather than latching at "seen once". The operator's real home was
  byte-listing-identical before and after.
- **Gate:** `make lint` green (black 2009 files, isort, flake8, mypy — no issues in 992 source files)
  and **181 passed** across `test_lv1_loop_end_ladder.py` (24), `test_lv1_accept_surface_use.py` (1),
  `test_loop_watchdog.py`, `test_after_turn_review.py`, `test_skill_ladder_review.py`,
  `test_skill_proposals.py`, `test_skill_usage.py`, `test_skill_surfacing.py`,
  `test_skill_allocation.py` and `test_skill_progressive_disclosure.py`, re-run on the combined tip
  rather than taken on report. Probe sweep: 16 `FALSIFICATION|if False and|# PROBE` hits, **identical
  count on the base commit** and none in the new files — the ~13 the brief expected was stale, so the
  count was confirmed against `8b4ca7b0` instead of assumed.
- **DISCOVERY (adjacent, not acted on) — the S2 chip has no channel yet.** `LV-2` will need the
  `used_skills` meta on the existing turn/run events; the ladder path already receives
  `loaded_skills`, but the runner does not forward the ALLOCATED set (`skill_alloc.loaded`, the same
  list `record_uses` consumes) to the frontend. That list is the honest input for "used N skills you
  approved" — the candidate list the ladder gets is every indexed skill, not the ones that reached
  the turn, and plumbing the wrong one would ship an inflated count.
