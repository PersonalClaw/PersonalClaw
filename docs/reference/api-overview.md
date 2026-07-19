# API overview

The gateway serves a REST + WebSocket API on the dashboard port (default `10000`).
All routes live under `/api/*` and require authentication (token or local-network
bypass, depending on your auth mode) — see `personalclaw token` for a tokenized URL.

Route registrations live in `src/personalclaw/dashboard/server.py` plus per-domain
handler modules (`dashboard/handlers/`, `tasks/handlers.py`, `workflows/handlers.py`,
`artifacts/handlers.py`, `lexicon/handlers.py`, `providers/*_routes.py`). This page
lists every route with a one-liner; the handler docstrings are the authoritative
per-route contract.

## System & auth

| Method + path | What it does |
|---|---|
| `GET /api/healthz` | Liveness probe. |
| `GET /api/status` | Runtime stats (uptime, sessions, counters). |
| `GET /api/system` | Host/system information. |
| `GET /api/auth-status` | Current auth mode + caller identity. |
| `GET /api/onboarding` | First-run onboarding state. |
| `GET /api/token/local` | Mint a local dashboard access token (loopback only). |
| `POST /api/logout` | Revoke all active dashboard sessions. |
| `GET /api/ws` | The main WebSocket (chat streaming + event fan-out). |
| `GET /api/logs` · `GET/POST /api/logs/level` | Read gateway logs; get/set log level. |
| `POST /api/system/restart` | Restart the gateway process. |
| `GET /api/suggestions` | Precomputed dashboard suggestion cards. |
| `GET /api/changelog` | Changelog for the update panel. |
| `GET /api/update/check` · `POST /api/update` · `POST /api/update/auto` · `POST /api/update/cancel` · `POST /api/update/simulate` | Core self-update: check, apply, toggle unattended mode, cancel, dry-run. |
| `GET /api/design/tokens/default` | Default design-token set. |

## Chat & sessions

| Method + path | What it does |
|---|---|
| `POST /api/chat` | Send a message (creates a session if needed). |
| `GET/POST /api/chat/sessions` | List sessions / create one. |
| `GET /api/chat/sessions/{session}` | Session detail + transcript. |
| `DELETE /api/chat/sessions/{session}` | Delete a session. |
| `POST /api/chat/sessions/cleanup` | Bulk-clean stale sessions. |
| `GET /api/chat/sessions/{session}/tool-result/{rid}` | Fetch a stored full tool result. |
| `POST /api/chat/sessions/{session}/stop` · `.../interrupt` | Soft-stop / interrupt the running turn. |
| `DELETE /api/chat/sessions/{session}/queue/{queue_id}` | Remove a queued message. |
| `POST /api/chat/sessions/{session}/agent` · `.../acp-agent` | Switch the session's agent / ACP agent. |
| `POST /api/chat/sessions/{session}/model` · `.../reasoning-effort` | Per-session model / reasoning-effort override. |
| `POST /api/chat/sessions/{session}/workspace-dir` | Change the session working directory. |
| `POST /api/chat/sessions/{session}/context` | Inject context into the session. |
| `POST /api/chat/sessions/{session}/fork` · `.../undo` · `.../drop` | Fork the session; undo the last turn; drop from the sidebar. |
| `POST /api/chat/sessions/{session}/resume` · `.../approve` | Resume an archived session; answer a tool-approval prompt. |
| `POST /api/chat/sessions/{session}/regenerate` · `.../switch-variant` · `.../edit-resend` | Regenerate an answer; switch answer variant; edit + resend a message. |
| `POST /api/chat/sessions/{session}/generate-title` · `PATCH .../title` | Auto-generate / set the title. |
| `PATCH /api/chat/sessions/{session}/color` · `.../folder` · `.../pin` | Set color; move to folder; pin. |
| `PUT /api/chat/sessions/{session}/tags` | Set session tags. |
| `POST /api/chat/sessions/{session}/side/open` · `.../side/turn` · `.../side/close` | Side-conversation lifecycle. |
| `POST /api/chat/sessions/{session}/handoff` · `.../channel-link` | Hand a chat off to a channel; link to a channel thread. |
| `POST /api/chat/mode` · `POST /api/chat/task-mode` | Set chat mode / task mode. |
| `POST /api/chat/nav/resolve-links` | Resolve internal navigation links in messages. |
| `GET/POST /api/chat/folders` · `PATCH/DELETE /api/chat/folders/{id}` | Chat folder CRUD. |
| `GET/POST /api/chat/tags` · `PATCH/DELETE /api/chat/tags/{id}` | Chat tag CRUD. |
| `GET/POST /api/chat/tag-columns` · `PUT .../order` · `PATCH/DELETE .../{id}` | Kanban tag-column CRUD + ordering. |
| `POST /api/sessions/retag-all` · `GET .../retag-all` · `POST .../retag-all/cancel` | Bulk re-tagging job control. |
| `GET /api/session/archive` · `GET /api/session/archive/{name}` | Archived transcript listing / detail. |
| `GET /api/sessions` · `DELETE /api/sessions` · `GET/DELETE /api/sessions/{key}` | Low-level session store listing / deletion. |
| `GET /api/sessions/context` · `.../health` · `.../search` · `POST .../restart` | Session context stats; health; full-text search; restart. |
| `GET /api/sessions/{id}/agents` · `GET /api/sessions/{id}/agents/{agent_id}` | Multi-agent (space) roster within a session. |
| `POST /api/send-message` | Deliver a message to a channel (generic outbound). |
| `POST /api/session-keepalive` · `GET /api/session-tool-policy` | Keep a session warm; read the session's tool policy. |
| `POST /api/optimizer/optimize` | Prompt-optimizer pass over a draft message. |
| `GET /api/recent-projects` | Recently used workspace directories. |

## Agents

| Method + path | What it does |
|---|---|
| `GET/POST /api/agents` · `PUT/DELETE /api/agents/{name}` | Agent profile CRUD (config-backed). |
| `GET /api/agents/installed` | Installed/connected agent runtimes. |
| `POST /api/agents/sync` | Re-sync agent definitions. |
| `GET/PATCH/DELETE /api/agents/detail/{name}` | Rich agent detail (merged profile + metadata). |
| `GET/PUT/DELETE /api/agent-metadata/{name}` | Per-agent markdown metadata store. |
| `GET /api/agent-providers` · `GET /api/agent-providers/{id}/agents` | Connected agent-runtime providers and their agents. |
| `GET /api/agent-marketplace/marketplaces` · `GET/POST /api/agent-marketplace/agents` · `GET/PUT/DELETE .../agents/{name}` · `POST .../agents/{name}/activate` · `POST .../agents/{name}/test` | Agent marketplace: browse, install, manage, activate, test. |
| `GET/PUT /api/agent/config` | Legacy whole-agent-config get/put. |
| `GET/PUT /api/config/default-agent` | Get/set the default agent. |
| `GET /api/agent-hooks` | Registered agent lifecycle hooks. |
| `GET /api/slash-commands` | Available slash commands for the composer. |

## Subagents & lessons

| Method + path | What it does |
|---|---|
| `GET/POST /api/spawn` · `GET/DELETE /api/spawn/{agent_id}` · `DELETE /api/spawn` | Background subagents: list, spawn, status, cancel one/all. |
| `GET/POST/DELETE /api/lessons` | Learned corrections (the `learn` store). |
| `GET/POST /api/autonudge` · `GET .../session/{session_name}` · `PATCH/DELETE .../{loop_id}` | Autonudge (same-session self-prompting) loops. |

## Models & providers

| Method + path | What it does |
|---|---|
| `GET /api/models/available` · `GET /api/models/active` · `PUT /api/models/active/{use_case}` | Model catalog; per-use-case bindings (chat/background/embedding/…). |
| `GET /api/models/chat` | Models eligible for the chat picker. |
| `GET/PUT /api/models/use-cases/{use_case}/settings` | Per-use-case settings (e.g. STT/TTS options). |
| `GET/POST /api/models/downloads` · `GET .../{id}/stream` · `DELETE .../{id}` | Local-model downloads (start, progress stream, cancel). |
| `GET /api/models/local/{provider}/search` · `DELETE /api/models/local/{provider}/{model}` | Search a local provider's catalog; delete a downloaded model. |
| `GET/POST /api/models/embedding/reindex` · `GET .../{id}/stream` | Embedding re-index jobs + progress stream. |
| `GET/POST /api/model-providers` · `PUT/DELETE .../{name}` · `POST .../{name}/test` | Model-provider instances CRUD + connection test. |
| `GET /api/model-providers/{name}/models` · `.../search` · `.../show` · `POST .../pull` · `POST .../models/delete` | Provider model listing/search/detail; pull + delete (local runtimes). |
| `GET /api/providers` · `GET .../{name}` · `GET .../{name}/schema` · `GET/PATCH .../{name}/config` · `POST .../{name}/enable` · `POST .../{name}/disable` | Installed extension providers: listing, config schema, config, enable/disable. |
| `GET/POST /api/providers/{name}/instances` · `GET/PUT/DELETE .../{id}` · `POST .../{id}/test` | Multi-instance providers (several accounts/endpoints of one type). |
| `GET /api/search/providers` · `GET /api/search/active` · `PUT /api/search/active/{use_case}` | Search providers + per-use-case search bindings. |
| `GET /api/action-providers` | Registered action providers. |

## Skills

| Method + path | What it does |
|---|---|
| `GET /api/skills` · `POST /api/skills` · `GET/PUT /api/skills/{name:.+}` · `DELETE /api/skills/{name}` | Installed skills CRUD. |
| `GET /api/skills/{name}/files` · `POST /api/skills/{name}/verify` | Skill file listing; hash-verify against the install baseline. |
| `GET /api/skills/marketplaces` · `GET /api/skills/search` · `GET /api/skills/marketplace/detail` · `POST /api/skills/install` | Marketplace browse/search/detail/install (supply-chain scanned). |
| `GET /api/skills/ephemeral/{session}` · `POST .../promote` · `DELETE .../{slug}` | Session-scoped ephemeral skills: list, promote to permanent, discard. |
| `GET /api/skills/proposals` · `GET .../{id}` · `POST .../{id}/accept` · `DELETE .../{id}` | Skill proposals inbox (from the learning ladder). |

## Knowledge

| Method + path | What it does |
|---|---|
| `GET/POST /api/knowledge/items` · `GET/PATCH/DELETE .../items/{id}` | Knowledge item CRUD. |
| `POST /api/knowledge/ingest` · `GET .../items/{id}/ingest/stream` | Ingest a source; stream ingestion progress. |
| `GET .../items/{id}/content` · `.../file` · `.../thumbnail` · `.../extracted` | Item content, original file, thumbnail, extracted text. |
| `POST .../items/{id}/generate-intelligence` · `POST /api/knowledge/regenerate-intelligence` | (Re)generate AI enrichment for one/all items. |
| `GET .../items/{id}/graph` · `.../related` · `.../intents` | Item-scoped graph, related items, matching intents. |
| `GET /api/knowledge/tags` · `.../providers` · `.../stats` | Tags, connected source providers, store stats. |
| `GET /api/knowledge/entities` · `.../entities/{id}/graph` · `.../entities/by-name/{name}/items` · `.../by-name/{name}/related` | Extracted entities + entity-centric graph/lookups. |
| `GET /api/knowledge/graph` | Whole-store knowledge graph. |
| `GET/POST /api/knowledge/intents` · `DELETE .../{id}` · `GET .../{id}/outcomes` · `POST .../{id}/run` · `POST .../{id}/generate-skill` | Standing intents: CRUD, run, outcomes, skill generation. |
| `GET /api/knowledge/embedding/status` · `POST .../generate` | Embedding coverage status; backfill embeddings. |
| `GET /api/knowledge/search-for-context` | Semantic search used for chat context injection. |
| `GET/POST /api/lexicon/terms` · `PATCH/DELETE .../{id}` · `POST /api/lexicon/rebuild` · `POST /api/lexicon/reset` | Personal lexicon (STT bias terms) CRUD + rebuild/reset. |
| `GET/POST /api/lexicon/corrections` · `PATCH .../{id}` | Transcription corrections. |

## Memory

| Method + path | What it does |
|---|---|
| `GET/PUT /api/memory/preferences` · `.../projects` · `.../history` | Markdown memory sections (preferences, projects, history). |
| `GET/PUT /api/memory/settings` | Memory behavior toggles + consolidation tuning. |
| `GET/PUT /api/memory/semantic` · `DELETE .../{key:.+}` | Semantic (key-value) memory. |
| `GET /api/memory/episodic` · `.../episodic/search` · `DELETE .../episodic/{id}` | Episodic memory listing/search/delete. |
| `GET /api/memory/recall` | Unified recall query (what the agent's `memory_recall` tool uses). |
| `GET /api/memory/events` · `POST .../events/{event_id}/undo` | Memory-write audit trail + undo. |
| `GET /api/memory/stats` · `.../lint` · `.../observability` · `.../graph` · `.../context-preview` · `.../daily-digests` | Store stats, lint findings, recall observability, graph, context preview, digests. |
| `POST /api/memory/consolidate` · `.../promote` · `.../migrate` · `.../import` | Force consolidation; promote episodic→semantic; migrate legacy; import JSON. |
| `GET /api/memory/embedding-status` · `.../embedding-models` · `POST .../enable-embeddings` · `.../disable-embeddings` · `.../activate-model` · `.../delete-model` | Memory embedding management. |
| `GET /api/memory/vault` · `POST .../vault/sync` | Obsidian-style vault status; force re-sync. |

## Tasks, projects & workflows

| Method + path | What it does |
|---|---|
| `GET/POST /api/tasks` · `GET/PUT/DELETE /api/tasks/{task_id}` | Task CRUD. |
| `GET /api/tasks/graph` · `.../ready` · `.../providers` | Dependency graph; unblocked tasks; task providers. |
| `POST /api/tasks/search` · `POST /api/tasks/bulk` | Search; bulk operations. |
| `GET/POST /api/tasks/{task_id}/comments` | Task comments. |
| `GET/POST /api/projects` · `GET/PUT/DELETE /api/projects/{project_id}` · `GET .../linked` | Project CRUD + linked entities. |
| `GET/POST /api/task-lists` · `GET/PUT/DELETE .../{list_id}` · `POST .../{list_id}/reset` | Task lists (checklists) + reset. |
| `GET/POST /api/workflows` · `GET/PUT/DELETE /api/workflows/{workflow_id}` | Workflow (SOP) CRUD. |
| `GET /api/workflows/{workflow_id}/graph` · `POST .../promote` | Workflow DAG view; promote a draft. |
| `GET /api/workflows/providers` · `POST /api/workflows/preview-match` · `GET .../used-by/{agent}` | Providers; test which workflow a message would surface; reverse usage. |

## Loops (autonomous goals)

| Method + path | What it does |
|---|---|
| `GET/POST /api/loops` · `GET/PUT/PATCH/DELETE /api/loops/{id}` | Goal-loop CRUD. |
| `POST /api/loops/validate` · `POST /api/loops/classify` | Validate a loop spec; classify goal kind. |
| `GET /api/loops/{id}/report` · `.../stream` | Loop report; live event stream. |
| `POST /api/loops/{id}/nudge` · `.../queue` · `.../autopilot` | Nudge the worker; queue guidance; toggle autopilot. |
| `GET /api/loops/{id}/plan-session` · `POST .../plan/start` · `.../plan/retry` · `.../plan/approve` · `.../plan/comment` · `.../plan/edit` | Planning-phase lifecycle (plan, review, approve). |
| `POST /api/loops/{id}/grill-tree` | Generate the assumption grill-tree for a loop. |
| `GET /api/loops/{id}/design/tokens` | Loop-scoped design-token overrides (design loops). |

## Triggers & automation

| Method + path | What it does |
|---|---|
| `GET/POST /api/triggers` · `PUT/DELETE /api/triggers/{id}` | Trigger (schedule/webhook/lifecycle) CRUD. |
| `POST /api/triggers/{id}/toggle` · `.../run` · `.../test` · `.../ack` · `.../to-chat` | Enable/disable; fire now; dry-run; acknowledge; convert result to chat. |
| `GET /api/triggers/history` · `GET /api/triggers/{id}/history` · `GET .../history/{run_id}` | Run history (global, per-trigger, per-run). |
| `GET /api/triggers/variables` | Template variables available to trigger messages. |
| `POST /api/hooks/agent` | Run an agent turn from an external webhook (token-authenticated). |

## Inbox, channels & notifications

| Method + path | What it does |
|---|---|
| `GET /api/inbox` · `.../pending` · `.../status` · `.../digest` · `.../providers` | Inbox listing, pending count, service status, AI digest, sources. |
| `PUT /api/inbox/{id}` · `POST .../{id}/draft` · `.../{id}/open` · `.../{id}/favorite` | Update an item; AI-draft a reply; mark opened; favorite. |
| `POST /api/inbox/send` · `POST /api/inbox/dismiss-all` · `POST /api/inbox/restart` | Send a reply; bulk dismiss; restart the poll service. |
| `GET/PUT /api/inbox/settings` | Inbox entity settings (alerts, retention, ranking). |
| `GET /api/channels` · `GET /api/channels/{name}` · `POST .../connect` · `.../disconnect` · `.../test` | Channel providers: list, detail, connect/disconnect, test. |
| `GET /api/channels/reply-targets` | Channels/threads a reply can be routed to. |
| `POST /api/channel/profile` · `POST /api/channel/upload-file` | Resolve a channel user profile; upload an attachment to a channel. |
| `GET /api/notifications` · `DELETE /api/notifications` · `POST .../clear` · `.../ack` · `.../unack` · `.../ack-all` | Notification center: list, clear, acknowledge. |
| `GET/PUT /api/notifications/settings` | Notification entity settings. |
| `GET /api/outbox` · `GET /api/outbox/{filename}` · `POST /api/outbox/notify` | Headless-run outbox artifacts + notify hook. |

## Apps (extension platform)

| Method + path | What it does |
|---|---|
| `GET /api/apps` · `POST /api/apps` · `GET/DELETE /api/apps/{name}` | Installed apps; install from a source; detail; uninstall. |
| `GET /api/apps/catalog` | The Store catalog (native + first-party + registered sources). |
| `GET/POST/DELETE /api/apps/sources` · `GET/POST/DELETE /api/apps/local-sources` | Third-party app sources (git URLs / local dirs). |
| `POST /api/apps/{name}/enable` · `.../disable` · `.../update` | Enable/disable; update (atomic with rollback). |
| `GET /api/apps/{name}/uninstall-preview` | What an uninstall would remove. |
| `GET/PUT /api/apps/{name}/config` | App config (schema-driven Configure form). |
| `POST /api/apps/{name}/agent-run` · `GET .../agent-run/{run_id}` | App-initiated agent runs (permission-gated). |
| `POST /api/apps/{name}/token` | Mint an app-scoped API token. |
| `GET /apps/{name}/ui/{tail:.*}` | Serve an app's bundled UI assets. |
| `* /apps/{name}/api/{tail:.*}` | Reverse proxy to the app's backend subprocess (credential-stripping, app-scoped bearer). |

## Prompts & themes

| Method + path | What it does |
|---|---|
| `GET/POST /api/prompts` · `GET/PUT/DELETE /api/prompts/{name:.+}` | Prompt template CRUD. |
| `POST /api/prompts/{name:.+}/render` · `.../launch` · `POST /api/prompts/preview` | Render a template; launch it as a chat; preview. |
| `GET/PUT /api/prompts/bindings` · `GET /api/prompts/syntax` | Use-case prompt bindings; template syntax reference. |
| `GET/POST /api/prompt-snippets` · `GET/PUT/DELETE .../{name:.+}` · `POST .../{name:.+}/render` | Reusable prompt snippets. |
| `GET/POST /api/themes` · `GET/PUT/DELETE /api/themes/{slug}` | UI themes (server entities, CSS-variable allowlisted). |

## Files, uploads & terminal

| Method + path | What it does |
|---|---|
| `GET /api/file-read` · `.../file-raw` · `.../file-list` · `.../file-search` · `.../file-content-search` · `.../file-complete` · `.../file-watch` | Workspace file panel: read, raw bytes, list, name/content search, path completion, change stream. |
| `POST /api/file-write` · `.../file-create` · `.../file-move` · `.../file-delete` · `.../file-upload` | File mutations. |
| `GET /api/file-git-status` · `.../file-git-log` · `.../file-git-commit` · `.../file-git-original` | Git status/log/commit-detail/pre-change content for the diff view. |
| `GET /api/browse-dirs` · `POST /api/create-dir` | Directory browser + mkdir. |
| `POST /api/upload` · `POST /api/upload/file` | Simple uploads (chat attachments). |
| `GET /api/uploads/limits` · `POST /api/uploads/init` · `PUT .../{id}/part` · `GET .../{id}` · `POST .../{id}/complete` | Chunked upload protocol for large files. |
| `GET /api/attachment-extract` | Extract text from an uploaded attachment. |
| `POST /api/reveal` | Reveal a path in the OS file manager (local installs). |
| `POST /api/screenshot` | Capture a screenshot artifact. |
| `POST /api/terminal/sessions` · `GET /api/terminal/sessions` · `DELETE .../{session_id}` | PTY terminal sessions. |
| `GET /api/ws/terminal/{session_id}` | Terminal WebSocket (keystrokes/output). |
| `GET /api/config-fs/stream` | Config-tree change stream (live UI refresh). |

## Artifacts

| Method + path | What it does |
|---|---|
| `GET/POST /api/artifacts` · `GET/PATCH/DELETE /api/artifacts/{slug}` | Generated artifact CRUD. |
| `GET /api/artifacts/{slug}/raw` | Raw artifact content. |
| `POST /api/artifacts/{slug}/regenerate` | Regenerate from its source prompt. |
| `GET /api/artifacts/{slug}/versions` · `GET .../versions/{version}` | Version history. |
| `GET/POST /api/artifacts/{slug}/events` | Artifact event log. |

## MCP servers

| Method + path | What it does |
|---|---|
| `GET /api/mcp` · `GET /api/mcp/active` | Configured MCP servers; currently active ones. |
| `GET/POST /api/mcp/probe` · `POST /api/mcp/probe/{name}` | Tool-discovery probes (all / one server). |
| `GET /api/mcp/pool-stats` | Connection-pool statistics. |
| `GET /api/mcp/importable` · `POST /api/mcp/sync` · `POST /api/mcp/apply` | Import servers from other tools' configs. |
| `POST /api/mcp/toggle` · `.../toggle-tool` · `.../toggle-all` · `.../remove` | Enable/disable servers/tools; remove a server. |
| `PUT/DELETE /api/mcp/servers/{name}` | Edit / delete a server definition. |

## Tools

| Method + path | What it does |
|---|---|
| `GET /api/tools` | The full tool registry (native + provider + MCP). |
| `POST /api/tools/invoke` | Invoke a tool directly (owner-only). |
| `POST /api/tools/toggle` · `POST /api/tools/provider-toggle` | Enable/disable a tool / a whole provider's tools. |

## Config

| Method + path | What it does |
|---|---|
| `GET /api/config/schema` | Machine-readable config field registry. |
| `GET/PUT/PATCH /api/config/personalclaw` | Read / replace / single-field-patch the core config. |
| `GET/PUT /api/dashboard/config` | Dashboard-section config (UI preference writes). |
| `GET/PUT /api/config/default-agent` | Default agent binding. |

## Security & approvals

| Method + path | What it does |
|---|---|
| `GET /api/security/stats` · `.../denied-commands` · `.../egress` | Security posture: stats, active deny patterns, egress policy. |
| `GET /api/sel/events` · `GET /api/sel/verify` · `POST /api/sel/rotate` | Tamper-evident security event log: read, HMAC-verify, key rotation. |
| `GET /api/approvals` · `POST /api/approvals/{id}/{action}` | Pending tool approvals; approve/deny. |

## Voice & speech

| Method + path | What it does |
|---|---|
| `POST /api/stt/transcribe` | Transcribe audio (bound STT provider). |
| `POST /api/voice/synthesize` | Synthesize speech (bound TTS provider). |

## Portability

| Method + path | What it does |
|---|---|
| `GET /api/portability/export` | Export state as a portable archive. |
| `POST /api/portability/preview` · `POST /api/portability/import` | Preview / apply an import. |

---

See also: [Configuration reference](configuration.md) · [CLI reference](cli.md)
