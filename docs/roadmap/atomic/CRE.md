# CI-RELEASE-ENGINEERING — atomic plans

**Source plan:** [`CI-RELEASE-ENGINEERING`](../plans/CI-RELEASE-ENGINEERING.md)  
**Code:** `CRE`  
**Source status:** done

CI-RELEASE-ENGINEERING is fully shipped: committed flake8 standard + whole-tree reformat (mypy 152→0), core ci.yml (lint/test/web/rails) + full.yml matrix, apps-repo ci.yml, release.yml (build/pypi/pypi-client/images/notes/attest via Trusted Publishing), uv.lock + Dependabot + SBOM + coverage badge, and a test-isolation root-cause pass that fixed 4 real product bugs the gate surfaced. Only owner real-world confirmations (GHCR packages→public on first push, optional main branch protection) remain — no agent-executable code left.

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

