# PROVIDER-BOUNDARY-COMPLETION — atomic plans

**Source plan:** [`PROVIDER-BOUNDARY-COMPLETION`](../plans/PROVIDER-BOUNDARY-COMPLETION.md)  
**Code:** `PBC`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PBC-1` | ✅ (#2682251) | Manifest seam fields: cli.setup, cli.doctor, loggerRoots (contract C1) | — | AppManifest gains CliConfig(setup, doctor) + loggerRoots with to_dict/from_dict parity and unknown-field forward-compat; round-trip test passes; absent fields default empty; existing manifests still parse (test_app_manifest.py extended) |
| `PBC-2` | ✅ (#2682251, 7f2b21c) | SDK cli types + app-contributed setup/doctor runners; delete core slack setup+doctor | `PBC-1` | new sdk/cli.py exports SetupContext+DoctorLine(+DoctorStatus); app_cli.py run_app_setup_steps + run_app_doctor_probes (5s thread timeout) wired into cli_setup.py/cli_doctor.py; setup --app flag added; core _setup_slack_tokens/_setup_slash_command and hardcoded doctor Slack section deleted; fixture setup fn runs, raising fn does not abort, sleeping probe times out to a fail line (test_app_cli.py, test_sdk_cli.py green) |
| `PBC-3` | ✅ (#1c879ec) | App-registered logger-root aggregation; delete constants.APP_LOGGER_ROOTS | `PBC-1` | catalog.installed_logger_roots() reads installed+enabled manifests without importing app code; cli.py log setup and dashboard/handlers/updates.py log-level handler consume ('personalclaw', *installed_logger_roots()) with graceful () when apps dir absent; grep for APP_LOGGER_ROOTS in src/ is empty; test_app_catalog.py P13-P16 pass |
| `PBC-4` | ✅ (#7538b63) | Slack app absorbs its CLI setup+doctor+loggerRoots (PersonalClawApps repo) | `PBC-2`, `PBC-3` | slack-channel gains cli_setup.py (moved _setup_slack_tokens/_setup_slash_command bodies, importing via sdk.channel/sdk.credentials only) + cli_doctor.py reproducing the old Slack doctor section; app.json declares cli.setup, cli.doctor, loggerRoots:[slack_runtime], dependencies.pythonDependencies:[slack-sdk]; app-side prompts are byte-identical to the old flow; V1 validation holds (install app -> setup runs slack step -> doctor shows app section -> logs under slack_runtime -> disable clears traces) |
| `PBC-5` | ✅ (#ea63235) | Packaging: resolve pip-step, drop slack-sdk from core deps + doctor probes | `PBC-4` | confirmed app_manager._install_python_deps pip-installs manifest pythonDependencies (pyproject 'no per-app pip step' comment corrected as FALSE); slack-sdk removed from core dependencies (only the [slack] extra remains); slack_sdk dropped from both cli_doctor.py dep probes; clean-venv pip install . pulls no slack-sdk; doctor deps line green without it; V2 validation holds |
| `PBC-6` | ✅ (#ea63235) | --slack-only clean-break removal (mapped to --headless) | — | --slack-only deleted outright (owner clean-break call, DEVIATION from plan's warn-one-release); dest renamed slack_only->headless at cli.py:232 parser + cli.py:151 consumer (E1 premise: plan said cli_server.py, actually cli.py); docs/reference/cli.md + two tests updated; CHANGELOG entry written |
| `PBC-7` | ✅ (#2d0d7e0) | Anti-regrowth residue rail: sweep test + machine-checked keeps allowlist | `PBC-2`, `PBC-3`, `PBC-5`, `PBC-6`, `EXT:CI-RELEASE-ENGINEERING:mount residue test in ci.yml` | tests/test_provider_boundary_residue.py greps src/ (case-insensitive) for vendor SDK imports + SLACK_*/xox credential literals, failing on hits outside docs/architecture/provider-boundary-keeps.txt (the machine-checked keeps table); green on tree; adding import slack_sdk to any core module turns it red naming the file; CI mount wired by CI-RELEASE-ENGINEERING S2 |

## Atom scopes

### `PBC-1` — Manifest seam fields: cli.setup, cli.doctor, loggerRoots (contract C1)

**Status:** done (PR #2682251)

S1 T1.1; Contracts C1 (Manifest fields); Design B/C/D field definitions

**Done when:** AppManifest gains CliConfig(setup, doctor) + loggerRoots with to_dict/from_dict parity and unknown-field forward-compat; round-trip test passes; absent fields default empty; existing manifests still parse (test_app_manifest.py extended)

### `PBC-2` — SDK cli types + app-contributed setup/doctor runners; delete core slack setup+doctor

**Status:** done (PR #2682251, 7f2b21c)

S1 T1.2a/T1.2b/T1.3; Design B (app-contributed CLI setup) + C (app-contributed doctor probes); Contracts C2 (SetupContext, DoctorLine)

**Done when:** new sdk/cli.py exports SetupContext+DoctorLine(+DoctorStatus); app_cli.py run_app_setup_steps + run_app_doctor_probes (5s thread timeout) wired into cli_setup.py/cli_doctor.py; setup --app flag added; core _setup_slack_tokens/_setup_slash_command and hardcoded doctor Slack section deleted; fixture setup fn runs, raising fn does not abort, sleeping probe times out to a fail line (test_app_cli.py, test_sdk_cli.py green)

### `PBC-3` — App-registered logger-root aggregation; delete constants.APP_LOGGER_ROOTS

**Status:** done (PR #1c879ec)

S1 T1.4; Design D (app-registered logger roots)

**Done when:** catalog.installed_logger_roots() reads installed+enabled manifests without importing app code; cli.py log setup and dashboard/handlers/updates.py log-level handler consume ('personalclaw', *installed_logger_roots()) with graceful () when apps dir absent; grep for APP_LOGGER_ROOTS in src/ is empty; test_app_catalog.py P13-P16 pass

### `PBC-4` — Slack app absorbs its CLI setup+doctor+loggerRoots (PersonalClawApps repo)

**Status:** done (PR #7538b63)

S1 T1.5 (cross-repo PersonalClawApps, branch feature-app-cli-seams)

**Done when:** slack-channel gains cli_setup.py (moved _setup_slack_tokens/_setup_slash_command bodies, importing via sdk.channel/sdk.credentials only) + cli_doctor.py reproducing the old Slack doctor section; app.json declares cli.setup, cli.doctor, loggerRoots:[slack_runtime], dependencies.pythonDependencies:[slack-sdk]; app-side prompts are byte-identical to the old flow; V1 validation holds (install app -> setup runs slack step -> doctor shows app section -> logs under slack_runtime -> disable clears traces)

### `PBC-5` — Packaging: resolve pip-step, drop slack-sdk from core deps + doctor probes

**Status:** done (PR #ea63235)

S2 T2.1 (pip-step finding) + T2.2 (dep drop); Design A (dependency cleanup #1,#2)

**Done when:** confirmed app_manager._install_python_deps pip-installs manifest pythonDependencies (pyproject 'no per-app pip step' comment corrected as FALSE); slack-sdk removed from core dependencies (only the [slack] extra remains); slack_sdk dropped from both cli_doctor.py dep probes; clean-venv pip install . pulls no slack-sdk; doctor deps line green without it; V2 validation holds

### `PBC-6` — --slack-only clean-break removal (mapped to --headless)

**Status:** done (PR #ea63235)

S2 T2.3; Design A (flag cleanup #6)

**Done when:** --slack-only deleted outright (owner clean-break call, DEVIATION from plan's warn-one-release); dest renamed slack_only->headless at cli.py:232 parser + cli.py:151 consumer (E1 premise: plan said cli_server.py, actually cli.py); docs/reference/cli.md + two tests updated; CHANGELOG entry written

### `PBC-7` — Anti-regrowth residue rail: sweep test + machine-checked keeps allowlist

**Status:** done (PR #2d0d7e0)

S2 T2.4; Design E (anti-regrowth rail); Contracts C3 (residue-sweep rail)

**Done when:** tests/test_provider_boundary_residue.py greps src/ (case-insensitive) for vendor SDK imports + SLACK_*/xox credential literals, failing on hits outside docs/architecture/provider-boundary-keeps.txt (the machine-checked keeps table); green on tree; adding import slack_sdk to any core module turns it red naming the file; CI mount wired by CI-RELEASE-ENGINEERING S2

