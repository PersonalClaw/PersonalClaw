"""A lifecycle hook held because its action asks first is a question you can answer.

🔴 The defect (Backlog: "A lifecycle hook whose action asks first is held with nothing to
answer"). A lifecycle trigger (Settings → Hooks) whose action asks first (the `one_tap` rung) was
held when its event fired, which is the safe half: nothing ran. But the hold filed a notice,
"<hook> is waiting for you", that registered no question, so the Inbox offered nothing that would
run the action for the event it held.

Now, as a stored trigger's held fire is (`test_a_held_fire_asks_a_question_you_can_answer`), the
hold is the hook's question: one Inbox row whose Approve runs the hook's action once for the event
it held, as your run, and whose Deny records that you declined. A check made before a tool call
cannot wait for an answer, so a gating fire's hold is explained, not asked, and the tool's
decision is what it was.

Driven through the real hook store's fire (`ScriptHookStore.fire`, and `fire_for_ids` for the
gating seam), the real rung ladder, the real Inbox, the real park store and the real answer route
(`trigger_runs.api_trigger_answer`), with an app's action installed the way the app loader
installs it, in a home of the test's own.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import types
from pathlib import Path

import guardrails_rung_routing_shared
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_guardrails_rung_routing import _inbox_rows
from test_guardrails_rung_routing import _install_app_action as _install

from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.hooks import ScriptHook, ScriptHookStore
from personalclaw.triggers import parks

HOOK = "h-acme"
TID = f"lifecycle:{HOOK}"
_clean_provider_registry = guardrails_rung_routing_shared._clean_provider_registry
_isolated_home = guardrails_rung_routing_shared._isolated_home


def _keep(home: Path, event: str) -> None:
    """One lifecycle trigger on *event* whose action you allowed, kept in *home*'s hooks.json."""
    hook = ScriptHook(
        id=HOOK,
        name="Acme after a write",
        event=event,
        provider="acme-do-thing",
        provider_config={"note": "x"},
        # The owner's yes (`triggers.grants`): the rung decides what an allowed action does next.
        capabilities={"providers": ["acme-do-thing"]},
    )
    (home / "hooks.json").write_text(json.dumps({"hooks": [hook.to_dict()]}))


@pytest.fixture()
def home(_isolated_home: Path) -> Path:
    """The home, with one lifecycle trigger on `PostToolUse`."""
    _keep(_isolated_home, "PostToolUse")
    return _isolated_home


@pytest.fixture()
def folder(tmp_path: Path) -> str:
    """The folder the chat the event is for works in."""
    work = tmp_path / "work"
    work.mkdir()
    return str(work)


def _store(home: Path) -> ScriptHookStore:
    return ScriptHookStore(home)


def _wrote(home: Path, folder: str, path: str = "notes.md") -> list:
    """One `PostToolUse` event: the chat's agent wrote *path*."""
    return asyncio.run(
        _store(home).fire(
            "PostToolUse",
            tool_name="Write",
            tool_input={"file_path": path},
            tool_response={"ok": True},
            cwd=folder,
        )
    )


def _answer(home: Path, token: str, answer: bool) -> web.Response:
    """The Inbox card's answer, addressed as the web client addresses it (`store:<id>`)."""
    app = web.Application()
    app["state"] = types.SimpleNamespace(
        push_refresh=lambda *k: None, _background_tasks=set(), _hook_store=_store(home)
    )
    path = f"/api/triggers/store:{TID}/answer"
    req = make_mocked_request("POST", path, match_info={"id": f"store:{TID}"}, app=app)
    req["user"] = "owner"

    async def _json():
        return {"resume_token": token, "answer": answer}

    req.json = _json  # type: ignore[assignment]
    return asyncio.run(trigger_runs.api_trigger_answer(req))


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


def _held(home: Path) -> list[dict]:
    """The hook's question rows still open: an answered one is kept, closed as handled."""
    return [
        r
        for r in _inbox_rows(home)
        if (r.get("refs") or {}).get("trigger_park") == TID
        and r.get("status") in ("pending", "seen")
    ]


def _status(home: Path) -> str:
    hook = _store(home).get(HOOK)
    assert hook is not None
    return hook.last_status


def test_the_hold_is_a_question_with_an_answer(home, folder):
    """🔴 Before: an `agent_request` row with no question registered, so nothing to answer."""
    action = _install(floor="one_tap", ceiling="one_tap")
    (result,) = _wrote(home, folder)
    assert action.calls == [] and "held for your approval" in result.error, "held: nothing ran"
    assert _status(home) == "held_for_rung"
    (row,) = _held(home)
    assert row["item_kind"] == "needs_input", row
    refs = row["refs"]
    assert (refs["action_type"], refs["rung"], refs["hook"], refs["provider"]) == (
        "app:acme.acme-do-thing",
        "one_tap",
        HOOK,
        "acme-do-thing",
    )
    assert refs["trigger"] == TID and refs["trigger_held"] == "acme-do-thing"
    question = row["message"].split("\n\n")[0]
    assert question.startswith("Run Acme after a write now? Its Acme Do Thing action"), question
    assert question.count("asks first") == 1, question
    park = parks.load(TID)
    assert park is not None and park.held == "acme-do-thing" and park.fires == 1
    assert refs.get("resume_token") == park.token
    assert not [r for r in _inbox_rows(home) if r.get("item_kind") == "agent_request"]


def test_approve_runs_the_action_once_for_the_event_it_held(home, folder):
    action = _install(floor="one_tap", ceiling="one_tap")
    _wrote(home, folder, "amber.md")
    token = parks.load(TID).token

    body = _body(_answer(home, token, True))

    assert (body["approved"], body["ok"], body["result"]) == (True, True, "ran"), body
    (ctx,) = action.calls
    assert ctx.trigger_id == TID and ctx.event == "PostToolUse"
    assert ctx.payload["tool_name"] == "Write" and "amber.md" in json.dumps(ctx.payload)
    assert ctx.payload["cwd"] == ctx.cwd and Path(ctx.cwd).samefile(folder), ctx.payload
    assert _status(home) == "ok", "the run's status is written as a fire's is"
    assert parks.load(TID) is None and _held(home) == []
    # A second answer finds nothing to run: one Approve, one run.
    assert _answer(home, token, True).status == 409
    assert len(action.calls) == 1


def test_deny_runs_nothing_and_says_you_declined(home, folder):
    action = _install(floor="one_tap", ceiling="one_tap")
    _wrote(home, folder)
    token = parks.load(TID).token

    body = _body(_answer(home, token, False))

    assert (body["approved"], body["result"]) == (False, "declined"), body
    assert action.calls == []
    assert _status(home) == "declined"
    assert parks.load(TID) is None and _held(home) == []


def test_a_second_event_before_an_answer_asks_nothing_new(home, folder):
    """One question per hook, however many events it holds: Approve runs the newest."""
    action = _install(floor="one_tap", ceiling="one_tap")
    _wrote(home, folder, "first.md")
    token = parks.load(TID).token
    _wrote(home, folder, "second.md")

    park = parks.load(TID)
    assert (park.token, park.fires) == (token, 2)
    assert len(_held(home)) == 1
    _answer(home, token, True)
    (ctx,) = action.calls
    assert "second.md" in json.dumps(ctx.payload) and "first.md" not in json.dumps(ctx.payload)


def test_an_approve_refused_by_an_incident_leaves_the_question_answerable(
    home, folder, monkeypatch
):
    """The refusals a Run button honours are read before the token is spent."""
    action = _install(floor="one_tap", ceiling="one_tap")
    _wrote(home, folder)
    token = parks.load(TID).token
    monkeypatch.setattr(
        "personalclaw.triggers.tools.manual_refusal", lambda: "incident mode is active"
    )

    body = _body(_answer(home, token, True))

    assert (body["ok"], body["refused"]) == (False, "incident mode is active"), body
    assert action.calls == []
    assert parks.load(TID).token == token and len(_held(home)) == 1


def test_a_lapsed_question_runs_nothing(home, folder):
    action = _install(floor="one_tap", ceiling="one_tap")
    _wrote(home, folder)
    park = parks.load(TID)
    park.created_at = time.time() - parks.answerable_secs() - 60
    parks._write(park)

    resp = _answer(home, park.token, True)

    assert resp.status == 409 and "lapsed" in _body(resp)["error"]["message"], _body(resp)
    assert action.calls == []


def test_a_gating_hooks_tool_decision_is_unchanged_and_its_hold_is_explained(home, folder):
    """A check made before a tool call cannot wait for an answer: nothing is asked of it."""
    action = _install(floor="one_tap", ceiling="one_tap")
    _keep(home, "PreToolUse")

    (result,) = asyncio.run(
        _store(home).fire_for_ids(
            "PreToolUse", {HOOK}, tool_name="Write", tool_input={"file_path": "x.md"}, cwd=folder
        )
    )

    assert action.calls == []
    # What the gating seam reads, as it was: the hold blocks nothing and lets nothing through.
    assert (result.exit_code, result.blocked) == (-1, False), result
    assert "held for your approval" in result.error
    assert parks.load(TID) is None and _held(home) == []
    (row,) = [r for r in _inbox_rows(home) if (r.get("refs") or {}).get("hook") == HOOK]
    assert row["message"].startswith("Acme after a write did not run"), row["message"]
    assert "cannot wait for an answer" in row["message"]


@pytest.mark.parametrize("since", ["gone", "a link into a credential folder"])
def test_an_approved_fire_whose_folder_changed_since_it_was_held_runs_nothing(
    home, folder, tmp_path, monkeypatch, since
):
    """Approve runs in the folder the fire was told, checked again as a fire checks it
    (`hooks.hook_folder`): a folder that has gone, or has since become a link to a credential
    folder, refuses the run."""
    action = _install(floor="one_tap", ceiling="one_tap")
    _wrote(home, folder)
    token = parks.load(TID).token
    os.replace(folder, tmp_path / "moved")
    if since != "gone":
        yours = tmp_path / "you"
        (yours / ".ssh").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(yours))
        os.symlink(yours / ".ssh", folder)

    body = _body(_answer(home, token, True))

    assert (body["approved"], body["ok"]) == (True, False), body
    assert body["result"].startswith("failed: ") and "not a folder it can run in" in body["result"]
    assert action.calls == []


def test_a_stored_trigger_never_takes_a_lifecycle_triggers_id(home):
    """The key a held hook's question is answered by is the hooks' alone."""
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=home)
    with pytest.raises(ValueError, match="lifecycle trigger's id"):
        store.upsert(Trigger(id=TID, name="Impostor", kind="clock", spec={"kind": "cron"}))
    assert store.get(TID) is None


def test_a_held_fire_of_another_trigger_under_a_lifecycle_id_asks_nothing(home):
    """The funnel every held fire is asked through (`parks.hold_fire`), from the gateway for a
    stored or an app-served row: one whose id is a lifecycle trigger's is not asked under it, so
    its Approve cannot run the hook that id names."""
    from types import SimpleNamespace

    served = SimpleNamespace(id=TID, name="An app's row")
    assert parks.hold_fire(served, {"x": 1}, provider="acme-do-thing", reason="it asks") is None
    assert parks.load(TID) is None and _held(home) == []
