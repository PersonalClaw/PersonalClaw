"""What a shell command's ``{{secret:NAME}}`` is filled with, as the agent's ``bash`` tool runs it.

A credential is named, never written: the agent writes ``{{secret:NAME}}`` in a command, and the
``bash`` tool fills it in as the command runs (``builtin_tools._bash_command``, through
``triggers.secrets.resolve``). This module is what it is filled with, and what the agent is told
when it cannot be.

It is filled through the one resolver (``llm.credentials.resolve_secret``) with the project this
call's work belongs to (:func:`call_project`): that project's own secret first, then the global
one. Only what the owner stored is filled in, never the gateway's environment: here the agent
chooses the name, and in a sandbox tier the environment is exactly what the sandbox keeps from the
command. A setting's own key and a project's stored key are refused before any resolver is asked.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.triggers.secrets import UnresolvedSecret

logger = logging.getLogger(__name__)


def call_project() -> str:
    """The project this tool call's work belongs to, or ``""`` for none.

    A workflow step's agent is no project's session: its run's project rides in the step's
    lineage (``engine.leaf_spawn_env``, read through ``mcp_shared.leaf_value``), taken from the
    run's record. Any other session — a chat in a project, a loop's worker — binds its own project
    for the turn (``builtin_tools.bind_tool_context``).
    """
    from personalclaw.agents.native.builtin_tools import current_project_id
    from personalclaw.mcp_shared import leaf_value

    return leaf_value("__wf_project_id").strip() or current_project_id()


def stored_secret(key: str) -> str:
    """What a command's ``{{secret:KEY}}`` is filled with: a credential the owner stored in
    Settings → Secrets — this call's project's own first, then the global one — and nothing else,
    or ``""``.

    The command is told nothing about where a value came from; the log keeps the name and the
    scope, never the value.
    """
    from personalclaw.llm.credentials import resolve_secret

    project = call_project()
    try:
        read = resolve_secret(key, project_id=project, environment=False)
    except KeyError:
        return ""
    except Exception:  # noqa: BLE001 - an unreadable store fills nothing, and says so
        logger.debug("credential store unreadable while filling %r", key, exc_info=True)
        return ""
    logger.info(
        "bash: {{secret:%s}} filled in from the %s secrets%s",
        key,
        read.scope,
        f" of project {project}" if read.scope == "project" else "",
    )
    return read.secret


def unfilled_reference(missing: UnresolvedSecret) -> str:
    """The refusal for a command whose ``{{secret:KEY}}`` names nothing the owner stored, or a key
    nothing reads by name."""
    reference = "{{secret:" + missing.key + "}}"
    refused = missing.refused
    if refused is not None:
        return (
            f"The command refers to {reference}, but {getattr(refused, 'cause', '')}. To use a "
            f"secret here, {getattr(refused, 'remedy', '')}. Nothing was run."
        )
    return (
        f"The command refers to {reference}, and Settings → Secrets holds no credential by that "
        "name. Nothing was run."
    )
