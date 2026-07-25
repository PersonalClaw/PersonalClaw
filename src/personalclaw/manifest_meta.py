"""The two facts the tool/route registries don't carry — a response-type
discriminator and one or two worked examples per tool — plus the route-exclusion
allowlist. This is the ONE hand-maintained input to :mod:`personalclaw.manifest`,
and it is exactly what :mod:`tests.test_api_manifest_drift` audits: a tool added
without an entry here (or a route neither described nor excluded) fails the suite
rather than becoming a silent, undocumented surface an agent has to guess at.

Design (Astryx's ``JSON_SUPPORTED``/``RESPONSE_TYPES`` pattern): registries own
name/description/schema; this map owns only what they can't express. Keep examples
FAITHFUL — the whole point of the manifest is that an agent never guesses a
signature, so an example with an invented parameter is worse than none. Every
key here is a real tool name and every ``args`` key is a real parameter of that
tool (schema-checked by the drift test).

``error_codes`` are intentionally empty until PLATFORM-LEGIBILITY §2 lands the
``AgentError`` code registry; the drift test asserts any code listed here exists
in that registry (vacuously true while all are empty), so §2 can populate them
tool-by-tool without a shape change here.
"""

from __future__ import annotations

import re
from typing import Any

# aiohttp's ``resource.canonical`` reports a dynamic segment ``{name:regex}`` with
# the regex stripped — ``{name}``. The live route walk (manifest.py) and the static
# AST walk (the drift test) must compare in the SAME space, or a route whose source
# carries a regex (``/apps/{name}/api/{tail:.*}``) matches the exclusion in one
# rendering but not the other — the exact two-rendering divergence this slice exists
# to prevent. Both sides normalize through this one function; MANIFEST_EXCLUDE keys
# are stored already-canonical.
_ROUTE_REGEX_SEG = re.compile(r"\{([^{}:]+):[^{}]+\}")


def canonical_route(path: str) -> str:
    """Strip the ``:regex`` from ``{name:regex}`` segments, matching aiohttp's canonical form."""
    return _ROUTE_REGEX_SEG.sub(r"{\1}", path)


# ── Route exclusions ────────────────────────────────────────────────────────
# Every registered HTTP route is either described in the manifest (the /api/*
# surface, walked live) or listed here with a reason. A stale entry (a path that
# no longer registers) also fails the drift test, so this set can't rot.
#
# Keyed by CANONICAL path (see canonical_route); the value is the one-line
# justification. Static mounts and the SPA/app-asset serving paths are UI
# transport, not an agent-callable API; the app reverse-proxy is reached via the
# app-route tools (§4), not directly.
MANIFEST_EXCLUDE: dict[str, str] = {
    "/": "SPA entry point (serves index.html) — UI transport, not an API",
    "/claw.svg": "favicon asset — UI transport",
    "/assets": "static mount for the built React bundle — UI transport",
    "/sprites": "static mount for sprite assets — UI transport",
    "/fonts": "static mount for web fonts — UI transport",
    "/vendor": "static mount for the import-map vendor shims — UI transport",
    "/apps/{name}/ui/{tail}": "per-app UI asset serving — UI transport, not an API",
    "/apps/{name}/api/{tail}": (
        "per-app backend reverse-proxy — reached via the app-route tools (§4), "
        "not called directly by agents"
    ),
}


def is_excluded_route(method: str, path: str) -> bool:
    """True if a (method, path) pair is a known non-API route (see MANIFEST_EXCLUDE).

    Matches on canonical path — the exclusion is about what the path *is* (UI
    transport / proxy), independent of method, so a static mount registered for
    any verb is covered by its single entry. ``path`` is canonicalized first so a
    live route reported as ``{name}`` matches a source path written ``{name:regex}``.
    """
    return canonical_route(path) in MANIFEST_EXCLUDE


# ── Tool response-type + examples ───────────────────────────────────────────
# response_type: the {type, data} discriminator a caller branches on (§1.3),
#                named <domain>.<shape> — adopted incrementally; a value here is
#                the declared shape for that tool's success payload.
# examples:      1-2 worked calls with FAITHFUL arg names (schema-checked). Each
#                is {"summary": <what it does>, "args": {<real params>}}.
# error_codes:   §2 AgentError codes this tool can return (empty until §2).
TOOL_META: dict[str, dict[str, Any]] = {
    # ── personalclaw-core (== mcp_core._list_tools) ──────────────────────────
    "skill_invoke": {
        "response_type": "skill.invoke.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Load a skill's instructions into the session",
                "args": {"name": "pclaw-api"},
            },
        ],
    },
    "skill_search": {
        "response_type": "skill.search.results",
        "error_codes": [],
        "examples": [
            {
                "summary": "Find skills matching a query",
                "args": {"query": "write a blog post", "limit": 5},
            },
        ],
    },
    "skill_remember": {
        "response_type": "skill.remember.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Persist a reusable how-to as a new skill",
                "args": {
                    "title": "Deploy the website",
                    "body": "Run npm run deploy from personalclaw.dev/",
                },
            },
        ],
    },
    "wait": {
        "response_type": "wait.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Pause before re-checking a long-running job",
                "args": {"seconds": 30, "reason": "let the build finish"},
            },
        ],
    },
    "hook_register": {
        "response_type": "hook.register.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Register a follow-up hook for the current work",
                "args": {
                    "hook_id": "verify-deploy",
                    "context_summary": "re-check the site is live after deploy",
                },
            },
        ],
    },
    "notify": {
        "response_type": "notify.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Send a notification to the user",
                "args": {"text": "The nightly backup finished cleanly."},
            },
        ],
    },
    "notify_attachment": {
        "response_type": "notify.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Notify with a file attachment",
                "args": {"path": "artifacts/report.pdf", "description": "Weekly report"},
            },
        ],
    },
    "loop_nudge_stop": {
        "response_type": "loop.nudge_stop.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Stop the autonomous nudge loop for this session",
                "args": {"reason": "goal reached"},
            },
        ],
    },
    # ── personalclaw-schedule ────────────────────────────────────────────────
    "schedule_list": {
        "response_type": "schedule.list",
        "error_codes": [],
        "examples": [{"summary": "List all scheduled jobs", "args": {}}],
    },
    "schedule_add": {
        "response_type": "schedule.job",
        "error_codes": [],
        "examples": [
            {
                "summary": "Schedule a recurring daily message",
                "args": {
                    "name": "morning-brief",
                    "message": "Summarize my calendar and unread inbox",
                    "cron_expr": "0 8 * * *",
                },
            },
            {
                "summary": "Schedule a one-off reminder after a delay",
                "args": {"name": "stretch", "message": "Take a break", "delay": 3600},
            },
        ],
    },
    "schedule_update": {
        "response_type": "schedule.job",
        "error_codes": [],
        "examples": [
            {
                "summary": "Change a job's schedule",
                "args": {"job_id": "morning-brief", "cron_expr": "0 9 * * *"},
            },
        ],
    },
    "schedule_remove": {
        "response_type": "schedule.remove.result",
        "error_codes": [],
        "examples": [{"summary": "Delete a scheduled job", "args": {"job_id": "morning-brief"}}],
    },
    "schedule_remove_all": {
        "response_type": "schedule.remove.result",
        "error_codes": [],
        "examples": [{"summary": "Delete every scheduled job", "args": {}}],
    },
    "schedule_pause": {
        "response_type": "schedule.job",
        "error_codes": [],
        "examples": [
            {"summary": "Pause a job without deleting it", "args": {"job_id": "morning-brief"}}
        ],
    },
    "schedule_resume": {
        "response_type": "schedule.job",
        "error_codes": [],
        "examples": [{"summary": "Resume a paused job", "args": {"job_id": "morning-brief"}}],
    },
    "schedule_trigger": {
        "response_type": "schedule.trigger.result",
        "error_codes": [],
        "examples": [
            {"summary": "Fire a scheduled job right now", "args": {"job_id": "morning-brief"}}
        ],
    },
    "schedule_natural": {
        "response_type": "schedule.job",
        "error_codes": [],
        "examples": [
            {
                "summary": "Schedule from a natural-language cadence",
                "args": {
                    "name": "standup",
                    "message": "Post my standup update",
                    "cadence": "every weekday at 9am",
                },
            },
        ],
    },
    # ── personalclaw-artifacts ───────────────────────────────────────────────
    "artifact_save": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Save generated text as a named artifact",
                "args": {
                    "name": "Launch plan",
                    "content": "# Launch plan\n...",
                    "kind": "document",
                },
            },
        ],
    },
    "artifact_get": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [{"summary": "Read an artifact by slug", "args": {"slug": "launch-plan"}}],
    },
    "artifact_update": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Replace an artifact's content",
                "args": {"slug": "launch-plan", "content": "# Launch plan v2\n..."},
            }
        ],
    },
    "artifact_list": {
        "response_type": "artifact.list",
        "error_codes": [],
        "examples": [{"summary": "List artifacts of a kind", "args": {"kind": "document"}}],
    },
    "artifact_versions": {
        "response_type": "artifact.versions",
        "error_codes": [],
        "examples": [
            {"summary": "List an artifact's version history", "args": {"slug": "launch-plan"}}
        ],
    },
    "artifact_delete": {
        "response_type": "artifact.delete.result",
        "error_codes": [],
        "examples": [{"summary": "Delete an artifact", "args": {"slug": "launch-plan"}}],
    },
    "image_generate": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Generate an image and save it as an artifact",
                "args": {"prompt": "a watercolor fox", "size": "1024x1024"},
            },
        ],
    },
    "video_generate": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Generate a short video",
                "args": {"prompt": "timelapse of clouds", "duration_seconds": 5},
            },
        ],
    },
    # ── personalclaw-memory ──────────────────────────────────────────────────
    "memory_remember": {
        "response_type": "memory.remember.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Persist a durable preference",
                "args": {"rule": "Prefer concise commit messages", "category": "style"},
            },
        ],
    },
    "memory_list": {
        "response_type": "memory.list",
        "error_codes": [],
        "examples": [{"summary": "List all remembered rules", "args": {}}],
    },
    "memory_forget": {
        "response_type": "memory.forget.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Forget rules matching a query",
                "args": {"query": "concise commit messages"},
            }
        ],
    },
    "memory_recall": {
        "response_type": "memory.recall.results",
        "error_codes": [],
        "examples": [
            {
                "summary": "Recall relevant memories for a topic",
                "args": {"query": "how do I like commit messages", "deep": False},
            }
        ],
    },
    # ── personalclaw-workflows ───────────────────────────────────────────────
    "workflow_list": {
        "response_type": "workflow.list",
        "error_codes": [],
        "examples": [{"summary": "List saved workflows", "args": {"scope": "global"}}],
    },
    "workflow_get": {
        "response_type": "workflow.detail",
        "error_codes": [],
        "examples": [
            {"summary": "Read a workflow definition", "args": {"workflow_id": "weekly-digest"}}
        ],
    },
    "workflow_run": {
        "response_type": "workflow.run.result",
        "error_codes": [],
        "examples": [{"summary": "Run a saved workflow", "args": {"workflow_id": "weekly-digest"}}],
    },
    "workflow_create": {
        "response_type": "workflow.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Create a two-step workflow",
                "args": {
                    "name": "weekly-digest",
                    "steps": [{"tool": "knowledge_search", "args": {"query": "this week"}}],
                },
            },
        ],
    },
    "workflow_promote": {
        "response_type": "workflow.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Promote a session workflow to global scope",
                "args": {"workflow_id": "weekly-digest", "scope": "global"},
            }
        ],
    },
    "prompt_render": {
        "response_type": "prompt.render.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Render a saved prompt with variables",
                "args": {"prompt_id": "review", "vars": {"file": "server.py"}},
            },
        ],
    },
    # ── personalclaw-subagents ───────────────────────────────────────────────
    "subagent_run": {
        "response_type": "subagent.run.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Run one subagent task",
                "args": {"task": "Summarize the open PRs", "agent": "general-purpose"},
            },
        ],
    },
    "subagent_list": {
        "response_type": "subagent.list",
        "error_codes": [],
        "examples": [{"summary": "List running/finished subagents", "args": {}}],
    },
    "subagent_status": {
        "response_type": "subagent.status",
        "error_codes": [],
        "examples": [
            {"summary": "Check one subagent's status", "args": {"agent_id": "sub-abc123"}}
        ],
    },
    # ── personalclaw-inbox-tools ─────────────────────────────────────────────
    "post_to_inbox": {
        "response_type": "inbox.post.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Post a message to the user's inbox",
                "args": {"message": "PR #42 is ready for review", "kind": "notification"},
            },
        ],
    },
    # ── personalclaw-knowledge-tools ─────────────────────────────────────────
    "knowledge_search": {
        "response_type": "knowledge.search.results",
        "error_codes": [],
        "examples": [
            {
                "summary": "Search the knowledge base",
                "args": {"query": "deployment runbook", "limit": 5},
            }
        ],
    },
    "knowledge_create": {
        "response_type": "knowledge.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Save a note to the knowledge base",
                "args": {
                    "type": "note",
                    "title": "Deploy runbook",
                    "content": "1. npm ci\n2. npm run deploy",
                },
            },
        ],
    },
    "knowledge_get": {
        "response_type": "knowledge.detail",
        "error_codes": [],
        "examples": [{"summary": "Read a knowledge item by id", "args": {"id": "kn_abc123"}}],
    },
    "knowledge_update": {
        "response_type": "knowledge.detail",
        "error_codes": [],
        "examples": [
            {"summary": "Pin a knowledge item", "args": {"id": "kn_abc123", "is_pinned": True}}
        ],
    },
    "knowledge_stats": {
        "response_type": "knowledge.stats",
        "error_codes": [],
        "examples": [{"summary": "Get knowledge-base counts", "args": {}}],
    },
    # ── personalclaw-project-tools ───────────────────────────────────────────
    "project_run_create": {
        "response_type": "project.run.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Create a code project run",
                "args": {
                    "kind": "code",
                    "task": "Add a health endpoint to the API",
                    "name": "health-endpoint",
                },
            },
        ],
    },
    "project_run_start": {
        "response_type": "project.run.detail",
        "error_codes": [],
        "examples": [
            {"summary": "Start a created project run", "args": {"project_id": "prj_abc123"}}
        ],
    },
    "project_run_status": {
        "response_type": "project.run.status",
        "error_codes": [],
        "examples": [
            {"summary": "Check a project run's status", "args": {"project_id": "prj_abc123"}}
        ],
    },
    "project_run_list": {
        "response_type": "project.run.list",
        "error_codes": [],
        "examples": [
            {"summary": "List recent project runs", "args": {"kind": "code", "limit": 10}}
        ],
    },
    # ── personalclaw-tasks-tools ─────────────────────────────────────────────
    "task_create": {
        "response_type": "task.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Create a task",
                "args": {"title": "Write launch email", "priority": "high", "due": "2026-08-01"},
            },
        ],
    },
    "task_list": {
        "response_type": "task.list",
        "error_codes": [],
        "examples": [{"summary": "List open tasks", "args": {"status": "open", "limit": 20}}],
    },
    "task_get": {
        "response_type": "task.detail",
        "error_codes": [],
        "examples": [{"summary": "Read a task by id", "args": {"id": "tsk_abc123"}}],
    },
    "task_update": {
        "response_type": "task.detail",
        "error_codes": [],
        "examples": [
            {"summary": "Mark a task done", "args": {"id": "tsk_abc123", "status": "done"}}
        ],
    },
    "task_ready": {
        "response_type": "task.list",
        "error_codes": [],
        "examples": [
            {"summary": "List tasks whose dependencies are met", "args": {"project": "launch"}}
        ],
    },
    "task_search": {
        "response_type": "task.list",
        "error_codes": [],
        "examples": [
            {"summary": "Search tasks", "args": {"query": "email", "status": ["open"], "limit": 20}}
        ],
    },
    "project_create": {
        "response_type": "project.detail",
        "error_codes": [],
        "examples": [{"summary": "Create a project to group tasks", "args": {"name": "Launch"}}],
    },
    "project_list": {
        "response_type": "project.list",
        "error_codes": [],
        "examples": [{"summary": "List projects", "args": {}}],
    },
    "task_list_create": {
        "response_type": "task.list_container.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Create a task list inside a project",
                "args": {"name": "Backlog", "project_name": "Launch"},
            }
        ],
    },
}
