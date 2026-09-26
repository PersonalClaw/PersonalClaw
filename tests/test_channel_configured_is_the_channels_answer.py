"""Whether a channel is configured is the channel app's own answer, not two credential names.

Core decided "a channel is configured" by looking for ``SLACK_APP_TOKEN`` and ``SLACK_BOT_TOKEN``
in the credential store, in three places: the gateway's startup line, ``personalclaw setup``'s
dashboard-URL prompt, and ``personalclaw doctor``'s remote-bind warning. That named one vendor in
core, and it went blind the moment Slack kept its tokens where it now does — in its own settings,
under keys it owns (PersonalClawApps #124): a working Slack read as "no channel configured".

``channel_transports.configured_channels`` asks each channel transport's own ``health()``:
``offline`` means it has nothing to connect with; ``ready`` and ``error`` (half-up) both mean it is
configured. Every shipped channel app answers that way.
"""

from __future__ import annotations

import logging
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest

from personalclaw import channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider


class _Transport(ChannelTransportProvider):
    def __init__(self, name: str, state: str, *, raises: bool = False) -> None:
        self._name, self._state, self._raises = name, state, raises

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._name.title())

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    async def health(self) -> dict[str, Any]:
        if self._raises:
            raise RuntimeError("probe broke")
        return {"state": self._state, "detail": ""}


@pytest.fixture
def registered():
    """Register transports for one test and take them out again."""
    added: list[str] = []

    def _add(*transports: _Transport) -> None:
        for t in transports:
            channel_transports.register_transport(t)
            added.append(t.name)

    yield _add
    for name in added:
        channel_transports.unregister_transport(name)


@pytest.mark.asyncio
async def test_a_channel_counts_when_its_own_health_says_it_has_what_it_needs(registered):
    registered(
        _Transport("offline-chan", "offline"),
        _Transport("half-up", "error"),
        _Transport("ready-chan", "ready"),
        _Transport("broken-probe", "ready", raises=True),
        _Transport(channel_transports.WEBUI_TRANSPORT, "ready"),
    )
    assert sorted(await channel_transports.configured_channels()) == ["Half-Up", "Ready-Chan"]


# ── personalclaw doctor: the remote-bind warning ────────────────────────────────────────────────

_NO_CHANNEL = "no channel configured — token generation unavailable"


def _doctor_on_a_remote_bind(capsys, creds: dict[str, str]) -> str:
    from personalclaw.cli_doctor import _doctor
    from personalclaw.config.loader import AppConfig

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=False),
        patch.object(AppConfig, "load_credentials", lambda self: dict(creds)),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    return capsys.readouterr().out


def test_doctor_sees_a_channel_configured_through_its_settings(registered, capsys):
    """No SLACK_* credential anywhere, and the Slack transport says it is configured."""
    registered(_Transport("slack", "error"))
    out = _doctor_on_a_remote_bind(capsys, {})
    assert "bind:        0.0.0.0" in out, "the probe did not reach the remote-bind branch"
    assert _NO_CHANNEL not in out, out


def test_doctor_does_not_take_slack_credential_names_for_a_channel(registered, capsys):
    """The two names alone, with no channel that reports itself configured, are not a channel."""
    registered(_Transport("slack", "offline"))
    out = _doctor_on_a_remote_bind(
        capsys, {"SLACK_APP_TOKEN": "xapp-1", "SLACK_BOT_TOKEN": "xoxb-1"}
    )
    assert _NO_CHANNEL in out, out


# ── personalclaw setup: the dashboard-URL prompt ────────────────────────────────────────────────


def _setup_url_prompt_asked(transports: list[_Transport], creds: dict[str, str]) -> bool:
    from personalclaw import cli_setup
    from personalclaw.config.loader import AppConfig

    asked: list[str] = []
    with (
        patch("personalclaw.providers.loader.build_channel_transports", lambda: transports),
        patch.object(AppConfig, "load_credentials", lambda self: dict(creds)),
        patch.object(cli_setup.socket, "gethostbyname", return_value="10.0.0.5"),
        patch.object(cli_setup, "_ask", side_effect=lambda prompt: asked.append(prompt) or ""),
    ):
        cli_setup._maybe_setup_dashboard_url()
    return bool(asked)


def test_setup_offers_the_dashboard_url_for_a_channel_configured_in_its_settings():
    assert _setup_url_prompt_asked([_Transport("telegram", "error")], {}) is True


def test_setup_does_not_take_slack_credential_names_for_a_channel():
    creds = {"SLACK_APP_TOKEN": "xapp-1", "SLACK_BOT_TOKEN": "xoxb-1"}
    assert _setup_url_prompt_asked([_Transport("telegram", "offline")], creds) is False


# ── the gateway's startup line ──────────────────────────────────────────────────────────────────


def _gateway():
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        return GatewayOrchestrator(cfg)


@pytest.mark.asyncio
async def test_the_gateway_says_dashboard_only_exactly_when_no_channel_is_configured(
    registered, caplog
):
    orch = _gateway()
    registered(_Transport("discord", "offline"))
    with caplog.at_level(logging.INFO, logger="personalclaw.gateway"):
        await orch._start_channel_inbound()
    assert any("dashboard-only" in r.getMessage() for r in caplog.records)

    caplog.clear()
    registered(_Transport("slack", "error"))
    with caplog.at_level(logging.INFO, logger="personalclaw.gateway"):
        await orch._start_channel_inbound()
    assert not any("dashboard-only" in r.getMessage() for r in caplog.records)
