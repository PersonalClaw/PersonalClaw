# LOCAL-MODEL-MANAGER-V2

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/LMMV.md`](../atomic/LMMV.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Local Model Manager v2 — Sidecar Isolation, Download UX, Token Cascade, Catalog Contract

**Status:** IN PROGRESS — one slice landed 2026-07-30 (§4.4 the shared multi-layout probe
`local_models/layouts.py` + §4.2 cleanup-candidate detection, PR #120; DEVIATION recorded in the
Execution log).
🔴 **The landed slice is HALF-INERT** (audit 2026-08-04): only `is_downloaded` is wired
(`dashboard/model_downloads.py:196`). `delete_all_layouts`, `cleanup_candidates`,
`reclaimable_bytes`, `downloaded_layouts`, `candidate_paths` and `hf_repo_dirname` have **zero
production callers** — the delete route still drives `provider.delete_model`, so the "disk never
frees" bug the log claims to fix is still live; §4.2's `cleanup-candidates`/`cleanup` endpoints and
the "Reclaim N GB" affordance were never built, leaving Success Criterion 2 unmet; and
`resilience/doctor.py:419` still calls the module "unbuilt".
Sidecar isolation, the capability matrix, the catalog contract, the HF-token cascade, selftests and
the memory-pressure widget are unstarted. Status corrected 2026-08-04.
(rev 2 — research-integrated 2026-07-12)

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

## Execution log

- 2026-08-21 — **PARTIAL (LMMV-7 / Session 5b, second pass). Both hardening rails made
  DERIVED instead of hand-listed; the six-provider matrix DRIVEN (the 2026-08-18 entry's
  "no local-model provider exists" was an unwired-apps-dir artifact, corrected below).
  Three clauses remain unmet, each with evidence.**

  **Clause audit first — most of this atom was already built.** Verified against
  `origin/main` (`addbc332`, #1628) before writing anything: the §2.2 budget helper
  (`local_models/budgets.py` — `catalog_window`/`model_budget`/`output_budget`) is
  consumed by **every** resolution branch of `one_shot_completion` through one seam,
  `_entry_kw` (`llm_helpers.py:456`), which puts the derived number in `max_tokens` at
  `:498`. The reasoning axis specifically is covered: `resolved_uc` is either the caller's
  real axis (`:443`) or collapses to `"reasoning"` (`:447`), and both reach the helper via
  the pin (`:535`), chain-advance (`:556`) and plain (`:588`) call sites. The named live
  callers carry no hardcoded cap to supersede — `nl_to_cron.py:54-57` and
  `inbox_service.py:375/395/416/438/459` pass only `use_case="background"` and inherit the
  derivation. `workflows/compaction.py` is still byte-identical: **no compaction logic
  rewritten**, the atom's explicit boundary.

  **What was actually missing: both regression rails were ONE-SIDED inventories.** Each
  proved every *listed* item was covered and said nothing about an item nobody listed —
  which is the only case a rail like this exists to catch.

  * **SC-10 (the model-root rail).** `test_every_guarded_entry_point_is_wrapped`
    parametrizes over `GUARDED_FUNCTIONS`, and its own docstring claimed it caught
    *"adding a new cache-root function to `layouts` and forgetting to list it"*. It cannot:
    an unlisted function is never iterated. Added
    `test_the_guarded_set_covers_every_cache_root_entry_point`, which **derives** the
    population by AST-parsing `layouts.py` for public functions whose first parameter is
    `cache_root` and reconciles it against `GUARDED_FUNCTIONS` in both directions. Parsed
    from SOURCE deliberately, not via `inspect.signature`: the autouse fixture has already
    replaced each guarded attribute with `_guarded(cache_root, ...)`, so a signature-based
    derivation would report `cache_root` first for anything already wrapped and confirm
    itself. **Falsified** by appending a `sweep_stale_layouts(cache_root, model)` to
    `layouts.py`: the new rail reds naming `['sweep_stale_layouts']` — and on the *same
    run* the pre-existing wrapping test **passed**, which is the hole measured directly.
  * **SC-9 (the two-population invariant).** The structural test iterated a hardcoded
    four-entry map, so a fifth registry growing a `refresh_providers` would inherit nothing.
    Now `_modules_defining_refresh_providers()` discovers the population from the package
    (text prefilter, then AST confirmation so a docstring mention or call site is not
    mistaken for a definition) and reconciles both directions. **Falsified** by adding a
    `refresh_providers()` that does `_providers.clear()` to `diarization/registry.py`: reds
    naming `['personalclaw.diarization.registry']`. Census: exactly four modules define
    `refresh_providers` today (stt, tts, image_gen, video_gen), and all seven public
    `cache_root` functions in `layouts.py` are guarded — both rails are complete *now*, and
    now stay complete.

  **The three locks re-falsified on this base, not assumed.** (a) restoring the untargeted
  `_providers.clear()` in `stt/registry.refresh_providers` → **2 reds** (the two-population
  case + the sidecar-survival gate); (b) `name=ext.name` → `name=provider.name` in
  `ModelTypeHandler.register` → **2 reds**; dropping `"stt"` from `_DOWNLOADABLE_CAPS` →
  **3 reds** (all three drift gates). (c) driven with a deliberately-offending probe file of
  three shapes, none using `pytest.raises` — a real HF-cache delete, a `~/.personalclaw`
  probe, **and an import-bound alias captured at module import before the fixture runs**.
  All three were caught. **DISCOVERY — why the import-bound shape is caught, and how
  fragile that is:** `delete_all_layouts` (`layouts.py:195`) resolves `candidate_paths` as a
  **module global**, so the patched wrapper intercepts it transitively *before* `shutil.rmtree`
  — the rail's depth is a consequence of that one call being a module-global lookup, not of
  the fixture's reach. An entry point that did its own fs work without calling another
  guarded function could still be reached through an import-bound alias.

  **The matrix — DRIVEN, and the previous entry's blocker was wrong.** The 2026-08-18 note
  recorded `{"providers": []}` and concluded *"no local-model provider exists to drive"*.
  The cause was not absence: the six apps live in the sibling `PersonalClawApps` clone and
  the gateway had no source pointing at it. With
  `PERSONALCLAW_FIRST_PARTY_APPS_DIR=<workspace>/PersonalClawApps`, `/api/apps/catalog`
  returns **45 local apps** and all six installed and enabled through the real endpoints
  (`POST /api/apps` then `POST /api/apps/{name}/enable`). Isolated home
  `<worktree>/.dev-home`, port 10778, `AUTH_MODE=none`; attribution asserted by the gateway
  writing `<worktree>/.dev-home/gateway.log` itself, and the port confirmed owned by the PID
  we started (a foreign gateway was live on 10698 throughout and was never touched).

  **DISCOVERY — an isolated `PERSONALCLAW_HOME` does NOT isolate model weights.**
  In the sibling `PersonalClawApps` repo, the `faster-whisper` provider module (line 45) and
  the `diarization-onnx` one (line 26) root their caches at `XDG_CACHE_HOME`/`~/.cache`
  (HF hub / `personalclaw/diarization-onnx`); only `piper-tts` and `sentence-transformers`
  honor `PERSONALCLAW_HOME`. The real `~/.cache/huggingface`
  holds genuinely downloaded weights, so a download or delete driven from a dev gateway
  targets them. The sweep therefore also set `XDG_CACHE_HOME`/`HF_HOME` inside the dev home,
  and a before/after `ls` of the real hub dir was kept as a control: **byte-identical across
  the whole sweep.** This is the production-side analogue of the incident SC-10 closed in
  tests — the test rail is structural, the dev-runtime one is a convention nobody sets.

  **Matrix results, per cell.** download/bind/delete were driven through
  `POST /api/models/downloads`, `PUT /api/models/active/{use_case}` and
  `DELETE /api/models/local/{provider}/{model}`; 355 MB of real weights landed in the dev
  home and every `downloaded` flag flipped true, then back to false after the deletes
  (`models/` 235M → 4.0K, `xdg-cache/` 120M → 136K, no file >1 MB left).

  | provider | download | bind | RUN | delete |
  |---|---|---|---|---|
  | faster-whisper (stt) | ✅ 75 MB | ✅ | ❌ no surface | ✅ |
  | piper-tts (tts) | ✅ | ✅ | ❌ no surface | ✅ |
  | sentence-transformers (embedding) | ✅ | ✅ | ✅ real inference | ✅ |
  | diarization-onnx (diarization) | ✅ 47 MB | ✅ | ❌ no surface | ✅ |
  | diarization-pyannote (diarization) | ❌ credential-gated | — | — | — |
  | ollama (chat+embedding) | ❌ not exercised | ✅ | ✅ real completion | ❌ not exercised |

  The embedding RUN leg is the one cell proven end to end: a reindex against an **empty**
  store first returned `done` with `total: 0` — a green that measures nothing — so a
  knowledge item was created and the reindex re-driven, returning `done=1, knowledge=1`
  through the bound local `all-MiniLM-L6-v2`. The ollama cell's RUN is a real chat
  completion, which also validates §2.2's consumer as a user: it goes through
  `one_shot_completion(use_case="background")` with `ollama:gemma4:12b` bound, i.e. the
  derived `max_tokens` reached a real local model and the call returned.

  **UNMET clause 1 — `diarization-pyannote` is credential-gated and cannot be exercised
  here.** Recorded rather than skipped: the model surfaces with `gated: true`, the download
  starts (`dl-5`) and ends `state: error`. **DISCOVERY (Success Criterion 3, not this
  atom's):** the failure is *unclassified* — `reason: ""` and a generic
  `"Failed to download model 'pyannote/speaker-diarization-3.1' from
  diarization-pyannote"`, where SC-3 requires the exact two-step accept-license / add-token
  instruction with deep links. The gate works; its legibility does not.

  **UNMET clause 2 — the ollama cell's download/delete legs were deliberately not driven.**
  Ollama stores models in the daemon's own `~/.ollama`, which `PERSONALCLAW_HOME` cannot
  redirect (it would need `OLLAMA_MODELS` on the already-running daemon). A delete there
  would remove a model the owner really pulled — the incident this plan exists to prevent —
  so bind + RUN were driven against the already-present `gemma4:12b` and the two write legs
  were left alone. Also worth noting for the card itself: the sixth provider only appears
  once a **config.json** ollama entry exists (`register_config_model_managers()` reads
  `registry.list_entries()`); installing and enabling the app is not sufficient. Creating one
  via `POST /api/model-providers` took the surface from 5 providers to **6**.

  **UNMET clause 3 / DISCOVERY — `POST /api/model-providers/{name}/selftest` is
  provider-blind, so Success Criterion 5 is UNMET.** `api_provider_selftest`
  (`dashboard/handlers/doctor.py:214`) reads `{name}` at `:224` and hands it to
  `_run_selftest(name)` at `:225`, which **never reads the parameter** (`:229` onward): it
  probes whatever is bound to the chat/embedding use cases and labels the answer with the
  caller's provider name. Measured: `faster-whisper`, `piper-tts` and `diarization-onnx` all
  returned `chat: ok, embedding: 384 dims` *after their weights had been deleted*, and
  `.../this-provider-does-not-exist-zzz/selftest` returns `ok: true` for both capabilities.
  SC-5 asks for a real inference per provider returning **honestly**; this returns a false
  green for every name including nonexistent ones. Left unfixed on purpose — the selftest is
  Session 3's deliverable (§7), and rewriting it here is scope this atom does not own.

  **Not done, and why:** the RUN leg for stt / tts / diarization has no user-reachable
  surface in this build — there is no voice/transcribe/synthesize handler in
  `dashboard/handlers/` and no `/api/models/local/{provider}/selftest` route, only `search`
  and the delete. That is a more precise statement of the earlier entry's "the RUN leg needs
  real weights": the weights were downloaded and bound here, and the gap is the missing
  surface, not the missing model.

  Gate: `make lint` clean. 44 passed across `test_local_model_root_guard`,
  `test_local_model_refresh_invariants`, `test_local_model_budgets`; full `make test` green.
  No config field added, so the five-point round-trip contract does not apply. The real HF
  cache was verified untouched by before/after control, and the falsification sweep
  (`FALSIFICATION|if False and|# PROBE|MUTANT`) is back to its benign baseline of 13.

- 2026-08-18 — **PARTIAL (LMMV-7 / Session 5b). The per-model context-budget helper +
  all three hardening regressions are DONE; the full-matrix as-a-user sweep is
  NOT EXERCISED (reason below).**

  **§2.2 the budget helper, and where the hardcoded constants actually were.** New
  `local_models/budgets.py` (`ContextBudget`, `catalog_window`, `model_budget`,
  `output_budget`) derives a window/output/input budget from the card's `context_tokens` /
  `output_tokens`, falling back to `model_windows.model_context_window` and then to one
  named `DEFAULT_OUTPUT_TOKENS = 4096`. The constants it supersedes were **not** in
  `one_shot_completion` — that path carried **no budget at all**, which is why a local
  model silently inherited `model_windows.DEFAULT_CONTEXT_WINDOW = 200_000` (its id is not
  in `model_tokens.json`, so the table's absent-model default answered) and the adapters'
  own output cap (`llm/anthropic.py:367` `max_tokens: int = 4096`; the anthropic model
  app's `options.pop("max_tokens", 4096)`). All three resolution branches of
  `one_shot_completion` (pin / chain-advance / plain) now ask the helper for the model they
  are about to run and ride the answer as a `max_tokens` build kwarg beside the existing
  `temperature` one. Because the bridge merges build kwargs with `setdefault`, an
  operator's configured `max_tokens` still wins — the derivation supplies a *default*, it
  does not override a choice.

  **DISCOVERY — the derivation had to be async or it would have shipped inert.**
  `LocalModelProvider.list_models` is a coroutine. A synchronous helper would have received
  a coroutine object, found no model in it, fallen through to the 200k table and returned a
  plausible number for every input — the exact declared-but-inert shape, and one no
  assertion on the helper's return value would have caught. `catalog_window` /
  `model_budget` / `output_budget` are `async` and awaited at the call site.

  **Anti-inertness is asserted at the CONSUMER, not on the helper.** A parametrized test
  patches the bridge seam and asserts the `max_tokens` that *reaches the resolution call*
  moves when the catalog moves: `(8192, 1024) → 1024`, `(4096, 0) → 2048`,
  `(0, 0) → 4096`. Falsified by making the consumer return the old constant — 3 reds.

  **`context_tokens = 0` is a normal card and has its own test, both directions.** A card
  that declares nothing is the common case, so `0` means *unknown*, never a window of zero:
  it falls back to the window table with `source="window-table"` and the named 4096 floor,
  and the declared case is asserted to *differ* from it. A window of `1` still yields
  strictly positive `output_tokens`/`input_tokens`; a declared output larger than the window
  is clamped to half of it (a 4k model handed a 4096-token cap would leave no room for the
  prompt). Nothing in the module divides by `context_tokens`.

  **No compaction logic was rewritten** — the atom's explicit boundary, honored literally.
  `workflows/compaction.py` is byte-identical: `prompt_char_budget`, `CHARS_PER_TOKEN`,
  `COMPACT_AT_FRACTION` and the head/tail protection are untouched. This plan's own §2.2
  wording is the reason ("it makes the number available"), so the helper hands over a number
  and decides nothing about what to drop.

  **Success Criterion 9 — the two-population invariant, with before/after counts.**
  `tests/test_local_model_refresh_invariants.py` drives all four registries that expose
  `refresh_providers()` (stt, tts, image_gen, video_gen). Each case asserts three things,
  not a smoke check: the app-contributed provider is **the same object** afterwards, the
  transient one is **gone** (the vacuity guard — a `refresh_providers` that had become a
  no-op would otherwise pass), and the dict shrinks by exactly the transient population's
  size. A fifth test pins the invariant structurally: every refreshing registry must keep a
  named transient-population marker (`_remote_names` / `_scanner_names` /
  `_auto_registered`), so an untargeted `_providers.clear()` cannot return quietly.

  **Registry drift.** A duck-typed sidecar proxy whose internal `.name` (`faster_whisper`)
  differs from its app name (`faster-whisper`) is driven through
  `ModelTypeHandler.register` and asserted to (a) satisfy `is_local_model_provider` without
  subclassing — and to be *refused* for a hosted-only capability set, so the second gate is
  a real gate; (b) land in the local-model registry under the **APP** name, with
  `get_provider("faster_whisper") is None`; (c) survive the stt registry's
  `refresh_providers()` in **both** registries. Falsified by restoring the original
  untargeted `_providers.clear()` in `stt/registry.refresh_providers` — 2 reds, including
  the drift test.

  **Success Criterion 10 — the deletion rail, structural not conventional.** A suite-level
  autouse fixture (`conftest._forbid_real_model_roots`) wraps all seven `layouts.py`
  cache-root entry points, including the single deletion sweep `delete_all_layouts`, and
  refuses a real model root. Detection lives in `tests/real_model_root_guard.py` for the
  same reason `real_home_guard` does — a rail that only ever runs where it should stay
  silent is indistinguishable from one that never fires — and
  `tests/test_local_model_root_guard.py` drives it both ways: every named real root and a
  `~/`-spelled string are refused, a `tmp_path` root passes through to the real
  implementation with real answers, the forbidden set is asserted non-empty and asserted to
  contain the two roots the incident touched, and each guarded entry point is asserted to
  actually be wrapped. **DEVIATION (small, deliberate):** the rail forbids the *named* real
  roots (`~/.personalclaw`, `~/.cache/huggingface|torch|whisper|piper`, `~/.ollama`, the
  macOS spellings) and the bare home, rather than all of `$HOME`. A developer's checkout
  normally lives under `$HOME`, so a blanket home-rejection fires on an ordinary relative
  path and gets switched off — which is how the convention that was already in force failed
  the first time.

  **NOT EXERCISED — the full download/delete/bind/RUN matrix across all 6 providers.**
  Driven as far as it goes: the gateway was started on an isolated dev home (port 10531,
  `PERSONALCLAW_AUTH_MODE=none`), `/api/models/available` and `/api/models/active` both
  answered 200 with well-formed envelopes, and `available` returned `{"providers": []}` —
  **no local-model provider exists to drive**, because every one of the six is an APP and
  `git ls-files apps/` is 0 in this repo (the same constraint §2's catalog work recorded).
  The RUN leg additionally needs real weights, and downloading them was out of bounds for
  this session. What *was* driven live, out of pytest, in a real process: the budget
  reaching the bridge for a 4k card (2048), a 32k card declaring 2048 (2048), an
  undeclared card (4096) and a hosted ref (4096) — i.e. the consumer half of §2.2 is
  validated as a user of the path even though the six-provider matrix is not. The matrix
  sweep belongs with the per-app migration in PersonalClawApps.

  Gate: `make lint` clean (black/isort/flake8/mypy, 906 files). 251 passed across
  `test_local_model_budgets`, `test_local_model_refresh_invariants`,
  `test_local_model_root_guard`, `test_local_model_catalog`, `test_local_model_layouts`,
  `test_local_model_sidecar`, `test_local_model_discovery_async`, `test_llm_helpers`,
  `test_config_roundtrip`, `test_config_baseline`, `test_agent_reference`,
  `test_inert_surface_baseline`. No config field was added, so the five-point round-trip
  contract does not apply. Zero writes under the real home during the session.

- 2026-07-30 — **PARTIAL (§4.4 the shared layout probe + §4.2 cleanup candidates).
  DEVIATION: scoped to one slice of a large plan.**

  **DEVIATION — why §4.4 and not the plan's front half.** This plan is ~12 sections spanning a
  capability matrix, sidecar venv isolation, resumable install jobs, a download manager rewrite,
  an HF token cascade, selftests and a memory widget. §4.4 was taken because it is (a) the one
  slice with a **consumer already waiting** — `resilience/doctor.py:418` carries a scope note
  reading *"the on-disk HF `models--` layout probe belongs to LOCAL-MODEL-MANAGER-V2
  (`local_models/layouts.py`, unbuilt)"* — and (b) a **bug-class fix rather than a feature**, so
  it is complete in itself and cannot be half-done. Sidecar isolation and the token cascade
  remain unstarted. Recorded per the sprint's decide-and-continue rule.

  **The bug class.** A downloaded model lands on disk in several shapes and every consumer was
  guessing at one: the HF hub snapshot (`models--{org}--{name}/snapshots/{rev}/`, where the model
  id **never appears literally**), a provider-native directory from its own `save()`, or a direct
  file with any of several extensions. Guessing wrong gives either a `downloaded` flag reading
  **False for a model sitting right there** — the user re-downloads gigabytes — or a `delete` that
  reports success while leaving weights behind, so **the disk never frees**. Both were observed;
  hence one shared probe over EVERY layout.

  **Three judgment calls worth naming.**
  1. **A partial is NOT downloaded.** `blobs/*.incomplete` or a bare `.part` means an interrupted
     fetch, and reporting it as present yields a model that fails at load with no explanation —
     worse than reporting nothing. A zero-byte file is likewise a placeholder, not a model.
  2. **Deleting is greedy.** `delete_all_layouts` removes every layout it finds (and the partials)
     rather than stopping at the first hit: a model fetched twice by different paths leaves two
     copies, and freeing one is the disk-never-frees bug in a different costume. Verified live —
     two copies, both deleted.
  3. **The runner's second opinion is ASYMMETRIC.** The provider's `downloaded` flag is
     authoritative when it says **yes** (only it knows backend-specific layouts); the probe is
     consulted only when it says **no**. A false NO costs the user gigabytes and is the common
     failure; a false YES would be worse, so the probe only ever ADDS a yes, and only on
     finished non-empty bytes.

  **Two self-inflicted test bugs, both instructive.** My first `.part` test failed for a real
  reason: partial suffixes were absent from `candidate_paths`, so `delete_all_layouts` could not
  sweep a cancelled download's leftovers — the invisible-leftover half of the same bug. Fixed in
  the code. Then the destructive-test guard the plan asks for **matched its own source twice** (a
  literal scan tripping over the very path names it warns about, then over the fs verbs it scans
  for). Replaced with a structural check — every fs-touching test in the module must take
  `tmp_path` — which is the property that actually protects the developer, since a test hard-coding
  a path would not have the fixture.

  **Validated as a user** against a REAL hf-hub cache shape (isolated tmp dir, never the models
  root): a symlinked `snapshots/{rev}/model.safetensors → blobs/{sha}` was detected as downloaded;
  an `.incomplete` blob beside it was surfaced as a cleanup candidate with its size (the "Reclaim
  N GB" input) and did **not** count as downloaded; a second provider-native copy made
  `downloaded_layouts` report **2**; and `delete_all_layouts` removed both, after which
  `is_downloaded` was False.

  **Gates:** `make lint` clean (mypy 557 files) · `make test` **9541 passed, 0 failed**.
  Tests: `tests/test_local_model_layouts.py`, 47 cases.

- 2026-08-05 — **DONE (LMMV-2 / Session 1 — Catalog & contract). The core mechanism, proven
  with a fixture catalog.json.**

  Landed §2.1/§2.2/§2.3 as an additive contract on `local_models/provider.py`: a
  `CapabilityMatrix` dataclass and eleven optional `LocalModel` fields
  (`matrix`, `runtime`, `runtime_contract`, `license`, `non_commercial`, `context_tokens`,
  `output_tokens`, `io_mime`, `status`, `integrity`, `config_only`), all appended so every existing
  keyword constructor and the old `-> bool`-shaped providers stay valid. `to_dict()` emits them, so
  they ride `GET /api/models/available` with no handler change (the registry's `to_local_model`
  returns a `LocalModel` as-is, so the fields pass through untouched). Added
  `LocalModelProvider._models_from_catalog(catalog_path, *, cache_root, active_downloads)` — reads
  the app-owned `catalog.json`, fail-soft (missing/malformed → `[]` with a warning; one bad card
  skipped, never blanks the list), maps each card to a `LocalModel`, filters `platforms` against a
  new `host_platform_token()` helper (`darwin-arm64`/`linux-x86_64`, arch aliases normalized;
  empty/absent = all hosts), computes `downloaded` via the §4.4 probe, and flags
  `integrity="truncated"` when a finished non-`config_only` model's on-disk bytes fall below 60% of
  its declared `size_mb` (Part 3's `layouts.on_disk_bytes` byte-sum helper) with no in-flight fetch.
  FE: `ModelsPanel` renders deprecated/sunset/non-commercial/truncated chips + a Repair button
  (re-uses the existing `startModelDownload` action), via a pure `modelChips()` mapping;
  `AvailableModel` + `CapabilityMatrix` typed in `api.ts`.

  **DEVIATION — per-app catalog.json migration is a PersonalClawApps-repo follow-up (cross-repo).**
  `git ls-files apps/` = 0: NO provider apps are git-tracked in this core repo (faster-whisper,
  sentence-transformers, piper-tts, diarization-onnx, diarization-pyannote all live in
  PersonalClawApps + seeded home copies). Editing a seeded/installed copy is not a durable change,
  so the fixed-five migration to `catalog.json` (and rewiring each `list_models()` to
  `_models_from_catalog`) is deferred to an apps-repo change. The core deliverable here is the full,
  reusable mechanism + contract, proven end-to-end by `tests/test_local_model_catalog.py` (37 cases)
  driving `_models_from_catalog` against a fixture catalog exercising every Success-Criteria-6/7
  behavior: an active model, a deprecated model (chip, still bindable), a `config_only` gated
  pyannote-shape model (never truncation-flagged), a non-commercial license (warning), a
  hand-truncated on-disk case (<60% → `integrity:truncated`), platform filtering, fail-soft, and the
  byte-sum/host-token/license-sniff helpers. This keeps the atom dependency-complete without a
  half-built mechanism; Success Criterion 6's "download/bind/RUN" through the real app UI completes
  when the apps-repo cards land.

- 2026-08-16 — **DONE (LMMV-5 / Session 4 — Sidecar isolation + resumable installs + the
  residency surface). Success Criterion 1 proven against a real killed child; Success
  Criterion 8's attribution + free-and-verify proven; two DEVIATIONS.**

  `local_models/sidecar.py` (runner, install jobs, live-runner table, watchdog) +
  `_sidecar_child.py` (the stdlib-only child harness, loaded by path because the app's
  dedicated venv has no `personalclaw` in it) + `local_models/residency.py` (what is resident,
  what it costs, and the unload lever). `ProviderConfig.execution` is a FIELD on the existing
  `model` type — `PROVIDER_TYPES` and the `_TypeHandler` set are untouched, and the default
  stays `in-process` under test, because a sidecar default would silently change the runtime of
  every provider already installed on every machine.

  **Success Criterion 1, the only way it means anything.** A real child is really `SIGKILL`ed
  mid-encode: the caller gets `SidecarCrashed(reason="signal_9")`, the gateway lives, and the
  next call brings generation 2 up and returns a real result. A mocked crash would have skipped
  the code that decides whether a half-written frame is believed — which is exactly where the
  first measured finding was.

  **Finding 1 — a truncated frame is only dangerous when it is VALID JSON.** The first test
  killed the child mid-fragment and asserted a crash; removing the newline-completeness guard
  still passed, because `json.loads` rejects `{"result": {"vector": [0.1,` on its own. The
  guard's unique job is narrower and worse: a frame that is complete, valid, and carries the
  pending request id, but has no terminating newline because the process died between the write
  and the flush. The test now produces exactly that, and without the guard the caller is handed
  `{'vector': ['HALF']}` from a dead process. A mutation that reds nothing is a lead, and this
  one led to a real hole in the test rather than a real hole in the code.

  **Finding 2 — a mis-nested `call` payload was silently dropped.** Worker arguments belong
  under `payload`; `{"method": "encode", "hang": true}` reached the worker as `{}` and the call
  returned instantly with a plausible answer. That is the arg-nesting bug class, so the child
  now REFUSES an unexpected top-level key instead of handing the worker an empty dict.

  **Two design calls worth naming.** (a) *The generation fence lives in exactly one place.*
  `deliver()` fences a superseded frame; `_await_reply` matches only on request id and
  deliberately does not repeat the generation compare. Two overlapping half-rules mask each
  other, and a mutation of either then reds nothing — the redundancy would have made the fence
  untestable. Ids are `"<generation>:<seq>"`, so the test forges the CURRENT id onto a
  previous-generation frame, the one shape only the counter can catch. (b) *`restarts` counts on
  generation, not on a live child handle.* The first version keyed off the handle and
  undercounted, because a crash detaches it — precisely the case the counter exists to report.

  **The watchdog is inspectable by construction** — `watchdog_sweep()` returns
  `{action: noop|respawned|budget_exhausted, generation, restarts}`, so the tests assert the
  decision instead of sleeping and hoping. `sidecar_restart_max` bounds consecutive respawns: a
  genuinely broken venv yields one honest error rather than a respawn busy-loop.

  **Installs ride the existing job registry** (§3.2 as written): `kind: "sidecar-install"` was
  already reserved in LMMV-3's canonical record, so there is no second registry and no second
  SSE hub. The deps receipt is written only after pip exits zero — that single ordering is what
  makes a killed install RESUME instead of skip. `DELETE` answers 409 while a job runs (deleting
  the tree under a live pip is how a half-installed venv is born) and 400 for a venv core did
  not create.

  **Residency is honest about what it cannot know.** A sidecar's `rss_mb` is child-reported
  (`stat` frames); an in-process model's is `None`, never a fabricated split of the gateway's
  own heap. `is_active` is ATTRIBUTION — a model still resident after its binding moved reads
  `is_active: false`, which is the reclaimable row and therefore sorts first — and it is matched
  under BOTH spellings of the provider name (the registry's app name and `provider.name`), or a
  bound model would show as reclaimable. Unload returns a fresh pressure snapshot, so "Unload
  frees RSS" is shown rather than asserted.

  **DEVIATION 1 — there is no Dashboard bento to put a tile in.** §7 says "reusing the existing
  Dashboard bento surface", but `DashboardPage.tsx` records that the bento grid and its per-user
  layout persistence were deliberately retired ("no bento boxes"). "On this machine" therefore
  ships as a hard-imported widget in a `<Section>` band — the shape `PinnedArtifacts` uses,
  which is what the retirement left in place. One `Meter` primitive was added to `ui/` (with its
  required `.doc.ts`) instead of hand-rolling a fourth linear bar next to the three that already
  exist, and both surfaces share `lib/residency.ts` so they cannot drift on which model is
  reclaimable.

  **DEVIATION 2 — the sentence-transformers sidecar VARIANT is an apps-repo follow-up**, the
  same cross-repo boundary LMMV-2 recorded: `git ls-files apps/` is still 0 here, so no provider
  app is tracked in core and an edit to a seeded copy is not a durable change. The variant is an
  `app.json` with `execution: "sidecar"` plus a worker module, both app-side. Everything core
  owes it exists and is proven end-to-end against a real child; what the apps-repo change adds is
  that the child is sentence-transformers. The env-var band-aids
  (`TOKENIZERS_PARALLELISM=false`, forced single-process encode) become that worker's internal
  defaults there, so §11's "DEMOTED to sidecar-internal defaults" completes with it.

  **Config:** a new `local_models` section (`pressure_warn_pct`, `sidecar_restart_max`) through
  all five wiring points + `config-baseline.json`. Both advisory: the threshold blocks nothing
  and nothing is ever auto-unloaded — on a single-user machine the person watching the bar is
  the one who decides what to close.

  **Gates:** `make lint` clean (mypy 876 files) · `tests/test_local_model_sidecar.py` 57 passed ·
  the rails (config-baseline / config-roundtrip / inert-surface / portability /
  durability-inventory / resilience-degraded / agent-reference / spawn-ceiling / dag-derived +
  the three local-model suites) 280 passed · `web` 275 files, 2745 tests, typecheck clean. Six
  mutations run; the one that reded nothing exposed a weak test (see Finding 1).
- 2026-08-16 — **PARTIAL (LMMV-6 / Session 5a — Subscription-credential model providers). The
  whole core mechanism + contract landed and tested; the ONE reference app is a
  PersonalClawApps deliverable and is NOT shipped. Atom deliberately left `todo`.**

  **What landed.** `llm/subscription_credentials.py` is the new seam: a declarative
  `SubscriptionSource` (`id`, `login_hint`, `credential_files`, `token_path`,
  `expires_at_path`, `expires_at_unit`), a process registry
  (`register_subscription_source`, last-wins, empty by default), a `SubscriptionAuth`
  result and two readers — `resolve_subscription_credential()` and
  `subscription_source_status()` (the `(available, reason)` shape). `BrandedProviderSpec`
  gains `credential_source: str` (round-trips through `to_dict`/`from_dict`, still
  hashable), and `_factory`'s credential order is now the documented five hops:
  `entry.credential` → `options.api_key` → **subscription-source resolver** →
  `spec.api_key_env` → anon placeholder. `providers/loader.py` `load_availability()`
  DERIVES the probe: when an app module exports no `availability()`, one is built from the
  `credential_source` its registered spec declares (read through the new
  `spec_credential_source()`, the same narrow core-facing reader shape as
  `spec_pricing()`), so a not-signed-in CLI greys the bundle out in the extensions list
  with the app's own reason. An explicit hook still wins.

  **Four judgment calls worth naming, because this reads someone else's credential store.**
  1. **Independent `if`s, not an `elif` chain.** A source that is merely not signed in must
     fall THROUGH to `spec.api_key_env` and then the placeholder. An `elif` would strand an
     app that declares both.
  2. **The resolver sits BELOW both explicit choices, permanently.** `credential_source` is
     not a second way to set an API key: a resolver that outranked `entry.credential` or
     `options.api_key` would silently override a key the user chose. There is a test per
     hop for exactly that reason.
  3. **No refresh, ever — a DEVIATION from §8's wording.** §8 says the adapter reads the
     CLI's store "refreshing via the CLI where the format demands it". Refreshing writes to
     a store PersonalClaw does not own, so it is dropped: an expired token is reported as
     not-signed-in with the app's login hint and the user re-runs their own CLI's login.
     Read-only is proven twice — structurally (the module contains no write-shaped call at
     all; the detector is checked for vacuity against `config/loader.py`, which does write)
     and behaviourally (bytes, mode, size and `st_mtime_ns` identical after a resolve, and
     no sidecar file appears — asserted on the signed-in, expired AND malformed paths).
  4. **A parse error is never "authenticated".** A truncated, empty, garbage, wrong-shape or
     `null`-branch store is not-signed-in with a reason. The malformed arm reports a FIXED
     sentence rather than the parser's message, so no fragment of a credential can ride out
     inside an error string — the test asserts the token, its first 12 chars, its last 12
     and the key name are all absent from the reason.

  **Core ships ZERO vendor rows (provider boundary).** §8's example is a specific agent CLI,
  but core does not learn that any CLI exists, where it keeps its token, or what its login
  verb is called. The APP declares the whole `SubscriptionSource` and owns the `login_hint`
  wording — the same discipline `llm/acp_agent.py` already follows with the
  bundle-declared `login_command` ("the vendor knows its own auth verb; the core never
  names it"). `SubscriptionSource` + `register_subscription_source` are re-exported from
  `sdk/provider_helpers.py` and are the only app-facing half.

  **DISCOVERY — an adjacent leak fixed in the same lines.** `_factory` popped the two
  api-key spellings with a short-circuit `or`, so an entry carrying BOTH `api_key` and
  `apiKey` left `apiKey` in `options` → `extra_options` → the SDK call kwargs
  ("unexpected keyword argument"). That is the exact bug class the module docstring already
  warns about for `base_url`/`endpoint`. Both spellings are now popped unconditionally;
  `snake_case` still wins. Regression test included.

  **WHY THE ATOM STAYS `todo` — the reference app is cross-repo and therefore unshipped.**
  `git ls-files apps/` = 0 in this repo: app bundles live in the separate PersonalClawApps
  repository, so the "ONE reference model-provider app ships (PersonalClawApps)" clause of
  the `done_when` cannot be satisfied from here, and no local `apps/` tree was invented to
  fake it. Consequence stated plainly: **until one app registers a `SubscriptionSource`,
  this mechanism is production-INERT** — the registry is empty, no spec declares a
  `credential_source`, and the derived availability probe never fires. Flipping the atom on
  the core half alone would hide that. The app also could not have been written first: the
  SDK exports it needs do not exist until this change lands.

  **The reference app's exact contract (for the PersonalClawApps follow-up).** One directory,
  two files, no new core work:

  * `app.json` — `name` (kebab-case), `version`, `displayName`, `description`, and a
    `provider` block: `type: "model"`, `implementation: "provider:create_provider"`,
    `providerType: "<the branded type the spec registers>"` (this field is what
    `load_availability` reads to find the spec — omitting it silently disables the derived
    probe), `capabilities`, and NO settings-schema api-key field (there is no key to ask
    for). Permissions: the minimum; the app itself needs no credential permission because
    core does the reading.
  * `provider.py` — import ONLY from `personalclaw.sdk.*`; call
    `register_subscription_source(SubscriptionSource(id=…, login_hint="sign in with `<verb>`
    first", credential_files=("~/…",), token_path=(…), expires_at_path=(…),
    expires_at_unit="ms"))` and then `register_branded_app(BrandedProviderSpec(type=…,
    protocol=…, default_base_url=…, default_model=…, credential_source="<that id>",
    api_key_env=""  # deliberately empty: subscription auth only))`, exposing the returned
    `create_provider` / `create_catalog`. No `availability()` hook is needed. Ship
    `test_provider.py`, `README.md`, `LICENSE` per the app-creation guide.

  Validation of the app half is the normal loop: add the app dir as a local Store source,
  install it, and confirm Settings › Providers greys the card out with the login sentence
  while the CLI is signed out, then binds and RUNs a completion once it is signed in — with
  no API key anywhere. Sessions/models/catalogs need nothing new: they flow through the
  ordinary branded-app path, and no agent runtime is involved.

  **Gates:** `make lint` clean (black/isort/flake8 + mypy 881 source files) ·
  `tests/test_subscription_credentials.py` **46 passed** (new) ·
  `test_provider_helpers.py` + `test_routing_rates.py` + `test_prompt_cache_marker.py` +
  `test_sampling_best_of_n.py` + `test_workflows_surfacing_channels.py` 245 passed ·
  `inert-surface-baseline.json` byte-identical to a fresh render with NO regeneration (the
  two new SDK exports are consumed through the SDK facade by the new tests, which is the
  ratchet's "add the missing reader", not a blessed higher number) · `make test` full suite
  green. Eight falsifying mutations were run against the live line and every one reded a
  named test — none vacuous.

- 2026-08-17 — **DISCOVERY (LMMV-6 / Session 5a follow-up). The five per-hop precedence
  tests did not actually pin the credential ORDER: swapping hops 1 and 2 was invisible to
  the entire suite. Closed with one ladder test. Atom still `todo` — the reference app
  remains the only outstanding clause, unchanged.**

  Re-verified the core half against the code first, because the premise handed to this
  session said `credential_source` was absent from `BrandedProviderSpec`. That premise was
  stale: commit `6a1e9058` is on `origin/main`, `credential_source` is at
  `provider_helpers.py:88`, and all four core clauses of the `done_when` hold. No core
  clause needed implementing.

  **The measured gap.** The entry above claims "eight falsifying mutations … none vacuous",
  but that set did not include an **adjacent swap of hops 1 and 2**. Each per-hop test
  supplies only the two sources it compares — the hop-1 test sets no `options.api_key`, the
  hop-2 test sets no `entry.credential` — so neither can observe the other's inversion.
  Mutating `_factory`'s hop-2 guard from `if cred is None and _opt_key:` to `if _opt_key:`
  (letting a per-instance key overwrite an explicit `entry.credential`, i.e. exactly the
  "silently override a key the user chose" failure judgment call 2 exists to prevent) left
  the module **46 passed, green**. A mutation that reds nothing is a lead, not a pass.

  **The fix.** `test_the_whole_five_hop_order_holds_as_one_descending_ladder` supplies ALL
  FIVE sources at once and knocks them out one rung at a time — entry credential → per-
  instance key → signed-in subscription → env key → anon placeholder — asserting the winner
  changes in exactly the documented sequence. Every rung keeps the lower sources populated,
  which is what makes an adjacent swap anywhere in the chain visible. Paired with
  `test_the_five_hop_ladder_has_five_DISTINCT_rungs` as its vacuity floor: the ladder can
  only prove an order if the five rungs are five different secrets, since two rungs sharing
  a value would let a swap between them read as a pass.

  **Falsifications (mutate live line, confirm applied, observe red, restore from a file
  copy — never `git checkout`).** (a) hops 1↔2 swapped → **only** the new ladder test reds,
  `AssertionError: assert 'OPT-KEY' == 'ENTRY-CRED'` (1 failed, 47 passed) — the precise
  mutation that was green before this change. (b) the terminal not-signed-in return replaced
  with `raise RuntimeError` → 19 soft-fail tests red including
  `test_load_availability_derives_a_probe_from_the_declared_credential_source`, proving the
  typed `(False, reason)` contract is load-bearing and not incidental. (c) `_expired()`
  forced to `return False` → `test_an_expired_sign_in_is_not_signed_in` and
  `test_an_EXPIRED_store_is_not_refreshed_or_rewritten` red (`assert True is False`).

  No credential store was written, and no real user store was read: the suite's fake stores
  live under `tmp_path`, and the run's real-home rail reported `~/.personalclaw` unchanged.
  No config field added, so no round-trip points move; no new provider TYPE, so the
  generated reference docs are untouched (`test_agent_reference.py` green).

  **Gates:** `make lint` clean (black 1756 files · isort · flake8 · mypy 903 source files) ·
  `test_subscription_credentials.py` 48 passed (46 + 2) · that file with
  `test_config_roundtrip.py` + `test_config_baseline.py` + `test_agent_reference.py` +
  `test_action_provider_chokepoints.py` + `test_inert_surface_baseline.py` + `tests/security/`
  **178 passed**. `inert-surface-baseline.json` needed no regeneration.

- **2026-08-19 — `LMMV-6` CLOSED (`todo` → `done`), on the clause that kept it open.** The atom's
  own 2026-08-16 entry left it `todo` for one stated reason: *"the ONE reference app is a
  PersonalClawApps deliverable and is NOT shipped"*. It is shipped. `claude-subscription/` is on
  `PersonalClawApps@main` (5 files — `provider.py`, `test_provider.py`, `app.json`, `README.md`,
  `LICENSE`), added by `5908a88` and since carried forward by `#42`, which gave the same spec its
  `prompt_cache=EXPLICIT` posture. Verified by content on both sides rather than by a PR title:
  `provider.py:49` declares `CREDENTIAL_SOURCE = "claude-code"` and its spec names that id, while on
  core `main` `sdk/provider_helpers.py:88` carries `credential_source: str = ""` round-tripping
  through `to_dict`/`from_dict` (`:112`, `:135`) and `providers/loader.py:176-178` derives the
  availability probe from `spec_credential_source(provider_type)`. So the five-hop credential order,
  the derived not-signed-in reason and a real app riding CLI auth all exist on `main` together.
  No code shipped with this entry — the atom's work was already merged; only the status was stale.

- **2026-08-19 — DISCOVERY (roadmap tracking, cross-plan): the atomic tables had drifted from
  `dag.json` in three ways, and nothing could see it.** While closing `LMMV-6` I found its row
  listed **twice** in `docs/roadmap/atomic/LMMV.md` — once as shipped, once, lower down, as not
  started. A census across all 71 atomic files found the class: **79 rows carried a status mark
  contradicting `dag.json`** (67 plain `todo` boxes on atoms that are `done`, over 30 files) and
  **17 atom ids appeared twice** in their own table. `AP`, `AS`, `CATO`, `PCS`, `PHF`, `PP`,
  `WF2LOO` and `WV` were the worst. Cause: `test_roadmap_dag_derived.py` guards `dag.json`'s own
  derived block and nothing coupled the human-readable tables to it, so every stale row was free.
  Fixed all of them (row count is now exactly 640 — one per atom) and added
  `tests/test_roadmap_atomic_status_sync.py`: mark ⇔ `done`, no duplicate rows, no row for an
  unknown atom, every atom has a row, and the leading "N atoms" count matches. Falsified all three
  new assertions by mutation (mark reverted → red; row duplicated → red; count 7→9 → red), each
  restored from a file copy. Six stale prose summaries were corrected by hand
  (`DC`/`INU`/`LMMV`/`MGAV`/`PT`/`TSE` — e.g. `INU` said "5 done, 3 todo" for a plan whose 8 atoms
  are all done); the *narrative* half of those sentences is deliberately left unrailed, since it
  has no canonical form — only its leading number is pinned.

**DONE — `LMMV-8` (2026-08-20).** Hardware-aware model fit landed: `local_models/fit.py` owns one
memory budget and one verdict. Unified memory is counted once — an integrated GPU's VRAM is never
added on top of system RAM — only a discrete GPU adds a second pool, and a configurable reserve is
held back for the OS and runtime. Driven end to end on a 48 GB Apple M5 Pro: budget 46080 MB against
49152 MB total, `unified_memory: true`, `discrete_vram_bytes: 0`. Falsified by deleting the
`if not host.unified_memory:` guard on the live line → `assert 22548578304 <= 17179869184`, a 16 GB
machine claiming 21 GB; restored from a file copy.

The three host-fact collectors are now one. `handlers_system.py` turned out to hold **three**
memory-total readers, not the one the plan predicted (`_get_static_system_info`,
`_collect_system_metrics`, plus `fit`'s own) — all now route through `residency.memory_pressure()`,
with the static card, the live tick and `fit.host_capacity()` independently agreeing at 48.0 GB. The
GPU capacity probe (vendor/model/VRAM total/unified) moved into `fit`; the widget keeps only its
live-telemetry query (`utilization.gpu,memory.used,temperature.gpu`) and all seven payload keys
survive. Free disk was deliberately left local in that file, with the reason recorded: `HostCapacity`
carries no disk *total*, so routing the gauge through the helper would mean two reads of one
filesystem in one payload that could disagree, and there is no capacity disk question there — the
capacity question is the pre-download precheck, which is `fit`'s and lives in `model_downloads.py`.

Validated as a user against a real installed provider (faster-whisper, six real cards 75→2900 MB) on
`#/settings/providers`: three fit chips with accessible names ("Fits — fits comfortably in ~1.0 GB"),
the filter ON by default showing 3 of 6 rows with a "3 hidden" notice, toggling to 6 and back to 3,
`role="switch"`, zero console errors. The reserve and the filter default round-trip through config,
verified live via `PATCH /api/config/personalclaw`: budget 46080 → 1024 → 0 MB as the reserve rose.

**DEVIATION — the verdict is judged per row, not on the family quote.** The atom asks for a median
family quote and a fit verdict; computing the verdict *from* that quote made a 16 GB model read
`yellow` on an 8 GB machine — precisely the "promise a fit the user will not get" the clause forbids.
So `quoted_size_mb` is the family median while the verdict is judged against the row's own
`size_mb`, falling back to the quote only for a row that publishes no size. The UI states the row's
own size and appends the family median only when the two differ.

**DISCOVERY — the family rule reaches tag catalogs, not the shipped static ones.** Variants are
grouped by `family_key(name) = name.split(":")[0]`, which is the ollama `family:tag` form. Measured:
for `qwen3:4b|8b|16b` the quote is the median (8000, not the minimum 4000) and the step-down resolves
(`largest_that_fits → 8000`). But faster-whisper ships `tiny|base|small|medium|large-v3|turbo` —
six colonless names, hence six families of one — so a live drive with a 1 GB budget returned
`fit_step_down: None` on every red row, and `quoted_size_mb == size_mb` throughout. The median quote
is harmless there (each row states its own exact size, so no false promise is possible), but the
step-down affordance cannot fire for that catalog. It is live for the tag-based ollama catalog, which
is a shipped provider, and is asserted at the handler and in the UI. Closing the gap properly needs a
provider-declared variant family — a `LocalModel` field plus per-app declarations in
PersonalClawApps — which is a cross-repo change and deliberately not folded into this atom. Widening
the rule inside core was rejected: grouping a provider's whole catalog would offer "Amy" as a
step-down for "Lessac" in piper-tts, i.e. a different voice presented as the same thing smaller.

**FIXED during validation — a measured budget of 0 read as "unknown".** `usable_memory_bytes` returns
0, distinctly from `None`, for a machine smaller than the reserve; the frontend gate tested
`budget_mb > 0` and so collapsed that legitimate value into the absent marker, silently disarming the
filter on exactly the machines that need it. Reproduced live: a 48 GB host with the reserve at 64 GB
chipped all six models "Won't fit" and hid none of them. The gate now tests `>= 0`, `measured` alone
carries the unknown case, and a test asserts the two answers DISAGREE. After the fix the same drive
hides all six with a "6 hidden" notice and an empty state that explains itself.

## Execution log — `LMMV-7` (Session 5b hardening) — third pass

- [2026-08-23][LMMV-7] **PARTIAL — the atom stays `todo`, and this pass was an AUDIT plus one real hole
  closed, not a build.** Most of the atom was already on `origin/main` before the pass began: the budget
  helper landed in `addbc332` (PR #1628) and both hardening rails were made derived in `39d28fda`. The two
  prior `PARTIAL` entries (2026-08-18, 2026-08-21) say so. Observations `O211`-`O218`, findings
  `G171`-`G176`.
- [2026-08-23][LMMV-7] **Clauses 1, (a), (b), (c) verified MET, by identity rather than by count.**
  Clause 1: `local_models/budgets.py` (`catalog_window` → `model_budget` → `output_budget`) is consumed at
  `llm_helpers.py:498` and awaited on **all three** resolution paths (pin `:535`, chain-advance `:556`,
  plain `:588`); `workflows/compaction.py` is **byte-identical**, so "no compaction logic rewritten"
  holds. Clause (a): every two-population case asserts the bundled provider **`is` the same object** and
  `set(before) - set(after) == {transient}`, with the population derived by AST and reconciled both ways;
  an independent census confirms no unnamed member (`_providers.clear()` appears only in
  `manifest_reference.py`, which clears the **tool** registry). Clause (b): asserts the app-name key
  resolves **and** the internal name is `None`.
- [2026-08-23][LMMV-7] 🔴 **Closed a DISCOVERY the prior pass recorded but did not fix: the SC-10
  model-root fixture's reach is exactly one attribute lookup deep.** It patches module attributes, so a
  module-level `from personalclaw.local_models.layouts import cleanup_candidates` binds the **unwrapped**
  object at collection time, before the fixture runs. Measured which entry point that actually bites:
  `cleanup_candidates` (`layouts.py:211`) walks `root.rglob("*")` itself at `:223` and delegates to
  nothing, so an alias of it reaches a real cache root unintercepted; the other six delegate to a guarded
  module global one hop in. Closed by **banning the shape** (`ORIGINALS` + an AST
  `import_bound_guarded_names()` detector + a suite-wide ban) rather than by tracking the delegation
  graph, so the hazard cannot be written rather than being chased.
- [2026-08-23][LMMV-7] **Clause 4 (the full download/delete/bind/RUN matrix across all six providers)
  remains NOT MET** — the same clause both prior passes left open.
- [2026-08-23][LMMV-7] 🔴 **The 2026-08-21 entry is WRONG where it says "there is no
  voice/transcribe/synthesize handler in `dashboard/handlers/`".** `POST /api/stt/transcribe` exists
  (`dashboard/server.py:647` → `handlers/core.py:186`) and `POST /api/voice/synthesize` exists
  (`server.py:1026`). The prior pass grepped only `handlers/`; **the routes are registered in
  `server.py`.** Diarization genuinely has no run route (`G172`). So two matrix cells are **"not
  exercised", not "no surface"** — a materially different reason.
- [2026-08-23][LMMV-7] **`G173` (MEDIUM) — `PERSONALCLAW_HOME` does not isolate model weights** for
  `faster-whisper` / `diarization-onnx`: they root at `XDG_CACHE_HOME`/`~/.cache`. That is the
  production-side analogue of the very incident SC-10 closes in tests, and today it rests on a convention
  nobody sets.
- **STILL UNVERIFIED / recorded.** `G171` the SC-5 selftest is provider-blind (`_run_selftest` uses `name`
  only as an output label, `handlers/doctor.py:266-267`), so it returns `ok: true` for deleted-weight
  providers and for nonexistent names — owned by LMMV-4, not this atom. `G175` one guarded-entry-point
  cell passes only because the wrapper raises before forwarding, so it never exercises the real arity.
  `G176` the SC-3 gated-download failure is unclassified (`reason: ""`), carried from the prior pass and
  not re-verified here.
- **`O216`: `origin/main` is not green.** A pristine full run on `origin/main` reds
  `test_loop_worktree_sparse::TestPoolBound::test_batch_creates_every_worktree` — the documented
  sparse-cone flake. Recorded so a future pass does not attribute it to its own change.

## Execution log — `LMMV-7` (Session 5b hardening) — fourth pass

- [2026-08-24][LMMV-7] **PARTIAL — the atom stays `todo`. This pass was a FALSIFICATION pass, not a
  build: the entire brief scope was already on `origin/main` at `0d90f5ab`.** The pass was briefed to
  build clause 1 plus the three regression locks (a)/(b)/(c) as if none existed. All four are shipped —
  `local_models/budgets.py` consumed at `llm_helpers.py:498` and `context_headroom.py:389-392`;
  (a)+(b) in `tests/test_local_model_refresh_invariants.py`; (c) as the autouse
  `_forbid_real_model_roots` fixture (`tests/conftest.py:307-350`) + `tests/real_model_root_guard.py`
  + `tests/test_local_model_root_guard.py`. **Nothing was rebuilt** — a second rail over the same
  invariant is dead code, and the third pass (2026-08-23) had already recorded them MET. What this pass
  adds is the evidence that verification *by reading* could not give: each rail was driven RED by
  mutating the live source line it depends on.
- [2026-08-24][LMMV-7] **(a) the two-population invariant — falsified.** Mutated
  `stt/registry.refresh_providers()` (`:99-101`) from the targeted `for name in list(_remote_names):
  _providers.pop(name, None)` to `_providers.clear()` — the original regression verbatim. **2 reds:**
  `test_refresh_keeps_the_bundled_population_remote_tracked[personalclaw.stt.registry]` (`assert None is
  <_Bundled>`) and `test_a_registered_sidecar_proxy_survives_a_use_case_refresh`. The AST census
  (`test_every_use_case_registry_that_refreshes_declares_a_transient_population`) reconciles the four
  refreshers both ways, so a fifth registry inherits the invariant when it is written.
- [2026-08-24][LMMV-7] **(b) registry drift — falsified on two of its three legs, independently.**
  Keying: mutated `providers/registry.py:1019` `name=ext.name` → `name=provider.name` → **2 reds**
  (`test_model_type_handler_keys_a_sidecar_proxy_by_the_app_name` + the survival gate). Duck-type:
  dropped `"stt"` from `_DOWNLOADABLE_CAPS` (`local_models/registry.py:228`) → **3 reds**, including
  `test_sidecar_proxy_satisfies_the_local_model_duck_type` directly. The third leg (refresh survival) is
  red under both mutations *and* under (a)'s, i.e. it is genuinely the point where the two invariants meet.
- [2026-08-24][LMMV-7] **(c) the model-root rail — falsified in both directions.** Coupling to live
  source: renamed `layouts.reclaimable_bytes` (`layouts.py:235`) → the autouse fixture raises
  `layouts.reclaimable_bytes no longer exists; update GUARDED_FUNCTIONS…` at SETUP of every test in the
  file. That is the intended blast radius — a renamed cache-root entry point stops the suite until it is
  re-listed, rather than silently narrowing the rail. Detection: neutralized `offending_root`'s comparison
  → **15+ reds**, most reporting `DID NOT RAISE AssertionError`, which is the proof the vacuity
  assertions are load-bearing and deliberately reach for real model dirs.
- [2026-08-24][LMMV-7] 🔴 **Closed `G175`, which the third pass recorded as STILL UNVERIFIED — and
  localized it to a name.** The finding was "one guarded-entry-point cell passes only because the wrapper
  raises before forwarding, so it never exercises the real arity." Measured which: **two** of the seven
  entry points take `cache_root` alone (`cleanup_candidates` AND `reclaimable_bytes`), but
  `test_every_guarded_entry_point_is_wrapped` special-cased only `cleanup_candidates`. So
  `reclaimable_bytes` was asserted through `fn(real_root, "some/model")` — a call the real one-argument
  function can never accept. It surfaced as `TypeError: reclaimable_bytes() takes 1 positional argument
  but 2 were given` while every sibling cell reported `DID NOT RAISE`, i.e. the cell was proving the
  guard fires *through a call shape that was itself wrong*. Fixed by DERIVING the trailing arguments from
  the live signature (`real_model_root_guard.trailing_args`, read off the recorded unwrapped
  `ORIGINALS[name]` — never off the module attribute, which the fixture has already replaced and which
  would confirm itself). Two new rails: the derived shape must be a call the real function accepts, and
  the derived arity must be non-uniform across the population (otherwise a constant would do, which is
  exactly the bug). Falsified by reverting `trailing_args` to the old uniform `("some/model",)` → **3
  reds**, both one-argument cells plus the non-uniformity floor.
- [2026-08-24][LMMV-7] **Clause 4 remains NOT MET — the atom stays a PARTIAL for the same reason all
  three prior passes left it open:** *"the full download/delete/bind/RUN matrix across all 6 providers
  validated as a user."* It needs real model weights downloaded per provider, so it is
  environment-gated, not code-gated. `G172` (diarization has no run route) and `G173`
  (`PERSONALCLAW_HOME` does not isolate `faster-whisper` / `diarization-onnx` weights, which root at
  `XDG_CACHE_HOME`/`~/.cache`) both stand un-retested this pass.
- [2026-08-24][LMMV-7] **Suite-wide validation of the `conftest.py` guard is DEFERRED to the owner's
  full union gate.** This pass changed no `src/` file and no `conftest.py` line; the two files it
  touched are the rail's own detection module and its own test. Coverage reported below.
