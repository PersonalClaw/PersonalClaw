# harness/ — the PersonalClaw self-development harness

**The harness, not the agent (and not the human's memory), owns verification.**

This is repo-inner dev infrastructure that mechanizes the project's verification *culture*
(campaign/LEDGER validation, auto-memory gotchas, hard-won bug-class knowledge) into
machine-checked, versioned, shared institutional knowledge. It lives beside `src/`,
`tests/`, and `scripts/`, and is **not** part of the shipped wheel (`pyproject` finds
packages only under `src/`).

It is built across the [Self-Verification plan](../docs/roadmap/plans/SELF-VERIFICATION.md)
sessions. Landed so far (Sessions 1–3):

```
harness/
  specs/
    rules/       # architectural invariants        (type: ai-coding-rule)
    scenarios/   # triage playbooks                (type: triage-scenario)
    tasks/       # per-fix / per-feature task specs (type: task)
  specs.py       # spec model + shape validation
  validate_refs.py  # live reference resolution (pytest --collect-only, AST fallback)
  profiles.py    # profile → concrete command mapping (fast/web/replay/full/scan)
  scanner.py     # static architectural-boundary scanner (7 checks, WHAT/WHY/FIX)
  selection.py   # touched-area → forced-profile mapping (diff-aware selection)
  diff.py        # git diff introspection (changed files/lines, fix-shaped commits)
  replay.py      # event-trace replay + metrics (dup rate, order, fanout, latency)
  baselines.py   # baseline gating (hard thresholds + drift; missing-scenario-fails)
  fanout_measure.py # token-matched fan-out vs single-agent verdict (sub-5pt == inconclusive)
  cli.py         # python -m harness  validate | explain | run [--diff] | scan [--diff] | replay
  traces/        # recorded NDJSON event traces + baselines.json
  exemplars/     # (Session 4) per-slice runnable exemplars
```

The recorder half lives in **core** (`personalclaw.trace_recorder`, env-gated by
`PERSONALCLAW_TRACE_DIR`, zero overhead when off) because core cannot import the harness.
The metrics/baseline half lives here. The FE-fold replay driver is
`web/src/harness/replayFold.ts` (+ `.test.ts`).

Later sessions add: resume-audit + MCP record/replay-as-fake-server (Session 4), and —
once the Workflows-v2 engine lands — the Self-QA Companion (§3).

## Event-trace replay (Session 3)

Turns the K42/K44/K45 stream-coalescer bug class into a *replayable* regression:

1. **Record** — set `PERSONALCLAW_TRACE_DIR=<dir>` and drive the gateway; taps at
   `SseRegistry.publish`, `DashboardState._broadcast`, `inbox_service._ingest`, and
   `mcp_client.call_tool` write redacted NDJSON (`{ts, stream, key, seq?, type, payload}`).
2. **Replay** — `python -m harness replay` folds backend-stream traces into metrics
   (`duplicate_event_rate`, `order_violation_count`, `reconnect_loss_count`,
   `event_fanout_ratio`, per-stream latency p50/p95) and gates them against
   `traces/baselines.json`; the vitest `replayFold.test.ts` folds chat/run traces through
   `coalesceReducers.ts` / `runFold.ts` and asserts the terminal state.
3. **Baselines** — hard thresholds (dup ≤ 0.005, order/loss = 0) + latency drift (+15%). A
   loosened threshold requires a rationale line; a missing scenario recording fails the
   gate (silent scenario drops are how baselines rot).

### Workflow journal-format gate (SV-5)

Two WF2 scenarios gate the **journal → SSE projection** format before any engine consumer
relies on it — `traces/workflow-journal-projection/` (a clean 3-node run) and
`traces/rewind-during-stream/` (a rewind bumps the epoch mid-stream; a stale in-flight event
must be dropped). Both are in `baselines.REQUIRED_SCENARIOS`, so **their absence from disk
fails the run outright** (not merely "one fewer scenario"). Each baseline pins a `fold` block
— the terminal state the pure Python **event-fold** (`replay.fold_workflow`, the mirror of
`web/src/pages/workflows/workflowFold.ts`) reconstructs. The fold compare is EXACT, not a
threshold: a journal/projection format change that breaks the event-fold law (a renamed event
kind, a dropped guard, a changed terminal state) changes the fold and fails the compare
(Success Criterion #4). No engine change was needed to record these — the workflow SSE tap at
`SseRegistry.publish` already covers the `workflow:<run_id>` key.

## Token-matched fan-out measurement (WF2WOR-9)

`harness/fanout_measure.py` implements WORK-CONTAINERS amendment (e): **before any widening of the
fan-out concurrency ceiling, a token-matched local comparison against the single-agent path on the
same work, with a sub-5-point delta reported as `inconclusive`.**

Why the tooling exists at all rather than an eyeballed A/B: the largest published fan-out win
(+90.2%) came with **~3.75x the tokens**, and that paper's own regression says token usage alone
explains **80%** of the outcome variance. An unmatched comparison measures the budget and credits
the topology. Meanwhile the field's noise floor exceeds most of its reported architecture deltas
(run-to-run variance 1-3 points; one scorer swap moved a result 79.0 -> 25.6; benchmarks run
n=24-100). So the module's job is to **decline**, and it has five verdicts, three of which are
refusals:

| verdict | meaning |
|---|---|
| `fanout_wins` / `single_wins` | \|delta\| ≥ 5 points, token-matched, above the within-arm spread |
| `inconclusive` | \|delta\| < 5 points, **or** a delta smaller than the arms' own spread |
| `not_token_matched` | spends differ by more than 5% (or an arm spent nothing) |
| `insufficient_trials` | fewer than 3 trials in an arm — n=1 cannot beat 1-3 point variance |

### The procedure

1. **Pick work both arms can do identically.** Read/analysis breadth is where fan-out has a measured
   case; coupled work is where it measured **+0.9 points for +44% cost**, so record which you chose.
2. **Run the fan-out arm** (a `compile_batch` batch — the leaf contract makes each leaf's objective,
   output format and boundary explicit, which removes format error as a confound). Record each
   trial's score in points and its **token spend**.
3. **Run the single-agent arm on the SAME work**, adding samples until its spend lands within 5% of
   the fan-out arm's. That is the token match: equal spend, not equal task count.
4. **Three trials minimum per arm.** Fewer is not a measurement.
5. Write the observations file and run it:

```bash
.venv/bin/python -m harness fanout-measure path/to/observations.json [--json]
```

```json
{
  "work": "rank 8 config files by risk (read-only analysis)",
  "arms": {
    "fanout": {"trials": [{"score": 62.0, "tokens": 41000}, ...]},
    "single": {"trials": [{"score": 60.0, "tokens": 40500}, ...]}
  }
}
```

6. **Record the verdict in the plan's execution log verbatim, `inconclusive` included.** The exit
   code is **0 for every honest verdict** — a non-zero on `inconclusive` would make the honest answer
   look like a broken run, and amendment (e)'s risk register names "a plan that only ever reports
   wins is not measuring" as the failure mode. Only a malformed observation file exits non-zero (2).

Do not lower `INCONCLUSIVE_BAND_POINTS` to make a result presentable. The band is the literature's
noise floor, not a preference.

## The scanner (Session 2)

Seven pure-static checks, each with a stable check-id a rule spec references via its
`scanner:` frontmatter. Every check is calibrated to produce **zero ERROR findings on a
clean HEAD** (a scanner that cries wolf erodes the whole harness):

| check-id | level | guards |
|---|---|---|
| `hook-provider-parity` | ERROR | action providers ⊆ `ALLOWED_HOOK_PROVIDERS` |
| `sse-event-registered` | ERROR | loop SSE event strings ⊆ FE `RUN_LIFECYCLE` |
| `config-four-points` | ERROR | a `_meta` field is mapped in `AppConfig.load()` |
| `app-sdk-boundary` | ERROR | `apps/` import core only via `personalclaw.sdk.*` |
| `destructive-test-isolation` | WARNING | tests touching home state carry `tmp_path`/`monkeypatch` |
| `fence-at-ingestion` | WARNING | external text → prompt is `fence_untrusted`'d |
| `no-naive-transcript-cut` | WARNING | transcript slices use the orphan-drop walk-back |

ERROR checks are exactly-derivable set invariants; WARNING checks are heuristics (advisory,
never a hard stop). `run --diff` scopes the line-based heuristics to the lines a diff
touched, so advisories are about *your* change.

### Diff-aware selection

`run --diff` computes the touched files vs the merge-base and **forces** the profiles that
guard them, independent of what the task spec claims — touching `web/src/pages/chat/`
forces `replay`; touching `config/loader.py` or `action_providers/` forces `scan`. The
spec author can add requirements; the diff can only add more, never remove.

## Usage

Run on the repo venv, from the repo root:

```bash
.venv/bin/python -m harness validate        # shape + reference-resolve every spec
.venv/bin/python -m harness validate --fast  # shape only (skip pytest collection)
.venv/bin/python -m harness explain T1.foo    # what commands/rules/tests a task owes
.venv/bin/python -m harness run T1.foo         # execute the task's required profiles
.venv/bin/python -m harness run --diff         # diff-aware selection (Session 2)
.venv/bin/python -m harness scan               # boundary scanner (Session 2)
.venv/bin/python -m harness fanout-measure obs.json  # token-matched fan-out verdict (WF2WOR-9)
```

## The three spec kinds

Each spec is markdown with a YAML frontmatter block. **Specs reference stable anchors only
— test node-ids, path globs, and scanner check-ids — never source line numbers.** Line
numbers drift on every edit (they were already all stale in the plan that spawned this
harness); node-ids and globs do not. `validate` enforces that the anchors resolve, which
is the spec-rot guard.

- **rule** (`type: ai-coding-rule`) — one architectural invariant. Frontmatter:
  `id, type, statement, appliesTo[globs], requiredTests[node-ids]?, requiredProfiles[]?,
  scanner<check-id>?, source, expiry_condition`. Body: why the rule exists + what
  compliance looks like, written for a coding agent.
- **scenario** (`type: triage-scenario`) — a diagnosis playbook for a symptom family.
  Frontmatter: `id, type, symptom, appliesTo, requiredRules[]?, acceptance[]`. Body: probe
  order + known causes + mitigations.
- **task** (`type: task`) — one fix/feature's contract. Frontmatter: `id, type, title,
  intent, touchedAreas, scenario?, requiredProfiles[], requiredRules[], requiredTests[],
  acceptance:{positive[], negative[]}`. **The `negative` acceptance clause is mandatory** —
  it is the "must NOT happen" half that prose LEDGER entries always drop.

## The same-PR rule (Session 2)

Every recurring constraint or fixed bug adds/updates a rule or scenario spec **in the same
commit** as the fix. This moves the "every fixed bug becomes permanent" memory-note habit
out of private, decaying auto-memory and into the versioned, greppable repo. Enforcement is
diff-aware and arrives with the scanner in Session 2.
