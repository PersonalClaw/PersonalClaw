"""Multi-instance storage for extensions that support multiple configured instances.

Extensions with ``multiInstance: true`` in their ProviderConfig can have
multiple named instances, each with its own config dict. Storage is at:
  ``~/.personalclaw/extensions/{extension_name}/instances/{instance_id}.json``

Singleton extensions (multiInstance: false) continue to use the single
config at ``~/.personalclaw/apps/{extension_name}/config.json`` via ProviderSettings.

An instance's secret fields are kept in the credential store under a key the instance owns,
and the record on disk holds a ``{{secret:…}}`` reference (:mod:`personalclaw.config.secret_refs`).
Every function here returns an :class:`ExtensionInstance` AS STORED — its ``config`` holds the
references, which the routes mask for the wire — and every writer stores a new value it is
handed. A consumer that needs the values asks :func:`resolved_config`, which resolves only keys
the instance's app holds: a reference to another owner's credential is refused, per instance.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config import secret_refs

logger = logging.getLogger(__name__)


def _instances_dir(extension_name: str) -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "extensions" / extension_name / "instances"


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read instance %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _stored(extension_name: str, record: dict[str, Any]) -> "ExtensionInstance":
    """The record as it is on disk. The owning app is the DIRECTORY's, never the record's own
    ``extension_name`` claim: a record cannot enrol itself under another app's keys."""
    inst = ExtensionInstance.from_dict(record)
    inst.extension_name = extension_name
    return inst


def resolved_config(inst: "ExtensionInstance") -> dict[str, Any]:
    """``inst``'s config with each secret holding its VALUE, for the provider that uses it.

    Raises :class:`~personalclaw.config.secret_refs.ForeignSecretReference` when the record
    references a credential its app does not hold."""
    return secret_refs.resolve(
        inst.config, owner=secret_refs.instance_owner(inst.extension_name, inst.id)
    )


def _write(inst: "ExtensionInstance", previous_config: dict[str, Any] | None) -> None:
    """Persist ``inst`` with its secret values moved into the credential store; ``inst.config``
    becomes the stored form. Raises :class:`ValueError` for a value the store refuses."""
    inst.config = secret_refs.store(
        inst.config,
        owner=secret_refs.instance_owner(inst.extension_name, inst.id),
        declared=secret_refs.declared_app_fields(inst.extension_name),
        previous=previous_config,
    )
    _write_record(inst)


def _write_record(inst: "ExtensionInstance") -> None:
    path = _instances_dir(inst.extension_name) / f"{inst.id}.json"
    atomic_write(path, json.dumps(inst.to_dict(), indent=2) + "\n")


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
    """List all instances for a multi-instance extension, as stored."""
    instances_path = _instances_dir(extension_name)
    if not instances_path.is_dir():
        return []
    results: list[ExtensionInstance] = []
    for f in sorted(instances_path.iterdir()):
        if not f.suffix == ".json":
            continue
        record = _read_record(f)
        if record is not None:
            results.append(_stored(extension_name, record))
    return results


def get_instance(extension_name: str, instance_id: str) -> ExtensionInstance | None:
    """Get a specific instance by ID, as stored."""
    path = _instances_dir(extension_name) / f"{instance_id}.json"
    if not path.is_file():
        return None
    record = _read_record(path)
    return None if record is None else _stored(extension_name, record)


def create_instance(
    extension_name: str,
    display_name: str,
    config: dict[str, Any],
    *,
    instance_id: str | None = None,
) -> ExtensionInstance:
    """Create a new instance for a multi-instance extension; returns it as stored. Raises
    :class:`ValueError` for a secret the store refuses (a NUL character, or a reference to
    another owner's credential), and nothing is written."""
    iid = instance_id or uuid.uuid4().hex[:12]
    inst = ExtensionInstance(
        id=iid,
        extension_name=extension_name,
        display_name=display_name,
        config=config,
        enabled=True,
    )
    _write(inst, None)
    return inst


def update_instance(
    extension_name: str,
    instance_id: str,
    *,
    display_name: str | None = None,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> ExtensionInstance | None:
    """Update an existing instance; returns it as stored, or None if not found.

    ``config`` replaces the stored one and goes through the credential store — a reference it
    carries must be the instance's own (the routes fold the stored references back in for a
    field left untouched), or the write is refused as :func:`create_instance`'s is. Without a
    ``config`` the stored one is kept exactly: renaming or switching off an instance does not
    re-judge settings it did not change.
    """
    path = _instances_dir(extension_name) / f"{instance_id}.json"
    record = _read_record(path) if path.is_file() else None
    if record is None:
        return None
    inst = _stored(extension_name, record)
    previous = dict(inst.config)
    if display_name is not None:
        inst.display_name = display_name
    if enabled is not None:
        inst.enabled = enabled
    if config is None:
        _write_record(inst)
        return inst
    inst.config = config
    _write(inst, previous)
    return inst


def delete_instance(extension_name: str, instance_id: str) -> bool:
    """Delete an instance, and the secrets it kept in the credential store. True if it existed."""
    path = _instances_dir(extension_name) / f"{instance_id}.json"
    if not path.is_file():
        return False
    path.unlink()
    secret_refs.purge([secret_refs.instance_owner(extension_name, instance_id).prefix])
    return True
