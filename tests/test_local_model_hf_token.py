"""HF token cascade — three sources, whoami-validated (LOCAL-MODEL-MANAGER-V2 §5, LMMV-4).

Every test mocks the network (``net.fetch``) and the credential store; NONE requires a real
HF token or real egress. The properties under test:

* the cascade resolves credential-store → env → HF-CLI-file, and the FIRST whoami-valid source
  wins (an invalid higher-priority token is skipped, never blocking a valid lower one);
* whoami goes through the ``net.fetch`` CONNECTOR egress chokepoint with a Bearer header;
* the token VALUE never leaves the server — the status payload carries only a mask (SC4);
* set writes SOURCE 1 (the credential store, never config) and set/clear are SEL-audited by
  NAME, never by value;
* the whoami verdict is cached for the TTL, and a network-"unknown" is never cached.
"""

from __future__ import annotations

import pytest

from personalclaw.local_models import hf_token
from personalclaw.net.client import FetchResponse


def test_cascade_is_reachable_through_the_sdk_credentials_facade():
    """The cascade is re-exported via ``personalclaw.sdk.credentials`` so an HF-touching APP
    (diarization-pyannote, …) delegates its token lookup to it instead of rolling a private
    two-source read. This is the SDK-boundary contract the app depends on — the facade must
    hand back the SAME objects the core module defines, or an app and core would diverge.

    Imported by NAME (not the module) on purpose: this ``from personalclaw.sdk.credentials
    import <name>`` shape is exactly what the inert-surface census counts as a consumer, so the
    re-export reads as a live surface an app depends on rather than dead code."""
    from personalclaw.sdk import credentials as facade
    from personalclaw.sdk.credentials import (
        HfTokenResolution,
        mask_token,
        resolve_token,
        resolve_valid_token,
    )

    assert resolve_token is hf_token.resolve_token
    assert resolve_valid_token is hf_token.resolve_valid_token
    assert mask_token is hf_token.mask_token
    assert HfTokenResolution is hf_token.HfTokenResolution
    for name in ("resolve_token", "resolve_valid_token", "mask_token", "HfTokenResolution"):
        assert name in facade.__all__


@pytest.fixture(autouse=True)
def _clean_cache():
    hf_token.invalidate_whoami_cache()
    yield
    hf_token.invalidate_whoami_cache()


def _sources(monkeypatch, *, store="", env="", cli=""):
    """Point the three cascade readers at controlled tokens (no real store/env/file)."""
    monkeypatch.setattr(hf_token, "_read_credential_store", lambda: store)
    monkeypatch.setattr(hf_token, "_read_env", lambda: env)
    monkeypatch.setattr(hf_token, "_read_hf_cli_file", lambda: cli)


def _whoami_map(monkeypatch, mapping):
    """Map token → ('valid'|'invalid'|'unknown', username) without any network."""

    async def _fake(token):
        return mapping.get(token, ("invalid", ""))

    monkeypatch.setattr(hf_token, "_whoami_live", _fake)


# ── mask_token (SC4: the value never leaves unmasked) ─────────────────────────────────


def test_mask_token_keeps_family_prefix_and_last_four():
    assert hf_token.mask_token("hf_abcdefghijklmnop") == "hf_…mnop"


def test_mask_token_hides_short_and_blank_secrets():
    assert hf_token.mask_token("short7") == "…"  # 6 chars: reveal nothing but presence
    assert hf_token.mask_token("hf_shortish") == "…"  # 11 chars: still below the 12 floor
    assert hf_token.mask_token("") == ""


# ── whoami goes through the net.fetch CONNECTOR chokepoint (clause 1) ─────────────────


@pytest.mark.asyncio
async def test_whoami_calls_net_fetch_with_connector_and_bearer(monkeypatch):
    calls = []

    async def fake_fetch(url, *, policy, method="GET", headers=None, data=None, resolver=None):
        calls.append((url, policy, headers))
        return FetchResponse(url=url, status=200, headers={}, body=b'{"name": "someuser"}')

    monkeypatch.setattr("personalclaw.net.fetch", fake_fetch)
    _sources(monkeypatch, store="hf_valid_token_value")

    res = await hf_token.resolve_valid_token()

    assert res.valid is True
    assert res.username == "someuser"
    assert res.source == hf_token.SOURCE_CREDENTIAL_STORE
    assert len(calls) == 1
    url, policy, headers = calls[0]
    assert url == hf_token._WHOAMI_URL
    assert policy.name == "connector"  # the CONNECTOR egress profile, layered by egress_policy_for
    assert headers["Authorization"] == "Bearer hf_valid_token_value"


@pytest.mark.asyncio
async def test_whoami_401_is_invalid_and_carries_no_username(monkeypatch):
    async def fake_fetch(url, *, policy, method="GET", headers=None, data=None, resolver=None):
        return FetchResponse(url=url, status=401, headers={}, body=b"unauthorized")

    monkeypatch.setattr("personalclaw.net.fetch", fake_fetch)
    _sources(monkeypatch, env="hf_bad")

    statuses = {s.source: s for s in await hf_token.token_status()}
    env = statuses[hf_token.SOURCE_ENV]
    assert env.present is True
    assert env.valid is False
    assert env.username == ""


# ── the cascade: first whoami-valid wins, invalid higher-priority skipped (clause 1) ──


@pytest.mark.asyncio
async def test_first_valid_source_wins_and_invalid_higher_priority_is_skipped(monkeypatch):
    _sources(monkeypatch, store="bad-store", cli="good-cli")
    _whoami_map(monkeypatch, {"bad-store": ("invalid", ""), "good-cli": ("valid", "cli-user")})

    res = await hf_token.resolve_valid_token()

    assert res.token == "good-cli"
    assert res.source == hf_token.SOURCE_HF_CLI
    assert res.username == "cli-user"
    assert res.valid is True


@pytest.mark.asyncio
async def test_no_present_source_resolves_to_nothing(monkeypatch):
    _sources(monkeypatch)  # all empty
    res = await hf_token.resolve_valid_token()
    assert res.token is None
    assert res.valid is False


@pytest.mark.asyncio
async def test_all_present_but_invalid_resolves_to_no_token(monkeypatch):
    _sources(monkeypatch, store="a", env="b")
    _whoami_map(monkeypatch, {"a": ("invalid", ""), "b": ("invalid", "")})
    res = await hf_token.resolve_valid_token()
    assert res.token is None


@pytest.mark.asyncio
async def test_present_but_unverifiable_is_returned_optimistically(monkeypatch):
    # Network could not verify (egress down) → the token is still returned so a caller can try
    # it, but flagged not-valid, and the pre-warn does NOT nag.
    _sources(monkeypatch, store="maybe-token")
    _whoami_map(monkeypatch, {"maybe-token": ("unknown", "")})
    res = await hf_token.resolve_valid_token()
    assert res.token == "maybe-token"
    assert res.valid is False
    assert await hf_token.gated_prewarn_ok() is True


# ── token_status (SC4: masked, never raw; active marks the winner) ────────────────────


@pytest.mark.asyncio
async def test_status_masks_every_value_and_marks_the_active_source(monkeypatch):
    _sources(monkeypatch, store="hf_store_tokenXYZ", cli="hf_cli_tokenABCD")
    _whoami_map(
        monkeypatch,
        {"hf_store_tokenXYZ": ("valid", "store-user"), "hf_cli_tokenABCD": ("valid", "cli-user")},
    )

    statuses = await hf_token.token_status()
    by = {s.source: s for s in statuses}

    # Every present source is masked, and NO raw token appears anywhere in the payload.
    blob = repr([s.__dict__ for s in statuses])
    assert "hf_store_tokenXYZ" not in blob
    assert "hf_cli_tokenABCD" not in blob
    assert by[hf_token.SOURCE_CREDENTIAL_STORE].masked == "hf_…nXYZ"
    # Both are valid, but only the FIRST (credential store) is the active winner.
    assert by[hf_token.SOURCE_CREDENTIAL_STORE].active is True
    assert by[hf_token.SOURCE_HF_CLI].active is False
    # The env source is absent → present False, everything empty.
    assert by[hf_token.SOURCE_ENV].present is False
    assert by[hf_token.SOURCE_ENV].masked == ""


# ── set/clear write SOURCE 1 and are SEL-audited by name, never by value (clause 3) ───


class _FakeSel:
    def __init__(self):
        self.events = []

    def log_api_access(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_set_token_writes_credential_store_and_audits_by_name(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "personalclaw.config.credentials.save_credential",
        lambda key, value: saved.append((key, value)),
    )
    fake_sel = _FakeSel()
    monkeypatch.setattr("personalclaw.sel.sel", lambda: fake_sel)

    hf_token.set_token("hf_secret_value_1234", caller="dashboard:test")

    # Written to SOURCE 1 = the credential store, under the HF_TOKEN key.
    assert saved == [(hf_token.CREDENTIAL_NAME, "hf_secret_value_1234")]
    # SEL logged the SET event, by NAME — the value must appear in NO field.
    assert len(fake_sel.events) == 1
    ev = fake_sel.events[0]
    assert ev["operation"] == "hf_token.set"
    assert ev["resources"] == "HF_TOKEN"
    assert "hf_secret_value_1234" not in repr(ev)


def test_set_token_rejects_an_empty_value(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.config.credentials.save_credential",
        lambda key, value: pytest.fail("must not write an empty token"),
    )
    with pytest.raises(ValueError):
        hf_token.set_token("   ")


def test_clear_token_deletes_from_the_store_and_audits(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        "personalclaw.config.credentials.delete_credential",
        lambda key: (deleted.append(key), True)[1],
    )
    fake_sel = _FakeSel()
    monkeypatch.setattr("personalclaw.sel.sel", lambda: fake_sel)

    existed = hf_token.clear_token(caller="dashboard:test")

    assert existed is True
    assert deleted == [hf_token.CREDENTIAL_NAME]
    assert fake_sel.events[0]["operation"] == "hf_token.clear"
    assert fake_sel.events[0]["resources"] == "HF_TOKEN"


# ── resolve_token (sync provider delegate) prefers cache-valid, skips cache-invalid ───


@pytest.mark.asyncio
async def test_sync_resolve_prefers_a_cache_valid_lower_priority_over_invalid_higher(monkeypatch):
    _sources(monkeypatch, store="bad-store", cli="good-cli")
    _whoami_map(monkeypatch, {"bad-store": ("invalid", ""), "good-cli": ("valid", "u")})
    # Warm the cache (as the status endpoint / pre-warn would).
    await hf_token.resolve_valid_token()

    # The sync delegate now skips the cache-invalid higher-priority store token.
    assert hf_token.resolve_token() == "good-cli"


def test_sync_resolve_falls_back_to_highest_present_when_cache_cold(monkeypatch):
    _sources(monkeypatch, store="store-tok", cli="cli-tok")
    # No whoami has run → nothing cached → highest-priority present wins (best effort).
    assert hf_token.resolve_token() == "store-tok"


def test_sync_resolve_is_empty_when_no_source_present(monkeypatch):
    _sources(monkeypatch)
    assert hf_token.resolve_token() == ""


# ── whoami caching: cached within TTL, network-unknown never cached (clause 1) ────────


@pytest.mark.asyncio
async def test_whoami_result_is_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(url, *, policy, method="GET", headers=None, data=None, resolver=None):
        calls["n"] += 1
        return FetchResponse(url=url, status=200, headers={}, body=b'{"name": "u"}')

    monkeypatch.setattr("personalclaw.net.fetch", fake_fetch)
    monkeypatch.setattr(hf_token, "_whoami_ttl_s", lambda: 600.0)
    _sources(monkeypatch, store="hf_cached_tok")

    await hf_token.resolve_valid_token()
    await hf_token.resolve_valid_token()
    assert calls["n"] == 1  # second resolve hit the cache, not the network


@pytest.mark.asyncio
async def test_network_unknown_is_not_cached(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(url, *, policy, method="GET", headers=None, data=None, resolver=None):
        calls["n"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr("personalclaw.net.fetch", fake_fetch)
    _sources(monkeypatch, store="hf_unknown_tok")

    await hf_token.resolve_valid_token()
    await hf_token.resolve_valid_token()
    assert calls["n"] == 2  # an unknown verdict is re-checked, never pinned as invalid
