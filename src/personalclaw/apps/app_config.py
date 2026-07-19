"""Per-app configuration — read/write validated against ``setup.configSchema``.

An app declares a JSON-Schema-ish ``configSchema`` in its manifest; the user's
chosen values live in ``~/.personalclaw/apps/{name}/data/config.json`` (inside
``data/`` so they survive updates — A2 preserves ``data/``). The gateway's
``GET/PUT /api/apps/{name}/config`` routes (A4) read/write through here.

Validation is deliberately small — required keys, declared types, and enum
membership — enough to reject malformed config at the boundary without pulling in
a full JSON-Schema engine. Unknown keys are rejected (an app shouldn't receive
config it never declared).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import app_dir
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.json"

# JSON-Schema ``type`` → the Python types we accept for it.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


class AppConfigError(Exception):
    """Submitted config failed validation against the app's configSchema."""


def _config_path(name: str) -> Path:
    return app_dir(name) / "data" / _CONFIG_FILENAME


def _schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def read_config(name: str) -> dict[str, Any]:
    """Return the persisted config for an app (empty dict if none saved yet)."""
    path = _config_path(name)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        logger.warning("app %s config unreadable; treating as empty", name, exc_info=True)
        return {}


def validate_config(values: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate ``values`` against a manifest ``configSchema``. Returns error list.

    Checks: only declared keys allowed; ``required`` keys present; declared
    ``type`` honored; ``enum`` membership. An empty schema accepts only an empty
    object (an app with no configSchema takes no config)."""
    errors: list[str] = []
    props = _schema_properties(schema)
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []

    declared = set(props.keys())
    for key in values:
        if key not in declared:
            errors.append(f"unknown config key: {key!r}")

    for key in required:
        if key not in values:
            errors.append(f"missing required config key: {key!r}")

    for key, spec in props.items():
        if key not in values:
            continue
        val = values[key]
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if isinstance(expected, str) and expected in _TYPE_MAP:
            accepted = _TYPE_MAP[expected]
            # bool is an int subclass — reject a bool where a number/integer is asked.
            if expected in ("number", "integer") and isinstance(val, bool):
                errors.append(f"config key {key!r} must be {expected}, got boolean")
            elif not isinstance(val, accepted):
                errors.append(f"config key {key!r} must be {expected}")
        enum = spec.get("enum")
        if isinstance(enum, list) and enum and val not in enum:
            errors.append(f"config key {key!r} must be one of {enum}")

    return errors


def write_config(name: str, values: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Validate then persist an app's config. Raises :class:`AppConfigError` on
    invalid input; returns the saved values on success."""
    if not isinstance(values, dict):
        raise AppConfigError("config must be a JSON object")
    errors = validate_config(values, schema)
    if errors:
        raise AppConfigError("; ".join(errors))
    path = _config_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(values, indent=2, sort_keys=True) + "\n", mode=0o600)
    return values
