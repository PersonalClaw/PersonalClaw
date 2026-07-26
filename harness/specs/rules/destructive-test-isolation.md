---
id: destructive-test-isolation
type: ai-coding-rule
statement: >
  Any test that writes to config dirs, the local-models dir, or the credential store must
  isolate itself with a `tmp_path`/`monkeypatch` fixture that redirects `config_dir()` —
  never touching the developer's real `~/.personalclaw` home.
appliesTo:
  - tests/**/*.py
scanner: destructive-test-isolation
source: >
  A destructive test once deleted the developer's real bound L6 model because it ran
  against the real home instead of a tmp dir. Tests that mutate on-disk state are
  process-global side effects; without isolation they corrupt the real environment and
  flake under xdist (workers stepping on one another's dirs).
expiry_condition: never (test-isolation hygiene is permanent).
---

# Destructive tests must isolate the home directory

Tests that create/modify/delete files under a config dir, the local-models directory, or
the credential store MUST redirect those paths to a per-test temp dir. The idiom: a
fixture that `monkeypatch`es `config_dir()` (and any absolute path derived from it) to a
`tmp_path`. This is a hard CONTRIBUTING rule — it has bitten before (a real bound model
was deleted from a developer's actual home by an unisolated test).

## What compliance looks like

- Take `tmp_path` (and `monkeypatch`) as fixtures.
- `monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))` **or** monkeypatch the
  `config_dir` resolver so every path the code under test computes lands under `tmp_path`.
- Never hardcode or fall back to the real home; never assume the test's CWD is disposable.

The scanner check `destructive-test-isolation` flags a test module that references
`config_dir`/`local_models`/credential-store paths but carries no `tmp_path`/`monkeypatch`
fixture — a WARNING-level heuristic (it can't prove intent, but a match is worth a look).
