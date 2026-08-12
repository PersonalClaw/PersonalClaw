# CI-RELEASE-ENGINEERING — atomic plans

**Source plan:** [`CI-RELEASE-ENGINEERING`](../plans/CI-RELEASE-ENGINEERING.md)  
**Code:** `CRE`  
**Source status:** done, with one strand REOPENED by `CRE-8` (see below)

`CRE-6` declared the test-isolation strand closed. It was closed for the failure it was
looking at — cross-test bleed between xdist workers — and open for the one it was not: a
run with no home patch still resolved its home to the **developer's real
`~/.personalclaw`** and wrote there. Measured, not inferred: one `-k` selection appended
44,402 bytes to the user's real `security_events.jsonl`; a full suite touched 26 real-home
families, net-**shrank** that audit log by 151,963 bytes (the retention prune ran against
it) and deleted 123 files. `CRE-8` closes that strand and adds the rail that would have
caught it. The lesson is worth keeping: `CRE-6` removed the global `PERSONALCLAW_HOME`
env rail for good reasons and fixed four real root causes, but it never added a detector
for the class it was fixing, so the residue was invisible for four months.

CI-RELEASE-ENGINEERING is otherwise fully shipped: committed flake8 standard + whole-tree reformat (mypy 152→0), core ci.yml (lint/test/web/rails) + full.yml matrix, apps-repo ci.yml, release.yml (build/pypi/pypi-client/images/notes/attest via Trusted Publishing), uv.lock + Dependabot + SBOM + coverage badge, and a test-isolation root-cause pass that fixed 4 real product bugs the gate surfaced. Only owner real-world confirmations (GHCR packages→public on first push, optional main branch protection) remain — no agent-executable code left.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `CRE-1` | ✅ | Formatting standard + whole-tree lint/type green (S1 premise correction) | — | setup.cfg [flake8] committed (100-char, E203/W503/E704 ignores, dist/build/venv excludes); black 26 + isort whole-tree reformat in an isolated style: commit; flake8 residue 588→0; mypy 152→0 (all real fixes bar #CI-1/2/3 documented inline-ignores); make lint exits 0 |
| `CRE-2` | ✅ | Red-test triage + core ci.yml/full.yml + README badges (S1) | `CRE-1` | known-red groups fixed-or-xfail'd per C2 (xfail_strict untouched, one filed issue per annotated group #6/#7/#8); .github/workflows/ci.yml authored with jobs lint/test/web/rails + concurrency cancel-in-progress; full.yml matrix {3.12,3.13}×{ubuntu,macos} skeleton; README CI badge renders; job ids match C1; deliberate lint error + test failure turn the run red |
| `CRE-3` | ✅ | Apps-repo CI + core rails mount (S2) | `CRE-2`, `EXT:PROVIDER-BOUNDARY-COMPLETION:residue rail test test_provider_boundary_residue` | apps-repo .github/workflows/ci.yml has manifest-validate (all app.json parse via core apps/manifest.py), tests (core installed from git, vendor SDKs uninstalled, per-bundle to avoid basename collisions), boundary (SDK-only import lint); scripts/validate_manifests.py + check_sdk_boundary.py exist; core ci.yml rails job mounts the plan-32 residue sweep unguarded; corrupting a manifest or adding a core-internal app import turns the respective job red |
| `CRE-4` | ✅ | Release pipeline release.yml (S3) | `EXT:DISTRIBUTION:wheel bundles web/dist (packaging change is DISTRIBUTION S1)` | release.yml on tag v* has build (npm build web → python -m build; wheel contains personalclaw/static/dist/index.html), pypi + pypi-client (Trusted Publishing, no token secrets, separate environment: release / release-client per unique PyPI publisher tuple), images (buildx linux/amd64+arm64 GHCR via GITHUB_TOKEN), notes (CHANGELOG section verbatim), attest (attest-build-provenance on wheel+images); YAML+C1 contract-valid and proven live via owner-approved rc tag |
| `CRE-5` | ✅ | Supply chain: uv.lock, Dependabot, audits, SBOM, coverage badge (S4) | `CRE-2`, `CRE-4` | uv.lock committed (171 pkgs) and CI installs via uv sync --locked (+ make lock target); .github/dependabot.yml in both repos (pip/npm/actions weekly, grouped); pip-audit + npm audit report-only in full.yml; syft SPDX-JSON SBOM for wheel+images in release.yml; self-owned coverage-badge shields JSON in full.yml + README badge; README supply-chain posture section; lockfile drift makes CI red |
| `CRE-6` | ✅ | Test-isolation root-cause + first-CI environment fixes (S1 amendment) | `CRE-2` | four isolation root causes fixed in-code with no reruns (conftest._reset_sel_singleton, _isolate_single_flight_locks, _tmp_home ordering, frozen_clock fixture); pytest-rerunfailures + global PERSONALCLAW_HOME rail removed from all pytest jobs; two real product bugs the runner surfaced fixed at source (sandbox.py _probe_unshare sequential two-step; BackendSupervisor ps -Awwo unlimited width); full suite deterministically green (7665 passed / 13 xfailed) with no flakes |
| `CRE-7` | ⬜ | Owner real-world provisioning remainder | `CRE-4`, `CRE-5` | GHCR packages confirmed public after first image push; coverage-badge home confirmed (shipped self-owned in full.yml); optional main branch protection decided (require ci.yml green) — owner-executed, no agent code change |
| `CRE-8` | ✅ | Stop the suite writing to the developer's real `~/.personalclaw`, and add the rail that fails when it does | `CRE-6` | an UNSPECIFIED home resolves to a per-test tmp dir at both seams that reach it (`config.loader.config_dir`, incl. every module-level binding of it, and `sel._default_dir`), while an EXPLICIT `$PERSONALCLAW_HOME` or a repointed `$HOME`/`Path.home` still passes through untouched so the real-home refusal rails (`test_seed` main-home, `test_cli_gateway_flags` yolo) stay non-vacuous; the three module-level constants that froze a home at IMPORT time — the one shape no fixture can reach — are converted to call-time resolvers in `src/` (`subagent_persistence._subagents_dir`, `session_map._sessions_dir`, dead `schedule._DEFAULT_DIR` deleted), with the tests that patched them realigned to the new seam rather than weakened; the previously-measured leaks are 0 bytes (`-k "project or memory or knowledge or recall"` 44,402→0; `test_learning_routes` 8,961→0; full suite 26 families/123 deletions→0); a session-scoped rail walks the real home once at `pytest_sessionfinish`, NAMES every entry created/modified/grown/deleted during the run and FAILS the session, with `ALLOWED_RESIDUE` empty (no blanket allowance) and an absent home reported as "nothing to compare" rather than a failure; the detector is importable and proven to fire against a fake root in `tests/test_real_home_guard.py` (append, create, byte-identical rewrite, deletion-via-parent, symlink non-follow, no-write-during-scan) |

## Atom scopes

### `CRE-1` — Formatting standard + whole-tree lint/type green (S1 premise correction)

**Status:** done

Design → Toolchain + supply chain; Task breakdown Session 1 T-S1a/T-S1b + lint-residue move; Filed issues #CI-1/#CI-2/#CI-3

**Done when:** setup.cfg [flake8] committed (100-char, E203/W503/E704 ignores, dist/build/venv excludes); black 26 + isort whole-tree reformat in an isolated style: commit; flake8 residue 588→0; mypy 152→0 (all real fixes bar #CI-1/2/3 documented inline-ignores); make lint exits 0

### `CRE-2` — Red-test triage + core ci.yml/full.yml + README badges (S1)

**Status:** done

Sessions S1; Task breakdown Session 1 T1.2/T1.3/T1.4/V1; Red-test policy (C2); Contracts C1 (ci.yml jobs lint/test/web/rails)

**Done when:** known-red groups fixed-or-xfail'd per C2 (xfail_strict untouched, one filed issue per annotated group #6/#7/#8); .github/workflows/ci.yml authored with jobs lint/test/web/rails + concurrency cancel-in-progress; full.yml matrix {3.12,3.13}×{ubuntu,macos} skeleton; README CI badge renders; job ids match C1; deliberate lint error + test failure turn the run red

### `CRE-3` — Apps-repo CI + core rails mount (S2)

**Status:** done

Sessions S2; Task breakdown Session 2 T2.1-T2.4/V2; Workflow set — apps repo; Contracts C1 rails job

**Done when:** apps-repo .github/workflows/ci.yml has manifest-validate (all app.json parse via core apps/manifest.py), tests (core installed from git, vendor SDKs uninstalled, per-bundle to avoid basename collisions), boundary (SDK-only import lint); scripts/validate_manifests.py + check_sdk_boundary.py exist; core ci.yml rails job mounts the plan-32 residue sweep unguarded; corrupting a manifest or adding a core-internal app import turns the respective job red

### `CRE-4` — Release pipeline release.yml (S3)

**Status:** done

Sessions S3; Task breakdown Session 3 T3.1-T3.5/V3; Contracts C1 release.yml (build/pypi/images/notes/attest); C2 owner two-package rule

**Done when:** release.yml on tag v* has build (npm build web → python -m build; wheel contains personalclaw/static/dist/index.html), pypi + pypi-client (Trusted Publishing, no token secrets, separate environment: release / release-client per unique PyPI publisher tuple), images (buildx linux/amd64+arm64 GHCR via GITHUB_TOKEN), notes (CHANGELOG section verbatim), attest (attest-build-provenance on wheel+images); YAML+C1 contract-valid and proven live via owner-approved rc tag

### `CRE-5` — Supply chain: uv.lock, Dependabot, audits, SBOM, coverage badge (S4)

**Status:** done

Sessions S4; Task breakdown Session 4 T4.1-T4.6/V4; Design → Toolchain + supply chain

**Done when:** uv.lock committed (171 pkgs) and CI installs via uv sync --locked (+ make lock target); .github/dependabot.yml in both repos (pip/npm/actions weekly, grouped); pip-audit + npm audit report-only in full.yml; syft SPDX-JSON SBOM for wheel+images in release.yml; self-owned coverage-badge shields JSON in full.yml + README badge; README supply-chain posture section; lockfile drift makes CI red

### `CRE-6` — Test-isolation root-cause + first-CI environment fixes (S1 amendment)

**Status:** done

Execution log — S1 amendment (isolation root-caused, mitigations retired); first/second/third-CI passes; PR wall-time budget

**Done when:** four isolation root causes fixed in-code with no reruns (conftest._reset_sel_singleton, _isolate_single_flight_locks, _tmp_home ordering, frozen_clock fixture); pytest-rerunfailures + global PERSONALCLAW_HOME rail removed from all pytest jobs; two real product bugs the runner surfaced fixed at source (sandbox.py _probe_unshare sequential two-step; BackendSupervisor ps -Awwo unlimited width); full suite deterministically green (7665 passed / 13 xfailed) with no flakes

### `CRE-7` — Owner real-world provisioning remainder

**Status:** todo

Owner tasks (real world) items 2/4/5; Status line 'Remaining are OWNER items only'

**Done when:** GHCR packages confirmed public after first image push; coverage-badge home confirmed (shipped self-owned in full.yml); optional main branch protection decided (require ci.yml green) — owner-executed, no agent code change

### `CRE-8` — The suite must not write to the developer's real home, and must fail when it does

**Status:** ✅ done (#PENDING)

Reopens the `CRE-6` test-isolation strand. Created 2026-08-11 after a byte-level measurement
of the real home across a `-k` selection, one file, and a full suite.

**Done when:** an UNSPECIFIED home resolves to a per-test tmp dir at both seams that reach the
real one (`config.loader.config_dir`, including every module-level binding of that function
object, and `sel._default_dir`), while an EXPLICIT `$PERSONALCLAW_HOME` — *including one
pointed deliberately at the real home* — and a repointed `$HOME`/`Path.home` both pass through
untouched, so the real-home refusal rails stay non-vacuous; the three module-level constants that
froze a home at IMPORT time are converted to call-time resolvers in `src/` (the one shape no fixture
can reach), with the tests that patched them realigned rather than weakened; the measured leaks read
0 bytes after the fix; a session-scoped rail walks the real home once at `pytest_sessionfinish`, NAMES
every entry created / modified / grown / deleted during the run and FAILS the session;
`ALLOWED_RESIDUE` is empty and exact-match only; an absent real home is reported as "nothing to
compare" rather than converted into a failure; and the detector is importable and proven to fire
against a fake root under `tmp_path`, never by writing to the real home to prove a point.

#### Why this is a live defect and not hygiene

Measured on `feature-wf2lea9-polish-tier`, and byte-identically on its parent — so it is
pre-existing and no current atom owns it:

- `pytest tests/ -k "project or memory or knowledge or recall"` → **+44,402 bytes** appended to
  the user's real `~/.personalclaw/security_events.jsonl` (`artifact_save` / `tool_invocation`
  rows carrying caller identities `mcp_core` and `dashboard:chat-1`), plus 46 new and 16 rewritten
  real-home entries.
- `pytest tests/test_learning_routes.py` alone → **+8,961 bytes** (its accept/reject tests reach
  `_audit` → `sel().log_api_access`).
- A **full suite** touched **26 distinct real-home families**, *net-shrank* `security_events.jsonl`
  by **151,963 bytes** — the 365-day retention prune ran against the user's live audit log — and
  **deleted 123 files**. A test run rewriting and pruning the user's security audit trail is a
  worse outcome than one appending to it: appended rows are visibly synthetic, whereas a prune
  destroys real history and leaves nothing to notice.
- The suite also wrote `tasks/*.json`, `codegraph/*.db`, `crashes/*.json`, `cron-history/`,
  `inbox.json`, `sessions.json`, `routing_stats.json`, `spend.json`, `memory.db`,
  `session_search.db`, `learning.db`, `skills/*/SKILL.md`, `subagents/`, `usage/`, `workflows/`,
  `screenshots/`, `code/` worktrees and `workspace/_ext/<slugged-worktree-path>/memory/*.md` —
  the last one leaving a directory per worktree it was ever run from.

The root cause is one line per seam. `sel.py`'s `SecurityEventLog.__init__` does
`self._dir = base_dir or _default_dir()`, and `_default_dir()` falls back to
`Path.home() / ".personalclaw"`. `conftest._reset_sel_singleton` (added by `CRE-6`) clears the
`__new__`-based singleton around every test, which fixed the bleed it was written for — but a
*fresh* SEL with no home patch binds the **real** home. The other 25 families are the same shape
through `config_dir()`.

#### Design

**1. Redirect the seam, not the environment.** Two mechanisms were available and both are
rejected, each for a reason already recorded in this repo. A global `PERSONALCLAW_HOME` for the
pytest jobs is what `CRE-6` deliberately **removed**: `config_dir` gives the env var precedence
over `Path.home()`, so a global value defeats every test that asserts env precedence *and* every
test that asserts the default resolution. A blanket `Path.home` patch is rejected in
`conftest._isolate_session_map`'s own docstring because it breaks the tests that assert real-home
safety rails — and it would additionally redefine the unrelated `Path.home()/".aws"` and
`Path.home()/".ssh"` paths that the artifact and task sensitivity tests assert on, quietly making
*those* vacuous. What remains is the pattern `conftest.py` already uses per subsystem
(`_isolate_session_map`, `_isolate_trigger_store`): point one subsystem's home-resolving seam at a
per-test tmp dir, last-wins if a test overrides it.

**2. One fixture, not thirteen, because the measurement says it is one seam.** The 26 writer
families reach the real home through exactly two resolvers: `config.loader.config_dir()`
(153 call sites) and `sel._default_dir()`. Writing thirteen fixtures would be thirteen guards on
one seam, and the fourteenth subsystem would leak again the day it lands. The fixture patches
those two, plus — because `from personalclaw.config.loader import config_dir` at module scope
binds the function object where patching the loader can never reach it — it re-points every
**identity-matched** binding of that same function object across loaded `personalclaw.*` modules
(58 of them). Identity-matched, so nothing else is touched; the 95 function-local imports,
including every `as _cd` alias, resolve from the loader at call time and are already covered.

**3. "Unspecified" is the whole contract.** Redirect only when the caller expressed no
preference: `$PERSONALCLAW_HOME` unset AND `Path.home()` still the real user's home. An explicit
`$PERSONALCLAW_HOME` passes through *even when it points at the real home*, because that is
exactly what `test_cli_gateway_flags`'s `--approval yolo` rails and `test_seed`'s main-home rails
do to assert a refusal — redirecting a deliberate choice would silently make those rails pass for
the wrong reason. A test that repoints `$HOME`/`Path.home` also passes through: it isolated
itself, and it reads its assertions back out of *its* home. The guarded resolver must also
**not delegate first** in the redirect case: `config_dir()` mkdirs whatever it resolves, so
delegating would create `~/.personalclaw` on a machine that has none.

**4. Fixture ordering is load-bearing.** The fixture is declared before `_reset_sel_singleton`,
so it is set up first and torn down last. The singleton is cleared around every test, so the next
`sel()` call *constructs* a `SecurityEventLog` — and that construction has to still see the
redirected `_default_dir`, or the leak returns through the reset that was supposed to help.

**5. The rail detects with one walk and no baseline.** `pytest_sessionstart` (controller only —
xdist workers share the one real home, so per-worker arming would multiply one leak into N
reports) records `time.time_ns()`; `pytest_sessionfinish` walks the real home once and reports
every entry with a newer `mtime_ns`. That catches creation, in-place modification *and* growth,
and catches deletion via the surviving parent directory's mtime. The snapshot-then-diff
alternative costs two walks of a >100k-file tree — seconds of tax on every single-file `pytest`
run; here startup is O(1) and only the finish path pays (measured 2.8s). The detector stats and
never opens, and treats an absent root as "nothing to compare" — the expected state in CI.

**6. Teeth, and why they are affordable.** The rail fails the session rather than printing a
warning, because after the fixture the population is **zero** — measured over a full suite — so
there is nothing to grandfather. `ALLOWED_RESIDUE` exists for a named, individually justified
residue and is empty; a prefix or glob allowance would convert the rail back into a baseline that
ratifies whatever leaks next. The one false-positive shape is a gateway running against the real
home while the suite runs, and that is also a defect: the dev gateway is documented to run
against an isolated `PERSONALCLAW_HOME`. The failure text says so, so the reader is not misled.

**7. A home frozen at import time is unreachable by any fixture — fix that at source.** The
rail caught 147 entries still landing in `subagents/` *after* the fixture was in place, because
`subagent_persistence` computed `_SUBAGENTS_DIR = config_dir() / "subagents"` at module scope,
before any fixture exists. No test-side redirect can reach that, and in production a
`$PERSONALCLAW_HOME` set after first import is silently ignored for the life of the process — so
it is a product bug, not a test artifact, and it gets a product fix: resolve per call. Sweeping the
class found two more (`session_map._SESSIONS_DIR`, whose readers decide whether a session still
exists, so `prune()` was consulting the developer's real `sessions/`; and a dead
`schedule._DEFAULT_DIR` whose import-time `config_dir()` mkdir'd the real home merely by importing
the module). Three instances, one class — and the rail is what found the one a code read missed.

**8. Non-vacuity is the point of splitting the detector out.** It lives in `tests/real_home_guard.py`
so `tests/test_real_home_guard.py` can drive it against a throwaway root under `tmp_path` and
prove it fires on an append, a creation, a **byte-identical rewrite** (the shape a size-only diff
misses — the suite really did rewrite `skills/*/SKILL.md` identically), and a deletion. Proving a
leak detector works by leaking is not a proof; it is the defect the detector exists to prevent.

#### Implementation plan

1. **Re-measure before touching anything** (`wc -c` on the real `security_events.jsonl` plus a
   full-tree size/mtime snapshot, around one `-k` selection and one file). If the numbers are
   already 0, stop — the premise moved and this atom is not needed.
2. **Attribute every writer** rather than guessing: a throwaway pytest plugin that wraps `open` /
   `os.open` / `os.mkdir` / `os.replace` / `sqlite3.connect` / `Path.write_*` and records a stack
   whenever the target is under the real home. This is what turns "SEL leaks" into "26 families,
   two seams" — and the second seam is the one a code read would have missed.
3. **Add `_isolate_real_home_writers`** to `tests/conftest.py`, above `_reset_sel_singleton`, per
   the design: guarded `config_dir` + guarded `sel._default_dir` + the identity sweep, with the
   rejected alternatives written into the docstring so the next reader does not re-litigate them.
4. **Add the detector** (`tests/real_home_guard.py`) and its non-vacuity suite, then the
   `pytest_sessionstart`/`pytest_sessionfinish` pair that arms it and fails the session.
4b. **Believe the rail over the fixture.** Whatever it still names is a seam the fixture cannot
   reach — expect `config_dir()` resolved into a module-level constant at import time. Convert
   those to call-time resolvers in `src/`, and realign (never weaken) the tests that patched the
   constant.
5. **Re-measure the same selections** and confirm 0 bytes and 0 changed entries — from *both* the
   rail and an independent snapshot diff, so the rail is not the only witness to its own success.
6. **Prove the safety rails still refuse**: `test_seed.py` (main-home refusal, with and without
   `--seed-replace`, symlinked target) and `test_cli_gateway_flags.py` (yolo refused when
   `$PERSONALCLAW_HOME` is unset, empty, the literal default, or `~` expanded).
7. **Gate**: `make lint`; the sel/isolation selections; the previously-leaking selections; the
   ratchets a new tests/ module trips (`test_inert_surface_baseline`, `test_agent_reference`,
   `test_docs_lint_baseline`); then the full suite once, to prove the rail passes clean and to
   measure the residue. Any residue that survives is named in `ALLOWED_RESIDUE` **and** in the
   plan's execution log, or it is not allowed at all.

**Scope guard — what this atom is NOT.** Every leak is closed at the seam that resolved the home,
never by an env rail or a test-side allowance. The `src/` half is deliberately narrow: only the
three module-level constants that froze a home at import time, because a frozen constant is the one
shape no fixture can reach — no other product behaviour changes. It does not re-open the
`--dist worksteal` scheduler question (`SH6.1` owns that), does not touch the CI workflow files —
the rail rides in `conftest.py`, so it protects a developer's laptop and CI equally — and does not
chase the two long-known async workflow flakes (`Timeout (>120s)` / `Runner is closed`) that
appear under CPU contention.
