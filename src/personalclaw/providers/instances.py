"""Multi-instance storage for extensions that support multiple configured instances.

Extensions with ``multiInstance: true`` in their ProviderConfig can have
multiple named instances, each with its own config dict. Storage is at:
  ``~/.personalclaw/extensions/{extension_name}/instances/{instance_id}.json``

Singleton extensions (multiInstance: false) continue to use the single
config at ``~/.personalclaw/apps/{extension_name}/config.json`` via ProviderSettings.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


def _instances_dir(extension_name: str) -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "extensions" / extension_name / "instances"


@dataclass
class ExtensionInstance:
    """A single named instance of a multi-instance extension."""

    id: str
    extension_name: str
    display_name: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "extension_name": self.extension_name,
            "display_name": self.display_name,
            "config": self.config,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtensionInstance":
        return cls(
            id=str(data.get("id", "")),
            extension_name=str(data.get("extension_name", "")),
            display_name=str(data.get("display_name", "")),
            config=dict(data.get("config", {})),
            enabled=bool(data.get("enabled", True)),
        )


def list_instances(extension_name: str) -> list[ExtensionInstance]:
    """List all instances for a multi-instance extension."""
    instances_path = _instances_dir(extension_name)
    if not instances_path.is_dir():
        return []
    results: list[ExtensionInstance] = []
    for f in sorted(instances_path.iterdir()):
        if not f.suffix == ".json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            inst = ExtensionInstance.from_dict(data)
            inst.extension_name = extension_name
            results.append(inst)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read instance %s: %s", f, exc)
    return results


def get_instance(extension_name: str, instance_id: str) -> ExtensionInstance | None:
    """Get a specific instance by ID."""
    path = _instances_dir(extension_name) / f"{instance_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        inst = ExtensionInstance.from_dict(data)
        inst.extension_name = extension_name
        return inst
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read instance %s: %s", path, exc)
        return None


def create_instance(
    extension_name: str,
    display_name: str,
    config: dict[str, Any],
    *,
    instance_id: str | None = None,
) -> ExtensionInstance:
    """Create a new instance for a multi-instance extension."""
    iid = instance_id or uuid.uuid4().hex[:12]
    inst = ExtensionInstance(
        id=iid,
        extension_name=extension_name,
        display_name=display_name,
        config=config,
        enabled=True,
    )
    path = _instances_dir(extension_name) / f"{iid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(inst.to_dict(), indent=2) + "\n")
    return inst


def update_instance(
    extension_name: str,
    instance_id: str,
    *,
    display_name: str | None = None,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> ExtensionInstance | None:
    """Update an existing instance. Returns None if not found."""
    inst = get_instance(extension_name, instance_id)
    if inst is None:
        return None
    if display_name is not None:
        inst.display_name = display_name
    if config is not None:
        inst.config = config
    if enabled is not None:
        inst.enabled = enabled
    path = _instances_dir(extension_name) / f"{instance_id}.json"
    atomic_write(path, json.dumps(inst.to_dict(), indent=2) + "\n")
    return inst


def delete_instance(extension_name: str, instance_id: str) -> bool:
    """Delete an instance. Returns True if it existed."""
    path = _instances_dir(extension_name) / f"{instance_id}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True
