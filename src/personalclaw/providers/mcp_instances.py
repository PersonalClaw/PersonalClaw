"""Present ``~/.personalclaw/mcp.json`` servers as multi-instance provider
instances for the generic ``mcp-tools`` settings card.

The MCP Tool Servers card in Settings → Providers is a ``multiInstance`` provider.
Rather than write the generic ``extensions/mcp-tools/instances/*.json`` store —
which the native MCP client never reads — its instance CRUD is repointed here so
it reads and writes the ONE store the system actually consumes:
``~/.personalclaw/mcp.json`` (loaded by :mod:`personalclaw.mcp_client` for the
native loop and merged into the agent config by :func:`personalclaw.agent.rebuild_agent_config`).

Each ``mcpServers`` entry maps to one :class:`ExtensionInstance`:

* ``id`` / ``display_name`` = the server name (the mcp.json key)
* ``config`` = ``{transport, command, args, endpoint}`` matching the card's
  ``settingsSchema`` (``transport`` is the spec's ``type``, read by
  ``mcp_discovery.mcp_transport``; ``args`` is a space-joined string; ``endpoint`` is a
  remote server's ``url``), each with every credential in it masked the way the Tools page's
  edit form masks it (``mcp_discovery.masked_args``). An instance is only ever this card's
  VIEW of a server — the store is ``mcp.json`` — so the view is what it holds, and a write puts
  each masked value back from the server as ``mcp.json`` has it (:func:`_config_to_spec`).
* ``enabled`` = NOT the spec's ``disabled`` flag

Writes preserve any ``env``/``headers`` already on the spec so editing from the
card never drops credentials configured elsewhere — and every write goes through
``secret_refs.write_mcp_document``, so those values stay in the credential store. A delete goes
through ``secret_refs.remove_mcp_servers``, the same one the Tools page uses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.mcp_status import switched_off
from personalclaw.providers.instances import ExtensionInstance

if TYPE_CHECKING:
    from personalclaw.mcp_discovery import McpServerInfo

MCP_TOOLS_EXTENSION = "mcp-tools"

# Server names flow into argv (spawn) and filesystem reads; constrain to a safe
# handle so the card can't write an injectable key into mcp.json.
_VALID_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _mcp_json_path() -> Path:
    # Resolve through config_dir() (honours PERSONALCLAW_HOME) rather than a hardcoded
    # Path.home(): a pack import under a dev/test home must write the server into THAT
    # home's mcp.json, never the real one. Matches agent._user_dir()'s resolution.
    from personalclaw.config.loader import config_dir

    return config_dir() / "mcp.json"


def _load() -> dict[str, Any]:
    path = _mcp_json_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    # The MCP document writer: each server's `env`/`headers` values reach the file as
    # credential-store references (`config.secret_refs`).
    from personalclaw.config.secret_refs import write_mcp_document

    write_mcp_document(_mcp_json_path(), data)


def _spec_to_instance(name: str, spec: dict[str, Any]) -> ExtensionInstance:
    from personalclaw.mcp_discovery import masked_args, masked_command, masked_url, mcp_transport

    args = spec.get("args", [])
    config: dict[str, Any] = {
        # The server's real transport, not "sse" for every URL: the card writes back what it
        # read, so a Streamable HTTP server read as "sse" was turned into one on the next save.
        "transport": mcp_transport(spec),
        # Masked like the Tools page's edit form: each of these can carry a token (`--api-key …`,
        # `?token=…`), and this list is read on every visit to Settings → Providers.
        "command": masked_command(str(spec.get("command") or "")),
        "args": " ".join(masked_args(args)) if isinstance(args, list) else str(args or ""),
        "endpoint": masked_url(str(spec.get("url") or "")),
    }
    return ExtensionInstance(
        id=name,
        extension_name=MCP_TOOLS_EXTENSION,
        display_name=name,
        config=config,
        enabled=not switched_off(spec, name),
    )


def _config_to_spec(config: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a card config dict into an mcp.json server spec.

    Preserves ``env``/``headers`` from any existing spec so credential material
    configured outside the card survives an edit — and ``plainEnv``, which says which of those
    values are settings rather than secrets, so an edit does not move a setting into the store.

    The card was seeded masked (:func:`_spec_to_instance`), so each mask it sends back is put back
    to the value it stands for in *existing*, and one that stands for nothing it can match raises
    :class:`~personalclaw.security.MaskConflict` (a ``ValueError``) rather than being saved as the
    value (`mcp_discovery.keep_masked_args`).
    """
    from personalclaw.config.secret_refs import MCP_PLAIN_ENV
    from personalclaw.mcp_discovery import keep_masked_args, keep_masked_command, keep_masked_url

    stored = existing if isinstance(existing, dict) else {}
    spec: dict[str, Any] = {}
    for k in ("env", "headers", MCP_PLAIN_ENV):
        if stored.get(k):
            spec[k] = stored[k]
    if switched_off(stored):
        spec["disabled"] = True

    transport = config.get("transport") or ("sse" if config.get("endpoint") else "stdio")
    if transport != "stdio":
        # Spelled out (`type`, the key every MCP reader asks — `mcp_discovery.mcp_transport`).
        spec["type"] = transport
        url = (config.get("endpoint") or "").strip()
        spec["url"] = keep_masked_url(url, str(stored.get("url") or ""))
    else:
        command = (config.get("command") or "").strip()
        spec["command"] = keep_masked_command(command, str(stored.get("command") or ""))
        raw_args = config.get("args") or ""
        args = raw_args if isinstance(raw_args, list) else raw_args.split()
        saved = stored.get("args")
        spec["args"] = keep_masked_args(args, saved if isinstance(saved, list) else [])
    return spec


def planned(instance_id: str, config: dict[str, Any], *, create: bool) -> McpServerInfo:
    """What the server *instance_id* runs once *config* is written, for the question its owner is
    asked before it is (`mcp_grants`). Raises ``ValueError`` for what :func:`create_instance`
    refuses, and ``LookupError`` for an update of a server that is not there."""
    from personalclaw import mcp_grants

    servers = _load().get("mcpServers", {})
    existing = servers.get(instance_id) if isinstance(servers, dict) else None
    if create:
        _check_new_name(instance_id, servers if isinstance(servers, dict) else {})
        return mcp_grants.server_of(instance_id, _config_to_spec(config, None))
    if not isinstance(existing, dict):
        raise LookupError(instance_id)
    return mcp_grants.server_of(instance_id, _config_to_spec(config, existing))


def saved(instance_id: str) -> McpServerInfo | None:
    """The server *instance_id* as ``mcp.json`` holds it now, or ``None``."""
    from personalclaw import mcp_grants

    spec = _load().get("mcpServers", {}).get(instance_id)
    return mcp_grants.server_of(instance_id, spec) if isinstance(spec, dict) else None


def _check_new_name(name: str, servers: dict[str, Any]) -> None:
    if not _VALID_NAME.match(name):
        raise ValueError("Server name must be 1–64 letters, digits, dashes, or underscores.")
    if name in servers:
        raise ValueError(f"Server {name!r} already exists.")


def list_instances() -> list[ExtensionInstance]:
    servers = _load().get("mcpServers", {})
    if not isinstance(servers, dict):
        return []
    return [
        _spec_to_instance(name, spec) for name, spec in servers.items() if isinstance(spec, dict)
    ]


def get_instance(instance_id: str) -> ExtensionInstance | None:
    spec = _load().get("mcpServers", {}).get(instance_id)
    return _spec_to_instance(instance_id, spec) if isinstance(spec, dict) else None


def create_instance(display_name: str, config: dict[str, Any]) -> ExtensionInstance:
    """Create a server entry in mcp.json. The display name IS the server key."""
    name = display_name.strip()
    data = _load()
    servers = data.setdefault("mcpServers", {})
    _check_new_name(name, servers)
    servers[name] = _config_to_spec(config, None)
    _save(data)
    return _spec_to_instance(name, servers[name])


def update_instance(
    instance_id: str,
    *,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> ExtensionInstance | None:
    data = _load()
    servers = data.get("mcpServers", {})
    existing = servers.get(instance_id)
    if not isinstance(existing, dict):
        return None
    spec = _config_to_spec(config, existing) if config is not None else dict(existing)
    if enabled is not None:
        if enabled:
            spec.pop("disabled", None)
        else:
            spec["disabled"] = True
    servers[instance_id] = spec
    _save(data)
    return _spec_to_instance(instance_id, spec)


def delete_instance(instance_id: str) -> bool:
    """Remove the server everywhere it is configured, and the values it owns.

    Through :func:`~personalclaw.config.secret_refs.remove_mcp_servers`, the one delete. This
    card used to remove the server from ``mcp.json`` only: the agent config kept its copy (the
    rebuild merges additively, so nothing took it out), the Tools page kept listing it, and its
    credential-store keys stayed, because that copy still referenced them.
    """
    from personalclaw.config.secret_refs import remove_mcp_servers

    return bool(remove_mcp_servers([instance_id]))
