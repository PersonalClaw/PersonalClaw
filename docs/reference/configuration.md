# Configuration reference

PersonalClaw's core configuration lives in one JSON file: **`~/.personalclaw/config.json`**
(the directory can be relocated with the `PERSONALCLAW_HOME` environment variable).

Three ways to change it:

1. **Dashboard UI** — most fields have a control in Settings (the "Where to set"
   column below names the panel).
2. **CLI** — `personalclaw config get|set <key> [value]` (dot-separated keys, e.g.
   `personalclaw config set session.timeout_secs 7200`), or `personalclaw config edit`
   to open the file in `$EDITOR`. `config get` withholds credentials; add `--reveal`
   when you need them (see the [CLI reference](cli.md#personalclaw-config)).
3. **API** — `GET /api/config/personalclaw` (full config),
   `PATCH /api/config/personalclaw {path, value}` (single-field, allowlisted),
   `GET /api/config/schema` (the machine-readable field registry this document
   is derived from).

A key like `loops.max_cycles_hard_cap` means `{"loops": {"max_cycles_hard_cap": …}}`
in the file. Fields marked **backend-only** have no dashboard control — set them via
CLI/file (most need a gateway restart). Fields with a UI panel are applied live.

Write a boolean as `true` or `false`, unquoted. A boolean written as anything else, at any depth
in the file, is read as the field's default and the gateway log says so, with one exception: text
that spells false (`"false"`, `"no"`, `"off"`, `"0"`) is read as `false`, so a switch you turned
off in quotes stays off. Text that spells true is not read as `true`: the setting keeps its
default.

Not everything is in `config.json` by design. Stored elsewhere:

- **Model bindings** (which model serves chat/background/embedding/…): Settings →
  Models → `~/.personalclaw/active_models.json`.
- **Search bindings**: Settings → Search → `~/.personalclaw/active_search_providers.json`.
- **Inbox + notification entity settings**: `~/.personalclaw/entity_settings/*.json`
  (edited via the Inbox / Notifications settings panels).
- **Per-app config**: each app's `data/config.json` (edited via the app's Configure form).
- **Provider credentials**: the `.env` credential store (written by `personalclaw setup`).

### If `config.json` cannot be read

An **absent** file means a first run, and the defaults in the tables below are used. So does an
empty one — a bare `touch`, or a create that never got its bytes.

A file that exists and **cannot be parsed** (truncated by an interrupted or disk-full write, not
valid UTF-8, or holding something that is not a JSON object) is a different case: nothing is known
about what you stored, so the defaults are not your intent. PersonalClaw keeps running, and these
fields are held at their most restrictive value until the file is repaired or removed:

| Key | Held at | Instead of its default |
|---|---|---|
| `agent.unattended_requires_verified_adapter` | `true` | `false` |

Every other field falls back to its default; `agent.approval_mode`'s default already asks. Three consequences worth knowing:

- **PersonalClaw's own agent uses no tools.** The tool lists set on your agents are in the file,
  so none of them is known, and a list is a limit: whichever agent a turn runs as may use only
  `tool_result_get`, and each refused call says the configuration file could not be read (see
  [An agent's tool list](#an-agents-tool-list-agentstools)).

- **Your file is not overwritten.** The substitution is in memory only, and every config write
  refuses outright rather than clobbering a document whose contents it cannot preserve — so the
  original bytes stay on disk and stay recoverable. Fix the JSON (`personalclaw config edit` opens
  the file as it is, and the fixed copy replaces it), or move the file aside to start from
  defaults.
- **Ceilings are not substituted.** `guardrails.budgets.max_tokens_per_run` /
  `max_tokens_per_day` / `max_dollars_per_day` and `sandbox.max_pids` / `max_rss_mb` all treat `0`
  as *unlimited*, so an unreadable file does drop a ceiling you set. There is no restrictive number
  to stand in for one you chose, and inventing one would show you a bound you never set as if it
  were stored. Repair the file to get them back.

`personalclaw doctor` reports an unreadable `config.json` as an issue and names the fields it
substituted.

### When two things write it at once

The dashboard, the CLI and `personalclaw setup` can write `config.json` at the same moment, from
different processes. Each takes a lock beside the file (`config.json.lock`) for as long as it reads
and writes, so the writes happen one at a time and none is lost: two changes to different settings
both land, and two changes to the same setting land in the order they were made. A save writes only
the settings it changed. A writer that waits more than five seconds for another gives up and says
so, and writes nothing — the dashboard answers `503`, and trying again works.

---

## Agent runtime (`agent.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `agent.approval_mode` | enum: `interactive`, `trust_reads`, `auto` | `interactive` | Settings → Agent defaults | Who approves a tool call that needs approval. `interactive` (Ask each time) asks you: a chat on its card, and an agent no chat started (a trigger's Invoke Agent agent whose step does not set its own approval, a subagent started outside a chat) in your Inbox. `trust_reads` also lets a chat run a read-only shell command without asking, and asks about every other call that needs approval — including one to a tool that declares nothing. `auto` lets an agent no chat started approve every call it makes, file changes and shell commands included; chats still ask, as under `interactive`. A tool that declares it only reads asks nobody in any mode. An automation or a loop runs as its own Allow or Mode says, whatever this is: an Invoke Agent step's own `approval_mode`, a Run prompt action, a workflow run started unattended and a loop's Mode each carry their own consent. An app's agent work, its scheduled jobs included, approves none of its calls whatever this is: each that needs approval asks you. Loosening it asks for your consent; Doctor names a stored `auto`. |
| `agent.provider` | string | `native` | backend-only (restart) | Default agent runtime for agents that don't set their own: `native` (in-process loop, models governed by Settings → Models), `acp`, or `acp:<cli>` to pin a connected CLI runtime. Per-agent `provider` overrides this. File-only by design — switching it mid-flight would strand live sessions. |
| `agent.yolo` | boolean | `false` | Settings → Agent defaults | Skip every tool-approval confirmation. Only use inside a sandbox or for trusted automation. Settings asks before turning it on, and `PATCH /api/config/personalclaw` refuses `true` without `"confirm": true` (`400 confirmation_required`); turning it off never needs consent. A security setting: no app can write it (see *Programmatic surfaces*). |
| `agent.approval_timeout_minutes` | integer (1–10080) | `120` | Settings → Agent defaults | How long a tool approval waits for an answer before it is denied — the Inbox then says what was denied (`system/auto_denied`). An approval a running subagent or workflow step asks for also ends with that work's own time limit (a subagent waiting for its owner to approve its start is not running yet, and waits the whole window), and an MCP server's question ends with its call ceiling. A workflow's approval gates wait this long too, except in a run started unattended (`attended: false`), where a gate gives up after 45 seconds so the run says it stopped rather than parking on a question nobody will see. An automation or a loop allowed to run on its own never waits: a call its grant does not cover is declined at once. Any other agent no chat started asks in your Inbox and waits this long, within its own time limit. |
| `agent.approval_channel` | string (a channel's name) | `""` | Settings → Notifications → Send approvals to | Which chat channel asks you to approve a tool call (the Approval needed row's Channel DM target, and a subagent asking to start) when the chat asking did not start on a channel: a chat in PersonalClaw, an unattended run, a trigger. A chat that started on a channel is always asked in that chat, since the person asking is there, with or without the Channel DM target; with it, this setting decides who is tried after a chat's own channel that cannot ask. Empty asks the first connected channel that knows you, in name order (Discord, Email, Slack, Telegram). A channel's name (`telegram`, `discord`, `email`, `slack`) asks only there: while it cannot reach you, the approval waits in PersonalClaw and no other channel is asked. A save naming a channel this gateway has no channel app for is refused with a 400 listing the ones it has. |
| `agent.acp_concurrent_sessions` | boolean | `false` | Settings → Agent defaults | Run multiple ACP chat sessions on ONE backend process (multiplexing) instead of one process per session — for backends that support session interleaving. |
| `agent.bot_name` | string (≤50 chars) | `""` | Settings → Account | Custom name the assistant identifies as. Letters and combining marks from any script, digits, spaces, apostrophes, `-`, `.` and `_`. A save carrying any other character (braces, markdown, symbols, control or invisible characters) is refused with a 400 naming it; `load()` strips the same characters from a hand-edited file. Empty = default. |
| `agent.orchestrator_skill` | boolean | `false` | Settings → Agent defaults | Enable agent delegation — generates and loads the orchestrator skill with the agent roster. |
| `agent.max_subagents` | integer (0–16) | `3` | Settings → Agent defaults | Maximum concurrent subagents. `0` = auto-size from host CPU + memory. |
| `agent.spawn_min_memory_gb` | number (0–64) | `4.0` | Settings → Agent defaults | Minimum available memory (GB) required to spawn a subagent. `0` disables the check. |
| `agent.subagent_max_turns` | integer (1–200) | `100` | Settings → Agent defaults | Default tool-call budget per subagent. |
| `agent.subagent_timeout_secs` | integer (60–7200) | `1800` | Settings → Agent defaults | Wall-clock timeout per subagent execution, counted from when it starts running: time spent queued for a slot or waiting for its owner to approve its start does not count. |
| `agent.subagent_cwd_allowed_roots` | list of strings | `[]` | Settings → Agent defaults → Allowed working directories | Folders besides the workspace where the agent works (`~` expands). The agent's file tools (`read_file`, `list_dir`, `glob`, `grep`, `repo_map`, `code_map`) read files in them with no approval (an entry that is the filesystem root or a system folder they ignore), and `write_file` and `edit_file` change files in them under the same approval, with the same diff, as files in the workspace; a turn's rewind restores them too. A room member's file tools reach them as well, and what a member may do there is still its own tier's: a read-only member only reads. A subagent's `cwd` may name one. Empty by default, so the file tools and a subagent work in the workspace only, and a step of a workflow run in the folder that run owns: its scratch workspace, its own worktree, the folder its project is bound to when it works in place, or its project's context folder. The list is read at every call: a folder you take out is out of reach at the next one. An Invoke Agent or Run prompt trigger names one as its working folder, and its save is refused for any other. The folders you add as knowledge sources (Knowledge → Sources) are readable too, read only, and only the files each source takes in. A calendar (`.ics`) file the file tools may read in one of these folders is a calendar the agent reads with `calendar_events`, and each turn names it. |
| `agent.log_level` | enum: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `WARNING` | Settings → Agent defaults | Persistent backend log level. Applied at startup; the `--verbose` CLI flag overrides it. |
| `agent.soft_stop_budget_secs` | number (0.5–60) | `10.0` | Settings → Agent defaults | Seconds to wait for a cooperative cancel before hard-killing a session. |
| `agent.unattended_requires_verified_adapter` | boolean | `false` | Settings → Agent defaults | Refuse an UNATTENDED spawn onto an external agent runner whose ACP adapter has no verified provenance: an `npx -y` fetch-at-launch, an adapter that changed since it was provisioned, or a runner with no catalog row. "Unattended" is derived from the session key, so it covers cron fires, an Unattended loop's workers and planner (a loop's Mode decides whether its sessions are watched), subagents, heartbeat tasks, inbox and side sweeps, channel deliveries and trigger dispatches — no caller has to opt in. Interactive chat and an Attended loop are never gated. Fails closed: an unverifiable runner is refused. |
| `agent.runner_health_check_secs` | int (60–86400) | `3600` | Settings → Agent defaults | How long a runner's measured health evidence counts as current. Past this, its row under Settings → Agents → Runners is marked check overdue instead of presenting an old reading as the present state. Nothing is probed automatically — this only decides when a stored measurement stops counting as an answer. |
| `agent.runner_idle_release_secs` | int (60–86400) | `1800` | Settings → Agent defaults | How long a session may hold an agent runner without using it. Past this the hold is released: the row under Settings → Agents → Runners stops naming that session as the holder and reads as free. Only the RECORD of the hold is released — the session itself is untouched — so a session that went quiet, or a gateway that was killed mid-turn, cannot leave a runner looking permanently taken. |
| `agent.durable_sessions` | boolean | `false` | Settings → Agent defaults | Run workers inside a tmux session on PersonalClaw's own tmux socket so their shell outlives the gateway process. On restart the recovery sweep recomputes each session's name from the run's identity, and a run whose worker is still alive is marked resumable instead of aborted. Requires the `tmux` binary: without it the setting has no effect and behaviour is exactly as today. |

The chat **model** is not a config field — bind models per use case in Settings →
Models, or per agent on the Agents page.

## Sessions (`session.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `session.timeout_secs` | integer (0–86400) | `3600` | Settings → Chat | Idle session timeout in seconds. |
| `session.autocompact_pct` | number (5–90) | `90.0` | Settings → Chat | Context usage percentage at which a chat compacts. An agent CLI whose app says it compacts itself does so at its own limit; any other agent CLI's session is restarted here, from the chat's history, and the chat says so. |
| `session.pool_size` | integer (0–10) | `0` | Settings → Chat | Pre-spawned ACP agent processes kept warm for instant session start. `0` disables. Only useful for ACP agents (subprocess spawn is the cost); the native runtime needs no pool. |
| `session.pool_agent` | string | `""` | Settings → Chat | Agent name for warm-pool processes. Empty uses `default_agent`. |
| `session.pool_ttl_secs` | integer (0–7200) | `1800` | Settings → Chat | Max age for pooled processes; stale ones are discarded at claim time. `0` disables. |

## Goal loops (`loops.*`)

Per-loop values (set in the loop creation form) override the defaults, and the hard cap
binds everything. The four runtime knobs are on **Settings → Autonomous loops**; the rest
are operator knobs with no control.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `loops.max_cycles_hard_cap` | integer | `100` | backend-only | Absolute ceiling on any loop's cycle budget, regardless of the per-loop limit. Safety brake against runaway cost. |
| `loops.default_idle_secs` | integer | `120` | backend-only | Seconds between worker cycles when a loop doesn't specify its own idle timer. |
| `loops.trust_ttl_secs` | integer | `86400` | backend-only | How long an Unattended loop's workers run their tool calls without asking before the supervisor pauses the loop for you to resume it. An Attended loop asks you for each call and holds no such grant. |
| `loops.judge_use_case` | enum: `reasoning`, `chat`, `code_tools`, `background`, `orchestration`, `loops` | `reasoning` | Settings → Autonomous loops | Which model use case the loop JUDGE rides — deliberately not the `loops` axis the worker rides, so a reviewer mistake is not correlated with the mistake it is reviewing. Set to `loops` to put judge and worker back on one binding. |
| `loops.stagnation_window` | integer (2–50) | `5` | Settings → Autonomous loops | How many consecutive cycles of no progress stall a loop. The supervisor compares the last N findings: byte-identical reports, an identical source set, or N cycles with no new findings all mean the loop is spinning. Minimum 2 — a window of 1 can only compare a cycle with itself. |
| `loops.check_work_stages` | boolean | `false` | Settings → Autonomous loops | After an SDLC stage's gate passes, re-derive 2–4 executable checks from what the stage CLAIMED and run them. Catches "the gate command passed but the claim was broader than the command". Off by default: it adds a filesystem pass per stage advance. |
| `loops.worktree_sparse` | boolean | `true` | Settings → Autonomous loops | When a parallel task's plan names the files it will touch, hydrate only those directories in its git worktree instead of the whole repo — most of a worktree's setup cost on a large codebase. A task that writes outside its stated scope widens its own worktree automatically, a task with no usable scope gets a full checkout, and the merged result is identical either way. Set `false` to always hydrate the full repo. |

## AI feedback (`feedback.*`)

The 👍/👎 capture on AI judgment outputs and the deterministic per-producer accuracy
thresholds. All four have a control in **Settings → AI feedback**. No model call anywhere
and no telemetry: every number here is counted locally.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `feedback.enabled` | boolean | `true` | Settings → AI feedback | Show 👍/👎 on AI judgment outputs (inbox classifications, drafted replies, digests, loop findings) and track per-source accuracy. Off is a full kill switch — every `/api/feedback` route 404s and the thumbs stop rendering. Verdicts already collected are kept. |
| `feedback.retire_threshold` | number (0.1–0.9) | `0.4` | Settings → AI feedback | Accuracy below which a judgment source earns a "retire this rule?" proposal, once it has at least `min_n` verdicts — and stops surfacing where that kind of source has a surfacing gate (today, skills). Higher retires sooner. |
| `feedback.min_n` | integer (3–50) | `5` | Settings → AI feedback | Verdicts required before a source's accuracy is shown or acted on. Below this its row reads "collecting" and no number is displayed. |
| `feedback.window_days` | integer (7–365) | `90` | Settings → AI feedback | How far back verdicts count toward a source's rolling accuracy. Shorter forgets an old mistake faster. |

## Memory (`memory.*`)

Behavior toggles live in Settings → Memory; tuning constants are backend-only.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `memory.semantic_confidence_threshold` | number | `0.8` | Settings → Memory | Confidence (0-1) an automatically learned semantic fact needs before memory keeps it; facts you add yourself are always kept. Read live — a change applies on the next write. |
| `memory.episodic_dedup_threshold` | number | `0.88` | backend-only | Cosine similarity above which a new episodic record is treated as a duplicate and skipped. |
| `memory.episodic_max_results` | integer | `8` | backend-only | Episodic records recalled per query. |
| `memory.episodic_max_count` | integer | `10000` | backend-only | Episodic store size cap; oldest records are pruned past it. |
| `memory.semantic_keys` | list of strings | `[]` | backend-only | Extra top-level semantic-record prefixes (namespaces) beyond the built-ins. |
| `memory.l1_manifest` | boolean | `true` | Settings → Memory | Inject only a small always-on manifest of your most-recalled facts; the agent pulls deeper memory on demand via the `memory_recall` tool. Off = inject full semantic + episodic memory every turn (legacy). |
| `memory.active_recall` | boolean | `true` | Settings → Memory | On an interactive turn, surface query-relevant memory just before the reply — bounded by a timeout + circuit breaker. Skipped for temporary and headless turns; an incognito turn recalls by keyword, without the embedding model. |
| `memory.proactive_commitments` | boolean | `false` | Settings → Memory | Let the agent infer future check-ins from conversation and deliver ONE natural reminder per window via the heartbeat. Opt-in; high-confidence only; capped per day; one-tap dismiss. |
| `memory.proactive_commitments_max_per_day` | integer | `3` | backend-only | Hard maximum active proactive check-ins per agent per day. |
| `memory.active_recall_timeout_ms` | integer | `1500` | backend-only | Hard budget for the pre-reply recall pass; on timeout the turn proceeds without it (circuit breaker trips after repeats). |
| `memory.auto_promote_enabled` | boolean | `true` | backend-only | Periodically promote repeated episodic memories into durable semantic facts (the self-learning loop) — guarded by a per-run cap + min-interval + single-flight. Off = promotion only via the Memory Studio button. |
| `memory.auto_promote_every_n` | integer | `10` | backend-only | Run promotion after every Nth history consolidation. |
| `memory.auto_promote_max_per_run` | integer | `5` | backend-only | Cap on clusters promoted in a single autonomous run. |
| `memory.history_idle_hours` | number (≥0.5) | `3.0` | Settings → Memory | Hours of inactivity before history consolidation. |
| `memory.history_max_days` | integer (≥7) | `365` | Settings → Memory | Maximum days of history to retain. |
| `memory.migrated` | boolean | `false` | managed automatically | Whether memory has been migrated to the vector store (set by `personalclaw memory migrate` / the API). |
| `memory.vault_mode` | `off` \| `mirror` \| `two_way` | `off` | Settings → Memory | The readable markdown vault (Obsidian-compatible: YAML frontmatter + `[[wikilinks]]` + graph view). `mirror` writes memory out and never reads it back — hand edits are overwritten. `two_way` also reads hand-edited pages back into memory on the next sync (your edit wins); a page the sync cannot parse confidently is left untouched and reported under Settings → Memory → Health. Back-reads the retired `memory.vault_enabled` bool: `true` loads as `mirror`, never `two_way`. |
| `memory.vault_path` | string | `memory-vault` | Settings → Memory | Where the markdown vault is written. Relative paths resolve under `~/.personalclaw`; absolute paths are used as-is. Only the default path is covered by `personalclaw snapshot`. |
| `memory.graph_enabled` | boolean | `true` | Settings → Memory | Link each memory to the people, projects and tools it mentions, so "what do I know about X?" follows links instead of relying on similarity search. Matching is exact-name and costs no tokens or LLM calls. Off = every graph surface falls back to today's search behavior; existing links are kept, so re-enabling needs no backfill. |
| `memory.push_context` | boolean | `false` | Settings → Memory | When a message names something the entity graph knows, volunteer up to 3 linked memories for that turn — even ones sharing no words with what you typed. Opt-in: it puts context in front of the model you did not ask for. Settings → Memory → Health reports how often what it volunteered was actually used. Never injects knowledge items (chips only). |
| `memory.push_min_confidence` | number (0-1) | `0.7` | Settings → Memory | How sure the entity match must be before memory is volunteered. Higher = declared aliases and exact names only; lower also admits looser matches (more offered, more of it irrelevant). |
| `memory.graph_topology_in_context` | boolean | `false` | Settings → Memory | At the start of a new session, add a ≤400-char map of the neighbourhoods in your memory graph ("people around project X") so the assistant knows which areas exist before it searches. Off by default: it spends context on every new session and says nothing useful until the graph has distinct communities. |
| `memory.holder_attribution` | boolean | `false` | Settings → Memory | Record WHOSE claim a memory is (you, the assistant, a named person, an outside source) and render it that way ("Alex believes…"). Second-hand claims are weight-capped lower, and a lower-authority claim can never retire something you said. Off = every memory is stored unattributed; already-attributed rows keep their holder, so flipping it back on needs no backfill. |
| `memory.slot_size_cap` | integer (200-4000) | `1400` | Settings → Memory | Character budget for the ONE always-injected Slots block (persona, preferences, pending items, glossary, self notes, self model). A spend paid on every session, so it is clamped at the consumer — a value outside the range cannot widen the block by editing this file. The per-slot caps that decide which individual register is full are fixed in code. |

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
| `skills.progressive_disclosure_threshold` | integer | `2` | backend-only | When more skills than this match a turn, inject only their index (name + description) and let the agent pull bodies on demand via `skill_invoke`. Clamped to `max_triggered - 1` (floor 1) — the match list is already capped at `max_triggered`, so a threshold at or above it can never be exceeded. `0` = always inline, and is never clamped. |

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

## Workflows (`workflows.*`)

The engine's runtime knobs live on **Settings → Workflows**. Workflow definitions, runs and
triggers are the `#/workflows` page, not this file.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `workflows.enabled` | boolean | `true` | Settings → Workflows | Master switch. Off stops new runs from starting and leaves stored definitions untouched. |
| `workflows.self_schedule_max_outstanding` | integer (0–200) | `20` | Settings → Workflows | How many enabled automations the agent may hold at once via its own scheduling tools. Counted over ENABLED agent-created automations, so pausing one frees a slot without deleting it. |
| `workflows.max_concurrent_llm_nodes` | integer (1–32) | `4` | Settings → Workflows | How many model-backed nodes may run at once in one workflow. |
| `workflows.max_concurrent_io_nodes` | integer (1–32) | `2` | Settings → Workflows | How many action nodes may run at once. Kept low on purpose: a fan-out over minutes-long local-model actions would otherwise starve the run's model calls. |
| `workflows.default_node_timeout_total_secs` | integer (0–86400) | `900` | Settings → Workflows | Wall-clock cap for one node. `0` disables it. |
| `workflows.default_node_timeout_stall_secs` | integer (0–86400) | `300` | Settings → Workflows | Stop a node after this long with NO progress, even under the total cap. Progress events reset the clock. `0` disables it. |
| `workflows.lease_ttl_secs` | integer (30–3600) | `900` | Settings → Workflows | How long a session's exclusive claim on a task lasts before another may take it. Deliberately short: a worker that needs longer renews, which proves it is alive. |
| `workflows.model_tier_reasoning` | string | `reasoning` | Settings → Workflows | Which model use case a node asking for the `reasoning` tier resolves to. Templates name an intent, never a model, so they stay portable. |
| `workflows.model_tier_standard` | string | `orchestration` | Settings → Workflows | Use case for the `standard` tier. Distinct from `fast` on purpose: collapsed onto one use case, the three tiers are decorative. |
| `workflows.model_tier_fast` | string | `background` | Settings → Workflows | Use case for the `fast` tier. |
| `workflows.surface_mode_default` | enum: `off`, `passive`, `suggest` | `off` | Settings → Workflows | What a NEWLY authored workflow does before you opt it in. `off` never surfaces itself (explicit invocation always works), `passive` injects its guidance, `suggest` may propose running itself. |
| `workflows.match_threshold` | number (0–1) | `0.62` | Settings → Workflows | How confident the embedding tie-breaker must be to override a keyword tie when two templates score alike. Only consulted on a tie — keyword matches decide first. |
| `workflows.max_materialized_per_foreach` | integer (1–500) | `20` | Settings → Workflows | The most Tasks one `foreach` node may put on your board. The run still executes every item; only the board rows are capped, and the run reports what it withheld. |
| `workflows.retention_per_def` | integer (1–10000) | `100` | Settings → Workflows | Oldest runs beyond this are pruned. Matches the per-job cap schedules use. |
| `workflows.confirmation_ttl_secs` | integer (0–2592000) | `604800` | Settings → Workflows | How long a pending approval stays live. A week, because the realistic case is being away. `0` never expires. A destructive confirmation auto-REJECTS on expiry; an ordinary one keeps waiting. |
| `workflows.default_quiet_windows` | string (`HH:MM-HH:MM`) | `""` | Settings → Workflows | A quiet window applied to new automations that set none of their own. May wrap midnight. Empty means no default. Per-trigger settings always win. |
| `workflows.duty_gate_default` | string | `""` | Settings → Workflows | The is-the-user-on-duty check applied to new automations that name none. Empty means no gate; `manual` is the built-in toggle and apps can supply others. The gate always fails OPEN. |
| `workflows.workspace_default_mode` | enum: `scratch`, `worktree`, `in_place`, `container` | `scratch` | Settings → Workflows | Where a run works when its template declares no `workspace.mode`. A template's own declaration always wins. `in_place` is deliberately never the default — that is the mode in which a destructive step runs against real state. |
| `workflows.workspace_teardown_on_expiry` | boolean | `true` | Settings → Workflows | Run a workspace's declared `teardown` before its directory is deleted by retention or an explicit delete. On, because teardown's job is to stop services while the directory still exists. |
`workflows.max_concurrent_nodes` was **removed** in this release (#465). It claimed to be the
per-run total "partitioned across typed lanes", and `lane_caps()` never consulted it — the two
`max_concurrent_*_nodes` rows above are the live partition. A stored value for it is ignored.

`workflows.max_active_runs` was also **removed** (#465). It had no reader and imposed no runtime
cap, so removing it changes no run-start behaviour; a stored value is ignored.

## Security (`security.*`)

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `security.denied_commands` | list of regexes (≤100) | `[]` | Settings → Security | User-added regexes for commands PersonalClaw must never run for the agent or an automation, appended to the always-on built-in denylist. Every path that runs a command asks them before it runs (the bash tool, an agent CLI's request, loop and workflow checks, workflow steps, bash actions, app setup hooks). Matched case-insensitively against the full command string. |
| `security.egress.allow_hosts` | list of strings | `[]` | Settings → Security | Hosts (bare domain covers subdomains). The agent's fetches, scrapes and webhooks may reach these even when they resolve to a private/LAN address — for homelab webhooks/services. They are also the only hosts the agent's shell commands (an agent CLI's when it asks first), and the programs apps start, reach without a person saying yes: a chat asks about any other, an unattended run (a loop, a schedule) is refused it, and an app's program is stopped unless its install review named the host. On the exclusive surfaces (the net-fetch action, outbound A2A) they are the only reach there is. |
| `security.egress.deny_hosts` | list of strings | `[]` | Settings → Security | Hosts the agent must never reach, even if public. A deny always overrides an allow. |
| `security.egress.allow_private` | boolean | `false` | Settings → Security | Permit the agent's fetches to reach any private/LAN address, not just the allowed hosts. Only enable on a fully trusted network — it removes SSRF protection for the whole LAN. A shell command still reaches only the allowed hosts without asking. |
| `security.outside_home` | list of strings | `[]` | Settings → Security → Outside PersonalClaw's home | The places outside the PersonalClaw home it may read, by name: `agent-skills` (`~/.agents/skills`, the skills other AI tools share), `huggingface-cache` (the machine-wide Hugging Face folder and its `huggingface-cli login` token), `sign-in:<source>` (a subscription provider's sign-in, such as Claude Code's) and `setup:<tool>` (another agent tool's setup, which the Tools page's import list and onboarding's Bring your setup over then list on every visit: `setup:claude_code` is `~/.claude`, `~/.claude.json` and the files of each project Claude Code lists, `setup:codex` is `~/.codex` and `~/.agents/skills`). Empty keeps every read and write inside the home, except a tool's setup you press **Look in** for, which that press reads once. Adding one asks you to confirm. PersonalClaw never writes or deletes in these places. |

## Inbox (`inbox.*`)

Alert keywords, name-mention alerts, and retention live in the Inbox settings panel
(entity store, not `config.json`). Config-side:

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `inbox.enabled` | boolean | `false` | Inbox → Settings ("Poll the drop folder" toggle) | Polls the built-in drop folder, `<home>/inbox/incoming/`, where a program on this machine can drop messages as JSON files. Off by default, since anything that can write to the machine can drop one there. It is not a switch for the inbox apps: an installed inbox app (Mail Inbox, Slack) is polled while its app is enabled. The inbox reads it at every poll, so a change applies at the next one, with no restart.
| `inbox.user_id` | string | `""` | channel-app setup | Your user id on the connected channel — used to skip your own messages. |
| `inbox.watched_channels` | list of strings | `[]` | Settings → Inbox ("Channels to read", shown while a polled source reads channels) | The channels an installed chat app's inbox source reads into the Inbox (Slack's), each by its id. Every source's poll is handed the list; one that reads it says so (`watches_channels`). Read at every poll, so an added channel is read from the next one; its first read starts after its newest message. An entry that is not one id is refused. |
| `inbox.poll_interval_seconds` | integer (min 30) | `60` | backend-only | Poll cadence. |
| `inbox.style_rules` | list of strings | `[]` | backend-only | Voice/style lines injected into AI reply drafting. |
| `inbox.sort_messages` | boolean | `true` | Settings → Inbox ("Sort new messages") | Sorts each new message into Needs reply, FYI or Noise on the background model, a few messages per call, inside the daily spend ceiling and never in incident mode. Off, messages arrive unsorted ("Not sorted yet") and nothing is sent to a model for them. Read before every sorting call, so it binds at once. |
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
| `tools.bg_compress_enabled` | boolean | `true` | Settings → Chat | Summarize the older part of idle chats in the background (topic-segmented, attention-weighted), so the history handed to the model when one is picked up again opens with a short summary instead of every message. Chats are never changed: every message stays as you left it. The summary is kept beside the chat (`sessions/{key}.summary.json`), stops being used the moment a message it covers changes, and is deleted with the chat. Uses the background model. Incognito/temporary chats are never summarized. |
| `tools.bg_compress_idle_days` | number (0–365) | `7.0` | Settings → Chat | Only summarize chats untouched for at least this long — an active chat is never summarized. |

## Voice (`voice.*`)

Behaviour of dictation and spoken replies. The MODEL for speech-to-text and
text-to-speech is bound in Settings → Models; these are the provider-agnostic knobs on
top of it. All of them are comfort settings rather than safety guards — turning one off
makes the voice loop noisier, never less safe.

Two text-to-speech switches are not keys here: **Enable text-to-speech** (any speech at all,
including the Speak button on a reply) and **Speak replies aloud** (each reply read out as soon
as it finishes, in the tab the message was sent from). Both are the Text-to-speech use case's
own settings (`enabled` and `auto_speak`), set in Settings → Speech & Transcription. A change
reaches a chat that is already open, in any tab or on another device: a reply that finishes after
**Speak replies aloud** is switched on is read out, and switching it off stops one being read.
A reading plays only in the tab that asked for it, the tab that sent the message or the one where
Speak was pressed, so a chat open twice is read out once. The `voice.*` keys below reach an open
chat the same way.

ffmpeg is not a key either. PersonalClaw runs it to cut a recording longer than the segment
threshold into parts before it is transcribed, to take the sound and frames out of a video, and
to join a spoken reply's sentences; a short recording is transcribed without it. It is looked for
on the `PATH` the gateway started with, then in `~/.local/bin`, `/opt/homebrew/bin` and
`/usr/local/bin`, and nowhere else, and it is run by that path: the gateway's own `PATH`, which
every program it starts inherits, is never changed to find it. Settings → Speech &
Transcription shows the one it found, or where it looked when there is none.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `voice.push_to_talk_chord` | string | `CommandOrControl+Shift+Space` | Settings → Speech & Transcription | The global shortcut the **desktop app** binds for push-to-talk: press to start capturing the microphone, press again to stop and transcribe into the composer at your cursor. An Electron accelerator string; needs at least one modifier, since a bare key would be taken from every other app on the machine. The desktop shell binds it and refuses an unusable or already-taken chord with a reason. Ignored in a browser tab (no global shortcuts). See [the desktop guide](../guides/desktop.md). |
| `voice.confirmation_phrases` | list of strings | `["do it", "go ahead", "send it", "execute"]` | Settings → Speech & Transcription | In hands-free mode a transcript accumulates and is only sent once one of these phrases ends what you just said, so a half-finished thought never becomes an executed instruction. A phrase is matched however speech-to-text writes it: capitals, punctuation, hyphens, apostrophes and the spaces between its words make no difference, so "Sendit." is "send it". Only whole words count, and a confirmation is never assembled from two words you said ("Goa head" is not "go ahead"), so write each of its words as a word. Push-to-talk and typed input ignore this. An empty list falls back to these defaults. |
| `voice.exit_phrases` | list of strings | `["cancel", "never mind", "forget it"]` | Settings → Speech & Transcription | Saying one of these in hands-free mode discards the accumulated transcript without sending it, and it wins over a confirmation said in the same breath ("send it, never mind" sends nothing). Matched like the confirmation phrases, and also when speech-to-text splits one of its words in two, so "Nevermind", "never-mind" and "never mind" all match either spelling of the phrase. |
| `voice.duplex_mute_enabled` | boolean | `true` | Settings → Speech & Transcription | Suspend the microphone and discard queued audio while a spoken reply plays. This is what stops the assistant hearing itself. |
| `voice.echo_filter_enabled` | boolean | `true` | Settings → Speech & Transcription | Drop a transcription sharing three consecutive words with what the assistant just spoke — the backstop for speaker bleed. Hands-free requests only; the dashboard shows the drop instead of looking deaf. |
| `voice.clean_for_speech_enabled` | boolean | `true` | Settings → Speech & Transcription | Strip code blocks, reduce URLs to their domain and paths to their filename, and drop CLI flags before synthesis. The chat transcript always keeps the full text — only the audio is cleaned. |
| `voice.voice_disclaimer_enabled` | boolean | `true` | Settings → Speech & Transcription | Append a one-line note to a dictated message telling the model the text came from speech recognition, so it self-corrects garbled homophones instead of confidently misreading them. |

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
| `dashboard.screen_share_enabled` | boolean | `false` | Settings → Chat | Master opt-in for the composer's "Share screen" control. Off (the default) hides the control **and** makes the server refuse a frame outright. On, a message can carry ONE frame of a screen/window you pick in the browser's own share dialog: held in memory for that single turn, never written to disk, dropped as soon as it is used. Pinning a frame (composer "+" → "Pin shared frame") is the only path to disk, and is refused in temporary/incognito chats. |
| `dashboard.auto_open_browser` | boolean | `true` | backend-only | Open the dashboard in a browser on gateway start (`--no-open` overrides per-run). |
| `dashboard.terminal` | object | `{"enabled": true}` | `enabled`: backend-only; `persist`: Terminal page | `enabled` is the kill switch for the built-in terminal (PTY) feature, read raw with a 30s cache. `persist` (tmux-backed persistence across gateway restarts) is toggled on the Terminal page. |

## Background work (`background.*`)

The limits on work nobody is watching, and how long a reply waits for a local model that is busy
with it. They are under the Background chain in **Settings → Models** (open Background, then
Limits). Each is read when a call is made, so a change applies to the next call with no restart.
None is a security setting: a longer limit only waits longer, and the spend ceilings
(`guardrails.budgets.*`) still bound what the calls cost. A hand-edited value outside its window
loads as the nearest end of it; the dashboard and `personalclaw config set` refuse it.

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `background.call_timeout_secs` | number (30–3600) | `300.0` | Settings → Models → Background | How long a background task may run before it is stopped and the next model of the Background chain is asked: a chat's chores, knowledge processing, digests, a schedule being read. Time spent waiting for its turn on a busy local model counts. A provider's own Request Timeout still bounds how long the answer may take to start. A call stopped by it says so, and where to raise it. |
| `background.max_output_tokens` | integer (512–65536) | `4096` | Settings → Models → Background | Most text a background task may write in one answer: a chat's title, its memory consolidation, suggestions and follow-ups, and heartbeat tasks (every turn of an agent on the Background chain). A one-shot background call keeps its model's own output limit. An answer to a chore or a one-shot call stopped at its limit is not read as one: the chain's next model is asked, and a consolidation none could finish is owed and said in a notice. A heartbeat task's result cut at it says so. A change rebuilds the background session at its next chore. |
| `background.busy_model_wait_secs` | number (0–300) | `15.0` | Settings → Models → Background | How long a reply waits for a busy local model before asking the next one. A reply, a step it runs or a page's answer goes ahead of background work on a model on this machine; when the call already running holds the model and the chain has another model, it waits this long, then that model answers. `0` asks the next model at once. |

## Top-level keys

| Key | Type | Default | Where to set | Description |
|---|---|---|---|---|
| `hooks` | object | `{}` | Triggers page / `/api/hooks` | The rules applied to messages and tool calls (auto-approve and auto-deny lists, auto-replies, transforms, context rules), plus `webhook_token` (kept in the credential store — the file holds a `{{secret:…}}` reference; set it with `personalclaw config set hooks.webhook_token <token>`; at least 32 characters, and the token a program sends to `POST /api/hooks/agent`, see [security](../architecture/security.md#webhooks)) and `auto_approve_sources`. Managed via the Triggers UI; documented here because the raw shape is config-visible. Two switches decide for subagents, each `false` unless set, each bounded by the operator ceiling: `auto_approve_subagent_spawn` starts a subagent without asking you (a chat's, a workflow step's, an Invoke Agent step's) and decides nothing it then does, so its calls are decided as any subagent's are; `auto_approve_subagent_tools` approves a subagent's own tool calls, and an Invoke Agent step's Allow says so. |
| `observe_max_messages` | integer | `200` | backend-only | Channel-observation ring-buffer size (messages kept per channel for context). |
| `observe_ttl_hours` | number | `168.0` | backend-only | How long observed channel messages stay usable as context. |
| `agents` | object | `{}` | Agents page | Named agent definitions (see below). |
| `default_agent` | string | `""` | Settings → Agent defaults | Active agent name from the `agents` section (also `PUT /api/config/default-agent`). |
| `memory_stores` | object | `{}` | backend-only | Named memory store definitions; `memory_stores.<name>.description` is a human-readable purpose. Stores are referenced by agent profiles. |
| `updates.auto` | string | `"off"` | Settings → Updates (source checkout only) | Opt-in unattended-apply mode (retired the legacy `auto_update` bool). `"off"` only notifies; `"staged"` applies at the next safe point — held while a session/subagent is in flight, and only ever the resolved channel/pin release tag, never raw `main`. Only a source checkout applies unattended: on a pip/uv install, the container image or the desktop app `"staged"` notifies exactly as `"off"` does, and Settings shows no control for it. |
| `updates.pin` | string | `""` | Settings → Updates | Stay on one exact release (`0.1.3`, or `0.3.0-rc.1` for a release candidate, which `0.3.0rc1` also names), overriding the channel. Only a release version is accepted — a version line, range or typo is refused when saved, here and by `personalclaw update --to`. A pin no published release carries offers and installs nothing, and Settings → Updates says so. Empty follows the channel. |
| `timezone` | string | `""` (system) | set by `personalclaw setup` | IANA timezone (e.g. `Asia/Tokyo`) for schedules, the clock the LLM sees, and the day spend is counted in: Settings → Usage's Today, 7 and 30 days and chart, the Settings tile, the daily spend caps (they start afresh at midnight in it) and the monthly usage recap. Empty uses this machine's timezone, else UTC. Per-job trigger timezones override it for that job only. |
| `snapshot_dir` | string | `""` | backend-only | Where `personalclaw snapshot` writes/reads portability snapshots. Empty = `~/.personalclaw/snapshots`. |

## Agent definitions (`agents.<name>.*`)

Managed on the **Agents page** (create/edit forms); stored under `agents` keyed by
agent name. Every field is optional — empty inherits the global default.

| Key | Type | Default | Description |
|---|---|---|---|
| `agents.*.provider` | string | `""` | Runtime backend: `native` (in-process loop) or `acp:<cli>` (external CLI). Empty inherits the global `agent.provider`. |
| `agents.*.provider_agent` | string | `""` | ACP provider agent name (modeId for `session/set_mode`). |
| `agents.*.acp_mode` | string | `""` | ACP permission/operating mode for adapters that expose one (e.g. `default`, `acceptEdits`, `plan`, `bypassPermissions`). Distinct from Approval Mode (the host gate). A mode that lets the CLI approve its own calls is sent as `default` instead, except on an Unattended loop whose owner let its CLI approve its own calls. A chat in the Plan task mode sends `plan`, and a CLI already running is told each change of mode before the next turn's prompt. |
| `agents.*.default_dir` | string | `""` | Working directory this agent opens in. Empty inherits the workspace root. Overridable per-session. |
| `agents.*.memory_store` | string | `""` | Memory provider for this agent. Empty uses the filesystem fallback scoped by working directory. |
| `agents.*.description` | string | `""` | Human-readable agent description. |
| `agents.*.system_prompt` | string | `""` | System prompt injected at session start. |
| `agents.*.voice` | string | `""` | WHO the agent is — tone, opinions, persona — kept separate from the operating rules and injected high-priority so personality survives long prompts. |
| `agents.*.model` | string | `""` | Default model for this agent. Overridable per-chat. |
| `agents.*.approval_mode` | string | `""` | `auto`, `interactive`, or empty (inherit global). |
| `agents.*.skills` | list | `[]` | The skills this agent may use: skill names. Empty is every skill. Held on PersonalClaw's own agent, not on an agent CLI — see [An agent's skill list](#an-agents-skill-list-agentsskills). |
| `agents.*.tools` | list | `[]` | The tools this agent may use: tool names, or patterns over them. Empty is every tool. A save that widens it asks you first. Held on PersonalClaw's own agent, not on an agent CLI — see [An agent's tool list](#an-agents-tool-list-agentstools). |
| `agents.*.triggers` | list | `[]` | Referenced lifecycle-trigger IDs. A lifecycle trigger fires ONLY for agents that list it. |
| `agents.*.source` | string | `personalclaw` | Agent origin: `personalclaw`, `marketplace`, or `builtin`. |

### An agent's tool list (`agents.*.tools`)

The Tools list on the Agents page. Empty, the default and the default agent's, lets the agent use
every tool PersonalClaw offers it. A list is the whole of what the agent may use:

- **An entry is a tool's name or a pattern over it.** The name as the Tools page lists it
  (`read_file`, `memory_recall`, `mcp/<server>/<tool>`), or a pattern in which `*` stands for any
  run of characters, `?` for one and `[…]` for one of a set. A pattern is matched against the
  whole name and case-sensitively, the way the operator ceiling's tool allowlist is: `mcp/files/*`
  is every tool of the MCP server `files`, and `READ_FILE` is no tool. There are no group names;
  list a group's tools, or a pattern that covers them.
- **PersonalClaw's own agent (`native`) holds it on every turn it runs**: a chat, a subagent, a
  loop's worker, an automation, a workflow step, a heartbeat task and the OpenAI-compatible
  endpoint alike. Its model is shown nothing else, and a call to any other tool is refused before
  anyone is asked about it, whatever would otherwise have approved it, so a model that names a
  tool it was not shown cannot reach it. The refusal names the tool and the agent; the audit log
  records the call `denied`, decided by `agent_tools`; the gateway log has a WARNING line for each
  refused call, an INFO line (`agent.log_level` `INFO`, or `personalclaw -v gateway`) saying what
  each narrowed catalog keeps of what is on offer, and a WARNING instead when an entry matches no
  tool on offer (a tool not installed here, or a typo). A chat that names no agent runs as the
  default agent, held to the default agent's list, and an edit of the list holds an open chat from
  its next turn.
- **Two kinds of tool are not the list's to remove.** `tool_result_get` is kept on every list: it
  reads back the rest of a long answer one of the agent's own calls gave, which every cut answer
  names. The runtime's own tools for finding the agent's tools (`tool_search`, `tool_schema`,
  `reset_tools`) search only what the list allows.
- **A list that cannot be read** (a hand-edited value that is not a list of names) lets the agent
  use nothing but `tool_result_get` until it is fixed, and its refusals say so. While `config.json`
  itself cannot be read, no list in it can, so the same holds for every agent until the file is
  repaired (see [If `config.json` cannot be read](#if-configjson-cannot-be-read)).
- **A built-in agent's list is PersonalClaw's own.** The Agents page shows it and does not edit
  it, and a refusal says PersonalClaw gives the agent its tools. The template refiner, which the
  `refine-template` and `optimize-harness` workflows run, has the one such list: it may use
  `refiner_evidence`, which reads a template's failure evidence already screened, and
  `propose_template_diff`, which files a change for a person to review, and nothing else.
- **A list only narrows.** A tool switched off on the Tools page stays off for every agent, and a
  run held to fewer tools (a read-only subagent, an app's agent tier, the operator ceiling's
  `tools` scope) is held to both.
- **A wider list asks you first.** A save that lets the agent call a tool it could not call before
  (a tool or a pattern added, or the list emptied, which is every tool) asks you to confirm, in the
  dialog a looser approval mode asks in, "Loosen a security setting?", naming the change: "Adds
  “bash”", "“read_file”, “grep” → Every tool". A save that only takes tools away asks nothing, and
  neither does the first tool ticked on an empty list, which narrows the agent to that one tool, nor
  a new agent's list, since a new agent starts from every tool. The direction is the list's own
  matcher's: a tool's name that a pattern on the list already matches adds nothing. A request that
  answers the question carries `"confirm": true`; without it the save is refused with
  `400 confirmation_required` and nothing is stored.
- **Work an agent starts runs as the agent it names.** A tool that starts other work
  (`subagent_run`, a workflow, a loop) starts it held to the list of the agent the work runs as: a
  spawn that names no agent runs as the agent of the chat that started it. Leave those tools off
  the list to keep an agent to its own tools.
- **An agent CLI (`acp:<cli>`) is not held to it.** The CLI runs its own tools in its own process,
  where no list of PersonalClaw's tool names can hold them, so the Agents page says the list is not
  applied to an agent that runs on one.

### An agent's skill list (`agents.*.skills`)

The Skills list on the Agents page. Empty, the default and the default agent's, lets the agent use
every skill. A list is the whole of what the agent may use:

- **An entry is a skill's name** as the Skills page lists it (`tiny-url`, `auto/release`). An entry
  that names no skill here holds the agent all the same, and the Agents page shows it as a ticked
  row saying so.
- **PersonalClaw's own agent (`native`) is held to it in both places a skill reaches the model.**
  Its turns are offered its skills: the always-on ones in full and an index of the rest when the
  session starts, and the ones that fit the message on every turn (see
  [How a skill reaches the model](../guides/skills.md#how-a-skill-reaches-the-model)). And its skill
  tools reach no other: `skill_search` finds only its skills, and `skill_invoke` or `skill_resource`
  for any other is refused, naming the skill and the agent. A chat that names no agent runs as the
  default agent, held to the default agent's list, and an edit of the list holds an open chat from
  its next turn.
- **With no list, an agent is offered what it always was.** The default agent's turns are offered
  every skill; another agent's, which carry their own instructions, none up front, though the agent
  can look any skill up with `skill_search`. The built-in loop worker (`personalclaw-loop`) is one:
  its own instructions carry a loop's protocol.
- **Two kinds of skill a list does not narrow**: a skill in the agent's own folder
  (`<home>/agents/<agent>/skills`), which only it sees, and the skills a loop's plan gives the phase
  the agent works, which you confirm when you review the plan: they load on the loop's turns, and
  the agent's skill tools reach them there.
- **A list that cannot be read** (a hand-edited value that is not a list of names) limits nothing:
  the agent is offered skills as an agent with no list is, and the gateway log says so. A skill is
  instructions, not a capability; what an agent can do is held by its tool list.
- **The Skills page says which agents a list holds.** A skill's Used by names each agent whose list
  names it, as its turns read the list.
- **An agent CLI (`acp:<cli>`) is not held to it.** The CLI loads its own skills, where no list of
  PersonalClaw's can hold them, so the Agents page says the list is not applied to an agent that
  runs on one, and the Skills page names no such agent.

---

## Environment variables

Not config-file fields, but part of the same operator surface:

| Variable | Effect |
|---|---|
| `PERSONALCLAW_HOME` | Relocate the config/state directory (default `~/.personalclaw`). |
| `PERSONALCLAW_PORT` | Override the dashboard/API port (default `10000`). Validated at CLI entry. A running gateway **overwrites** this in its own environment with the port it actually bound, so every child it spawns agrees with the live socket even under `--port` / `--port auto`. |
| `PERSONALCLAW_WORKSPACE` | Workspace root for LLM working directories: the default chat workspace, and where the folder picker opens. Default `workspace` in the PersonalClaw home (`~/.personalclaw/workspace`), or the folder `personalclaw setup` saved; the container image sets `/data/workspace`, so it lives on the image's one volume. A chat's file tools read and change files here, in the folders listed in `agent.subagent_cwd_allowed_roots`, and (read only) in the folders added as knowledge sources and in each installed skill's own folder, and nowhere else. |
| `PERSONALCLAW_BIND_HOST` | Bind address for the gateway (e.g. `0.0.0.0` for LAN access). |
| `PERSONALCLAW_BYPASS_LOCAL_NETWORKS` | `1` = skip token auth for any client whose **resolved** address is private (loopback/RFC1918/link-local/ULA). Dev convenience for a trusted LAN. **Do not set it behind a reverse proxy:** the address the gateway resolves is then the proxy's own, which is private, so requests forwarded from anywhere are admitted with no token. `personalclaw doctor`'s `remote` row fails when this is set together with `dashboard.trusted_proxies` or `dashboard.public_url`. See [remote-access.md](../guides/remote-access.md). |
| `PERSONALCLAW_FIRST_PARTY_APPS_DIR` | Point a packaged install at a first-party apps directory. |
| `PERSONALCLAW_SKIP_APP_BACKENDS` | Don't launch app backend subprocesses (test isolation). |
| `PERSONALCLAW_CREDENTIAL_BACKEND` | Where new credentials are stored: `keychain` (OS secret service, needs the `keychain` extra) or `dotenv` (default — `~/.personalclaw/.env` at mode 0600). A `keychain` request on a machine with no usable secret service falls back to `.env` 0600 and `personalclaw doctor` says so. Reads always see both of the home's stores, so switching back never hides an existing secret. Each home files its keychain items under a name of its own: the default home under `personalclaw`, any other home under `personalclaw-<id>`, the id minted on its first keychain write and kept in the home's `keychain_namespace` file, so no home lists, reads, overwrites or deletes another's. A copy of a home's folder carries that file and so shares the name: delete it in the copy to give the copy its own. `personalclaw doctor` and Settings → Secrets name the one in use. |

### How a child finds its gateway

Tool subprocesses (the `personalclaw-core` MCP server, sandboxed cron scripts, an ACP CLI's
MCP children) resolve the gateway's API base through **one** owner, `personalclaw.gateway_base`,
which answers from the socket the gateway actually bound — in order:

1. `PERSONALCLAW_PORT`, which the gateway overwrites with its bound port after binding;
2. `~/.personalclaw/gateway.runtime.json`, the same value recorded inside the home for a child
   whose environment was rebuilt from an allowlist (ignored when the pid it names is gone);
3. an **explicit** port in `dashboard.url`.

If none of the three answers, the call is **refused** with a message naming all three. It is
never sent to the default port: on a host running more than one instance, `10000` is another
instance's gateway — with its own home, config and state — so a guess is a cross-instance
read or write, not a degraded local call.

The call carries the home's local secret, and it is sent the way a command sends its own
(`home_gateway.open_loopback`): never through a proxy named in the environment, which a child is
handed for its own downloads, and following no redirect. A scheduled script's launcher, which runs
where PersonalClaw cannot be imported, builds the same transport.

### How a command finds its gateway

A command you run (`personalclaw token`, `status`, `logout`, `chat`, `run` and the others listed
in the [CLI reference](cli.md)) talks to the gateway of the home it runs for, through
`personalclaw.home_gateway`:

1. `--port`, else `PERSONALCLAW_PORT`, when one names a port;
2. else the port in the home's `gateway.runtime.json`, when the pid it names is alive.

`dashboard.url` and the default port are not sources: they say where a gateway binds, not that one
of this home is listening there. Before it sends the home's local secret, or a token minted with it,
the command asks `GET /api/healthz` at that port, which needs no sign-in, and compares its
`home_id` with the same fingerprint of its own home; another home's gateway, or a program that is
not one, is refused, and is sent nothing more. The requests go to `127.0.0.1`, never through a
proxy named in the environment, and follow no redirect.

- `GET /api/config/personalclaw` — the full config as JSON, for the owner. A request carrying an **app** identity gets only the fields its manifest declares in `permissions.config`, nested where the full read has them, and `403 config_field_not_declared` when it declares none.
- `PATCH /api/config/personalclaw {path, value}` — single-field writes, allowlisted; non-editable paths return 400. An app may write only a field its manifest names in `permissions.config`; any other answers `403 config_field_not_declared`, and a manifest that names a security setting fails to install. A field that is a **security setting** (its `_EDITABLE_CONFIG` entry declares a `SecurityControl` — approval mode and YOLO, sign-in and 2FA, egress, the keychain, the sandbox ceilings, guardrail budgets, external access, sync, and a few more) follows two more rules. A write that LOOSENS it needs `"confirm": true` in the body (the JSON literal), or it answers `400 confirmation_required` with `{field, consent, title, change}` in `error.detail` — the sentence Settings shows before it resends, and what the write changes from and to (`"$33.50 → $10,033.50"`), with `caution` added for a raise of ten times the value in effect or more; tightening never needs it. And a request carrying an **app** identity is refused `403 security_setting_owner_only`, in either direction and whatever it sends. `config/edit_spec.py` holds the rules; the settings are exactly the entries that declare one.
- `GET /api/config/schema` — the full field registry (labels, help, types, defaults, deprecations) auto-derived from the config dataclasses. This document is generated against it.
- `personalclaw config get|set <key> [value]` — CLI equivalent; `set` validates through the same loader. `get` withholds credential-named fields (`api_key`, `bot_token`, …) unless `--reveal` is passed.

See also: [API overview](api-overview.md) · [CLI reference](cli.md) ·
[Getting started](../guides/getting-started.md)
