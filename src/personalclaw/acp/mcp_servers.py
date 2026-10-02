"""The ``personalclaw-core`` MCP server rendered in ACP ``session/new`` wire shape.

Prong A. The native tool registry (knowledge / tasks /
loops / inbox / artifacts / workflows / subagents / web) reaches an ACP CLI only
through the ``personalclaw-core`` stdio MCP server. Before this module every live
``session/new`` sent ``"mcpServers": []``, so an ACP session had none of it — the
single largest capability cliff in the ACP parity audit (gap 1).

The server spec itself is NOT invented here: :data:`personalclaw.agent.
_MANAGED_MCP_SERVERS` is the one source of truth for what ``personalclaw-core``
is (command + args), and the kiro-targeted ``agents/personalclaw.json`` generator
already renders the same entry. This module only translates that spec into the
ACP protocol's shape and attaches the environment the server needs to answer as
*this* session.

Two shapes, one spec:

* the agent-config shape is a **mapping** keyed by server name
  (``{"personalclaw-core": {"command": ..., "args": [...]}}``);
* the ACP ``session/new`` shape is an **array** of objects that each carry their
  own ``name``, and whose ``env`` is an array of ``{"name", "value"}`` pairs.

Env is declared explicitly rather than relied upon by inheritance. The CLI starts
from the child allowlist (``transport.py``, ``sandbox.build_child_env``) and its MCP
children would normally inherit that in turn, but a CLI is free to spawn MCP servers
with a filtered environment. Two variables decide whether the server answers
correctly at all, so neither may be left to inheritance:

``PERSONALCLAW_HOME``
    ``mcp_core`` resolves ``config_dir()`` for the IPC secret, the gateway port
    file and the ``session_pid_<pid>.txt`` files. Losing it sends an
    isolated-home session's tool calls at the operator's real home.

``PERSONALCLAW_SESSION_KEY``
    the session inject-back. ``mcp_core._resolve_session_key`` prefers this env
    var and only falls back to walking the process tree for a
    ``session_pid_<pid>.txt`` file. Declaring it makes inject-back exact instead
    of dependent on an ancestor walk through the CLI's own process tree.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

CORE_SERVER_NAME = "personalclaw-core"

#: The kinds an ACP CLI gives a call to an MCP server's tool when it titles the call with the
#: tool's own name: claude-code's ``other``, or no kind at all. A call of any other kind is one
#: of the CLI's own tools, and its title is whatever the CLI chose — claude-code titles a shell
#: call's approval with the model's own description of the command — so only on one of these
#: kinds can a title be a tool name rather than prose.
_NAME_TITLED_KINDS = frozenset({"", "other"})


def _arguments(tool_input: object) -> dict[str, Any] | None:
    """A call's input as an object: ``{}`` when it carries none, ``None`` when it is unreadable.

    The permission frame carries the input as the JSON text the ``tool_call`` frame cached, the
    ``tool_call`` frame as the object itself; an edit tool's cached input is a unified diff.
    """
    if isinstance(tool_input, dict):
        return tool_input
    if not tool_input:
        return {}
    if isinstance(tool_input, str):
        try:
            value = json.loads(tool_input)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _core_call(title: str, tool_kind: str, tool_input: object) -> tuple[str, Any, bool]:
    """``(tool, arguments, exact)`` for a call that names a ``personalclaw-core`` tool.

    The three wire shapes, read from the adapters' own sources and the ``AAP-4`` drive:

    * claude-code — ``mcp__personalclaw-core__<tool>``, kind ``other``, the input is the
      tool's arguments;
    * codex — ``mcp.personalclaw-core.<tool>``, kind ``execute``, and the input is the
      structured ``{server, tool, arguments}`` codex builds for an MCP call, which a shell
      call never carries;
    * kiro-cli — ``Running: @personalclaw-core/<tool>``. Not ``exact``: kiro titles its shell
      calls ``Running: <command>``, so a command that happens to read
      ``@personalclaw-core/<tool>`` produces the same title.

    ``("", None, False)`` when the call names none of them.
    """
    title = title or ""
    kind = (tool_kind or "").lower()
    args = _arguments(tool_input)
    claude = f"mcp__{CORE_SERVER_NAME}__"
    if kind in _NAME_TITLED_KINDS and title.startswith(claude):
        return title[len(claude) :], args, True
    if kind == "execute" and args is not None and args.get("server") == CORE_SERVER_NAME:
        tool = args.get("tool")
        if isinstance(tool, str) and title == f"mcp.{CORE_SERVER_NAME}.{tool}":
            inner = args.get("arguments")
            return tool, ({} if inner is None else inner), True
    kiro = f"Running: @{CORE_SERVER_NAME}/"
    if title.startswith(kiro):
        return title[len(kiro) :], args, False
    return "", None, False


def core_tool_declaration(
    title: str, tool_kind: str, tool_input: object
) -> tuple[str, bool, bool, bool]:
    """``(risk_level, builds, proposes, tells_owner)`` for an ACP call to one of PersonalClaw's
    own tools.

    An ACP CLI declares nothing about its tools, so a call from one carries no declaration and
    is treated as a change. The exception is a call to the ``personalclaw-core`` server the
    host serves itself: those tools declare exactly what they do, so the call can carry the
    same declaration a native call does, and Ask mode, Trust reads and the card treat
    ``memory_recall`` as the read it is and ``artifact_delete`` as the delete. ``tells_owner``
    is whether THIS call does nothing but tell the owner something
    (``tool_providers.base.only_tells_the_owner``).

    The lookup is exact — the tool's own name, on the one server — and it takes the
    declaration only when the call cannot be something else wearing the name:

    * the call's arguments must all be ones the tool takes. A CLI tool whose title the model
      writes (claude-code's question tool titles itself with the question) cannot pass as
      ours by choosing its text;
    * on kiro-cli's title, which one of its shell calls can also produce, only a
      ``destructive`` declaration is taken. That one only adds a question; a read, a Build
      mode producer, a proposal or a notice to the owner would let the shell call through.

    ``("", False, False, False)`` for every other call.
    """
    from personalclaw import mcp_core
    from personalclaw.tool_providers.base import (
        BUILDS_META_KEY,
        PROPOSES_META_KEY,
        TELLS_OWNER_META_KEY,
        RiskLevel,
        only_tells_the_owner,
        risk_from_annotations,
    )

    name, args, exact = _core_call(title, tool_kind, tool_input)
    tool = mcp_core.own_tool(name) if name else None
    if tool is None:
        return "", False, False, False
    risk = risk_from_annotations(tool.get("annotations"), trusted=True)
    if not exact:
        return (risk.value if risk is RiskLevel.DESTRUCTIVE else ""), False, False, False
    schema = tool.get("inputSchema")
    takes = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(args, dict) or not set(args) <= set(takes if isinstance(takes, dict) else ()):
        return "", False, False, False
    meta = tool.get("_meta")
    meta = meta if isinstance(meta, dict) else {}
    notice_args = meta.get(TELLS_OWNER_META_KEY)
    tells_owner = isinstance(notice_args, list) and only_tells_the_owner(notice_args, args)
    return (
        risk.value,
        meta.get(BUILDS_META_KEY) is True,
        meta.get(PROPOSES_META_KEY) is True,
        tells_owner,
    )


def core_mcp_servers(
    *, session_key: str | None = None, env: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """Return the ACP ``mcpServers`` array carrying ``personalclaw-core``.

    ``session_key`` is the live session key (``AcpClient._session_key`` /
    ``SessionManager``'s key). It is read at call time, not at construction time,
    because the pool rekeys a warm process between sessions — a spec captured in
    ``__init__`` would pin the first session's key onto every later one.

    ``env`` is the session's own environment (``AcpClient``'s ``extra_env``): what in it tells the
    server's tools which leaf they run for and which tier holds it (``mcp_shared.LEAF_KEYS``) is
    declared to the server as well, since a CLI is free to start it without the environment the
    CLI itself was given, and without the tier the server would list a read-only subagent every
    tool.

    Returns an empty list when the spec cannot be rendered, which reproduces the
    pre-AAP-4 behaviour rather than failing a session open.
    """
    from personalclaw.agent import _MANAGED_MCP_SERVERS

    spec = _MANAGED_MCP_SERVERS.get(CORE_SERVER_NAME)
    if not spec:  # pragma: no cover - defensive; the entry is a module constant
        return []
    command = spec.get("command") or spec["command_fn"]()
    if not command:  # pragma: no cover - _resolve_personalclaw_bin always returns a str
        return []

    from personalclaw.config import config_dir

    declared: list[dict[str, str]] = [{"name": "PERSONALCLAW_HOME", "value": str(config_dir())}]
    # The gateway's PORT, declared rather than assumed — and asked of the ONE owner of that
    # answer (#2539). This read ``parse_dashboard_url(dashboard.url)``, which falls back to a
    # fixed 10000: a gateway started with ``--port`` (or the ``--port auto`` that
    # ``--test-mode`` uses) declared **10000** to its MCP child, and on a multi-instance host
    # 10000 is a DIFFERENT instance's gateway, not a dead socket. Earlier symptom on a kiro ACP
    # session (`K58`): every HTTP-bridged core tool answered ``<urlopen error [Errno 61]
    # Connection refused>`` while the in-process tools beside it worked.
    #
    # If the base cannot be resolved we declare NOTHING rather than a guess — the child then
    # refuses loudly at its first tool call, naming the cause, instead of quietly addressing a
    # stranger. Unreachable in practice: a running gateway has published its bound port.
    from personalclaw import gateway_base

    try:
        declared.append({"name": "PERSONALCLAW_PORT", "value": str(gateway_base.resolve_port())})
    except gateway_base.GatewayBaseUnresolved as exc:
        logger.warning("not declaring PERSONALCLAW_PORT to %s: %s", CORE_SERVER_NAME, exc)
    if session_key:
        declared.append({"name": "PERSONALCLAW_SESSION_KEY", "value": str(session_key)})
    from personalclaw.mcp_shared import leaf_lineage

    declared.extend({"name": key, "value": value} for key, value in leaf_lineage(env).items())

    return [
        {
            "name": CORE_SERVER_NAME,
            "command": str(command),
            "args": [str(a) for a in spec.get("args") or []],
            "env": declared,
        }
    ]
