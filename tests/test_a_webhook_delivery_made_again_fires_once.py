"""A webhook delivery its sender names runs once, however many times the sender makes it.

A program that posts to a webhook automation and loses the answer (a timeout, a dropped
connection) sends the post again, and each post was the automation firing: one delivery ran its
action twice. The same held for an outside system calling back an agent's callback: each call was
a turn. Neither door could tell a delivery made again from a new one, since a post carried nothing
that named it.

Now a sender names each delivery with an ``Idempotency-Key`` header. A post whose name the door
already took (from that sender, for that automation or that callback's session) is answered as
received and starts nothing, for a week, after a restart too (the record lives in the home). A
name is taken only with a fire or a turn that starts: a post the automation's own rules held, or
one refused for capacity, took nothing, and the same name sent again later runs. A post without
the header is a new delivery every time, as before, and a header that names nothing is refused.

The fire door is driven on the real gateway, asking for a sign-in, as a program meets it; the
callback door through its handler, with the turn it starts recorded instead of run.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from signed_in_gateway import Gateway, signed_in_gateway

from personalclaw.action_providers.base import ActionResult
from personalclaw.cancellation import settle
from personalclaw.dashboard.handlers import hooks as hooks_mod
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: The action the automation runs: one that records each run and succeeds.
PROBE = "delivery-probe"
AUTOMATION = "store:webhook:build-finished"


class _Probe:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    async def execute(self, config: dict, ctx: Any, timeout: int = 30) -> ActionResult:
        self.runs.append(dict(ctx.payload or {}))
        return ActionResult(success=True, stdout="done")


@pytest.fixture
def probe(monkeypatch) -> _Probe:
    from personalclaw.action_providers import registry

    action = _Probe()
    registry._ensure_default_providers_registered()
    monkeypatch.setitem(registry._providers, PROBE, action)
    return action


def _automation(home: Path, **fields: Any) -> None:
    """A webhook automation its owner made and allowed to run its action."""
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id="webhook:build-finished",
            name="Build finished",
            kind="webhook",
            created_by="user",
            workflow={"inline": {"provider": PROBE, "config": {}}},
            capabilities={"providers": [PROBE]},
            **fields,
        )
    )


async def _sender(gw: Gateway) -> str:
    status, body = await gw.as_owner(
        "POST",
        "/api/external-access/clients",
        json={"label": "Build server", "surfaces": ["webhook"], "scope": {"trigger": AUTOMATION}},
    )
    assert status == 200, body
    return str(body["token"])


async def _post(gw: Gateway, token: str, name: str | None = None) -> tuple[int, dict]:
    """The program's post, naming its delivery with *name* when it gives one, and what it
    started, run to its end."""
    headers = {"Authorization": f"Bearer {token}"}
    if name is not None:
        headers["Idempotency-Key"] = name
    async with aiohttp.ClientSession() as http:
        resp = await http.post(
            gw.url(f"/api/triggers/{AUTOMATION}/fire"), data=b"build 214 passed", headers=headers
        )
        answer = resp.status, await resp.json(content_type=None)
    await settle(gw.state._background_tasks)
    return answer


def _audited(home: Path) -> list[int]:
    path = home / "inbound_audit.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r["status"] for r in rows if r["surface"] == "webhook"]


# ── the fire door ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_post_its_sender_makes_again_fires_its_automation_once(
    tmp_path, monkeypatch, probe
):
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home)
        token = await _sender(gw)

        first = await _post(gw, token, "delivery-0001")
        again = await _post(gw, token, "delivery-0001")
        quoted = await _post(gw, token, '"delivery-0001"')

    assert first[0] == 202 and first[1]["accepted"] is True, first
    assert "already_received" not in first[1]
    for status, body in (again, quoted):
        assert status == 202 and body["already_received"] is True, body
    assert len(probe.runs) == 1, "the post made again fired the automation again"
    assert _audited(gw.home) == [202, 202, 202], "every answer is a row of the inbound audit"


@pytest.mark.asyncio
async def test_two_deliveries_and_posts_that_name_none_each_fire(tmp_path, monkeypatch, probe):
    """The partner of the test above: a different name is a different delivery, and a post that
    names none is a new one every time, as it always was."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home)
        token = await _sender(gw)
        answers = [
            await _post(gw, token, name) for name in ("delivery-0001", "delivery-0002", None, None)
        ]

    assert [status for status, _ in answers] == [202] * 4, answers
    assert not any(body.get("already_received") for _, body in answers)
    assert len(probe.runs) == 4


@pytest.mark.asyncio
async def test_a_post_its_automations_rules_held_takes_no_name_and_its_retry_fires(
    tmp_path, monkeypatch, probe
):
    """A post the automation's hourly cap held started nothing, so its name is still free: once
    the cap allows, the same post sent again fires."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home, gates={"max_runs_per_hour": 1})
        token = await _sender(gw)

        ran = await _post(gw, token, "delivery-0001")
        held = await _post(gw, token, "delivery-0002")
        _automation(gw.home, gates={"max_runs_per_hour": 5})
        retried = await _post(gw, token, "delivery-0002")

    assert ran[0] == 202 and held[0] == 429, (ran, held)
    assert retried[0] == 202 and "already_received" not in retried[1], retried
    assert len(probe.runs) == 2


@pytest.mark.asyncio
async def test_a_delivery_name_that_names_nothing_is_refused(tmp_path, monkeypatch, probe):
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _automation(gw.home)
        token = await _sender(gw)
        answers = [await _post(gw, token, name) for name in ("", '""', "x" * 256)]

    for status, body in answers:
        assert status == 400, body
        assert body["error"]["code"] == "invalid_request"
        assert "Idempotency-Key" in body["error"]["message"]
    assert probe.runs == []


# ── the callback door ─────────────────────────────────────────────────────────


class _Turns:
    """Stands in for the turn a call back starts, and records what it was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def __call__(self, *args):
        self.calls.append(args)
        hooks_mod._hook_semaphore.release()


@pytest.fixture
def turns(tmp_path, monkeypatch):
    from personalclaw.inbound import caps

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    caps.reset_for_tests()
    recorder = _Turns()
    monkeypatch.setattr(hooks_mod, "_run_hook_agent", recorder)
    monkeypatch.setattr(hooks_mod, "_hook_token_refusal", lambda _request: "")
    return recorder


def _call_back(session_key: str, name: str | None) -> dict:
    """The outside system posting its results from this machine, holding the webhook token."""
    app = web.Application()
    app["state"] = types.SimpleNamespace(_background_tasks=set())
    headers = {} if name is None else {"Idempotency-Key": name}
    req = make_mocked_request("POST", "/api/hooks/agent", headers=headers, app=app).clone(
        remote="127.0.0.1"
    )

    async def _json():
        return {"message": "CI passed", "sessionKey": session_key, "deliver": False}

    req.json = _json  # type: ignore[assignment]

    async def _go():
        resp = await hooks_mod.api_hooks_agent(req)
        for task in list(app["state"]._background_tasks):
            await task
        return resp

    resp = asyncio.run(_go())
    return {**json.loads(resp.body), "http": resp.status}


def test_a_call_back_made_again_runs_one_turn(turns):
    first = _call_back("hook:ci-1", "run-41")
    again = _call_back("hook:ci-1", "run-41")
    other_session = _call_back("hook:ci-2", "run-41")
    unnamed = [_call_back("hook:ci-1", None) for _ in range(2)]

    assert first["http"] == 200 and "already_received" not in first
    assert again["http"] == 200 and again["already_received"] is True
    assert other_session["http"] == 200 and "already_received" not in other_session
    assert [c[1] for c in turns.calls] == ["hook:ci-1", "hook:ci-2", "hook:ci-1", "hook:ci-1"]
    assert not any(u.get("already_received") for u in unnamed)


def test_a_call_back_refused_for_capacity_takes_no_name(turns, monkeypatch):
    monkeypatch.setattr(hooks_mod, "_hook_semaphore", asyncio.Semaphore(0))
    busy = _call_back("hook:ci-1", "run-41")
    monkeypatch.setattr(hooks_mod, "_hook_semaphore", asyncio.Semaphore(1))
    retried = _call_back("hook:ci-1", "run-41")

    assert busy["http"] == 429, busy
    assert retried["http"] == 200 and "already_received" not in retried
    assert len(turns.calls) == 1


def test_a_call_back_whose_name_names_nothing_is_refused(turns):
    refused = _call_back("hook:ci-1", "bad\tname")

    assert refused["http"] == 400
    assert refused["error"]["code"] == "invalid_request"
    assert turns.calls == []
