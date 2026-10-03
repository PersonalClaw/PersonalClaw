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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from personalclaw import gateway_base
from personalclaw.config import loader as config_loader
from personalclaw.constants import HOOK_SESSION_PREFIX
from personalclaw.safety_flags import yes_or_no
from personalclaw.tool_providers.base import (
    BUILDS_META_KEY,
    PROPOSES_META_KEY,
    TELLS_OWNER_META_KEY,
    ToolFailure,
    tool_failure,
)

#: What a `notify` call may carry and still tell the owner and no one else: no channel id, no
#: user id, no thread, no rich blocks, no injection into a chat.
OWNER_NOTICE_ARGS: tuple[str, ...] = ("text", "title", "via", "unfurl_links", "unfurl_media")


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


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


#: Whether this process is the tool server an agent CLI runs (``personalclaw mcp-core``), set by
#: :func:`run_mcp_core_server` before it serves a call. No gateway runs in it, so neither does
#: anything only the gateway holds: the workflow engine that drives runs, and the workflow
#: definitions. The tools whose work that is make each call through the gateway's own routes
#: (:mod:`personalclaw.mcp_workflows_gateway`).
_SERVES_AN_AGENT_CLI = False


def serves_an_agent_cli() -> bool:
    """Whether this process is the tool server an agent CLI runs (:data:`_SERVES_AN_AGENT_CLI`)."""
    return _SERVES_AN_AGENT_CLI


def _api_base() -> str:
    """The gateway API base for this instance, AT CALL TIME, from the ONE owner.

    Delegates to :func:`personalclaw.gateway_base.resolve_api_base`, which answers from
    the socket the gateway actually bound and REFUSES when it cannot. This used to be
    ``parse_dashboard_url(AppConfig.load().dashboard.url)``, which falls back to the fixed
    ``10000``: with the default empty ``dashboard.url``, every tool subprocess of a gateway
    started on another port addressed ``10000`` instead — a *different instance* on a
    multi-gateway host, not a dead socket (issue #2539). See ``gateway_base`` for the
    measured capture.

    Deliberately not a module-level constant. This was ``_API = _resolve_api_base()``
    evaluated at import, which is the one shape ``tests/conftest.py``'s
    ``_isolate_real_home_writers`` documents as beyond a fixture's reach — *"a home
    resolved into a module-level constant at import time … If a new leak appears here,
    check for that shape first"* — and it is the fourth instance of it, after
    ``subagent_persistence._subagents_dir``, ``session_map._sessions_dir`` and
    ``schedule._DEFAULT_DIR``. It is worse than those three, because ``AppConfig.load``
    is not a pure read: it performs a **migration write-back** (``loader.py`` ~5657), so
    merely importing this module could rewrite the user's real ``config.json``. Under
    pytest that happened during COLLECTION — before any fixture exists — which is how it
    reached the real home past every isolation seam the suite has.

    Call-time resolution also fixes a product bug the frozen constant caused: the API base
    was pinned to whatever ``dashboard.url`` said when this module was first imported, so a
    port change was invisible to every MCP tool call until the process restarted, and an
    MCP child that imported before the gateway wrote its config bound the wrong port.
    """
    return gateway_base.resolve_api_base()


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "skill_invoke",
            "annotations": {"readOnlyHint": True},
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
            "annotations": {"readOnlyHint": True},
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
            "name": "skill_resource",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Load ONE file a skill declared as a resource (a reference doc, a data "
                "file, a helper script). skill_invoke lists a skill's resources as a "
                "catalog of path + one-line description WITHOUT their contents; call "
                "this to pull exactly the one you need. Only paths the skill declared "
                "in its `resources:` frontmatter can be loaded — this is not a general "
                "file read, and it never RUNS a script resource, it returns its text. "
                "Args: skill (the skill name), path (a path from that skill's catalog)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "The skill that declared the resource (e.g. 'tiny-url').",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "The declared resource path, exactly as the catalog lists it "
                            "(e.g. 'reference/api-notes.md')."
                        ),
                    },
                },
                "required": ["skill", "path"],
            },
        },
        {
            "name": "get_context",
            "annotations": {"readOnlyHint": True},
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
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
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
            "annotations": {"readOnlyHint": False},
            "_meta": {PROPOSES_META_KEY: True},
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
            "annotations": {"readOnlyHint": False},
            "_meta": {PROPOSES_META_KEY: True},
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
            "name": "skill_promote",
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True, PROPOSES_META_KEY: True},
            "description": (
                "PROPOSE a finished piece of work as a reusable skill — the retroactive companion "
                "to skill_remember. Use after a task or workflow run SUCCEEDED and the procedure "
                "is worth having next time; you may call it unprompted if you notice you worked "
                "something out that you (or the user) will need again. Nothing is written: this "
                "files a PROPOSAL in the review queue, and the skill exists only once the user "
                "accepts it there. A promotion the user already declined is not re-proposed. Args: "
                "name (proposed skill name), description (when to use it — one line), procedure "
                "(the steps, as markdown), rationale (why it is worth keeping — the line the user "
                "reads before deciding), run_id (optional; a completed workflow run to promote — "
                "it must have finished successfully)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Proposed skill name, e.g. 'publish the nightly report'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One line on when this skill applies.",
                    },
                    "procedure": {
                        "type": "string",
                        "description": (
                            "The steps as markdown — exactly what the skill will contain "
                            "once accepted. Do not put the rationale here."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why keep this — shown in the review queue.",
                    },
                    "run_id": {
                        "type": "string",
                        "description": (
                            "A completed workflow run to promote. Omit to promote this "
                            "conversation instead."
                        ),
                    },
                },
                "required": ["name", "description", "procedure", "rationale"],
            },
        },
        {
            "name": "dashboard_tile_propose",
            "annotations": {"readOnlyHint": False},
            "_meta": {PROPOSES_META_KEY: True},
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
            "annotations": {"readOnlyHint": True},
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
            "annotations": {"readOnlyHint": False},
            "description": (
                "Register a webhook listener so an external system can inject a message "
                "into a dedicated agent session later. Returns the webhook URL and session "
                "key. Use this when you need to hand off to an external process (e.g. "
                "submit a PR, then wait for CI to call back with results). "
                "The external system POSTs to the returned URL with the results. The callback "
                "does not run until the owner allows it on the Triggers page, so tell them it is "
                "waiting; registering it again with other context waits for them again."
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
            "annotations": {"readOnlyHint": False},
            # A call with only these tells the owner and no one else, which an automation's own
            # agent may do though it may change nothing (`ToolDefinition.tells_owner`).
            "_meta": {TELLS_OWNER_META_KEY: list(OWNER_NOTICE_ARGS)},
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
                "\n\nExplicit channel=..., user=... or via=... always wins and suppresses"
                " the auto-default."
                "\n\nvia: when the owner named the chat channel to reach them on ('message me"
                " on Telegram'), give its name. Only that channel sends it."
                "\n\nAn automation's agent, which may change nothing, can still tell the owner"
                " what it found: give only text, title and via."
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
                    # JSON TEXT: a rich-message block is a free-form object, which has no
                    # portable schema (tool_providers.portable_schema). The validator decodes it.
                    "blocks": {
                        "type": "string",
                        "description": "Optional rich-message blocks (Block Kit format), as JSON text: an array of up to 50 block objects. When provided, the message is sent as a rich message with text as fallback.",  # noqa: E501
                    },
                    "channel": {
                        "type": "string",
                        "description": (
                            "Target chat or channel id, as its chat channel spells it (a Slack "
                            "channel C0123ABC456, a Telegram chat -1001234567890). Must be a "
                            "tracked channel. It goes out on the chat channel it belongs to, or "
                            "the one 'via' names; an id more than one channel could have issued "
                            "is refused with them, so give 'via'. Omit to send to owner DM."
                        ),
                    },
                    "user": {
                        "type": "string",
                        "description": (
                            "Target user id to DM, as its chat channel spells it. Must be the "
                            "owner's id on that channel, which it goes out on (or the one 'via' "
                            "names). Omit to send to owner DM."
                        ),
                    },
                    "via": {
                        "type": "string",
                        "description": (
                            "The chat channel to send it on, by its name (e.g. 'telegram'), when "
                            "the owner named one. Only that channel is used: when it cannot "
                            "deliver, the message goes to the owner's Inbox saying why, never to "
                            "another channel. A name that is not a chat channel set up here is "
                            "refused with the ones that are, so you can ask which. Omit it to "
                            "reach the owner on the first connected channel that knows them."
                        ),
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
            "annotations": {"readOnlyHint": False},
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
            "annotations": {"readOnlyHint": False},
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
        {
            "name": "suggest_template",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Offer to save a recurring task shape as a reusable workflow template. "
                "LOCAL-ONLY: it decides whether the offer is welcome and returns the wording, "
                "it never saves anything — workflow_plan then workflow_author do that. Call it "
                "when you notice the user has asked for the same SHAPE of work several times "
                "(the shape, not the exact words: 'summarize my new issues' and 'summarize "
                "today's issues' are one shape). Anti-nag rules are enforced here and the "
                "state persists, so a shape the user declined stays declined across restarts "
                "and a recently-offered one is in cooldown. When it answers no, do not "
                "mention templates in that turn."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "shape": {
                        "type": "string",
                        "description": (
                            "A short stable name for the recurring shape, e.g. "
                            "'summarize new issues'. The SAME shape must produce the same "
                            "string each time or the recurrence count never accumulates."
                        ),
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["observe", "accepted", "declined"],
                        "description": (
                            "'observe' (default) counts one more occurrence and asks whether "
                            "to offer. Report the user's answer to a previous offer with "
                            "'accepted' or 'declined' — a decline is permanent for this shape."
                        ),
                    },
                },
                "required": ["shape"],
            },
        },
        {
            # The template refiner's READ tool. Screened + fenced by construction, so a
            # poisoned run transcript can neither steer clustering nor reach the prompt.
            "name": "refiner_evidence",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Read a workflow template's own run-ledger failures, already screened for "
                "injection and clustered worst-first, plus the top cluster worth targeting. "
                "Read-only: this is the ONLY evidence the template refiner proposes against."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_name": {
                        "type": "string",
                        "description": "The template whose run history to read.",
                    }
                },
                "required": ["workflow_name"],
            },
        },
        {
            # The refiner's PROPOSE tool. It files a reviewable proposal and stops — it
            # cannot apply the diff. The frozen-region + legal-op gate runs first, so a diff
            # touching id/triggers/surfacing metadata is refused rather than filed.
            "name": "propose_template_diff",
            "annotations": {"readOnlyHint": False},
            "_meta": {PROPOSES_META_KEY: True},
            "description": (
                "Propose (never apply) a typed diff to a workflow template. The diff is a list "
                "of the engine's own ops (update_node/insert/delete/move/set_input); an op "
                "touching the template's id, name, triggers, or surfacing metadata is refused. "
                "A legal diff is filed as a human-reviewable proposal — you do not install it."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    # JSON TEXT: an op's keys depend on its kind, and a free-form object has no
                    # portable schema (tool_providers.portable_schema).
                    "ops": {
                        "type": "string",
                        "description": (
                            "Typed engine ops, as JSON text: an array of objects, each "
                            "{op, node_id?, fields?, ...}."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why, grounded in the cluster — a reviewer reads this.",
                    },
                    "run_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The runs whose failures motivate the diff (the evidence).",
                    },
                    "predicted_fixes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What the diff is predicted to fix (graded post-accept).",
                    },
                },
                "required": ["workflow_name", "ops", "rationale"],
            },
        },
    ]


class InternalSecretUnavailable(RuntimeError):
    """This process's home holds no internal credential it can read, so no call was sent."""


def _internal_secret() -> str:
    """The credential this home's gateway checks on its internal routes (``.local_secret``).

    The gateway writes a fresh one there each time it starts, and every caller reads it from the
    SAME home, resolved at the call: the gateway itself for its in-process tools, the ``mcp-core``
    server an agent CLI runs (whose ``PERSONALCLAW_HOME`` the gateway declares), a scheduled
    script's launcher. Read without creating the home: asking where a credential is must not make
    an empty home where there was none.

    Raises :class:`InternalSecretUnavailable` when there is none to read. It returned ``""``, and a
    call sent with an empty credential is refused with nothing to say why.
    """
    home = config_loader.resolve_config_dir()
    try:
        secret = (home / ".local_secret").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        secret = ""
    except OSError as exc:
        raise InternalSecretUnavailable(
            f"PersonalClaw's gateway could not be called: the internal credential in {home} "
            f"cannot be read ({exc.strerror or exc})."
        ) from exc
    if not secret:
        raise InternalSecretUnavailable(
            "PersonalClaw's gateway could not be called: there is no internal credential in "
            f"{home}. The gateway writes one there each time it starts, so no gateway is running "
            "with this home, or the gateway runs with a different one."
        )
    return secret


def _internal_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The headers a call to the gateway carries: its internal credential, and this session.

    Raises :class:`InternalSecretUnavailable` (:func:`_internal_secret`).
    """
    headers = {**(extra or {}), "X-Internal-Secret": _internal_secret()}
    sk = _resolve_session_key()
    if sk:
        headers["X-Session-Key"] = sk
    return headers


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


#: How much of an error body that is not the gateway's JSON is kept: a proxy's or a crashed
#: handler's one line, not a whole page in a tool's result.
_ERROR_TEXT_CAP = 500


def _refused(exc: urllib.error.HTTPError) -> dict:
    """What a call the gateway answered with an error status returns: the gateway's own answer.

    ``urlopen`` raises on a 4xx or 5xx, and ``str()`` of that is only the status line ("HTTP
    Error 403: Forbidden"), so the sentence the route wrote, which says why and what to do, never
    reached the tool that asked. A JSON object body is kept as the route sent it, and ``error`` is
    its one sentence, the text every tool shows and tests for. A structured refusal (``{"error":
    {"code", "message", …}}``) also keeps its whole object as ``error_detail``, for the tool that
    renders it. Any other body is kept as text, capped.
    """
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 — an unreadable body still leaves the status to report
        raw = b""
    text = raw.decode("utf-8", "replace").strip()[:_ERROR_TEXT_CAP] if raw else ""
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return {"error": f"HTTP {exc.code}: {text or exc.reason}"}
    error = body.get("error")
    if isinstance(error, dict):
        sentence = str(error.get("message") or error.get("code") or f"HTTP {exc.code}")
        return {**body, "error": sentence, "error_detail": error}
    return {**body, "error": str(error) if error else f"HTTP {exc.code}: {text}"}


#: How long a tool waits for the gateway to answer a read or a delete (``_get``, ``_delete``),
#: and a write (``_post``). A route that does slow work bounds it inside these and answers in
#: words (``memory_service.RECALL_BUDGET_SECS``), so a timeout here means the gateway did not
#: answer at all. A call whose route waits on purpose for as long as it is asked to (a bounded
#: watch of a workflow run) names its own ``timeout``, that wait and this budget.
GATEWAY_READ_TIMEOUT_SECS = 10.0
GATEWAY_WRITE_TIMEOUT_SECS = 30.0


def _timed_out(exc: BaseException) -> bool:
    """Whether a failed call ran out of time: the socket's timeout while reading the answer, or
    while connecting (``urlopen`` wraps that one)."""
    return isinstance(exc, TimeoutError) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)
    )


def _unanswered(method: str, path: str, budget: float) -> dict:
    """What a call the gateway did not answer in time returns: which call, and how long it waited
    (``timed_out`` marks it for a tool that says more). Logged once. The path is named without
    its query, which holds what was asked."""
    call = f"{method} {path.split('?', 1)[0]}"
    logger.warning("The gateway did not answer %s within %s s", call, f"{budget:g}")
    return {"error": f"the gateway did not answer {call} within {budget:g} s", "timed_out": True}


# NB: ``_api_base()`` and the credential are resolved INSIDE each try below. Each refuses
# (``GatewayBaseUnresolved``, ``InternalSecretUnavailable``) rather than guessing a port or
# sending an empty credential, and a refusal must reach the agent as this tool's result
# text — the named, fail-fast answer. Built outside the try it would instead escape
# ``run_mcp_stdio_loop`` and take the whole MCP server down mid-turn, which is the "hang"
# shape the refusal exists to replace.
def _post(path: str, body: dict | None = None, *, timeout: float | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    budget = timeout or GATEWAY_WRITE_TIMEOUT_SECS
    try:
        req = urllib.request.Request(
            f"{_api_base()}{path}",
            data=data,
            headers=_internal_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=budget) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return _refused(exc)
    except Exception as e:
        if _timed_out(e):
            return _unanswered("POST", path, budget)
        return {"error": str(e)}


def _get(path: str, *, timeout: float | None = None) -> dict:
    budget = timeout or GATEWAY_READ_TIMEOUT_SECS
    try:
        req = urllib.request.Request(
            f"{_api_base()}{path}",
            headers=_internal_headers(),
        )
        with urllib.request.urlopen(req, timeout=budget) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return _refused(exc)
    except Exception as e:
        if _timed_out(e):
            return _unanswered("GET", path, budget)
        return {"error": str(e)}


def _delete(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode() if body else None
    try:
        req = urllib.request.Request(
            f"{_api_base()}{path}",
            data=data,
            headers=_internal_headers({"Content-Type": "application/json"} if data else None),
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=GATEWAY_READ_TIMEOUT_SECS) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return _refused(exc)
    except Exception as e:
        if _timed_out(e):
            return _unanswered("DELETE", path, GATEWAY_READ_TIMEOUT_SECS)
        return {"error": str(e)}


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against schema. Returns cleaned args."""
    from personalclaw.validation import MCP_CORE_SCHEMAS, validate_tool_args

    schema = MCP_CORE_SCHEMAS.get(name)
    if schema:
        return validate_tool_args(args, schema)
    return args  # tools without schemas (memory_list) pass through


def _current_session_thread_ts() -> str | None:
    """Read the current session's thread_ts from the most recent session_pid file.

    Scans the ACTIVE home. This globbed `Path.home() / ".personalclaw"` outright, so a tool
    call in an isolated-home session picked up the most recent pid file of a DIFFERENT
    instance and reported that session's thread — silently, since the read succeeds.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.hooks import safe_read_file_bytes

    try:
        pid_files = sorted(
            config_dir().glob("session_pid_*.txt"),
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


def _render_resource_catalog(skill_name: str, loader: Any) -> str:
    """The L0 resource catalog skill_invoke appends — one line per DECLARED resource.

    Names and one-line descriptions only. Contents are never inlined here: that is
    the whole point of the tier (a skill with a 200KB reference doc costs one line
    of context until the agent asks for it). No resources → the empty string, so a
    skill without any is byte-identical to before.
    """
    try:
        resources = loader.resources_for(skill_name)
    except Exception:  # a catalog is a nicety; never fail an invoke over it
        logger.debug("resource catalog skipped for %s", skill_name, exc_info=True)
        return ""
    if not resources:
        return ""
    lines = [
        "[Resources — DECLARED, not loaded. Load ONE with "
        f'skill_resource(skill="{skill_name}", path="…"):]'
    ]
    for res in resources:
        lines.append(f"- {res.path}" + (f" — {res.description}" if res.description else ""))
    return "\n".join(lines) + "\n"


def _render_skill_folder(skill_name: str, loader: Any) -> str:
    """Where a skill's own files are, for instructions that name one ("Use template.md"): the
    skill's folder, written from ``~``, when it holds anything besides those instructions and an
    install's hidden record. ``""`` otherwise, so a skill that is its ``SKILL.md`` alone reads as
    before. Said the same to every agent: the native file tools read it with no approval
    (``file_scope``), and an agent CLI opens it with its own."""
    from personalclaw.home_paths import from_home
    from personalclaw.skills.loader import is_instructions_file

    skill_file = loader.skill_file(skill_name)
    if skill_file is None:
        return ""
    folder = skill_file.parent
    try:
        ships = any(
            not entry.name.startswith(".") and not is_instructions_file(entry.name)
            for entry in folder.iterdir()
        )
    except OSError:  # a listing that cannot be made names nothing
        return ""
    if not ships:
        return ""
    return f"[This skill's folder: {from_home(folder)}. The files it names are there.]\n"


def _load_skill_resource(args: dict[str, Any]) -> str:
    """``skill_resource(skill, path)`` — read ONE declared resource of one skill.

    All the refusal logic lives in ``SkillsLoader.read_resource`` (allowlist,
    post-realpath containment, cap); this is the presentation half. The content is
    third-party-authored text arriving from outside the user↔agent trust boundary,
    so it is FENCED as data — a resource that says "ignore your instructions" is
    quoted, not obeyed. The truncation notice sits OUTSIDE the fence so it reads as
    harness text rather than as part of the resource.
    """
    from personalclaw.skills.loader import (
        RESOURCE_MAX_BYTES,
        SkillResourceRefused,
        SkillsLoader,
    )

    skill_name = (args.get("skill") or "").strip()
    rel_path = (args.get("path") or "").strip()
    if not skill_name or not rel_path:
        return tool_failure("both skill and path are required.")
    loader = SkillsLoader()
    try:
        read = loader.read_resource(skill_name, rel_path)
    except SkillResourceRefused as exc:
        return tool_failure(f"{exc}")
    # Usage-recorded like skill_invoke: loading a resource IS a use of the skill,
    # so surfacing-ranking and the curator see it.
    try:
        from personalclaw.skills.usage import SkillUsageStore

        SkillUsageStore().record_use(skill_name)
    except Exception:
        logger.debug("skill_resource usage record skipped", exc_info=True)

    from personalclaw.security import fence_untrusted

    body = fence_untrusted(
        read.text,
        source="skill-resource",
        source_type="skill_resource",
        source_id=f"{read.skill}/{read.path}",
        transformation_path="read",
    )
    out = [f"[Skill resource: {read.skill}/{read.path} — read-only, not executed]", body]
    if read.truncated:
        out.append(
            f"[Truncated: showing the first {RESOURCE_MAX_BYTES} of {read.size} bytes — "
            "open the file directly for the rest.]"
        )
    return "\n".join(out)


def _message_payload(args: dict[str, Any]) -> dict[str, Any] | ToolFailure:
    """What ``notify`` asks the gateway to send (``/api/send-message``), from its arguments; or
    the refusal of a ``session`` it cannot route by."""
    payload: dict[str, Any] = {"text": args["text"], "title": args.get("title", "Agent Message")}
    if args.get("blocks"):
        payload["blocks"] = args["blocks"]
    if args.get("channel"):
        payload["channel"] = args["channel"]
    if args.get("user"):
        payload["user"] = args["user"]
    if args.get("via"):
        payload["via"] = args["via"]
    if "unfurl_links" in args:
        payload["unfurl_links"] = args["unfurl_links"]
    if "unfurl_media" in args:
        payload["unfurl_media"] = args["unfurl_media"]
    if args.get("thread_ts"):
        payload["thread_ts"] = args["thread_ts"]
    if yes_or_no(args.get("reply_broadcast")) is True:
        payload["reply_broadcast"] = args["reply_broadcast"]
    # ───────────────────────────────────────────────────────────────
    # Cron delivery contract (see messaging.py:api_send_message for the
    # full version). Default for cron callers that didn't set any of
    # session/channel/user: auto-apply session="origin" so the message
    # injects into the spawning chat. Explicit session="channel" opts out
    # and routes to the owner's messaging channel. Explicit channel/user/via
    # always wins.
    # ───────────────────────────────────────────────────────────────
    session = args.get("session")
    caller_session_env = os.environ.get("PERSONALCLAW_SESSION_KEY", "")
    if (
        not session
        and not args.get("channel")
        and not args.get("user")
        and not args.get("via")
        and caller_session_env.startswith("cron:")
    ):
        session = "origin"
    if session:
        if session not in ("origin", "channel"):
            return tool_failure('session must be "origin" or "channel".')
        payload["session"] = session
        caller_session = _resolve_session_key()
        if caller_session.startswith("cron:"):
            payload["caller_session"] = caller_session
    return payload


def _message_refusal(args: dict[str, Any]) -> ToolFailure | None:
    """The gateway's refusal of this message, asked without sending it (``dry_run``): a chat
    channel not set up here, an id no channel here issued, a channel not tracked, a user who is
    not the owner. None when it would go out, and when the gateway could not say: only an answer
    marked as the check's (``"dry_run": true``) is one, never a request that did not reach it."""
    payload = _message_payload(args)
    if isinstance(payload, ToolFailure):
        return payload
    resp = _post("/api/send-message", {**payload, "dry_run": True})
    if resp.get("dry_run") is not True or resp.get("ok") is True:
        return None
    return tool_failure(str(resp.get("error") or resp))


def _read_attachment(args: dict[str, Any]) -> bytes | tuple[str, ToolFailure]:
    """The bytes ``notify_attachment`` sends, read once and checked; or why it refuses the file, as
    the audit's code and the tool's answer: a control character in its path, a path no file
    surface may read or a file too large, a name or a text that carries a secret, a file that is
    not UTF-8 text. The handler sends exactly the bytes this checked."""
    from personalclaw.file_roots import control_character_in
    from personalclaw.hooks import FileTooLargeError, safe_read_file_bytes
    from personalclaw.security import redact

    src = Path(args.get("path", ""))
    # The copy in the outbox keeps the source's name, so a name no file surface takes is
    # refused here too (`file_roots.CONTROL_CHARS`).
    bad = control_character_in(str(args.get("path", "")))
    if bad:
        return f"control_character: {bad}", tool_failure(
            f"the path has a control character ({bad}) in it; rename the file without it first"
        )
    try:
        raw = safe_read_file_bytes(str(src))
    except FileTooLargeError as e:
        return f"file_too_large: {e}", tool_failure(f"{e}")
    if raw is None:
        return f"path_not_allowed: {src}", tool_failure(f"file not found or access denied: {src}")
    if redact(src.name) != src.name:
        return f"sensitive_filename: {redact(src.name)}", tool_failure(
            "filename contains sensitive content. Rename the file first."
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "not_utf8", tool_failure("only UTF-8 text files are supported")
    if redact(text) != text:
        return "sensitive_content_detected", tool_failure(
            "file content contains sensitive data; send aborted"
        )
    return raw


def _preflight(name: str, raw_args: dict[str, Any]) -> ToolFailure | None:
    """What these tools refuse before anyone is asked to approve a call: a tool this leaf may not
    call and arguments the tool's schema refuses (``mcp_shared.admitted_arguments``), then for a
    message or a file for the owner what its send refuses, asked without sending anything."""
    from personalclaw.mcp_shared import admitted_arguments

    args = admitted_arguments(name, raw_args, _validate_args)
    if isinstance(args, ToolFailure):
        return args
    if name == "notify":
        return _message_refusal(args)
    if name == "notify_attachment":
        read = _read_attachment(args)
        return read[1] if isinstance(read, tuple) else None
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
            return tool_failure("name is required.")
        from personalclaw.skills.loader import SkillsLoader

        loader = SkillsLoader()
        content = loader.load_skill(skill_name)
        if content is None:
            return tool_failure(
                f"no skill named '{skill_name}'. Check the skill index for exact names."
            )
        # Phase-2 disclosure: record the load as a use (#25) so surfacing-ranking
        # and the curator see on-demand invocations, then return the full body.
        try:
            from personalclaw.skills.usage import SkillUsageStore

            SkillUsageStore().record_use(skill_name)
        except Exception:
            logger.debug("skill_invoke usage record skipped", exc_info=True)
        stripped = loader.strip_frontmatter(content)
        # The body, the folder its files are in, and an L0 CATALOG of declared resources —
        # paths and one-line descriptions only, never their contents. The agent pulls one with
        # skill_resource when it decides it needs it.
        folder = _render_skill_folder(skill_name, loader)
        catalog = _render_resource_catalog(skill_name, loader)
        return f"[Skill: {skill_name}]\n{stripped}\n{folder}{catalog}[End of skill]"

    if name == "skill_resource":
        return _load_skill_resource(args)

    if name == "skill_remember":
        title = (args.get("title") or "").strip()
        body = (args.get("body") or "").strip()
        if not title or not body:
            return tool_failure("both title and body are required.")
        from personalclaw.skills import ephemeral

        session_key = get_current_session_key() or "default"
        draft = ephemeral.remember(session_key, title, body)
        if draft is None:
            return tool_failure(
                "could not save the draft (empty after redaction, or this "
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
            return tool_failure("query is required.")
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
        # The path is written in the call, like every internal call's, so the census of what the
        # internal credential must open can read it.
        resp = _get(f"/api/context?{'&'.join(qs)}")
        if resp.get("error"):
            return tool_failure(f"loading context: {resp['error']}")
        # The endpoint already renders the tiered markdown body; return it verbatim so
        # the agent reads the same block an adapter file would carry.
        return resp.get("text") or "No context available for this project."

    if name == "project_context_review":
        return _project_context_review(args)

    if name == "skill_promote":
        return _skill_promote(args)

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
        from personalclaw import webhook_callbacks
        from personalclaw.validation import REGISTER_HOOK_SCHEMA, validate_tool_args

        args = validate_tool_args(args, REGISTER_HOOK_SCHEMA)

        hook_id = str(args.get("hook_id", "")).strip()
        if not hook_id:
            return tool_failure("hook_id is required")
        context_summary = str(args.get("context_summary", ""))
        # 🔴 In the callbacks' own file (`webhook_callbacks`), never `hooks.json`: that is the
        # lifecycle trigger store's, and a registration written into it replaced the owner's
        # triggers (`hook_id="hooks"`) or was dropped at the store's next save. Registered NOT
        # allowed to run: the turn a callback starts runs with the agent's tools from the context
        # saved here, so it waits for the owner's Allow on the Triggers page, like a trigger the
        # chat makes (`triggers.grants`).
        callback = webhook_callbacks.register(hook_id, context_summary)
        waiting = not webhook_callbacks.allowed(callback)
        # Resolve webhook URL. A refusal from the base owner is returned as the tool's
        # result: a hook URL naming the wrong port is worse than no hook URL, because the
        # external system would POST its results into another instance.
        from urllib.parse import urlparse

        try:
            _resolved_base = _api_base()
        except gateway_base.GatewayBaseUnresolved as exc:
            return tool_failure(f"cannot build the webhook URL — {exc}")
        parsed = urlparse(_resolved_base)
        base = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            base += f":{parsed.port}"
        url = f"{base}/api/hooks/agent"
        from personalclaw.security import redact_credentials, redact_exfiltration_urls
        from personalclaw.sel import sel

        hook_id_safe, _ = redact_exfiltration_urls(hook_id)
        hook_id_safe, _ = redact_credentials(hook_id_safe)
        session_key_safe = f"{HOOK_SESSION_PREFIX}{hook_id_safe}"
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
            f"Context summary saved for session resume.\n"
            + (
                "It does not run until the owner allows it on the Triggers page, which asks them "
                "first: until then a call to it is refused. Tell them it is waiting."
                if waiting
                else "The owner allowed this callback with this context, so a call to it runs."
            )
        )

    if name == "notify":
        payload = _message_payload(args)
        if isinstance(payload, ToolFailure):
            return payload
        resp = _post("/api/send-message", payload)
        if not resp.get("ok"):
            # A refusal says so by its type; the gateway's sentence, when it sent one, is the
            # reason (a channel not set up here names the ones that are).
            return tool_failure(str(resp.get("error") or resp))
        if resp.get("session"):
            return "Message injected into target session."
        if resp.get("inbox"):
            # Connected channels, none of which reached the owner: it went to the Inbox.
            return f"Message delivered to the Inbox. {resp.get('detail', '')}".strip()
        # Explicit session="channel" is the opt-out, not a failure — surface
        # the actual outcome (channel delivery + notification) instead of the
        # "session unavailable" fallback message.
        if payload.get("session") == "channel":
            ts = resp.get("ts", "")
            if resp.get("channel"):
                return f"Message sent to channel. ts={ts}" if ts else "Message sent to channel."
            return "Message delivered as dashboard notification (channel unavailable)."
        if payload.get("session"):
            return "Session injection unavailable — target session not found or caller is not a cron. Message delivered normally."  # noqa: E501
        ts = resp.get("ts", "")
        return f"Message sent. ts={ts}" if ts else "Message sent."

    if name == "notify_attachment":
        import uuid

        from personalclaw.config.loader import outbox_dir
        from personalclaw.security import redact
        from personalclaw.sel import sel

        src = Path(args.get("path", ""))
        desc = redact(args.get("description", ""))
        read = _read_attachment(args)
        if isinstance(read, tuple):
            code, refused = read
            sel().log_tool_invocation(
                session_key="mcp_core",
                source="mcp",
                tool_name="notify_attachment",
                outcome="denied",
                error=code,
            )
            return refused
        raw = read
        clean_name = src.name
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
            return tool_failure(f"{d['error']}")
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

    if name == "suggest_template":
        return _suggest_template(args)

    if name == "refiner_evidence":
        from personalclaw.learning import refiner_tools

        evidence = refiner_tools.gather_evidence(str(args.get("workflow_name", "") or ""))
        return json.dumps({"ok": True, **evidence}, indent=2, ensure_ascii=False, default=str)

    if name == "propose_template_diff":
        from personalclaw.learning import refiner_tools
        from personalclaw.validation import decode_json_text

        ops = decode_json_text(args.get("ops"))
        if not isinstance(ops, list) or not ops:
            return tool_failure(
                "'ops' must be a non-empty JSON array of typed ops.", code="WF_REFINE_NO_OPS"
            )
        result = refiner_tools.file_template_diff(
            str(args.get("workflow_name", "") or ""),
            ops=[o for o in ops if isinstance(o, dict)],
            rationale=str(args.get("rationale", "") or ""),
            run_ids=[str(r) for r in (args.get("run_ids") or [])],
            predicted_fixes=[str(p) for p in (args.get("predicted_fixes") or [])],
        )
        return json.dumps({"ok": True, **result}, indent=2, ensure_ascii=False, default=str)

    return f"Unknown tool: {name}"


def _suggest_template(args: dict[str, Any]) -> str:
    """Decide whether the "save as template" offer is welcome, and return its wording (UP-R9).

    The DECISION is `template_pipeline.should_nudge`'s and the wording is `nudge_text`'s — both
    already implement the anti-nag rules, and re-deciding here would give the feature two ideas of
    when it may speak. What this adds is the persistence those rules need to be rules at all: the
    occurrence count, the decline, and the cooldown are read from and written back to disk, so a
    restart cannot re-offer a shape the user just refused.

    Returns the refusal REASON when it declines, because a model that is told only "no" will ask
    again next turn; one told "declined for this shape" will not.
    """
    from personalclaw.validation import SUGGEST_TEMPLATE_SCHEMA, validate_tool_args
    from personalclaw.workflows import template_pipeline, template_store

    args = validate_tool_args(args, SUGGEST_TEMPLATE_SCHEMA)
    shape = str(args.get("shape", "") or "").strip()
    if not shape:
        return tool_failure("'shape' is required.", code="SUGGEST_TEMPLATE_SHAPE_REQUIRED")
    decision = str(args.get("decision", "") or "observe").strip().lower()

    state = template_store.load_nudge(shape)

    if decision == "accepted":
        # Terminal for the shape: an accepted shape has a template, and `should_nudge` will not
        # offer again. Recorded rather than inferred from a later save, because the save happens in
        # a different tool and a signal that depended on it would be lost when the user saves
        # manually.
        state.accepted = True
        template_store.save_nudge(state)
        return (
            f"Recorded: “{shape}” is saved as a template. Call workflow_plan (optionally with "
            "source_session_id to mine this conversation), then workflow_author to write it."
        )
    if decision == "declined":
        state.declined = True
        template_store.save_nudge(state)
        return (
            f"Recorded: no template for “{shape}”. This shape will not be suggested again — "
            "do not raise it in a later turn."
        )

    # `observe`: one more sighting, then ask the rules.
    state.occurrences += 1
    turn = template_store.bump_turn()
    offer, reason = template_pipeline.should_nudge(state, turn=turn)
    if not offer:
        # The count is still saved. A sighting that went unrecorded because it did not yet clear
        # the threshold is a shape that never reaches the threshold.
        template_store.save_nudge(state)
        return f"Do not suggest a template for “{shape}” right now: {reason}."

    state.last_offered_turn = turn
    template_store.save_nudge(state)
    return (
        f"Suggest a template ({reason}). Say this to the user, in your own voice:\n\n"
        f"{template_pipeline.nudge_text(state)}\n\n"
        "If they say yes, call suggest_template again with decision='accepted' and then "
        "workflow_plan. If they say no, call it with decision='declined' so this shape is "
        "never raised again."
    )


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
    """This session's turns, for grounding a review's or promotion's proposals. Best-effort.

    Shared by `project_context_review` and `skill_promote`: both file proposals that a human reads
    against the conversation that motivated them, and both treat a failed read as "no evidence"
    rather than an error — a proposal with a thinner excerpt still beats no proposal.
    """
    if not session_key:
        return []
    try:
        from personalclaw.history import ConversationLog

        return ConversationLog().recent(session_key, max_messages=100)
    except Exception:
        logger.debug("transcript read skipped for proposal grounding", exc_info=True)
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
        return tool_failure("name is required (the proposed template name).")
    if not steps:
        return tool_failure(
            "steps is required — a non-empty list of the procedure's steps, in order."
        )

    # `template_surfaced` resolved against the real def registry, not left at its dataclass
    # default: the TEMPLATE_EXISTS pre-gate depends on library state, and defaulting it False
    # would make that branch unreachable in production.
    surfaced = slug.lower() in _workflow_names()

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


def _workflow_names() -> set[str]:
    """The names of the workflows this instance has, lowercased; none when they cannot be read.

    The gateway's registry holds them. The tool server an agent CLI runs holds none, so there the
    gateway's own list answers (``GET /api/workflows``): read here, every name would be missing.
    """
    if serves_an_agent_cli():
        listed = _get("/api/workflows").get("defs")
    else:
        from personalclaw.workflows import service

        try:
            listed = _run_coro(service.list_defs()).get("defs")
        except Exception:  # noqa: BLE001 - an unreadable library names nothing
            logger.debug("the workflow definitions could not be listed", exc_info=True)
            listed = None
    return {str(d.get("name") or "").strip().lower() for d in listed or [] if isinstance(d, dict)}


def _run_coro(coro: Any) -> Any:
    """Run a coroutine from this sync tool boundary, whether or not a loop is already running."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    from personalclaw import memory_writes

    with memory_writes.ScopeCarryingExecutor(max_workers=1) as pool:
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
        return tool_failure("at least one item is required.")
    project_id = _resolve_review_project_id(str(args.get("project_id") or ""))
    if not project_id:
        return tool_failure(
            "no project to review. This session is not bound to a project — "
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


#: How each typed refusal reads back to the agent. Every `Refusal` member is mapped, because the
#: model that named a bad candidate is the one that can fix it — a bare "declined" teaches it
#: nothing, which is the same reason `template_save_from_session` reports its skip_reason.
_PROMOTE_REFUSALS = {
    "needs_name": "name is required (the proposed skill name).",
    "needs_description": "description is required (one line on when the skill applies).",
    "needs_procedure": "procedure is required (the steps, as markdown).",
    "needs_rationale": "rationale is required — it is what the user reads before deciding.",
    "unusable_name": (
        "that name cannot become a skill name. Use a short phrase of letters, digits and spaces."
    ),
    "procedure_too_long": "the procedure is too long for a skill. Condense it to the steps.",
    "run_not_found": "no such run. Check the run_id, or omit it to promote this conversation.",
    "run_not_successful": (
        "that run did not finish successfully. Only a completed run is worth promoting."
    ),
    "already_decided": (
        "the user already decided on this exact skill, so it was not re-proposed. "
        "Nothing was filed and nothing is wrong."
    ),
    "queue_refused": "the review queue could not accept it. Nothing was filed.",
}


def _skill_promote(args: dict[str, Any]) -> str:
    """Promote a completed run or conversation into a skill PROPOSAL. Writes NO skill (LEA-11).

    Delegates to `learning.skill_promotion`, which verifies a named run actually completed and files
    through the shared human-gated queue. Reports what reached the QUEUE — never what was installed
    — so the agent tells the user what to review, and cannot claim a skill now exists.
    """
    from personalclaw.learning import skill_promotion

    session_key = _resolve_session_key()
    run_id = str(args.get("run_id") or "").strip()
    result = skill_promotion.promote(
        name=str(args.get("name") or ""),
        description=str(args.get("description") or ""),
        procedure=str(args.get("procedure") or ""),
        rationale=str(args.get("rationale") or ""),
        run_id=run_id,
        session_key=session_key,
        # A run carries its own evidence in the ledger; a conversation promotion is grounded in
        # the turns that produced it.
        transcript=None if run_id else _review_transcript(session_key),
    )
    if result.proposal is None:
        detail = _PROMOTE_REFUSALS.get(result.refusal, "it was declined.")
        return f"Nothing filed: {detail}"
    return (
        f"Filed a skill proposal — '{result.proposal.title}' — nothing was written to the skill "
        "library. The skill is created only when the user accepts it in the Learning review queue."
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
        return tool_failure("slug is required (the saved artifact to pin).")
    view_id = str(args.get("view_id") or "").strip() or views_store.PRESET_OVERVIEW_ID
    size = str(args.get("size") or "m")
    ref = slug if slug.startswith("artifact:") else f"artifact:{slug}"
    try:
        views_store.add_tile(view_id, ref, size=size, added_by="agent")
    except views_store.ViewNotFoundError:
        return tool_failure(f"no view '{view_id}' to propose onto.")
    except ValueError as exc:
        return tool_failure(f"{exc}")
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
    # Desktop computer use. The category module is the THIN SHIM: it declares the
    # seven tools and forwards each call to the gateway's in-gateway dispatch. It is aggregated
    # here rather than served by a second `personalclaw mcp-computer` stdio server because this
    # process is already the shim the plan describes — one composition root, one identity
    # resolver, and a process that never imports a driver.
    "personalclaw.computer_use.tools",
)


def _aggregated_list_tools() -> list[dict[str, Any]]:
    """Core tools + every aggregated category's tools (the ACP MCP-server surface), each offered
    with what its validator enforces (`validation.offered_schema`), as the native loop offers it."""
    import importlib

    from personalclaw.validation import offered_schema, tool_field_schema

    tools = list(_list_tools())
    for mod_path in _AGGREGATED_CATEGORY_MODULES:
        tools.extend(importlib.import_module(mod_path)._list_tools())
    return [
        (
            {
                **tool,
                "inputSchema": offered_schema(tool["inputSchema"], tool_field_schema(tool["name"])),
            }
            if "inputSchema" in tool
            else tool
        )
        for tool in tools
    ]


def own_tool(name: str) -> dict[str, Any] | None:
    """The tool dict PersonalClaw's own ``mcp-core`` surface serves as *name*, or ``None``.

    Where a seam that holds only a tool's NAME reads what the tool declares
    (``annotations``/``_meta``): the in-process handler every one of these tools funnels
    through (``mcp_shared.leaf_tool_denial``). An exact lookup, never a match.
    """
    for tool in _aggregated_list_tools():
        if tool.get("name") == name:
            return tool
    return None


def _aggregated_call_tool(name: str, raw_args: dict[str, Any]) -> str:
    """Route a tool call to the owning category module, else core's own dispatch."""
    import importlib

    for mod_path in _AGGREGATED_CATEGORY_MODULES:
        mod = importlib.import_module(mod_path)
        if any(t["name"] == name for t in mod._list_tools()):
            return mod._call_tool(name, raw_args)
    return _call_tool(name, raw_args)


#: The mode the gateway answered for each session this process has served
#: (:func:`_call_as_its_session`). A session's mode is fixed when it is created, so one answer
#: holds for the life of the process; an answer that could not be had is not kept.
_SESSION_MODES: dict[str, str] = {}

#: What a call this process cannot name the chat of is answered (:func:`_call_as_its_session`).
UNNAMED_CALL = (
    "This call was not made: PersonalClaw's tool server could not tell which chat it is for, and "
    "it makes no call for a chat it cannot name, since the call would run as no one's work."
)


def _call_as_its_session(name: str, raw_args: dict[str, Any]) -> str:
    """Run one call of this server's tools as the chat it serves: the ``mcp-core`` dispatch.

    An agent CLI's tools run in this process, outside the gateway, where the session scope a
    chat's turn holds (:mod:`personalclaw.memory_writes`) is empty, so an Incognito chat's request
    for an image reached the image model from here. A call made for a session therefore asks the
    gateway what that session is (``GET /api/chat/sessions/model-reach``, the answer the gateway's
    own stores give it) and runs as deriving from it when it keeps nothing: no model but its own
    reads the call's work, and the stores refuse its writes, as in the gateway. A session the
    gateway cannot answer for is taken to keep nothing.

    A call this process cannot name the chat of is made nowhere (:data:`UNNAMED_CALL`), as the
    gateway refuses it too (``internal_call_names_no_work``). The chat is named to this process
    when its agent CLI is started for it, and a warm-pool process started before its chat is tied
    to the chat that claims it (``session_pid.tie_to_session``), so only a tie that cannot be read
    leaves a call unnamed, and guessing the chat is the one thing worse than not answering.

    The call runs with that session bound (:func:`set_current_session_key`), as a call the
    built-in agent makes does, so what the call reaches on the network is held to the session's
    egress tier here too (``net.policy.egress_policy_for_run``).
    """
    from personalclaw import memory_writes

    key = _resolve_session_key()
    if not key:
        return tool_failure(UNNAMED_CALL, code="internal_call_names_no_work")
    mode = _SESSION_MODES.get(key)
    if mode is None:
        reply = _get("/api/chat/sessions/model-reach")
        answered = reply.get("memory_mode") if not reply.get("error") else None
        if isinstance(answered, str) and answered:
            mode = _SESSION_MODES[key] = answered
        else:
            mode = memory_writes.UNREADABLE
    token = set_current_session_key(key)
    try:
        if mode == memory_writes.PERSISTENT:
            return _aggregated_call_tool(name, raw_args)
        with memory_writes.derived_from(key, memory_mode=mode):
            return _aggregated_call_tool(name, raw_args)
    finally:
        reset_current_session_key(token)


def run_mcp_core_server() -> None:
    """Run MCP stdio server for core agent tools — the single endpoint an ACP CLI
    consumes, aggregating every native tool category into one surface. Each call runs as the chat
    it serves (:func:`_call_as_its_session`), and what only the gateway holds is reached through
    it (:func:`serves_an_agent_cli`)."""
    from personalclaw.mcp_shared import run_mcp_stdio_loop

    global _SERVES_AN_AGENT_CLI
    _SERVES_AN_AGENT_CLI = True
    run_mcp_stdio_loop("personalclaw-core", "1.0.0", _aggregated_list_tools, _call_as_its_session)
