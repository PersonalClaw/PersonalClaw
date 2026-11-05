"""The gateway the desktop app starts listens where the app reaches it, whatever the home stores.

A bind host the home's ``.env`` or its stored credentials hold doesn't replace the one the app
names, and ``resolve_bind_host`` answers the app's. Each store is pinned on its own, with a
control: without the app's value, that store's value is what the gateway would bind, so the case
can't pass blind.
"""

from __future__ import annotations

import pytest

#: A bind host the home stores. Any value other than the app's would do.
STORED = "::1"
#: What the desktop app names (``LOOPBACK_BIND_HOST`` in ``desktop/gatewayEnv.js``).
NAMED_BY_THE_APP = "127.0.0.1"


@pytest.fixture
def home(tmp_path, monkeypatch, unset_env):
    """A home of the test's own: no bind host in the environment, and no stored credential."""
    from personalclaw.config import credentials

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    # In the home, so the folder's `.env` the CLI also reads is the home's, never the checkout's.
    monkeypatch.chdir(home)
    unset_env("PERSONALCLAW_BIND_HOST")
    monkeypatch.setattr(credentials, "_dotenv_credentials", lambda home=None: {})
    monkeypatch.setattr(credentials, "_keychain_credentials", lambda *a, **k: {})
    return home


@pytest.fixture
def env_file_storing_it(home):
    """The home's ``.env`` holds :data:`STORED`."""
    (home / ".env").write_text(f"PERSONALCLAW_BIND_HOST={STORED}\n")
    return home


@pytest.fixture
def credentials_storing_it(home, monkeypatch):
    """The stored credentials hold :data:`STORED`."""
    from personalclaw.config import credentials

    stored = {"PERSONALCLAW_BIND_HOST": STORED}
    monkeypatch.setattr(credentials, "_dotenv_credentials", lambda home=None: dict(stored))
    monkeypatch.setattr(credentials, "_keychain_credentials", lambda *a, **k: dict(stored))
    return home


def _gateway_reads_its_home() -> str:
    """Read the home the way a starting gateway does, then answer the bind host it would take."""
    from personalclaw.cli import _read_the_env_files
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.origin import resolve_bind_host

    _read_the_env_files()
    AppConfig().load_credentials()
    return resolve_bind_host()


def test_the_apps_bind_host_wins_over_the_homes_env_file(env_file_storing_it, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_BIND_HOST", NAMED_BY_THE_APP)
    assert _gateway_reads_its_home() == NAMED_BY_THE_APP


def test_the_apps_bind_host_wins_over_the_stored_credentials(credentials_storing_it, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_BIND_HOST", NAMED_BY_THE_APP)
    assert _gateway_reads_its_home() == NAMED_BY_THE_APP


def test_the_control_without_the_apps_value_takes_the_env_files(env_file_storing_it):
    assert _gateway_reads_its_home() == STORED


def test_the_control_without_the_apps_value_takes_the_stored_credentials(credentials_storing_it):
    assert _gateway_reads_its_home() == STORED
