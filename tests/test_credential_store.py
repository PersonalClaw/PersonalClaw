"""``CredentialStore``: a credential read by name, from the one credential store.

``CredentialStore(home).resolve(NAME)`` is what a provider's ``credential``, a
``{{secret:NAME}}`` and an app's ``personalclaw.sdk.credentials`` read. It reads the store
Settings → Secrets writes (``config.credentials``: the OS keychain, else ``<home>/.env``) and
nothing else. ``credentials.json``, the descriptor file it read until this release, means
nothing to it (``tests/test_one_credential_store.py`` covers the move of that file).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from personalclaw.config import credentials as store
from personalclaw.config import loader
from personalclaw.llm.credentials import CredentialStore, OwnedCredentialRefused
from tests.test_credential_backend import _install_stub_keyring

NAME = "CS_UNIT_TOKEN"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.delenv(store.CREDENTIAL_BACKEND_ENV, raising=False)
    monkeypatch.setenv(NAME, "x")
    monkeypatch.delenv(NAME)  # registered, so teardown removes what save_credential mirrors
    assert loader.env_path() == cfg / ".env"
    return cfg


class TestResolveOrder:
    """The environment, then the keychain, then ``<home>/.env``."""

    def test_a_value_in_env_file_resolves(self, home: Path) -> None:
        (home / ".env").write_text(f"{NAME}=from-dotenv\n", encoding="utf-8")

        cred = CredentialStore(home).resolve(NAME)

        assert (cred.secret, cred.source, cred.kind) == ("from-dotenv", "file", "api_key")

    def test_the_environment_wins(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (home / ".env").write_text(f"{NAME}=from-dotenv\n", encoding="utf-8")
        monkeypatch.setenv(NAME, "from-env")

        cred = CredentialStore(home).resolve(NAME)

        assert (cred.secret, cred.source) == ("from-env", "env")

    def test_the_keychain_wins_over_env_file(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stub_keyring(monkeypatch)
        monkeypatch.setenv(store.CREDENTIAL_BACKEND_ENV, "keychain")
        store.save_credential(NAME, "from-keychain")
        monkeypatch.delenv(NAME)
        (home / ".env").write_text(f"{NAME}=from-dotenv\n", encoding="utf-8")

        cred = CredentialStore(home).resolve(NAME)

        assert (cred.secret, cred.source) == ("from-keychain", "keychain")

    def test_a_quoted_value_reads_back_as_the_store_wrote_it(self, home: Path) -> None:
        """The old reader parsed ``.env`` itself, verbatim, so a multi-line value the store
        had quoted came back with its quotes and escapes."""
        pem = "-----BEGIN KEY-----\nabc\n-----END KEY-----"
        store.save_credential(NAME, pem)
        os.environ.pop(NAME, None)

        assert CredentialStore(home).resolve(NAME).secret == pem

    def test_the_home_it_is_given_is_the_one_it_reads(self, home: Path, tmp_path: Path) -> None:
        other = tmp_path / "other-home"
        other.mkdir()
        (other / ".env").write_text(f"{NAME}=other-home\n", encoding="utf-8")

        assert CredentialStore(other).resolve(NAME).secret == "other-home"
        with pytest.raises(KeyError):
            CredentialStore(home).resolve(NAME)


class TestWhatItRefuses:
    def test_a_name_nothing_stored_raises_key_error(self, home: Path) -> None:
        with pytest.raises(KeyError):
            CredentialStore(home).resolve("CS_NEVER_STORED")

    def test_a_descriptor_in_credentials_json_is_not_a_credential(self, home: Path) -> None:
        (home / "credentials.json").write_text(
            json.dumps({NAME: {"type": "api_key", "value": "inline"}}), encoding="utf-8"
        )

        with pytest.raises(KeyError):
            CredentialStore(home).resolve(NAME)

    def test_an_owned_key_is_refused_before_any_value_is_read(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owned = f"{store.OWNED_KEY_PREFIX}PROVIDER_X_1234ABCD__API_KEY"
        store.save_credential(owned, "sk-owned")
        monkeypatch.setattr(
            store, "find_credential", lambda *a, **k: pytest.fail("an owned key was read")
        )

        with pytest.raises(OwnedCredentialRefused) as refused:
            CredentialStore(home).resolve(owned)

        assert isinstance(refused.value, KeyError)
        assert "sk-owned" not in str(refused.value)

    def test_a_deleted_secret_is_gone_on_the_next_read(self, home: Path) -> None:
        reader = CredentialStore(home)
        store.save_credential(NAME, "rotating")
        os.environ.pop(NAME, None)
        assert reader.resolve(NAME).secret == "rotating"

        store.delete_credential(NAME)

        with pytest.raises(KeyError):
            reader.resolve(NAME)


def test_loose_env_file_permissions_are_tightened_on_read(home: Path) -> None:
    env = home / ".env"
    env.write_text(f"{NAME}=v\n", encoding="utf-8")
    env.chmod(0o644)

    CredentialStore(home).resolve(NAME)

    assert env.stat().st_mode & 0o777 == 0o600
