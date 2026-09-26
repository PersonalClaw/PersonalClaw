"""Pack UNINSTALL — remove what a pack installed, never a copy you edited (AGENT-PACKS §1/§9).

Settings → Packs had no way to remove a pack, and this package had no removal code at all, so a
pack you tried once stayed for good: its skills in every agent's list, its agent definitions, its
prompt, its workflow, its staged automations. Uninstall is the two install records read to their
end — the ledger (:mod:`packs.installed`) says what the pack put here, and the §1 drift lock
(:func:`packs.update.stamp_locks`) says what each component looked like when it landed:

* **A component whose bytes still match its lock is the pack's copy, and goes.** One whose bytes
  changed is yours now and stays, named with the reason. So does one with no lock, because "I
  cannot tell whether you edited this" must not resolve to "so I'll delete it" — the update
  flow's rule, applied to removal.
* **A recorded path is trusted only where its kind installs.** The ledger is a file in the home,
  so a lock is removed only when its path is exactly :func:`packs.import_.component_path` for
  that kind and is not a symlink. Anything else is refused whatever its digest says, so the
  ledger cannot become a way to delete an arbitrary file.
* **A pack that is still in use is refused whole.** The agents its roster deploy made live and the
  automations its trigger deploy added are rows in YOUR stores, which you may have changed since
  and nothing records whether you did. Uninstall names them and where to remove them, and removes
  nothing until they are gone — deleting them could take your edits, and leaving them would leave
  an agent and an automation pointing at skills and a workflow that are no longer there.
* **What you set up with your own credentials stays.** A connector the install configured is an
  MCP server you chose to add, with your secret; the plan names it and leaves it for Settings →
  MCP servers.

A dry run first, like an update: :func:`plan_uninstall` only reads, :func:`apply_uninstall` writes.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.packs.installed import InstalledPack

logger = logging.getLogger(__name__)

#: The staging files an install writes beside a pack's staged triggers. Pack metadata rather than
#: components (no lock, nothing you edit), so they go with the pack.
_STAGING_FILES = ("roster.json", "config_subset.json")


class PackUninstallError(Exception):
    """An uninstall that did not happen, with the stable code and status the route answers."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class KeptComponent:
    """A component uninstall leaves in place, with the reason a UI shows verbatim."""

    ref: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"ref": self.ref, "reason": self.reason}


@dataclass
class InUse:
    """A live row deployed from the pack, which must be removed before the pack can be."""

    kind: str  # "agent" | "automation"
    id: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "name": self.name}


@dataclass
class UninstallPlan:
    """What an uninstall removes and what it leaves. Every component ref the ledger lists lands in
    exactly one of ``removed``, ``kept`` and ``missing``."""

    pack: str
    version: str
    removed: list[str] = field(default_factory=list)
    kept: list[KeptComponent] = field(default_factory=list)
    #: Components already gone from disk — nothing to remove.
    missing: list[str] = field(default_factory=list)
    in_use: list[InUse] = field(default_factory=list)
    #: MCP servers the pack's connectors configured, which stay.
    servers: list[str] = field(default_factory=list)
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "version": self.version,
            "removed": list(self.removed),
            "kept": [k.to_dict() for k in self.kept],
            "missing": list(self.missing),
            "in_use": [u.to_dict() for u in self.in_use],
            "servers": list(self.servers),
            "applied": self.applied,
        }


def _home() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir()


def _installed(name: str, home: Path) -> InstalledPack:
    from personalclaw.packs.installed import load_installed

    pack = next((p for p in load_installed(home) if p.name == name), None)
    if pack is None:
        raise PackUninstallError("pack_not_installed", f"pack not installed: {name}", 404)
    return pack


def _removable_path(ref: str, lock: dict[str, str], home: Path, stage: str) -> Path | None:
    """The component's path when it is exactly where THIS component installs, else ``None``.

    Compared lexically (``normpath``), never through ``resolve``: resolving would follow a symlink
    to wherever it points and then agree with itself.
    """
    from personalclaw.packs.import_ import component_path, could_have_landed_as

    kind, _, orig_id = ref.partition(":")
    recorded = Path(os.path.normpath(home / lock.get("path", "")))
    # The id the component landed under is the segment its kind is named by.
    if kind == "skill":
        cid = recorded.name
    elif kind in ("template", "agent"):
        cid = recorded.parent.name
    else:
        cid = recorded.stem
    if not could_have_landed_as(orig_id, cid):
        return None
    expected = component_path(kind, cid, home, stage)
    if expected is None or Path(os.path.normpath(expected)) != recorded:
        return None
    if recorded.is_symlink():
        return None
    return recorded


def _classify(pack: InstalledPack, home: Path) -> tuple[UninstallPlan, dict[str, Path]]:
    from personalclaw.packs.update import component_digest

    plan = UninstallPlan(pack=pack.name, version=pack.version)
    targets: dict[str, Path] = {}
    for ref in pack.components:
        lock = pack.component_locks.get(ref)
        if not lock or not lock.get("computedHash"):
            plan.kept.append(
                KeptComponent(
                    ref,
                    "installed before each component's contents were recorded, so there is no "
                    "telling whether you changed it — it stays",
                )
            )
            continue
        path = _removable_path(ref, lock, home, pack.name)
        if path is None:
            plan.kept.append(
                KeptComponent(
                    ref,
                    f"its recorded location {lock.get('path', '')!r} is outside where packs "
                    "install it, so it is not removed",
                )
            )
            continue
        if not path.exists():
            plan.missing.append(ref)
            continue
        if component_digest(path) != lock["computedHash"]:
            plan.kept.append(
                KeptComponent(ref, "you edited it after it was installed, so it stays")
            )
            continue
        plan.removed.append(ref)
        targets[ref] = path
    plan.in_use = _in_use(pack, home)
    plan.servers = sorted(
        str(c.get("server_name"))
        for c in pack.connectors
        if c.get("mode") == "configure" and c.get("server_name")
    )
    return plan, targets


def _in_use(pack: InstalledPack, home: Path) -> list[InUse]:
    from personalclaw.config.loader import AppConfig
    from personalclaw.packs.triggers import deployed_triggers

    source = f"pack:{pack.name}"
    rows = [
        InUse("agent", agent, agent)
        for agent, profile in sorted(AppConfig.load().agents.items())
        if getattr(profile, "source", "") == source
    ]
    rows.extend(
        InUse("automation", t.id, t.name or t.id) for t in deployed_triggers(pack.name, home)
    )
    return rows


def plan_uninstall(name: str) -> UninstallPlan:
    """The dry run: what :func:`apply_uninstall` would remove and leave. Writes nothing."""
    home = _home()
    plan, _targets = _classify(_installed(name, home), home)
    return plan


def _in_use_message(name: str, rows: list[InUse]) -> str:
    agents = [r.name for r in rows if r.kind == "agent"]
    automations = [f"“{r.name}”" for r in rows if r.kind == "automation"]
    parts: list[str] = []
    places: list[str] = []
    if agents:
        parts.append(f"the agent{'s' if len(agents) > 1 else ''} {', '.join(agents)}")
        places.append("Agents")
    if automations:
        noun = "automations" if len(automations) > 1 else "automation"
        parts.append(f"the {noun} {', '.join(automations)}")
        places.append("Automations")
    return (
        f"{name} is still in use — {' and '.join(parts)} came from it. "
        f"Remove {'them' if len(rows) > 1 else 'it'} in {' and '.join(places)}, then uninstall."
    )


def _prune_empty(directory: Path, stop: Path) -> None:
    """Remove ``directory`` and its now-empty parents, up to but never including ``stop``."""
    current = directory
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def apply_uninstall(name: str) -> UninstallPlan:
    """Remove the pack: its unedited components, its staging area, and its ledger record.

    Refused whole (``pack_in_use``) while anything deployed from it is live. A component that
    cannot be deleted leaves the ledger record in place (``pack_uninstall_incomplete``), so the
    next dry run lists exactly what is left and running uninstall again finishes the job.
    """
    from personalclaw.packs import import_ as pack_import
    from personalclaw.packs.installed import forget_install

    home = _home()
    pack = _installed(name, home)
    plan, targets = _classify(pack, home)
    if plan.in_use:
        pack_import._audit(
            "pack_uninstall",
            "refused",
            resources=pack.name,
            error="in use: " + ", ".join(f"{r.kind}:{r.id}" for r in plan.in_use),
        )
        raise PackUninstallError("pack_in_use", _in_use_message(pack.name, plan.in_use), 409)

    failed: list[str] = []
    for ref, path in targets.items():
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            logger.warning("pack uninstall %s: could not remove %s: %s", name, path, exc)
            failed.append(ref)
            continue
        kind = ref.split(":", 1)[0]
        if kind in ("template", "agent"):
            # The definition's own directory (`workflows/defs/<id>/`, `agents/<id>/`) goes with
            # its only file; the directory that holds every definition stays.
            _prune_empty(path.parent, path.parent.parent)

    staged = pack_import._staged_dir(home, pack.name)
    for filename in _STAGING_FILES:
        staged_file = staged / filename
        if staged_file.is_file() and not staged_file.is_symlink():
            staged_file.unlink()
    for directory in (staged / "triggers", staged):
        _prune_empty(directory, staged.parent)

    if failed:
        pack_import._audit(
            "pack_uninstall", "incomplete", resources=pack.name, error=", ".join(failed)
        )
        raise PackUninstallError(
            "pack_uninstall_incomplete",
            f"could not remove {', '.join(failed)} — uninstall {name} again to finish",
            500,
        )
    forget_install(pack.name, home)
    plan.applied = True
    pack_import._audit(
        "pack_uninstall",
        "applied",
        resources=(
            f"{pack.name}@{pack.version} ({len(plan.removed)} removed, {len(plan.kept)} kept)"
        ),
    )
    return plan
