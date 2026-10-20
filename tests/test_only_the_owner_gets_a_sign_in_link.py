"""A channel's "open the dashboard" link is minted for the channel's owner, and nobody else.

A dashboard sign-in token opens the whole dashboard as the owner, whatever id it names. The channel
SDK published only ``generate_token(user_id, ttl)``, which mints one for any id it is handed, and a
channel's dashboard command handed it the id of whoever asked: an allowed correspondent who typed
the command was sent a link that signed them in as the owner.

``owner_sign_in_token(provider, user_id, ttl)`` is the mint a channel's link uses now. It mints only
for the owner id that channel keeps (``owner_id_for(provider)``), and refuses anyone else with the
sentence the channel shows them. Each refusal has its floor: the owner, asking the same way, gets
a token that signs in.
"""

from __future__ import annotations

import os

import pytest

from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID

# The published names a channel app mints its link with.
from personalclaw.sdk.channel import (
    MAX_SESSION_TTL_SECS,
    NOT_THE_OWNER_SENTENCE,
    owner_sign_in_token,
)

OWNER = "U0OWNER01"
SOMEONE = "U0SOMEONE"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The session store and the credential store in tmp_path, with no owner anywhere."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = (CRED_OWNER_ID, *(owner_id_credential(p) for p in ("slack", "telegram")))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)


def _owner_is(monkeypatch, provider: str, user_id: str) -> None:
    monkeypatch.setenv(owner_id_credential(provider), user_id)


def _signs_in_as(token: str) -> str:
    from personalclaw.dashboard.token_auth import validate_token

    valid, user, reason = validate_token(token)
    assert valid, reason
    return user


@pytest.fixture
def minted(monkeypatch):
    """Every token the mint underneath is asked for, so a refusal can be shown to mint none."""
    from personalclaw.dashboard import token_auth

    calls: list[str] = []
    real = token_auth.generate_token

    def spy(user_id: str, ttl_seconds: int = 3600, **kw):
        calls.append(user_id)
        return real(user_id, ttl_seconds, **kw)

    monkeypatch.setattr(token_auth, "generate_token", spy)
    return calls


def test_the_owner_gets_a_link_that_signs_in(monkeypatch, minted):
    _owner_is(monkeypatch, "slack", OWNER)
    token = owner_sign_in_token("slack", OWNER, 3600)
    assert _signs_in_as(token) == OWNER
    assert minted == [OWNER]


def test_anyone_else_is_refused_and_nothing_is_minted(monkeypatch, minted):
    _owner_is(monkeypatch, "slack", OWNER)
    with pytest.raises(ValueError) as refused:
        owner_sign_in_token("slack", SOMEONE, 3600)
    assert str(refused.value) == NOT_THE_OWNER_SENTENCE
    assert minted == []
    # The floor: the owner, the same way.
    assert _signs_in_as(owner_sign_in_token("slack", OWNER, 3600)) == OWNER


def test_a_channel_that_knows_no_owner_mints_for_nobody(minted):
    for who in (OWNER, SOMEONE, ""):
        with pytest.raises(ValueError, match="Only this channel's owner"):
            owner_sign_in_token("slack", who, 3600)
    assert minted == []


def test_the_owner_of_another_channel_is_not_this_ones(monkeypatch, minted):
    """Each channel keeps its own owner id: the id Telegram knows the owner by is no Slack owner."""
    _owner_is(monkeypatch, "telegram", OWNER)
    with pytest.raises(ValueError, match="Only this channel's owner"):
        owner_sign_in_token("slack", OWNER, 3600)
    assert minted == []
    assert _signs_in_as(owner_sign_in_token("telegram", OWNER, 3600)) == OWNER


def test_the_refusal_is_a_sentence_the_channel_can_show():
    assert NOT_THE_OWNER_SENTENCE.startswith("Only this channel's owner can get a dashboard link")
    assert NOT_THE_OWNER_SENTENCE.endswith(".")


def test_the_owner_asking_for_too_long_is_told_the_limit(monkeypatch):
    _owner_is(monkeypatch, "slack", OWNER)
    with pytest.raises(ValueError) as refused:
        owner_sign_in_token("slack", OWNER, MAX_SESSION_TTL_SECS + 1)
    assert str(refused.value) != NOT_THE_OWNER_SENTENCE
    assert "90 days" in str(refused.value)


def test_the_channel_sdk_publishes_no_mint_for_just_anyone():
    """``generate_token`` mints a sign-in for any id it is handed, so the channel SDK stops
    publishing it once no channel app mints with it: the owner's mint is the one a channel has.
    It stays core's own (``dashboard.token_auth``), which every sign-in door calls."""
    import personalclaw.sdk.channel as channel_sdk
    from personalclaw.dashboard import token_auth

    assert "generate_token" not in channel_sdk.__all__
    assert not hasattr(channel_sdk, "generate_token"), "a second route to an owner sign-in"
    assert "owner_sign_in_token" in channel_sdk.__all__  # the floor: the owner's mint stays
    assert callable(token_auth.generate_token)
