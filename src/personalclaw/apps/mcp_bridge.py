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
from personalclaw.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


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
    # The MCP document writer: a server's `env`/`headers` values reach the file as
    # credential-store references (`config.secret_refs`), and removing a server deletes them.
    from personalclaw.config.secret_refs import write_mcp_document

    write_mcp_document(_mcp_json_path(), data)


def _ns(app_name: str, server: str) -> str:
    return f"{app_name}{_NS_SEP}{server}"


def server_app(key: str) -> str | None:
    """The app a namespaced ``{app}:{server}`` key belongs to, or ``None`` for a user's own
    server. The credential-reference resolver reads it: an app's server resolves only that
    app's keys (``config.secret_refs.SecretOwner.holds``)."""
    app, sep, server = key.partition(_NS_SEP)
    return app if sep and app and server else None


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
    from personalclaw.mcp_discovery import server_name_problem

    registered: list[str] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        problem = server_name_problem(_ns(manifest.name, str(name)))
        if problem is not None:
            # Its tools would be named as another of the app's servers'.
            logger.warning("app %s: MCP server %r not registered: %s", manifest.name, name, problem)
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
    """Remove every ``{app_name}:*`` MCP server from the live config AND the installed agent
    config, with the values it owns. Returns how many servers were removed.

    The agent config (``personalclaw.json``) also carries the server spec under
    ``mcpServers`` plus ``@{app}:{server}`` refs in ``tools``/``allowedTools`` —
    discovery reads it as a ``source="agent"`` server, so a deregister that only
    cleaned mcp.json left the server visible + uncallable forever (the bug behind
    'the provider didn't delete'). The names are collected from both files, refs included,
    and removed through ``secret_refs.remove_mcp_servers``, the one delete every surface uses."""
    from personalclaw.config.secret_refs import mcp_documents, remove_mcp_servers

    prefix = f"{app_name}{_NS_SEP}"
    names: set[str] = set()
    for path in mcp_documents():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        servers = doc.get("mcpServers")
        if isinstance(servers, dict):
            names.update(k for k in servers if k.startswith(prefix))
        for list_key in ("tools", "allowedTools"):
            listed = doc.get(list_key)
            names.update(
                t[1:]
                for t in (listed if isinstance(listed, list) else [])
                if isinstance(t, str) and t.startswith(f"@{prefix}")
            )
    removed = remove_mcp_servers(names)
    if removed:
        logger.info("app %s: deregistered MCP servers %s", app_name, removed)
    return len(removed)


def app_mcp_server_keys(app_name: str) -> list[str]:
    """The live MCP server keys currently registered by an app (introspection)."""
    bucket = _load().get("mcpServers", {})
    if not isinstance(bucket, dict):
        return []
    prefix = f"{app_name}{_NS_SEP}"
    return sorted(k for k in bucket if k.startswith(prefix))
