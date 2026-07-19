"""App dependency ledger — reference-counted shared-dependency tracking (A3).

When two apps both need the same MCP server / skill / agent, uninstalling one
must NOT break the other. The ledger records, per shared dependency, which apps
``installedBy`` it — so uninstall can classify each dep:

* **removable**   — only the app being removed referenced it → safe to remove.
* **shared**      — another installed app still needs it → keep.
* **userInstalled** — the user installed it directly (no app owns it) → never
  auto-remove.

Persisted at ``~/.personalclaw/apps/.dependency-ledger.json`` under an fcntl
advisory lock (mirroring schedule_history / hooks). Keyed by ``"<kind>:<id>"``
(e.g. ``"mcp:some-server"``, ``"skill:foo"``, ``"agent:bar"``).
"""

from __future__ import annotations

import fcntl
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from personalclaw.apps.manager import apps_dir
from personalclaw.apps.manifest import AppManifest
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_LEDGER_FILENAME = ".dependency-ledger.json"
_LOCK_FILENAME = ".dependency-ledger.lock"
_DEP_KINDS = ("mcp", "skills", "agents")


class DepDisposition(str, Enum):
    REMOVABLE = "removable"        # only this app referenced it
    SHARED = "shared"             # another app still needs it
    USER_INSTALLED = "userInstalled"  # no app owns it — user installed directly


@dataclass
class DepClassification:
    key: str        # "<kind>:<id>"
    kind: str       # mcp | skill | agent
    dep_id: str
    disposition: DepDisposition
    remaining: list[str]  # apps (other than the one removed) still referencing it

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "kind": self.kind, "id": self.dep_id,
            "disposition": self.disposition.value, "remaining": list(self.remaining),
        }


def _ledger_path() -> Path:
    return apps_dir() / _LEDGER_FILENAME


@contextmanager
def _locked() -> Iterator[None]:
    """Cross-process advisory lock around a read-modify-write of the ledger."""
    apps_dir().mkdir(parents=True, exist_ok=True)
    lock = apps_dir() / _LOCK_FILENAME
    fd = lock.open("w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _read() -> dict[str, list[str]]:
    path = _ledger_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # normalize to {key: [app, ...]}
        return {str(k): sorted({str(a) for a in v}) for k, v in data.items() if isinstance(v, list)}
    except (json.JSONDecodeError, OSError):
        logger.warning("dependency ledger unreadable; treating as empty", exc_info=True)
        return {}


def _write(ledger: dict[str, list[str]]) -> None:
    # Drop emptied entries so an unreferenced dep doesn't linger as userInstalled-looking.
    pruned = {k: sorted(set(v)) for k, v in ledger.items() if v}
    atomic_write(_ledger_path(), json.dumps(pruned, indent=2, sort_keys=True) + "\n", mode=0o600)


def _dep_id(entry: Any) -> str:
    """A marketplace dep entry is a bare id string or {"id": ...}."""
    if isinstance(entry, dict):
        return str(entry.get("id", "")).strip()
    return str(entry).strip()


def manifest_dep_keys(manifest: AppManifest) -> list[str]:
    """The ``<kind>:<id>`` keys an app contributes via dependencies.marketplace.

    Singularizes the kind for readability (``skills``→``skill``, ``agents``→
    ``agent``; ``mcp`` stays ``mcp``)."""
    out: list[str] = []
    mkt = manifest.dependencies.marketplace
    for kind, entries in (("mcp", mkt.mcp), ("skill", mkt.skills), ("agent", mkt.agents)):
        for entry in entries:
            dep_id = _dep_id(entry)
            if dep_id:
                out.append(f"{kind}:{dep_id}")
    return out


def record_install(manifest: AppManifest) -> None:
    """Add the app to the ``installedBy`` set of each dep it declares."""
    keys = manifest_dep_keys(manifest)
    if not keys:
        return
    with _locked():
        ledger = _read()
        for key in keys:
            apps = set(ledger.get(key, []))
            apps.add(manifest.name)
            ledger[key] = sorted(apps)
        _write(ledger)


def classify_uninstall(manifest: AppManifest) -> list[DepClassification]:
    """Classify each dep of the app about to be uninstalled (read-only preview).

    A dep the app declares but the ledger has NO record of → userInstalled
    (the user added it directly; an app shouldn't claim it). A dep only this app
    references → removable. A dep another app also references → shared."""
    out: list[DepClassification] = []
    ledger = _read()
    for key in manifest_dep_keys(manifest):
        kind, _, dep_id = key.partition(":")
        owners = set(ledger.get(key, []))
        remaining = sorted(owners - {manifest.name})
        if manifest.name not in owners:
            disp = DepDisposition.USER_INSTALLED
        elif remaining:
            disp = DepDisposition.SHARED
        else:
            disp = DepDisposition.REMOVABLE
        out.append(DepClassification(key=key, kind=kind, dep_id=dep_id,
                                     disposition=disp, remaining=remaining))
    return out


def record_uninstall(manifest: AppManifest) -> list[DepClassification]:
    """Remove the app from every dep's ``installedBy`` set and return the
    classification (computed BEFORE removal so 'removable' reflects this app's
    departure). The caller actually removes the ``removable`` deps."""
    classification = classify_uninstall(manifest)
    keys = manifest_dep_keys(manifest)
    if keys:
        with _locked():
            ledger = _read()
            for key in keys:
                if key in ledger:
                    ledger[key] = sorted(set(ledger[key]) - {manifest.name})
            _write(ledger)
    return classification


def installed_by(key: str) -> list[str]:
    """Apps currently referencing a dep ``<kind>:<id>`` (for tests/introspection)."""
    return _read().get(key, [])
