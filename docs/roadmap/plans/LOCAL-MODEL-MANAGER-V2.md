# Plan: Local Model Manager v2 — Sidecar Isolation, Download UX, Token Cascade, Catalog Contract

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)
**Created:** 2026-07-12
**Depends on:** nothing in the Workflows-v2 program (v2-independent, Wave 0). Extends the ✅-complete local-model-provider contract campaign (unified `LocalModel`/`LocalModelProvider` across all 6 locals).
**Scope:** the structural layer the contract campaign lacked — process isolation for crash-prone native libs, honest download/install UX, token handling, real-inference selftests, a declarative catalog contract, and runtime observability with unload controls.

---

## Research Integration (2026-07-12)

One approved workstream item folded in, mechanism-level:

- **NEW-8** (core) — sidecar isolation → §3; resumable install jobs + child-reported memory → §3.2, §7; canonical server-side download progress with poll/cancel/.part atomic writes → §4; gated-repo error translation → §4.3; three-source HF token cascade with whoami validation → §5; per-provider real-inference selftest endpoints → §6; declarative catalog with truncation detection → §2.3; runtime-contract metadata + license surfacing → §2.2; loaded-models/memory-pressure widget with unload → §7
- **NEW-8 am.(a)** — per-model structured capability matrix → §2.1
- **NEW-8 am.(b)** — model-aware per-model context budgets → §2.2
- **NEW-8 am.(c)** — declarative model-card data files (drop-a-file add/deprecate) → §2.3
- **NEW-8 am.(d)** — subscription-credential model providers (reuse Claude Code/Codex/Copilot auth) → §8

---

## Overview

The local-model contract campaign proved the full download/delete/bind/RUN matrix for all 6 local providers — by hand, through the real UI, finding 13 bugs. Every one of those bugs was patched *per-bug*: the loky segfault got env-var band-aids (`TOKENIZERS_PARALLELISM=false`, forced single-process encode), the HF `models--…` layout miss got a wider probe, the phantom binding got a synthetic row, the pyannote gated-repo friction got resolved manually with a user-supplied token. This plan is the *systematic* answer to each of those bug classes:

- **Crash class** (loky segfault stranding the store unsearchable; the full-suite native segfault in torch+faiss+av teardown) → optional dedicated-venv **subprocess sidecars** so a native-lib crash kills a child, never the gateway (§3).
- **Detection/delete class** (ST checked only the `model.save()` layout and missed `models--…`; a destructive test deleted the user's real bound L6 model) → a **declarative catalog with expected sizes and a truncation/weights-missing detector**, plus one shared multi-layout probe helper (§2.3, §4.4).
- **Token class** (pyannote's HF-token friction was the last cell of the matrix to close) → a **three-source token cascade with live `whoami` validation** (§5), and **gated-repo errors translated to the exact user action** (§4.3).
- **"Is it actually working?" class** (the matrix RUN column was proven manually) → **per-provider real-inference selftest endpoints** that automate it (§6).
- **"What is eating my RAM?" class** (models outlive provider switches; sidecars add child processes) → a **loaded-models / memory-pressure surface with unload** (§7).

**Soul guardrail:** single user, one machine, local files. No model registry service, no fleet telemetry, no hardware-tier gatekeeping — the fits-machine flag was deliberately removed in the contract campaign and stays removed (§10). Everything here is a better *manager* for the models the user already chose.

### Starting points (verified against code, 2026-07-12 recon)

The design builds on what actually exists — several earlier assumptions are corrected here:

- **`LocalModelProvider` is the management axis, not the inference axis** (`local_models/provider.py`): `name`/`display_name` abstract props, `searchable: bool = False` class attr, `is_available() -> bool`, `list_models() -> list[LocalModel]`, `search_models(q)`, `download_model(name) -> bool`, `delete_model(name) -> bool`, `cache_dir() -> str | None` (the dir whose on-disk growth tracks a download). Inference rides the use-case ABCs — a local provider subclasses BOTH (e.g. `apps/faster-whisper/provider.py:56 class FasterWhisperProvider(SttProvider, LocalModelProvider)`).
- **The 6 locals:** faster-whisper (stt), piper-tts (tts), sentence-transformers (embedding), diarization-onnx + diarization-pyannote (diarization), ollama-models (chat+embedding — surfaced via the `_ManagerBackedLocalProvider` adapter over its `ModelManager`, `local_models/registry.py:121`, wired by `register_config_model_managers()` at startup).
- **The registry keys on the APP name** (`ext.name`, e.g. `faster-whisper`), not `provider.name` (`faster_whisper`) — `ModelTypeHandler` duck-types `is_local_model_provider(obj, capabilities)` (`local_models/registry.py:217`: contract check AND (subclass OR caps ∩ `_DOWNLOADABLE_CAPS = {stt, tts, embedding, diarization, chat}`)) and calls `register_provider(provider, capabilities, name=ext.name)`. The use-case registries accept both spellings via alias tuples. Every wrapper this plan introduces must preserve the app-name key and the duck-typed contract.
- **A download-job runner ALREADY exists** — `dashboard/model_downloads.py`: `ModelDownloadRegistry` owns background jobs, per-job SSE streams (hub key `download:<id>`), a byte-poller sampling `_dir_size(_cache_root(provider))` against `_expected_size_bytes`, and cancel. HTTP surface in `dashboard/handlers/model_downloads.py`: `GET/POST /api/models/downloads`, `GET /api/models/downloads/{id}/stream` (SSE), `DELETE /api/models/downloads/{id}`, `DELETE /api/models/local/{provider}/{model}`, `GET /api/models/local/{provider}/search`. §4 **upgrades this in place** — it does not build a second download path.
- **`capableModels` is a FRONTEND function** (`web/src/pages/settings/ModelsPanel.tsx:43`) filtering `GET /api/models/available` rows per use-case and synthesizing rows for bound-but-absent models (the phantom-binding fix). There is no backend symbol by that name — license/deprecation surfacing at bind time (§2.2) is payload + FE work, not a backend hook.
- **Two-population registry gotcha:** use-case registries split populations — app-registered bundled providers must survive `refresh_providers()`; only config-derived remote adapters are rebuilt on config change. Clearing everything silently unregisters STT until restart (documented regression). Sidecar-wrapped providers register as *bundled* and must survive refresh.
- **Token handling today is two-source and validation-free:** `apps/diarization-pyannote/provider.py:55 _hf_token()` reads app config `hf_token` else `HF_TOKEN` env. No `whoami`, no per-source status, no HF-CLI-file fallback, and each provider rolls its own.
- **Bug-class history this plan must answer** (memory refs): `reference_st_reindex_loky_segfault`, `full-suite-native-segfault`, `reference_local_model_delete_detection` (HF `models--…` layout miss + phantom binding + destructive-test isolation), `reference_whisper_bias_prompt_budget` (per-provider hotword budgets), and the pyannote 3.x→4.x runtime-contract break (`itertracks`/`DiarizeOutput`) that only surfaced after the gated-repo/token hurdle was cleared.

---

## 1. Design Tenets

1. **Upgrade seams in place.** Every mechanism lands on an existing seam: the `LocalModelProvider` ABC, `ModelDownloadRegistry`, `GET /api/models/available`, `ModelsPanel.tsx`. No parallel registries, no second download path, no new provider *type*.
2. **Additive contract evolution.** `download_model() -> bool` and `is_available() -> bool` keep working for any provider that never opts in; the duck-typed `is_local_model_provider` check must accept both old and new shapes. New capability is expressed as optional methods/fields with sensible defaults on the ABC.
3. **Honest degradation with machine-readable reasons.** Every failure path emits a typed reason string (`gated_repo:no_token`, `truncated:expected_1200mb_got_87mb`, `sidecar_crashed:signal_11`) that the UI can translate into one concrete user action — the omnivoice `diarization_skipped:no_token` idiom.
4. **Nothing here touches memory or knowledge.** Model cards, tokens, download jobs, and runtime state are provider/config machinery. The knowledge store (`knowledge.db`, user items) and the memory subsystem (harness internals) are out of scope; no `knowledge_*` or memory API appears in this plan.

---

## 2. Catalog & Contract Upgrades

### 2.1 Structured capability matrix on `LocalModel` (am.a)

`LocalModel.capabilities: list[str]` is a coarse list over `{stt, tts, embedding, diarization, chat}` — and it is **load-bearing**: `_DOWNLOADABLE_CAPS` intersection in `is_local_model_provider` is what excludes hosted providers (FAL) inheriting no-op stubs, and active-model pruning derives known names from it. So the matrix is **additive, not a replacement** (the approved amendment said "replacing"; recon shows the flat list must survive at the registry boundary — corrected):

```python
@dataclass
class CapabilityMatrix:            # local_models/provider.py — optional, default None
    word_timestamps: bool = False
    segment_timestamps: bool = False
    speaker_labels: bool = False          # a joint transcribe+diarize model fills `speaker` at source
    acoustic_events: bool = False
    hotword_biasing: bool = False
    hotword_budget: int = 0               # chars/tokens the bias lever tolerates — the whisper 224-token
                                          # bug-class becomes a declared, per-model number
    languages: list[str] = field(default_factory=list)   # [] = unknown/broad
    reasoning_budget_control: bool = False # thinking-budget honorable per request (chat models)

# LocalModel gains: matrix: CapabilityMatrix | None = None
```

Consumers: binding UIs render feature chips instead of guessing; the Lexicon→bias wiring reads `hotword_budget` instead of assuming whisper's limit; a future joint transcribe+diarize provider (MOSS-Transcribe-Diarize-class, 0.9B, CPU-capable) declares `capabilities=["stt","diarization"]` + `speaker_labels=True` so downstream fusion can no-op. The matrix rides `GET /api/models/available` into `AvailableModel` (FE type) so `capableModels()` filtering can consult flags without a second fetch.

### 2.2 Runtime-contract metadata + license surfacing (NEW-8 core, am.b)

`LocalModel` gains runtime-contract fields (all optional, sourced from the model card §2.3):

| Field | Purpose |
|---|---|
| `license: str` | SPDX id, normalized never rejected (omnivoice rule) |
| `non_commercial: bool` | derived from license; **rendered as a warning chip at bind time** in ModelsPanel (pyannote community-1 is exactly this case) |
| `runtime: str` | `ctranslate2` / `onnx` / `torch` / `piper` / `gguf-llamacpp` — a `.gguf` file is NOT evidence of runnability (MOSS GGUF needs a custom runtime); feasibility checks test the *runtime*, not the file format |
| `runtime_contract: str` | provider-defined version tag for the inference API surface (e.g. `pyannote>=4`, `itertracks` vs `DiarizeOutput`) — the pyannote 3.x→4.x break becomes a declared, testable string instead of a silent crash |
| `context_tokens: int` / `output_tokens: int` | **per-model context budgets (am.b):** chat/embedding models declare real windows; consumers (compression/summarization triggers, the reasoning-axis `one_shot_completion` path) derive budgets from catalog metadata instead of hardcoded constants. Budget derivation is a helper in `local_models/`, consumed by callers — this plan does NOT rewrite any compaction logic, it makes the number available |
| `io_mime: dict` | input/output MIME types (`audio/wav → text/plain`) for planner/binding sanity checks |

Bind-time surfacing is **frontend + payload work**: `model_registry.py`'s `GET /api/models/available` serializes the new fields; `ModelsPanel.tsx` renders license/deprecation/non-commercial chips in the picker `capableModels()` feeds. No backend "bind gate" — warn, don't block (single-user machine; the user's license posture is their call).

### 2.3 Declarative model-card catalog with truncation detection (NEW-8 core, am.c)

Each fixed-catalog local provider currently hardcodes its model list in `provider.py`. Replace with a **declarative catalog file** the provider loads — adding or deprecating a model becomes a file drop, no code change:

- **Location:** `catalog.json` in the app dir (repo `apps/<name>/catalog.json`, propagated to the installed copy via the normal `POST /api/apps/{name}/update` flow). JSON, not YAML — matching every other PClaw store (omnivoice uses YAML; adapted to house style).
- **Per-entry schema:** `{name, label, status: active|deprecated|sunset, deprecated_at?, size_mb, config_only?: bool, platforms?: ["darwin-arm64", …], gated?: bool, source, license, runtime, runtime_contract, context_tokens?, output_tokens?, io_mime?, matrix: {…}, parameter_schema?: {…}}`. `parameter_schema` is Draft-07 + `x-meta`, same dialect as `ProviderConfig.settingsSchema`, describing per-model tunables.
- **`ABC` default implementation:** `LocalModelProvider.list_models()` gains a protected helper `_models_from_catalog()` that reads the file, filters `platforms` against the host, maps entries to `LocalModel`, and computes `downloaded` via the shared multi-layout probe (§4.4). Providers with dynamic catalogs (ollama via `ModelManager.search_catalog` — the recon-verified catalog axis) are untouched; the model-card file is for the fixed-catalog five.
- **Truncation / weights-missing detection:** `size_mb` is the expected footprint. Detector: on-disk size < 60% of expected AND no active download job → the model row carries `integrity: "truncated"` + a Repair (re-download) affordance; `config_only: true` is the escape hatch for pipeline repos with no local weights (pyannote's pipeline layout — without it, tiny caches misflag). This is the systematic form of the delete/detection bug-class fix.
- **Deprecation flow:** `status: deprecated` renders a chip + keeps the model bindable; `sunset` hides it from new bindings but never breaks an existing `active_models.json` ref (a pinned ref that can't resolve RAISES by design — sunsetting must not manufacture that).

---

## 3. Sidecar Isolation (dedicated-venv subprocesses)

The structural fix for the crash class. **Opt-in per provider**, because for well-behaved libs in-process is simpler and faster.

### 3.1 Execution modes

`ProviderConfig` (app manifest) gains `execution: "in-process" | "sidecar"` (default in-process). For `sidecar`:

- A new `local_models/sidecar.py` runner owns: dedicated venv at `~/.personalclaw/apps/{name}/venv/` (pip deps from the manifest's `pythonDependencies` installed THERE, not the shared core venv — today's `dependencies.pythonDependencies` land in the core venv and require a gateway restart; sidecar apps escape both problems), a child process speaking newline-JSON over stdio (same shape as the piper/Kokoro-style worker pattern), and a supervisor with the app-backend watchdog's semantics (relaunch on crash, don't survive gateway restart, `start_enabled_app_backends()` precedent in `dashboard/server.py`).
- **The registration seam is unchanged:** the app's factory returns a thin in-gateway proxy object that still subclasses the use-case ABC + `LocalModelProvider` — `ModelTypeHandler` duck-typing, the APP-name registry key, and the two-population rule (bundled proxies survive `refresh_providers()`) all hold with zero registry changes. The proxy forwards `transcribe`/`encode`/… calls to the child; a child crash raises a typed `SidecarCrashed(reason="signal_11")` in the caller instead of segfaulting the gateway.
- **Process-generation counters** (ULS pattern): every spawn increments a generation; health-waits and in-flight calls carry the generation they awaited and abort when superseded — no stale-child races on restart.
- **Crash-class targets:** `sentence-transformers` ships a sidecar variant first (the loky segfault provider — the env-var band-aids in `reference_st_reindex_loky_segfault` become the sidecar's *internal* defaults rather than gateway-wide settings), then `faster-whisper-isolated` as an explicit opt-in variant. pyannote (torch) is the third candidate.
- **Child-reported memory:** the stdio protocol includes a periodic `{"rss_mb": N}` stat frame; feeds §7.

### 3.2 Resumable install jobs

Sidecar install (venv create + pip + weights) is a **background job with a rich poll shape**, reusing the `ModelDownloadRegistry` job/SSE plumbing rather than a new registry:

```
GET /api/models/sidecar/{provider}/install/status →
{provider, installed, managed, install_dir,
 job: {state, steps: [{name, status}], log_tail: [...], error, remediation, weights_progress}}
```

- `remediation` is distinct from `error` — the actionable next step ("re-run install", "free 2 GB", "add an HF token in Settings → Models"), the omnivoice field this shape is lifted from.
- Install is **resumable/idempotent**: each step existence-checks before doing work (venv exists → skip; package importable → skip; weights pass the §4.4 probe → skip), so a killed install re-runs from where it died.
- `DELETE` of a sidecar install refuses (`409`) while a job runs; user-managed venvs (`managed: false`) are never deleted.

---

## 4. Download Manager v2

All changes land INSIDE `dashboard/model_downloads.py` + its handlers — the existing job registry, SSE hub, and route table are the base.

### 4.1 Canonical server-side progress record

`ModelDownloadJob.to_dict()` becomes the **one canonical progress shape**:

```
{id, provider, model, kind: "weights"|"sidecar-install",
 state: queued|running|done|error|cancelled,
 progress, speed_bps, eta_s, total_bytes, downloaded_bytes,
 error, reason}          # reason = typed machine-readable string (§1 tenet 3)
```

- **Poll is the primary contract, SSE the accelerator** (ULS reattach rule): `GET /api/models/downloads` already lists live + recent jobs — the FE owns NO download state; on mount/tab-switch ModelsPanel polls the list and *reattaches* its progress UI to any job already running, then subscribes to the job's SSE stream for deltas. Reloading the page mid-download must never orphan the bar.
- **Byte-level progress where the fetch allows it:** providers may implement optional `download_model_ex(name, progress_cb)` reporting real byte counts; the existing `_dir_size(cache_root)` poller stays as the universal fallback (it is honest-but-coarse, and correct for HF snapshot downloads that fan out over files). `total_bytes` comes from the model card's `size_mb` (§2.3) when the fetch can't pre-flight it.

### 4.2 `.part` atomic writes + cleanup candidates

- Any download path the runner itself drives (direct-URL weights: piper voices, onnx models) writes `<dest>.part` then `os.replace` — the repo-wide `atomic_write` convention applied to large binaries.
- HF-hub-driven downloads already stage internally; the runner's job is not to re-implement them but to **detect their leftovers**: `GET /api/models/downloads/cleanup-candidates` enumerates `*.part`, `*.tmp`, and incomplete `models--…/blobs` entries under each provider's `cache_dir()`, with sizes; `POST …/cleanup {confirm:true}` deletes. Surfaced as a "Reclaim N GB" affordance in ModelsPanel.
- Cancel (exists today) additionally records the partial as a cleanup candidate instead of leaving it invisible.

### 4.3 Gated-repo error translation (NEW-8 core)

The exact pyannote friction, generalized into the shared download error path:

- The runner classifies fetch failures: HF `x-error-code: GatedRepo` header / 401 / 403-with-license-hint → `reason: "gated_repo:no_token"` or `"gated_repo:license_not_accepted"`; DNS/timeout → `"network"`; disk → `"disk_full"`.
- The FE translation table turns each into ONE concrete action: gated → "This model requires a HuggingFace token and license acceptance. [Open model page] [Add token in Settings]" — deep-linking to the §5 token settings and the HF repo page. `LocalModel.gated` (field exists today) pre-warns *before* the user clicks Download when no valid token is present (§5 cascade status is known server-side).
- Failed-gated jobs are **never auto-retried** — retry without the token is guaranteed friction.

### 4.4 One multi-layout downloaded/delete probe

The delete-detection bug-class gets a single shared helper instead of per-provider guesses: `local_models/layouts.py` with `is_downloaded(cache_root, model) -> bool` and `delete_all_layouts(cache_root, model) -> list[Path]` probing EVERY layout a download can produce — the provider's own `save()` layout, the HF hub `models--{org}--{name}` snapshot layout, and direct-file layouts. All 6 providers converge on it; the §2.3 truncation detector and the `_is_downloaded` check in the job runner (`model_downloads.py:172`) call the same helper. Unit tests pin each layout; **every fs-touching test monkeypatches `_models_dir` to `tmp_path`** (the destructive-test lesson is a test-suite invariant here, asserted by a fixture).

---

## 5. HF Token Cascade (three sources, whoami-validated)

One shared helper (`local_models/hf_token.py`, re-exported via `sdk.credentials`) replacing each provider's private two-source lookup:

1. **App credential** — the PClaw credential store: `save_credential()` → `~/.personalclaw/.env` (0600, mirrored to `os.environ`). *(Reality correction: omnivoice Fernet-encrypts in SQLite with a machine-id key; PClaw's real seam is the existing `.env` credential store — same file the branded-provider `_factory` credential order already reads. No new encryption machinery.)*
2. **Environment** — `HF_TOKEN`, legacy `HUGGING_FACE_HUB_TOKEN`.
3. **HF CLI file** — `~/.cache/huggingface/token`.

**The first source that has a token AND survives a live `whoami` call wins** — an invalid higher-priority token is skipped with a per-source status, never blocking. `whoami` goes through the **`net.fetch` egress chokepoint** (host `huggingface.co`, CONNECTOR-profile policy via `egress_policy_for`) — never hand-rolled aiohttp — and results are cached (~10 min TTL) so list renders don't hammer HF.

Settings surface (Settings → Models): per-source rows with masked preview (`hf_…3jw`), whoami username + check on the valid source, "Active" badge, and a set/clear field writing source 1. `GET /api/models/hf-token/status` returns per-source `{present, valid, username?, masked}` — values never leave the server unmasked. Provider migration: `diarization-pyannote`'s `_hf_token()` (and any future HF-touching provider) delegates to the cascade; its app-config `hf_token` field is honored one release as a fourth read-only source, then migrated into the credential store on first load.

---

## 6. Per-Provider Real-Inference Selftest + Health

The contract campaign proved the RUN column of the matrix manually; these endpoints automate it.

- `GET /api/models/local/{provider}/health` — cheap: `is_available()` for in-process, sidecar spawn+ping for sidecars. **Never 500s** — exceptions become `{ok: false, message}`; messages mask tokens; returns `{provider, ok, message, latency_ms}`. To carry the *why*, the ABC gains optional `availability_detail() -> tuple[bool, str]` (default wraps the existing `is_available() -> bool` with a generic message — the bool contract is untouched, and `is_local_model_provider` duck-typing is unaffected). The `(True, "ready — <advice>")` convention surfaces upgrade hints.
- `POST /api/models/local/{provider}/selftest {model?}` — a tiny **real inference** per capability: stt transcribes a bundled 1-second fixture wav; tts synthesizes a fixed phrase; embedding encodes one sentence and checks the vector dim against the bound store; diarization runs the fixture through the pipeline (this exact test would have caught the pyannote `itertracks`/`DiarizeOutput` 3.x→4.x break at download time instead of at first user ingestion); chat (ollama) runs an 8-token completion. Bounded timeout (90s default, config), **serialized behind a `single_flight` lock** (`concurrency.py` — the existing fcntl seam, not a new mutex), user-click only (never cron — a selftest can page a model into RAM). Returns `{ok, duration_ms, detail}` per capability; failures carry the typed reason vocabulary.
- ModelsPanel: a "Test" button per provider/model row rendering the result inline; sidecar providers run the selftest *in the child*, doubling as an isolation smoke test.

---

## 7. Loaded-Models / Memory-Pressure Widget

- `GET /api/models/loaded` enumerates every resident occupant: in-process provider instances (detected via each provider's declared `_MODEL_ATTRS`-style non-None model attributes — the ABC gains optional `loaded_models() -> list[dict]` with a reflective default), sidecar children (child-reported RSS from §3.1 stat frames), and warm singletons; plus a system RAM pressure snapshot (`vm_stat`-derived on macOS). Each row: `{provider, model, kind: in-process|sidecar, rss_mb?, is_active}` — `is_active` attribution matters because a model can stay resident after the binding moved elsewhere (omnivoice's exact lesson).
- `POST /api/models/unload {provider}` — the ABC gains optional idempotent `unload()` (clear model attrs / terminate+respawn-cold the sidecar). Also gains optional `ensure_ready()` separating load budget from inference budget — a warming provider reports "loading", not "hung".
- FE: a compact loaded-models section in Settings → Models (rows + Unload buttons + pressure bar), and — reusing the existing Dashboard bento surface — an optional "On this machine" tile answering "what is occupying my RAM right now". Pressure warning threshold configurable (§9 config).

---

## 8. Subscription-Credential Model Providers (am.d)

Users already authenticate Claude Code / Codex / Copilot-class agent CLIs on this machine (`apps/claude-code-agent`, `apps/codex-agent`, `apps/kiro-cli-agent` exist as agent-provider apps). A *model* provider app should be able to ride that auth with **no separate API key**:

- `BrandedProviderSpec` (`sdk/provider_helpers.py`) gains `credential_source: str | None` — when set (e.g. `"claude-code"`), `_factory`'s credential order becomes: entry.credential → `options.api_key` → **subscription-source resolver** → `spec.api_key_env` → anon placeholder. The resolver is a small per-source adapter reading the CLI's own credential store (OAuth token file / keychain entry) read-only, refreshing via the CLI where the format demands it.
- Failure is soft and typed: source not logged in → the provider's availability hook (`providers/loader.py` module-level `availability()`) reports `(False, "sign in with `claude login` first")` so the extensions list greys it out with the reason.
- Scope discipline: this is a *credential resolution* feature only — sessions, models, and catalogs flow through the normal branded-app path; no agent runtime is involved. Ships with ONE reference app; others follow the pattern.

---

## 9. Provider & Config Plug-in Map

Where each piece plugs into the pluggable-provider architecture (recon: providers.md) — nothing invents a parallel extension path:

- **No new provider type.** Everything extends the `model` type: `ProviderConfig.execution` (sidecar flag) is a manifest field on the existing type; `PROVIDER_TYPES` and the `_TypeHandler` set are untouched (the `test_manifest_types_match_handlers` guard stays green by construction).
- **No new action providers.** This plan adds nothing to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) — stated explicitly so nobody "helpfully" wires a download action into hooks; downloads stay user-initiated HTTP.
- **Registration is unchanged:** apps deliver providers; `ModelTypeHandler.register()` duck-types `is_local_model_provider` and keys `local_models/registry` on the APP name; sidecar proxies subclass the same ABCs so registration, the alias tuples, and active-model pruning are untouched. Bundled (app-registered) providers — including sidecar proxies — survive `refresh_providers()`; only config-derived remote adapters rebuild (the two-population invariant is a regression test in this plan).
- **ABC changes are additive** on `local_models/provider.py` and re-exported via `sdk.local_model` (SDK_VERSION stays 1.0-compatible: new optional methods with defaults). `catalog.json` is app-owned content propagated by the normal app update flow (`POST /api/apps/{name}/update`).
- **Download/HTTP surface** stays in `dashboard/model_downloads.py` + `dashboard/handlers/model_downloads.py` (routes added: sidecar install status, cleanup-candidates, health, selftest, loaded, unload, hf-token status). Ollama's management keeps flowing through the `ModelCatalog`/`ModelManager` axis (`llm/catalog.py:328` — `pull_model → PullProgress`), adapted by `_ManagerBackedLocalProvider`; its `PullProgress` frames map onto the §4.1 canonical record inside the adapter.
- **New config = a `LocalModelsConfig` section**, wired through the FOUR points (recon: persistence-security gotcha #1): (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (omission = silently dropped), (c) `to_dict()` (new top-level section must be added), (d) `_EDITABLE_CONFIG` PATCH allowlist + FE for the runtime-editable knobs. Fields: `selftest_timeout_s`, `pressure_warn_pct`, `download_parallelism`, `whoami_ttl_s`, `sidecar_restart_max`.
- **Egress:** `whoami`, direct-URL weight fetches the runner drives, and any endpoint probing go through `net.fetch` with `egress_policy_for(CONNECTOR)`; HF-hub library fetches are provider-internal (unchanged), but their *failures* are classified at the runner boundary (§4.3).
- **Secrets:** tokens live in the credential store (`.env`, 0600) — never in `catalog.json`, job records, SSE frames, or logs; all status payloads mask (`hf_…3jw`); SEL (`sel.py`) receives audit events for token set/clear and sidecar venv installs, as it does for skill installs today.
- **Memory vs Knowledge (user directive):** this plan writes to neither. Model cards/tokens/jobs are provider+config state under `~/.personalclaw/apps/*` and `config_dir()`. Any future "audio QA over recordings" capability that persists user content targets the knowledge store and belongs to a knowledge/voice plan, not this one; learning about model choices belongs to LEARNING-FLYWHEEL.

---

## 10. What We Deliberately Do NOT Build

- **No fits-machine / hardware-tier gating.** Deliberately removed in the contract campaign; stays removed. If fit estimation ever returns it lives in *search results* as an honest advisory string, never on the binding path.
- **No auto-benchmarking / auto-persisted backend winners** (ULS's flagship) — measure-and-remember is LEARNING-FLYWHEEL territory; this plan only makes the selftest primitive it would need.
- **No model recommendation engine, no fleet/registry service, no telemetry.**
- **No new inference use-cases** (`audio_qa`, joint-transcriber ABC) — the §2.1 matrix makes them *declarable*; adding the use-case axis is a separate plan.
- **No YAML.** Model cards are JSON like every other PClaw store.
- **No blanket sidecar migration.** In-process stays the default; sidecars are earned by a crash history.

---

## 11. Disposition Table

| Surface | Verdict | Detail |
|---|---|---|
| `local_models/provider.py` ABC | **EXTENDED, additive** | `matrix`, runtime-contract fields, `availability_detail()`, `download_model_ex()`, `unload()`, `ensure_ready()`, `loaded_models()`, `_models_from_catalog()` — all optional with defaults; `is_available()/download_model() -> bool` contracts untouched |
| `local_models/registry.py` | **KEPT verbatim** | APP-name keying, `_DOWNLOADABLE_CAPS` gating, `_ManagerBackedLocalProvider`, two-population split — all preserved; regression test added for the refresh-survival invariant |
| `dashboard/model_downloads.py` job runner | **UPGRADED in place** | canonical record shape, `.part`+cleanup, error classification, reattach-first FE contract; dir-growth poller kept as fallback; SSE hub kept |
| `dashboard/handlers/model_downloads.py` | **EXTENDED** | +sidecar install status, cleanup-candidates, health, selftest, loaded/unload, hf-token status routes |
| Per-provider hardcoded model lists | **REPLACED by `catalog.json`** | fixed-catalog five; ollama's dynamic `ModelManager` catalog untouched |
| Per-provider `_hf_token()` lookups | **REPLACED by the cascade** | pyannote's config field honored one release, then migrated to the credential store |
| `web/.../ModelsPanel.tsx` (`capableModels` et al.) | **EXTENDED** | license/deprecation/integrity chips, matrix chips, gated pre-warn, Test buttons, loaded-models section, cleanup affordance, token settings — all FE (recon: `capableModels` is frontend-only) |
| Env-var segfault band-aids (`TOKENIZERS_PARALLELISM` etc.) | **DEMOTED to sidecar-internal defaults** | once the ST sidecar ships; gateway-wide settings removed after one release of coexistence |

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Sidecar stdio protocol becomes a second app-backend platform | Scope fence: newline-JSON, 5 verbs (call/stat/ping/load/unload), no HTTP, no routing — anything richer is an app backend |
| Proxy latency on hot paths (embedding under re-index) | Batch calls in the protocol; sidecar is opt-in per provider; embedding keeps single-process internals regardless |
| Registry drift (wrapper loses APP-name key / duck-type match) | Unit test: register a sidecar proxy through `ModelTypeHandler`, assert registry key + `is_local_model_provider` + refresh survival |
| Catalog file drift vs installed copies | `catalog.json` rides the existing app-update propagation; manifest-vs-UI audit habit (`reference_app_manifest_vs_ui_gap`) extended to catalog-vs-UI |
| Truncation detector false-positives | 60% threshold + `config_only` escape + never auto-delete (Repair is user-initiated) |
| whoami calls leaking tokens to logs/SEL | mask-everywhere rule; status endpoint returns masked previews only; egress audit logs host, never headers |
| Selftest paging models into RAM at bad times | user-click only, `single_flight`-serialized, bounded timeout, never scheduled |
| Destructive tests hitting real model dirs (it happened) | suite-level fixture asserting `_models_dir`/cache roots point into `tmp_path` for any fs-touching test |
| Subscription-credential formats churn upstream | per-source adapters are tiny + fail-soft to the availability hook; no refresh logic beyond what the CLI itself offers |

---

## Implementation Effort

**~5 sessions, Wave 0, v2-independent:**

- **Session 1 — Catalog & contract:** `CapabilityMatrix` + runtime-contract fields + `catalog.json` loader + truncation detector + shared multi-layout probe (`layouts.py`) + `/api/models/available` payload + ModelsPanel chips. Migrate the fixed-catalog five to model cards.
- **Session 2 — Download manager v2:** canonical job record, poll-first/reattach FE contract, `.part` + cleanup candidates, gated-repo/network/disk error classification + FE translation table, cancel→cleanup wiring, ollama `PullProgress` mapping.
- **Session 3 — Tokens + selftest:** three-source cascade with whoami (via `net.fetch`) + settings surface + pyannote migration; health + selftest endpoints with per-capability fixtures + Test buttons; SEL audit events.
- **Session 4 — Sidecars + observability:** `sidecar.py` runner (venv, stdio protocol, generation counters, watchdog), sentence-transformers sidecar variant first, resumable install jobs on the job registry, loaded-models/unload/pressure endpoints + FE widget.
- **Session 5 — Subscription credentials + hardening:** `credential_source` resolver + one reference app; per-model context-budget helper consumed by the reasoning-axis path; two-population + registry-drift + destructive-test-isolation regression tests; as-a-user validation sweep of the full download/delete/bind/RUN matrix through the new surfaces (the contract-campaign method, now partially automated by selftest).

## Success Criteria

1. Kill the sentence-transformers sidecar child mid-encode: the gateway survives, the caller gets a typed `SidecarCrashed`, the watchdog respawns, and search recovers without a restart — the loky-segfault class is structurally closed.
2. Start a 1+ GB model download, reload the page, switch tabs: the progress bar reattaches to the server-side job every time; cancel leaves a visible cleanup candidate; "Reclaim" deletes it.
3. Attempting a gated pyannote download with no valid token yields the exact two-step instruction (accept license / add token) with working deep links — and never auto-retries.
4. With an invalid token in env and a valid one in the HF CLI file, the cascade skips to the CLI token, whoami shows the username, and the badge marks it Active; no unmasked token appears in any payload, log, or SEL line.
5. Selftest on every one of the 6 providers runs a real inference and returns honestly — and a deliberately broken runtime contract (pyannote-4-style API break) fails the selftest with a typed reason instead of passing on file presence.
6. Dropping a new entry into `faster-whisper/catalog.json` (no code change) makes the model appear, download, bind, and RUN; flipping it to `deprecated` shows the chip without breaking an existing binding; a hand-truncated weights dir shows `truncated` + Repair.
7. A non-commercial-licensed model shows its warning chip at bind time in the exact picker `capableModels()` renders.
8. The loaded-models widget lists every resident occupant with attribution; Unload actually frees RSS (verified by the pressure snapshot); a model resident after a binding switch shows `is_active: false`.
9. `refresh_providers()` after a config change leaves every bundled/sidecar provider registered (the two-population regression stays closed, now under test).
10. The full test suite runs with a fixture guaranteeing no fs-touching test can reach a real model dir — the bound-model-deletion incident is unreproducible by construction.
