# DISTRIBUTION — atomic plans

**Source plan:** [`DISTRIBUTION`](../plans/DISTRIBUTION.md)  
**Code:** `DIST`  
**Source status:** done

DISTRIBUTION is DONE for all code deliverables (S1–S4). 13 atoms: 9 done (packaging correctness, SDK demotion core+apps halves, sdist SPA fix, client packaging, docs restructure, bootstrap install.sh, containers, self-update generalization) plus the first PyPI publish (PyPI carries core+client through 0.1.3) and `DIST-13`, which routed `personalclaw update` through that per-kind updater — S4 had replaced the git-only path on the dashboard side only, so every pip/pipx/uv-tool user's `update` still dead-ended on a missing source tree. Remaining todo: owner clean-VM walkthroughs V1–V4 and the out-of-scope S5 Homebrew/Nix channels. The one intra-plan gate that mattered — the class-B update_kind_aware gate — was re-scoped to a plain clean break under the pre-1.0 banner (the migration-backed gate regime is deferred; no lifecycle/ package). Key cross-plan edges: CI-RELEASE owns the build pipeline that produces every artifact these paths install; T1.4's SDK demotion was contingent on PROVIDER-BOUNDARY-COMPLETION S2's app pip-step (confirmed supported); DISCOVERABILITY hosts /install.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DIST-1` | ✅ (#6b0104e, eb5f0c4) | Packaging correctness: [project.urls], single-sourced __version__, drop zip-safe, verify_wheel contract (C3/C4) | `EXT:CI-RELEASE:release.yml must run npm build before python -m build and host the verify_wheel step (T3.1)` | pip show lists all 5 Project-URLs; test_version_consistency goes red when pyproject/__version__/CHANGELOG-latest disagree; scripts/verify_wheel.py exits 0 — SPA present in wheel, installs Node-free into a scratch venv, boots gateway --test-mode, / and /api/healthz return 200; wired into release.yml |
| `DIST-2` | ✅ (#71db09f) | Demote openai/anthropic to extras + require_sdk lazy-import errors (core-repo half of T1.4) | `EXT:PROVIDER-BOUNDARY-COMPLETION:S2 app pip-step (app_manager._install_python_deps) must install app-declared pythonDependencies — confirmed DONE before this proceeded (else DEVIATION/skip)` | clean install without extras: wheel METADATA carries openai/anthropic ONLY as `extra ==` entries; missing-SDK error text names the exact `pip install 'personalclaw[openai]'` remedy + doctor hint at the 4 lazy-import sites; test_sdk_deps green |
| `DIST-3` | ✅ (#fix-sdist-bundles-spa) | MANIFEST.in grafts web/dist into the sdist so wheel-from-sdist carries the SPA | `DIST-1` | `python -m build` (no args, the release command) then verify_wheel.py PASSES — the wheel built from the sdist carries the SPA; guarded by tests/test_sdist_bundles_spa.py; must land before the first tag push |
| `DIST-4` | ✅ (#13e46fd) | personalclaw-client packaging metadata + a client CI job | `EXT:CI-RELEASE:client CI job wiring + separate `release-client` publish environment` | client wheel METADATA correct (name/urls/classifiers/markdown README); `pip install personalclaw-client` works; the new client CI job builds the wheel and runs its 18-test suite green in a fresh venv |
| `DIST-5` | ✅ (#2b880d7) | Docs restructure: getting-started uv-first install matrix + extras table; CONTRIBUTING framed as contributor path | `DIST-1` | a stranger following docs/guides/getting-started.md never runs Node or git; §A install matrix + extras guidance table present; venv/`make web-build` path moved to CONTRIBUTING dev-setup |
| `DIST-6` | ✅ (#5bc4c98) | Bootstrap install.sh (uv-first, --container flag) + host at website /install | `EXT:DISCOVERABILITY:plan 36 owns the website repo that hosts /install` | `sh -n`/`dash -n` clean; `sh install.sh` on bare ubuntu+macos reaches a working CLI; `--container` prints the compose snippet; idempotent re-run upgrades; served as text/plain at personalclaw.dev/install |
| `DIST-7` | ✅ (#feature-provider-sdk-deps) | Provider apps declare openai/anthropic pythonDependencies (apps-repo half of T1.4) | `DIST-2`, `EXT:PROVIDER-BOUNDARY-COMPLETION:manifest pip-step installs the declared deps into the shared venv` | the 12 provider apps that build OpenAIProvider/AnthropicProvider declare the matching SDK in manifest dependencies.pythonDependencies; all 38 manifests parse + round-trip stable; per-bundle app tests green |
| `DIST-8` | ✅ (#b93c337, 843c3cd) | Containers: install-kind env in both Dockerfiles + container guide + README compose snippet & install matrix | `EXT:CI-RELEASE:multi-arch (amd64+arm64) images published from CI to ghcr` | PERSONALCLAW_INSTALL_KIND=container baked into Dockerfile.backend + Dockerfile.web; docs/guides/containers.md covers ports/volumes/.env/snapshot-backup/pull-update; README carries the 2-line compose snippet + §A matrix table |
| `DIST-9` | ✅ (#ded75c0, aac5a84, 880131d, b22cc8f, a65d2fd) | Install-kind-aware self-update: detect_install_kind, tag-driven check, per-kind apply, per-kind Updates panel (S4, clean break) | `DIST-8` | detect_install_kind() classifies git/pip/container/desktop; C2 wire-shape conformance test locked (Tier-S); git rides release tags with a dev_mode override, pip does `pip install -U` + graceful re-exec (no web build), container/desktop return structured instructions; per-kind Updates panel + POST /api/update/dev-mode; dashboard.update_dev_mode config round-trips; CHANGELOG entry recording the clean-break DEVIATION (no update_kind_aware gate) and `snapshot` advice |
| `DIST-10` | ✅ (#PyPI 0.1.0–0.1.3 (owner-triggered)) | First real PyPI publish of core + client; verify uv tool / pipx on a clean machine (T2.1, owner) | `DIST-1`, `DIST-3`, `DIST-4`, `EXT:CI-RELEASE:release.yml publishes core (env release) + client (env release-client)` | PyPI carries `personalclaw` + `personalclaw-client` (live through 0.1.3); `uv tool install personalclaw` and `pipx install personalclaw` both yield a working CLI on a clean machine |
| `DIST-11` | ⬜ | Owner clean-machine walkthroughs V1–V4 (wheel install, getting-started, container, per-kind self-update) | `DIST-1`, `DIST-5`, `DIST-8`, `DIST-9`, `DIST-10` | each clean-VM/never-seen-the-project walkthrough passes end to end (V1 wheel→onboarding→first chat Node-absent; V2 getting-started verbatim; V3 container two-commands→TLS dashboard→state survives compose down/up; V4 all four kind self-update paths) and is recorded in the Execution log |
| `DIST-12` | ⬜ | Convenience channels: Homebrew tap + Nix flake (S5, post-launch, out of scope for this loop) | `DIST-10` | `brew install personalclaw/tap/personalclaw` works on a clean mac; `nix run .#personalclaw -- --version` prints the version; per-release smoke checklists recorded; README matrix updated |
| `DIST-13` | ✅ (#PENDING) | Route `personalclaw update` through the per-kind updater; lift it out of the dashboard layer into core `self_update` | `DIST-9` | the install-kind decision + the shared git/pip primitives live in core `src/personalclaw/self_update.py` and `dashboard/handlers/updates_kind.py` is DELETED (no re-export shim), with every call site moved in the same commit — the dashboard handlers, `gateway._auto_apply_update`'s `_package_root`, and the CLI; `personalclaw update` dispatches exhaustively over `INSTALL_KINDS` with NO default arm (git = fetch/reset/build/install honouring `dashboard.update_dev_mode`; pip/pipx/uv-tool = resolved installer `-U personalclaw==<latest>` needing no source tree, then "restart the gateway"; container + desktop print instructions and exit 0; an unmapped kind names what it detected and exits 1); the destructive-reset confirmation survives, and a NON-INTERACTIVE stdin refuses the reset (exit 1) instead of prompting, reading a piped "y" or raising EOFError; the `mainline` default is replaced by current-branch → `refs/remotes/origin/HEAD` → `git remote show origin` → the literal `main`, railed by a test asserting no updater module carries the old literal; one test drives every CLI branch with git/pip faked at two seams (never a real `reset --hard` or `pip -U`); spawn-ceiling census re-keyed to the moved sites; CHANGELOG entry |

## Atom scopes

### `DIST-1` — Packaging correctness: [project.urls], single-sourced __version__, drop zip-safe, verify_wheel contract (C3/C4)

**Status:** done (PR #6b0104e, eb5f0c4)

Design §B; Session 1 T1.1–T1.3, T1.5; Contracts C3, C4

**Done when:** pip show lists all 5 Project-URLs; test_version_consistency goes red when pyproject/__version__/CHANGELOG-latest disagree; scripts/verify_wheel.py exits 0 — SPA present in wheel, installs Node-free into a scratch venv, boots gateway --test-mode, / and /api/healthz return 200; wired into release.yml

### `DIST-2` — Demote openai/anthropic to extras + require_sdk lazy-import errors (core-repo half of T1.4)

**Status:** done (PR #71db09f)

Design §B (LLM-SDK demotion, contingent); Session 1 T1.4 core half

**Done when:** clean install without extras: wheel METADATA carries openai/anthropic ONLY as `extra ==` entries; missing-SDK error text names the exact `pip install 'personalclaw[openai]'` remedy + doctor hint at the 4 lazy-import sites; test_sdk_deps green

### `DIST-3` — MANIFEST.in grafts web/dist into the sdist so wheel-from-sdist carries the SPA

**Status:** done (PR #fix-sdist-bundles-spa)

Contract C4 wheel contract; Execution log 'Release dry-run — RELEASE-BLOCKING BUG'

**Done when:** `python -m build` (no args, the release command) then verify_wheel.py PASSES — the wheel built from the sdist carries the SPA; guarded by tests/test_sdist_bundles_spa.py; must land before the first tag push

### `DIST-4` — personalclaw-client packaging metadata + a client CI job

**Status:** done (PR #13e46fd)

Session 2 T2.3; a stable published surface

**Done when:** client wheel METADATA correct (name/urls/classifiers/markdown README); `pip install personalclaw-client` works; the new client CI job builds the wheel and runs its 18-test suite green in a fresh venv

### `DIST-5` — Docs restructure: getting-started uv-first install matrix + extras table; CONTRIBUTING framed as contributor path

**Status:** done (PR #2b880d7)

Design §A; Session 2 T2.4

**Done when:** a stranger following docs/guides/getting-started.md never runs Node or git; §A install matrix + extras guidance table present; venv/`make web-build` path moved to CONTRIBUTING dev-setup

### `DIST-6` — Bootstrap install.sh (uv-first, --container flag) + host at website /install

**Status:** done (PR #5bc4c98)

Design §E; Session 2 T2.2 (cross-repo deliverable)

**Done when:** `sh -n`/`dash -n` clean; `sh install.sh` on bare ubuntu+macos reaches a working CLI; `--container` prints the compose snippet; idempotent re-run upgrades; served as text/plain at personalclaw.dev/install

### `DIST-7` — Provider apps declare openai/anthropic pythonDependencies (apps-repo half of T1.4)

**Status:** done (PR #feature-provider-sdk-deps)

Session 1 T1.4 cross-repo half; §2.6 app-manifest dependencies

**Done when:** the 12 provider apps that build OpenAIProvider/AnthropicProvider declare the matching SDK in manifest dependencies.pythonDependencies; all 38 manifests parse + round-trip stable; per-bundle app tests green

### `DIST-8` — Containers: install-kind env in both Dockerfiles + container guide + README compose snippet & install matrix

**Status:** done (PR #b93c337, 843c3cd)

Design §D; Session 3 T3.1–T3.3

**Done when:** PERSONALCLAW_INSTALL_KIND=container baked into Dockerfile.backend + Dockerfile.web; docs/guides/containers.md covers ports/volumes/.env/snapshot-backup/pull-update; README carries the 2-line compose snippet + §A matrix table

### `DIST-9` — Install-kind-aware self-update: detect_install_kind, tag-driven check, per-kind apply, per-kind Updates panel (S4, clean break)

**Status:** done (PR #ded75c0, aac5a84, 880131d, b22cc8f, a65d2fd)

Design §C; Contracts C1, C2, C5; Session 4 T4.1–T4.5

**Done when:** detect_install_kind() classifies git/pip/container/desktop; C2 wire-shape conformance test locked (Tier-S); git rides release tags with a dev_mode override, pip does `pip install -U` + graceful re-exec (no web build), container/desktop return structured instructions; per-kind Updates panel + POST /api/update/dev-mode; dashboard.update_dev_mode config round-trips; CHANGELOG entry recording the clean-break DEVIATION (no update_kind_aware gate) and `snapshot` advice

### `DIST-10` — First real PyPI publish of core + client; verify uv tool / pipx on a clean machine (T2.1, owner)

**Status:** done (PR #PyPI 0.1.0–0.1.3 (owner-triggered))

Session 2 T2.1; Owner task 1

**Done when:** PyPI carries `personalclaw` + `personalclaw-client` (live through 0.1.3); `uv tool install personalclaw` and `pipx install personalclaw` both yield a working CLI on a clean machine

### `DIST-11` — Owner clean-machine walkthroughs V1–V4 (wheel install, getting-started, container, per-kind self-update)

**Status:** todo

Session 1 V1, Session 2 V2, Session 3 V3, Session 4 V4; Owner task 2

**Done when:** each clean-VM/never-seen-the-project walkthrough passes end to end (V1 wheel→onboarding→first chat Node-absent; V2 getting-started verbatim; V3 container two-commands→TLS dashboard→state survives compose down/up; V4 all four kind self-update paths) and is recorded in the Execution log

### `DIST-12` — Convenience channels: Homebrew tap + Nix flake (S5, post-launch, out of scope for this loop)

**Status:** todo

Design §A (Homebrew/Nix row); Session 5 T5.1–T5.2, V5; Owner task 3

**Done when:** `brew install personalclaw/tap/personalclaw` works on a clean mac; `nix run .#personalclaw -- --version` prints the version; per-release smoke checklists recorded; README matrix updated

### `DIST-13` — Route `personalclaw update` through the per-kind updater; lift it out of the dashboard layer

**Status:** ✅ done (#PENDING)

Reopens the `DIST-9` (S4) self-update strand. Created 2026-08-11 after reading the CLI's update
path against the machinery S4 shipped.

**Done when:** the install-kind decision + the shared git/pip primitives live in core `src/personalclaw/self_update.py` and `dashboard/handlers/updates_kind.py` is DELETED (no re-export shim), with every call site moved in the same commit — the dashboard handlers, `gateway._auto_apply_update`'s `_package_root`, and the CLI; `personalclaw update` dispatches exhaustively over `INSTALL_KINDS` with NO default arm (git = fetch/reset/build/install honouring `dashboard.update_dev_mode`; pip/pipx/uv-tool = resolved installer `-U personalclaw==<latest>` needing no source tree, then "restart the gateway"; container + desktop print instructions and exit 0; an unmapped kind names what it detected and exits 1); the destructive-reset confirmation survives, and a NON-INTERACTIVE stdin refuses the reset (exit 1) instead of prompting, reading a piped "y" or raising EOFError; the `mainline` default is replaced by current-branch → `refs/remotes/origin/HEAD` → `git remote show origin` → the literal `main`, railed by a test asserting no updater module carries the old literal; one test drives every CLI branch with git/pip faked at two seams (never a real `reset --hard` or `pip -U`); spawn-ceiling census re-keyed to the moved sites; CHANGELOG entry

#### Design

**1. The defect: S4 replaced the git-only path on one side only.** `DIST-9`'s execution log records
that the per-kind behaviour "replaced, not gated" the git-only updater. The dashboard side did.
`cli.py:592` registers `update`, `cli.py:944` dispatches to `cli_server._update`, and that function
was still: require `$PERSONALCLAW_PROJECT_DIR`, require a `.git` dir, `git fetch` + `git reset
--hard` + rebuild. `grep -rn "updates_kind\|detect_install_kind" src/personalclaw/cli*.py` returned
nothing. So for the two installs README.md:165-166 and `docs/guides/getting-started.md`:31-32
document FIRST — `pipx install personalclaw`, `pip install personalclaw` — `personalclaw update`
printed **"❌ PERSONALCLAW_PROJECT_DIR not set — cannot locate source tree"** and exited 1. A dead
end, with the correct code one module away.

**2. Why the layering caused it, and why the fix is a move rather than an import.** The taxonomy
shipped as `dashboard/handlers/updates_kind.py`, so the only way to reach `detect_install_kind()`
was to import an HTTP handler. The CLI reasonably declined, and kept its own path — which is how a
decision layer with one reachable consumer drifts. Adding a CLI→handler import would have preserved
the smell and invited the next such import. The decision + the shared primitives move to core
`personalclaw/self_update.py`; the dashboard handler and the CLI both become callers, and
`updates_kind.py` is deleted outright — no re-export, no shim, every call site moved in the same
commit (clean break under the pre-1.0 banner).

**3. What moves, and what deliberately does not.** Moved: `detect_install_kind` / `InstallKind` /
`INSTALL_KINDS`, the release probe + cache (`fetch_latest_release`, `build_update_status`,
`normalize_version`, `version_tuple`, `read_release_cache`), `package_root`,
`commits_behind_upstream`, `installer_error_summary`, `container_instructions`, `upgrade_spec`, and
new sync git primitives. Left in the handler: the 409 in-flight guard, `push_update_progress`
publishing, `_graceful_reexec`, `_live_auth_mode`, redaction, and the legacy changelog-diff check —
all HTTP/async concerns. **Rejected: one shared `apply(kind, progress=…)`.** The two lifecycles
genuinely differ — the dashboard applies asynchronously, streams progress and re-execs the live
gateway; the CLI is a short-lived synchronous process that prints, prompts a TTY, and has no server
to re-exec — so a single apply would be a callback-shaped abstraction over two different lifecycles.
The shared part is the *decision* plus the primitives, and that is what moved.

**4. The CLI dispatch is closed, with no default arm.** git keeps today's pipeline and now honours
`dashboard.update_dev_mode` exactly as the dashboard's apply does (OFF = ride release tags, so being
on the latest tag is "up to date" even when `main` is ahead; ON = track every commit). pip/pipx/uv-
tool run the resolved installer with `-U personalclaw==<latest>` and require no source tree; there
is no re-exec, because this process is the CLI and not the gateway, so it prints
`personalclaw restart` rather than bouncing a server nobody asked it to touch. container and desktop
print instructions. An **unmapped** kind names what it detected and exits 1 — there is no `else`
that falls through to `git reset --hard`, which on a tree that kind may not even own is the worst
available guess. `INSTALL_KINDS` (derived from the `Literal`) plus a set-equality test against the
CLI's handled set is the ratchet: a new member reds the dispatch test instead of landing in an
else-branch.

**5. Two exit-code decisions, made rather than left ambiguous.** container/desktop exit **0**: the
status answers "did the command do its job?", not "did bytes change?" — the git branch has always
exited 0 on "Already up to date", so 0 never meant "something changed" here, and for these kinds the
job IS delegation. A correctly configured container install is not a failure, and an unattended
caller cannot act on printed instructions anyway, so non-zero would add noise exactly where it
cannot help. The counter-argument (a script running `personalclaw update && restart` learns nothing)
is real, so the printed text is unambiguous about who must act, and a script that needs the
distinction reads `apply_method` from `GET /api/update/check`. Second: a **non-interactive stdin**
does NOT prompt and exits **1**. `input()` on a pipe either reads a piped "y" — destroying
uncommitted work nobody agreed to lose — or raises EOFError into a traceback. Refusing is
recoverable (stash or commit, re-run); a wrong yes is not. An interactive "n" still exits 0, because
declining is a choice, whereas a refusal on someone's behalf means the update they asked for did not
happen.

**6. `mainline` was never a branch here.** `cli_server` defaulted `branch = "mainline"` when
`git rev-parse --abbrev-ref HEAD` came back empty or `HEAD` (detached), so a detached-HEAD update
fetched a ref this repository has never carried. `resolve_default_branch` answers honestly instead:
the checked-out branch (updating means advancing the branch you are on), else the remote's own HEAD
read from the LOCAL `refs/remotes/origin/HEAD` (offline-safe), else `git remote show origin`'s
`HEAD branch:` (network, so last among the probes, and `(unknown)` is not a branch name), else the
literal `main` — the repo's real default. `gateway._auto_apply_update` already coerced to `main`, so
the CLI was the sole holdout. A test asserts the old literal appears in no updater module, including
in prose a later edit could copy back into code.

**7. Nothing ships inert.** Every branch is driven: the four kinds, the unmapped-kind refusal, the
dispatch-exhaustiveness ratchet, up-to-date, dev-mode-off-on-latest-tag, fetch failure, the
confirmation confirmed / declined / EOF'd / refused-without-a-TTY, the pip pin, the unpinned offline
upgrade, the installer-error summary, and NoInstallerError. Faking is at exactly two seams —
`self_update._run_git` (every sync git spawn funnels through it) and `cli_server.subprocess.run` —
so no test can reach a real `git reset --hard` or `pip -U`.

#### Implementation plan

1. **Verify the premise before writing anything**: `grep -rn "updates_kind\|detect_install_kind"
   src/personalclaw/cli*.py` must return nothing, and `cli_server._update` must still require a
   `.git` dir. If the CLI already routes per kind, stop — the premise moved.
2. **Create `src/personalclaw/self_update.py`** with the moved decision + probe + primitive surface,
   plus `resolve_default_branch`, `container_instructions`, `upgrade_spec`, and the single `_run_git`
   seam (timeout → rc 124, missing binary → rc 127; a timeout is an ordinary updater failure, not an
   exception every caller must wrap).
3. **Delete `updates_kind.py`** and move every call site in the same commit: the five handler
   imports, `gateway._auto_apply_update`'s `_package_root`, the `handlers/__init__` re-export of the
   duplicate `_version_tuple`. Prove none was left behind by grepping every moved symbol name across
   `src/`, `tests/`, and `web/src`.
4. **Rewrite `cli_server._update`** as the closed dispatch plus one function per kind, with the
   installer and the `setup --agent-only` re-run behind small shared helpers.
5. **Re-key the spawn-ceiling census** (`tests/test_spawn_ceiling_audit.py`) for the moved and new
   spawn sites — a stale allowlist entry reds the audit in both directions, which is the point.
6. **Tests**: a new `tests/test_cli_update_kinds.py` driving every branch; branch-resolution and
   `_run_git` failure-shape tests plus the `mainline` regression rail in `tests/test_self_update.py`
   (renamed from `test_updates_kind.py`); realign — never weaken — the four existing update test
   modules to the new seam.
7. **Gate**: `make lint`; the update/install-kind/cli selections; the ratchets a new `src/` module
   and a renamed test module trip (`test_inert_surface_baseline`, `test_agent_reference`,
   `test_docs_lint_baseline`, `test_config_roundtrip`); then the full suite once, and report the
   real-home rail verdict.

**Scope guard — what this atom is NOT.** It does not add a fifth `InstallKind`: `pip` already covers
pip, pipx and uv-tool because the apply is identical for all three, and `_installer.install_argv`
already resolves which program performs it. It does not touch the dashboard's HTTP behaviour, the
C2 wire shape, or the frontend. It does not make the CLI re-exec or auto-restart a running gateway.
