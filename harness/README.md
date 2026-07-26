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
