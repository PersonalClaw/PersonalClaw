# AMBIENT-SURFACES — atomic plans

**Source plan:** [`AMBIENT-SURFACES`](../plans/AMBIENT-SURFACES.md)  
**Code:** `AS`  
**Source status:** proposed

9 atoms, all todo. AS-1 (composable home / dashboard-as-views) is the spine most others build on. AS-3, AS-4, AS-7 are independent. AS-6 gated on AUTOMATION-SUBSTRATE; AS-8 gated on INBOX-NOTIFICATIONS-UNIFICATION S1-2.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `AS-1` | ✅ | Composable home: dashboard-as-views registry + Overview preset + pinning + AmbientConfig | — | dashboard_views.json store + /api/dashboard/views CRUD (presets read-only) exist; locked "Overview" preset renders byte-identical to today's dashboard when the registry is empty; artifact tiles are artifact:<slug> refs with size+order only (no x/y coordinates anywhere); pin-to-dashboard control on WidgetFrame saves-then-POSTs a tile; cached SWR paint via useCachedData; dashboard_tile_propose tool writes added_by:agent rows rendering with an accept/dismiss chip; AmbientConfig section wired through all 5 points (dataclass+_meta, load(), to_dict(), _EDITABLE_CONFIG PATCH allowlist, Settings control) and test_config_roundtrip passes. |
| `AS-2` | ✅ | Chatless refresh: layout/data split render transform + ttl refresh + freshness/error chips | `AS-1` | a pinned tile in ttl mode refreshes via a deterministic LLM-free render transform (WF2 node interpolating {{...}} bindings into the stored skeleton); a steady-state refresh makes zero LLM calls confirmed by a ledger-only row carrying near-zero token cost + duration; tile header renders relative freshness + one ok/error dot per data node with error-on-hover + a deep link to the ledger row; a failed refresh keeps last-good content painted and turns the chip red (never an empty panel). View-trigger binding is a later in-place ttl→view upgrade (EXT:AUTOMATION-SUBSTRATE step 8) and is NOT required to complete this atom. |
| `AS-3` | ✅ | Artifact iteration: EDITMODE tweak controls + click-annotation corrections | — | dragging an EDITMODE color/range control restyles the artifact live with zero network requests (batched postMessage(__edit_mode_set_keys) into the sandboxed iframe); Save reads live values back, rewrites the marker-fenced /*EDITMODE-BEGIN*/ block, and writes a new artifact version whose restore round-trips exactly; visual-output SKILL documents the ≤8-param authoring protocol; toggling annotate captures selector+context per clicked element (data-testid→id→class→nth-child priority) and click-annotating two elements produces ONE correction directive carrying both anchors, dispatched to chat via the C32 refresh-injection path or into a design loop's guidance.txt. |
| `AS-4` | ✅ | Generative-UI core: typed component registry + streaming genui renderer + visualize primitive | — | ui/genui/registry.ts with defineComponent + bundled core component set (all token-driven; tokenLint ratchet stays at zero); <widget kind="genui"> renders through the existing parseWidgetBlocks seam in the host React tree with drop-invalid validation (unknown-component/missing-required/excess-args surfaced for self-correction, no null holes); library.prompt() derives the authoring section mechanically behind a small endpoint; visualize resolves via one_shot_completion on the reasoning axis (llm_helpers.py:275) with tools disabled by construction, exposed as an MCP tool in mcp_artifacts and as a WF2 node type; an adversarial payload naming an unknown component renders everything else and drops that line with a typed error. |
| `AS-5` | ✅ | Widget action bridge: extract useWidgetActionBridge, route non-chat hosts, harden | `AS-1` | web/src/ui/widget/useWidgetActionBridge.ts extracted with ChatPage behavior byte-identical; clicking a [data-action] in an artifact-library preview (chatEmbeds) and in the tile band lands the [UI] turn in a chat session via the ne:launch-chat path; the child→parent wire contract (widget-height/widget-action/widget-error; reserved __edit_mode_* parent→child) is documented with a test fixture; isTrusted and e.source spoof fixtures produce no turn; oversized payloads clip at 16 KiB with an honest …truncated marker. |
| `AS-6` | ⬜ | Genui action routing + app-contributed components + L0/L1/L2 surface overlay + safe mode | `AS-4`, `AS-5`, `EXT:AUTOMATION-SUBSTRATE:resume-target path for gate resolution + frozen capability set on tile re-fire` | genui component actions emit dual payloads (llmFriendlyMessage + humanFriendlyMessage); a chat-born widget action becomes the next user turn showing humanFriendlyMessage (not raw JSON); a workflow-gate-emitted form widget submission resolves the run's wait node so the run advances; tile-widget actions re-fire the bound workflow within the trigger's frozen capability set; an installed app registers a genui component that appears in generated UIs and is removed on disable, and an attempt to shadow a core component name is refused at register time; each L1/L2 layer load is error-boundaried and #/dashboard?safe=1 (plus a --safe-surfaces gateway flag) forces maxLayer=0 pure-L0. |
| `AS-7` | ⬜ | macOS menu-bar tray companion (thin client app) | — | a first-party app with platform:{os:[darwin], installMode:client} renders live run rows (GET /api/loops), pending approvals (GET /api/approvals) with one-click Approve/Deny (POST /api/approvals/{id}/{action}), and needs-input deep links (#/loops/<id>); ONE /api/ws connection consumed as refetch signals (never payloads) with backoff-reconnect; a badge = pending approvals + needs-input; muting notifications in Settings mutes the tray (single notification_allowed gate); gateway-offline renders a grey "gateway offline" state; install drops the native menu-bar binary + a login LaunchAgent and uninstall removes both; manifest permissions.api/events scope it, with no provider block and no new PROVIDER_TYPES entry. |
| `AS-8` | ⬜ | Mission Control preset: four attention lanes with inline resolution | `AS-1`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:S1-2 kind registry + inbox-as-attention-store` | a locked "Mission Control" view renders four attention lanes (Needs-approval / Your-turn / Working / Idle) sourced from the unified attention store; approving from a lane resolves the approval via the existing /api/approvals actions; a pending question answered from a card (options as actionable buttons) unblocks its loop. |
| `AS-9` | ✅ | Agent-worlds seam: AgentActivityFeed contract + useAgentActivity hook + first-party world | `AS-1` | the AgentActivityFeed contract is documented and a useAgentActivity() hook folds GET /api/loops + chat session states + subagent states, refreshed by existing WS envelopes (chat_status/sessions/subagent*/update_progress) as signals only; one first-party modern (WebGL/high-craft canvas) world renders live states from the hook alone (no private endpoints) with smooth state interpolation and a passing prefers-reduced-motion static-layout audit; an APP-PLATFORM-EVOLUTION coordination note for app-contributed worlds is added as a doc note (not code). |

## Atom scopes

### `AS-1` — Composable home: dashboard-as-views registry + Overview preset + pinning + AmbientConfig

**Status:** todo

§1 The Composable Home — Pinned Tiles Band (§1.1 tile registry, §1.2 placement, §1.3 pinning, §1.4 tile rendering + data seam); Amendment 2026-07-26 round 2 (a) Dashboard-as-views, task A2-1; Provider & Config Plug-in Map (AmbientConfig 5-point wiring). Note: A2-1 supersedes §1.2's single-band placement with named views.

**Done when:** dashboard_views.json store + /api/dashboard/views CRUD (presets read-only) exist; locked "Overview" preset renders byte-identical to today's dashboard when the registry is empty; artifact tiles are artifact:<slug> refs with size+order only (no x/y coordinates anywhere); pin-to-dashboard control on WidgetFrame saves-then-POSTs a tile; cached SWR paint via useCachedData; dashboard_tile_propose tool writes added_by:agent rows rendering with an accept/dismiss chip; AmbientConfig section wired through all 5 points (dataclass+_meta, load(), to_dict(), _EDITABLE_CONFIG PATCH allowlist, Settings control) and test_config_roundtrip passes.

### `AS-2` — Chatless refresh: layout/data split render transform + ttl refresh + freshness/error chips

**Status:** done

§2 Live Artifacts — Chatless Refresh (§2.1 layout/data split, §2.2 artifact-update action provider [INHERITED — landed WORKFLOWS-V2 Slice 9b, registered + hook-allowlisted; verify/consume], §2.3 refresh execution + cost honesty, §2.4 freshness + per-source error chips).

**Done when:** a pinned tile in ttl mode refreshes via a deterministic LLM-free render transform (WF2 node interpolating {{...}} bindings into the stored skeleton); a steady-state refresh makes zero LLM calls confirmed by a ledger-only row carrying near-zero token cost + duration; tile header renders relative freshness + one ok/error dot per data node with error-on-hover + a deep link to the ledger row; a failed refresh keeps last-good content painted and turns the chip red (never an empty panel). View-trigger binding is a later in-place ttl→view upgrade (EXT:AUTOMATION-SUBSTRATE step 8) and is NOT required to complete this atom.

### `AS-3` — Artifact iteration: EDITMODE tweak controls + click-annotation corrections

**Status:** done

§3 EDITMODE — Tweakable Artifact Parameters (zero LLM round-trips); §4 Annotate Mode — Element-Anchored Corrections.

**Done when:** dragging an EDITMODE color/range control restyles the artifact live with zero network requests (batched postMessage(__edit_mode_set_keys) into the sandboxed iframe); Save reads live values back, rewrites the marker-fenced /*EDITMODE-BEGIN*/ block, and writes a new artifact version whose restore round-trips exactly; visual-output SKILL documents the ≤8-param authoring protocol; toggling annotate captures selector+context per clicked element (data-testid→id→class→nth-child priority) and click-annotating two elements produces ONE correction directive carrying both anchors, dispatched to chat via the C32 refresh-injection path or into a design loop's guidance.txt.

### `AS-4` — Generative-UI core: typed component registry + streaming genui renderer + visualize primitive

**Status:** todo

§5.1 Typed component registry; §5.2 Streaming renderer alongside markdown; §5.3 visualize(data, hint) — one agency-free primitive.

**Done when:** ui/genui/registry.ts with defineComponent + bundled core component set (all token-driven; tokenLint ratchet stays at zero); <widget kind="genui"> renders through the existing parseWidgetBlocks seam in the host React tree with drop-invalid validation (unknown-component/missing-required/excess-args surfaced for self-correction, no null holes); library.prompt() derives the authoring section mechanically behind a small endpoint; visualize resolves via one_shot_completion on the reasoning axis (llm_helpers.py:275) with tools disabled by construction, exposed as an MCP tool in mcp_artifacts and as a WF2 node type; an adversarial payload naming an unknown component renders everything else and drops that line with a typed error.

### `AS-5` — Widget action bridge: extract useWidgetActionBridge, route non-chat hosts, harden

**Status:** done

Amendment 2026-07-26 round 1 (interactive in-chat widgets), tasks T5-A1 and T5-A2; §5.4's raw-HTML widget path (the leg §5.4 leaves in the iframe). Bridge exists un-extracted today (WidgetFrame.tsx→ne:widget-action→ChatPage.tsx).

**Done when:** web/src/ui/widget/useWidgetActionBridge.ts extracted with ChatPage behavior byte-identical; clicking a [data-action] in an artifact-library preview (chatEmbeds) and in the tile band lands the [UI] turn in a chat session via the ne:launch-chat path; the child→parent wire contract (widget-height/widget-action/widget-error; reserved __edit_mode_* parent→child) is documented with a test fixture; isTrusted and e.source spoof fixtures produce no turn; oversized payloads clip at 16 KiB with an honest …truncated marker.

### `AS-6` — Genui action routing + app-contributed components + L0/L1/L2 surface overlay + safe mode

**Status:** todo

§5.4 Widget trees feeding actions back into execution (dual payloads; continue-conversation / gate resolution / tile re-fire); §5.1 app-extension (manifest ui components via appSdk host-module map); §6 Layered Surface Overlay (L0/L1/L2) + Safe Mode; typed-questionnaire consumer.

**Done when:** genui component actions emit dual payloads (llmFriendlyMessage + humanFriendlyMessage); a chat-born widget action becomes the next user turn showing humanFriendlyMessage (not raw JSON); a workflow-gate-emitted form widget submission resolves the run's wait node so the run advances; tile-widget actions re-fire the bound workflow within the trigger's frozen capability set; an installed app registers a genui component that appears in generated UIs and is removed on disable, and an attempt to shadow a core component name is refused at register time; each L1/L2 layer load is error-boundaried and #/dashboard?safe=1 (plus a --safe-surfaces gateway flag) forces maxLayer=0 pure-L0.

### `AS-7` — macOS menu-bar tray companion (thin client app)

**Status:** todo

§7 Menu-Bar Companion (§7.1 what it shows, §7.2 how it connects — thin shell/no new backend, §7.3 packaging through the app platform).

**Done when:** a first-party app with platform:{os:[darwin], installMode:client} renders live run rows (GET /api/loops), pending approvals (GET /api/approvals) with one-click Approve/Deny (POST /api/approvals/{id}/{action}), and needs-input deep links (#/loops/<id>); ONE /api/ws connection consumed as refetch signals (never payloads) with backoff-reconnect; a badge = pending approvals + needs-input; muting notifications in Settings mutes the tray (single notification_allowed gate); gateway-offline renders a grey "gateway offline" state; install drops the native menu-bar binary + a login LaunchAgent and uninstall removes both; manifest permissions.api/events scope it, with no provider block and no new PROVIDER_TYPES entry.

### `AS-8` — Mission Control preset: four attention lanes with inline resolution

**Status:** todo

Amendment 2026-07-26 round 2 (a) Dashboard-as-views, task A2-2 (Mission Control preset over the unified attention store).

**Done when:** a locked "Mission Control" view renders four attention lanes (Needs-approval / Your-turn / Working / Idle) sourced from the unified attention store; approving from a lane resolves the approval via the existing /api/approvals actions; a pending question answered from a card (options as actionable buttons) unblocks its loop.

### `AS-9` — Agent-worlds seam: AgentActivityFeed contract + useAgentActivity hook + first-party world

**Status:** todo

Amendment 2026-07-26 round 2 (b) Agent worlds, task A2-3 (platform contributes the seam, not the scene).

**Done when:** the AgentActivityFeed contract is documented and a useAgentActivity() hook folds GET /api/loops + chat session states + subagent states, refreshed by existing WS envelopes (chat_status/sessions/subagent*/update_progress) as signals only; one first-party modern (WebGL/high-craft canvas) world renders live states from the hook alone (no private endpoints) with smooth state interpolation and a passing prefers-reduced-motion static-layout audit; an APP-PLATFORM-EVOLUTION coordination note for app-contributed worlds is added as a doc note (not code).

