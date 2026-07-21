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
