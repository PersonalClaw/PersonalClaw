# The Provider Boundary — What Belongs in Core, and Why

PersonalClaw's central architectural tenet: **the core package is
provider-agnostic**. Core contains capability-enabling mechanisms (protocols,
registries, resolvers); every integration with a *specific* provider — a
vendor's endpoints, auth, catalogs, binaries, wire quirks — lives in an app
bundle. This document records where the line is drawn and, for each surface
that *looks* vendor-flavored but stays in core, why that judgment was made.

Paths are relative to `PersonalClaw/src/personalclaw/` unless noted;
`apps/` is the first-party bundle directory at the workspace root.

## Why a boundary at all

The boundary is what makes the system composable: any model provider, channel,
agent runtime, or search engine can be added by installing an app — no core
edits. It also keeps core testable in isolation (the core test suite collects
and passes without the sibling `apps/` directory present) and keeps
vendor-specific dependencies (SDKs, scrapers, block-format builders) out of the
core dependency set. Apps import core **only** through the `personalclaw.sdk.*`
facade (26 modules), enforced by `tests/test_apps_import_boundary.py`.

## The boundary-judgment table

Not everything with a vendor's name in it is drift. Some surfaces are
*protocol* or *reference data* that core must own to function. Each row below
is a deliberate, documented judgment (also recorded in-module at each site).

| Surface | Home | Judgment |
|---|---|---|
| `llm/anthropic.py`, `llm/openai.py` | core | **Wire-protocol clients only.** They speak the Anthropic/OpenAI HTTP message formats — formats many providers reuse. Neither module calls `register_type` at import; registration is owned by `apps/anthropic-models/provider.py` and `apps/openai-models/provider.py`. Core ships the client; an app decides it is *used*. |
| `stt/`, `tts/`, `image_gen/` `openai_provider.py` | core | **OpenAI-*compatible* protocol clients.** The `/v1/audio`, `/v1/images` shapes are a de-facto protocol implemented by many vendors. Vendor **catalogs** (which model ids exist, their properties) are contributed by apps via the `media_catalogs.py` catalog-contribution seam — `apps/openai-models` owns the OpenAI ones. |
| `acp/dialect.py` (`ClaudeCodeDialect`, `CodexDialect`) | core | **Protocol-shape strategies**, not vendor logic. They encode the small frame-shape differences between the Zed-maintained ACP adapters. App bundles select a dialect by id (`options["dialect"]`); nothing in core infers a vendor from argv or binary names. |
| `llm/catalog.py` family map + `infer_capabilities()` | core | **Fallback-only reference data.** Providers that *declare* capabilities always win; the vendor-name markers only classify unknown models discovered via `/v1/models`. Same class as public model-pricing tables (`pricing.py`, `model_pricing.json`) — data about the world, not an integration. |
| `security.py` `xox[bpas]-` token patterns; `sandbox.py` `SLACK_*` env denylist | core | **Secret-DETECTION data.** These patterns exist to *redact and block* leaked credentials. Renaming them to something generic would break the control they implement. Deliberate keep. |
| `CRED_SLACK_*` constants | core `config/loader.py`, re-exported by `sdk/channel.py` | The literal `.env` credential-store key names (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`) that existing installs already hold. The loader is the credential store's home (the bottom layer, below all apps); `sdk/channel.py` re-exports them as the app-facing surface, so the slack app imports via the SDK and no import direction is inverted. Renaming the keys would break existing installs for zero gain. |
| `constants.APP_LOGGER_ROOTS` (`"slack_runtime"`) | core | The list of app logger namespaces the CLI log setup (`cli.py`) and the dashboard log-level handler consume. Apps *registering* their own logger roots (instead of core listing them) is a post-publication roadmap item — deliberately not built yet. |
| Everything else vendor-specific | `apps/` bundles | Endpoints, auth flows, catalogs, binary resolution, block/attachment formats, scraping — all bundle-resident. |

## The app-bundle landscape (36 first-party bundles)

- **16 model-provider apps** — 5 protocol-thin branded apps (built on
  `sdk/provider_helpers.py` `register_branded_app`), 2 generic-endpoint apps,
  4 full-protocol apps, 5 local-inference apps (faster-whisper, piper-tts,
  sentence-transformers, diarization-onnx, diarization-pyannote).
- **7 search-provider apps** — registered through `search_providers/`; the
  zero-config floor is a declared `keyless` *capability*, not a vendor name
  in core (`search_providers/registry.py::_keyless_provider`: first registered
  keyless provider wins).
- **3 agent apps** — `claude-code-agent`, `codex-agent`, `kiro-cli-agent`:
  binary resolution, dialect selection, and login argv are all bundle-resident;
  core `acp/` is the vendor-neutral protocol layer.
- **1 channel app** — `slack-channel`, the full reference channel provider
  (see [inbox-channels.md](inbox-channels.md)).
- **3 tool apps, 1 action app, 1 skills app** (`skills-sh` marketplace),
  **2 backend+UI apps** (Minutes, Growth — contributed dashboards).

## How resolution works (no vendor names in the path)

1. **Bindings** — `~/.personalclaw/active_models.json` maps *use cases*
   (chat, background, embedding, ingestion, stt, tts, …) to an ordered list of
   provider/model refs; the first resolvable ref wins.
2. **Build** — `llm/registry.py` `registry.build` constructs the client via the
   factory the owning app registered with `register_type`. A per-session
   `model` override kwarg is threaded through and honored by every factory.
3. **Catalog/management** — `ModelCatalog`/`ModelManager` is the shared
   catalog and management seam; provider instances live in `config.json`
   `providers[]` (credential-chain providers store no secret material there).
4. **Local models** — `local_models/provider.py` defines the unified
   `LocalModel`/`LocalModelProvider` contract (list/download/delete plus
   gated/source metadata), orthogonal to the inference ABCs. Download detection
   must probe every filesystem layout a provider writes; deletes clear all
   layouts; a binding to a catalog-absent model surfaces as a synthetic
   not-downloaded row rather than disappearing.

## Case study: how Slack left core

The clearest illustration of the tenet is the Slack extraction (originally
13,097 LOC across 11 core modules). The end state:

- **Core kept the seams**: `channel_transports/` (inbound; a
  `ChannelTransportProvider` ABC with `start_inbound(services)`) and
  `channel_delivery.py` (outbound; the `ChannelDelivery` protocol —
  `deliver_text`, `deliver_rich`, `upload_attachment`, streaming primitives,
  `resolve_user_name`, `build_thread_link`, …). Core never constructs a vendor
  URL — even the "open this thread" deep link is produced by the app behind
  `build_thread_link`.
- **The app got the vendor logic**: `apps/slack-channel/slack_runtime/`
  (14 modules — transport, runtime facade, delivery, events, interactions,
  blocks, files, settings with a loud one-time `migrate_from_core()`).
- **Generic residue was extracted, not deleted**: LLM text utilities misfiled
  in the Slack module became core `textfmt.py`; the gateway orchestrator
  (which was ~95% core boot logic living in `slack/gateway.py`) became core
  `gateway.py`.
- **Naming followed the seam**: routes are `/api/channels/reply-targets`,
  `/api/channel/profile`; the delivery grammar is `deliver="channel[:...]"`;
  session-origin labels are `origin="channel"`. Old Slack-named routes 404 —
  clean break, no aliases.

The same pattern applied to the Ollama client (`llm/ollama.py`, 1002 LOC →
`apps/ollama-models/provider.py`) and the vendor-specific inbox source
(deleted outright — channels are channel providers, not inbox sources).

## Rules of thumb for contributors

- Adding a provider? Start an app bundle. If core needs an edit, you are
  probably missing a seam — propose the seam, not the vendor patch.
- A vendor name in core is acceptable only as (a) a wire-protocol
  implementation many vendors share, (b) fallback-only reference data that
  declared capabilities override, or (c) secret-detection material. Document
  the judgment in-module.
- Apps import core only via `personalclaw.sdk.*`. The boundary test will fail
  your PR otherwise.
- Registration belongs to the app (`register_type`, transport registration,
  catalog contribution) — never module-level side effects in core.
