"""Extension Loader — starts the installed apps at gateway startup, and imports an app's code.

Every app is an installed app: the native ones ship inside core (``personalclaw/apps/native/``)
and are seeded into ``~/.personalclaw/apps/`` beside the ones the user installed, so one walk
starts them all — :func:`personalclaw.apps.app_runtime.start_installed`, which loads each enabled
app through the same load an enable runs. This module is the startup around it (the steps that
come before any app can load, and the watchdogs after) and the import of an app's own modules.
"""

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from personalclaw.apps.manager import app_dir
from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    app_dir_on_path,
    bundle_module_file,
    load_bundle_module,
)

if TYPE_CHECKING:
    from personalclaw.providers.registry import RegisteredProvider

logger = logging.getLogger(__name__)

# Native apps ship inside the package at personalclaw/apps/native/. The directory is
# owned by apps/native_contract.py (the lower layer); this is the loader's alias for it.
BUNDLED_DIR = NATIVE_DIR


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
    with app_dir_on_path(ext.name, ext_dir):
        return importlib.import_module(module_path)


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
    returning ``(available: bool, reason: str)``, so the UI can grey out +
    block-enable a provider that would only ever fail — without the core knowing
    anything vendor-specific. Resolved from the same ``module.path`` as the
    ``implementation`` entry-point; ``None`` when the module defines no such hook
    (the common case).

    Called ONLY by the availability probe child (``providers/availability_probe.py``):
    resolving a hook imports the app's module, and running one is app code of
    unbounded cost, so neither ever happens inside the gateway. The hook's
    cheapness rule — and the check to build it from — is
    :mod:`personalclaw.sdk.availability`.

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


def _start_installed_apps(*, gateway: bool) -> None:
    """Start every installed app, in a process that has just started — the shared startup.

    First what any app's load needs: the installed apps' Python packages (``<home>/app-python``)
    on the import path, after the interpreter's own entries, and the native apps seeded as
    installed apps (first run; after that their packaged files are refreshed), so the walk below
    finds them. Then the walk itself (:func:`personalclaw.apps.app_runtime.start_installed`),
    and last the one generic app-route tool provider (§4.2), which surfaces every enabled app's
    ``agentCallable`` backend routes as ``app_<name>_<op>`` tools and reads the installed apps
    live on each listing, so registering it once is enough.
    """
    from personalclaw.apps import app_python, app_runtime

    app_python.activate()
    try:
        from personalclaw.apps.app_manager import seed_builtin_apps

        seeded = seed_builtin_apps()
        if seeded:
            logger.info("Seeded default-installed apps: %s", seeded)
    except Exception:
        logger.debug("default-app seeding failed", exc_info=True)

    app_runtime.start_installed(gateway=gateway)

    try:
        from personalclaw.tool_providers.app_routes import register as _register_app_routes

        _register_app_routes()
    except Exception:
        logger.debug("app-routes tool provider registration failed", exc_info=True)


def register_extension_providers() -> None:
    """Load every enabled app's providers IN THIS PROCESS — for a process that is not the gateway.

    Importing an app's module is what runs its module-level ``register_type`` /
    ``register_scanner`` / ``register_catalog`` and so wires its provider type, media scanners
    and discovery catalog into the process-wide registries. Any entry point that resolves
    providers OUTSIDE the gateway (a CLI command, a worker) must call this — otherwise only core's
    built-in providers are visible and an app-contributed provider (Bedrock embedding, …) silently
    reads as "unknown provider" / "no executor" there, even though the same app resolves fine
    inside the gateway (ES-3).

    The same walk gateway startup makes (:func:`load_all_extensions`), with each enabled app
    loaded as far as this process runs any of it: its providers, prompts, skills and proposal
    kinds. It starts NO app server or subprocess and NO watchdog thread; those are the long-lived
    gateway's concern, not a short-lived process's.
    """
    _start_installed_apps(gateway=False)


def bootstrap_cli_providers() -> None:
    """Register app-contributed providers for a standalone (non-gateway) process.

    Mirrors the gateway's provider-init sequence (``dashboard/server.py`` right after
    :func:`load_all_extensions`) so a CLI command that builds a real embedding/chat
    provider resolves it the same way the gateway does — but WITHOUT launching the
    gateway's app-backend subprocesses or watchdogs (see
    :func:`register_extension_providers`). Three steps, in the gateway's order:

    1. import + register the installed provider apps (their types + media scanners);
    2. migrate any legacy Settings > Models bindings (best-effort);
    3. replay ``config.json`` provider entries into the LLM registry so config-defined
       providers (ollama, openai-compatible, …) resolve too.

    Idempotent and safe to call once at the start of a provider-dependent command.
    """
    register_extension_providers()
    try:
        from personalclaw.providers.use_cases import migrate_legacy_bindings

        migrate_legacy_bindings()
    except Exception:
        logger.debug("legacy binding migration failed", exc_info=True)
    try:
        from personalclaw.llm.registry import sync_entries_from_config

        sync_entries_from_config()
    except Exception:
        logger.debug("config provider-entry sync failed", exc_info=True)


def build_channel_transports() -> list[Any]:
    """The installed + enabled channel apps' transports, BUILT but not registered.

    For a short-lived process that asks a channel a question without booting the provider
    registry — ``personalclaw setup`` asking whether any channel is configured. Built through
    the same handler the registry enables them with (the app's factory over its saved
    settings), so a transport answers here exactly as it does in the gateway; nothing is
    registered and no receiver starts. An app whose transport cannot be built is skipped,
    and says why.
    """
    from personalclaw.apps import app_python, app_runtime
    from personalclaw.providers.registry import ChannelTypeHandler, RegisteredProvider

    app_python.activate()
    built: list[Any] = []
    for manifest, enabled in app_runtime.installed():
        if not enabled:
            continue
        for cfg in manifest.all_providers():
            if cfg.type != "channel":
                continue
            ext = RegisteredProvider(name=manifest.name, manifest=manifest, provider_config=cfg)
            try:
                built.append(ChannelTypeHandler().create(ext))
            except Exception:
                logger.warning(
                    "channel app %s: transport could not be built", ext.name, exc_info=True
                )
    return built


def _repair_app_packages_logged(repair: Callable[[], list[str]]) -> None:
    try:
        repaired = repair()
        if repaired:
            logger.info("Reinstalled missing Python packages for apps: %s", repaired)
    except Exception:
        logger.warning("app package repair failed", exc_info=True)


def load_all_extensions() -> None:
    """Gateway startup: every installed app started through the one load, then the watchdogs.

    Called once, from the dashboard's boot block, before ``config.json``'s provider entries are
    replayed into the model registry — every app's provider TYPES are registered by then. Each
    enabled app starts exactly as an enable starts it
    (:func:`personalclaw.apps.app_runtime.start_installed`): its code and registrations, then its
    MCP servers, backend and background worker. A non-gateway entry point (a CLI command, a
    worker) calls :func:`register_extension_providers` — or the CLI wrapper
    :func:`bootstrap_cli_providers` — instead, so it does not also spawn processes it will never
    supervise.
    """
    # Reconcile any app update that crashed mid-swap BEFORE the walk reads the apps tree (A2
    # crash recovery) — restore a half-swapped app from its leftover .{name}.rollback dir, or
    # drop a stale one. A gateway-restart concern, so a CLI process's startup does not do it.
    try:
        from personalclaw.apps.app_manager import recover_interrupted_updates

        recovered = recover_interrupted_updates()
        if recovered:
            logger.info("Recovered interrupted app updates: %s", recovered)
    except Exception:
        logger.debug("app update recovery failed", exc_info=True)

    _start_installed_apps(gateway=True)

    # Rebuild whatever the installed apps' Python packages are missing — a new image's Python,
    # a changed core dependency, a restored snapshot. Off the boot path: it can run pip for
    # minutes, and the apps it repairs are started again when it finishes.
    try:
        import threading

        from personalclaw.apps.app_manager import repair_app_packages

        threading.Thread(
            target=_repair_app_packages_logged,
            args=(repair_app_packages,),
            name="app-packages-repair",
            daemon=True,
        ).start()
    except Exception:
        logger.debug("app package repair did not start", exc_info=True)

    # The load above started every enabled app's backend and worker; these sweeps keep them
    # running from here on — each revives a crashed child and stops one whose app went away.
    try:
        from personalclaw.apps.backend_runtime import start_backend_watchdog

        start_backend_watchdog()
        # APE-3: the same sweep shape for app background WORKERS — portless children with no
        # health check, so liveness is `proc.poll()` and nothing stronger.
        from personalclaw.apps.worker_runtime import start_worker_watchdog

        start_worker_watchdog()
        # Same semantics, different children: a model sidecar (LMMV §3.1) is respawned on
        # crash and never survives the gateway. A sweep over an empty runner table is
        # free, so this costs nothing until an app declares `execution: sidecar`.
        from personalclaw.local_models.sidecar import start_sidecar_watchdog

        start_sidecar_watchdog()
    except Exception:
        logger.debug("app watchdog startup failed", exc_info=True)


def stop_extension_watchdogs() -> None:
    """Stop the three sweepers :func:`load_all_extensions` started — the gateway's cleanup half.

    Each is stopped separately so one failure cannot leave the other two running. Called BEFORE
    the backends are terminated: a backend watchdog still sweeping would revive them.
    """
    from personalclaw.apps.backend_runtime import stop_backend_watchdog
    from personalclaw.apps.worker_runtime import stop_worker_watchdog
    from personalclaw.local_models.sidecar import stop_sidecar_watchdog

    for stop in (stop_backend_watchdog, stop_worker_watchdog, stop_sidecar_watchdog):
        try:
            stop()
        except Exception:
            logger.debug("stopping %s failed", stop.__name__, exc_info=True)
