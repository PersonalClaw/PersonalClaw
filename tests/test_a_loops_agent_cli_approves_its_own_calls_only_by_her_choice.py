"""A loop's agent CLI approves its own calls only where its owner chose that, for that loop.

The choice (``agent_cli_self_approval``) is off for every loop, offered only for an Unattended loop
on an agent CLI whose not-gateable residual PersonalClaw declares, turned on only with her yes,
audited whichever way it changes, shown on the loop, read fail-closed, and gone with the loop.
What it does to a turn is driven end to end in
``test_an_unattended_agent_cli_turn_asks_before_it_acts.py``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import agent_cli_self_approval as self_approval
from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.sel import sel

#: An agent CLI whose residual PersonalClaw declares, and one nobody measured.
MEASURED = "acp:claude-code"
UNMEASURED = "acp:example-cli"
ROUTE = "/api/loops/{id}/agent-cli-self-approval"


def _loop(*, provider: str = MEASURED, attended: bool = False) -> Loop:
    return store.create(
        Loop(
            id="",
            name="Pantry",
            kind="goal",
            task="Keep the pantry list up to date.",
            attended=attended,
            provider=provider,
        )
    )


class _State:
    def push_refresh(self, *kinds):
        pass


def _put(loop_id: str, body: dict) -> web.Response:
    from personalclaw.request_validation import RequestValidationError

    app = web.Application()
    app["state"] = _State()
    req = make_mocked_request("PUT", ROUTE.format(id=loop_id), match_info={"id": loop_id}, app=app)
    req["user"] = "dashboard"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    try:
        return asyncio.get_event_loop().run_until_complete(H.api_loop_agent_cli_self_approval(req))
    except RequestValidationError as exc:
        return exc.response


def _body(response: web.Response) -> dict:
    return json.loads(response.text)


def _changes() -> list[dict]:
    """The choice's audit rows, oldest first (``recent`` reads newest first)."""
    rows = [r for r in sel().recent(200) if r.get("operation") == "loop.agent_cli_self_approval"]
    return list(reversed(rows))


# ── off until she turns it on ─────────────────────────────────────────────────────────────────


def test_it_is_off_for_a_new_loop_and_shown_on_it():
    loop = _loop()
    assert self_approval.view(loop) == {
        "allowed": False,
        "available": True,
        "unavailable": "",
        "cli": "Claude Code",
    }
    assert H._loop_view(loop.id)["agent_cli_self_approval"]["allowed"] is False


def test_turning_it_on_asks_her_first_and_changes_nothing_until_she_says_yes():
    loop = _loop()
    asked = _put(loop.id, {"allowed": True})

    assert asked.status == 400
    error = _body(asked)["error"]
    assert error["code"] == "confirmation_required"
    assert error["detail"]["title"] == "Let Claude Code approve its own calls?"
    assert "Claude Code will approve its own tool calls in this loop" in error["detail"]["consent"]
    assert self_approval.for_loop(loop.id).allowed is False
    assert _changes() == []


def test_her_yes_turns_it_on_shows_it_and_audits_it():
    loop = _loop()
    answer = _put(loop.id, {"allowed": True, "confirm": True})

    assert answer.status == 200
    assert _body(answer)["agent_cli_self_approval"]["allowed"] is True
    assert H._loop_view(loop.id)["agent_cli_self_approval"]["allowed"] is True
    [row] = _changes()
    assert row["outcome"] == "enabled"
    assert f"loop={loop.id}" in row["resources"] and MEASURED in row["resources"]


def test_turning_it_off_needs_no_yes_and_is_audited_too():
    loop = _loop()
    assert _put(loop.id, {"allowed": True, "confirm": True}).status == 200

    answer = _put(loop.id, {"allowed": False})

    assert answer.status == 200
    assert self_approval.for_loop(loop.id).allowed is False
    assert [r["outcome"] for r in _changes()] == ["enabled", "disabled"]


def test_the_switch_must_say_which_way():
    loop = _loop()
    assert _put(loop.id, {}).status == 400
    assert _put(loop.id, {"allowed": "true", "confirm": True}).status == 400
    assert self_approval.for_loop(loop.id).allowed is False


# ── offered only where it can apply ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("provider", "attended", "why"),
    [
        ("", False, "It runs on PersonalClaw's own agent"),
        (MEASURED, True, "It is Attended"),
        (UNMEASURED, False, "PersonalClaw hasn't measured what Example Cli runs without asking"),
    ],
)
def test_a_loop_that_cannot_have_it_is_refused_and_told_why(provider, attended, why):
    loop = _loop(provider=provider, attended=attended)
    refused = _put(loop.id, {"allowed": True, "confirm": True})

    assert refused.status == 409
    error = _body(refused)["error"]
    assert error["code"] == "loop_agent_cli_self_approval_unavailable"
    assert error["message"].startswith(why)
    assert self_approval.for_loop(loop.id).allowed is False
    view = self_approval.view(loop)
    assert view["available"] is False and view["unavailable"].startswith(why)
    with pytest.raises(self_approval.NotOffered):
        self_approval.set_for_loop(loop, True, caller="dashboard")


def test_an_ended_loop_is_refused():
    loop = _loop()
    store.update_status(loop.id, LoopStatus.COMPLETE)
    refused = _put(loop.id, {"allowed": False})
    assert refused.status == 409
    assert _body(refused)["error"]["code"] == "loop_finished"


def test_an_app_can_never_set_it():
    from personalclaw.apps.permissions import OwnerOnly, route_authz

    assert isinstance(route_authz("PUT", ROUTE), OwnerOnly)


# ── what a turn reads ─────────────────────────────────────────────────────────────────────────


def test_a_turn_reads_her_choice_for_its_own_loop_and_its_own_cli():
    loop = _loop()
    self_approval.set_for_loop(loop, True, caller="dashboard")

    assert self_approval.chosen_for(f"loop-{loop.id}", MEASURED) is True
    assert self_approval.chosen_for(f"loop-plan-{loop.id}", MEASURED) is True
    # A turn on another CLI than the one she chose it for, and work that is not the loop's.
    assert self_approval.chosen_for(f"loop-{loop.id}", "acp:codex") is False
    assert self_approval.chosen_for("chat-1-pantry", MEASURED) is False
    assert self_approval.chosen_for("inbound:cli:pantry", MEASURED) is False


def test_a_loop_turned_attended_is_given_nothing_by_a_choice_it_kept():
    loop = _loop()
    self_approval.set_for_loop(loop, True, caller="dashboard")
    store.update_spec(loop.id, {"attended": True})

    assert self_approval.chosen_for(f"loop-{loop.id}", MEASURED) is False
    assert self_approval.view(store.get(loop.id))["allowed"] is False


def test_an_unreadable_store_allows_nothing(tmp_path):
    from personalclaw.providers.entity_routes import _entity_settings_path

    loop = _loop()
    self_approval.set_for_loop(loop, True, caller="dashboard")
    _entity_settings_path("agent_cli_self_approval").write_text("{ not json", encoding="utf-8")

    assert self_approval.load() == {}
    assert self_approval.chosen_for(f"loop-{loop.id}", MEASURED) is False


@pytest.mark.parametrize("stored", ["false", "maybe", 0, None, ["true"]])
def test_a_stored_value_that_does_not_spell_yes_allows_nothing(stored):
    from personalclaw.providers.entity_routes import _save_entity_settings

    loop = _loop()
    _save_entity_settings("agent_cli_self_approval", {"loops": {loop.id: {"allowed": stored}}})

    assert self_approval.for_loop(loop.id).allowed is False
    assert self_approval.chosen_for(f"loop-{loop.id}", MEASURED) is False


def test_the_stored_document_round_trips():
    loop = _loop()
    chosen = self_approval.set_for_loop(loop, True, caller="dashboard")

    assert chosen.changed_at
    stored = self_approval.to_dict(self_approval.load())
    assert stored == {"loops": {loop.id: {"allowed": True, "changed_at": chosen.changed_at}}}
    assert self_approval.Choice.from_dict(stored["loops"][loop.id]) == chosen


def test_deleting_the_loop_takes_her_choice_with_it():
    loop = _loop()
    self_approval.set_for_loop(loop, True, caller="dashboard")

    assert store.delete(loop.id)

    assert loop.id not in self_approval.load()
