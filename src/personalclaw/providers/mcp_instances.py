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
  ``settingsSchema`` (``args`` is a space-joined string; ``endpoint`` is the SSE
  ``url``)
* ``enabled`` = NOT the spec's ``disabled`` flag

Writes preserve any ``env``/``headers`` already on the spec so editing from the
card never drops credentials configured elsewhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from personalclaw.providers.instances import ExtensionInstance

MCP_TOOLS_EXTENSION = "mcp-tools"

# Server names flow into argv (spawn) and filesystem reads; constrain to a safe
# handle so the card can't write an injectable key into mcp.json.
_VALID_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _mcp_json_path() -> Path:
    return Path.home() / ".personalclaw" / "mcp.json"


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
    from personalclaw.agent import _atomic_json_write  # circular import

    path = _mcp_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(path, data)


def _spec_to_instance(name: str, spec: dict[str, Any]) -> ExtensionInstance:
    url = spec.get("url", "")
    args = spec.get("args", [])
    config: dict[str, Any] = {
        "transport": "sse" if url else "stdio",
        "command": spec.get("command", ""),
        "args": " ".join(args) if isinstance(args, list) else str(args or ""),
        "endpoint": url,
    }
    return ExtensionInstance(
        id=name,
        extension_name=MCP_TOOLS_EXTENSION,
        display_name=name,
        config=config,
        enabled=spec.get("disabled") is not True,
    )


def _config_to_spec(config: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a card config dict into an mcp.json server spec.

    Preserves ``env``/``headers`` from any existing spec so credential material
    configured outside the card survives an edit.
    """
    spec: dict[str, Any] = {}
    if isinstance(existing, dict):
        for k in ("env", "headers"):
            if existing.get(k):
                spec[k] = existing[k]
        if existing.get("disabled") is True:
            spec["disabled"] = True

    transport = config.get("transport") or ("sse" if config.get("endpoint") else "stdio")
    if transport == "sse":
        spec["url"] = (config.get("endpoint") or "").strip()
    else:
        spec["command"] = (config.get("command") or "").strip()
        raw_args = config.get("args") or ""
        if isinstance(raw_args, list):
            spec["args"] = raw_args
        else:
            spec["args"] = raw_args.split() if raw_args.strip() else []
    return spec


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
    if not _VALID_NAME.match(name):
        raise ValueError("Server name must be 1–64 letters, digits, dashes, or underscores.")
    data = _load()
    servers = data.setdefault("mcpServers", {})
    if name in servers:
        raise ValueError(f"Server {name!r} already exists.")
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
    data = _load()
    servers = data.get("mcpServers", {})
    if instance_id not in servers:
        return False
    del servers[instance_id]
    _save(data)
    return True
