# Configuration reference

PersonalClaw's core configuration lives in one JSON file: **`~/.personalclaw/config.json`**
(the directory can be relocated with the `PERSONALCLAW_HOME` environment variable).

Three ways to change it:

1. **Dashboard UI** — most fields have a control in Settings (the "Where to set"
   column below names the panel).
2. **CLI** — `personalclaw config get|set <key> [value]` (dot-separated keys, e.g.
   `personalclaw config set session.timeout_secs 7200`), or `personalclaw config edit`
   to open the file in `$EDITOR`.
3. **API** — `GET /api/config/personalclaw` (full config),
   `PATCH /api/config/personalclaw {path, value}` (single-field, allowlisted),
   `GET /api/config/schema` (the machine-readable field registry this document
   is derived from).

A key like `loops.max_cycles_hard_cap` means `{"loops": {"max_cycles_hard_cap": …}}`
in the file. Fields marked **backend-only** have no dashboard control — set them via
CLI/file (most need a gateway restart). Fields with a UI panel are applied live.

Not everything is in `config.json` by design. Stored elsewhere:

- **Model bindings** (which model serves chat/background/embedding/…): Settings →
  Models → `~/.personalclaw/active_models.json`.
- **Search bindings**: Settings → Search → `~/.personalclaw/active_search_providers.json`.
- **Inbox + notification entity settings**: `~/.personalclaw/entity_settings/*.json`
  (edited via the Inbox / Notifications settings panels).
- **Per-app config**: each app's `data/config.json` (edited via the app's Configure form).
- **Provider credentials**: the `.env` credential store (written by `personalclaw setup`).

---

## Agent runtime (`agent.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `agent.approval_mode` | enum: `auto`, `interactive`, `trust_reads` | `auto` | Settings → Agent defaults | Tool approval mode. `trust_reads` auto-approves read-only tools and asks for everything else. |
| `agent.provider` | string | `native` | backend-only (restart) | Default agent runtime for agents that don't set their own: `native` (in-process loop, models governed by Settings → Models), `acp`, or `acp:<cli>` to pin a connected CLI runtime. Per-agent `provider` overrides this. File-only by design — switching it mid-flight would strand live sessions. |
| `agent.sandbox` | enum: `auto`, `off` | `auto` | Settings → Agent defaults | Sandbox mode for the ACP provider. |
| `agent.yolo` | boolean | `false` | Settings → Agent defaults | Skip every tool-approval confirmation. Only use inside a sandbox or for trusted automation. |
| `agent.acp_concurrent_sessions` | boolean | `false` | Settings → Agent defaults | Run multiple ACP chat sessions on ONE backend process (multiplexing) instead of one process per session — for backends that support session interleaving. |
| `agent.bot_name` | string (≤50 chars) | `""` | Settings → Account | Custom name the assistant identifies as. Sanitized at the write boundary (markdown/braces stripped). Empty = default. |
| `agent.orchestrator_skill` | boolean | `false` | Settings → Agent defaults | Enable agent delegation — generates and loads the orchestrator skill with the agent roster. |
| `agent.max_subagents` | integer (0–16) | `3` | Settings → Agent defaults | Maximum concurrent subagents. `0` = auto-size from host CPU + memory. |
| `agent.spawn_min_memory_gb` | number (0–64) | `4.0` | Settings → Agent defaults | Minimum available memory (GB) required to spawn a subagent. `0` disables the check. |
| `agent.subagent_max_turns` | integer (1–200) | `100` | Settings → Agent defaults | Default tool-call budget per subagent. |
| `agent.subagent_timeout_secs` | integer (60–7200) | `1800` | Settings → Agent defaults | Wall-clock timeout per subagent execution. |
| `agent.subagent_cwd_allowed_roots` | list of strings | `["~/workspace", "~/workplace"]` | Settings → Agent defaults | Directory roots under which a subagent's `cwd` override is permitted (`~` expands). Empty list disables cwd overrides. |
| `agent.log_level` | enum: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `WARNING` | Settings → Agent defaults | Persistent backend log level. Applied at startup; the `--verbose` CLI flag overrides it. |
| `agent.soft_stop_budget_secs` | number (0.5–60) | `10.0` | Settings → Agent defaults | Seconds to wait for a cooperative cancel before hard-killing a session. |

The chat **model** is not a config field — bind models per use case in Settings →
Models, or per agent on the Agents page.

## Sessions (`session.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `session.timeout_secs` | integer (0–86400) | `3600` | Settings → Chat | Idle session timeout in seconds. |
| `session.autocompact_pct` | number (5–90) | `90.0` | Settings → Chat | Context usage percentage at which auto-compaction triggers. |
| `session.pool_size` | integer (0–10) | `0` | Settings → Chat | Pre-spawned ACP agent processes kept warm for instant session start. `0` disables. Only useful for ACP agents (subprocess spawn is the cost); the native runtime needs no pool. |
| `session.pool_agent` | string | `""` | Settings → Chat | Agent name for warm-pool processes. Empty uses `default_agent`. |
| `session.pool_ttl_secs` | integer (0–7200) | `1800` | Settings → Chat | Max age for pooled processes; stale ones are discarded at claim time. `0` disables. |

## Goal loops (`loops.*`)

All backend-only operator knobs; per-loop values (set in the loop creation form)
override the defaults, and the hard cap binds everything.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `loops.max_cycles_hard_cap` | integer | `100` | backend-only | Absolute ceiling on any loop's cycle budget, regardless of the per-loop limit. Safety brake against runaway cost. |
| `loops.default_idle_secs` | integer | `120` | backend-only | Seconds between worker cycles when a loop doesn't specify its own idle timer. |
| `loops.trust_ttl_secs` | integer | `86400` | backend-only | How long a loop worker keeps auto-approved tool trust before the supervisor expires it and requires re-authorization. |

## Memory (`memory.*`)

Behavior toggles live in Settings → Memory; tuning constants are backend-only.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `memory.semantic_confidence_threshold` | number | `0.8` | backend-only | Minimum similarity for a semantic-memory hit to be injected. |
| `memory.episodic_dedup_threshold` | number | `0.88` | backend-only | Cosine similarity above which a new episodic record is treated as a duplicate and skipped. |
| `memory.episodic_max_results` | integer | `8` | backend-only | Episodic records recalled per query. |
| `memory.episodic_max_count` | integer | `10000` | backend-only | Episodic store size cap; oldest records are pruned past it. |
| `memory.semantic_keys` | list of strings | `[]` | backend-only | Extra top-level semantic-record prefixes (namespaces) beyond the built-ins. |
| `memory.l1_manifest` | boolean | `true` | Settings → Memory | Inject only a small always-on manifest of your most-recalled facts; the agent pulls deeper memory on demand via the `memory_recall` tool. Off = inject full semantic + episodic memory every turn (legacy). |
| `memory.active_recall` | boolean | `true` | Settings → Memory | On an interactive turn, surface query-relevant memory just before the reply — bounded by a timeout + circuit breaker. Skipped for temporary/incognito/headless turns. |
| `memory.proactive_commitments` | boolean | `false` | Settings → Memory | Let the agent infer future check-ins from conversation and deliver ONE natural reminder per window via the heartbeat. Opt-in; high-confidence only; capped per day; one-tap dismiss. |
| `memory.proactive_commitments_max_per_day` | integer | `3` | backend-only | Hard maximum active proactive check-ins per agent per day. |
| `memory.active_recall_timeout_ms` | integer | `1500` | backend-only | Hard budget for the pre-reply recall pass; on timeout the turn proceeds without it (circuit breaker trips after repeats). |
| `memory.auto_promote_enabled` | boolean | `true` | backend-only | Periodically promote repeated episodic memories into durable semantic facts (the self-learning loop) — guarded by a per-run cap + min-interval + single-flight. Off = promotion only via the Memory Studio button. |
| `memory.auto_promote_every_n` | integer | `10` | backend-only | Run promotion after every Nth history consolidation. |
| `memory.auto_promote_max_per_run` | integer | `5` | backend-only | Cap on clusters promoted in a single autonomous run. |
| `memory.history_idle_hours` | number (≥0.5) | `3.0` | Settings → Memory | Hours of inactivity before history consolidation. |
| `memory.history_max_days` | integer (≥7) | `365` | Settings → Memory | Maximum days of history to retain. |
| `memory.migrated` | boolean | `false` | managed automatically | Whether memory has been migrated to the vector store (set by `personalclaw memory migrate` / the API). |
| `memory.vault_enabled` | boolean | `false` | Settings → Memory | Mirror memory to a browsable markdown vault (Obsidian-compatible: YAML frontmatter + `[[wikilinks]]` + graph view). Read-only — regenerated from the memory store. |
| `memory.vault_path` | string | `memory-vault` | Settings → Memory | Where the markdown vault is written. Relative paths resolve under `~/.personalclaw`; absolute paths are used as-is. |

## Skills (`skills.*`)

Skill management (install/enable/proposals) is the Skills page; these backend-only
keys tune the automatic skill machinery.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `skills.max_triggered` | integer (≥1) | `3` | backend-only | Max skills surfaced per message (semantic ∪ keyword match). |
| `skills.auto_create_from_sessions` | boolean | `false` | backend-only | Analyze completed sessions and synthesize a reusable SKILL.md when a non-trivial procedure is detected (lands under `skills/auto/`). |
| `skills.auto_refine_on_deviation` | boolean | `false` | backend-only | Update an auto-created skill when the agent succeeds via a different tool sequence (requires `auto_create_from_sessions`). |
| `skills.auto_min_tool_calls` | integer (≥2) | `5` | backend-only | Minimum tool calls for a session to qualify for skill extraction. |
| `skills.auto_similarity_threshold` | number (0–1) | `0.85` | backend-only | Skip creation when an existing skill's description overlaps ≥ this fraction. |
| `skills.progressive_disclosure_threshold` | integer | `8` | backend-only | When more skills than this match a turn, inject only their index (name + description) and let the agent pull bodies on demand via `skill_invoke`. `0` = always inline. |

## After-turn learning (`learning.*`)

The continuous self-improvement review that runs after learning-worthy turns
(distinct from session-end consolidation). All backend-only.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `learning.enabled` | boolean | `true` | backend-only | Kill switch for the after-turn review (always skipped for incognito/temporary sessions). |
| `learning.min_tool_calls` | integer | `4` | backend-only | A turn with at least this many tool calls qualifies even without a correction signal. |
| `learning.correction_heuristic` | boolean | `true` | backend-only | Treat a correcting user message ("no, actually, …") as a first-class learning signal. |
| `learning.surface_chip` | boolean | `true` | backend-only | Show the quiet "Learned: …" chip in chat when something is captured. |
| `learning.skill_ladder` | boolean | `true` | backend-only | Allow the review to PROPOSE reusable skills — never auto-installed; proposals land in the Skill-proposals inbox for approval. |

## Workflow surfacing (`workflows.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `workflows.enabled` | boolean | `true` | backend-only | Kill switch for SOP surfacing (auto-inject the best-matching workflow above threshold). |
| `workflows.match_threshold` | number (0–1) | `0.62` | backend-only | Cosine-similarity gate for a workflow match. The keyword fallback uses a fixed 0.7 word-overlap. |

## Security (`security.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `security.denied_commands` | list of regexes (≤100) | `[]` | Settings → Security | User-added regexes for shell commands the agent must never run, appended to the always-on built-in denylist. Matched case-insensitively against the full command string. |
| `security.egress.allow_hosts` | list of strings | `[]` | Settings → Security | Hosts (bare domain covers subdomains) permitted even when they resolve to a private/LAN address — for homelab webhooks/services. Applies to all egress surfaces. |
| `security.egress.deny_hosts` | list of strings | `[]` | Settings → Security | Hosts the agent must never reach, even if public. A deny always overrides an allow. |
| `security.egress.allow_private` | boolean | `false` | Settings → Security | Permit egress to private/LAN addresses globally. Only enable on a fully trusted network — it removes SSRF protection for the whole LAN. |

## Inbox (`inbox.*`)

Alert keywords, name-mention alerts, and retention live in the Inbox settings panel
(entity store, not `config.json`). Config-side:

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `inbox.enabled` | boolean | `false` | Inbox → Settings ("Poll sources" toggle) | Gates the poll-based message sources. The UI toggle calls `/api/inbox/restart` after flipping so the service re-attaches. |
| `inbox.user_id` | string | `""` | channel-app setup | Your user id on the connected channel — used to skip your own messages. |
| `inbox.watched_channels` | list of strings | `[]` | channel-app setup | Channel ids the poll loop watches. |
| `inbox.poll_interval_seconds` | integer (min 30) | `60` | backend-only | Poll cadence. |
| `inbox.style_rules` | list of strings | `[]` | backend-only | Voice/style lines injected into AI reply drafting. |
| `inbox.test_mode` | boolean | `false` | backend-only | Ingest your OWN messages too (demo/testing). |
| `inbox.engagement_ranking_enabled` | boolean | `false` | Inbox → Settings | Rank the inbox by how much you engage with each channel/sender (favorites, opens, replies boost; dismisses lower) on top of recency. |
| `inbox.engagement_half_life_days` | number (0–365) | `0.0` | Inbox → Settings | How fast an engagement boost fades (`0` = the default ~6.6 days). |

## Tool output (`tools.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `tools.projection_rules` | list of objects | `[]` | Settings → Tool output | User-taught rules mapping a tool-output content marker (regex) to a builtin projection strategy, so a large output keeps its salient slice instead of a generic cut. Consulted before the heuristic sniff; a bad regex is skipped. |
| `tools.projection_rules[].name` | string | `""` | Settings → Tool output | Short label for the rule. |
| `tools.projection_rules[].match_regex` | string | `""` | Settings → Tool output | Regex matched against the start of a tool's output. |
| `tools.projection_rules[].strategy` | enum: `log`, `diff`, `json`, `test`, `csv` | `log` | Settings → Tool output | The builtin projector to apply. |

## Dashboard (`dashboard.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `dashboard.url` | string | `""` | written by `personalclaw setup` | Advertised dashboard origin — used in links delivered to external channels and by server bind/origin checks. |
| `dashboard.restore_sessions` | boolean | `false` | Settings → Chat | Re-open recently active sessions on startup. |
| `dashboard.restore_window_minutes` | integer (0–1440) | `30` | Settings → Chat | Time window for session restoration. `0` = restore all. |
| `dashboard.user_name` | string | `""` | Settings → Account | How the system addresses the operator. Set during first-run onboarding; instance-level so it follows you across browsers/machines. |
| `dashboard.merge_queued_messages` | boolean | `false` | Settings → Chat | Concatenate follow-up messages while the agent is busy instead of queueing them separately. |
| `dashboard.auto_tag_sessions` | boolean | `true` | Settings → Chat | When a chat's title is auto-generated, also propose and assign tags in the same pass. Never touches chats you've tagged, or incognito/temporary chats. |
| `dashboard.mcp_probe_timeout_secs` | integer (5–120) | `15` | backend-only (PATCH-editable) | Per-server timeout for MCP tool-discovery probes; the gateway's MCP status sweep budget derives from it (+15s). |
| `dashboard.widget_density` | enum: `more`, `less` | `more` | Settings → Chat | How aggressively the agent uses inline widgets. |
| `dashboard.send_on_enter` | boolean | `true` | Settings → Chat | Enter sends (Shift+Enter for newline). Off: Enter inserts a newline; Cmd/Ctrl+Enter sends. |
| `dashboard.show_timestamps` | boolean | `false` | Settings → Chat | Display a timestamp on each chat message. |
| `dashboard.show_thinking_inline` | boolean | `false` | Settings → Chat | Show intermediate reasoning between tool calls instead of collapsing it. |
| `dashboard.simplified_tool_names` | boolean | `false` | Settings → Chat | Inline tool pills show a simplified purpose instead of the exact command. |
| `dashboard.confirm_close_session` | boolean | `false` | Settings → Chat | Ask for confirmation when closing a session from the sidebar. |
| `dashboard.auto_open_browser` | boolean | `true` | backend-only | Open the dashboard in a browser on gateway start (`--no-open` overrides per-run). |
| `dashboard.terminal` | object | `{"enabled": true}` | `enabled`: backend-only; `persist`: Terminal page | `enabled` is the kill switch for the built-in terminal (PTY) feature, read raw with a 30s cache. `persist` (tmux-backed persistence across gateway restarts) is toggled on the Terminal page. |
| `dashboard.dashboard_layout` | object | `{}` | Home dashboard (drag/resize widgets) | The home dashboard's customized widget layout. Empty = the curated default. |

## Top-level keys

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `hooks` | object | `{}` | Triggers page / `/api/hooks` | Webhook trigger config by hook id, plus `webhook_token` and `auto_approve_sources`. Managed via the Triggers UI; documented here because the raw shape is config-visible. |
| `observe_max_messages` | integer | `200` | backend-only | Channel-observation ring-buffer size (messages kept per channel for context). |
| `observe_ttl_hours` | number | `168.0` | backend-only | How long observed channel messages stay usable as context. |
| `agents` | object | `{}` | Agents page | Named agent definitions (see below). |
| `default_agent` | string | `""` | Settings → Agent defaults | Active agent name from the `agents` section (also `PUT /api/config/default-agent`). |
| `memory_stores` | object | `{}` | backend-only | Named memory store definitions; `memory_stores.<name>.description` is a human-readable purpose. Stores are referenced by agent profiles. |
| `auto_update` | boolean | `true` | Settings → Updates | Automatically apply core updates when a new version is found (update checks always run; this gates the unattended pull + rebuild + restart). |
| `timezone` | string | `""` (system) | set by `personalclaw setup` | IANA timezone (e.g. `Asia/Tokyo`) for schedules and the clock the LLM sees. Per-job trigger timezones override it. |
| `snapshot_dir` | string | `""` | backend-only | Where `personalclaw snapshot` writes/reads portability snapshots. Empty = `~/.personalclaw/snapshots`. |

## Agent definitions (`agents.<name>.*`)

Managed on the **Agents page** (create/edit forms); stored under `agents` keyed by
agent name. Every field is optional — empty inherits the global default.

| Key | Type | Default | Description |
|---|---|---|---|
| `agents.*.provider` | string | `""` | Runtime backend: `native` (in-process loop) or `acp:<cli>` (external CLI). Empty inherits the global `agent.provider`. |
| `agents.*.provider_agent` | string | `""` | ACP provider agent name (modeId for `session/set_mode`). |
| `agents.*.acp_mode` | string | `""` | ACP permission/operating mode for adapters that expose one (e.g. `default`, `acceptEdits`, `plan`, `bypassPermissions`). Distinct from Approval Mode (the host gate). |
| `agents.*.default_dir` | string | `""` | Working directory this agent opens in. Empty inherits the workspace root. Overridable per-session. |
| `agents.*.memory_store` | string | `""` | Memory provider for this agent. Empty uses the filesystem fallback scoped by working directory. |
| `agents.*.description` | string | `""` | Human-readable agent description. |
| `agents.*.system_prompt` | string | `""` | System prompt injected at session start. |
| `agents.*.voice` | string | `""` | WHO the agent is — tone, opinions, persona — kept separate from the operating rules and injected high-priority so personality survives long prompts. |
| `agents.*.model` | string | `""` | Default model for this agent. Overridable per-chat. |
| `agents.*.approval_mode` | string | `""` | `auto`, `interactive`, or empty (inherit global). |
| `agents.*.skills` | list | `[]` | Skill names loaded for this agent. |
| `agents.*.tools` | list | `[]` | Allowed tool name patterns for this agent. |
| `agents.*.triggers` | list | `[]` | Referenced lifecycle-trigger IDs. A lifecycle trigger fires ONLY for agents that list it. |
| `agents.*.source` | string | `personalclaw` | Agent origin: `personalclaw`, `marketplace`, or `builtin`. |

---

## Environment variables

Not config-file fields, but part of the same operator surface:

| Variable | Effect |
|---|---|
| `PERSONALCLAW_HOME` | Relocate the config/state directory (default `~/.personalclaw`). |
| `PERSONALCLAW_PORT` | Override the dashboard/API port (default `10000`). Validated at CLI entry. |
| `PERSONALCLAW_WORKSPACE` | Workspace root for LLM working directories. |
| `PERSONALCLAW_BIND_HOST` | Bind address for the gateway (e.g. `0.0.0.0` for LAN access). |
| `PERSONALCLAW_BYPASS_LOCAL_NETWORKS` | `1` = skip token auth for loopback/RFC1918 clients (dev convenience; public origins still need a token). |
| `PERSONALCLAW_FIRST_PARTY_APPS_DIR` | Point a packaged install at a first-party apps directory. |
| `PERSONALCLAW_SKIP_APP_BACKENDS` | Don't launch app backend subprocesses (test isolation). |

## Programmatic surfaces

- `GET /api/config/personalclaw` — full config as JSON (owner-only).
- `PATCH /api/config/personalclaw {path, value}` — single-field writes, allowlisted; non-editable paths return 400.
- `GET /api/config/schema` — the full field registry (labels, help, types, defaults, deprecations) auto-derived from the config dataclasses. This document is generated against it.
- `personalclaw config get|set <key> [value]` — CLI equivalent; `set` validates through the same loader.

See also: [API overview](api-overview.md) · [CLI reference](cli.md) ·
[Getting started](../guides/getting-started.md)
