# Plan: Ambient Surfaces — Composable Home, Live Artifacts, Generative UI, Tray Presence

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-12  
**Depends on:** WORKFLOWS-V2.md Slices 0-2 (run engine, for workflow-bound tile refresh); WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md §1.2 `view` trigger kind + ledger-only fires (tiles degrade gracefully to client-TTL refresh until it lands)  
**Scope:** Open the hardcoded dashboard into a user-composable home of live artifact tiles; give artifacts existence outside chat threads (chatless refresh, tweakable parameters, click-annotation); build the generative-UI layer (typed component registry, streaming renderer, agency-free visualize, action feedback into execution); ship a macOS menu-bar companion for runs and approvals

---

## Research Integration (2026-07-12)

Two approved recommendations folded in (mechanism-level, not appendix):

- **NEW-6** — user-composable home + live artifacts + generative-UI layer → §1 (tile registry), §2 (chatless refresh, freshness chips, SWR paint), §5 (component registry, streaming renderer, `visualize`)
- **NEW-6 am.(a)** — layered L0/L1/L2 FE surface overlay + `maxLayer=0` safe-mode recovery → §6
- **NEW-6 am.(b)** — EDITMODE artifact parameter protocol (marker-fenced JSON → typed tweak controls, zero LLM round-trips) → §3
- **NEW-6 am.5** — annotate mode on visual artifacts (element-anchored correction directives) + widget trees whose events feed back into execution → §4, §5.4
- **NEW-24** — macOS menu-bar/tray companion: live run progress + one-click approvals → §7

---

## Overview

PersonalClaw's daily-driver gap is not capability — it is *placement*. The platform already has ~80% of "live artifacts": agent-emitted `<widget>` blocks render as sandboxed blob-iframes, widgets save to a **versioned artifact store** (`artifacts/native.py`: `<slug>/current.html` + `versions/vN.html`), stable slugs reconcile across refreshes (`ui/widget/widgetSlug.ts`), and the C32 living-view affordance lets the agent refresh a saved widget in place. What is missing is exactly what makes a dashboard a dashboard: **existence outside a chat thread** — a refresh path that needs no chat session, and a home-surface placement the user composes. And the whole system lives inside one browser tab: needs-input pauses and pending approvals are only as fast as the human notices them.

This plan delivers four surfaces on one artifact spine:

1. **A composable home** — pin any saved artifact / workflow output as a self-refreshing dashboard tile (registry: tile = artifact slug + refresh trigger + size hint).
2. **Live artifacts** — layout/data split so steady-state refreshes are LLM-free and layout-stable; EDITMODE tweak controls and click-annotation so iterating on a visual artifact stops costing chat turns.
3. **A generative-UI layer** — a Zod-typed component registry (app-extendable), a streaming renderer alongside markdown, one agency-free `visualize(data, hint)` primitive shared by cockpit summaries, tiles, digests, and "chart this" chat asks, and widget trees whose events flow back into chat turns and workflow gates.
4. **A menu-bar companion** — a thin macOS tray shell over the existing gateway APIs + WS: live run progress, pending approvals, one-click approve/deny. No push infrastructure; one machine, one user.

**Soul guardrail:** personal-scale throughout. The tile registry is one JSON file under `~/.personalclaw`, not a widget marketplace. The generative-UI layer renders *registered* components only — controlled rendering is the safety model, not a moderation pipeline. The tray is a renderer of the existing notification/approval feed, never a second delivery path. Agent-proposed tiles and agent-rewritten surfaces propose; the user pins.

### Starting points (verified against code, 2026-07-12 recon)

The design below builds on what actually exists — and respects one deliberate retirement:

- **The customizable dashboard grid was RETIRED — a clean break, documented in code.** `web/src/pages/dashboard/DashboardPage.tsx:24`: *"no bento boxes … The customizable grid + per-user layout persistence were retired (clean break); everyone gets this one content-first layout."* There is **no widget registry, no add/remove/reorder mechanism, no masonry** — the 9 first-party widgets (HeroPulse, ActionCenter, ActiveWork, Tasks, Suggestions, Schedule, Knowledge, Memory, SystemHealth) are hard imports. "Bento" survives only as the Settings-home card helpers (`pages/settings/bento.tsx`). Earlier drafts of this recommendation said "add/remove/reorder on the existing masonry" — **there is no masonry**. This plan reintroduces composability *deliberately and narrowly* (§1): the retirement killed per-user re-arrangement of *first-party chrome*; what returns is a single additive **Pinned band** of *artifact-backed* tiles. First-party widgets stay hard-imported; the fixed launcher-forward layout stays the default; an empty registry renders exactly today's page.
- **`DashboardLiveProvider` (`pages/dashboard/DashboardLive.tsx`) is the real data seam** — ONE `useChatSocket` for the whole dashboard, WS envelopes as refetch *signals* (never payloads), `useVisiblePoll` at FAST_POLL=8000/SLOW_POLL=20000. Tiles join this provider; they do not open their own sockets, and no payload-carrying dashboard events are introduced.
- **Chat `<widget>` blocks already render as sandboxed blob-iframes**: `ui/widget/blocks.ts` `parseWidgetBlocks` (only call site `ui/Markdown.tsx:366`) → `WidgetFrame.tsx` (`sandbox="allow-scripts"`, null origin) with theme tokens injected by `widgetSrcdoc.ts` (TOKEN_ALIASES); `kind="react"` → ReactWidgetFrame. The generative-UI layer (§5) builds on this parse/render seam rather than a new one.
- **`artifact_update` already exists** — as an MCP tool (`mcp_artifacts.py:84,304`, schema in `validation.py:674`) and as `PATCH /api/artifacts/{slug}` (`artifacts/handlers.py:169`). Every update snapshots a version. The C32 living-view refresh works by injecting *"refresh artifact \"<slug>\" in place"* into a **chat session** (WidgetFrame.tsx:75-80, `skills/bundled/visual-output/SKILL.md`). The new piece is the **chatless** path (§2): an `artifact-update` **action provider** so trigger-fired workflow runs can rewrite `current.html` with no chat session — which per provider rules must be added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`).
- **The artifact store is already pluggable**: `ArtifactProvider` ABC (`artifacts/provider.py:21`) + registry (`artifacts/registry.py`, native provider lazily registered). Tiles bind to slugs through this seam; nothing in this plan assumes the native backend.
- **The approval/run surface the tray needs already exists**: `GET /api/approvals` + `POST /api/approvals/{id}/{action}` (`web/src/lib/api.ts:1401-1403`, same shape as the `approval` WS envelope), `api.uLoops()` for run states, the ONE multiplexed `/api/ws`, and `state.notify` gated by `notification_allowed()` (`providers/entity_routes.py:171`) — THE delivery gate. The tray consumes all of these; it invents none.
- **The token-lint ratchet is at zero** (`design/tokenLint.test.ts`, allowlist `[]`): registry components (§5) must be token-driven; iframe-injected artifact HTML is exempt by construction (tokens arrive via `widgetSrcdoc` aliases).
- **`visualize` must resolve models through the reasoning axis**: chat/code_tools resolution returns the NativeAgentRuntime (`provider_bridge.py:477`) — an agency-free data→UI call goes through `one_shot_completion` / the `reasoning` chat sub-category (`llm_helpers.py:275`), never the chat axis.

---

## 1. The Composable Home — Pinned Tiles Band

### 1.1 Tile registry

One store: `~/.personalclaw/dashboard_tiles.json` (atomic write, same file conventions as `active_models.json`):

```python
@dataclass
class DashboardTile:
    id: str                # uuid4 hex[:8]
    slug: str              # artifact slug — the ONLY content pointer (tile = projection of an Artifact)
    title: str | None      # override; default = artifact name
    size: str              # "s" | "m" | "l" | "full" — a HINT to the band's flow layout, not coordinates
    refresh: dict          # {"mode": "manual"}                       — refresh only via the tile's refresh button
                           # {"mode": "ttl", "ttl_secs": int}         — client-TTL (pre-substrate fallback)
                           # {"mode": "view", "trigger_id": str}      — bound AUTOMATION-SUBSTRATE view trigger
    order: int             # explicit ordering within the band
    added_by: str          # "user" | "agent" — agent additions are PROPOSALS (render with an accept/dismiss chip)
```

No x/y/w/h grid coordinates, no per-user layout engine, no drag-grid — the failure mode that got the bento retired. `size` + `order` feed a simple flow layout inside one band. That is the entire persistence surface.

### 1.2 Placement — one additive band, not a resurrected grid

`DashboardPage.tsx` gains ONE new section — a **"Pinned"** band rendered between the recent-chat chips and the HeroPulse strip, inside the existing `DashboardLiveProvider`, honoring `--content-width`. Empty registry ⇒ the band renders nothing and the page is byte-identical to today. First-party widgets are untouched (still hard-imported; still not registry entries — the registry covers artifact-backed tiles ONLY). Edit affordances: a band-level edit toggle (uses the `useEditFlag` push/replace convention) exposing remove + reorder (up/down + drag within the band); no free-form canvas.

### 1.3 Pinning

- **Pin-to-dashboard on `WidgetFrame`**: a pin control beside the existing save-as-artifact bookmark (WidgetFrame.tsx:138). Pinning implies saving (an unpinned-unsaved widget is first saved via the existing `api.createArtifact` path with its stable `effectiveWidgetSlug`), then `POST /api/dashboard/tiles {slug, size}`.
- **Pin from the artifact library / any artifact detail surface**: same endpoint; any artifact `kind` the content registry (`ui/content/contentTypes.ts`) can preview is pinnable — widgets and HTML render live, documents/images render their preview capability.
- **Pin a workflow output**: a workflow whose sink is `artifact-update` (§2.2) produces a slug; pinning that slug makes the workflow's output a living tile. This is the "pin any workflow output" path — no special tile type needed; everything reduces to a slug.
- **Agent-proposed tiles**: `dashboard_tile_propose` (a small addition to `mcp_artifacts.py`'s tool family) writes `added_by: "agent"` rows that render with an accept/dismiss chip. Propose-don't-pin: the agent never silently rearranges the user's home.

### 1.4 Tile rendering + data seam

Each tile is a `WidgetFrame` over the artifact's `current.html` (same sandbox, same `widgetSrcdoc` token injection — theme consistency for free), with a tile header carrying title, freshness chip (§2.4), refresh button, and unpin.

- **Stale-while-revalidate paint**: content loads through `useCachedData` (`{persist: true}`) — cached HTML paints instantly, the refresh check kicks, new content swaps in. Real stale content, not shimmer.
- **WS refetch signal**: `DashboardLive` adds `artifact_update`-family envelopes to its debounced refetch set — a chat-side or workflow-side update to a pinned slug refreshes the tile within the debounce window. Signals, not payloads (the DashboardLive contract).
- **View-trigger fire**: a tile in `mode: "view"` rendering past its trigger's TTL fires the AUTOMATION-SUBSTRATE view trigger (one POST; within TTL the cache serves). In `mode: "ttl"` (pre-substrate), the tile POSTs a plain refresh endpoint that re-runs the bound data workflow directly — same UX, upgraded transparently when the substrate lands.

---

## 2. Live Artifacts — Chatless Refresh

### 2.1 The layout/data split (the cost + stability trick)

Practitioner evidence (chatprd-live-dashboard): "same layout, new data, no re-prompting" — the refresh path must not involve an LLM rewriting HTML. A live artifact is:

- a **skeleton**: the stored HTML with `{{...}}` data-slot bindings (WORKFLOWS-V2 binding expressions are the slot primitive), generated ONCE by an LLM (chat turn or workflow stage);
- a **bound data workflow**: a WORKFLOWS-V2 def (degenerate case: one action node — e.g. `api fetch` or a knowledge query) whose outputs fill the slots;
- a **render transform**: a deterministic, LLM-free node that interpolates workflow bindings into the skeleton and hands the result to the `artifact-update` sink.

First generation is creative; steady-state refreshes are pure transforms — cheap, deterministic, layout-stable. Restyling ("make it look like early-2000s software") is a chat ask that regenerates the skeleton and snapshots a version; the existing per-version restore covers the practitioner's "restyle broke my layout" rollback case.

### 2.2 The `artifact-update` action provider

The chatless sink. Implements `ActionProvider` (`action_providers/base.py:50`): `execute(action_config={slug, content|content_binding, name?}, ctx, timeout)` → resolves the target through the artifact **provider registry** (`artifacts/registry.py`, never the native class directly), writes via the same code path as `PATCH /api/artifacts/{slug}` (version snapshot, prune, redaction — all inherited), returns `ActionResult{outcome: "done"}` with the slug + new version in `stdout`.

Plug-in fidelity (non-negotiable): registered via `register_action_provider` in core (it is store-adjacent plumbing, like `create-task`) **and added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`)** — without that, trigger create/update rejects it even though the UI offers it. `supports_dry_run = True` (a dry run renders the transform and reports the would-be diff without writing — consistent with the T9 dispatcher rule). It does NOT support blocking (it is a sink, not a gate).

The existing chat-side paths are untouched: `artifact_update` (MCP tool) remains the attended path; C32's "refresh artifact in place" chat injection remains for conversational refreshes. This provider is the third, unattended leg.

### 2.3 Refresh execution + cost honesty

A tile refresh = a **ledger-only fire** (AUTOMATION-SUBSTRATE two-weight rule): row in the run ledger carrying per-refresh token cost (usually zero — pure transform), duration, and per-source outcomes. Refreshes never spawn full run directories (the 1440-run-dirs critique is structurally avoided: `view` triggers never fire unviewed, and fired refreshes are ledger-weight). Refresh caps ride the trigger's gates (`rate_cap`, budget) — this plan adds none of its own.

### 2.4 Freshness + error chips (per-source status)

The top practitioner complaints are *silent* empty panels and *silent* write failures. Every workflow-backed tile header renders:

- **freshness**: relative last-refresh time, from the last ledger row;
- **per-source chips**: one ok/error dot per data node in the bound workflow (the ledger row's per-node outcomes), with the error message on hover;
- **deep link**: chip click → the run ledger row (the statusUrl contract from AUTOMATION-SUBSTRATE decision 13).

A failed refresh keeps the last-good content painted and turns the chip red — never an empty panel.

---

## 3. EDITMODE — Tweakable Artifact Parameters (zero LLM round-trips)

Adopted from open-codesign's shipped spec (research #15), fitted to the WidgetFrame sandbox:

- The model embeds ONE marker-fenced JSON block in the artifact's inline script: `/*EDITMODE-BEGIN*/ {…} /*EDITMODE-END*/`. Keys map **1:1 to `:root` CSS custom properties** (sans `--`); values are CSS strings. The visual-output skill gains the authoring protocol (declare tunables once; ≤8 params — more signals poor CSS-variable hygiene and overwhelms the UI).
- The renderer (`WidgetFrame` / tile) parses the block into `EditModeParam[]` — `{key, label, type: color|range|select|toggle, default, min?, max?, step?, unit?, options?}` — and derives typed controls in a fold-out tweak rail.
- Live edits go parent→iframe via batched `postMessage({type:'__edit_mode_set_keys', edits})` — the sandboxed iframe (`allow-scripts`, null origin) applies them to CSS custom properties client-side. **Zero LLM round-trips.**
- **Save** reads live values back (`getPropertyValue`), rewrites the block into the source, and writes a **new artifact version** via the existing update path. Values persist across revisions; color formats normalized (picker hex vs oklch).
- Trap list carried over verbatim: preserve param values across skeleton revisions; the LLM generates the initial block, the **renderer owns persistence**.

Repeated tweaking of the same param is a LEARNING-FLYWHEEL signal (promote into the artifact's defaults / a design lesson) — emitted as a proposal, never auto-applied.

---

## 4. Annotate Mode — Element-Anchored Corrections

For visual artifacts (HTML widgets, rendered designs, screenshots): describing changes in text is the slowest loop in design iteration. Annotate mode closes it:

- Toggling annotate on a `WidgetFrame`/tile injects a small annotation script via `widgetSrcdoc` (same injection seam as the theme tokens — nothing new crosses the sandbox boundary except one more `postMessage` vocabulary).
- The user clicks elements; each click captures **scope metadata** `{selector, tag, outerHTML (capped), parent context}` with selector priority `data-testid` → `id` → class chain (excluding utility-class noise) → `nth-child` (open-codesign's click-to-revise contract), plus a freeform note per annotation. Screenshots/images get coordinate-box annotations instead of selectors.
- Annotations compose into ONE structured correction directive — a fenced block of element-anchored instructions — dispatched to whatever owns the artifact: a chat message (via the existing C32 refresh-injection path, extended with the directive body) for chat-born widgets, or a `needs_input`-style guidance note for design-loop deliverables (landing in the loop's `guidance.txt`, which the design kind already consumes).
- The correction is **data with provenance**, not executed UI: the receiving agent regenerates the skeleton; annotate mode itself never mutates the artifact.

---

## 5. The Generative-UI Layer

### 5.1 Typed component registry

`web/src/ui/genui/registry.ts` — the same one-`register()`-call discipline as the content registry (`ui/content/registerBuiltins.ts`):

```ts
defineComponent({
  name: 'StatTile',            // registry key the DSL references
  description: '…',            // feeds the generated prompt section
  props: z.object({ … }),      // Zod schema — key ORDER is the positional-arg contract
  component: StatTile,          // token-driven React component (ratchet applies)
  group: 'Data' | 'Layout' | 'Forms' | 'Charts',
})
```

Bundled core set (small — every component costs prompt space): Stack/Card/Tabs layout, StatTile/Table/List data, Bar/Line/Spark charts, Form/Input/Select/Slider/Button, Callout, Timeline. Chart components follow the dataviz conventions already in the design system.

**App extension**: an installed app's manifest `ui` block gains a `components` entry (module exporting `register(lib)` calls), loaded through the existing `appSdk` host-module map and gated by manifest permissions — apps extend the ONE shared library with per-group prompt notes. This rides the app platform's existing manifest/permissions model; it is NOT a new provider type, so `PROVIDER_TYPES` and the type-handler set are untouched (the #47 guard is not in play — stated to prevent a future author "helpfully" adding one side).

### 5.2 Streaming renderer alongside markdown

A new widget block kind on the EXISTING parse seam: `<widget kind="genui">` carrying a line-oriented DSL (`id = Component(args…)`, forward references legal, top-down generation so structure paints before data — the thesys-openui shape at ~half the tokens of JSON). `parseWidgetBlocks` (`ui/widget/blocks.ts`) already handles streaming/unclosed trailing blocks; the genui kind reuses that, and the renderer re-parses per chunk.

**Controlled rendering is the safety model**: output is validated against the registry — unknown components, missing required props, and unresolved refs are **dropped, not fatal** (no null holes); every component renders inside the host React tree (not an iframe) precisely *because* only registered, schema-validated components with typed props can render. Typed, LLM-friendly validation errors (`unknown-component`, `missing-required`, `excess-args`) are surfaced back for one-shot self-correction. Raw HTML keeps going to the sandboxed iframe path — the two kinds never mix trust levels.

**Prompt generation is mechanical**: `library.prompt()` derives the authoring section (per-component signature lines from schema key order, grouped sections with steering notes, 1-2 few-shot examples) and exposes it via a small endpoint so the visual-output skill and workflow node prompts embed the CURRENT registry — hand-maintained component docs are banned (they drift).

### 5.3 `visualize(data, hint)` — one agency-free primitive

`visualize(data, hint) → genui DSL` — the two-step pattern: the reasoning agent produces *data*; a separate no-tools generation step renders it. One shared mechanism behind:

- run-cockpit summaries (fold Run Ledger events → visualize),
- dashboard tiles (a workflow's render step when no skeleton exists yet),
- inbox digests,
- "chart this" / "show me X as a table" chat asks.

Model resolution: `one_shot_completion` on the **`reasoning` use-case axis** (chat sub-category, `llm_helpers.py:275`) — never chat/code_tools, which returns the NativeAgentRuntime (recon invariant). Tools disabled by construction; output constrained to the registry DSL and validated per §5.2. Exposed as an MCP tool (`visualize`) in the artifacts tool family and as a WORKFLOWS-V2 node type for pipelines.

### 5.4 Widget trees feeding actions back into execution

Registry components may declare actions; activation emits **dual payloads** (thesys contract): `llmFriendlyMessage` (rich — full form state, machine-bound) + `humanFriendlyMessage` (the short label the transcript shows). Routing by producer:

- **chat-born widgets** → continue-conversation: the action becomes the next user turn (via the existing `ne:launch-chat` / chat-injection paths), `humanFriendlyMessage` rendered as the visible user message — no more form-submit-as-ugly-JSON;
- **workflow-emitted widgets** (a skill or workflow node emits a genui tree as a gate's prompt) → the action resolves the run's wait/gate node through AUTOMATION-SUBSTRATE's resume-target path — closing the loop from generated UI back into execution;
- **tile widgets** → actions run through the tile's bound workflow (re-fire with bound args), subject to the trigger's frozen capability set — a rendered button can never introduce actions the trigger didn't declare (the frozen action-set invariant applies to UI-originated fires too).

The typed-questionnaire widget (planner grill, needs-input inbox) becomes one registry consumer among many — one component family, many producers.

---

## 6. Layered Surface Overlay (L0/L1/L2) + Safe Mode

Once agents can generate UI, the failure to prevent is an agent-rewritten surface bricking the app. The overlay makes that structurally impossible:

- **L0 — core**: the shipped `web/dist` bundle. Immutable at runtime; the ratchet + build pipeline own it.
- **L1 — app**: app-contributed pages/components (existing `uiPages` + §5.1 component modules), loaded through the appSdk host map, removable by disabling the app.
- **L2 — user/agent**: agent-rewritten or user-customized surface files (custom tile skins, genui layout overrides) under `~/.personalclaw/surfaces/`, resolved last.

Resolution is **replace-vs-compose per surface kind**: component registrations COMPOSE (an L2 registration may add, never shadow, an L0 component name — shadowing core primitives is refused at register time); tile skins and artifact skeletons REPLACE (highest layer wins, versioned like artifacts). Every L1/L2 load is error-boundaried (the `safe()` pattern from the tool-renderer registry — a broken layer falls through, never blanks the surface).

**Safe-mode recovery route**: `#/dashboard?safe=1` (and a `--safe-surfaces` gateway flag) forces `maxLayer=0` — pure L0, no app modules, no user overlays, tiles rendered as inert links. Because L0 is immutable and the safe route is part of L0, agent-rewritten UI **cannot** brick the app: the recovery path never routes through anything an agent can touch.

Scope honesty: this is an overlay for the *ambient surfaces this plan creates* (tiles, genui, skins) — it is not a general FE plugin system, and it does not permit rewriting core pages.

---

## 7. Menu-Bar Companion (macOS tray)

The approval-latency bottleneck: unattended work is only as fast as the human notices a needs-input pause. A thin, native menu-bar micro-surface — deliberately NOT a desktop app:

### 7.1 What it shows

- **Live run progress**: active loops/runs (`GET /api/loops` — the `uLoops` surface), rendered as compact rows (kind icon, title, status dot, done/total from the same fold the FE uses).
- **Pending approvals**: `GET /api/approvals` rows with **one-click Approve / Deny** → `POST /api/approvals/{id}/{action}` — byte-identical to what the dashboard Action Center calls.
- **Needs-input items**: loops in `needs_input` with their `pending_question`, deep-linking into the browser (`#/loops/<id>` / `#/code/<id>` — the SdlcRef contract).
- A menu-bar badge count = pending approvals + needs-input (the same aggregation the Projects nav badge uses today).

### 7.2 How it connects (thin shell — no new backend)

- ONE connection to the multiplexed `/api/ws`, filtering `approval`, `approval_resolved`, `update_progress`, `chat_status`, `notification` envelopes **as refetch signals** (the DashboardLive contract — the tray never expects payload-carrying events; it refetches the two GET endpoints on signal, debounced).
- Auth: `X-Session-Key: tray:ui` against the local gateway — one machine, loopback only. No push infrastructure, no relay, no accounts.
- Notifications remain gated by `notification_allowed()` — the tray renders the same feed the notification bell does; muting in Settings mutes the tray. **One delivery gate, ever.**
- Gateway-down state renders honestly (grey icon + "gateway offline"), with the same backoff-reconnect discipline as `useChatSocket`.

### 7.3 Packaging — through the app platform

Ships as a first-party app using the manifest's existing client-install seam: `platform: {os: ["darwin"], installMode: "client"}`. The app's install step drops a small native menu-bar binary (Swift menu-bar extra or equivalent single-binary shell; NOT Electron — the whole point is a <10MB always-on presence) plus a LaunchAgent for login start; uninstall removes both. Its manifest `permissions.api` declares exactly the endpoints above; `permissions.events` declares the WS envelope filter. No `provider` block — it contributes no providers; it is a pure client of existing surfaces, and the app platform's permission model is what scopes it.

---

## 8. Disposition Table

| Surface | Verdict | Detail |
|---|---|---|
| `DashboardPage.tsx` fixed layout | **KEPT** | The clean break holds. One additive Pinned band (§1.2); empty registry ⇒ identical page. No grid, no per-widget layout persistence, first-party widgets stay hard-imported |
| `DashboardLive.tsx` | **KEPT — the data seam** | Tiles live inside the provider; `artifact_update`-family envelopes join the debounced refetch set. Signals, not payloads |
| `WidgetFrame.tsx` | **EXTENDED** | Gains pin-to-dashboard (§1.3), EDITMODE tweak rail (§3), annotate toggle (§4). Sandbox model unchanged |
| `blocks.ts` / `widgetSrcdoc.ts` | **EXTENDED** | New `kind="genui"` on the existing parse seam (§5.2); srcdoc gains the annotate script + EDITMODE postMessage vocabulary. Raw-HTML widgets keep the iframe path |
| C32 chat refresh (`refresh artifact … in place`) | **KEPT — the attended path** | Conversational refreshes stay chat-mediated; §2 adds the unattended leg beside it, replacing nothing |
| `mcp_artifacts.py` `artifact_update` + `PATCH /api/artifacts/{slug}` | **KEPT** | The `artifact-update` **action provider** (§2.2) is a sibling entry point into the same store code path — one write path, three doors (MCP / HTTP / action) |
| `artifacts/registry.py` provider seam | **KEPT — the content pointer** | Tiles resolve slugs through `get_provider()`; the plan never binds to `NativeArtifactProvider` directly |
| `pages/settings/bento.tsx` | **UNTOUCHED** | The surviving "bento" is Settings-home chrome; it is not this plan's tile system and must not be conflated with it |
| SdlcProgressCard | **UNTOUCHED (pattern donor)** | Stays the hard-coded tool-output→live-card case; §5's registry does not absorb it (its REST-polling lifecycle is deliberately bespoke) |
| Notification delivery (`notification_allowed()`) | **KEPT — THE gate** | Tray, tiles, and genui all render the existing feed; no second delivery path is built |
| `ui/content/contentTypes.ts` | **KEPT (pattern + consumer)** | The genui registry copies its one-`register()`-call discipline; non-widget artifact tiles render through its preview capability |

---

## 9. What We Deliberately Do NOT Build

- **No resurrection of the customizable grid.** No x/y coordinates, no drag-canvas, no per-user layout engine — the retirement (`DashboardPage.tsx:24`) was correct about *chrome*; this plan reopens only *content* (artifact tiles, one band, size hints).
- **No widget marketplace / tile store.** Tiles are the user's own artifacts. Apps contribute *components* (§5.1) through the existing manifest, not tiles.
- **No background tile refresh while unviewed.** `view`-trigger semantics only — pull beats push for personal dashboards (the practitioner who deleted his nightly cron is the proof case). Users who want scheduled synthesis have clock triggers; that is the substrate's business.
- **No LLM in the steady-state refresh path.** Skeleton regeneration is an explicit, versioned, user-visible act.
- **No iframe-rendered genui.** Registry components render in the host tree because controlled rendering (registered components + schema validation + drop-invalid) IS the sandbox; raw HTML stays in the real iframe sandbox. The two never mix.
- **No general FE plugin system.** The L0/L1/L2 overlay covers ambient surfaces only; core pages are not rewritable.
- **No Windows/Linux tray, no mobile app, no push relay.** macOS menu-bar first (the user's machine); the tray is loopback-only by construction.
- **No second WS, no payload-carrying dashboard events, no state library** — the existing fabric (one multiplexed WS + refetch signals + module caches) carries everything.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Re-creating the retired bento's failure mode (layout fiddling > value) | One band, flow layout, size hints only; no coordinates; empty registry = today's page; the first-party layout is never editable |
| Tile refresh storms / cost creep | `view` triggers never fire unviewed; ledger-only weight + per-trigger rate caps + visible per-refresh cost (§2.3); LLM-free steady state |
| Silent tile failures (the live-artifacts top complaint) | Freshness + per-source error chips + last-good-content-stays-painted + ledger deep link (§2.4) |
| Rendered genui as an injection surface | Controlled rendering: registry-restricted output, schema validation, drop-invalid, no raw HTML in the host tree; actions limited to declared vocabulary; tile-originated fires bound by the trigger's frozen capability set (§5.4) |
| Agent-rewritten surface bricks the app | L0 immutable + error-boundaried layer loads + `?safe=1` maxLayer=0 route that never touches agent-writable files (§6) |
| Write-on-view hazard (live-artifacts' documented failure) | Refresh workflows inherit the substrate's creation-time capability allowlist — view/clock-fired runs default read-only; write-capable refresh requires the explicit opt-in badge |
| EDITMODE block corruption across revisions | Renderer owns persistence; save rewrites the block as a NEW artifact version (restore covers regressions); param cap 8 |
| Tray drifts into a second app | Thin-shell discipline: two GET endpoints + one POST + WS signals; no local state beyond a debounce cache; packaged/permissioned through the app manifest (§7.3) |
| Registry prompt bloat | Small core set; per-group notes; app components load behind their group; prompt generated mechanically so pruning is one `register()` removal |
| api.ts merge-conflict surface (2000-line flat file) | New endpoints land in one contiguous `// ── dashboard tiles` block; genui types live in `ui/genui/`, not api.ts |

---

## Provider & Config Plug-in Map

Where each new piece plugs into the pluggable-provider architecture — nothing invents a parallel extension path:

- **`artifact-update` action provider** (§2.2): implements `ActionProvider`, registered via `action_providers/registry.py:register_action_provider` among the core-native set, **AND added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`)** — the create/update validation allowlist (skipping this is the known rejection bug-class). `supports_dry_run=True`; settings schema follows the action-provider conventions (enums over free text, absolute paths).
- **Artifact access** goes through the **artifact provider registry** (`artifacts/registry.py:get_provider`) — tiles, EDITMODE saves, and the action provider all resolve slugs via the `ArtifactProvider` ABC seam, so a future non-native artifact backend inherits the whole surface.
- **View triggers** are AUTOMATION-SUBSTRATE's `view` kind — this plan is their first consumer, not their owner. Tile refresh workflows dispatch through the action registry exactly as every trigger kind does; refresh runs inherit the substrate's `headless` profile + frozen capability allowlist.
- **The tray companion is an app**: manifest `platform: {os: ["darwin"], installMode: "client"}`, `permissions.api` + `permissions.events` scoping, install/uninstall lifecycle via `app_manager.py`. No provider block, no new provider type. **No entry is added to `PROVIDER_TYPES`** anywhere in this plan — and if a future revision ever adds one, it must land together with its `_TypeHandler` (the `test_manifest_types_match_handlers` / #47 guard).
- **App-contributed genui components** ride the manifest `ui` block + the `appSdk` host-module map + manifest permissions — the same seam `uiPages` uses. FE-side registration mirrors `ui/content/registerBuiltins.ts`.
- **`visualize` model resolution**: `one_shot_completion(use_case=…)` mapping to the **`reasoning`** chat sub-category via `active_models.json` — never the chat/code_tools axis (NativeAgentRuntime). Exposed as an MCP tool in the `mcp_artifacts` module (already in `mcp_core._TOOL_MODULES`) — no new tool category.
- **New config = an `AmbientConfig` section**, wired through the FOUR points: (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (omission = silently dropped), (c) `to_dict()` serialization, (d) the PATCH `_EDITABLE_CONFIG` allowlist + FE `api.ts`/Settings panel for runtime-editable knobs. Fields: `tiles_enabled`, `max_tiles` (default 12), `default_refresh_ttl_secs`, `genui_enabled`, `surfaces_max_layer` (the safe-mode knob), `tray_enabled`.
- **Memory vs knowledge boundary (user directive)**: tiles and digests that persist synthesized content write to the **knowledge store** (`personalclaw.knowledge.*` — user items); EDITMODE-repetition and tile-usage learning signals are **proposals into the memory subsystem** owned by LEARNING-FLYWHEEL (harness mechanics). Neither surface ever writes the other's store, and `knowledge_*` names in this plan always mean `knowledge.db`.
- **Design tokens**: registry components use `design/tokenRegistry.ts` tokens exclusively (the lint ratchet is at zero and stays there); artifact-HTML theming continues through `widgetSrcdoc` TOKEN_ALIASES.
- **FE lifecycle events**: any new SSE lifecycle event this plan's surfaces emit MUST be appended to `RUN_LIFECYCLE` (`useRunStream.ts`) — EventSource silently drops unregistered types (recon invariant).

---

## Implementation Effort

**~6 sessions** (tile band + chatless refresh can start on WORKFLOWS-V2 Slices 0-2; `view`-trigger binding upgrades in place when AUTOMATION-SUBSTRATE step 8 lands):

- **Session 1 — Composable home**: `dashboard_tiles.json` store + `/api/dashboard/tiles` CRUD + Pinned band in `DashboardPage` (inside `DashboardLiveProvider`) + pin-to-dashboard on `WidgetFrame` + SWR paint + agent-propose tool. `AmbientConfig` four-point wiring.
- **Session 2 — Chatless refresh**: layout/data split render transform (workflow node) + `artifact-update` action provider (+ `ALLOWED_HOOK_PROVIDERS`) + ttl-mode refresh endpoint + freshness/error chips + ledger cost surfacing. View-mode binding stubbed behind the ttl fallback.
- **Session 3 — Artifact iteration**: EDITMODE protocol end-to-end (skill authoring rules, renderer parse, tweak rail, postMessage batch, save-as-version) + annotate mode (srcdoc script, selector capture, correction-directive dispatch to chat + design-loop guidance).
- **Session 4 — Generative UI core**: component registry + bundled component set + streaming `kind="genui"` renderer with drop-invalid validation + mechanical prompt generation endpoint + `visualize` MCP tool + workflow node (reasoning axis).
- **Session 5 — Actions + extension + overlay**: dual-payload action routing (continue-conversation / gate resolution / tile re-fire) + app-contributed components via manifest `ui` + L0/L1/L2 overlay + `?safe=1` recovery route + typed-questionnaire consumer.
- **Session 6 — Tray companion**: menu-bar app (native shell, WS signal client, approvals/runs rows, one-click resolve, deep links, offline state) + app-platform packaging (client installMode, LaunchAgent, permissions) + badge aggregation.

## Success Criteria

1. A widget produced in chat can be pinned to the home in two clicks; the tile survives gateway restart, paints instantly from cache, and refreshes on view past TTL — with the refresh appearing as a ledger-only row carrying its (near-zero) token cost.
2. A steady-state tile refresh makes **zero LLM calls** (verified by the ledger row) and is layout-stable across 20 consecutive refreshes with changing data.
3. Killing the bound data source turns the tile's source chip red with the error on hover and a working deep link to the ledger row — the last-good content stays painted; nothing renders empty silently.
4. An empty tile registry renders a byte-identical dashboard to today's; the retired grid stays retired (no coordinates anywhere in `dashboard_tiles.json`).
5. Dragging an EDITMODE color/range control restyles the artifact live with zero network requests; Save produces a new artifact version whose restore round-trips exactly.
6. Click-annotating two elements on a rendered design and submitting produces ONE correction message containing both element anchors (selector + context), and a design loop consumes it as guidance without a manual retype.
7. A `visualize` call with tools "enabled" in its input is impossible by construction (no tool plumbing exists on that path), resolves through the `reasoning` axis, and an adversarial data payload containing an unknown component name renders everything else and drops the unknown line with a typed error.
8. An installed app registers a genui component that appears in generated UIs; disabling the app removes it; an app attempting to shadow a core component name is refused at registration.
9. With a deliberately broken L2 surface file in place, the dashboard still renders (error boundary) and `#/dashboard?safe=1` renders pure L0 — verified by corrupting every L2 file.
10. A form widget emitted by a workflow gate, when submitted, resolves the run's wait node (the run advances) and the transcript shows the `humanFriendlyMessage`, not raw JSON.
11. An approval raised while the browser is closed appears in the menu bar within the WS debounce window; one click approves it; the parent run proceeds — round-trip measured under 5 seconds. Muting notifications in Settings mutes the tray (one gate).
12. A trigger-fired tile-refresh run attempting an action outside its frozen capability set fails as a typed ledger record (the write-on-view hazard is structurally closed).
