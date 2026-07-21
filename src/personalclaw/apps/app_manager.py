"""App Platform lifecycle — install / enable / disable / uninstall (A1).

The runtime is PClaw-native, built on top of
the existing manifest (:mod:`apps.manifest`) + storage primitives
(:mod:`apps.manager`). Turns "read what's present" into a real, safety-gated
lifecycle:

* **install(source)** — copy → **stage in quarantine** → validate manifest →
  **scan staged content** (the shared :class:`SkillScanner` gate; ``dangerous``
  is terminal, non-overridable) → require consent for risky verdicts → run
  ``setup.onInstall`` (bounded subprocess) → register providers → write
  ``installed.json``.
* **enable / disable(name)** — run ``setup.onEnable``/``onDisable`` (bounded),
  flip the provider registration.
* **uninstall(name)** — run ``setup.onUninstall`` → deregister → remove files.

Every lifecycle action is SEL-audited. Executing a third-party ``setup`` hook is
RCE-by-design, so a hook only runs after the scanner passes (or the caller gives
explicit consent for a ``warning``) — never on a ``dangerous`` verdict, and never
auto-forced for an unattended/agent-initiated install.

Atomic update + rollback is A2; the dependency ledger is A3; this is the core.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import (
    APP_MANIFEST_FILENAME,
    INSTALLED_META_FILENAME,
    InstalledApp,
    _now_iso,
    _read_installed,
    _write_installed,
    app_dir,
    apps_dir,
)
from personalclaw.apps.manifest import AppManifest
from personalclaw.atomic_write import atomic_write
from personalclaw.sel import sel
from personalclaw.supply_chain import ScanReport, TrustTier, Verdict, default_scanner

logger = logging.getLogger(__name__)

_QUARANTINE_DIRNAME = ".quarantine"
_HOOK_DEFAULT_TIMEOUT = 60  # seconds; setup.onInstall/onUpdate cap
_ROLLBACK_SUFFIX = ".rollback"  # ~/.personalclaw/apps/.{name}.rollback during update
_APP_DATA_DIRNAME = "data"  # app-scoped state preserved across updates


class AppLifecycleError(Exception):
    """A lifecycle operation failed (validation, scan refusal, hook error)."""


@dataclass
class InstallResult:
    """Outcome of an install attempt — surfaced to the API/UI."""

    ok: bool
    name: str = ""
    scan: ScanReport | None = None
    error: str = ""
    needs_consent: bool = False  # a warning verdict the caller must confirm
    restart_required: bool = (
        False  # a new python dep was installed; gateway must restart to import it
    )
    # P21 platform gate: set when the app can't be server-installed here (installMode=client,
    # or this OS isn't in the app's `os` list). The install did NOT commit; the UI shows the
    # copy-paste client-install one-liner instead. `client_install` = {shell, postInstall}.
    needs_client_install: bool = False
    client_install: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "name": self.name,
            "error": self.error,
            "needs_consent": self.needs_consent,
            "restart_required": self.restart_required,
            "needs_client_install": self.needs_client_install,
            "client_install": self.client_install,
            "scan": self.scan.to_dict() if self.scan else None,
        }


def _audit(
    operation: str, outcome: str, name: str, *, caller: str = "app_manager", error: str = ""
) -> None:
    try:
        sel().log_api_access(
            caller=caller,
            operation=f"app.{operation}",
            outcome=outcome,
            source="app_platform",
            resources=f"app={name}",
            error=error,
        )
    except Exception:  # noqa: BLE001 — audit must never break the lifecycle
        logger.debug("app lifecycle audit failed", exc_info=True)


def _quarantine_dir() -> Path:
    d = apps_dir() / _QUARANTINE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tier_for_origin(origin: str) -> TrustTier:
    """Map an install origin to the scanner trust tier."""
    return {
        "builtin": TrustTier.BUILTIN,
        "registry": TrustTier.OFFICIAL,
        "local": TrustTier.COMMUNITY,
        "external": TrustTier.COMMUNITY,
    }.get(origin, TrustTier.COMMUNITY)


def _run_hook(cmd: str, *, cwd: Path, timeout: int, env_name: str) -> None:
    """Run a setup hook as a bounded subprocess. Raises on failure/timeout.

    Mirrors the run-script/bash bounded discipline: a timeout-bounded subprocess
    in the app's own dir. The scanner has already vetted the staged content
    before this ever runs (install gate); a hook that errors aborts the op.
    """
    if not cmd.strip():
        return
    try:
        proc = subprocess.run(  # noqa: S602 — intentional: vetted third-party setup hook
            cmd,
            shell=True,
            cwd=str(cwd),
            timeout=max(1, timeout),
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppLifecycleError(f"{env_name} hook timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise AppLifecycleError(f"{env_name} hook exited {proc.returncode}: {tail}")


_PIP_TIMEOUT = 600  # seconds — a heavy wheel (torch) can take minutes


def _install_python_deps(manifest: AppManifest) -> bool:
    """Pip-install an app's declared ``pythonDependencies`` into the shared core
    venv. Core ships lean; the app that needs a heavy lib brings it.

    Returns True iff a package was actually installed (⇒ the gateway must RESTART
    to import it — the running process already imported its module set). If every
    requirement is already satisfied, this is a no-op and returns False. Best-effort
    on already-satisfied detection; when unsure it installs (pip itself is the
    final arbiter and skips already-present pins fast).
    """
    reqs = list(manifest.dependencies.pythonDependencies)
    if not reqs:
        return False

    # Which requirements are already satisfied? Only then can we skip the restart.
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _dist_version

        from packaging.requirements import Requirement  # bundled via pip

        missing: list[str] = []
        for spec in reqs:
            try:
                req = Requirement(spec)
                have = _dist_version(req.name)
                if req.specifier and not req.specifier.contains(have, prereleases=True):
                    missing.append(spec)
            except PackageNotFoundError:
                missing.append(spec)
            except Exception:  # noqa: BLE001 — unparseable spec → let pip decide
                missing.append(spec)
    except Exception:  # noqa: BLE001 — packaging/metadata unavailable → install all
        missing = reqs

    if not missing:
        logger.info("app %s: all %d python deps already satisfied", manifest.name, len(reqs))
        return False

    logger.info("app %s: installing python deps %s", manifest.name, missing)
    try:
        proc = subprocess.run(  # noqa: S603 — deps come from a scanned+vetted manifest
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing],
            timeout=_PIP_TIMEOUT,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppLifecycleError(
            f"python dependency install timed out after {_PIP_TIMEOUT}s: {missing}"
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise AppLifecycleError(f"pip install failed for {missing}: {tail}")
    return True


def _load_staged_manifest(staged: Path) -> AppManifest:
    mpath = staged / APP_MANIFEST_FILENAME
    if not mpath.is_file():
        raise AppLifecycleError(f"no {APP_MANIFEST_FILENAME} in source")
    try:
        manifest = AppManifest.from_json_file(mpath)
    except Exception as exc:  # noqa: BLE001
        raise AppLifecycleError(f"invalid manifest: {exc}") from exc
    errors = manifest.validate()
    if errors:
        raise AppLifecycleError(f"manifest validation failed: {'; '.join(errors)}")
    return manifest


def _provider_registry():
    from personalclaw.providers.registry import get_provider_registry

    return get_provider_registry()


def _start_backend(manifest: AppManifest) -> None:
    """Launch the app's backend subprocess (if declared) for the reverse-proxy."""
    if not manifest.backend.entryPoint:
        return
    try:
        from personalclaw.apps.backend_runtime import get_backend_supervisor

        get_backend_supervisor().start(manifest)
    except Exception:
        logger.debug("app %s: backend start failed", manifest.name, exc_info=True)


def _stop_backend(name: str) -> None:
    try:
        from personalclaw.apps.backend_runtime import get_backend_supervisor

        get_backend_supervisor().stop(name)
    except Exception:
        logger.debug("app %s: backend stop failed", name, exc_info=True)


def _register_mcp(manifest: AppManifest) -> None:
    """Wire the app's declared mcpServers into the live MCP config."""
    if not manifest.mcpServers:
        return
    try:
        from personalclaw.apps import mcp_bridge

        mcp_bridge.register_app_mcp_servers(manifest)
    except Exception:
        logger.debug("app %s: MCP register failed", manifest.name, exc_info=True)


def _deregister_mcp(name: str) -> None:
    try:
        from personalclaw.apps import mcp_bridge

        mcp_bridge.deregister_app_mcp_servers(name)
    except Exception:
        logger.debug("app %s: MCP deregister failed", name, exc_info=True)


def _seed_app_prompts(manifest: AppManifest, name: str) -> None:
    """Seed the app's declared prompts/snippets into the native store (an app OWNS
    its prompts). Best-effort: a seeding failure never breaks the lifecycle."""
    if not manifest.prompts:
        return
    try:
        from personalclaw.apps.prompt_seed import seed_app_prompts

        seed_app_prompts(manifest, app_dir(name))
    except Exception:
        logger.debug("app %s: prompt seed failed", name, exc_info=True)


def _remove_app_prompts(manifest: AppManifest, name: str) -> None:
    """Remove the app's own seeded prompts + unregister its prompt use-cases."""
    try:
        from personalclaw.apps.prompt_seed import remove_app_prompts

        remove_app_prompts(manifest, app_dir(name))
    except Exception:
        logger.debug("app %s: prompt remove failed", name, exc_info=True)


def install(
    source: str | Path,
    *,
    origin: str = "local",
    confirm: bool = False,
    caller: str = "app_manager",
    source_ref: str | None = None,
) -> InstallResult:
    """Install an app from a local directory ``source`` (path/git → A4 fetch).

    Staged → scanned → (consent) → onInstall → registered. A ``dangerous`` scan
    verdict is terminal: never installs, ``confirm`` does NOT override it. A
    ``warning`` requires ``confirm=True`` (the install UI's explicit consent).

    ``source_ref`` is the provenance recorded in ``installed.json`` — the ORIGINAL
    source string (e.g. the git URL), not the resolved local dir. A git clone
    resolves to a throwaway temp path; recording that is useless for grouping the
    Store by source, so the handler passes the URL here. Defaults to ``source``.
    """
    src = Path(source)
    if not src.is_dir():
        _audit("install", "error", str(source), caller=caller, error="source not a directory")
        return InstallResult(ok=False, error=f"source is not a directory: {source}")

    # 1. Stage in quarantine FIRST — dangerous content never touches the live tree.
    staged_root = _quarantine_dir()
    try:
        manifest_peek = _load_staged_manifest(src)
    except AppLifecycleError as exc:
        _audit("install", "error", str(source), caller=caller, error=str(exc))
        return InstallResult(ok=False, error=str(exc))
    name = manifest_peek.name
    staged = staged_root / name
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(src, staged)

    try:
        # 2. Re-validate the staged manifest (source-of-truth is the staged copy).
        manifest = _load_staged_manifest(staged)

        # 3. Scan the staged content — the gate.
        tier = _tier_for_origin(origin)
        report = default_scanner.scan(staged, tier)
        if report.verdict is Verdict.DANGEROUS:
            _audit("install", "refused", name, caller=caller, error="scan: dangerous")
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                error="install refused: scanner flagged dangerous content",
            )
        if report.verdict is Verdict.WARNING and not confirm:
            _audit("install", "needs_consent", name, caller=caller)
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                needs_consent=True,
                error="install needs consent: scanner raised warnings",
            )

        # 3.5 Platform gate (P21 Gap B). An app that must be installed on the user's
        # local machine (installMode="client") or that doesn't support THIS server's OS
        # can't be server-installed here — short-circuit to a client-install result
        # (the copy-paste one-liner) WITHOUT committing anything to the live tree. The
        # client-install shell runs on the user's machine, OUTSIDE the scanner, so it's
        # surfaced as trusted-by-inspection copy-paste, never auto-run.
        import sys as _sys

        platform_cfg = manifest.platform
        if platform_cfg is not None and (
            platform_cfg.installMode == "client"
            or not platform_cfg.supports_platform(_sys.platform)
        ):
            ci = platform_cfg.clientInstall.to_dict()
            _audit(
                "install",
                "client_install_required",
                name,
                caller=caller,
                error=f"installMode={platform_cfg.installMode} os={platform_cfg.os}",
            )
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                needs_client_install=True,
                client_install=ci or {},
                error=(
                    f"'{name}' installs on your local machine, not this server"
                    if platform_cfg.installMode == "client"
                    else f"'{name}' does not support this server's platform ({_sys.platform})"
                ),
            )

        # 4. Commit: move staged → live app dir.
        dest = app_dir(name)
        if dest.exists():
            _audit("install", "error", name, caller=caller, error="already installed")
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                error=f"app {name!r} already installed (use update)",
            )
        shutil.move(str(staged), str(dest))

        # Ensure the app's data/ dir exists BEFORE any hook runs — apps write
        # state there (it's the dir preserved across updates), and an onInstall
        # hook commonly seeds it.
        (dest / _APP_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)

        # 5a. Install declared python deps into the shared venv (core is lean; the
        # app brings its heavy libs). Before the onInstall hook so a hook can import
        # them. A newly-installed dep needs a gateway restart to become importable.
        try:
            restart_required = _install_python_deps(manifest)
        except AppLifecycleError as exc:
            shutil.rmtree(dest, ignore_errors=True)  # roll back the commit
            _audit("install", "error", name, caller=caller, error=str(exc))
            return InstallResult(ok=False, name=name, scan=report, error=str(exc))

        # 5b. Run onInstall (bounded) — only after the gate passed.
        try:
            _run_hook(
                manifest.setup.onInstall,
                cwd=dest,
                timeout=_HOOK_DEFAULT_TIMEOUT,
                env_name="onInstall",
            )
        except AppLifecycleError as exc:
            shutil.rmtree(dest, ignore_errors=True)  # roll back the commit
            _audit("install", "error", name, caller=caller, error=str(exc))
            return InstallResult(ok=False, name=name, scan=report, error=str(exc))

        # 6. Persist installed.json + register providers.
        meta = InstalledApp(
            name=name,
            version=manifest.version,
            displayName=manifest.displayName or name,
            enabled=True,
            installedAt=_now_iso(),
            updatedAt=_now_iso(),
            source=str(source_ref if source_ref is not None else source),
            origin=origin if origin in {"builtin", "registry", "local", "external"} else "local",
        )
        _write_installed(name, meta)
        if manifest.all_providers():
            try:
                _provider_registry().register(manifest, enabled=True)
            except Exception:
                logger.exception("app %s: provider registration failed", name)
        # Seed the app's own prompts/snippets into the native store (idempotent,
        # non-clobbering) so an app OWNS the prompts it ships.
        _seed_app_prompts(manifest, name)
        # Record this app against each shared dependency it declares (A3 ledger),
        # so a later uninstall can tell removable from shared.
        try:
            from personalclaw.apps import dependency_ledger

            dependency_ledger.record_install(manifest)
        except Exception:
            logger.debug("app %s: dependency-ledger record failed", name, exc_info=True)
        _register_mcp(manifest)
        _start_backend(manifest)
        _audit("install", "ok", name, caller=caller)
        return InstallResult(ok=True, name=name, scan=report, restart_required=restart_required)
    except AppLifecycleError as exc:
        _audit("install", "error", name, caller=caller, error=str(exc))
        return InstallResult(ok=False, name=name, error=str(exc))
    finally:
        shutil.rmtree(staged, ignore_errors=True)  # GC quarantine (success moved it)


def _rollback_dir(name: str) -> Path:
    return apps_dir() / f".{name}{_ROLLBACK_SUFFIX}"


def update(
    source: str | Path,
    name: str | None = None,
    *,
    origin: str = "local",
    confirm: bool = False,
    caller: str = "app_manager",
) -> InstallResult:
    """Atomically update an installed app to new code at ``source`` (A2).

    State machine, rollback on ANY failure:

      stage+scan new  →  preserve old data/  →  move live → .{name}.rollback
                      →  swap new in  →  run onUpdate
        success:  drop .rollback, re-register, write installed.json
        failure:  restore .rollback → live, re-register OLD, drop the failed new

    The new code is scanned BEFORE the swap (an update is a fresh fetch of mutable
    content), so a now-dangerous update never lands — and the old app is untouched
    if it's refused. A leftover ``.{name}.rollback`` dir signals an update that
    crashed mid-swap; :func:`recover_interrupted_updates` reconciles it at startup.
    """
    src = Path(source)
    if not src.is_dir():
        return InstallResult(ok=False, error=f"source is not a directory: {source}")
    try:
        peek = _load_staged_manifest(src)
    except AppLifecycleError as exc:
        return InstallResult(ok=False, error=str(exc))
    name = name or peek.name
    if _read_installed(name) is None:
        return InstallResult(
            ok=False, name=name, error=f"app {name!r} is not installed (use install)"
        )

    staged_root = _quarantine_dir()
    staged = staged_root / f"{name}{_ROLLBACK_SUFFIX}.new"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(src, staged)

    live = app_dir(name)
    rollback = _rollback_dir(name)
    try:
        manifest = _load_staged_manifest(staged)
        if manifest.name != name:
            return InstallResult(
                ok=False,
                name=name,
                scan=None,
                error=f"manifest name {manifest.name!r} ≠ target {name!r}",
            )
        # Scan the new content (fresh fetch → re-scan; same gate as install).
        report = default_scanner.scan(staged, _tier_for_origin(origin))
        if report.verdict is Verdict.DANGEROUS:
            _audit("update", "refused", name, caller=caller, error="scan: dangerous")
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                error="update refused: scanner flagged dangerous content",
            )
        if report.verdict is Verdict.WARNING and not confirm:
            _audit("update", "needs_consent", name, caller=caller)
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                needs_consent=True,
                error="update needs consent: scanner raised warnings",
            )

        # Preserve the old app's data/ into the new tree (state survives updates).
        old_data = live / _APP_DATA_DIRNAME
        if old_data.is_dir():
            new_data = staged / _APP_DATA_DIRNAME
            if new_data.exists():
                shutil.rmtree(new_data, ignore_errors=True)
            shutil.copytree(old_data, new_data)
        # Preserve installed.json (gateway-written metadata, not part of the app
        # source) so the swapped-in tree keeps its install record + enabled state.
        old_meta_file = live / INSTALLED_META_FILENAME
        if old_meta_file.is_file():
            shutil.copy2(old_meta_file, staged / INSTALLED_META_FILENAME)

        # Deregister old providers before the swap so the registry never points at
        # a half-swapped dir.
        old_manifest = _manifest_of(name)
        _stop_backend(name)  # old backend must release the port before the swap
        _deregister_mcp(name)  # drop old app's MCP servers before the swap
        if old_manifest is not None and old_manifest.all_providers():
            _provider_registry().disable(name)
        # Drop the OLD app's prompt files before the swap (the new tree re-seeds
        # them) so a prompt renamed/removed between versions doesn't linger.
        if old_manifest is not None:
            _remove_app_prompts(old_manifest, name)

        # ── the swap: live → .rollback, new → live ──
        if rollback.exists():
            shutil.rmtree(rollback, ignore_errors=True)
        shutil.move(str(live), str(rollback))
        try:
            shutil.move(str(staged), str(live))
            (live / _APP_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
            _run_hook(
                manifest.setup.onUpdate,
                cwd=live,
                timeout=_HOOK_DEFAULT_TIMEOUT,
                env_name="onUpdate",
            )
        except Exception as exc:  # noqa: BLE001 — ANY swap/hook failure → restore
            # Restore: drop the failed new, move .rollback back to live.
            shutil.rmtree(live, ignore_errors=True)
            if rollback.exists():
                shutil.move(str(rollback), str(live))
            if old_manifest is not None and old_manifest.all_providers():
                _provider_registry().register(old_manifest, enabled=True)
            if old_manifest is not None:
                _register_mcp(old_manifest)  # restore old app's MCP servers
                _start_backend(old_manifest)  # bring the old backend back up
                _seed_app_prompts(old_manifest, name)  # restore old app's prompts
            _audit("update", "error", name, caller=caller, error=str(exc))
            return InstallResult(
                ok=False, name=name, scan=report, error=f"update failed, rolled back: {exc}"
            )

        # Success: drop the rollback, re-register new, bump installed.json.
        shutil.rmtree(rollback, ignore_errors=True)
        # Install any python deps the new version added (before provider re-register
        # so a freshly-imported provider can see them). A new dep ⇒ restart needed.
        restart_required = _install_python_deps(manifest)
        meta = _read_installed(name)
        if meta is not None:
            meta.version = manifest.version
            meta.updatedAt = _now_iso()
            _write_installed(name, meta)
        if manifest.all_providers():
            _provider_registry().register(manifest, enabled=bool(meta and meta.enabled))
        # Re-seed the NEW app's prompts (only when it stays enabled; a disabled app
        # carries no live prompts). Non-clobbering, so a user edit survives.
        if meta is None or meta.enabled:
            _seed_app_prompts(manifest, name)
            _register_mcp(manifest)  # wire the new app's MCP servers
            _start_backend(manifest)  # launch the new backend (skip if disabled)
        _audit("update", "ok", name, caller=caller)
        return InstallResult(ok=True, name=name, scan=report, restart_required=restart_required)
    except AppLifecycleError as exc:
        _audit("update", "error", name, caller=caller, error=str(exc))
        return InstallResult(ok=False, name=name, error=str(exc))
    finally:
        shutil.rmtree(staged, ignore_errors=True)


_SEED_MARKER_FILENAME = ".seeded-builtins.json"


def _seed_marker_path() -> Path:
    return apps_dir() / _SEED_MARKER_FILENAME


def _read_seed_marker() -> set[str]:
    """Names of builtin apps that have ALREADY been seeded (so a user uninstall
    is permanent — a seeded-then-removed app must not resurrect on restart)."""
    p = _seed_marker_path()
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(n) for n in data.get("seeded", [])}
    except (json.JSONDecodeError, OSError):
        return set()


def _write_seed_marker(seeded: set[str]) -> None:
    p = _seed_marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps({"seeded": sorted(seeded)}, indent=2) + "\n")


def _resync_native_manifest(name: str, src_manifest: "Path") -> None:
    """Refresh an already-seeded native app's ``app.json`` from packaged source when
    it differs. Manifest-only: never touches ``data/`` (user config) or
    ``installed.json`` (enabled state). No-op if the app dir is missing or the
    manifest already matches (byte-compare avoids needless writes)."""
    dest_manifest = app_dir(name) / APP_MANIFEST_FILENAME
    if not dest_manifest.parent.is_dir():
        return  # not installed on disk (e.g. seeded marker but dir gone) — leave it
    try:
        src_bytes = src_manifest.read_bytes()
        if dest_manifest.is_file() and dest_manifest.read_bytes() == src_bytes:
            return  # already current — no churn
        dest_manifest.write_bytes(src_bytes)
        logger.info("Re-synced native app manifest %r from packaged source", name)
    except OSError:
        logger.debug("Could not re-sync native manifest %s", name, exc_info=True)


def seed_builtin_apps() -> list[str]:
    """Seed every ``native`` manifest (from ``apps/native/``) as a real
    installed app.

    A native app is visible + configurable in the Apps UI (seeded through the
    installed-app path) but LOCKED ON — disable/uninstall/force-uninstall are
    refused (see the guards in ``disable``/``uninstall``/``force_uninstall``). On
    first run we copy its ``apps/native/<name>/`` dir into
    ``~/.personalclaw/apps/<name>/`` and write an ``installed.json`` (origin
    ``builtin``, enabled), so discovery picks it up through the installed-app path.

    Seed-ONCE by a persisted marker: a name we've seeded before is never re-seeded.
    (Because native apps can't be uninstalled, the marker just avoids clobbering a
    user's config edits on restart.) Returns the names newly seeded this run.
    Called once at startup, BEFORE extension discovery.
    """
    from personalclaw.providers.loader import BUNDLED_DIR

    if not BUNDLED_DIR.is_dir():
        return []
    seeded = _read_seed_marker()
    newly: list[str] = []
    changed = False
    for entry in sorted(BUNDLED_DIR.iterdir()):
        manifest_file = entry / APP_MANIFEST_FILENAME if entry.is_dir() else None
        if not manifest_file or not manifest_file.is_file():
            continue
        try:
            manifest = AppManifest.from_json_file(manifest_file)
        except Exception:
            logger.warning("seed: failed to parse native manifest %s", entry.name, exc_info=True)
            continue
        if not manifest.native:
            continue
        name = manifest.name
        # Seed-once for INSTALL, but re-sync the MANIFEST on every boot. A native app
        # is locked (can't be disabled/uninstalled/edited by the user) and its app.json
        # (schema/description/capabilities) is packaged-source-owned — user config lives
        # separately in data/config.json, which we never touch. So a manifest fix in
        # apps/native/ MUST reach an existing install; the old seed-once-skip stranded
        # it forever (bug #24: the create-task assignee/due/labels schema fix #21 never
        # propagated). Re-copy app.json when it differs; leave data/ + installed.json.
        if name in seeded:
            _resync_native_manifest(name, manifest_file)
            continue
        seeded.add(name)
        changed = True
        dest = app_dir(name)
        if dest.exists():
            # Already present (e.g. a prior partial run) — just mark it seeded.
            newly.append(name)
            continue
        try:
            shutil.copytree(entry, dest)
            (dest / _APP_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
            meta = InstalledApp(
                name=name,
                version=manifest.version,
                displayName=manifest.displayName or name,
                enabled=True,
                installedAt=_now_iso(),
                updatedAt=_now_iso(),
                source="builtin",
                origin="builtin",
            )
            _write_installed(name, meta)
            _audit("seed", "ok", name)
            newly.append(name)
        except Exception as exc:  # noqa: BLE001 — one bad seed must not block the rest
            logger.warning("seed: failed to seed builtin app %s: %s", name, exc)
            shutil.rmtree(dest, ignore_errors=True)
    if changed:
        _write_seed_marker(seeded)
    # One-shot migration: ollama-models was once native (bundled) but was de-cored
    # into a normal first-party app. Its installed.json still says origin="builtin"
    # and it's in the seed marker, which makes _is_native() lock it (disable/uninstall
    # refused). Fix: downgrade origin to "local" and remove from the seed marker so it
    # behaves like every other first-party app (user-manageable).
    _OLLAMA_MIGRATION_NAME = "ollama-models"
    if _OLLAMA_MIGRATION_NAME in seeded:
        ollama_meta = _read_installed(_OLLAMA_MIGRATION_NAME)
        if ollama_meta is not None and ollama_meta.origin == "builtin":
            manifest_check = _manifest_of(_OLLAMA_MIGRATION_NAME)
            if manifest_check is None or not manifest_check.native:
                ollama_meta.origin = "local"
                ollama_meta.updatedAt = _now_iso()
                _write_installed(_OLLAMA_MIGRATION_NAME, ollama_meta)
                logger.info("migrated %s from builtin→local (de-cored)", _OLLAMA_MIGRATION_NAME)
        seeded.discard(_OLLAMA_MIGRATION_NAME)
        _write_seed_marker(seeded)

    return newly


def start_enabled_app_backends() -> list[str]:
    """Launch the backend subprocess for every enabled installed app that
    declares one (called once at gateway startup). Backends are subprocesses —
    they don't survive a gateway restart, so an enabled app would otherwise show
    'backend down' until manually re-enabled. Returns the names started.

    Gated by ``PERSONALCLAW_SKIP_APP_BACKENDS`` (set by the test suite): a test
    that exercises the extension loader must not spawn — or reap — the real
    user's app backends."""
    import os

    from personalclaw.apps.manager import list_apps

    if os.environ.get("PERSONALCLAW_SKIP_APP_BACKENDS"):
        return []

    started: list[str] = []
    for app_info in list_apps():
        if not app_info.get("enabled", False):
            continue
        manifest_data = app_info.get("manifest", {})
        if not manifest_data.get("backend", {}).get("entryPoint"):
            continue
        name = app_info.get("name", "")
        try:
            manifest = AppManifest.from_dict(manifest_data)
            from personalclaw.apps.backend_runtime import get_backend_supervisor

            sup = get_backend_supervisor()
            # Reap any orphans a prior gateway left running for this app (crash /
            # kill -9 / force-exit) BEFORE spawning a fresh one — otherwise each
            # ungraceful restart stacks another backend (reparented to init).
            entry = (app_dir(name) / manifest.backend.entryPoint).resolve()
            sup.reap_orphans(name, entry)
            if sup.start(manifest) is not None:
                started.append(name)
        except Exception:
            logger.warning("app %s: startup backend launch failed", name, exc_info=True)
    return started


def recover_interrupted_updates() -> list[str]:
    """Reconcile leftover ``.{name}.rollback`` dirs from an update that crashed
    mid-swap (called at startup). If ``live`` is missing/empty, restore from the
    rollback; otherwise the swap completed and the rollback is stale — drop it.
    Returns the names recovered."""
    recovered: list[str] = []
    root = apps_dir()
    if not root.is_dir():
        return recovered
    for entry in root.iterdir():
        if not (
            entry.is_dir() and entry.name.startswith(".") and entry.name.endswith(_ROLLBACK_SUFFIX)
        ):
            continue
        name = entry.name[1 : -len(_ROLLBACK_SUFFIX)]
        live = app_dir(name)
        try:
            if not live.exists() or not any(live.iterdir()):
                # Crash between "move live→rollback" and "move new→live": restore.
                if live.exists():
                    shutil.rmtree(live, ignore_errors=True)
                shutil.move(str(entry), str(live))
                recovered.append(name)
                _audit("update_recover", "restored", name)
            else:
                shutil.rmtree(entry, ignore_errors=True)  # stale rollback
                _audit("update_recover", "dropped_stale", name)
        except OSError:
            logger.warning("failed to reconcile rollback dir %s", entry, exc_info=True)
    return recovered


def enable(name: str, *, caller: str = "app_manager") -> bool:
    meta = _read_installed(name)
    if meta is None:
        return False
    manifest = _manifest_of(name)
    if manifest is not None:
        try:
            _run_hook(
                manifest.setup.onEnable,
                cwd=app_dir(name),
                timeout=manifest.setup.onEnableTimeout,
                env_name="onEnable",
            )
        except AppLifecycleError as exc:
            _audit("enable", "error", name, caller=caller, error=str(exc))
            return False
    meta.enabled = True
    meta.updatedAt = _now_iso()
    _write_installed(name, meta)
    if manifest is not None and manifest.all_providers():
        _provider_registry().enable(name)
    if manifest is not None:
        _seed_app_prompts(manifest, name)  # the app OWNS its prompts; seed on enable
        _register_mcp(manifest)
        _start_backend(manifest)
    _audit("enable", "ok", name, caller=caller)
    return True


def _is_native(name: str) -> bool:
    """A native app is locked on — disable/uninstall/force-uninstall refuse.
    Identified by its manifest ``native`` flag (belt-and-suspenders: also the
    ``builtin`` origin, since only native apps seed with that origin)."""
    manifest = _manifest_of(name)
    if manifest is not None and manifest.native:
        return True
    meta = _read_installed(name)
    return meta is not None and getattr(meta, "origin", "") == "builtin"


def disable(name: str, *, caller: str = "app_manager") -> bool:
    meta = _read_installed(name)
    if meta is None:
        return False
    if _is_native(name):
        logger.info("app %s is native (locked) — disable refused", name)
        _audit("disable", "refused_native", name, caller=caller)
        return False
    manifest = _manifest_of(name)
    _stop_backend(name)
    _deregister_mcp(name)
    if manifest is not None and manifest.all_providers():
        _provider_registry().disable(name)
    if manifest is not None:
        _remove_app_prompts(manifest, name)  # drop the app's own seeded prompts
        try:
            _run_hook(
                manifest.setup.onDisable,
                cwd=app_dir(name),
                timeout=manifest.setup.onDisableTimeout,
                env_name="onDisable",
            )
        except AppLifecycleError as exc:
            # Already deregistered; log but don't fail the disable (it IS disabled).
            logger.warning("app %s onDisable hook failed: %s", name, exc)
    meta.enabled = False
    meta.updatedAt = _now_iso()
    _write_installed(name, meta)
    _audit("disable", "ok", name, caller=caller)
    return True


def preview_uninstall(name: str) -> list:
    """Read-only: classify each shared dependency this app declares as
    removable / shared / userInstalled (A3), for the uninstall-confirm UI. Empty
    list if the app or its manifest is absent."""
    manifest = _manifest_of(name)
    if manifest is None:
        return []
    from personalclaw.apps import dependency_ledger

    return dependency_ledger.classify_uninstall(manifest)


def uninstall(name: str, *, caller: str = "app_manager") -> bool:
    """Uninstall = DEACTIVATE (keep files). An app the user 'uninstalls' is turned
    OFF, not deleted: its providers deregister, backend stops, MCP servers drop,
    and ``installed.json.enabled`` flips to false — but the files stay on disk so
    it can be re-activated instantly (no re-fetch) and its data/ is preserved.

    True filesystem removal is :func:`force_uninstall` (the hidden 'Advanced →
    Force uninstall'). This mirrors how a provider app's install IS its on-switch:
    uninstall is the off-switch, force-uninstall is the eradicate."""
    meta = _read_installed(name)
    if meta is None:
        return False
    if _is_native(name):
        logger.info("app %s is native (locked) — uninstall refused", name)
        _audit("uninstall", "refused_native", name, caller=caller)
        return False
    # Deactivate via the same teardown as disable, but audit it as an uninstall.
    ok = disable(name, caller=caller)
    if ok:
        _audit("uninstall", "ok", name, caller=caller)
    return ok


def force_uninstall(name: str, *, caller: str = "app_manager") -> bool:
    """Run onUninstall → deregister → consult the dependency ledger → REMOVE FILES.

    The hidden, destructive path (Advanced → Force uninstall): the app's own files
    are removed from disk. Shared dependencies (still needed by another installed
    app) and user-installed ones are LEFT; only deps this app solely owned are
    eligible for removal (the caller/marketplace does the actual dep removal — the
    ledger decides *which*). A force-removed default-seeded app stays gone (the
    seed-once marker is not cleared)."""
    meta = _read_installed(name)
    if meta is None:
        return False
    if _is_native(name):
        logger.info("app %s is native (locked) — force-uninstall refused", name)
        _audit("force_uninstall", "refused_native", name, caller=caller)
        return False
    manifest = _manifest_of(name)
    _stop_backend(name)
    _deregister_mcp(name)
    if manifest is not None:
        _remove_app_prompts(manifest, name)  # drop the app's own seeded prompts
        try:
            _run_hook(
                manifest.setup.onUninstall,
                cwd=app_dir(name),
                timeout=_HOOK_DEFAULT_TIMEOUT,
                env_name="onUninstall",
            )
        except AppLifecycleError as exc:
            logger.warning("app %s onUninstall hook failed (removing anyway): %s", name, exc)
    if manifest is not None and manifest.all_providers():
        # Forget it entirely (not just disable) so it doesn't linger as a disabled
        # ghost in the providers list until the next restart.
        _provider_registry().deregister(name)
    # Consult + update the dependency ledger BEFORE removing files (so 'removable'
    # reflects this app's departure). Shared/userInstalled deps are kept.
    if manifest is not None:
        try:
            from personalclaw.apps import dependency_ledger

            removed = dependency_ledger.record_uninstall(manifest)
            kept = [
                c.key
                for c in removed
                if c.disposition is not dependency_ledger.DepDisposition.REMOVABLE
            ]
            if kept:
                logger.info("app %s force-uninstall: keeping shared/user deps %s", name, kept)
        except Exception:
            logger.debug("app %s: dependency-ledger uninstall failed", name, exc_info=True)
    shutil.rmtree(app_dir(name), ignore_errors=True)
    _audit("force_uninstall", "ok", name, caller=caller)
    return True


def _manifest_of(name: str) -> AppManifest | None:
    # A path-escaping name (app_dir now rejects '../', '/etc', … — #44) is simply
    # "not an installed app": return None so callers/routes 404 cleanly rather than
    # surfacing the guard's ValueError as an unhandled 500.
    try:
        mpath = app_dir(name) / APP_MANIFEST_FILENAME
    except ValueError:
        return None
    if not mpath.is_file():
        return None
    try:
        return AppManifest.from_json_file(mpath)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: manifest load failed", name, exc_info=True)
        return None
