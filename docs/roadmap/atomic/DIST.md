# DISTRIBUTION — atomic plans

**Source plan:** [`DISTRIBUTION`](../plans/DISTRIBUTION.md)  
**Code:** `DIST`  
**Source status:** done

DISTRIBUTION is DONE for all code deliverables (S1–S4). 12 atoms: 9 done (packaging correctness, SDK demotion core+apps halves, sdist SPA fix, client packaging, docs restructure, bootstrap install.sh, containers, self-update generalization) plus the first PyPI publish (PyPI carries core+client through 0.1.3). Remaining todo: owner clean-VM walkthroughs V1–V4 and the out-of-scope S5 Homebrew/Nix channels. The one intra-plan gate that mattered — the class-B update_kind_aware gate — was re-scoped to a plain clean break under the pre-1.0 banner (the migration-backed gate regime is deferred; no lifecycle/ package). Key cross-plan edges: CI-RELEASE owns the build pipeline that produces every artifact these paths install; T1.4's SDK demotion was contingent on PROVIDER-BOUNDARY-COMPLETION S2's app pip-step (confirmed supported); DISCOVERABILITY hosts /install.

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

