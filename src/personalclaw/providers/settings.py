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
        """Validate config against a JSON Schema. Returns list of errors."""
        if not schema:
            return []
        errors: list[str] = []
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        for key in required:
            if key not in config:
                label = properties.get(key, {}).get("x-meta", {}).get("label", key)
                errors.append(f"Missing required field: {label}")

        for key, value in config.items():
            if key not in properties:
                continue
            prop_schema = properties[key]
            prop_type = prop_schema.get("type", "")
            if prop_type == "string" and not isinstance(value, str):
                errors.append(f"{key}: expected string")
            elif prop_type == "number" and not isinstance(value, (int, float)):
                errors.append(f"{key}: expected number")
            elif prop_type == "integer" and not isinstance(value, int):
                errors.append(f"{key}: expected integer")
            elif prop_type == "boolean" and not isinstance(value, bool):
                errors.append(f"{key}: expected boolean")
            elif prop_type == "array" and not isinstance(value, list):
                errors.append(f"{key}: expected array")
            elif prop_type == "object" and not isinstance(value, dict):
                errors.append(f"{key}: expected object")

            if "enum" in prop_schema and value not in prop_schema["enum"]:
                errors.append(f"{key}: must be one of {prop_schema['enum']}")

        return errors
