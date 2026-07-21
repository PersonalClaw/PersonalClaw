"""Register an app's declared MCP servers into the live MCP config.

An app may ship its OWN MCP server(s) in ``manifest.mcpServers`` (distinct from
``dependencies`` MCP servers it merely needs). Those were parsed but never wired
into the running system. This bridge writes them into PClaw's MCP store
(``~/.personalclaw/mcp.json`` ``mcpServers`` — the same file
:mod:`providers.mcp_instances` and :mod:`mcp_client` read) on enable/install, and
removes them on disable/uninstall.

Entries are namespaced ``{app}:{server}`` so two apps (or an app and the user)
can't collide on a server key, and so deregistration removes exactly this app's
servers and nothing else.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.apps.manifest import AppManifest
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_NS_SEP = ":"  # app-name and server-name are kebab-case; ':' can't appear in either


def _mcp_json_path() -> Path:
    return config_dir() / "mcp.json"


def _load() -> dict[str, Any]:
    path = _mcp_json_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        logger.warning("mcp.json unreadable; treating as empty", exc_info=True)
        return {}


def _save(data: dict[str, Any]) -> None:
    from personalclaw.atomic_write import atomic_write

    path = _mcp_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o600)


def _ns(app_name: str, server: str) -> str:
    return f"{app_name}{_NS_SEP}{server}"


def register_app_mcp_servers(manifest: AppManifest) -> list[str]:
    """Write the app's manifest ``mcpServers`` into the live MCP config,
    namespaced ``{app}:{server}``. Returns the registered keys. Idempotent —
    re-registering overwrites the app's own entries."""
    servers = manifest.mcpServers or {}
    if not isinstance(servers, dict) or not servers:
        return []
    data = _load()
    bucket = data.setdefault("mcpServers", {})
    if not isinstance(bucket, dict):
        bucket = {}
        data["mcpServers"] = bucket
    # Resolve the app dir once so a stdio server shipped INSIDE the app package
    # (relative command/args like "backend/mcp_server.py") can actually spawn —
    # the MCP client doesn't chdir per server, so without a cwd a relative path
    # resolves against the gateway's cwd and never starts. A spec that already
    # sets an absolute cwd (or a remote url server) is left untouched.
    from personalclaw.apps.manager import app_dir

    try:
        base = app_dir(manifest.name)
    except Exception:
        base = None
    registered: list[str] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        spec = dict(spec)  # don't mutate the manifest's object
        if base is not None and spec.get("command") and "url" not in spec and not spec.get("cwd"):
            spec["cwd"] = str(base)
        key = _ns(manifest.name, str(name))
        bucket[key] = spec
        registered.append(key)
    if registered:
        _save(data)
        logger.info("app %s: registered MCP servers %s", manifest.name, registered)
    return registered


def deregister_app_mcp_servers(app_name: str) -> int:
    """Remove every ``{app_name}:*`` MCP server from the live config AND the
    installed agent config. Returns the count removed from mcp.json.

    The agent config (``personalclaw.json``) also carries the server spec under
    ``mcpServers`` plus ``@{app}:{server}`` refs in ``tools``/``allowedTools`` —
    discovery reads it as a ``source="agent"`` server, so a deregister that only
    cleaned mcp.json left the server visible + uncallable forever (the bug behind
    'the provider didn't delete'). Clean both stores so uninstalling the app
    actually removes its servers everywhere."""
    data = _load()
    bucket = data.get("mcpServers")
    prefix = f"{app_name}{_NS_SEP}"
    doomed: list[str] = []
    if isinstance(bucket, dict):
        doomed = [k for k in bucket if k.startswith(prefix)]
        for k in doomed:
            bucket.pop(k, None)
        if doomed:
            _save(data)
            logger.info("app %s: deregistered MCP servers %s (mcp.json)", app_name, doomed)
    _deregister_from_agent_config(prefix)
    return len(doomed)


def _deregister_from_agent_config(prefix: str) -> None:
    """Strip every ``{prefix}*`` server from the installed agent config —
    ``mcpServers`` specs + the ``@name`` refs in ``tools``/``allowedTools`` — so an
    app-contributed (source="agent") MCP server is fully removed on uninstall."""
    try:
        from personalclaw.agent import AGENT_FILENAME, _atomic_json_write

        path = config_dir() / "agents" / AGENT_FILENAME
        if not path.is_file():
            return
        cfg = json.loads(path.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers")
        changed = False
        if isinstance(servers, dict):
            for k in [k for k in servers if k.startswith(prefix)]:
                servers.pop(k, None)
                changed = True
        for list_key in ("tools", "allowedTools"):
            lst = cfg.get(list_key)
            if isinstance(lst, list):
                kept = [
                    t for t in lst if not (isinstance(t, str) and t.lstrip("@").startswith(prefix))
                ]
                if len(kept) != len(lst):
                    cfg[list_key] = kept
                    changed = True
        if changed:
            _atomic_json_write(path, cfg)
            logger.info("deregistered MCP servers %s* from agent config", prefix)
    except Exception:
        logger.debug("agent-config MCP deregister skipped for %s*", prefix, exc_info=True)


def app_mcp_server_keys(app_name: str) -> list[str]:
    """The live MCP server keys currently registered by an app (introspection)."""
    bucket = _load().get("mcpServers", {})
    if not isinstance(bucket, dict):
        return []
    prefix = f"{app_name}{_NS_SEP}"
    return sorted(k for k in bucket if k.startswith(prefix))
