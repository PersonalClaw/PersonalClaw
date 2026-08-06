# AGENT-ROUTING — atomic plans

**Source plan:** [`AGENT-ROUTING`](../plans/AGENT-ROUTING.md)  
**Code:** `AR2`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `AR2-1` | ✅ (#feature-agent-routing) | Add specialty + route_hints to AgentProfile and AgentDefinition (full round-trip on both layers) | — | both fields survive a save→load→save round-trip on BOTH agent layers (config AgentProfile via dataclass+_meta+load() mapping+to_dict; marketplace AgentDefinition via from_dict+_UPDATABLE+validate ≤1024-char cap); regression test mirroring test_agent_voice.py green |
| `AR2-2` | ✅ (#feature-agent-routing) | agents/routing.py pure classifier: eligible_candidates + classify (keyword 0.7 → embedding 0.62 + 0.1 margin) + embedding cache with staleness check | `AR2-1` | pure-function tests pass: keyword hit, embedding hit, low-confidence None, no-embedder falls back to keyword-only, reserved agents never candidates; classifier never raises |
| `AR2-3` | ✅ (#feature-agent-routing) | suggest_for_send hook in api_chat + routing_suggestion WS broadcast + SEL agents.routing_suggest log | `AR2-2` | a default-agent + persistent-memory send matching a specialist broadcasts routing_suggestion; a temporary/incognito or explicit-agent session never does; a raising classifier never breaks the send; frequency cap (1/5 turns) and agents_routing.enabled honored |
| `AR2-4` | ✅ (#feature-agent-routing) | AgentsRoutingConfig 5-point wiring + suppression store (entity_settings/agent_routing.json) + dismiss/unmute/status routes | `AR2-2` | test_config_roundtrip.py green; dismiss×1 → 24h cooldown honored; dismiss×3 → muted-until-unmute; corrupt store file fails OPEN (nothing suppressed + warn); routes registered before /api/agents/{name} using §2.2 error envelope |
| `AR2-5` | ✅ (#feature-agent-routing) | RoutingChip component: WS-driven pill, Route→setSessionAgent+toast, dismiss, FEEDBACK-SIGNAL double-write | `AR2-3`, `AR2-4`, `EXT:FEEDBACK-SIGNAL:routing_suggestion target-kind + routing_pair producer-kind vocabulary and record_feedback API (plan 58, shipped 2026-07-27)` | chip renders non-blockingly on routing_suggestion for the open session, Route re-targets the session via existing switch path, dismiss suppresses, both actions double-write feedback (routing_suggestion target / routing_pair producer), auto-clears on send/switch, typecheck + vitest green |
| `AR2-6` | ✅ (#feature-agent-routing) | Authoring fields: Specialty + Routing hints in AgentForm.tsx | `AR2-1` | Specialty + Routing-hints fields (with comma-separated-utterances hint text) round-trip through agent create/edit via draft/empty/toDraft/payload + SavedAgent type |
| `AR2-7` | ✅ (#feature-agent-routing) | Agent routing settings block (enabled / min confidence / cooldown) in Settings → Chat | `AR2-4` | toggling enabled off stops suggestions immediately with no restart, via the agents_routing.* PATCH allowlist |
| `AR2-8` | ⬜ | Muted-state row + Unmute affordance on the agent detail page | `AR2-4` | a muted agent shows its muted state on AgentDetail.tsx and an Unmute control (routingUnmute API, already shipped) restores it; muted/unmute state reflected via routingStatus |

## Atom scopes

### `AR2-1` — Add specialty + route_hints to AgentProfile and AgentDefinition (full round-trip on both layers)

**Status:** done (PR #feature-agent-routing)

Design S1 (routing metadata); Contracts C1; Task breakdown Session 1 T1.1

**Done when:** both fields survive a save→load→save round-trip on BOTH agent layers (config AgentProfile via dataclass+_meta+load() mapping+to_dict; marketplace AgentDefinition via from_dict+_UPDATABLE+validate ≤1024-char cap); regression test mirroring test_agent_voice.py green

### `AR2-2` — agents/routing.py pure classifier: eligible_candidates + classify (keyword 0.7 → embedding 0.62 + 0.1 margin) + embedding cache with staleness check

**Status:** done (PR #feature-agent-routing)

Design S1 (the classifier); Contracts C2; Task breakdown Session 1 T1.2

**Done when:** pure-function tests pass: keyword hit, embedding hit, low-confidence None, no-embedder falls back to keyword-only, reserved agents never candidates; classifier never raises

### `AR2-3` — suggest_for_send hook in api_chat + routing_suggestion WS broadcast + SEL agents.routing_suggest log

**Status:** done (PR #feature-agent-routing)

Design S1 (the suggestion emission); Contracts C2 suggest_for_send + C4 WS event; Task breakdown Session 1 T1.3

**Done when:** a default-agent + persistent-memory send matching a specialist broadcasts routing_suggestion; a temporary/incognito or explicit-agent session never does; a raising classifier never breaks the send; frequency cap (1/5 turns) and agents_routing.enabled honored

### `AR2-4` — AgentsRoutingConfig 5-point wiring + suppression store (entity_settings/agent_routing.json) + dismiss/unmute/status routes

**Status:** done (PR #feature-agent-routing)

Design S2 (suppression memory) + Config; Contracts C3, C4, C5; Task breakdown Session 1 T1.4

**Done when:** test_config_roundtrip.py green; dismiss×1 → 24h cooldown honored; dismiss×3 → muted-until-unmute; corrupt store file fails OPEN (nothing suppressed + warn); routes registered before /api/agents/{name} using §2.2 error envelope

### `AR2-5` — RoutingChip component: WS-driven pill, Route→setSessionAgent+toast, dismiss, FEEDBACK-SIGNAL double-write

**Status:** done (PR #feature-agent-routing)

Design S2 (the chip); Task breakdown Session 2 T2.1; Risks (FEEDBACK-SIGNAL coordination)

**Done when:** chip renders non-blockingly on routing_suggestion for the open session, Route re-targets the session via existing switch path, dismiss suppresses, both actions double-write feedback (routing_suggestion target / routing_pair producer), auto-clears on send/switch, typecheck + vitest green

### `AR2-6` — Authoring fields: Specialty + Routing hints in AgentForm.tsx

**Status:** done (PR #feature-agent-routing)

Design S1 (authoring surface); Task breakdown Session 2 T2.2 (form fields)

**Done when:** Specialty + Routing-hints fields (with comma-separated-utterances hint text) round-trip through agent create/edit via draft/empty/toDraft/payload + SavedAgent type

### `AR2-7` — Agent routing settings block (enabled / min confidence / cooldown) in Settings → Chat

**Status:** done (PR #feature-agent-routing)

Design S2 (Config); Contracts C5; Task breakdown Session 2 T2.3

**Done when:** toggling enabled off stops suggestions immediately with no restart, via the agents_routing.* PATCH allowlist

### `AR2-8` — Muted-state row + Unmute affordance on the agent detail page

**Status:** todo

Task breakdown Session 2 T2.2 (muted-state row + Unmute on the agent detail page); Execution log DEVIATION 2026-07-27

**Done when:** a muted agent shows its muted state on AgentDetail.tsx and an Unmute control (routingUnmute API, already shipped) restores it; muted/unmute state reflected via routingStatus

