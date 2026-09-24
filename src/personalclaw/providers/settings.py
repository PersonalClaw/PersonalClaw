"""Per-extension configuration storage.

Each extension owns its config at ``~/.personalclaw/apps/{name}/data/config.json``
— inside ``data/`` so it survives app updates (A2 preserves ``data/``). This is the
SAME path the Apps config UI writes through :mod:`apps.app_config`; a provider built
at boot reads its user settings from here. (Historically this read the app-dir root
``config.json`` while the UI wrote to ``data/config.json`` — so a key set in the UI
never reached the provider. Unified onto ``data/`` — bug #31.)

This module provides read/write with JSON Schema validation against the
extension's declared ``settingsSchema``.
"""

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import app_dir
from personalclaw.apps.schema_validate import validate_properties
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


class ProviderSettings:
    """Read/write per-extension config with schema validation."""

    @staticmethod
    def config_path(extension_name: str) -> Path:
        # Inside data/ so it survives updates (A2 preserves data/) — the SAME file
        # apps.app_config writes through the Apps config UI (bug #31: these once
        # diverged, so UI-set provider keys never reached the provider at build).
        return app_dir(extension_name) / "data" / "config.json"

    @staticmethod
    def load(extension_name: str) -> dict[str, Any]:
        path = ProviderSettings.config_path(extension_name)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read extension config %s: %s", path, exc)
            return {}

    @staticmethod
    def save(extension_name: str, config: dict[str, Any]) -> None:
        path = ProviderSettings.config_path(extension_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(config, indent=2) + "\n")

    @staticmethod
    def update(extension_name: str, partial: dict[str, Any]) -> dict[str, Any]:
        current = ProviderSettings.load(extension_name)
        current.update(partial)
        ProviderSettings.save(extension_name, current)
        return current

    @staticmethod
    def validate(config: dict[str, Any], schema: dict[str, Any]) -> list[str]:
        """Validate config against a declared ``settingsSchema``. Returns list of errors.

        The per-property rules live in :mod:`personalclaw.apps.schema_validate`, shared with
        the app ``configSchema`` path. This copy used to implement them itself and had drifted:
        it enforced no bound at all (so ``confidence_threshold`` declaring ``[0.0, 1.0]``
        accepted ``5``) and accepted ``True`` for an ``integer``, since ``bool`` is an ``int``
        subclass (#616).

        What stays HERE is this path's own object-level policy, which differs from the app
        path's on purpose: a provider with no schema is unvalidated, and an unknown key is
        IGNORED rather than refused — a stored config may carry a key from an older manifest,
        and refusing it would make the whole config unsavable.
        """
        if not schema:
            return []
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return []
        # An undeclared key is simply not checked (the shared module skips whatever the schema
        # does not describe). The app path layers its own refusal on top of that; this one does
        # not, deliberately.
        return validate_properties(config, properties, schema.get("required", []))
