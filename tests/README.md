# Tests

PersonalClaw uses pytest with pytest-asyncio. ~290 test files cover the gateway,
provider registry, auth modes, config schema, Slack flows, app SDK, and more.

## Running tests

```bash
# Full suite, one xdist worker per core, no coverage
make test

# The same, invoking pytest directly
python -m pytest -n auto --dist worksteal

# A bare pytest runs in ONE process with no coverage tracer: right for a file,
# hours for the whole suite. Add --cov=personalclaw to measure coverage.

# Specific file
python -m pytest tests/test_provider_registry.py -v

# Filter by keyword
python -m pytest -k provider_lazy_imports -v

# Re-run only the tests that failed last time
python -m pytest --lf -v
```

CI's gate is `.github/workflows/ci.yml`: its `lint` job runs `make lint` (black,
isort, flake8 and mypy), and its `test-shard` and `test-macos` jobs run the suite.
Locally, `make lint` and `make test` run the same checks. `make check` and
`make gate` run the change and merge tiers' jobs from `tools/ci/catalogue.toml`
the way CI runs them (`python -m tools.ci run-job <id>` runs one); a job this
machine cannot run, such as the other platform's suite, is named and the run
exits 2 (see "Local runs" in `docs/maintainers/ci.md`).

## What the suite downloads

Two kinds of test need files from the internet. A run fetches each once, through
PersonalClaw's own download path, and keeps it for the next run in pytest's cache
folder in the checkout (`.pytest_cache/d/`):

- `tree-sitter-grammars/`: the code map's grammars for this machine, about 25 MB,
  fetched by the first test that parses code, through the egress guard and
  checked against the language pack's manifest. The workers of a run share
  them: one fetches while the others wait for it.
- `bundled-chat-model/`: the default chat model's weight, 145 MB, fetched by the
  first test in `tests/test_bundled_model_gate.py` that drives a chat turn on it,
  with `scripts/fetch_bundled_model.py`, and checked against its sign-off record
  before any test reads it. No other test file asks for it.

Set `PERSONALCLAW_TEST_DOWNLOADS=<folder>` to keep them in `<folder>` instead. A
run reads what the folder holds and fetches only what is missing into it, so a
folder that holds both serves a run that fetches nothing, and one folder can
serve every checkout on a machine. `bundled-chat-model/` is only read once it
holds the weight; `tree-sitter-grammars/` must be writable, since the language
pack unpacks each grammar into it the first time a run parses that language.

## Bytecode cache (mutation testing is only evidence with this armed)

Every run points its bytecode cache at a fresh temp directory —
`tests/conftest.py` calls `pycache_guard.activate()` before it imports anything
under test. Nothing you have to remember, and nothing to add to a mutation
cycle: it applies to `make test`, a targeted `pytest tests/test_x.py::test_y`,
and CI alike.

It exists because CPython validates a `.pyc` against the source's
`(int(mtime), size)`, and a mutation-testing cycle defeats that validator by
construction: many mutations are same-length edits (`>=` → `<=`, `and` → `or`,
one identifier for another of equal length) and mutate → run → revert → mutate
lands inside one integer second. Both hold ⇒ the interpreter runs the
**previous** bytecode and the suite reports a result for code that is not on
disk, in the false-confidence direction (the mutation reads as *caught*).
`python -B` does **not** fix this — it stops the interpreter *writing* a cache,
not *reading* one — and `-p no:cacheprovider` is about `.pytest_cache`, not
`__pycache__`. Rationale, measurements and the alternative that was weighed:
`tests/pycache_guard.py`. Proof: `tests/test_pycache_guard.py`.

It covers stale bytecode only. A mutation run that *dies* partway through leaves
the mutation in the source, no cache involved, and this rail does not see that
(#2710).

This is not a claim that any past "N mutations applied, N caught" result was
wrong. It is that nothing enforced the invariant, so no past claim was
self-certifying. From here the run enforces the bytecode half.

## The `security` marker

A test module that proves a security or fail-closed boundary declares
`pytestmark = pytest.mark.security`, in a list beside any mark it already has.
The rules that protect such tests key on this marker, never on a folder: a
security-marked test is never rerun into green, quarantined or selected out.
`pytest -m security` runs exactly that set.

A census keeps the set complete. `tests/security_census.py` reads every test
module's source with `ast`, without importing it, and requires the marker on:

1. every module under `tests/security/`;
2. every module that imports a security registry or a fail-closed reader
   (`personalclaw.security`, `personalclaw.apps.permissions`,
   `personalclaw.automation_posture`, `personalclaw.safety_flags`,
   `personalclaw.browse.safety_script`), a function-level import included;
3. the families named by file: `test_apps_cannot_*`, `test_apps_see_only_*`,
   and the browser safety tests and real-home and credential guards listed one
   by one in `NAMED_MODULES`, each with what it proves.

The `security marker` row of the CI contract table (`tests/rails/test_ci_contract.py`)
fails on a required module without the marker, on a misspelt one, and on a
registry or named module that no longer exists, so a rename cannot shrink the
set. Planted violations prove the row can fail. A module may carry the marker
without matching a clause: the set grows by hand and never shrinks by accident.
A run in which the row ran ends with a `security marker census` line giving how
many modules must carry the marker, how many do, and how many of the test items
that run collected carry it.

## Tests that read the source tree

A test that reads the package, `web/src`, `docs` or the workflows reads them through
`tests/source_index.py` rather than walking them itself. Take the walk that matches what it
reads: `package_files()` for the package's `.py` modules, `package_any_files()` for every file
under the package (an app's `app.json`, a bundled workflow), `web_files()`, `docs_files()` or
`workflow_files()`, and `under(files, folder)` to keep one folder of a walk. Each walk fails if
it finds fewer files than its floor. `text(path)` and `tree(path)` read and parse a file once per
process, and every test shares the result, so never change a tree. A worker builds the index
before the first test whose module reads it, outside that test's timeout. The "no test walks a
source root itself" row of `tests/rails/test_ci_contract.py` fails on a test that walks one of these
roots on its own. A test that must walk one itself goes in `NOT_THROUGH_THE_INDEX` in
`tests/private_walkers.py`, by `file::function`, with the reason it cannot use the index.

## A test that needs another timeout

Every test runs under the suite's default cap, `--timeout` in `pyproject.toml`'s addopts. A
test that needs another one says so with `@pytest.mark.timeout(N)` (or a module's `pytestmark`,
or a conftest hook's `add_marker`), and lists that mark in `tests/timeout-marks.toml` with the
cap and a one-line reason: what the test does that takes the time, and what it was measured at
when that is known. The "every-timeout-mark-is-listed-with-its-reason" row of
`tests/rails/test_ci_contract.py` fails on a mark the list lacks, an entry whose mark is gone, a
cap that differs from its entry, and an entry with no reason. The cap is GitHub CI's: a busy
laptop is not a reason to raise one. `tests/timeout_marks.py` says how a mark's site is named.

## Rails: `tests/rails`

A rail checks the source tree rather than running the product: a census, a ratchet, a parity
or wiring check, a row of the CI contract table. Rails live in `tests/rails/` and run once per
CI run, in `ci.yml`'s `rails` job, and nightly on macOS in `breadth.yml`'s `rails-macos` leg. No
suite shard runs them. To run the same set the `rails` job runs:

```bash
python -m pytest tests/rails -n 4 --dist worksteal
```

`tools/ci/rails_census.py` decides which modules are rails, from their source: a module is a rail
when one of its tests reads the tree (the source index, `.github`, a walk or a parse of a
repository file) and none boots a gateway, a browser or a container, or reaches the network. A
local child such as `git`, and an in-process `TestServer`, are fine. `python -m tools.ci rails
census` prints each verdict with its reason. The "every rail lives in tests/rails" row of the
contract table fails on a rail outside `tests/rails/` and on a non-rail inside it. Two kinds of
module stay where they are only while they are one: a security-marked one stays whatever it
reads, and a module with both tree tests and behaviour tests is split, its tree half in
`tests/rails/test_<name>_tree.py` and what both halves use in a helper module beside the suite.

A rail names the repository from `REPO` in `tests/rails/rails_repo.py`, not from `parents[n]`
of its own file. A rail imports no behaviour test module: a name both need lives in a helper
module in `tests/`.

## Conventions

- A variation earns its own test only by the rule in
  [docs/maintainers/testing.md](../docs/maintainers/testing.md); otherwise it is a row of one
  `subtests` table test
- Test files: `tests/test_<module>.py`
- pytest-asyncio is configured in strict mode — every async test needs
  `@pytest.mark.asyncio`
- Use the `tmp_path` fixture for filesystem tests. `tests/conftest.py` overrides it to name each
  folder `t<N>-<test name>` from a counter, because pytest's own scans the whole temporary folder
  on every call and grows slower with every test a worker runs; for the same reason no
  function-scoped autouse fixture calls `mktemp()` (a row of `tests/rails/test_ci_contract.py`)
- `--fixture-costs` writes `reports/fixture-costs.json`: each fixture's own setup and teardown
  time per call (calls, median, p95 and total in wall time; median and total in this thread's CPU
  time, the steadier number on a loaded machine), and each test's setup and teardown totals,
  merged across xdist workers and under `-n 0` alike. An autouse fixture in `tests/conftest.py`
  stays under a 1 ms median setup there, or
  does its work only when a test needs it, with one exception: `_isolate_real_home_writers`
  makes the one comparison of `sys.modules` per test that it and `_restore_provider_registry`
  share, and its median was 0.82-1.33 ms per CI shard on Ubuntu (over 1 ms on three of four;
  0.43 ms on a macOS arm64 machine). Every other one was under 0.7 ms on every shard
- Use `monkeypatch` for config / environment overrides
- Mock subprocess providers (e.g. `AcpAgentProvider`) — never spawn real
  external processes in tests
- Group related tests in classes: `class TestFeatureName:`

## Smoke tests

- `tests/smoke_gateway.sh` — end-to-end gateway security smoke test (requires
  a running gateway on `localhost:10000`)
- `tests/smoke_sandbox.sh` — sandbox isolation smoke test
- `tests/debug_sandbox.sh` — on-host sandbox check (detected backend + wrapped `ls ~/.aws/`)

These are not run by `make test`; they are manual scripts for verifying live
behavior against a running gateway.
