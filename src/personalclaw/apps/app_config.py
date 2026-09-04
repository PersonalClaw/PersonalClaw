"""Per-app configuration — read/write validated against ``setup.configSchema``.

An app declares a JSON-Schema-ish ``configSchema`` in its manifest; the user's
chosen values live in ``~/.personalclaw/apps/{name}/data/config.json`` (inside
``data/`` so they survive updates — A2 preserves ``data/``). The gateway's
``GET/PUT /api/apps/{name}/config`` routes (A4) read/write through here.

Validation is deliberately a JSON-Schema SUBSET rather than a full engine. The per-property
rules are shared with the provider ``settingsSchema`` path (see
:mod:`personalclaw.apps.schema_validate`); what is specific to apps stays here — unknown keys
are rejected, because an app shouldn't receive config it never declared.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import app_dir
from personalclaw.apps.schema_validate import validate_properties
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.json"


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

    The per-property rules — ``type``, ``enum``, the numeric bounds and the string
    constraints — live in :mod:`personalclaw.apps.schema_validate`, shared with the provider
    ``settingsSchema`` path so the two cannot drift apart again. That module names the
    supported keyword set rather than leaving it implicit, because a declared keyword the
    platform ignores is a trap for the author who wrote it (#616).

    What stays HERE is this path's own object-level policy, which differs from the provider
    path's on purpose: an unknown key is refused, because an app's config is exactly what its
    manifest declares — and so an empty schema accepts only an empty object (an app with no
    ``configSchema`` takes no config at all).
    """
    errors: list[str] = []
    props = _schema_properties(schema)

    declared = set(props.keys())
    for key in values:
        if key not in declared:
            errors.append(f"unknown config key: {key!r}")

    errors.extend(validate_properties(values, props, schema.get("required", [])))
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
