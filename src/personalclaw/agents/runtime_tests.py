"""An agent runtime's readiness, read without starting it — and the Test that does start it.

PersonalClaw never starts another agent's CLI unless the user asks for that specific action. So
an ``acp:<cli>`` runtime's readiness comes from two things that need nothing run:

* whether its CLI is installed — :meth:`AgentProvider.presence`, which reads PATH and the paths
  its app declared;
* what the user's last Test of it found, and when — the record this module keeps.

A runtime that is installed and that the user has never tested reads ``untested``, and says so.

:func:`run_test` is the ONE path that starts the CLI, and only the user reaches it: the Test on
the runtime's card (``POST /api/agent-providers/{id}/test``) and ``personalclaw doctor
--start-agent-clis``. It starts the CLI once — ACP ``initialize`` and one empty session — records
the answer, and the agents the runtime offered in that session, then stops it. Those agents are
what the chat's agent picker lists for the runtime until the next Test. A Test that could not list
them keeps why, and a reader says so, rather than showing a runtime that offers none.

The record is one small JSON file per runtime beside the runner sidecars in ``agent-metadata/``
(which lists only ``*.md`` as routing notes), and it names the command it tested. A record for a
different command — the app was updated, the CLI was moved — says nothing about this one, so it
reads as not tried rather than as an old verdict on another program.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.agents.provider import DiscoveredAgent, ReadinessStatus

logger = logging.getLogger(__name__)

__all__ = [
    "discovery_failure",
    "last_test",
    "readiness",
    "record_path",
    "run_test",
    "runtime_label",
    "tested_agents",
]

_RECORD_SUFFIX = ".runtime-test.json"
_UNSAFE_NAME = re.compile(r"[^a-z0-9_-]+")

#: The one Test per runtime in flight on the running loop: a second press while the first is
#: still starting the CLI waits for that answer instead of starting the CLI a second time.
_running: dict[str, "asyncio.Task[dict[str, Any]]"] = {}


def record_path(runtime_id: str) -> Path:
    """Where the last Test of *runtime_id* is kept (the file need not exist)."""
    from personalclaw import agent_metadata

    slug = _UNSAFE_NAME.sub("-", runtime_id.lower()).strip("-") or "runtime"
    return agent_metadata.metadata_dir() / f"{slug}{_RECORD_SUFFIX}"


def _command_of(entry: Any) -> list[str]:
    command = dict(entry.options or {}).get("command")
    return [str(part) for part in command] if isinstance(command, list) else []


def last_test(entry: Any) -> dict[str, Any] | None:
    """What the last Test of *entry* found, for the command it runs now, else ``None``.

    Read-only and tolerant: a missing, unreadable or foreign record reads as "not tried". That
    is the restrictive answer here, not the permissive one — it offers a Test rather than
    presenting a verdict nobody can vouch for.
    """
    path = record_path(entry.name)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("agent runtime test record %s is unreadable; reading it as not tried", path)
        return None
    if not isinstance(raw, dict) or raw.get("runtime_id") != entry.name:
        return None
    if raw.get("command") != _command_of(entry):
        return None
    return raw


def _provider_class(entry: Any) -> Any:
    from personalclaw.agents.registry import get_agent_provider_class

    return get_agent_provider_class("acp" if entry.type == "acp_agent" else entry.type)


def _fields(status: "ReadinessStatus", tested_at: str | None) -> dict[str, Any]:
    return {
        "ready": bool(status.ready),
        "state": status.state,
        "detail": status.detail,
        "login_command": list(status.login_command) if status.login_command else None,
        "tested_at": tested_at,
    }


def readiness(entry: Any) -> dict[str, Any]:
    """*entry*'s readiness as a listing shows it — ``{ready, state, detail, login_command,
    tested_at}`` — starting NOTHING.

    What is on disk now wins over the record: a CLI that is gone reads ``not_found`` whatever
    an earlier Test said. An installed CLI reads its last Test for the command it runs now, or
    ``untested`` with ``tested_at: null`` when there is none.
    """
    from personalclaw.agents.provider import ReadinessStatus

    cls = _provider_class(entry)
    if cls is None:
        detail = f"no agent provider registered for {entry.type!r}"
        return _fields(ReadinessStatus(ready=False, state="error", detail=detail), None)
    present = cls.presence(dict(entry.options or {}))
    if present.state != "untested":
        return _fields(present, None)
    last = last_test(entry)
    if last is None:
        return _fields(present, None)
    login = last.get("login_command")
    return {
        "ready": bool(last.get("ready")),
        "state": str(last.get("state") or "error"),
        "detail": str(last.get("detail") or ""),
        "login_command": [str(p) for p in login] if isinstance(login, list) and login else None,
        "tested_at": str(last.get("tested_at") or "") or None,
    }


def tested_agents(entry: Any) -> list[dict[str, Any]] | None:
    """The agents *entry* offered at its last Test, or ``None`` when that is not known.

    ``[]`` is a Test that opened a session and read that the runtime offers none. ``None`` is
    no Test at all, or one that did not list them (:func:`discovery_failure` says why): unknown,
    which a reader must never show as "none".
    """
    last = last_test(entry)
    if last is None:
        return None
    agents = last.get("agents")
    return [a for a in agents if isinstance(a, dict)] if isinstance(agents, list) else None


def discovery_failure(entry: Any) -> str | None:
    """Why *entry*'s last Test did not list the agents it offers, or ``None``.

    ``None`` when it has not been tested (nothing failed; it is not tried yet) or when the Test
    listed them. The reason completes "its last Test …".
    """
    last = last_test(entry)
    if last is None or isinstance(last.get("agents"), list):
        return None
    return str(last.get("discovery_error") or "did not list them")


def runtime_label(entry: Any) -> str:
    """A display label for a runtime ("Claude Code", "Codex"), for the agents it lists.

    Title-cases the ``acp:<cli>`` suffix (``claude-code`` → ``Claude Code``). Display polish
    lives at this presentation layer; the backend stays neutral."""
    name = str(entry.name or "")
    cli = name.split(":", 1)[-1] if ":" in name else name
    return " ".join(w.capitalize() for w in cli.replace("_", "-").split("-") if w) or cli


def _record(
    entry: Any,
    status: "ReadinessStatus",
    agents: list["DiscoveredAgent"] | None,
    discovery_error: str,
) -> dict[str, Any]:
    """Write the Test's answer. ``agents`` is ``None`` when the Test did not list them, and then
    ``discovery_error`` says why: a failure is kept as a failure, never as an empty list."""
    from personalclaw.atomic_write import atomic_write

    tested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "runtime_id": entry.name,
        "command": _command_of(entry),
        "tested_at": tested_at,
        **_fields(status, tested_at),
        "agents": None if agents is None else [asdict(agent) for agent in agents],
        "discovery_error": discovery_error,
    }
    atomic_write(record_path(entry.name), json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


async def _test(entry: Any) -> dict[str, Any]:
    from personalclaw.agents.provider import ReadinessStatus

    cls = _provider_class(entry)
    if cls is None:
        return readiness(entry)
    options = dict(entry.options or {})
    # Nothing to start: an absent CLI is a fact about now, not a Test result, so it is not
    # recorded — once the CLI is installed its card must read "not tried", not this.
    if cls.presence(options).state != "untested":
        return readiness(entry)
    try:
        status = await cls.probe_readiness(options)
    except Exception as exc:  # noqa: BLE001 - a Test reports its failure, it never raises
        logger.debug("agent runtime test failed for %s", entry.name, exc_info=True)
        status = ReadinessStatus(ready=False, state="error", detail=f"probe failed: {exc}")
    if status.state == "not_found":
        # The probe found it could not start the CLI at all (e.g. no Node for its adapter):
        # also a fact about now, reported and not recorded.
        return _fields(status, None)
    agents: list[DiscoveredAgent] | None = None
    discovery_error = ""
    if not status.ready:
        discovery_error = f"did not start it cleanly ({status.state}): {status.detail}"
    elif not status.session_snapshot:
        discovery_error = "opened no session whose answer lists them"
    else:
        try:
            agents = cls.agents_from_snapshot(
                {**options, "runtime_id": entry.name, "runtime_label": runtime_label(entry)},
                status.session_snapshot,
            )
        except Exception as exc:  # noqa: BLE001 - an answer it cannot read is a failure, said so
            logger.debug("runtime test: unreadable snapshot for %s", entry.name, exc_info=True)
            discovery_error = f"got an answer that could not be read: {exc}"
    try:
        record = _record(entry, status, agents, discovery_error)
    except Exception:  # noqa: BLE001 - the answer still reaches the user who asked
        logger.warning("agent runtime test of %s ran but could not be recorded", entry.name)
        return _fields(status, None)
    return _fields(status, str(record["tested_at"]))


async def run_test(entry: Any) -> dict[str, Any]:
    """Start *entry*'s CLI once, because the user asked, and record what that found.

    Returns *entry*'s readiness in :func:`readiness`'s shape, now carrying this Test's answer.
    Never raises. Call it ONLY for a Test the user started (see the module docstring).
    """
    loop = asyncio.get_running_loop()
    running = _running.get(entry.name)
    # A Test stranded on a loop that has since closed never reports done.
    if running is None or running.done() or running.get_loop() is not loop:
        running = loop.create_task(_test(entry))
        _running[entry.name] = running
    return await asyncio.shield(running)
