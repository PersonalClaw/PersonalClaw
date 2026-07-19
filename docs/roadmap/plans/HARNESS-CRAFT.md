# Plan: Harness Craft — Fast Worktrees + Best-of-N + Check-Work

**Status:** PROPOSED (created 2026-07-17 from grok-build analysis — xai-org/grok-build, Apache-2.0, opened 2026-07-14)
**Created:** 2026-07-17
**Wave:** 2/3 — Session 1 (fast worktrees) is independent and can land any time a measured bottleneck justifies it; Sessions 2-3 (best-of-N, check-work) deliberately WAIT for WORKFLOWS-V2 Slices 0-3 so the sampling/verification primitives are built as engine templates + skills together, not as pre-engine one-offs that v2 would then absorb.
**Depends on:** nothing for Session 1. Sessions 2-3: WORKFLOWS-V2 Slices 0-3 (judge-panel/fan-out template machinery) — soft dependency; the SKILL.md halves work standalone, but the engine templates are where the value compounds. SELF-VERIFICATION Sessions 1-2 (spec harness) shares the judge substrate.
**Feeds:** LEARNING-FLYWHEEL (best-of-N outcomes are quality signals for the self-model); EVALUATION-SUBSTRATE (N-sample judge data seeds bake-off baselines); WORKFLOWS-V2-LOOPS-EVOLUTION (SDLC verify stage consumes the check-work skill).

---

## Source Analysis (2026-07-17)

xai-org/grok-build ships three mechanisms this plan ports, adapted to PersonalClaw's substrate:

- **`xai-fast-worktree`** — a dedicated crate for cheap parallel git checkouts, used by their TUI's worktree commands and subagent fan-out. Confirms parallel-checkout speed matters enough at scale to earn dedicated engineering. → §1.
- **`best-of-n` bundled skill** — sample N candidate responses, judge, present/pick the winner. Shipped as a first-class built-in beside `code-review` and `check-work`. → §2.
- **`check-work` bundled skill** — a universal "review what you just did before declaring done" pass any surface can invoke — not tied to their coding loop. → §3.

Our positions: worktree fan-out EXISTS (`loop/worktree.py`, 287 lines — linked checkouts sharing the object store, per-project roots, merge-back, sequential fallback); gate judges EXIST in the SDLC engine (verify/test commands + conservative judge over deliverables, `loop/kinds/sdlc.py`); the LLMJudge helper EXISTS (used by loop gates and planned for EXTERNAL-ACCESS §9 replay). What's missing is (a) worktree setup cost tuning for large repos, (b) sampling-then-judging as a REUSABLE primitive rather than an engine-internal pattern, (c) post-task verification reachable from chat/workflows, not just SDLC stages.

---

## Overview

Three quality-of-craft mechanisms that make the harness measurably better at its core job — parallel execution speed, response quality under sampling, and self-verification before "done". Each is small, each is grounded in a shipped precedent (grok-build), and each lands on a seam PersonalClaw already owns rather than adding a new subsystem.

**Soul guardrail:** no new provider types, no new stores beyond one JSONL, no new UI surfaces beyond skill outputs and one Settings toggle. Best-of-N multiplies LLM cost by N — it is opt-in per invocation, never a default, and rides the SpendMeter/ModelCallGuard chokepoint like every other multi-call pattern.

---

## 1. Fast Worktrees — Setup-Cost Tuning for Parallel Fan-Out (Session 1)

`loop/worktree.py` creates one full linked checkout per parallel task. Object store is already shared (git worktrees do this natively); the cost that remains is working-tree hydration — on a large repo, N parallel tasks pay N full checkouts, serially, inside the 30s `_TIMEOUT` budget.

### 1.1 Measure first
- Instrument `create_worktree` with a duration log line (repo size class: file count from `git ls-files | wc -l` cached per workspace). A fan-out of 4 on a 10K-file repo is the benchmark case. **If measurement shows <2s per worktree on the benchmark, Sessions 1's remaining items are SKIPPED and the plan is re-scoped** — this is explicitly a measured-bottleneck plan, honoring the "perf tuning without a measured bottleneck" objection that deferred it.

### 1.2 Sparse + shallow hydration (the grok-build lesson, git-native)
- **Sparse checkout for scoped tasks:** when a task's plan names its target paths (SDLC decomposition already produces per-task file scopes where available), create the worktree with `git sparse-checkout set <paths>` — the working tree hydrates only what the task touches. Fallback: full checkout when scope is absent/unreliable. Merge-back is unaffected (branches carry full commits regardless of working-tree sparseness).
- **Parallel creation:** worktree creation for a phase's READY tasks currently runs in the scheduler loop; batch the `git worktree add` calls through a small thread pool (bounded by `os.cpu_count()`, ceiling 4) — creation is I/O-bound and git handles concurrent `worktree add` safely (each takes the repo lock briefly).
- **Reuse pool:** on phase completion, instead of `worktree remove` + re-add for the next phase, RESET surviving worktrees to the new base (`git checkout -B <new-branch> <base>` + `git clean -fd`) — reuse beats recreate for repos where hydration dominates. Pool capped at the parallelism limit; teardown at loop end unchanged (including the restart-reap path).

### 1.3 Doctrine unchanged
- All calls stay best-effort + time-bounded; any failure degrades to today's sequential path. The reuse pool is transparent to the merge-back logic (same branch-per-task contract). No config surface beyond one `loop.worktree_sparse` bool (default true) through the standard four wiring points.

---

## 2. Best-of-N — Sampling as a First-Class Primitive (Session 2)

grok-build ships `best-of-n` as a bundled skill. PersonalClaw's version is TWO halves sharing one core: a bundled SKILL.md (chat-invocable) and a v2 workflow template (engine-invocable) — built together so the judge logic exists once.

### 2.1 The core: `sampling.py` helper
- `best_of_n(prompt, n, judge_criteria, use_case="background") -> {winner, candidates, judgments}`: N parallel `one_shot_completion` calls (temperature-varied), then LLMJudge scores each against `judge_criteria`, returns the winner + full slate. Every call rides the ModelCallGuard chokepoint (metering, breaker, audit) — N× cost is visible in `model_calls.jsonl`, budgeted by the SpendMeter.
- Outcome record appended to `~/.personalclaw/sampling_outcomes.jsonl` (bounded): `{ts, n, criteria_digest, winner_idx, score_spread, tokens_total}` — the LEARNING-FLYWHEEL/EVALUATION-SUBSTRATE feed (did sampling actually help? is the spread ever meaningful for this use-case?).

### 2.2 The skill: `skills/bundled/best-of-n/SKILL.md`
- Triggers: "give me N options", "best of", "try a few versions", "sample and pick". Behavior: confirm N + criteria with the user (N capped at 5), run the core, present the winner with a collapsible slate of runners-up (the OPTIONS chip pattern for "use #2 instead"). The skill NAMES the cost multiplier in its confirmation ("this runs N model calls").
- The confirmation gate follows the `grill` skill's ambiguous-trigger precedent: explicit triggers activate immediately; ambiguous ones offer the choice.

### 2.3 The template: v2 judge-panel consumer (lands with/after WORKFLOWS-V2 Slice 3)
- A `best-of-n` workflow template: fan-out node (N samples) → judge node → select node. This is the engine-native form the roadmap already anticipated ("v2's judge-panel pattern will make this a template"); the template CALLS the §2.1 core rather than reimplementing, so skill and template stay behaviorally identical.

---

## 3. Check-Work — Universal Post-Task Verification (Session 3)

grok-build ships `check-work` as a built-in skill beside its coding loop. Ours must compose with what already exists rather than duplicate it: the SDLC verify stage (verify/test commands + deliverable judge) and SELF-VERIFICATION's planned QA Companion.

### 3.1 The skill: `skills/bundled/check-work/SKILL.md`
- Triggers: "check your work", "verify that", "did that actually work", post-hoc "are you sure". Behavior: reconstruct WHAT was claimed done this session (recent turns + tool calls), derive 2-4 CHECKS (file exists/content matches claim, command re-runs clean, endpoint answers, artifact renders), EXECUTE them with real tool calls (never self-report), report pass/fail per check with evidence quotes.
- Doctrine (from the loop-judge-independence work): ground truth over self-report — a check that cannot be executed is reported as "unverifiable", never assumed passing.

### 3.2 Composition, not duplication
- **SDLC verify stage** gains an OPTIONAL post-gate hook: when a stage's gate passes, `loop.check_work_stages` (default off) additionally runs the skill's check-derivation over the stage deliverable — catching the "gate command passed but the claim is broader than the command" class. One config bool; the skill logic is the same module.
- **QA Companion boundary (SELF-VERIFICATION S3):** check-work is the LIGHT, immediate, in-session half (seconds, current claims); the QA Companion is the DEEP, bundled-template half (spec-driven, whole-feature). The skill's doc names this boundary so the two never grow into each other; if SELF-VERIFICATION S3 lands first, check-work delegates its "deep verify" escalation to it.
- **Workflow template:** a `check-work` node template (same engine timing as §2.3) so any v2 workflow can end with a verification node — the engine form of the same module.

### 3.3 Chat surfacing
- After a turn where the agent claims completion of a multi-step task (heuristic: ≥3 tool calls + completion language), the existing suggestion-chip surface MAY offer "Check this work" as a chip (config `chat.offer_check_work`, default on) — invocation is always the user's click, never automatic (cost + latency stay user-consented).

---

## Provider-Fidelity Wiring

- **No new provider types.** The sampling core is a helper over `one_shot_completion`; skills are bundled SKILL.md dirs (existing loader); templates are v2 spec files (engine-owned).
- **Config:** `loop.worktree_sparse` (bool), `loop.check_work_stages` (bool), `chat.offer_check_work` (bool) — each through the four wiring points (dataclass `_meta`, `AppConfig.load`, `to_dict`, `_EDITABLE_CONFIG` + FE).
- **Stores:** `sampling_outcomes.jsonl` (bounded, derived-data class — excluded from snapshots). No memory.db / knowledge.db writes; flywheel learns from the JSONL via the proposal path only.
- **SEL:** nothing here is security-eventful; normal logging only.

---

## Implementation Effort

**~3 sessions.**

- **Session 1 — fast worktrees (§1):** instrumentation + benchmark; sparse-checkout for scoped tasks; pooled parallel creation; reuse-reset between phases; config wiring; regression = existing worktree tests + a fan-out-of-4 timing assertion on the benchmark repo. **Gate: skip remaining items if measurement shows no bottleneck.**
- **Session 2 — best-of-N (§2):** `sampling.py` core + guard/meter wiring + outcomes JSONL; bundled skill with confirmation gate + slate presentation; as-a-user validation (chat: "give me 3 versions of this email, pick the best").
- **Session 3 — check-work (§3):** bundled skill (claim reconstruction → executable checks → evidence report); SDLC post-gate hook behind config; chat suggestion chip; QA-Companion boundary doc; as-a-user validation (agent builds something small, user says "check your work", checks execute for real).

Session 1 is fully independent (Wave 2, or whenever fan-out slowness is observed). Sessions 2-3's skill halves are standalone-shippable; their template halves land with WORKFLOWS-V2 Slice 3+ (Wave 2/3). If v2 slips, the skills ship alone and the templates follow — the §2.1/§3.1 cores are the stable seam either way.

---

## Risks

| Risk | Mitigation |
|---|---|
| Worktree "optimization" without a real bottleneck (the reason this was deferred) | §1.1 measure-first gate: <2s/worktree on the benchmark = skip and re-scope; instrumentation ships regardless (cheap, informative) |
| Sparse checkout breaks a task that touches unplanned files | Task scope is a HINT: sparse worktrees auto-widen on first out-of-scope write failure (`git sparse-checkout add`), fallback to full hydration; merge-back unaffected by construction |
| Reuse pool leaks state between phases | `checkout -B` + `clean -fd` reset; pool is torn down on any reset failure (degrade to today's remove+add); restart-reap path unchanged |
| Best-of-N burns N× tokens silently | Opt-in per invocation; confirmation names the multiplier; every call metered through ModelCallGuard; SpendMeter budgets apply; outcomes JSONL makes "was it worth it" answerable |
| check-work self-reports instead of executing | Doctrine inherited from loop-judge-independence: checks are tool calls or "unverifiable" — the skill text forbids assumed passes; validation includes an adversarial case (claim made, artifact deliberately broken, skill must catch it) |
| Skill/template drift (two behaviors for one name) | Both halves call the same §2.1/§3.1 core module; templates are thin spec wrappers; a shared test exercises both entry points |
| Pre-empting SELF-VERIFICATION's QA Companion | Explicit boundary in §3.2 (light/immediate vs deep/spec-driven); check-work escalates to the Companion when it exists rather than growing depth |

---

## Success Criteria

1. On the benchmark repo (≥10K files), a fan-out of 4 completes worktree setup in under half the pre-plan measured time — or the measurement gate documents that no work was needed.
2. A scoped SDLC task runs in a sparse worktree containing only its target paths; a task that writes outside its scope auto-widens without failing; merge-back produces identical results to full checkouts (diff-verified).
3. "Give me 3 versions and pick the best" in chat: confirmation names 3× cost, three candidates generate in parallel, the judge's winner renders with runners-up collapsible, choosing a runner-up works, and `model_calls.jsonl` shows all calls metered.
4. `sampling_outcomes.jsonl` accumulates records the Evaluation Substrate can read (score spread per criteria class) with no telemetry pipeline.
5. Agent completes a multi-step task with a deliberately planted flaw; "check your work" derives executable checks, actually runs them, and reports the flaw with evidence — zero self-reported passes.
6. With `loop.check_work_stages` on, an SDLC stage whose gate command passes but whose deliverable misses a claimed file is caught at the post-gate hook.
7. All three config fields round-trip through Settings (visible, editable, persisted) — the four-wiring-points lint passes.
8. With WORKFLOWS-V2 Slice 3 landed: the `best-of-n` and `check-work` templates run engine-side, behaviorally identical to their skill halves (shared-core test green).
