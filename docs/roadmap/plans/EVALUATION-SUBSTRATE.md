# EVALUATION-SUBSTRATE

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/ES.md`](../atomic/ES.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Evaluation Evidence Substrate — Template Studies, Ablation, Retrieval Benchmark, Trust Ladder

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog)
**Created:** 2026-07-13
**Wave:** 3-4 — the study registry, retrieval harness, and judge benchmark (§3, §5, §6) are v2-independent and can front-run; template studies and the trust ladder (§2, §4) consume the WORKFLOWS-V2 Run Ledger (Slices 0-3) and LEARNING-FLYWHEEL's proposal queue (steps 3+), so they land after the flywheel's v2-coupled steps.
**Depends on:** WORKFLOWS-V2 (Run Ledger acceptance criteria, §5 event table) for §2/§4/§8; WORKFLOWS-V2-LEARNING-FLYWHEEL (proposal queue, GateOK, change manifests, maturity levels) as the machinery this plan feeds evidence into; AUTONOMY-GUARDRAILS §2 (`model_calls.jsonl` attempt audit) for §7's production sampling. §3, §5, §6 depend on nothing beyond the existing `eval/` package.
**Companions:** WORKFLOWS-V2-UNIVERSAL-PLANNING (owns autonomy mode-switching — this plan supplies the evidence its Earned Autonomy consumes), MEMORY-GRAPH-AND-VAULT (its per-arm volunteered-vs-used stats are the online half of §5's offline retrieval benchmark).
**Scope:** the offline, replayable evidence layer that makes autonomy *earned* rather than configured — pre-registered A/B template studies, a harness ablation runner + model-upgrade watchdog, a retrieval eval harness with per-arm ablation, an evidence-gated trust-graduation ladder, a judge benchmark harness, one shared experiment-matrix runner, a production-sampled model bake-off, and a bundled optimize-harness template. **This plan explicitly RE-OPENS LEARNING-FLYWHEEL §3.4's contingent eval gate** — see the Overview for the researched counterargument and the changed sizing that makes the re-open safe.

---

## Research Integration (2026-07-13)

- **NEW-11 core** (pre-registered A/B template studies: k-run paired old-vs-new, hidden locked validation commands, blinded rubric-pinned judging with agreement checks; harness ablation runner + model-upgrade watchdog; retrieval eval harness with per-arm P@k/R@k ablation; trust-graduation ladder; nodding-loop detection; judge-calibration + scaffolding-retirement proposals; human-attention accounting) → §2, §3, §4, §5; nodding-loop / retirement dispositions in §9 (mostly approved elsewhere — evidence supply is the remainder).
- **NEW-11 amendment 1** (judge benchmark harness — fixed inputs × model tiers × iteration counts → tier-recommendation table with honest failure-mode notes; experiment-matrix runner shared between harness-ablation and local-model validation; model bake-off from production-sampled real inputs; bundled optimize-harness template with hypothesis-abandon + no-improvement-halt stop conditions) → §6, §5.4, §7, §8.
- **NEW-11 amendment 2 (OpenJarvis shapes)** (skills/templates benchmark harness — `bench --max-samples --seeds` per-skill impact measurement; teacher/student spec search over local traces; sidecar overlays as apply/revert) → §3.3, §8.3; sidecar overlays and the teacher/student split are ALREADY approved in LEARNING-FLYWHEEL §3.1 (LEARN-R3) — §8 consumes them, does not re-specify them.

**Overlap honored (rule: reference the approved mechanism, scope to the remainder):**

| Approved mechanism | Where it lives | What THIS plan adds on top |
|---|---|---|
| **LEARN-R2** — held-out replay gate (GateOK), median-of-3 critic, frozen region, canary revert, harvested regression suite | LEARNING-FLYWHEEL §3.1 | GateOK is a *pre-surfacing filter on individual diffs*. §2 adds the formal instrument above it: pre-registered, k-run, **blinded, paired** studies with hidden locked validation — the evidence tier that gates *trust graduation* (§4), not proposal surfacing. The harvested regression suite becomes a study's default input corpus. |
| **LEARN-R4** — Measure floor (surfacing_events, mechanical "used", per-arm confidence, Beta-Binomial trust) | LEARNING-FLYWHEEL §2.5 | R4 is *online, weak-labeled, free*. §5 is the *offline, ground-truth-labeled, versioned* complement: P@k/R@k with arms toggled, runnable before/after any retrieval change. R4's events supply §5's candidate query mining; §5's verdicts justify R4's dark-shipped arms. |
| **LOOP-R3** — rubric convergence contract (fixed 0-2 dimensions, evidence citation, `judge_samples: N` median, judge-verdict ledger + hardening loop) | LOOPS-EVOLUTION Runtime Hints + Migration Checklist | The rubric contract is the *format*; §2.3's blinded judging and §6's judge benchmark *pin and calibrate* it: rubric-hash pinning, position-swap agreement checks, and the tier-recommendation table that says which model each rubric actually needs. |
| **LEARN-R9** — scaffolding-retirement proposal kind (needs "ablation-grade evidence") | LEARNING-FLYWHEEL §2.2 | R9 *requires* ablation-grade evidence but nothing generates it. §3's ablation runner is the generator; retirement proposals cite its reports. |
| **LEARN-R10** — nodding-loop detector, judge-divergence events | LEARNING-FLYWHEEL §3.1 | Kept there (online statistics over the ledger). §2.2's hidden locked validation adds the *structural* anti-nodding measure: a check the worker cannot read cannot be nodded at. |
| **LEARN-R11** — template maturity L0-L3 from ledger-derived health | LEARNING-FLYWHEEL §3.1 | Maturity stays flywheel-computed. §4 adds the missing top rung: L3 + a **passing, unexpired template study** = the unattended grant, recorded as an auditable trust record with model-fingerprint expiry. |
| **LEARN-R16** — change manifests + predict-then-verify attribution verdicts | LEARNING-FLYWHEEL §2.2/§3.1 | §8's optimize-harness template emits R16-shaped manifests per candidate; §4's ladder consumes verdict history as a trust signal (as R16 already anticipates for "the NEW-11 trust ladder"). |
| **UP-R6** — Earned Autonomy (report-only first runs, promotion suggested after N successes, mid-run demotion) | UNIVERSAL-PLANNING §Earned Autonomy | Mode-switching ownership unchanged. §4 upgrades "N verified successes" from a count to an evidence record UNIVERSAL-PLANNING's approval gate reads. |
| **WF2-R13** — per-node cost/model/tokens in Run Ledger + `consulted` events | WORKFLOWS-V2 §5 | Consumed as-is. §4.4 adds ONE new derived ledger dimension: human-attention accounting (a query over events that already exist, plus one small event addition). |

---

## Overview

**The re-open, stated honestly.** LEARNING-FLYWHEEL §3.4 demoted the eval regression gate as "CI/CD machinery sized for a team," betting that GateOK + canary revert + the harvested suite cover the risk. The research corpus (17 sources) converges on the counterargument: **self-improvement without offline replayable evals is theater.** auto-harness's 96-experiment run shows *why* the demotion was miscalibrated — its regression suite was not authored CI machinery but a **harvested set that grew 0→17 cases**, personal-scale by construction, and it was precisely the tightening suite (most candidates rejected in iterations ~60-90) that kept later gains "genuinely additive." MetaHarness makes the search/test split a *structural* contract (`evaluate_test` artifacts never visible during search); GBrain's skillopt refuses candidates that beat the benchmark but regress held-out tasks; the harness-engineering course documents judges that "talk themselves into approving" and components whose compensating assumptions expire silently on model upgrades. The re-open is therefore NOT the team-CI §3.4 rejected: no golden-run authoring burden, no CI service — a handful of replayable cases per template, harvested from real runs, executed by the machinery below. The one hard precondition §3.4 named is honored and owned here: **`EvalRunner.run_scenario` mutates process-global `PERSONALCLAW_WORKSPACE` env (`eval/runner.py:216`, verified — not concurrency-safe); §1.3 moves study/benchmark execution to subprocess isolation before anything in this plan runs against a live gateway.**

**Verified starting points (recon 2026-07-12, re-checked against code where load-bearing):**

- An `eval/` package EXISTS and is the substrate to extend, not replace: `eval/judge.py:LLMJudge` (builds via `provider_factory("eval_judge")`, prompt from the `eval-judge` use-case, `pass_threshold=3.0`, rejects tool-permission requests, parse-failure → score 0 — reject-by-default), `eval/runner.py:EvalRunner` (fresh temp workspace per scenario; the env-mutation hazard above), `eval/scenario.py` (`AssertionType`: contains/not_contains/regex/equals/judge; `Scenario{sessions, seed, dimensions}`).
- A judge-calibration probe EXISTS: `loop/instrument.py:probe_judge` (strong-vs-null separation ≥1.5) — §6 generalizes exactly this shape across model tiers instead of inventing a new one.
- Retrieval arms EXIST on both stores: knowledge — `knowledge/retrieval.py:HybridRetriever` ("FTS5 keyword + graph traversal + optional vector search, fused with RRF", title-boost in RRF-score units, `match_type` per hit); memory — flat hybrid recall (0.6·vec + 0.4·kw, `vector_memory.py` ~L1063), gaining a graph arm via MEMORY-GRAPH-AND-VAULT. Nothing measures any of them offline.
- The Run Ledger event table (WORKFLOWS-V2 §5) already specifies `step_completed{tokens, model, provider, cost_usd}`, `gate_rejected{user_comment}`, `gate_criterion{score, hard_fail}`, `user_edited_mid_flight{ops}`, `consulted`, judge verdicts as first-class events with `status=discard` for reverted iterations — §2 and §4 are ledger *consumers*, filed there as acceptance criteria already.
- Background model resolution: `one_shot_completion(use_case=…)` over `active_models.json` bindings (`providers/provider_bridge.py`) — the model-upgrade watchdog (§3.2) keys off changes to this file, a real, single seam.
- Bundled workflow templates ship via `workflows/bundled/` synced by `workflows/native.py` — §8's optimize-harness template lands there like any starter.
- The `run-workflow` action provider is already in `ALLOWED_HOOK_PROVIDERS` (`src/personalclaw/validation.py`) — every periodic runner in this plan fires as a trigger→workflow, no allowlist change needed.
- AUTONOMY-GUARDRAILS §2 introduces `model_calls.jsonl` (attempt-level audit: use_case, provider, model, tokens, latency, failure_mode) — §7's bake-off samples real production inputs from it rather than inventing synthetic ones.

**One sentence of architecture:** everything below is *one small store* (`~/.personalclaw/evals/`), *one shared runner* (§1), and *five consumers of it* (§2 studies, §3 ablation, §5 retrieval, §6 judge benchmark, §7 bake-off) — plus the trust ladder (§4) that turns their outputs into autonomy grants, and one bundled template (§8) that turns the loop on PClaw's own artifacts.

**Soul guardrail:** sized for one user. A "study" is k≈5 paired runs, not a fleet job. A "benchmark" is a dozen fixtures harvested from the user's own history, not a dataset download. Results are TSV/JSON files the user can open, judged verdicts cite evidence, and every graduation is a proposal the human accepts — propose-don't-write applies to *trust itself*. No CI service, no dashboards-as-infrastructure: the FE surface is one tab on the existing Learning page.

---

## 1. The Shared Substrate — eval store, matrix runner, isolation fix

### 1.1 The evals store

```
~/.personalclaw/evals/
  studies/<study_id>/
    registration.json        # pre-registered spec (§2.1) — hash-stamped, immutable after arm-1 starts
    locked/                  # hidden validation commands + expected outputs (0600; §2.2)
    runs/<arm>/<n>/          # per-run artifacts (journal ref, outputs, judge verdicts)
    verdict.json             # computed result + agreement stats
  benchmarks/<name>/         # fixture sets: retrieval corpora+qrels (§5), judge fixtures (§6), ablation sets (§3)
  matrices/<matrix_id>/      # experiment-matrix outputs: experiment.json, trials.json, aggregates.json (+ .tsv)
  results.tsv                # append-only cross-study ledger: study_id, kind, verdict, score_old, score_new, k, model_fp, ts
  trust/<template_id>.json   # trust-graduation records (§4)
```

All JSON via `atomic_write`; `results.tsv` is append-only (the auto-harness `results.tsv` pattern — every attempt logged, including failures and expired grants). The directory joins `snapshot.py` `VALID_COMPONENTS`/`CORE_FILES` and `portability.py`'s export tree **explicitly** (recon gotcha 10: coverage is partial and a new store is invisible to backup unless listed). Locked validation content is excluded from portability export (it is secret-adjacent by function, not by content — exporting it to a shared bundle would leak the answer key).

### 1.2 The experiment-matrix runner (NEW-11 amendment 1b — the shared engine)

One runner, five consumers. Modeled on MetaHarness's `experiment` command (config keys `project_dirs, backends, budgets, trial_count, models`; outputs `experiment.json/trials.json/aggregates.json` + TSVs + a registry table):

```python
# evals/matrix.py
@dataclass(frozen=True)
class MatrixSpec:
    subject: str            # template id | retrieval-arm set | judge fixture set | use-case
    axes: dict[str, list]   # {model: [...], iterations: [...], arm_mask: [...], budget: [...]}
    trial_count: int = 3
    scorer: str             # "judge" | "assertion" | "qrels" | "command"
    budget_usd: float       # hard cap — runner refuses to start a cell it can't afford

async def run_matrix(spec: MatrixSpec) -> MatrixResult   # trials + aggregates, persisted to matrices/
```

- Cells execute **sequentially** (single-user machine; also sidesteps any residual env-mutation concern) with per-cell wall-clock timeouts and a cost preflight against the guardrails `SpendMeter` (AUTONOMY-GUARDRAILS §1.1) when present — the matrix runner is a *client* of the budget floor, never exempt from it.
- Model axis values are `active_models.json` ref forms (`Provider:model`) resolved through the existing bridge — the matrix never hardcodes a provider (provider fidelity: resolution goes through `resolve_provider_for_use_case` / the build-kwarg `model` override convention, `provider_bridge.py:844`).
- Every cell's raw artifacts persist under `matrices/<id>/` (full per-run artifact retention — amendment 1a's requirement) so a surprising aggregate is always drillable to the run that produced it.
- Aggregates carry the three-state outcome from auto-harness: `passed(score) | failed(score) | verifier_absent` — `verifier_absent` (infra error/timeout, the `None`-reward semantics) is never averaged as 0 into a recommendation without being reported separately.

### 1.3 Isolation fix (precondition, owned here)

`EvalRunner.run_scenario`'s `PERSONALCLAW_WORKSPACE` env mutation moves to **subprocess execution**: the matrix runner spawns each scenario/study run as a child process (`sys.executable -m personalclaw.evals.child --spec …`) with the workspace override in the child's env only. The child reuses `EvalRunner` unchanged internally; the parent gateway process never mutates its own env. This is the §3.4 precondition ("a live-gateway hazard") discharged, and it is Session-1 work because everything else stands on it.

---

## 2. Pre-Registered Template Studies (the eval gate, re-opened)

The formal instrument for "is template v(N+1) actually better than v(N)?" — above GateOK, below nothing.

### 2.1 Pre-registration

A study is registered BEFORE any candidate runs execute (`registration.json`, immutable once arm-1 starts — the MetaHarness `onboard`-before-implement shape: "search/test splits, metrics, budget, and leakage risks defined before implementation"):

```json
{
  "study_id": "st-3f2a91c4",
  "kind": "template_ab",
  "subject": {"template_id": "wf-…", "old_version": 7, "new_version": 8, "diff_proposal_id": "pr-…"},
  "hypothesis": "adding the verify gate at step 3 reduces failed runs on inbox-triage inputs",
  "inputs": ["case ids from the template's harvested regression suite (LEARN-R2) + N recent real-run input snapshots"],
  "k": 5,
  "metric": "primary: rubric median (LOOP-R3 dimensions, pinned); guard: wall_secs, cost_usd, attention_events",
  "rubric_sha256": "…",
  "locked_checks": ["locked/check_01.json", "…"],
  "decision_rule": "win_rate > 0.5 with sign-test p from paired wins/losses/ties at k=5; ANY locked-check regression = fail regardless",
  "model_fingerprint": {"chat": "Provider:model", "eval_judge": "Provider:model"},
  "budget_usd": 2.0
}
```

- **k-run paired**: each input case runs k times under OLD and k times under NEW (same seed profile where the scenario supports it), compared *pairwise per case* — win/loss/tie per case, aggregated by win rate. Paired comparison at k≈5 is the smallest design that survives judge noise (GBrain's finding that single-run judge acceptance is indistinguishable from noise, already honored by LEARN-R2's median-of-3 — studies add the pairing).
- Default input corpus = the template's **harvested regression suite** (LEARN-R2's organically-grown set) plus recent real-run inputs sampled from the Run Ledger — no authored goldens required; a template with an empty suite gets a smaller, honestly-labeled "low-power" study.
- The pre-registration itself can be DRAFTED by the flywheel refiner (it knows the diff and the failure cluster), but registration is a proposal-queue item: **the human registers the study; the substrate runs it.**

### 2.2 Hidden locked validation commands (structural anti-nodding)

Each study carries `locked/` checks — command + expected-outcome pairs (the MetaHarness weighted `file_phrase`/`command` task DSL: `{id, weight, command, expect_exit_code}` / `{id, path, weight, required_phrases[]}` — a minimal eval DSL, cheaper than authoring pytest suites per template):

- Stored under `evals/studies/<id>/locked/` (0600), **never rendered into any worker session's prompt, bindings, or workspace** — the auto-harness `HARNESS_SAVE_TRACE=0` doctrine: information hygiene is structural, not instructional. The study child-process runner executes locked checks *supervisor-side after* each run completes, in the run's output workspace.
- This is the structural complement to LEARN-R10's statistical nodding-loop detector: a check that the worker cannot read cannot become a 100%-pass fake-check, because the worker cannot shape its output to the check's letter. LEARN-R10 stays where it is (approved); §2.2 supplies the class of checks its detector should never fire on.
- Command execution goes through the existing screen (`audit_bash_command`, the `loop/gates.py:run_verify_command` tristate convention: True/False/None with exit-127→None) so a locked check that can't run reports `verifier_absent`, never a silent pass.

### 2.3 Blinded, rubric-pinned judging with agreement checks

- **Rubric pinning**: the judge rubric (LOOP-R3's fixed 0-2 dimensions with evidence citations) is hashed at registration (`rubric_sha256`); the judge prompt renders from the pinned text. A rubric edited mid-study invalidates the study (hash mismatch → verdict `invalidated`).
- **Blinding**: the judge (`eval/judge.py:LLMJudge`, `eval_judge` use-case — no new judge machinery) receives paired outputs labeled A/B with randomized assignment recorded outside the prompt; it never sees version numbers, timestamps, or the hypothesis. Judging is comparative ("which better satisfies the rubric, or tie"), which is more discriminative than absolute scoring at personal sample sizes.
- **Agreement checks**: (a) each pair is judged with `judge_samples: 3` median (the approved LOOP-R3 mechanism, reused verbatim); (b) each pair is additionally judged **position-swapped** (A/B then B/A) — a pair whose verdict flips with position is recorded as `tie/no-signal`, not counted for either arm (position bias is the dominant comparative-judge artifact); (c) the study verdict reports the agreement rate; below a floor (default 0.6) the verdict is `judge_unreliable` and the study auto-files a judge-calibration item into §6's benchmark queue instead of a template verdict — a bad judge produces work for the judge harness, never a fake win.
- Parse failures score 0 per LLMJudge's existing reject-by-default behavior; `cannot_judge` (LOOP-R3's typed escape hatch) counts as no-signal.

### 2.4 What a study verdict does

- `verdict.json` + a `results.tsv` row, always — wins, losses, and `invalidated`/`judge_unreliable` alike (append-only honesty, the auto-harness ledger rule).
- A **pass** is the evidence unit §4's trust ladder consumes and the strongest signal on the diff's LEARN-R16 change manifest (`predicted_fixes` verified by a study, not just by attribution drift).
- A **fail** on a flywheel-accepted diff auto-files a demotion/revert proposal through the unified queue — same channel as LEARN-R2's canary revert, stronger evidence.
- Studies gate NOTHING silently: GateOK still filters proposals pre-surfacing (approved, unchanged); studies are the *deliberate* instrument the user (or the trust ladder) invokes when the stakes are graduation, not surfacing.

---

## 3. Harness Ablation Runner + Model-Upgrade Watchdog

Harness components compensate for model weaknesses whose assumptions expire silently (the course's Anthropic example: sprint-splitting became dead weight when Opus 4.6 decomposed natively, while the evaluator still earned its keep). Nothing in PClaw can currently answer "does this judge/stage/hint still pay for itself?"

### 3.1 The ablation runner

A periodic (default monthly — the course's cadence), trigger-fired workflow (`run-workflow` action provider; a cron today, `Trigger{kind:clock}` after AUTOMATION-SUBSTRATE):

1. **Pick one component** from the ablation registry: a template's gate/judge node, a runtime hint (rubric dimension, decomposition hint), a surfacing source (skills 0.55 arm vs SOP 0.62 arm — the split LEARNING-FLYWHEEL §2.4 keeps "until measurement shows it unjustified"; this is that measurement), or a §2.4-slot allocator stage. One component per run, never batched (LEARN-R9's one-at-a-time removal rule, applied to measurement too).
2. **Run a small fixed benchmark** through the matrix runner (§1.2): the component's owning template(s) replayed over their harvested suites with the component ON vs OFF (`arm_mask` axis). Component toggling is a config/spec **overlay applied only inside the child process** — the live template/config is never mutated.
3. **Score + recommend**: keep (measurable degradation when off) / **remove** (no delta — files a LEARN-R9 `retirement` proposal WITH this report attached as the "ablation-grade evidence" R9 requires but nothing previously generated) / **lighten** (delta exists but a cheaper variant matches — e.g. judge at a smaller tier per §6's table).
4. Every recommendation is a proposal through the flywheel queue. The runner never edits anything.

Ablation-delta honesty rides LEARN-R4's already-approved rule ("every surfacing heuristic ships with a measured delta and is removed if ~0") — this runner is the offline generator for deltas the online events can't isolate.

### 3.2 The model-upgrade watchdog

- **Seam**: `active_models.json` is the single file where use-case bindings change (`provider_bridge.py` reads it for every resolution). The watchdog is a `kind:file` watcher on it (post-substrate; an mtime check on the maintenance tick today). A change to the `chat`/`reasoning`/`background`/`eval_judge` binding computes a new **model fingerprint** and:
  1. Queues a re-benchmark of judge prompts (§6, against the new tier) and of the top-N most-run templates' harvested suites (matrix runner, small budget).
  2. **Expires trust grants** whose `model_fingerprint` no longer matches (§4.3) — graduation evidence is model-specific by construction, because harness components compensate for *specific* model weaknesses.
  3. Files ONE digest notification (through `DashboardState.notify`, the existing gate) summarizing what was queued and what expired — never N notifications.
- Baselines are per-fingerprint rows in `results.tsv`, so "did the upgrade change anything" is a query, not a feeling.

### 3.3 Skills/templates impact bench (amendment 2, OpenJarvis shape)

The OpenJarvis `jarvis bench skills --max-samples --seeds` measurement — per-skill impact from real runs — maps onto the same machinery: for a given skill, replay its harvested/consulted run inputs (the WF2-R13 `consulted` ledger event tells us which runs actually loaded it) with the skill surfaced vs suppressed (`arm_mask`), and report the outcome delta. This is the measurement half that makes LEARN-R2-gated skill-overlay acceptance (sidecar overlays, approved in LEARN-R3) benchmarkable per skill, and it feeds the curator's aging with something better than recency.

---

## 4. Trust-Graduation Ladder — autonomy earned via ledger + study evidence

### 4.1 Division of labor (unchanged owners, new evidence tier)

- LEARN-R11 (approved) computes **maturity L0-L3** from static spec signals + demonstrated ledger activity. Unchanged; the flywheel computes it.
- UP-R6 (approved) owns **mode-switching**: report-only first runs, promotion *suggested* after N verified successes, mid-run demotion, remembered-last-choice. Unchanged; UNIVERSAL-PLANNING owns the approval gate.
- **This plan adds the rung both point at but neither specifies**: the durable, auditable *evidence record* that converts "L3 + N successes" into a standing unattended grant — and expires it when its premises die.

### 4.2 The trust record

```json
// evals/trust/<template_id>.json
{
  "template_id": "wf-…", "level": "unattended",
  "granted_at": "…", "granted_by": "user",
  "evidence": {
    "maturity": "L3",
    "study_ids": ["st-…"],                    // ≥1 passing §2 study on the CURRENT version
    "clean_runs": 14, "run_ids_sample": ["…"],
    "attention_trend": {"per_run_p50_events": 0.2, "window": "30d"}
  },
  "model_fingerprint": {"chat": "…", "eval_judge": "…"},
  "expires": {"on_model_change": true, "on_template_version_change": true, "max_age_days": 180}
}
```

- **Grant path**: the ladder emits a *graduation proposal* into the unified queue when preconditions hold (maturity L3 per the flywheel; ≥1 passing study on the current version; attention trend not regressing). The human accepts; the record is written; the accept is SEL-audited (`sel.py`) exactly like a skill install — granting autonomy is a security-relevant event.
- **Consumption**: UNIVERSAL-PLANNING's approval gate reads the record to decide what to OFFER (a valid record = unattended offered by default for this template; absent/expired = the existing report-only/per-stage defaults). The engine's HITL typing, risk-registry caps, and autonomy floors all still apply on top — trust never overrides a risk-registry hit.
- **Revocation is mechanical**: a HARMFUL attribution verdict (LEARN-R16), a failed §2 study, a nodding-loop flag (LEARN-R10), or watchdog expiry (§3.2) flips the record to `revoked` with the triggering evidence id, files a notification, and the next run falls back to per-stage. Revocation needs no human (fail-safe direction); re-granting always does.

### 4.3 Ladder rungs (data, not ceremony)

`observed` (report-only; UP-R6's default) → `gated` (per-stage) → `verified` (first-stage-only / auto-approve read-only; N clean runs) → `unattended` (L3 + passing study + valid fingerprint). Rung names are stored, surfaced as a chip on template rows and in the approval dialog — one glance answers "why is this template allowed to run overnight?"

### 4.4 Human-attention accounting (the optimization target)

Autonomy's honest objective is *attention saved without outcome regression*. The Run Ledger already records the attention events (`gate_rejected`, `user_edited_mid_flight`, needs-input continuation records, gate answers, judge overrides / `judge_divergence`); this plan adds:

- **One derived metric**, `attention_events_per_run` (count-based; optionally dwell-estimated from needs-input open→resolve timestamps that the WF2-R7 continuation records already carry) — computed by ledger query, stored nowhere new.
- **One small ledger addition**: needs-input *resolution* events carry `resolved_after_secs` (the continuation record has `expires_at`/created timestamps; this makes the dwell explicit rather than re-derived).
- The trust ladder's promotion proposal and the Learning-page panel both report it: a template graduating to unattended should show attention trending → 0 while its rubric medians hold — and a graduated template whose attention *rises* (user keeps intervening post-grant) is a mechanical demotion signal.

---

## 5. Retrieval Eval Harness with Per-Arm Ablation

The substrate MEMORY-GRAPH-AND-VAULT (NEW-3) needs to justify itself — GBrain's credibility rests on exactly this artifact (BrainBench: a small versioned corpus, P@5/R@5, and a published graph-disabled ablation showing +31.4 P@5 from the graph arm alone).

### 5.1 Two targets, one runner, boundary respected

- **Knowledge target**: `knowledge/retrieval.py:HybridRetriever` — arms = FTS5 keyword / graph traversal / vector, RRF-fused, plus any future rerank stage. The harness calls the retriever with arms masked (a test-only constructor knob or arm filter — the retriever already tags hits with `match_type`, so per-arm attribution is free).
- **Memory target**: `vector_memory` hybrid recall (0.6·vec + 0.4·kw) and, once MEMORY-GRAPH lands, its graph arm and push-context resolver arms (alias/exact/fuzzy).
- **Boundary (user directive, load-bearing)**: KNOWLEDGE = the user's personal items in knowledge.db; MEMORY = harness mechanics in memory.db. The harness runs the SAME runner against each store **read-only** and never cross-queries, never shares a corpus, never writes to either — fixtures and qrels live in `evals/benchmarks/`, which is harness mechanics, not memory entries and not knowledge items.

### 5.2 Corpus + qrels, personal-scale

- A benchmark = `{corpus_snapshot_ref, queries: [{q, relevant_ids[]}], created_at, store: knowledge|memory}` — a few dozen queries, not thousands. Sources, cheapest first: (a) **mined weak labels** from LEARN-R4's `surfacing_events` + MEMORY-GRAPH's volunteered-vs-used events (a hit retrieved-then-used is a positive; high-confidence chip-muted is a negative); (b) a **hand-labeling pass** proposed as a 10-minute Learning-page card ("mark which of these 8 results answer this real query of yours") — the human supplies ground truth for the head queries; (c) synthetic entity queries generated from the alias table (known-item search: the page IS the answer).
- Corpus versioning by snapshot reference (row-id set + content hash), NOT by copying the stores — re-running an old benchmark against a grown store reports "corpus drifted" honestly instead of silently changing denominators.

### 5.3 Metrics + ablation report

P@k / R@k (k=5 default) per arm-mask, plus per-arm *contribution* (score with arm ON minus OFF — the BrainBench ablation shape). The report answers, with numbers: does the graph arm earn its complexity; does vector beat keyword on this user's actual corpus; what would a reranker have to beat. Dark-shipped arms (LEARN-R4's rule: new arms judged by citation data before enablement) get their offline verdict here before the online one accumulates.

### 5.4 Shared machinery note

The harness is a §1.2 matrix consumer (`scorer: "qrels"`, axes = arm_mask × k), so its reports land in the same `matrices/` + registry-table UI as everything else — one place to look, per amendment 1b's "shared between harness-ablation and local-model validation."

---

## 6. Judge Benchmark Harness → Tier-Recommendation Table

Every gate in the flywheel and the engine ultimately rests on LLMJudge verdicts, and the single calibration instrument today is `loop/instrument.py:probe_judge`'s strong-vs-null separation (≥1.5) on one model. Generalize it:

- **Fixture set** (`evals/benchmarks/judge/`): pairs of (artifact, rubric, known-good verdict) harvested from real judged runs — including deliberately-bad exemplars (the null probes), past `judge_divergence` cases (user overrode the judge — gold calibration data, already ledger events per LOOP-R3), and forbidden-success-mode cases. A dozen-to-thirty fixtures; grown organically like the regression suites.
- **Matrix**: fixtures × model tiers (every model bound or bindable to `eval_judge`/`background`, local tiers included) × iteration counts (`judge_samples` 1/3/5) — full per-run artifact retention (amendment 1a), run through §1.2.
- **Output — the tier-recommendation table**, published as a static artifact + a Settings/Learning panel table: per (rubric-class × tier × samples): agreement-with-known-verdict, strong-vs-null separation, position-swap flip rate, cost, wall time — with **honest failure-mode notes** per cell ("local 8B: parses reliably, cannot cite evidence lines"; "tier-X at samples=1: verdict flips with position 40%"). The table is what lets the user (and the ablation runner's "lighten" recommendation, §3.1) bind judges to the cheapest tier that actually judges — and what §2.3's `judge_unreliable` verdicts queue new fixtures into.
- Rebinding a use-case from the table is a **user action** on the existing Models panel — the harness recommends, the human rebinds (same posture as §7).

---

## 7. Model Bake-Off from Production-Sampled Inputs

For choosing cheap models behind micro-tasks (inbox classify/draft, title generation, intent synthesis, memory lint — everything on the `background` use-case):

- **Sampling**: real inputs are drawn from `model_calls.jsonl` (AUTONOMY-GUARDRAILS §2's attempt audit carries `use_case` + the audit correlates attempts; where prompt bodies aren't retained, the sampler captures the next N live inputs per use-case behind a temporary, size-capped, user-enabled flag) — production-sampled, never synthetic, because micro-task models fail on the user's real formatting quirks, not on benchmarks.
- **Privacy floor**: sampled inputs pass `security.py:redact()` before persisting to `evals/benchmarks/bakeoff/`; the capture flag is off by default and auto-expires (config, four wiring points).
- **Run**: matrix over candidate models (local `LocalModel`-contract providers + bound cloud refs) × the sampled set; scored by rubric-pinned comparative judging (§2.3 machinery) or task-native assertions where the output is checkable (classification labels, JSON validity via the guardrails `output_type` path).
- **Output**: a per-use-case recommendation row ("`background`: local X matches tier-Y at 0.04× cost on your inbox traffic; fails on threads >8K tokens") → a *proposal*; the user rebinds via `active_models.json` as today. Distinct from NEW-25's learned per-call routing (still backlog): this is offline, per-use-case, human-applied.

---

## 8. Bundled `optimize-harness` Template

The proactive half: budgeted search over PClaw's own harness artifacts (a template's prompt blocks, a skill body, an SOP), expressible entirely in the v2 node taxonomy — shipped as a starter in `workflows/bundled/` like any template.

### 8.1 The loop (auto-harness + MetaHarness composed onto v2 nodes)

`loop` node wrapping: **propose** (subagent or BYO runner emits a candidate edit + LEARN-R16 change manifest with `predicted_fixes`/`risk_tasks`) → **diff + scope-check** (the engine's `allowed_write_paths` snapshot/diff → `scope_violation` terminal state — WORKFLOWS-V2 §write-scope, already specified there; candidates that touch the frozen region are dead regardless of score) → **validate** (cheap existence/non-emptiness before any LLM spend — MetaHarness's ordering; `no_change` candidates inherit parent scores without re-evaluation) → **score** (weighted `file_phrase`/`command` checks + rubric judge over the target's harvested suite, via §1.2 in a child workspace) → **keep/discard** (hill-climb: keep strict improvement against BOTH the regression-suite threshold AND the monotonic best-ever from the target's `results.tsv` rows — auto-harness's dual gate, which LEARN-R2 already approved as GateOK; here it runs inside the search).

### 8.2 Stop conditions + budget (amendment 1d)

Declared in the template spec, enforced by the engine's existing breaker/budget machinery: `hypothesis_abandon_after: 3` (same fix attempted 3× → abandon the hypothesis, the auto-harness rule), `no_improvement_halt: 5` (5 consecutive non-improving iterations → halt, write a summary + a structured `needs_from_human` journal entry), `budget_usd` hard cap (guardrails SpendMeter), and per-iteration `results.tsv`-style ledger rows including discards.

### 8.3 Propose-don't-write, exactly

Inner keep/discard operates on **candidate copies in the run workspace only**. The winning candidate lands as a proposal in the unified queue — a template-diff (versioned apply, LEARN-R2 discipline unchanged) or a skill **sidecar overlay** (LEARN-R3's approved apply/revert mechanism; base file and its `.pclaw-lock.json` hashes never mutated, `verify_skill_integrity` stays green). The teacher/student split (frontier model diagnoses failure clusters in local traces, proposes typed tier-gated edits) is already approved in LEARN-R3's tiers — this template is simply a *runnable packaging* of it with the search loop attached. Refiner tool-scoping carries over verbatim: the optimizing agent gets `propose_*` tools only.

### 8.4 Experience directory

Per MetaHarness's headline finding (+7.7pts for agentic proposers reading raw prior artifacts vs compressed summaries — already adopted by LEARN-R3 for the refiner): each iteration's workspace carries `.experience/` with prior candidates' diffs, scores, and check results, indexed. Pareto secondary selection on (score, context-cost) uses TokenJuice's existing accounting as the cost metric.

---

## 9. Disposition & Dependency Notes

| Item | Verdict |
|---|---|
| Flywheel §3.4 contingent eval gate | **RE-OPENED, re-sized** — not team CI: harvested inputs, k≈5 paired runs, child-process isolation (§1.3 discharges §3.4's named precondition). GateOK/canary/harvested-suite (LEARN-R2) stay as the always-on cheap tier; studies are the deliberate instrument above them |
| `eval/` package (judge, runner, scenario) | **EXTEND** — LLMJudge reused for all judging (no second judge); EvalRunner wrapped in subprocess isolation; scenario/assertion vocabulary reused; new `evals/` modules (matrix, studies, retrieval, bakeoff) live beside it |
| `loop/instrument.py:probe_judge` | **GENERALIZE** into §6's judge benchmark (same strong-vs-null shape, multi-tier); the probe itself stays as the template-save-time quick check (LOOP-R3 keeps it) |
| Nodding-loop detection | **APPROVED elsewhere** (LEARN-R10, statistical) — this plan adds only the structural complement (§2.2 hidden locked checks) |
| Scaffolding-retirement + judge-calibration proposals | **APPROVED elsewhere** (LEARN-R9, LEARN-R10/LOOP-R3) — this plan is their missing *evidence generator* (§3 ablation reports, §6 fixtures from divergence events) |
| Template maturity / autonomy modes | **UNCHANGED OWNERS** (LEARN-R11 computes, UP-R6 offers) — §4 adds the trust record + study rung + fingerprint expiry + attention metric between them |
| Retrieval benchmark vs MEMORY-GRAPH's online stats | **COMPLEMENTARY** — MEMORY-GRAPH's volunteered-vs-used per-arm precision (health tab) stays; §5 is the offline, ground-truth, arm-ablated verdict; each feeds the other (mined qrels ↔ dark-ship judgments) |
| Experiment-matrix machinery | **ONE runner** (§1.2) shared by §2/§3/§5/§6/§7 and available to LOCAL-MODEL-MANAGER-V2 validation ("does the local 20b handle this template or time out" — the MetaHarness result class) |
| NEW-25 learned routing | **NOT here** (still backlog) — §7 is offline per-use-case recommendation, human-applied; the telemetry it would need (WF2-R13 per-node cost, model_calls.jsonl) is consumed read-only |
| Sequencing | §1, §5, §6 need nothing new (Wave-3-early / can front-run); §2, §4, §8 need Run Ledger (WF2 Slices 0-3) + flywheel proposal queue (steps 3+); §3.2 watchdog needs only `active_models.json` (works today via maintenance-tick mtime check, upgrades to `kind:file` trigger post-substrate) |

---

## 10. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE.** Evaluation is substrate, like guardrails — nothing registers through `_TypeHandler`s. Model resolution is exclusively `one_shot_completion(use_case=…)` / `provider_factory("eval_judge")` over `active_models.json`; the matrix's model axis uses the canonical `Provider:model` ref form and the `model` build-kwarg override convention (`provider_bridge.py:844`). Local models participate through the existing `LocalModel`/`LocalModelProvider` contract — the harness never special-cases a provider.
- **Action providers:** every periodic runner (ablation cadence, watchdog re-benchmarks, bake-off) fires as trigger → `run-workflow`, which is ALREADY in `ALLOWED_HOOK_PROVIDERS` (`src/personalclaw/validation.py`) — **no allowlist change needed**. If a future slice ships a dedicated `run-study` action provider instead, it MUST be added to that frozenset or hook create/update rejects it (restating the rule because this substrate is where such a provider would be born).
- **Config:** new top-level `EvalsConfig` section beside `SecurityConfig`, wired through the FOUR points: (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce); (b) `AppConfig.load()` explicit field-by-field mapping (`loader.py:1638-1802` — omission = silent drop); (c) `to_dict()` — a NEW top-level section must be added at `loader.py:1930`; (d) `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) + FE for the runtime-editable subset. Fields: `evals.enabled`, `evals.study_default_k=5`, `evals.judge_agreement_floor=0.6`, `evals.ablation_cadence_days=30`, `evals.bakeoff_capture_enabled=false` (+ auto-expiry), `evals.default_budget_usd`.
- **Stores:** `~/.personalclaw/evals/` (§1.1) — `atomic_write` JSON + append-only TSV; added to `snapshot.py` `VALID_COMPONENTS`/`CORE_FILES` and `portability.py` export (locked/ excluded from export). Fixtures/qrels/results are **harness mechanics**: nothing here writes to `memory.db` or `knowledge.db` (the retrieval harness reads both, read-only), and no eval artifact is a memory entry or a knowledge item — the MEMORY/KNOWLEDGE boundary is untouched by construction.
- **SEL:** trust grants/revocations, study registrations, and bake-off capture-flag flips log to `sel.py:SecurityEventLog`, same as skill installs and egress blocks.
- **Guardrails:** the matrix runner meters through `SpendMeter` and respects the incident kill switch (an active incident suspends all eval runs — they are unattended work); child processes inherit `DISABLE_LIVE_WRITES` semantics in tests.
- **FE:** one new tab on the Learning page (studies list + verdicts, ablation reports, the §6 tier table, trust-ladder chips with evidence drill-down, attention trend sparkline) + the trust chip on template rows and in UNIVERSAL-PLANNING's approval dialog. New SSE/refresh needs ride `push_refresh`; any new per-run stream event must be added to the FE's registered event union (the `RUN_LIFECYCLE` gotcha — EventSource drops unregistered types).
- **Apps:** third-party apps get no eval write path; app-shipped templates are eligible study/ablation subjects like any template (their pinned `{source, computedHash}` metadata distinguishes upstream drift from local candidates, per LEARNING-FLYWHEEL §2.3).

---

## 11. Implementation Effort

**~5 sessions.**

- **Session 1 — substrate + isolation (§1):** `evals/` store bootstrap (+ snapshot/portability listing); subprocess isolation for EvalRunner (the §3.4 precondition); the experiment-matrix runner with budget preflight, three-state outcomes, per-cell artifact retention; `EvalsConfig` through all four wiring points; SEL hooks.
- **Session 2 — retrieval harness + judge benchmark (§5, §6):** arm-masked runners against HybridRetriever and memory recall (read-only); qrels mining from surfacing/volunteer events + the hand-label card; P@k/R@k ablation report; judge fixture harvesting (incl. divergence events) + the tier-recommendation table + Settings/Learning rendering. Both v2-independent — this session can front-run.
- **Session 3 — template studies (§2):** registration schema + immutability; locked-check store + supervisor-side execution; blinded paired judging (position-swap, agreement floor, `judge_unreliable` routing); verdict → results.tsv → proposal-queue wiring (demotion on fail).
- **Session 4 — ablation runner + watchdog + trust ladder (§3, §4):** component registry + overlay-in-child toggling; keep/remove/lighten reports feeding LEARN-R9 retirement proposals; `active_models.json` watchdog (fingerprints, grant expiry, digest notification); trust records + graduation proposals + revocation paths + attention-accounting queries; approval-dialog + template-row chips.
- **Session 5 — bake-off + optimize-harness template + validation (§7, §8):** production sampler (redaction, capped, auto-expiring flag) + per-use-case recommendation flow; the bundled optimize-harness template (stop conditions, dual gate, experience dir, proposal emission) in `workflows/bundled/`; end-to-end as-a-user validation sweep across all surfaces.

Sessions 1-2 are shippable before any v2 slice; 3-5 assume Run Ledger + flywheel queue availability.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Study cost blowout (k×2 runs × judge samples) | Pre-registered `budget_usd` hard cap + SpendMeter preflight; comparative judging is cheaper than absolute; k=5 default; studies are deliberate events (graduation-gated), not per-diff |
| Judge noise swamps small-k signal | Paired design + median-of-3 + position-swap agreement + the `judge_unreliable` escape verdict — a noisy judge produces judge-harness work, never a false template verdict |
| Locked checks leak into worker context via workspace | Checks live under `evals/`, executed supervisor-side in the child's OUTPUT workspace post-run; never bound, never copied into run dirs; a regression test asserts no `locked/` path appears in any rendered prompt |
| Harvested suites too small early → underpowered studies | Verdicts carry explicit power labels (`low_power` at <N cases); low-power passes grant `verified`, never `unattended` — the ladder degrades honestly instead of pretending |
| Corpus drift invalidates retrieval baselines | Snapshot-ref versioning; drifted re-runs report "corpus drifted" with both numbers rather than silently comparing across denominators |
| Trust-grant expiry storms on every model tweak | Expiry only on fingerprint-RELEVANT use-case changes (chat/reasoning/eval_judge for the template's executor); one digest notification; re-benchmarks queued small-budget, oldest-grant first |
| Ablation overlay diverges from live behavior | Overlays applied in child processes against the SAME spec/config the live path loads (one loader, one toggle point); a fixture test diffs child-resolved config against live-resolved config with the mask empty |
| Production-input sampling captures sensitive text | Off by default, `redact()` before persist, size-capped, auto-expiring, SEL-audited flag flips; sampled files live under the 0600 store and are excluded from portability export |
| This plan quietly becomes MLOps | Soul tripwires: no service processes (everything trigger-fired or user-invoked), all results are files, every promotion/removal/rebind is a human-accepted proposal, FE is one tab — reviewers should reject any slice adding a daemon or an auto-apply lane |

---

## Success Criteria

1. A flywheel template-diff seeking unattended graduation runs a pre-registered study: k=5 paired old-vs-new over the harvested suite, blinded median-of-3 position-swapped judging, locked checks executed supervisor-side — and the verdict, agreement rate, and per-run artifacts are inspectable from the Learning page; a rubric edited mid-study invalidates it.
2. A prompt-injection-shaped or overfit candidate that games the visible rubric still fails: the hidden locked checks (never present in any worker prompt or workspace — regression-tested) catch the regression, and the study fails regardless of judge score.
3. The ablation runner produces a keep/remove/lighten report for one component per cadence with measured deltas; a no-delta component's report attaches as evidence on a LEARN-R9 retirement proposal — and at least one real component (a hint, a gate, a surfacing arm) gets retired or lightened on evidence within the first month of operation.
4. Changing the `eval_judge` or `reasoning` binding in `active_models.json` expires matching trust grants, queues re-benchmarks under a small budget, and emits exactly ONE digest notification; per-fingerprint baselines make "did the upgrade change anything" a `results.tsv` query.
5. The retrieval harness reports P@5/R@5 per arm-mask for BOTH stores (knowledge and memory, run separately, read-only) from a personal-scale qrels set; the graph arm's marginal contribution is a number, and a dark-shipped arm gets its offline verdict before enablement.
6. The judge tier-recommendation table shows agreement/separation/flip-rate/cost/time per (tier × samples) with honest failure-mode notes, and rebinding a judge to a cheaper adequate tier is one user action informed by it.
7. A template reaches `unattended` ONLY via: flywheel-computed L3 + a passing unexpired study + a human-accepted graduation proposal (SEL-audited); a HARMFUL verdict, failed study, or fingerprint expiry revokes mechanically and the next run falls back to per-stage.
8. Attention accounting answers "is autonomy paying?": per-template attention-events-per-run trends render on the Learning page, graduation proposals cite the trend, and a post-grant attention rise files a demotion signal.
9. The bundled optimize-harness template completes a budgeted search over one of PClaw's own skills/templates: candidates scope-checked by diff, scored against the dual gate (suite threshold + monotonic best-ever), halted by hypothesis-abandon/no-improvement rules — and its winner arrives as a PROPOSAL (template version or skill sidecar overlay) that the human installs; nothing live mutates during search.
10. The whole substrate runs with zero new daemons: every runner is a trigger-fired workflow or a user click, every result is a file under `~/.personalclaw/evals/`, and snapshot/restore round-trips it.

## Amendment (2026-07-26 — gap analysis round 2, owner-approved mechanisms)

**The three-loop sharpening.** The plan already contains all three loops in embryo; sibling-platform evidence says the make-or-break move is making them *distinct, named, and cross-referencing*: Loop 1 lab evals say "should be better," Loop 3 field metrics say "is," and Loop 2 is the gate between a change and shipping. This amendment sharpens existing sections rather than duplicating: §2/§1 already carry pinning and studies (Loop 1's formal tier); §2.4/§8 already gate flywheel diffs (Loop 2's deliberate tier); §4.4 already computes attention (part of Loop 3). What is missing: the existing `eval/` embryo (4 scenarios in `eval/scenarios/*.json`, `LLMJudge`, `EvalRunner` — verified) never grew into a versioned scenario LIBRARY with pinned runs; regression gating has no *cheap always-on subset* below the k=5 study tier; and no surface renders field metrics beside lab results. New dependency noted: **FEEDBACK-SIGNAL (plan 58, created this same rev)** supplies Loop 3's 👍/👎 records, alongside the earned-autonomy ledger (AUTONOMY-GUARDRAILS round-2 amendment, `autonomy_rungs.json` + SEL approval outcomes).

### Contract-level design

- **Loop 1 — scenario evals (lab):** grow `eval/scenarios/` into `~/.personalclaw/evals/scenarios/` (joins the §1.1 store), a versioned library over seeded fixture homes (the `tests_fixtures/` seed mechanism `--seed` already ships; scenarios reference a fixture home by name so runs are reproducible from a clean state). Every run is PINNED:

```python
# evals/pinning.py
@dataclass(frozen=True)
class RunPin:
    scenario_id: str
    scenario_sha256: str
    model_fingerprint: dict[str, str]   # per use-case "Provider:model", from active_models.json
    prompt_pack_sha256: str             # hash over the resolved system/judge prompts
    config_snapshot_ref: str            # relevant AppConfig subset hash
```

  Executed via the §1.2 matrix runner in §1.3's subprocess isolation (unchanged); results persist as artifacts under `matrices/` + a `results.tsv` row carrying the pin — "did anything change" is a pin-diff query. This extends §3.2's model fingerprint from a watchdog key to a universal run attribute.
- **Loop 2 — regression gating:** a curated CHEAP subset (`evals/scenarios/gate/` — a dozen fast scenarios, assertion-heavy, judge-light) re-runs before any prompt/skill/routing change ships. Explicitly INCLUDING self-modification proposals from the Learning-Flywheel interpretive arm: the proposal card carries `{before: {scenario_scores}, after: {scenario_scores}, pin}` (the golden-set pattern — §8.3's proposal emission gains the two score columns; GateOK stays the flywheel's own pre-surfacing filter, this is the substrate-side evidence attached to what surfaces). A gate-subset run is minutes and cents, sitting BELOW the §2 k=5 study tier (which remains the graduation instrument).
- **Loop 3 — live quality (field):** derived metrics, computed by query, stored nowhere new (the §4.4 discipline): per-template/per-action-type 👍/👎 rate + edit-before-approve rate from plan 58's FEEDBACK-SIGNAL store, plus approval/rejection/undo rates from the earned-autonomy ledger. Rendered BESIDE lab results on the §10 Learning-page tab — one row per subject: lab score (Loop 1, pinned) | gate status (Loop 2) | field trend (Loop 3). A subject whose lab score rose while its field trend fell is flagged `lab_field_divergence` — the honest "should-be vs is" check, and a §4.2 trust-record demotion signal.

### Session placement

Sharpens, doesn't append: RunPin + scenario library extend **Session 1** (the store/matrix session); the gate subset + proposal score columns extend **Session 3** (studies/verdict wiring); Loop 3 rendering extends **Session 4** (trust ladder + attention queries — same page, same data direction). The added dependency (plan 58 S1) gates only the Loop 3 half of Session 4. Honest count ~5 → **~6** (the three extensions together are one honest session, spread across S1/S3/S4).

| ID | Task | Files | Done when |
|---|---|---|---|
| E1 | `RunPin` + scenario library migration (`eval/scenarios/` → versioned `evals/scenarios/` over named fixture homes); every matrix/study/gate run persists its pin; pin-diff query over `results.tsv` (extends Session 1) | `evals/pinning.py`, `evals/matrix.py`, `eval/runner.py` child wiring, store layout | re-running a scenario after a model rebind yields a different fingerprint row, same scenario hash; a run without a pin cannot be written to results.tsv |
| E2 | Gate subset (`evals/scenarios/gate/`) + before/after score columns on flywheel self-modification proposals; a prompt/skill/routing proposal without a gate run renders "ungated" honestly (never blocks pre-flywheel) (extends Session 3) | gate scenario set, §8.3 proposal emission, proposal card FE | a planted regression in a candidate skill edit shows a score drop on its own proposal card before the user accepts; gate run cost is bounded and metered via SpendMeter |
| E3 | Loop 3 derived field metrics (plan-58 records + autonomy ledger + SEL outcomes, query-computed) rendered beside lab results on the Learning tab; `lab_field_divergence` flag feeding §4.2 demotion (extends Session 4; gated on plan 58 S1) | ledger query module, Learning-page tab, trust-record wiring | the tab answers "lab says better — is it?" per subject in one row; a post-ship field decline on a lab-improved subject files a divergence flag and a demotion signal, mechanically |

## Execution log — ES-1a (pure evals substrate: store + matrix types + EvalsConfig) — ES-1 split

- **ES-1 DECOMPOSED (tick-14 design verdict); ES-1a DONE.** ES-1 split into 1a (pure store +
  dataclasses + config round-trip + inventory listing — deterministic, no process/LLM) and 1b (the
  child-process matrix runner + SpendMeter preflight + SEL). Design verified ES-1 startable now with
  NO EXECUTION-ISOLATION dependency — the "spawned child with PERSONALCLAW_WORKSPACE in the child
  env only" is a plain `subprocess` + `os.environ.copy()` (precedent: `schedule_script.py:
  run_script_sandboxed`), strictly weaker than and independent of EI's SandboxProvider.
- **ES-1a shipped:** new `evals/` package — `matrix.py` (the shared TYPES: frozen `MatrixSpec` with
  JSON round-trip, `CellResult`, `MatrixResult`, and the three-state `PASSED|FAILED|VERIFIER_ABSENT`
  `aggregate()` that computes the mean over scored cells ONLY — a verifier that couldn't run is
  counted separately, NEVER averaged in as a 0); `store.py` (`evals/` layout under the home,
  `matrices/<id>/` artifact dirs with `experiment.json`/`aggregates.json`/`trials.json` via
  `atomic_write`, and the append-only `results.tsv` ledger with a stable ordered column set +
  tab/newline neutralization + unknown-key rejection so it can't be silently widened; `studies/`/
  `benchmarks/`/`trust/` deliberately NOT created — no writer yet, no dead scaffolding). `EvalsConfig`
  wired through all 4 config points (dataclass+`_meta`, explicit `load()` mapping, `to_dict()`,
  `_EDITABLE_CONFIG`) — with `bakeoff_capture_enabled` DELIBERATELY EXCLUDED from the editable
  allowlist (a privacy-sensitive capture flag, off-by-default, SEL-audited when flipped, mirroring
  `inbound.mcp.allow_remote`). One `evals` `StateEntry` (KIND_TREE, DOMAIN_PLATFORM,
  MERGE_UNION_BY_ID) claims the whole tree so `audit_home()` + snapshot/portability cover it. No user
  surface (the FE tab is a later atom) → no CHANGELOG. **Remaining:** ES-1b (child-process `run_matrix`
  + budget preflight + SEL run-lifecycle hooks; the `locked/` export-exclusion the design flagged is a
  STUDIES concern for ES-2/ES-5, not here). **Gates:** `make lint` clean (718 files);
  `tests/test_evals_store.py` (10) + config round-trip/schema + durability inventory (48 total) +
  `test_snapshot.py` (111) pass.

## Execution log — ES-1b (child-process matrix runner) — ES-1 COMPLETE

- **ES-1b DONE; ES-1 (evals store + experiment-matrix runner + isolation fix) COMPLETE.**
  `evals/runner.py::run_matrix` composes ES-1a's types + store: expand axes cartesian product ×
  `trial_count`, run cells SEQUENTIALLY, aggregate via the three-state `aggregate()`, persist
  per-cell trials + aggregates + experiment under `matrices/<id>/`, append a `results.tsv` row, SEL
  log start/finish. `evals/child.py` is the `python -m personalclaw.evals.child <descriptor.json>`
  entrypoint running ONE cell via the reused `EvalRunner`. **§1.3 isolation:** each cell spawns with
  `os.environ.copy()` + `PERSONALCLAW_WORKSPACE` set on the COPY for the child only — the parent
  gateway env is NEVER mutated (`eval/runner.py` byte-for-byte untouched; a test asserts
  `dict(os.environ)` unchanged across a run). Three-state maps a verifier that couldn't run
  (timeout / non-zero exit / unparseable stdout / budget-EXCEEDED-preflight-so-no-spawn / spawn
  OSError) to `VERIFIER_ABSENT`, never a false `FAILED`; `run_matrix` never raises out.
  `store.py` gained `write/read_matrix_trials`. No user surface → no CHANGELOG. **Operational note:**
  implemented in an isolated worktree after tick-15's health-fix `git checkout` on the shared tree
  displaced it; recovered intact and rebased onto the current #832 (which carries the #831
  FTS5-degrade-test fix). **Remaining EVALUATION-SUBSTRATE:** ES-2+ (RunPin, judge bench, studies,
  the FE tab) — later atoms. **Gates:** `make lint` clean (720 files);
  `tests/test_evals_matrix_runner.py` (21) + `test_evals_store.py` (10) + `test_eval_harness.py` (59
  — proves eval/runner.py unchanged) pass.

---

## Execution log — ES-4 (judge benchmark harness → tier-recommendation table) — §6 COMPLETE

- **ES-4 DONE.** `evals/judge_bench.py` (+ the shipped `evals/benchmarks/judge/starter.json`)
  generalizes `loop/instrument.probe_judge` from one strong/null pair on one model to a matrix over
  **fixtures × judge tier × `judge_samples` 1/3/5 × position**, and publishes the
  tier-recommendation table §6 asks for: agreement-with-known-verdict, strong-vs-null separation,
  position-swap flip rate, cost and wall time per (rubric-class × tier × samples), each row carrying
  the harness's own failure-mode notes.

- **Nothing in the judge vocabulary was re-minted.** `judge_instruction` renders the prompt,
  `parse_judge_json` → `validate_verdict` → `aggregate_samples` decide the cell, so a tier is
  measured on the exact object a live judge gate hands it — including the PASS-needs-proof
  precondition and the strict-majority rule. `judge_calibration.CANARY_MIN_SEPARATION` is imported
  as the separation floor rather than restated (a second threshold would make one judge trustworthy
  to the benchmark and blind to the canary), and agreement is
  `judge_calibration.DivergenceRecord.direction`: a fixture's known verdict IS a human label, so a
  judge disagreeing with it IS a divergence record, and the offline metric and the live one are now
  one function. `expand_cells` moved from `runner.py` (private) to `matrix.py` (public) so BOTH
  matrix consumers cross their axes with one rule; `pinning.compute_pin_for_subject` was extracted
  so a non-scenario subject pins through the same three environment parts without touching the
  append-only ledger header.

- **The load-bearing property is that the axes are CONSUMED**, because a declared axis nothing reads
  produces N identical runs wearing different labels — a fabricated comparison that looks real in
  every artifact. `judge_samples` decides both the call count (recorded on the observation as
  `calls`) and the aggregate verdict; `tier` decides the use case via the engine's own
  `DEFAULT_MODEL_TIERS`, asserted equal to it so a drift there is a failing test rather than a
  silently different measurement. Three falsifications confirmed the cover: ignoring the sample axis
  reds `assert 1 == 3`; dropping the null's score in the separation computation reds
  `assert 4.0 == 0.0`; returning a fixed use case reds `assert 'passed' == 'verifier_absent'`.

- **MEASURED BUG, found by driving the harness rather than by reading it.** The separation metric
  matched a null against *any* strong fixture in the same rubric class. With two pairs in one class
  that compares `conv-null-restate` to `conv-strong-tests` — the wrong difference under the
  right-looking name, and it HID a collapse (a 5.0-vs-1.0 pair masked a 2.0-vs-2.0 one). Fixed by
  carrying the DECLARED `counterpart_id` on `Observation` and matching exactly, within one position
  (a cross-slot comparison would fold positional bias into the separation number). Regression:
  `test_separation_uses_the_DECLARED_counterpart_not_any_strong_in_the_class`. The same pass also
  found the collapse note printed once per slot rather than once per pair, burying every other note.

- **Unmeasured is never adequate — mechanized, not left to the reader.** A class with no strong/null
  pair reports `separation: None` and is INADEQUATE; a class never position-swapped reports
  `flip_rate: None` and is INADEQUATE; a cell whose judge produced no parseable object is
  `VERIFIER_ABSENT` with protocol errors counted separately, never averaged in as a wrong answer;
  and cost is `None` rather than `0.0` when nothing priced the call, so `recommend` returns
  `cost_unknown` instead of ranking an unpriced model cheapest. One missed forbidden-success-mode
  case disqualifies a tier on its own — a disqualifier is a fact, the same rule
  `aggregate_samples` applies.

- **DEVIATION (floors are constants, not config).** The three adequacy floors are module constants
  with the `fanout_measure.INCONCLUSIVE_BAND_POINTS` justification: a floor an operator can lower is
  not a floor, and the one move that makes an inadequate tier presentable is editing the number that
  called it inadequate. `EvalsConfig.judge_agreement_floor` was deliberately NOT reused — its
  documented consumer is ES-5's study verdict over position-swap agreement, a different metric on a
  different subject. Consequence: **no config field was added**, so no `config-baseline.json` or
  `docs/reference/configuration.md` churn.

- **DEVIATION (no child process).** `run_matrix` spawns children to contain exactly one hazard:
  `EvalRunner.run_scenario` mutating `PERSONALCLAW_WORKSPACE` in the calling process (§1.3). A judge
  fixture is a block of text plus a rubric — nothing executes, no workspace is written, and
  `EvalRunner` is never constructed — so this consumer runs in-process while reusing the shared
  spec, expansion, aggregation and artifact sinks. Skipping the spawn is not skipping the rail.

- **Two honest gaps.** (1) The shipped `starter` set is AUTHORED, not harvested from a real user's
  history; a shipped seed cannot be. It carries all three families §6 names (real judged runs,
  deliberately-bad null probes, forbidden-success-mode admissions) across two rubric classes, and
  the growth path is real: a set of the same name under `~/.personalclaw/evals/benchmarks/judge/`
  wins over the packaged one, the `prompt_pack` resolution rule reused deliberately so nothing has
  to be backfilled into the home. (2) Mining past `judge_divergence` ledger events into fixtures is
  NOT built here. `divergences_from_journal` already exists and queueing fixtures from
  `judge_unreliable` verdicts is ES-5's own criterion, so a second miner now would be exactly the
  duplicate this atom's design avoids.

- **What is unexercised without a live provider:** whether a REAL model at a given tier clears the
  floors. Every test stubs the judge at the one named `JudgeCaller` seam, so the arithmetic, the
  axis consumption, the refusals and the artifacts are covered and the model's behaviour is not.
  Cost is read from the guard's attempt audit (`guardrails.audit.read_recent`), which only produces
  numbers on a real call; wall time is a clock and is always real. Determinism: the table is a pure
  function of `observations.json` (asserted byte-identical across renders, including from a
  round-tripped file), and the recorded nondeterminism budget is per-cell `sample_verdicts` — a row
  whose samples disagreed says so in its notes.

- **Surfaces:** `personalclaw judge-bench [--tiers --samples --budget --dry-run --list-sets]` — the
  RUN, with a spend preflight because the full shipped matrix is 180 cells / 540 judge calls; a
  READ-ONLY `GET /api/evals/judge-bench` (deliberately no POST: a click must not start hundreds of
  judge calls, and §6's posture is that the harness recommends and the human rebinds); the
  Judge-tiers panel on the Learning page, rendering `null` as "not measured" and never as `0.00`;
  and a one-click **Bind as default** on the recommended use-case row of Settings → Models, which
  sets the recommended ref as chain position 0.

- **No CHANGELOG entry.** The user-visible surfaces are additive and read-only (a new contributor-run
  CLI command, a new panel, a new GET), no persisted shape moved, and no behaviour changed for
  anyone who never runs the benchmark — this is the "not class-B/S" case. `src/personalclaw/reference/`
  was regenerated in the same commit because the new route joins the generated route index.

- **Gates:** `make lint` clean (black/isort/flake8/mypy — 887 source files, no issues);
  `tests/test_evals_judge_bench.py` (46) + `test_evals_routes.py` (6) + `test_evals_matrix_runner.py`
  + `test_evals_pinning.py` + `test_evals_store.py` + `test_durability_inventory.py` +
  `test_portability.py` + `test_agent_reference.py` all pass; `web` typecheck clean, full
  `npm test` green (3214 tests) after adding the new `api.judgeBench` to three partial api mocks the
  Models/Learning panels' existing suites hold; `npm run build` clean.


## Execution log — ES-5 (pre-registered template A/B studies) — §2 **PARTIAL**, atom stays `todo`

- [2026-08-23][ES-5] **PARTIAL. The instrument is complete, honest and railed; nothing drives it.**
  `evals/studies.py` (1611 lines) implements all of §2 — immutable pre-registration, the `locked/` check
  DSL, a hash-pinned rubric with four-way invalidation, the worker-visible surface plus its leak guard,
  supervisor-side `locked/` execution in the child output workspace, blinded position-swapped
  median-of-3 judging, the agreement floor, and the verdict/persist/evidence/demotion/calibration path.
  Eight of ten `done_when` clauses hold. **Two do not, and one of them is the sentence the criterion opens
  with**, so the atom stays `todo`.

- [2026-08-23][ES-5] 🔴 **UNMET clause 1 — "a flywheel template-diff RUNS a pre-registered study".**
  There is no caller. `run_study` / `register_study` / `decide` / `emit_evidence` /
  `file_demotion_proposal` have **zero production importers**; the only production importer of `studies`
  is the read-only handler, and it imports just `study_index` / `study_view`. There is **no production
  `ArmRunner`** — it is a bare type alias, and `arm_runner` is a required kwarg with no default, unlike
  `caller: JudgeCaller | None = None` which falls back to `live_judge_caller`. So nothing can execute a
  template arm. There is also **no invocation surface**: the sibling precedent is explicit — ES-4's
  equally expensive `run_judge_bench` is invoked from `cli_commands.py:730` with a spend preflight and
  `--dry-run`, and ES-5 has no equivalent. The handler docstring's phrase *"the RUN is a deliberate
  invocation"* names a thing that does not exist.

- [2026-08-23][ES-5] 🔴 **UNMET clause 2 — "over the harvested suite".** No harvested-suite →
  `StudyCase` builder exists; the word `harvested` appears in `studies.py` only inside a comment
  (line 116).

- [2026-08-23][ES-5] **The drive deliberately did NOT bolt on a CLI command, and that was the right call.**
  Without an `ArmRunner` a command could not run anything, and inventing an arm-execution seam over the
  WF2 engine is a separate coherent scope. Everything the table below marks "(mechanism)" is exercised
  only through injected seams. **Remaining scope for the follow-on session:** a production `ArmRunner`, a
  harvested-suite case builder, a `register_study` hook on the flywheel's `file_template_diff`
  (`learning/refiner_tools.py:73`), and a CLI command modelled on `_judge_bench`.

- [2026-08-23][ES-5] **All four anti-cheat clauses carry a negative assertion AND a vacuity floor** —
  which is the part of §2 that would otherwise ship inert. The `locked/`-leak guard is the best-guarded:
  `_assert_absent` **raises if the token set is empty or the scan set is empty** (an empty negative
  assertion is not an assertion), two tests prove those refusals, a third plants a locked phrase in a
  template body and requires red, `MIN_LEAK_TOKEN_LEN = 4` refuses tokens too short to detect, and
  `test_a_new_worker_payload_text_field_is_scanned_by_DEFAULT` makes a future field scanned by default.
  Rubric pinning is checked in **both** directions (live rubric moved, and the study's own pinned copy
  tampered), decided on the hash never an mtime, with a floor proving a no-op touch does **not**
  invalidate. The position swap exchanges outputs for real and randomizes slot-A per (study, case,
  trial), floored by *a position-biased judge produces NO winner*. The agreement floor is floored by *a
  consistent judge clears it*, treats an unmeasurable rate as below every floor (`None` ≠ `0.0`), and
  excludes `cannot_judge` from the denominator.
- [2026-08-23][ES-5] **A config field that had no consumer now has one.** `judge_agreement_floor` was
  documented and read nowhere (`judge_bench.py:145`); §2's floor makes it live.

- [2026-08-23][ES-5] **Two shared-contract misses surfaced only in the FULL suite, not the targeted run.**
  `studies_unreadable` / `study_absent` were emitted with no `HTTP_ERROR_CODES` row, and the two new
  routes made the offline agent reference stale (753 → 755 agent-callable of 759). Both are the
  wire-code/generated-artifact contracts that a feature-scoped test set cannot see. The reference
  regeneration was verified to resolve **into the worktree**, with the main checkout's `reference/` at 0
  dirty files before and after — the known "a generator run from a worktree writes the main checkout"
  hazard did not fire.

- [2026-08-23][ES-5] **`docs/design/consistency-audit.json` is a generated REPORTER, not a ratchet.**
  `consistencyAudit.test.ts` rewrites it every run and never fails on drift. The commit was carrying a
  self-contradicting copy (it still recorded `raw-button: 1` for `StudiesPanel.tsx`, generated before the
  button fix). Regenerated; its honest delta is `filesScanned: 549 → 550`. The real ratchets
  (`primitiveAdoption.baseline.json` at `rawButton: 265`, `tokenLint.allowlist.json`) are **untouched**,
  and `web/src/design/` is entirely absent from the diff — `StudiesPanel.tsx` is held to the strict
  token standard rather than allowlisted.

| `done_when` clause | Status |
|---|---|
| k=5 paired old-vs-new | MET (mechanism) — `DEFAULT_K = 5` from `evals.study_default_k`, not a literal |
| over the harvested suite | 🔴 **NOT MET** — no harvested → `StudyCase` builder |
| immutable `registration.json` | MET — second write refused, read-only on disk, `locked/` 0600 in 0700 |
| blinded median-of-3 position-swapped | MET |
| agreement floor + `judge_unreliable` | MET |
| `locked/` supervisor-side in the child workspace | MET — escaping path and unrunnable command both yield `verifier_absent`, never a silent pass |
| verdict + agreement + artifacts on the Learning page | MET — 11 FE tests, incl. "never renders the locked checks or the rubric text" |
| pass → evidence unit + `results.tsv` row | MET (mechanism) — a refused ledger pin is reported, not hidden |
| fail → demotion/revert proposal | MET (mechanism) |
| **a flywheel template-diff RUNS it** | 🔴 **NOT MET** — no caller, no `ArmRunner`, no CLI |
## Execution log — harvested regression suite (the `EXT:LEARN-R2` primitive ES-5 and ES-7 §3.3 both bottomed out on)

- [2026-08-24][harvest] **Built the run→scenario harvest path.** Not an atom of its own: it is the
  primitive both `ES-5` ("k=5 paired old-vs-new **over the harvested suite**") and `ES-7` §3.3 ("replays
  **consulted runs**") landed PARTIAL for want of. Confirmed absent on `origin/main` before building:
  `git grep -ciE 'harvest' -- src/` returns **one file, one hit** — a *comment* at
  `evals/judge_bench.py:102`; `from_run|from_ledger|run_to_scenario` returns nothing; **`StudyCase` does
  not exist anywhere** in `src/`, `tests/` or `docs/`; and nothing outside `evals/` has ever written a
  scenario. `learning/` does read the ledger (`mining.py:211`, `refiner_tools.py:53`, `run_end.py:259`)
  but only for evidence clustering, never to produce a scenario.

- [2026-08-24][harvest] 🔴 **`run_started` and `run_finished` are NOT in `LEDGER_KINDS`, so the real reader
  structurally cannot see a run's inputs.** Verified by executing it, not by reading it — and re-verified
  independently at integration: both symbols are exported from `personalclaw.ledger` yet
  `RUN_STARTED in LEDGER_KINDS` is `False`. `LedgerWriter.write` mirrors into `events.jsonl` only
  `if kind in LEDGER_KINDS`, so `reader.read_events` returns `[]` for **the only record that carries
  `inputs`** — and that empty list is indistinguishable from *"the run took no inputs"*. A harvester built
  on `read_events` alone would have silently produced input-free cases forever. Resolved by **extending**
  the real reader (`read_journal()` + `journal_only_kinds()`, bound engine-side as
  `journal.journal_records()`) rather than adding a second reader.

  | What | Kind | File | Field |
  |---|---|---|---|
  | Inputs | `run_started` | `journal.jsonl` **only** | `inputs`, `spec_version`, `resumed` |
  | Final status | `run_finished` | `journal.jsonl` **only** | `status`, `elapsed_secs` |
  | Outputs | `step_completed` | `events.jsonl` | `output_ref` (pointer; body spilled), `resolved_prompt_ref` |
  | Failure | `step_failed` | `events.jsonl` | `failure`, `failure_signature` |
  | Skill/template loaded | `consulted` | `events.jsonl` | `ref` — the ES-7 §3.3 join key |

- [2026-08-24][harvest] 🔴 **`redact_credentials` is NOT idempotent over a composed `key: value` line, and
  the second pass DESTROYS the field name.** This is a live property of the shared helper, not a harvest
  bug. Reproduced independently at integration:
  `redact_credentials("api_key: [REDACTED: credential]")` → **`"[REDACTED: credential] credential]"`**.
  It *is* idempotent on its own direct output (a raw secret collapses to `[REDACTED: credential]` whole),
  which is why the hazard hides: it bites only a caller that screens values individually and then composes
  `key: value` strings and screens again. The first design here used exactly the single trailing
  whole-scenario chokepoint that looks safest, and it corrupted every case whose inputs held a credential.
  Restructured to screen each source **exactly once at entry**, with the non-idempotence pinned in a test
  so nobody "simplifies" it back. **Any other caller composing `key: value` from already-screened values
  will hit this.**
- [2026-08-24][harvest] **`WorkflowRun.inputs` (the SQLite column) is written straight from the API request
  and is never redacted**; `run_started.inputs` is the same dict *after* the writer's `redact()`. The
  harvest reads only the ledger, and a run with **no `run_started` is refused** rather than harvested off
  the row — that refusal is what makes "a harvested case cannot contain a credential" a property of the
  read path rather than a hope.

- [2026-08-24][harvest] **An empty population is a refusal; harvesting zero from real runs is a
  measurement.** `HarvestReport.is_refusal` is `considered == 0`, deliberately **not** `population == 0`:
  *"no replay population: the Run Ledger holds no harvestable terminal run, which is not the same as a
  harvested suite of zero cases"*, and the CLI **exits 1**. A harvest that looked at runs and kept none
  exits **0** with per-reason counts. `load_harvested_suite()` **raises** `EmptyHarvestError` rather than
  returning `[]`. Re-falsified at integration: changing the predicate to `len(self.cases) == 0` reds two
  tests including the CLI exit-code assertion — the distinction is load-bearing, and a zero-case suite
  scored against a threshold would otherwise read as a pass.

- [2026-08-24][harvest] **Redaction proof with two vacuity floors, not one.** The credential is planted
  **raw** via `store.append_jsonl`, bypassing `Journal.write` — planting through `run_started` would let the
  *writer* strip it and leave the screen unexercised (mechanism-not-use). Floor 1 stubs `redact` to identity
  and requires the credential to **appear**. Floor 2 proves the planted shape is one `redact_credentials`
  actually matches, and pins that `password=hunter2` passes through **untouched** — so the suite cannot pass
  by planting a string nothing was going to strip.

- ⚠️ **Two pre-existing hazards that make "the harvested suite is green" meaningless unless the judge is
  on.** `personalclaw eval --all` globs the whole installed dir, so harvested cases are picked up by the
  existing runner — desirable, but it means a zero-turn case would be *run* and *pass* while asserting
  nothing (hence the "always ≥1 turn" invariant and its falsification). And `Assertion.check` returns
  `True` for `AssertionType.JUDGE` when `--judge` is off, so a harvested case — like the shipped
  `context_accumulation` — passes **vacuously**. Neither is this work's to fix.

- **NOT done, deliberately: LEARN-R2's promotion discipline.** *"a previously-failing task that now passes
  is re-run, and only if it passes again is it promoted"*
  (`docs/research/learnings/verification-and-judging.md:96`). This harvest admits any terminal run. That
  gate needs a re-run capability, which is `ArmRunner`'s job on the ES-5 branch, so building it here would
  have meant guessing at that branch's interface.
- **What each consumer still needs.** **ES-5:** a `StudyCase` adapter (trivial — `load_harvested_suite()` →
  `[(name, sha256)]`); the `low_power` label, whose threshold the plan **never gives** (*"`low_power` at <N
  cases"* — owner decision), noting ES-5 must catch `EmptyHarvestError` to emit an honestly-labelled
  low-power study rather than crash; and the `ArmRunner`. The hard constraint is already satisfied:
  `store.append_result` refuses a row without a complete `RunPin`, and a harvested case **is** a library
  scenario with a named `fixture_home`, so it pins exactly like an authored one. **ES-7 §3.3:** a
  `workflow_name`-style filter over `consulted_refs`; and a per-skill `MatrixSpec.subject` form —
  `subject`'s own comment enumerates `template id | retrieval-arm set | judge fixture set | use-case`, and
  **"skill" is not in that list**, so that is an unmade design decision, not a missing function.
  `resolved_prompt_ref` is recorded per harvested output precisely because reconstructing a prompt from
  today's template plus `inputs` would replay the very variable an A/B is trying to hold still.
- **The `EXT:` dep was worse than "satisfied on paper".** LEARN-R2's harvested suite was never
  *specified* — its total vocabulary across the plan set is *"a small set of past successful runs per
  template"* and *"grows organically from previously-failing-now-passing runs"*, referenced by six
  consumers. There was no contract to under-deliver against.
## Execution log — ES-7 (ablation runner + skills bench + model-upgrade watchdog) — §3 **PARTIAL**, atom stays `todo`

- [2026-08-23][ES-7] **PARTIAL.** Eight of nine `done_when` clauses hold and are railed; §3.3's *"replays
  consulted runs"* is met in **population and provenance but not in inputs**, so the atom stays `todo`.
  Ships `evals/overlay.py`, `evals/ablation.py`, `evals/model_watchdog.py`, `evals/skills_bench.py`,
  `skills/suppression.py`, the `arm_mask` plumbing through `evals/runner.py` → `evals/child.py`, a real
  maintenance-tick call site (`durability/service.py:857`, inside the periodic loop, gated on
  `evals.enabled` — not on `auto_backup`), a `personalclaw ablation` CLI, and `GET /api/evals/ablation`.

- [2026-08-23][ES-7] 🔴 **A config-flag target that does not exist produced a FABRICATED `remove`
  recommendation.** `_patch_child_config` writes any dotted key into the cell's `config.json`;
  `AppConfig.load()` then **drops** an unrecognized key during normalization. The arm therefore ran with
  the component fully ON, scored identically to the baseline, and would be reported as a no-delta
  `remove` — a fabricated retirement filed as *ablation-grade* evidence, which is the strongest evidence
  class the retirement path accepts. The fix had to be a **refusal in both processes**
  (`overlay.config_field_exists()` at the parent's `validate_component` before paying for the matrix, and
  again at the child's `apply_in_child` so a hand-built overlay cannot bypass it). The secondary hazard
  was probed and does not exist: `config_field_exists`' own `AppConfig.load()` does not cache and cannot
  poison the later patch (no caching in `config/loader.py`; confirmed empirically).

- [2026-08-23][ES-7] **Config normalization looked like a live-state mutation, and the guard accused
  itself.** `AppConfig.load()` rewrites `config.json` in full (defaults + a `meta.lastTouchedAt` stamp) on
  its first load of an un-normalized file, and the run pin loads config *inside* the guarded block — so on
  a fresh or hand-edited home the very first ablation reported the one thing §3.1 forbids.
  `_normalize_config_before_snapshot()` (`ablation.py:280`) runs first, with a vacuity floor asserting the
  normalization actually happened.

- [2026-08-23][ES-7] **"live spec/config never mutated" is railed as a byte-identical negative that also
  covers the RAISING run** — the case the obvious implementation gets wrong by restoring on the success
  path and leaking on the exception path. `live_state_unchanged()` sha256s `config.json`,
  `active_models.json`, every `use_case_settings/*.json` and the component's declared spec refs, compares
  in a **`finally`**, and when the body both raised and drifted raises `LiveStateMutatedError` with the
  body's error as `__cause__`; `run_ablation` writes **no report** on a leaked run. Falsified at
  integration: making the drift check success-path-only (`drift = [] if failure is not None else …`) reds
  `test_live_state_guard_still_checks_after_a_run_that_raised`. Scope stated honestly: the guard's default
  set is the write surfaces the overlay can actually reach — template specs enter only via
  `component.live_refs`, and the overlay has no code path that writes a spec file at all.

- [2026-08-23][ES-7] **"exactly ONE digest" is asserted as a COUNT, which is the assertion that matters.**
  `len(notifier.calls) == 1` for a **three**-binding rebind, with all three use cases present in that one
  body. A `>= 1` assertion would have waved through the mutation that wraps the notifier in
  `for _change in result.changes:` — falsified, `assert 3 == 1`. Floors both ways: a no-change tick emits
  `[]`, a first observation records a baseline and no digest, and three later ticks still yield exactly 1.
  The fingerprint is computed through `RunPin.model_fp()` so it equals the ledger's own `model_fp` column
  rather than a second hash of the same facts; mtime is a pre-filter only, so a rewrite that moved a
  *fallback* is `file_touched_no_rebind`, not a rebind.

- [2026-08-23][ES-7] **§3.3 PARTIAL — surfaced-vs-suppressed is verified, "replays consulted runs" is
  not literal.** `verify_suppression` assembles both arms through the real `allocate_skills` and requires
  the body **present in one and absent in the other** — the presence half is what stops an empty prompt
  satisfying the negative trivially — and an unverified suppression refuses before spending a run.
  Suppression bites at the single choke point `SkillsLoader.load_skill`. **The gap:** `consulted_runs()`
  reads the WF2-R13 `consulted` ledger event and uses it to *gate and attribute* the bench, but the
  artifact replayed is a scenario-library `subject`, not the consulted runs' own inputs. No run→scenario
  harvest path exists anywhere in `evals/` — harvested suites belong to ES-5, which is itself PARTIAL for
  the mirror-image reason.

- [2026-08-24][ES-7 §3.3] **The `consulted_refs` filter — the gap above is now closed in INPUTS. Atom
  still `todo`** (§3.3's plural replay and the `MatrixSpec.subject` decision remain, below).
  `harvest.installed_harvested_cases`/`load_harvested_suite` gained a `consulted_ref=` scope in the same
  shape as the existing `workflow_name=` (keyword-only, `""` = no filter), and `skills_bench.bench_skill`
  now DERIVES its subject from it: with no `--subject`, the replayed artifact is the newest harvested case
  whose own run consulted the skill. `subject_origin` (`harvested`|`operator`), `subject_run_id` and
  `subject_candidates` ride on the report so a bench cannot claim a replay it did not do.

- [2026-08-24][ES-7 §3.3] 🔴 **`consulted_refs` was written by `harvest.py:360` and read by NOTHING** —
  a populated, untested, inert field. `git grep consulted_refs -- src/ tests/` returned exactly ONE hit
  before this change. The harvest drive recorded it as *"the ES-7 §3.3 join key"* and then nothing joined
  on it, which is the shape where a filter is later built over a field nobody ever verified is populated.
  `test_consulted_refs_records_what_the_run_actually_loaded` now pins both the populated case and the
  `[]` case (a run that consulted nothing records the ABSENCE, so the scope can tell "loaded nothing"
  from "never looked").

- [2026-08-24][ES-7 §3.3] **ONE matcher, deliberately — the `foo`/`foo-bar` predicate was about to be
  duplicated.** The live event scan (`consulted_runs`) and the frozen-ref scope read the SAME
  `consulted` `ref` from two places, so `skills_bench._ref_names_skill` was PROMOTED to
  `harvest.ref_names_skill` and the private copy DELETED, not left in place. Two predicates would have
  disagreed about `foo` vs `foo-bar` and one skill's bench would have replayed another skill's runs under
  the wrong name. Railed by identity (`skills_bench.harvest.ref_names_skill is harvest.ref_names_skill`)
  plus `not hasattr(skills_bench, "_ref_names_skill")`; re-introducing a private matcher reds both suites.

- [2026-08-24][ES-7 §3.3] **`--subject` was a defaulted field, i.e. an unsupplied input, i.e. a dead
  bench.** `cli.py`'s `--subject` defaults to `""` and `bench_skill` refused outright on it, so the
  invocation a user actually types (`personalclaw ablation --skill <name>`) could never score anything.
  The filter is what makes that default path work; `--subject` is now an override, and `--dry-run`
  resolves the subject the SAME way the scored path does so the preflight cannot print `<none>` for an
  invocation that would have found one. Asserted through the real dispatch
  (`cli._ablation is cli_commands._ablation`), not through `bench_skill` directly.

- [2026-08-24][ES-7 §3.3] **Falsified both directions plus the rail.** A filter that matches nothing and
  one that matches everything both pass "no crash" and "returns a list", so the guard was mutated three
  ways on the LIVE line and grepped back each time: `if consulted_ref and False` (includes everything) →
  **5 red** across both suites including `['alpha','beta','gamma'] == ['alpha']` and the CLI call site;
  `if consulted_ref:` (excludes everything) → **4 red** including `'' == 'harvested_release_triage_…'`;
  re-adding a private `_ref_names_skill` → **2 red**. Every scope assertion is paired with an unfiltered
  vacuity floor (`len(load_harvested_suite()) == 3`) so a scope that emptied because nothing was
  harvested cannot read as a working filter.

- [2026-08-24][ES-7 §3.3] ⚠️ **Test-isolation hazard, measured:** adding
  `monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)` to `bench_home` — on top
  of `$PERSONALCLAW_HOME`, the pattern `test_evals_harvest.py`'s fixture uses — **leaked the run store
  between tests in one process**: the six tests that reuse the fixed run id `run-a` failed on
  `UNIQUE constraint failed: runs.id`, and the file's runtime went 5s → 57s. Removing the second patch
  restored 19/19. `$PERSONALCLAW_HOME` alone is what redirects an import-bound store (conftest's
  real-home rail already re-points every binding of that function object), and the fixture now ASSERTS
  the redirect for both `scenarios.installed_dir()` and `workflows.store._db_path()` rather than
  patching harder. Mechanism not pinned; the A/B is reproducible. **`test_evals_harvest.py` still has
  the double patch and passes only because its runs use generated ids — the same leak there would be
  invisible.**

- **Still open on §3.3, deliberately NOT settled here.** The clause says *"replays consulted runs"*
  (plural) and `MatrixSpec` carries exactly ONE `subject`, so this scores the newest case and REPORTS
  `subject_candidates` it did not score. Scoring the whole population needs the per-skill
  `MatrixSpec.subject` form the harvest drive already flagged as an unmade design decision — `subject`'s
  own comment enumerates `template id | retrieval-arm set | judge fixture set | use-case` and "skill" is
  not in that list. **Owner call, not this drive's.**

- [2026-08-23][ES-7] **Plan/code drift found and recorded rather than implemented: §3.2 names four
  watched bindings, but `eval_judge` is not in `providers.use_cases.VALID_USE_CASES`**, so
  `active_models.json` cannot hold it and watching it would have been a control reporting "no change"
  forever. The code watches `("chat", "reasoning", "background")` and keeps `PLAN_WATCHED_USE_CASES` as
  the record of the discrepancy. **The plan text should be corrected.**

- ⚠️ ~~**One inert surface deliberately left open and flagged.**~~ **CLOSED 2026-08-25.**
  `GET /api/evals/ablation` shipped with **no frontend consumer**, unlike ES-4's sibling
  `/api/evals/judge-bench` which renders as `JudgeBenchPanel` on the Learning page. The route was
  registered, tested and in `routes.md`, but nothing read it. FE was not in ES-7's `done_when` and the
  drive could not gate a web change from its worktree, so it was reported rather than half-shipped.
  It is now consumed: `web/src/pages/learning/AblationPanel.tsx` + an `api.ablation()` client, rendered
  from `LearningPage.tsx` beside `RetrievalBenchPanel`. **This does NOT flip ES-7** — the FE was never
  in its `done_when`; the row it closes is this note.

  Two findings from closing it, both worth carrying:

  1. **The isolation-test trap the original note describes is reproducible one level up.** A suite that
     only mounts `AblationPanel` stays fully green with the `<AblationPanel …>` render deleted from
     `LearningPage` — measured, not assumed: with the render removed, 10 of 11 cases passed and only the
     call-site rail went red. So the load-bearing test is `is rendered BY LearningPage, and the api
     client is actually called`, which asserts both halves (the request fires AND the payload paints) and
     carries a vacuity floor proving the heading query is not satisfiable by an empty tree. Mirroring
     `JudgeBenchPanel.test.tsx`'s shape alone would have shipped the same inert-control gap as a green
     suite.
  2. **The panel honours all THREE of the handler's codes, not two.** `api_evals_ablation` deliberately
     mints `evals_disabled` / `ablation_absent` / `ablation_unreadable` because they send a reader to
     three different places (the switch, the registry, a bug). `JudgeBenchPanel` collapses `evals_disabled`
     into its generic `LoadError`; this panel does not, since "no ablation has run yet" is the state a user
     occupies for **months** (monthly cadence, registry starts empty) and is therefore precisely the state
     that must not read as a failure — nor a failure as it.

  One forced touch outside the change's natural surface: `proposalCache.test.tsx`'s `vi.mock` of
  `lib/api` enumerates every read `LearningPage` makes, so adding a seventh made 5 of its 9 cases throw
  inside a passive effect. Its own comment already documents that obligation; `ablation` was added as the
  fifth entry in the same form.

- [2026-08-23][ES-7] **A died-mid-flight commit was RED four ways while its tree was clean** — worth
  remembering as a diagnostic: a clean `git status` is not evidence of a green commit. 10 of 38 ablation
  tests failed because the shared fixture targeted `workflows.judge_enabled`, **a config field that does
  not exist**, so the drive's own new validation refused it and every test asserted the refusal instead
  of the behaviour; `make lint` failed on mypy at `cli_commands.py:790`; `ablation_absent` /
  `ablation_unreadable` were emitted with no `HTTP_ERROR_CODES` row; and `reference/routes.md` had been
  **hand-edited** rather than generated, with `index.md`'s counts never bumped. The fixture was retargeted
  to real fields and the reference regenerated via `python -m personalclaw.manifest_reference`, verified
  to write the worktree and not the main checkout.
- [2026-08-23][ES-7] **Minor over-claim corrected in-code:** `overlay.py`'s docstring said *"No code path
  here can reach the live home."* The refusal in `_cell_home()` is narrower — it catches an absent
  `PERSONALCLAW_HOME` or one resolving to the **default** `~/.personalclaw`, not an arbitrary non-default
  real home. The actual guarantee comes from `_spawn_cell` always handing the child a
  `TemporaryDirectory`; the refusal is a secondary rail.


## Execution log — ES-5 executor (§2 clause 1: a flywheel template-diff runs a study) — clause now **PARTIAL→driven**, atom stays `todo`

- [2026-08-24][ES-5] **The instrument now runs end to end, but auto-run did NOT ship — clause 1 is
  register-automatically, run-on-invocation.** New `evals/study_arms.py` (693 lines): `TemplateArmRunner` /
  `live_arm_runner` (the production `ArmRunner`, one `one_shot_completion` per arm on the arm's rendered
  template prompt, in a per-(case,trial,arm) workspace whose `output.md` the `locked/` checks read);
  `harvested_study_cases()` (the `StudyCase` adapter over `harvest.load_harvested_suite()`);
  `render_arm_prompt()` (binds via the engine's own `workflows.bindings.resolve` — no second dialect);
  `arm_bodies_for_ops()` (OLD/NEW via `mutations.apply_batch`, the human-accept path, **writing nothing**);
  `assert_arms_differ()` (identical arms refused — that case reports a confident `tie`, not a failure);
  and `preflight()`/`StudyPreflight` (`cases×k×2` arm + `cases×k×2×samples` judge calls). Call sites:
  `learning/refiner_tools.py:127` (`_preregister_study` — a filed template diff returns a `study_id` and
  the registration lands on disk), `studies.py:1450` (`arm_runner=None` → `live_arm_runner`), and
  `cli_commands.py:_study` ← `cli.py "study"` (`--list`/`--view`/`--run`/`--dry-run`).

- [2026-08-24][ES-5] 🔴 **DESIGN DECISION, flagged for owner reversal — register-at-file, run-on-invocation
  (not auto-run).** A 3-case suite at k=5 is **30 arm + 90 judge** model calls; an agent tool call that
  silently starts that is exactly what the preflight exists to prevent, and auto-run is Autonomy-Guardrails'
  spend territory. So the flywheel diff **pre-registers** automatically and the **run** is a deliberate
  `personalclaw study --run`. Everything from registration to verdict is real and connected — but
  *"a flywheel template-diff RUNS a study"* with no human in the loop is **not** what shipped, so clause 1
  is recorded PARTIAL rather than claimed met. **Owner call: ratify this seam, or wire auto-run behind a
  Guardrails spend gate.**

- [2026-08-24][ES-5] **An arm is ONE model call on the rendered prompt, not a full engine run — and that is
  a hard bound, not a shortcut.** `workflows.service.start_run` refuses without a `supervisor`, and
  `ActionServices.workflows` is set only at `gateway.py:2496` from the workflow watchdog — so an
  engine-driven arm would be permanently **inert** in the CLI, i.e. the exact defect being closed. It is
  also what a refiner diff actually changes: `check_diff` freezes `id`/`triggers`/surfacing metadata, so
  legal ops move prompt text. A trajectory-level arm belongs with whoever builds the headless engine driver.

- [2026-08-24][ES-5] 🔴 **Two bugs the live drive found that reading would not have.** (1) **The CLI was
  inert** — nothing registers a workflow-def provider outside gateway boot, so `--run` refused every study
  with `no workflow definition named …` while the def sat on disk; fixed by `_live_spec` calling
  `register_native_provider()` + `register_bundled_provider()` (idempotent). (2) **`ArmOutput.ok` was an
  unread field** — `run_study` never consulted it, and this runner is the first thing that ever sets it
  `False`; left unread, a provider outage would hand the judge `""` and **the other arm would win on
  infrastructure failure**. Fixed: an unfinished arm yields an unjudgeable pair and the judge is **not
  called at all**. Re-falsified at integration: forcing `unfinished = []` reds
  `test_an_unfinished_arm_is_NOT_judged_as_an_empty_answer` with *"the judge was asked about an arm that
  never ran"* (1 failed, 21 passed).

- [2026-08-24][ES-5] **Owner decision A — the low-power threshold N.** `LOW_POWER_CASES = 3` **already
  existed** on main (`studies.py:119`) and is **arbitrary** — not from the plan or a power calculation. It
  is a named module constant (not a config field) and a **label only**, never a verdict change. This work
  surfaces it in the preflight so a user sees it before spending. **Ratify 3, or promote it to
  `evals.study_low_power_cases` (full round-trip).**

- [2026-08-24][ES-5] **Owner decision B — LEARN-R2 promotion discipline NOT implemented; it needs a design
  call, not code.** The gate *"a previously-failing task that now passes is re-run, and only if it passes
  again is it promoted"* needs a predicate for "passes". A harvested case's only assertion is a JUDGE
  assertion, and `Assertion.check` returns `True` for `AssertionType.JUDGE` when `--judge` is off — so the
  predicate is **`True` by construction** in the default path, and the gate would sit on a vacuous check.
  This runner produces text for a *comparative* judge, not pass/fail against a case's own assertions. Owner
  must pick: **(a)** couple the promotion gate to `--judge`-on re-runs (real spend inside the harvest), or
  **(b)** give harvested cases a non-judge assertion from the recorded baseline — which the harvest
  **deliberately refuses**, because turning one observed answer into a `contains` assertion mints a golden
  nobody reviewed.

- [2026-08-24][ES-5] **New finding, not mine to fix:** `mutations.apply_batch` silently accepts a malformed
  `update_node` with `fields: {"config": {...}}` and produces a spec with **`config.config`** nested,
  `issues == []`. A refiner emitting that shape would get a spec nobody authored, with no error.

- **Brief corrections.** (1) The plan *does* name a `low_power` threshold as a shipped constant (`= 3`), so
  "the plan never gives N — implement the label" was wrong; the label existed, the pre-spend surfacing did
  not. (2) `load_harvested_suite()` returns `list[dict]` of full library-shaped scenarios, not
  `[(name, sha256)]`; the sha is computed with `scenarios.sha256_of_scenario_data`. (3) A production
  `ArmRunner` driving the WF2 engine is **not buildable in-session** (see the supervisor bound above).

- **Gate:** `make lint` 0 (mypy 993 files); `test_evals_study_arms` 21 + a 158-test targeted set; probe
  residue 0. A new `one_shot_completion` call site required a `_CALL_SITE_SURFACES` row
  (`test_resilience_degraded_lint`), added as `"evals/study_arms.py": "assistant_reasoning"`. Every negative
  assertion carries a paired positive floor; one floor initially failed for a *good* reason — a slot-A-only
  judge correctly became `no_signal` under the position swap — and was rewritten to vote on content.

## Execution log — ES-3 (retrieval eval harness, per-arm P@k/R@k, both stores) — §5 **PARTIAL**, atom stays `todo`

- [2026-08-25][ES-3] **PARTIAL.** Six of the criterion's seven clauses hold and are railed; the seventh
  ("mined from surfacing/volunteer events") is met on the MEMORY side and met by a **substitute** on the
  knowledge side, because the source the plan names does not exist. Ships
  `evals/retrieval_bench.py`, an `arms=` mask on `knowledge.retrieval.HybridRetriever.search`, a new
  `vector_memory.VectorMemoryStore.rank_semantic` extracted out of `get_semantic_context`,
  `MemoryGraph.volunteer_qrels`, a `personalclaw retrieval-eval` CLI, `GET /api/evals/retrieval`,
  `GET /api/evals/retrieval/card`, `POST /api/evals/retrieval/labels`, and a `RetrievalBenchPanel` on the
  Learning page carrying §5.2's hand-label card.

- [2026-08-25][ES-3] **The subject slot was designed, not invented.** `MatrixSpec.subject`'s own comment
  already enumerates "retrieval-arm set" and `scorer` already enumerates `"qrels"` — so this is a declared
  slot being filled. What it could NOT reuse is `runner.run_matrix`: that body resolves
  `spec.subject` through `scenarios.resolve_scenario_path` and spawns a child per cell, which a subject
  that is a benchmark rather than a scenario cannot satisfy. So the harness is a §1.2 *consumer* the way
  ES-4's judge bench is — shared `expand_cells`/`aggregate`/`aggregate_by`, the same `matrices/<id>/` sinks,
  the same `store.append_result` + `pinning.compute_pin_for_subject` — and spawns nothing, because
  retrieval is deterministic, local and read-only.

- [2026-08-25][ES-3] 🔴 **DEVIATION: `surfacing_events` has no writer, so §5.2's source (a) is half
  unavailable.** `learning/measure.per_arm_precision` is pure and takes events; nothing in the tree writes
  the table it reads, which `dashboard/handlers/learning.py:351` already records in prose ("*a
  `surfacing_events` table that nothing writes yet*"). The memory half of source (a) is real and is used
  (`mem_volunteer_events` → `volunteer_qrels`). For KNOWLEDGE the substitute is `intent_outcomes`
  (`intent_name` = the standing intent's goal, i.e. a query; `item_id` = a positive), which has a live
  writer in `knowledge/pipeline/runner.py` and is **not circular**: the intent stage runs at ingest over an
  item's consolidated content and never consults the retriever.

- [2026-08-25][ES-3] 🔴 **DEVIATION: §5.2's source (c) — synthetic entity queries from the alias table —
  was deliberately NOT built.** Both stores' graph arms take entity mentions as their INPUT
  (`knowledge.retrieval._graph_search` walks `entities`→`mentions`; `MemoryGraph.recall_refs` walks
  `mem_links`). A qrels set generated from mentions would score the graph arm against its own index and
  manufacture the exact "+P@5 from the graph arm" headline the report exists to TEST. That is ES-7's
  fabricated-`remove` failure with the sign flipped, and it would be filed as ablation-grade evidence.
  Declining it is recorded rather than silently skipped.

- [2026-08-25][ES-3] **The metric contract is the whole atom, and it is asymmetric on purpose.** `P@k` over
  an empty candidate set is `0/0` — `None`, with `REASON_NO_CANDIDATES`, and the cell is `VERIFIER_ABSENT`
  so `matrix.aggregate` reports `mean_score=None` instead of averaging a zero the retriever never earned.
  `R@k` over an empty candidate set is a real `0.0` (it found none of the known answers) and `None` only
  when the qrels name no relevant id at all. Both absences are counted in their own published columns
  (`no_candidate_queries`, `undefined_recall_queries`), so "no candidates" and "0 relevant" are never
  spelled the same way. Falsification: returning `0.0` for the empty case reds six tests, including
  `assert 'failed' == 'verifier_absent'` at the `aggregate` call site.

- [2026-08-25][ES-3] **The harness falsifies its own mask, per run.** Every run includes an all-arms-off
  CONTROL cell that must retrieve nothing; a control that came back with hits means the mask never reached
  the retriever and every per-arm delta is noise, so the run raises `MaskNotAppliedError` instead of
  publishing. Confirmed by making `HybridRetriever.search` ignore `arms`: the three end-to-end runs raise,
  not merely a unit assertion. A masked arm's *input* is never computed either (no FTS query, no traversal,
  no embedding call), so a cell measures the arm's ABSENCE rather than its exclusion from fusion.

- [2026-08-25][ES-3] 🔴 **A declared arm with no executor scores exactly like its own absence.** Built
  bare, `HybridRetriever(store)` has no embedder, so the vector arm returns nothing, its leave-one-out
  delta is 0.0, and the report would have said "vector is worthless" with confidence. Fixed on both sides:
  the harness binds the same embedder production binds, and `arm_executors()` is read off the objects that
  actually ran, with `arm_verdict(..., has_executor=False)` forcing `unmeasured` BEFORE it looks at the
  delta. Falsification: dropping that short-circuit turns a dead arm into `enable`.

- [2026-08-25][ES-3] 🔴 **A REAL bug the drive-it-yourself pass found, in the shared `matrices/` sink.**
  ES-4's `list_bench_runs` claimed every `matrices/<id>/` that had a `table.json` — a working proxy for "is
  a judge bench" only while ES-4 was the only writer of one. ES-3 is a second writer, so the newest
  retrieval run was served AS the newest judge bench and `JudgeBenchPanel` read `row.wall_secs` off a P@k
  row: `Cannot read properties of undefined (reading 'toFixed')`, the whole Learning page in its error
  boundary. Found in a browser, and only after removing my own panel and still reproducing it. The fix is
  an explicit stamp in the ARTIFACT (`judge_bench.TABLE_KIND` / `retrieval_bench.LEDGER_KIND`), **not** a
  run-id prefix — the id is caller-supplied (the judge bench's own suite passes `bench_id="bench-test"`), so
  a prefix rule stranded ES-4's own runs, which is the same bug with the roles swapped. An unstamped table
  now belongs to neither consumer, and pre-stamp runs in an existing home read as "not measured yet".

- [2026-08-25][ES-3] **DEVIATION (two existing rails re-scoped, deliberately).**
  `test_evals_routes.py`'s two "no way to START a run" rails asserted `{GET, HEAD}` over the WHOLE
  `/api/evals/*` verb set. §5.2's hand-label card needs one write — one JSON file under `evals/`, no model
  call, no store write — so the rails were narrowed to their stated intent: an enumerated
  `_ALLOWED_WRITES`, plus a per-route assertion that no `_RUN_ROUTES` entry accepts a mutating verb. That
  is stronger than the verb count it replaced, which would not have noticed a POST added to a run route
  once any other write existed.
  `test_wire_error_envelope_census.py`'s `UNRESOLVED_PAYLOAD_CEILING` went 202 → **204** for two
  200-status success bodies (`json_response(view)` / `json_response(card)`, the same shape the three reads
  already on that surface use). Spelling their keys at the call site would resolve them and was rejected as
  schema duplication; instead the 2 is PINNED by a new test asserting this module emits no flat envelope at
  all, so the slack cannot later be spent on the hazard the ceiling exists for.

- [2026-08-25][ES-3] **`rank_semantic` is an EXTRACTION, not a second ranker.** The memory target is
  `get_semantic_context`'s `0.6·vec + 0.4·kw` hybrid plus MEMORY-GRAPH's graph boost — but it returned a
  formatted prompt block, so nothing could measure it. The ranking now lives in `rank_semantic` and the
  formatter calls it: one hybrid-recall rule, and an offline P@k is measured on the object a live turn
  ranks with. 157 pre-existing memory tests pass unchanged.
  Same discipline on the ground truth: `volunteer_qrels` and the live health panel's
  `volunteer_precision` share `_USED_EXPR`/`_VOLUNTEER_FROM_WHERE`, so "used" has one definition.

- [2026-08-25][ES-3] **A bug in this atom's own first cut.** The card round-trip read
  `entry["relevant"] or entry["already_relevant"]`, so a human marking a query with NO relevant result
  (`[]` — a real judgement) fell through and silently re-inherited the MINED label they had just overruled.
  Now keyed on KEY PRESENCE. Verified end to end through the real UI: unticking every candidate for one
  query lands `relevant_ids: []` in `benchmarks/retrieval/memory.json`, and that query's recall is then
  reported as undefined rather than as a zero.

- [2026-08-25][ES-3] **Measured on a seeded dev home, driven from `personalclaw retrieval-eval` with no
  flags.** Knowledge (16 items, 10 mined queries): the vector arm earns `enable` at ΔP@5 **+0.2259**;
  keyword and graph both `hold` at +0.0069; graph ALONE scores P@5 0.8750 over 4 scored queries with 6
  no-candidate. Memory (14 records, 8 queries): the vector arm is `hold` at ΔP@5 **−0.5083** — it actively
  costs precision on that corpus, which is exactly the "does vector beat keyword on THIS user's corpus"
  question §5.3 asks. The control row reports `p_at_k: null` with all queries no-candidate on both stores.

- **What is NOT closed, precisely.** (i) §5.2's source (a) is unavailable for the knowledge store until
  something writes `surfacing_events`; the substitute is `intent_outcomes` and is labelled
  `mined:intent_outcomes` in every qrels row, so a reader can tell. (ii) §5.2's source (c) is declined on
  circularity grounds (above). (iii) The push-context resolver arms (`memory_push.ARM_ALIAS/EXACT/SUFFIX`)
  are NOT a bench target — they resolve an ENTITY, not a ranked record list, so they do not fit the
  `Retriever` signature; the memory bench covers the graph/keyword/vector arms the criterion names. (iv)
  `RunPin` requires a non-empty `model_fingerprint`, and a retrieval score is a function of the EMBEDDING
  model, not the chat model. `active_models.json`'s `embedding` use case is in the fingerprint, so the pin
  is not wrong — but a home with a corpus and no bindings cannot run the bench, and whether the pin should
  narrow to the embedding binding for this subject is an owner call, not this atom's to make.

- **Gate:** `make lint` clean (mypy 1002 files); `make test` **26194 passed, 0 failed** (a first pass showed
  4: two were mine and fixed — the new `json_error` codes needed `HTTP_ERROR_CODES` rows and the census
  ceiling needed the pinned raise above; `test_subagent_cwd` and `test_subagent_fanout` both pass at `-n0`
  and are the known xdist/SEL isolation flakes); `npm run typecheck:web` clean; `npm run test:web`
  **479 files / 5053 tests passed**; `npm run build` clean; `scripts/gate_report.py` 6/6; probe residue 16
  tree-wide with **0** introduced. `src/personalclaw/reference/routes.md` regenerated for the three new
  routes (verified written into the worktree, not the main checkout).

## Execution log — ES-5 registration seal (§2.1 "immutable `registration.json`") — clause was DECORATION, now binding; atom stays `todo`

- [2026-08-25][ES-5] 🔴 **§2.1's pin was self-referential, and the 2026-08-23 audit's "MET — second
  write refused, read-only on disk" was too generous.** Everything that verified a study lived in
  `evals/studies/<id>/`: `rubric_status()` compared the pinned `rubric.md` against `rubric_sha256`, and
  `rubric_sha256` is a FIELD OF `registration.json` in the same directory. So the whole four-way rubric
  invalidation was defeated by two ordinary edits — rewrite `rubric.md`, set `rubric_sha256` to the new
  text's hash — after which every hash in the directory agrees with every other one and the study reads
  as pristine. `registration.json` also carries `agreement_floor` and `k`, which `decide()` READS as the
  verdict threshold, so an experimenter who saw `judge_unreliable` could lower the floor and re-run. And
  `reg.sha256()` — docstring: *"the study's identity in the results ledger"* — was **recomputed from
  whatever was on disk at run time** and written into the evidence unit (`studies.py:1333`), the
  `results.tsv` pin (`_pin_for`) and `study_view`, with nothing to compare it against. A hash computed
  from the value it is meant to pin cannot pin it. The only protection was mode `0400`, which the module
  itself calls *"a tripwire, not the lock"*.

- [2026-08-25][ES-5] **What shipped: the seal, one journal outside the directory under verification.**
  `store.append_study_seal()` / `read_study_seal()` write `evals/study_seals.tsv`
  (`study_id`/`registration_sha256`/`sealed_ts`), append-only, **first row wins on read** — so the
  cheapest attack (append the edited registration's hash) is a no-op, and the next cheapest (rewrite or
  truncate the journal) is a different and much louder act than editing one JSON field. This is
  tamper-EVIDENT, not tamper-proof: the same standard the pinned rubric already claimed, one directory
  further out, which is the difference between a claim that can fail and one that cannot.
  `studies.seal_status()` returns `ok` / `registration_unsealed` / `registration_tampered`.

- [2026-08-25][ES-5] **Two call sites, and the precedence is load-bearing.** (1) `run_study` checks the
  seal **before the rubric and before arm 1** — before, because `rubric_sha256` is a field of the object
  being verified, so checking the rubric first is checking a claim against itself. (2)
  `study_arms.arm_bodies_for_study` raises `StudyArmError`, which is what makes the refusal reach
  `personalclaw study --run` **and `--dry-run`**: the CLI calls it before the spend preflight, and a dry
  run never reaches `run_study` at all, so without this second site the seal would have missed the one
  surface that exists to be consulted before spending. `decide()` gained `seal_state`/`seal_detail` with
  the same shape as `rubric_state`, invalidating first.

- [2026-08-25][ES-5] **An UNSEALED registration is `invalidated`, not tolerated — a deliberate clean
  break.** "No seal was ever recorded" and "the seal was deleted" are indistinguishable from inside the
  check, and the permissive reading of an indistinguishable pair IS the hole (an attacker who can edit
  the registration can delete a journal, and would be rewarded for it). A study registered before this
  journal existed therefore has to be re-registered: one command, under the pre-1.0 banner, and the
  flywheel re-registers on the next filed diff. `study_seals.tsv` is deliberately **not**
  `derived_within` on the `evals` inventory entry — excluding it from snapshots would make every
  restored study read as unsealed, which is the opposite of the `locked/` exclusion's reasoning.

- [2026-08-25][ES-5] **The discriminating test is the one nothing else could catch.**
  `test_a_forged_rubric_pin_that_the_RUBRIC_CHECK_calls_OK_is_still_invalidated` forges the rubric AND
  its hash, and its FIRST assertion is the floor: `rubric_status(forged, forged_rubric) == (RUBRIC_OK,
  "")`. That proves the study is invisible to the pre-seal implementation, so the red the seal produces
  is a red nothing was catching. Falsification of the `run_study` site (replacing `seal_status(reg)` with
  a literal `SEAL_OK`) reds three tests with **`assert 'win' == 'invalidated'`** — with the seal deleted,
  a tampered study returns a WIN, which is the exact artifact §2 exists to prevent. Falsifying the
  first-wins read (last-wins) reds only the append test; falsifying the `arm_bodies_for_study` site reds
  the CLI test with *"DID NOT RAISE SystemExit"* while its intact-seal floor still passes.

- [2026-08-25][ES-5] 🔴 **A vacuity floor found a real round-trip bug, and the seal is what made it
  matter.** `registration_from_dict` read `float(data.get("agreement_floor") or 0.6)` — a **falsy 0.0
  became 0.6**. Harmless before (a lossy read nobody hashed); with a seal it is a false accusation: a
  user whose `evals.judge_agreement_floor` is `0` registers a study whose rehydrated form hashes
  differently from the bytes on disk, so every one of their studies would be `registration_tampered`.
  Fixed to an `is None` check, with `test_a_zero_agreement_floor_ROUND_TRIPS_or_the_seal_calls_an_honest
  _study_tampered` pinning it. A default is what an ABSENT key means, never what a zero means.

- [2026-08-25][ES-5] **Revised clause status — nine of ten hold; the earlier table's "over the harvested
  suite" row is stale** (closed by the 2026-08-24 executor session's `harvested_study_cases()`; the log
  wins over the table). The one clause still short is the sentence the criterion opens with, and it is
  short by an **owner decision, not by missing code**: a flywheel template-diff **pre-registers**
  automatically and the RUN is `personalclaw study --run`. This work strengthens the case for ratifying
  that seam rather than reversing it — auto-registration is only safe *because* the registration is now
  binding, and a 3-case suite at k=5 is 30 arm + 90 judge calls that no agent tool call should start
  silently (Autonomy-Guardrails' territory). **Owner call, unchanged and now cheaper to ratify.**

- [2026-08-25][ES-5] **What the seal does NOT cover, stated so nobody reads `seal_status: ok` as more
  than it says.** It covers the registration — hypothesis, metric, decision rule, `k`, `agreement_floor`,
  `rubric_sha256`, `budget_usd`, and the locked checks' **filenames**. It does not cover a locked check's
  **content**: `locked/*.json` is `0600` but unhashed, so weakening an `expect_exit_code` or dropping a
  `required_phrases` entry after registration is currently undetectable, and §2.2's anti-nodding half is
  as editable as §2.1's was. The clean closure is one field — `locked_sha256` over
  `[c.to_dict() for c in load_locked_checks(id)]`, computed at registration from `sorted(parsed, key=id)`
  and verified in `run_study` as `locked_checks_tampered` — which the seal then covers transitively. NOT
  built here: it is outside the criterion's clause list, it changes the registration schema, and getting
  the sort/formatting normalization wrong would invalidate honest studies. Recommended as the next ES-5
  increment, with the two standing owner decisions (`LOW_POWER_CASES = 3`; LEARN-R2 promotion discipline).

- **Gate:** `make lint` clean (black 2058 files, mypy 1012 source files, 0 issues); `scripts/gate_report.py`
  **6/6 PASS**; targeted `pytest --no-cov` — `test_evals_studies` + `test_evals_study_arms` +
  `test_evals_store` + `test_evals_routes` + `test_evals_harvest` + `test_evals_pinning` +
  `test_durability_inventory` + `test_snapshot` **320 passed**, the three refiner suites **123 passed**,
  eight new seal tests green; probe residue **16 tree-wide, 0 introduced**; every falsification restored
  from a file copy at the literal path and grepped back.
---

## Execution log — ES-11 (bundled optimize-harness template + the budgeted search) — §8 **PARTIAL**, atom stays `todo`

- **[2026-08-25][ES-11] DONE — the deliverable that did not exist now does.** Census before starting:
  21 bundled templates, **none** matching `optimi` or `harness`; `no_improvement_halt` **absent from
  `src/` and `tests/` entirely**. Two of the criterion's three halt conditions were already real
  (`hypothesis_abandon_after` at `loop/tick.py:202`/`:543`, `budget_usd` across 11 files); the third
  was a declared strategy with no executor. Landed: `src/personalclaw/evals/optimize.py` (the search)
  and `workflows/bundled/optimize-harness/workflow.json` (the declarative packaging).
  `tests/test_workflows_bundled.py`'s `EXPECTED` ratchet moves **21 → 22**.

- **`no_improvement_halt`'s call site is `optimize.run_search`, halt check 3.** DEVIATION recorded
  deliberately: it is NOT a second name for `loop/tick.py`'s `no_progress_stop`. Those are the same
  RULE (`max(window) <= window[0]`) over different SUBJECTS — loop cycles there, search candidates
  here — so `optimize.no_improvement` reproduces tick's arithmetic exactly rather than aliasing its
  field. Adding `no_improvement_halt` to `TickConfig` beside `no_progress_stop` would have minted a
  second dialect of "it stopped improving" for one shared consumer, and `runtime_hints.execution.breaker`
  has **no live reader at all** today (`execution_hints.py` says so explicitly — that plumbing is
  WF2LOO-7's, not this atom's). `tick.py` was therefore not touched.

- **"Nothing live mutates" is proven by OBSERVATION, twice over, because one detector is not enough.**
  (i) `LiveWitness` content-hashes the live artifact + its `.pclaw-lock.json` before the search and
  re-hashes after; drift raises `LiveMutationError`. (ii) The engine's own `scope.snapshot` brackets
  **the proposer call**, not just the candidate write — a diff taken after the proposer returned would
  observe only `run_search`'s own writes and score every escape as clean. The two detectors catch
  different things and the test suite is what separates them: a scorer that persists a change raises
  (i), and a proposer that writes the live file and restores **identical bytes** defeats (i) by
  construction and is caught by (ii) as `scope_violation`. The end-to-end byte-comparison test uses the
  test file's **own** hasher, not `LiveWitness` — a witness certifying itself would pass with an empty
  dict on both sides.

- **The dual gate is railed one half at a time**, each holding the other satisfied: a candidate that
  beats the best-ever but misses the suite threshold is `below_suite_threshold`; one that clears the
  threshold but ties or misses the best-ever is `not_best_ever`. Ties lose. The best-ever floor is
  captured **once** (`capture_best_ever`, counted to exactly one call across a 3-iteration search) —
  a per-iteration re-read would fold the candidate being scored into the floor meant to pin it and the
  second half would be free.

- **A frozen-region touch is refused, not recorded**, on two paths that must both hold: the module's
  `scope_check` (frozen beats allowed — a frozen path *nested inside* an allowed root still violates,
  which is the one rule `allowed_write_paths` cannot express) and the template's `verify_scope`
  expression gate. **Measured during falsification:** deleting the frozen half of `scope_check` reds
  only the `frozen_BEATS_allowed` unit test — the two end-to-end tests still pass, because a frozen
  root that sits *outside* the sandbox is already caught by the plain allowed-scope diff. So the
  frozen rule's unique contribution is observable only in the nested case, and that is where it is
  railed. Recorded rather than papered over.

- **What is NOT closed, precisely.** (i) The winner arrives as a **template-version diff** only. The
  LEARN-R3 **skill sidecar overlay** half of the criterion's "template version diff OR sidecar overlay"
  is unbuildable today: there is no skill-sidecar apply/revert mechanism in the tree (`git grep sidecar`
  finds agent-runner metadata and app execution modes, nothing for skills), so that arm awaits LEARN-R3.
  The criterion's OR is satisfied; the second disjunct is not available. (ii) The search's per-iteration
  ledger lives in the sandbox's `.experience/index.json` and in `search.json`; it does **not** append
  `results.tsv` rows, because `store.append_result` requires a complete `RunPin` and a candidate scored
  by a caller-supplied scorer has no honest model fingerprint to pin. The gate READS `results.tsv` (the
  best-ever floor) — writing to it is the model-attribution problem ES-3 already flagged as an owner
  call, and inventing a pin here would put unattributable rows in the shared ledger. (iii) Nothing wires
  the template to a trigger or a Learning-page surface; it is a starter a user runs, which is what §8
  asks for, but the FE half of §10 is untouched by this atom. (iv) The template's propose stage needs a
  real model to run end to end, so the *composed* run is not covered by an offline test — every
  mechanism it depends on is, and the template↔module seam is asserted by name in both directions
  (subcommands ⊆ `COMMANDS`, `PC_OPT_*` keys ⊆ the module's env tables), so a renamed subcommand or a
  dropped env key reds the suite rather than failing at run time.
  **The atom therefore stays `todo`:** clause (i) is a named disjunct of the criterion that no code in
  this tree can satisfy yet.

- **No config field was added.** `loader.py` is at 5900 lines against a 6000-line ceiling
  `test_structural_baseline.py` marks FORBIDDEN TO RAISE, with the rail's ≥100-line headroom demand
  exactly at the floor — so a new `DashboardConfig` field would red that gate with nothing left to
  compress. The search's whole envelope is declared per-run as template **inputs** instead
  (`budget_usd` required with no default, the three windows with defaults), which is the right home for
  it anyway: a per-search dollar ceiling is not a global setting.

- **Falsification.** Three live mutations, each grepped back before running and each restored from a
  literal-path file copy: (1) `if False and no_improvement(...)` → `test_no_improvement_halt_HALTS` red
  (`ITERATIONS_EXHAUSTED` instead of `NO_IMPROVEMENT`); (2) `beats_best_ever` forced to `True` →
  `test_half_B_alone_cannot_admit` + `test_a_TIE_with_the_best_ever_loses` red; (3) frozen half of
  `scope_check` removed → `test_frozen_BEATS_allowed` red (and the finding recorded above).

- **The `verdict-type` ratchet caught this atom minting a 25th verdict dialect, and the fix was
  the rename the ratchet's own rationale prescribes.** The first draft named the scope-diff
  result `ScopeVerdict` and gave `DualGate` a `.verdict()` method; `structural-duplication`
  reported `optimize.py: re-derived implementations rose 0 -> 1; new site(s):
  ['verdict-type:ScopeVerdict']`. That is a scope diff in the write-scope domain, not a judge
  verdict — so it is now `FrozenScopeReport` (composing the engine's own `scope.ScopeReport`
  rather than restating it) and `DualGate.decide()`. **`structural-baseline.json` is byte-
  unchanged**: a fresh render after the rename matches the committed file exactly, so the
  ratchet was satisfied by fixing the code and nothing was blessed.

- **One generated artifact moved and it is this atom's:** `tests/fixtures/frontier_golden/bundled.jsonl`
  gains **19 rows, 0 removed** — every added row is `optimize-harness`, and `policies.jsonl` is
  byte-unchanged, so no existing template's schedule shifted. The rows read
  `preflight` → `propose` → `scope_check` → `verify_scope` → `adjudicate`, which is the intended
  order and independent confirmation that the loop body schedules the frozen-region check BEFORE
  the adjudication rather than beside it. Regenerated by running the WORKTREE's copy of
  `tests/test_workflows_frontier_golden.py` (its `GOLDEN_DIR` is `Path(__file__).parent/...`, so
  the write landed in the worktree and not in the main checkout — verified).

- **Also driven as a real subprocess, not only in-process** — `python3 -m personalclaw.evals.optimize`
  is what the template's bash nodes actually type, so it was exercised that way against a temp home:
  `preflight` witnesses 2 files and reports the envelope; `scope-check` returns `clean`; after
  tampering with the live `workflow.json` the same command returns `scope_violation` naming the exact
  path; `adjudicate` then reports `clears_suite_threshold: true` AND `beats_best_ever: true` and STILL
  `admitted: false, outcome: scope_violation` — "dead regardless of score", observed rather than
  asserted; an unbudgeted `preflight` exits 1 with a JSON refusal rather than a traceback.

- **Gate:** `make lint` clean (black 2065 files, isort, flake8, mypy 1015 source files);
  `make test` **26699 passed, 0 failed, 30 skipped, 12 xfailed** (424s); `scripts/gate_report.py`
  **6/6**; `tests/test_evals_optimize.py` 47 tests; probe residue **16 tree-wide with 0 introduced**;
  worktree clean. No `web/` change (this atom ships no FE surface — see the not-closed list).

---

## Execution log — ES-3 (continued: the read-only clause, in full; qrels provenance) — atom stays `todo`

- **[2026-08-26][ES-3] Startability re-established against CODE first, and the finding is that this
  atom was already ~95% built.** `src/personalclaw/evals/retrieval_bench.py` (1457 lines), the
  `arms=` mask on both retrievers, the CLI, three routes and `RetrievalBenchPanel` all landed on
  2026-08-25 and are on `main`. Six of the criterion's seven clauses hold. This session did NOT
  rebuild any of it; it closed two gaps where the shipped report said more than it measured, and
  re-verified the load-bearing claims by mutation rather than by reading the previous log.

- **[2026-08-26][ES-3] 🔴 A REAL gap in §5.1's read-only clause: it was half-asserted.** §5.1 says the
  harness "never writes to **either**" store, but `run_retrieval_bench` and `card_for_store` wrapped
  only the store the run OPENED in `store_unchanged`. A knowledge run that wrote to `memory.db` — the
  single write the KNOWLEDGE/MEMORY boundary exists to forbid — passed the rail. Now
  `stores_unchanged()` guards the measured path plus the sibling live store, composed out of
  `store_unchanged` rather than re-deriving the digest/drift comparison, so "unchanged" keeps one
  definition and one error message.
  **Scope restriction, deliberate:** `sibling_store_paths` returns nothing for a path outside
  `config_dir()`. A caller measuring a `tmp_path` database is not measuring this home, and digesting
  the real `~/.personalclaw/memory.db` to guard it would make a test READ a live file a running
  gateway may be writing — a read-only rail must not itself reach outside the home under test. The
  lookup passes `create=False`: a read-only rail that mkdirs is not read-only.
  **Falsified both directions.** Reverting the run call site to `store_unchanged(resolved)` (live
  line mutated, grepped back) reds exactly one test — `DID NOT RAISE StoreMutatedError`, 1 failed /
  57 passed — and the same invocation is 58 passed after restoring from a file copy at the literal
  path. The green side is railed separately, because it would otherwise be VACUOUS: every other
  passing test leaves the sibling store ABSENT, which digests identically before and after by
  construction, so `test_a_real_sibling_store_does_not_make_the_rail_fire` runs a knowledge bench
  with a real, populated, still-OPEN `memory.db` beside it and asserts the guard stays silent.

- **[2026-08-26][ES-3] The qrels provenance was on disk but not in the report.** The 2026-08-25 log
  claims the `intent_outcomes` substitute is "labelled `mined:intent_outcomes` in every qrels row, so
  a reader can tell". Measured: a reader can tell only by opening `benchmark.json` — `table.json`, the
  API view and the panel published P@5/R@5 with no statement of which labels produced them. Since
  this harness mines a SUBSTITUTE for one of §5.2's three named sources, the mix is part of reading
  the score. `RetrievalBenchmark.sources()` counts it server-side (a frontend re-deriving it would
  eventually disagree with the runner — `latest_retrieval_view`'s own doctrine) and `table.json`
  carries `qrels_sources` + `queries`. An unlabelled query counts under its own `""` bucket rather
  than being dropped: a census that hides its own unlabelled rows overstates what it knows.
  **Absence is not zero, on the FE too.** A run written before the census existed has no key, and the
  panel renders "Ground truth: not stated by this run", never "0 from every source". Falsified:
  collapsing that branch to `return null` reds `says the provenance is unstated…` (1 failed / 11
  passed → 12 passed after restore); dropping `"qrels_sources"` from the artifact reds the run test
  with `KeyError: 'qrels_sources'` (1 failed / 59 passed → 60 passed after restore).

- **[2026-08-26][ES-3] 🔴 E6 ESCALATION, not improvised: `surfacing_events` does not exist as a
  schema.** The one clause still open is §5.2's source (a) for the knowledge store. Verified this
  session: `surfacing_events` appears in the whole tree exactly ONCE, as prose in
  `dashboard/handlers/learning.py:352`. There is no table, no schema, no reader and no writer —
  `learning/measure.per_arm_precision(events)` is a PURE function over a list of dicts. So mining it
  would mean minting LEARN-R4's schema and its writer inside ES-3, which is another plan's
  deliverable (LEARNING-FLYWHEEL §2.5). Recorded rather than improvised. The knowledge-side
  substitute (`intent_outcomes`) and the memory-side real source (`mem_volunteer_events`) both stand,
  and their mix is now visible in the report per the item above.

- **What is still NOT closed** (unchanged from 2026-08-25 except where noted): (i) §5.2 source (a) for
  knowledge — blocked on the above, now with the substitute's provenance PUBLISHED rather than only
  on disk; (ii) §5.2 source (c) stays declined on circularity grounds; (iii) the push-context resolver
  arms (`memory_push.ARM_ALIAS/EXACT/SUFFIX`) are still not a bench target, and this session found a
  SECOND reason beyond the signature mismatch: `mem_volunteer_events` records `entity_name`/`arm` but
  never the surface text that triggered the match, so there is no stored input to replay a resolver on
  — an offline resolver bench is not buildable from the state the tree persists, and synthesizing the
  surface from the alias table is exactly source (c)'s circularity; (iv) the `RunPin`
  embedding-vs-chat-fingerprint question remains an OWNER CALL and is not decided here.

- **Also observed, not fixed (out of this atom's scope):** `open_store(memory)` calls `handle.init()`
  — a `CREATE TABLE IF NOT EXISTS` write — before the digest is taken, which the code documents; and
  `personalclaw retrieval-eval` has no test of its own (`git grep -l 'retrieval-eval' -- tests/` is
  empty), so the CLI's argument handling is covered only through `run_retrieval_bench`.

- **Gate:** `make lint` clean (black 2088 files, isort, flake8, mypy **1027 source files**);
  `scripts/gate_report.py` **6/6** (including `structural-duplication` — the new guard composes the
  existing one rather than cloning it); targeted `pytest --no-cov`
  `test_retrieval_bench.py` + `test_evals_routes.py` + `test_evals_store.py` +
  `test_knowledge_contradiction.py` **167 passed** (61 in the bench suite, up from 54);
  `npm run typecheck:web` clean; `npm run test:web` **492 files / 5229 tests passed** with
  `docs/design/consistency-audit.json` unchanged; `npm run build` clean; probe residue **16 tree-wide
  with 0 introduced**; `git status --porcelain` empty.
## Execution log — ES-7 §3.1 (the LEARN-R9 hand-off: the evidence GRADE is now read) — atom stays `todo`

- [2026-08-26][ES-7 §3.1] **Verify-first drive. Eight of nine `done_when` clauses were already on
  `main` and railed** — the periodic runner (`ablation.run_cadence`, live at
  `durability/service.py:857` inside the periodic loop), one component per cadence
  (`pick_component` round-robin + `state["cursor"]`), keep/remove/lighten
  (`test_all_three_verdicts_are_reachable`), child-process overlay toggling
  (`runner.py:175` spawns `python -m personalclaw.evals.child` with the overlay on the env COPY
  only), the byte-identity live-state guard **with its vacuity leg**
  (`test_live_state_guard_reds_when_a_mutation_leaks`), §3.3's surfaced-vs-suppressed replay, the
  watchdog's exactly-one digest (`test_a_rebind_of_three_bindings_emits_exactly_one_digest`) and its
  per-fingerprint `results.tsv` baselines. **Nothing was rebuilt.** The unmeasured-arm honesty
  property was also already held at every surface checked — `arm_mean` returns `None`,
  `_proposal_body` prints `n/a`, `AblationPanel.fmtMean` prints `not measured`, and both
  `test_an_unscored_baseline_is_none_not_zero` and `test_an_unmeasured_arm_is_reported_not_averaged`
  pin it.

- [2026-08-26][ES-7 §3.1] 🔴 **The one clause that was HALF met: "attaches as the ablation-grade
  evidence" attached, and nothing read the grade.** `file_retirement_proposal` stamps
  `evidence_strength="ablation"` — the tier that means a paired on/off measurement rather than a
  co-occurrence — and `git grep evidence_strength -- src/ web/src` returned **nine `enqueue` call
  sites across eight modules and ZERO readers**: not a queue gate, not `inbox.row_from_proposal`,
  not the `GET /api/learning/proposals` payload, not `LearningRow` in `api.ts`, not the page. The
  refs ARE read (`LearningPage` renders their count, `bulk_acceptable` requires one), so the two
  modules were not merely coexisting — but the row a human decides a RETIREMENT on read
  `2 evidence ref(s)` whether the null result was measured or merely co-occurred, which is the one
  distinction that decides that particular question. Deleting the stamp would have reddened one
  assertion on the returned object and nothing else.

- [2026-08-26][ES-7 §3.1] **Closed as a READER, not as a gate.** `inbox.Row` carries
  `evidence_strength`, `to_dict()` publishes it, and `LearningPage`'s evidence clause now reads
  `evidenceLabel(row)` — `2 evidence ref(s) · measured on/off` for an ablation, `· correlated` for a
  co-occurrence, `· measured (controlled study)` for §2's `causal`. **Deliberately NOT a
  strength-based admission gate:** eight other modules stamp a tier, so a gate would silently
  re-scope every one of their proposals, and no `done_when` in this plan asks for one. Surfacing is
  what the clause asks for and it is contained.
  **An UNGRADED tier ("" from a record filed before the tier existed, or a name this build does not
  know) renders `ungraded`, never a fallback grade** — the same rule as `fmtMean(null)`: turning
  "nobody said" into `correlated` would be the evidence-tier form of drawing an unmeasured arm as
  `0.000`.

- [2026-08-26][ES-7 §3.1] **Falsified both directions, on the LIVE line, restored from a file copy.**
  (1) `to_dict` hardcoded to `"evidence_strength": "correlated"` (`ast.parse`d, grepped back) →
  **2 red**, `assert 'correlated' == 'ablation'`, one in `test_learning_inbox.py` and one in the
  end-to-end `test_the_ablation_grade_reaches_the_row_a_reviewer_decides_on`; restored → the SAME
  invocation **2 passed**. (2) `LearningPage`'s clause reverted to the count-only form it shipped
  with → `evidenceGrade.test.tsx` **2 failed / 4 passed**; restored → **6 passed**, same invocation.
  The two page-level cases are the ones that move: everything else in that file calls
  `evidenceLabel` directly and would survive the deletion, which is exactly the state the field was
  already in. Every tier assertion is paired with a vacuity floor comparing two rows that carry the
  SAME ref count, so a label built from the count alone cannot pass.

- [2026-08-26][ES-7 §3.1] **Still `todo`, and NOT for anything added here.** §3.3's plural replay
  remains the open clause: `MatrixSpec` carries one `subject`, so the bench scores the newest
  harvested case and reports `subject_candidates` it did not score. That needs the per-skill
  `MatrixSpec.subject` form — **an owner scope call, unchanged by this drive**. The §3.2 plan-text
  drift recorded on 2026-08-23 (the plan names four watched bindings; `eval_judge` is not in
  `VALID_USE_CASES`, so only three are watchable) is also still open as a **plan-text** correction.

- **Gate:** `make lint` clean (black 2094 files, isort, flake8, mypy 1029 source files);
  `make test` **27084 passed, 1 failed, 30 skipped, 12 xfailed** (738s) — the one red is
  `test_inbound_mcp.py::TestTransport::test_rate_cap_returns_429_with_retry_after` (`assert 21 == 20`,
  a token bucket over-admitting by one under an 18-worker load), **green alone in 67s** and touching
  no file in this diff; `scripts/gate_report.py` **6/6**; `npm run typecheck:web` clean,
  `npm run test:web` **493 files / 5233 tests passed** (and `docs/design/consistency-audit.json` came
  back byte-identical), `npm run build` clean. One further narrowed-run flake seen and dismissed:
  `test_consulted_runs_reads_the_wf2_r13_ledger_event` ERRORed with *"the run store must not resolve
  to the real home"* on one 7-file invocation and passed on the identical re-run — the known
  xdist-reshuffle isolation family, in a file this diff does not touch. Probe residue **16 tree-wide,
  0 introduced**; worktree clean.

- [2026-08-26][ES-7] **Integration follow-up: a real-data rail that expires every time the log grows,
  and it is the THIRD PR this family has reddened.** `test` failed on *both* duplicate runs on the
  same SHA, so it was deterministic and mine — but not in ES-7's feature code.
  `test_the_real_es7_verdict_comes_from_its_own_tagged_entry` asserted a literal phrase
  (`"now closed in INPUTS"`, later widened to two literals) against whichever entry `decide_log`
  picks. This PR's own new §3.1 entry displaced both, so the enumeration needed a third member —
  **its second expiry in two days**, the previous one already recorded in the test's own docstring.
  **The enumeration cannot converge, and that is a property of the data, not of the wording.** The
  plan log is append-only, so *which* of ES-7's entries wins changes with every legitimate entry
  added. Censused across the file rather than assumed: of the **four** real-data rails that call
  `decide_log`, **three have now reddened a PR for exactly this reason** — `MRT-5`'s (#2066), this
  one (#2098), and `test_the_real_inherited_verdicts_are_gone` (#2101, still open).
  **So the positive "which ruling is cited" clause was dropped rather than extended.** It never
  carried the defect. The defect is that `[harvest]`'s text — an entry that says of itself *"Not an
  atom of its own"* — used to adjudicate `ES-7`, and that is pinned by three PROVENANCE assertions
  (`"[ES-7" in excerpt`, `"[harvest]" not in`, `"Not an atom of its own" not in`) which do not care
  which of ES-7's own entries wins. `verdict == PARTIAL` still holds the adjudication.
  **Non-vacuous:** neutering `decide_log`'s provenance preference so it returns `hits[0]`
  unconditionally reds **11** tests including this one; restoring from a file copy returns 78 passed.
  The mutation was checked to parse before running, so the red is a failure and not a collection
  error.
- [2026-08-26][ES-7] **A second red the rebase surfaced, and it is the pure "a green batch is not a
  green union" shape.** `LV-4` merged (#2073) while this branch was in flight and added an
  `api.identityReport` call to the same `LearningPage` that `evidenceGrade.test.tsx` renders. This
  file's `vi.mock('../../lib/api')` lists only its OWN reads, and a partial mock does not fail on the
  missing key — it throws `api.identityReport is not a function` **from inside the render**, so both
  call-site tests died before asserting anything. **Neither PR could see it alone:** LV-4 added the
  call, this file predates it, and the break exists only in the combination. Fixed here by adding the
  key with a REJECTING stub rather than a resolving one — the page must paint the ablation grade
  whether or not the identity report resolves, and if it ever grows a real dependency on that payload
  these tests should say so rather than silently pass. Falsified both directions: removing the entry
  reds exactly those 2 tests, restoring returns 497 files / 5277 tests.

- [2026-08-26][ES-5] **OWNER RULING — `ES-5` flips to `done`.** Nine of ten clauses already held; the
  tenth ("a flywheel template-diff RUNS a study") was short by an owner decision, not by code: the
  diff pre-registers automatically, but the run is a deliberate `study --run` (30 arm + 90 judge calls
  at 3 cases × k=5), where §2.1 says "the human registers the study; the substrate runs it".
  RULED: **ratify register-at-file / run-on-invocation.** §2.1's concern is that a study be
  *pre-registered before it is run* — which the shipped seam satisfies exactly, and arguably better,
  because registration is now mechanical and cannot be skipped. Auto-running 120 model calls without a
  human present is precisely the unattended spend AUTONOMY-GUARDRAILS exists to refuse, so gating
  auto-run behind Guardrails spend would add a second control for a behaviour we do not want in the
  first place. The inversion is only in WHO REGISTERS, and the shipped direction is the safer one.
  Flipped; no owner input remains.

- [2026-08-26][ES-11] **OWNER RULINGS on both open questions — neither needs further owner input, and
  the remainder is small.** (1) The `LEARN-R3` skill-sidecar overlay arm does not exist in the tree.
  RULED: **the criterion's OR is already satisfied by the template-diff disjunct** — do not build a
  second arm to satisfy a disjunction that is true, and do not re-scope the clause to demand both.
  (2) No `results.tsv` write, because `store.append_result` requires a complete `RunPin` and a
  candidate scored by a caller-supplied scorer has no honest model fingerprint. RULED: **do not invent
  a fingerprint.** An unscored candidate writes NO results row. An invented fingerprint would poison
  every per-fingerprint baseline that reads the same file, which is a far worse failure than a missing
  row — and it is the kind of failure that surfaces months later as an inexplicable regression.
  REMAINING WORK, ordinary: make that absence *legible* at the read surface, so a human sees
  "unscored" rather than a missing or zero result. `ES-11` stays `todo` on that one reader.

## Execution log — ES-11 (the unscored candidate is now LEGIBLE) — §8 **COMPLETE**, ready to flip

- [2026-08-26][ES-11] **DONE — the remaining reader landed; every `done_when` clause now holds.**
  Scope was exactly the remainder the ruling above named: neither open question was re-litigated, no
  second overlay arm was built, and `append_result` still refuses an incomplete `RunPin`. What was
  wrong was the *rendering*: a `scope_violation` row carried `score: 0.0` and a `no_change` row
  carried the inherited incumbent, so both read as "measured, and it was nothing" in the two files a
  person (and the report node's prompt) actually opens — `.experience/index.json` and `search.json`.
  Three states now have three names, in one vocabulary reused from `retrieval_bench`
  (`SCORE_NO_CANDIDATES == REASON_NO_CANDIDATES == "no_candidates"`, so there is no second spelling
  of "there was nothing to measure"): `LedgerRow.to_dict` emits `score: None` plus
  `score_state: scored|unscored`, `SearchOutcome.to_dict` emits `results_state` plus
  `candidates`/`scored_candidates`/`unscored_candidates`, and `_cmd_adjudicate`'s per-iteration
  verdict carries the same two keys. `UNSCORED_OUTCOMES` is the closed classifier and is derived
  from `outcome` on purpose — `_cmd_adjudicate` rewrites the whole index from rows it reads back
  through `_as_float`, which turns a rendered `None` into `0.0`, so a flag stored beside the score
  would have resurrected the zero on iteration 2. That round trip is its own test.

- [2026-08-26][ES-11] **The anti-vacuity leg is the one that matters here: with no candidates at
  all, every "unscored" assertion passes for the wrong reason.** So the three states are compared
  as a SET across three real searches (`test_the_three_states_are_THREE_different_renderings`) —
  two collapsing into one rendering is a red even when each state's own test stays green. Assertions
  are on the FILES a real `run_search` wrote, not on `to_dict()` called by hand, because a rendering
  nobody's read path reaches is the inert-control shape this program keeps re-finding. Falsified
  four ways, each mutation `git grep`-confirmed and restored from a file copy: unscored → the scored
  label reds 2 tests (the ledger reader + the round trip); unscored `score` → the `0.0` reds the
  same 2 with *"a 0.0 here reads as a measurement that came up empty"*; no-candidates → the
  `unscored` state reds a DIFFERENT 2 (the third-state test + the set-of-three); and the unscored
  surface → `scored` reds the set-of-three alone. Four distinct reds, so the legs discriminate
  rather than all keying on one line. `tests/test_evals_optimize.py` 47 → 53 tests, all green;
  `make lint` clean; `src/personalclaw/config/loader.py` untouched at 5900 lines.

- [2026-08-26][ES-11] **Deliberately NOT changed, recorded so it is not re-found as a gap.**
  `SearchOutcome.winner_score` still renders `0.0` when nothing was admitted. That zero is already
  named by `admitted: false` + `winner_fingerprint: ""`, it is not one of the three candidate-level
  states the ruling is about, and the bundled template's report node declares `winner_score` as a
  required `number` in its own output schema — nulling it would be a template-contract change for
  no legibility gain. `ES.md`'s file-level narrative ("entirely unstarted; all 11 atoms are todo")
  is also stale against the row marks; it is the free-text summary `test_roadmap_atomic_status_sync`
  deliberately does not rail, and correcting it is a tracking pass, not this atom.

## Execution log — ES-3 (the `surfacing_events` blocker is CLOSED; source (a) is a real reader) — atom stays `todo`

- **[2026-08-27][ES-3] The 2026-08-26 E6 escalation is RESOLVED by another plan, exactly as
  escalating expected.** That entry recorded `surfacing_events` as appearing in the whole tree once,
  as prose, with "no table, no schema, no reader and no writer", and refused to mint LEARNING-FLYWHEEL
  §2.5's deliverable inside ES-3. `LEARN-R4` then landed it (`7a7877a5`):
  `src/personalclaw/learning/surfacing_events.py` ships `SurfacingEvent` + `SurfacingEventStore` in
  `learning.db`, with a writer reached from `skills.allocation.allocate_skills` and a 90d prune on the
  curator tick. §5.2's source (a) for the knowledge store is now a REAL reader —
  `mine_surfacing_qrels` — labelled `mined:surfacing_events` in every qrels row it produces.

- **[2026-08-27][ES-3] 🔴 DEVIATION from the briefed change: source (a) is ADDED, `intent_outcomes`
  is NOT deleted, and the two are not a fallback pair.** The instruction was to *switch* the knowledge
  source from `intent_outcomes` to `surfacing_events`. Measured before writing: `surfacing_events` is
  ONE log shared by every surfacing arm, and its `entity` is whatever id that arm ranks.
  `git grep -n 'SurfacingEvent('` finds exactly ONE production writer — `skills/allocation.py:461`,
  `kind="skill"` with the SKILL NAME as `entity` — while the knowledge target is
  `HybridRetriever` over knowledge `item_id`s (`knowledge/retrieval.py:155` `store.get_item(item_id)`).
  A skill name is never an `item_id`. So a literal switch would have put unreachable positives in
  `relevant_ids` and driven `P@k` to **0.0 for every arm and every mask, the good ones included** —
  publishing "retrieval is broken" as a finding about the retriever rather than about the label, which
  is the same failure the owner's `RunPin` ruling forbids one level up. `intent_outcomes` is therefore
  NOT a substitute awaiting replacement: it is the only label source in the knowledge `item_id` space,
  it is non-circular (ingest-time, never consults retrieval), and deleting it would have traded a
  working measurement (vector arm ΔP@5 **+0.2259** on the seeded dev home) for an empty one. The two
  sources are unioned with distinct `source` labels, `RetrievalBenchmark.sources()` publishes the mix,
  and a query labelled by both keeps the source-(a) row (an observation of what a real turn used
  outranks an ingest-time guess; merging the id sets under one `source` would publish a provenance
  true of neither). The false "SUBSTITUTE … switching this source over is ES-3's own remaining work"
  prose in `sources()` and in the module header is deleted, not softened.

- **[2026-08-27][ES-3] An unresolvable positive is DROPPED and COUNTED, never kept as a miss.**
  `mine_surfacing_qrels` resolves every candidate `entity` through `handle.get_item` and excludes the
  ones this store cannot contain, logging the count at INFO — because "source (a) mined nothing" and
  "source (a) mined rows this store cannot contain" are different facts and only the second one names
  its cause. Silently dropping them would make the empty result read as an uninstrumented home when
  the truth is a foreign arm's labels.

- **[2026-08-27][ES-3] What still does NOT hold, and it is upstream, not here.** Source (a)'s reader
  is correct and its data is empty on the knowledge arm: with `skills.allocation` the only writer,
  zero rows survive corpus resolution. `test_the_only_live_writer_contributes_no_knowledge_labels`
  rails that measured fact so it cannot close by accident — when a surfacing arm that ranks knowledge
  ITEMS is instrumented (LEARNING-FLYWHEEL §2.5's other three mechanical-`used` clauses: template run
  started, run outcome, lesson cited by `after_turn_review`; plus the ambient render's
  lesson/memory/persona candidates), that test flips and says so. **The atom stays `todo` and the row
  stays ⬜:** a P@5 whose source-(a) contribution is zero rows is a rail that matches nothing, and
  marking it 🟡 would spend the atom's own evidence.

- **[2026-08-27][ES-3] Owner rulings applied, none re-litigated.** (i) The `RunPin`
  embedding-fingerprint question: **do NOT invent a fingerprint** — an unpinnable run is unscored and
  renders as such. Nothing in this session needed a new state: `run_retrieval_bench` already *raises*
  `store.PinRequiredError` before any measurement on an incomplete pin, so no score, no `0.0` and no
  missing row is ever published for one, and ES-11's `SCORE_SCORED`/`SCORE_UNSCORED`/
  `SCORE_NO_CANDIDATES` vocabulary was deliberately NOT re-minted here (this harness's own absent-metric
  vocabulary — `REASON_NO_CANDIDATES`/`REASON_NO_RELEVANT` + `VERIFIER_ABSENT` — already covers its
  cells). Whether the pin should narrow to the embedding binding remains unanswered and is untouched.
  (ii) Source (c) stays DECLINED on circularity. (iii) The memory arm is OUT OF SCOPE: `LEARN-R4`
  recorded `mem_volunteer_events` as carrying no surface text, which is the second, still-open reason
  the resolver arms are not a bench target. Recorded as a named, dated PARTIAL rather than half-built.

- **[2026-08-27][ES-3] Read-only, re-verified rather than assumed.** `learning.db` is neither store
  §5.1 guards, and `SurfacingEventStore.read` declares its table with `CREATE TABLE IF NOT EXISTS` on
  a fresh home — recorded here so it is not re-found as a rail violation. The miner runs BEFORE
  `run_retrieval_bench`'s `with stores_unchanged(...)` block, and the cross-store rail
  (`test_a_knowledge_run_refuses_a_write_to_the_memory_store`) was re-run and re-falsified this
  session; it still bites.

- **Falsifications (each mutated the LIVE line, `git grep`'d back, then restored from a file copy at
  the literal path — never `git checkout`).** (1) Collapsing `mine_knowledge_qrels` to
  `return mine_intent_qrels(handle)` reds `…reads_surfacing_events_as_source_a` and
  `…the_two_mined_sources_union_and_source_a_wins_a_shared_query` — **2 collected, 2 FAILED** (that
  `-n0` run then wedged in teardown before printing its summary, so the counts are the evidence, not
  the assertion text). Source (a) is genuinely read, not decoration. (2) Deleting the `get_item`
  resolution so unresolvable entities are kept reds both drop-rail tests — **2 collected, 2 FAILED**
  with `AssertionError: assert [QrelsQuery(…source='mined:surfacing_events')] == []` and
  `AssertionError: a label naming an id this store cannot contain was mined anyway`. The second red is
  the exact shape of the 0.0-for-every-arm failure a literal source switch would have shipped.
  (3) Neutering `sibling_store_paths` to `return []` reds
  `…a_knowledge_run_refuses_a_write_to_the_memory_store` — **1 collected, 1 FAILED,
  `Failed: DID NOT RAISE StoreMutatedError`** — so the cross-store read-only rail still bites.

- **Gate** (`PERSONALCLAW_HOME` unset): `make lint` clean (black 2164 files, isort, flake8, mypy
  **1068 source files**); `scripts/gate_report.py` **6/6**; targeted `pytest --no-cov` over
  `test_retrieval_bench.py` + `test_learning_surfacing_events.py` + `test_evals_routes.py` +
  `test_evals_store.py` + `test_structural_baseline.py` + `test_roadmap_atomic_status_sync.py` +
  `test_roadmap_dag_derived.py` — **174 collected, 174 passed, 0 failed**, with the conftest
  real-home rail reporting `/Users/golani/.personalclaw unchanged by this run`. `web/` untouched, so
  the npm legs were not run. `src/personalclaw/config/loader.py` is **5647 lines before and after** —
  not touched. Probe residue: **0** in either diff-touched file; `git status --porcelain` empty apart
  from `?? .venv`.

## Execution log — ES-6 (Loop-2 cheap gate subset + before/after proposal columns) — amendment E2 **PARTIAL**, atom stays `todo`

- **[2026-08-28][ES-6] The dozen is declared IN the scenario, NOT in `evals/scenarios/gate/`
  (DEVIATION from the amendment's sketch, with the measurement behind it).** The installed library is
  a FLAT directory and three readers glob it — `resolve_scenario_path`, `install_library`'s manifest
  pass, and `personalclaw eval` — and **none of them descends**: `install_library` iterates
  `target.iterdir()` and skips anything whose suffix is not a scenario suffix, so a `gate/` subdir is
  invisible to it and to the manifest, and a bare `gate/x` name is unresolvable. So membership is
  `"tiers": ["gate"]` on the scenario, read by `scenarios.tiers_of` and recorded per-scenario in
  `evals/scenario_library.json`. That follows the rule `origin_of` states outright — derive it by
  INSPECTING the data, never from a side list of names — and it lets a user's own scenario join the
  tier by adding one field. Consequence recorded rather than hidden: the field moves each scenario's
  `scenario_sha256`, so each of the twelve **also bumps `version` 1 → 2** (the backfill is
  version-keyed; without the bump every existing home would keep the old file and read an EMPTY gate
  subset — a silent no-op), and their prior ledger rows sit under the older hash. That is what the
  pin is for.

- **[2026-08-28][ES-6] Which twelve, and why exactly twelve.** Census of the shipped library before
  choosing: **14 scenarios, 23 turns total.** The `≤ 2 turns` cut is exactly 12 (8 at one turn, 4 at
  two) and the two it drops are `context_accumulation` (3 turns, 3 sessions) and
  `memory_recall_basic` (4 turns) — the two multi-session memory scenarios, which are also 2 of the 3
  carrying `judge` assertions. So "a curated dozen fast" is a natural cut of the existing library, not
  a number picked to match the amendment.

- **[2026-08-28][ES-6] "Fast" and "judge-light" are STRUCTURAL, and the second one is a correctness
  bug, not a style preference.** A tagged scenario over `MAX_GATE_TURNS` (2) is excluded with its turn
  count in the reason. A tagged scenario with **no non-judge assertion** is excluded too, and the
  measurement is in the suite: the child runs `EvalRunner(judge_enabled=False)`, whose
  `assertion_results` comprehension **filters judge assertions out of the scored set**
  (`eval/runner.py:509`), so such a scenario reaches `total_assertions == 0` and
  `child.result_from_scenario` falls back to **`1.0`** — a fabricated perfect score that would sit in
  a gate mean as if it were evidence. `test_a_tagged_judge_only_scenario_is_excluded…` asserts the
  exclusion AND re-measures the 1.0 fallback, so the reason cannot rot into a comment.

- **[2026-08-28][ES-6] Measured wall clock (the claim "fast" has to earn).** `--list` reports
  **12 scenarios, 16 turns per arm, 32 for before+after**. A real end-to-end `run_gate` in an isolated
  home with no reachable provider — real `run_matrix`, real subprocess spawn, real `empty` fixture seed
  per cell — took **33.5 s for 24 child spawns (~1.4 s/cell)**. That is the gate's FIXED floor; a real
  run adds one model call per turn (32 turns), so the honest statement is "seconds of harness plus 32
  model calls", which is the amendment's "minutes and cents". Every cell in that drive was
  `VERIFIER_ABSENT` and the report published `mean_score: None`, i.e. **it did not invent a score for a
  run that could not measure one.**

- **[2026-08-28][ES-6] The bound is enforced at the meter, and "unbudgeted" is UNGATED rather than
  unbounded.** No new config field: `EvalsConfig.default_budget_usd` already exists and is documented
  as the cap "a matrix/study run refuses to exceed" — a gate run is a matrix run, so the round-trip
  contract is untouched and `config/loader.py` is **5619 lines before and after**. `budget_usd <= 0`
  means UNLIMITED to `Budget`, which is the one thing a pre-ship gate must never be, so the refusal is
  structural: no ceiling ⇒ no run ⇒ `ungated` naming the knob. Then per cell: `meter.check_run` BEFORE
  (an `EXCEEDED` verdict stops the sweep and the un-run cells are NAMED in `bound["not_run"]`) and
  `meter.charge` of the child's OWN reported spend AFTER (`child.spend_from_home`, read inside the
  throwaway home before the parent deletes it, persisted by `_write_cell_artifact` and read back by
  `gate.cell_spend`). The charge is what makes the check bind — without it `run_totals` reads the zero
  S153 recorded. The matrix spec's own `budget_usd` is deliberately left at 0: its preflight is
  DAY-scoped, and comparing a per-gate ceiling against a whole day's spend would refuse the gate for
  spend that had nothing to do with it.

- **[2026-08-28][ES-6] No second runner, and no second refusal rail.** Scores come from
  `runner.run_matrix` through `evals/child.py` — one matrix per (arm × scenario) at `trial_count=1`,
  the shape `skills_bench.bench_skill` and `ablation.run_ablation` already use, including their
  injectable `run_matrix=`. The arm rides the spawn env on the SAME `os.environ.copy()` the workspace
  and home overrides ride, and the child stages it through `overlay.throwaway_home()` — which is
  `_cell_home` promoted to a public name rather than copied, because two answers to "may I write here"
  is one too many for a guard whose whole job is to have no exceptions. `test_apply_in_child_REFUSES_the_real_home`
  covers both holes (unset, and pointing at `~/.personalclaw`).

- **[2026-08-28][ES-6] `{before, after, pin}` — and the pin is trimmed, never synthesized.** The
  subject is the SUBSET, so the pin is `compute_pin_for_subject("gate", <canonical hash over
  {name: scenario_sha256}>)` — ES-4's move for a fixture SET. `RunPin.is_complete()` is a
  PRECONDITION: a home with no bound model has no honest `model_fingerprint`, so the run does not
  happen and the report is `ungated` with the missing part named. That applies ES-11's ruling
  verbatim — an invented fingerprint poisons every per-fingerprint baseline reading the same
  `results.tsv`. `before` reads the LIVE home's content at the candidate's own paths (read-only, in
  the parent) and `after` is what an accept would write, rendered by the REAL install rail, so the two
  arms name the same paths and differ only in the bytes at them
  (`test_the_candidate_comes_from_the_real_install_rail` compares it byte-for-byte against what
  `install_accepted_skill` puts on disk). The ledger row uses the `score_old`/`score_new` columns that
  have existed since ES-1 for exactly this shape.

- **[2026-08-28][ES-6] "Ungated" NEVER blocks, and that is tested from the accept path.**
  `proposals.accept` deliberately does not read `Proposal.gate`; `attach_gate` deliberately does not
  touch `status` or `updated_at` (a measurement is not a decision, and bumping the timestamp would
  re-sort the queue for something the user did not do). Two tests drive `accept` to completion — one
  with no gate run, one with a **measured regression** — because the columns inform the decision and
  must not take it.

- **[2026-08-28][ES-6] Honesty vocabulary: two existing ones reused, none minted.** The STATE word is
  the atom's own (`ungated`); the SCORE-CELL absence follows the panels' house string **"not
  measured"** (`JudgeBenchPanel`/`StudiesPanel`/`AblationPanel`/`RetrievalBenchPanel`/`BenchmarkPanel`
  all spell a null mean that way), and the three-value discipline is ES-11's
  `SCORE_SCORED`/`SCORE_UNSCORED`/`SCORE_NO_CANDIDATES` reasoning rather than a fourth dialect —
  `delta` is `None` and not `0.0` when an arm never scored, because "the arms tied" and "one arm never
  scored" are the same number and different facts. **No collision with the concurrent ES-11 work**:
  its surface is `evals/optimize.py` + `tests/test_evals_optimize.py` + two on-disk JSON files, and it
  has ZERO frontend readers; this atom's is `evals/gate.py`, `learning/{proposals,inbox}.py`,
  `handlers`-free, `learningMeta.ts` and `LearningPage.tsx`. Disjoint, and `optimize.py` was not
  touched.

- **[2026-08-28][ES-6] Where the tests stop being real.** Everything is shipped code except the LLM:
  the real library + manifest, the real `load_scenario`, the real `Assertion.check`, the real subset
  selection, the real `RunPin`, the real `SpendMeter`, the real `candidate_files` rail, the real
  proposal store and the real inbox projection. `_ScoringMatrix` substitutes for `run_matrix` at the
  boundary `skills_bench`/`ablation` already make injectable, and models the agent as a PERFECT-RECALL
  reader of whatever the arm staged — the strongest honest assumption, and the one that makes a
  planted regression in the candidate TEXT observable without buying tokens. The child-side half of
  the seam (arm → spawn env → staging → throwaway-home refusal) is tested against the REAL code.

- **Falsifications (each mutated the LIVE line, `git grep`'d the mutation back, observed the red with
  its count, then restored from a file copy at the literal path — never `git checkout`; baseline
  `tests/test_evals_gate.py -n 0 --no-cov` = **43 collected, 43 passed**).**
  (1) `GATE_TIER = "gate"` → `"gate_MUTANT_F1"`: **3 failed, 18 passed, 22 errors** (the `gate_home`
  fixture's own subset assertion errors every test built on it) — the tier marker is load-bearing
  across 25 tests. (1b, the narrow one) `MAX_GATE_TURNS = 2` → `99`: **1 failed, 42 passed** —
  `test_a_tagged_scenario_over_the_turn_ceiling_is_excluded_with_a_reason`, `assert 'slow_probe' not in
  ['gate_probe', 'slow_probe']`. So "fast" is enforced, not asserted.
  (2) `ArtifactArm(label=ARM_AFTER, files=after_files)` → `files=before_files` (the candidate never
  reaches the child): **4 failed, 39 passed**, headed by
  `test_a_planted_regression_shows_a_score_drop_on_the_proposal_card` `assert 1.0 == 0.0`. **Vacuity
  partner `test_a_CLEAN_candidate_shows_no_drop` stayed GREEN** — so the drop comes from the plant and
  not from the plumbing.
  (3) Deleting the `meter.charge(...)` call: **2 failed, 41 passed** —
  `test_the_childs_reported_spend_is_charged_to_the_meter` (`assert 0.0 == 0.02`) and
  `test_the_budget_STOPS_the_sweep_and_names_what_did_not_run` (`assert False is True`). The charge is
  what makes the bound bind; **`test_an_unbudgeted_gate_is_ungated_not_unbounded` stayed GREEN**, so
  the two halves of clause 3 fail independently.
  (4) Blanking `summary()`'s `UNGATED_NOT_RUN` fallback: **2 failed, 41 passed** — the row projects an
  empty reason. **The clause-2 regression test stayed GREEN**, so "ungated renders" and "a scored
  proposal shows before/after" are independently falsifiable, as required.
  (5) FE: deleting `{gateLabel(row)}` from `LearningPage`: **3 failed, 10 passed** of 13 — the three
  page-render rails, while every direct-helper case stayed green (which is exactly the dead-code state
  a helper-only suite would have shipped).
  After each: `git diff --stat HEAD` empty, `git status --porcelain` empty.

- **Live drive of the user-facing copy (isolated temp home, real CLI entrypoint).** A real
  `skill_promotion.promote()` proposal, then `personalclaw eval-gate <pid>` through each ungated path:
  evals off → *"the eval substrate is off, so nothing re-ran — this is a judgement call on the evidence
  above, not on a score"*; evals on with no budget → *"no eval budget is set, so a gate run would have
  had no ceiling at all — set evals.default_budget_usd to get before/after scores"*; budget set with no
  model bound → *"a gate run could not be pinned to a model, and an unpinned score is not evidence
  (missing: model_fingerprint)"*. The inbox row projected each verbatim with `before`/`after`/`delta`
  all `None` and `pin: {}`. `--dry-run` prints the 24-cell preflight and, on a zero ceiling, says
  *"unset — this run would be UNGATED"* rather than printing `$0` (fixed during the drive).

- **[2026-08-28][ES-6] Deliberately NOT done.** (a) No HTTP endpoint. Every sibling eval RUNS from the
  CLI (`study`, `ablation`, `judge-bench`, `retrieval-eval`) and the `/api/evals/*` routes only READ
  artifacts; a gate run is tens of seconds of harness plus 32 model calls, and holding a request open
  for that would be a shape nothing else in the substrate has. So `personalclaw eval-gate` runs it and
  the card reads the persisted report — which also means **no new `json_error` code and no
  `HTTP_ERROR_CODES` row** (the learning handlers use the flat `{"error": …}` envelope anyway).
  (b) The gate is NOT called from `enqueue`: filing is a per-turn path and a synchronous gate there
  would stall it. (c) `prompt` and `template_diff` renderers — see the atom's `todo` note.
  (d) `optimize.py` untouched, on purpose, with ES-11 in flight in it; `propose_winner` still stuffs
  its scores into free-text prose, which is the obvious follow-up once that lands.

- **[2026-08-28][ES-6] DISCOVERY the full suite found, and the DEVIATION it forced.** The ten
  `sk_*` scenarios ARE `learning_bench.BENCH_TASKS`, and their `version` field is **shared between
  two contracts**: the library backfill's reinstall key AND `learning_bench.TASK_SET_VERSION`
  (`test_every_register_task_ships_as_a_scenario_with_deterministic_assertions` asserts they are
  equal). Tagging them for the gate tier forces both to move: without the bump the tag never reaches
  an existing home, with it the task-set assertion reds. Resolved by bumping `TASK_SET_VERSION`
  1 → 2, which is the honest direction — the field's own docstring says "the mechanical anchor is
  each scenario's `sha256` … which is why `task_set_fingerprint` reads the manifest rather than
  trusting this integer", and the tag moved every one of those hashes, so leaving the integer at 1
  would have it claim v1 subjects while the anchor says otherwise. Nothing real is invalidated: the
  only non-test reader stamps it on a report payload, no baseline is keyed on it, and the bench's
  documented ordinary state is "has never run". Two v1 literals in its suite were rewritten to
  derive from the constant — one of them, the parametrized `task_set_version=2` mutation, had
  become EQUAL to its own fixture after the bump, so the "same task_set_version" reproduction
  condition could no longer fail and that case was measuring nothing.

- **[2026-08-28][ES-6] One deliberate ceiling raise, with the decision recorded.**
  `test_audit_outcome_families.py`'s unclassified-outcome ceiling went 32 → 33 for
  `halted_on_budget`. The rail's own message offers "classify it into a family, into
  AUDIT_OUTCOME_SUCCESS, or raise this ceiling deliberately", and its docstring says it exists so a
  new word is *a decision someone makes*. This is that decision: a gate that stopped on its declared
  ceiling is the control WORKING — nothing was denied to a caller (not `denied`), the mechanism did
  not break (not `failed`), the sweep is incomplete (not a success) — so it stays unclassified for
  the same reason `expired` does, and putting it in a family would make the audit log assert a
  refusal or a fault that never happened.

- **Gate** (`PERSONALCLAW_HOME` unset). `make lint` clean (black **2196** files, isort **8.0.1** —
  the 9.x default venv reports 8 phantom errors on a clean tree — flake8, mypy **1083 source files**).
  Targeted `pytest --no-cov`: `test_evals_gate.py` **43/43**; the neighbour sweep
  (`test_evals_matrix_runner` + `test_evals_ablation` + `test_evals_skills_bench` + `test_evals_pinning`
  + `test_evals_store` + `test_evals_routes` + `test_config_roundtrip` + `test_evals_optimize` +
  `test_learning_inbox` + `test_proposals_contract` + `test_skill_promotion` + `test_evals_harvest` +
  `test_evals_studies`) **403 collected, 403 passed**, conftest real-home rail reporting
  `/Users/golani/.personalclaw unchanged by this run`. `npm run typecheck:web` clean;
  `web/src/pages/learning/gateColumns.test.tsx` **13/13**. `config/loader.py` **5619 lines before and
  after** — untouched. Python here is 3.14.7 (CI is 3.12), so `test_connector_pack.py`'s two
  version-only reds are pre-existing and not in the diff's blast radius.

- **Full suite, rebased onto `origin/main` (`c3a99dcf`): 5 failed, 28380 passed, 31 skipped,
  12 xfailed in 774 s — every failure pre-existing, none in the diff's blast radius, and the
  real-home rail reported `/Users/golani/.personalclaw unchanged by this run`.**
  `test_connector_pack.py` ×2 reproduce ALONE on this machine's Python 3.14.7 (the message names
  `importlib._bootstrap._find_and_load`, i.e. the 3.14 import-machinery change; CI runs 3.12).
  `test_subagent.py` ×3 (`TestSubagentReaper` ×2 + `TestSpawnWithApprovalCallback::…sel_rejection`)
  are the documented xdist SEL-mock leak: **all three are GREEN when `test_subagent.py` runs
  alone**, in the same 4½-minute run that reproduced the two connector-pack reds. A FIRST pass of
  the same suite pre-rebase had two more, both now closed: `test_agent_reference` was stale on the
  base commit `ca8e3c09` and fixed upstream by `05acba85` ("regenerate index.md — its route counts
  had drifted"), so the rebase resolved it — verified by rendering the reference against the current
  main tree, where 0 files differ; and that pass's real-home rail failure named a modified
  `security_events.jsonl`, which is **three foreign `personalclaw gateway` processes** alive on this
  machine, not this diff — the 43-test and 403-test targeted runs that exercise the new
  `gate._sel_log` writer both reported the rail clean, and so did the rebased full run.
- **[2026-08-27] OWNER RULINGS — `ES-7` and `ES-8`, two of eleven one-line scope calls that together gated 20+ atoms.**
  `ES-7`: **drop to three watched bindings.** No per-skill `subject` on `MatrixSpec` and no `eval_judge` member in
  `providers/use_cases.USE_CASES`. `subject`'s own comment enumerates template id / retrieval-arm set / judge fixture
  set / use-case — "skill" is not in that list, and widening a **shared** contract for one consumer is how a schema
  stops meaning anything. `eval_judge` is not a use case the router serves; minting one to satisfy a watchdog's count
  would be inventing a member to satisfy a test — the inert-control shape this plan itself keeps finding.
  `ES-8`: **narrow the gate to inputs that exist.** One rung vocabulary, and it is `guardrails/autonomy.py`. This
  program has already paid for a duplicate vocabulary once (`WF2LOO-16` reconciled a THIRD verdict dialect, with the
  recorded hazard that downstream work first mints a FIFTH); a second rung ladder is that hazard with a different noun.
  `ES-8` also needs decomposing — four §4 sub-scopes in one atom measures ~4 sessions. Full reasoning in each atom's
  `blocked_reason`.
## Execution log — ES-11 (independent re-verification: the reader clause is CLOSED) — §8 **COMPLETE**

- **[2026-08-27][ES-11] NO WORK WAS NEEDED — the clause was already on `main`, and the brief that sent
  me looking for it was one day stale.** I was briefed to implement the owner ruling's remainder ("an
  unscored candidate writes NO results row, and that absence must RENDER as 'unscored'"). It is already
  implemented and merged: `ce642b2e` is the search engine and `e97c5012` is *"ES-11 render an unscored
  optimize candidate as unscored, never 0.0"*. Recorded rather than silently re-done, because the honest
  finding here is that the atom's own header still said `**Status:** todo` with no date while its row
  said `🟡 impl landed` with no date either — so nothing in the tracking surface distinguished "not
  built" from "built, awaiting an owner flip", which is the same absence-versus-zero confusion one
  level up from the defect the atom fixes.
- **[2026-08-27][ES-11] Re-measured every claim rather than trusting the log above it.**
  `tests/test_evals_optimize.py` collects **53 tests, all passing** (matching the 47 → 53 the previous
  entry claims). `UNSCORED_OUTCOMES == {"scope_violation", "no_change"}` is closed and derived from
  `outcome`, for the round-trip reason the code documents at `optimize.py:526-536`: `_cmd_adjudicate`
  reads prior rows back through `_as_float`, which turns a rendered `None` into `0.0`, so a flag stored
  beside the score would have resurrected the zero on iteration 2. One correction to the previous
  entry's own numbers: it signs off with "`src/personalclaw/config/loader.py` untouched at 5900 lines" —
  measured on `main` at `ca8e3c09` that file is **5619** lines, so the figure was ~281 lines stale when it
  was written. It is untouched either way; the count is what was wrong, and a ceiling figure carried from
  memory instead of measured is how a stale one gets re-published.
- **[2026-08-27][ES-11] The consumer census the "fix one reader, leave another blank" hazard demands —
  all three are covered, and there is NO frontend surface.** `git grep` for importers of
  `personalclaw.evals.optimize` finds the module is reached only through
  `python3 -m personalclaw.evals.optimize <subcommand>` from the bundled template's three `bash` nodes.
  There is no dashboard handler, no API view and no `web/` component, so the clause's "render" is
  satisfied at the CLI/JSON surface and is proved there: (1) `.experience/index.json` via
  `LedgerRow.to_dict` → `score: None` + `score_state`; (2) `search.json` via `SearchOutcome.to_dict` →
  `results_state` + `candidates`/`scored_candidates`/`unscored_candidates`; (3) `_cmd_adjudicate`'s
  per-iteration verdict, which is what the report node reads as `{{nodes.search.output}}`, carrying the
  same two keys. Two further surfaces were checked and correctly need nothing: `propose_winner`
  interpolates `Score {outcome.winner_score}` but is unreachable unless a candidate was ADMITTED (and
  `admitted` is a scored outcome by construction), and `BestEver` already separates "no history" from
  "everything scored 0" via `rows_considered`, which is the same distinction one level out.
- **[2026-08-27][ES-11] Precedent confirmed, not re-chosen.** The vocabulary reuses
  `retrieval_bench`'s — `SCORE_NO_CANDIDATES == REASON_NO_CANDIDATES == "no_candidates"` — so there is
  no second spelling of "there was nothing to measure", and it is the third application of the same
  house rule that renders an unknown `outcome_grade` as `ungraded` rather than falling back to a grade
  (`ES-7`; cross-referenced from `PA.md:202` and `decisions.py:22`). No third vocabulary was minted.
- **[2026-08-27][ES-11] Deliberately NOT done.** No code was touched, so nothing was re-falsified —
  mutating a merged, already-falsified line to re-observe someone else's red is spend without a finding.
  `SearchOutcome.winner_score` still renders `0.0` when nothing was admitted, for the reason the previous
  entry gives (it is named by `admitted: false` + `winner_fingerprint: ""`, and the bundled template's
  report node declares `winner_score` a required `number`). `dag.json` untouched — the flip is the
  owner's. This change is docs-only: the dated status line the atom lacked, plus the file-level narrative
  that still claimed "entirely unstarted; all 11 atoms are todo" when four are `done`.
