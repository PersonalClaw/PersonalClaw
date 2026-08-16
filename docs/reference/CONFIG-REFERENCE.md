# Config reference — operator knobs (`~/.personalclaw/config.json`)

Most settings live in the dashboard (Settings → …). The fields below are
**deliberately backend-only**: operator/deployment knobs edited via
`personalclaw config set <key> <value>` or by editing `config.json` directly
(the gateway needs a restart for most of them). Everything not listed here has
a dashboard control — see `GET /api/config/schema` for the full registry with
labels, help text, types, and defaults.

Conventions: a key like `loops.max_cycles_hard_cap` means
`{"loops": {"max_cycles_hard_cap": …}}` in config.json.

## Agent runtime

| Key | Default | What it does |
|---|---|---|
| `agent.provider` | `native` | Default agent runtime for agents that don't set their own: `native` (in-process loop, models governed by Settings → Models), `acp`, or `acp:<cli>` to pin a connected CLI runtime. Per-agent `provider` (Agents page) overrides this. Deployment-level; switching it mid-flight would strand live sessions, hence file-only + restart. |

The chat **model** is not a config field — bind models per use case in
Settings → Models (`~/.personalclaw/active_models.json`), or per agent on the
Agents page.

## Goal loops (`loops.*`)

| Key | Default | What it does |
|---|---|---|
| `loops.max_cycles_hard_cap` | `100` | Absolute ceiling on any loop's cycle budget, regardless of the per-loop limit. Safety brake against runaway cost. |
| `loops.default_idle_secs` | `120` | Seconds between worker cycles when a loop doesn't specify its own idle timer. |
| `loops.trust_ttl_secs` | `86400` | How long a loop worker keeps auto-approved tool trust before the supervisor expires it and requires re-authorization. |
| `loops.stagnation_window` | `5` | How many consecutive no-progress cycles stall a loop (STAGNANT → "needs direction"). The supervisor compares the last N findings on three signals: byte-identical cycle reports, an identical set of sources/files the cycle recorded, and the worker's own `new_findings_count`. The first two are observed by the supervisor, so a worker claiming progress cannot suppress them; the third is a claim, and its absence no longer counts as progress. Clamped to `2`–`50` (a window of 1 could only compare a cycle with itself). Read per poll, so a change applies without a restart. |
| `loops.judge_use_case` | `reasoning` | Which model axis the loop JUDGE rides — deliberately not the `loops` axis its worker rides, so a reviewer mistake is not correlated with the mistake it reviews. Any chat-family axis (`reasoning`, `chat`, `code_tools`, `background`, `orchestration`, or `loops` to put judge and worker back on one binding); an unrecognized value falls back to `reasoning`, never to `loops`. Read per judge call, so a change applies to the next cycle with no restart. |

Per-loop values (set in the loop creation form) override the defaults; the hard
cap binds everything.

## Memory tuning (`memory.*`)

The behavior toggles (L1 manifest, active recall, proactive check-ins, vault)
are in Settings → Memory. These are the tuning constants under them:

| Key | Default | What it does |
|---|---|---|
| `memory.semantic_confidence_threshold` | `0.8` | Minimum similarity for a semantic-memory hit to be injected. |
| `memory.episodic_dedup_threshold` | `0.88` | Cosine similarity above which a new episodic record is treated as a duplicate and skipped. |
| `memory.episodic_max_results` | `8` | Episodic records recalled per query. |
| `memory.episodic_max_count` | `10000` | Episodic store size cap; oldest records are pruned past it. |
| `memory.semantic_keys` | `[]` | Extra top-level semantic-record prefixes (namespaces) beyond the built-ins. |
| `memory.proactive_commitments_max_per_day` | `3` | Daily cap on inferred proactive check-ins (the toggle itself is in the UI). |
| `memory.active_recall_timeout_ms` | `1500` | Budget for the just-in-time recall pass; the circuit breaker trips past it. |
| `memory.auto_promote_enabled` | `true` | Periodically promote the most-recalled facts into the L1 manifest. |
| `memory.auto_promote_every_n` | `10` | Run promotion every N consolidations. |
| `memory.auto_promote_max_per_run` | `5` | Max facts promoted per run. |
| `memory.vault_path` | `memory-vault` | Where the readable markdown vault is written, relative to `~/.personalclaw` (absolute paths allowed). The mode selector (`off`/`mirror`/`two_way`) is in Settings → Memory. Only the DEFAULT path is a declared state entry, so a relocated vault is outside `personalclaw snapshot`. |

## Skills automation (`skills.*`)

Skill management (install/enable/proposals) is the Skills page; these tune the
automatic skill machinery:

| Key | Default | What it does |
|---|---|---|
| `skills.max_triggered` | `3` | Max skills surfaced per message (semantic ∪ keyword match). |
| `skills.auto_create_from_sessions` | `false` | Analyze completed sessions and synthesize a reusable SKILL.md when a non-trivial procedure is detected (lands under `skills/auto/`). |
| `skills.auto_refine_on_deviation` | `false` | Update an auto-created skill when the agent succeeds via a different tool sequence (requires `auto_create_from_sessions`). |
| `skills.auto_min_tool_calls` | `5` | Minimum tool calls for a session to qualify for skill extraction. |
| `skills.auto_similarity_threshold` | `0.85` | Skip creation when an existing skill's description overlaps ≥ this fraction. |
| `skills.progressive_disclosure_threshold` | `8` | When more skills than this match a turn, inject only their index (name+description) and let the agent pull bodies on demand. `0` = always inline. |

## After-turn learning (`learning.*`)

The continuous self-improvement review that runs after learning-worthy turns
(distinct from session-end consolidation). Kill switches + tuning:

| Key | Default | What it does |
|---|---|---|
| `learning.enabled` | `true` | Master switch for the after-turn review (skipped for incognito/temporary sessions regardless). |
| `learning.min_tool_calls` | `4` | A turn with at least this many tool calls qualifies even without a correction signal. |
| `learning.correction_heuristic` | `true` | Treat a correcting user message ("no, actually, …") as a first-class learning signal. |
| `learning.surface_chip` | `true` | Show the quiet "Learned: …" chip in chat when something is captured. |
| `learning.skill_ladder` | `true` | Allow the review to PROPOSE reusable skills (never auto-installed — proposals land in the Skill-proposals inbox). |

## Workflow surfacing (`workflows.*`)

| Key | Default | What it does |
|---|---|---|
| `workflows.enabled` | `true` | Kill switch for SOP surfacing (inject the best-matching workflow above threshold). |
| `workflows.match_threshold` | `0.62` | Cosine gate for a workflow match (0–1). |

## Inbox (`inbox.*`)

Alert keywords, name-mention alerts, and retention are in the Inbox settings
panel (entity store). Config-side:

| Key | Default | What it does |
|---|---|---|
| `inbox.enabled` | `false` | Gates the poll-based message sources (the UI "Poll sources" toggle writes this). |
| `inbox.user_id` | `""` | Your user id on the connected channel (set by channel-app setup) — used to skip your own messages. |
| `inbox.watched_channels` | `[]` | Channel ids the poll loop watches (channel-app setup). |
| `inbox.poll_interval_seconds` | `60` | Poll cadence (min 30). |
| `inbox.style_rules` | `[]` | Voice/style lines injected into AI reply drafting ("Match this voice/style when replying"). |
| `inbox.test_mode` | `false` | Ingest your OWN messages too (demo/testing; also a CLI flag). |
| `inbox.engagement_half_life_days` | `0` | Engagement-ranking decay half-life; `0` = no decay. The ranking toggle is in the UI. |

## Child processes (`sandbox.*`)

Resource ceilings and the environment allowlist applied to agent-influenced
children (bash tools, hook and cron-script children, app backends, MCP servers).

| Key | Default | What it does |
|---|---|---|
| `sandbox.nofile` | `4096` | Soft `RLIMIT_NOFILE` for an agent-influenced child; `0` disables the cap. ACP session hosts are exempt by profile (they multiplex many MCP pipes). |
| `sandbox.max_pids` | `0` (off) | `RLIMIT_NPROC` ceiling. Off by default because `RLIMIT_NPROC` is a **per-user** limit counting every process you already run — an absolute cap can break a busy host with "cannot fork". |
| `sandbox.max_rss_mb` | `0` (off) | `RLIMIT_AS` ceiling in MB. Off by default: `RLIMIT_AS` is coarse and breaks memory-mapped toolchains. |
| `sandbox.env_passthrough` | `[]` | Extra environment variable **names** that hook, cron-script and bash-action children inherit from the gateway. Those children do **not** inherit the gateway environment: they get a minimal base (`PATH`, `SHELL`, `PWD`, `TERM`, `PYTHONPATH`, locale/`TZ`, `HOME`/`TMPDIR`/`USER`/`XDG_*`, proxy and CA-bundle settings, and `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT`) plus the names you list here. Use it when a script legitimately needs one more variable (e.g. `SLACK_BOT_TOKEN` for a notifier, or a language runtime's variable). Names matching the credential floor (`AWS_SECRET*`, `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME`, `GIT_ASKPASS`) are refused even when declared. Withheld names are listed in the debug log at each spawn — run the gateway with `--verbose` to see what a script is missing. |

## Top-level

| Key | Default | What it does |
|---|---|---|
| `hooks` | `{}` | Webhook trigger config by hook id, plus `webhook_token` and `auto_approve_sources` (sources whose tool calls are auto-approved). Managed via the Triggers UI/API (`/api/hooks`); documented here because the raw shape is config-visible. |
| `observe_max_messages` | `200` | Channel-observation ring buffer size (messages kept per channel for context). |
| `observe_ttl_hours` | `168` | How long observed channel messages stay usable as context. |
| `timezone` | `""` (system) | IANA timezone (e.g. `Asia/Tokyo`) for schedules and the clock the LLM sees. Set by `personalclaw setup`; per-job trigger timezones override it. |
| `snapshot_dir` | `""` (default dir) | Where `personalclaw snapshot` writes/reads portability snapshots. |
| `dashboard.url` | `""` | Advertised dashboard origin (host:port) — written by `personalclaw setup`, consumed by the server bind/origin checks. |
| `dashboard.auto_open_browser` | `true` | Open the dashboard in a browser on gateway start (`--no-open` overrides per-run). |
| `dashboard.terminal.enabled` | `true` | Kill switch for the built-in terminal (PTY) feature. Read raw with a 30s cache; `dashboard.terminal.persist` (tmux-backed persistence) is editable in the Terminal page. |
| `dashboard.mcp_probe_timeout_secs` | `15` | Per-server timeout (5–120 s) for MCP tool-discovery probes; the gateway's MCP status sweep budget derives from it (+15 s). PATCH-editable via the config API, no dashboard control. |
| `memory_stores.<name>.description` | — | Optional description for a named memory store (stores are referenced by agent profiles). |

## Programmatic surfaces

- `GET /api/config/personalclaw` — full config as JSON (sensitive defaults masked in the schema, not here; this endpoint is owner-only).
- `PATCH /api/config/personalclaw {path, value}` — single-field writes, allowlisted (`_EDITABLE_CONFIG`); non-editable paths return 400.
- `GET /api/config/schema` — the full field registry (labels, help, types, defaults, deprecations) auto-derived from the config dataclasses.
- `personalclaw config get|set <key> [value]` — CLI equivalent; `set` validates through the same loader.
