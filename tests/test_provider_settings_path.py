"""ProviderSettings config path — must agree with the Apps config UI's path.

Regression for bug #31: the provider-build read path (ProviderSettings.config_path)
resolved to ``app_dir/config.json`` while the Apps config UI (apps.app_config) wrote
to ``app_dir/data/config.json``. So a provider key set in the UI never reached the
provider when it was built at boot (brave/tavily/etc. showed unavailable despite a
configured key). The two MUST resolve to the same file, inside ``data/`` (A2-preserved).
"""

from __future__ import annotations

import pytest

from personalclaw.providers.settings import ProviderSettings


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.apps.manager as mgr

    monkeypatch.setattr(mgr, "config_dir", lambda: tmp_path)
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    return tmp_path


def test_provider_settings_path_is_under_data():
    p = ProviderSettings.config_path("brave-search")
    assert p.name == "config.json"
    assert p.parent.name == "data", "provider config must live in data/ (survives updates)"


def test_provider_settings_agrees_with_app_config_path():
    """The provider-build read path and the Apps-UI write path must be the SAME file."""
    from personalclaw.apps.app_config import _config_path as ui_write_path

    assert ProviderSettings.config_path("brave-search") == ui_write_path("brave-search"), (
        "ProviderSettings (provider build reads) and app_config (UI writes) must "
        "resolve to the identical file — bug #31 was that they diverged."
    )


def test_round_trip_key_visible_to_provider(tmp_path):
    """A key written via ProviderSettings.save is read back by load (same path)."""
    ProviderSettings.save("brave-search", {"api_key": "sk-brave-probe"})
    loaded = ProviderSettings.load("brave-search")
    assert loaded.get("api_key") == "sk-brave-probe"
    # and it landed in data/config.json specifically
    assert (ProviderSettings.config_path("brave-search")).parent.name == "data"
