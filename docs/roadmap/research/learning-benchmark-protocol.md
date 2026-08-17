# Learning Benchmark Protocol — does an approved skill make the next run better?

**Status:** PROTOCOL v1 — FROZEN 2026-08-16 (LEARNING-VISIBILITY T4.1, atom [`LV-6`](../atomic/LV.md)). Owner-signed; see §8.
**Question:** When a user approves a learned skill, does the next matching run measurably improve — and by how much, at what token cost?
**Executed by:** [`LV-7`](../atomic/LV.md) as an EVALUATION-SUBSTRATE study. This document is the measurement contract `LV-7` implements; it deliberately builds nothing.
**Reviewability rule:** the protocol is frozen BEFORE any run, so a disappointing result cannot retroactively edit the method. §6 lists what invalidates a result; §7 lists what must exist before a single number may be published.

---

## 1. The claim under test, and the three ways this measurement can lie

The claim is narrow on purpose: **a skill the user approved improves the next matching run.** Not "learning works", not "the flywheel pays for itself" — one paired comparison over a frozen task set.

Three failure modes are more likely than a wrong answer, so each gets a mechanical guard rather than a reader's caution:

1. **The arms were never actually different.** A comparison that labels two identical runs "skills-on" and "skills-off" produces a delta of pure noise and reports it as a finding. Guard: §3's arm-integrity check — every run must carry positive evidence of what it injected, and a run whose evidence contradicts its label is discarded, not averaged.
2. **The budget explains the delta, and the topology gets the credit.** This repo already refuses that shape once: [`harness/fanout_measure.py`](../../../harness/fanout_measure.py) will not name a winner unless both arms spent within `TOKEN_MATCH_TOLERANCE` of each other. The same rule binds here (§5).
3. **The delta is smaller than the noise.** Run-to-run variance on this kind of task set exceeds most architecture deltas in the literature. `INCONCLUSIVE_BAND_POINTS = 5.0` is not a tunable (§5).

The honesty rule that follows from all three, and the reason this doc exists before the runs: **the result is published at whatever magnitude it lands, including `inconclusive` and including a skills-off win.** A protocol that only ever reports wins is not measuring.

## 2. The task set

### 2.1 Schema

Tasks are scenario files in the versioned eval scenario library — the same files the matrix runner pins. There is no benchmark-specific format.

- **Location:** `$PERSONALCLAW_HOME/evals/scenarios/<id>.json`, installed by `personalclaw.evals.scenarios::install_library` (idempotent backfill; `personalclaw eval` calls it on every invocation).
- **Read shape** (`personalclaw/eval/scenario.py::_parse_scenario`): `name`, `description`, `dimensions[]`, `seed{preferences, projects, lessons[]}`, `sessions[].name`, `sessions[].turns[].user`, `sessions[].turns[].assertions[]{type, value, case_sensitive}`.
- **Assertion types** (`personalclaw/eval/scenario.py::AssertionType`): `contains`, `not_contains`, `regex`, `equals`, `judge`. Benchmark tasks use only the four deterministic types — `judge` is excluded by §6 because a scorer swap is one of the largest documented sources of result movement, and a judged score would make the arms' comparability depend on a model.
- **Pinning shape** (`personalclaw/evals/scenarios.py`): `version` (int) and `fixture_home` (str) are read by the library manifest and `RunPin`, **not** by `_parse_scenario`. Consequence, verified: `personalclaw eval` ignores `fixture_home` entirely — only the matrix path honours it. §3 therefore forbids running the arms through the bare CLI.

Every benchmark task declares `"dimensions": ["skill_impact"]` so `personalclaw/eval/runner.py::score_by_dimension` folds the set as one group.

### 2.2 The frozen register

Ten tasks, one per bundled skill family that admits a deterministic assertion. Selection rule, stated so it can be checked rather than trusted: **one task per skill under `src/personalclaw/skills/bundled/` whose procedure has an observable, non-judged outcome.** The 14 bundled skills were enumerated from a fresh home (§9, probe 2); four are excluded and named below, which leaves exactly ten. No task was chosen after seeing a result, because no result exists yet.

| Task id | Skill under test | What the task asks | Assertion shape |
|---|---|---|---|
| `sk_check_work` | `check-work` | Produce a short artifact, then verify it against a stated requirement list | `regex` over an enumerated verification list |
| `sk_task_project` | `task-and-project` | Create a project, add three tasks, report status | `contains` on each task title + the status line |
| `sk_knowledge_grounding` | `knowledge-grounding` | Answer a question the seeded knowledge store can support | `contains` on the grounded fact, `not_contains` on the ungrounded distractor |
| `sk_memory_discipline` | `memory-discipline` | After a correction, state what will and will not be persisted | `contains` on the persist decision, `not_contains` on over-capture |
| `sk_artifacts` | `artifacts` | Emit a versioned artifact rather than inline prose | `regex` on the artifact reference form |
| `sk_editorial_document` | `editorial-document` | Structure a multi-section document from unordered notes | `regex` on section ordering |
| `sk_delegation` | `delegation` | Split a two-part task and state the split before working | `contains` on both parts + the split statement |
| `sk_grill` | `grill` | Interrogate a supplied claim before accepting it | `not_contains` on bare acceptance, `contains` on the challenge |
| `sk_best_of_n` | `best-of-n` | Generate candidates, then select with a stated criterion | `regex` on candidate count + `contains` on the criterion |
| `sk_visual_output` | `visual-output` | Render structured output in the declared visual syntax | `regex` on the syntax envelope |

**Excluded, with reasons:** `loop-worker` (fires only inside a loop, not a chat scenario — a chat-shaped task would measure nothing); `pclaw-api` and `pclaw-features` (self-referential documentation skills — the assertion would test doc recall, not a procedure); `infographic-syntax` (same observable surface as `visual-output`; two tasks over one surface would double-weight it).

### 2.3 Freeze rule

The register above is **task set v1**. Adding, removing or editing a task mints **v2** and invalidates every v1 result for comparison purposes — v1 and v2 numbers are never plotted on one axis. The mechanical anchor already exists: each scenario's `sha256` is recorded in the library manifest (`evals/scenario_library.json`) and carried into every result row as `scenario_sha256` (`personalclaw/evals/store.py` `RESULTS_COLUMNS`). A row whose `scenario_sha256` is absent from the v1 manifest is not a v1 result.

**The scenario JSON files do not exist yet.** This section freezes the *specification* of the set; authoring the ten files is `LV-7`'s work. Four scenarios ship today (`context_accumulation`, `lesson_application`, `memory_recall_basic`, `smoke_test`, all `origin: shipped`) and none of them is a benchmark task.

## 3. Arms and pairing

**Two arms over identical work:** `skills_on` (the approved skill is available to surfacing) and `skills_off` (it is not). Everything else is held fixed by the pin.

- **Isolation.** Each trial runs as one matrix cell in a spawned child (`personalclaw/evals/runner.py::_spawn_cell`), with `PERSONALCLAW_WORKSPACE` and `PERSONALCLAW_HOME` set on a *copy* of the env (`evals/runner.py:158-159`) pointing at a per-cell temp dir the child seeds from the scenario's named `fixture_home`. The operator's home is never read and never written.
- **The bare CLI is forbidden for benchmark runs.** `personalclaw eval` isolates only `PERSONALCLAW_WORKSPACE` (`personalclaw/eval/runner.py::EvalRunner.run_scenario`), not `PERSONALCLAW_HOME`. Skills live at `config_dir()/skills` (`personalclaw/skills/loader.py:207`), so a CLI run would surface **the operator's real skills** into the arm. `personalclaw eval` is a development smoke path only. It also writes its report to `Path.cwd()/eval_results/`, which is not a pinned location.
- **Pairing.** `k = 5` trials per arm — the substrate's own default for a paired A/B study (`EvalsConfig.study_default_k`, `personalclaw/config/loader.py`), described there as "the smallest paired design that survives judge noise". `MIN_TRIALS_PER_ARM = 3` is the hard floor below which no verdict may be offered at all; five is the target, three is the refusal line.
- **The pin is the comparability claim.** `personalclaw/evals/pinning.py::compute_pin` records `scenario_sha256`, `model_fingerprint`, `prompt_pack_sha256`, `config_snapshot_ref`, `fixture_home`, `library_version`; `personalclaw/evals/store.py::append_result` raises `PinRequiredError` before touching the ledger if the pin is incomplete. Verified (§9, probe 4): a home with no model bindings yields an incomplete pin, so **an unbound home cannot record a benchmark result** — which is the correct behaviour, not an obstacle to work around.
- **Arm integrity is checked, not assumed.** Skill injection already emits a SEL record: `sel().log_tool_invocation(tool_name="skill_surface", metadata={"skills": [...]})` at `personalclaw/skills/loader.py:741`. The `skills_on` arm must show the task's skill in that record; the `skills_off` arm must show no `skill_surface` record naming it. A trial whose evidence contradicts its label is **discarded and re-run**, never averaged. §7 gap G4 is why this check cannot be performed today.

## 4. Metrics and the surface that produces each

Every metric names the code that produces it. Where nothing produces it, the row says so instead of describing a procedure nobody can run.

| Metric | Produced by | Reaches a durable row? |
|---|---|---|
| completion (pass / fail) | `ScenarioResult.passed` → `evals/child.py::result_from_scenario` `passed` → `_spawn_cell` maps to `PASSED`/`FAILED` | **Yes** — cell outcome + `verdict` column |
| score (assertion pass rate) | `result_from_scenario`: `passed_assertions / total_assertions` | **Yes** — `score_old` / `score_new` columns |
| wall time | `ScenarioResult.elapsed_secs` → cell summary `elapsed_secs`, retained in the cell artifact | Cell artifact only — **no `results.tsv` column** |
| tool-call count | `TurnResult.tool_calls` (`personalclaw/eval/runner.py:448`) | **No** — dropped twice (gap G3) |
| tool-call count (fallback) | SEL `tool_invocation` events, `source="eval_runner"` (`eval/runner.py:449`) | **No** — written into the per-cell throwaway home (gap G4) |
| tokens (the verdict's denominator) | `guardrails/audit.py::AttemptRecord` `tokens_in`/`tokens_out`/`dollars_est` in `config_dir()/model_calls.jsonl` | **No** — same throwaway home (gap G4) |
| arm integrity | SEL `skill_surface`, `metadata.skills` (`skills/loader.py:741`) | **No** — same throwaway home (gap G4) |

Two properties of this table are load-bearing and easy to misread:

- **Tokens are estimated, not provider-reported.** `AttemptRecord.estimated` exists precisely to say so ("dollars/tokens are heuristic, not provider-reported"). Any published token ratio must carry that word.
- **The ledger is not a source here.** `personalclaw/ledger/reader.py::run_totals` returns real `tokens` / `cost_usd` / `steps_*`, but it is keyed by workflow `run_id`. Benchmark tasks are chat scenarios and write no workflow-run ledger, so the ledger is the wrong reader for this study. Citing it would be the plausible-sounding mistake this protocol is written to avoid.

## 5. The verdict rule

The thresholds are not re-derived here. They are the constants in [`harness/fanout_measure.py`](../../../harness/fanout_measure.py), imported rather than copied:

- `INCONCLUSIVE_BAND_POINTS = 5.0` (`harness/fanout_measure.py:42`) — a sub-5-point mean delta is reported as `inconclusive`, including in our favour.
- `TOKEN_MATCH_TOLERANCE = 0.05` (`:48`) — arms whose total spend differs by more than 5% yield `not_token_matched`, which is the measurement declining a question it did not ask.
- `MIN_TRIALS_PER_ARM = 3` (`:54`) — fewer trials yields `insufficient_trials`. No verdict is offered.
- **Within-arm spread beats the delta.** A delta above the band is still `inconclusive` when one arm's own max-minus-min reaches the band. Published output always shows spread beside delta.

Verified executable (§9, probe 6): `python -m harness fanout-measure <observations.json>` computes exactly this, prints the spread and token ratio beside the verdict, and exits non-zero on a malformed observation file.

**The module is arm-name-bound, and that is a real constraint, not a formality.** `load_observations` requires arms literally named `fanout` and `single` and errors on anything else (§9, probe 5). The *posture* is reusable as a specification and the three constants are importable; the `compare()` / `load_observations()` pair is not reusable for a skills-on/off arm pair. `LV-7` must either generalise the arm vocabulary in that module — deliberately fixed today, so that is an owner call, not a drive-by edit — or write a thin sibling that imports the same constants. Relabelling `skills_on` as `fanout` to get a green run would be a lie in the output file.

## 6. Exclusions and invalidations

**Excluded from the task set:** `judge`-type assertions (scorer swaps move results more than most architecture deltas); anything requiring network egress (non-reproducible); anything whose assertion depends on wall-clock date; the four bundled skills named in §2.2.

**Excluded from a published number:** trials mapped to `VERIFIER_ABSENT` — a timeout, spawn fault, non-zero child exit or unparseable child output. `_spawn_cell` deliberately maps every infrastructure failure to `VERIFIER_ABSENT` rather than `FAILED`, so an absent cell is never silently counted as a skills-off win. Absent cells are reported as a count, not dropped from the report.

**Invalidates a result:** any change to the task set (§2.3); any change to `scenario_sha256`, `prompt_pack_sha256`, `config_snapshot_ref` or `model_fingerprint` between arms — the pin exists to make this checkable rather than promised; an arm-integrity contradiction (§3); editing this protocol after runs have started. A mid-study protocol edit invalidates the study, exactly as a mid-study rubric edit does in EVALUATION-SUBSTRATE.

## 7. Named gaps — what must exist before a single number is published

This protocol is executable in its *design*: every surface it names exists and was exercised (§9). It is **not runnable end-to-end today**, for five specific reasons. Each is stated as a precondition on `LV-7`, not as a caveat on the method.

**G1 — There is no way to run a skills-off arm. This is the blocking gap.** Three candidate levers were probed and all three fail:

- `skills.max_triggered` cannot be zeroed. `SkillsConfig.__post_init__` clamps any value below 1 to 1 with a warning (`personalclaw/config/loader.py:1817-1819`), verified at runtime (§9, probe 1).
- An empty fixture home is not a skills-off home. Constructing `SkillsLoader` calls `_ensure_builtin_skills(self._dir)` (`personalclaw/skills/loader.py:293`), which syncs `src/personalclaw/skills/bundled/` (`skills/loader.py:41`) into the home. A brand-new empty home holds **14 skills** immediately afterwards (§9, probe 2).
- `feedback.suppressed_producers` is not a switch. It is derived from measured producer accuracy (`personalclaw/feedback.py::suppressed_producers`) and reaches surfacing only as `("skill_synthesis", key)` pairs (`personalclaw/skills/surfacing.py:304`), so it cannot suppress a bundled skill on request.

No env override exists either. **The lever is already designed and already owned elsewhere:** EVALUATION-SUBSTRATE §3.3 (atom [`ES-7`](../atomic/ES.md)) specifies replaying runs "with the skill surfaced vs suppressed (`arm_mask`)". `arm_mask` appears nowhere in `src/`, `harness/` or `tests/` — it is unbuilt. `LV-7` must **consume** `ES-7`'s `arm_mask`, not grow a second toggle beside it (one owner per mechanism, [AGENTS.md](../../../AGENTS.md) §"Shared conventions"). `LV-7`'s atom row currently declares `EXT:EVALUATION-SUBSTRATE:S1-2` only; §3 is a later ES session, so that dependency is understated. Recorded as a DISCOVERY in the LEARNING-VISIBILITY execution log rather than edited here — the roadmap is owner-maintained.

**G2 — A declared arm axis changes nothing.** `MatrixSpec(axes={"arm": [...]})` expands correctly: an `arm` axis with two values at `trial_count=3` yields six cells carrying distinct `arm` coords (§9, probe 7). But `evals/child.py` reads exactly one coordinate — `coords.get("model")` at `evals/child.py:162`, the only `coords.get` call in the file. Every other axis value is recorded in the cell coords and honoured by nothing. Declaring an `arm` axis today produces six identical runs labelled two ways: a fabricated comparison that would look like a real one in every artifact. `LV-7` must make the child honour the arm coordinate, or the axis must not be used.

**G3 — `tool_calls` is captured and then dropped twice.** `TurnResult.tool_calls` is populated per turn (`eval/runner.py:448`) and survives neither aggregation boundary: `ScenarioResult.summary()` and `evals/child.py::result_from_scenario` both omit it (§9, probe 8). `RESULTS_COLUMNS` has no column for it. The plan's declared metric set (completion, tool_calls, wall_ms) is therefore two-thirds unreachable from the matrix path. The fix is small and local — carry a count through both functions — and is `LV-7`'s.

**G4 — Per-cell evidence dies with the cell's home.** The cell *artifact* directory lives under the real home (`evals/matrices/<id>/cell-NNNN/`) and survives. The cell's `PERSONALCLAW_HOME` is a `tempfile.TemporaryDirectory` and does not. Three things the protocol depends on are written into that doomed home: the SEL log (arm integrity, §3; tool-call fallback, §4) and `model_calls.jsonl` (the token denominator, §5). The good news is that the token accounting the honest verdict requires **already exists per-cell** — `AttemptRecord` carries `tokens_in`, `tokens_out`, `dollars_est`, `latency_ms` — it is merely thrown away. `LV-7` must fold both files into the cell result payload or the cell artifact before the child exits.

**G5 — `run_matrix` has no production caller.** Its only callers in the tree are `tests/test_evals_pinning.py` and `tests/test_evals_matrix_runner.py`. There is no CLI, no handler, no script. `personalclaw eval` does not reach it (and must not be used for benchmark runs — §3). A one-command reproduction is `LV-7`'s `done_when`; today there is no command. Relatedly, `evals.enabled` and `evals.study_default_k` appear in the PATCH allowlist (`dashboard/handlers/core.py:643-644`) and are read by nothing under `personalclaw/evals/` — `LV-7` should read `study_default_k` rather than hardcode `k`, and should not instruct an operator to flip a switch that currently changes nothing.

**Consequence, stated plainly:** until G1 and G2 close, no skills-on/off number exists to publish, and a number produced without them would be a labelling artifact. G3–G5 bound what can be *reported* rather than whether a run happens.

## 8. Publication, sign-off and reproduction

- **Publish regardless of magnitude.** `inconclusive`, `not_token_matched` and a skills-off win are all publishable outcomes and are published with the same prominence as a win. The verdict is published with its `notes`, its within-arm spread and its token ratio — never the verdict alone.
- **Report absent cells.** The `VERIFIER_ABSENT` count is part of the result, not a footnote.
- **Say "estimated" about tokens** (§4).
- **Attribution.** LEARNING-VISIBILITY's soul guardrail is honest counts only: no time-saved or productivity math is derived from this benchmark. It measures completion, score, tool calls and wall time. Anything else would be invented.
- **Owner sign-off (owner task 2):** this protocol and the §2.2 register are signed off as v1 on 2026-08-16, including the commitment to publish a modest or negative result. The publish decision was made here, before the measurement, which is the only point at which it can be made honestly.
- **Reproduction (V4).** An independent re-run reproduces within the stated variance when it uses the same task-set version, produces the same `scenario_sha256` set, records a pin whose `prompt_pack_sha256` and `config_snapshot_ref` match, and lands a verdict of the same class. A re-run that changes the verdict class is a finding to publish, not a run to discard.

## 9. Probes run while writing this protocol (2026-08-16)

Each measurement the protocol prescribes was exercised once against the code on `main`, in throwaway homes. Full outputs are in the `LV-6` execution-log entry in [LEARNING-VISIBILITY](../plans/LEARNING-VISIBILITY.md); summarised here so a reviewer knows which claims are measured and which are read.

1. `SkillsConfig(max_triggered=0)` → `1`, with a warning. Also `-5` → `1`. (G1)
2. Fresh empty `PERSONALCLAW_HOME` + `SkillsLoader()` → 14 skills in `skills/`. (G1)
3. `install_library()` → 4 shipped scenarios, all `origin: "shipped"`, all `fixture_home: "empty"`. (§2.3)
4. `compute_pin("lesson_application")` → complete except `model_fingerprint`; `is_complete() == False` in an unbound home. (§3)
5. `harness fanout-measure` on arms named `skills_on`/`skills_off` → refused, exit 2. (§5)
6. The same observations with `fanout`/`single` → `fanout_wins`, printing delta, band, spread and token ratio. (§5)
7. `_expand_cells(MatrixSpec(axes={"arm": [...]}, trial_count=3))` → 6 cells with distinct `arm` coords; AST census of `evals/child.py` → exactly one `coords.get` call, for `"model"`. (G2)
8. `result_from_scenario` over a `ScenarioResult` whose turn recorded three tool calls → no tool-call field in either the scenario summary or the cell result. (G3)

**Not run here, and why:** no paired arm was executed. It cannot be — G1 means there is no skills-off arm to run, and executing one arm twice under two labels is the exact artifact §1 exists to prevent. No model was called, so no cost was incurred and no provider-reported token count was observed.
