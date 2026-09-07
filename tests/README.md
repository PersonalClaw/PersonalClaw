# Tests

PersonalClaw uses pytest with pytest-asyncio. ~290 test files cover the gateway,
provider registry, auth modes, config schema, Slack flows, app SDK, and more.

## Running tests

```bash
# Full suite via the OSS Makefile target
make test

# Or invoke pytest directly
python -m pytest

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

## Conventions

- Test files: `tests/test_<module>.py`
- pytest-asyncio is configured in strict mode — every async test needs
  `@pytest.mark.asyncio`
- Use the `tmp_path` fixture for filesystem tests
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
