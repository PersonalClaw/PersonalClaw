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
- The `run-workflow` action provider is already in `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) — every periodic runner in this plan fires as a trigger→workflow, no allowlist change needed.
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
- **Action providers:** every periodic runner (ablation cadence, watchdog re-benchmarks, bake-off) fires as trigger → `run-workflow`, which is ALREADY in `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) — **no allowlist change needed**. If a future slice ships a dedicated `run-study` action provider instead, it MUST be added to that frozenset or hook create/update rejects it (restating the rule because this substrate is where such a provider would be born).
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
