"""An outside program fires a webhook automation by doing exactly what it was told to do.

A webhook automation runs when a program posts to its address, ``POST /api/triggers/<id>/fire``,
with a sender token made for that one automation. Measured on a gateway that asks for a sign-in
before this was written:

* no sender token could be made: Settings' client route refused the ``webhook`` surface as
  unknown, and nothing else (the automation's page, the CLI) offered one;
* a token made around that refusal was refused before the door read it, with the dashboard's own
  ``auth_bearer_invalid``, since the door was not one of the routes that sign their callers in
  themselves;
* the automation's required ``spec.token_ref`` was read by nothing at all.

Driven here as the owner and the program meet it: the real gateway asking for a sign-in, the owner
making the token where they make it (Settings' route, the automation's page, the CLI), and the
program sending what the answer said to send, from this machine.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from signed_in_gateway import Gateway, signed_in_gateway

from personalclaw.triggers import tools as trigger_tools
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: What the automation does when it fires: tell its owner, with what the program sent.
NOTIFY = {
    "provider": "notify",
    "config": {"title_template": "The build finished", "body_template": "$body"},
}


def _automation(home: Path, slug: str = "build-finished", *, spec: dict | None = None) -> str:
    """A webhook automation its owner made and allowed. Its address's id (``store:webhook:…``).

    Allowed as the owner's yes leaves it: its action's provider granted (`triggers.grants`)."""
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=f"webhook:{slug}",
            name=slug.replace("-", " ").capitalize(),
            kind="webhook",
            created_by="user",
            spec=dict(spec or {}),
            workflow={"inline": dict(NOTIFY)},
            capabilities={"providers": ["notify"]},
        )
    )
    return f"store:webhook:{slug}"


async def _make_sender(gw: Gateway, automation: str, label: str = "Build server") -> dict:
    """The owner makes a sender token through Settings' route. Its answer."""
    status, body = await gw.as_owner(
        "POST",
        "/api/external-access/clients",
        json={"label": label, "surfaces": ["webhook"], "scope": {"trigger": automation}},
    )
    assert status == 200, body
    return body


async def _follow(command: str) -> tuple[int, Any]:
    """Run a printed ``curl`` command as the program would: its header, its body, its address."""
    words = shlex.split(command)
    assert words[0] == "curl", command
    headers: dict[str, str] = {}
    data = ""
    url = ""
    rest = iter(words[1:])
    for word in rest:
        if word == "-H":
            name, _, value = next(rest).partition(":")
            headers[name.strip()] = value.strip()
        elif word == "--data":
            data = next(rest)
        else:
            url = word
    async with aiohttp.ClientSession() as http:
        resp = await http.post(url, data=data.encode(), headers=headers)
        return resp.status, await resp.json(content_type=None)


async def _post(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    async with aiohttp.ClientSession() as http:
        resp = await http.post(url, data=b"the build passed", headers=headers or {})
        return resp.status, await resp.json(content_type=None)


async def _runs(gw: Gateway, automation: str, *, at_least: int = 1) -> list[dict]:
    """The automation's history as its page reads it, once it holds *at_least* runs."""
    deadline = time.monotonic() + 10
    while True:
        status, body = await gw.as_owner("GET", f"/api/triggers/{automation}/history")
        assert status == 200, body
        if len(body["runs"]) >= at_least or time.monotonic() > deadline:
            return body["runs"]
        await asyncio.sleep(0.05)


def _audit(home: Path) -> list[dict]:
    """The inbound audit: one row per request any inbound surface answered."""
    path = home / "inbound_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def _security_log(gw: Gateway) -> list[dict]:
    status, body = await gw.as_owner("GET", "/api/security/audit?limit=200")
    assert status == 200, body
    return body["events"]


# ── the program does what it was told, and the automation runs ──


@pytest.mark.asyncio
async def test_the_token_made_in_settings_fires_the_automation_as_its_answer_says(
    tmp_path, monkeypatch
):
    """🔴 Red before: Settings' route answered 400 "unknown surfaces: webhook", so no token could
    be made, and nothing could fire the automation."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        made = await _make_sender(gw, automation)

        assert made["webhook"]["url"] == gw.url(f"/api/triggers/{automation}/fire")
        assert made["webhook"]["header"] == f"Authorization: Bearer {made['token']}"
        status, body = await _follow(made["webhook"]["curl"])

        assert status == 202, body
        assert body["accepted"] is True
        (run,) = await _runs(gw, automation)
        assert run["status"] == "success", run
        audited = [r for r in _audit(gw.home) if r["surface"] == "webhook"]
        assert [(r["status"], r["client_id"]) for r in audited] == [(202, made["client_id"])]
        allowed = [e for e in await _security_log(gw) if e.get("outcome") == "allowed"]
        assert any(
            e.get("caller_identity") == f"inbound:webhook:{made['client_id']}" for e in allowed
        ), "the security log names the sender of an accepted fire"


@pytest.mark.asyncio
async def test_the_command_the_cli_prints_fires_the_automation(tmp_path, monkeypatch, capsys):
    """🔴 Red before: there was no `personalclaw inbound webhook` at all."""
    from personalclaw.cli import build_parser
    from personalclaw.inbound.auth import inbound_cmd

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        args = build_parser().parse_args(
            ["inbound", "webhook", "create", automation, "--label", "Build server"]
        )

        assert inbound_cmd(args) == 0
        printed = capsys.readouterr().out
        (command,) = [
            line.strip() for line in printed.splitlines() if line.strip().startswith("curl ")
        ]
        status, body = await _follow(command)

        assert status == 202, body
        assert gw.url(f"/api/triggers/{automation}/fire") in printed
        assert "programs on the machine PersonalClaw runs on" in printed
        (run,) = await _runs(gw, automation)
        assert run["status"] == "success", run


def test_the_cli_lists_its_sender_tokens_in_words_and_revokes_one(tmp_path, monkeypatch, capsys):
    """🔴 Red before: there was no `personalclaw inbound webhook`. `list` names each sender token,
    when it stops working and when it was last used, in words and never with its token; `revoke`
    ends one."""
    from personalclaw.cli import build_parser
    from personalclaw.inbound import clients
    from personalclaw.inbound.auth import inbound_cmd

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    automation = _automation(tmp_path)
    used, used_token = clients.create_client(
        "Build server", surfaces=["webhook"], scope={"trigger": automation}
    )
    clients.touch_last_seen(used.client_id)
    idle, idle_token = clients.create_client(
        "Deploy script", surfaces=["webhook"], scope={"trigger": automation}
    )
    parse = build_parser().parse_args

    assert inbound_cmd(parse(["inbound", "webhook", "list", automation])) == 0
    listed = capsys.readouterr().out
    (busy,) = [line for line in listed.splitlines() if "“Build server”" in line]
    (quiet,) = [line for line in listed.splitlines() if "“Deploy script”" in line]
    assert "works until" in busy and "last used today at" in busy
    assert "works until" in quiet and quiet.endswith("never used")
    assert used_token not in listed and idle_token not in listed

    assert inbound_cmd(parse(["inbound", "webhook", "revoke", idle.client_id])) == 0
    assert "Revoked the sender token “Deploy script”" in capsys.readouterr().out
    assert inbound_cmd(parse(["inbound", "webhook", "list", automation])) == 0
    assert "Deploy script" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_the_automations_page_shows_its_address_and_who_may_send_to_it(tmp_path, monkeypatch):
    """🔴 Red before: the page said "When its webhook receives a request" and nothing else, with
    no address to give a program, and no list of what could send to it."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        made = await _make_sender(gw, automation, label="Build server")

        status, body = await gw.as_owner("GET", "/api/triggers")
        assert status == 200, body
        (row,) = [t for t in body["triggers"] if t["id"] == automation]
        assert row["webhook"]["url"] == made["webhook"]["url"]
        assert [(s["label"], s["client_id"]) for s in row["webhook"]["senders"]] == [
            ("Build server", made["client_id"])
        ]
        status, answer = await _post(
            row["webhook"]["url"], headers={"Authorization": f"Bearer {made['token']}"}
        )
        assert status == 202, answer


# ── what is refused, and how it says so ──


@pytest.mark.asyncio
async def test_a_fire_without_its_sender_token_is_refused_with_a_sentence_and_an_audit_row(
    tmp_path, monkeypatch
):
    """🔴 Red before: the dashboard's sign-in answered first, 403 `auth_bearer_invalid` or the
    browser's "This device isn't signed in" sentence, and the door wrote no audit row."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        other = _automation(gw.home, "deploy-finished")
        made_for_other = await _make_sender(gw, other, label="Deploy server")
        fire = gw.url(f"/api/triggers/{automation}/fire")

        for headers in ({}, {"Authorization": "Bearer not-a-token-anyone-made"}):
            status, body = await _post(fire, headers=headers)
            assert status == 401, body
            assert body["error"]["code"] == "unauthorized"
            message = body["error"]["message"]
            assert "Authorization: Bearer" in message and "sender token" in message
            assert "personalclaw inbound webhook create" in message
        status, body = await _post(
            fire, headers={"Authorization": f"Bearer {made_for_other['token']}"}
        )
        assert status == 403, body
        assert "made for another automation" in body["error"]["message"]

        refused = [r for r in _audit(gw.home) if r["surface"] == "webhook"]
        assert [r["status"] for r in refused] == [401, 401, 403]
        assert all(r.get("refused_reason") for r in refused)
        denied = [
            e
            for e in await _security_log(gw)
            if e.get("outcome") == "denied"
            and str(e.get("caller_identity", "")).startswith("inbound:webhook")
        ]
        assert len(denied) == 3, denied
        assert await _runs(gw, automation, at_least=0) == []


@pytest.mark.asyncio
async def test_neither_the_internal_credential_nor_a_sign_in_fires_it(tmp_path, monkeypatch):
    """Control, the same before and after: the door takes its sender token and nothing else. The
    gateway's internal credential is refused as one, and the owner's own sign-in is not a sender
    token, now that the door signs its callers in itself."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        fire = gw.url(f"/api/triggers/{automation}/fire")

        status, body = await _post(fire, headers={"X-Internal-Secret": gw.internal_secret})
        assert status == 403 and body["error"]["code"] == "internal_route_refused", body
        status, body = await _post(
            fire, headers={"Authorization": f"Bearer {await gw.owner_token()}"}
        )
        assert status == 401 and body["error"]["code"] == "unauthorized", body
        assert await _runs(gw, automation, at_least=0) == []


@pytest.mark.asyncio
async def test_a_fire_from_another_address_is_refused_with_how_to_reach_it(tmp_path, monkeypatch):
    """🔴 Red before: the door asked nothing about where the request came from, so a program on
    another machine that sent a browser's `Origin` passed the origin check and reached the token
    check. The webhook takes programs on PersonalClaw's own machine, as the other inbound surfaces
    do, and says how to reach it from anywhere else."""
    from personalclaw.dashboard.handlers import trigger_runs
    from personalclaw.inbound import clients as clients_mod

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    automation = _automation(tmp_path)
    _client, token = clients_mod.create_client(
        "Build server", surfaces=["webhook"], scope={"trigger": automation}
    )
    app = web.Application()
    app["state"] = type("State", (), {"_background_tasks": set()})()
    request = make_mocked_request(
        "POST",
        f"/api/triggers/{automation}/fire",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://127.0.0.1:10000"},
        match_info={"id": automation},
        app=app,
    ).clone(remote="192.0.2.10")

    response = await trigger_runs.api_trigger_fire(request)

    assert response.status == 403
    message = json.loads(response.body)["error"]["message"]
    assert "192.0.2.10" in message and "over SSH" in message
    (row,) = _audit(tmp_path)
    assert row["status"] == 403 and row["surface"] == "webhook" and row["refused_reason"]
    assert app["state"]._background_tasks == set()


# ── what a sender token is, and when it stops ──


@pytest.mark.asyncio
async def test_a_sender_token_is_made_for_one_webhook_automation_and_nothing_else(
    tmp_path, monkeypatch
):
    """🔴 Red before: every one of these was refused for the same reason, "unknown surfaces:
    webhook", which said nothing about what a sender token is."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        manual = trigger_tools.create(
            TriggerStore(base_dir=gw.home),
            name="By hand",
            kind="manual",
            workflow={"inline": dict(NOTIFY)},
            created_by="user",
            owner_consented=True,
        )
        assert manual.ok, manual.text
        asks = {
            "with another surface": {
                "surfaces": ["webhook", "mcp"],
                "scope": {"trigger": automation},
            },
            "with no automation": {"surfaces": ["webhook"]},
            "for an automation that is not there": {
                "surfaces": ["webhook"],
                "scope": {"trigger": "store:webhook:nothing-here"},
            },
            "for an automation that is not a webhook": {
                "surfaces": ["webhook"],
                "scope": {"trigger": f"store:{manual.data['trigger']['id']}"},
            },
            "pinned to an agent": {
                "surfaces": ["webhook"],
                "scope": {"trigger": automation},
                "agent": "researcher",
            },
        }
        for why, ask in asks.items():
            status, body = await gw.as_owner(
                "POST", "/api/external-access/clients", json={"label": why, **ask}
            )
            assert status == 400, (why, body)
            assert "sender token" in body["error"]["message"], (why, body)

        status, body = await gw.as_owner(
            "POST",
            "/api/external-access/clients",
            json={
                "label": "Build server",
                "surfaces": ["webhook"],
                "scope": {"trigger": automation.removeprefix("store:")},
            },
        )
        assert status == 200, body
        assert body["webhook"]["url"] == gw.url(f"/api/triggers/{automation}/fire")


@pytest.mark.asyncio
async def test_deleting_the_automation_ends_its_sender_tokens(tmp_path, monkeypatch):
    """An automation made again under the same id (one that never ran, a restore, an import) gets
    the same address, so a token made for the one that was deleted must not fire the new one."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        made = await _make_sender(gw, automation)

        status, body = await gw.as_owner("DELETE", f"/api/triggers/{automation}")
        assert status == 200, body
        assert _automation(gw.home) == automation
        status, body = await _post(
            gw.url(f"/api/triggers/{automation}/fire"),
            headers={"Authorization": f"Bearer {made['token']}"},
        )

        assert status == 401, body
        assert "revoked" in body["error"]["message"]
        status, view = await gw.as_owner("GET", "/api/external-access")
        assert made["client_id"] not in [c["client_id"] for c in view["clients"]]


@pytest.mark.asyncio
async def test_a_sender_token_switched_off_in_settings_fires_nothing_until_switched_back_on(
    tmp_path, monkeypatch
):
    """Settings' switch is a sender token's own: off, its program is refused in the words that
    say where it goes back on, and the audit row says it was switched off; on, it fires again."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home)
        made = await _make_sender(gw, automation)
        switch = f"/api/external-access/clients/{made['client_id']}/disabled"
        sent = {"Authorization": f"Bearer {made['token']}"}
        fire = gw.url(f"/api/triggers/{automation}/fire")

        status, body = await gw.as_owner("POST", switch, json={"disabled": True})
        assert status == 200, body
        status, refused = await _post(fire, headers=sent)
        assert status == 401, refused
        assert "Settings → External Access" in refused["error"]["message"]
        (row,) = [r for r in _audit(gw.home) if r["surface"] == "webhook"]
        assert "disabled" in row["refused_reason"]

        status, body = await gw.as_owner("POST", switch, json={"disabled": False})
        assert status == 200, body
        status, accepted = await _post(fire, headers=sent)

        assert status == 202, accepted
        (run,) = await _runs(gw, automation)
        assert run["status"] == "success", run


def test_an_ended_token_is_named_for_what_it_was_made_for_wherever_it_is_sent(
    tmp_path, monkeypatch
):
    """🔴 Red before: a revoked sender token was told "Register the client again", which no owner
    can do for an automation; a revoked token is named for what it was made for, at whichever door
    its holder sends it."""
    from personalclaw.inbound import clients, tokens

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    sender, sender_token = clients.create_client(
        "Build server", surfaces=["webhook"], scope={"trigger": "store:webhook:build-finished"}
    )
    ide, ide_token = clients.create_client("Editor", surfaces=["mcp"])
    assert clients.revoke_client(sender.client_id) and clients.revoke_client(ide.client_id)

    for door in ("webhook", "mcp"):
        said = tokens.ending(door, sender_token)
        assert said is not None and said.sentence.startswith(
            "The sender token “Build server” was revoked"
        ), (door, said)
        assert "on its page in PersonalClaw" in said.sentence
        said = tokens.ending(door, ide_token)
        assert said is not None and said.sentence.startswith(
            "The token for the “Editor” client was revoked"
        ), (door, said)


@pytest.mark.asyncio
async def test_an_automation_written_elsewhere_gets_no_sender_token_and_fires_nothing_here(
    tmp_path, monkeypatch
):
    """🔴 Red before: the fire asked nothing about who wrote the automation, so one another
    machine's owner wrote, which this one only shows and never runs, ran here for a client pinned
    to it. No sender token is made for it, and a client pinned to it fires nothing."""
    from personalclaw.inbound import clients as clients_mod

    monkeypatch.setattr("personalclaw.identity.current_username", lambda: "noor")
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        TriggerStore(base_dir=gw.home).upsert(
            Trigger(
                id="webhook:theirs",
                name="Theirs",
                kind="webhook",
                created_by="user",
                author="sam",
                workflow={"inline": dict(NOTIFY)},
                capabilities={"providers": ["notify"]},
            )
        )
        automation = "store:webhook:theirs"

        status, body = await gw.as_owner(
            "POST",
            "/api/external-access/clients",
            json={
                "label": "Sam's build",
                "surfaces": ["webhook"],
                "scope": {"trigger": automation},
            },
        )
        assert status == 400, body
        assert "someone else's" in body["error"]["message"]
        _client, token = clients_mod.create_client(
            "Sam's build", surfaces=["webhook"], scope={"trigger": automation}
        )
        status, body = await _post(
            gw.url(f"/api/triggers/{automation}/fire"), headers={"Authorization": f"Bearer {token}"}
        )

        assert status == 404, body
        assert await _runs(gw, automation, at_least=0) == []


# ── the attended run is the owner's, as it was ──


@pytest.mark.asyncio
async def test_run_now_on_the_automations_page_runs_it_as_before(tmp_path, monkeypatch):
    """Positive control, the same before and after: the owner's Run now is attended, runs the
    action and records it, and anything but the owner's sign-in is refused there. On an automation
    saved as one was before (its `token_ref` included), which runs by hand as it did."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        automation = _automation(gw.home, spec={"token_ref": "{{secret:WH_TOKEN}}"})

        status, body = await gw.as_owner("POST", f"/api/triggers/{automation}/run")

        assert status == 200, body
        assert body["ok"] is True, body
        (run,) = await _runs(gw, automation)
        assert run["status"] == "success", run
        status, body = await _post(
            gw.url(f"/api/triggers/{automation}/run"),
            headers={"Authorization": "Bearer not-a-sign-in"},
        )
        assert status == 403, body


def test_a_webhook_automation_needs_no_spec_and_token_ref_is_not_one_of_its_fields():
    """🔴 Red before: a webhook automation was refused without a `token_ref`, which the fire never
    read; its sender tokens are what admit a caller."""
    from personalclaw.triggers.models import validate_spec

    assert validate_spec("webhook", {}) == []
    (issue,) = validate_spec("webhook", {"token_ref": "{{secret:WH_TOKEN}}"})
    assert issue.path == "spec.token_ref" and issue.severity != "error"


def test_an_automation_saved_with_a_token_ref_loses_it_at_the_next_start(tmp_path):
    """🔴 Red before: the field nothing read stayed in `triggers.json`, the token itself in it when
    that is what its author typed. The next start takes it out and leaves the rest of the row as
    it was; the start after finds nothing to take."""
    from personalclaw.triggers.boot_migrate import migrate_and_arm

    _automation(tmp_path, spec={"token_ref": "a-token-typed-in-before"})
    _automation(tmp_path, "deploy-finished", spec={"token_ref": "{{secret:WH_TOKEN}}"})
    before = {r.trigger.id: r.trigger.to_dict() for r in TriggerStore(base_dir=tmp_path).load()}

    migrate_and_arm(tmp_path)
    migrate_and_arm(tmp_path)

    assert "a-token-typed-in-before" not in (tmp_path / "triggers.json").read_text()
    after = TriggerStore(base_dir=tmp_path).load()
    assert sorted(r.trigger.id for r in after) == sorted(before)
    for row in after:
        assert row.ok and not row.warnings, row.issues
        assert row.trigger.to_dict() == {**before[row.trigger.id], "spec": {}}
