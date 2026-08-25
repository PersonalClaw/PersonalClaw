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

Two fixtures ship:

| Fixture | Contents |
|---|---|
| `empty` | A bare home — just the `fixture.yaml` marker. Everything else is created on first boot. |
| `demo-home` | A home that looks used, for screenshots and demos: two projects with briefs, three task lists, ten tasks spanning every status (one blocked on a real dependency), markdown memory (preferences, project context, two days of history), five knowledge docs, and one completed loop with a three-phase plan. Onboarding is pre-completed, so it boots straight to the dashboard. Semantic/episodic memory *records* are still not included — that store is SQLite-only with no text tier, unlike the markdown memory the fixture does carry. |

## `personalclaw chat`

Chat with the agent from the terminal.

| Flag | Effect |
|---|---|
| *(no flags)* | Interactive chat mode. |
| `-m, --message TEXT` | Send a single message non-interactively. |
| `--model NAME` | Model to use for this run (default: the configured chat binding). |

## `personalclaw run`

Run ONE headless turn against the local gateway and exit — the scripting/CI entry
point. Unlike `chat -m` (which talks to a provider directly, with no gateway, session,
safety profile or tool gate), `run` drives the same `POST /api/chat` + `/api/ws` pair the
dashboard uses, so a scripted turn is gated exactly like an interactive one.

| Flag | Effect |
|---|---|
| `-p, --prompt TEXT` | **Required.** The prompt for this turn. An empty or whitespace-only value is refused (exit 2). |
| `--format {plain,json,streaming-json}` | `plain` (default) = final text only, pipes cleanly; `json` = one `{result, session, turns, tool_calls, tokens, duration_ms}` document; `streaming-json` = NDJSON of the `chat_chunk`/`tool_call`/`chat_done` WS frames the dashboard consumes. |
| `--agent NAME` | Agent to run the turn as (default: the configured default agent). |
| `--model NAME` | Model override for this turn. |
| `--session KEY` | Continue a **named persistent** session (`inbound:cli:<key>`). Omitted = a fresh stateless one-shot per invocation. |
| `--cwd DIR` | Working directory for the turn's tools. |
| `--allow` | Grant write/execute tools. **Default is read-only.** |
| `--timeout SECS` | Ceiling on the turn (default 600). |
| `--port PORT` | Gateway port (default: resolved like every other client command). |

Exit code is `0` when the turn succeeded, `1` on a failed turn or transport error, and
`2` on a refused invocation (blank prompt, or a read-only run on an ACP agent).

### Safety posture

A `run` turn uses an `inbound:cli:` session key, which classifies as **unattended**, so
it resolves through the `HEADLESS` safety profile by construction.

* **Read-only by default.** The session's *task mode* is set to `ask`, so every
  non-read-only tool call is denied before the approval gate — the same gate the native
  runtime enforces, which Trust/YOLO cannot bypass. A tool the classifier cannot read
  (an opaque shell command, an unlabelled external MCP tool) is **denied**, not allowed.
* **`--allow` is the explicit write grant** (task mode `agent`).
* **The posture is always announced on stderr**, for both modes, so stdout stays
  pipeable and a script is self-documenting about what it asked for.
* **ACP-backed agents are refused in read-only mode** (exit 2). An unattended ACP turn
  runs with permissions bypassed so the dialect never asks, which means the task-mode
  gate never sees a tool call and read-only cannot be enforced. Use `--allow` to opt
  into the full grant you would really be getting, or point `--agent` at a
  native-runtime agent where read-only *is* enforced.
* Spend is attributed to the SpendMeter run scope `cli`, under the `HEADLESS` profile's
  budget (your configured per-day ceiling).

### Gateway lifecycle

`run` probes `/api/healthz` on the resolved port. If a gateway is already running it
**reuses** it (minting a token via `.local_secret`, so `run` must share that gateway's
`PERSONALCLAW_HOME`) and leaves it running. If none is running, `run` starts a
**transient** gateway on an ephemeral port, uses its `--json-ready` handshake, and kills
it by pid on exit.

### CI smoke test

```bash
# One turn, machine-readable, fails the job on a failed turn.
personalclaw run -p 'Reply with exactly: OK' --format json | jq -er '.result'
```

A ready-made script and GitHub Action live at `scripts/ci_smoke_run.sh` and
`.github/workflows/headless-run-smoke.yml`.

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

## `personalclaw app`

Scaffold a third-party app.

| Subcommand | What it does |
|---|---|
| `app new --list-types` | Print the provider types this build accepts, derived at runtime from the provider registry — plus the SDK contract each type's stub implements and how many providers of that type are registered. A type added upstream shows up here without a scaffold change. |
| `app new NAME --type TYPE [--dir DIR] [--display-name] [--description] [--author] [--force]` | Generate an installable app: `app.json` (validated against core's own manifest parser, with the plan-32 `cli.*` seams and `loggerRoots`), a provider stub implementing that type's SDK ABC, a passing `test_provider.py`, `README.md`, and an MIT `LICENSE`. Declares no permissions — add only what the provider uses. |
| `app new --from-template [--dir DIR] [--template-url URL] [--template-archive FILE] [--force]` | Fork-and-go: fetch the [`PersonalClaw/app-template`](https://github.com/PersonalClaw/app-template) repo into `DIR/app-template` instead of generating. Same `--type tool` output, plus CI and a clone-to-installed README. Takes no NAME — renaming is a documented four-edit step in the template's README; use `--type` to generate a named app. |

Names are kebab-case. `pytest <dir>` passes on the generated bundle as-generated, and
installing it from that local path registers the provider.

`--from-template` is the only part of `app new` that uses the network, and it fails closed:
`https` only, to an allowlisted host (`codeload.github.com`), no redirects followed at all, a
non-200 refused, and a per-member/whole-archive byte cap. Archive members must be regular
files or directories with relative in-tree paths — a symlink, hardlink, device or `../`
member is refused, and every write path is re-checked for containment after
canonicalisation. An existing non-empty target is refused unless `--force`.
`--template-archive` reads a `.tar.gz` already on disk and touches no network.

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
| `personalclaw backup export [OUT_DIR] [--incremental]` | Export state as **deterministic shards** — canonical JSONL per store plus a SHA-256 manifest, byte-identical for identical state (so it diffs cleanly and syncs without re-uploading unchanged data). Defaults to `<home>/shards`. `--incremental` re-exports only the stores whose content changed. Secrets are never exported. |
| `personalclaw backup validate [SHARD_DIR]` | Verify an export end to end: the manifest parses, every declared shard exists, and each one's byte length, row count, and SHA-256 re-derive — plus every row re-parses. **Exits non-zero on any problem**, so it works as a cron/CI check. A backup nobody has verified is a hope, not a backup. |

## Inbound surfaces

PersonalClaw can expose a **read-only MCP endpoint** at `POST /mcp` so a local MCP
client (your IDE, an MCP inspector) can ask it questions. It is off by default and
stays off until you both mint a token and flip the flags — and it only answers
loopback callers.

There are **five** inbound surfaces in the config schema — `openai`, `mcp`, `a2a`,
`capture`, `bridge` — each with its own token and its own `enabled` flag. Only `mcp`
has a route today; enabling one of the other four is accepted by config and simply
has nothing to mount yet.

| Command | What it does |
|---|---|
| `personalclaw inbound token create <surface> [--rotate]` | Mint that surface's bearer token, stored in the **credential store** (keychain, else `.env` at `0600`) as `PERSONALCLAW_INBOUND_<SURFACE>_TOKEN`. **Printed once** — copy it into your client immediately. `--rotate` replaces an existing token, which immediately invalidates the old one. |
| `personalclaw inbound token show <surface>` | Report whether a usable token is configured, and why not if it isn't. Deliberately never prints the value: a credential the CLI can re-read is one an unattended process can exfiltrate. Lost it? Rotate. |

A token is refused if it is shorter than 32 bytes, equal to the dashboard token or
internal secret, or equal to **another surface's** token — five surfaces sharing one
bearer would collapse five independently revocable credentials into one.

Minting a token is not enough on its own — enable the surface too, and the master
switch above it:

```bash
personalclaw inbound token create mcp                     # copy the printed bearer token
personalclaw config set external_access.enabled true      # master gate, all surfaces
personalclaw config set external_access.mcp.enabled true  # this surface
```

Every condition is checked on every request, so setting either flag to `false` is an
immediate kill switch — no restart needed, and `external_access.enabled false` takes
all five down at once. A third layer is per-client (Settings → External Access can
disable one integration without touching the surface), and a fourth is the guardrails
incident flag: while an incident is active every inbound request gets `503`. All four
parse **fail-closed** — an unreadable flag reads as *off*, which is the inverse of a
guard flag, because for an inbound surface OFF is the safe state.

When a surface refuses to mount, the gateway log carries one line naming the exact
reason. Every request (allowed or refused) is recorded in `<home>/inbound_audit.jsonl`,
and refusals also land in the security event log.

Remote access (`external_access.<surface>.allow_remote` + `external_access.public_url`)
exists but is **discouraged**, and does not work for an MCP client at all — see
[Use from your IDE](../guides/use-from-your-ide.md). Neither knob is editable from the
dashboard; they are config-file-only on purpose, and the PATCH endpoint refuses them
rather than ignoring them.

## Other commands

| Command | What it does |
|---|---|
| `personalclaw consolidate KEY \| --all` | Run skill/memory extraction over a session transcript now (the same path the idle poll and session-end triggers use). |
| `personalclaw discover [--timeout SECS] [--json]` | Find PersonalClaw gateways advertising themselves on the local network (mDNS/DNS-SD `_personalclaw._tcp`). Prints each one's name and base URL. Finding nothing is a normal result and exits 0 — discovery is opt-in, is a no-op on a loopback-only gateway, and many networks filter multicast. See [Companion apps](../guides/companion-apps.md). |
| `personalclaw eval [SCENARIOS...] [--all] [--judge]` | Run multi-session evaluation scenarios (default: a ~30s smoke test; `--judge` enables LLM scoring). |
| `personalclaw mcp-schedule` / `personalclaw mcp-core` | Internal MCP server entry points spawned by ACP agents — not user-facing (hidden from `--help`). |

---

See also: [Configuration reference](configuration.md) ·
[API overview](api-overview.md) · [Getting started](../guides/getting-started.md)
