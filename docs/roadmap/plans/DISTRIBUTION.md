# Plan: Distribution & Packaging — One Command to a Talking Agent

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner target: one-command install + easy download-and-launch per user preference)
**Created:** 2026-07-18
**Wave:** 0 — launch-gating. Consumes CI-RELEASE-ENGINEERING's release pipeline; feeds PUBLICATION, PLATFORM-REACH, DISCOVERABILITY-LAUNCH (the `/install` script).
**Depends on:** CI-RELEASE-ENGINEERING S3 (pipeline automation). Coordinates with PROVIDER-BOUNDARY-COMPLETION (dependency-set changes ship together) and LIFECYCLE-DOCTRINE (self-update behavior change is class B, gated).
**Scope:** every supported install path becomes one command with no toolchain surprises, and self-update works for every path — not just git checkouts. **Soul guardrail:** all channels are projections of ONE release artifact set (wheel + images) built by the release pipeline — no per-channel special builds. A channel ships only with a per-release smoke check (automated or checklisted); anything less is documented as community-maintained.

---

## Context (code recon, 2026-07-18 — two findings shrink S1)

1. **The wheel can already carry the SPA.** `setup.py` defines `BuildWithWeb(build_py)`: at build time it copies repo `web/dist` (or a pre-built `src/personalclaw/static/dist`) into `build_lib/personalclaw/static/dist`. And `frontend.py::ensure_dev_dist_symlink` *prefers a real in-package `static/dist` directory* over creating the dev symlink (line ~39: real dir with `index.html` wins). So a wheel built after `make web-build` already installs and serves its own assets — the machinery exists; what's missing is the pipeline running the web build first (CI-RELEASE S3), verification, and docs that stop telling users to run `make web-build`.
2. **Self-update is layout-aware but install-kind-blind.** `dashboard/handlers/updates.py`: `_resolve_project_dir` handles standalone vs monorepo checkouts; `_commits_behind_upstream` does best-effort `git fetch` + ahead-count; apply = `git pull` → `pip install -e .` → web build → re-exec. A wheel/container install has no repo — every updater surface is wrong for them today. Version is dual-sourced (`src/personalclaw/__init__.py` `__version__` + pyproject) with no consistency check.

Also verified: Python floor 3.12 (above Ubuntu 22.04/Debian 12 system Pythons — uv erases this); `[project.urls]` absent; `pysqlite3-binary` linux-x86_64-only (ARM handling in PLATFORM-REACH); compose pulls `ghcr.io/personalclaw/*`; `GET /api/changelog` reads a nonexistent `CHANGELOG.md` (creation is a PUBLICATION S1 step now).

## Design

### A. Install paths (the user-facing matrix)

| Path | Command | Audience |
|---|---|---|
| **uv tool (recommended)** | `uv tool install personalclaw && personalclaw setup` | anyone; uv provides Python 3.12 itself |
| pipx / pip | `pipx install personalclaw` / `pip install personalclaw` | Python users |
| **Bootstrap one-liner** | `curl -fsSL https://personalclaw.dev/install \| sh` | the README hero; installs uv if absent → uv tool install → runs setup |
| Docker Compose | 2-line snippet using the published-image `compose.yaml` | self-hosters, Windows rung 1 |
| Git checkout | unchanged (contributors) | development |
| Homebrew / Nix | S5, post-launch | mac/nix crowds |

Docs restructure: `getting-started.md` leads with uv-tool; the venv+`make web-build` path moves to CONTRIBUTING (it is now the *contributor* path only). Extras guidance table: `[models]` (heavy, local inference), `[mcp]`, `[bedrock]`, `[js-render]` — what each unlocks and costs.

### B. Packaging correctness (S1)

- Release pipeline order guaranteed: `npm ci && npm run build` → `python -m build` (owned by CI-RELEASE `release.yml`; this plan owns verifying the artifact).
- `[project.urls]` added (Homepage=personalclaw.dev, Documentation, Source, Changelog, Issues).
- **Version single-sourcing:** pyproject is the source; `__init__.__version__` switches to `importlib.metadata.version("personalclaw")` with a static fallback for source-tree runs; a test asserts pyproject/CHANGELOG-latest/`--version` agree at release time.
- `zip-safe = true` dropped (runtime reads packaged files via paths — `frontend.py`, `config/defaults.json`, bundled skills; safe > clever).
- LLM-SDK demotion (`openai`, `anthropic` → extras) is **contingent** on PROVIDER-BOUNDARY-COMPLETION S2's pip-step verification: if first-party apps can declare pip deps (installed at app-install), demote both and let the branded apps carry them (lazy importers already raise; error text gains the exact `pip install personalclaw[openai]` remedy + doctor hint). If not, defer demotion and document the weight honestly. Either way `slack-sdk` exits (plan 32).

### C. Install-kind detection + self-update generalization (S3-S4, class B gated)

- `detect_install_kind() -> {"git","pip","container","desktop"}`: git = `_resolve_project_dir` finds a `.git`; container = `PERSONALCLAW_INSTALL_KIND=container` env baked into the Dockerfiles; desktop = set by the Electron shell (plan 45); else pip.
- **Check (all kinds):** compare running version against the latest GitHub Release tag (`GET /repos/PersonalClaw/PersonalClaw/releases/latest`, cached, offline-tolerant) — replaces commits-behind as the availability signal (tags are the release truth; `main` is for developers). Git installs additionally show commits-behind as secondary info.
- **Apply per kind:** *git* → existing pipeline unchanged (`git pull --ff-only` is safe under the no-force-push model), but gated on a new release tag existing so checkout users ride releases, with a visible "dev mode: update on every commit" override for contributors; *pip* → `pip install -U personalclaw==<tag>` into `sys.prefix` (same interpreter — mirrors the existing `pip install -e .` step) → graceful re-exec (existing machinery); no web build step needed (assets ship in the wheel); *container* → no in-place apply: the panel shows the exact `docker compose pull && docker compose up -d` commands + release notes (honest instructions beat pretending); *desktop* → delegates to electron-updater (plan 45).
- Progress events (`update_progress` WS) and the 409-on-concurrent-apply guard are kind-agnostic and stay.
- Lifecycle: `lifecycle.gates` gate `update_kind_aware` (class B); old git-only behavior retires after one release; no data migration involved (state = none).

### D. Containers (S2)

Images from CI (amd64+arm64). Add `PERSONALCLAW_INSTALL_KIND=container` to both Dockerfiles; verify the published-image compose path end to end on a clean VM (gateway + web/TLS proxy, healthchecks, volume persistence across `compose down/up`); README gets the 2-line snippet; `docs/guides/` gains a container page (ports, volume backup via `personalclaw snapshot` inside the container, env-file pattern per `.env.example`).

### E. Bootstrap script (S2, lives in the website repo)

`install.sh`: POSIX sh; detects OS/arch; installs uv if missing (official installer, checksum-pinned); `uv tool install personalclaw`; prints next steps and offers to run `personalclaw setup`. Idempotent re-runs upgrade. A `--container` flag prints the compose snippet instead. CI smoke (plan 33 full.yml): run the script in a bare ubuntu container weekly.

## Sessions

**S1 — Packaging correctness (≈1).** §B items; build a wheel locally after web-build; install into a clean venv; `personalclaw gateway` serves the SPA with **no Node present**; document the artifact contract in CONTRIBUTING. *Validation:* clean-VM (or empty-container) wheel install → onboarding → first chat, Node absent throughout.
**S2 — PyPI + bootstrap + client (≈1).** First real PyPI publish via the pipeline (after TestPyPI rehearsal, plan 33 S3); `uv tool`/pipx paths verified; bootstrap script written + smoke-tested (ships via website repo, plan 36); publish `personalclaw-client` (Tier-S per plan 31). Docs restructure lands.
**S3 — Containers (≈1).** §D. *Validation:* clean VM, two commands, dashboard reachable, state survives recreation.
**S4 — Self-update generalization (≈1).** §C behind the gate; per-kind validation: git checkout one-tag-behind updates; pip venv one-version-behind updates; container shows instructions; changelog panel renders the real CHANGELOG. Gate default-on for new installs; flip note in CHANGELOG.
**S5 — Convenience channels (≈1, post-launch).** Homebrew tap (formula wrapping the wheel via `uv tool` or brew's python@3.12) + Nix flake; each with a per-release smoke checklist; README install matrix updated.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md); gate/migration per plan 31 §4)

### C1 — Install-kind detection (`dashboard/handlers/updates.py` or new `updates_kind.py`)

```python
InstallKind = Literal["git", "pip", "container", "desktop"]

def detect_install_kind() -> InstallKind: ...
# Resolution order (first hit wins):
#   env PERSONALCLAW_INSTALL_KIND in {"container","desktop"} → that   (baked into Dockerfiles / set by the Electron shell)
#   _resolve_project_dir() contains a .git dir                → "git"
#   else                                                       → "pip"
```

### C2 — Update-check payload (returned by the check endpoint; **Tier-S** wire shape once clients read it)

```jsonc
{
  "kind": "git|pip|container|desktop",
  "current": "0.1.2",              // importlib.metadata.version("personalclaw")
  "latest": "0.1.3",               // from GitHub releases/latest, ETag-cached ~/.personalclaw/update_check.json
  "update_available": true,
  "commits_behind": 4,             // git kind only; else null
  "apply_method": "pipeline|pip_upgrade|instructions|desktop_delegate",
  "instructions": ["docker compose pull", "docker compose up -d"]  // container kind only; else []
}
```

### C3 — Version single-sourcing
`__init__.__version__ = importlib.metadata.version("personalclaw")` guarded by `PackageNotFoundError` → literal fallback for source runs. `tests/test_version_consistency.py`: pyproject `version` == `__version__` == latest `CHANGELOG.md` heading.

### C4 — Wheel contract (`scripts/verify_wheel.py`)
Asserts the built wheel contains `personalclaw/static/dist/index.html`, installs into a scratch venv with NO Node, boots `personalclaw gateway --test-mode`, GETs `/` (200, HTML) and `/api/healthz` (200). Exit 0 = contract met. Run in `release.yml` (plan 33 T3.1).

### C5 — Gate + config
Gate `update_kind_aware` (class **B**, plan 31 §4.1; default OFF, → ON for fresh installs at S4, removal one release later). Config additions (5-point wiring, §2.1): `dashboard.update_dev_mode: bool` (git installs: update per-commit vs per-tag) via `_EDITABLE_CONFIG`. No migration (state = none).

### Integration points
- **Calls:** `_resolve_project_dir`/`_commits_behind_upstream` (existing updates.py), `importlib.metadata`, GitHub releases API (unauth, ETag), `pip` in `sys.prefix`, the existing graceful-re-exec machinery, `gate_enabled("update_kind_aware")`.
- **Called by:** the Settings Updates panel (frontend), `personalclaw` update CLI if any.
- **Owned by pipeline:** `release.yml` (plan 33) builds the artifacts this plan's paths install; `setup.py::BuildWithWeb` (existing) already stages `web/dist` into the wheel — verify, don't rebuild.
- **Coordination:** DESKTOP (45) sets `PERSONALCLAW_INSTALL_KIND=desktop` + consumes the `desktop_delegate` branch; DISCOVERABILITY (36) hosts the `/install` bootstrap script.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Packaging correctness

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Add `[project.urls]` (Homepage https://personalclaw.dev, Documentation, Source, Changelog, Issues — org URLs) | `pyproject.toml` | `pip show personalclaw` lists them after local install |
| T1.2 | Version single-sourcing: `__init__.__version__` = `importlib.metadata.version("personalclaw")` guarded by `PackageNotFoundError` → fallback literal; add consistency test (pyproject version == `__version__` == CHANGELOG latest heading) | `src/personalclaw/__init__.py`, create `tests/test_version_consistency.py` | test red when any of the three disagree (fixture-verified), green on tree |
| T1.3 | Drop `zip-safe = true` | `pyproject.toml` | wheel builds; packaged-data reads unaffected (T1.5 proves) |
| T1.4 | LLM-SDK demotion **(conditional on plan 32 T2.1 outcome — read its Execution log first; if pip-step unsupported, log DEVIATION and skip to T1.5)**: move `openai`, `anthropic` from `dependencies` to their existing extras; branded apps declare the dep via the verified mechanism; sharpen lazy-import error messages in `llm/openai.py`, `llm/anthropic.py`, `stt/openai_provider.py`, `tts/openai_provider.py` to name the exact remedy | `pyproject.toml`, app manifests (apps repo), the four lazy-import sites | clean install without extras: chat via an app-declared provider works after app install; error text on missing SDK names `pip install personalclaw[openai]` |
| T1.5 | Wheel proof: script/CI step that builds (`npm run build` → `python -m build`), installs into a scratch venv, asserts `personalclaw/static/dist/index.html` inside site-packages, boots `personalclaw gateway --test-mode` and GETs `/` + `/api/healthz` | create `scripts/verify_wheel.py` (invoked by release.yml T3.1's unzip check or replacing it) | script exits 0 locally and in CI |
| V1 | Validation: clean VM/container with NO Node: install the wheel → onboarding → first chat. Update `docs/guides/getting-started.md` ONLY after this passes (uv-first rewrite is T2.4) | — | walkthrough passes; Execution log records the environment used |

### Session 2 — PyPI + bootstrap + client

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | First real PyPI publish via release pipeline (owner triggers the environment approval); verify `uv tool install personalclaw` + `pipx install personalclaw` on a clean machine | — (pipeline exists) | both commands yield a working `personalclaw` on PATH |
| T2.2 | Bootstrap `install.sh`: POSIX sh, `set -eu`; OS/arch detect; uv presence check → official installer if absent; `uv tool install personalclaw`; print next steps + offer `personalclaw setup`; `--container` prints compose snippet; idempotent | website repo: `public/install` (plan 36 owns the repo; coordinate — script content is this task's deliverable, land it wherever plan 36 S1 put the repo) | `sh install.sh` on bare ubuntu + macos gets to a working CLI; shellcheck clean |
| T2.3 | Publish `personalclaw-client` to PyPI (name per owner task 1); wire into apps/client CI as a pinned dep where used | `packages/personalclaw-client-py/pyproject.toml` (name/urls check), release workflow addition | `pip install personalclaw-client` works; its tests green in CI |
| T2.4 | Docs restructure: getting-started leads with uv-tool + bootstrap + compose matrix (§A table); venv/`make web-build` path moves to CONTRIBUTING dev-setup; extras guidance table added | `docs/guides/getting-started.md`, `CONTRIBUTING.md` | a stranger following getting-started never runs Node or git |
| V2 | Validation: follow the new getting-started verbatim on a clean machine (uv path) — install → setup → provider app → first chat | — | zero friction points, else fix before close |

### Session 3 — Containers

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `PERSONALCLAW_INSTALL_KIND=container` env in both Dockerfiles | `deploy/docker/Dockerfile.backend`, `Dockerfile.web` | `docker exec … env` shows it |
| T3.2 | Container guide: ports, volumes, `.env` pattern, backup via `personalclaw snapshot` inside the container, update = pull+up | create `docs/guides/containers.md` | doc walkthrough matches T3.3 validation exactly |
| T3.3 | README 2-line compose snippet + install matrix table (§A) | `README.md` | snippet copy-pastes clean on a fresh VM |
| V3 | Validation: clean VM → two commands → dashboard reachable via web container TLS → create session + memory → `compose down && up` → state intact | — | holds |

### Session 4 — Self-update generalization (class B, gate `update_kind_aware`)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | `detect_install_kind()` per Design §C (git/.git probe via `_resolve_project_dir`, container env, desktop env, else pip) + unit tests for all four | `src/personalclaw/dashboard/handlers/updates.py` (or new `updates_kind.py` beside it), tests | four fixtures classify correctly |
| T4.2 | Tag-driven check: latest-release probe (`GET /repos/PersonalClaw/PersonalClaw/releases/latest`, ETag cache in `~/.personalclaw/update_check.json`, offline-tolerant) compared against `__version__`; git kind additionally surfaces commits-behind as secondary | same module | check returns {kind, current, latest, update_available}; offline returns cached view without error |
| T4.3 | Apply per kind behind the gate: git = existing pipeline gated on new-tag-present (+ `dev_mode` config override for every-commit updates); pip = `pip install -U personalclaw==<tag>` into `sys.prefix` → existing graceful re-exec, no web build; container/desktop = structured "instructions" response (exact commands / delegate marker) | same module | git fixture one-tag-behind updates; pip venv one-version-behind updates; container returns instructions payload; 409-on-concurrent preserved |
| T4.4 | Frontend Updates panel: render per-kind states (instructions view for container; dev-mode toggle for git; unchanged progress stream otherwise) | `web/src/pages/settings/` updates panel component (locate via existing `update_progress` consumer) | panel shows correct affordance per kind against a mocked API |
| T4.5 | Register gate `update_kind_aware` (class B, plan DISTRIBUTION, removal = one release after default-on) + CHANGELOG entry; default ON for fresh installs, existing installs flip via migration-less config default | `src/personalclaw/lifecycle/gates.py` registration site, `CHANGELOG.md` | gate listed in `personalclaw gates list`; old path reachable with gate off |
| V4 | Validation: all four kind walkthroughs from §C run as a user (git checkout, wheel venv, container VM, desktop stub env var); changelog panel renders real CHANGELOG.md | — | each kind behaves per design; ledger written |

### Session 5 — Convenience channels (post-launch)

| ID | Task | Files | Done when |
|---|---|---|---|
| T5.1 | Homebrew tap: `personalclaw/homebrew-tap` repo, formula installing via `uv tool` (or brew python) pinned to the latest release; per-release bump automation in release.yml | tap repo, `release.yml` | `brew install personalclaw/tap/personalclaw` works on a clean mac |
| T5.2 | Nix flake: package + `nix run` app output; flake check in full.yml (best-effort, report-only) | `flake.nix`, `flake.lock` | `nix run .#personalclaw -- --version` prints the version |
| V5 | Validation: both channels' smoke checklists executed and recorded | — | holds |

## Owner tasks (real world)

1. **Reserve/publish PyPI names**: `personalclaw` ✅ (pending publisher registered 2026-07-20, env `release`) and `personalclaw-client` (verified free 2026-07-20; register its pending publisher under a SEPARATE GitHub environment `release-client` — PyPI rejects a duplicate (owner, repo, workflow, environment) tuple while pending. The `release-client` env exists with reviewer=keyurgolani). **No TestPyPI** (owner 2026-07-20). release.yml publishes `personalclaw-client` from its own job under `environment: release-client`.
2. **Clean-machine validations** (S1, S3, S4): each needs a machine/VM that has never seen the project — a fresh cloud VM or a wiped container works; ~30 min each, following the validation scripts above.
3. **Homebrew tap decision** (S5): personal tap (`personalclaw/homebrew-tap`) now vs. homebrew-core submission later (core requires notability; tap first).
4. Approve the docs restructure (getting-started leads with uv; venv path moves to CONTRIBUTING) — it changes the project's public "how do I run this" answer.

## Risks & open questions

- **PEP 771-style default extras are not assumed** — the LLM-SDK demotion path explicitly rides the app pip-step verification instead; if both mechanisms disappoint, the SDKs stay hard deps and the boundary doc says why (honest > pure).
- **GitHub API rate limits on update checks** — unauthenticated 60/hr/IP is ample for a personal gateway (check ≤ hourly, cached, ETag'd); no token required or requested.
- **Open:** whether `uv tool install` users get told about `[models]` extras interactively during `setup` (recommended: doctor detects absent local-model deps and prints the exact upgrade command).

---

## Execution log

Format: one line per task/event — `DONE` / `DEVIATION` / `DISCOVERY` / `BLOCKED` — under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md).

### Session 1 — Packaging correctness

- **DISCOVERY (S1, pyproject TOML nesting bug)** — the initial `[project.urls]` insertion was placed AFTER `classifiers` but BEFORE the bare `dependencies` key. Because a TOML sub-table header captures every following bare key until the next header, `dependencies` (and nothing else, since `[project.optional-dependencies]` is its own header) was silently absorbed into `[project.urls]`, leaving `[project].dependencies` empty. A wheel built from that state carried **0 `Requires-Dist`** entries — every runtime dependency would have vanished from `pip install personalclaw`. Fixed by moving `[project.urls]` to sit AFTER the `dependencies` array (immediately before `[project.optional-dependencies]`) and adding an inline guard comment. Verified: `tomllib.load` now reports 20 core deps + 15 extras, and `urls` holds exactly the 5 URL keys.
- **DONE T1.1** — `[project.urls]` added (Homepage=personalclaw.dev, Documentation, Source, Changelog, Issues → org URLs). Evidence: built wheel METADATA carries all 5 `Project-URL:` lines.
- **DONE T1.2** — Version single-sourcing: `personalclaw.__version__` = `importlib.metadata.version("personalclaw")` guarded by `PackageNotFoundError` → `_FALLBACK_VERSION = "0.1.0"` literal for source-tree runs. `tests/test_version_consistency.py` added: asserts pyproject `[project].version` == `__version__` == `_FALLBACK_VERSION` == latest dated `CHANGELOG.md` heading. Evidence: 3 tests pass; `__version__` resolves to `0.1.0` via importlib.metadata under the editable install.
- **DONE T1.3** — `zip-safe = true` dropped from `[tool.setuptools]` (replaced with an explanatory comment); the runtime reads packaged files by path (`frontend.py` serves `static/dist/*`, `config/defaults.json`, bundled skills/apps). Evidence: wheel builds; SPA `personalclaw/static/dist/index.html` is present inside the wheel and packaged-data path reads are unaffected.
- **VERIFY (S1 partial)** — Built the wheel locally (`python -m build --wheel`) and inspected METADATA: 52 `Requires-Dist` (20 core + extras), 5 `Project-URL`, SPA `index.html` present in the wheel. `make lint` fully green (black/isort/flake8/mypy — 451 source files, mypy clean). `tests/test_version_consistency.py` green. T1.4 (LLM-SDK demotion) and T1.5 (`scripts/verify_wheel.py`) and V1 (clean-VM walkthrough) remain.
- **DEVIATION (branch mechanics)** — brief requests a new `feature-distribution` branch off main; the loop engine manages branching (each parallel task runs on its own engine-managed branch that merges back). Per the coder-runtime branch guidance, work is committed on the CURRENT branch rather than a self-created feature branch to avoid stranding the diff. Owner authorship (Keyur Golani / keyurrgolani@gmail.com) + DCO sign-off preserved; no agent co-author trailer.

### Session 1 (cont.) — Cycle 2

- **PREMISE CONFIRMED (T1.4 precondition)** — read PROVIDER-BOUNDARY-COMPLETION Execution log: T2.1 DONE — `apps/app_manager.py::_install_python_deps` (L142, called from install L365 + update L529) DOES pip-install manifest `dependencies.pythonDependencies` into the shared venv. The app pip-step mechanism is SUPPORTED, so T1.4's demotion proceeds (not the DEVIATION/skip branch).
- **DONE T1.4 (core-repo half)** — Demoted `openai` and `anthropic` out of core `[project].dependencies`; they remain as the `[openai]` / `[anthropic]` extras. Added `src/personalclaw/_sdk_deps.py` with `require_sdk(module, extra, feature=None)` → returns the module or raises `MissingSDKError(ImportError)` naming `pip install 'personalclaw[<extra>]'` + `personalclaw doctor`. Wired the four lazy-import sites: `llm/openai.py` (ctor), `llm/anthropic.py` (ctor) route through `require_sdk`; `stt/openai_provider.py` + `tts/openai_provider.py` runtime paths now log the exact `[openai]` remedy (their `is_available()` `except ImportError` probes still work — `MissingSDKError` subclasses `ImportError`). Added `tests/test_sdk_deps.py`. Doctor needs no change: its core-deps probe checks only `websockets, aiohttp`, and provider health is per-provider with no hardcoded openai/anthropic list (plan 32). CHANGELOG `### Changed` entry added.
  - Evidence: `tomllib` → 18 core deps, openai/anthropic absent from core, present as extras. Rebuilt wheel METADATA: openai/anthropic appear ONLY as `Requires-Dist: openai>=1.0; extra == "openai"` / `anthropic; extra == "anthropic"` (no unconditional line); total Requires-Dist 52→50. Both providers construct in the dev venv (require_sdk returns the real module). `test_sdk_deps.py` + `test_llm_helpers.py` green; lint (black/isort/flake8/mypy) clean on all touched files.
- **CROSS-REPO ACTION (T1.4, apps repo `PersonalClawApps` — maintainer hand-applies)** — the branded OpenAI/Anthropic provider apps must declare their SDK so the installer pulls it. In each app's `app.json` (`manifest.json`), add under the top-level `dependencies` object (create it if absent), mirroring the slack-channel precedent (plan 32 T1.5, commit `7538b63`):
    - openai-models app `app.json`:
      ```json
      "dependencies": { "pythonDependencies": ["openai>=1.0"] }
      ```
    - anthropic-models app `app.json`:
      ```json
      "dependencies": { "pythonDependencies": ["anthropic>=0.20"] }
      ```
    (If the OpenAI STT/TTS providers ship as their own apps rather than under openai-models, add the same `openai>=1.0` line to those manifests.) After install/update the pipeline pip-installs these into the shared venv; a fresh dep requires a gateway restart (`restart_required` in the install result). No core-repo file carries this — it lands in the apps repo.

### Session 1 (cont.) — Cycle 3

- **DONE T1.5 (wheel contract, C4)** — Authored `scripts/verify_wheel.py` (stdlib-only): asserts `personalclaw/static/dist/index.html` is in the wheel, installs the wheel into a fresh scratch `venv` (from the wheel alone — no source tree, no npm), boots `personalclaw gateway --test-mode` (reading the deterministic `PERSONALCLAW_READY:{json}` line for the auto-selected port), and probes `GET /api/healthz` (200 JSON `{status: ok, version}`) + `GET /` (200 HTML SPA shell). Runs with `PERSONALCLAW_AUTH_MODE=none` (loopback-pinned by `effective_bind`) so `/` serves in the smoke. Exit 0 = contract met. `--build` / `--wheel GLOB` / `--keep` flags.
  - Wired into `release.yml`: replaced the shallow `index.html`-in-namelist check with `python scripts/verify_wheel.py --wheel "dist/*.whl"` (release.yml still valid YAML).
  - Evidence (local run, dev box): built `dist/personalclaw-0.1.0-py3-none-any.whl` (9.1 MB), ran the verifier → PASS: SPA present; wheel installed into scratch venv; gateway READY on 127.0.0.1:50494; `/api/healthz` → 200 `{status: ok, version: 0.1.0}`; `/` → 200 HTML. lint clean (black/isort/flake8, mypy on the script). NOTE: this dev box has Node on PATH so the script printed the can't-*prove*-Node-absence warning — the true no-Node proof runs on the CI runner's fresh verify venv / a clean VM (V1).
  - S1 now has T1.1–T1.5 implemented + locally verified. V1 (clean-VM/empty-container wheel install → onboarding → first chat, Node absent throughout) remains an owner real-world step; the getting-started uv-first rewrite is deferred to T2.4 per the plan.

### Session 2 — Cycle 4

- **DONE T2.3 (personalclaw-client packaging + CI)** — Enriched `packages/personalclaw-client-py/pyproject.toml`: added `readme`, `license` (MIT), `authors`, `keywords`, full `classifiers` (py3.9–3.13, OS-Independent, AsyncIO), `[project.urls]` (Homepage/Documentation/Source/Issues — placed AFTER `dependencies` to avoid the TOML-absorb footgun), and `[tool.setuptools.packages.find]`. Added `packages/personalclaw-client-py/README.md` (rendered on PyPI — `Description-Content-Type: text/markdown`). Added a `client` job to `ci.yml` (builds the client wheel + runs its 18-test suite in a fresh venv) so a break is caught pre-merge — the client was previously untested in CI. `.gitignore`: `dist-client/`, `.venv-client/`.
  - Evidence: client wheel METADATA now carries Name=personalclaw-client, Requires-Python>=3.9, License MIT, 4 Project-URL, 9 Classifier, markdown description; `tomllib` → 1 dep, urls clean (no absorbed `dependencies`). Locally simulated the CI client job (build → fresh-venv `[dev]` install → pytest): 18 passed. `ci.yml`/`release.yml` valid YAML. The pre-existing `release.yml` client job (env `release-client`) already publishes it; owner registered the pending publisher (owner task 1).
  - Note: one client test (`test_contains_app_name`) asserts `.personalclaw` in the data-dir path; it fails ONLY inside this loop because the loop sets `PERSONALCLAW_HOME=.dev-home`. On CI (ephemeral `$HOME`, no override) and in a clean env it passes — verified with `env -u PERSONALCLAW_HOME`. Left as-is (not a client bug); the new CI job runs without a global home so it stays green.
- **T2.1 / T2.2 (owner + website repo):** T2.1 (first real PyPI publish + `uv tool install` / `pipx` verify on a clean machine) is an owner real-world step — owner states publishing is handled (pending publisher registered 2026-07-20, env `release`). T2.2 (`install.sh` bootstrap) lives in the personalclaw.dev website repo (plan 36 owns) — its content is produced as a deliverable in a later cycle's Execution-log instruction block (cross-repo). Neither has core-repo code.
- **DONE T2.4 (docs restructure)** — Rewrote `docs/guides/getting-started.md` to lead with the §A install matrix: **uv tool** (recommended, brings its own Python 3.12), the `curl … | sh` bootstrap one-liner, pipx, pip, and Docker Compose — with the git-checkout path redirected to CONTRIBUTING. Dropped the git-clone/venv/`make web-build` first-run steps (that's the contributor path). Added an **extras guidance table** (openai/anthropic/bedrock/mcp/js-render/models — what each unlocks + rough weight + the exact `pip install 'personalclaw[…]'` / `uv tool install 'personalclaw[…]'`) and a pre-1.0 breaking-changes banner advising `personalclaw snapshot`. Added a Docker Compose section (`cp .env.example .env` → `docker compose -f deploy/compose/compose.yaml up -d`, port 10000, persistent volume, links to the container guide) and fixed troubleshooting so SPA-not-built notes apply to source checkouts only. `CONTRIBUTING.md` "Development setup" now explicitly frames itself as the build-from-source/contributor path and cross-links getting-started (the venv/`make web-build` path already lived there — plan 32's CI work; no move needed, just framing).
  - Forward reference: getting-started links `containers.md` (created in S3 T3.2) — a deliberate stub link to keep the matrix complete; the file lands with S3.
  - Owner note (plan owner task 4): this changes the project's public "how do I run this" answer (uv-first, contributor path demoted) — flagged for owner approval; implemented per T2.4 since it's the assigned task and the wheel/serve contract is proven locally (T1.5). V2 (follow the new getting-started verbatim on a clean machine) is an owner real-world validation step.

### Session 3 — Cycle 6

- **DONE T3.1 (container install-kind env)** — Added `PERSONALCLAW_INSTALL_KIND=container` to both Dockerfiles: `deploy/docker/Dockerfile.backend` runtime `ENV` block (the gateway process that runs `detect_install_kind()` — S4 T4.1) and `deploy/docker/Dockerfile.web` (nginx front-end sets it too for deployment-wide consistency, with a comment that the backend is the real consumer). Matches C1's resolution order (env `PERSONALCLAW_INSTALL_KIND=container` wins first). Nothing reads it yet — S4 T4.1's `detect_install_kind()` will. Verified the ENV lines are present in both files; a running container's `docker … exec env` would show it (the done-criterion — Docker not runnable in this sandbox, so asserted by file inspection).
- **DONE T3.2 (container guide)** — Created `docs/guides/containers.md`: services table (gateway + web), ports (3000→HTTPS 3443 app, gateway 10000 loopback), the `personalclaw_home` volume + `down` vs `down -v` warning, the `.env` pattern (image tag / port / bind host / auth mode / api key), getting the dashboard URL (`docker compose … exec personalclaw-gateway personalclaw token` or api_key mode), backups via `personalclaw snapshot` inside the container, updates = `docker compose … pull && up -d` (no in-place self-update — mirrors the S4 container branch), the opt-in `with-slack` profile, and troubleshooting (502 self-heal, self-signed cert). This resolves the forward reference from getting-started (T2.4). Used project-name-agnostic `docker compose … exec/cp` invocations (not a guessed `<project>-<svc>-1` container name). `CHANGELOG` link path (`../../CHANGELOG.md`) resolves.
  - Remaining S3: T3.3 (README 2-line compose snippet + §A install matrix table) and V3 (owner clean-VM: two commands → dashboard reachable via TLS → create session+memory → `compose down && up` → state intact).
- **DONE T3.3 (README install matrix + compose snippet)** — Rewrote the README `## Quickstart`: leads with `uv tool install personalclaw && personalclaw setup` (recommended) + the `curl … | sh` bootstrap one-liner, then a §A **install matrix** table (uv tool / bootstrap / pipx / pip / Docker Compose / git-checkout→CONTRIBUTING), and a **2-line Docker Compose snippet** (`cp .env.example .env && docker compose -f deploy/compose/compose.yaml up -d`) linking the container guide. Replaced the old git-clone + venv + `make web-build` quickstart (that path now lives in CONTRIBUTING, linked). Escaped pipe in the bootstrap table cell; links to `CONTRIBUTING.md#development-setup` and `docs/guides/containers.md` resolve.
  - S3 core-repo/docs tasks (T3.1 Dockerfile env, T3.2 container guide, T3.3 README) are done. V3 (owner clean-VM: two commands → dashboard via TLS → create session+memory → `compose down && up` → state intact) is an owner real-world validation step (Docker not runnable in this sandbox).

### Session 4 — Cycle 8 (self-update generalization — CLEAN BREAK)

- **DEVIATION (C5 / T4.5 gate — recorded per brief):** the plan wires S4 behind a `lifecycle.gates` gate `update_kind_aware` (class B, plan 31 §4). Per the owner decision (2026-07-20) S4 is taken as a **plain clean break**: there is NO `lifecycle/gates.py` machinery in the tree (verified — only `loop/gates.py`, an unrelated auto-nudge gate), and LIFECYCLE-DOCTRINE (plan 31) is deferred to the Stage 2→3 boundary. So the per-kind self-update behavior is implemented **directly, without gate/registration machinery**; the old git-only path is replaced, not gated. A CHANGELOG entry lands with T4.5 and release notes will advise `personalclaw snapshot`. No `lifecycle/gates.py` registration is created (the module is absent).
- **DONE T4.1 (`detect_install_kind`)** — New module `src/personalclaw/dashboard/handlers/updates_kind.py`: `detect_install_kind() -> InstallKind` (`git|pip|container|desktop`) per C1 resolution order — env `PERSONALCLAW_INSTALL_KIND` in {container,desktop} wins first (case-insensitive, junk ignored), else a resolvable `PERSONALCLAW_PROJECT_DIR` whose dir (or monorepo parent) contains a `.git` entry (dir OR worktree/submodule file) → `git`, else `pip`. Pure/no-side-effect. Added `tests/test_updates_kind.py` — 10 tests covering all four kinds + case-insensitivity, junk-env fall-through, `.git` file (worktree), monorepo-parent `.git`, and no-git→pip.
  - Evidence: 10/10 tests pass; `make lint` green (black/isort/flake8/mypy — 453 source files). Reuses the existing `PERSONALCLAW_PROJECT_DIR` signal (set by `cli._detect_project_dir`) so git-vs-pip matches how the gateway already locates its source tree.
  - Remaining S4: T4.2 (tag-driven latest-release check w/ ETag cache, offline-tolerant, git adds commits-behind), T4.3 (per-kind apply), T4.4 (frontend Updates panel), T4.5 (CHANGELOG + `dashboard.update_dev_mode` config round-trip). V4 = owner per-kind walkthroughs.
- **DONE T4.2 (tag-driven check + C2 payload)** — Extended `updates_kind.py`: `fetch_latest_release()` GETs `api.github.com/repos/PersonalClaw/PersonalClaw/releases/latest` with `If-None-Match` from a cache at `config_dir()/update_check.json` — 304 or ANY network error degrades to the cached view without raising (offline-tolerant); a 200 re-caches `{tag,name,body,etag,checked_at}`. `build_update_status(current)` assembles the **C2** payload `{kind, current, latest, update_available, commits_behind, apply_method, instructions}` (+ `release_name`/`release_notes`): `latest` from the release tag (normalized, `v0.1.3`==`0.1.3`), `update_available` a numeric version compare, `apply_method` per kind (git=`pipeline`, pip=`pip_upgrade`, container=`instructions`, desktop=`desktop_delegate`), git kind adds `commits_behind` via the existing `_commits_behind_upstream`, container carries the `docker compose … pull && up -d` instructions. Unauthenticated GitHub API (60/hr/IP, ETag'd, ≤hourly) — no token. Added 8 T4.2 tests (version normalize/compare, cache round-trip, per-kind status, offline→no false "update available", offline→cached view). Tests are hermetic (no real network — the offline test monkeypatches `aiohttp.ClientSession` to raise and asserts the cached view returns).
  - Evidence: 18/18 tests in `tests/test_updates_kind.py` pass; `make lint` green (mypy 453 files). Not yet wired into the check endpoint — that lands with T4.3 (apply) + the endpoint swap, to keep this cycle atomic.
  - Remaining S4: T4.3 (per-kind apply: git=pipeline gated on new tag + `update_dev_mode` override; pip=`pip install -U personalclaw==<tag>` into `sys.prefix` → graceful re-exec, no web build; container/desktop=instructions; keep the 409 guard), T4.4 (frontend Updates panel per-kind), T4.5 (CHANGELOG + `dashboard.update_dev_mode` config round-trip). V4 = owner per-kind walkthroughs.
- **DONE T4.3 (per-kind apply) + T4.5 config round-trip** — Wired `GET /api/update/check` to merge the C2 `build_update_status` payload (all kinds get the tag-driven `update_available`; legacy git changelog-diff fields kept for the current panel; adds `update_dev_mode`). `POST /api/update` now branches on `detect_install_kind()`: **container/desktop** return a structured `{status:"instructions", kind, apply_method, instructions}` payload (no apply runs, no in-flight slot claimed); **pip** routes to new `_apply_pip_update()` — `pip install -U personalclaw==<tag>` (pinned to the latest release tag when known, else unpinned `-U`) via `sys.executable` into the running prefix → graceful re-exec, NO web build (assets ship in the wheel), sharing the 409 concurrent-apply guard; **git** keeps the existing pull→`pip install -e .`→build→re-exec pipeline, now consulting `dashboard.update_dev_mode` (advisory — the commits-behind probe already short-circuits nothing-to-pull to a plain restart; the tag-availability signal is surfaced by the check endpoint, keeping the apply path hermetic/no-network).
    - **T4.5 config round-trip (done here, needed by T4.3):** added `DashboardConfig.update_dev_mode: bool = False` with `_meta` (title/description), wired into `from_dict` (`dashboard_data.get("update_dev_mode", False)`) and serialized via the existing `asdict(self.dashboard)` in `to_dict`. Verified the round-trip: a `config.json` with `dashboard.update_dev_mode=true` loads to `True` and re-serializes. (The frontend control lands with the T4.4 panel.)
    - Evidence: 50 tests pass (`test_config_roundtrip` + `test_updates_kind` + new `test_update_apply_kind` [container→instructions, desktop→instructions, pip→routes to pip update] + `test_update_progress` — the 5 git-pipeline tests updated to create a `.git` dir so `detect_install_kind`→git, matching their intent). `make lint` green (mypy 453). Apply path is hermetic (build_update_status only called in the container/pip branches, monkeypatched in tests).
    - Remaining S4: T4.4 (frontend Updates panel: per-kind states — instructions view for container, dev-mode toggle for git, unchanged progress stream — locate via the existing `update_progress` consumer + the `update_dev_mode` config control), T4.5 CHANGELOG entry (the config field is done; the CHANGELOG + release-note snapshot advice remain). V4 = owner per-kind walkthroughs.
- **DONE T4.4 (frontend Updates panel per-kind) + dev-mode endpoint** — Backend: added `POST /api/update/dev-mode` (`api_update_dev_mode`) persisting `dashboard.update_dev_mode` (nested; preserves other dashboard keys; boolean-validated), exported it, and registered the route beside `/api/update/auto`. Frontend (`web/src/pages/settings/UpdatesPanel.tsx` + `lib/api.ts`): extended the `UpdateCheck` type with the C2 fields (`kind, current, update_available, commits_behind, apply_method, instructions, update_dev_mode, release_notes`); the panel now renders per-kind — shows the **install type** label; git surfaces **commits-behind** as secondary text + a **Developer update mode** toggle (track commits vs release tags); container hides the in-app Update button and shows the exact `docker compose … pull && up -d` commands (from the apply response's `instructions`); desktop shows a "updates itself on next launch" note; pip/git keep the in-app Update button + the unchanged progress-overlay stream. Added `setUpdateDevMode` to the api client.
    - Evidence: web `npm run typecheck:web` clean, `npm run build` succeeds, `npm run test:web` 70/70 pass. Backend: 3 new dev-mode endpoint tests (persists nested, rejects non-bool, preserves other dashboard keys + real load round-trip) — 6/6 in `test_update_apply_kind`. `make lint` green (mypy 453). `web/dist` not committed (gitignored).
    - Remaining S4: T4.5 CHANGELOG entry for the class-B/S self-update behavior change (the `update_dev_mode` config field + per-kind apply are done; the CHANGELOG note + release-note `snapshot` advice remain). V4 = owner per-kind walkthroughs (git one-tag-behind updates; pip venv one-version-behind updates; container shows instructions; desktop stub; changelog panel renders real CHANGELOG.md).
- **DONE T4.5 (CHANGELOG entry) — S4 code+docs complete** — Added a CHANGELOG `### Changed` entry for the install-kind-aware self-update: tag-driven availability signal (ETag-cached, offline-tolerant), per-kind apply (git pipeline + `dashboard.update_dev_mode` toggle / pip `-U` + re-exec, no web build / container pull+recreate instructions / desktop delegate), and the per-kind Updates panel. Explicitly notes the **clean-break / no-gate DEVIATION** (no `update_kind_aware` gate — LIFECYCLE-DOCTRINE deferred, owner decision 2026-07-20), the behavior change (git now rides release tags by default; dev-mode restores per-commit), and **advises `personalclaw snapshot` before updating** — all per the brief.
    - **FULL-SUITE REGRESSION VALIDATION (verify/test gate):** with the loop's env overrides cleared to mirror the CI runner's no-global-home contract (`env -u PERSONALCLAW_HOME -u PERSONALCLAW_BIND_HOST -u PERSONALCLAW_BYPASS_LOCAL_NETWORKS … PERSONALCLAW_SKIP_APP_BACKENDS=1 pytest`), the **full suite is green: 7696 passed, 28 skipped, 13 xfailed, 0 failed.** (A naive run inside the loop shows 34 failures that are entirely the loop's `PERSONALCLAW_HOME=.dev-home` + `PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1` overrides — config-dir/token-auth/home-path tests — NOT the changes; CI does not set these, per ci.yml's documented contract.) `make lint` green (mypy 453). Web gate green (typecheck + build + 70 vitest, cycle 11). Version-consistency test green (CHANGELOG edit is under [Unreleased], not a dated heading).
    - S4 tasks T4.1–T4.5 are implemented + validated. **V4** (owner per-kind real-world walkthroughs) is the remaining owner step. S5 (Homebrew/Nix) is OUT of scope per the brief — STOPPING at S4.

### Cross-repo deliverables — Cycle 13

- **DONE T2.2 (bootstrap `install.sh` — content produced)** — Wrote the actual bootstrap script at `deploy/website/install.sh` (staged in-repo for the website repo, plan 36): POSIX sh (`set -eu`, validated with `sh -n` AND `dash -n`), OS/arch detect, `uv` presence check → official `astral.sh/uv/install.sh` if absent (curl→wget fallback; PATH fix-up for `~/.local/bin`/`~/.cargo/bin`), `uv tool install --upgrade personalclaw` (idempotent — re-runs upgrade), prints next steps + offers `personalclaw setup` (TTY-guarded so `curl | sh` never hangs), and a `--container` flag that prints the compose snippet. Verified: `--help`, `--container`, and unknown-arg (exit 1) paths work; the PATH loop is `set -e`-safe. Added `deploy/website/README.md` consolidating both cross-repo instruction blocks (install.sh → website `/install`; openai/anthropic `pythonDependencies` → apps repo manifests from T1.4) + the owner V-row/T2.1 checklist. **Apply:** copy `install.sh` to the website repo's static-assets path, serve at `/install` as `text/plain`, wire the plan-33 weekly smoke + shellcheck.
  - Evidence: `sh -n deploy/website/install.sh` + `dash -n` both clean; `--container` prints the compose snippet; `--help` prints usage; `--bogus` exits 1. The script is chmod +x. No core-repo behavior touched (staging + docs only).

---

## Execution log — Closeout summary (S1–S4 complete; S5 out of scope)

**All core-repo + staged deliverables for Sessions S1–S4 are implemented and validated locally.** Commits on `main` (owner authorship + DCO, one conceptual commit per task):

| Task | Commit | Deliverable |
|---|---|---|
| T1.1–T1.3 | `6b0104e` | `[project.urls]`, single-source `__version__` + consistency test, drop `zip-safe`; fixed the TOML nesting bug that had zeroed the wheel's `Requires-Dist` |
| T1.4 | `71db09f` | Demote `openai`/`anthropic` to extras; `_sdk_deps.require_sdk`/`MissingSDKError` at the 4 lazy-import sites |
| T1.5 | `eb5f0c4` | `scripts/verify_wheel.py` (contract C4) + wired into `release.yml` |
| T2.3 | `13e46fd` | `personalclaw-client` packaging metadata + a `client` CI job |
| T2.4 | `2b880d7` | getting-started uv-first install matrix + extras table; CONTRIBUTING framed as contributor path |
| T3.1 + T3.2 | `b93c337` | `PERSONALCLAW_INSTALL_KIND=container` in both Dockerfiles; `docs/guides/containers.md` |
| T3.3 | `843c3cd` | README install matrix + 2-line compose snippet |
| T4.1 | `ded75c0` | `updates_kind.detect_install_kind()` (C1) + tests |
| T4.2 | `aac5a84` | tag-driven check + ETag cache + C2 payload (`build_update_status`) |
| T4.3 + T4.5(config) | `880131d` | per-kind apply (git/pip/container/desktop) + `dashboard.update_dev_mode` round-trip |
| T4.4 | `b22cc8f` | per-kind Updates panel + `POST /api/update/dev-mode` |
| T4.5 (changelog) | `a65d2fd` | CHANGELOG entry for the kind-aware self-update (clean-break, snapshot advice) |
| T2.2 | `5bc4c98` | `deploy/website/install.sh` bootstrap + cross-repo instruction README |

**Validation at closeout:** `make lint` green (black/isort/flake8/mypy — 453 source files); full pytest suite green under the CI no-global-home contract (**7696 passed, 0 failed** — the only in-loop "failures" are the loop's `PERSONALCLAW_HOME`/`BYPASS_LOCAL_NETWORKS` env overrides, which CI does not set); web gate green (`typecheck:web` + `build` + 70 vitest); built wheel proven Node-free-servable by `scripts/verify_wheel.py`.

**DEVIATIONS (recorded above, restated):** (1) S4 self-update is a **clean break** — no `update_kind_aware` gate (C5/T4.5) because `lifecycle/gates.py` does not exist (LIFECYCLE-DOCTRINE deferred; owner decision 2026-07-20); the old git-only updater is replaced directly, CHANGELOG advises `personalclaw snapshot`. (2) Work committed on the loop's base branch (engine manages branching) rather than a self-created `feature-distribution` branch.

**OWNER real-world steps remaining (no core-repo code):** T2.1 (first PyPI publish via `release.yml` env `release` + verify `uv tool`/`pipx` on a clean machine); V1 (clean-VM/empty-container wheel install → onboarding → first chat, Node absent); V2 (follow the new getting-started on a clean machine); V3 (container clean-VM: two commands → TLS dashboard → session+memory → `compose down && up` → state intact); V4 (per-kind self-update walkthroughs). Cross-repo hand-apply (staged in `deploy/website/README.md`): `install.sh` → website `/install` (plan 36); openai/anthropic `pythonDependencies` → apps-repo provider manifests.

**S5 (Homebrew tap / Nix flake): OUT of scope for this loop (owner decision 2026-07-21). STOPPED at S4 — did not enter S5.**

### Post-closeout hardening — Cycle 15

- **HARDENING (T4.3 — `update_dev_mode` now enforced, not advisory)** — Self-audit found the git-apply `update_dev_mode` read was only logged, so the toggle had no user-visible effect (a "not done until a user can use it" gap). Fixed hermetically: the git apply path now reads the CACHED release view (`updates_kind._read_cache` — no network) and, when dev mode is OFF and the running version is ≥ the latest cached release tag, degrades to the existing restart-only path even if the upstream has new commits (ride release TAGS, not commits). Dev mode ON keeps the full pull pipeline (track commits). Added `test_dev_mode_off_on_latest_tag_restarts_only` (commits-behind=3 + cached tag == current + dev_mode off → `restarting` fires, `pulling` does not, no git pull subprocess runs). Evidence: `test_update_progress` 25 passed; `make lint` green (mypy 453). The toggle added in T4.4/T4.5 now has a real, tested effect end-to-end.

### Post-closeout regression check — Cycle 16

- **WHEEL RE-VERIFICATION (soul guardrail)** — After all S1–S4 code changes (T1.4 dep demotion, T4.x updater modules), rebuilt the wheel and re-ran `scripts/verify_wheel.py` end-to-end: **PASS** — SPA packaged, installs from the wheel alone into a scratch venv, `personalclaw gateway --test-mode` boots, `/api/healthz` → 200 `{status: ok, version: 0.1.0}`, `/` → 200 HTML. Re-confirmed the freshly-built wheel's METADATA: **18 core `Requires-Dist`; `openai`/`anthropic` present ONLY as `extra ==` entries** (T1.4 demotion holds). Proves the leaner dependency set does not break the gateway's boot/serve path — the one release artifact every channel projects is still self-contained and servable Node-free.

### Post-closeout contract audit — Cycle 17

- **C2 WIRE-SHAPE CONFORMANCE (Tier-S) — audited + locked** — Verified `build_update_status` emits exactly the C2 contract keys (`kind, current, latest, update_available, commits_behind, apply_method, instructions`) with the pinned per-kind semantics: `apply_method` git=`pipeline`/pip=`pip_upgrade`/container=`instructions`/desktop=`desktop_delegate`; `commits_behind` = null for non-git; `instructions` = 2 compose commands for container, `[]` otherwise; `current`/`latest` normalized (no leading `v`). Two additive extras (`release_name`, `release_notes`) are a backward-compatible superset. Added `test_c2_wire_shape_conformance` locking the shape so a future refactor can't silently drift the Tier-S wire shape. Evidence: `test_updates_kind` 19 passed; `make lint` green (mypy 453).

### Cross-repo hand-applies COMPLETED — 2026-07-21

The two T2.2 / T1.4 sibling-repo deliverables that closeout had left staged (they land
outside the core repo, so the core-scoped loop couldn't apply them) are now **applied and
validated in the sibling repos** — closing the live gap where the README hero command
`curl -fsSL https://personalclaw.dev/install | sh` (README.md:137,151; getting-started.md:30)
resolved to a 404.

- **DONE — `install.sh` → `personalclaw.dev`** — Copied `deploy/website/install.sh` to the
  website repo at `public/install` (Astro serves `public/` verbatim → `/install`); added a
  `/install` header rule in `vercel.json` for `Content-Type: text/plain; charset=utf-8`.
  Validated: `sh -n` + `dash -n` clean; `astro check` OK; `npm run build` OK; `dist/install`
  is byte-identical to the staged source; `validate:build` OK (HTML-route validator is
  unaffected by the extension-less asset). **Owner note:** the full website pre-push gate
  (`npm run test:prepush` → Playwright visual + Lighthouse) still runs at push time per
  CLAUDE.md §5; the correctness-relevant subset was run here.
- **DONE — provider-app `pythonDependencies` → `PersonalClawApps`** — Corrected the scope
  the staged note under-counted: the SDK is needed by **all 12 apps** that build core's
  `OpenAIProvider`/`AnthropicProvider` (inference uses the vendor SDK client — `llm/catalog.py:322`),
  not just `openai-models`/`anthropic-models`. Added `"openai>=1.0"` to the 10 openai-wire apps
  (openai-models, openai-compatible, alibaba-, deepseek-, google-, groq-, mistral-, together-,
  vllm-models, meta-muse-spark) and `"anthropic>=0.20"` to the 2 anthropic-wire apps
  (anthropic-models, anthropic-compatible); specifiers match core's canonical extras. Not touched:
  ollama-models (httpx is a core dep), bedrock-models (already declares boto3), openai-tools
  (REST via net.fetch). Validated: all 38 apps-repo manifests parse + round-trip stable against
  core's `AppManifest`; the 12 edited manifests each expose their dep through the parser; per-bundle
  app tests green (openai-models 16, anthropic-models 19, vllm-models 19, google/deepseek/groq/
  mistral/together 9 each, openai-compatible + anthropic-compatible 7 each).

**Remaining plan-34 items are now owner real-world tasks** (need PyPI credentials or a clean
machine): T2.1 first PyPI publish; V1–V4 clean-machine walkthroughs. The sibling-repo changes
are committed on branches pending owner review + the per-repo push gates (website
`feature-install-endpoint`: `test:prepush` green locally; apps `feature-provider-sdk-deps`:
manifest-validate/tests/boundary green locally).

### Release dry-run — RELEASE-BLOCKING BUG found + fixed (2026-07-21)

Before queuing the first `v0.1.0` tag, dry-ran the exact `release.yml` build path locally
(`npm run build` → `python -m build` → `scripts/verify_wheel.py`). **`verify_wheel.py` FAILED:
the wheel carried no SPA.** Root cause: `python -m build` (no args — the release + `make build`
command) builds the sdist first, then builds the wheel **from the sdist**; `setup.py`'s
`BuildWithWeb` only copies `web/dist` when it exists in the build tree, but the sdist never
included `web/dist` (no `MANIFEST.in`), so the wheel-from-sdist shipped SPA-less. The earlier
"wheel proven servable" evidence used `python -m build --wheel` (builds from the source tree,
where `web/dist` exists) — a **different** command than the release job runs — so the defect
stayed latent, and the release pipeline had **never actually run** (no tag had ever been pushed).
Fix on branch `fix-sdist-bundles-spa`: add `MANIFEST.in` grafting `web/dist` into the sdist (the
sdist is now self-contained too), guarded by `tests/test_sdist_bundles_spa.py` + a CHANGELOG
Fixed entry. Re-ran `python -m build` (no args) + `verify_wheel.py`: **PASS** — wheel carries the
SPA (453 assets), installs Node-free into a scratch venv, boots `gateway --test-mode`, serves
`/` (200 HTML) + `/api/healthz` (200). This doubles as the S1/V1 Node-absent evidence. **This
bug must land before the first tag push** or the release job's verify-wheel step fails.
