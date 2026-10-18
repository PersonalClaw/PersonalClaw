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

The OSS CI gate is reproduced end-to-end by:

```bash
docker build --target test .
```

That stage runs `black --check`, `isort --check-only`, `flake8`, `mypy`, and
`pytest` against the in-image source tree.

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

The `security marker` row of the CI contract table (`tests/test_ci_contract.py`)
fails on a required module without the marker, on a misspelt one, and on a
registry or named module that no longer exists, so a rename cannot shrink the
set. Planted violations prove the row can fail. A module may carry the marker
without matching a clause: the set grows by hand and never shrinks by accident.
A run in which the row ran ends with a `security marker census` line giving how
many modules must carry the marker, how many do, and how many of the test items
that run collected carry it.

## Conventions

- Test files: `tests/test_<module>.py`
- pytest-asyncio is configured in strict mode — every async test needs
  `@pytest.mark.asyncio`
- Use the `tmp_path` fixture for filesystem tests. `tests/conftest.py` overrides it to name each
  folder `t<N>-<test name>` from a counter, because pytest's own scans the whole temporary folder
  on every call and grows slower with every test a worker runs; for the same reason no
  function-scoped autouse fixture calls `mktemp()` (a row of `tests/test_ci_contract.py`)
- `--fixture-costs` writes `reports/fixture-costs.json`: each fixture's own setup time per call
  (median, p95, calls), merged across xdist workers and under `-n 0` alike. Teardown is not
  counted. An autouse fixture in `tests/conftest.py` stays under a 1 ms median setup there, or
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
