# Verification & Judging

> Part of the PersonalClaw research-learnings library. Source-agnostic; distilled 2026-07-13 from a 95-source competitive-research corpus.

## Principles (the durable truths)

**Self-grading is structurally broken, not a wording problem.** An agent grading its own output confidently praises it even when plainly mediocre: the writing context is stuffed with the chain of self-persuasion, so the agent sees its reasons, not the result — "a model is its own output's best defense attorney." Multiple independent systems converge on the same conclusion: tuning a *standalone skeptical evaluator* is far more tractable than making a generator critical of its own work (an idea explicitly borrowed from GANs). You cannot ask an author to step outside its own perspective, but you can swap in an agent with entirely different instructions that carries none of the self-persuasion. Inside a loop, self-grading amplifies — each round the agent nods at itself and drifts from real quality.

**A loop's floor is its evaluator.** The generator's level decides what a system *can* produce; the evaluator's level decides what it *will not* produce. Empirically: a strong generator with a weak judge yields confident garbage; a modest generator with a sharp judge yields slow, reliable progress — "and the second is what compounds." Engineering effort belongs on the judge side.

**Completion must be externalized to the harness/engine, never the worker.** Neural nets are systematically overconfident, and same-model self-evaluation is structurally generous. "Done" = external gates (executed verification, independent review clean), never agent self-assessment. A worker may *request* a state transition; the engine executes the declared verification and flips the state irreversibly (pass-state gating). One measured case lifted real completion from 37.5% to 87.5% by moving verification ownership from the agent to the harness.

**Judges must act, not read.** A read-only evaluator judges "does this look right," not "does it run right." Judgment must be based on executed ground truth: run the tests and paste real output, drive the UI like a QA engineer, hit the API, inspect the artifact, screenshot the result. "Screenshots don't lie — if you can't see it working in a screenshot, it doesn't work."

**Independence is necessary but not sufficient — judges need explicit skepticism calibration.** An untuned independent judge "identifies real issues, then talks itself into approving anyway." Default stance must be doubt: assume the work is broken until proven otherwise, instructed to *find reasons to reject*. Separation without calibration produces the same nodding outcome one layer removed.

**Deterministic checks come before probabilistic ones.** Anything deterministic logic can solve never goes to an LLM — where you draw that line decides whether the system is reliable. Cheap mechanical validation (schema, length, existence, exit codes, ~0.3ms) runs before any LLM judge call; hard-coded gates the agent cannot skip (lint, tests, commit) interleave with LLM steps. Reliability comes from the quality of the constraints, not the size of the model.

**Verification exists to shorten mistake-to-discovery distance.** The cost of a mistake scales with the number of turns it survives; a long-running autonomous system is by construction a machine for maximizing turns. A wrong belief written to state is read back tomorrow *as established fact* and becomes load-bearing. Every judging mechanism — independent evaluator, human checkpoint, budget cap, proof gate — is justified by how much it shortens that distance.

**Evidence over claims, structurally.** Verified completion requires proof artifacts the runner checks for existence and integrity, not prose assertions. "Do not fabricate success; the external validator and evaluator decide outcomes" belongs verbatim in worker instructions — but the enforcement must be mechanical, because instructions alone are the weakest layer.

## Mechanisms (implementation-ready designs)

### Maker/checker separation contract
- Implementer and verifier must differ by **agent, session, model, or instructions** — implementer can never mark its own work done. Encode isolation as a dispatch vocabulary the engine enforces: `run-isolated-subagent` (fresh-context guarantee — required wherever context bleed invalidates the result) and `run-cross-model-subagent` (different model than parent; implies isolation). Same model + new instructions often keeps the same blind spots; swap models for unattended/high-stakes judging, and use the *stronger/deeper* model tier for judges while workers run on the cheap tier (a proven economics split: expensive model reserved for exactly the judge nodes).
- Stop conditions for goal-directed loops are judged by a **fresh model after each worker turn** (banking's maker–checker principle applied to termination) — never by the model doing the work. `self_judge: true` should be an explicit opt-in flag so the default is safe.
- Pre-announce the judge to the worker: every activation prompt ends with "your work will be reviewed by [X], which will demand [specific evidence]." Injecting the gate's acceptance criteria into the worker's prompt measurably raises first-pass rates — cheap and mechanical.
- Hermetic 3-role topology for self-verifying code: code writer, test writer, and QA arbiter run with **disjoint allowed context + leak checks**; the arbiter assigns blame (a fixture deliberately seeds a wrong generated test to verify blame lands on the test writer). Generated tests are never presumed correct — AI-written tests as the only check on AI-written code "puts a lot of faith into the AI-generated tests."

### Ground-truth independence (structural, not instructional)
- **Hidden validation commands**: the judge runs locked validation commands the worker never sees (`hidden_validation_commands` in the task manifest, separate from public `validation_commands`). Rubric ruling: `validation_passed = false` if any check was "added, removed, disabled, skipped, or modified" (including `--no-verify`) — worker tampering with validation config is automatic failure.
- **Structural information hygiene beats instructions**: "never read test traces" is enforced by never *writing* them where the worker can read (the data doesn't exist, vs. a rule saying don't look). Split evaluation into search-time tasks (visible during iteration) and held-out test tasks that run **only at run end** on surviving candidates — test-stage artifacts structurally never leak into the worker/proposer context.
- Judges re-execute the ground truth themselves: re-run the command, read the artifact, drive the browser — and **report actual output into the ledger** so verifier theater (sign-off while CI fails) is detectable after the fact.

### Act-capable adversarial judge (working prompt shape)
A field-proven evaluator prompt: "ROLE: Adversarial code reviewer. ASSUME: this code is BROKEN until proven otherwise. DO NOT praise. Find what fails. CHECK in order: 1. Does it run? (execute, don't read) 2. Tests: run them, paste real output. 3. Edge cases the author skipped. 4. Does behavior match the ticket? VERDICT: PASS only if every check holds. Otherwise REJECT + list each reason." Hook the evaluator to real tools (browser automation, shell, API clients) so its judgment basis is "I clicked the button, the page navigated, here is the screenshot" rather than "this code looks fine." Judges must "prove the code works, not confirm it exists."

### Typed verdict contracts
- Judge-flavored gates emit a **closed verdict enum** — e.g. `PASS | RETRY | ESCALATE | REJECT` — so the scheduler routes on data, never on parsing prose. Every conditional router's possible returns must be statically enumerated and fully mapped (label drift in free-text verdicts crashes orchestration mid-run).
- Per-issue verdict payload: `{description, expected, actual, evidence, fix_instruction, files_to_modify, severity}` — this is what flows back into a retry attempt, journaled per attempt.
- One canonical **finding schema** reused across every review/judge surface: `[Severity] Title` + Location / Problem / Why it matters / Recommended fix / Status(Open|Fixed|Needs decision), with a 4-level severity ladder (Critical = block, Major = should-fix, Minor = non-blocking, Nit = optional). Uniform findings make gate predicates ("no Critical|Major open") and outcome mining identical everywhere.
- Structured output with graceful degradation: bind a typed schema; on provider failure retry the *same prompt* as free text so the pipeline never blocks; render typed results to canonical markdown with stable headers so downstream parsing is **deterministic regex, not another LLM call**.
- Distinguish `verifier_absent` (None) from score 0: a result the verifier never produced (infra error/timeout) can never be promoted into a regression suite, is reported as TIMEOUT not FAIL, and pass-rate denominators count silently-dropped work as failure. Three-state node outcomes: `passed(score)` / `failed(score)` / `verifier_absent`.

### Rubric scoring with hard thresholds
- Judge output = fixed dimensions scored on a small scale (0–2 works), **evidence citation required per score** ("WCAG needs 4.5:1, measured 2.1:1"), terminal verdict Accept/Revise/Block plus required fields: missing evidence, required fixes, next review trigger.
- **Per-criterion hard thresholds, no averaging**: any hard failure fails the gate. A weighted-average rubric lets one catastrophic dimension hide behind good ones.
- Few-shot scored exemplars with detailed breakdowns in the judge prompt reduce score drift across runs. Criteria *wording* steers generator behavior in unexpected ways ("the best designs are museum quality" caused visual convergence) — review rubric text for steering side-effects.
- Calibrate scoring shape to the work's prescriptiveness: exact-procedure work → per-step compliance scoring; principle-level work → outcome rubric; single-fact checks → boolean.
- A minimal machine-checkable rubric DSL: weighted `file_phrase` checks (`{path, weight, required_phrases[]}`) + weighted `command` checks (`{weight, command, expect_exit_code}`); objective = hit_weight / total_weight. Scores instruction quality and executable behavior in one rubric, and per-task results feed change attribution.

### Deterministic pre-LLM verification tiers
- **Verification ladder with a no-skip invariant**: ordered tiers static analysis → runtime behavior → system-level/e2e; failing tier N blocks tier N+1; no tier may be skipped. E2E changes *behavior*, not just results — agents that know e2e is coming design for integration (one case: 5 defects, all unit-missed, all e2e-caught; 2s→15s runtime was acceptable).
- Cheap mechanical validation before every LLM judge: empty-output, JSON parse (auto-strip markdown fencing first — fixes most format failures with zero retries), required keys, length bounds, forbidden phrases. Most failures are catchable in well under a millisecond; invalid ⇒ skip the judge entirely.
- **Deterministic, LLM-free circuit breaker** over the run ledger, evaluated before each loop iteration: trip on max-iterations (default 10), same-error stagnation (identical failure signature N× consecutive, default 3), consecutive failures (default 5), or token-budget breach. Exit contract: 0 = continue, 2 = escalate to human — a typed `escalated` terminal state distinct from `failed`. Requires per-attempt ledger fields `{iteration, action, outcome: success|failure|noop, error_signature, tokens_used}`; the collapsed error signature is what makes stagnation detection possible.
- Classify every control on two axes: feedforward (rules, docs — steer before acting) vs feedback (tests, linters, review agents — observe after), and computational (deterministic, cheap, run on every change) vs inferential (LLM judge, slow, run at gates). Feedback-only agents repeat mistakes; feedforward-only agents never find out whether they worked. "Keep quality left": fast checks pre-commit, expensive checks post-integration, continuous sensors (drift/anomaly judges) outside the change lifecycle.

### Forbidden success modes (oracle denylists)
Every verification oracle should carry three parts: `success_condition`, `failure_signature`, and **`forbidden_success_modes`** — an explicit denylist of degenerate ways to "pass" (delete the failing test, ignore the exit code, stub the output, bypass the gate). The judge verifies none occurred before accepting. For reproduction-type work the semantics invert: `fail_reproduced` is the success outcome (exit 0), so node success conditions must be expression-definable, not hardcoded "exit 0". Replay outcome taxonomy: `pass` / `fail_reproduced` / `fail_new` / `inconclusive_env_error`.

### Consecutive-clean stop rules
- Review/fix loops terminate only when **two consecutive independent reviews find no Critical or Major issues** — a double-clean rule that is empirically a stronger, cheap-to-spec convergence criterion than single-pass approval, and directly expressible as a loop-exit predicate "N consecutive iterations satisfying P" (needs only a counter in the journal; rewind must reset it).
- Complementary abandonment ladder for non-converging loops: one hypothesis per iteration → revert on gate failure → abandon a hypothesis after 3 failures of the same hypothesis → halt after 5 consecutive iterations without improvement on the held-out score → write summary and surface findings to the human. Attempt caps ~3 per task before escalation is the convergent default across independent systems.

### Nodding-loop detection (statistical fake-check detector)
A check that has **never once said "no" across hundreds of turns is a statistical impossibility for real workloads and therefore proof no real check exists**. This is computable from the run ledger: flag any gate/judge with a 100% pass rate over N runs; surface per-template "said-no" metrics (rejects/passes, retries consumed) with a warning badge on templates whose gates never reject. Corollary promotion rule: an evaluator earns gating authority by first demonstrably stopping a real bad run — "a loop earns the right to run more agents by first demonstrating it can stop a single bad one."

### Judge noise guards
- **Single-run LLM-judge acceptance was statistically indistinguishable from noise; median-of-3 with an epsilon margin was the working fix** — accept a change only if the median of 3 judge runs beats the incumbent best by > 0.05. Expose as judge config: `judge_samples: 3, aggregate: median, epsilon: 0.05` for any accept/reject-grade decision.
- **Blind grading**: strip provenance the judge doesn't need — which arm of an experiment, which attempt of a retry loop produced the output. Knowledge of the favored arm inflates its scores; "attempt 3 of 3" biases toward mercy-passing.
- For human/multi-rater evaluation: two blinded raters + a third adjudicator; inter-rater agreement (Cohen's/Fleiss' kappa) below 0.6 downgrades claims to exploratory.

### Judge calibration priors (portable prompt content)
Anti-sycophancy priors that work as literal judge-prompt text, convergent across independent systems:
- "Default to finding 3–5 issues — 'zero issues found' is a red flag; look harder."
- "Perfect scores (A+, 98/100) are fantasy on first attempts." Normalize revision: "first implementations typically require 2–3 revision cycles; a B/B+ rating is normal and healthy" — this defuses the pressure to inflate.
- Default verdict is NEEDS WORK / REJECT; the judge must justify a pass, not a fail.
- **Anti-fence-sitting** for graded verdicts: use a 5-tier scale and "reserve the middle verdict for situations where the evidence on both sides is genuinely balanced; otherwise commit to the side with the stronger arguments. Be decisive and ground every conclusion in specific evidence."
- **Scope-anchored judging**: "do not add luxury requirements that weren't in the original spec" — judges drift toward invented criteria as readily as workers drift toward scope creep.
- Mandatory ground-truth commands run FIRST (screenshot, `ls` what's actually built, grep for claimed features), plus an AUTOMATIC-FAIL trigger list.
- Retry feedback constraint: "Fix ONLY the issues listed. Do NOT introduce new features."

### Judge hardening loop (the meta-mechanism)
Judges are tuned artifacts, not set-and-forget: read the judge's logs, find divergences from human judgment (a user overriding a judge-passed deliverable is a first-class `judge-divergence` event), patch the judge prompt, repeat — expect **3–5 tuning rounds** before a judge is trustworthy. Bias improvement energy toward judge/gate calibration over generator prompt tweaks: the sharp judge is what compounds. See [self-improvement-loops](self-improvement-loops.md) for the surrounding flywheel.

### Evidence & proof gates
- `required_artifacts: [glob]` on a work unit: the runner refuses to mark it complete until declared proof files exist in the workspace — an engine-enforced existence guard fully independent of the agent's self-report. Record per-artifact digests (path/size/sha256) in the ledger.
- **Evidence bundles** as the reviewability currency for unattended work: screenshot, video, contact-sheet, logs, metadata grouped under one schema-versioned manifest (per-file kind, content-type, size, SHA256, expiry), attached to the run/PR. Unattended runs need proof, not prose. Structure reviews as Summary / Before-After / Evidence.
- Deterministic validator scripts shipped *inside* the skill/SOP with a validate-until-clean loop (e.g. a recalc script run until JSON status = "success", zero error cells) — portable ground truth that travels with the procedure. Pair with numeric sanity bands (domain-plausible ranges) and provenance tagging: unsourced figures marked `[UNSOURCED]`, never estimated.
- **Checkpoint-not-presentation**: a complete-looking final artifact is evidence the workflow *ran to the end*, not that its gates passed. Render partial work as partial; never show a final-artifact preview while upstream gates are unverified — presentation biases acceptance.

### Baseline, reproduction, and regression classification
- **Baseline-before-edit as a hard invariant**: run the full validation set BEFORE any mutation and split results into passing / pre-existing failures / coverage gaps, so post-change failures classify as regression (fix/revert) vs pre-existing (note, continue) vs implementation-detail test (update + document). On resume, re-run the cheap baseline before advancing; broken baseline → repair first.
- **Reproduction-before-edit**: no code edits until a failing automated test, failing command, or documented manual repro with evidence exists — with a narrowly defined infeasibility escape ("depends on production data or infrastructure that cannot be simulated locally"; effort alone doesn't qualify). The repro artifact doubles as the validation artifact: must fail pre-fix, pass post-fix.
- **Dual acceptance gate** for iterative optimization: (a) harvested regression suite pass-rate ≥ threshold (0.8) — don't re-break what's fixed — AND (b) held-out score ≥ historical best-ever (monotonic ratchet) — don't overfit. Suites are *harvested, not authored*: a previously-failing task that now passes is re-run, and only if it passes again is it promoted into the suite ("promotion still requires a real verifier pass"; a verifier-never-ran result can never be promoted). This grows personal-scale suites (0→17 cases over 96 experiments) where authored golden suites are team-scale machinery.

### Change attribution: predict-then-verify
Every self-modification/fix proposal must declare `predicted_fixes[]` and `risk_tasks[]` up front. After evaluation, diff parent-vs-candidate per-task results into `fixed[]`/`regressed[]` and score each declared change: **EFFECTIVE** (all predictions fixed) / **PARTIALLY_EFFECTIVE** / **INEFFECTIVE** / **MIXED** / **HARMFUL** (risk realized, nothing fixed), plus `unattributed_regressions` — regressions nobody predicted or flagged, the scariest class. Verdict history per proposal source becomes a trust signal; HARMFUL verdicts auto-generate revert proposals. See [self-improvement-loops](self-improvement-loops.md).

### Failure-mode-classified retry (not blind retry)
Re-sending an identical prompt usually fails identically. Classify every failure into a typed enum (`schema_violation, constraint_violation, prompt_injection, token_overflow, hallucination, timeout, ...`) and inject a per-mode correction note into the NEXT attempt's prompt ("Return ONLY a valid JSON object...", "Do not invent citations, statistics, or names"). Security rule: injection-blocked and circuit-open failures are **never retried** (retrying an injection lets an attacker brute-force the guard — see [security-and-guardrails](security-and-guardrails.md)). Audit every attempt with one correlated record: `{audit_id, attempt, failure_mode, latency_ms, token_count, passed, strategy: first|mutated_retry|fallback}`.

### Escalation as a typed artifact with a closed decision menu
After N (=3) failed verify cycles, emit a structured escalation: full per-attempt failure history, root-cause hypothesis ("one-off or pattern? was the task properly scoped?"), and a **closed option menu** — reassign to a different agent / decompose into sub-tasks / revise architecture / accept with documented limitations / defer — plus impact fields and a named decision-maker. This makes human (or supervisor-stage) resolution one click instead of transcript archaeology. Route escalations to a needs-input surface with staleness alerts (>24h lingering re-notifies).

### Evaluation methodology for verifying the verifiers
When measuring whether structure/judging actually helps: paired within-task design holding model/tools/repo constant; confirmatory only if hypotheses+tasks+rubric are frozen pre-run; ≥30 tasks, k≥3 runs per (task, condition); fresh context per run. Metrics beyond pass-rate: **RP@k** (share of tasks where EVERY run passes — the variance/reliability metric that workflow structure most improves), CPR (clean pass: passed ∧ validation intact ∧ no regression ∧ no rework), NRR (no repair needed), RFR (regression-free, requires hidden checks passing). Ruling: work that would be reverted rather than repaired counts as *failed*, not rework. Automated power warnings when tasks<30 / runs<3 / CI half-width > ±0.15. For validating a skill/prompt intervention: three arms — treatment / nothing / **same-length trivial placebo** ("be careful about quality") — with blind grading; beating placebo, not vacuum, is the headline (if placebo ≈ treatment the intervention is theater; treatment *below* placebo means it's actively harmful).

## Patterns & compositions

- **Deterministic-gate interleaving**: LLM step → hard-coded gate the agent cannot skip (lint/tests/commit) → LLM fix step → hard-coded gate. The production pattern behind a measured 1,300 machine-written merged PRs/week (all still human-reviewed — "the human did not leave, but changed desks, from writing to reviewing").
- **Cheap-to-expensive verification funnel**: existence/schema validators (ms, free) → deterministic circuit breaker over the ledger → executable checks (tests, commands) → LLM rubric judge (deep model) → human gate. Each tier prunes before the next spends.
- **Dev↔QA loop with escalation**: assign → implement → evidence-collecting QA judge → PASS: next task, reset retry counter / FAIL(attempt<3): typed per-issue feedback, "fix only listed issues" / attempt=3: typed escalation artifact. Pipeline meta-metrics: first-pass QA rate target 70%+, average retries/task <1.5, gate pass rate 80%+.
- **Sprint contract negotiation**: before a work unit runs, generator and evaluator (or planner and user) negotiate what "done" means — scope / verification standards / exclusions — and the gate later enforces that contract. "Writing the done condition first has caught more scope drift than any prompt change." A generated plan where any stage lacks executable verification, or the whole lacks a machine-checkable stopping condition, is invalid (see [planning-and-decomposition](planning-and-decomposition.md)).
- **Debate → judge**: adversarial multi-role debate (bounded rounds) whose free-form dialogue is ephemeral; only the judge's structured verdict is written back to durable state. Judge gets the deep model and the anti-fence-sitting calibration. Parallel judge panels work with explicit orthogonality constraints ("stay strictly within this lens") — see [multi-agent-orchestration](multi-agent-orchestration.md).
- **Background QA with evidence**: stimulus (commit/event) → impact triage (skips recorded with one-line rationale, ledger-only) → user-level scenario generation (fresh state, fault injection, restart/resume arcs, resource-growth assertions) → real-environment execution → evidence bundle → proposed fix. Verification as a standing loop, not a phase.
- **CI/settle-window verification**: when watching external checks, poll (20s), enforce a timeout (1800s), a no-checks grace period (60s), and a **settle window** (20s) so late-registering checks can't fake green.
- **Judge-earned autonomy**: autonomy/parallelism graduates on verification evidence — evaluator has caught ≥1 real failure, N clean runs, budgets never breached — not on configuration. See [automation-and-triggers](automation-and-triggers.md).

## Anti-patterns & failure modes

- **Verifier theater**: a verifier that signs off without executing the check (or without reporting actual output) while CI fails. Mitigation is contract-level: judge must run in isolation, execute ground truth, and log real output.
- **The nodding loop**: no verification move at all; symptom is a loop that has never once said "no" to itself. Hasty builders install the visible-output moves (discovery, handoff) and skip the safety moves (verification, persistence, scheduling) — they cluster.
- **Self-grading / premature victory declaration**: "done by feel," completion claims without fresh evidence. Aggravated by context anxiety — some models rush and skip verification when the context window runs low (model-specific; harness must compensate).
- **Single-run LLM-judge acceptance**: indistinguishable from noise for accept/reject decisions on close calls. Always aggregate (median-of-N + epsilon) for decisions that mutate durable artifacts.
- **Talked-into-approving**: independent judges that find real issues then approve anyway. Independence without a skeptical default stance and hard per-criterion thresholds reproduces sycophancy.
- **Blind retries**: re-sending the identical failed prompt; and its evil twin, retry storms with no stagnation detection. Same failure twice → add a guardrail/check, don't retry and hope.
- **Averaging rubric dimensions**: lets a catastrophic failure hide behind high scores elsewhere; hard thresholds per criterion, any failure fails.
- **Grading with provenance**: judges who know which attempt/arm produced the output inflate the favored one.
- **AI tests as the only check on AI code**: the test writer needs the same maker/checker treatment as the code writer.
- **Fixing flakes with naked retries**: classify first; quarantine via explicit ticket + human sign-off, never "fix" flakes with code or blind reruns.
- **Presenting final artifacts mid-workflow**: biases acceptance; completeness of appearance is not gate evidence.
- **Free-text verdicts**: prose the router must parse; label drift crashes orchestration or silently misroutes. Closed enums + complete path maps.
- **Judge criteria as scope creep**: judges inventing "luxury requirements" beyond the spec — anchor judging to the negotiated contract.
- **Letter-grade inflation**: unanchored scales drift to A+; use graded ladders with calibrated priors ("B/B+ is healthy") or small numeric scales with evidence citations.

## Quantitative findings

- Engine-owned completion verification lifted real completion **37.5% → 87.5%** in one measured case; pass-state gating (harness flips state, irreversibly) is the mechanism.
- Single-run LLM-judge acceptance was **statistically indistinguishable from noise**; **median-of-3 + epsilon > 0.05** was the working fix (~$0.71–0.90 per 20-task validation run).
- Same model, same prompt: bare run 20 min/$9/broken vs planner+generator+evaluator harness 6h/$200/fully working; a later simplified harness (evaluator kept, sprints removed) 3h50m/$124.70 with QA rounds ~$3–4 each.
- Adding verification layers alone moved a team's success rate **20% → 60% → 80% → ~100%** (instructions → executable verification commands → progress files), model unchanged.
- Sprint contract + evidence-cited rubric turned 3–4 blind retry cycles (~45 min) into **1 iteration (~15 min)**.
- Judge hardening takes **3–5 tuning rounds** of diffing judge verdicts vs human judgment.
- Harness-as-optimization-target with dual gate (regression ≥0.8 + monotonic best-ever): benchmark score **0.560 → 0.780 (~40% relative)** over 96 fully-autonomous experiments on a fixed model; harvested regression suite grew 0 → 17 cases.
- Circuit-breaker defaults that work: max-iterations 10, same-error stagnation 3, consecutive failures 5; fix-loop attempt cap ~3; abandon a hypothesis after 3 failures; halt after 5 consecutive non-improving iterations.
- Double-clean stop rule: **2 consecutive** independent clean reviews (no Critical/Major).
- Pipeline health targets: first-pass QA rate ≥70%, avg retries/task <1.5, gate pass rate ~80% (100% is the nodding-loop signal, not health).
- E2E vs unit blind spots: 5 defects, all unit-missed, all e2e-caught; 2s→15s runtime acceptable.
- Mechanical pre-LLM validation runs in ~0.2–0.3ms; auto-stripping markdown fencing before JSON parse fixes most format failures with zero retries.
- Evaluation rigor floor: ≥30 tasks, k≥3 runs, κ≥0.6 inter-rater agreement, CI half-width ≤ ±0.15 — below any of these, findings are exploratory.
- External-CI verification: poll 20s / timeout 1800s / no-checks grace 60s / 20s settle window against late-registering checks.

## Open questions

- **Judge shelf life across model generations**: harness components compensate for era-specific weaknesses ("harnesses don't shrink; they move"); evaluator value is task-dependent and should be re-validated by one-at-a-time ablation after model upgrades — but no corpus source gives a principled re-calibration cadence beyond "monthly, ablate one component."
- **Nodding-loop threshold**: 100%-pass-over-N is clearly a red flag, but the corpus offers no principled N or expected base reject-rate per task class; too-low N false-alarms on genuinely easy templates.
- **Cost floor for act-capable judges at personal scale**: browser-driving evidence-collecting judges are proven at $3–4/round in team settings; whether every personal-automation gate can afford execution (vs. tiered static-first ladders) is unresolved.
- **How much context should a blind judge get?** Stripping attempt/arm provenance reduces bias, but judges also need the negotiated contract and prior findings to avoid re-litigating; the minimal sufficient judge context is uncharacterized.
- **Verifying the escalation path**: consecutive-clean and circuit-breaker rules produce typed escalations, but escalation failure (items lingering unseen >24h) is itself only mitigated by alerts — no source closes the loop on verifying that humans actually adjudicate.
- **Cross-domain outcome ground truth**: deferred, benchmark-relative outcome scoring (measure at horizon vs a baseline) is proven for finance; generalized metric providers for code/content/personal goals remain a design sketch — see [self-improvement-loops](self-improvement-loops.md).
