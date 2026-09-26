"""Each channel keeps its OWN owner id, and an owner notification uses the one for its channel.

Slack, Telegram and Discord all wrote ``PERSONALCLAW_OWNER_ID``, so setting up a second channel
overwrote the first one's owner with an id from another platform (PersonalClawApps #124, note 7).
Core then compounded it: every owner notification picked a connected channel with
``owner_reachable()`` and addressed it to that ONE shared id, so with Discord and Telegram
connected a cron result could go through Discord addressed to a Telegram user id.

Now a channel stores its owner under ``owner_id_credential(<provider>)`` and core reads it with
``owner_id_for(<provider>)``, which falls back to the shared key while a channel app still writes
that one. ``channel_delivery.reach_owner`` addresses each channel it tries with that channel's
own owner id (``test_owner_notification_falls_through.py`` covers its fall-through).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_delivery
from personalclaw.config.credentials import save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig

# The published names — what a channel app imports.
from personalclaw.sdk.channel import owner_id_credential, owner_id_for

_KEYS = ("SLACK", "TELEGRAM", "DISCORD", "GOOGLE_CHAT")


@pytest.fixture(autouse=True)
def _no_owner_ids_in_the_environment(monkeypatch):
    """`save_credential` mirrors a named key into os.environ: keep each test's owner ids its own."""
    monkeypatch.delenv(CRED_OWNER_ID, raising=False)
    for key in _KEYS:
        monkeypatch.delenv(f"{CRED_OWNER_ID}_{key}", raising=False)
    yield
    import os

    for key in (CRED_OWNER_ID, *(f"{CRED_OWNER_ID}_{k}" for k in _KEYS)):
        os.environ.pop(key, None)


def _delivery(dm: str) -> MagicMock:
    d = MagicMock()
    d.open_dm = AsyncMock(return_value=dm)
    d.deliver_notification = AsyncMock(return_value="1.0")
    d.deliver_text = AsyncMock(return_value="1.0")
    return d


def test_each_channel_has_its_own_key():
    assert owner_id_credential("slack") == "PERSONALCLAW_OWNER_ID_SLACK"
    assert owner_id_credential("google-chat") == "PERSONALCLAW_OWNER_ID_GOOGLE_CHAT"
    with pytest.raises(ValueError):
        owner_id_credential(" ")


def test_setting_up_a_second_channel_leaves_the_first_ones_owner_alone():
    """The bug: the second setup overwrote the one shared key."""
    save_credential(owner_id_credential("slack"), "U0SLACK")
    save_credential(owner_id_credential("telegram"), "4242")
    assert owner_id_for("slack") == "U0SLACK"
    assert owner_id_for("telegram") == "4242"


def test_a_channel_without_its_own_key_keeps_the_shared_one():
    """A channel app that still writes the shared key keeps its owner: the fallback is how."""
    save_credential(CRED_OWNER_ID, "U0SHARED")
    assert owner_id_for("slack") == "U0SHARED"
    save_credential(owner_id_credential("slack"), "U0OWN")
    assert owner_id_for("slack") == "U0OWN" and owner_id_for("discord") == "U0SHARED"


def test_the_environment_wins_as_it_does_for_every_named_credential(monkeypatch):
    save_credential(owner_id_credential("slack"), "U0STORED")
    monkeypatch.setenv(owner_id_credential("slack"), "U0ENV")
    assert owner_id_for("slack") == "U0ENV"
    assert AppConfig().load_credentials()[owner_id_credential("slack")] == "U0ENV"


@pytest.mark.asyncio
async def test_the_owner_is_addressed_with_the_id_of_the_channel_that_reaches_them():
    """Discord connected with no owner, Telegram connected with one: the owner is reached on
    Telegram, with Telegram's id — not on Discord (first, sorted) with somebody else's."""
    discord, telegram = _delivery("D-DM"), _delivery("T-DM")
    channel_delivery.register(discord, provider="discord")
    channel_delivery.register(telegram, provider="telegram")
    save_credential(owner_id_credential("telegram"), "4242")
    reached = await channel_delivery.reach_owner(lambda d, dm: d.deliver_text(dm, "hi"))
    assert (reached.provider, reached.delivery, reached.channel) == ("telegram", telegram, "T-DM")
    telegram.open_dm.assert_awaited_once_with("4242")
    discord.open_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_gateway_notification_goes_to_the_owner_on_that_owners_channel(monkeypatch):
    """The gateway's owner DM (a heartbeat or cron result) with two channels connected, the
    first of which has a different owner id stored under the shared key."""
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={CRED_OWNER_ID: "U0SLACKISH"}):
        orch = GatewayOrchestrator(cfg)
    orch.dashboard_state = None
    discord, telegram = _delivery("D-DM"), _delivery("T-DM")
    orch.register_channel_delivery(discord, provider="discord")
    orch.register_channel_delivery(telegram, provider="telegram")
    save_credential(owner_id_credential("discord"), "998877665544332211")
    save_credential(owner_id_credential("telegram"), "4242")

    await orch._deliver_result("Nightly", "task", "done", "channel")

    discord.open_dm.assert_awaited_once_with("998877665544332211")
    discord.deliver_notification.assert_awaited_once()
    telegram.open_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_channel_that_knows_its_owner_means_no_dm():
    discord = _delivery("D-DM")
    channel_delivery.register(discord, provider="discord")
    reached = await channel_delivery.reach_owner(lambda d, dm: d.deliver_text(dm, "hi"))
    assert not reached.delivered and reached.reasons == ("discord has no owner id",)
    discord.open_dm.assert_not_awaited()


def test_the_sdk_publishes_the_core_resolver_itself():
    from personalclaw.config import credentials
    from personalclaw.sdk import channel

    assert owner_id_credential is credentials.owner_id_credential
    assert owner_id_for is credentials.owner_id_for
    assert {"owner_id_credential", "owner_id_for"} <= set(channel.__all__)
