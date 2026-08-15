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
    "/login": (
        "the owner sign-in page (REMOTE-USER-AUTH C3) — a rendered HTML form for a "
        "HUMAN browser, UI transport. The agent-callable surface is POST "
        "/api/auth/login, which IS in the manifest"
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
    "get_context": {
        "response_type": "context.routed.manifest",
        "error_codes": [],
        "examples": [
            {
                "summary": "Load the current project's routed context at task start",
                "args": {},
            },
            {
                "summary": "Score the context against the task at hand",
                "args": {"query": "add a settings toggle", "project_id": "p-1a2b3c4d"},
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
    "template_save_from_session": {
        "response_type": "template.save.proposal.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Propose the session's procedure as a reusable template (draft only)",
                "args": {
                    "name": "nightly-report",
                    "description": "Build and publish the nightly report",
                    "steps": [
                        "fetch {{source_url}} and validate the payload",
                        "transform the result into {{format}}",
                        "publish it to {{target}} and verify the output",
                    ],
                },
            },
        ],
    },
    "project_context_review": {
        "response_type": "project.context.review.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Propose a project instruction from what this chat established",
                "args": {
                    "items": [
                        {
                            "kind": "project_instruction",
                            "body": "Always run `make lint` before committing.",
                            "rationale": "We agreed lint must pass pre-commit",
                        }
                    ]
                },
            },
        ],
    },
    "skill_promote": {
        "response_type": "skill.promote.proposal.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Promote a completed run into a skill proposal (nothing written)",
                "args": {
                    "name": "publish the nightly report",
                    "description": "Build and publish the nightly report end to end",
                    "procedure": (
                        "1. Fetch the source feed and validate the payload.\n"
                        "2. Render the report.\n"
                        "3. Publish it and verify the published copy."
                    ),
                    "rationale": "We worked this out from scratch and it will recur nightly",
                    "run_id": "run-2f8a1c",
                },
            },
        ],
    },
    "dashboard_tile_propose": {
        "response_type": "dashboard.tile.propose.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Propose a saved dashboard artifact as a tile on the home",
                "args": {"slug": "sales-live-board", "size": "l"},
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
    "suggest_template": {
        "response_type": "template.nudge.decision",
        # Empty like every sibling planning tool: its refusals are readable text carrying a
        # bracketed code, not `errors.ERROR_CODES` entries (that registry is `ERR_*` and
        # append-only — a text code does not belong in it).
        "error_codes": [],
        "examples": [
            {
                "summary": "Count a recurring shape and ask whether to offer a template",
                "args": {"shape": "summarize new issues"},
            },
            {
                "summary": "Record that the user refused — permanent for this shape",
                "args": {"shape": "summarize new issues", "decision": "declined"},
            },
        ],
    },
    "refiner_evidence": {
        "response_type": "refiner.evidence",
        "error_codes": [],
        "examples": [
            {
                "summary": "Read a template's clustered, screened failure evidence",
                "args": {"workflow_name": "code-project"},
            },
        ],
    },
    "propose_template_diff": {
        "response_type": "refiner.proposal.result",
        # Refusals (empty diff, illegal/frozen ops) are readable text with a bracketed code,
        # not `errors.ERROR_CODES` entries — same convention as the planning tools above.
        "error_codes": [],
        "examples": [
            {
                "summary": "Propose a typed diff to a template, citing the runs that motivate it",
                "args": {
                    "workflow_name": "code-project",
                    "ops": [{"op": "update_node", "node_id": "build", "fields": {"retries": 2}}],
                    "rationale": "The build step fails transiently; a retry clears it.",
                    "run_ids": ["r1", "r2", "r3"],
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
    "document_create": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Export an existing knowledge item as a Word document",
                "args": {"name": "Saved research", "source": "<knowledge item id>"},
            },
            {
                "summary": "Turn markdown into a downloadable Word document",
                "args": {
                    "name": "Q3 Review",
                    "markdown": "# Q3 Review\n\nRevenue grew.\n\n- EMEA up 18%\n",
                },
            },
        ],
    },
    "sheet_create": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Build a spreadsheet with numbers kept numeric",
                "args": {
                    "name": "Regional sales",
                    "sheets": {"Sales": [["Region", "Q1"], ["EMEA", 120]]},
                },
            },
        ],
    },
    "deck_create": {
        "response_type": "artifact.detail",
        "error_codes": [],
        "examples": [
            {
                "summary": "Turn a markdown outline into a PowerPoint deck",
                "args": {
                    "name": "Q3 Strategy",
                    "markdown": "# Q3 Strategy\n\n## Where we are\n\n- Revenue up 18%\n",
                },
            },
        ],
    },
    "document_formats": {
        "response_type": "text",
        "error_codes": [],
        "examples": [{"summary": "Check which formats are available", "args": {}}],
    },
    "visualize": {
        "response_type": "genui.widget",
        "error_codes": [],
        "examples": [
            {
                "summary": "Render monthly totals as a bar chart",
                "args": {
                    "data": {"Jan": 120, "Feb": 150, "Mar": 180},
                    "hint": "show as a bar chart of monthly totals",
                },
            },
        ],
    },
    # ── personalclaw-prompts ─────────────────────────────────────────────────
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
    # ── personalclaw-workflows ───────────────────────────────────────────────
    "workflow_author": {
        "response_type": "workflow.def.saved",
        "error_codes": [],
        "examples": [
            {
                "summary": "Validate a two-stage spec without saving it",
                "args": {
                    "name": "triage-inbox",
                    "root": {
                        "kind": "sequence",
                        "id": "main",
                        "children": [
                            {
                                "kind": "infer",
                                "id": "classify",
                                "config": {"prompt": "Classify: {{inputs.text}}"},
                            }
                        ],
                    },
                    "save": False,
                },
            },
        ],
    },
    "workflow_plan": {
        "response_type": "workflow.plan.draft",
        "error_codes": [],
        "examples": [
            {
                "summary": "Draft a plan from a goal",
                "args": {"goal": "summarize new issues each morning", "rigor": "standard"},
            },
        ],
    },
    "workflow_list_defs": {
        "response_type": "workflow.def.list",
        "error_codes": [],
        "examples": [{"summary": "List every workflow definition", "args": {}}],
    },
    "workflow_get_def": {
        "response_type": "workflow.def.detail",
        "error_codes": [],
        "examples": [{"summary": "Read one definition", "args": {"name": "triage-inbox"}}],
    },
    "workflow_start": {
        "response_type": "workflow.run.started",
        "error_codes": [],
        "examples": [
            {
                "summary": "Start a run in the background",
                "args": {"name": "triage-inbox", "inputs": {"since": "1h"}},
            },
        ],
    },
    "workflow_status": {
        "response_type": "workflow.run.status",
        "error_codes": [],
        "examples": [{"summary": "Check a run", "args": {"run_id": "a1b2c3d4"}}],
    },
    "workflow_observe": {
        "response_type": "workflow.run.delta",
        "error_codes": [],
        "examples": [
            {
                "summary": "Watch a run for three seconds",
                "args": {"run_id": "a1b2c3d4", "duration_ms": 3000},
            },
        ],
    },
    "workflow_edit": {
        "response_type": "workflow.mutation.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Preview what editing a pending prompt would re-run",
                "args": {
                    "run_id": "a1b2c3d4",
                    "ops": [
                        {
                            "op": "update_node",
                            "node_id": "produce",
                            "fields": {"prompt": "Be concise."},
                        }
                    ],
                    "preview_only": True,
                },
            },
        ],
    },
    "workflow_skip": {
        "response_type": "workflow.mutation.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Skip a pending node",
                "args": {"run_id": "a1b2c3d4", "node_ids": ["optional_review"]},
            },
        ],
    },
    "workflow_rewind": {
        "response_type": "workflow.mutation.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Re-run a stage and everything reading its output",
                "args": {"run_id": "a1b2c3d4", "node_id": "produce"},
            },
        ],
    },
    "workflow_run_from": {
        "response_type": "workflow.mutation.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Redo only what follows a node",
                "args": {"run_id": "a1b2c3d4", "node_id": "gather"},
            },
        ],
    },
    "workflow_fork": {
        "response_type": "workflow.fork.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Branch a run to try an alternative",
                "args": {"run_id": "a1b2c3d4", "note": "stricter judge"},
            },
        ],
    },
    "workflow_pause": {
        "response_type": "workflow.run.paused",
        "error_codes": [],
        "examples": [{"summary": "Pause a run", "args": {"run_id": "a1b2c3d4"}}],
    },
    "workflow_resume": {
        "response_type": "workflow.gate.resolved",
        "error_codes": [],
        "examples": [
            {
                "summary": "Approve a waiting gate",
                "args": {"run_id": "a1b2c3d4", "answer": True},
            },
        ],
    },
    "workflow_cancel": {
        "response_type": "workflow.run.cancelled",
        "error_codes": [],
        "examples": [{"summary": "Cancel a run", "args": {"run_id": "a1b2c3d4"}}],
    },
    "workflow_output": {
        "response_type": "workflow.node.output",
        "error_codes": [],
        "examples": [
            {
                "summary": "Read a node's output",
                "args": {"run_id": "a1b2c3d4", "node_id": "produce"},
            },
        ],
    },
    "workflow_audit": {
        "response_type": "workflow.audit.report",
        "error_codes": [],
        "examples": [{"summary": "Report drifted runs without repairing", "args": {}}],
    },
    "workflow_manifest": {
        "response_type": "workflow.manifest",
        "error_codes": [],
        "examples": [{"summary": "Get the authoring reference", "args": {}}],
    },
    "workflow_delete_def": {
        "response_type": "workflow.def.deleted",
        "error_codes": [],
        "examples": [{"summary": "Delete a definition", "args": {"name": "old-workflow"}}],
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
    "triage_rules": {
        # PA-1: the taught approve/deny rules the proactive digest consults. `suppressed`
        # is not an assertable verdict (a cooldown is derived from declines), so the
        # examples only show what a caller may actually write.
        "response_type": "memory.triage_rules",
        "error_codes": [],
        "examples": [
            {"summary": "List the taught triage rules", "args": {"action": "list"}},
            {
                "summary": "Always approve archiving newsletters",
                "args": {
                    "action": "add",
                    "pattern": "archive:newsletter",
                    "verdict": "approve",
                },
            },
            {
                "summary": "Revoke a rule by id",
                "args": {"action": "revoke", "id": "user.approval.archive:newsletter"},
            },
        ],
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
    # ── personalclaw-ui-docs ─────────────────────────────────────────────────
    "code_map": {
        "response_type": "code.map.symbol",
        "error_codes": [],
        "examples": [
            {
                "summary": "Find where a function is defined and what calls it",
                "args": {"symbol": "parse_source"},
            },
            {
                "summary": "Outline one file's imports and definitions",
                "args": {"file": "src/personalclaw/codegraph/parse.py"},
            },
            {
                "summary": "Re-index a tree changed outside the session, then look up",
                "args": {"symbol": "CodeGraphIndex", "refresh": True},
            },
        ],
    },
    "code_map_overview": {
        "response_type": "code.map.overview",
        "error_codes": [],
        "examples": [
            {
                "summary": "Get the shape of an unfamiliar codebase before exploring it",
                "args": {},
            },
        ],
    },
    "ui_search": {
        "response_type": "ui.search.results",
        "error_codes": [],
        "examples": [
            {
                "summary": "Find the design-system primitive for a labelled action",
                "args": {"query": "button submit", "limit": 5},
            },
            {
                "summary": "Search for a design token",
                "args": {"query": "primary color"},
            },
        ],
    },
    "ui_get": {
        "response_type": "ui.get.doc",
        "error_codes": [],
        "examples": [
            {
                "summary": "Read a component's full props + best practices",
                "args": {"name": "SidePanel"},
            },
            {
                "summary": "Read just one section of a component's doc",
                "args": {"name": "Button", "section": "props"},
            },
        ],
    },
    # ── personalclaw-automation (== mcp_automation._list_tools) ───────────────
    "automation_create": {
        "response_type": "automation.create.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Create a file-watch automation in one message",
                "args": {
                    "name": "Summarize notes",
                    "when": "when a file in ~/notes changes",
                    "message": "Summarize the changed file into my knowledge base",
                },
            },
            {
                "summary": "Create a scheduled automation",
                "args": {"name": "Daily digest", "when": "every weekday at 9", "message": "digest"},
            },
        ],
    },
    "automation_list": {
        "response_type": "automation.list.result",
        "error_codes": [],
        "examples": [
            {"summary": "List all automations with health", "args": {}},
            {
                "summary": "List only active file automations",
                "args": {"kind": "file", "state": "active"},
            },
        ],
    },
    "automation_update": {
        "response_type": "automation.update.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Rename an automation",
                "args": {"id": "file:summarize-notes", "patch": {"name": "Notes summarizer"}},
            },
        ],
    },
    "automation_pause": {
        "response_type": "automation.pause.result",
        "error_codes": [],
        "examples": [
            {"summary": "Pause an automation", "args": {"id": "file:summarize-notes"}},
        ],
    },
    "automation_resume": {
        "response_type": "automation.resume.result",
        "error_codes": [],
        "examples": [
            {"summary": "Resume a paused automation", "args": {"id": "file:summarize-notes"}},
        ],
    },
    "automation_run": {
        "response_type": "automation.run.result",
        "error_codes": [],
        "examples": [
            {"summary": "Fire an automation now", "args": {"id": "file:summarize-notes"}},
            {
                "summary": "Preview what would run without executing",
                "args": {"id": "file:summarize-notes", "dry_run": True},
            },
        ],
    },
    "automation_history": {
        "response_type": "automation.history.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Recent runs of an automation",
                "args": {"id": "file:summarize-notes", "n": 10},
            },
        ],
    },
    "automation_delete": {
        "response_type": "automation.delete.result",
        "error_codes": [],
        "examples": [
            {
                "summary": "Delete an automation permanently",
                "args": {"id": "file:summarize-notes", "confirm": True},
            },
        ],
    },
    "automation_delete_all": {
        "response_type": "automation.delete_all.result",
        "error_codes": [],
        "examples": [
            {
                # No `created_by` in the example because there is no such PARAMETER (S109): the
                # scope is the caller's identity, so an example showing one would advertise an
                # argument that would let an agent delete the user's own automations.
                "summary": "Delete every automation you created",
                "args": {"confirm": True},
            },
        ],
    },
}
