# Plan: CI & Release Engineering — Verifiable Quality + a Real Release Pipeline

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18 from the pre-launch investigation & owner alignment review)
**Created:** 2026-07-18
**Wave:** 0 — precondition for PUBLICATION credibility and for every plan that ships artifacts (DISTRIBUTION, PLATFORM-REACH, DESKTOP-CAPABILITIES).
**Depends on:** nothing hard. DISTRIBUTION consumes the release pipeline; PROVIDER-BOUNDARY-COMPLETION and LIFECYCLE-DOCTRINE contribute checks that mount in this plan's workflows.
**Scope:** CI for both repos + a tag-triggered release pipeline + supply-chain hygiene matching the product's own install-gate preaching. **Owner design constraint: efficient but not rigid** — coverage and confidence without becoming the development bottleneck. **Soul guardrail:** CI is a verification substrate, not bureaucracy — no mandatory-review gates on a solo repo, no flaky-test tolerance ("green by default, exceptions annotated in code"), and no acceleration machinery (path filters, test selection) until a *measured* PR wall-time exceeds the budget (measured-bottleneck-gated, the HARNESS-CRAFT discipline).

---

## Context (code recon, 2026-07-18)

- No `.github/` in either repo. Local tooling is strong and CI-ready as-is: `make lint` = black --check + isort --check + flake8 + mypy; `make test` = pytest with `-n auto --dist worksteal --timeout=120 --cov` (426 test files, hypothesis + xfail_strict); `web`: `npm run build` = `tsc --noEmit && vite build`, `npm test` = vitest.
- Known-red baseline (from the publication campaign): `test_process_tree`, `provider_helpers`, `registry_config_sync`, +10 gateway cron-callback failures — documented in a plan file, invisible in code.
- No Python lockfile anywhere. Docker: `deploy/docker/Dockerfile.backend` is already layer-optimized (stub-package dep layer cached on `pyproject.toml`; base `python:3.13-slim`); compose auto-detects docker/podman/finch. Compose pulls `ghcr.io/personalclaw/*` — images no pipeline currently builds.
- Apps-repo tests are CI-friendly by construction: they stub vendor SDKs into `sys.modules` (verified in `anthropic-models/test_provider.py`) — CI needs core installed for `personalclaw.sdk.*` imports but NOT vendor SDKs.
- Destructive-test isolation rule (CONTRIBUTING): tests must never touch a real `~/.personalclaw` — CI runners are clean by nature, but keep `PERSONALCLAW_HOME` pointed at a temp dir in workflow env as a belt-and-suspenders rail.

## Design

### Workflow set — core repo (`.github/workflows/`)

| File | Trigger | Jobs |
|---|---|---|
| `ci.yml` | PR + push to feature branches | **lint** (black/isort/flake8/mypy, uv-cached); **test** (ubuntu, py3.12, full suite via xdist — single job; see budget note); **web** (npm ci, typecheck, vitest, vite build); **rails** (residue sweep from plan 32, gate-lifetime lint + stability drift from plan 31 — cheap, one job). `concurrency: cancel-in-progress` per ref. |
| `full.yml` | push to `main` + nightly cron | full matrix {3.12, 3.13} × {ubuntu, macos}; coverage XML artifact + badge update; `pip-audit` + `npm audit` (report-only); the frontend URL-doctrine and config round-trip tests are implicitly in the suite — named in the job summary for legibility. |
| `release.yml` | tag `v*` | build sdist+wheel **with prebuilt `web/dist`** (runs `npm ci && npm run build` first; packaging change itself is DISTRIBUTION S1) → `pypa/gh-action-pypi-publish` via **Trusted Publishing** (no stored tokens) → buildx **linux/amd64+arm64** images pushed to GHCR (auth = `GITHUB_TOKEN`) → GitHub Release with notes generated from `CHANGELOG.md` → **artifact attestations** (`actions/attest-build-provenance`) on wheel + images. Publish jobs sit behind a manual `environment: release` approval — the pipeline exists; the owner pulls the trigger. |

**PR wall-time budget:** ≤10 minutes. Measure on the first week; only if exceeded, add acceleration in this order: (1) split slowest shard into its own job, (2) `--cov` off on PRs (coverage on `full.yml` only), (3) path-filtered web job. No test-selection cleverness before measurement (soul guardrail).

### Workflow set — apps repo

`ci.yml` (PR + main): **manifest-validate** (every `app.json` parses against `apps/manifest.py` — small script imports core's parser); **tests** (install core via `pip install "personalclaw @ git+https://github.com/PersonalClaw/PersonalClaw@main"` until PyPI exists, then pin the released version; run each bundle's `test_*.py`; vendor SDKs stay uninstalled — the stub pattern is the contract, and a test that requires a real SDK fails loudly here, which is correct); **boundary** (the SDK-only import lint run from the apps side).

### Red-test policy (the S1 gate)

Fix-or-annotate, in code: each of the four known groups is root-caused with a timebox (½ day each); what isn't fixed inside the timebox becomes `xfail(reason="<root-cause hypothesis + issue #>", strict=False)` or `skip` with a filed issue. Suite green = zero unexplained failures; `xfail_strict` stays on so accidental passes surface. The plan-doc ledger in PUBLICATION.md is superseded (already re-homed here by its rev-9 amendment).

### Toolchain + supply chain

- **uv everywhere:** `uv.lock` committed (core + a constraints export for apps CI); CI installs via `uv sync --locked`; local dev docs updated (`make` targets gain uv-aware variants; Makefile keeps working with plain venvs — no forced migration).
- **Dependabot** (native, no hosted service): `pip`, `npm` (web/ + desktop/), `github-actions` ecosystems, weekly, lockfile-aware.
- **SBOM:** syft SPDX json attached to every GitHub Release (wheel + image scans).
- **Attestations over signatures initially:** GitHub build-provenance attestations ship in S3 (zero key management); cosign image signing deferred to SECURITY-HARDENING if wanted beyond attestations.
- **Coverage badge:** workflow-updated shields JSON in a gist or the website repo — no third-party coverage service by default (keeps the surface self-owned; Codecov remains an easy later swap).

## Sessions

**S1 — Red-test triage + core `ci.yml` (≈1 session).** Triage per policy above; author `ci.yml` + concurrency + `PERSONALCLAW_HOME` rail; badges into README. *Validation:* a PR with a deliberate lint error + a deliberate test failure shows red; revert shows green; wall-time recorded against the budget.

**S2 — Apps CI + rails (≈1 session).** Apps `ci.yml` (manifest-validate, tests-with-stubs, boundary); mount plan-31/32 rail tests in core `ci.yml`'s rails job. *Validation:* break an `app.json` on a branch → red; add a core-internal import to an app → boundary red.

**S3 — Release pipeline (≈1 session).** `release.yml` on a `v*` tag: wheel with web assets → **PyPI (main, Trusted Publishing)** → GHCR multi-arch → release notes → attestations. **Amended 2026-07-20 (owner): no TestPyPI** — publish straight to main PyPI; the safety mechanism is the `release` environment's required-reviewer gate (owner approves each run) plus a prerelease tag (`v0.1.1-rc1`) for the first real exercise, not a separate test index. *Validation (no live runner available):* the wheel-build step runs locally and produces a wheel that CONTAINS `web/dist` (`python -m build`); every publish/image/notes/attest job is YAML- and contract-validated against C1; the first real publish is the owner-approved `v0.1.1-rc1` tag push.

**S4 — Supply chain (≈1 session).** `uv.lock` + CI switch to `--locked`; Dependabot configs; pip-audit/npm-audit into `full.yml`; SBOM step into `release.yml`; coverage badge automation; README/docs "supply-chain posture" section (public statement of the above — the marketing half).

## Contracts & Interfaces (workflow contracts — stable names other plans depend on; conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Workflow files + job names (other plans reference these by name)

| File | Trigger | Jobs (stable ids) |
|---|---|---|
| `.github/workflows/ci.yml` | PR + feature-branch push | `lint`, `test`, `web`, `rails` |
| `.github/workflows/full.yml` | push `main` + nightly | `matrix` (3.12/3.13 × ubuntu/macos + arm jobs from plan 39), `audit`, `coverage` |
| `.github/workflows/release.yml` | tag `v*` | `build`, `pypi`, `images`, `notes`, `attest` |

- The `rails` job runs plan-31/32 generic tests (`test_lifecycle_gates`, `test_provider_boundary_residue`, `test_stability_inventory`) — those plans add their test files; this plan mounts them.
- `release.yml` env: `environment: release` (manual-approval gate = the owner). Artifacts: wheel+sdist (with `web/dist`, verified by DISTRIBUTION `verify_wheel`), GHCR images `ghcr.io/personalclaw/personalclaw-{gateway,web}:{<tag>,latest}` (amd64+arm64), SBOM (syft SPDX-JSON), build-provenance attestations.
- **Trusted Publishing** (no stored PyPI token): publisher = org `personalclaw`, repo `personalclaw`, workflow `release.yml`, environment `release`.

### C2 — Red-test annotation convention
Unfixed known-reds become `@pytest.mark.xfail(reason="<root-cause> — #<issue>", strict=False)` or `skip`; `xfail_strict` stays global. "Green by default, exceptions annotated in code" — never a plan-doc ledger.

### Integration points
- **Consumed by:** DISTRIBUTION (release.yml builds its artifacts), PLATFORM-REACH (adds arm jobs to full.yml), DESKTOP (adds a signed-dmg job to release.yml), every plan adding a rail test (mounted in `ci.yml` rails).
- **Owner-provisioned:** PyPI trusted publisher, GHCR org packages, `release` environment reviewer, Dependabot enablement.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Red-test triage + core CI

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Reproduce the four known-red groups locally (`python -m pytest tests/ -k "process_tree or provider_helpers or registry_config_sync" -q`; find the 10 cron-callback failures via a full run log); record each failure signature in the Execution log | — | all reds enumerated with test ids |
| T1.2 | For each group, timebox ½ day: root-cause and fix, else mark `@pytest.mark.xfail(reason="<hypothesis> — issue #<n>", strict=False)` or `skip` with reason; file one issue per annotated group (issue creation is an owner task if repos lack issues — leave titles+bodies in the Execution log) | the failing test files only | `make test` exits 0; zero unannotated failures; `xfail_strict` untouched |
| T1.3 | Author `ci.yml`: jobs `lint` (uv-cached, `make lint`), `test` (ubuntu, py3.12, `PERSONALCLAW_HOME=$RUNNER_TEMP/pclaw-home python -m pytest`), `web` (`npm ci && npm run typecheck && npm test && npm run build` in `web/`), `rails` (placeholder step until plans 31/32 tests exist — runs them if present via `pytest tests/test_lifecycle_gates.py tests/test_provider_boundary_residue.py --co -q \|\| true` guard REMOVED once both land; note as DISCOVERY if still guarded at S4); `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}` | create `.github/workflows/ci.yml` | pushed branch runs all jobs; deliberate lint error + test failure each turn the run red; wall-time recorded in Execution log vs the 10-min budget |
| T1.4 | Badges: CI (workflow badge) + license into both README headers | `README.md` (core; apps repo's in S2) | badges render on the repo page |
| V1 | Validation: open a scratch PR with an intentional flake8 violation → red; revert → green; confirm no job writes outside `$RUNNER_TEMP` | — | observed as stated |

### Session 2 — Apps CI + rails

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Manifest-validate script: iterate `*/app.json`, parse via core `apps/manifest.py` `from_dict`, fail listing offenders | apps repo: `scripts/validate_manifests.py`, workflow job | corrupting a fixture manifest on a branch turns the job red |
| T2.2 | Apps `ci.yml`: install core (`pip install "personalclaw @ git+https://github.com/PersonalClaw/PersonalClaw@main"` — switch to version pin at DISTRIBUTION S2, leave a dated comment), run `python -m pytest */test_*.py -q`, run manifest-validate; NO vendor SDKs installed | apps repo: `.github/workflows/ci.yml` | suite green with stubs only; a test importing a real vendor SDK fails loudly (fixture-verified) |
| T2.3 | Boundary job: run the SDK-only import lint from the apps side (reuse/port `tests/test_apps_import_boundary.py` logic to scan app bundles for `personalclaw.` imports outside `personalclaw.sdk.`) | apps repo: `scripts/check_sdk_boundary.py` + job | adding `from personalclaw.config import loader` to an app turns it red |
| T2.4 | Mount plan-31/32 rail tests as first-class in core `ci.yml` rails job (remove any T1.3 guard) | `.github/workflows/ci.yml` | rails job runs both tests unguarded |
| V2 | Validation: both repos' Actions tabs green on main; each rail proven red-able per its own plan's fixtures | — | holds |

### Session 3 — Release pipeline

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `release.yml` skeleton on tag `v*`: job `build` (checkout → `npm ci && npm run build` in web/ → `python -m build` → upload wheel+sdist artifacts) | `.github/workflows/release.yml` | rc tag on a branch produces artifacts; wheel contains `personalclaw/static/dist/index.html` (unzip check step in-job) |
| T3.2 | PyPI publish job: `pypa/gh-action-pypi-publish@release/v1`, `environment: release`, trusted publishing (no token secrets), **main PyPI directly — no TestPyPI variant** (owner 2026-07-20). The `environment: release` required-reviewer gate is the pre-publish safety mechanism. **Two-package rule (owner 2026-07-20):** `personalclaw` publishes under `environment: release`; `personalclaw-client` (plan 34 S2) publishes from a SEPARATE job under `environment: release-client` — PyPI's pending-publisher tuple (owner, repo, workflow, environment) must be unique per project, so the two packages need distinct environments (both gated, reviewer=keyurgolani). | `release.yml` | job is contract-valid (uses trusted publishing, sits behind `environment: release`); the first owner-approved `v0.1.1-rc1` tag publishes to PyPI and `uvx personalclaw --version` prints the rc version on a clean machine |
| T3.3 | Images job: buildx multi-arch (linux/amd64,linux/arm64) for `deploy/docker/Dockerfile.backend` + `Dockerfile.web`, tags `ghcr.io/personalclaw/personalclaw-{gateway,web}:{<tag>,latest}`, `GITHUB_TOKEN` auth | `release.yml` | `docker compose -f deploy/compose/compose.yaml up -d` on a clean VM pulls the rc images and passes healthchecks |
| T3.4 | Release-notes job: generate from `CHANGELOG.md` latest section → GitHub Release body; attach SBOM placeholder step (filled S4) | `release.yml` | release page shows the changelog section verbatim |
| T3.5 | Attestations: `actions/attest-build-provenance` on wheel + images | `release.yml` | `gh attestation verify` passes on the wheel |
| V3 | Validation: owner-approved `v0.1.1-rc1` tag push exercises the whole pipeline: artifacts, **main-PyPI publish**, GHCR, release notes, attestation verify — all from one tag, gated by the `release` environment approval | — | one tag → everything (after owner approves the run); Execution log records timings |

### Session 4 — Supply chain

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | `uv lock` → commit `uv.lock`; switch CI installs to `uv sync --locked` (Makefile untouched — add `make lock` convenience target only) | `uv.lock`, workflows, `Makefile` | CI green from lockfile; `make lock` refreshes it |
| T4.2 | Dependabot: `pip` (root), `npm` (`/web`, `/desktop`), `github-actions`, weekly, grouped minor/patch | `.github/dependabot.yml` both repos | config validates (Dependabot tab shows ecosystems) |
| T4.3 | Nightly audits: `pip-audit` + `npm audit --audit-level=high` into `full.yml`, report-only (job never fails the workflow; summary annotation instead) | `.github/workflows/full.yml` | nightly run shows audit summaries |
| T4.4 | SBOM: syft SPDX-JSON for wheel + images attached to the GitHub Release | `release.yml` | release assets include both SBOMs |
| T4.5 | Coverage badge: `full.yml` writes coverage % to a shields endpoint JSON (gist or website repo per owner decision); README badge | `full.yml`, `README.md` | badge shows a live number |
| T4.6 | Public posture doc: README "Supply chain" section (lockfile, audits, SBOM, attestations, disclosure link) | `README.md` | section present, links resolve |
| V4 | Validation: nightly run green end-to-end; lockfile drift (edit a pin locally) makes CI red | — | holds |

## Owner tasks (real world)

1. **PyPI:** ✅ DONE (2026-07-20) — Trusted Publishing pending publisher added for project `personalclaw` (GitHub publisher: org `personalclaw`, repo `personalclaw`, workflow `release.yml`, environment `release`). **No TestPyPI** (owner 2026-07-20 — publish to main PyPI directly). Add the same publisher for `personalclaw-client` when DISTRIBUTION S2 lands.
2. **GitHub org settings:** ✅ mostly DONE (2026-07-20) — all 3 org repos made **public**; the `release` environment exists with **required reviewer = keyurgolani** (the manual release trigger). Remaining: GHCR package publishing goes public on the first image push (S3), then confirm Packages → public.
3. **Enable Dependabot** alerts/updates on both repos (Settings → Code security) when S4 lands.
4. **Decide the coverage-badge home** (gist vs website repo) — 5 min, S4.
5. Optional: branch protection on `main` (require ci.yml green) — recommended once the first outside PR arrives.

## Risks & open questions

- **Suite wall-time unknown** until first CI run — the budget + escalation ladder handles either outcome; macOS runners are the likely slow/pricey axis (full.yml only, so PRs never wait on them).
- **Apps CI against `main` core** can break when core moves — acceptable pre-PyPI; switch to version pins at DISTRIBUTION S2 and treat a red apps-CI-on-pin-bump as the compatibility signal it is.
- **Open:** whether `mypy` runs over `tests/` too (Makefile lints `$(PKG)` only today) — keep parity with local (`src` only) to avoid a new noise source; revisit under plan 31's strictness ratchet.

## Execution log

Entries are DONE / DEVIATION / DISCOVERY / BLOCKED, appended as work lands. The
workflows are proven by running each job's local-command equivalent here (no live
GitHub runner is available); each YAML is validated by `yaml.safe_load` + a C1
job/step contract check. See CI-RELEASE-ENGINEERING findings under
`.dev-home/loop/e35e8425/` for per-cycle evidence.

### Filed issues (referenced by inline xfail/type-ignore markers)

These track pre-existing debt surfaced (not caused) by S1's lint/test contract.
Each inline marker below carries the issue number so the suppression is auditable
and removable when the root cause is fixed.

- **#CI-1 — mypy: `list`/`ArtifactProvider.list`-shadowed `list[...]` annotations flagged `valid-type`.**
  Body: The artifact/skills provider ABCs define a `list(...)` method (Task/Prompt
  entity convention). With that method in class scope, mypy resolves the builtin
  `list[...]` in sibling annotations to the method → `[valid-type]`. 9 sites
  (artifacts/provider.py, artifacts/native.py, skills/marketplace.py). Not a runtime
  bug (`from __future__ import annotations` makes them lazy strings). Fix options:
  rename the method (API break — deferred) or a `builtins.list` alias sweep. Marked
  `# type: ignore[valid-type]  # CI-1` pending that decision.

- **#CI-2 — ModelProvider / AgentProvider used interchangeably at the session+pool seams.**
  Body: `AcpSessionProvider(AgentProvider)` is returned where callers annotate
  `ModelProvider | None` (session pool, provider_bridge). The two hierarchies share a
  duck-typed lifecycle + `complete`/`stream` surface but are not related by inheritance,
  so mypy flags `[return-value]` at the seam. Unifying them (a common runtime Protocol)
  is an architecture change out of scope for plan 33. Marked `# type: ignore[return-value]  # CI-2`.
  Also covers `ProviderRegistry.build(name, **config)` where `config: dict[str, object]`
  can't prove the typed `session_key` kwarg (`[arg-type]`) — the options dict never
  carries session_key; `# type: ignore[arg-type]  # CI-2`.

- **#CI-3 — optional model-management surface duck-typed across the core↔app boundary.**
  Body: The core `EmbeddingProvider` ABC is inference-only (`embed`/`embed_batch`).
  Model management (`list_models`/`delete_model`/download) is an OPTIONAL capability
  implemented by the sentence-transformers *app* provider (sibling repo), called via
  `native_provider()` in `embedding_providers/registry.py`. The core can't reference
  the app's `ModelInfo` return type, so these calls are `[attr-defined]` against the
  ABC. Guarded by try/except already. Marked `# type: ignore[attr-defined]  # CI-3`
  pending a formal optional ModelManagement protocol at DISTRIBUTION.

### S1 — Formatting standard + core ci.yml

- **DONE T-S1a** — setup.cfg `[flake8]` authored (max-line-length=100;
  extend-ignore=E203,W503,E704; exclude incl. `src/personalclaw/static/dist`,
  web/dist, build, .venv, node_modules, caches; per-file-ignores `__init__.py:F401`
  + `cli.py:E402`). black pinned `>=26,<27` in the pyproject dev extra. flake8
  auto-discovers setup.cfg from repo root (verified).
- **DONE T-S1b** — whole-tree black (26.5.1) reformat of 691 files + isort (black
  profile, 100), isolated `style:` commit. black --check + isort --check-only green.
- **DONE (lint residue, move iii)** — after the reformat, flake8 residual (588→0)
  cleared: F821×14 (incl. two real latent bugs in provider_bridge.py — a swallowed
  unimported `get_default_registry` and a use-before-assignment `name`), F811×2
  (removed a dead shadowed `get_workflow`), F541/E731/E231/E741, F841×14 (dead test
  locals), E402×16 (noqa deliberate deferred imports + hoist), F401×104 (AST-driven
  removal; one re-export seam `cli_setup.env_path` restored with noqa), E501×448
  (noqa on semantic single-line strings + rewrapped multiline prose/SQL/HTML).
  **flake8 now reports 0.** `make lint` is black+isort+flake8 green; mypy in progress.
- **DISCOVERY** — the plan's Context premise "local tooling is strong and CI-ready
  as-is" is FALSE (E1-class, owner-resolved): on main `make lint` was heavily red
  (black 26 would reformat 691 files; flake8 had NO committed config → 79-char
  default flagged ~29.8k incl. ~29.1k E501 vs the code's 100-char standard). S1's
  formatting-standard work is the correction.
- **DISCOVERY** — pre-existing test red (NOT this branch):
  `tests/test_cli.py::TestConfigDirOverride::test_setup_slack_tokens_writes_to_config_dir`
  imports `_setup_slack_tokens` from `personalclaw.cli_setup`, but that symbol lives
  in `sdk/cli.py` and was never in `cli_setup` on main (637fd11). Queued for T1.2
  red-test triage (fix-or-xfail per C2).
- **DISCOVERY (real bug, fixed)** — the mypy triage surfaced that
  `loop_routes._installed_capability_catalogs` imported `personalclaw.skills.registry`
  (no such module) and called `.list_installed()` on both skills and workflows
  registries (no such method) — both wrapped in `try/except`, so the classify
  skill+workflow catalogs were SILENTLY ALWAYS EMPTY. Rewired to the real APIs
  (`skills.marketplace.list_local_skills()` + `await workflows.registry.list_all_workflows()`);
  the catalog now populates (verified: workflows resolve, skills scan the discovery paths).
- **DISCOVERY (real bug, fixed)** — mypy surfaced that `cli_setup` stored credentials
  via `CredentialStore.upsert(Credential(name=, value=))` — but `CredentialStore` has no
  `upsert` (it has `save(descriptors)`), `Credential` has no `value` field, and the ctor
  takes the HOME dir not a file path. Wrapped in try/except, so `--credential NAME=VAL`
  setup SILENTLY FAILED to persist. Fixed to the real API (`CredentialStore(config_dir())`
  → merge `{name: {"type": "api_key", "value": val}}` into descriptors → `save`); verified
  a store→resolve round-trip returns the secret.
- **DISCOVERY (real bug, fixed)** — `subagent` called
  `_cleanup_session_files_sync(session_id, provider)` but the function takes only
  `session_id` — the extra arg raised TypeError, swallowed by try/except, so completed-
  subagent ACP session-file cleanup SILENTLY NEVER RAN. Dropped the spurious arg.
- **DISCOVERY (real bug, fixed)** — `invoke_agent_provider` read
  `AppConfig.load().hooks.auto_approve_subagent_spawn`, but `AppConfig.hooks` is a
  raw `dict` (hook definitions), NOT a `HooksConfig` — that attribute access raised
  AttributeError, swallowed by try/except, so the global "auto-approve subagent spawn"
  flag SILENTLY NEVER TOOK EFFECT. Fixed to
  `HooksConfig.from_dict(AppConfig.load().hooks).auto_approve_subagent_spawn`.
- **DONE (V1 lint half)** — `make lint` exits 0: black --check + isort --check-only +
  flake8 (src tests) + mypy (src/personalclaw) all green. mypy went 140→0 via the
  triage above — real fixes throughout, with only #CI-1/#CI-2/#CI-3 + a few documented
  typing-limitation inline-ignores (TypeVar default, abstract-class-as-discovery-filter,
  faiss untyped optional C-ext). The `make test` triage (T1.2) is the remaining half of V1.

## Execution log

- [2026-07-20][premise] E1 correction: the Context claim "local tooling is strong and CI-ready as-is" is materially FALSE — on main `make lint` is heavily red (black 26 would reformat ~883 files vs code written to black ≤23 style; NO committed flake8 config → 79-char default flagged ~29k E501 vs the code's 100; mypy 152 errors). Owner-resolved: robust committed flake8 config (100-char, black-compat ignores) + reformat the tree + pin black target-version=py312; mypy fixed to 0.
- [2026-07-20][S1] DONE: red-test triage → `make lint` GREEN (black+isort+flake8 0 + mypy 152→0, all real fixes) and `make test` green-by-default. Real-fixed: test_process_tree (client/transport split — AcpProcess.snapshot_process_tree) + 2 plan-32 regressions the suite caught (incomplete resolved→stt_resolved rename in cli_doctor; obsolete _setup_slack_tokens test removed). xfailed the pre-existing baseline reds with filed issues: #7 (10 gateway cron-callback), #6 (context/native-tool/pid-lifecycle), #8 (test_history SEL-audit xdist race). Authored `.github/workflows/ci.yml` (lint/test/web/rails, concurrency cancel-in-progress, PERSONALCLAW_HOME rail) + `full.yml` (matrix 3.12/3.13 × ubuntu/macos, audit, coverage) + README CI badge. Job ids match C1.
- [2026-07-20][S2] DONE: apps-repo `.github/workflows/ci.yml` (manifest-validate — all 38 app.json parse against core's parser; tests — per-bundle to avoid duplicate test_*.py basename collisions; boundary — SDK-only import lint). The boundary lint CAUGHT 3 real pre-existing violations (uncaught until now because core's boundary test skips when the sibling isn't named `apps/`): alibaba/bedrock/google-models deep-imported media_scanners/embedding_providers.base/stt.provider. Fixed by routing through sdk.* (added `register_scanner` to sdk/model.py; embedding/stt were already re-exported). Also fixed 8 pre-existing app-test reds the tests job surfaced (fal-image FAL request_id mocks + veo2 '8s'; openai-compatible /models via sdk.net.fetch; openai-models embedding_model via extra_options + stale core docstring). All 35 app suites green. Core rails job mounts the plan-32 residue sweep.
- [2026-07-20][S3] DONE: `.github/workflows/release.yml` (build/pypi/pypi-client/images/notes/attest). Wheel-with-web/dist proven locally (271 static/dist entries incl index.html). Two-package publish: personalclaw via `environment: release`, personalclaw-client via `environment: release-client` (unique PyPI pending-publisher tuple; both owner-registered). Multi-arch GHCR via GITHUB_TOKEN; CHANGELOG-section release notes (verified extraction incl EOF section + rc fallback); build-provenance attestations. Owner amendment folded: NO TestPyPI — main PyPI directly, gated by the release env approval.
- [2026-07-20][S4] DONE: `uv.lock` committed (171 pkgs); ci.yml + full.yml install via `uv sync --locked` + `uv run`; `.github/dependabot.yml` both repos (pip/npm/actions weekly, grouped); SBOM (syft SPDX-JSON) in release.yml; self-owned coverage-badge shields JSON in full.yml; README supply-chain posture section; `[tool.black] target-version=py312`.
- [2026-07-20][validation] make lint GREEN (all 4 tools); `make test` green-by-default (7663 passed / 14 xfailed / 0 failed, stable across runs); all 4 workflow YAMLs parse + job commands proven locally (no live runner available — the C1 contracts are contract-checked, the wheel build + lint + tests + manifest-validate + boundary + coverage snippet all run green here); actionlint not installed (DISCOVERY).
- [2026-07-20][DISCOVERY] core's tests/test_apps_import_boundary.py hardcodes `parents[2]/apps` and SKIPS when the sibling is named PersonalClawApps — so the 3 app boundary violations went uncaught locally. The apps-repo boundary CI job now covers this from the apps side; core's test could gain a PERSONALCLAW_FIRST_PARTY_APPS_DIR-aware path (out of scope here).
- [2026-07-20][HANDOFF] Executed via a code-kind goal loop (e35e8425) through S1's mypy triage (152→57); the loop failed 3× on transient Bedrock SSL write-timeouts (commute network blips, not code). Per owner authorization the maintainer took over and hand-finished S1 tail + S2/S3/S4 under this protocol. Owner tasks completed during execution: all 3 org repos public; `release`/`release-client` environments with required-reviewer gate; PyPI Trusted Publishing pending publishers for both packages; Dependabot alerts on. Remaining owner tasks: GHCR packages → public after first image push; coverage-badge gist/site home; branch protection on main (optional).
- [2026-07-20][S1 amendment — isolation root-caused, mitigations retired] Owner directed fixing the test isolation at the source rather than shipping the loadscope/reruns mitigations. Four root causes, all fixed in-code (no reruns, no masking); the earlier "xdist flake" xfails (#8/#10) are removed and their tests pass deterministically: (1) `SecurityEventLog` process singleton cached the first test's dir per worker → `conftest._reset_sel_singleton` clears it around every test. (2) `single_flight` locks under `config_dir()/locks/<key>` are shared across xdist workers (one `PERSONALCLAW_HOME`); sibling tests reusing a consolidation key contended for one lock file, and the loser SKIPPED its guarded work → empty SEL record → `assert 0==1` → `conftest._isolate_single_flight_locks` gives each test its own lock dir. (3) `test_single_flight_reaper` legitimately exercises cross-PROCESS locking (spawns a child), which fixture (2) desynced → its `_tmp_home` now depends on the global fixture (runs last) and re-derives the lock dir from `PERSONALCLAW_HOME`. (4) `test_default_assemble_matches_build_message` byte-compared two builds whose `[CURRENT DATE] HH:MM` marker differs across a minute rollover → freeze `context.datetime` for that test. CORRECTION to the [premise] guidance and the S1/T1.3 entries above: the workflows must NOT set a global `PERSONALCLAW_HOME` — the suite runs under the documented no-global-home contract (`make test` = bare `pytest`; CONTRIBUTING mandates per-test `config_dir`/`tmp_path` isolation, NOT a global override). A global value defeats 5 tests that assert the default `.personalclaw` resolution or patch `Path.home()` (config_dir gives the env var precedence over Path.home), which would red the test/matrix/coverage jobs on the first CI run; the "belt-and-suspenders rail" was wrong. Removed `PERSONALCLAW_HOME` from all four pytest jobs (ci test+rails, full matrix+coverage). pyproject restored to `--dist worksteal`; `pytest-rerunfailures` dropped from `[test]` extra + `uv.lock`; `--reruns` removed from ci.yml/full.yml. Validation: full suite 0/5-flaked deterministically green under worksteal with NO reruns (7665 passed / 13 xfailed = the genuine #6/#7 standalone reds only), env-unset run leaves the real `~/.personalclaw` untouched, all four CI jobs (lint/test/web/rails) mirror green locally.
- [2026-07-20][first-CI discoveries — things only a real runner surfaces] The first pushed CI run exposed three environment-specific breakages that no local mirror could (local core installs from the tree; local OS is macOS). All fixed at the source: (a) CORE test job — 24 failures in `test_script_hooks`/`test_schedule_script` with `sandbox: unshare(NEWNS) failed: errno 1`. Root cause is a probe/reality mismatch in `sandbox.py::_probe_unshare`: it probed the ATOMIC `unshare(NEWUSER|NEWNS)`, but the launcher does the two calls SEQUENTIALLY, and hardened kernels (Ubuntu 23.10+/GitHub runners with `apparmor_restrict_unprivileged_userns=1`) allow the atomic form yet DENY a standalone `unshare(NEWNS)` from inside a userns → false-positive backend selection, then every sandboxed script died at runtime. This was a real product bug (script hooks broken in any restricted container), not a test artifact. Fixed the probe to mirror the launcher's sequential two-step so `detect_backend()` honestly falls back to `none`; verified by simulating backend=none locally (36 passed/2 skipped where the runner had 24 failed). (b) APPS manifest-validate + tests jobs — `uv pip install --system` against the runner's PEP-668 `/usr` (`error: externally managed`) → switched both to a uv-managed venv exported via `$GITHUB_PATH`/`VIRTUAL_ENV` (drop `--system`). (c) APPS boundary job — `uv: command not found` (a stray `uv python install` with no `Install uv` step; the job uses `actions/setup-python`) → removed the dead step. Re-validated locally + re-pushing for a clean runner confirmation.
- [2026-07-20][second-CI pass — 24→3→0] The probe/venv fixes turned the runner green on apps (all 3 jobs) and cut the core test job from 24 fails to 3, all in `test_backend_runtime_reap.py` (positive-match reap tests found 0 processes; the spare-tests trivially passed on 0). Root cause: `BackendSupervisor._pids_running` runs `ps -Ao …command=` and substring-matches the backend entry path, but Linux procps TRUNCATES the command column to ~screen width (80 cols) when stdout isn't a TTY — the runner's long temp path (`/home/runner/work/_temp/pytest-of-runner/…/apps/myapp/backend/server.py`) got clipped before the needle, so nothing matched. macOS `ps` doesn't truncate (confirmed locally: still matches at COLUMNS=80), which is why it passed locally. Real product bug — orphan reaping silently no-ops for any backend under a long path. Fixed by `ps -Awwo …` (`-ww` = unlimited width; no-op on macOS). Reap suite 7/7 green locally.
- [2026-07-20][third-CI pass — 3→1→0] The reap fix cleared those 3; one wall-clock flake remained: `test_context_engine.py::test_failing_engine_quarantined_to_default` — the SAME minute-rollover class as the earlier `test_default_assemble_matches_build_message` (build a message twice, byte-compare, `[CURRENT DATE] HH:MM` differs across a minute boundary), which happened to roll on the runner. Rather than patch it inline, promoted the clock-freeze to a shared `frozen_clock` fixture and applied it to BOTH double-build byte-compare tests in the file. Swept the rest of the suite for the same pattern (two independent timestamped assemblies compared for equality): these two were the only instances — `test_context.py` date-tz tests build once and substring-assert; `test_manifest_crons`/`test_autonudge`/`test_queue_cancel` compare static config/user strings, not assemblies. Local: file 4/4 green, lint clean.
