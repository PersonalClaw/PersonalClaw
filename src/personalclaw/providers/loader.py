"""Extension Loader — discovers and loads native + installed extensions at startup.

Scans two sources:
1. NATIVE apps from ``personalclaw/apps/native/`` (ship inside core)
2. Installed extensions from ``~/.personalclaw/apps/`` (user-installed via marketplace)

For each extension with a ``provider`` section in its manifest, registers it
with the :class:`~personalclaw.providers.registry.ProviderRegistry`.
"""

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.manager import list_apps, app_dir

if TYPE_CHECKING:
    from personalclaw.providers.registry import RegisteredProvider

logger = logging.getLogger(__name__)

# Native apps ship inside the package at personalclaw/apps/native/.
# (loader.py lives in personalclaw/providers/, so go up one to personalclaw/.)
BUNDLED_DIR = Path(__file__).parent.parent / "apps" / "native"


def discover_bundled_extensions() -> list[AppManifest]:
    """Scan ``personalclaw/apps/native/`` for native-app manifests."""
    manifests: list[AppManifest] = []
    if not BUNDLED_DIR.is_dir():
        return manifests
    for entry in sorted(BUNDLED_DIR.iterdir()):
        manifest_file = entry / "app.json" if entry.is_dir() else None
        if not manifest_file or not manifest_file.is_file():
            continue
        try:
            manifest = AppManifest.from_json_file(manifest_file)
            # Native apps are seeded as real installed apps (seed_builtin_apps) and
            # register through the installed-app path; skip them here so they never
            # register twice. (Post-taxonomy every native-dir app is native:true, so
            # this list is normally empty — kept as a guard against a stray manifest.)
            if manifest.provider and not manifest.native:
                manifests.append(manifest)
        except Exception:
            logger.warning("Failed to parse bundled extension: %s", entry.name, exc_info=True)
    return manifests


def discover_installed_extensions() -> list[tuple[AppManifest, bool]]:
    """Scan installed apps for extensions with provider declarations.

    Returns (manifest, enabled) pairs.
    """
    results: list[tuple[AppManifest, bool]] = []
    for app_info in list_apps():
        manifest_data = app_info.get("manifest", {})
        if not manifest_data.get("provider"):
            continue
        try:
            manifest = AppManifest.from_dict(manifest_data)
            if manifest.provider:
                enabled = app_info.get("enabled", False)
                results.append((manifest, enabled))
        except Exception:
            logger.warning(
                "Failed to parse installed extension: %s",
                app_info.get("name", "?"),
                exc_info=True,
            )
    return results


def _load_ext_module(ext: "RegisteredProvider", module_path: str) -> Any:
    """Import an extension's implementation module.

    A BUNDLED extension's ``module_path`` is a real dotted package path
    (``personalclaw.search_providers.foo``) — import it normally. An INSTALLED
    app's ``module_path`` is a bare top-level name relative to its own dir
    (``provider``, ``main``); two apps commonly share such a name, so importing
    it as-is would collide in ``sys.modules`` (the first app's module wins and
    the second silently mis-loads). Load it from the app's own file under a
    namespaced module name (``_pclaw_app_{name}__{module}``) so apps never
    clash. Falls back to plain import for path-based bundled modules.
    """
    ext_dir = _resolve_ext_dir(ext)
    is_bundled = ext_dir is not None and ext_dir == BUNDLED_DIR / ext.name
    # Bundled extension → its module is a real package module; import normally.
    if is_bundled or "." in module_path:
        added = False
        if ext_dir and str(ext_dir) not in sys.path:
            sys.path.insert(0, str(ext_dir)); added = True
        try:
            return importlib.import_module(module_path)
        finally:
            if added:
                sys.path.remove(str(ext_dir))
    # Installed app → load the file directly under a namespaced module name so
    # two apps that both ship e.g. provider.py can't collide in sys.modules.
    if ext_dir is None:
        return importlib.import_module(module_path)  # last resort

    rel = module_path.replace(".", "/") + ".py"
    file_path = ext_dir / rel
    if not file_path.is_file():
        # Fall back to sys.path import (e.g. package dir module) under namespacing.
        added = str(ext_dir) not in sys.path
        if added:
            sys.path.insert(0, str(ext_dir))
        try:
            return importlib.import_module(module_path)
        finally:
            if added:
                sys.path.remove(str(ext_dir))
    unique_name = f"_pclaw_app_{ext.name.replace('-', '_')}__{module_path.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(unique_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_path!r} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    # Register under the unique name + put the app dir on sys.path during exec so
    # the module's own sibling imports still resolve.
    sys.modules[unique_name] = module
    added = str(ext_dir) not in sys.path
    if added:
        sys.path.insert(0, str(ext_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        if added:
            sys.path.remove(str(ext_dir))
    return module


def load_factory(ext: "RegisteredProvider") -> Callable[..., Any]:
    """Import and return the factory function from an extension's implementation path.

    The implementation path format is ``module.path:factory_fn``.
    For bundled extensions, the module is resolved from the backend package.
    For installed apps, the module is loaded from the app's own file under a
    namespaced name so two apps sharing a module name can't collide.
    """
    impl_path = ext.provider_config.implementation
    module_path, _, func_name = impl_path.rpartition(":")
    if not module_path or not func_name:
        raise ValueError(f"Invalid implementation path: {impl_path!r}")
    module = _load_ext_module(ext, module_path)
    return getattr(module, func_name)


def load_availability(ext: "RegisteredProvider") -> "Callable[[], tuple[bool, str]] | None":
    """Return an extension's optional ``availability()`` probe, or ``None``.

    A bundle whose provider can be unusable on a given machine (e.g. it wraps a
    binary that isn't installed) may export a module-level ``availability()``
    returning ``(available: bool, reason: str)``. The extension-list API calls
    it so the UI can grey out + block-enable a provider that would only ever
    fail — without the core knowing anything vendor-specific. Resolved from the
    same ``module.path`` as the ``implementation`` entry-point; ``None`` when the
    module defines no such hook (the common case).
    """
    impl_path = ext.provider_config.implementation
    module_path, _, _ = impl_path.rpartition(":")
    if not module_path:
        return None
    try:
        module = _load_ext_module(ext, module_path)
        fn = getattr(module, "availability", None)
        return fn if callable(fn) else None
    except Exception:
        return None


def _resolve_ext_dir(ext: "RegisteredProvider") -> Path | None:
    """Determine the filesystem root for an extension's code."""
    from personalclaw.providers.registry import RegisteredProvider

    name = ext.name
    bundled_path = BUNDLED_DIR / name
    if bundled_path.is_dir():
        return bundled_path
    installed_path = app_dir(name)
    if installed_path.is_dir():
        return installed_path
    return None


def _seed_extension_prompts(manifest: AppManifest, *, enabled: bool) -> None:
    """Seed an extension's declared prompts at startup (an app OWNS its prompts).

    Resolves the extension's dir (bundled or installed) and writes its prompt/
    snippet definitions into the native store, idempotent + non-clobbering — the
    same discipline core uses for its catalog. A disabled installed extension
    carries no live prompts, so seeding is skipped for it. Best-effort: never
    breaks discovery."""
    if not getattr(manifest, "prompts", None) or not enabled:
        return
    name = manifest.name
    ext_dir = BUNDLED_DIR / name
    if not ext_dir.is_dir():
        ext_dir = app_dir(name)
    if not ext_dir.is_dir():
        return
    try:
        from personalclaw.apps.prompt_seed import seed_app_prompts
        seed_app_prompts(manifest, ext_dir)
    except Exception:
        logger.debug("extension %s: prompt seed failed", name, exc_info=True)


def _seed_promptonly_installed_apps() -> None:
    """Seed prompts for enabled installed apps that declare prompts but NO provider
    (so they aren't in ``discover_installed_extensions``). Best-effort."""
    for app_info in list_apps():
        if not app_info.get("enabled", False):
            continue
        manifest_data = app_info.get("manifest", {})
        if manifest_data.get("provider") or manifest_data.get("providers"):
            continue  # provider apps already seeded via the discovery path
        if not manifest_data.get("prompts"):
            continue
        try:
            manifest = AppManifest.from_dict(manifest_data)
            _seed_extension_prompts(manifest, enabled=True)
        except Exception:
            logger.debug("prompt-only app %s: seed failed",
                         app_info.get("name", "?"), exc_info=True)


def load_all_extensions() -> None:
    """Main entry point: discover and register all extensions.

    Called once during gateway startup.
    """
    from personalclaw.providers.registry import get_provider_registry

    registry = get_provider_registry()

    # Reconcile any app update that crashed mid-swap BEFORE discovery reads the
    # apps tree (A2 crash recovery) — restore a half-swapped app from its
    # leftover .{name}.rollback dir, or drop a stale one.
    try:
        from personalclaw.apps.app_manager import recover_interrupted_updates

        recovered = recover_interrupted_updates()
        if recovered:
            logger.info("Recovered interrupted app updates: %s", recovered)
    except Exception:
        logger.debug("app update recovery failed", exc_info=True)

    # Seed native apps as real installed apps (first run only; seed-once
    # marker). MUST run before discovery so the seeded apps are picked up via the
    # installed-app path.
    try:
        from personalclaw.apps.app_manager import seed_builtin_apps

        seeded = seed_builtin_apps()
        if seeded:
            logger.info("Seeded default-installed apps: %s", seeded)
    except Exception:
        logger.debug("default-app seeding failed", exc_info=True)

    for manifest in discover_bundled_extensions():
        registry.register(manifest, enabled=True)
        # An always-on bundled provider OWNS its prompts: seed them at startup the
        # same way core seeds its catalog (idempotent, non-clobbering).
        _seed_extension_prompts(manifest, enabled=True)
        logger.debug("Registered bundled extension: %s", manifest.name)

    for manifest, enabled in discover_installed_extensions():
        registry.register(manifest, enabled=enabled)
        _seed_extension_prompts(manifest, enabled=enabled)
        logger.debug(
            "Registered installed extension: %s (enabled=%s)", manifest.name, enabled
        )

    # An installed app that has NO provider (a pure prompts/skills/sops app) is not
    # in either discovery list above, yet still owns prompts it must seed at startup.
    _seed_promptonly_installed_apps()

    # Relaunch enabled apps' backend subprocesses (they don't survive a gateway
    # restart) so an installed+enabled app's reverse-proxy is live from startup.
    # Then start a watchdog that periodically checks + revives crashed backends.
    try:
        from personalclaw.apps.app_manager import start_enabled_app_backends

        started = start_enabled_app_backends()
        if started:
            logger.info("Started enabled app backends: %s", started)
        from personalclaw.apps.backend_runtime import start_backend_watchdog

        start_backend_watchdog()
    except Exception:
        logger.debug("app backend startup launch failed", exc_info=True)
