"""Exporting your agents into Claude Code's own agents folder — ``POST /api/agents/export``.

Thin over :mod:`personalclaw.packs.external_formats`, which owns every rail: the write needs a
confirmed destination, a rendered file can never land outside it, a credential in an agent's
text blocks the whole export, and a file PersonalClaw did not write is never overwritten.

The route adds the two answers only the gateway has:

* **Which agents are yours.** The agents in your config, minus the built-ins that run the
  platform and the default agent, whose instructions are the prompt bound in Settings rather
  than its own. A name that is not one of yours is refused, never skipped: a silently smaller
  export is an export that looks like it worked.
* **Where Claude Code reads its agents from.** Worked out here, from ``$CLAUDE_CONFIG_DIR`` as
  Claude Code itself does (:func:`~personalclaw.packs.external_formats.default_dest_dir`), and
  never taken from the request. The confirming request names the folder it was shown, and one
  that is not the folder now is refused, so the only folder that can be written is the one the
  owner saw.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body
from personalclaw.safety_flags import confirm_granted

if TYPE_CHECKING:
    from personalclaw.packs.external_formats import ExportResult

logger = logging.getLogger(__name__)


def _clip(name: str) -> str:
    """A name from the request, short enough to echo back in a sentence and an audit row."""
    return name if len(name) <= 63 else name[:60] + "…"


def _agents_problem(cfg: Any, names: list[str]) -> str:
    """Why ``names`` cannot all be exported, or ``""`` when they can."""
    from personalclaw.agents.defaults import is_default_agent, is_reserved_agent

    unknown = sorted({_clip(n) for n in names if n not in cfg.agents})
    own = sorted(
        {
            _clip(n)
            for n in names
            if n in cfg.agents and (is_reserved_agent(n) or is_default_agent(n))
        }
    )
    problems = []
    if unknown:
        problems.append(f"no agent of yours is named {', '.join(unknown)}")
    if own:
        one = len(own) == 1
        problems.append(
            f"{', '.join(own)} {'is' if one else 'are'} PersonalClaw's own and "
            f"stay{'s' if one else ''} with it"
        )
    return f"Nothing was exported: {'; '.join(problems)}." if problems else ""


def _exported_sentence(result: ExportResult) -> str:
    """The one sentence the Agents page shows once an export is written. Composed here, and shown
    verbatim: which files changed and which were already there is the server's to say."""
    written = ", ".join(p.name for p in result.written)
    unchanged = ", ".join(p.name for p in result.unchanged)
    if not result.written:
        return (
            f"Nothing changed in {result.dest_dir}: every agent was already there as exported "
            f"({unchanged})."
        )
    count = len(result.written)
    sentence = f"Exported {count} agent{'' if count == 1 else 's'} to {result.dest_dir}: {written}."
    if result.unchanged:
        sentence += f" Already there and unchanged: {unchanged}."
    return sentence


def _audit(*, outcome: str, resources: str, error: str = "") -> None:
    from personalclaw.sel import sel

    sel().log_api_access(
        caller="dashboard",
        operation="agent.export",
        outcome=outcome,
        source="dashboard",
        resources=resources,
        error=error,
    )


async def api_agents_export(request: web.Request) -> web.Response:
    """POST /api/agents/export — write your agents into Claude Code's agents folder, once confirmed.

    Body ``{"agents": [<names>], "confirm": true, "dest": "<the folder the plan named>"}``.
    Without ``confirm`` nothing is written: the answer is the plan — ``dest``, each file with
    what writing it would do there (``new``, ``same``, ``replace`` or ``theirs``) and the agents
    in it, the files a credential holds back (``blocked``), and ``refusal``: the sentence the
    write would refuse with, or ``null`` when it would go ahead. With ``confirm: true`` it
    writes, and answers what it wrote and the sentence to show.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.packs.external_formats import (
        CLAUDE_CODE_AGENTS,
        ExportBlocked,
        ExportClobberRefused,
        ExportRefused,
        ExportWriteFailed,
        agent_from_profile,
        default_dest_dir,
        export_entities,
        export_preview,
    )

    body = await json_object_body(request)
    names = body.get("agents")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
        return json_error(
            "agent_export_agents_invalid",
            message="Name the agents to export: `agents` is a list of your agents' names.",
            status=400,
        )
    cfg = AppConfig.load()
    problem = _agents_problem(cfg, names)
    if problem:
        return json_error("agent_export_agents_invalid", message=problem, status=400)
    wanted = set(names)
    # In the order the Agents page lists them, so a file list reads the way the page does.
    entities = [agent_from_profile(n, p) for n, p in cfg.agents.items() if n in wanted]
    dest = default_dest_dir(CLAUDE_CODE_AGENTS)
    assert dest is not None  # Claude Code's agents folder is home-anchored, so it always resolves

    if not confirm_granted(body):
        try:
            plan = export_preview(CLAUDE_CODE_AGENTS, entities, dest)
        except ExportRefused as exc:
            return json_error("agent_export_refused", message=str(exc), status=400)
        return web.json_response({"ok": True, **plan})

    if str(body.get("dest") or "") != str(dest):
        return json_error(
            "agent_export_dest_changed",
            message=(
                f"This export was not confirmed for {dest}, the folder Claude Code reads its "
                "agents from now, so nothing was written. Review the export and confirm again."
            ),
            status=409,
        )
    listed = ", ".join(sorted(wanted))
    try:
        result = export_entities(CLAUDE_CODE_AGENTS, entities, dest, confirm_dest=True)
    except ExportClobberRefused as exc:
        _audit(outcome="denied", resources=f"{dest}: {listed}", error="a file in the way")
        return json_error("agent_export_would_overwrite", message=str(exc), status=409)
    except ExportBlocked as exc:
        _audit(outcome="denied", resources=f"{dest}: {listed}", error="a credential in the text")
        return json_error("agent_export_blocked", message=str(exc), status=409)
    except ExportRefused as exc:
        return json_error("agent_export_refused", message=str(exc), status=400)
    except ExportWriteFailed as exc:
        logger.warning("agent export stopped part-way: %s", exc)
        _audit(outcome="error", resources=f"{dest}: {listed}", error=str(exc)[:200])
        return json_error("agent_export_write_failed", message=str(exc), status=500)
    changed = ", ".join(p.name for p in result.written) or "nothing changed"
    _audit(outcome="ok", resources=f"{dest}: {changed}")
    return web.json_response(
        {
            "ok": True,
            "dest": str(result.dest_dir),
            "written": [str(p) for p in result.written],
            "unchanged": [str(p) for p in result.unchanged],
            "message": _exported_sentence(result),
        }
    )
