"""HF token cascade + status masking (LMMV-4 §5, Success Criterion 4).

Every test isolates the credential store (``env_path``), the process environment,
the HF-CLI file (``HF_HOME``), and the whoami cache — no test touches the real home,
network, or a real token.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.local_models import hf_token as ht
from personalclaw.local_models.hf_token import (
    SOURCE_CREDENTIAL,
    SOURCE_ENV,
    SOURCE_HF_CLI,
    _WhoamiResult,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point every cascade source at tmp_path and clear the whoami cache per test."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr("personalclaw.config.loader.env_path", lambda: env_file)
    # HF CLI token file under an isolated HF_HOME.
    hf_home = tmp_path / "hfcache"
    hf_home.mkdir()
    monkeypatch.setenv("HF_HOME", str(hf_home))
    # Clear the env-source keys so a developer's real HF_TOKEN can't leak in.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    ht._whoami_cache.clear()
    yield
    ht._whoami_cache.clear()


def _stub_whoami(valid_tokens: dict[str, str]):
    """A fake _whoami: valid iff the token is a key of ``valid_tokens`` (→ username)."""

    async def _fake(token: str) -> _WhoamiResult:
        if token in valid_tokens:
            return _WhoamiResult(valid=True, username=valid_tokens[token], expires_at=1e18)
        return _WhoamiResult(valid=False, username=None, expires_at=1e18)

    return _fake


# ── masking (Success Criterion 4) ──────────────────────────────────────────────


def test_mask_never_reveals_full_token():
    masked = ht._mask("hf_abcdefghijklmnop3jw")
    assert masked == "hf_…3jw"
    assert "abcdefghijk" not in masked


def test_mask_short_token_is_ellipsis():
    assert ht._mask("hf_x") == "…"


# ── cascade order + whoami skip (Success Criterion 4) ──────────────────────────


@pytest.mark.asyncio
async def test_invalid_higher_priority_skips_to_valid_cli_token(monkeypatch):
    """An INVALID env token is skipped for a VALID HF-CLI token (the plan's exact
    scenario: env invalid, CLI valid → CLI wins, username shown, no unmasked leak)."""
    monkeypatch.setenv("HF_TOKEN", "hf_env_invalid_000")
    (ht._hf_cli_token_path()).write_text("hf_cli_valid_999", encoding="utf-8")
    monkeypatch.setattr(ht, "_whoami", _stub_whoami({"hf_cli_valid_999": "alice"}))

    status = await ht.hf_token_status()
    assert status["active_source"] == SOURCE_HF_CLI
    assert status["username"] == "alice"
    by = {s["source"]: s for s in status["sources"]}
    assert by[SOURCE_ENV]["present"] is True and by[SOURCE_ENV]["valid"] is False
    assert by[SOURCE_HF_CLI]["valid"] is True and by[SOURCE_HF_CLI]["username"] == "alice"
    # No unmasked token anywhere in the payload.
    blob = json.dumps(status)
    assert "hf_env_invalid_000" not in blob
    assert "hf_cli_valid_999" not in blob
    assert by[SOURCE_HF_CLI]["masked"] == "hf_…999"


@pytest.mark.asyncio
async def test_credential_store_wins_when_valid(monkeypatch):
    ht.set_hf_token("hf_cred_valid_abc")
    monkeypatch.setenv("HF_TOKEN", "hf_env_valid_xyz")
    monkeypatch.setattr(
        ht, "_whoami", _stub_whoami({"hf_cred_valid_abc": "bob", "hf_env_valid_xyz": "eve"})
    )
    status = await ht.hf_token_status()
    assert status["active_source"] == SOURCE_CREDENTIAL
    assert status["username"] == "bob"


@pytest.mark.asyncio
async def test_resolve_returns_first_valid_token(monkeypatch):
    (ht._hf_cli_token_path()).write_text("hf_cli_valid_999", encoding="utf-8")
    monkeypatch.setattr(ht, "_whoami", _stub_whoami({"hf_cli_valid_999": "alice"}))
    assert await ht.resolve_hf_token() == "hf_cli_valid_999"


@pytest.mark.asyncio
async def test_no_valid_token_yields_none_and_no_active(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_bad")
    monkeypatch.setattr(ht, "_whoami", _stub_whoami({}))
    assert await ht.resolve_hf_token() is None
    status = await ht.hf_token_status()
    assert status["active_source"] is None


# ── presence (network-free pre-warn) ───────────────────────────────────────────


def test_present_is_network_free_and_true_when_any_source_set(monkeypatch):
    assert ht.hf_token_present() is False
    monkeypatch.setenv("HF_TOKEN", "hf_anything")
    assert ht.hf_token_present() is True


# ── set / clear (source 1 only, SEL-audited) ───────────────────────────────────


def test_set_writes_credential_env_0600(monkeypatch):
    audits: list[tuple[str, str]] = []
    monkeypatch.setattr(ht, "_sel_log", lambda op, outcome, error="": audits.append((op, outcome)))
    ht.set_hf_token("hf_secret_value")
    from personalclaw.config.loader import env_path

    ep = env_path()
    assert ep.exists()
    assert "HF_TOKEN=hf_secret_value" in ep.read_text()
    assert (ep.stat().st_mode & 0o777) == 0o600
    assert ("hf_token.set", "ok") in audits
    # Source-1 read sees it; process env is NOT mirrored (kept distinct from source 2).
    assert ht._read_credential_token() == "hf_secret_value"


def test_clear_removes_credential_source(monkeypatch):
    audits: list[tuple[str, str]] = []
    monkeypatch.setattr(ht, "_sel_log", lambda op, outcome, error="": audits.append((op, outcome)))
    ht.set_hf_token("hf_secret_value")
    ht.clear_hf_token()
    assert ht._read_credential_token() is None
    assert ("hf_token.clear", "ok") in audits


def test_set_empty_token_rejected():
    with pytest.raises(ValueError):
        ht.set_hf_token("   ")


# ── whoami goes through the net.fetch egress chokepoint, cached ────────────────


@pytest.mark.asyncio
async def test_whoami_uses_egress_chokepoint_and_caches(monkeypatch):
    """whoami must call personalclaw.net.client.fetch (the CONNECTOR chokepoint), and a
    second lookup within TTL must hit the cache (one network call, not two)."""
    calls: list[dict] = []

    class _Resp:
        status = 200
        body = b'{"name": "carol", "type": "user"}'

    async def _fake_fetch(url, *, policy, headers=None, **kw):
        calls.append({"url": url, "policy": policy.name, "headers": headers})
        return _Resp()

    monkeypatch.setattr(ht, "fetch", _fake_fetch)
    monkeypatch.setattr(ht, "_whoami_ttl_s", lambda: 600)

    r1 = await ht._whoami("hf_live_token")
    r2 = await ht._whoami("hf_live_token")
    assert r1.valid and r1.username == "carol"
    assert r2.valid and r2.username == "carol"
    assert len(calls) == 1  # cached second time
    assert "huggingface.co/api/whoami-v2" in calls[0]["url"]
    assert calls[0]["policy"] == "connector"
    assert calls[0]["headers"]["Authorization"] == "Bearer hf_live_token"


@pytest.mark.asyncio
async def test_whoami_401_is_invalid(monkeypatch):
    class _Resp:
        status = 401
        body = b"unauthorized"

    async def _fake_fetch(url, *, policy, headers=None, **kw):
        return _Resp()

    monkeypatch.setattr(ht, "fetch", _fake_fetch)
    r = await ht._whoami("hf_bad")
    assert r.valid is False and r.username is None


@pytest.mark.asyncio
async def test_whoami_transport_error_is_invalid(monkeypatch):
    async def _boom(url, *, policy, headers=None, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(ht, "fetch", _boom)
    r = await ht._whoami("hf_x")
    assert r.valid is False


def test_sdk_credentials_reexports_cascade():
    import personalclaw.sdk.credentials as sdk

    assert hasattr(sdk, "resolve_hf_token")
    assert hasattr(sdk, "hf_token_status")
    assert hasattr(sdk, "hf_token_present")
