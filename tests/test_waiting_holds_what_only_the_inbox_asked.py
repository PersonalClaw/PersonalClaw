"""What waits on your answer is in the waiting list, wherever it asked you.

`GET /api/waiting` listed a loop only while it had a question for you. A loop the supervisor cut
off when it stalled, and one that stopped at a block, wait on your steer just as much, and the
sidebar marked both as waiting, yet neither reached the list, its badge, the tray or the buddy.
And a steer to a stalled loop left it stalled: only a waiting or a blocked loop's worker was
started again.

The asks only the Inbox raised (a folder waiting for your trust, a held action, a run waiting
after you declined a step, an escalated run) are listed from the Inbox's own rows, and they keep
the Inbox's own rules: no app reads them, a row an app raised is never one of them, a key an app
chooses never reaches a row the gateway raised, and every door the list names as an answer is
answered by you alone.

Each case below is red on the code before the change, except the ones marked as guards.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_apps_cannot_post_into_your_chats import _gateway
from test_apps_cannot_run_code_or_bypass_approvals import _home, _install
from test_running_now import _held
from test_waiting_list import _read
from test_work_phase_classifier import CHAT, _World

from personalclaw import waiting

#: An answer as every item carries it, an empty one included (``waiting._answer``).
ANSWER_KEYS = {"method", "path", "takes", "body"}
#: An agent's credential, as its tools present it, for the test chat.
AGENT = {"X-Internal-Secret": "the gateway's", "X-Session-Key": f"dashboard:{CHAT}"}

#: A credential the display mask catches.
SECRET = "ghp_" + "A1b2C3d4E5" * 4
MASK = "[REDACTED"
APP = "probe-waiting"
#: The asks only the Inbox raised, as the list names them.
INBOX_KINDS = ("folder_trust", "held_action", "declined_wait", "escalated_run")


def _stopped_loops() -> dict[str, str]:
    """A loop the supervisor cut off at a stall, and one stopped at a block, each with a reason."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    stalled = loops.create(Loop(id="", name="Sort the inbox", kind="goal", task="sort"))
    loops.update_status(stalled.id, LoopStatus.RUNNING)
    loop_files.record_breaker_trip(stalled.id, 3, f"No new findings in 3 cycles near {SECRET}")
    loops.update_status(stalled.id, LoopStatus.STAGNANT)
    blocked = loops.create(Loop(id="", name="Ship the fix", kind="goal", task="fix"))
    loops.update_status(blocked.id, LoopStatus.RUNNING)
    loops.update_status(
        blocked.id, LoopStatus.BLOCKED, error_message=f"The tests need {SECRET} to run"
    )
    return {"stalled": stalled.id, "blocked": blocked.id}


@pytest.mark.asyncio
async def test_a_stalled_and_a_blocked_loop_wait_on_you_with_their_reason(tmp_path):
    w = _World(tmp_path, _held(asyncio.Event()))
    ids = _stopped_loops()

    status, body = await _read(w.state)

    assert status == 200, body
    items = {i["id"]: i for i in body["waiting"]}
    stalled = items[f"loop_question:{ids['stalled']}"]
    blocked = items[f"loop_question:{ids['blocked']}"]
    assert stalled["brief"]["status"] == "stagnant" and blocked["brief"]["status"] == "blocked"
    assert stalled["brief"]["why"].startswith("No new findings in 3 cycles")
    assert blocked["brief"]["why"].startswith("The tests need")
    assert SECRET not in json.dumps(body) and MASK in stalled["brief"]["why"]
    for item, ident in ((stalled, ids["stalled"]), (blocked, ids["blocked"])):
        assert item["consequence"] == "asks"
        assert item["answer"]["path"] == f"/api/loops/{ident}/nudge"
        assert item["answer"]["takes"] == ["text"]
        assert item["asked_at"], (item["id"], "asked when it stopped")


@pytest.mark.asyncio
async def test_a_steer_to_a_stalled_loop_starts_it_again_and_it_leaves_the_list(
    tmp_path, monkeypatch
):
    from personalclaw.loop import manager
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import LoopStatus

    w = _World(tmp_path, _held(asyncio.Event()))
    ids = _stopped_loops()
    started: list[str] = []

    async def start(_state: Any, _svc: Any, loop_id: str) -> Any:
        started.append(loop_id)
        return loops.update_status(loop_id, LoopStatus.RUNNING)

    monkeypatch.setattr(manager, "start", start)
    svc = SimpleNamespace(get_by_session=lambda _key: None)

    await manager.nudge(w.state, svc, ids["stalled"], "Try the archive folder too")

    assert started == [ids["stalled"]]
    _status, body = await _read(w.state)
    assert f"loop_question:{ids['stalled']}" not in {i["id"] for i in body["waiting"]}


@pytest.mark.asyncio
async def test_only_you_answer_a_stalled_or_a_blocked_loop(tmp_path):
    """The list's rule is that an agent or an app never answers an item: a nudge to a loop that
    waits on you, stalled or blocked, is refused to both, as an answer to its question is."""
    from personalclaw.dashboard.handlers import loop_routes

    routes = [("POST", "/api/loops/{id}/nudge", loop_routes.api_loop_nudge)]
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/waiting", "/api/loops"]})
        w = _World(tmp_path, _held(asyncio.Event()))
        ids = _stopped_loops()
        for caller, headers in ((APP, {}), ("", AGENT)):
            async with TestClient(TestServer(_gateway(w.state, caller, routes))) as http:
                for ident in ids.values():
                    resp = await http.post(
                        f"/api/loops/{ident}/nudge", json={"text": "go on"}, headers=headers
                    )
                    assert resp.status == 403, (caller or "agent", ident, await resp.text())
                    if not caller:
                        # The door's own answer rule, not a 403 from anything in front of it.
                        assert (await resp.json())["error"]["code"] == "approval_owner_only"
        _status, body = await _read(w.state)
    listed = {i["id"] for i in body["waiting"]}
    assert {f"loop_question:{i}" for i in ids.values()} <= listed, "something was answered"


def test_the_list_reads_a_stall_from_its_last_breaker_trip():
    """The watchdog writes the stall with no reason on the row: the list reads the reason the
    supervisor gave, its latest when it tripped more than once."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    loop = loops.create(Loop(id="", name="Watch prices", kind="goal", task="watch"))
    loops.update_status(loop.id, LoopStatus.RUNNING)
    loop_files.record_breaker_trip(loop.id, 2, "an older stall")
    loop_files.record_breaker_trip(loop.id, 5, "the latest stall")
    loops.update_status(loop.id, LoopStatus.STAGNANT)

    items, _unreadable = waiting.read(waiting.Snapshot())

    [item] = [i for i in items if i["id"] == f"loop_question:{loop.id}"]
    assert item["brief"]["why"] == "the latest stall"


# ── what only the Inbox asked ───────────────────────────────────────────────────────────────


def _live_inbox(state: Any, monkeypatch: Any) -> Any:
    """A running Inbox on *state*, as the gateway serves it, and the dashboard its raisers find."""
    from personalclaw.inbox import InboxState, InboxStore
    from personalclaw.inbox_providers import native_source

    store = InboxStore()
    store.load()
    held = InboxState()
    held.load()
    state._inbox_svc = SimpleNamespace(inbox=store, state=held)
    monkeypatch.setattr(native_source, "get_dashboard_state", lambda: state)
    return store


def _run(ident: str, **kw: Any) -> Any:
    return SimpleNamespace(
        id=ident,
        title=kw.get("title", ""),
        workflow_name=kw.get("workflow_name", "release"),
        loop_kind=kw.get("loop_kind", ""),
        origin=None,
        parent_run_id=None,
        spawned_by_node_id=None,
        error_message=kw.get("error_message", ""),
        attention=kw.get("attention", {}),
    )


def _raise_each(state: Any) -> None:
    """Each ask only the Inbox raised, by its own raiser, and a failed run that waits on nothing."""
    from personalclaw.guardrails import project_trust, rungs
    from personalclaw.workflows import attention
    from personalclaw.workflows.models import RunStatus

    project_trust._prompt_trust_vs_preview("/srv/garden", state)
    rungs.announce_withheld(
        rungs.RungRoute(route=rungs.ROUTE_ASK, key="email.send", rung=rungs.RUNG_ONE_TAP),
        title="Send the weekly update?",
        body=f"It would send the update signed with {SECRET}",
    )
    attention.raise_declined_wait(
        state, _run("run-declined", title="Nightly report"), cycle=2, said="you declined Send"
    )
    attention.announce_run_end(
        state,
        _run("run-escalated", title="Release", error_message="investigate escalated"),
        RunStatus.ESCALATED,
    )
    attention.announce_run_end(
        state, _run("run-failed", title="Backup", error_message="disk full"), RunStatus.FAILED
    )


def _by_kind(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["kind"]: i for i in body["waiting"]}


@pytest.mark.asyncio
async def test_each_ask_only_the_inbox_raised_is_listed(tmp_path, monkeypatch):
    w = _World(tmp_path, _held(asyncio.Event()))
    _live_inbox(w.state, monkeypatch)
    _raise_each(w.state)

    status, body = await _read(w.state)

    assert status == 200, body
    kinds = _by_kind(body)
    assert set(INBOX_KINDS) <= set(kinds), sorted(kinds)
    trust = kinds["folder_trust"]
    assert trust["consequence"] == "changes"
    assert trust["brief"]["dir"] == "/srv/garden"
    # The exact action its door takes (``api_project_trust`` requires ``trusted``).
    assert trust["answer"] == {
        "method": "POST",
        "path": "/api/guardrails/project-trust",
        "takes": ["trust"],
        "body": {"dir": "/srv/garden", "trusted": True},
    }
    for kind in ("held_action", "declined_wait", "escalated_run"):
        item = kinds[kind]
        assert item["consequence"] == "asks", kind
        assert item["answer"] == {"method": "", "path": "", "takes": [], "body": {}}, kind
        assert item["brief"]["title"], kind
    for item in (kinds[k] for k in INBOX_KINDS):
        row = item["brief"]["inbox_item"]
        assert item["route"] == f"#/inbox?open={row}"
        assert item["id"] == f"{item['kind']}:{row}"
    assert kinds["declined_wait"]["work"] == "workflow:run-declined"
    assert kinds["held_action"]["brief"]["action_type"] == "email.send"
    assert SECRET not in json.dumps(
        body
    ), "a row's words are listed masked, as the Inbox serves them"
    assert not any(
        "run-failed" in json.dumps(i["brief"]) for i in body["waiting"]
    ), "a failed run that waits on nothing is not waiting on you"


@pytest.mark.asyncio
async def test_a_loop_run_that_escalated_is_listed_as_one(tmp_path, monkeypatch):
    """A guard: a run started as a loop that escalated is an escalated run too, known by the
    ending its raiser files (``_announce_loop_end``), and its card opens the loop."""
    from personalclaw.workflows import attention
    from personalclaw.workflows.models import RunStatus

    w = _World(tmp_path, _held(asyncio.Event()))
    _live_inbox(w.state, monkeypatch)
    run = _run(
        "run-loop",
        title="Tidy the garden notes",
        loop_kind="goal",
        attention={"cause": "", "detail": "It could not reach the notes folder"},
    )
    attention.announce_run_end(w.state, run, RunStatus.ESCALATED)

    _status, body = await _read(w.state)

    [item] = [i for i in body["waiting"] if i["kind"] == "escalated_run"]
    assert item["brief"]["loop"] == "run-loop" and item["brief"]["loop_kind"] == "goal"
    assert item["work"] == "loop:run-loop"


@pytest.mark.asyncio
async def test_a_row_closed_in_the_inbox_leaves_the_list(tmp_path, monkeypatch):
    from personalclaw.inbox import set_item_status

    w = _World(tmp_path, _held(asyncio.Event()))
    store = _live_inbox(w.state, monkeypatch)
    _raise_each(w.state)
    rows = [r for r in store.items.values() if r.refs.get("action_type") == "email.send"]
    assert rows

    set_item_status(w.state, store, rows, "handled")

    _status, body = await _read(w.state)
    assert "held_action" not in _by_kind(body)
    assert "folder_trust" in _by_kind(body)


def test_raising_and_closing_each_rings_the_list(tmp_path, monkeypatch):
    """The tray and the buddy read the list again only when it rings, so each of these rows rings
    it as it is raised and as it closes, as every other source does."""
    from personalclaw.inbox import set_item_status

    w = _World(tmp_path, _held(asyncio.Event()))
    store = _live_inbox(w.state, monkeypatch)
    rings: list[Any] = []
    monkeypatch.setattr(waiting, "changed", lambda state: rings.append(state))

    _raise_each(w.state)
    raised = len(rings)
    listed = [r for r in store.items.values() if "run-failed" not in json.dumps(r.refs)]
    set_item_status(w.state, store, listed, "dismissed")

    assert raised >= len(INBOX_KINDS), raised
    assert len(rings) - raised >= 1, "closing them rang nothing"
    assert all(state is w.state for state in rings)


# ── a question a loop's task agent asks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_question_a_code_loops_task_agent_asks_is_listed_with_its_text(tmp_path):
    """An attended Code loop's task agent asks as its prompt tells it to: one ``{question, why}`` in
    the loop's own ``questions.json``, which stops the loop at ``needs_input`` (the watchdog's
    ``_handle_question``). It is listed as the loop's question, with its words, as the scheduler's
    merge question is, so the badge, the tray and Needs you see it without the loop's page open.
    (A field report of one such question missing from the list could not be reproduced from that
    loop's setup; this case keeps it listed.)"""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    w = _World(tmp_path, _held(asyncio.Event()))
    loop = loops.create(
        Loop(
            id="",
            name="Fix the ten picks",
            kind="code",
            task="fix them",
            attended=True,
            kind_config={"entry_stage": "ideation", "project_kind": "greenfield"},
        )
    )
    loops.update_status(loop.id, LoopStatus.RUNNING)
    loop_files.write_question(
        loop.id,
        "May the agents work in /srv/allowed, which the deny rule names?",
        why="Two of the tasks run there",
    )
    loops.update_status(loop.id, LoopStatus.NEEDS_INPUT)

    _status, body = await _read(w.state)

    [item] = [i for i in body["waiting"] if i["id"] == f"loop_question:{loop.id}"]
    assert (
        item["brief"]["question"]
        == "May the agents work in /srv/allowed, which the deny rule names?"
    )
    assert item["brief"]["why"] == "Two of the tasks run there"
    assert item["route"] == f"#/code/{loop.id}"
    assert item["answer"]["path"] == f"/api/loops/{loop.id}/nudge"


# ── who reads and answers what only the Inbox asked ─────────────────────────────────────────

#: An app that reads the list, may ask for your Inbox, and raises proposals of its own.
APP_MANIFEST = {
    "api": ["/api/waiting", "/api/inbox", "/api/inbox/proposals"],
    "proposals": [{"kind_suffix": "draft", "label": "Draft"}],
}


async def _propose(state: Any, app: str = APP, **body: Any) -> str:
    """*app*'s proposal, through its own door (``POST /api/inbox/proposals``): its row id."""
    from personalclaw.dashboard import handlers_inbox

    route = ("POST", "/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create)
    payload = {"kind_suffix": "draft", "apply": {"app_callback": {"route": "send"}}, **body}
    async with TestClient(TestServer(_gateway(state, app, [route]))) as http:
        resp = await http.post("/api/inbox/proposals", json=payload)
        assert resp.status == 201, await resp.text()
        return str((await resp.json())["id"])


@pytest.mark.asyncio
async def test_an_app_reads_none_of_the_asks_only_the_inbox_raised(tmp_path, monkeypatch):
    """Each of them is a row of your Inbox, which no app reads (``GET /api/inbox`` is yours alone):
    an app that reads the list reads every other item, and none of these, nor their rows' ids."""
    from personalclaw.dashboard import handlers_inbox

    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, APP_MANIFEST)
        w = _World(tmp_path, _held(asyncio.Event()))
        store = _live_inbox(w.state, monkeypatch)
        _raise_each(w.state)
        ids = _stopped_loops()
        _status, mine = await _read(w.state)
        status, theirs = await _read(w.state, APP)
        route = ("GET", "/api/inbox", handlers_inbox.api_inbox_list)
        async with TestClient(TestServer(_gateway(w.state, APP, [route]))) as http:
            inbox_status = (await http.get("/api/inbox")).status

    assert set(INBOX_KINDS) <= set(_by_kind(mine)), "you read all four"
    assert status == 200, theirs
    assert not set(INBOX_KINDS) & {i["kind"] for i in theirs["waiting"]}, theirs
    assert {f"loop_question:{i}" for i in ids.values()} <= {i["id"] for i in theirs["waiting"]}
    text = json.dumps(theirs)
    assert not [row for row in store.items if row in text], "a row's id reached the app"
    assert "/srv/garden" not in text and "email.send" not in text
    assert inbox_status == 403, "the Inbox itself stays yours alone"


@pytest.mark.asyncio
async def test_every_reader_reads_a_whole_answer_on_each_ask(tmp_path, monkeypatch):
    """An item's answer is ``{method, path, takes, body}`` whoever reads it, an ask answered in the
    Inbox included, so a read by anyone but you (``for_reader``) never fails on one: an agent
    whose chat holds a held action reads its chat's items, the held action among them."""
    from personalclaw.guardrails import rungs

    w = _World(tmp_path, _held(asyncio.Event()))
    _live_inbox(w.state, monkeypatch)
    _raise_each(w.state)
    rungs.announce_withheld(
        rungs.RungRoute(route=rungs.ROUTE_ASK, key="files.delete", rung=rungs.RUNG_ONE_TAP),
        title="Delete the old drafts?",
        refs={"session": f"dashboard:{CHAT}"},
    )

    _status, mine = await _read(w.state)
    status, theirs = await _read(w.state, "", **AGENT)

    assert all(set(i["answer"]) == ANSWER_KEYS for i in mine["waiting"]), mine
    assert status == 200, theirs
    [held] = [i for i in theirs["waiting"] if i["kind"] == "held_action"]
    assert held["session"] == CHAT and held["brief"]["action_type"] == "files.delete"
    assert held["answer"] == {"method": "", "path": "", "takes": [], "body": {}}


@pytest.mark.asyncio
async def test_a_row_an_app_raised_is_never_one_of_the_asks_the_inbox_raised(tmp_path, monkeypatch):
    """A kind is read from what only its raiser files, never from a key a caller chooses: an app's
    proposal filed under an escalated run's key is not listed as one, and the real one still is."""
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, APP_MANIFEST)
        w = _World(tmp_path, _held(asyncio.Event()))
        _live_inbox(w.state, monkeypatch)
        _raise_each(w.state)
        spoof = await _propose(
            w.state,
            title="Release escalated",
            preview="Open it to see why",
            dedup_key="workflow-run:run-spoof:escalated",
        )
        _status, body = await _read(w.state)

    escalated = [i for i in body["waiting"] if i["kind"] == "escalated_run"]
    assert [i["brief"].get("workflow") for i in escalated] == ["run-escalated"], escalated
    assert spoof not in json.dumps(body), "the app's proposal was listed as an ask"
    # A row an app raised is none of the four, whatever it files on its refs.
    core = {"workflow": "run-1", "ending": waiting.ESCALATED}
    assert waiting.inbox_kind(core, "needs_input", "workflows") == "escalated_run"
    assert waiting.inbox_kind(core, "needs_input", f"app:{APP}") == ""
    assert waiting.inbox_kind({**core, "app": APP}, "needs_input", "workflows") == ""


@pytest.mark.asyncio
async def test_only_you_resume_a_loop_that_waits_on_you(tmp_path):
    """A resume of a stalled, a blocked or a waiting loop answers it, as the nudge does: an agent's
    resume is refused by the door's own rule and the loop stays where it waits. This is hardening:
    the gateway's sign-in keeps the agent's credential off this route before it gets here."""
    from personalclaw.dashboard.handlers import loop_routes
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    routes = [("PATCH", "/api/loops/{id}", loop_routes.api_loop_action)]
    w = _World(tmp_path, _held(asyncio.Event()))
    ids = _stopped_loops()
    asking = loops.create(Loop(id="", name="Plan the garden", kind="goal", task="plan"))
    loops.update_status(asking.id, LoopStatus.RUNNING)
    loop_files.write_question(asking.id, "Which beds first?", why="Two are shaded")
    loops.update_status(asking.id, LoopStatus.NEEDS_INPUT)
    ids["asking"] = asking.id
    async with TestClient(TestServer(_gateway(w.state, "", routes))) as http:
        for ident in ids.values():
            resp = await http.patch(f"/api/loops/{ident}", json={"action": "resume"}, headers=AGENT)
            assert resp.status == 403, (ident, await resp.text())
            assert (await resp.json())["error"]["code"] == "approval_owner_only"
    statuses = {name: loops.get(ident).status for name, ident in ids.items()}
    assert statuses == {"stalled": "stagnant", "blocked": "blocked", "asking": "needs_input"}


@pytest.mark.asyncio
async def test_only_you_trust_a_folder_the_list_asks_about(tmp_path, monkeypatch):
    """The list names the folder's own door as a trust request's answer, and only you answer it:
    an agent's Trust is refused by the door's own rule and the folder stays in Preview, while your
    Trust, sent as the list gives it, trusts it. This is hardening: the gateway's sign-in keeps
    the agent's credential off this route before it gets here."""
    from personalclaw.dashboard.handlers import core
    from personalclaw.guardrails import project_trust

    routes = [("POST", "/api/guardrails/project-trust", core.api_project_trust)]
    w = _World(tmp_path, _held(asyncio.Event()))
    _live_inbox(w.state, monkeypatch)
    garden = project_trust.resolve_dir(str(tmp_path / "garden"))
    project_trust._prompt_trust_vs_preview(garden, w.state)
    _status, body = await _read(w.state)
    [ask] = [i for i in body["waiting"] if i["kind"] == "folder_trust"]
    answer = ask["answer"]

    async with TestClient(TestServer(_gateway(w.state, "", routes))) as http:
        refused = await http.post(answer["path"], json=answer["body"], headers=AGENT)
        refused_body = await refused.json()
        held = project_trust.project_decision(garden)
        taken = await http.post(answer["path"], json=answer["body"])
        taken_body = await taken.json()

    assert refused.status == 403, refused_body
    assert refused_body["error"]["code"] == "approval_owner_only"
    assert held != project_trust.DECISION_TRUSTED
    assert taken.status == 200, taken_body
    assert project_trust.project_decision(garden) == project_trust.DECISION_TRUSTED


@pytest.mark.asyncio
async def test_behind_the_gateways_sign_in_only_you_trust_a_folder_or_resume_a_loop(
    tmp_path, monkeypatch
):
    """Both doors behind every middleware the gateway runs, its sign-in included: the agent's
    credential neither trusts a folder nor resumes a stalled loop (the sign-in refuses it first,
    which is why the doors' own rule, pinned above behind the permission middleware alone, is
    hardening), and you, signed in, do both through the doors' own rule."""
    from signed_in_gateway import signed_in_gateway

    from personalclaw.guardrails import project_trust
    from personalclaw.loop import manager
    from personalclaw.loop import store as loops
    from personalclaw.loop import validation
    from personalclaw.loop.loop import LoopStatus
    from personalclaw.triggers import nudge

    started: list[str] = []

    async def start(_state: Any, _svc: Any, loop_id: str) -> Any:
        started.append(loop_id)
        return loops.update_status(loop_id, LoopStatus.RUNNING)

    async def ready(_loop: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(manager, "start", start)
    monkeypatch.setattr(validation, "worker_tools_blocker", ready)
    monkeypatch.setattr(nudge, "get_instance", lambda: SimpleNamespace())
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        garden = project_trust.resolve_dir(str(tmp_path / "garden"))
        stalled = _stopped_loops()["stalled"]
        trust = {"dir": garden, "trusted": True}
        path, loop_path = "/api/guardrails/project-trust", f"/api/loops/{stalled}"
        resume = {"action": "resume"}
        agent_trust, _ = await gw.as_work_of("dashboard:any-chat", "POST", path, json=trust)
        agent_resume, _ = await gw.as_work_of("dashboard:any-chat", "PATCH", loop_path, json=resume)
        held = project_trust.project_decision(garden)
        still = loops.get(stalled).status
        owner_trust, trusted = await gw.as_owner("POST", path, json=trust)
        owner_resume, resumed = await gw.as_owner("PATCH", loop_path, json=resume)

    assert agent_trust in (401, 403) and agent_resume in (401, 403), (agent_trust, agent_resume)
    assert held != project_trust.DECISION_TRUSTED and still == "stagnant"
    assert owner_trust == 200 and trusted["trusted"] is True, trusted
    assert owner_resume == 200 and started == [stalled], resumed


# ── the list moves with the Inbox's every move ──────────────────────────────────────────────


def test_a_row_that_ages_out_or_comes_with_the_file_rings_the_list(tmp_path, monkeypatch):
    """Two writers move rows without a status write: the retention sweep, and a file a sync or a
    restore brought. Each rings the list when a row it moved is one the list lists."""
    from personalclaw.inbox import InboxStore

    w = _World(tmp_path, _held(asyncio.Event()))
    store = _live_inbox(w.state, monkeypatch)
    _raise_each(w.state)
    rings: list[Any] = []
    monkeypatch.setattr(waiting, "changed", lambda state: rings.append(state))

    other = InboxStore()
    other.load()
    [held] = [i for i, r in other.items.items() if r.refs.get("action_type") == "email.send"]
    other.items.pop(held)
    other.save()
    store.refresh()
    from_the_file = len(rings)

    [trust] = [r for r in store.items.values() if r.refs.get("guardrail") == "project_trust"]
    trust.created_at -= 91 * 86400
    store.cleanup_by_retention(90)

    assert held not in store.items
    assert from_the_file >= 1, "a row the file took away rang nothing"
    assert trust.id not in store.items
    assert len(rings) > from_the_file, "an aged-out ask rang nothing"


# ── a stall's reason, a block's age, and one set of what waits on you ──────────────────────────


def test_a_stall_is_read_from_its_ledger_only_when_the_ledger_moved(monkeypatch):
    """The list is read again on every ring, and a loop that ran for days before it stalled has a
    long ledger: its last trip is read once, and again only once a new line is written."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import journal
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    loop = loops.create(Loop(id="", name="Watch rates", kind="goal", task="watch"))
    loops.update_status(loop.id, LoopStatus.RUNNING)
    loop_files.record_breaker_trip(loop.id, 2, "the first stall")
    loops.update_status(loop.id, LoopStatus.STAGNANT)
    reads: list[str] = []
    real = journal.ledger

    def counted(loop_id: str, **kw: Any) -> list[dict[str, Any]]:
        reads.append(loop_id)
        return real(loop_id, **kw)

    monkeypatch.setattr(journal, "ledger", counted)

    def why() -> str:
        items, _unreadable = waiting.read(waiting.Snapshot())
        [item] = [i for i in items if i["id"] == f"loop_question:{loop.id}"]
        return str(item["brief"]["why"])

    assert why() == "the first stall" and why() == "the first stall"
    assert reads == [loop.id], reads
    loop_files.record_breaker_trip(loop.id, 4, "the second stall")
    assert why() == "the second stall"
    assert reads == [loop.id, loop.id], reads


def test_a_blocked_loop_is_asked_when_it_stopped():
    """A block files no trip: its age is from when the loop's status was written blocked."""
    import time
    from datetime import datetime

    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    loop = loops.create(Loop(id="", name="Ship it", kind="goal", task="ship"))
    loops.update_status(loop.id, LoopStatus.RUNNING)
    before = time.time()
    loops.update_status(loop.id, LoopStatus.BLOCKED, error_message="The tests need a key")
    after = time.time()

    items, _unreadable = waiting.read(waiting.Snapshot())

    [item] = [i for i in items if i["id"] == f"loop_question:{loop.id}"]
    assert item["asked_at"], item
    at = datetime.fromisoformat(str(item["asked_at"]).replace("Z", "+00:00")).timestamp()
    assert before - 1 <= at <= after + 1, (before, at, after)
    assert item["age_secs"] is not None


def test_one_set_says_which_loops_wait_on_you():
    """The list, the nudge's and the resume's answer rule and the steer's restart read one set,
    so none of them drifts from the others."""
    from personalclaw.dashboard.handlers import loop_routes
    from personalclaw.loop import loop, manager

    assert loop.WAITS_ON_YOU == {"needs_input", "stagnant", "blocked"}
    assert loop_routes.WAITS_ON_YOU is loop.WAITS_ON_YOU
    assert manager.WAITS_ON_YOU is loop.WAITS_ON_YOU


# ── a dedup key is its raiser's own ─────────────────────────────────────────────────────────

#: A second app, with the same manifest as the first.
OTHER_APP = "probe-waiting-2"


def _notified(state: Any, monkeypatch: Any) -> list[str]:
    """The Inbox row each notification on *state* is for, as they fire."""
    rows: list[str] = []
    monkeypatch.setattr(
        state,
        "notify",
        lambda *_a, meta=None, **_kw: rows.append(str((meta or {}).get("inbox_item"))),
    )
    return rows


@pytest.mark.asyncio
async def test_a_key_an_app_sends_never_swallows_a_row_the_gateway_raises(tmp_path, monkeypatch):
    """A key dedupes only its own raiser's rows: an app's proposal filed under a folder's trust key,
    by its door or as a row filed before keys were its raiser's own, leaves the trust request the
    gateway then raises a row of its own, and its notification fires; sent under the key of a
    request the gateway already raised, it is a row of its own and hands the app nothing of it."""
    from personalclaw.guardrails import project_trust
    from personalclaw.inbox import InboxItem

    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, APP_MANIFEST)
        w = _World(tmp_path, _held(asyncio.Event()))
        store = _live_inbox(w.state, monkeypatch)
        notes = _notified(w.state, monkeypatch)
        garden = project_trust.resolve_dir(str(tmp_path / "garden"))
        shed = project_trust.resolve_dir(str(tmp_path / "shed"))
        pond = project_trust.resolve_dir(str(tmp_path / "pond"))
        project_trust._prompt_trust_vs_preview(pond, w.state)
        after = await _propose(w.state, APP, title="Trust it?", dedup_key=f"project_trust:{pond}")
        by_door = await _propose(
            w.state, APP, title="Trust it?", dedup_key=f"project_trust:{garden}"
        )
        # A row an app filed before a key was its raiser's own: the key stored as the app sent it.
        before = InboxItem(
            id="proposal_before_1",
            channel=f"app:{APP}",
            channel_name=f"app:{APP}",
            thread_ts=None,
            message="Trust the shed?",
            sender_id=f"app:{APP}",
            sender_name=f"app:{APP}",
            created_at=1.0,
            source=f"app:{APP}",
            item_kind="proposal",
            refs={"app": APP, "dedup_key": f"project_trust:{shed}"},
        )
        store.add(before)
        project_trust._prompt_trust_vs_preview(garden, w.state)
        project_trust._prompt_trust_vs_preview(shed, w.state)
        _status, body = await _read(w.state)

    trusts = {
        i["brief"]["dir"]: i["brief"]["inbox_item"]
        for i in body["waiting"]
        if i["kind"] == "folder_trust"
    }
    assert set(trusts) == {garden, shed, pond}, "a trust request was deduped into an app's row"
    assert after != trusts[pond], "the app was handed the gateway's row"
    assert trusts[garden] != by_door and trusts[shed] != before.id
    assert {trusts[garden], trusts[shed]} <= set(
        notes
    ), "a trust request's notification never fired"
    assert store.items[by_door].status in ("pending", "seen") and before.id in store.items


@pytest.mark.asyncio
async def test_an_apps_repeat_dedupes_into_its_own_row(tmp_path, monkeypatch):
    """A guard: an app's proposal sent twice under one key is one row, with one notification."""
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, APP_MANIFEST)
        w = _World(tmp_path, _held(asyncio.Event()))
        store = _live_inbox(w.state, monkeypatch)
        notes = _notified(w.state, monkeypatch)
        first = await _propose(w.state, APP, title="Send the reply?", dedup_key="reply:42")
        again = await _propose(w.state, APP, title="Send the reply?", dedup_key="reply:42")

    assert again == first
    assert [r for r in store.items.values() if r.source == f"app:{APP}"] == [store.items[first]]
    assert notes.count(first) == 1


@pytest.mark.asyncio
async def test_two_apps_that_send_one_key_keep_two_rows(tmp_path, monkeypatch):
    """One app's key never dedupes another's row: each is its own raiser."""
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, APP_MANIFEST)
        _install(tmp_path / "home", OTHER_APP, APP_MANIFEST)
        w = _World(tmp_path, _held(asyncio.Event()))
        store = _live_inbox(w.state, monkeypatch)
        notes = _notified(w.state, monkeypatch)
        mine = await _propose(w.state, APP, title="Send the reply?", dedup_key="reply:42")
        theirs = await _propose(w.state, OTHER_APP, title="Send the reply?", dedup_key="reply:42")

    assert mine != theirs
    assert {store.items[mine].source, store.items[theirs].source} == {
        f"app:{APP}",
        f"app:{OTHER_APP}",
    }
    assert {mine, theirs} <= set(notes)
