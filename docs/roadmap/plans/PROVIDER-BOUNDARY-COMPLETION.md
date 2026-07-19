# Plan: Provider-Boundary Completion — Retire the Slack Residue in Core

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner-confirmed: core entities are *channels*; anything Slack-specific belongs to the slack-channel app)
**Created:** 2026-07-18
**Wave:** 0 — standalone cleanup of a verified residue list; no dependencies.
**Depends on:** nothing hard. Coordinates with DISTRIBUTION (dependency-set change ships in the same release that fixes `doctor`'s probe) and CI-RELEASE-ENGINEERING (the residue-sweep CI check lands in its workflow set). Honors the deliberate-keeps table in `docs/architecture/provider-boundary.md`.
**Scope:** finish the Slack extraction. The seams are already vendor-blind (verified: core ships only `reference_echo` + `webui` transports; all 14 Slack modules live in `apps/slack-channel/slack_runtime/`; thread deep-links come from the app behind `build_thread_link`). What remains is enumerated residue in packaging, the CLI, and constants — plus the two *generic seams* whose absence caused the residue (app-contributed CLI setup, app-registered logger roots). **Soul guardrail:** the documented deliberate keeps are NOT in scope — `xox[bpas]-` secret-detection patterns, `sandbox.py` `SLACK_*` env denylist, and `CRED_SLACK_*` key names in `config/loader.py` re-exported via `sdk/channel.py`. Touching those is drift, not cleanup. And the new seams are *thin*: a setup step and a doctor probe are functions an app registers — not a plugin framework.

---

## The verified residue list (code recon, 2026-07-18)

| # | Site | Problem |
|---|---|---|
| 1 | `pyproject.toml` `dependencies` | `slack-sdk>=3.27,<4` hard core dep with **zero imports in `src/`** |
| 2 | `cli_doctor.py:288,310` | dep probe runs `import websockets, slack_sdk, aiohttp` — an app's SDK treated as core-required |
| 3 | `cli_doctor.py:379-397` | hardcoded "Slack Channel App" section (token presence, workspace hint) |
| 4 | `cli_setup.py:133-139` + `_setup_slack_tokens()` (:247+) + `_setup_slash_command()` | the slack app's interactive setup living in core CLI; the in-code comment already declares extraction as the plan ("a `setup` hook apps register") |
| 5 | `constants.py:15` `APP_LOGGER_ROOTS = ("slack_runtime",)` | consumers: `cli.py:750-762` (log setup, two loops) and `dashboard/handlers/updates.py:686-692` (log-level handler applies levels to `("personalclaw", *APP_LOGGER_ROOTS)`) |
| 6 | `cli_server.py` `--slack-only` | legacy alias for `--headless` |

Manifest recon: `apps/manifest.py` today models CronEntry / UIPage / UISidebar / UIConfig / BackendConfig / Permissions / `setup.onInstall` / `configSchema` — there is **no** contribution field for CLI setup, doctor probes, or logger roots. Those are the seams to add (design below). Note a documentation inconsistency to resolve while here: `pyproject.toml`'s extras comment says "there is no per-app pip step" while `docs/architecture/app-platform.md` describes a pip-deps install step — reconcile which is true for first-party bundles and document it (affects where `slack-sdk` gets installed from after removal, #1).

---

## Design

### A. Dependency + flag cleanup (residue #1, #2, #6)

- Remove `slack-sdk` from core `dependencies`. Resolution order for how the app gets it: (i) if the app-platform pip-deps step supports first-party bundles, the slack app declares it there (verify the manifest field + `app_manager.py` behavior — the pyproject comment and app-platform doc currently disagree); (ii) otherwise the existing `personalclaw[slack]` extra remains the documented install path for slack users and the doc inconsistency is fixed to say so. Either way the core wheel sheds the dep.
- `doctor` dep probe drops `slack_sdk` from the required set (aiohttp + websockets remain).
- `--slack-only`: emit a one-release deprecation warning mapping to `--headless`, then delete (change class R/B-lite per LIFECYCLE-DOCTRINE; registered as a gate-less deprecation with a CHANGELOG entry — no state migration involved).

### B. App-contributed CLI setup (residue #4 — the seam the in-code comment promises)

- **Manifest field:** `cli.setup: "module:function"` (optional). The function receives a small typed context (`SetupContext`: credential store accessors from `sdk/credentials`, `ProviderSettings` handle, print/input helpers) and runs its interactive step.
- **Runner:** `personalclaw setup` iterates **installed + enabled** apps' manifests after core steps (credentials → models → then app steps, alphabetical), importing the declared function from the installed app dir (same import mechanics as `providers/loader.py` — pinned dir, module import; the app passed the install scanner, so executing its declared setup code at the *user's explicit request* is within the existing trust model). Failures print and continue — one broken app must not kill setup.
- **Migration:** `_setup_slack_tokens` + `_setup_slash_command` move verbatim into `apps/slack-channel` (new `cli_setup.py` in the app, manifest gains `cli.setup`); core `cli_setup.py` deletes the passthrough and its `CRED_SLACK_*` imports (the constants stay in `config/loader.py` + `sdk/channel.py` — deliberate keep; the *app* keeps importing them via the SDK).
- SEL: an `app_cli_setup` event per executed step (caller=cli, app name, outcome).

### C. App-contributed doctor probes (residue #3)

- **Manifest field:** `cli.doctor: "module:function"` returning a typed `list[DoctorLine]` (label, status ok/warn/fail/info, detail). `cli_doctor` renders a per-app section for each installed+enabled app declaring one, executed with a hard timeout (5s) and exception guard (a hanging probe prints `fail: probe timeout`, never hangs doctor).
- The slack app's probe reproduces today's exact output (token presence via SDK credential accessors, workspace-test hint). Core's hardcoded section deletes in the same commit (clean break within the change).

### D. App-registered logger roots (residue #5)

- **Manifest field:** `loggerRoots: ["slack_runtime"]` (static data — belongs in the manifest, not code registration, so `cli.py` log setup can read it *without* loading app code).
- **Aggregation:** a small helper in `apps/catalog.py` (or `app_manager`) reads installed+enabled manifests → `installed_logger_roots()`. `cli.py:750-762` and `updates.py:686-692` consume it (union with `"personalclaw"`), falling back gracefully when the apps dir is absent (fresh installs, tests). Delete `constants.APP_LOGGER_ROOTS`.
- Update the provider-boundary.md table row from "deliberately not built yet" to the manifest-field design.

### E. Anti-regrowth rail

`tests/test_provider_boundary_residue.py`: greps `src/` for vendor markers (slack/telegram/discord/…, case-insensitive) and fails on any hit outside an allowlist file (`docs/architecture/provider-boundary-keeps.txt`) that enumerates the deliberate keeps with their judgment one-liners — the allowlist IS the keeps table, machine-checked. Runs locally + in CI (workflow wiring in CI-RELEASE-ENGINEERING S2).

---

## Sessions

**S1 — Seams + slack migration (≈1 session).** Manifest fields (`cli.setup`, `cli.doctor`, `loggerRoots`) + parsing/serialization + unknown-field forward-compat preserved; setup runner + doctor renderer + logger-root aggregation; slack app gains `cli_setup.py`/`cli_doctor.py` + manifest entries; core deletions (#3, #4, #5 sites). *Validation as a user:* fresh fixture home → install slack app → `personalclaw setup` runs the slack step from the app → `doctor` shows the app section → app logs still appear under `slack_runtime` at the chosen level → uninstall app → setup/doctor show no slack traces.

**S2 — Packaging + rails (≈1 session).** `slack-sdk` out of core deps + resolution of the pip-step inconsistency (verify `app_manager.py`; fix whichever doc is wrong); doctor probe list updated; `--slack-only` deprecation warning in; residue-sweep test + keeps-allowlist file; CHANGELOG entries. *Validation:* clean venv `pip install .` (no slack-sdk pulled), slack app still functions in a gateway whose env has the extra/app-dep installed; residue test green; deliberately add a fake `import slack_sdk` in core → test red.

---

## Contracts & Interfaces (this plan OWNS the three new manifest seams; conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Manifest fields (added to `apps/manifest.py`, to_dict/from_dict parity, unknown-field-preserving per §3.8)

```jsonc
// app.json additions (all optional):
{
  "cli": {
    "setup":  "cli_setup:run",     // "module:function" in the app dir; runs during `personalclaw setup`
    "doctor": "cli_doctor:probe"   // "module:function"; returns list[DoctorLine]
  },
  "loggerRoots": ["slack_runtime"] // logger namespaces the app logs under (static data, read WITHOUT importing app code)
}
```

### C2 — `SetupContext` and `DoctorLine` (new, in `src/personalclaw/sdk/cli.py` — a new sdk module, exported per §2.8)

```python
@dataclass
class SetupContext:
    app_name: str
    get_credential: Callable[[str], str]           # from sdk/credentials
    save_credential: Callable[[str, str], None]    # from sdk/credentials
    settings: "ProviderSettings"                    # bound to this app (§2.6)
    print: Callable[[str], None]
    input: Callable[[str], str]                     # prompt; honors non-interactive (returns "" )

@dataclass
class DoctorLine:
    label: str
    status: Literal["ok", "warn", "fail", "info"]
    detail: str = ""
```

- **Setup runner** (`cli_setup.py`): after core steps, for each installed+enabled app whose manifest has `cli.setup`, import `module:function` from the app dir (pin dir on sys.path exactly like `providers/loader.py`), call `fn(SetupContext(...))`. Exceptions → `ctx.print("⚠️ <app>: <err>")` + continue. Order: core credentials → core models → apps alphabetical. Flag `--app <name>` runs only that app's step. SEL: `sel().log_api_access(caller="cli:setup", operation=f"app_cli_setup:{app}", outcome=…, source="cli")`.
- **Doctor runner** (`cli_doctor.py`): for each such app with `cli.doctor`, import + call with a 5s timeout (thread + join, or `signal.alarm` on posix) expecting `list[DoctorLine]`; timeout/exception → one `DoctorLine("<app>", "fail", "probe error/timeout")`. Render a per-app section.
- **Logger-root aggregation** (`apps/catalog.py`): `installed_logger_roots() -> tuple[str, ...]` reads installed+enabled manifests' `loggerRoots` (JSON only, no app import); consumers `cli.py:750-762` + `updates.py:686-692` use `("personalclaw", *installed_logger_roots())`; graceful `()` when apps dir absent.

### C3 — Residue-sweep rail
`tests/test_provider_boundary_residue.py` greps `src/` (case-insensitive) for `{slack,telegram,discord,whatsapp,signal,imessage}`; fails on any hit whose file:line is not listed in `docs/architecture/provider-boundary-keeps.txt` (format: one `path — judgment` line per deliberate keep; this file IS the machine-checked keeps table).

### Integration points
- **Calls:** `apps/manifest.py` parser, `providers/loader.py` import mechanics, `sdk/credentials`, `ProviderSettings`, `sel()`.
- **Called by:** `personalclaw setup`, `personalclaw doctor`, `cli.py` log setup, `updates.py` log-level handler.
- **Consumed by later plans:** 45 (DESKTOP adds a `desktop:` permission via the same manifest-field pattern), 38 (ECOSYSTEM scaffold emits `cli.*`/`loggerRoots`), 40 (channel apps ship `cli.setup`/`cli.doctor`).
- **Deletes:** `constants.APP_LOGGER_ROOTS`, core `_setup_slack_tokens`/`_setup_slash_command`, `cli_doctor.py` hardcoded Slack section.
- **Coordination:** DISTRIBUTION T1.4 (LLM-SDK demotion) depends on T2.1's pip-step finding — read this plan's Execution log first.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Seams + slack migration

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Manifest fields: add `cli.setup: str` (module:function), `cli.doctor: str`, `loggerRoots: list[str]` to the manifest dataclasses with to_dict/from_dict parity; unknown-field forward-compat untouched | `src/personalclaw/apps/manifest.py` + its tests | round-trip test for the three fields; absent fields default empty; existing manifests still parse |
| T1.2 | Setup runner: after core steps in `personalclaw setup`, iterate installed+enabled apps with `cli.setup`; import declared function from the installed app dir (mirror `providers/loader.py` import mechanics: pin dir on sys.path, import module, resolve attr); pass `SetupContext` (cred accessors from `sdk/credentials`, `ProviderSettings` handle, print/input); exceptions print `⚠️ <app>: <err>` and continue; support `personalclaw setup --app <name>` | `src/personalclaw/cli_setup.py`, small `SetupContext` in `src/personalclaw/sdk/util.py` or new `sdk/cli.py` | fixture app with a setup fn runs inside `personalclaw setup`; a raising fn doesn't abort the wizard; `--app` runs only that app |
| T1.3 | Doctor contributions: for each installed+enabled app with `cli.doctor`, import + call with 5s `signal`/thread timeout, expect `list[DoctorLine]` (new tiny dataclass in the same sdk module); render per-app section; timeout/exception → single `fail` line, doctor continues | `src/personalclaw/cli_doctor.py`, sdk module from T1.2 | fixture probe renders; a `time.sleep(10)` probe shows timeout fail without hanging |
| T1.4 | Logger-root aggregation: `installed_logger_roots() -> tuple[str,...]` reading installed+enabled manifests (no app code import); swap consumers `cli.py:750-762` and `dashboard/handlers/updates.py:686-692` to `("personalclaw", *installed_logger_roots())`; delete `constants.APP_LOGGER_ROOTS` | `src/personalclaw/apps/catalog.py` (or `app_manager.py` — match where manifest iteration already lives), `src/personalclaw/cli.py`, `src/personalclaw/dashboard/handlers/updates.py`, `src/personalclaw/constants.py` | grep for `APP_LOGGER_ROOTS` in src/ returns nothing; log-level handler still applies levels to a fixture app's namespace |
| T1.5 | Slack app absorbs its CLI: move `_setup_slack_tokens` + `_setup_slash_command` bodies to `apps/slack-channel/cli_setup.py` (imports via `personalclaw.sdk.channel` / `sdk.credentials` only); add `cli_doctor.py` reproducing today's Slack section (token presence via cred accessors, workspace-test hint); manifest gains `cli.setup`, `cli.doctor`, `loggerRoots: ["slack_runtime"]` | apps repo: `slack-channel/cli_setup.py`, `slack-channel/cli_doctor.py`, `slack-channel/app.json`; core: delete the two functions + their call sites + `CRED_SLACK_*`/`CRED_OWNER_ID` imports from `src/personalclaw/cli_setup.py`, delete `cli_doctor.py:379-397` block | core `cli_setup.py`/`cli_doctor.py` grep clean of "slack" (case-insensitive); app-side step produces byte-identical prompts to the old flow |
| V1 | Validation: fresh fixture home → install slack app → `personalclaw setup` runs the slack step from the app (masked-hint path round-trips an existing token) → `doctor` shows the app section → app logs appear under `slack_runtime` at the set level → disable app → both commands show no slack traces | — | every observation holds; ledger written |

### Session 2 — Packaging + rails

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Resolve the pip-step question: read `src/personalclaw/apps/app_manager.py` install pipeline — does it install manifest-declared Python deps? Record the answer in the plan's Execution log; then EITHER add `"pythonDependencies": ["slack-sdk>=3.27,<4"]` to the slack app manifest (if supported) OR fix the pyproject extras comment + keep `personalclaw[slack]` documented as the slack install path | `apps/app_manager.py` (read only), slack `app.json` or `pyproject.toml` comment + `docs/architecture/app-platform.md` | the doc and the code agree; DISCOVERY note if the mechanism needs building (that's E6 — file it, don't build it here) |
| T2.2 | Drop `slack-sdk` from core `dependencies`; drop `slack_sdk` from both doctor dep probes (`cli_doctor.py:288,310`) | `pyproject.toml`, `src/personalclaw/cli_doctor.py` | clean venv `pip install .` pulls no slack-sdk; doctor deps line green without it |
| T2.3 | `--slack-only`: emit `DeprecationWarning`-style stderr line mapping to `--headless` (removal note in CHANGELOG); keep behavior identical this release | `src/personalclaw/cli_server.py`, `CHANGELOG.md` | flag still works; warning printed once |
| T2.4 | Residue rail: `tests/test_provider_boundary_residue.py` — case-insensitive grep of `src/` for vendor markers (slack, telegram, discord, whatsapp, signal, imessage) failing on hits outside `docs/architecture/provider-boundary-keeps.txt` (create: one line per deliberate keep, path + judgment) | new test + keeps file | test green on tree; adding `import slack_sdk` to any core module turns it red naming the file |
| V2 | Validation: clean-venv install boots gateway; slack app functions in a gateway with the extra/app-dep present; keeps file lists exactly the provider-boundary.md table rows | — | all hold; CHANGELOG entries written |

## Owner tasks (real world)

1. None external. One decision: confirm `--slack-only` removal timing (S2 defaults to warn-one-release-then-delete).
2. After S1 lands, re-run `personalclaw setup` once on the real home to confirm the migrated slack step round-trips existing credentials (masked-hint path).

## Risks & open questions

- **Doctor executes app code without the gateway** — bounded by timeout + the install-time scan gate + user-initiated context; if that posture feels too loose later, probes can be demoted to manifest-declared *static* checks (credential-key presence lists) at the cost of expressiveness. Decide only if a concrete abuse surfaces (ratchet doctrine).
- **Open:** whether `cli.setup` steps should also be invokable individually (`personalclaw setup --app slack-channel`) — cheap to add in S1; default yes.
