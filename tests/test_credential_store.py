"""Unit tests for CredentialStore.

Validates: Requirements R4.1, R4.2, R4.3, R4.4, R4.6.

Property-based coverage for Property 5 (Credential Non-Leakage) is
scheduled separately (task 33); this file uses plain pytest only.
"""

import json
from pathlib import Path

import pytest

from personalclaw.llm.credentials import Credential, CredentialStore


def _write_credentials(home: Path, descriptors: dict[str, dict[str, object]]) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / CredentialStore.CREDENTIALS_FILE
    path.write_text(json.dumps(descriptors), encoding="utf-8")
    path.chmod(CredentialStore.FILE_MODE)
    return path


class TestResolveChain:
    """Resolution order R4.1: env → inline value → .env → none."""

    # ── R4.1 step 1: env var present ─────────────────────────────────

    def test_env_var_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_credentials(
            tmp_path,
            {"anthropic_api_key": {"type": "api_key", "value_env": "ANTHROPIC_API_KEY"}},
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-secret")

        store = CredentialStore(tmp_path)
        cred = store.resolve("anthropic_api_key")

        assert cred.kind == "api_key"
        assert cred.secret == "env-secret"
        assert cred.source == "env"

    # ── R4.3: env beats inline value ─────────────────────────────────

    def test_env_var_preferred_over_inline_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_credentials(
            tmp_path,
            {
                "anthropic_api_key": {
                    "type": "api_key",
                    "value_env": "ANTHROPIC_API_KEY",
                    "value": "inline-secret",
                }
            },
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-secret")

        store = CredentialStore(tmp_path)
        cred = store.resolve("anthropic_api_key")

        assert cred.secret == "env-secret"
        assert cred.source == "env"

    # ── R4.1 step 2: inline value when env unset ─────────────────────

    def test_inline_value_when_env_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_credentials(
            tmp_path,
            {
                "openai_api_key": {
                    "type": "api_key",
                    "value_env": "OPENAI_API_KEY",
                    "value": "inline-secret",
                }
            },
        )
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        store = CredentialStore(tmp_path)
        cred = store.resolve("openai_api_key")

        assert cred.secret == "inline-secret"
        assert cred.source == "file"

    # ── R4.1 step 3: <HOME>/.env fallback ────────────────────────────

    def test_env_file_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_credentials(
            tmp_path,
            {"openai_api_key": {"type": "api_key"}},
        )
        env_file = tmp_path / CredentialStore.ENV_FILE
        env_file.write_text(
            "# comment line\n" "\n" "openai_api_key=from-env-file\n" "OTHER_KEY=ignored\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        store = CredentialStore(tmp_path)
        cred = store.resolve("openai_api_key")

        assert cred.secret == "from-env-file"
        assert cred.source == "file"

    # ── R4.1 step 4: nothing configured ──────────────────────────────

    def test_no_value_anywhere(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_credentials(
            tmp_path,
            {"openai_api_key": {"type": "api_key", "value_env": "OPENAI_API_KEY"}},
        )
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        store = CredentialStore(tmp_path)
        cred = store.resolve("openai_api_key")

        assert cred.secret is None
        assert cred.source == "none"


class TestKindHandling:
    """Per-kind behavior: none and secret-bearing kinds."""

    def test_kind_none_returns_no_secret(self, tmp_path: Path) -> None:
        _write_credentials(tmp_path, {"ollama_local": {"type": "none"}})

        store = CredentialStore(tmp_path)
        cred = store.resolve("ollama_local")

        assert cred.kind == "none"
        assert cred.secret is None
        assert cred.source == "none"


class TestUnknownName:
    """R4.2: resolving an unknown name raises KeyError."""

    def test_unknown_name_raises_key_error(self, tmp_path: Path) -> None:
        _write_credentials(tmp_path, {})
        store = CredentialStore(tmp_path)

        with pytest.raises(KeyError):
            store.resolve("does-not-exist")


class TestListNeverLeaksSecrets:
    """R4.4 / Property 5: list() returns secret=None for every entry."""

    def test_list_strips_secrets_even_when_env_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_credentials(
            tmp_path,
            {
                "anthropic_api_key": {"type": "api_key", "value_env": "ANTHROPIC_API_KEY"},
                "openai_api_key": {"type": "api_key", "value": "inline"},
                "ollama_local": {"type": "none"},
            },
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")

        store = CredentialStore(tmp_path)
        listed = store.list()

        assert {c.name for c in listed} == {
            "anthropic_api_key",
            "openai_api_key",
            "ollama_local",
        }
        for cred in listed:
            assert cred.secret is None, f"{cred.name} leaked a secret"

        # Sanity-check that the secret IS available through resolve(),
        # so the list-stripping is meaningful (not just "no secret to leak").
        assert store.resolve("anthropic_api_key").secret == "should-not-leak"
        # And the stringified list should not contain the secret either.
        assert "should-not-leak" not in repr(listed)


class TestPermissions:
    """R4.6: credentials.json is written / kept at 0o600."""

    def test_save_creates_file_with_mode_0600(self, tmp_path: Path) -> None:
        store = CredentialStore(tmp_path)
        store.save({"anthropic_api_key": {"type": "api_key", "value_env": "ANTHROPIC_API_KEY"}})

        creds_path = tmp_path / CredentialStore.CREDENTIALS_FILE
        assert creds_path.is_file()
        mode = creds_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

        # Round-trip: reload and confirm the saved descriptor is visible.
        store2 = CredentialStore(tmp_path)
        assert store2.has("anthropic_api_key")

    def test_loose_permissions_tightened_on_read(self, tmp_path: Path) -> None:
        path = tmp_path / CredentialStore.CREDENTIALS_FILE
        tmp_path.mkdir(exist_ok=True)
        path.write_text(json.dumps({"x": {"type": "none"}}), encoding="utf-8")
        path.chmod(0o644)

        CredentialStore(tmp_path)

        mode = path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected store to tighten to 0o600, got {oct(mode)}"

    def test_save_updates_in_memory_descriptors(self, tmp_path: Path) -> None:
        store = CredentialStore(tmp_path)
        store.save({"k": {"type": "none"}})

        assert store.has("k")
        cred = store.resolve("k")
        assert isinstance(cred, Credential)
        assert cred.kind == "none"
