---
name: pclaw-features
description: Channel-neutral reference for PersonalClaw's capabilities — sessions, agents, models, dashboard, scheduling, goal loops, tasks, subagents, file/media handling, and config — so you can explain what PersonalClaw can do and route the user to the right surface.
always: false
triggers: help, commands, what can you do, getting started, onboard, capabilities, features, dashboard, setup, how do i
---
# PersonalClaw Features

PersonalClaw is a personal AI workspace: persistent **sessions**, configurable
**agents** and **models**, autonomous **goal loops**, a **dashboard** web UI, and
a set of native tools (files, knowledge, memory, subagents, scheduling,
artifacts, notifications). It runs as a long-lived gateway and can be reached
from multiple surfaces — the dashboard, and connected **channels** (e.g. a Slack
channel, if one is configured). This skill describes capabilities
**channel-neutrally**: prefer the dashboard or the native tools, and only mention
a specific channel's command syntax when the user is actually on that channel.

## Core capabilities

| Capability | What it is |
|---|---|
| **Sessions** | Persistent chat sessions with warm history. Resume an earlier session or start fresh; sessions survive restarts. |
| **Agents** | Named agent profiles (system prompt, model binding, tools, hooks, workflows). Switch the active agent or override it for one session. |
| **Models** | A bound model per session/agent. `auto` lets PersonalClaw choose; or pin a specific native/remote model. |
| **Goal Loops** | The unified autonomous goal engine — classify a target, plan it, then loop one self-directed cycle per turn while a deterministic supervisor decides done-ness (see the `loop-worker` skill). |
| **Subagents** | Spin up background workers for parallel/isolated work via `subagent_run` (see the `delegation` skill). |
| **Tasks** | Tracked units of work with status and dependency links, via the `/api/tasks` CRUD and the native task provider (see the `task-and-project` skill). |
| **Scheduling** | Recurring/deferred jobs via the `schedule_*` tools (add/list/get/update/pause/resume/remove/trigger). |
| **Knowledge** | A searchable knowledge pool: `knowledge_search` / `knowledge_get` / `knowledge_create` (see the `knowledge-grounding` skill). |
| **Memory** | Durable lessons, preferences, and facts: `memory_remember` / `memory_list` / `memory_forget` (see the `memory-discipline` skill). |
| **Artifacts** | Named, versioned generated content (widgets, HTML, docs) via `artifact_*` (see the `artifacts` skill). |
| **Notifications** | Reach the user out-of-band via `notify` (and `notify_attachment` for files). |
| **Hooks** | Event-driven handoffs registered with `hook_register`. |
| **Workflows** | Reusable standard operating procedures: `workflow_list` / `workflow_get` / `workflow_run` / `workflow_create`. |

## Dashboard

The dashboard is the primary web UI — chat, sessions, agents, models, memory,
knowledge, scheduling, goal loops, artifacts, files, and system monitoring all
live there. Point the user to the relevant dashboard page when they ask "where
do I…": e.g. memory and lessons under the memory page, scheduled jobs under the
scheduling page, saved artifacts at `/artifacts/<slug>`.

**Access:**
- **Local / loopback** — open the dashboard URL directly; loopback access needs
  no token. Over SSH, forward the port (`ssh -L <port>:localhost:<port> <host>`)
  and open `http://localhost:<port>`.
- **Remote** — if a public dashboard URL is configured, remote access uses a
  short-lived presigned link (request one from whatever surface you're on).

## Configuration

Configuration lives in `~/.personalclaw/config.json` (override the home dir with
`PERSONALCLAW_HOME`). Common fields:

- `agent.approval_mode` — `"auto"` (approve all tool calls) or `"interactive"`
  (confirm each tool call).
- `agent.model` — default model (`"auto"` or a specific model id).
- `dashboard.url` — hostname/port/bind for remote dashboard access. Omit for
  localhost-only.

```json
{
  "agent": { "approval_mode": "interactive", "model": "auto" },
  "dashboard": { "url": "http://my-host.example.com:8080" }
}
```

| Environment variable | Purpose | Default |
|---|---|---|
| `PERSONALCLAW_HOME` | Config/data directory | `~/.personalclaw` |
| `PERSONALCLAW_PORT` | Dashboard port (dev mode) | `10000` |
| `PERSONALCLAW_PROJECT_DIR` | Agent config/skills directory | Auto-detected |

## File & media handling

PersonalClaw processes attachments the user sends on any channel that supports
them:

| Attachment type | Behavior |
|---|---|
| **Voice memos** (audio/*) | Transcribed locally (speech-to-text), text injected into the conversation. |
| **Images** (png, jpeg, gif, webp, bmp) | Sent to the model as vision input. |
| **Text/code files** (text/*) | Content read and injected inline (truncated for very large files). |
| **Other files** | Metadata noted; not auto-downloaded. |

Speech-to-text is on by default where a transcription backend is available.

## Channel surfaces

PersonalClaw can be driven from connected channels in addition to the dashboard.
Each channel may expose its own command syntax — describe it only when the user
is on that channel. **For example, if using a Slack channel:** users typically
request a dashboard link, toggle auto-approval, switch agents, manage tracked
channels, or force-halt a running turn via that channel's commands; the channel
also defines per-channel activation modes (respond always / on mention / observe
/ off). Treat any such channel-specific command surface as an implementation
detail of that channel, not a core PersonalClaw concept — the underlying
capabilities (sessions, agents, scheduling, stop/cancel) are the same regardless
of surface.
