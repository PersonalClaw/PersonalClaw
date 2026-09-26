"""Per-app configuration — read/write validated against ``setup.configSchema``.

An app declares a JSON-Schema-ish ``configSchema`` in its manifest; the user's
chosen values live in ``~/.personalclaw/apps/{name}/data/config.json`` (inside
``data/`` so they survive updates — A2 preserves ``data/``). The gateway's
``GET/PUT /api/apps/{name}/config`` routes (A4) read/write through here.

Validation is deliberately a JSON-Schema SUBSET rather than a full engine. The per-property
rules are shared with the provider ``settingsSchema`` path (see
:mod:`personalclaw.apps.schema_validate`); what is specific to apps stays here — unknown keys
are rejected, because an app shouldn't receive config it never declared.

A field the schema declares ``x-meta.sensitive`` (or whose name is credential-shaped) is kept
in the credential store, and the file holds a ``{{secret:…}}`` reference: :func:`write_config`
stores (:mod:`personalclaw.config.secret_refs`), refusing a reference to a credential another
owner holds. The settings routes read :func:`read_stored`, never the values: what they send is
the references, masked. The app itself reads the values through ``ProviderSettings.load`` (the
same file), which resolves only keys the app holds.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import app_dir, read_app_owned_text
from personalclaw.apps.schema_validate import validate_properties
from personalclaw.apps.secret_fields import sensitive_field_names
from personalclaw.atomic_write import atomic_write
from personalclaw.config import secret_refs

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.json"


class AppConfigError(Exception):
    """Submitted config failed validation against the app's configSchema."""


def _config_path(name: str) -> Path:
    return app_dir(name) / "data" / _CONFIG_FILENAME


def _schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def read_stored(name: str) -> dict[str, Any]:
    """The file as it is on disk — secret fields are references. Never read through a link:
    the app writes ``data/``, so a ``config.json`` it made a link reads as no config."""
    root = app_dir(name)
    try:
        data = json.loads(read_app_owned_text(root, "data", _CONFIG_FILENAME))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning(
            "app %s: data/%s not read (%s); treating as empty", name, _CONFIG_FILENAME, exc
        )
        return {}
    return data if isinstance(data, dict) else {}


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
    invalid input — a reference to a credential another owner holds included, with the
    sentence that says what to do instead; returns the saved values on success."""
    if not isinstance(values, dict):
        raise AppConfigError("config must be a JSON object")
    errors = validate_config(values, schema)
    if errors:
        raise AppConfigError("; ".join(errors))
    try:
        stored = secret_refs.store(
            values,
            owner=secret_refs.app_owner(name),
            declared=sensitive_field_names(schema) | secret_refs.declared_app_fields(name),
            previous=read_stored(name),
        )
    except ValueError as exc:
        raise AppConfigError(str(exc)) from exc
    atomic_write(_config_path(name), json.dumps(stored, indent=2, sort_keys=True) + "\n")
    return values
