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

## Contracts & Interfaces (builds ON existing synthesis machinery — [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md) §3.6)

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

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

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
