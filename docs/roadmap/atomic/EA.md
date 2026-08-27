# EXTERNAL-ACCESS — atomic plans

**Source plan:** [`EXTERNAL-ACCESS`](../plans/EXTERNAL-ACCESS.md)  
**Code:** `EA`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `EA-1` | ✅ | Shared inbound access seam — widen plan-41 substrate to 5 surfaces + per-client identity | `EXT:MCP-READONLY-INBOUND:inbound/ substrate (auth/caps/audit/mcp_http/tools) to widen`, `EXT:AUTONOMY-GUARDRAILS:incident.json flag + guard_flag pattern` | New ExternalAccessConfig top-level section wired through all 4 points (dataclass+_meta, load(), to_dict(), _EDITABLE_CONFIG runtime subset — tokens/public_url NOT PATCH-editable); per-surface tokens (openai/mcp/a2a/capture/bridge) via save_credential with ≥32-byte + not-dashboard-token refusal; `~/.personalclaw/inbound_clients.json` (0600, atomic_write) with label/token_hash/surfaces/agent/tools/scope/rate_overrides/disabled; constant-time client lookup with bindings-as-pins (arg override = 403 + SEL); per-client token-bucket (1rps/burst20/4 concurrent) + result caps + auto-disable-on-repeat-breach; layered kill switches (master + per-surface + per-client + AUTONOMY-GUARDRAILS incident check, fail-closed parse); single fence_untrusted response wrapper; inbound_audit.jsonl (2× trim) + SEL on security events; Settings→External Access skeleton; inbound_clients.json + sender_trust.json join export/snapshot sets; test_config_roundtrip passes. |
| `EA-2` | ✅ | OpenAI-compatible inbound dialect (/v1/*) — agent-as-model + audio aliases | `EA-1`, `EXT:AUTONOMY-GUARDRAILS:headless SafetyProfile + SpendMeter (Budget scope=trigger) + ModelCallGuard` | POST /v1/chat/completions maps model=personalclaw/<agent> AND bare <agent> to an agent via resolve_agent_bindings; SSE chat.completion.chunk translation with [DONE] + usage block, non-stream returns one completion; `user` field + X-PersonalClaw-Session header → inbound:<client_id>:<sha8> session (stateless unless persistent_sessions); inbound: added to _STATELESS_PREFIXES + guardrails headless classification; tool calls execute server-side (never surfaced as tool_calls deltas), needs-approval returns dashboard-pointer terminal message; unknown agent → 404 in OpenAI error shape with stable code; /v1/audio/* thin aliases over /api/voice/synthesize + /api/stt/transcribe via resolve_provider_for_use_case (tts-1/whisper-1 cosmetic, resolve_voice(name) seam); per-client SpendMeter budgets via ModelCallGuard; unmodified `openai` SDK holds multi-turn convo + curl /v1/audio/speech returns bound-TTS audio (Success Criteria 2); zero provider names in dialect code paths. |
| `EA-3` | ✅ (##75/#83/#116) | Curated read-only MCP server (/mcp) — INHERITED from plan 41 | — | Shipped under sibling plan 41 MCP-READONLY-INBOUND (src/personalclaw/inbound/mcp_http.py + tools.py, mounted at dashboard/server.py:410, fail-closed per __init__.py). EA does NOT rebuild this; per-client subsetting/scope-pins ride EA-1's bindings. Catalogued so the DAG shows §3 satisfied externally. |
| `EA-4` | ✅ | Self-describing MCP control bridge — loopback FE semantic actions | `EA-1` | Loopback-only (allow_remote-exempt) bridge on a random ephemeral port via its own aiohttp runner; control_bridge.json (0600, atomic_write, rewritten each boot, deleted on clean shutdown) carries {port,url,token_ref,schema_version,actions_digest}; action registry emits {name,params_schema,sideEffect,requiresConfirmation,description} for open_cockpit/read_transcript/list_automations/create_task/toggle_automation/run_trigger_dry/notify; confirm-flagged actions return {status:needs_confirmation,confirm_token} + DashboardState.notify, resolved by user in dashboard or `personalclaw inbound confirm <token>`; write actions call the same internal FE handlers (no parallel mutation path); no destructive actions in v1; every call audit-lined (Success Criterion 9). |
| `EA-5` | 🟡 every clause met on `main` + PR #2121 | External-agent capture proxy (/capture/v1) + telemetry import | `EA-1`, `EXT:LEARNING-FLYWHEEL:learning.db staging tier + capture_hygiene fence rule (records without it; mining hookup is one adapter)` | /capture/v1/chat/completions + /capture/v1/messages forward verbatim to a client-record `upstream` ProviderEntry (standard credential order) with passthrough fallback; loopback-only always; SSE piped via a dedicated streaming client that pre-flights guard.evaluate against an operator-visible upstream host allowlist (web/render.py:76 pattern); stream-first, record-async off hot path (asyncio.to_thread), recording failure never fails the forwarded request; capture/<id>.jsonl (0600) turn records + full-content sidecar; read-skill attribution via skill_path_map; redact()→fence_untrusted(source=capture:<client_id>) BEFORE persist; new `capture` staging source in learning.db (records durably even if flywheel steps 1-3 absent — hookup is one adapter); retention prune at capture.retention_days (default 30) on curator tick; `personalclaw capture import <file> --format jsonl\|json\|sse` + POST /capture/import normalize→redact→fence→stage, idempotent by content hash, malformed-lines skipped-and-counted (Success Criteria 5,6). |
| `EA-6` | ⬜ | Local A/B replay harness — evidence generator on captured sessions | `EA-5`, `EXT:LEARNING-FLYWHEEL:proposal queue + LEARN-R2 held-out replay gate manifests; eval/judge.py LLMJudge` | Curator-cadence background pass extracts replay_cases (tool-free-preferring, ≤3/session, provenance-pointed) from capture sessions; given a pending skill/template proposal, run each case twice via one_shot_completion(use_case=background) — baseline vs candidate — scored with eval/judge.py:LLMJudge (eval_judge binding, parse-failure→0 reject); verdict {cases,candidate_mean,baseline_mean,verdict:improved\|neutral\|regressed} attached to the proposal's evidence manifest and rendered on the Proposal Inbox card (NOT a gate — human still accepts; feeds LEARN-R2); composes one_shot_completion+LLMJudge directly, NEVER eval/runner.py (env-mutation hazard); replay LLM spend meters via ModelCallGuard under a learning-scope budget, exhaustion defers replays with labeled cards (Success Criterion 7). |
| `EA-7` | ⬜ | Sender-trust substrate — channels' inbound-identity half + DM pairing | `EXT:CHANNEL-EXPANSION:channel trust seam (plan 40, precedes EXTERNAL-ACCESS per hard rules)`, `EXT:PROVIDER-BOUNDARY:apps/slack-channel allowlist migration` | channel_transports/trust.py with one store sender_trust.json (atomic_write) {transport:{sender_id:{state,display,paired_at,source}}} + check_sender(transport,sender_id)->TrustDecision consulted at the single gateway channel-ingestion chokepoint BEFORE any agent session; DM pairing codes (8-char, 1h, max 3 pending) via `personalclaw channel pair <transport>` + Settings, unknown-no-code senders dropped-and-counted (one SEL line, zero agent tokens); Slack app allowlist.py data migrated in + its Allow/Deny buttons refitted to write this store (behavior preserved, one migration); ChannelTransportProvider ABC unchanged, new transports inherit trust with zero code (Success Criterion 8). NOTE: the trust seam is OWNED by plan 40 (CHANNEL-EXPANSION) and PRECEDES this plan (plan header line 11 + workspace hard rules) — this atom completes/consumes it, resolving the doc/plan drift on who authors channel_trust.py. |
| `EA-8` | ⬜ | A2A gateway (inbound agent card + tasks→WorkflowRun) + a2a-call outbound provider | `EA-1`, `EXT:WORKFLOWS-V2:Slices 0-3 run engine + journal (inbound task mapping; outbound a2a-call is independent)` | GET /a2a/agent-card serves a card whose skills are workflow templates with a2a_published:true (default false, per-template toggle in template detail UI); POST /a2a/tasks maps an A2A task onto a WorkflowRun (v2 run-start seam), streams A2A task lifecycle, returns fenced artifacts under headless profile + client budget; card mounts EMPTY until WF2 slices land; apps/a2a-action first-party app (provider type action, entity a2a, ActionProvider factory, webhook-action precedent) whose name is added to ALLOWED_HOOK_PROVIDERS (validation.py:555) or hook create/update rejects it; execute sends one A2A task via net.fetch under CONNECTOR policy + egress_policy_for deny-by-default host allowlist; selectable by all trigger kinds + workflow action nodes once registered (Success Criterion 10). |
| `EA-9` | ✅ | Headless CLI mode — `personalclaw run` one-shot scripted turns | `EA-1`, `EXT:AUTONOMY-GUARDRAILS:headless SafetyProfile classification` | `personalclaw run -p "<prompt>" [--format plain\|json\|streaming-json] [--agent] [--model] [--session] [--cwd] [--allow]` executes one turn against the local gateway, auto-starting a transient gateway (reusing the doctor readiness probe) when none runs, exits 0/nonzero per turn success; plain=final text, json=one {result,session,turns,tool_calls,tokens,duration_ms} doc, streaming-json=NDJSON of the same WS envelope frames (chat_chunk/tool_call/chat_done); inbound:cli: prefix inherits the §2.3 headless SafetyProfile (read-only defaults; --allow write grant printed to stderr); budgets ride SpendMeter scope_key=cli; --session opts into a persistent named session (default stateless); CI smoke-test recipe in docs + shell-script and GitHub-Action validation. |

## Atom scopes

### `EA-1` — Shared inbound access seam — widen plan-41 substrate to 5 surfaces + per-client identity

**Status:** done

§1 (The Inbound Access Layer): §1.1 fail-closed enablement, §1.2 per-client identity/bindings, §1.3 hard caps, §1.4 framing wrapper, §1.5 audit; §10 stores; §11 ExternalAccessConfig 4-point wiring. Session 1.

**Done when:** New ExternalAccessConfig top-level section wired through all 4 points (dataclass+_meta, load(), to_dict(), _EDITABLE_CONFIG runtime subset — tokens/public_url NOT PATCH-editable); per-surface tokens (openai/mcp/a2a/capture/bridge) via save_credential with ≥32-byte + not-dashboard-token refusal; `~/.personalclaw/inbound_clients.json` (0600, atomic_write) with label/token_hash/surfaces/agent/tools/scope/rate_overrides/disabled; constant-time client lookup with bindings-as-pins (arg override = 403 + SEL); per-client token-bucket (1rps/burst20/4 concurrent) + result caps + auto-disable-on-repeat-breach; layered kill switches (master + per-surface + per-client + AUTONOMY-GUARDRAILS incident check, fail-closed parse); single fence_untrusted response wrapper; inbound_audit.jsonl (2× trim) + SEL on security events; Settings→External Access skeleton; inbound_clients.json + sender_trust.json join export/snapshot sets; test_config_roundtrip passes.

### `EA-2` — OpenAI-compatible inbound dialect (/v1/*) — agent-as-model + audio aliases

**Status:** todo

§2 (Dialect 1): §2.1 /v1/chat/completions + /v1/models, §2.2 /v1/audio/{speech,transcriptions,voices} aliases, §2.3 headless-profile safety composition; Amendment 2026-07-26 T2-A1/T2-A2. Session 2 (the 'doorway').

**Done when:** POST /v1/chat/completions maps model=personalclaw/<agent> AND bare <agent> to an agent via resolve_agent_bindings; SSE chat.completion.chunk translation with [DONE] + usage block, non-stream returns one completion; `user` field + X-PersonalClaw-Session header → inbound:<client_id>:<sha8> session (stateless unless persistent_sessions); inbound: added to _STATELESS_PREFIXES + guardrails headless classification; tool calls execute server-side (never surfaced as tool_calls deltas), needs-approval returns dashboard-pointer terminal message; unknown agent → 404 in OpenAI error shape with stable code; /v1/audio/* thin aliases over /api/voice/synthesize + /api/stt/transcribe via resolve_provider_for_use_case (tts-1/whisper-1 cosmetic, resolve_voice(name) seam); per-client SpendMeter budgets via ModelCallGuard; unmodified `openai` SDK holds multi-turn convo + curl /v1/audio/speech returns bound-TTS audio (Success Criteria 2); zero provider names in dialect code paths.

### `EA-3` — Curated read-only MCP server (/mcp) — INHERITED from plan 41

**Status:** done (PR ##75/#83/#116)

§3 (Dialect 2): streamable-HTTP /mcp mount + 5 curated read-only tools (memory_recall, knowledge_search, tasks_list/task_get, speak, search_transcripts). Session 3 (MCP half).

**Done when:** Shipped under sibling plan 41 MCP-READONLY-INBOUND (src/personalclaw/inbound/mcp_http.py + tools.py, mounted at dashboard/server.py:410, fail-closed per __init__.py). EA does NOT rebuild this; per-client subsetting/scope-pins ride EA-1's bindings. Catalogued so the DAG shows §3 satisfied externally.

### `EA-4` — Self-describing MCP control bridge — loopback FE semantic actions

**Status:** done

§4 (Dialect 3): random-port loopback bridge, control_bridge.json discovery file, typed self-describing action registry, server-side requiresConfirmation flow. Session 3 (bridge half).

**Done when:** Loopback-only (allow_remote-exempt) bridge on a random ephemeral port via its own aiohttp runner; control_bridge.json (0600, atomic_write, rewritten each boot, deleted on clean shutdown) carries {port,url,token_ref,schema_version,actions_digest}; action registry emits {name,params_schema,sideEffect,requiresConfirmation,description} for open_cockpit/read_transcript/list_automations/create_task/toggle_automation/run_trigger_dry/notify; confirm-flagged actions return {status:needs_confirmation,confirm_token} + DashboardState.notify, resolved by user in dashboard or `personalclaw inbound confirm <token>`; write actions call the same internal FE handlers (no parallel mutation path); no destructive actions in v1; every call audit-lined (Success Criterion 9).

### `EA-5` — External-agent capture proxy (/capture/v1) + telemetry import

**Status:** 🟡 implementation complete; flips to `done` when PR #2121 lands. Every `done_when`
clause is met on `main` except two that #2121 carries — `POST /capture/import` and the
*operator-visible* half of the upstream host allowlist (its Settings control). The two clauses
this session closed were the last ones needing code: the `capture` staging source in
`learning.db` (the fourth cadence) and `prune()`'s call site on the curator tick.

§7 (Dialect 5 capture proxy): §7.1 dual-wire OpenAI/Anthropic proxy, §7.2 recording + fence-at-ingestion; §8 telemetry import. Session 4.

**Done when:** /capture/v1/chat/completions + /capture/v1/messages forward verbatim to a client-record `upstream` ProviderEntry (standard credential order) with passthrough fallback; loopback-only always; SSE piped via a dedicated streaming client that pre-flights guard.evaluate against an operator-visible upstream host allowlist (web/render.py:76 pattern); stream-first, record-async off hot path (asyncio.to_thread), recording failure never fails the forwarded request; capture/<id>.jsonl (0600) turn records + full-content sidecar; read-skill attribution via skill_path_map; redact()→fence_untrusted(source=capture:<client_id>) BEFORE persist; new `capture` staging source in learning.db (records durably even if flywheel steps 1-3 absent — hookup is one adapter); retention prune at capture.retention_days (default 30) on curator tick; `personalclaw capture import <file> --format jsonl|json|sse` + POST /capture/import normalize→redact→fence→stage, idempotent by content hash, malformed-lines skipped-and-counted (Success Criteria 5,6).

### `EA-6` — Local A/B replay harness — evidence generator on captured sessions

**Status:** todo

§9 (Local A/B replay harness): replay-case mining on curator cadence, current-vs-candidate replay + LLMJudge scoring, evidence-manifest attachment. Session 5.

**Done when:** Curator-cadence background pass extracts replay_cases (tool-free-preferring, ≤3/session, provenance-pointed) from capture sessions; given a pending skill/template proposal, run each case twice via one_shot_completion(use_case=background) — baseline vs candidate — scored with eval/judge.py:LLMJudge (eval_judge binding, parse-failure→0 reject); verdict {cases,candidate_mean,baseline_mean,verdict:improved|neutral|regressed} attached to the proposal's evidence manifest and rendered on the Proposal Inbox card (NOT a gate — human still accepts; feeds LEARN-R2); composes one_shot_completion+LLMJudge directly, NEVER eval/runner.py (env-mutation hazard); replay LLM spend meters via ModelCallGuard under a learning-scope budget, exhaustion defers replays with labeled cards (Success Criterion 7).

### `EA-7` — Sender-trust substrate — channels' inbound-identity half + DM pairing

**Status:** todo

§6 (Sender-trust substrate): channel_transports/trust.py store + check_sender chokepoint, DM pairing codes, per-sender allowlist Settings, Slack allowlist migration + UI refit. Session 6 (trust half).

**Done when:** channel_transports/trust.py with one store sender_trust.json (atomic_write) {transport:{sender_id:{state,display,paired_at,source}}} + check_sender(transport,sender_id)->TrustDecision consulted at the single gateway channel-ingestion chokepoint BEFORE any agent session; DM pairing codes (8-char, 1h, max 3 pending) via `personalclaw channel pair <transport>` + Settings, unknown-no-code senders dropped-and-counted (one SEL line, zero agent tokens); Slack app allowlist.py data migrated in + its Allow/Deny buttons refitted to write this store (behavior preserved, one migration); ChannelTransportProvider ABC unchanged, new transports inherit trust with zero code (Success Criterion 8). NOTE: the trust seam is OWNED by plan 40 (CHANNEL-EXPANSION) and PRECEDES this plan (plan header line 11 + workspace hard rules) — this atom completes/consumes it, resolving the doc/plan drift on who authors channel_trust.py.

### `EA-8` — A2A gateway (inbound agent card + tasks→WorkflowRun) + a2a-call outbound provider

**Status:** todo

§5 (Dialect 4 A2A): inbound GET /a2a/agent-card + POST /a2a/tasks, a2a_published template flag, apps/a2a-action outbound provider + ALLOWED_HOOK_PROVIDERS. §11 provider-fidelity wiring. Session 6 (A2A half).

**Done when:** GET /a2a/agent-card serves a card whose skills are workflow templates with a2a_published:true (default false, per-template toggle in template detail UI); POST /a2a/tasks maps an A2A task onto a WorkflowRun (v2 run-start seam), streams A2A task lifecycle, returns fenced artifacts under headless profile + client budget; card mounts EMPTY until WF2 slices land; apps/a2a-action first-party app (provider type action, entity a2a, ActionProvider factory, webhook-action precedent) whose name is added to ALLOWED_HOOK_PROVIDERS (validation.py:555) or hook create/update rejects it; execute sends one A2A task via net.fetch under CONNECTOR policy + egress_policy_for deny-by-default host allowlist; selectable by all trigger kinds + workflow action nodes once registered (Success Criterion 10).

### `EA-9` — Headless CLI mode — `personalclaw run` one-shot scripted turns

**Status:** done (PR #2011)

§9.5 (Headless CLI Mode, grok-build learning): run subcommand + 3 formatters, transient-gateway bootstrap, inbound:cli: headless classification, --allow grant flag, CI recipe docs. Session 7.

**Done when:** `personalclaw run -p "<prompt>" [--format plain|json|streaming-json] [--agent] [--model] [--session] [--cwd] [--allow]` executes one turn against the local gateway, auto-starting a transient gateway (reusing the doctor readiness probe) when none runs, exits 0/nonzero per turn success; plain=final text, json=one {result,session,turns,tool_calls,tokens,duration_ms} doc, streaming-json=NDJSON of the same WS envelope frames (chat_chunk/tool_call/chat_done); inbound:cli: prefix inherits the §2.3 headless SafetyProfile (read-only defaults; --allow write grant printed to stderr); budgets ride SpendMeter scope_key=cli; --session opts into a persistent named session (default stateless); CI smoke-test recipe in docs + shell-script and GitHub-Action validation.

