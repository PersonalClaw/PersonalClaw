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

from personalclaw.apps.manager import app_dir, list_apps
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    bundle_module_file,
    load_bundle_module,
)

if TYPE_CHECKING:
    from personalclaw.providers.registry import RegisteredProvider

logger = logging.getLogger(__name__)

# Native apps ship inside the package at personalclaw/apps/native/. The directory is
# owned by apps/native_contract.py (the lower layer); this is the loader's alias for it.
BUNDLED_DIR = NATIVE_DIR


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

    ONE rule for both tiers (APE-5): if ``module_path`` resolves to a file inside the
    extension's own directory, load it from there under a namespaced module name
    (``apps/native_contract.load_bundle_module``); otherwise ``module_path`` is a real
    dotted package path (``personalclaw.tasks.native``) and is imported normally.

    Namespacing is what makes the bundle-local form safe: two apps commonly ship the same
    bare module name (``provider``, ``main``), and a plain ``import provider`` would let
    the first app's module win while the second silently mis-loads. That protection used
    to apply to INSTALLED apps only — a bundled app was routed to the plain-import branch
    by tier, so the moment two bundles shipped ``provider.py`` one of them would have
    loaded the other's code. The tier test is gone; the file test replaces it.
    """
    ext_dir = _resolve_ext_dir(ext)
    if ext_dir is not None and bundle_module_file(ext_dir, module_path) is not None:
        return load_bundle_module(ext_dir, ext.name, module_path)
    # Not a file in the app's dir → a dotted package path, or a package DIRECTORY module
    # reached through the app dir on sys.path.
    added = False
    if ext_dir and str(ext_dir) not in sys.path:
        sys.path.insert(0, str(ext_dir))
        added = True
    try:
        return importlib.import_module(module_path)
    finally:
        if added and ext_dir and str(ext_dir) in sys.path:
            sys.path.remove(str(ext_dir))


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

    A branded model app that rides an agent CLI's subscription login has exactly
    one way to be unusable — that CLI is not signed in — so it does not have to
    hand-write the hook: when the module exports none, a probe is DERIVED from the
    ``credential_source`` its registered spec declares. An explicit hook still wins,
    since an app that wrote one knows something extra about its own machine.
    """
    impl_path = ext.provider_config.implementation
    module_path, _, _ = impl_path.rpartition(":")
    if not module_path:
        return None
    try:
        module = _load_ext_module(ext, module_path)
        fn = getattr(module, "availability", None)
        if callable(fn):
            return fn
    except Exception:
        logger.debug("availability hook lookup failed for %s", ext.name, exc_info=True)
    return _subscription_availability(ext)


def _subscription_availability(
    ext: "RegisteredProvider",
) -> "Callable[[], tuple[bool, str]] | None":
    """A ``(bool, reason)`` probe derived from the ext's declared subscription source.

    ``None`` for every provider that declares none — which is all of them but a
    subscription model app. Importing the app's module (done by the caller, just above) is
    what populated the spec registry, so this reads a live declaration rather than
    guessing. The reason text is the APP's own ``login_hint``: core never names a vendor's
    login verb.
    """
    provider_type = str(getattr(ext.provider_config, "providerType", "") or "").strip()
    if not provider_type:
        return None
    try:
        from personalclaw.llm.branded_specs import spec_credential_source
        from personalclaw.llm.subscription_credentials import subscription_source_status

        source = spec_credential_source(provider_type)
    except Exception:
        return None
    if not source:
        return None
    return lambda: subscription_source_status(source)


def _resolve_ext_dir(ext: "RegisteredProvider") -> Path | None:
    """Determine the filesystem root for an extension's code."""

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


def _installed_origin(name: str) -> str:
    """The recorded install origin for an installed app (default ``local``)."""
    try:
        from personalclaw.apps.app_manager import _origin_of

        return _origin_of(name)
    except Exception:
        return "local"


def _seed_extension_skills(manifest: AppManifest, *, enabled: bool, origin: str) -> None:
    """Seed an extension's declared SKILL.md skills at startup (an app OWNS its skills).

    Mirrors :func:`_seed_extension_prompts` but routes through the supply-chain
    chokepoint at the app's trust ``origin`` — an app skill never bypasses the gate
    (§4.1). A disabled installed extension carries no live skills, so seeding is
    skipped for it. Best-effort: never breaks discovery."""
    if not getattr(manifest, "skills", None) or not enabled:
        return
    name = manifest.name
    ext_dir = BUNDLED_DIR / name
    if not ext_dir.is_dir():
        ext_dir = app_dir(name)
    if not ext_dir.is_dir():
        return
    try:
        from personalclaw.apps.skill_seed import seed_app_skills

        seed_app_skills(manifest, ext_dir, origin=origin)
    except Exception:
        logger.debug("extension %s: skill seed failed", name, exc_info=True)


def _seed_promptonly_installed_apps() -> None:
    """Seed prompts + skills for enabled installed apps that declare them but have NO
    provider (so they aren't in ``discover_installed_extensions``). Best-effort."""
    for app_info in list_apps():
        if not app_info.get("enabled", False):
            continue
        manifest_data = app_info.get("manifest", {})
        if manifest_data.get("provider") or manifest_data.get("providers"):
            continue  # provider apps already seeded via the discovery path
        if not manifest_data.get("prompts") and not manifest_data.get("skills"):
            continue
        try:
            manifest = AppManifest.from_dict(manifest_data)
            _seed_extension_prompts(manifest, enabled=True)
            _seed_extension_skills(
                manifest, enabled=True, origin=str(app_info.get("origin", "") or "local")
            )
        except Exception:
            logger.debug(
                "prompt-only app %s: seed failed", app_info.get("name", "?"), exc_info=True
            )


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
        # An always-on bundled provider OWNS its prompts + skills: seed them at
        # startup the same way core seeds its catalog (idempotent, non-clobbering).
        # A bundled extension is native → the ``builtin`` trust tier.
        _seed_extension_prompts(manifest, enabled=True)
        _seed_extension_skills(manifest, enabled=True, origin="builtin")
        logger.debug("Registered bundled extension: %s", manifest.name)

    for manifest, enabled in discover_installed_extensions():
        registry.register(manifest, enabled=enabled)
        _seed_extension_prompts(manifest, enabled=enabled)
        _seed_extension_skills(manifest, enabled=enabled, origin=_installed_origin(manifest.name))
        logger.debug("Registered installed extension: %s (enabled=%s)", manifest.name, enabled)

    # An installed app that has NO provider (a pure prompts/skills/sops app) is not
    # in either discovery list above, yet still owns prompts it must seed at startup.
    _seed_promptonly_installed_apps()

    # The single generic app-route tool provider (§4.2): surfaces every enabled
    # app's declared agentCallable backend routes as ``app_<name>_<op>`` tools. It
    # reads the installed apps live on each list_tools, so enable/disable/update
    # resync for free — registering it once here is enough.
    try:
        from personalclaw.tool_providers.app_routes import register as _register_app_routes

        _register_app_routes()
    except Exception:
        logger.debug("app-routes tool provider registration failed", exc_info=True)

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
        # APE-3: the same sweep shape for app background WORKERS — portless children with no
        # health check, so liveness is `proc.poll()` and nothing stronger. Deliberately not
        # paired with a `start_enabled_app_workers()` call above: the sweep is self-healing
        # (it starts a declared worker for an enabled app and stops one whose app, grant or
        # declaration went away), so boot needs no second entry point that could disagree
        # with it about who should be running.
        from personalclaw.apps.worker_runtime import start_worker_watchdog

        start_worker_watchdog()
        # Same semantics, different children: a model sidecar (LMMV §3.1) is respawned on
        # crash and never survives the gateway. A sweep over an empty runner table is
        # free, so this costs nothing until an app declares `execution: sidecar`.
        from personalclaw.local_models.sidecar import start_sidecar_watchdog

        start_sidecar_watchdog()
    except Exception:
        logger.debug("app backend startup launch failed", exc_info=True)
