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

**S3 — Release pipeline (≈1 session).** `release.yml` end to end against a `v0.1.1-rc` prerelease tag on a fork/private run: wheel with web assets → TestPyPI (trusted publishing dry-run) → GHCR multi-arch → release notes → attestations. *Validation:* `uvx --index testpypi personalclaw` boots the gateway on a clean VM; `docker compose up` with the fresh images works via the published-image compose file.

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
| T3.2 | PyPI publish job: `pypa/gh-action-pypi-publish@release/v1`, `environment: release`, trusted publishing (no token secrets); TestPyPI variant first behind an input flag | `release.yml` | TestPyPI rehearsal succeeds; `uvx --index-url https://test.pypi.org/simple/ personalclaw --version` prints the rc version on a clean machine |
| T3.3 | Images job: buildx multi-arch (linux/amd64,linux/arm64) for `deploy/docker/Dockerfile.backend` + `Dockerfile.web`, tags `ghcr.io/personalclaw/personalclaw-{gateway,web}:{<tag>,latest}`, `GITHUB_TOKEN` auth | `release.yml` | `docker compose -f deploy/compose/compose.yaml up -d` on a clean VM pulls the rc images and passes healthchecks |
| T3.4 | Release-notes job: generate from `CHANGELOG.md` latest section → GitHub Release body; attach SBOM placeholder step (filled S4) | `release.yml` | release page shows the changelog section verbatim |
| T3.5 | Attestations: `actions/attest-build-provenance` on wheel + images | `release.yml` | `gh attestation verify` passes on the wheel |
| V3 | Validation: full dry-run on `v0.1.1-rc1`: artifacts, TestPyPI, GHCR, release notes, attestation verify — all from one tag push | — | one tag → everything; Execution log records timings |

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

1. **PyPI:** create the account (or use existing), then configure **Trusted Publishing** for project `personalclaw` (and later `personalclaw-client`): PyPI → project → Publishing → add GitHub publisher (org `personalclaw`, repo `personalclaw`, workflow `release.yml`, environment `release`). Do the same on TestPyPI for S3's rehearsal. ~15 min.
2. **GitHub org settings** (after DISCOVERABILITY-LAUNCH S1 creates the org): enable Actions for org repos; create the `release` environment with "required reviewer = you" (this is the manual release trigger); allow GHCR package publishing (Packages → public).
3. **Enable Dependabot** alerts/updates on both repos (Settings → Code security) when S4 lands.
4. **Decide the coverage-badge home** (gist vs website repo) — 5 min, S4.
5. Optional: branch protection on `main` (require ci.yml green) — recommended once the first outside PR arrives.

## Risks & open questions

- **Suite wall-time unknown** until first CI run — the budget + escalation ladder handles either outcome; macOS runners are the likely slow/pricey axis (full.yml only, so PRs never wait on them).
- **Apps CI against `main` core** can break when core moves — acceptable pre-PyPI; switch to version pins at DISTRIBUTION S2 and treat a red apps-CI-on-pin-bump as the compatibility signal it is.
- **Open:** whether `mypy` runs over `tests/` too (Makefile lints `$(PKG)` only today) — keep parity with local (`src` only) to avoid a new noise source; revisit under plan 31's strictness ratchet.
