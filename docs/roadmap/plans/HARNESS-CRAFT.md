# HARNESS-CRAFT

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/HC.md`](../atomic/HC.md) as 5 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
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

## Execution log

- **[2026-08-21] HC-6 DONE — independent tool calls dispatch concurrently under reader/writer
  path reservations.** `src/personalclaw/agents/native/dispatch_plan.py` owns the reservations and
  the wave partition; `runtime.py`'s `_execute_tool_batch`/`_execute_wave` dispatch them.

  **BEFORE (required first, and measured on unmodified `origin/main` @ `0c64e9dc`).** A
  representative multi-lookup turn — 8 independent read-only lookups (3 greps, 2 globs, a
  `repo_map`, 2 `read_file`s) over a 1200-file synthetic repo, timed from the first
  `EVENT_TOOL_CALL` to the last `EVENT_TOOL_RESULT`, 7 trials after one warm turn:
  `[2044.9, 2010.7, 1593.3, 1413.7, 1699.4, 3180.1, 3593.6] ms` — mean **2219.4 ms**, min 1413.7,
  spread **2179.9 ms**. (Machine under concurrent load from four other agents' suites, so
  pessimistic.)

  **AFTER, same script, same repo size, same machine:**
  `[920.6, 953.6, 929.6, 918.9, 909.6, 970.9, 993.3] ms` — mean **942.4 ms**, max 993.3, spread
  **83.7 ms**. **2.35× on the means, and unanimous**: the WORST concurrent sample beat the BEST
  serial one by 420 ms, so no amount of tail noise moves the verdict. The spread collapsing from
  2180 ms to 84 ms is the same finding from the other side — the baseline's tail *was* serial
  accumulation.

  The shipped mechanism is `python -m harness dispatch-bench` (`harness/tool_dispatch_bench.py`),
  which reproduces both arms from the SHIPPED `tool batch mode=… calls=… waves=… widest=… ms=…`
  log line, exactly as HC-1's `worktree-bench` reads its own. Its baseline arm is the real runtime
  at `max_tool_concurrency=1`, not a simulation of serial dispatch. Recorded run (contended):
  serial `[833, 762, 771, 792, 1411, 1283, 763]` vs concurrent `[483, 501, 494, 521, 485, 480, 492]`,
  `waves=[1] widest=[8]`, verdict **`improved`** — 241 ms of daylight against a 119 ms band.

  **DEVIATION — the gate's band is keyed to the baseline's MEDIAN, not its mean.** The first
  recorded run reported `unresolved` on arms that did not overlap at all: one 10168 ms
  contention-hit serial sample in a ~1.3 s arm pulled the mean to 2795 ms and inflated a
  mean-keyed band to 419 ms, past the whole effect. That is noise in the baseline's *tail*
  deciding a question about its *floor* — the exact failure HC-1's own `evaluate_gate` docstring
  refuses ("a mean with a spread larger than itself looks like pure noise"). Pinned by
  `test_an_outlier_in_the_baselines_tail_cannot_widen_the_band_away_from_a_verdict` with that
  run's real numbers.

  **DISCOVERY — `_last_result_meta` was a latent cross-contamination, not just a refactor
  target.** The runtime parked each tool result's typed metadata (`truncated`,
  `recovery_hints`, `agent_error`) in ONE instance slot written by `_invoke` and read at the
  emit site. Correct while exactly one call could be in flight; with two, call A's metadata
  renders on call B's card. Removed rather than locked: `_invoke`/`_guard_and_invoke` now fill a
  caller-owned sink, so the value belongs to the dispatch that produced it.

  **Composition with AG-14 (concurrent, separate branch).** A reservation decides ordering; a gate
  decides admission. A call that needs interactive approval reserves EVERYTHING and therefore runs
  alone, so any pre-write read gate at the write seam sees exactly the serial world it was written
  against and the two never contend.

- **[2026-08-16][S3] HC-2 DONE — sparse + pooled + reused worktree hydration.**
  Built on HC-1's `loop/worktree.py` (the premise held: `add_worktree` is the one creator, and
  `sdlc._schedule_parallel` its fan-out). Nothing named `sparse` existed anywhere under `src/`, so
  all three levers are new; the shipped shape came out of driving real git first, and two of the
  plan's own recipes turned out to be wrong.

  **DEVIATION 1 — the plan's sparse recipe is the slow way round.** §1.2 says "create the worktree
  with `git sparse-checkout set <paths>`". Measured, `worktree add` then `sparse-checkout set` pays
  the ENTIRE hydration cost and then pays again to delete what it just wrote — strictly worse than
  today. The saving only exists as `worktree add --no-checkout` → `sparse-checkout set` →
  `checkout`, which never writes the out-of-scope files at all. All three steps sit inside HC-1's
  timed window, since with sparse on the final `checkout` *is* the hydration. Cone mode (the
  default) is deliberate: entries are directories and root-level files stay hydrated, so a scoped
  worktree still has `pyproject`/`Makefile` — asserted, because a task that cannot build is not a
  saving.

  **DEVIATION 2 — the reuse recipe needs three commands, not two.** §1.2 specifies
  `checkout -B <branch> <base>` + `clean -fd`. That pair provably leaves BOTH a modified tracked
  file and a staged index in place (`checkout -B` carries local modifications across by design;
  `clean` only touches untracked paths) — i.e. it hands the next run the previous run's work while
  reporting success. `reset --hard` sits between them, and `clean` takes `-x` as well because
  ignored files are the previous run's build output. Each gap has its own test.

  **DEVIATION 3 — the reset is NOT on the acquire path.** The first cut reset any existing worktree
  inside `add_worktree`, which would have deleted a live task's in-progress work: that early return
  is also the RESUME path (a loop restarting mid-task). The reuse pool's real call site is
  `_reap_merge_done`'s conflict auto-resolve, which already did `remove_worktree` + full re-add for
  a task about to re-run on the merged base — exactly the "remove + re-add for the next phase" §1.2
  replaces. It now resets and falls back to the old teardown on any failure. A test pins the resume
  guard so the reset cannot be "optimized" back.

  **The measured reason auto-widening is load-bearing.** In a cone-mode worktree an out-of-cone
  write lands on disk, but `git add -A` then refuses it and stages NOTHING (exit 1), and
  `merge_worktree` DISCARDS that exit code (it always has) — so the branch merges without the work
  and the merge reports `ok=True`. Silent data loss with a green status everywhere. `widen_for_pending`
  therefore runs inside `merge_worktree` before its `add -A`, and the tests assert the file is in the
  RESULTING COMMIT, never that a widen command was issued. A dedicated test pins the defect by
  disabling the widen and asserting `ok is True` with the file gone.

  **Scope is a HINT, per §Risks — there is no task field to read.** §1.2 claims "SDLC decomposition
  already produces per-task file scopes"; it does not. `Task` has no path/scope field (only
  `title`/`description`/`action_plan`/`exit_criteria`), and adding one would be new state shape. So
  scope is EXTRACTED from those texts (`scope_candidates`) and VALIDATED against the index
  (`resolve_scope` → tracked directories, cached per workspace). A hallucinated path is dropped, a
  too-wide result (>8 dirs) means "no scope", and any git failure means full hydration. Polarity is
  deliberately not modelled — "do not touch web/src" still contributes `web/src`, documented in a
  test, because over-inclusion only costs some saving while under-inclusion is recovered by widening;
  neither can break a task. Driving the real path found one genuine extractor bug this way: a
  sentence-final "…do not touch web/." yielded the token `web/.`, which resolved to `web` and
  silently widened the scope. Fixed + regression-tested.

  **Config: `loops.worktree_sparse` (default true), FIVE points not four.** Section is `loops.`, not
  the plan's `loop.` — same deviation HC-4 recorded, as this repo has no `loop` section. Dataclass +
  `_meta`, `AppConfig.load()`, `to_dict()` (automatic via `asdict`), `_EDITABLE_CONFIG` PATCH
  allowlist, plus `config-baseline.json` (regenerated) and `docs/reference/configuration.md`, which
  `test_config_roundtrip.py` does not cover. **No FE control** — nothing in `web/` reads
  `config.loops` (its siblings `check_work_stages`/`judge_use_case`/`stagnation_window` have none
  either), so a Loops settings panel is new scope; consistent with HC-4's ruling. The field is read
  at ONE chokepoint (`scope_for_task`) so a caller cannot bypass it, and the tests drive the CONSUMER:
  flipping it produces a full checkout end-to-end. That leg earned its keep — `sparse_enabled` fails
  open to False, and the first cut called a `get_config()` that does not exist in this repo, which
  looked exactly like the feature being switched off. A real-config-file round-trip test now catches
  a wrong accessor.

  **Pool.** `pool_size() = max(1, min(cpu_count, 4, n_items))`, matching the existing
  `sdlc._POOL_CAP`. Asserted as a NUMBER on both sides of the ceiling (cpu_count 64 → 4; cpu_count 2
  → 2, the vacuity floor), plus a spy on the real `ThreadPoolExecutor` construction, since on this
  18-core box an unbounded pool would satisfy any "a pool exists" check. One spec skips the pool; one
  raising worker cannot sink the batch.

  **Tests.** 47 in `tests/test_loop_worktree_sparse.py`, all against real git under `tmp_path` with
  `config_dir` redirected (real-home rail clean). Every sparse test first asserts an out-of-scope
  path is ABSENT, else "sparse" is unfalsifiable. Two AST tests cover the call sites: that
  `_schedule_parallel` uses `add_worktrees` + `scope_for_task` and no longer calls `add_worktree`
  serially, and that `_reap_merge_done` resets with `remove_worktree` still reachable and GATED on
  the reset failing. Merge-back diff-identity is a direct tree-hash comparison between a sparse and a
  full worktree making the same edits. The fan-out timing assertion is deliberately RELATIVE
  (batched ≤ 1.6× serial in-process) — HC-1 measured a 7.5 s spread across four samples on this
  machine under load, so an absolute budget would be a flake generator.

  **Gate.** `make lint` green (black, isort, flake8, mypy — 895 files). `pytest
  tests/test_loop_worktree_sparse.py tests/test_loop_worktree.py tests/test_loop_worktree_timing.py
  tests/test_config_roundtrip.py tests/test_config_baseline.py tests/test_durability_inventory.py`
  → **124 passed**; adding `test_sdlc_tools.py test_inert_surface_baseline.py
  test_docs_lint_baseline.py` → **55 passed**. `tools/regen_dag_derived.py --check` current.

  **Falsifications (3).** Dropping the `sparse-checkout add` in `widen_for_pending` → `AssertionError:
  the cone did not grow`, `merge_back_carries_an_out_of_scope_write` fails, and
  `AssertionError: sparse merge-back diverged from full: [...]`. Unbounding the pool → `assert 16 == 4`
  and `AssertionError: executor was not bounded: [6]`. Removing `clean -fdx` from the reset →
  `AssertionError: assert not True` on both the leftover-file and ignored-build-output tests.

  **As-a-user drive** (throwaway repo, 103 tracked files, isolated home): scope derived from real task
  text = `['docs/reference', 'src/personalclaw/loop']` (`web` correctly excluded); fan-out of 4 through
  `pool_size(4)=4`; **63 of 103 files hydrated**, out-of-scope absent, `pyproject.toml` present; an
  out-of-scope write showed `git add -A` rc=1 (the loss) → widen → cone grew to include
  `web/src/pages`; merge-back `ok=True` with the out-of-scope file in the base HEAD tree AND the
  in-scope edit; reuse reset cleared a leftover file, restored a clobbered tracked file, emptied the
  index, and PRESERVED the cone. One drive artifact worth recording: re-running with a persisted
  `PERSONALCLAW_HOME` but a deleted repo made every leg fail, because the stale worktree dirs took the
  `outcome=reused ms=0` early return — reset both the repo and the home when driving this.

  **CHANGELOG: no entry.** Contributor-facing harness plumbing. The one user-visible surface is a
  backend-only config default that changes no observable outcome: a scoped task's merged result is
  byte-identical either way (asserted by tree hash), so there is nothing for a user to notice beyond
  faster parallel phases.

- **[2026-08-15][S2] HC-3 IMPLEMENTATION COMPLETE, ATOM BLOCKED on an owner live-run.**
  `src/personalclaw/sampling.py` ships the core: N temperature-varied `one_shot_completion`
  calls fanned out concurrently (in-flight counter peaks at N *and* a fan-out span assertion,
  so a sequential loop cannot pass), an `LLMJudge` pass returning winner+candidates+judgments,
  and a bounded record appended to `~/.personalclaw/sampling_outcomes.jsonl` (declared as a
  `StateEntry`, snapshot-excluded). Fail-open in two tiers: one failed sample loses only that
  candidate; all-N-failed returns an explicit `winner=None` envelope rather than raising or
  fabricating an answer; a dead judge returns the slate `judged=False`. Determinism: highest
  score, ties to the lowest candidate index — proven across two runs whose N calls complete in
  opposite order. Mapped in `_CALL_SITE_SURFACES` as `assistant_reasoning`.
  `skills/bundled/best-of-n/SKILL.md` + a `best_of_n` MCP tool (schema, `TOOL_META`,
  regenerated reference) make it reachable from chat rather than an inert export.
  **MEASURED FINDING:** `one_shot_completion` had **no temperature parameter**, so
  "temperature-varied" was unbuildable as written. Added `temperature: float | None`, threaded
  through all three resolution paths and read into `extra_options` by the branded factory —
  the `embedding_model` precedent. Passing `extra_options=` as a build kwarg would have been
  *silently swallowed* by that factory (a live reader of an unwritten key), so the named-kwarg
  route is asserted behaviourally. Caveat documented: a model in extended-thinking mode drops a
  custom temperature, collapsing the ladder to a zero-spread slate.
  **BLOCKED, not done:** the third `done_when` clause — *"chat 'give me 3 versions and pick the
  best' validated end-to-end with all N calls visible in `model_calls.jsonl`"* — requires a
  running gateway bound to a real model. No agent can perform it (and doing so spends real
  money), so claiming `done` would assert a validation nobody ran. Every seam is unit-proven;
  what remains is one owner run on the dev instance. Status is `blocked` so a later tick does
  not re-implement the core.

- **[2026-08-16][S1] HC-1 DONE — instrumentation shipped; gate verdict `proceed`, with a named
  contention caveat at the floor.**
  **Premise correction (DEVIATION):** §1.1 says "instrument `create_worktree`". No such function
  exists. The real single creator is `loop.worktree.add_worktree`, and both production callers go
  through it (`loop/kinds/sdlc.py:1360` phase fan-out, `workflows/provisioning.py:487` for
  `mode: worktree` runs). Instrumented there rather than at either wrapper, so one line covers both
  paths.
  **The line (a contract, not a debug aid):**
  `worktree add outcome=created task=t-x ms=812 files=10432 size_class=large`, behind
  `TIMING_LOG_PREFIX`. `outcome` ∈ `created|reused|failed` because the three populations must never
  be averaged together — `reused` is `add_worktree`'s idempotent early return (near zero, and the
  datapoint §1.2's reuse pool would be judged against) and `failed` carries the duration that burned
  the 30 s `_TIMEOUT`. `files` AND `size_class` both: the count makes two runs comparable as a repo
  grows, the class makes a mixed log greppable without arithmetic. Decade buckets
  (tiny/small/medium/large/huge) so §1.1's 10K benchmark case lands ON the `large` floor instead of
  straddling a boundary; `unknown` is distinct from every real class so an unmeasurable repo never
  reads as a cheap one.
  **Cache:** `repo_file_count()` memoizes `git ls-files` per workspace abspath for the process, and
  `_log_creation` resolves the class only AFTER the clock stops. Both matter: instrumented the other
  way round, the first worktree of every process — the one a benchmark reads first — would report its
  own probe as hydration. A test injects a 600 ms `ls-files` and asserts the logged duration does not
  contain it.
  **Benchmark:** `harness/worktree_bench.py` + `python -m harness worktree-bench`. It parses the
  SHIPPED log line instead of keeping its own stopwatch — one measurement, not two that can drift,
  and the log contract gains a real reader. This repo has **3,047** tracked files and does NOT
  qualify as the ≥10K benchmark case, so the tool synthesizes a deterministic 10,000-file repo
  (100 files/dir, ~200 B each, content a function of the file index so two syntheses produce
  identical trees) under a `tempfile.TemporaryDirectory`. Nothing enters the committed tree. It
  refuses to run against the real `~/.personalclaw` (worktrees land under `config_dir()`, and its own
  cleanup would then delete inside the user's home).
  **RECORDED BASELINE — fan-out of 4, 10,000 files, sequential (today's path):** samples
  `[10442, 4501, 2947, 2975]` ms · mean **5216 ms/worktree** · median 3738 · max 10442 · spread 7495
  · sequential total **20.9 s** · outcomes `{created: 4}`.
  **CONTENTION CAVEAT (evidence, not a hedge):** every number in this session was taken while other
  agents ran full test suites and npm builds. Load average **21–35 on 18 cores** across the runs
  (captured with `uptime` before and after each). Two earlier trials of the same configuration:
  mean 13042 ms with samples `[23014, 11351, 4760]` **and one worktree FAILED at the 30 s
  `_TIMEOUT`**, and mean 6720 ms with `[13758, 6144, 3009, 3971]`. Run-to-run variation on identical
  input is therefore ~2x, and it is all in the upper tail. To separate hydration from the machine, a
  **40-file control arm was run in the same window**: mean **286 ms**, spread **18 ms**, total
  1142 ms. Subtracting that floor leaves ~2.7 s of genuine hydration in even the cheapest 10K
  sample, so the cost is size-driven, not ambient.
  **GATE VERDICT: `proceed` to `HC-2`** — unanimous: all four worktrees exceeded the 2000 ms gate,
  the cheapest by 947 ms. **The margin at the floor is thin and I am not going to dress it up:** a
  ~1.25x idle speedup would move that cheapest sample into the ±20% unresolved band. Consequence for
  `HC-2`: it is unblocked, but re-run `python -m harness worktree-bench --files 10000` on an idle
  machine and confirm before spending the atom. Independently of the threshold, trial 1's lost
  worktree is a second bottleneck signal that no contention discount removes — at this repo size a
  fan-out can drop a task's worktree entirely, not merely slow it.
  **DEVIATION (gate statistic).** The plan phrases the gate as "<2s per worktree", which reads as a
  mean. Implemented as UNANIMITY over the samples instead, and the measurement is what forced it: an
  intermediate version refused on a spread-exceeds-mean rule and would have reported `unresolved`
  for an arm whose every observation was more than twice the gate, while a mean-versus-threshold test
  would have decided the atom on which of four samples got descheduled. A right-skewed four-sample
  arm has no trustworthy central estimate; agreement is the only thing it can support. Under-size
  repos, narrower fan-outs, empty arms and straddling samples are all `unresolved` — a third outcome
  the `done_when` does not name, kept because "the measurement cannot answer" is a real result and
  rounding it to `skip` or `proceed` would be the failure this atom exists to avoid.
  **Falsifications (each run, mutation applied then reverted).**
  1. Duration hard-coded to `0` → 1 red:
     `AssertionError: logged 0ms ignores the injected sleep / assert 0 >= ((0.3 * 1000) * 0.9)`.
  2. Duration hard-coded to `999999` → the same test's OTHER bound reds:
     `AssertionError: logged 999999ms exceeds observed 718.5775419929996ms`. Both bounds are
     load-safe by construction — the floor is a sleep injected into the git call, the ceiling is a
     wall clock around that same call, so contention moves them together and neither is a fixed
     threshold.
  3. `size_class=` dropped from the line → **7 red** across both halves, incl.
     `test_creation_logs_one_parseable_row_with_the_repo_size`,
     `test_a_failed_creation_still_reports_its_duration`, and the harness's
     `test_parses_the_line_the_module_actually_emits` (the row regex is anchored on the full field
     set on purpose, so a lost tag is a parse failure, not a defaulted field).
  4. `repo_file_count` moved INSIDE the timed window → 1 red:
     `AssertionError: ['size-probe', 'git-worktree-add-returned', 'size-probe']`.
  5. `logger.info` → `logger.debug` → **7 red** (5 instrumentation + 2 harness). Worth recording
     because I predicted this one wrong: I expected the harness half to survive, since
     `collect_timing_rows` raises the logger level itself. It does not — raising the level to INFO
     does not resurrect a DEBUG call, so the collector is a level guard after all.
  6. **A mutation that reded NOTHING, called out as required:** `median_ms` replaced by a constant
     `0.0` → **46 passed, 0 red.** The median was printed by the CLI and by `to_dict` and asserted
     by nothing — a reported number nobody was defending. Closed in the same commit by asserting
     every field `to_dict` emits (`test_to_dict_carries_every_reported_number_and_the_verdict`);
     re-running the same mutation now reds. This is the one place these tests were weaker than they
     looked, and it was the report rather than the measurement.
  **One of my own tests was wall-clock-flaky and the contention caught it.** The first version of
  `test_the_size_probe_is_not_inside_the_measured_window` made `git ls-files` sleep 600 ms and
  asserted the logged duration stayed under that. It went red mid-gate —
  `logged 801ms contains the 600ms size probe` — because under load the real `git worktree add`
  itself took 801 ms. The assertion had quietly assumed the work was cheaper than the probe.
  Rewritten as `test_the_size_probe_runs_after_the_clock_stops`: an ORDERING assertion (the probe
  must be called only after `git worktree add` returns), which is the actual invariant, has no
  threshold, and cannot be flaked by load. Mutation 4 still reds against it.
  **A real-home rail red that was NOT mine.** One combined gate run reported
  `real-home rail FAILED: 2 entries under /Users/golani/.personalclaw changed` — `memory.db-shm`
  (32768 B) and `memory.db-wal` (0 B). Both are SQLite WAL sidecars that any reader creates, not
  state. Isolated it: the two new files alone, the pre-existing files alone, and the full combined
  selection all re-ran with `~/.personalclaw unchanged`, twice. Attributed to concurrent activity on
  this shared machine (other agents were running full suites in the same window) rather than to this
  change. Recorded rather than silently re-run, and NOT added to `ALLOWED_RESIDUE`.
  **Gate (all from `/private/tmp/hc1-wt`).** `make lint` clean — black 1686 files, isort, flake8,
  mypy "no issues found in 870 source files". `pytest tests/test_loop_worktree_timing.py
  tests/test_harness_worktree_bench.py` → **11 + 26 = 37 passed**. Existing `add_worktree`
  consumers green: `test_loop_worktree.py`, `test_workflows_provisioning.py`,
  `test_workflows_worktrees.py`, `test_loop_manager.py`. Ratchets/gates green:
  `test_inert_surface_baseline.py` (no new config key, enum member, trigger kind, `_EDITABLE_CONFIG`
  entry or SDK export, so the census is untouched — baseline NOT regenerated),
  `test_portability.py`, `test_durability_inventory.py`, `test_resilience_degraded_lint.py`,
  `test_roadmap_dag_derived.py` (incl.
  `test_regenerating_the_committed_file_is_a_no_op` after
  `PYTHONPATH=…/src python3 tools/regen_dag_derived.py` — 640 atoms, 125 ready, 876 edges, 0
  dangling, no `regressed:` line), `harness`'s own `test_harness_validate.py` +
  `python -m harness validate` (15 specs valid, 0 warnings). `test_config_baseline.py` not
  applicable: no config field was added (§1.3's `loop.worktree_sparse` belongs to `HC-2`). CLI exit
  codes verified against the `fanout-measure` precedent: `2` for a measurement that could not happen
  (`not a git repo`), `0` for an honest `unresolved` verdict.
  **Full suite:** `pytest tests/ --no-cov -q -p no:randomly -n 4` →
  **20421 passed, 30 skipped, 12 xfailed, 0 failed** in 16m44s, `real-home rail:
  ~/.personalclaw unchanged by this run`.
  **CHANGELOG: no entry.** This is developer instrumentation plus a dev-harness benchmark — a log
  line at INFO and a `python -m harness` subcommand. No user-facing surface, no config field, no
  behavior change to any run: `add_worktree` returns exactly what it returned before.
