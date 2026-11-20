"""An external agent's ``open_cockpit`` opens a dashboard page in the tab you used last, or says
why not.

The control bridge offered ``open_cockpit`` and answered ``{route}`` as if it had worked: it
broadcast whatever route the call built to every open page, and nothing in the dashboard read the
frame. Now:

* the page must be one an agent may open (``bridge.AGENT_PAGES``, a pinned subset of the
  dashboard's routes), and an id only of the ASCII characters a ``personalclaw://`` link's segment
  may carry;
* a client opens at most one page every ``NAVIGATE_EVERY_SECS``;
* the frame carries the page, the id and the client's name, never a built route, and goes to the
  one dashboard tab on this computer that took focus last: not to every tab, not to an app's or a
  scoped client's socket, not to a paired device's, not to another address;
* each of those that cannot be done answers an error and sends nothing, so the agent is told the
  truth.

The dashboard's reader is ``web/src/app/anAgentOpensAPageInTheTabYouUsedLast.test.tsx``.
"""

from __future__ import annotations

import asyncio
import itertools
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.inbound import bridge, caps
from personalclaw.inbound.clients import InboundClient
from tests.test_control_bridge import _payload, _request, _State

ROOT = Path(__file__).resolve().parents[1]

#: The pages an agent may open, pinned: one more is a decision to make here, not a side effect of
#: the dashboard adding a route.
PINNED = {
    "dashboard",
    "chat",
    "projects",
    "library",
    "knowledge",
    "memory",
    "tasks",
    "inbox",
    "automations",
    "triggers",
    "workflows",
    "files",
    "artifacts",
    "notifications",
    "loop",
    "loops",
    "mission-control",
}


@pytest.fixture(autouse=True)
def admitted(monkeypatch):
    monkeypatch.setattr(bridge, "admission_problem", lambda _s: (None, 200))
    monkeypatch.setattr(bridge, "peer_allowed", lambda _r, _s: (True, ""))
    monkeypatch.setattr(bridge, "verify_bearer", lambda _s, _t: True)
    caps.reset_for_tests()
    yield
    caps.reset_for_tests()


async def _open(params: dict, state: _State | None = None) -> tuple[int, dict, list]:
    state = state or _State()
    resp = await bridge.handle_action(
        _request(
            state,
            headers={"Authorization": "Bearer x"},
            body={"action": "open_cockpit", "params": params},
        )
    )
    return resp.status, _payload(resp), state.broadcasts


def test_the_pages_an_agent_may_open_are_pinned_dashboard_routes():
    text = (ROOT / "desktop" / "deepLinks.js").read_text(encoding="utf-8")
    block = re.search(r"const ROUTES = new Set\(\[(.*?)\]\);", text, re.S)
    assert block, "deepLinks.js no longer declares ROUTES as a Set literal"
    routes = set(re.findall(r'"([a-z-]+)"', block.group(1)))
    assert set(bridge.AGENT_PAGES) == PINNED
    assert PINNED <= routes, sorted(PINNED - routes)
    # The pages that configure what runs, run code, or hold sign-ins are not an agent's to open.
    assert not {"settings", "terminal", "app", "apps", "code", "discover", "tools"} & PINNED


@pytest.mark.asyncio
async def test_a_page_and_an_id_go_to_the_tab_used_last_as_fields_not_a_route():
    status, body, sent = await _open({"kind": "loops", "id": "abc12345"})

    assert status == 200, body
    assert body["result"]["route"] == "#/loops/abc12345"
    assert sent == [
        (
            "navigate",
            {
                "kind": "loops",
                "id": "abc12345",
                "source": "control_bridge",
                "client": "A local agent",
            },
        )
    ]


@pytest.mark.asyncio
async def test_the_frame_names_the_client_that_asked(monkeypatch):
    editor = InboundClient(client_id="c-editor", label="Editor extension", surfaces=["bridge"])
    monkeypatch.setattr(bridge, "lookup_by_token", lambda _t, _s: (editor, ""))

    status, body, sent = await _open({"kind": "chat"})

    assert status == 200, body
    assert sent[0][1]["client"] == "Editor extension"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"kind": "nowhere"},
        {"kind": "settings"},
        {"kind": "terminal"},
        {"kind": "javascript:alert(1)"},
        {"kind": "loops/../settings"},
        {"kind": "loops", "id": ".."},
        {"kind": "loops", "id": "a/b"},
        {"kind": "loops", "id": "x?y=1"},
        {"kind": "loops", "id": "x#y"},
        {"kind": "loops", "id": "a" * 129},
        {"kind": "loops", "id": "café"},
        {"kind": "loops", "id": "ａｂｃ"},
        {"kind": "loops", "id": "x＠y"},
    ],
)
async def test_anything_but_an_agents_page_is_refused_and_sends_nothing(params):
    """🔴 Red before: each was broadcast to every page as a route, and answered as opened."""
    status, body, sent = await _open(params)

    assert status == 400, body
    assert body["error"]["code"] == "bad_request"
    assert sent == []


@pytest.mark.asyncio
async def test_with_no_dashboard_open_here_the_agent_is_told_so():
    status, body, sent = await _open({"kind": "loops"}, _State(focused=False))

    assert status == 400, body
    assert "no dashboard is open on this computer" in body["error"]["message"]
    assert sent == []


@pytest.mark.asyncio
async def test_a_client_opens_at_most_one_page_every_few_seconds(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(caps.time, "monotonic", lambda: now[0])
    state = _State()

    first, _, _ = await _open({"kind": "loops"}, state)
    second, body, _ = await _open({"kind": "chat"}, state)
    now[0] += bridge.NAVIGATE_EVERY_SECS + 0.1
    third, _, _ = await _open({"kind": "tasks"}, state)

    assert (first, second, third) == (200, 400, 200)
    assert "at most one page every 3 seconds" in body["error"]["message"]
    assert [frame["kind"] for _, frame in state.broadcasts] == ["loops", "tasks"]


# ── the one tab it goes to ───────────────────────────────────────────────────────────────────


@pytest.fixture
def hub(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    from personalclaw.dashboard import state as state_mod
    from personalclaw.dashboard.state import DashboardState

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(state_mod, "config_dir", lambda: tmp_path)
    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


def _socket() -> MagicMock:
    return MagicMock(closed=False, send_str=AsyncMock())


@pytest.mark.asyncio
async def test_navigate_goes_to_the_owners_tab_here_that_took_focus_last(hub, monkeypatch):
    ticks = itertools.count(10)
    monkeypatch.setattr(
        "personalclaw.dashboard.ws_state.time.monotonic", lambda: float(next(ticks))
    )
    earlier, later, app, scoped, elsewhere = (_socket() for _ in range(5))
    hub.register_ws(earlier, here=True)
    hub.register_ws(later, here=True)
    hub.register_ws(app, app="some-app", here=True)
    hub.register_ws(scoped, scope=("loops",), here=True)
    hub.register_ws(elsewhere)  # a paired phone, or another address: not the owner's here
    for ws in (earlier, later, app, scoped, elsewhere):
        hub.note_focus(ws)

    assert hub.send_to_last_focused("navigate", {"kind": "loops"}) is True
    await asyncio.sleep(0)

    later.send_str.assert_called_once()
    for ws in (earlier, app, scoped, elsewhere):
        ws.send_str.assert_not_called()


def test_a_tab_that_never_took_focus_or_has_closed_is_not_sent_to(hub):
    unfocused, closed = _socket(), _socket()
    hub.register_ws(unfocused, here=True)
    hub.register_ws(closed, here=True)
    hub.note_focus(closed)
    hub.unregister_ws(closed)

    assert hub.send_to_last_focused("navigate", {"kind": "loops"}) is False


@pytest.mark.parametrize(
    ("remote", "host", "headers", "app", "scope", "paired", "here"),
    [
        ("127.0.0.1", "127.0.0.1", {}, "", (), "", True),
        ("::1", "::1", {}, "", (), "", True),
        ("127.0.0.1", "localhost", {}, "", (), "", True),
        ("192.168.1.20", "192.168.1.5", {}, "", (), "", False),
        ("127.0.0.1", "claw.example.com", {}, "", (), "", False),
        ("127.0.0.1", "127.0.0.1", {"X-Real-IP": "203.0.113.9"}, "", (), "", False),
        ("127.0.0.1", "127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}, "", (), "", False),
        ("127.0.0.1", "127.0.0.1", {}, "some-app", (), "", False),
        ("127.0.0.1", "127.0.0.1", {}, "", ("loops",), "", False),
        ("127.0.0.1", "127.0.0.1", {}, "", (), "phone-1", False),
    ],
    ids=[
        "loopback",
        "loopback v6",
        "localhost",
        "another address",
        "a tunnel's public name",
        "a proxy's X-Real-IP",
        "a proxy's X-Forwarded-For",
        "an app",
        "a scoped client",
        "a phone",
    ],
)
def test_only_the_owners_own_socket_on_this_computer_is_here(
    monkeypatch, remote, host, headers, app, scope, paired, here
):
    from personalclaw.dashboard import ws

    monkeypatch.setattr(ws, "_paired_device_session", lambda _r: paired)
    request = MagicMock(remote=remote, headers=headers)
    request.url.host = host
    request.get = lambda key, default=None: {"app": app}.get(key, default)

    assert ws._the_owners_own_here(request, scope) is here


def test_a_listed_trusted_proxy_on_this_computer_is_not_here(monkeypatch):
    """A proxy the owner listed (``dashboard.trusted_proxies``) that runs on this computer is a
    loopback peer for every visitor it carries, with or without a forwarding header."""
    from personalclaw.dashboard import exposure, ws

    monkeypatch.setattr(exposure, "trusted_proxies", lambda cfg=None: ["127.0.0.1"])
    monkeypatch.setattr(ws, "_paired_device_session", lambda _r: "")
    request = MagicMock(remote="127.0.0.1", headers={})
    request.url.host = "127.0.0.1"
    request.get = lambda key, default=None: None

    assert ws._the_owners_own_here(request, ()) is False
