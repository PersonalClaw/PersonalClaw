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

Commands that talk to a running gateway (`chat`, `run`, `status`, `token`, `logout`, `spawn`,
`auth revoke`, `auth rotate-key`, `cron trigger`, `doctor`) reach the gateway of the home they
run for: the one at the port that home's gateway recorded in its home once it listened, or at the
port `--port` or `PERSONALCLAW_PORT` names (`--port` first). No other port is assumed. Before
anything that carries a credential is sent, the gateway there is asked which home it serves
(`GET /api/healthz`, whose `home_id` is a fingerprint of the gateway's home), and another home's
gateway, or a program that is not a gateway, is refused with a sentence that says so: the home's
local secret goes to its own gateway and nowhere else. None of these requests goes through a proxy
from the environment. With no gateway of the home running, a command says so and names the command
that starts one; `run` starts its own for the turn, `auth revoke` and `auth rotate-key` change the
home directly, and `doctor` measures in its own process. `stop` and `restart` find the gateway from
the same record, and their `--port` only checks it.

## Exit codes and output streams

| Exit | Meaning |
|---|---|
| `0` | The command did what you asked. |
| `1` | It ran and refused or failed: no gateway is running, the job id is not there, a check found a problem. |
| `2` | The command line is wrong, and the usage is on stderr: `personalclaw` alone, a group with none of its commands (`personalclaw cron`), a required argument that is missing or empty, two options that exclude each other (`cron add --every 60 --cron "0 9 * * *"`). |

`--help` prints on stdout and exits `0`. `auth`, `incident` and `push` alone show their status
and `skills` alone lists the installed skills; every other group needs one of its commands.
A command whose section below lists its own codes (`gateway`, `chat`, `run`) follows that list.

stdout carries only what a command produces: the link `token` prints, the jobs `cron list`
prints, a report. A refusal, a failure or a usage message goes to stderr, so
`url=$(personalclaw token)` captures a link or nothing. A check whose report is its answer
(`doctor`, `security verify`, `backup validate`, `inbound token show`) prints the report on
stdout either way, and its exit status says whether it passed.

## `personalclaw gateway`

Start the PersonalClaw server (dashboard + channels). This is the long-running
process everything else talks to.

Started at a terminal, it prints a sign-in link for the dashboard, and opens it in the
default browser unless `--no-open` (or `dashboard.auto_open_browser: false`) says not to.
When its output goes to a file instead (a service's log, the `gateway-restart.log` a
detached `personalclaw restart` writes, a container's logs, a pipe), it prints the
dashboard's address without a sign-in link and says to run `personalclaw token` for one;
a link it opens in the browser goes to the browser only. Each start ends the startup link
of the start before it, when no browser opened that link.

**One gateway serves a home.** Before it does anything else, a gateway takes its home's claim, a
lock on `gateway.lock` in the home, and it holds it until it exits; the system lets go of it
however the gateway ends, a crash included. A start on a home that another gateway already serves,
whether from a terminal, the desktop app, a service, `personalclaw run` or a script, ends there:
before it seeds, binds a port, records where it listens or writes the home's local secret. It
writes nothing into the home, and its last line on stderr says which gateway serves it:

```
PersonalClaw did not start: another gateway already serves /home/user/.personalclaw-dev (pid 4711, http://127.0.0.1:20417). Use that one, or stop it first: PERSONALCLAW_HOME=/home/user/.personalclaw-dev personalclaw stop
```

Until the gateway serving the home has recorded where it listens, and shown at that address that it
is the home's, the parenthesis reads `(it is still starting)`. The record a stopped or crashed
gateway left stops no start. A home whose lock cannot be taken (a link at `gateway.lock`, something
there that is not a file, a disk that cannot lock files) is not served: the start says why.

| Exit | Meaning |
|---|---|
| `0` | It stopped because it was asked to. |
| `1` | It could not start, or stopped on an error: its port was taken, its home's lock could not be taken, governance could not be established, a restart could not start its new image. |
| `2` | The command line is wrong or names what it may not use: `--port abc`, `--approval yolo` on the default home, a `--seed` fixture that does not exist or a target that is not empty. |
| `3` | Another gateway already serves this home. Nothing in the home was changed. |

| Flag | Effect |
|---|---|
| `--headless` | Run without the dashboard: none of its pages are served (the dashboard itself, its sign-in and device-pairing pages), and no dashboard address, sign-in link or SSH tunnel instructions are printed. Everything else runs as it does with it: the installed apps, channels, automations, and the API that the CLI and the agent's tools use, behind the same sign-in. |
| `--no-crons` | Skip the cron scheduler — use when another instance handles cron execution. |
| `--no-open` | Do not auto-open the dashboard URL in the browser on startup. |
| `--port PORT` | Override the dashboard port — an integer, or `auto` for an OS-assigned ephemeral port. Falls back to config when omitted. |
| `--json-ready` | Print one `PERSONALCLAW_READY:{...}` line (port, token, token_expires_in, token_expires_at, pid, home) once bound — for test harnesses. The token is an owner sign-in: send it as `Authorization: Bearer`, or open `/?token=` with it in a browser. It lasts as long as a browser sign-in (`auth.session_ttl`, 30 days by default); treat captured stdout as sensitive. |
| `--approval {reads,yolo,interactive}` | How the agents PersonalClaw runs in the background (a subagent, and the agent an automation or a workflow step starts) have their tool calls approved. `reads` approves a tool that declares it only reads and a read-only shell command, and asks for the rest; `yolo` approves every call (refused unless `PERSONALCLAW_HOME` is explicitly non-default); `interactive` asks, as omitting the flag does. A chat keeps its own approval mode. |
| `--test-mode` | Convenience bundle: `--port auto --no-open --json-ready --approval reads` (explicit `--port`/`--approval` win). |
| `--seed FIXTURE` | Dev tool: populate `$PERSONALCLAW_HOME` from a named fixture (under `tests_fixtures/`) before starting. Refuses the main gateway home (`~/.personalclaw`) and non-empty targets. It seeds once: a restart of that gateway (the dashboard's Restart, an update) serves the home as it is, and seeds nothing again. |
| `--seed-replace` | With `--seed`, stop the home's tmux server and empty `$PERSONALCLAW_HOME` before copying; the lock this start holds on the home stays. Never overrides the main-home rail, and never touches a home another gateway serves: that start is refused before anything in the home is touched. |
| `--seed-local-model` | Bind a local Ollama provider into `$PERSONALCLAW_HOME` after seeding, so the home can run a real chat turn. Conditional and never fatal — see below. |
| `--local-model-endpoint URL` | Endpoint for `--seed-local-model` (default `http://localhost:11434`, or `$PERSONALCLAW_LOCAL_MODEL_ENDPOINT`). |
| `--local-model MODEL_ID` | Model to bind (default: the endpoint's most recently modified chat-capable model, or `$PERSONALCLAW_LOCAL_MODEL`). |
| `--local-model-apps-dir DIR` | Local checkout of the apps repo to install the `ollama-models` provider app from, when it is not already installed in the home (or `$PERSONALCLAW_LOCAL_MODEL_APPS_DIR`). |

Three fixtures ship:

| Fixture | Contents |
|---|---|
| `empty` | A bare home — just the `fixture.yaml` marker. Everything else is created on first boot. |
| `demo-home` | A home that looks used, for screenshots and demos: two projects with briefs, three task lists, ten tasks spanning every status (one blocked on a real dependency), markdown memory (preferences, project context, two days of history), five knowledge docs, and one completed loop with a three-phase plan. Onboarding is pre-completed, so it boots straight to the dashboard. Semantic/episodic memory *records* are still not included — that store is SQLite-only with no text tier, unlike the markdown memory the fixture does carry. **No model provider** — the fixture is a byte-identical copy on every machine, so it cannot carry any one machine's endpoint; `--seed-local-model` is the step that binds one. |
| `six-month-home` | A home an earlier release (0.1.3) wrote across six months of use, through that release's own writers: `config.json`, run history for two scheduled jobs, Inbox and notification settings, a loop, the memory database, markdown memory and a knowledge database. Seed it to check that a home an older release left behind still loads; `six-month-home.manifest.json` beside it records what each store holds, and `tests/test_state_survival.py` holds a fresh build to that record. No model provider, for the same reason as `demo-home`. |

### Binding a model into a seeded home

A seeded home carries no model provider, so the three surfaces that exist only as the
product of a turn are empty: `/api/sessions` is `0`, `/api/approvals` is `[]` (an
approval is written by the tool-permission gate on a real turn), and `/api/artifacts` is
`[]` (artifacts are agent-produced). `--seed-local-model` closes that in the same
command:

```
PERSONALCLAW_HOME=~/.personalclaw-demo \
  personalclaw gateway --seed demo-home --seed-replace --seed-local-model
```

It probes Ollama's `/api/tags` and, only when a server answers with a usable model, does
three things: confirms or installs the `ollama-models` provider app, writes the
`providers[]` entry into `config.json`, and binds the `chat` use case (plus `embedding`,
when the endpoint has an embedding model) in `active_models.json`. No credential is
involved — a local Ollama needs none, which is why it is the provider on this path.

It degrades rather than half-populates. If nothing is listening, if the endpoint has no
chat model pulled, or if the provider app is neither installed nor reachable from a
local app source, **nothing is written**: the home is exactly what the fixture copied,
the gateway starts normally, and one `seed-local-model: skipped_…` line names which
precondition failed and how to satisfy it. The exit status is unaffected, so a machine
with no Ollama seeds exactly as it did before the flag existed.

To bind a home that already exists — an evals cell home, a research-lab home — without
re-seeding it or booting a gateway:

```
PERSONALCLAW_HOME=… python -m personalclaw.seed_local_model
```

## `personalclaw chat`

Chat with your assistant from the terminal. The chat runs in your running gateway: it is a
chat like one you start in the dashboard, with your assistant's name, its memory and its
tools, and it stays in the dashboard's chat list, where you can go on with it.

| Flag | Effect |
|---|---|
| *(no flags)* | Chat until you type `exit` (or `quit`) or press Ctrl+D. Your first message opens the chat. |
| `-m, --message TEXT` | Send one message, print its reply, and exit. An empty or whitespace-only message is refused (exit 2). |
| `--model NAME` | Model for this chat (default: the chat model bound in Settings → Models). |
| `--port PORT` | Port of this home's gateway (default: the port it recorded when it started; `PERSONALCLAW_PORT` names one too). A gateway there that is not this home's is refused. |

The reply streams to stdout. A call that asks for approval waits for your answer as it does
in any chat: it is listed in the dashboard (Home, the Inbox and the chat itself) and on your
phone, and asked on the chat channel your approvals go to. The terminal says on stderr what
is waiting and how it ended (Approved, Denied, Expired or Cancelled). Notices and errors go
to stderr too, so `personalclaw chat -m '…' > reply.txt` keeps the reply alone.

Ctrl+C during a reply stops that turn in the gateway, as Stop does in the dashboard: a call
still waiting for approval is cancelled and never runs. Ctrl+C at the `you>` prompt leaves
the chat.

`chat` needs the gateway. With none running on the port it says so, with the command that
starts one (`personalclaw gateway`, or `personalclaw restart` when a service is installed for
this home), and exits 1. Like `run`, it must share that gateway's `PERSONALCLAW_HOME`: it
signs in with the home's local secret.

Exit code is `0` when the message's turn completed (or you left the interactive chat), `1`
when no gateway is running, the turn did not complete (it failed or was stopped) or the
connection to the gateway failed, and `2` for an empty `-m`.

## `personalclaw run`

Run ONE headless turn against the local gateway and exit — the scripting/CI entry
point. Like `chat`, `run` drives the same `POST /api/chat` + `/api/ws` pair the dashboard
uses, so a scripted turn is gated exactly like an interactive one. Unlike `chat`, its turn is
unattended (see the safety posture below): nobody is there to answer an approval.

| Flag | Effect |
|---|---|
| `-p, --prompt TEXT` | **Required.** The prompt for this turn. An empty or whitespace-only value is refused (exit 2). |
| `--format {plain,json,streaming-json}` | `plain` (default) = final text only, pipes cleanly; `json` = one `{result, session, outcome, turns, tool_calls, tokens, duration_ms}` document; `streaming-json` = NDJSON of the `chat_chunk`/`tool_call`/`chat_done` WS frames the dashboard consumes. `outcome` (and the final `chat_done`'s) says how the turn ended: `complete`, `stopped`, `error` or `interrupted` (the gateway restarted or shut down); the JSON document's says `timed_out`, `cancelled` or `connection_lost` when the run ended the turn itself ([below](#when-a-run-ends-before-its-turn)). |
| `--agent NAME` | Agent to run the turn as (default: the configured default agent). |
| `--model NAME` | Model override for this turn. |
| `--session KEY` | Continue a **named persistent** session (`inbound:cli:<key>`). Omitted = a fresh stateless one-shot per invocation. |
| `--cwd DIR` | Working directory for the turn's tools. |
| `--allow` | Grant write/execute tools, and approve the run's calls without asking. **Default is read-only.** |
| `--timeout SECS` | Ceiling on the turn (default 600). When it passes, `run` stops the turn in the gateway before it exits. |
| `--port PORT` | Port of this home's gateway (default: the port it recorded when it started; `PERSONALCLAW_PORT` names one too). A gateway there that is not this home's is refused. |

Exit code is `0` when the turn completed, `1` when it ended with an error, was stopped before
it finished, ran past its `--timeout`, the transport failed, or `--allow` could not grant the
run's writes (the operator's approval ceiling refuses it), and `2` on a refused invocation (a
blank prompt). A run that Ctrl+C, SIGTERM or a closed terminal ended exits as that signal ends
a program (130 for Ctrl+C, 143 for SIGTERM), so a script running it stops too. The code
follows how the gateway says the turn ended, not the
error rows along the way: a transient failure the gateway retried and then finished exits `0`.

### Safety posture

A `run` turn uses an `inbound:cli:` session key, which classifies as **unattended**, so
it resolves through the `HEADLESS` safety profile by construction.

* **Read-only by default.** The session's *task mode* is set to `ask`, so every
  non-read-only tool call is denied before the approval gate — the same gate the native
  runtime enforces, which Trust/YOLO cannot bypass. A tool the classifier cannot read
  (an opaque shell command, an unlabelled external MCP tool) is **denied**, not allowed.
* **A tool that only reads runs**, in both modes: it declares it only reads, and a read asks
  nobody. Any other call that asks for approval is declined in read-only mode, since nobody is
  there to answer it.
* **`--allow` is the explicit write grant**, in two parts: task mode `agent` admits the calls
  that change things, and Trust on the run's own chat approves them, because a headless turn
  has nobody to ask. Trust for one chat answers that chat's approvals only. The operator's
  approval ceiling bounds it like any grant: under `approval: ask` the run stops before its
  turn, saying why. The Trust is the run's, for its turn: the gateway ends it when that turn
  ends or is stopped, so a helper's report the run did not wait for, or the next run of the same
  `--session`, gets none of it, and a call it asks about is declined.
* **The posture is always announced on stderr**, for both modes, so stdout stays
  pipeable and a script is self-documenting about what it asked for.
* **An agent CLI is held the same way, for the calls it asks about.** The turn tells the
  CLI the mode in which it asks PersonalClaw first, so in read-only mode each change it asks
  about is denied before anything could approve it, and a call nothing approves is declined
  at once, with its reason, since nobody is there to answer it. A call the CLI's own settings
  let it run without asking is outside that: the run reports it, and stops the turn if it may
  have changed something, which the posture line says
  ([limitations §1](../security/limitations.md#1-an-agent-cli-is-held-to-the-rails-only-for-the-calls-it-asks-about)).
* Spend is attributed to the SpendMeter run scope `cli`, under the `HEADLESS` profile's
  budget (your configured per-day ceiling).

### When a run ends before its turn

The turn runs in the gateway, not in the command, so a run that stops waiting for it stops it
there first, as Stop does in the dashboard: when its `--timeout` passes, on Ctrl+C, SIGTERM (a CI
job cancelled, a `timeout` wrapper) or its terminal closing, and when its connection to the
gateway closes. The gateway ends the run's Trust before it asks the turn to stop, so no call is
approved on it while the turn winds down, and the run follows the turn until the gateway says it
has ended. stderr says what ended the run and what the gateway did; the JSON document's
`outcome` is `timed_out`, `cancelled` or `connection_lost`, and its `result` is what the turn
said before it stopped. A second Ctrl+C stops the waiting at once.

When the gateway cannot be told (it has gone away), `run` says so, with the command that starts
it again. A turn still running in a gateway nothing reaches keeps going to its end, and the
run's Trust ends with it.

### Gateway lifecycle

`run` probes `/api/healthz` on the resolved port. If a gateway is already running it
**reuses** it (minting a token via `.local_secret`, so `run` must share that gateway's
`PERSONALCLAW_HOME`) and leaves it running. If none is running, `run` starts a
**transient** gateway on an ephemeral port, uses its `--json-ready` handshake, and kills
it by pid on exit.

### CI smoke test

```bash
# One turn, machine-readable, fails the job on a turn that did not complete.
personalclaw run -p 'Reply with exactly: OK' --format json | jq -er 'select(.outcome == "complete") | .result'
```

A ready-made script and GitHub Action live at `scripts/ci_smoke_run.sh` and
`.github/workflows/headless-run-smoke.yml`.

## `personalclaw setup`

Install agent config and configure credentials (interactive wizard).

| Flag | Effect |
|---|---|
| `--agent-only` | Only install agent config; skip credential prompts. |
| `--clean` | Fresh install — don't merge MCP servers/tools from existing config. |
| `--mode {docker,service,none}` | Deployment mode: print the README's one-container `docker run`, the system service (systemd/launchd) install, or nothing. |
| `--provider RUNTIME` | Set the runtime an agent runs on when it names none: `native` (the built-in loop, on the models Settings → Models binds), `acp`, or `acp:<cli>` for a connected agent CLI. Any other value is refused. The chat model itself is chosen in Settings → Models. |
| `--credential NAME[=VALUE]` | Save a secret under `NAME` in the credential store Settings → Secrets lists, where `{{secret:NAME}}` and a provider's `credential` read it. The value comes after `=`, else from the environment variable `NAME`. |
| `--app NAME` | Run only the named installed app's setup step. |

A step that fails never ends on "Done!". It says why on stderr and `setup` goes on to the
next step, then ends on a summary naming each failed step with the command that runs it
again (`personalclaw setup`, `personalclaw setup --agent-only`, or
`personalclaw setup --app NAME` for an app's step), and exits 1. What the other steps did
stays saved, and running `setup` again is safe: Enter at a prompt keeps its answer.

While `PERSONALCLAW_WORKSPACE` is set, the workspace step shows that folder and asks
nothing: the variable wins over a folder saved here.

## `personalclaw doctor`

Verify the PersonalClaw setup (credentials, model bindings, channel tokens,
directories, and each agent CLI an installed agent app set up).
Its `git:` row shows the git on `PATH` and its version, and fails for a git older than
2.12, which PersonalClaw's git refuses.

Doctor starts no agent CLI unless you ask it to. For each one it reports whether the CLI
is installed and what its last Test found (the Test on its card in Settings → Providers);
one nobody has tested reads as installed and not started, which is not an issue.

The check changes none of your configuration. Its **MCP Tools** rows read the agent runtime config,
`agents/personalclaw.json` in the home: whether the entry for PersonalClaw's own server starts a
program that is on this machine, and whether `@personalclaw-core` is in `tools` (the tools the
agent is offered) and in `allowedTools` (the tools it runs without asking). A missing server
entry, or a command that is not a program here, is an issue; its repair is the Fix on the same
check in Settings → Doctor → Tools, which shows what it writes and asks first, and a gateway start
sets the entry up again too. The two lists are yours: doctor says where the server stands in them,
and neither doctor nor any Fix adds a tool to either.

Its **Backups** row is the Doctor page's check of the scheduled backups: whether the last
snapshot and the last export worked, a failure in the words Settings → Backups uses, how many
runs in a row it has lasted, what to do about it, and the newest snapshot a restore can still
bring back. It reads the backup record of this home, which the gateway writes, so it needs no
gateway running. A failure is reported there and does not fail the setup check.

With a gateway of this home running, the **Maintenance** score and deficits and each model
provider's row under **Provider Health** are the gateway's own: the score its Doctor page shows,
and each instance as its connection test found it (one that cannot be used says why, in its own
words). Doctor asks it with a token that lasts two minutes, minted with the home's local secret as
a sign-in link is, and listed under Settings → Devices while it lasts. With none running,
the `measured:` row says the score was measured here, where no channel receives, and each provider
reads as registered and not tested.

| Flag | What it does |
|---|---|
| `--start-agent-clis` | Also start each of those agent CLIs once — its ACP handshake and one empty session, what its Test does — to check it runs and is signed in, and record the answer for its card. |
| `--paths` | Print the resolved install paths (reference docs, config, skills, install dir) as `key<TAB>path` lines, and exit. |
| `--rebuild-routing-stats` | Refold `routing_stats.json` from the model-call audit log, and exit. |

## Gateway lifecycle

| Command | What it does |
|---|---|
| `personalclaw status [--port]` | Show runtime stats from this home's running gateway, or say that none is running and how to start it, and the service installed for this home, if there is one, with whether it is running. |
| `personalclaw stop [--port]` | Stop this home's gateway, and return once it has exited. It finds the gateway from the record the gateway keeps in its home (its port and pid), so it needs no other program. `--port` stops it only if it listens on that port. With a service installed for this home and running, it stops the service and leaves it installed: it starts again at your next login (macOS) or the next boot (Linux), or with `personalclaw restart`. In a container it changes nothing and prints the host command that stops the container. |
| `personalclaw restart [--port]` | Restart the gateway: the service installed for this home, whether or not it is running (a gateway started outside it is stopped first), else stop this home's gateway and start a fresh one on the port it had. A home has one gateway, so a fresh one starts only once the old one has exited. A service installed for another home is left alone. In a container it changes nothing and prints the host command that restarts the container. |
| `personalclaw logs [-f] [-n LINES]` | Show gateway logs (`-f` live tail; `-n` line count, default 100). Reads the systemd journal (Linux service), the launchd service's log, `~/Library/Logs/PersonalClaw/gateway.err` (macOS), or `gateway.log` in the home (a gateway that is not a service). Each holds the same lines as Settings → Diagnostics → Live logs: the gateway's own, every loaded app's from the moment it loads, what each app's backend, background worker and engine print (masked, tagged with the app), and any library's warnings. |
| `personalclaw token [--port] [--ttl 20h]` | Print a sign-in link for the dashboard, from this home's gateway. Open it in a browser to sign that browser in, or send the token after `?token=` as an `Authorization: Bearer` header from a script. It lasts 20 hours unless `--ttl` says otherwise (`30m`, `20h`, `7d`; at most `90d`, the limit for a long-lived credential — longer is refused, with a sentence saying why), and it says so on stderr, with the time it stops working. Every sign-in is listed under Settings → Devices, where it can be signed out. |
| `personalclaw logout [--port]` | Sign every device and token out, everywhere. Each one's next request is told when and from where, and how to sign back in. |
| `personalclaw update [--to VERSION]` | Move this install to the newest release on its `updates` channel, or to its pinned release: it upgrades the wheel, checks out the release tag in a git clone, or prints the host's commands that replace a container's image. It installs with the tool that made the environment (uv or pip); when that tool is not on PATH it changes nothing and says what to run, and when the install fails it puts the git clone back on the commit it was on and exits 1 saying why. A Ctrl-C while it installs stops it the same way, says what that left and exits 130; once the install has finished, it says the update will finish. When that release is not newer than the one running (or is the pinned one, already running), it says so and changes nothing. A gateway that is running keeps its code until it restarts (`personalclaw restart`). `--to` pins that release first, which is also how you roll back. |

## `personalclaw service`

Manage the gateway as a system service — systemd unit on Linux
(`/etc/systemd/system/`, requires sudo) or launchd LaunchAgent on macOS
(`~/Library/LaunchAgents/`, no sudo). Survives SSH disconnect, auto-restarts on
crash, auto-starts on boot.

In a container there is no service: the container runtime keeps the gateway running
(the README's `docker run --restart unless-stopped`, or Compose's `restart:
unless-stopped`). Each subcommand there says so and changes nothing; `service status`
also says whether the gateway is running.

| Subcommand | What it does |
|---|---|
| `service install [--env NAME]… [--no-env NAME]…` | Install and start the gateway service, carrying the variables below from this shell. `--env NAME` carries one more, `--no-env NAME` leaves one out. |
| `service uninstall` | Stop and remove the gateway service, and stop the tmux server its home's persistent terminals and durable workers run in. |
| `service status` | Show service status (systemctl/launchctl) and the environment the installed service starts the gateway in. |

On macOS, launchd writes the service's output to `~/Library/Logs/PersonalClaw/gateway.log`
and `gateway.err`. Both, and their folder, are readable only by you, and neither holds a
sign-in link: run `personalclaw token` for one. On Linux the output goes to the systemd
journal.

### The service's environment

A service is not started from your shell, so it does not see what your shell
exports. `service install` writes each of these into the unit or plist when the
shell running it sets it, and prints what it carried:

| For | Variables |
|---|---|
| PersonalClaw | `PERSONALCLAW_HOME`, `PERSONALCLAW_WORKSPACE`, `PERSONALCLAW_PORT`, `PERSONALCLAW_CREDENTIAL_BACKEND`, `PERSONALCLAW_FIRST_PARTY_APPS_DIR` |
| AWS: the Amazon Bedrock app and any AWS tool | `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_REGION`, `AWS_DEFAULT_REGION`, `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_SDK_LOAD_CONFIG`, `AWS_CA_BUNDLE`, `AWS_ROLE_ARN`, `AWS_ROLE_SESSION_NAME`, `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_STS_REGIONAL_ENDPOINTS`, `AWS_ENDPOINT_URL` |
| Where agent CLIs and model tools keep their files | `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HF_HOME`, `HF_HUB_CACHE`, `HF_TOKEN_PATH`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME` |
| Proxies and TLS trust | `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY`, `https_proxy`, `http_proxy`, `all_proxy`, `no_proxy`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, `GIT_SSL_CAINFO`, `GIT_SSL_CAPATH` |

The service file sets `HOME` and `PATH` itself (and `USER` on Linux). Anything
that widens who can reach the gateway, such as `PERSONALCLAW_BIND_HOST`, is carried
only when you ask for it with `--env`.

**A secret is never written into a service file.** The systemd unit is readable
by every user on the machine, and the plist by any program running as you. A
variable whose name is a secret (`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`,
`GITHUB_TOKEN`, …) or whose value holds one (a proxy URL with a password in it)
is left out, even when asked for with `--env`, and `service install` prints a
sentence saying why. Save secrets in Settings → Secrets or with
`personalclaw setup --credential NAME=…` instead: the gateway puts every secret
saved there into its own environment when it starts, so the service and the
tools it runs see them.

To change what the service carries, set or unset the variable in your shell and
run `personalclaw service install` again.

## `personalclaw cron`

Manage scheduled jobs.

| Subcommand | What it does |
|---|---|
| `cron list` | List cron jobs. |
| `cron add NAME MESSAGE (--every SECS \| --cron EXPR) [--channel NAME[:ID]] [--approval-mode auto] [--yes]` | Add a job with one cadence: an interval of more than 0 seconds (`--every`) or a cron expression (`--cron "0 9 * * MON-FRI"`); optionally send results on a chat channel: `--channel telegram` for your DMs there, `--channel telegram:-100123` for a chat. The channel checks the id; `--approval-mode auto` auto-approves the job's tools. A job runs an agent with its tools while you are away, so the command asks what the Triggers page's create dialog asks: without `--yes` it prints the question and creates nothing (exit 1). |
| `cron update JOB_ID [--name] [--message] [--every SECS \| --cron EXPR] [--channel NAME[:ID]] [--approval-mode auto\|default] [--yes]` | Update a job (`default` resets approval mode); give at least one change, and at most one new cadence. The rest of its action stays as it is: its agent, its model, the files it may change and its other settings. A new `--message` changes what the job's agent is told to do, and `--approval-mode auto` lets it approve its own tool calls, so each asks what the Triggers page's editor asks: without `--yes` the command prints the question and changes nothing (exit 1). |
| `cron remove JOB_ID` | Remove a job. |
| `cron pause JOB_ID` / `cron resume JOB_ID` | Pause / resume a job. |
| `cron trigger JOB_ID` | Fire a job immediately, through the running gateway. `JOB_ID` is an id `cron list` shows, such as `clock:nightly-report` for a job `cron add` made; one that is not there is refused with `Job not found` (exit 1) and nothing is sent. |

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
| `memory export [-o FILE]` | Export all memory to JSON (default: stdout). A file it writes is readable only by you. |
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
| `app new NAME --type TYPE [--dir DIR] [--display-name] [--description] [--author] [--force]` | Generate an installable app: `app.json` (validated against core's own manifest parser, with the `cli.*` setup and doctor seams), a provider stub implementing that type's SDK ABC, a passing `test_provider.py`, `README.md`, an MIT `LICENSE`, and a `.gitignore` that keeps bytecode, test caches, virtualenvs and UI build residue out of git (compiled bytecode records the absolute path it was built from). Declares no permissions — add only what the provider uses. |
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
| `config get [KEY]` | Get a value by dot-separated key, or the whole config with no key. **Credentials are withheld**: any field whose name marks it as a secret (`api_key`, `bot_token`, `client_secret`, …) prints as `••••••••`, and a note on stderr names what was withheld. |
| `config get [KEY] --reveal` | The same, with credentials in the clear: a value kept in the credential store is printed itself, not its `{{secret:…}}` reference (one whose value the store no longer holds is printed as the reference, and named on stderr). Use this — not the masked form — as the source of a file you intend to `config set --file` back; that write stores every revealed value again. |
| `config set KEY VALUE` | Set one value (validated through the loader), merged into the existing `config.json` so keys the loader does not model — `providers`, `use_cases`, `slack`, `meta` — are preserved. Refuses (exit 1) if the existing file cannot be read or parsed, rather than overwriting content it could not see. |
| `config set --file FILE` | Apply a whole JSON document to `config.json` — the write side of the `config get --reveal > f.json` round-trip. A top-level block that is in `config.json` but missing from `FILE` **refuses the write (exit 1)** and is named, because this path preserves blocks it is not shown rather than deleting them, so omission cannot mean removal: use `config unset` to remove one. A field arriving as the `••••••••` placeholder means "keep what is on disk"; a placeholder that cannot be matched to a stored value refuses the write (exit 1) rather than overwriting the credential with bullets. |
| `config unset KEY` | Remove a dot-separated key or a whole top-level block from `config.json` — the only way to delete one. A modelled key returns to its default; an unmodeled block (`providers`, `slack`, …) is gone. Refuses (exit 1) if `KEY` is not in the file, so a typo cannot report success, and if the existing file cannot be read. |
| `config edit` | Open a copy of `config.json` in `$EDITOR`. When the editor exits 0 and leaves a JSON object, what you changed is written back, and a setting another process changed while the editor was open is kept unless you changed the same key. An editor that fails, or leaves anything that is not a JSON object, changes nothing (exit 1). A `config.json` that cannot be read opens as it is, and the fixed copy replaces it. |

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
| `security events [-n LIMIT]` | Show recent security event log entries (default 20), masked, each with what it ran or touched: a shell call's command, the file a write changed. |
| `security verify` | Verify security event log HMAC integrity. |

## Backup & restore

| Command | What it does |
|---|---|
| `personalclaw snapshot [OUTPUT_DIR] [--keep N] [--list]` | Create a portable backup of PersonalClaw state (keeps the N most recent, default 7, and the newest one a restore drill verified while the newer ones are unverified; it names each snapshot it removes and why; `--list` shows existing snapshots). A snapshot is the backup a restore reads: it holds every store whole, your skills, scripts, uploads and the other folders of files included, with every file at its path. Each database in it, wherever it is in the home and an installed app's own included, is one consistent copy taken through SQLite's backup API, never the bytes of a file being written, and a file of yours named like one of PersonalClaw's databases is kept like any other file. It is written to `<home>/snapshots` unless `snapshot_dir` says otherwise, so it is on this machine until you point it somewhere else, and it is readable only by you from its first byte, as is every file extracted from it. It carries no credential value — keys and tokens stay in the credential store, and settings hold references to them. It leaves out each app's engine (the app's own Python environment, `apps/<app>/venv`), which is built for the machine it runs on. |
| `personalclaw restore [SNAPSHOT] [--mode replace\|merge] [--dry-run] [--components LIST] [--list-components] [--force]` | Restore state from a snapshot `.tar.gz`, or from an export `.zip` (Settings → Import / Export), which is restored whole, so `--components` does not apply to it. With the gateway running, a merge of either kind runs, here or from the dashboard, and a replace is refused by both in the same words, naming this command. A merge brings each log's entries in where they belong in time — notifications, run history, feedback, the model-call audit and the security log — so a log's newest entries stay its newest, and its bound lets the oldest go. A merge that leaves a part unchanged (a store it could not read, a table it had to skip, a log that kept changing while it was merged, or a part the home holds behind a link) names each one in its last line instead of `✅ Merge complete.` and exits 1, an export archive's as a snapshot's; the dashboard's merge and import name them too. No restore writes through a link the home holds: where the home has a symbolic link at a path the snapshot or archive brings, at a folder on the way to it, or beside a database where SQLite keeps its log, or a file there with another name (a hard link), that part is left exactly as it is. A merge reads nothing through the link and writes nothing there; a replace neither moves it aside nor puts the snapshot's copy in its place, says `Replace finished, but … left unchanged` with the link's path, and exits 1. Remove the link and restore again to bring that part in. An export archive's merge says what became of each store it holds: merged (the knowledge library, the vocabulary, the learning log and each project's memories and learning log row by row, tags by name), copied into a home without it, or left unchanged and why (this home's settings, feedback and any other single-file store it already has stay its own). A merge of either kind brings in every row of the other home's learning log once, matched by an identity each row keeps in every home, so merging the same archive again adds nothing. A merge takes an installed app's database as it takes the rest of the app's data: one this home lacks comes in whole, and one it has stays as it is. No restore puts a database's write-ahead log (`-wal`, `-shm`) into the home: a merge puts a database in on its own, removing first a log this home kept at its path with no database there (which SQLite discards when it next opens that database), and a replace moves this home's database aside with its log, so a restored database never opens with pages another copy of it wrote. A refused replace names the running gateway's pid and port. A replace (of a snapshot or an export archive) brings each automation that runs on its own back paused, and says how many and why (`--dry-run` says how many it would): a snapshot names the PersonalClaw home that took it, and one from another home may still be running there, so resume them with **Resume all** on the Triggers page once that home is retired. Restoring this home's own snapshot at a terminal asks whether to resume them now; a snapshot that names no home is treated as another's. A merge brings automations in switched off already. `--force` replaces even while the gateway runs. It names each restored app whose engine is not installed here; Install engine, on the app's card in Settings → Providers, puts it back. `--mode replace` moves the current state into `pre-restore-<timestamp>/`, as it is (never a copy of it), except each app's engine: an app the snapshot brings back keeps the engine it has here, since an engine is built for this machine and a snapshot never carries one, and the restore names those apps. An app the snapshot does not have is set aside in that folder with its engine, whose size the restore names, and deleting the folder reclaims it. |
| `personalclaw backup export [OUT_DIR] [--incremental]` | Export your records as **deterministic shards** — canonical JSONL per store plus a SHA-256 manifest, byte-identical for identical state (so it diffs cleanly), in the format sync carries between machines. It is not a backup: it holds no folder of files (skills, scripts, uploads, the workspace, installed apps), each database is rows, and nothing restores from it. The backup is `personalclaw snapshot`. Defaults to `<home>/shards`; `OUT_DIR` must be empty or hold an earlier export, of which only what the export wrote is replaced. It names each file it could not carry, and exits non-zero when there is one. It reads nothing through a link the home holds: a store behind a symbolic link (at its folder, at a folder on the way to it, or beside a database), or a file of a store that is a symbolic link or has another name (a hard link), is one it could not carry, named with why, and a link at the home's `machine_id` refuses the export. `--incremental` re-exports only the stores whose content changed. Secrets are never exported. Every shard and the manifest are readable only by you. |
| `personalclaw backup validate [SHARD_DIR]` | Verify an export end to end: the manifest parses, every declared shard exists, and each one's byte length, row count, and SHA-256 re-derive — plus every row re-parses. **Exits non-zero on any problem**, so it works as a cron/CI check. A snapshot is verified by the restore drill (Settings → Backups → Verify a restore). |

**Scheduled backups say when they fail.** While automatic backups are on
(`durability.auto_backup`), the gateway takes a snapshot every night, into `<home>/snapshots` or
`snapshot_dir`, exports your records every hour into `<home>/shards`, and runs a restore drill
once a month (`durability.restore_drills`). Each run is recorded with what it did, whether it was
a scheduled one or Run now on Settings → Backups. When a snapshot or an export fails, for example
because its folder may not be written or its disk is full, its line on Settings → Backups reads as
failed, with the reason in plain words, what to do and how many runs in a row it has failed. When
that job last worked stays beside the failure, and for the snapshot so does the newest snapshot a
restore can still bring back. A note says so when a job starts failing, again when it fails for a
new reason, and once when it works again. The Doctor page and `personalclaw doctor` report it for
as long as it lasts. A scheduled snapshot or export that fails stays due, so it is tried again at
the next check, every five minutes. An export that could not carry a file keeps its hour instead:
it names the file, and each hourly export reads that store again until the file can be carried.
The restore drill reads each database the snapshot holds, and only those: a file of yours named `.db` that is not a database is not read as one. It notes every verdict, passed or failed.

## Disk footprint

| Command | What it does |
|---|---|
| `personalclaw footprint [--json] [--reclaim]` | Per-store **bytes on disk** for every store in the state manifest, plus a **growth rate** and the store that is growing fastest. `--reclaim` compacts every database now (FTS5 merge → `PRAGMA optimize` → `VACUUM`) and reports the measured change: the bytes freed, or how much the stores grew when compacting left them larger (a database the running gateway holds open can keep the rewritten pages in its `-wal` file). With `--json`, `reclaim.net_change_bytes` is signed and `freed_bytes`/`growth_bytes` are never negative. |

Each run records one sample, so **a rate appears from the second run onward** — a
single reading cannot tell "not growing" from "measured once", and the report says
`not yet measurable` rather than printing a fabricated `0 B/day`.

You do not have to run this for the space to come back. The gateway's durability loop
prunes runs past `workflows.retention_per_def` and reclaims the freed pages **daily**,
independently of `durability.auto_backup` — turning off scheduled backups does not stop
reclaiming disk. `--reclaim` is for wanting the space now rather than at the next
cadence; it shares a lock with the scheduled pass, so the two cannot collide.

Deleting rows from a SQLite store does **not** shrink the file on its own — the pages
are marked free and reused later. That is why pruning and reclaiming are one job.

## Inbound surfaces

PersonalClaw can expose a **read-only MCP endpoint** at `POST /mcp` so a local MCP
client (your IDE, an MCP inspector) can ask it questions. It is off by default and
stays off until you both mint a token and flip the flags — and it only answers
loopback callers.

There are **five** inbound surfaces in the config schema — `openai`, `mcp`, `a2a`,
`capture`, `bridge` — each with its own token and its own `enabled` flag. On the
gateway's own port, `mcp` answers at `/mcp`; `openai` at `/v1/chat/completions`,
`/v1/models` and `/v1/audio/{speech,transcriptions,voices}`; `a2a` at
`/a2a/agent-card`, `/a2a/tasks` and `/a2a/tasks/{task_id}`; and `capture` at
`/capture/v1/chat/completions`, `/capture/v1/messages` and `/capture/import`. `bridge`
listens on a loopback port of its own. Each route checks its surface's own token, so
the dashboard's sign-in check steps aside for exactly those routes, and any other path
under those prefixes still needs a dashboard sign-in.

On the `openai` surface, `model` names one of your **agents** (`personalclaw/<agent>` or the
bare name), and `GET /v1/models` lists the ones a client may ask for. A client's requests share
a session: one per `user` field (or `X-PersonalClaw-Session` header) when the client is
registered to keep its conversation, otherwise one for the client. A client that keeps no
conversation is answered as if each request were its first: each runs on a runtime started for
it (an agent CLI opens a new session for it), and nothing an earlier request said, or was
answered, reaches the next one's model. A request that names another agent than its session runs
hands the session to that agent first, the way the dashboard's agent picker hands a chat over, so
its turn runs on a runtime built for that agent, with its model and its instructions, and what
the conversation records names it.

Whether a registered client keeps its conversation is your choice, and it keeps none until you
make it. Register the client with `"persistent_sessions": true` (`POST
/api/external-access/clients`), or change it at any time: on the client's row under **Settings →
External Access** (*Conversation*: **One per user** or **Each request alone**), or with `POST
/api/external-access/clients/{client_id}/persistent-sessions` and `{"persistent_sessions": true}`
or `false`. Only a client bound to the `openai` surface has a conversation to keep, so the choice
is refused for any other; a caller signing in with the surface's own token is no registered
client and keeps none. A change starts the client's conversations over, whichever way it goes: a
turn running when you make it finishes and is answered as it began, and no request after it
continues a conversation from before it, neither one the client kept nor the last request it had
answered alone. Those conversations stay in your chat history as they were. Each change is a row
in the security event log.

A session answers one request at a time. A request that arrives while its session is still
answering another, or while that answer is still being sent, is refused with `409` and the code
`session_busy`, and the answer in progress is left as it is. Ask again once it has finished; a
client that keeps its conversation can also send it in another session.

What a client asks for on the `openai` surface is work nobody watches, so the spend caps for such
work (**Settings → Guardrails**) hold all of it: its chat turns, its speech (`/v1/audio/speech`)
and its transcriptions (`/v1/audio/transcriptions`). Each is counted against the day's dollar cap
and in **Settings → Usage**, under the client, and each is held to the run ceiling an inbound
caller is given. A request past a cap is refused before anything is spoken or transcribed, in the
cap's own words: speech answers `503` with the code `tts_spend_refused`, a transcription `502`
with `transcription_failed`. A session a client names in a header (`X-Session-Key`) changes none
of this: the route signs its clients in itself, and a request is the work of the client its token
proves.

| Command | What it does |
|---|---|
| `personalclaw inbound token create <surface> [--rotate] [--ttl 90d]` | Mint that surface's bearer token, stored in the **credential store** (keychain, else `.env` at `0600`) as `PERSONALCLAW_INBOUND_<SURFACE>_TOKEN`. **Printed once** — copy it into your client immediately. It works for `--ttl` (`30m`, `20h`, `7d`; default and limit 90 days — longer is refused, never shortened), and the output says until when. `--rotate` replaces a working token, which immediately invalidates the old one; a token that expired or was revoked is replaced without it. |
| `personalclaw inbound token show <surface>` | Report whether a usable token is configured, when it was created and when it stops working — or why it is not usable. Deliberately never prints the value: a credential the CLI can re-read is one an unattended process can exfiltrate. Lost it? Rotate. |
| `personalclaw inbound token revoke <surface>` | Revoke that surface's token at once: whatever still presents it is refused and told it was revoked. The surface stays on, so a registered client's own token keeps working, and the revoked value stays refused for as long as it is configured — even when the environment sets it again at the next start. `create` then makes a new one. |
| `personalclaw inbound webhook create <automation-id> [--label NAME] [--ttl 90d]` | Make a **sender token** for a webhook automation, named by its id as `personalclaw cron list` or its page shows it (`webhook:<name>` or `store:webhook:<name>`). **Printed once**, with the address a program posts to and a `curl` command that fires the automation from this machine. It is bound to the webhook alone, pinned to that automation and kept only as a hash; it works for `--ttl` (default and limit 90 days). Settings → External Access and Settings → Devices list it. |
| `personalclaw inbound webhook list [<automation-id>]` | The sender tokens made here, or one automation's: when each stops working, and when it was last used. Never a token. |
| `personalclaw inbound webhook revoke <client id>` | Revoke a sender token at once: a program still sending it is refused, and told it was revoked. |

The webhook is not one of the five: it is always served, and what admits a call is a token made
for it — a sender token for a webhook automation (`POST /api/triggers/<id>/fire`), or your webhook
token (`hooks.webhook_token`) for an agent's callback (`POST /api/hooks/agent`). Both take
requests only from programs on this machine; see
[security](../architecture/security.md#webhooks) and
[automations](../guides/automations.md#when-a-program-starts-one-webhooks).

A token is refused if it is shorter than 32 bytes, equal to the dashboard token or
internal secret, or equal to **another surface's** token — five surfaces sharing one
bearer would collapse five independently revocable credentials into one.

Every integration token lasts at most 90 days — a surface token for its `--ttl`, a registered
client's for the `ttl` it was registered with. A token from before lifetimes existed, or one set
outside the CLI (Settings → Secrets, an environment variable), lasts 90 days from the first time
the gateway sees it; a client registered before then, 90 days from its registration. Past its
lifetime a token is refused with the same `unauthorized` code as any other refusal, and a
sentence saying it stopped working, when, and how to get a new one. **Settings → Devices** lists
every integration token with when it stops working, and revokes any of them.

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

A surface that is off, or has no working token, answers `404` on its next request, and the
audit trail names the switch that refused it: every request (allowed or refused) is recorded in
`<home>/inbound_audit.jsonl`, and refusals also land in the security event log. Nothing needs a
restart — except the control bridge, whose own loopback listener starts with the gateway.

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
| `personalclaw mcp-core` | The internal MCP server entry point spawned by ACP agents — not user-facing, and genuinely hidden from `--help` (`cli.HIDDEN_COMMANDS`). Still dispatchable; it just is not advertised. (`personalclaw mcp-schedule` was retired with the `schedule_*` tool aliases in #328 and no longer exists.) |
| `personalclaw optimize-harness STEP` | One step (`preflight`, `experience`, `scope-check`, `adjudicate`) of the bundled `optimize-harness` workflow template, which its bash steps run with the step's inputs in `PC_OPT_*` variables; each reads and writes only the search's own sandbox, and answers in JSON on stdout, a refusal included (its `error` is the step's failure). Its scoring and filing steps are not among them: scoring calls models and filing writes the proposal queue, the Inbox and the study registry in the home, so both run in the gateway, as the `optimize-score` and `optimize-file` actions. Not user-facing, and hidden from `--help` the same way. |

---

See also: [Configuration reference](configuration.md) ·
[API overview](api-overview.md) · [Getting started](../guides/getting-started.md)
