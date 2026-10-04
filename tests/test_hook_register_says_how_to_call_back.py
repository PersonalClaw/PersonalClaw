"""What ``hook_register`` tells an outside system to send is what the gateway takes.

The chat's ``hook_register`` saves a callback and answers with an address and what to post to it,
for the agent to hand to the outside system that will call back. Measured before this was written,
on a gateway that asks for a sign-in: the answer said to post the webhook token straight to
``/api/hooks/agent`` as ``Authorization: Bearer``, and the gateway refused that with its own
``auth_bearer_invalid`` before the route read it (with ``x-personalclaw-token``, with the browser's
"isn't signed in" sentence). The route took a call only from a program on this machine holding the
gateway's internal credential, which opens every other internal operation too, and which nothing
was ever told to send.

So the answer is read here as the outside system reads it, and followed to the letter against the
real gateway: its address, its header and its body, with only the placeholders filled in.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from signed_in_gateway import Gateway, signed_in_gateway

from personalclaw import mcp_core
from personalclaw.dashboard.handlers import hooks as hooks_mod

#: A webhook token as the owner sets one: long, and nothing else's.
TOKEN = "wh-" + "k7Qe2mZp9xR4" * 4
#: What the outside system has to say when it calls back.
RESULTS = "CI passed on the release branch"


@pytest.fixture
def turns(monkeypatch):
    """Stands in for the agent turn a call back starts, and records what it was handed."""
    calls: list[tuple] = []

    async def _turn(*args):
        calls.append(args)
        hooks_mod._hook_semaphore.release()

    monkeypatch.setattr(hooks_mod, "_run_hook_agent", _turn)
    monkeypatch.setattr(
        mcp_core, "_resolve_session_key", lambda: "dashboard:chat-ci", raising=False
    )
    return calls


def _set_webhook_token(token: str) -> None:
    """What `personalclaw config set hooks.webhook_token <token>` stores."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.hooks["webhook_token"] = token
    cfg.save()


def _register(hook_id: str = "review:pr-1") -> str:
    return mcp_core._call_tool_inner(
        "hook_register", {"hook_id": hook_id, "context_summary": "merge it once CI is green"}
    )


def _instruction(said: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """The address, header and body the answer says to send, read as an outside system reads it."""
    (url,) = re.findall(r"https?://\S+/api/hooks/agent", said)
    (body,) = [json.loads(line) for line in re.findall(r"\{.*\"sessionKey\".*\}", said)]
    (header,) = re.findall(r"(Authorization): Bearer <([^>]+)>", said)
    return url, {header[0]: f"Bearer <{header[1]}>"}, body


def _filled(headers: dict[str, str], body: dict[str, Any]) -> tuple[dict[str, str], dict]:
    """Only the placeholders filled: the owner's token, and the outside system's results."""
    filled = {name: re.sub(r"<[^>]+>", TOKEN, value) for name, value in headers.items()}
    return filled, {**body, "message": RESULTS}


async def _send(url: str, headers: dict[str, str], body: Any) -> tuple[int, Any]:
    async with aiohttp.ClientSession() as http:
        resp = await http.post(url, json=body, headers=headers)
        return resp.status, await resp.json(content_type=None)


async def _allow(gw: Gateway, hook_id: str = "review:pr-1") -> None:
    """The owner's Allow on the Triggers page, which names the context it showed them."""
    status, listed = await gw.as_owner("GET", "/api/triggers?type=callback")
    assert status == 200, listed
    (row,) = [t for t in listed["triggers"] if t["raw_id"] == hook_id]
    status, body = await gw.as_owner(
        "POST",
        f"/api/triggers/callback:{hook_id}/toggle",
        json={"enabled": True, "seal": row["seal"], "confirm": True},
    )
    assert status == 200, body


async def _started(turns: list[tuple]) -> list[tuple]:
    """The turns the gateway started, once the one it accepted has started: it starts it in the
    background, after it answered."""
    deadline = time.monotonic() + 5
    while not turns and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    return turns


def _audit(home: Path) -> list[dict]:
    path = home / "inbound_audit.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r["surface"] == "webhook"]


@pytest.mark.asyncio
async def test_the_printed_instruction_calls_the_callback_back(tmp_path, monkeypatch, turns):
    """🔴 Red before: the gateway answered 403 `auth_bearer_invalid` to exactly what the answer
    said to send, waiting callback or allowed one alike."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _set_webhook_token(TOKEN)
        url, headers, body = _instruction(_register())
        headers, body = _filled(headers, body)

        status, waiting = await _send(url, headers, body)
        assert status == 403, waiting
        assert waiting["error"]["code"] == "not_allowed"
        assert "Triggers page" in waiting["error"]["message"]
        assert turns == []

        await _allow(gw)
        status, accepted = await _send(url, headers, body)

        assert status == 200, accepted
        assert accepted == {"status": "accepted", "sessionKey": "hook:review:pr-1"}
        ((_state, session_key, message, *_rest),) = await _started(turns)
        assert (session_key, message) == ("hook:review:pr-1", RESULTS)
        assert [r["status"] for r in _audit(gw.home)] == [403, 202]


@pytest.mark.asyncio
async def test_the_answer_says_where_the_address_answers_and_how_the_token_is_set(
    tmp_path, monkeypatch, turns
):
    """🔴 Red before: "External systems should POST to this URL" with a `localhost` address and
    no word that it answers this machine only, or where the token it names comes from."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        said = _register()

        url, headers, _body = _instruction(said)
        assert url == gw.url("/api/hooks/agent")
        assert "programs on the machine PersonalClaw runs on" in said and "over SSH" in said
        assert headers == {"Authorization": "Bearer <webhook token>"}
        assert "personalclaw config set hooks.webhook_token" in said
        assert "at least 32 characters" in said


@pytest.mark.asyncio
async def test_a_call_back_without_the_webhook_token_is_refused_with_a_sentence_and_audited(
    tmp_path, monkeypatch, turns
):
    """🔴 Red before: the gateway's sign-in answered first, and the route itself answered a bare
    `{"error": "unauthorized"}` that said nothing about what was missing."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _set_webhook_token(TOKEN)
        url, headers, body = _instruction(_register())
        _filled_headers, body = _filled(headers, body)
        await _allow(gw)

        for sent in ({}, {"Authorization": "Bearer not-the-webhook-token"}):
            status, refused = await _send(url, sent, body)
            assert status == 401, refused
            assert refused["error"]["code"] == "unauthorized"
            message = refused["error"]["message"]
            assert "webhook token" in message and "Authorization: Bearer" in message

        assert turns == []
        assert [r["status"] for r in _audit(gw.home)] == [401, 401]
        assert all(r.get("refused_reason") for r in _audit(gw.home))
        status, log = await gw.as_owner("GET", "/api/security/audit?limit=200")
        denied = [
            e
            for e in log["events"]
            if e.get("outcome") == "denied"
            and str(e.get("caller_identity", "")).startswith("inbound:webhook")
        ]
        assert len(denied) == 2, denied


@pytest.mark.asyncio
async def test_the_internal_credential_does_not_open_it(tmp_path, monkeypatch, turns):
    """🔴 Red before: from this machine, the gateway's internal credential was what opened the
    route, a credential that opens every other internal operation too."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _set_webhook_token(TOKEN)
        url, headers, body = _instruction(_register())
        headers, body = _filled(headers, body)
        await _allow(gw)

        status, refused = await _send(
            url, {**headers, "X-Internal-Secret": gw.internal_secret}, body
        )

        assert status == 403, refused
        assert refused["error"]["code"] == "internal_route_refused"
        assert turns == []


@pytest.mark.asyncio
async def test_a_webhook_token_shorter_than_32_characters_opens_nothing(
    tmp_path, monkeypatch, turns
):
    """🔴 Red before: any token at all was taken, however short; and the gateway's sign-in refused
    the call before that mattered. The token is now the one thing between the address and an
    agent turn, so a short one is refused like a missing one, and the audit row says why."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _set_webhook_token("short-token")
        url, _headers, body = _instruction(_register())
        _unused, body = _filled({}, body)
        await _allow(gw)

        status, refused = await _send(url, {"Authorization": "Bearer short-token"}, body)

        assert status == 401, refused
        (row,) = _audit(gw.home)
        assert "32" in row["refused_reason"]
        assert turns == []


@pytest.mark.asyncio
async def test_a_call_back_from_another_address_is_refused_with_how_to_reach_it(
    tmp_path, monkeypatch, turns
):
    """🔴 Red before: from another address the route answered the gateway's bare "Forbidden",
    which named neither the cause nor the way to reach it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _set_webhook_token(TOKEN)
    app = web.Application()
    app["state"] = type("State", (), {"_background_tasks": set()})()
    request = make_mocked_request(
        "POST",
        "/api/hooks/agent",
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1:10000"},
        app=app,
    ).clone(remote="192.0.2.10")

    response = await hooks_mod.api_hooks_agent(request)

    assert response.status == 403
    message = json.loads(response.body)["error"]["message"]
    assert "192.0.2.10" in message and "over SSH" in message
    (row,) = _audit(tmp_path)
    assert row["status"] == 403 and row["refused_reason"]
    assert turns == []
