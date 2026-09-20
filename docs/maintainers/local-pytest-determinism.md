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
