# HARNESS-CRAFT — atomic plans

**Source plan:** [`HARNESS-CRAFT`](../plans/HARNESS-CRAFT.md)  
**Code:** `HC`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `HC-1` | ⬜ | Worktree instrumentation + fan-out-of-4 benchmark & measure-first gate | — | create_worktree logs a duration line tagged with the repo size class (git ls-files count, cached per workspace); a fan-out-of-4 on a >=10K-file benchmark repo has a recorded baseline setup time; the <2s-per-worktree gate is evaluated and its outcome (proceed to HC-2, or SKIP+re-scope) is documented. Instrumentation ships regardless of the gate. |
| `HC-2` | ⬜ | Sparse + parallel + reuse worktree hydration with loop.worktree_sparse config | `HC-1` | Scoped SDLC tasks create sparse worktrees hydrating only target paths; an out-of-scope write auto-widens (git sparse-checkout add) without failing; phase-READY worktree creation batches through a bounded (cpu_count, ceil 4) thread pool; a reuse pool resets surviving worktrees (checkout -B + clean -fd) between phases and tears down to remove+add on any reset failure; merge-back is diff-identical to full checkouts; loop.worktree_sparse (default true) round-trips through the four wiring points (dataclass _meta, load, to_dict, _EDITABLE_CONFIG+FE); existing worktree tests plus a fan-out timing assertion pass (SC 1, 2, 7). |
| `HC-3` | ⬜ | Best-of-N sampling core (sampling.py) + bundled best-of-n skill | — | best_of_n(prompt,n,judge_criteria,use_case) fires N temperature-varied parallel one_shot_completion calls through the ModelCallGuard chokepoint, LLMJudge scores each and returns winner+candidates+judgments, and appends a bounded record (ts,n,criteria_digest,winner_idx,score_spread,tokens_total) to ~/.personalclaw/sampling_outcomes.jsonl (snapshot-excluded); skills/bundled/best-of-n/SKILL.md confirms N (cap 5)+criteria naming the N-call cost multiplier (grill ambiguous-trigger precedent), presents winner with collapsible runners-up and a working 'use #2' choice; chat 'give me 3 versions and pick the best' validated end-to-end with all N calls visible in model_calls.jsonl (SC 3, 4). |
| `HC-4` | ✅ | Check-work skill + SDLC post-gate hook + chat suggestion chip | — | skills/bundled/check-work/SKILL.md reconstructs session claims, derives 2-4 executable checks, runs them with real tool calls (unverifiable checks reported as such, never assumed passing), and reports pass/fail with evidence; an adversarial planted-flaw case is caught with zero self-reported passes; loop.check_work_stages (default off) runs the same check-derivation module after a passing SDLC gate and catches a claimed-but-missing file; chat.offer_check_work (default on) offers a 'Check this work' chip after >=3-tool-call completion turns (invocation always user-clicked); the QA-Companion light-vs-deep boundary doc is present; both config bools round-trip through the four wiring points (SC 5, 6, 7). Soft, non-blocking: delegates deep-verify escalation to SELF-VERIFICATION S3 QA Companion only if that has landed. |
| `HC-5` | ⬜ | v2 workflow templates for best-of-n and check-work (engine-native halves) | `HC-3`, `HC-4`, `EXT:WORKFLOWS-V2:Slice 3 judge-panel/fan-out template machinery` | A best-of-n workflow template (fan-out N -> judge -> select) and a check-work node template run engine-side, each CALLING the §2.1/§3.1 cores (no reimplementation) so template and skill are behaviorally identical; a shared-core test exercising both skill and template entry points is green (SC 8). |

## Atom scopes

### `HC-1` — Worktree instrumentation + fan-out-of-4 benchmark & measure-first gate

**Status:** todo

§1.1 Measure first; §Implementation Effort Session 1 (instrumentation/benchmark half)

**Done when:** create_worktree logs a duration line tagged with the repo size class (git ls-files count, cached per workspace); a fan-out-of-4 on a >=10K-file benchmark repo has a recorded baseline setup time; the <2s-per-worktree gate is evaluated and its outcome (proceed to HC-2, or SKIP+re-scope) is documented. Instrumentation ships regardless of the gate.

### `HC-2` — Sparse + parallel + reuse worktree hydration with loop.worktree_sparse config

**Status:** todo

§1.2 Sparse + shallow hydration; §1.3 Doctrine unchanged

**Done when:** Scoped SDLC tasks create sparse worktrees hydrating only target paths; an out-of-scope write auto-widens (git sparse-checkout add) without failing; phase-READY worktree creation batches through a bounded (cpu_count, ceil 4) thread pool; a reuse pool resets surviving worktrees (checkout -B + clean -fd) between phases and tears down to remove+add on any reset failure; merge-back is diff-identical to full checkouts; loop.worktree_sparse (default true) round-trips through the four wiring points (dataclass _meta, load, to_dict, _EDITABLE_CONFIG+FE); existing worktree tests plus a fan-out timing assertion pass (SC 1, 2, 7).

### `HC-3` — Best-of-N sampling core (sampling.py) + bundled best-of-n skill

**Status:** todo

§2.1 The core: sampling.py helper; §2.2 The skill: skills/bundled/best-of-n/SKILL.md

**Done when:** best_of_n(prompt,n,judge_criteria,use_case) fires N temperature-varied parallel one_shot_completion calls through the ModelCallGuard chokepoint, LLMJudge scores each and returns winner+candidates+judgments, and appends a bounded record (ts,n,criteria_digest,winner_idx,score_spread,tokens_total) to ~/.personalclaw/sampling_outcomes.jsonl (snapshot-excluded); skills/bundled/best-of-n/SKILL.md confirms N (cap 5)+criteria naming the N-call cost multiplier (grill ambiguous-trigger precedent), presents winner with collapsible runners-up and a working 'use #2' choice; chat 'give me 3 versions and pick the best' validated end-to-end with all N calls visible in model_calls.jsonl (SC 3, 4).

### `HC-4` — Check-work skill + SDLC post-gate hook + chat suggestion chip

**Status:** done

§3.1 The skill: skills/bundled/check-work/SKILL.md; §3.2 Composition, not duplication (SDLC post-gate hook + QA-Companion boundary doc); §3.3 Chat surfacing

**Done when:** skills/bundled/check-work/SKILL.md reconstructs session claims, derives 2-4 executable checks, runs them with real tool calls (unverifiable checks reported as such, never assumed passing), and reports pass/fail with evidence; an adversarial planted-flaw case is caught with zero self-reported passes; loop.check_work_stages (default off) runs the same check-derivation module after a passing SDLC gate and catches a claimed-but-missing file; chat.offer_check_work (default on) offers a 'Check this work' chip after >=3-tool-call completion turns (invocation always user-clicked); the QA-Companion light-vs-deep boundary doc is present; both config bools round-trip through the four wiring points (SC 5, 6, 7). Soft, non-blocking: delegates deep-verify escalation to SELF-VERIFICATION S3 QA Companion only if that has landed.

**DONE.** `personalclaw/check_work.py` is the one derivation core (reconstruct → derive
2-4 → execute → report); both entry points call it, so skill and hook can never diverge.
`skills/bundled/check-work/SKILL.md` + `references/qa-boundary.md` ship it (packaged via a
new `skills/bundled/*/references/*.md` glob). `CodeKind._check_work_post_gate` runs it
after a passing stage gate behind `loops.check_work_stages` (default off, fails OPEN) and
holds the stage on a failed derived check. `dashboard.offer_check_work` (default on)
broadcasts `chat_check_work_offer` after a ≥3-tool-call completion turn; the FE
`CheckWorkChip` sends "check your work" only on a click. 39 tests in
`tests/test_check_work.py`, incl. the adversarial planted-flaw case (pass/fail/
unverifiable with zero self-reported passes).

**DEVIATIONS.** (1) Config sections are `loops.check_work_stages` and
`dashboard.offer_check_work`, not the plan's `loop.`/`chat.` — this repo has no `loop` or
`chat` config section; the chat-surface prefs (incl. `followup_chips`, the §3.3 chip
surface) live in `dashboard`. (2) Wiring point 4 for `loops.check_work_stages` is the
`_EDITABLE_CONFIG` PATCH allowlist with no Settings control: NOTHING in `web/` reads
`config.loops` (its siblings `judge_use_case`/`stagnation_window` have none either), so a
Loops settings panel would be new scope. `dashboard.offer_check_work` is user-facing and
does get a toggle + typed API field. (3) The module never shells out on its own
authority — command checks need an injected runner, else they are `unverifiable`.

**DISCOVERY.** Running the skill on its own session caught a real defect in the core: the
bare-path scan re-matched `work/SKILL.md` out of the backticked
`…/bundled/check-work/SKILL.md`, inventing a claim that then FAILED. Fixed (scan runs on
the sentence with backticked spans removed) with a regression test.

### `HC-5` — v2 workflow templates for best-of-n and check-work (engine-native halves)

**Status:** todo

§2.3 The template: v2 judge-panel consumer; §3.2 Workflow template (check-work node); Success Criteria 8

**Done when:** A best-of-n workflow template (fan-out N -> judge -> select) and a check-work node template run engine-side, each CALLING the §2.1/§3.1 cores (no reimplementation) so template and skill are behaviorally identical; a shared-core test exercising both skill and template entry points is green (SC 8).

