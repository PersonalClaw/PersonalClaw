"""Every inbound surface records a registered client's use, so Settings › Devices says when.

Only the MCP endpoint and trigger runs wrote a client's ``last_seen_at``: a client in daily use on
the OpenAI-compatible endpoint, A2A, the capture proxy or the control bridge read "Last used
never", so the owner couldn't tell a token in use from one to revoke. Each surface now calls
``clients.admitted(client)`` once its last gate before the work has passed. A refused request
records nothing, and the write is held to one every ``tokens._LAST_SEEN_EVERY_SECS``, as a
surface token's is.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.inbound import auth, clients, tokens


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = [auth.token_env_key(surface) for surface in auth.surfaces()]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    clients.reset_for_tests()
    yield
    clients.reset_for_tests()
    for key in keys:
        os.environ.pop(key, None)


def _config(monkeypatch) -> None:
    """External access on, every surface on, on this machine only."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    on = {name: Surface(enabled=True) for name in auth.surfaces()}
    cfg.external_access = ExternalAccessConfig(enabled=True, **on)
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    for surface in auth.surfaces():
        auth.create_surface_token(surface)


def _request(method: str, path: str, token: str):
    transport = MagicMock()
    transport.get_extra_info.side_effect = lambda name, default=None: (
        ("127.0.0.1", 52100) if name == "peername" else default
    )
    app = web.Application()
    app["state"] = None
    return make_mocked_request(
        method,
        path,
        headers={"Authorization": f"Bearer {token}", "Host": "127.0.0.1"},
        transport=transport,
        app=app,
    )


def _seen(client_id: str) -> str:
    return clients.load_clients()[client_id].last_seen_at


def _client(surface: str) -> tuple[str, str]:
    made, token = clients.create_client(f"{surface} app", surfaces=[surface])
    assert made.last_seen_at == ""
    return made.client_id, token


def test_an_admitted_client_has_its_use_recorded_at_most_every_few_minutes(monkeypatch):
    made, _ = clients.create_client("notes app", surfaces=["openai"])
    saves: list[int] = []
    real_save = clients.save_clients
    monkeypatch.setattr(clients, "save_clients", lambda c: saves.append(1) or real_save(c))

    clients.admitted(clients.load_clients()[made.client_id])
    assert _seen(made.client_id)
    clients.admitted(clients.load_clients()[made.client_id])
    assert len(saves) == 1  # the second, inside the window, writes nothing

    stale = datetime.now(timezone.utc) - timedelta(seconds=tokens._LAST_SEEN_EVERY_SECS + 1)
    record = clients.load_clients()[made.client_id]
    record.last_seen_at = stale.isoformat()
    clients.admitted(record)
    assert len(saves) == 2


def test_a_surface_token_caller_records_nothing():
    clients.admitted(None)  # no client, nothing to write, nothing raised


def test_a_client_record_that_cant_be_read_never_fails_the_request():
    """A surface passes whatever its lookup gave it; one with no readable fields records nothing
    and raises nothing, so the request goes on."""
    from types import SimpleNamespace

    clients.admitted(SimpleNamespace())
    clients.admitted(SimpleNamespace(client_id="c1", last_seen_at=object()))


def test_an_unreadable_last_seen_is_written_over():
    made, _ = clients.create_client("notes app", surfaces=["openai"])
    record = clients.load_clients()[made.client_id]
    record.last_seen_at = "not a time"
    clients.admitted(record)
    assert _seen(made.client_id) != "not a time"


def test_a_last_seen_ahead_of_now_is_written_over():
    """🔴 Red before: a time ahead of now read as recent, so no use was written until it passed."""
    made, _ = clients.create_client("notes app", surfaces=["openai"])
    ahead = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    held = clients.load_clients()
    held[made.client_id].last_seen_at = ahead
    clients.save_clients(held)
    clients.admitted(clients.load_clients()[made.client_id])
    assert _seen(made.client_id) != ahead


def test_a2a_records_its_clients_use(monkeypatch):
    """🔴 Red before: A2A never recorded a client's use."""
    from personalclaw.inbound import a2a

    _config(monkeypatch)
    client_id, token = _client("a2a")
    refusal, admitted_id, _caps = a2a._admit(_request("POST", "/a2a/tasks", token), a2a.ROUTE_TASKS)
    assert refusal is None and admitted_id == client_id
    assert _seen(client_id)


def test_capture_records_its_clients_use(monkeypatch):
    """🔴 Red before."""
    from personalclaw.inbound import capture_proxy

    _config(monkeypatch)
    client_id, token = _client("capture")
    refusal, _reason, admitted_id = capture_proxy._admit(
        _request("POST", "/capture/v1/messages", token), "/capture/v1/messages"
    )
    assert refusal is None and admitted_id == client_id
    assert _seen(client_id)


def test_the_bridge_records_its_clients_use(monkeypatch):
    """🔴 Red before."""
    from personalclaw.inbound import bridge

    _config(monkeypatch)
    client_id, token = _client(bridge.BRIDGE_SURFACE)
    refusal, _reason, client = bridge._admit(_request("GET", "/actions", token), "/actions")
    assert refusal is None and client.client_id == client_id
    assert _seen(client_id)


@pytest.mark.asyncio
async def test_the_openai_endpoints_models_records_its_clients_use(monkeypatch):
    """🔴 Red before: the OpenAI-compatible endpoint never recorded a client's use."""
    from personalclaw.inbound import openai_dialect

    _config(monkeypatch)
    monkeypatch.setattr(openai_dialect, "visible_agents", lambda client, cfg: [])
    client_id, token = _client("openai")
    resp = await openai_dialect.handle_models(_request("GET", openai_dialect.ROUTE_MODELS, token))
    assert resp.status == 200
    assert _seen(client_id)


def test_the_openai_endpoints_rate_check_records_a_use_only_when_it_admits(monkeypatch):
    """🔴 Red before for the admitted call. A call the rate limit refuses records nothing."""
    from personalclaw.inbound import caps, openai_dialect

    client_id, _token = _client("openai")
    client = clients.load_clients()[client_id]
    monkeypatch.setattr(caps, "check_rate_for_client", lambda *a, **k: False)
    monkeypatch.setattr(caps, "retry_after_for_client", lambda *a, **k: 1)
    assert openai_dialect._rate_refusal(openai_dialect.ROUTE_CHAT, client, client_id) is not None
    assert _seen(client_id) == ""
    monkeypatch.setattr(caps, "check_rate_for_client", lambda *a, **k: True)
    assert openai_dialect._rate_refusal(openai_dialect.ROUTE_CHAT, client, client_id) is None
    assert _seen(client_id)


def test_a_client_refused_on_another_surface_records_nothing(monkeypatch):
    """A client bound to one surface presenting its token on another is refused, and that is no
    use of the surface it was refused on."""
    from personalclaw.inbound import a2a

    _config(monkeypatch)
    client_id, token = _client("openai")
    refusal, _id, _caps = a2a._admit(_request("POST", "/a2a/tasks", token), a2a.ROUTE_TASKS)
    assert refusal is not None and refusal.status == 401
    assert _seen(client_id) == ""
