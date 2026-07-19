# ACP Agent Parity Audit — native loop vs claude-code / codex / kiro-cli

**Date:** 2026-07-14 · **Method:** code-level read of `src/personalclaw/agents/native/`, `src/personalclaw/acp/`, `src/personalclaw/llm/acp_agent.py` + `acp_session_provider.py`, `src/personalclaw/dashboard/chat_runner.py`, `src/personalclaw/session.py`, `src/personalclaw/providers/provider_bridge.py`, and the three agent apps in `apps/`. Every verdict cites the file it came from. Runtime behavior NOT executed — cells marked UNKNOWN need the follow-up as-a-user sweep (§6).

**Verdict up front:** the harness is architected so that *most* deep integration lives ABOVE the provider seam (in `chat_runner._run_chat`, which is provider-neutral) or BELOW it (in the model provider). Those features work identically for ACP agents. The real gaps cluster in four places: (1) the native **tool surface** (the in-process registry: knowledge/tasks/loops/inbox/artifacts/workflows/subagents/web tools) reaches an ACP CLI **only via the `personalclaw-core` MCP server, and only if that CLI's own config spawns it** — the host never passes `mcpServers` into `session/new` on the live paths; (2) **per-tool machinery** that lives inside `NativeAgentRuntime` (failure breaker, structural loop detection, tool retrieval, dry-run, unattended stripping, steering); (3) **learning capture** (procedural outcomes drain is native-only); (4) **per-dialect protocol capability differences** (plan mode, permission modes, effort, concurrent sessions, resume).

---

## 1. Architecture: where the seam sits

```
chat_runner._run_chat  ──────────────  provider-neutral. Consumes AgentEvent stream
  │                                    (text/thinking/tool_call/tool_result/permission/
  │                                    complete) from EITHER runtime.
  ├── SessionManager.get_or_create (session.py:788) → provider_bridge
  │       provider_kind == "" | "native"  → NativeAgentRuntime (in-process ReAct loop)
  │       provider_kind == "acp:<cli>"    → AcpAgentProvider (spawn CLI subprocess)
  │                                         or AcpSessionProvider (P9 shared connection,
  │                                         default dialect + acp_concurrent_sessions flag)
  ├── Native: ModelProvider.complete() per turn; tools invoked in-process
  └── ACP: session/prompt over JSON-RPC stdio; CLI runs its OWN loop + OWN tools
```

Key structural facts (evidence):

- **Context injection is provider-neutral.** `assemble_context(...)` builds the full turn-0 prompt (memory recall, lessons, history, episodic, skills index, knowledge, project preamble, task-mode framing as `system_prompt_suffix`) and the result is passed as the *message string* to `client.stream(full_message)` — same call for both runtimes (`chat_runner.py:1361-1388, 1466`). An ACP CLI receives all of this as prompt text; it does not receive it as a system prompt (the CLI owns its own system prompt).
- **The approval gate is provider-neutral but the native runtime enforces MORE, EARLIER.** ACP tools reach the host gate only when the CLI emits `session/request_permission` (`chat_runner.py:1763`). The native runtime enforces deny-list → task-mode → PreToolUse hooks → approval *inside the loop before any execution* (`runtime.py:711-765`), so trust/YOLO cannot bypass ask/plan/build. For ACP, the host **never forwards auto-approve modes**; only `plan` is forwarded as a native mode to Zed dialects (`chat_runner.py:1056-1066`), and unattended loops set `bypassPermissions` explicitly (`loop/manager.py:181`).
- **The event translation is single-path.** `acp/adapter.py:acp_event_to_agent_event` maps AcpEvent → the neutral AgentEvent both `AcpAgentProvider` and `AcpSessionProvider` use (`acp_session_provider.py:106-110`), so UI cards/telemetry render the same shape.

## 2. The three ACP agent apps

| | claude-code-agent | codex-agent | kiro-cli-agent |
|---|---|---|---|
| Registry entry | `acp:claude-code` | `acp:codex` | `acp:kiro-cli` |
| Adapter | `@agentclientprotocol/claude-agent-acp` (npx-provisionable), delegates to `claude` via `CLAUDE_CODE_EXECUTABLE` | `@agentclientprotocol/codex-acp`, delegates to `codex` via `CODEX_PATH` | none — `kiro-cli acp` speaks ACP natively |
| Dialect | `claude-code` (ZedAdapterDialect: int protoVersion=1, model/mode/effort via `session/set_config_option`, no agent-activation verb) | `codex` (same Zed shape) | `default` (date-string protoVersion, agents via `session/set_mode`, model via `session/set_model`, NO mode/effort axis) |
| Permission modes | native modes `default/acceptEdits/plan/dontAsk/bypassPermissions` (host forwards only `plan` + loop `bypassPermissions`) | same | none (dialect `set_mode_request` returns None) |
| Reasoning effort | via `configOptions.effort` (verbatim values) | same | none (`set_effort_request` → None) |
| Sub-agents/personas | none (one base agent; `use_runtime_prefix`) | none | `availableModes` ARE personas — the picker shows each kiro agent JSON (e.g. the `~/.aws/amazonq/cli-agents/*.json` / `~/.kiro/agents/*.json` files) |
| Concurrent sessions (P9) | NO (`supports_concurrent_sessions=False` on ZedAdapterDialect — unproven) | NO | YES (DefaultDialect proven by the 2026-07-06 spike) + `acp_concurrent_sessions` flag |
| Config hardening | opt-in `PERSONALCLAW_CC_ISOLATE=1` → isolated `CLAUDE_CONFIG_DIR`, strips `permissions.allow/ask` + `defaultMode` | none needed (every tool arrives as request_permission) | none |
| Session files / resume | `session_files_dir` unset in the bundle registration (`register_acp_cli_entry` call passes none) → `session/load` resume gated on a session file that never exists → **resume effectively dead** | same | `loadSession` capability-gated; same session-file gate applies |

Registration: each app's `create_provider` calls `personalclaw.sdk.acp.register_acp_cli_entry` (`acp_bundles/_register.py`) which publishes an `acp_agent` ProviderEntry; absent binary → nothing registered → runtime greyed out.

## 3. Binaries + auth on this machine (2026-07-14)

| Binary | Path | Auth signal |
|---|---|---|
| `claude` | `/Users/golani/.toolbox/bin/claude` | `~/.claude/settings.json` + `~/.claude.json` present → plausibly authenticated |
| `codex` | `/Users/golani/.toolbox/bin/codex` | `~/.codex/` populated (config.toml, sessions/, goals_1.sqlite) → plausibly authenticated |
| `codex-acp` (adapter) | `/Users/golani/.nvm/versions/node/v24.13.0/bin/codex-acp` | installed on disk — no npx fallback needed |
| `claude-agent-acp` (adapter) | **not on PATH** → resolution falls to `npx -y @agentclientprotocol/claude-agent-acp` (Node 24 present, so npx path viable; `create_provider(provision=True)` will try a durable install on enable) | n/a |
| `kiro-cli` | `/Users/golani/.toolbox/bin/kiro-cli` (toolbox shim) | `~/Library/Application Support/kiro-cli/` + `~/.kiro/` populated (agents/, settings/, data.sqlite3) → plausibly authenticated (Amazon-internal; may need fresh `mwinit`) |
| `kiro` | not found (only `kiro-cli`) | — |

Note: kiro persona agents exist at `~/.kiro/agents/` (10 AIPowerUserCapabilities-gpu-* files) and `~/.aws/amazonq/cli-agents/` (amzn-builder, atlas). **No `personalclaw.json` is linked into either kiro agents dir** — the harness writes its agent config (with the `personalclaw-core`/`personalclaw-schedule` MCP servers + `@Builder`) only to `~/.personalclaw/agents/personalclaw.json` (`agent.py:92-93, 1091`). Whether kiro-cli discovers it there is an UNKNOWN for the sweep; if it doesn't, the kiro runtime runs with NO PersonalClaw MCP tools.

## 4. The capability matrix

Legend: **WIRED** (works, evidence cited) · **PARTIAL** (subset; see note) · **ABSENT** (code shows it cannot reach this runtime) · **UNKNOWN** (needs runtime test). "Zed" = claude-code + codex (identical dialect shape); kiro = default dialect. Evidence in `src/personalclaw/` unless noted.

### 4a. Prompt-side context (provider-neutral — flows as message text)

| Feature | native | claude-code | codex | kiro-cli | Evidence |
|---|---|---|---|---|---|
| Memory recall injection (turn-0 context) | WIRED | WIRED | WIRED | WIRED | `assemble_context` → message string, `chat_runner.py:1361` |
| Knowledge context (@-mention + KnowledgeContextPicker `meta.knowledge`) | WIRED | WIRED | WIRED | WIRED | `_inject_knowledge_content`, `chat_runner.py:697-742` |
| Attachments/paste (extracted text prepended) | WIRED | WIRED | WIRED | WIRED | `_inject_attachment_content`, `chat_runner.py:646-694` |
| @prompt expansion (+ typed vars, snippets) | WIRED | WIRED | WIRED | WIRED | `_expand_prompt_mention`, `chat_runner.py:562-643` |
| Skills index in context + `skill_invoke`/`skill_search` execution | WIRED | PARTIAL | PARTIAL | PARTIAL | Index is prompt text (neutral). *Execution* needs `personalclaw-core` MCP (`mcp_core.py:_list_tools`) which the host does NOT pass at `session/new` — `client.py:419` sends `"mcpServers": []`. Reaches the CLI only if the CLI's own config spawns it (kiro: via `~/.personalclaw/agents/personalclaw.json` if discovered; claude/codex: only if the user configured it in `~/.claude.json`/`~/.codex/config.toml`) |
| Session-live skill drafts (`skill_remember`) | WIRED | PARTIAL | PARTIAL | PARTIAL | same MCP-reachability caveat |
| Task-mode framing (Agent/Ask/Plan/Build system_prompt_suffix) | WIRED | WIRED | WIRED | WIRED | `task_mode_framing` → `system_prompt_suffix`, `chat_runner.py:1035, 1378` |
| Agent profile system prompt / voice layer | WIRED | PARTIAL | PARTIAL | PARTIAL | native: `AgentRuntimeDefinition.system_prompt` (+`_compose_voice`, `provider_bridge.py:370`); ACP: `bindings.system_prompt` flows only as `system_prompt_override` into `assemble_context` message text — the CLI's own system prompt still dominates |
| Project binding (context preamble + cwd) | WIRED | WIRED | WIRED | WIRED | preamble `chat_runner.py:1320-1323`; cwd via `get_or_create(cwd=…)` → ACP spawn cwd (`transport.py:341`) |
| project_id → artifact stamping | WIRED | ABSENT | ABSENT | ABSENT | native-only kwarg: bridge pops `project_id` for the native builder (`provider_bridge.py:541`); `session.py:1088` comment "native runtime binds it per turn"; ACP artifact_save (via MCP if reachable) has no bound project contextvar in the CLI process |
| Persona injection (Lumon theme) | WIRED | WIRED | WIRED | WIRED | `_maybe_inject_persona` message prepend, `chat_runner.py:1315` |
| Cancelled-turn preamble re-injection | WIRED | WIRED | WIRED | WIRED | `chat_runner.py:1261-1282` |
| Compressed thread-history bootstrap (new process) | WIRED | WIRED | WIRED | WIRED | `compress_thread_history`, `chat_runner.py:1252-1260`; ACP additionally has `session/load` resume — see below |

### 4b. Approvals / permissions / safety

| Feature | native | claude-code | codex | kiro-cli | Evidence |
|---|---|---|---|---|---|
| Interactive approval cards | WIRED | WIRED | WIRED | WIRED | EVENT_PERMISSION_REQUEST path, `chat_runner.py:1763+`; dialects normalize options (`dialect.py:176-234, 373-389`) |
| trust_reads (effective-safe auto-approve) | WIRED | PARTIAL | PARTIAL | PARTIAL | gate is neutral (`chat_runner.py:1927`) but `resolve_effective_risk` downgrades on *declared* risk + read-only-bash heuristics; ACP tools carry NO declared risk_level (only native stamps it, `runtime.py:597`) → ACP effective-risk relies on name/kind/input heuristics only |
| Trust (session) / YOLO (global) auto-approve | WIRED | WIRED | WIRED | WIRED | `chat_runner.py:1958+`; ACP approval echoes the agent-offered optionId (`dialect.select_allow_option_id`) |
| Per-agent approval floor ("Always allow") | WIRED | WIRED | WIRED | WIRED | `_agent_floor_seeded`, `chat_runner.py:1138-1170` |
| Task-mode enforcement BEFORE approval (trust can't bypass) | WIRED | PARTIAL | PARTIAL | PARTIAL | native: `_guard_and_invoke` runs task-mode gate pre-approval (`runtime.py:740-747`); ACP: gate applies ONLY to tools that surface `request_permission` (`chat_runner.py:1771-1795`) — a CLI-side auto-approved tool (e.g. reads Claude allows itself) never reaches the host gate. Explicitly noted in code: "ACP runtimes … gate via their own protocol + only reach here when they request approval" |
| Plan mode → native backend plan | (host gate) | WIRED | WIRED | ABSENT | `acp_mode="plan"` forwarded for Zed dialects (`chat_runner.py:1065-1066`, `ZedAdapterDialect.set_mode_request`); kiro default dialect returns None |
| Hard deny-list (`security.is_denied`) pre-execution | WIRED | ABSENT | ABSENT | ABSENT | `runtime.py:729` native-only; ACP tools execute in the CLI process — the host can only reject at the permission prompt |
| PreToolUse hooks blocking execution | WIRED | PARTIAL | PARTIAL | PARTIAL | native: blocking, agent-scoped (`runtime.py:749-761`, wired `provider_bridge.py:415-439`); ACP: blocking only on the request_permission path (`chat_runner.py:1878+`); for CLI-auto-approved tools hooks fire *informationally after the tool already runs* (`chat_runner.py:1623-1632` "hooks are informational only") |
| PostToolUse / Stop / SessionStart / UserPromptSubmit / Error hooks | WIRED | WIRED | WIRED | WIRED | `chat_runner.py:1441-1450, 1756-1762, 2464, 2558` (neutral) |
| SEL audit of every executed tool + effective risk | WIRED | WIRED | WIRED | WIRED | EVENT_TOOL_CALL logging, `chat_runner.py:1607-1622` |
| Unattended mode (strip interactive tools + fail-fast approvals, T5) | WIRED | ABSENT | ABSENT | ABSENT | native-only: bridge pops `unattended` (`provider_bridge.py:534`), `runtime.py:350-358, 623-635`; comment at `session.py:1084`: "Native-runtime-only; the bridge pops it for other runtimes". ACP substitute = `bypassPermissions` (Zed only, `loop/manager.py:181`) — kiro has NEITHER (no mode axis + no unattended) |
| Dry-run replay (T9 observe mode) | WIRED | ABSENT | ABSENT | ABSENT | `dry_run` native-only (`provider_bridge.py:537`, `runtime.py:718-726`) |
| OS sandbox wrap of the agent process | (in-process) | WIRED | WIRED | WIRED | `wrap_argv`, `transport.py:316` |
| Isolated CLI config hardening | n/a | WIRED (opt-in) | n/a | n/a | `PERSONALCLAW_CC_ISOLATE`, `apps/claude-code-agent/provider.py:106-164` |

### 4c. Tools

| Feature | native | claude-code | codex | kiro-cli | Evidence |
|---|---|---|---|---|---|
| Filesystem/shell tools (cwd-confined + extra_tool_roots) | WIRED | PARTIAL | PARTIAL | PARTIAL | native: `NativeBuiltinToolProvider` PLATFORM (`provider_bridge.py:454-460`); ACP: the CLI's OWN file/bash tools (unconfined beyond CLI settings); `extra_tool_roots` is a native-only kwarg (`provider_bridge.py:527`) |
| Full native tool registry (knowledge/tasks/loops/inbox/memory/artifacts/workflows/subagents/web/schedule + MCP adapters) | WIRED | UNKNOWN | UNKNOWN | UNKNOWN | native: registry `_list_tool_providers()` (`provider_bridge.py:461`); ACP: reachable ONLY as the `personalclaw-core` aggregated MCP server (`mcp_core.py:918-952` aggregates artifacts/workflows/memory/subagents + core) **if the CLI spawns it** — host sends `"mcpServers": []` at `session/new` (`client.py:419, 481`; the `mcp_servers` param exists only on the P9 pool path, `acp_session_provider.py:240-247`, and no live caller passes it — `session.py:510-522` doesn't). kiro is designed to read `~/.personalclaw/agents/personalclaw.json` (tools list includes `@personalclaw-core`); claude/codex have no seeded config |
| Tool disable prefs (PT3/UT4 per-tool + per-provider) | WIRED | ABSENT | ABSENT | ABSENT | `tool_prefs.load_disabled` consumed in `runtime.py:320-347` only |
| Per-turn tool retrieval + progressive disclosure (tool_search/tool_schema) | WIRED | ABSENT | ABSENT | ABSENT | `ToolRetriever`, `runtime.py:370-395, 414-442` |
| Failure breaker (warn@3/block@5/circuit@30) | WIRED | ABSENT | ABSENT | ABSENT | `_FailureBreaker`, `runtime.py:70-215, 601-709` |
| Structural loop detection (no-progress/ping-pong) | WIRED | ABSENT | ABSENT | ABSENT | `record_structural`, `runtime.py:178-214` |
| Typed tool-result meta (content_type/raw_ref/truncated/recovery_hints/ok) | WIRED | ABSENT | ABSENT | ABSENT | native `_invoke` captures meta (`runtime.py:831-849`); `chat_runner.py:1704` "Empty for backends (ACP) that don't supply it" |
| Structured tool-input rendering (dict → schema-driven fields) | WIRED | ABSENT | ABSENT | ABSENT | `_redact_tool_input_obj` returns None for the ACP string form (`chat_runner.py:286-318`) |
| File-change diff chips (write_file/edit_file before/after) | WIRED | ABSENT | ABSENT | ABSENT | `_WRITE_FILE_TOOLS = {"write_file","edit_file"}` — native tool names only (`chat_runner.py:352-418`); Claude's Edit/Write never match |
| AskUserQuestion card | WIRED | UNKNOWN | UNKNOWN | UNKNOWN | keyed on tool title == "AskUserQuestion" (`chat_runner.py:1605`); fires only if the CLI exposes an identically-named tool (via MCP it could) |
| Subagents (`subagent_run` + completion inject-back) | WIRED | UNKNOWN | UNKNOWN | UNKNOWN | native: in-process via registry + `set_current_session_key` contextvar (`runtime.py:804-830`); ACP: via `personalclaw-core` MCP if reachable — session-key resolution then uses the `session_pid_<pid>.txt` file the runner writes (`chat_runner.py:1183-1190`) + `PERSONALCLAW_SESSION_KEY` env (`transport.py:323-326`) |
| MCP tools (external servers) | WIRED | PARTIAL | PARTIAL | PARTIAL | native: MCP adapters are registry tool-providers; ACP: only the CLI's own MCP config (host `mcpServers: []`) — the dashboard MCP manager writes `~/.personalclaw/mcp.json` → rebuilt into `personalclaw.json` (`handlers/mcp.py:1238-1242`), which only kiro plausibly reads |
| Queue-steering mid-turn (#37) | WIRED | ABSENT | ABSENT | ABSENT | `set_steer_source` gated on hasattr (`chat_runner.py:1458-1462` "Native runtime only (the ACP CLIs don't expose the seam)"); `runtime.py:544-560, 968-971` |

### 4d. Learning / memory

| Feature | native | claude-code | codex | kiro-cli | Evidence |
|---|---|---|---|---|---|
| Preference-facet capture (every turn) | WIRED | WIRED | WIRED | WIRED | `_maybe_after_turn_review` runs off session/messages (neutral), `chat_runner.py:119-193` |
| Correction→lesson review | WIRED | WIRED | WIRED | WIRED | same |
| Procedural-outcome capture (M5d tool-outcome drain) | WIRED | ABSENT | ABSENT | ABSENT | `drain_tool_outcomes` exists only on NativeAgentRuntime (`runtime.py:953-960`); `chat_runner.py:168-179` "native runtime only — ACP providers don't accumulate them" |
| Skill-ladder review (4-tier, propose-only) | WIRED | WIRED | WIRED | WIRED | `_maybe_skill_ladder_review` (neutral), `chat_runner.py:195-256` |
| Memory consolidation on session end | WIRED | WIRED | WIRED | WIRED | `_maybe_consolidate`, `chat_runner.py:2430` |
| Incognito/restricted no-write guarantees | WIRED | WIRED | WIRED | WIRED | `is_restricted`/`_ephemeral` guards, `chat_runner.py:130-137, 211-215` + `_apply_incognito_prefix` |

### 4e. Session / conversation mechanics

| Feature | native | claude-code | codex | kiro-cli | Evidence |
|---|---|---|---|---|---|
| Variants / regenerate (‹n/N› switcher) | WIRED | WIRED | WIRED | WIRED | `_pending_variants` + `regenerate_hint` in `_run_chat` (neutral); `chat_regenerate.py:97` |
| Edit & resend, branch continuation (fork) | WIRED | WIRED | WIRED | WIRED | `chat_fork.py`/`chat_undo.py` operate on session messages, not the provider |
| Queued messages (merge/pop + live bubbles) | WIRED | WIRED | WIRED | WIRED | finally-block queue drain, `chat_runner.py:2605-2665` |
| Empty-turn auto-retry | WIRED | WIRED | WIRED | WIRED | `is_empty_turn`, `chat_runner.py:87-116, 2385` |
| Auto-nudge re-arm (loops) | WIRED | WIRED | WIRED | WIRED | `chat_runner.py:2562-2577` |
| Context-% accounting | WIRED | PARTIAL | PARTIAL | PARTIAL | native: provider-reported `context_usage_pct` per completion (`runtime.py:499-500`); ACP: `last_prompt_stats.context_pct` parsed from frames (`acp_agent.py:532`, `translate.extract_context_pct`) — depends on the backend emitting it; UNKNOWN which of the three do |
| Compaction | WIRED (host-owned structured compaction ≥70%, `runtime.py:871-907`) | WIRED (CLI-owned; `/compact` + `wait_for_compaction`, `client.py:619-630`) | UNKNOWN (adapter may not emit compaction frames) | UNKNOWN | `chat_runner.py:2203, 2336-2370` |
| Slash commands (`/compact`, `/usage`, … via stream_command) | PARTIAL (AgentProvider default maps stream_command→stream, i.e. plain prompt — `agents/provider.py:144-146`) | WIRED (protocol `commands/execute`) | UNKNOWN | UNKNOWN | `chat_runner.py:1466`; `client.py:537-548` |
| Session resume across gateway restarts (`session/load`) | n/a (native rebuilds from history) | PARTIAL — capability negotiated but `session_files_dir` is never registered by the bundle → the session-file existence check fails → falls to `session/new` + compressed history | same | same | `client.py:388-414`; `apps/*/provider.py` `register_acp_cli_entry` calls pass no `session_files_dir` |
| Warm pool / instant start | WIRED (in-process, trivially) | WIRED (ACP connection pool `claim` + live respecialize set_agent/set_model/set_mode/set_effort) | WIRED | WIRED + P9 concurrent shared-connection (default dialect only, double-gated) | `session.py:880-935, 475-543`; `connection_pool.py:202-246` |
| Concurrent sessions on one process (P9) | n/a | ABSENT (dialect False) | ABSENT | WIRED (gated: `supports_concurrent_sessions=True` + `acp_concurrent_sessions` flag) | `dialect.py:251-263`, `acp_session_provider.py:215-229` |
| Pipe-death auto-retry / re-queue | n/a | WIRED | WIRED | WIRED | `AcpProcessDied` handling, `chat_runner.py:2480-2493` |
| Model override per session (composer picker) | WIRED (model kwarg → complete(), `provider_bridge.py:851-855`) | WIRED (`set_config_option model`) | WIRED | WIRED (`session/set_model`) | `dialect.py:114-119, 289-295` |
| Reasoning effort per turn | WIRED (`reasoning_effort` → complete(), `runtime.py:243-245, 480`) | WIRED (`configOptions.effort`) | WIRED | ABSENT (default dialect: `set_effort_request` → None) | `dialect.py:136-141, 311-322` |
| Agent/persona selection | WIRED (agent profiles) | ABSENT (no persona axis — one base agent) | ABSENT | WIRED (`session/set_mode` modeId = kiro agent name; personas discovered from availableModes) | `dialect.py:102-112, 285-287`; `acp_agent.py:242-330` |
| Discovered-agent ephemeral binding (chat picker → `POST …/acp-agent`) | n/a | WIRED | WIRED | WIRED | `chat_handlers.py:1207-1265`; `chat_runner.py:1041-1044` |
| Turn telemetry (event/tool counts, tokens, cost estimate) | WIRED | WIRED | WIRED | WIRED | both stamp event_count/tool_call_count (`runtime.py:523-533`; `client.py:580-586`); cost estimated from pricing table when provider reports none (`chat_runner.py:2280-2288`) |

### 4f. Cross-app surfaces (which backend do they use?)

| Surface | Backend routing | Evidence |
|---|---|---|
| Loops (Code/Design/Research/Goal — ALL kinds) | Default native; **any loop can bind an ACP provider** (`loop.provider` → `session.acp_provider`), unattended ACP loops get `bypassPermissions` (Zed dialects only — kiro would keep its defaults) | `loop/manager.py:172-199` |
| Cron / scheduled run-prompt / run-workflow | Whatever the session's bindings resolve; unattended flag is native-only, so an ACP-bound scheduled run can wedge on interactive prompts | `provider_bridge.py:528-534`, `session.py:1080-1084` |
| Inbox AI draft / digest / summarize | Neither — `one_shot_completion(use_case="background")` = a bare ModelProvider, never an agent runtime | `inbox_service.py:287-357` |
| Auto-title, suggestions, consolidation ("background" session) | Bare ModelProvider (`_model_axis_only` excludes acp_agent entries) | `provider_bridge.py:320-342` |
| Prompt optimizer | `handlers/optimizer.py` → one-shot completions (model axis) | file present; not an agent-loop consumer |
| Channel (Slack) sessions | Provider-neutral `_run_chat` (mirroring via `channel_delivery` works for both) | `chat_runner.py:1472-1488, 2466-2475` |
| Voice: STT insert / TTS reply | Media providers via composer/`chat_voice.py` — orthogonal to agent runtime (text lands in the composer/message) | `dashboard/chat_voice.py` |
| Screenshot capture / file panel | Dashboard handlers (`handlers/files.py`) — attach as files → 4a attachment injection (neutral) | `handlers/files.py` |
| Subagent completions inject-back + pending-context drain | Session-level (neutral) — `_pending_subagent_failures` / `_pending_context`, `chat_runner.py:1286-1309` | |

## 5. Top 10 gaps (ranked by user impact)

1. **Native tool registry does not reliably reach ACP CLIs.** Host sends `mcpServers: []` at `session/new` (`acp/client.py:419,481`); the `personalclaw-core` aggregated MCP server reaches a CLI only through that CLI's own config. claude/codex get NO seeded config; kiro's discovery of `~/.personalclaw/agents/personalclaw.json` is unverified on this machine. Net effect: knowledge/tasks/inbox/artifacts/workflows/subagent/notify tools are likely absent on claude/codex sessions.
2. **CLI-side auto-approved tools bypass every host pre-execution control** (deny-list, task-mode, blocking PreToolUse hooks). Only tools that emit `session/request_permission` hit the host gate; hooks on EVENT_TOOL_CALL are explicitly informational (`chat_runner.py:1623-1632`).
3. **Unattended/dry-run machinery is native-only** (`provider_bridge.py:528-537`): an ACP-bound loop/cron can wedge on interactive prompts. Partial mitigation (`bypassPermissions`) exists for claude/codex only; **kiro-cli unattended loops have no mitigation at all** (no mode axis).
4. **Procedural memory (M5d) never learns from ACP turns** — `drain_tool_outcomes` exists only on the native runtime (`chat_runner.py:168-179`).
5. **Loop-guard machinery (failure breaker, structural loop detection, circuit breaker) is native-only** — an ACP agent stuck in a tool-failure loop burns budget with no host-side brake.
6. **ACP session resume is effectively dead**: `session/load` requires a `session_files_dir` no bundle registers, so every provider restart falls back to compressed-history bootstrap (`client.py:388-414`).
7. **Tool-result/UI fidelity gap**: typed result meta (content_type/raw_ref/truncated/recovery_hints/ok), structured input rendering, and file-change diff chips are all ABSENT for ACP (string-only input, native write-tool names).
8. **Effective-risk indicators are heuristic-only for ACP** — no declared risk_level on ACP tools, so trust_reads and the approval-card risk chip rely on name/kind/bash parsing.
9. **Per-dialect capability asymmetries**: kiro lacks plan-mode forwarding + reasoning effort; claude/codex lack personas + P9 concurrent sessions; slash-command support beyond claude is unverified.
10. **project_id / extra_tool_roots / queue-steering don't cross the seam** — Project artifact stamping, brownfield loop extra roots, and mid-turn steering all silently degrade on ACP sessions.

## 6. As-a-user validation sweep (per provider)

**Bind the provider (UI):** Chat → agent picker (composer header) → "Discovered agents" section lists each ready `acp:<cli>` runtime (populated by `GET /api/agent-providers` readiness + `discover_agents`); picking one issues `POST /api/chat/sessions/{s}/acp-agent {provider: "acp:<cli>", provider_agent?, model?, reasoning_effort?}` and resets the runtime session. Verify the activity line reads `Session created · … · via acp:<cli>`. Clear by re-picking a native agent (empty provider). For kiro, also test picking a persona (modeId) row.

Ordered most-likely-to-differ first — per provider (claude-code, codex, kiro-cli), with a native control run of the same step:

1. **MCP tool reachability (gap 1):** ask "list your available tools; do you have knowledge_search / task_create / notify?" Then ask it to `knowledge_search` something known. Record whether personalclaw-core tools exist at all. For kiro: check whether `~/.personalclaw/agents/personalclaw.json` is honored (ask it to run `@personalclaw-core` notify).
2. **Approval-gate coverage (gap 2):** ask for (a) a file read, (b) a file write, (c) a destructive bash (`rm` a scratch file). Note which surface a host approval card vs run silently. Then set task-mode=Ask and repeat (b) — does the write still happen?
3. **Plan mode (gap 9):** switch to Plan, ask for a small code change. claude/codex should plan natively (forwarded `acp_mode=plan`); kiro should be blocked only by the host gate.
4. **Unattended loop (gap 3):** create a small Code loop bound to the provider, unattended. Watch for wedging on interactive prompts (esp. kiro) and whether writes execute (claude/codex bypassPermissions).
5. **Resume (gap 6):** mid-conversation, restart the gateway; continue the chat. Expect "Session resumed" NOT to appear (falls to compressed history); verify continuity quality.
6. **Tool-card fidelity (gap 7):** run a multi-tool turn; check cards for input args, output, done-state, diff chips (expect none), recovery hints on a failed tool (expect none).
7. **Context/turn telemetry:** check context-% chip and "Turn complete: N events, M tool calls" line after a turn; `/compact` behavior (claude expected to work; codex/kiro unknown).
8. **Reasoning effort + model override:** set effort in the composer (claude/codex should honor; kiro pill should be absent/no-op) and pick a discovered model.
9. **Learning:** issue a correction ("no — always use X"); verify the "Learned:" chip fires (should, neutral) and that no procedural outcomes were recorded (expected gap 4).
10. **Steering + queued messages:** send a message mid-turn (steer mode) — expect it to queue (not steer) on ACP; verify queue drains after the turn.
11. **Subagents (if MCP reachable):** ask it to spawn a subagent; verify completion injects back into the right session.
12. **Concurrent sessions (kiro only):** with `acp_concurrent_sessions` on, open two kiro chats; verify one process (single PID) serves both interleaved.

## 7. Binary availability snapshot (repeat of §3, for the sweep header)

- claude: `~/.toolbox/bin/claude` — auth artifacts present.
- codex: `~/.toolbox/bin/codex` + adapter `codex-acp` installed (nvm node 24) — auth artifacts present.
- kiro-cli: `~/.toolbox/bin/kiro-cli` (toolbox shim) — kiro data dirs populated; Amazon-internal auth (mwinit) freshness unverified.
- claude-agent-acp adapter: NOT installed; will provision via npx/durable-install on first enable (Node ≥ 20 present).
