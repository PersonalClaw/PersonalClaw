# CLI reference

The `personalclaw` command is the single entry point (installed by
`pip install -e .` via the `personalclaw` console script; source:
`src/personalclaw/cli.py`). Run `personalclaw <command> --help` for the live help
text — this page mirrors it.

## Global options

| Flag | Effect |
|---|---|
| `--version` | Print the version and exit. |
| `-v` / `--verbose` | Increase log verbosity (`-v` INFO, `-vv` DEBUG). Overrides the persisted `agent.log_level`. |

Commands that talk to a running gateway (`status`, `stop`, `restart`, `token`,
`logout`, `spawn`) accept `--port` (default: resolved from the `PERSONALCLAW_PORT`
env var or the `dashboard.url` config).

## `personalclaw gateway`

Start the PersonalClaw server (dashboard + channels). This is the long-running
process everything else talks to.

| Flag | Effect |
|---|---|
| `--headless` | Serve channels only; skip the dashboard web server and SSH tunnel instructions. |
| `--no-crons` | Skip the cron scheduler — use when another instance handles cron execution. |
| `--no-open` | Do not auto-open the dashboard URL in the browser on startup. |
| `--port PORT` | Override the dashboard port — an integer, or `auto` for an OS-assigned ephemeral port. Falls back to config when omitted. |
| `--json-ready` | Print one `PERSONALCLAW_READY:{...}` line (port, token, pid, home) once bound — for test harnesses. The token grants access for up to 20 hours; treat captured stdout as sensitive. |
| `--approval {reads,yolo,interactive}` | Default tool-approval mode. `reads` auto-approves read-only tools; `yolo` auto-approves everything (refused unless `PERSONALCLAW_HOME` is explicitly non-default); `interactive` uses the prompt flow. |
| `--test-mode` | Convenience bundle: `--port auto --no-open --json-ready --approval reads` (explicit `--port`/`--approval` win). |
| `--seed FIXTURE` | Dev tool: populate `$PERSONALCLAW_HOME` from a named fixture (under `tests_fixtures/`) before starting. Refuses the main gateway home (`~/.personalclaw`) and non-empty targets. |
| `--seed-replace` | With `--seed`, wipe `$PERSONALCLAW_HOME` before copying. Never overrides the main-home rail. |

## `personalclaw chat`

Chat with the agent from the terminal.

| Flag | Effect |
|---|---|
| *(no flags)* | Interactive chat mode. |
| `-m, --message TEXT` | Send a single message non-interactively. |
| `--model NAME` | Model to use for this run (default: the configured chat binding). |

## `personalclaw setup`

Install agent config and configure credentials (interactive wizard).

| Flag | Effect |
|---|---|
| `--agent-only` | Only install agent config; skip credential prompts. |
| `--clean` | Fresh install — don't merge MCP servers/tools from existing config. |
| `--mode {docker,service,none}` | Deployment mode: Docker Compose, system service (systemd/launchd), or none. |
| `--provider NAME` | Set the default chat provider by registry entry name. |
| `--credential NAME[=VALUE]` | Store a named credential (value from the argument or an env var). |

## `personalclaw doctor`

Verify the PersonalClaw setup (credentials, model bindings, channel tokens,
directories). No flags.

## Gateway lifecycle

| Command | What it does |
|---|---|
| `personalclaw status [--port]` | Show runtime stats from the running gateway. |
| `personalclaw stop [--port]` | Stop a running gateway. |
| `personalclaw restart [--port]` | Restart the gateway (service if installed, else foreground). |
| `personalclaw logs [-f] [-n LINES]` | Show gateway logs (`-f` live tail; `-n` line count, default 100). Reads the systemd journal (Linux service), launchd stdout file (macOS), or the foreground log file. |
| `personalclaw token [--port] [--ttl 20h]` | Print a dashboard access URL with a fresh auth token (`--ttl` e.g. `1h`, `30m`). |
| `personalclaw logout [--port]` | Revoke all active dashboard sessions. |
| `personalclaw update` | Update PersonalClaw to the latest version (git fetch + rebuild). |

## `personalclaw service`

Manage the gateway as a system service — systemd unit on Linux
(`/etc/systemd/system/`, requires sudo) or launchd LaunchAgent on macOS
(`~/Library/LaunchAgents/`, no sudo). Survives SSH disconnect, auto-restarts on
crash, auto-starts on boot.

| Subcommand | What it does |
|---|---|
| `service install` | Install and start the gateway service. |
| `service uninstall` | Stop and remove the gateway service. |
| `service status` | Show service status (systemctl/launchctl). |

## `personalclaw cron`

Manage scheduled jobs.

| Subcommand | What it does |
|---|---|
| `cron list` | List cron jobs. |
| `cron add NAME MESSAGE [--every SECS] [--cron EXPR] [--channel ID] [--approval-mode auto]` | Add a job — interval (`--every`) or cron expression (`--cron "0 9 * * MON-FRI"`); optionally post results to a channel; `--approval-mode auto` auto-approves the job's tools. |
| `cron update JOB_ID [--name] [--message] [--every SECS] [--cron EXPR] [--channel ID] [--approval-mode auto\|default]` | Update a job (`default` resets approval mode). |
| `cron remove JOB_ID` | Remove a job. |
| `cron pause JOB_ID` / `cron resume JOB_ID` | Pause / resume a job. |
| `cron trigger JOB_ID` | Fire a job immediately. |

## `personalclaw spawn`

Manage background subagents.

| Subcommand | What it does |
|---|---|
| `spawn run TASK [--async]` | Spawn a subagent; waits for the result unless `--async` (fire-and-forget). |
| `spawn list` | List active subagents. |

## `personalclaw learn`

Save or manage learned corrections.

| Subcommand | What it does |
|---|---|
| `learn add RULE [--category tool\|preference\|knowledge] [--negative TEXT]` | Save a lesson (default category `knowledge`; `--negative` records what NOT to do). |
| `learn list` | List all lessons. |
| `learn remove QUERY` | Remove lessons whose rule matches a substring. |

## `personalclaw memory`

Manage the vector memory system.

| Subcommand | What it does |
|---|---|
| `memory list` | Show semantic memory entries. |
| `memory search QUERY` | Search episodic memories. |
| `memory stats` | Show memory statistics. |
| `memory audit` | Scan memory for suspicious content. |
| `memory export [-o FILE]` | Export all memory to JSON (default: stdout). |
| `memory import FILE` | Import memory from a JSON export. |
| `memory migrate` | Migrate legacy markdown memory to the vector store. |

## `personalclaw agent`

Manage agent definitions.

| Subcommand | What it does |
|---|---|
| `agent list` | List agents. |
| `agent create --name NAME [--provider-agent NAME] [--default-dir PATH] [--memory-store NAME]` | Create an agent. |
| `agent update NAME [--provider-agent] [--default-dir] [--memory-store]` | Update an agent. |
| `agent delete NAME` | Delete an agent. |

## `personalclaw config`

Get or set configuration values (see the [configuration reference](configuration.md)).

| Subcommand | What it does |
|---|---|
| `config get [KEY]` | Get a value by dot-separated key, or the whole config with no key. |
| `config set KEY VALUE` / `config set --file FILE` | Set a value (validated through the loader) or load a full config from JSON. |
| `config edit` | Open `config.json` in `$EDITOR`. |

## `personalclaw skills`

Manage skills from the skills marketplace.

| Subcommand | What it does |
|---|---|
| `skills list` | List locally installed skills. |
| `skills search QUERY [--marketplace skills.sh]` | Search a marketplace. |
| `skills install ID [--marketplace] [--target DIR] [--force]` | Install a skill (e.g. `vercel-labs/agent-skills/next-js`). Installs are supply-chain scanned; `--force` overrides a WARNING verdict — a DANGEROUS verdict is never overridable. |
| `skills remove NAME` | Remove a locally installed skill. |
| `skills curate [--dry-run]` | Groom the `auto/` skill library (age active→stale→archived by last use). |
| `skills verify` | Check installed skills' file hashes against their install baseline (detects post-install tampering). |

## `personalclaw security`

Security audit and deny list.

| Subcommand | What it does |
|---|---|
| `security audit` | Scan conversation history for suspicious tool usage. |
| `security deny-list` | Show active deny patterns. |
| `security events [-n LIMIT]` | Show recent security event log entries (default 20). |
| `security verify` | Verify security event log HMAC integrity. |

## Backup & restore

| Command | What it does |
|---|---|
| `personalclaw snapshot [OUTPUT_DIR] [--keep N] [--list]` | Create a portable backup of PersonalClaw state (keeps the N most recent, default 7; `--list` shows existing snapshots). |
| `personalclaw restore [SNAPSHOT] [--mode replace\|merge] [--dry-run] [--components LIST] [--list-components] [--force]` | Restore state from a snapshot `.tar.gz`. `--force` restores even while the gateway runs. |

## Other commands

| Command | What it does |
|---|---|
| `personalclaw consolidate KEY \| --all` | Run skill/memory extraction over a session transcript now (the same path the idle poll and session-end triggers use). |
| `personalclaw eval [SCENARIOS...] [--all] [--judge]` | Run multi-session evaluation scenarios (default: a ~30s smoke test; `--judge` enables LLM scoring). |
| `personalclaw mcp-schedule` / `personalclaw mcp-core` | Internal MCP server entry points spawned by ACP agents — not user-facing (hidden from `--help`). |

---

See also: [Configuration reference](configuration.md) ·
[API overview](api-overview.md) · [Getting started](../guides/getting-started.md)
