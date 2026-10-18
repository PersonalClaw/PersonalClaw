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
import time
from unittest.mock import MagicMock, patch

import pytest
import terminal_persist_capability_shared

from personalclaw import tmux_substrate
from personalclaw.dashboard.handlers import terminal
from tests.test_terminal_handler import _make_request  # noqa: F401  (shared request builder)

_enabled = terminal_persist_capability_shared._enabled


async def _list(monkeypatch, *, tmux: bool, enabled: bool = True) -> dict:
    """Drive the list handler with the host's tmux presence forced either way.

    🪤 The patch target is `tmux_substrate.tmux_available`, NOT a private probe on the handler.
    The handler used to own a byte-identical `_tmux_available()` beside the substrate's, and
    patching the handler's copy could move the published fact while the recovery sweep's fact
    stayed put — the two disagreeing is the whole failure mode. With one owner there is nothing
    left to move independently, which is why this indirection is load-bearing rather than tidy.
    """
    monkeypatch.setattr(tmux_substrate, "tmux_available", lambda: tmux)
    terminal._enabled_cache[0] = enabled
    terminal._enabled_cache[1] = time.monotonic()
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
    monkeypatch.setattr(tmux_substrate, "tmux_available", lambda: False)
    monkeypatch.setattr(terminal, "_get_config", lambda request: {"persist": True})
    assert terminal._persist_enabled(_make_request()) is False

    monkeypatch.setattr(tmux_substrate, "tmux_available", lambda: True)
    assert terminal._persist_enabled(_make_request()) is True

    monkeypatch.setattr(terminal, "_get_config", lambda request: {"persist": False})
    assert terminal._persist_enabled(_make_request()) is False


@pytest.mark.asyncio
async def test_the_capability_is_published_even_when_the_panel_is_off(monkeypatch):
    """ "Can this host persist" is a HOST fact, so switching the terminal panel off must not
    erase it.

    The disabled branch returned its own literal payload — `{"enabled": False, "sessions": []}`
    — which is exactly the shape that loses a key silently, and it now matters beyond the
    terminal: Settings › Agent's durable-workers row asks THIS endpoint whether the host can
    keep a worker's shell alive, a feature that has nothing to do with the panel. Because the
    client reads an absent field as UNAVAILABLE (so an older gateway cannot accidentally
    promise), omitting it here did not read as "unknown" — it read as "this host has no tmux",
    inventing a limitation on a host that has one.
    """
    body = await _list(monkeypatch, tmux=True, enabled=False)
    assert body["enabled"] is False
    assert body["persist_available"] is True
    # And the negative case still reports honestly rather than falling back to a default.
    assert (await _list(monkeypatch, tmux=False, enabled=False))["persist_available"] is False
