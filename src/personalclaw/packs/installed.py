"""Installed-pack ledger (AGENT-PACKS §9, AP-3).

``<home>/packs/installed.json`` records what each imported pack put on this machine: its
components, the resolution of each connector requirement (§3.3), and its post-install
setup skill (§3.4). It is the READER surface behind two done_when contracts:

* a **skipped connector** degrades with a machine-readable ``connector_missing:<name>``
  marker recorded here, so a connector-dependent feature (and the pack detail page) can
  read "this is unavailable" without re-deriving it;
* a **setup skill** is re-runnable — the ledger keeps ``setup_pending`` true while a pack
  carries one, so the "Finish setup" chip always has something to re-invoke.

It is NOT the import journal (:class:`packs.import_._Journal` — the crash-safe rollback
ledger, deleted on success). This ledger is the durable post-install record; it is written
only after a commit fully succeeds, so it never describes a rolled-back pack.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

LEDGER_FILE = "installed.json"


@dataclass
class InstalledPack:
    """One installed pack's durable record.

    ``connector_markers`` holds every ``connector_missing:<name>`` for a skipped connector
    (the degraded-completion surface). ``setup_skill`` is the committed skill id of the
    pack's ``setup/SKILL.md`` (empty when the pack ships none); ``setup_pending`` stays true
    while a setup skill exists — the re-runnable "Finish setup" affordance never expires on
    its own (a user re-runs the interview whenever they want).
    """

    name: str
    version: str
    components: list[str] = field(default_factory=list)  # ["skill:cfo-report", ...]
    connectors: list[dict[str, Any]] = field(default_factory=list)  # ConnectorResolution dicts
    connector_markers: list[str] = field(default_factory=list)  # ["connector_missing:x", ...]
    setup_skill: str = ""
    setup_pending: bool = False
    installed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ledger_path(home: Path | None = None) -> Path:
    return (home or config_dir()) / "packs" / LEDGER_FILE


def load_installed(home: Path | None = None) -> list[InstalledPack]:
    """Every recorded installed pack (empty when nothing has been imported)."""
    path = _ledger_path(home)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("installed-pack ledger unreadable at %s", path)
        return []
    if not isinstance(raw, dict):
        return []
    out: list[InstalledPack] = []
    for name, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        out.append(
            InstalledPack(
                name=str(name),
                version=str(rec.get("version", "")),
                components=[str(c) for c in rec.get("components", [])],
                connectors=[c for c in rec.get("connectors", []) if isinstance(c, dict)],
                connector_markers=[str(m) for m in rec.get("connector_markers", [])],
                setup_skill=str(rec.get("setup_skill", "")),
                setup_pending=bool(rec.get("setup_pending", False)),
                installed_at=str(rec.get("installed_at", "")),
            )
        )
    return out


def record_install(pack: InstalledPack, home: Path | None = None) -> None:
    """Upsert one pack's record into the ledger (keyed by pack name), written atomically.

    Re-importing a pack overwrites its record (the same name = the same pack); other packs'
    records are preserved. The ledger is a dict keyed by name so a read is an O(1) lookup and
    a re-import never duplicates a row.
    """
    from personalclaw.atomic_write import atomic_write

    path = _ledger_path(home)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = {}
    rec = pack.to_dict()
    rec.pop("name", None)  # the key IS the name; don't duplicate it in the value
    existing[pack.name] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
