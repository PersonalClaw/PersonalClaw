"""PersonalClaw core agent tools + the ``mcp-core`` MCP-server composition root.

Two roles:

* **Residual core tools** — the cross-cutting tools that don't belong to a single
  entity category, owned here and served in-process via the bundled
  ``personalclaw-core`` tool provider:
      skill_invoke       — load a skill's full instructions on demand
      wait               — pause the loop for an external system
      hook_register      — register a webhook-listener session
      notify             — reach the user via their notification channel
      notify_attachment  — notify with a file attachment
      loop_nudge_stop    — halt the autonomous self-nudge loop
  (The entity-specific tool groups live in their own modules + providers —
  ``mcp_subagents`` / ``mcp_memory`` / ``mcp_artifacts`` / ``mcp_prompts``.)

* **MCP-server composition root** — ``run_mcp_core_server`` runs as
  ``personalclaw mcp-core``, the single stdio MCP server an ACP CLI (claude-code/
  codex) spawns. It aggregates this module's tools with every category module's
  (``_AGGREGATED_CATEGORY_MODULES``) so that one server exposes the FULL tool set,
  while the native loop calls each provider's handlers in-process.

The shared session/HTTP plumbing (``_resolve_session_key`` / ``_get`` / ``_post`` /
``_delete`` / ``_CURRENT_AGENT_ID``) is owned here and imported by the category
modules. Tool names are entity-prefixed and PClaw-native.
"""

import contextvars
import json
import logging
import os
import platform
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from personalclaw.config.loader import AppConfig, config_dir
from personalclaw.dashboard.origin import parse_dashboard_url

logger = logging.getLogger(__name__)

# Session key for the in-process tool caller (the native agent runtime), set
# per-turn. Subprocess MCP servers carry PERSONALCLAW_SESSION_KEY in their env;
# the in-process native loop runs inside the gateway and has no per-turn env, so
# it publishes its session key here for _resolve_session_key() to consult. This
# is what lets a subagent spawned by a native worker inherit the parent's trust
# (goal loop unattended mode), instead of resolving a stale gateway PID file.
_CURRENT_SESSION_KEY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_current_session_key", default=""
)


def set_current_session_key(session_key: str):
    """Bind the in-process tool caller's session key for the current context.

    Returns the contextvars.Token so the caller can reset() it after the turn.
    """
    return _CURRENT_SESSION_KEY.set(session_key or "")


def reset_current_session_key(token) -> None:
    """Restore the prior session-key binding (pass the token from set_…)."""
    try:
        _CURRENT_SESSION_KEY.reset(token)
        return
    except (ValueError, LookupError):
        pass


def get_current_session_key() -> str:
    """The session key bound for the current tool-calling context ("" if none).

    Lets the external-MCP adapter route a call to the per-session connection of a
    stateful server, so each session's browser/shell state stays isolated."""
    return _CURRENT_SESSION_KEY.get()


# The turn's resolved agent binding id (native profile name | acp:<cli>/<modeId>) —
# see `agents.identity.resolve_agent_id` for the canonical form. Published by the
# native loop so a tool can attribute or scope work to the agent that is running.
# Kept here (not in the workflow package) because it is not workflow-specific: the
# old `workflow_create` was its first consumer, not its owner.
_CURRENT_AGENT_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_current_agent_id", default=""
)


def set_current_agent_id(agent_id: str):
    """Bind the in-process tool caller's resolved agent id. Returns a reset token."""
    return _CURRENT_AGENT_ID.set(agent_id or "")


def reset_current_agent_id(token) -> None:
    """Restore the prior agent-id binding (pass the token from set_…)."""
    try:
        _CURRENT_AGENT_ID.reset(token)
    except (ValueError, LookupError):
        pass


def _resolve_api_base() -> str:
    """Resolve the gateway API base URL from ``dashboard.url`` config."""
    cfg = AppConfig.load()
    _host, port = parse_dashboard_url(cfg.dashboard.url)
    return f"http://localhost:{port}"


_API = _resolve_api_base()


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "skill_invoke",
            "description": (
                "Load a skill's full instructions by name. Your context carries only "
                "a compact INDEX of available skills (name + one-line description); "
                "when a listed skill fits the task, call this to pull its complete "
                "step-by-step body before acting. Prefer this over reading the skill "
                "file directly — it records the skill as used so the library can keep "
                "what helps and retire what doesn't."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name from the index (e.g. 'tiny-url' or 'auto/release').",  # noqa: E501
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "skill_search",
            "description": (
                "Find a skill by capability across your ENTIRE skill library — not just "
                "the skills surfaced in your context this turn. Use when the task might "
                "have a matching skill but you don't see one in the index. Returns "
                "ranked name + description; then call skill_invoke(name) to load its "
                "full steps. Args: query (str), optional limit (int)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What you're trying to do (capability/intent).",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 20)."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_context",
            "description": (
                "Call at the START of every task to load this project's routed context. "
                "Returns, in lost-in-the-middle order: hard RULES & directives (the "
                "project brief + operating procedure) at the top; then scored mid-tier "
                "content — how this user works (memory-derived lessons/preferences), the "
                "skills available here, and titled pointers to reference material "
                "(knowledge items — retrieve a body on demand, never inlined); and at the "
                "bottom an L0 CATALOG of what was NOT loaded, each with the tool/route that "
                "pulls it (memory_recall, skill_invoke, GET /api/knowledge/items). "
                "Optionally pass a `query` to score the mid tier against the task at hand, "
                "and a `project_id` to target a specific project (defaults to this "
                "session's project). Read-only: never writes to memory or knowledge."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What you're about to do — scores the mid-tier memory/"
                            "knowledge content. Omit to score against the project itself."
                        ),
                    },
                    "project_id": {
                        "type": "string",
                        "description": (
                            "Target project id (e.g. 'p-1a2b3c4d'). Omit to use the "
                            "current session's bound project, else the Personal default."
                        ),
                    },
                },
                "required": [],
            },
        },
        {
            "name": "skill_remember",
            "description": (
                'Capture a skill the USER just taught you ("from now on…", "always do X", '
                '"remember this workflow"). Writes a SESSION-LIVE draft: it\'s active for the '
                "rest of THIS chat immediately, and at the chat's end the user is asked whether "
                "to save it permanently (to this agent or all agents) or forget it. Use ONLY for "
                "durable how-to the user explicitly wants kept — not for one-off facts (that's "
                "memory) or transient state. Args: title (short name), body (the steps/rule)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short skill name, e.g. 'deploy checklist'.",
                    },
                    "body": {
                        "type": "string",
                        "description": "The procedure/rule to remember (markdown).",
                    },
                },
                "required": ["title", "body"],
            },
        },
        {
            "name": "template_save_from_session",
            "description": (
                "Propose saving the multi-step procedure just carried out in this session as a "
                "reusable workflow template. Files a DRAFT proposal for the user to accept or "
                "reject — it never writes a definition, so use it freely when the work looks "
                "repeatable (use workflow_author instead when the user asks to SAVE a workflow "
                "outright). A deterministic gate scores the steps first and may decline "
                "(one-step plans, no reusable placeholders, a template that already exists); the "
                "decline and its reason come back to you. Put {{placeholders}} wherever a value "
                "would differ on the next run — steps with nothing parameterizable are a "
                "recording of one run, not a template, and get declined."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Proposed template name: lowercase, digits, hyphens.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "The procedure, one step per entry, in order. Use {{placeholders}} "
                            "for values that change between runs."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "One line on what the procedure accomplishes.",
                    },
                },
                "required": ["name", "steps"],
            },
        },
        {
            "name": "project_context_review",
            "description": (
                "Review THIS conversation and propose updates to the current project's context — "
                "its instructions, an inlined context file, or a skill. Call ONLY when the user "
                "asks you to review/capture what was established here (e.g. 'review this chat and "
                "update the project'); it does not run automatically. You identify the changes "
                "from the conversation and pass them as `items`, each with a one-line `rationale` "
                "the user reads before deciding. Nothing is written: each item becomes a PROPOSAL "
                "in the review queue, and the project changes only when the user accepts it there. "
                "A change the user already declined is not re-proposed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The proposed changes.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "project_instruction",
                                        "project_file",
                                        "project_skill",
                                    ],
                                    "description": (
                                        "instruction = append to the project's operating "
                                        "procedure; file = an inlined context file; skill = a new "
                                        "reusable skill."
                                    ),
                                },
                                "body": {
                                    "type": "string",
                                    "description": "The exact content to write once accepted.",
                                },
                                "rationale": {
                                    "type": "string",
                                    "description": "Why this change — shown in the review queue.",
                                },
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "Filename (project_file) or skill name (project_skill). "
                                        "Omit for an instruction."
                                    ),
                                },
                            },
                            "required": ["kind", "body", "rationale"],
                        },
                    },
                    "project_id": {
                        "type": "string",
                        "description": (
                            "Target project id. Omit to use this session's bound project."
                        ),
                    },
                },
                "required": ["items"],
            },
        },
        {
            "name": "dashboard_tile_propose",
            "description": (
                "PROPOSE a saved artifact as a dashboard tile on the user's composable home. "
                "The artifact must already be saved (a slug); this pins a PROPOSAL that renders "
                "with an accept/dismiss chip — the user decides. You never silently rearrange "
                "their home. Use when you've built a view/artifact the user would want to keep "
                "visible (a live dashboard, a status board). Args: slug (the artifact slug), "
                "size (s|m|l|full, default m), view_id (target view; omit for the Overview home)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "The saved artifact's slug to pin as a tile.",
                    },
                    "size": {
                        "type": "string",
                        "enum": ["s", "m", "l", "full"],
                        "description": "Flow-layout size hint (default m). No coordinates.",
                    },
                    "view_id": {
                        "type": "string",
                        "description": "Target view id. Omit to propose onto the Overview home.",
                    },
                },
                "required": ["slug"],
            },
        },
        {
            "name": "wait",
            "description": (
                "Pause execution for a specified duration while preserving full session "
                "context. Use when waiting for external systems (code review, CI "
                "pipeline, deployment). Max 1800s (30 min)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Duration to wait in seconds (60-1800)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why we are waiting (shown to user)",
                    },
                },
                "required": ["seconds", "reason"],
            },
        },
        {
            "name": "hook_register",
            "description": (
                "Register a webhook listener so an external system can inject a message "
                "into a dedicated agent session later. Returns the webhook URL and session "
                "key. Use this when you need to hand off to an external process (e.g. "
                "submit a PR, then wait for CI to call back with results). "
                "The external system POSTs to the returned URL with the results."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hook_id": {
                        "type": "string",
                        "description": "Unique identifier for this hook (e.g. 'review:pr-123')",
                    },
                    "context_summary": {
                        "type": "string",
                        "description": "Summary of current work context for session resume",
                    },
                },
                "required": ["hook_id", "context_summary"],
            },
        },
        {
            "name": "notify",
            "description": (
                "Notify the user via their configured notification channel(s) "
                "(dashboard notification, plus any connected messaging channel such "
                "as Slack or Discord). By default reaches the owner. Use this whenever you "
                "decide someone should be told something — most commonly in silent "
                "cron jobs, but any time proactive notification is needed."
                "\n\nDelivery contract for cron jobs:"
                '\n  1. Try the originating dashboard session first (session="origin"),'
                " so the session agent can react to the message, not just display it."
                " When injection succeeds, the message appears in the chat UI — no"
                " extra notification is fired."
                "\n  2. Fall through to the owner's messaging channel if origin is"
                " unreachable (tab closed, history deleted, or cron has no origin —"
                " e.g. created from the dashboard UI)."
                '\n  3. On the fallback path (including session="channel" and non-cron'
                " callers), a dashboard notification also fires so messages that"
                " couldn't reach their origin still surface. Invariant: messages are"
                " never silently dropped."
                "\n\nsession param:"
                '\n  "origin"  — inject into the session that spawned this cron.'
                '\n  "channel" — explicitly route to the owner\'s messaging channel,'
                " bypassing origin."
                '\n  omitted + cron caller → auto-applies "origin" (you usually'
                ' want this — pick "channel" only if the message should specifically'
                " reach the messaging channel and not the spawning chat)."
                "\n  omitted + non-cron caller → owner channel (default behavior)."
                "\n\nExplicit channel=... or user=... always wins and suppresses"
                " the auto-default."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Message text. Also used as fallback when blocks are provided.",  # noqa: E501
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title for the notification",
                    },
                    "blocks": {
                        "type": "array",
                        "description": "Optional rich-message blocks array (Block Kit format). When provided, the message is sent as a rich message with text as fallback.",  # noqa: E501
                        "items": {"type": "object"},
                        "maxItems": 50,
                    },
                    "channel": {
                        "type": "string",
                        "description": "Target channel ID (e.g. C0123ABC456). Must be a tracked channel. Omit to send to owner DM.",  # noqa: E501
                    },
                    "user": {
                        "type": "string",
                        "description": "Target user ID (e.g. U0123ABC456) to DM. Must be an allowed user. Omit to send to owner DM.",  # noqa: E501
                    },
                    "unfurl_links": {
                        "type": "boolean",
                        "description": "Whether to unfurl URL link previews. Defaults to true.",
                    },
                    "unfurl_media": {
                        "type": "boolean",
                        "description": "Whether to unfurl media (images/video) previews. Defaults to true.",  # noqa: E501
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": (
                            "Optional channel thread timestamp (e.g. '1712793600.123456'). "
                            "When provided, the message is posted as a threaded reply under "
                            "that parent message. Works with 'channel' (thread in channel) "
                            "or 'user' (thread in DM)."
                        ),
                    },
                    "reply_broadcast": {
                        "type": "boolean",
                        "description": (
                            "When true and 'thread_ts' is set, also broadcast the threaded reply "
                            "to the channel's main message list. Requires 'thread_ts' — passing "
                            "reply_broadcast=true without thread_ts returns 400. Defaults to false."
                        ),
                    },
                    "session": {
                        "type": "string",
                        "enum": ["origin", "channel"],
                        "description": (
                            "Routing opt-in/opt-out for cron messages. "
                            '"origin" injects into the dashboard session that created '
                            "this cron (auto-applied for cron callers that set neither "
                            'channel nor user). "channel" explicitly routes to the '
                            "owner's messaging channel, bypassing origin. Fallback paths "
                            '(origin unreachable, explicit "channel", non-cron caller) '
                            "also fire a dashboard notification so the message isn't "
                            "silently dropped."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
        {
            "name": "notify_attachment",
            "description": (
                "Send a file to the user. Copies the file to the outbox and "
                "notifies the dashboard/channel with a download link. Use when "
                "you've generated a report, export, artifact, or any file the "
                "user should receive."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file to send"},
                    "description": {
                        "type": "string",
                        "description": "Brief description of what the file is",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "loop_nudge_stop",
            "description": (
                "Stop the auto-nudge loop driving your current session. Call this "
                "when you determine the loop should halt (e.g. goal complete, "
                "blocked on user input, or a STOP sentinel file indicates shutdown). "
                "Removes the loop from the AutoNudgeService so no further nudges "
                "fire into this session. Safe to call even if no loop is active — "
                "returns a no-op message."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the loop is being stopped (logged for audit)",
                    },
                },
            },
        },
    ]


def _internal_secret() -> str:
    """Read the per-session secret for IPC authentication."""
    try:
        return (config_dir() / ".local_secret").read_text().strip()
    except Exception:
        return ""


def _get_ppid(pid: int) -> int:
    """Get parent PID cross-platform. Returns 0 on failure."""
    try:
        if platform.system() == "Linux":
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("PPid:"):
                    return int(line.split()[1])
        else:
            out = subprocess.check_output(
                ["ps", "-o", "ppid=", "-p", str(pid)], text=True, timeout=2
            )
            return int(out.strip())
    except Exception:
        pass
    return 0


def _resolve_session_key() -> str:
    """Return the real session key, falling back to PID file when env var is absent.

    Warm-pool ACP agent processes have no PERSONALCLAW_SESSION_KEY env var (the pool
    spawns with an empty key so rekey() + PID file provide the correct mapping).

    After rekey, the process tree may be: gateway -> ACP agent (pool, has PID file)
    -> ACP agent child -> MCP server.  os.getppid() returns the
    immediate parent which has no PID file.  Walk up ancestors
    until we find a matching file or hit init.
    """
    sk = os.environ.get("PERSONALCLAW_SESSION_KEY", "")
    if sk:
        return sk
    # In-process native runtime: it runs inside the gateway (no per-turn env var
    # and no PID file of its own), so it binds its session key via a contextvar.
    sk = _CURRENT_SESSION_KEY.get()
    if sk:
        return sk
    try:
        cfg_dir = config_dir()
        pid = os.getppid()
        seen: set[int] = set()
        while pid > 1 and pid not in seen:
            seen.add(pid)
            pid_file = cfg_dir / f"session_pid_{pid}.txt"
            if pid_file.exists():
                return pid_file.read_text(encoding="utf-8").strip()
            pid = _get_ppid(pid)
    except Exception:
        pass
    return ""


def _post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json", "X-Internal-Secret": _internal_secret()}
    sk = _resolve_session_key()
    if sk:
        headers["X-Session-Key"] = sk
    req = urllib.request.Request(
        f"{_API}{path}",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _get(path: str) -> dict:
    headers = {"X-Internal-Secret": _internal_secret()}
    sk = _resolve_session_key()
    if sk:
        headers["X-Session-Key"] = sk
    req = urllib.request.Request(
        f"{_API}{path}",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _delete(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode() if body else None
    headers = {"X-Internal-Secret": _internal_secret()}
    sk = _resolve_session_key()
    if sk:
        headers["X-Session-Key"] = sk
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{_API}{path}",
        data=data,
        headers=headers,
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against schema. Returns cleaned args."""
    from personalclaw.validation import MCP_CORE_SCHEMAS, validate_tool_args

    schema = MCP_CORE_SCHEMAS.get(name)
    if schema:
        return validate_tool_args(args, schema)
    return args  # tools without schemas (memory_list) pass through


def _current_session_thread_ts() -> str | None:
    """Read the current session's thread_ts from the most recent session_pid file."""
    from pathlib import Path

    from personalclaw.hooks import safe_read_file_bytes

    try:
        pid_files = sorted(
            (Path.home() / ".personalclaw").glob("session_pid_*.txt"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if pid_files:
            raw = safe_read_file_bytes(str(pid_files[0]))
            if raw is None:
                return None
            ts = raw.decode("utf-8").strip()
            if ts and not ts.startswith("dashboard:"):
                return ts
    except Exception:
        pass
    return None


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key="mcp_core",
        downstream_service="personalclaw-core",
    )


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    if name == "skill_invoke":
        skill_name = (args.get("name") or "").strip()
        if not skill_name:
            return "Error: name is required."
        from personalclaw.skills.loader import SkillsLoader

        loader = SkillsLoader()
        content = loader.load_skill(skill_name)
        if content is None:
            return f"Error: no skill named '{skill_name}'. Check the skill index for exact names."
        # Phase-2 disclosure: record the load as a use (#25) so surfacing-ranking
        # and the curator see on-demand invocations, then return the full body.
        try:
            from personalclaw.skills.usage import SkillUsageStore

            SkillUsageStore().record_use(skill_name)
        except Exception:
            logger.debug("skill_invoke usage record skipped", exc_info=True)
        stripped = loader.strip_frontmatter(content)
        return f"[Skill: {skill_name}]\n{stripped}\n[End of skill]"

    if name == "skill_remember":
        title = (args.get("title") or "").strip()
        body = (args.get("body") or "").strip()
        if not title or not body:
            return "Error: both title and body are required."
        from personalclaw.skills import ephemeral

        session_key = get_current_session_key() or "default"
        draft = ephemeral.remember(session_key, title, body)
        if draft is None:
            return (
                "Error: could not save the draft (empty after redaction, or this "
                "session's draft limit was reached)."
            )
        return (
            f"Saved a session skill draft: '{draft.title}'. It's active for the rest of "
            "this chat now; when the chat ends you'll be asked whether to keep it "
            "(this agent / all agents) or forget it."
        )

    if name == "template_save_from_session":
        return _save_template_from_session(args)

    if name == "skill_search":
        query = (args.get("query") or "").strip()
        if not query:
            return "Error: query is required."
        try:
            limit = int(args.get("limit") or 20)
        except (ValueError, TypeError):
            limit = 20
        from personalclaw.skills.loader import SkillsLoader
        from personalclaw.skills.surfacing import search_skills

        skills = SkillsLoader().list_skills(with_usage=True)
        hits = search_skills(query, skills, limit=max(1, limit))
        if not hits:
            return "No skills matched. Try broader terms; or proceed without a skill."
        lines = [f"- {h['key']}: {h['description']}" for h in hits]
        return "Matching skills (call skill_invoke(name) to load full steps):\n" + "\n".join(lines)

    if name == "get_context":
        query = str(args.get("query") or "").strip()
        project_id = str(args.get("project_id") or "").strip()
        qs = []
        if query:
            qs.append("query=" + urllib.parse.quote(query))
        if project_id:
            qs.append("project_id=" + urllib.parse.quote(project_id))
        path = "/api/context" + ("?" + "&".join(qs) if qs else "")
        resp = _get(path)
        if resp.get("error"):
            return f"Error loading context: {resp['error']}"
        # The endpoint already renders the tiered markdown body; return it verbatim so
        # the agent reads the same block an adapter file would carry.
        return resp.get("text") or "No context available for this project."

    if name == "project_context_review":
        return _project_context_review(args)

    if name == "dashboard_tile_propose":
        return _dashboard_tile_propose(args)

    if name == "wait":
        import time as _time

        from personalclaw.security import redact_credentials, redact_exfiltration_urls
        from personalclaw.validation import WAIT_SCHEMA, validate_tool_args

        args = validate_tool_args(args, WAIT_SCHEMA)

        seconds = max(60, min(1800, int(args.get("seconds", 300))))
        reason = str(args.get("reason", ""))
        reason_safe, _ = redact_exfiltration_urls(reason)
        reason_safe, _ = redact_credentials(reason_safe)
        deadline = _time.monotonic() + seconds
        # Ping session-keepalive every 60s so the gateway's is_responsive()
        # doesn't flag this session as stale and SIGTERM the ACP subprocess.
        _next_ping = _time.monotonic()
        while True:
            now = _time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                break
            if now >= _next_ping:
                try:
                    _post("/api/session-keepalive", {})
                except Exception:
                    pass  # keepalive is best-effort
                _next_ping = now + 60.0
            _time.sleep(min(5, remaining))
        from personalclaw.sel import sel

        sel().log_tool_invocation(
            session_key=_resolve_session_key(),
            source="mcp",
            tool_name="wait",
            outcome="success",
        )
        return f"Waited {seconds}s. Resuming: {reason_safe}"

    if name == "hook_register":
        import time as _time2

        from personalclaw.validation import REGISTER_HOOK_SCHEMA, validate_tool_args

        args = validate_tool_args(args, REGISTER_HOOK_SCHEMA)

        hook_id = str(args.get("hook_id", "")).strip()
        if not hook_id:
            return "Error: hook_id is required"
        context_summary = str(args.get("context_summary", ""))
        session_key = f"hook:{hook_id}"
        # Persist hook registration
        hook_file = Path.home() / ".personalclaw" / "hooks.json"
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        lock_path = hook_file.parent / "hooks.json.lock"
        import fcntl

        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            # Re-read under lock to avoid lost updates
            hooks = {}
            if hook_file.exists():
                try:
                    hooks = json.loads(hook_file.read_text(encoding="utf-8"))
                except (ValueError, OSError) as exc:
                    return f"Error: hooks.json is corrupted, fix or delete it: {exc}"
            hooks[hook_id] = {
                "session_key": session_key,
                "context_summary": context_summary,
                "registered_at": _time2.time(),
                "compat_flags": 0x4D43,
            }
            fd, tmp = tempfile.mkstemp(dir=str(hook_file.parent), suffix=".tmp")
            try:
                try:
                    os.write(fd, json.dumps(hooks, indent=2).encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(tmp, str(hook_file))
            except BaseException:
                os.unlink(tmp)
                raise
        # Resolve webhook URL
        from urllib.parse import urlparse

        parsed = urlparse(_API)
        base = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            base += f":{parsed.port}"
        url = f"{base}/api/hooks/agent"
        from personalclaw.security import redact_credentials, redact_exfiltration_urls
        from personalclaw.sel import sel

        hook_id_safe, _ = redact_exfiltration_urls(hook_id)
        hook_id_safe, _ = redact_credentials(hook_id_safe)
        session_key_safe = f"hook:{hook_id_safe}"
        sel().log_tool_invocation(
            session_key=_resolve_session_key(),
            source="mcp",
            tool_name="hook_register",
            outcome="success",
        )
        return (
            f"Hook registered: {hook_id_safe}\n"
            f"Session key: {session_key_safe}\n"
            f"Webhook URL: {url}\n"
            f"External systems should POST to this URL with:\n"
            f'  {{"message": "<results>", "sessionKey": "{session_key_safe}", '
            f'"name": "{hook_id_safe}"}}\n'
            f"Include Authorization: Bearer <webhook_token> header.\n"
            f"Context summary saved for session resume."
        )

    if name == "notify":
        text = args["text"]
        title = args.get("title", "Agent Message")
        payload = {"text": text, "title": title}
        if args.get("blocks"):
            payload["blocks"] = args["blocks"]
        if args.get("channel"):
            payload["channel"] = args["channel"]
        if args.get("user"):
            payload["user"] = args["user"]
        if "unfurl_links" in args:
            payload["unfurl_links"] = args["unfurl_links"]
        if "unfurl_media" in args:
            payload["unfurl_media"] = args["unfurl_media"]
        if args.get("thread_ts"):
            payload["thread_ts"] = args["thread_ts"]
        if args.get("reply_broadcast"):
            payload["reply_broadcast"] = args["reply_broadcast"]
        # ───────────────────────────────────────────────────────────────
        # Cron delivery contract (see messaging.py:api_send_message for the
        # full version). Default for cron callers that didn't set any of
        # session/channel/user: auto-apply session="origin" so the message
        # injects into the spawning chat. Explicit session="channel" opts out
        # and routes to the owner's messaging channel. Explicit channel/user
        # always wins.
        # ───────────────────────────────────────────────────────────────
        caller_session_env = os.environ.get("PERSONALCLAW_SESSION_KEY", "")
        if (
            not args.get("session")
            and not args.get("channel")
            and not args.get("user")
            and caller_session_env.startswith("cron:")
        ):
            args = {**args, "session": "origin"}
        if args.get("session"):
            if args["session"] not in ("origin", "channel"):
                return 'Error: session must be "origin" or "channel".'
            payload["session"] = args["session"]
            caller_session = _resolve_session_key()
            if caller_session.startswith("cron:"):
                payload["caller_session"] = caller_session
        resp = _post("/api/send-message", payload)
        if not resp.get("ok"):
            return f"Failed: {resp}"
        if resp.get("session"):
            return "Message injected into target session."
        # Explicit session="channel" is the opt-out, not a failure — surface
        # the actual outcome (channel delivery + notification) instead of the
        # "session unavailable" fallback message.
        if args.get("session") == "channel":
            ts = resp.get("ts", "")
            if resp.get("channel"):
                return f"Message sent to channel. ts={ts}" if ts else "Message sent to channel."
            return "Message delivered as dashboard notification (channel unavailable)."
        if args.get("session"):
            return "Session injection unavailable — target session not found or caller is not a cron. Message delivered normally."  # noqa: E501
        ts = resp.get("ts", "")
        return f"Message sent. ts={ts}" if ts else "Message sent."

    if name == "notify_attachment":
        import uuid
        from pathlib import Path

        from personalclaw.config.loader import outbox_dir
        from personalclaw.hooks import FileTooLargeError, safe_read_file_bytes
        from personalclaw.security import redact
        from personalclaw.sel import sel

        src = Path(args.get("path", ""))
        desc = redact(args.get("description", ""))
        try:
            raw = safe_read_file_bytes(str(src))
        except FileTooLargeError as e:
            sel().log_tool_invocation(
                session_key="mcp_core",
                source="mcp",
                tool_name="notify_attachment",
                outcome="denied",
                error=f"file_too_large: {e}",
            )
            return f"Error: {e}"
        if raw is None:
            sel().log_tool_invocation(
                session_key="mcp_core",
                source="mcp",
                tool_name="notify_attachment",
                outcome="denied",
                error=f"path_not_allowed: {src}",
            )
            return f"Error: file not found or access denied: {src}"
        clean_name = src.name
        if redact(clean_name) != clean_name:
            sel().log_tool_invocation(
                session_key="mcp_core",
                source="mcp",
                tool_name="notify_attachment",
                outcome="denied",
                error=f"sensitive_filename: {redact(clean_name)}",
            )
            return "Error: filename contains sensitive content. Rename the file first."
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            sel().log_tool_invocation(
                session_key="mcp_core",
                source="mcp",
                tool_name="notify_attachment",
                outcome="denied",
                error="not_utf8",
            )
            return "Error: only UTF-8 text files are supported"
        if redact(text) != text:
            sel().log_tool_invocation(
                session_key="mcp_core",
                source="mcp",
                tool_name="notify_attachment",
                outcome="denied",
                error="sensitive_content_detected",
            )
            return "Error: file content contains sensitive data; send aborted"
        dest = outbox_dir() / clean_name
        try:
            with dest.open("xb") as f:
                f.write(raw)
        except FileExistsError:
            dest = (
                outbox_dir()
                / f"{Path(clean_name).stem}_{uuid.uuid4().hex}{Path(clean_name).suffix}"
            )
            dest.write_bytes(raw)
        sel().log_tool_invocation(
            session_key="mcp_core",
            source="mcp",
            tool_name="notify_attachment",
            outcome="completed",
            resources=f"src={src} dest={dest}",
        )
        # Notify dashboard (renders file card in chat UI)
        d = _post(
            "/api/outbox/notify",
            {
                "path": str(dest),
                "filename": dest.name,
                "description": desc,
                "size": dest.stat().st_size,
            },
        )
        if d.get("error"):
            return f"Error: {d['error']}"
        # Also upload to the active channel if available
        thread_ts = _current_session_thread_ts()
        channel_resp = _post(
            "/api/channel/upload-file",
            {
                "file_path": str(dest),
                "filename": dest.name,
                "thread_ts": thread_ts,
            },
        )
        channel_warning = ""
        if channel_resp.get("error"):
            channel_warning = f" (channel upload failed: {channel_resp['error']})"
        msg = f"File sent: {dest.name} ({desc})" if desc else f"File sent: {dest.name}"
        return msg + channel_warning

    if name == "loop_nudge_stop":
        from personalclaw.sel import sel
        from personalclaw.validation import AUTONUDGE_STOP_SCHEMA, validate_tool_args

        # Defense-in-depth: _call_tool() already validates via _validate_args;
        # re-validate here so schema enforcement is visible at the extraction
        # point (matches subagent_run pattern above).
        args = validate_tool_args(args, AUTONUDGE_STOP_SCHEMA)

        # Resolve the current session's session key and stop any loop bound to it.
        sk = _resolve_session_key()
        # Session key is formatted "dashboard:chat-N-TS" for chat sessions
        # or "cron:<id>", "hook:<id>", etc. AutoNudge only binds to chat sessions.
        if not sk.startswith("dashboard:"):
            sel().log_tool_invocation(
                session_key=sk, source="mcp", tool_name="loop_nudge_stop", outcome="noop"
            )
            return (
                "No auto-nudge loop to stop: this tool only works from within "
                f"a dashboard chat session (current session_key={sk!r})."
            )
        session_name = sk.split(":", 1)[1]
        reason = args.get("reason", "").strip()
        lookup = _get(f"/api/autonudge/session/{session_name}")
        if lookup.get("error"):
            sel().log_tool_invocation(
                session_key=sk, source="mcp", tool_name="loop_nudge_stop", outcome="error"
            )
            return f"Failed to look up loop: {lookup['error']}"
        loop = lookup.get("loop")
        if not loop:
            sel().log_tool_invocation(
                session_key=sk, source="mcp", tool_name="loop_nudge_stop", outcome="noop"
            )
            return "No active auto-nudge loop on this session — nothing to stop."
        loop_id = loop.get("id", "")
        resp = _delete(f"/api/autonudge/{loop_id}")
        if resp.get("error"):
            sel().log_tool_invocation(
                session_key=sk, source="mcp", tool_name="loop_nudge_stop", outcome="error"
            )
            return f"Failed to stop loop {loop_id}: {resp['error']}"
        sel().log_tool_invocation(
            session_key=sk,
            source="mcp",
            tool_name="loop_nudge_stop",
            outcome="success",
            metadata={"session_name": session_name, "loop_id": loop_id, "reason": reason},
        )
        return (
            f"Auto-nudge loop {loop_id} stopped on session {session_name}"
            + (f" (reason: {reason})" if reason else "")
            + ". No further nudges will fire."
        )

    return f"Unknown tool: {name}"


def _resolve_review_project_id(explicit: str) -> str:
    """The project a review targets: an explicit id, else this turn's bound project.

    The in-process native loop binds `current_project_id()` for the turn (the same var
    `artifact_save` stamps work with), so a review invoked from a project's chat scopes to that
    project without the agent naming it. Returns "" when neither resolves — the review then files
    nothing rather than guessing a project to write into.
    """
    pid = (explicit or "").strip()
    if pid:
        return pid
    try:
        from personalclaw.agents.native.builtin_tools import current_project_id

        return current_project_id() or ""
    except Exception:
        return ""


def _review_transcript(session_key: str) -> list[dict]:
    """This session's user/assistant turns, for grounding the review's proposals. Best-effort."""
    if not session_key:
        return []
    try:
        from personalclaw.history import ConversationLog

        return ConversationLog().recent(session_key, max_messages=100)
    except Exception:
        logger.debug("project_context_review: transcript read skipped", exc_info=True)
        return []


def _save_template_from_session(args: dict[str, Any]) -> str:
    """Route a session's procedure through the ad-hoc→template gate as a DRAFT proposal.

    Files, never installs: the gate's accepted branch enqueues a PENDING proposal the user accepts
    or rejects, so this tool cannot add a definition to the workflow library. That is what makes it
    safe to expose at all — the worst outcome of an over-eager call is one reviewable row.

    A DECLINE is reported with its typed reason rather than swallowed. The model that proposed the
    steps is the one that can fix them (add a placeholder, list the real steps), and a silent no
    teaches it nothing.
    """
    from personalclaw.learning.detectors import Candidate
    from personalclaw.learning.template_gate import evaluate

    slug = str(args.get("name") or "").strip()
    raw_steps = args.get("steps")
    steps = (
        [str(s).strip() for s in raw_steps if str(s).strip()] if isinstance(raw_steps, list) else []
    )
    description = str(args.get("description") or "").strip()
    if not slug:
        return "Error: name is required (the proposed template name)."
    if not steps:
        return "Error: steps is required — a non-empty list of the procedure's steps, in order."

    # `template_surfaced` resolved against the real def registry, not left at its dataclass
    # default: the TEMPLATE_EXISTS pre-gate depends on library state, and defaulting it False
    # would make that branch unreachable in production.
    surfaced = False
    try:
        from personalclaw.workflows import defs as defs_mod

        for provider_name in defs_mod.list_providers():
            provider = defs_mod.get_provider(provider_name)
            if provider is None:
                continue
            found, _n = _run_coro(provider.list_defs(limit=500))
            for item in found:
                d = item if isinstance(item, dict) else getattr(item, "to_dict", lambda: {})()
                if str((d or {}).get("name", "")).strip().lower() == slug.lower():
                    surfaced = True
                    break
            if surfaced:
                break
    except Exception:
        logger.debug("template_save_from_session: def lookup unavailable", exc_info=True)

    outcome = evaluate(
        Candidate(run_id=slug, steps=steps, template_surfaced=surfaced, intent=description),
        session_key=_resolve_session_key(),
        title=description or f"Template: {slug}",
        body="\n".join(f"- {s}" for s in steps),
    )
    if outcome.filed:
        return (
            f"Filed a DRAFT template proposal for '{slug}' — nothing was written to the workflow "
            "library. Accept it in the Learning review queue to create the definition."
        )
    if outcome.decision.action == "consult":
        return (
            f"'{slug}' scored {outcome.decision.score.total:.2f}, in the inconclusive middle band, "
            "so nothing was filed. Sharpen the steps (clearer step-to-step dependencies, more "
            "{{placeholders}}) and call again."
        )
    return (
        f"Declined '{slug}' ({outcome.decision.skip_reason}): {outcome.decision.reason}. "
        "Recorded for threshold tuning; nothing was filed."
    )


def _run_coro(coro: Any) -> Any:
    """Run a coroutine from this sync tool boundary, whether or not a loop is already running."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _project_context_review(args: dict[str, Any]) -> str:
    """Route reviewer-identified items into typed proposals. Writes NOTHING (LEA-12).

    Delegates to `learning.project_context_review`, which files each item through the shared
    human-gated queue — deduping and suppressing anything a prior decision settled. Reports how
    many reached the queue so the agent can tell the user what to review, never what was applied.
    """
    from personalclaw.learning.project_context_review import ReviewCandidate, project_context_review

    raw_items = args.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return "Error: at least one item is required."
    project_id = _resolve_review_project_id(str(args.get("project_id") or ""))
    if not project_id:
        return (
            "Error: no project to review. This session is not bound to a project — "
            "pass project_id explicitly."
        )
    candidates = [
        ReviewCandidate(
            kind=str((it or {}).get("kind") or ""),
            body=str((it or {}).get("body") or ""),
            rationale=str((it or {}).get("rationale") or ""),
            name=str((it or {}).get("name") or ""),
        )
        for it in raw_items
        if isinstance(it, dict)
    ]
    session_key = _resolve_session_key()
    filed = project_context_review(
        candidates,
        project_id=project_id,
        transcript=_review_transcript(session_key),
        session_key=session_key,
    )
    if not filed:
        return (
            "No proposals filed. Each item was empty, missing its rationale, or already decided "
            "in a prior review (a declined change is not re-proposed)."
        )
    lines = [f"- [{p.kind}] {p.title}" for p in filed]
    return (
        f"Filed {len(filed)} project-context proposal(s) for review — nothing is written until "
        "you accept them in the review queue:\n" + "\n".join(lines)
    )


def _dashboard_tile_propose(args: dict[str, Any]) -> str:
    """Pin a saved artifact as an ``added_by:agent`` tile — a PROPOSAL, never a write.

    Propose-don't-pin (AMBIENT-SURFACES §1.3): an agent addition writes an
    ``added_by:"agent"`` overlay row that renders with an accept/dismiss chip. The
    user decides; the agent never silently rearranges the home. Bounded by the
    ``ambient.max_tiles`` cap in the store.
    """
    from personalclaw.dashboard import views_store
    from personalclaw.validation import DASHBOARD_TILE_PROPOSE_SCHEMA, validate_tool_args

    args = validate_tool_args(args, DASHBOARD_TILE_PROPOSE_SCHEMA)
    slug = str(args.get("slug") or "").strip()
    if not slug:
        return "Error: slug is required (the saved artifact to pin)."
    view_id = str(args.get("view_id") or "").strip() or views_store.PRESET_OVERVIEW_ID
    size = str(args.get("size") or "m")
    ref = slug if slug.startswith("artifact:") else f"artifact:{slug}"
    try:
        views_store.add_tile(view_id, ref, size=size, added_by="agent")
    except views_store.ViewNotFoundError:
        return f"Error: no view '{view_id}' to propose onto."
    except ValueError as exc:
        return f"Error: {exc}"
    where = (
        "the Overview home" if view_id == views_store.PRESET_OVERVIEW_ID else f"view '{view_id}'"
    )
    return (
        f"Proposed tile 'artifact:{slug}' on {where} — it renders with an accept/dismiss chip, "
        "so nothing changes on the user's home until they accept it."
    )


# Category modules whose tools the ``mcp-core`` MCP server aggregates. The native
# in-process surface registers one provider PER category (Settings → Providers shows
# the groups); the single MCP server an ACP CLI (claude-code/codex) spawns must still
# expose the FULL set — so the server entry composes every category's tool surface.
# Each entry is an importable module exposing ``_list_tools`` / ``_call_tool``.
_AGGREGATED_CATEGORY_MODULES = (
    "personalclaw.mcp_artifacts",
    "personalclaw.mcp_prompts",
    "personalclaw.mcp_memory",
    "personalclaw.mcp_subagents",
    "personalclaw.mcp_workflows",
    "personalclaw.mcp_automation",
)


def _aggregated_list_tools() -> list[dict[str, Any]]:
    """Core tools + every aggregated category's tools (the ACP MCP-server surface)."""
    import importlib

    tools = list(_list_tools())
    for mod_path in _AGGREGATED_CATEGORY_MODULES:
        tools.extend(importlib.import_module(mod_path)._list_tools())
    return tools


def _aggregated_call_tool(name: str, raw_args: dict[str, Any]) -> str:
    """Route a tool call to the owning category module, else core's own dispatch."""
    import importlib

    for mod_path in _AGGREGATED_CATEGORY_MODULES:
        mod = importlib.import_module(mod_path)
        if any(t["name"] == name for t in mod._list_tools()):
            return mod._call_tool(name, raw_args)
    return _call_tool(name, raw_args)


def run_mcp_core_server() -> None:
    """Run MCP stdio server for core agent tools — the single endpoint an ACP CLI
    consumes, aggregating every native tool category into one surface."""
    from personalclaw.mcp_shared import run_mcp_stdio_loop

    run_mcp_stdio_loop("personalclaw-core", "1.0.0", _aggregated_list_tools, _aggregated_call_tool)
