# Local pytest determinism measurements

This document records the reproducible evidence behind issue #684. The unit of
measurement is the set of failing pytest node IDs, not the cumulative failure
count printed while workers continue running.

## Baseline

- Source: clean `origin/main` at `2e007601b`.
- Runtime: CPython 3.12.13 in a worktree-local virtual environment.
- Installed command: `pip install -e ".[dev]"`.
- Pytest: 9.1.1.
- Local worker cap: `PYTEST_XDIST_AUTO_NUM_WORKERS=3`.
- Global home override: none, matching the repository's no-global-home test
  contract.

## Local and CI invocation delta

The local gate runs `.venv/bin/python -m pytest`, inheriting the repository
addopts: coverage, `-n auto`, `--dist worksteal`, and a 120-second timeout.
The worker cap resolves `auto` to three workers for these measurements.

The Full workflow does not run the same shape. Its matrix partitions the suite
into four disjoint pytest-split groups, disables coverage for each group, and
caps macOS groups at two xdist workers. It also sets
`PERSONALCLAW_SKIP_APP_BACKENDS=1`. The dedicated coverage job runs separately.

## Measurement protocol

Each full local run captures stdout and stderr to a distinct file under
`/tmp`. The report records all three verdict channels independently:

1. process exit code;
2. failing pytest node-ID set;
3. session-level rail output.

Two baseline runs establish the stable intersection and the unstable symmetric
difference. Suspected interference is then reproduced with the implicated node
IDs together and alone before any fix is attributed to it.

Run A used the repository's ordinary local invocation. Its session rail
reported four writes under the operator's real PersonalClaw home. Other lanes
were active on the same host, so those writes cannot be attributed to this
suite without further evidence. Run B therefore loaded a temporary external
pytest safety plugin that redirected only unscoped, default-home access outside
an active test protocol. It did not set a global `PERSONALCLAW_HOME`: that
experiment was rejected because it overrode tests' own isolated-home fixtures
and created nine artificial inbox failures.

Two other attempts are excluded from the measurement: a sandboxed run whose
socket and process denials caused 1,941 failures plus 47 collection errors, and
a run whose worktree disappeared at 67 percent. Neither is application
evidence.

## Baseline failing sets

Run A completed with 8 failures, 34,371 passes, 40 skips, and 14 expected
failures:

- four
  `tests/test_cron_expr_corpus.py::test_croniter_still_agrees_with_the_recorded_verdict`
  cases: `'0 9 * * * * *'`, `'0 9 * * * * 2026'`,
  `'0 9 * * * 30 *'`, and `'5-1 * * * *'`;
- `tests/test_sel.py::TestSingleton::test_sel_accessor`;
- `tests/test_terminal_handler.py::TestTerminalWsIntegration::test_ws_spawn_env_disables_shell_auto_update`;
- `tests/test_triggers_tools.py::TestRegistrationRefusesWhatCouldNeverRun::test_an_UPDATE_cannot_walk_around_either_refusal`;
- `tests/test_workflows_lifecycle_e2e.py::TestPerformance::test_a_deep_spec_also_schedules_quickly`.

Run B completed with 7 failures, 34,388 passes, 40 skips, and 14 expected
failures:

- the same four cron-corpus cases;
- `tests/test_inbound_mcp.py::TestTransport::test_rate_cap_returns_429_with_retry_after`;
- the same terminal test;
- the same trigger-registration test.

The intersection is 6 node IDs: the four cron-corpus cases, terminal, and
trigger registration. The symmetric difference is 3 node IDs: SEL accessor and
deep-workflow performance appeared only in A; inbound MCP rate limiting
appeared only in B.

## Proven mechanisms

### Installer-dependent cron grammar

The fresh local `pip install -e ".[dev]"` resolved croniter 6.2.4 because the
direct requirement allowed every major below 7. CI runs `uv sync --locked` and
resolved 2.0.7. Those parser majors disagree on the four corpus expressions and
on the trigger expression `5-1 * * * *`, producing five stable local-only
failures from one environment asymmetry.

The direct requirement now stays on major 2, matching the lock and the grammar
against which the shared frontend/backend corpus was measured. A rail joins
the project requirement to the lock metadata and locked package major.

### Cross-test SEL class replacement

Two snapshot-test helpers changed homes by reloading `personalclaw.sel`.
Modules collected earlier import `SecurityEventLog` by symbol, so a reload
replaced the public class object while leaving those imports pointing at the
old object. The later singleton accessor then failed an `isinstance` check
according to worker ordering.

The accessor passes alone. Running
`test_the_SEL_merge_is_SKIPPED_when_the_HMAC_KEY_DIFFERS` before the accessor
reproduces the failure. The helpers now reset the singleton state without
reloading the module, and a rail asserts that both helper paths preserve the
public class identity.

### Host login shell in a boundary test

The terminal test failed alone and timed out while executing the developer's
real `/bin/sh -l`. Its contract is the environment supplied at subprocess
spawn, not the contents or latency of host startup files. The test now
intercepts the real spawn boundary, drives the WebSocket through its live
handler loop, and directly asserts `DISABLE_AUTO_UPDATE=true`.

### Scheduler delay counted as workflow work

The deep-workflow assertion measured 104.1 ms in run A. The operation is pure
CPU work, but the test used elapsed wall time, so host scheduler delay counted
against the implementation during a wide parallel suite. Both lifecycle
performance checks now use process CPU time while retaining the original
100 ms bound.

### A second request after the observed rate refusal

The inbound MCP test's burst already observed exactly 20 successes followed by
three 429 responses. It discarded those responses and issued another request
solely to inspect `Retry-After`. At the configured one-token-per-second refill
rate, host scheduling could make that later request succeed. A standalone
probe confirmed a 429 with `Retry-After: 1` followed by a 200 after 1.1
seconds. The test now inspects the first refusal it actually observed.

## Rails and vacuity floor

Focused validation after the fixes passed 81 tests. The mutation harness then
temporarily restored five failure modes:

1. widened the direct cron requirement back across parser majors;
2. reloaded the SEL module in the fixture writer;
3. reloaded it in the fixture verifier;
4. changed the terminal spawn environment away from `true`;
5. made the rate-limit rail inspect successful responses.

All five mutations were caught and none survived. The original run-A
wall-clock performance failure is the empirical falsification for the
CPU-time correction; wall-clock scheduling noise is intentionally not
manufactured inside the test.

## Issue #6 and measured residual

The three named issue #6 failures are separate hidden debt, not a subset of
issue #684's observed local-gate failures. With expected failures forced to
run, they fail deterministically: inconsistent config-profile warning
semantics, a native-tool category fixture resolving the wrong repository
parent, and a stale PID-lifecycle double using a removed client method. They
remain expected failures and appear in neither baseline failing set.

The unexplained residual before the post-fix full run is the real-home session
rail: run A saw four changed paths, but concurrent lanes prevent honest
attribution. No test was pointed at or allowed to clean the real home during
this investigation.
