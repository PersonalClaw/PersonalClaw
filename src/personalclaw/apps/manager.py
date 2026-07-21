"""App storage helpers — the path + installed-metadata primitives the provider/
extension system reads.

Each provider/extension lives under ``~/.personalclaw/apps/{name}/`` with an
``installed.json`` describing version + enabled state and an ``app.json`` manifest.
The third-party app-platform lifecycle (install/update/enable/disable/uninstall)
was retired; what remains is reading what's present so the provider loader can
discover installed extensions.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from personalclaw.apps.manifest import KEBAB_RE, AppManifest
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

APP_MANIFEST_FILENAME = "app.json"
INSTALLED_META_FILENAME = "installed.json"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def apps_dir() -> Path:
    """Return the root directory for installed apps: ``~/.personalclaw/apps/``."""
    return config_dir() / "apps"


def _validate_app_name(name: str) -> str:
    """Reject a name that isn't a valid app id before it can name a directory.

    App names are kebab-case (the same ``KEBAB_RE`` the manifest enforces). This
    guard exists because ``app_data_dir`` *creates* the directory: without it,
    any caller (or stray test) handing in a fuzzed/invalid name silently mkdir's
    a junk dir under ``apps/`` — which once accumulated 16k empty dirs and made
    ``list_apps`` stat-storm on every request. Fail loud instead of polluting.
    """
    if not name or not KEBAB_RE.match(name):
        raise ValueError(f"invalid app name {name!r} (must be kebab-case)")
    return name


def _reject_path_escape(name: str) -> str:
    """Reject a name that could escape the apps/ sandbox as a path segment.

    Defense-in-depth at the single chokepoint every app-scoped path flows through
    (config read/write, backend entry resolution, onEnable/onDisable hooks with
    ``cwd=app_dir(name)``, and ``shutil.rmtree(app_dir(name))`` on uninstall). A
    traversal name (``../``, ``/etc``, an absolute path, …) reaching any of those
    would escape the sandbox — worst case an rmtree OUTSIDE apps/. This is a
    NARROW escape check (not full kebab-strictness): ``list_apps`` iterates real
    on-disk dir names and legitimately hands back special dirs like ``.quarantine``
    (skill supply-chain) — those can't escape, so they pass; only genuine
    traversal is blocked. ``_validate_app_name`` (full KEBAB_RE) still gates the
    INSTALL path where a fresh name is minted."""
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or name.startswith("..")
        or ".." in (name.replace("\\", "/").split("/"))  # any '..' segment
    ):
        raise ValueError(f"invalid app name {name!r} (path escape rejected)")
    return name


def app_dir(name: str) -> Path:
    """Return the directory for a specific installed app.

    Rejects a path-escaping ``name`` FIRST (see :func:`_reject_path_escape`): this
    is the single chokepoint every app-scoped path flows through (config, backend
    entry, lifecycle hooks, uninstall rmtree), so a traversal name can't escape the
    apps/ sandbox from ANY caller — not just the API routes (which already 404 an
    unknown name via the manifest check)."""
    return apps_dir() / _reject_path_escape(name)


def app_data_dir(name: str) -> Path:
    """Return the app-scoped data directory: ``~/.personalclaw/apps/{name}/data/``.

    This helper *creates* the directory, so it uses the STRICTER kebab guard
    (``_validate_app_name``, not just the traversal check in ``app_dir``): a fresh
    name being minted must be a valid app id, else a fuzzed/junk name silently
    mkdir-pollutes apps/ (once accumulated 16k empty dirs → list_apps stat-storm).
    """
    d = app_dir(_validate_app_name(name)) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Installed metadata
# ---------------------------------------------------------------------------

# Valid values for InstalledApp classification fields
_VALID_ORIGIN: frozenset[str] = frozenset({"builtin", "registry", "local", "external"})
_VALID_RESOURCES: frozenset[str] = frozenset({"gateway", "app"})
_VALID_LIFECYCLE: frozenset[str] = frozenset({"gateway", "app", "locked"})


@dataclass
class InstalledApp:
    """Metadata persisted in ``installed.json`` for each installed app/extension."""

    name: str = ""
    version: str = ""
    displayName: str = ""  # noqa: N815
    enabled: bool = True
    installedAt: str = ""  # noqa: N815
    updatedAt: str = ""  # noqa: N815
    source: str = ""  # concrete provenance: path, URL, "registry:name", "builtin"
    origin: str = "registry"  # builtin | registry | local | external
    resources: str = "gateway"  # gateway | app
    lifecycle: str = "gateway"  # gateway | app | locked
    schemaVersion: int = 2  # noqa: N815  — schema version for future migrations

    def validate_fields(self) -> list[str]:
        """Validate classification field values. Returns error list (empty = valid)."""
        errors: list[str] = []
        if self.origin not in _VALID_ORIGIN:
            errors.append(f"invalid origin: {self.origin!r}")
        if self.resources not in _VALID_RESOURCES:
            errors.append(f"invalid resources: {self.resources!r}")
        if self.lifecycle not in _VALID_LIFECYCLE:
            errors.append(f"invalid lifecycle: {self.lifecycle!r}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v or isinstance(v, (bool, int))}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstalledApp":
        inst = cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            displayName=str(data.get("displayName", "")),
            enabled=bool(data.get("enabled", True)),
            installedAt=str(data.get("installedAt", "")),
            updatedAt=str(data.get("updatedAt", "")),
            source=str(data.get("source", "")),
            origin=str(data.get("origin", "registry")),
            resources=str(data.get("resources", "gateway")),
            lifecycle=str(data.get("lifecycle", "gateway")),
            schemaVersion=int(data.get("schemaVersion", 1)),
        )
        errors = inst.validate_fields()
        if errors:
            logger.warning(
                "InstalledApp %s has invalid fields: %s — using defaults",
                inst.name,
                errors,
            )
            if inst.origin not in _VALID_ORIGIN:
                inst.origin = "registry"
            if inst.resources not in _VALID_RESOURCES:
                inst.resources = "gateway"
            if inst.lifecycle not in _VALID_LIFECYCLE:
                inst.lifecycle = "gateway"
        return inst


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_installed(name: str) -> InstalledApp | None:
    """Read installed.json for an app, or None if not installed."""
    # A path-escaping name (app_dir rejects it — #44) can't be an installed app;
    # return None so lifecycle callers (incl. force_uninstall's rmtree pre-check)
    # treat it as "not installed" rather than surfacing the guard's ValueError.
    try:
        meta_path = app_dir(name) / INSTALLED_META_FILENAME
    except ValueError:
        return None
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return InstalledApp.from_dict(data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", meta_path, exc)
        return None


def _write_installed(name: str, meta: InstalledApp) -> None:
    """Write installed.json for an app."""
    meta_path = app_dir(name) / INSTALLED_META_FILENAME
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(meta_path, json.dumps(meta.to_dict(), indent=2) + "\n")


def list_apps() -> list[dict[str, Any]]:
    """Return metadata for all installed apps/extensions (read-only discovery)."""
    root = apps_dir()
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        meta = _read_installed(entry.name)
        if not meta:
            continue
        manifest_path = entry / APP_MANIFEST_FILENAME
        manifest_data: dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                manifest = AppManifest.from_json_file(manifest_path)
                manifest_data = manifest.to_dict()
                # A self-managed app may update its own app.json directly; sync the
                # version so discovery reflects the real one, not a stale installed.json.
                if (
                    meta.lifecycle == "app"
                    and manifest.version
                    and manifest.version != meta.version
                ):
                    meta.version = manifest.version
                    meta.updatedAt = _now_iso()
                    _write_installed(entry.name, meta)
            except Exception:
                pass
        result.append({**meta.to_dict(), "manifest": manifest_data})
    return result
