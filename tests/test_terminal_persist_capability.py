"""The terminal session list publishes whether tmux-backed persistence is POSSIBLE.

Issue 545. `_persist_enabled` has always been the conjunction of two things — the opt-in config
flag and a usable `tmux` binary — but only the flag reached the client. So the terminal header
derived its promise from the value it had just written and rendered "Sessions are tmux-backed, so
they survive a restart." on a host with no tmux, where `_persist_enabled` is False and every
session dies with the gateway. The backend was gating correctly and simply hiding the reason.

`persist_available` is that second conjunct, published. The flag stays the user's INTENT; this says
whether the intent can be honoured, and only both together mean persistence.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.dashboard.handlers import terminal
from tests.test_terminal_handler import _make_request  # noqa: F401  (shared request builder)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """The panel must be on for the list handler to answer at all."""
    import time

    terminal._enabled_cache[0] = True
    terminal._enabled_cache[1] = time.monotonic()
    yield
    terminal._enabled_cache[0] = False
    terminal._enabled_cache[1] = 0.0


async def _list(monkeypatch, *, tmux: bool) -> dict:
    """Drive the list handler with the host's tmux presence forced either way."""
    monkeypatch.setattr(terminal, "_tmux_available", lambda: tmux)
    req = _make_request()
    with patch.object(terminal, "_sel") as mock_sel:
        mock_sel.return_value.log_api_access = MagicMock()
        resp = await terminal.api_terminal_list(req)
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_reports_tmux_present(monkeypatch):
    assert await _list(monkeypatch, tmux=True) == {
        "enabled": True,
        "persist_available": True,
        "sessions": [],
    }


@pytest.mark.asyncio
async def test_reports_tmux_absent(monkeypatch):
    """The case the issue was filed for: the flag can be on and persistence still impossible."""
    body = await _list(monkeypatch, tmux=False)
    assert body["persist_available"] is False


@pytest.mark.asyncio
async def test_the_capability_is_the_hosts_answer_not_the_config_flag(monkeypatch):
    """`persist_available` must not move with the opt-in flag.

    This is the whole defect in one assertion. The flag is what the user asked for; if the
    published capability tracked it, the client would be right back to reading its own write and
    the header could claim tmux survival on a host that has none.
    """
    monkeypatch.setattr(terminal, "_get_config", lambda request: {"persist": True})
    assert (await _list(monkeypatch, tmux=False))["persist_available"] is False

    monkeypatch.setattr(terminal, "_get_config", lambda request: {"persist": False})
    assert (await _list(monkeypatch, tmux=True))["persist_available"] is True


@pytest.mark.asyncio
async def test_persistence_needs_both(monkeypatch):
    """Pin the conjunction the published fact exists to explain."""
    monkeypatch.setattr(terminal, "_tmux_available", lambda: False)
    monkeypatch.setattr(terminal, "_get_config", lambda request: {"persist": True})
    assert terminal._persist_enabled(_make_request()) is False

    monkeypatch.setattr(terminal, "_tmux_available", lambda: True)
    assert terminal._persist_enabled(_make_request()) is True

    monkeypatch.setattr(terminal, "_get_config", lambda request: {"persist": False})
    assert terminal._persist_enabled(_make_request()) is False
