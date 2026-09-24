"""The ``personalclaw-core`` MCP server rendered in ACP ``session/new`` wire shape.

Prong A of ACP-AGENT-PARITY §2.1. The native tool registry (knowledge / tasks /
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

Env is declared explicitly rather than relied upon by inheritance. The CLI
inherits the gateway's environment (``transport.py`` spawns with ``{**os.environ}``)
and its MCP children would normally inherit that in turn, but a CLI is free to
spawn MCP servers with a filtered environment. Two variables decide whether the
server answers correctly at all, so neither may be left to inheritance:

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

import logging
from typing import Any

logger = logging.getLogger(__name__)

CORE_SERVER_NAME = "personalclaw-core"


def core_mcp_servers(*, session_key: str | None = None) -> list[dict[str, Any]]:
    """Return the ACP ``mcpServers`` array carrying ``personalclaw-core``.

    ``session_key`` is the live session key (``AcpClient._session_key`` /
    ``SessionManager``'s key). It is read at call time, not at construction time,
    because the pool rekeys a warm process between sessions — a spec captured in
    ``__init__`` would pin the first session's key onto every later one.

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

    env: list[dict[str, str]] = [{"name": "PERSONALCLAW_HOME", "value": str(config_dir())}]
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
        env.append({"name": "PERSONALCLAW_PORT", "value": str(gateway_base.resolve_port())})
    except gateway_base.GatewayBaseUnresolved as exc:
        logger.warning("not declaring PERSONALCLAW_PORT to %s: %s", CORE_SERVER_NAME, exc)
    if session_key:
        env.append({"name": "PERSONALCLAW_SESSION_KEY", "value": str(session_key)})

    return [
        {
            "name": CORE_SERVER_NAME,
            "command": str(command),
            "args": [str(a) for a in spec.get("args") or []],
            "env": env,
        }
    ]
