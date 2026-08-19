# LOCAL-MODEL-MANAGER-V2 — atomic plans

**Source plan:** [`LOCAL-MODEL-MANAGER-V2`](../plans/LOCAL-MODEL-MANAGER-V2.md)  
**Code:** `LMMV`  
**Source status:** in_progress

8 atoms: 6 done, 2 todo. The plan's 5 declared sessions are complete (starting with the §4.4/§4.2 layouts.py probe, PR #120), Session 5 having split into independent subscription-creds (§8, LMMV-6 — closed once its reference app landed in PersonalClawApps) and the LMMV-7 hardening/validation capstone, which remains open. No cross-plan dependencies — this is a Wave-0, v2-independent floor. **Capability-gap amendment (2026-08-19)** adds `LMMV-8`: a hardware-aware fit verdict. Every input already exists — the GPU probe, total memory and free disk are all collected — but none reaches the decision, so nothing tells a user whether a model will run before they download it. The atom insists on ONE budget function and ONE verdict, because two independent budget computations disagree in exactly the case that OOMs (an integrated GPU's VRAM counted on top of the system RAM it is carved from).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `LMMV-1` | ✅ (##120) | Shared multi-layout downloaded/delete probe + cleanup-candidate detection (local_models/layouts.py) | — | local_models/layouts.py exports is_downloaded / delete_all_layouts (greedy, all copies) / downloaded_layouts / candidate_paths / reclaimable_bytes / hf_repo_dirname probing save()/HF models-- snapshot/direct-file layouts, with partials excluded from 'downloaded'; tests/test_local_model_layouts.py (47 cases) green; is_downloaded wired into dashboard/model_downloads.py job runner as the asymmetric 'second opinion'. NOTE HALF-INERT: only is_downloaded has a production caller — delete/cleanup helpers remain unwired (finished by LMMV-3). |
| `LMMV-2` | ✅ | Session 1 — Catalog & contract: CapabilityMatrix, runtime-contract/license fields, declarative catalog.json loader + truncation detector, available payload + ModelsPanel chips | `LMMV-1` | CapabilityMatrix (optional, default None) plus runtime/runtime_contract/license/non_commercial/context_tokens/output_tokens/io_mime fields added additively to LocalModel; LocalModelProvider._models_from_catalog() reads app-owned catalog.json, filters platforms against host, and computes downloaded via the §4.4 probe; truncation detector flags on-disk <60% of size_mb (with config_only escape hatch) as integrity:truncated + Repair affordance; the fixed-catalog five migrated to catalog.json (dropping a new entry makes a model appear/download/bind/RUN, deprecated shows a chip without breaking bindings); GET /api/models/available serializes the new fields; ModelsPanel renders matrix/license/non-commercial/deprecation/integrity chips (Success Criteria 6 & 7). |
| `LMMV-3` | ✅ | Session 2 — Download Manager v2: canonical job record, poll-first reattach, .part + cleanup candidates, gated/network/disk error classification, wire delete_all_layouts | `LMMV-1` | ModelDownloadJob.to_dict() emits the one canonical shape (kind/state/progress/reason typed string); FE owns no download state — on mount/tab-switch it polls GET /api/models/downloads and reattaches progress bars, never orphaning a bar on reload (Success Criterion 2); GET /api/models/downloads/cleanup-candidates + POST .../cleanup power a 'Reclaim N GB' affordance in ModelsPanel; the delete route drives layouts.delete_all_layouts (closes the still-live disk-never-frees bug, completing the half-inert LMMV-1 helpers); cancel records the partial as a cleanup candidate; runner-driven direct-URL fetches write .part then os.replace; fetch failures classified gated_repo:no_token / gated_repo:license_not_accepted / network / disk_full with the FE translation table + deep links (failed-gated never auto-retried); ollama PullProgress mapped onto the canonical record inside _ManagerBackedLocalProvider. |
| `LMMV-4` | ✅ | Session 3 — HF token cascade (3-source, whoami-validated) + per-provider real-inference selftest & health endpoints | — | local_models/hf_token.py (re-exported via sdk.credentials) resolves credential-store(.env) → HF_TOKEN/legacy env → ~/.cache/huggingface/token, first whoami-valid source wins via the net.fetch CONNECTOR egress chokepoint (cached ~whoami_ttl_s); GET /api/models/hf-token/status returns per-source {present,valid,username?,masked} with values never leaving the server unmasked (Success Criterion 4); a set/clear field writes source 1 and diarization-pyannote._hf_token() delegates to the cascade (its app-config field honored one release then migrated); GET /api/models/local/{provider}/health never 500s (uses ABC availability_detail()); POST .../selftest runs a real per-capability inference (bundled fixtures, single_flight-serialized, bounded timeout, user-click only) returning typed reasons so a pyannote-4-style contract break fails on API not file presence (Success Criterion 5); Test buttons render inline in ModelsPanel; SEL logs token set/clear; the gated pre-warn consumes cascade status server-side. |
| `LMMV-5` | ✅ | Session 4 — Sidecar isolation runner + resumable install jobs + loaded-models/memory-pressure widget | `LMMV-3` | local_models/sidecar.py runner owns a per-app dedicated venv, a newline-JSON stdio child (5 verbs), process-generation counters, and a watchdog; ProviderConfig gains execution: in-process\|sidecar (default in-process, PROVIDER_TYPES/handler set untouched); the sentence-transformers sidecar variant, killed mid-encode, keeps the gateway alive, raises typed SidecarCrashed, respawns, and search recovers without restart (Success Criterion 1); resumable/idempotent install jobs run on ModelDownloadRegistry via GET /api/models/sidecar/{provider}/install/status (steps/log_tail/remediation, DELETE refuses 409 while running); GET /api/models/loaded + POST /api/models/unload back a compact FE loaded-models section (rows + Unload + pressure bar) and a Dashboard bento 'On this machine' tile via ABC loaded_models()/unload()/ensure_ready(); Unload frees RSS per the pressure snapshot and a model resident after a binding switch shows is_active:false (Success Criterion 8); child-reported rss_mb stat frames feed the widget. |
| `LMMV-6` | ✅ | Session 5a — Subscription-credential model providers (credential_source resolver + one reference app) | — | BrandedProviderSpec (sdk/provider_helpers.py) gains credential_source; _factory's credential order becomes entry.credential → options.api_key → subscription-source resolver → spec.api_key_env → anon placeholder; the resolver reads the named agent CLI's own credential store read-only (e.g. claude-code OAuth/keychain); not-logged-in fails soft/typed via providers/loader.py availability() reporting (False, 'sign in with `claude login` first') so the extensions list greys it out with the reason; ONE reference model-provider app ships (PersonalClawApps) riding CLI auth with no separate API key, sessions/models/catalogs flowing through the normal branded-app path (no agent runtime involved). |
| `LMMV-7` | ⬜ | Session 5b — Hardening: per-model context-budget helper, refresh/registry-drift/destructive-test regressions, full-matrix as-a-user validation | `LMMV-2`, `LMMV-3`, `LMMV-4`, `LMMV-5`, `LMMV-6` | A budget-derivation helper in local_models/ is consumed by the reasoning-axis one_shot_completion path, deriving budgets from catalog context_tokens/output_tokens instead of hardcoded constants (no compaction logic rewritten); regression tests lock: (a) refresh_providers() leaves every bundled/sidecar provider registered — the two-population invariant (Success Criterion 9), (b) a sidecar proxy registered through ModelTypeHandler keeps the APP-name key + is_local_model_provider duck-type + refresh survival (registry-drift), (c) a suite-level fixture asserts no fs-touching test can reach a real model dir / cache root — only tmp_path (Success Criterion 10, the bound-model-deletion incident unreproducible by construction); the full download/delete/bind/RUN matrix across all 6 providers is validated as a user through the new surfaces. |
| `LMMV-8` | ⬜ | Hardware-aware model fit: one memory budget, one traffic-light verdict, fit-filtered browse | — | ONE module owns one memory-budget function and one fit verdict — unified memory counted once, an integrated GPU's VRAM never added on top of system RAM (the arithmetic that otherwise reports a larger budget than the machine has and then OOMs on load), only discrete VRAM adding a second pool, and a fixed reserve subtracted for the OS and runtime — producing a red/yellow/green/unknown verdict from model weights plus a KV-cache estimate; the host facts we ALREADY collect (GPU probe, total memory, free disk) are routed through one helper instead of three unrelated call sites, so the fit answer cannot disagree with itself; every model row renders a fit chip beside the existing status chips, and a browse filter that hides models the device cannot run defaults ON while an unknown or unmeasured budget hides NOTHING; a quoted size uses the median variant rather than the smallest so the chip cannot promise a fit the user will not get from the variant they actually pick, and the download panel steps down to the largest variant that fits; a pre-download free-space check refuses with a typed reason naming both numbers and SKIPS WITH A WARNING when the filesystem cannot be measured (an unmeasurable disk is not a reason to block a good download); unit tests pin the budget arithmetic including a machine smaller than the reserve, plus a vacuity assertion that on a synthetic small host at least one shipped model is red and at least one is green; the filter default and the reserve round-trip through config |

## Atom scopes

### `LMMV-1` — Shared multi-layout downloaded/delete probe + cleanup-candidate detection (local_models/layouts.py)

**Status:** done (PR ##120)

§4.4 One multi-layout downloaded/delete probe; §4.2 cleanup-candidate detection helpers

**Done when:** local_models/layouts.py exports is_downloaded / delete_all_layouts (greedy, all copies) / downloaded_layouts / candidate_paths / reclaimable_bytes / hf_repo_dirname probing save()/HF models-- snapshot/direct-file layouts, with partials excluded from 'downloaded'; tests/test_local_model_layouts.py (47 cases) green; is_downloaded wired into dashboard/model_downloads.py job runner as the asymmetric 'second opinion'. NOTE HALF-INERT: only is_downloaded has a production caller — delete/cleanup helpers remain unwired (finished by LMMV-3).

### `LMMV-2` — Session 1 — Catalog & contract: CapabilityMatrix, runtime-contract/license fields, declarative catalog.json loader + truncation detector, available payload + ModelsPanel chips

**Status:** todo

§2.1 Structured capability matrix on LocalModel; §2.2 Runtime-contract metadata + license surfacing; §2.3 Declarative model-card catalog with truncation detection

**Done when:** CapabilityMatrix (optional, default None) plus runtime/runtime_contract/license/non_commercial/context_tokens/output_tokens/io_mime fields added additively to LocalModel; LocalModelProvider._models_from_catalog() reads app-owned catalog.json, filters platforms against host, and computes downloaded via the §4.4 probe; truncation detector flags on-disk <60% of size_mb (with config_only escape hatch) as integrity:truncated + Repair affordance; the fixed-catalog five migrated to catalog.json (dropping a new entry makes a model appear/download/bind/RUN, deprecated shows a chip without breaking bindings); GET /api/models/available serializes the new fields; ModelsPanel renders matrix/license/non-commercial/deprecation/integrity chips (Success Criteria 6 & 7).

### `LMMV-3` — Session 2 — Download Manager v2: canonical job record, poll-first reattach, .part + cleanup candidates, gated/network/disk error classification, wire delete_all_layouts

**Status:** todo

§4.1 Canonical server-side progress record; §4.2 .part atomic writes + cleanup candidates; §4.3 Gated-repo error translation; §9 download_parallelism config field

**Done when:** ModelDownloadJob.to_dict() emits the one canonical shape (kind/state/progress/reason typed string); FE owns no download state — on mount/tab-switch it polls GET /api/models/downloads and reattaches progress bars, never orphaning a bar on reload (Success Criterion 2); GET /api/models/downloads/cleanup-candidates + POST .../cleanup power a 'Reclaim N GB' affordance in ModelsPanel; the delete route drives layouts.delete_all_layouts (closes the still-live disk-never-frees bug, completing the half-inert LMMV-1 helpers); cancel records the partial as a cleanup candidate; runner-driven direct-URL fetches write .part then os.replace; fetch failures classified gated_repo:no_token / gated_repo:license_not_accepted / network / disk_full with the FE translation table + deep links (failed-gated never auto-retried); ollama PullProgress mapped onto the canonical record inside _ManagerBackedLocalProvider.

### `LMMV-4` — Session 3 — HF token cascade (3-source, whoami-validated) + per-provider real-inference selftest & health endpoints

**Status:** todo

§5 HF Token Cascade; §6 Per-Provider Real-Inference Selftest + Health; §4.3 gated pre-warn (token-status-aware); §9 whoami_ttl_s / selftest_timeout_s config fields

**Done when:** local_models/hf_token.py (re-exported via sdk.credentials) resolves credential-store(.env) → HF_TOKEN/legacy env → ~/.cache/huggingface/token, first whoami-valid source wins via the net.fetch CONNECTOR egress chokepoint (cached ~whoami_ttl_s); GET /api/models/hf-token/status returns per-source {present,valid,username?,masked} with values never leaving the server unmasked (Success Criterion 4); a set/clear field writes source 1 and diarization-pyannote._hf_token() delegates to the cascade (its app-config field honored one release then migrated); GET /api/models/local/{provider}/health never 500s (uses ABC availability_detail()); POST .../selftest runs a real per-capability inference (bundled fixtures, single_flight-serialized, bounded timeout, user-click only) returning typed reasons so a pyannote-4-style contract break fails on API not file presence (Success Criterion 5); Test buttons render inline in ModelsPanel; SEL logs token set/clear; the gated pre-warn consumes cascade status server-side.

### `LMMV-5` — Session 4 — Sidecar isolation runner + resumable install jobs + loaded-models/memory-pressure widget

**Status:** done

§3 Sidecar Isolation (dedicated-venv subprocesses); §3.2 Resumable install jobs; §7 Loaded-Models / Memory-Pressure Widget; §9 pressure_warn_pct / sidecar_restart_max config fields

**Done when:** local_models/sidecar.py runner owns a per-app dedicated venv, a newline-JSON stdio child (5 verbs), process-generation counters, and a watchdog; ProviderConfig gains execution: in-process|sidecar (default in-process, PROVIDER_TYPES/handler set untouched); the sentence-transformers sidecar variant, killed mid-encode, keeps the gateway alive, raises typed SidecarCrashed, respawns, and search recovers without restart (Success Criterion 1); resumable/idempotent install jobs run on ModelDownloadRegistry via GET /api/models/sidecar/{provider}/install/status (steps/log_tail/remediation, DELETE refuses 409 while running); GET /api/models/loaded + POST /api/models/unload back a compact FE loaded-models section (rows + Unload + pressure bar) and a Dashboard bento 'On this machine' tile via ABC loaded_models()/unload()/ensure_ready(); Unload frees RSS per the pressure snapshot and a model resident after a binding switch shows is_active:false (Success Criterion 8); child-reported rss_mb stat frames feed the widget.

**DONE.** `src/personalclaw/local_models/sidecar.py` owns the runner and
`_sidecar_child.py` the child harness — stdlib-only, loaded by path, because the app's
dedicated venv (`~/.personalclaw/apps/{app}/venv`) has no `personalclaw` in it. Five verbs
(`ping`/`load`/`call`/`stat`/`unload`), one JSON object per line, and the §12 scope fence
held: no HTTP, no routing.

**Success Criterion 1 is proven by really killing a real child mid-call**, not by a mocked
failure return. `SIGKILL` during an encode yields `SidecarCrashed(reason="signal_9")` with
`typed_reason="sidecar_crashed:signal_9"`, the gateway (the test process) lives, and the
NEXT call spawns generation 2 and returns a real vector — recovery with no restart and no
re-registration, because the child is spawned on demand.

**Two measured findings.** (1) *A truncated frame is only dangerous when it is VALID JSON.*
The first version of the test killed the child mid-fragment, and removing the
newline-completeness guard still passed — JSON parsing rejects `{"result": {"vector": [0.1,`
on its own. So the test was rewritten around the case only the newline rule can catch: a
complete, valid frame carrying the pending request id but no terminating newline. Without
the guard the caller is handed `{'vector': ['HALF']}` from a process that died before
finishing the write; with it, `SidecarCrashed`. (2) *A mis-nested `call` payload was
silently dropped.* `{"method": "encode", "hang": true}` reached the worker as `{}` because
worker arguments belong under `payload` — the arg-nesting bug class. The child now refuses
an unexpected top-level key instead of handing the worker an empty dict.

**The generation fence lives in exactly one place** (`SidecarRunner.deliver`), and
`_await_reply` deliberately does NOT repeat it: two half-rules mask each other, and a
mutation of either then reds nothing. Request ids are `"<generation>:<seq>"`, so a zombie's
reply is normally fenced by the id; the test forges the CURRENT id onto a
previous-generation frame, which only the counter can catch. Removing the fence makes the
caller believe the dead child's answer.

**The watchdog returns its decision as data** (`noop`/`respawned`/`budget_exhausted` with the
generation and restart count), so tests assert the outcome instead of sleeping. `restarts` is
counted on generation, not on a live child handle — the first version undercounted, because a
crash detaches the handle, which is exactly the case the counter exists to report.
`sidecar_restart_max` bounds consecutive respawns so a genuinely broken venv produces one
honest error rather than a busy-loop.

**Install jobs ride the EXISTING registry** (`ModelDownloadRegistry.start_install`, `kind:
"sidecar-install"` — a value LMMV-3 already reserved), not a second one. venv → deps →
weights, each existence-checked, and the deps receipt is written only after pip exits zero,
which is what makes a killed install resume rather than skip. `GET
/api/models/sidecar/{provider}/install/status` merges the canonical job state with step
detail + `log_tail` + a `remediation` distinct from `error`; `DELETE` answers 409 while a job
runs and 400 for a venv core did not create (marker-file absent → never deleted).

**Residency** (`local_models/residency.py`) backs `GET /api/models/loaded` + `POST
/api/models/unload`. A sidecar's `rss_mb` is CHILD-REPORTED (the gateway cannot see another
process's heap); an in-process model's is `None`, never a fabricated split of the gateway's
own footprint. `is_active` is attribution, not liveness, and is checked under BOTH spellings
of the provider name (registry app-name and `provider.name`) or a bound model would read as
reclaimable. Unload returns a fresh pressure snapshot, so the bar moving is the proof.

**DEVIATION — no Dashboard bento tile: the bento was retired.** `DashboardPage.tsx` states it
outright ("no bento boxes … the customizable grid + per-user layout persistence were
retired"), so "On this machine" ships as a hard-imported widget in a `<Section>` band, the
same shape `PinnedArtifacts` uses. One `Meter` primitive was added to `ui/` (with its
`.doc.ts`) rather than hand-rolling a fourth linear bar; both surfaces share
`lib/residency.ts`, so they cannot drift on which model is reclaimable.

**DEVIATION — the sentence-transformers sidecar VARIANT is a PersonalClawApps follow-up.**
`git ls-files apps/` is still 0 in this repo (the LMMV-2 finding): no provider app is tracked
here, so the ST variant's `app.json` (`execution: "sidecar"`) and its worker module land in
the apps repo. The core mechanism is complete and Success Criterion 1 is proven end-to-end
against a real child; what the apps-repo change adds is that child being sentence-transformers.

**Config:** a new `local_models` section (`pressure_warn_pct`, `sidecar_restart_max`) wired
through all five points + `config-baseline.json`. Both are advisory on the user's own machine:
the threshold blocks nothing, and nothing is ever auto-unloaded.

**Gates:** `make lint` clean (mypy 876 files) · `tests/test_local_model_sidecar.py` 57 passed
· the config/inert/portability/durability/reference/spawn-ceiling/dag rails 280 passed ·
`web` 275 files / 2745 tests passed, typecheck clean. Six mutations were run; the one that
reded nothing (`total > 0` in the pressure warn) exposed a weak test, not dead code — the
guard only bites at `warn_pct=0`, which the test now covers.

### `LMMV-6` — Session 5a — Subscription-credential model providers (credential_source resolver + one reference app)

**Status:** todo

§8 Subscription-Credential Model Providers (am.d)

**Done when:** BrandedProviderSpec (sdk/provider_helpers.py) gains credential_source; _factory's credential order becomes entry.credential → options.api_key → subscription-source resolver → spec.api_key_env → anon placeholder; the resolver reads the named agent CLI's own credential store read-only (e.g. claude-code OAuth/keychain); not-logged-in fails soft/typed via providers/loader.py availability() reporting (False, 'sign in with `claude login` first') so the extensions list greys it out with the reason; ONE reference model-provider app ships (PersonalClawApps) riding CLI auth with no separate API key, sessions/models/catalogs flowing through the normal branded-app path (no agent runtime involved). NOTE CORE HALF LANDED, atom stays todo: the credential_source field, the five-hop _factory order, the read-only resolver (llm/subscription_credentials.py) and the availability() probe providers/loader.py derives from a declared credential_source are all in with tests (tests/test_subscription_credentials.py). ONLY the reference app remains, and it is a PersonalClawApps deliverable — a separate repository, unreachable from core (git ls-files apps/ is 0 here) — so the mechanism is production-INERT until one app registers a SubscriptionSource. Its exact app.json + provider.py contract is written out in the plan's Execution log entry for this session.

### `LMMV-7` — Session 5b — Hardening: per-model context-budget helper, refresh/registry-drift/destructive-test regressions, full-matrix as-a-user validation

**Status:** todo

§2.2 per-model context-budget helper; §11 Disposition invariants; §12 risk regressions; Success Criteria 9 & 10; Session 5 as-a-user validation sweep

**Done when:** A budget-derivation helper in local_models/ is consumed by the reasoning-axis one_shot_completion path, deriving budgets from catalog context_tokens/output_tokens instead of hardcoded constants (no compaction logic rewritten); regression tests lock: (a) refresh_providers() leaves every bundled/sidecar provider registered — the two-population invariant (Success Criterion 9), (b) a sidecar proxy registered through ModelTypeHandler keeps the APP-name key + is_local_model_provider duck-type + refresh survival (registry-drift), (c) a suite-level fixture asserts no fs-touching test can reach a real model dir / cache root — only tmp_path (Success Criterion 10, the bound-model-deletion incident unreproducible by construction); the full download/delete/bind/RUN matrix across all 6 providers is validated as a user through the new surfaces.

### `LMMV-8` — Hardware-aware model fit: one memory budget, one traffic-light verdict, fit-filtered browse

**Status:** todo

Capability-gap amendment (2026-08-19)

**Done when:** ONE module owns one memory-budget function and one fit verdict — unified memory counted once, an integrated GPU's VRAM never added on top of system RAM (the arithmetic that otherwise reports a larger budget than the machine has and then OOMs on load), only discrete VRAM adding a second pool, and a fixed reserve subtracted for the OS and runtime — producing a red/yellow/green/unknown verdict from model weights plus a KV-cache estimate; the host facts we ALREADY collect (GPU probe, total memory, free disk) are routed through one helper instead of three unrelated call sites, so the fit answer cannot disagree with itself; every model row renders a fit chip beside the existing status chips, and a browse filter that hides models the device cannot run defaults ON while an unknown or unmeasured budget hides NOTHING; a quoted size uses the median variant rather than the smallest so the chip cannot promise a fit the user will not get from the variant they actually pick, and the download panel steps down to the largest variant that fits; a pre-download free-space check refuses with a typed reason naming both numbers and SKIPS WITH A WARNING when the filesystem cannot be measured (an unmeasurable disk is not a reason to block a good download); unit tests pin the budget arithmetic including a machine smaller than the reserve, plus a vacuity assertion that on a synthetic small host at least one shipped model is red and at least one is green; the filter default and the reserve round-trip through config
