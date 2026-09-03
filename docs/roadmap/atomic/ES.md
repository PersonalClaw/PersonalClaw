# EVALUATION-SUBSTRATE — atomic plans

**Source plan:** [`EVALUATION-SUBSTRATE`](../plans/EVALUATION-SUBSTRATE.md)  
**Code:** `ES`  
**Source status:** proposed

Of 12 atoms, four are `done` (ES-1, ES-2, ES-4, ES-5) and eight remain open; ES-11's implementation has
landed in full and the atom is held only for the owner's `dag.json` flip. *(This narrative read "entirely
unstarted; all 11 atoms are todo" until 2026-08-27 — stale against its own row marks. It is the free-text
summary `test_roadmap_atomic_status_sync` deliberately does not rail, which is exactly why it drifted.)*
§1/§5/§6 (+§3.2 watchdog) are v2-independent and startable now against the existing eval/ package; §2/§4/§7/§8 and the E3 amendment are gated on WORKFLOWS-V2 (Run Ledger), WORKFLOWS-V2-LEARNING-FLYWHEEL (proposal queue/maturity/manifests), WORKFLOWS-V2-UNIVERSAL-PLANNING (UP-R6 approval gate), AUTONOMY-GUARDRAILS (model_calls.jsonl, SpendMeter), and FEEDBACK-SIGNAL (plan 58). Cuts follow the plan's own §-sections and its ~6-session map; the E1/E2/E3 amendment items are given their own atoms with explicit edges.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `ES-1` | ✅ | Shared eval substrate: store, experiment-matrix runner, subprocess isolation fix, config + SEL wiring | — | matrix runner (MatrixSpec/run_matrix) executes a scenario in a spawned child process with the PERSONALCLAW_WORKSPACE override in the child only (parent env never mutated); budget preflight, three-state passed/failed/verifier_absent aggregates, and per-cell artifact retention land under ~/.personalclaw/evals/matrices/; EvalsConfig round-trips through loader dataclass/load()/to_dict()/_EDITABLE_CONFIG; the evals/ store joins snapshot VALID_COMPONENTS/CORE_FILES and portability export (locked/ excluded); snapshot/restore round-trips it |
| `ES-2` | ✅ | RunPin + versioned scenario library migration (amendment E1) | `ES-1` | eval/scenarios/*.json migrate to versioned ~/.personalclaw/evals/scenarios/ over named seeded fixture homes; every matrix/study/gate run persists a RunPin (scenario_sha256, model_fingerprint, prompt_pack_sha256, config_snapshot_ref); re-running a scenario after a model rebind yields a different fingerprint row with the same scenario hash, and a run without a pin cannot be written to results.tsv |
| `ES-3` | ✅ | Retrieval eval harness with per-arm P@k/R@k ablation (both stores, read-only) | `ES-1`, `EXT:MEMORY-GRAPH-AND-VAULT:memory-side graph arm + push-context resolver arms for the memory ablation` | arm-masked runner reports P@5/R@5 per arm-mask for BOTH knowledge (HybridRetriever FTS5/graph/vector) and memory recall, run separately and read-only, from a personal-scale qrels set mined from surfacing/volunteer events plus a hand-label card; per-arm marginal contribution is a number and a dark-shipped arm gets its offline verdict before enablement; reports land in matrices/ via scorer:qrels |
| `ES-4` | ✅ | Judge benchmark harness → tier-recommendation table | `ES-1` | fixture set (real judged runs, deliberately-bad null probes, forbidden-success-mode cases) runs through the matrix over fixtures × judge tiers × judge_samples 1/3/5; the tier-recommendation table shows agreement-with-known-verdict, strong-vs-null separation, position-swap flip rate, cost and wall time per (rubric-class × tier × samples) with honest failure-mode notes, rendered on a Settings/Learning panel; rebinding a judge to the cheapest adequate tier is one user action on the Models panel |
| `ES-5` | ✅ | Pre-registered template A/B studies (the re-opened eval gate) | `ES-1`, `EXT:WORKFLOWS-V2:Run Ledger Slices 0-3 (§5 event table) for real-run input sampling + verdict events`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:proposal queue + LEARN-R2 harvested regression suite` | a flywheel template-diff runs a pre-registered study: k=5 paired old-vs-new over the harvested suite, immutable registration.json (rubric_sha256 pinned; mid-study rubric edit → invalidated), blinded median-of-3 position-swapped judging with agreement floor and judge_unreliable routing, locked/ checks executed supervisor-side in the child output workspace (never rendered into any worker prompt — regression-tested); verdict + agreement rate + per-run artifacts inspectable from the Learning page; a pass emits an evidence unit + results.tsv row, a fail auto-files a demotion/revert proposal |
| `ES-6` | ✅ | Loop-2 cheap gate subset + before/after score columns on self-modification proposals (amendment E2) | `ES-2`, `ES-5`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:self-modification proposal cards (GateOK arm)` | a curated dozen fast assertion-heavy scenarios re-run before a prompt/skill/routing proposal ships; a planted regression in a candidate skill edit shows a score drop on its own proposal card ({before,after,pin}) before the user accepts; gate-run cost is bounded and metered via SpendMeter, and a proposal with no gate run renders 'ungated' honestly (never blocks) |
| `ES-7` | ✅ | Harness ablation runner + skills bench + model-upgrade watchdog | `ES-1`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:LEARN-R9 retirement proposal kind + proposal queue`, `EXT:WORKFLOWS-V2:WF2-R13 consulted ledger event (for §3.3)` | the periodic ablation runner produces a keep/remove/lighten report for one component per cadence with measured on-vs-off deltas via child-process overlay toggling (live spec/config never mutated), and a no-delta component's report attaches as the ablation-grade evidence on a LEARN-R9 retirement proposal; the §3.3 skills bench replays consulted runs with a skill surfaced-vs-suppressed; the watchdog computes a model fingerprint on active_models.json changes, queues small-budget re-benchmarks, and emits exactly ONE digest notification, with per-fingerprint results.tsv baselines |
| `ES-8` | ⬜ | Trust-graduation ladder: trust records, graduation/revocation, rungs, attention accounting | `ES-1`, `ES-5`, `ES-7`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:LEARN-R11 maturity + LEARN-R16 verdicts + LEARN-R10 nodding-loop + proposal queue`, `EXT:WORKFLOWS-V2-UNIVERSAL-PLANNING:UP-R6 approval gate reads the trust record`, `EXT:WORKFLOWS-V2:Run Ledger attention events (gate_rejected, user_edited_mid_flight, needs-input continuation)` | a template reaches 'unattended' ONLY via flywheel-computed L3 + a passing unexpired §2 study + a human-accepted, SEL-audited graduation proposal; a HARMFUL LEARN-R16 verdict, failed study, nodding-loop flag, or watchdog fingerprint expiry revokes the trust record mechanically and the next run falls back to per-stage; rung chips render on template rows and the approval dialog; attention_events_per_run (plus resolved_after_secs ledger addition) trends on the Learning page, graduation proposals cite the trend, and a post-grant attention rise files a demotion signal |
| `ES-9` | ⬜ | Loop-3 live field metrics beside lab results + lab_field_divergence (amendment E3) | `ES-8`, `EXT:FEEDBACK-SIGNAL:plan-58 S1 👍/👎 + edit-before-approve records`, `EXT:AUTONOMY-GUARDRAILS:earned-autonomy ledger (autonomy_rungs.json) + SEL approval outcomes` | per-template/per-action-type 👍/👎 and edit-before-approve rates (query-computed, stored nowhere new) render beside Loop-1 lab score and Loop-2 gate status as one row per subject on the Learning tab; a subject whose lab score rose while its field trend fell is flagged lab_field_divergence and files a §4.2 trust-record demotion signal mechanically |
| `ES-10` | ✅ | Model bake-off from production-sampled inputs → per-use-case recommendation | `ES-1`, `ES-5`, `EXT:AUTONOMY-GUARDRAILS:model_calls.jsonl §2 attempt audit + SpendMeter` | candidate models run over real inputs sampled from model_calls.jsonl (or a temporary size-capped capture behind a redact()-gated, off-by-default, auto-expiring flag), scored by rubric-pinned comparative judging or task-native assertions; a per-use-case recommendation row lands as a proposal the user applies by rebinding active_models.json; sampled files live under the 0600 store, are excluded from portability export, and capture-flag flips are SEL-audited |
| `ES-11` | ✅ | Bundled optimize-harness template (budgeted search over PClaw's own artifacts) | `ES-1`, `EXT:WORKFLOWS-V2:v2 node taxonomy (loop node) + allowed_write_paths write-scope + breaker/budget machinery`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:LEARN-R16 change manifests + LEARN-R3 sidecar overlays + refiner tool-scoping`, `EXT:AUTONOMY-GUARDRAILS:SpendMeter` | a starter template in workflows/bundled/ completes a budgeted loop-node search over one PClaw skill/template: candidates are scope-checked by diff (frozen-region touch → scope_violation), scored against the dual gate (harvested-suite threshold AND monotonic best-ever from results.tsv), and halted by hypothesis_abandon_after/no_improvement_halt/budget_usd; the winner arrives as a proposal (template version diff or LEARN-R3 sidecar overlay) the human installs, and nothing live mutates during the search |
| `ES-12` | ✅ | Judge verdict integrity: verdicts must be answerable from the evidence shown (T04) | — | Judge prompt/schema constrains verdicts to an answerable set given the evidence slice; an answerability check rejects/flags verdicts referencing evidence not shown; regression fixture from the draft reproduces then passes; verdict records carry the evidence hash they judged. |

## Atom scopes

### `ES-1` — Shared eval substrate: store, experiment-matrix runner, subprocess isolation fix, config + SEL wiring

**Status:** todo

§1.1 evals store, §1.2 experiment-matrix runner, §1.3 isolation fix; §10 EvalsConfig 4-point wiring + snapshot/portability listing + SEL hooks

**Done when:** matrix runner (MatrixSpec/run_matrix) executes a scenario in a spawned child process with the PERSONALCLAW_WORKSPACE override in the child only (parent env never mutated); budget preflight, three-state passed/failed/verifier_absent aggregates, and per-cell artifact retention land under ~/.personalclaw/evals/matrices/; EvalsConfig round-trips through loader dataclass/load()/to_dict()/_EDITABLE_CONFIG; the evals/ store joins snapshot VALID_COMPONENTS/CORE_FILES and portability export (locked/ excluded); snapshot/restore round-trips it

### `ES-2` — RunPin + versioned scenario library migration (amendment E1)

**Status:** done

Amendment 2026-07-26 E1 — evals/pinning.py RunPin, Loop-1 scenario library, results.tsv pin

**DONE.** `eval/scenarios/` is deleted; the shipped set is `evals/library/*.json` (each declaring
`version` + `fixture_home`), installed into `~/.personalclaw/evals/scenarios/` by an idempotent,
data-keyed backfill (`evals/scenarios.py`) that never clobbers a local edit at an equal-or-higher
version. `personalclaw eval` and the matrix runner now read that one library. `evals/pinning.py`
owns `RunPin`; `run_matrix` computes it BEFORE any cell (an incomplete pin refuses the run rather
than burning spend), persists `matrices/<id>/pin.json` plus a per-cell pin carrying the model-axis
override, and `store.append_result` requires a complete pin — the ledger's five new pin columns are
written from the pin, not from the caller. Each cell now also gets its own `PERSONALCLAW_HOME`
(child env only) seeded from the scenario's named fixture. Measured: a rebind of `active_models.json`
between two runs yields two `model_fp` values under one `scenario_sha256` (`pin_diff`), and editing
a scenario turn yields two scenario hashes.

**Done when:** eval/scenarios/*.json migrate to versioned ~/.personalclaw/evals/scenarios/ over named seeded fixture homes; every matrix/study/gate run persists a RunPin (scenario_sha256, model_fingerprint, prompt_pack_sha256, config_snapshot_ref); re-running a scenario after a model rebind yields a different fingerprint row with the same scenario hash, and a run without a pin cannot be written to results.tsv

### `ES-3` — Retrieval eval harness with per-arm P@k/R@k ablation (both stores, read-only)

**Status:** todo

§5 (5.1 two targets, 5.2 corpus+qrels, 5.3 metrics+ablation, 5.4 shared machinery)

**Done when:** arm-masked runner reports P@5/R@5 per arm-mask for BOTH knowledge (HybridRetriever FTS5/graph/vector) and memory recall, run separately and read-only, from a personal-scale qrels set mined from surfacing/volunteer events plus a hand-label card; per-arm marginal contribution is a number and a dark-shipped arm gets its offline verdict before enablement; reports land in matrices/ via scorer:qrels

**PARTIAL (2026-08-27).** The `surfacing_events` blocker recorded on 2026-08-26 is CLOSED by
`LEARN-R4` (`7a7877a5`), and §5.2's source (a) for the knowledge store is now a REAL reader:
`mine_surfacing_qrels` takes a `used` candidate's `entity` as a positive and labels it
`mined:surfacing_events`. What does not yet hold is the DATA, and it is upstream: the only
production writer of `surfacing_events` is `skills.allocation`, which stores `kind="skill"` with a
SKILL NAME as `entity` — never a knowledge `item_id` — so source (a) labels nothing on the
knowledge arm until a surfacing arm that ranks knowledge ITEMS is instrumented
(LEARNING-FLYWHEEL §2.5's remaining three mechanical-`used` clauses). The memory arm's source (a)
is real (`mem_volunteer_events` via `volunteer_qrels`); its resolver arms stay out of scope
(no stored surface text). Atom stays `todo` — a P@5 mined from zero source-(a) labels would be a
rail that matches nothing.

### `ES-4` — Judge benchmark harness → tier-recommendation table

**Status:** done

§6 (generalizes loop/instrument.py:probe_judge across tiers)

**DONE.** `evals/judge_bench.py` crosses a fixture set × judge tier × `judge_samples` 1/3/5 ×
position through the SHARED `matrix.expand_cells`/`aggregate` and the same `matrices/<id>/`
artifact sinks, and publishes `table.json`/`table.tsv`/`recommendations.json` beside
`observations.json`. The judge vocabulary is reused, not re-minted: `judge_instruction` renders
the prompt, `parse_judge_json`/`validate_verdict`/`aggregate_samples` decide the cell (so a tier
is measured on the object a live gate hands it), `judge_calibration.CANARY_MIN_SEPARATION` is the
separation floor, and agreement-with-known-verdict is `DivergenceRecord.direction` — a fixture's
known verdict IS a human label, so the benchmark's agreement metric and the product's live one are
now the same arithmetic. The three adequacy floors are module constants, NOT config: a floor an
operator can lower is not a floor. `EvalsConfig.judge_agreement_floor` was deliberately not reused
— its documented consumer is ES-5's study verdict over a different metric.

**Measured while building, and load-bearing:** the axes are CONSUMED, and each has a test that
reds when it stops being. `judge_samples` decides the call count (recorded on the observation) AND
the aggregate verdict (1 PASS + 2 REJECT ⇒ samples=1 passes, samples=3 fails); `tier` decides the
use case through the engine's own `DEFAULT_MODEL_TIERS`. Three falsifications confirmed it:
ignoring the sample axis reds with `assert 1 == 3`; dropping the null's score in the separation
computation reds with `assert 4.0 == 0.0`; returning a fixed use case reds with
`assert 'passed' == 'verifier_absent'`.

**A real bug the drive-it-yourself pass found:** separation matched a null against *any* strong
fixture in the same rubric class, which pairs `conv-null-restate` with `conv-strong-tests` the
moment a class holds two pairs — the wrong difference under the right-looking name. `Observation`
now carries the DECLARED `counterpart_id` and the pairing is exact, regression-tested at
`test_separation_uses_the_DECLARED_counterpart_not_any_strong_in_the_class`.

**Unmeasured is never adequate.** A class with no strong/null pair reports `separation: None` and
is INADEQUATE; a class never position-swapped reports `flip_rate: None` and is INADEQUATE; a cell
whose judge produced no parseable object is `VERIFIER_ABSENT` with its protocol errors counted, not
a wrong answer; cost is `None` rather than `0.0` when nothing priced the call, so an unpriced model
cannot win "cheapest adequate tier" by looking free — `recommend` returns `cost_unknown` instead of
inventing an ordering. One missed forbidden-success-mode case disqualifies a tier on its own.

**Surfaces:** `personalclaw judge-bench` (with a `--dry-run` spend preflight — the full shipped
matrix is 180 cells / 540 judge calls), a READ-ONLY `GET /api/evals/judge-bench` (no route starts a
run), the Judge-tiers panel on the Learning page, and a one-click "Bind as default" on the
recommended use-case row of Settings → Models. No subprocess: `run_matrix`'s spawn exists to
contain `EvalRunner.run_scenario`'s `PERSONALCLAW_WORKSPACE` mutation, and a judge fixture never
constructs an `EvalRunner`.

**Two honest gaps, stated rather than implied.** (1) The shipped `starter` set is AUTHORED, not
harvested from this user's history — a shipped seed cannot be. It carries all three families §6
names across two rubric classes, and the growth path is real (a set of the same name in
`~/.personalclaw/evals/benchmarks/judge/` wins over the packaged one). (2) Mining past
`judge_divergence` ledger events into fixtures is NOT built here: `divergences_from_journal`
already exists, and queueing fixtures from `judge_unreliable` verdicts is ES-5's own criterion, so
building a second miner now would be the duplicate this atom's whole design avoids. Whether a REAL
model at a given tier clears the floors is unexercised without a live provider; every test stubs
the judge at the one named `JudgeCaller` seam.

**Done when:** fixture set (real judged runs, deliberately-bad null probes, forbidden-success-mode cases) runs through the matrix over fixtures × judge tiers × judge_samples 1/3/5; the tier-recommendation table shows agreement-with-known-verdict, strong-vs-null separation, position-swap flip rate, cost and wall time per (rubric-class × tier × samples) with honest failure-mode notes, rendered on a Settings/Learning panel; rebinding a judge to the cheapest adequate tier is one user action on the Models panel

### `ES-5` — Pre-registered template A/B studies (the re-opened eval gate)

**Status:** todo

§2 (2.1 pre-registration, 2.2 hidden locked validation, 2.3 blinded rubric-pinned judging, 2.4 verdict wiring)

**Done when:** a flywheel template-diff runs a pre-registered study: k=5 paired old-vs-new over the harvested suite, immutable registration.json (rubric_sha256 pinned; mid-study rubric edit → invalidated), blinded median-of-3 position-swapped judging with agreement floor and judge_unreliable routing, locked/ checks executed supervisor-side in the child output workspace (never rendered into any worker prompt — regression-tested); verdict + agreement rate + per-run artifacts inspectable from the Learning page; a pass emits an evidence unit + results.tsv row, a fail auto-files a demotion/revert proposal

### `ES-6` — Loop-2 cheap gate subset + before/after score columns on self-modification proposals (amendment E2)

**Status:** todo — 🟡 implementation landed 2026-08-28, atom stays open on ONE clause word

Amendment 2026-07-26 E2 — evals/scenarios/gate/ + §8.3 proposal emission score columns + proposal card FE

**[2026-08-28] 🟡 IMPLEMENTATION LANDED.** `evals/gate.py` owns the Loop-2 tier: the subset is
declared IN each scenario as `"tiers": ["gate"]` (not a `scenarios/gate/` subdir — the installed
library is a flat dir three readers glob and none descends), twelve shipped scenarios carry the tag
at a bumped `version`, and `install_library` records the tier in the manifest. Selection is
structurally filtered, not promised: over `MAX_GATE_TURNS` turns → excluded with a reason, and a
tagged scenario with no non-judge assertion → excluded, because the child runs
`EvalRunner(judge_enabled=False)` and a judge-only scenario reaches `total_assertions == 0` and
publishes a fabricated `1.0`. Scores come from the existing `run_matrix`/child (one matrix per
arm × scenario, `trial_count=1`) via a new `artifact_arm=` kwarg; the child stages the arm through
the SAME `throwaway_home()` refusal the ablation overlay uses (`overlay._cell_home` was promoted to
that public name — one answer to "may I write here"). The bound is enforced at the meter: no
positive `evals.default_budget_usd` ⇒ **ungated, not unbounded**; `check_run` before every cell and
`meter.charge` of the child's own reported spend after it, so the ceiling STOPS the sweep and names
what did not run. `Proposal.gate` + `inbox.Row.gate` carry `{before, after, pin}` to the card, which
renders "ungated" + a reason when nothing ran and "not measured" (the eval panels' own string) for a
null mean — never `0.0`, never a synthesized pin.

**What keeps the atom `todo`:** clause 1 says a gate re-runs *"before a prompt/skill/routing
proposal ships"*. Only the **skill** half is wired — `Kind.SKILL` is the one kind whose candidate
artifact is renderable through its own install rail (`skill_promotion.candidate_files` →
`SkillsLoader.create_auto_skill`). A `prompt` proposal's body is a JSON fence an accept re-parses into
`prompts/<name>.yaml`, and a `template_diff`'s is a typed ops list applied by
`_apply_accepted_template_diff`; neither declares a stageable file today, so both render an honest
`ungated` naming the kind. Extending `_CANDIDATE_RENDERERS` to those two closes the row.

**Done when:** a curated dozen fast assertion-heavy scenarios re-run before a prompt/skill/routing proposal ships; a planted regression in a candidate skill edit shows a score drop on its own proposal card ({before,after,pin}) before the user accepts; gate-run cost is bounded and metered via SpendMeter, and a proposal with no gate run renders 'ungated' honestly (never blocks)

### `ES-7` — Harness ablation runner + skills bench + model-upgrade watchdog

**Status:** todo

§3 (3.1 ablation runner, 3.2 model-upgrade watchdog, 3.3 skills/templates impact bench)

**Done when:** the periodic ablation runner produces a keep/remove/lighten report for one component per cadence with measured on-vs-off deltas via child-process overlay toggling (live spec/config never mutated), and a no-delta component's report attaches as the ablation-grade evidence on a LEARN-R9 retirement proposal; the §3.3 skills bench replays consulted runs with a skill surfaced-vs-suppressed; the watchdog computes a model fingerprint on active_models.json changes, queues small-budget re-benchmarks, and emits exactly ONE digest notification, with per-fingerprint results.tsv baselines

### `ES-8` — Trust-graduation ladder: trust records, graduation/revocation, rungs, attention accounting

**Status:** todo

§4 (4.1 division of labor, 4.2 trust record, 4.3 ladder rungs, 4.4 human-attention accounting)

**Done when:** a template reaches 'unattended' ONLY via flywheel-computed L3 + a passing unexpired §2 study + a human-accepted, SEL-audited graduation proposal; a HARMFUL LEARN-R16 verdict, failed study, nodding-loop flag, or watchdog fingerprint expiry revokes the trust record mechanically and the next run falls back to per-stage; rung chips render on template rows and the approval dialog; attention_events_per_run (plus resolved_after_secs ledger addition) trends on the Learning page, graduation proposals cite the trend, and a post-grant attention rise files a demotion signal

### `ES-9` — Loop-3 live field metrics beside lab results + lab_field_divergence (amendment E3)

**Status:** todo

Amendment 2026-07-26 E3 — Loop 3 live quality rendering + divergence flag feeding §4.2 demotion

**Done when:** per-template/per-action-type 👍/👎 and edit-before-approve rates (query-computed, stored nowhere new) render beside Loop-1 lab score and Loop-2 gate status as one row per subject on the Learning tab; a subject whose lab score rose while its field trend fell is flagged lab_field_divergence and files a §4.2 trust-record demotion signal mechanically

### `ES-10` — Model bake-off from production-sampled inputs → per-use-case recommendation

**Status:** todo

§7 (production sampling, privacy floor, matrix run, per-use-case recommendation)

**Done when:** candidate models run over real inputs sampled from model_calls.jsonl (or a temporary size-capped capture behind a redact()-gated, off-by-default, auto-expiring flag), scored by rubric-pinned comparative judging or task-native assertions; a per-use-case recommendation row lands as a proposal the user applies by rebinding active_models.json; sampled files live under the 0600 store, are excluded from portability export, and capture-flag flips are SEL-audited

### `ES-11` — Bundled optimize-harness template (budgeted search over PClaw's own artifacts)

**Status:** todo — implementation COMPLETE; every `done_when` clause holds. The engine landed in
`ce642b2e` and the last open clause (an unscored candidate must RENDER as "unscored") in `e97c5012`;
re-verified against `main` on 2026-08-27, including a census of all three read surfaces. Held only for
the owner's `dag.json` flip.

§8 (8.1 the loop, 8.2 stop conditions+budget, 8.3 propose-don't-write, 8.4 experience directory)

**Done when:** a starter template in workflows/bundled/ completes a budgeted loop-node search over one PClaw skill/template: candidates are scope-checked by diff (frozen-region touch → scope_violation), scored against the dual gate (harvested-suite threshold AND monotonic best-ever from results.tsv), and halted by hypothesis_abandon_after/no_improvement_halt/budget_usd; the winner arrives as a proposal (template version diff or LEARN-R3 sidecar overlay) the human installs, and nothing live mutates during the search

