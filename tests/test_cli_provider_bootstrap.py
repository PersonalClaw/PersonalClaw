"""ES-3 — a standalone (non-gateway) process must bootstrap app-contributed providers.

The gap this closes: the gateway imports every enabled provider app's module at boot
(``providers.loader.load_all_extensions``), which is what runs the app's module-level
``register_type`` / ``register_scanner`` / ``register_catalog`` and makes its provider
resolvable. A CLI command (``personalclaw retrieval-eval`` and the rest of the eval
family) runs in its OWN process that never did that, so its provider registry was empty
— an app-provided embedding provider (Bedrock) was invisible and the retrieval bench's
vector arm reported "no executor" even with the embedder bound.

These tests assert the extracted, reusable registration path (:func:`register_extension_providers`)
imports an enabled installed provider app's module in this process, skips a disabled
one, that the CLI wrapper (:func:`bootstrap_cli_providers`) runs it plus the config sync
the gateway runs, and that the gateway's own ``load_all_extensions`` still delegates to
the shared path AND keeps launching its backend subprocesses + watchdogs.

Home isolation: ``conftest``'s autouse fixture re-points ``config_dir`` (and thus
``apps_dir``) under a tmp home, so the fake app is written + discovered there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from personalclaw.apps.manager import apps_dir
from personalclaw.apps.native_contract import namespaced_module_name
from personalclaw.providers import loader
from personalclaw.providers.registry import get_provider_registry, reset_provider_registry

_APP = "es3-bootstrap-probe"


def _install_provider_app(*, enabled: bool, receipt: Path) -> None:
    """Write an installed model-provider app whose ``provider.py`` records the fact
    that it was imported by touching ``receipt`` at module load — exactly the seam a
    real app (bedrock-models) uses to register its scanners on import."""
    app_root = apps_dir() / _APP
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "name": _APP,
                "version": "1.0.0",
                "displayName": "ES-3 Bootstrap Probe",
                "description": "test-only provider app",
                "provider": {
                    "type": "model",
                    "providerType": "es3-probe",
                    "implementation": "provider:create_provider",
                    "capabilities": ["embedding"],
                },
            }
        ),
        encoding="utf-8",
    )
    (app_root / "installed.json").write_text(
        json.dumps(
            {
                "name": _APP,
                "version": "1.0.0",
                "enabled": enabled,
                "origin": "local",
                "lifecycle": "gateway",
                "resources": "gateway",
            }
        ),
        encoding="utf-8",
    )
    (app_root / "provider.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(receipt)!r}).write_text('imported', encoding='utf-8')\n\n\n"
        "def create_provider(config=None):\n"
        "    return object()\n",
        encoding="utf-8",
    )


@pytest.fixture()
def _only_the_probe_app(monkeypatch):
    """Neutralise the native-app seeding + bundled discovery so the registration pass
    processes ONLY the fake installed app under test — fast + hermetic. Restores the
    process-global provider registry and drops the fake module afterwards."""
    monkeypatch.setattr("personalclaw.apps.app_manager.seed_builtin_apps", lambda: [])
    monkeypatch.setattr(loader, "discover_bundled_extensions", lambda: [])
    try:
        yield
    finally:
        get_provider_registry().deregister(_APP)
        reset_provider_registry()
        sys.modules.pop(namespaced_module_name(_APP, "provider"), None)


def test_register_extension_providers_imports_an_enabled_installed_app(
    tmp_path, _only_the_probe_app
):
    receipt = tmp_path / "imported.flag"
    _install_provider_app(enabled=True, receipt=receipt)

    # A standalone process that has NOT bootstrapped has not imported the app.
    assert not receipt.exists()

    loader.register_extension_providers()

    assert receipt.read_text(encoding="utf-8") == "imported", (
        "an enabled installed provider app's module must be imported by the CLI "
        "registration path (this is what registers an app's provider/scanner)"
    )
    assert get_provider_registry().get(_APP) is not None


def test_register_extension_providers_skips_a_disabled_installed_app(tmp_path, _only_the_probe_app):
    receipt = tmp_path / "imported.flag"
    _install_provider_app(enabled=False, receipt=receipt)

    loader.register_extension_providers()

    assert not receipt.exists(), "a DISABLED app's module must not be imported"


def test_bootstrap_cli_providers_registers_then_syncs_config(monkeypatch):
    """The CLI wrapper mirrors the gateway's provider-init order: register the
    extension providers, migrate legacy bindings, then replay config.json entries into
    the LLM registry — so config-defined providers resolve in the CLI process too."""
    order: list[str] = []
    monkeypatch.setattr(loader, "register_extension_providers", lambda: order.append("register"))
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.migrate_legacy_bindings",
        lambda: order.append("migrate") or True,
    )
    monkeypatch.setattr(
        "personalclaw.llm.registry.sync_entries_from_config",
        lambda: order.append("sync") or 0,
    )

    loader.bootstrap_cli_providers()

    assert order == ["register", "migrate", "sync"]


def test_load_all_extensions_delegates_and_keeps_the_gateway_tail(monkeypatch):
    """The refactor must not lose either half: ``load_all_extensions`` still runs the
    shared registration path AND still launches the gateway's backend subprocesses +
    watchdogs (which a plain CLI bootstrap deliberately does not)."""
    calls: list[str] = []
    monkeypatch.setattr(
        "personalclaw.apps.app_manager.recover_interrupted_updates",
        lambda: calls.append("recover") or [],
    )
    monkeypatch.setattr(loader, "register_extension_providers", lambda: calls.append("register"))
    monkeypatch.setattr(
        "personalclaw.apps.app_manager.start_enabled_app_backends",
        lambda: calls.append("backends") or [],
    )
    monkeypatch.setattr(
        "personalclaw.apps.backend_runtime.start_backend_watchdog",
        lambda: calls.append("backend_watchdog"),
    )
    monkeypatch.setattr(
        "personalclaw.apps.worker_runtime.start_worker_watchdog",
        lambda: calls.append("worker_watchdog"),
    )
    monkeypatch.setattr(
        "personalclaw.local_models.sidecar.start_sidecar_watchdog",
        lambda: calls.append("sidecar_watchdog"),
    )

    loader.load_all_extensions()

    assert "register" in calls, "the gateway path must delegate to register_extension_providers"
    for expected in ("backends", "backend_watchdog", "worker_watchdog", "sidecar_watchdog"):
        assert expected in calls, f"the gateway tail lost its {expected!r} launch"
