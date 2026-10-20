"""Each gateway start's sign-in link replaces the one the start before it made.

Every start minted a new 30-day owner session for its sign-in link. A link no browser opened
stayed live for its whole lifetime anyway, so a service that launchd restarted again and again (a
crash loop under KeepAlive) piled up one live credential per start, each listed in Settings →
Devices as "The link the gateway printed at startup".

The behaviour the code must have: a start ends every earlier startup link that no browser has
opened, whether or not it makes a new one, and a device that presents an ended one is told why.
A link a browser DID open is that browser's sign-in, and a restart leaves it signed in (the
browser that opens the next link swaps its sign-in for the new one by itself).

Driven through the real start path (``GatewayOrchestrator.run``) and the real session store.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from test_a_sign_in_link_never_reaches_a_log import _start_gateway, _startup_links

from personalclaw import gateway
from personalclaw.dashboard import session_store, token_auth

_CHROME_ON_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


@pytest.fixture(autouse=True)
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each start prints its link to a terminal, and opens no browser."""
    monkeypatch.setattr(gateway, "_shown_at_a_terminal", lambda: True, raising=False)


def test_restarts_leave_one_startup_link(capsys) -> None:
    """🔑 Three starts, one live startup link: the newest."""
    for _ in range(3):
        _start_gateway(no_open=True)
    capsys.readouterr()

    links = _startup_links()
    assert len(links) == 1, f"{len(links)} startup links are live after three starts"


def test_a_replaced_link_tells_its_holder_why(capsys) -> None:
    _start_gateway(no_open=True)
    (first,) = _startup_links()
    _start_gateway(no_open=True)
    capsys.readouterr()

    ended = session_store.ended_session(first)
    assert ended is not None and ended.reason == session_store.END_SUPERSEDED, ended
    assert first not in session_store.load_sessions()


def test_a_start_that_makes_no_link_still_ends_the_one_before(monkeypatch, capsys) -> None:
    """A service start shows no link and opens no browser, so it makes none; the link an
    earlier start printed is still moot, and it ends."""
    _start_gateway(no_open=True)
    assert len(_startup_links()) == 1
    monkeypatch.setattr(gateway, "_shown_at_a_terminal", lambda: False)
    _start_gateway(no_open=True)
    capsys.readouterr()

    assert _startup_links() == {}


def test_a_link_a_browser_opened_stays_that_browsers_sign_in(capsys) -> None:
    """The control arm: a restart does not sign the owner's browser out."""
    _start_gateway(no_open=True)
    (opened,) = _startup_links()
    session_store.note_client(
        opened, ip="127.0.0.1", user_agent=_CHROME_ON_MAC, browser_carrier=True
    )
    _start_gateway(no_open=True)
    capsys.readouterr()

    links = _startup_links()
    assert opened in links, "the browser's sign-in survives the restart"
    assert links[opened].device.kind == "browser"
    assert len(links) == 2, "the browser's sign-in, and the newest start's link"


def test_the_ended_link_reads_as_replaced_by_a_newer_start(capsys) -> None:
    """The sentence a device holding a replaced link reads says what happened."""
    _start_gateway(no_open=True)
    printed = re.search(r"\?token=([^\s&]+)", capsys.readouterr().out)
    assert printed, "the start at a terminal printed its link"
    _start_gateway(no_open=True)
    capsys.readouterr()

    notice = token_auth.signed_out_notice(unquote(printed.group(1)))
    assert notice is not None and notice.reason == session_store.END_SUPERSEDED, notice
    assert "started again" in notice.message, notice.message
