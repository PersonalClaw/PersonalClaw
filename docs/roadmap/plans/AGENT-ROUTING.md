# Plan: Agent Routing — Suggest the Right Specialist, Never Route Silently

**Status:** DESIGNED — created 2026-07-26 (roadmap rev 12; owner ask: sibling-platform gap analysis greenlight — "automatic routing to the right specialist," owner-decided **suggest-first**)
**Created:** 2026-07-26
**Wave:** 2
**Depends on:** nothing hard (builds on the shipped agent marketplace + session-agent binding + the UnifiedEmbedder). Coordinates with INBOX-NOTIFICATIONS-UNIFICATION (42 — this plan deliberately does NOT emit inbox items; the chip is ephemeral chat chrome, so there is no attention-contract dependency), AUTONOMY-GUARDRAILS (any future *auto*-routing graduation would ride its earned-autonomy machinery — forward hook only, out of scope here), WORKFLOWS-V2 (the SOP surfacing engine in `workflows/surfacing.py` is the calibration precedent this plan mirrors, not a dependency).
**Scope:** ~2 sessions. When the user sends a message in a **default-agent** chat and an installed specialist agent is a clearly better fit, a small **non-blocking chip** appears above the composer: "Route to \<agent\>?" — one click re-targets the session to that agent (via the existing `POST /api/chat/sessions/{session}/agent` switch path); dismissing it is remembered per agent so it never nags. Classification is **deterministic-first** (keyword/phrase overlap against per-agent routing metadata), **embedding-similarity second** (cosine via the active embedding binding when one is bound), **LLM never in the hot path**. No suggestion when confidence is low, when the user explicitly picked an agent for the session, or in temporary/incognito sessions. **Silent auto-routing is explicitly OUT of scope** — a future earned-autonomy mechanism (post AUTONOMY-GUARDRAILS trust accrual) may graduate a high-precision pair to auto; this plan only leaves the SEL breadcrumbs such a mechanism would need. **Soul guardrail:** routing metadata lives on the provider-agnostic `AgentDefinition`/`AgentProfile` layer — never on a vendor runtime; the classifier is pure functions over that metadata + the one unified embedding path (`embedding_providers.registry.get_active_embed_fn()`), with zero per-provider logic. The suggestion is a *proposal* (propose-don't-write): nothing about the session changes until the user clicks. Class **B** note: the dismissal-suppression counters are new persisted state (`entity_settings/agent_routing.json`) — pre-LIFECYCLE-DOCTRINE this ships as a plain clean break under the pre-1.0 banner (no gate, no migration; additive file, tolerant reads).

---

## Context (code recon, 2026-07-26)

- **Agent definitions** (`src/personalclaw/agents/marketplace.py`): `AgentDefinition` dataclass — `name, description, model, system_prompt, voice, skills, provider_entry, mcp_servers, source, created_at, updated_at, provider`. Loader-allowlist gotcha is documented in-file twice (`voice`, `provider`): a new field MUST be read in `from_dict` (line ~81) *and* added to `_UPDATABLE` in `LocalAgentMarketplace.update` (line ~228) or it is silently dropped on every round-trip. Stored as `~/.personalclaw/agents/<name>/agent.json`, atomic-written. There is **no routing metadata today** — `description` is the only intent-ish text.
- **Config-side agent profiles** (`src/personalclaw/config/loader.py:809` `AgentProfile`): `provider, provider_agent, acp_mode, default_dir, memory_store, description, system_prompt, voice, model, approval_mode, skills, tools, triggers, source` — the chat actually binds to THESE (config `agents` dict), with the marketplace synced in via `POST /api/agents/sync` (`dashboard/handlers/agents.py:656`). The same silent-drop hazard exists in `load()`'s explicit `AgentProfile(...)` mapping at `loader.py:1895` (the `voice`/`triggers` comments there mark prior instances). `RESERVED_AGENT_NAMES` (`agents/defaults.py:360`) marks background workers (lite/loop/coder/planners) that must never be routing candidates.
- **How a chat binds an agent** (`dashboard/chat_handlers.py`): `api_chat` (`POST /api/chat`, `chat_handlers.py:54`) accepts `agent` in the body; a session with `session.agent` set rejects mismatches (409, line 123-128) — so "the user explicitly picked an agent" is *observable* as a non-empty `session.agent` at send time. Switching is `api_chat_session_agent` (`POST /api/chat/sessions/{session}/agent`, line 1296): sets `session.agent`, clears ACP overrides, resolves `resolve_agent_bindings(cfg, matched)` for `workspace_dir`, `await state.sessions.reset(...)`, persists via `conversation_log.update_metadata`. Every assignment/denial emits SEL `agent_assignment` via `_emit_agent_assignment` (`dashboard/chat_utils.py:352`). Session model `_ChatSession` (`dashboard/state.py:184`): `agent`, `memory_mode` (`persistent|incognito|temporary`, `VALID_MEMORY_MODES` at state.py:181), `.blocks_reads` property (`memory_mode == "temporary"`, state.py:527).
- **Embedding** (`src/personalclaw/knowledge/embedder.py`): `UnifiedEmbedder` wraps `embedding_providers.registry.get_active_embed_fn()`; `create_embedder_from_config` returns `None` when nothing is bound (embedding gracefully off). Provider-agnostic — the one sanctioned path.
- **The calibrated precedent** (`src/personalclaw/workflows/surfacing.py`): SOP surfacing is exactly the deterministic-first/embedding-second shape this plan needs — `_keyword_score` (per-phrase word-overlap over comma-separated `match_text`, mirroring `SkillsLoader._MIN_TRIGGER_OVERLAP = 0.7` in `skills/loader.py:18`), `_cosine`, `DEFAULT_MATCH_THRESHOLD = 0.62` (config-tunable `workflows.match_threshold`, `loader.py:1343`), cached `match_embedding` + `embedding_model` staleness check (`workflows/models.py:58-60`), and the never-break-a-turn `surface_for_turn_sync` wrapper. This plan reuses the *pattern and thresholds*, not the module.
- **Background-compute precedent** (`src/personalclaw/suggestions.py`): `SuggestionsCache` + fire-and-forget `asyncio.create_task` registered on `state._background_tasks`, `GET /api/suggestions` (`dashboard/server.py:485`). Routing classification is far cheaper (no LLM) so it runs inline per-send, but the cache-on-state pattern (`get_suggestions_cache`) is the model for caching candidate embeddings.
- **Chip render sites** (`web/src/pages/ChatPage.tsx`, 3368 lines): `SessionSkillsReview` (`web/src/pages/chat/SessionSkillsReview.tsx`) is the exemplar — a subtle pill above the composer, mounted at `ChatPage.tsx:1814` next to the composer, re-checked on `refreshKey` (turn-settled epoch), renders `null` when empty. `SuggestionChips` (`ChatPage.tsx:129`) shows the pill styling vocabulary. Composer selection state: `selection: ComposerValue` (`ChatPage.tsx:519`; type in `web/src/ui/composer/types.ts:18`); agent switch goes through `api.setSessionAgent` (`web/src/lib/api.ts:1469`). WS events arrive via `useChatSocket` (`web/src/lib/useChatSocket.ts`), dispatched by `m.type` in ChatPage (e.g. `activity_event` at line 732).
- **Per-entity persistence precedent**: `entity_settings/<name>.json` via `_load_entity_settings`/`_save_entity_settings` (`providers/entity_routes.py:31/42`, atomic-write, tolerant reads) — used by legibility dismissals (`legibility/discover.py:296`). This is where dismissal counters belong per INTEGRATION-ARCHITECTURE §2.1 (per-entity user preference, not operator config).
- **Gap:** no routing metadata on agents, no classifier, no suggestion surface, no suppression memory. The orchestrator skill (`agent.orchestrator_skill`, `loader.py`) delegates *within* a turn via subagents — a different mechanism (LLM-driven, in-turn); this plan is pre-turn, deterministic, and user-consented. They coexist; neither replaces the other.

## Design

- **S1 — Routing metadata + the classifier (backend).** `AgentProfile` and `AgentDefinition` gain two optional fields: `specialty` (one-line "what this agent is the specialist for") and `route_hints` (comma-separated example utterances / trigger phrases, the same authoring vocabulary as `Workflow.match_text`). Both wired through the full round-trip on both layers (dataclass + `_meta`, `load()` mapping at `loader.py:1895`, `from_dict` + `_UPDATABLE` in `marketplace.py`, `to_dict`, agents API create/update handlers, `AgentForm.tsx`). A new pure module `agents/routing.py` classifies a message against eligible candidates: **stage 1** per-phrase keyword overlap over `route_hints` (gate 0.7, mirroring `_keyword_score`); **stage 2** cosine over cached specialty embeddings (gate 0.62, mirroring `DEFAULT_MATCH_THRESHOLD`) via `get_active_embed_fn()` — skipped entirely when no embedding model is bound; **no stage 3** — the LLM is never in the hot path. Candidate embeddings (`specialty + route_hints`) are computed lazily on first use + on metadata change, cached in-process on `DashboardState` (the `SuggestionsCache` pattern) with the `embedding_model` staleness check from `workflows/models.py`. Eligibility filter *before* scoring: candidate is a non-reserved native profile with non-empty `specialty` or `route_hints`; session is default-agent (`session.agent` empty or == the seeded default), `memory_mode == "persistent"`, and the (default→candidate) pair is not suppressed.
- **S1 — The suggestion emission.** Hooked into `api_chat` (`chat_handlers.py`) right after the send is accepted, best-effort and non-blocking (a `try/except` returning None, the `surface_for_turn_sync` discipline — a classifier error can never break a turn). On a match above gate with a clear margin (top score − runner-up ≥ 0.1), broadcast a WS event `routing_suggestion` `{session, agent, specialty, score, method}` via `state.broadcast_ws` (`state.py:1644`). No inbox item, no notification — ephemeral chrome only.
- **S2 — The chip (frontend) + suppression memory.** ChatPage handles `routing_suggestion` in its existing `useChatSocket` dispatch and renders a `RoutingChip` above the composer (the `SessionSkillsReview` slot at `ChatPage.tsx:1814`): "**\<agent\>** handles this — route this chat to it?" with **Route** and a dismiss ✕. Route → `api.setSessionAgent(session, agent)` (the existing switch path — bindings, reset, persistence, SEL all come free) + a confirmation toast (`notify` from `appSdk.tsx:350`); the chip never auto-fires. Dismiss → `POST /api/agents/routing/dismiss {agent}`; the backend increments a per-target-agent counter in `entity_settings/agent_routing.json` — 1 dismissal = 24h cooldown for that agent, 3 cumulative = permanently muted until the user re-enables in the agent's detail page. Chip also auto-clears on next send or agent switch. One suggestion max per session per N turns (default: once per 5 user turns) so it never nags even before dismissal.
- **Config:** `agents_routing` section — `enabled: bool = True` (kill-switch), `min_confidence: float = 0.62`, `cooldown_hours: float = 24.0` — 5-point wired (`_EDITABLE_CONFIG` entries `agents_routing.enabled` etc.), surfaced as a small block in Settings → Chat (`web/src/pages/settings/ChatPanel.tsx`).
- **Audit:** every emitted suggestion + user response logs SEL (`sel().log_api_access(operation="agents.routing_suggest", outcome="suggested|accepted|dismissed", ...)`) — the precision record a future earned-autonomy graduation (forward hook, NOT in scope) would consume.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Routing metadata (additive fields, both agent layers)
```python
# config/loader.py AgentProfile (+ _meta; + load() mapping at ~:1895; + to_dict via asdict)
specialty: str = ""     # "What this agent specializes in" — one line, drives the embedding
route_hints: str = ""   # comma-separated example utterances (the Workflow.match_text vocabulary)

# agents/marketplace.py AgentDefinition (+ from_dict read; + _UPDATABLE; + validate: ≤1024 chars each)
specialty: str = ""
route_hints: str = ""
```
Both optional, default `""` = "not a routing candidate" (opt-in per agent; zero behavior change for existing agents).

### C2 — Classifier (`src/personalclaw/agents/routing.py`, new — pure functions)
```python
@dataclass(frozen=True)
class RouteCandidate:
    agent: str          # AgentProfile config key
    specialty: str
    score: float
    method: str         # "keyword" | "embedding"

def eligible_candidates(cfg: AppConfig) -> list[str]: ...
    # non-reserved (agents/defaults.py:is_reserved_agent), specialty or route_hints non-empty,
    # provider resolvable; NEVER the session's current agent

def classify(message: str, candidates: list[...], *, min_confidence: float = 0.62) -> RouteCandidate | None: ...
    # stage 1: per-phrase keyword overlap over route_hints (gate 0.7, mirrors workflows/surfacing._keyword_score)
    # stage 2: cosine vs cached specialty embedding via get_active_embed_fn() (gate min_confidence);
    #          None when no embed fn bound. Returns None on low confidence or margin < 0.1. Never raises.

def suggest_for_send(state, session, message) -> RouteCandidate | None: ...
    # the api_chat hook: eligibility (default-agent + persistent memory_mode + not suppressed
    # + per-session frequency cap) → classify → SEL log → WS broadcast. Best-effort, never blocks the send.
```

### C3 — Suppression store (`entity_settings/agent_routing.json`; via `_load_entity_settings`/`_save_entity_settings`)
```json
{"dismissals": {"<agent>": {"count": 2, "last_dismissed_at": 1753500000.0}},
 "muted": ["<agent>"]}
```
Tolerant reads (missing/corrupt → `{}` = nothing suppressed — fail OPEN per §2.7, availability surface). Class B (new persisted file): plain clean break under the pre-1.0 banner.

### C4 — Routes + WS event (§2.2 error envelope for new routes)
```python
POST /api/agents/routing/dismiss   {"agent": "<name>"}        # bump counter / mute at 3
POST /api/agents/routing/unmute    {"agent": "<name>"}        # from the agent detail page
GET  /api/agents/routing/status                               # {enabled, muted:[...], dismissals:{...}}
# WS (broadcast_ws): {"type": "routing_suggestion",
#   "data": {"session", "agent", "specialty", "score", "method"}}
```

### C5 — Config (5-point wiring per §2.1; `tests/test_config_roundtrip.py` covers)
```python
@dataclass
class AgentsRoutingConfig:
    enabled: bool = True        # kill-switch — chip never renders when off
    min_confidence: float = 0.62
    cooldown_hours: float = 24.0
# _EDITABLE_CONFIG: "agents_routing.enabled" {"type": "bool"},
#   "agents_routing.min_confidence" {"type": "float", "min": 0.3, "max": 0.95},
#   "agents_routing.cooldown_hours" {"type": "float", "min": 0.0, "max": 720.0}
```

### Integration points
- **Calls:** `embedding_providers.registry.get_active_embed_fn()` (the one embedding path), `agents/defaults.py::is_reserved_agent`, `providers/entity_routes.py::_load_entity_settings/_save_entity_settings`, `state.broadcast_ws` (`state.py:1644`), `sel().log_api_access` (§2.3), frontend `api.setSessionAgent` (`api.ts:1469` → the existing `api_chat_session_agent` switch path — reused untouched).
- **Called by:** `api_chat` (`chat_handlers.py:54` — the one hook site), ChatPage's `useChatSocket` dispatch, `AgentForm.tsx` (metadata authoring), agent detail page (unmute).
- **Storage owned:** `entity_settings/agent_routing.json`; the two metadata fields inside existing `config.json` agents + `agent.json` files (additive; tolerant `from_dict` reads mean old files load unchanged).
- **Zero telemetry:** scores/dismissals never leave the instance; SEL is the only record.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Metadata + classifier + emission (backend)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Add `specialty` + `route_hints` to `AgentProfile` (dataclass + `_meta` + `load()` mapping at `loader.py:1895` + `to_dict`) and `AgentDefinition` (`from_dict` + `_UPDATABLE` + `validate` length caps) — the documented loader-allowlist gotcha is the test target | `src/personalclaw/config/loader.py`, `src/personalclaw/agents/marketplace.py`, `dashboard/handlers/agents.py` (create/update pass-through) | both fields survive a save→load→save round-trip on BOTH layers (regression test mirrors `tests/test_agent_voice.py`'s shape) |
| T1.2 | `agents/routing.py`: `eligible_candidates`, `classify` (keyword 0.7 → embedding 0.62 + 0.1 margin, `None` when no embed fn), embedding cache on `DashboardState` with `embedding_model` staleness check | `src/personalclaw/agents/routing.py` (new) | pure-function tests: keyword hit, embedding hit, low-confidence None, no-embedder falls back to keyword-only, reserved agents never candidates |
| T1.3 | `suggest_for_send` hook in `api_chat` (after send accepted, try/except-wrapped, non-blocking) + WS `routing_suggestion` broadcast + SEL `agents.routing_suggest` log; gating: default-agent session only, `memory_mode == "persistent"` only, frequency cap, `agents_routing.enabled` | `src/personalclaw/dashboard/chat_handlers.py`, `agents/routing.py` | a send in a default chat matching a specialist broadcasts the event; a temporary session or explicit-agent session never does (tests); a raising classifier does not break the send |
| T1.4 | `AgentsRoutingConfig` 5-point wiring + `_EDITABLE_CONFIG` entries + suppression store + dismiss/unmute/status routes (§2.2 envelope) | `config/loader.py`, `dashboard/handlers/core.py`, `agents/routing.py`, `dashboard/server.py` | `test_config_roundtrip.py` green; dismiss×1 → 24h cooldown honored; dismiss×3 → muted; corrupt store file → fail-open (nothing suppressed, warn log) |
| V1 | Validation (as a user): seed two specialist agents with `route_hints`; send a matching message via curl + watch WS; verify SEL entries; verify no event for incognito/temporary and for an explicitly-agented session | — | holds |

### Session 2 — Chip UI + authoring + suppression UX

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `RoutingChip` component (the `SessionSkillsReview` pill pattern): renders on `routing_suggestion` for the open session, "Route" → `api.setSessionAgent` + toast, ✕ → dismiss endpoint; auto-clears on next send / agent switch; honors `prefers-reduced-motion` via existing MotionConfig | `web/src/pages/chat/RoutingChip.tsx` (new), `web/src/pages/ChatPage.tsx` (WS dispatch + mount at the :1814 slot), `web/src/lib/api.ts` (3 routing calls) | chip appears non-blockingly; one click re-targets the session (composer selection updates); dismiss suppresses; typecheck + vitest green |
| T2.2 | Authoring surface: `Specialty` + `Routing hints` fields in `AgentForm.tsx` (with the "comma-separated example utterances" hint text); muted-state row + Unmute on the agent detail page | `web/src/pages/agents/AgentForm.tsx`, `web/src/pages/agents/AgentDetail.tsx` | fields round-trip through create/edit; a muted agent shows its state + unmute works |
| T2.3 | Settings block (enabled / min confidence / cooldown) in Settings → Chat via the PATCH allowlist | `web/src/pages/settings/ChatPanel.tsx` | toggling `enabled` off stops suggestions immediately (no restart) |
| V2 | Validation (as a user, full loop in the dev home): author hints on an agent → chat as default → chip appears → route → conversation continues under the specialist → new chat, dismiss 3× → never again → unmute restores; inspect UI/console/network/SEL/`entity_settings/agent_routing.json` | — | holds |

## Owner tasks (real world)
1. **Author `specialty`/`route_hints` on your real installed specialists** (the dev fixtures prove the mechanism; your agents make it useful) — the plan ships the fields empty everywhere.
2. **Confirm the suppression policy** (1 dismissal = 24h, 3 = mute-until-unmute) — proposed as the never-nags floor; tune if too timid/aggressive after a week of dogfooding.
3. **Ratify the forward hook**: silent auto-routing stays out until an earned-autonomy mechanism (post AUTONOMY-GUARDRAILS) proposes graduation criteria over the SEL precision record — this plan takes no position on those criteria.

## Risks & open questions
- **Keyword-stage precision on short messages** — a 2-word message can spuriously clear the 0.7 overlap gate. Mitigation: require ≥3 words in the matched phrase for a keyword-only suggestion (the margin rule already helps); the V1 fixture includes short-message negatives.
- **Two agent layers drift** (`AgentProfile` vs `AgentDefinition`) — metadata is authored on whichever layer the user edits; the existing `/api/agents/sync` flow is a no-op stub (`_do_agents_sync`, `handlers/agents.py:662`), so this plan treats **config `AgentProfile` as the routing source of truth** (it's what chat binds) and the marketplace fields as the portable copy. If sync ever becomes real, it must carry these fields — noted for that future plan.
- **Embedding-cache invalidation** on agent metadata edit — cache keyed by `(agent, updated_at/config-mtime, embedding_model)`; a stale entry degrades to keyword (never wrong-agent-by-stale-vector), matching the `workflows` staleness discipline.
- **Open:** should ACP-discovered agents (no local metadata home) ever be candidates? Deferred — they have no `specialty` field to author; a DISCOVERY entry if demand appears.
- **FEEDBACK-SIGNAL coordination (plan 58 shipped 2026-07-27, its T3.4):** the chip's Route/dismiss handlers must DOUBLE-WRITE a feedback record — Route → `record_feedback(target_kind="routing_suggestion", target_id=<suggestion id>, verdict="up", producer_kind="routing_pair", producer_id=f"{default}->{candidate}")`, dismiss → `verdict="down"` — so routing-pair accuracy appears in `GET /api/feedback/producers` with zero extra UI. The store, closed vocabulary (`routing_suggestion` target kind + `routing_pair` producer kind), and API are live; wire at this plan's T3.x action handlers when the chip lands.
