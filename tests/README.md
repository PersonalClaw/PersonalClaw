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
