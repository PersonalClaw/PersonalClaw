"""INU-7 — the C6 Proposal contract and its apply dispatcher.

What these pin, in the order they matter:

1. The apply case set is CLOSED — zero / two / unknown keys all raise, and every
   ``ApplyCase`` member has a dispatcher entry (no unmapped value reaches a default).
2. Each of the four cases routes to the EXISTING dispatcher, asserted by patching the
   dispatcher's own symbol and observing the call — not by re-testing what it does.
3. **A failed apply keeps the item PENDING with the error.** One test per case, because a
   proposal that silently vanished is worse than one that failed loudly.
4. Edit-then-approve replaces the payload apply receives; an edit on a non-editable
   proposal is refused rather than ignored.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw import proposals_contract as pc
from personalclaw.inbox import InboxItem, InboxStore, ItemStatus


def _item(apply: dict, *, editable: bool = False, status: str = ItemStatus.PENDING.value):
    payload = pc.Proposal(
        title="Do the thing",
        preview="details",
        provenance="learning",
        editable=editable,
        apply=apply,
    )
    item = _row("inbox-1", refs={pc.REFS_KEY: payload.to_dict()})
    item.status = status
    return item


def _row(item_id: str, *, refs: dict) -> InboxItem:
    return InboxItem(
        id=item_id,
        channel="learning",
        channel_name="learning",
        thread_ts=None,
        message="Do the thing",
        sender_id="learning",
        sender_name="learning",
        item_kind="proposal",
        refs=refs,
    )


def _store(item):
    store = InboxStore()
    store.add(item)
    return store


# --------------------------------------------------------------------------- closed set


def test_apply_case_set_is_closed_and_total():
    assert set(pc._DISPATCH) == set(pc.ApplyCase)
    assert {c.value for c in pc.ApplyCase} == {
        "action",
        "workflow",
        "skill_promotion",
        "app_callback",
    }


@pytest.mark.parametrize(
    "apply",
    [
        {},
        {"action": {}, "workflow": {}},
        {"nope": {}},
    ],
    ids=["empty", "two-cases", "unknown-case"],
)
def test_malformed_apply_refuses_rather_than_guessing(apply):
    with pytest.raises(pc.ProposalError):
        pc.Proposal(title="t", apply=apply).apply_case()


def test_unknown_case_never_falls_through_to_a_default():
    outcome = asyncio.run(pc.apply_proposal(pc.Proposal(title="t", apply={"ghost": {}})))
    assert outcome.ok is False
    assert "unknown apply case" in outcome.error


def test_round_trip_is_lossless_and_from_dict_is_tolerant():
    p = pc.Proposal(
        title="t",
        preview="diff body",
        preview_kind="diff",
        provenance="app:demo",
        expires_at="2030-01-01T00:00:00+00:00",
        editable=True,
        apply={"workflow": {"ref": "wf"}},
    )
    assert pc.Proposal.from_dict(p.to_dict()) == p
    loose = pc.Proposal.from_dict({"title": "t", "preview_kind": "bogus"})
    assert loose.preview_kind == "text" and loose.apply == {}


def test_expiry_blocks_apply_and_an_unparseable_stamp_does_not():
    item = _item({"workflow": {"ref": "wf"}})
    item.refs[pc.REFS_KEY]["expires_at"] = "2000-01-01T00:00:00+00:00"
    store = _store(item)
    outcome = asyncio.run(pc.apply_item(item, store=store))
    assert outcome.ok is False and outcome.error == "proposal expired"
    assert item.status == ItemStatus.PENDING.value
    assert pc.Proposal.from_dict({"title": "t", "expires_at": "not-a-date"}).is_expired() is False


# ------------------------------------------------------- each case → existing dispatcher


def test_action_case_routes_through_the_action_provider_registry(monkeypatch):
    from personalclaw.action_providers import base as action_base
    from personalclaw.action_providers import registry

    seen = {}

    class _Provider:
        async def execute(self, action_config, ctx, timeout=30):
            seen["config"] = action_config
            seen["event"] = ctx.event
            return action_base.ActionResult(success=True, stdout="ran")

    monkeypatch.setattr(registry, "get_action_provider", lambda name: _Provider())
    item = _item({"action": {"provider": "webhook", "config": {"url": "x"}}})
    outcome = asyncio.run(pc.apply_item(item, store=_store(item)))
    assert outcome.ok and outcome.case == "action"
    assert seen["config"] == {"url": "x"} and seen["event"] == "proposal_apply"
    assert item.status == ItemStatus.HANDLED.value
    assert item.refs[pc.RESULT_KEY]["result"]["output"] == "ran"


def test_an_active_kill_switch_refuses_an_action_apply(monkeypatch):
    """🔴 The kill switch, on the Approve path — asserted BEHAVIOURALLY, not by source text.

    `test_action_provider_chokepoints` only checks that `manual_refusal` APPEARS in this module,
    so a neutered gate (`if False and refusal:`) passes that rail while firing actions during an
    incident. This test is the one that fails: the provider must never be reached, and the item
    must stay PENDING carrying the reason — a refused approval is not a silently dropped one.
    """
    from personalclaw.action_providers import registry
    from personalclaw.triggers import tools as trigger_tools

    reached = {"executed": False}

    class _Provider:
        async def execute(self, action_config, ctx, timeout=30):  # pragma: no cover — must not run
            reached["executed"] = True
            raise AssertionError("the kill switch let an action provider execute")

    monkeypatch.setattr(registry, "get_action_provider", lambda name: _Provider())
    monkeypatch.setattr(trigger_tools, "manual_refusal", lambda: "incident active")

    item = _item({"action": {"provider": "webhook", "config": {"url": "x"}}})
    outcome = asyncio.run(pc.apply_item(item, store=_store(item)))

    assert reached["executed"] is False, "the provider ran despite the kill switch"
    assert not outcome.ok and "incident active" in (outcome.error or "")
    assert item.status == ItemStatus.PENDING.value, "a refused apply must stay PENDING"
    assert "incident active" in str(item.refs[pc.ERROR_KEY])


def test_workflow_case_routes_through_service_start_run(monkeypatch):
    from personalclaw.workflows import service

    calls = {}

    async def _start_run(**kwargs):
        calls.update(kwargs)
        return {"ok": True, "run_id": "run-9"}

    monkeypatch.setattr(service, "start_run", _start_run)
    item = _item({"workflow": {"ref": "nightly", "inputs": {"a": 1}}})
    outcome = asyncio.run(pc.apply_item(item, store=_store(item)))
    assert outcome.ok and outcome.result["run_id"] == "run-9"
    assert calls["name"] == "nightly" and calls["inputs"] == {"a": 1}
    # Idempotent per item: a double-click is a retry, not a second run.
    assert calls["idempotency_key"] == "proposal:inbox-1"


def test_workflow_case_refuses_an_inline_def_instead_of_pretending(monkeypatch):
    item = _item({"workflow": {"inline": {"nodes": []}}})
    outcome = asyncio.run(pc.apply_item(item, store=_store(item)))
    assert outcome.ok is False and "`ref`" in outcome.error
    assert item.status == ItemStatus.PENDING.value


def test_skill_promotion_case_routes_through_learning_proposals_accept(monkeypatch):
    from personalclaw.learning import proposals as learning_proposals

    calls = {}

    def _accept(pid, *, installer=None, actor="user"):
        calls["pid"] = pid
        calls["actor"] = actor
        calls["installer"] = installer
        return type("P", (), {"status": "accepted"})()

    monkeypatch.setattr(learning_proposals, "accept", _accept)
    installer = object()
    item = _item({"skill_promotion": {"pid": "lp-7"}})
    outcome = asyncio.run(pc.apply_item(item, store=_store(item), installer=installer))
    assert outcome.ok and outcome.result == {"pid": "lp-7", "status": "accepted"}
    assert calls["pid"] == "lp-7" and calls["actor"] == "user"
    assert calls["installer"] is installer


def test_app_callback_case_routes_through_the_app_route_proxy(monkeypatch):
    from personalclaw.tool_providers import app_routes

    calls = {}

    def _resolve(app_name, op, arguments):
        calls["resolve"] = (app_name, op, arguments)
        return "resolution"

    async def _call(resolution):
        calls["called"] = resolution
        return type("R", (), {"success": True, "output": "ok"})()

    monkeypatch.setattr(app_routes, "resolve_route", _resolve)
    monkeypatch.setattr(app_routes, "call_app_route", _call)
    item = _item({"app_callback": {"app": "demo", "route": "confirm", "arguments": {"id": 2}}})
    outcome = asyncio.run(pc.apply_item(item, store=_store(item)))
    assert outcome.ok and outcome.result["route"] == "confirm"
    assert calls["resolve"] == ("demo", "confirm", {"id": 2})
    assert calls["called"] == "resolution"


# ------------------------------------------------- a failed apply KEEPS the item PENDING


@pytest.mark.parametrize(
    "case,apply,patch",
    [
        (
            "action",
            {"action": {"provider": "webhook"}},
            ("personalclaw.action_providers.registry", "get_action_provider", lambda n: None),
        ),
        ("workflow", {"workflow": {"ref": "wf"}}, None),
        ("skill_promotion", {"skill_promotion": {"pid": "lp-1"}}, None),
        ("app_callback", {"app_callback": {"app": "demo", "route": "r"}}, None),
    ],
)
def test_failed_apply_keeps_the_item_pending_with_the_error(case, apply, patch, monkeypatch):
    """Every case's failure path. The status MUST NOT move and the error MUST be recorded."""
    if patch is not None:
        mod = __import__(patch[0], fromlist=["x"])
        monkeypatch.setattr(mod, patch[1], patch[2])
    if case == "workflow":
        from personalclaw.workflows import service

        async def _start_run(**kwargs):
            return {"ok": False, "error": "boom"}

        monkeypatch.setattr(service, "start_run", _start_run)
    if case == "skill_promotion":
        from personalclaw.learning import proposals as learning_proposals

        def _accept(pid, *, installer=None, actor="user"):
            raise learning_proposals.AcceptError("no proposal 'lp-1'")

        monkeypatch.setattr(learning_proposals, "accept", _accept)
    if case == "app_callback":
        from personalclaw.tool_providers import app_routes

        def _resolve(*a, **k):
            raise RuntimeError("route not declared")

        monkeypatch.setattr(app_routes, "resolve_route", _resolve)

    item = _item(apply)
    store = _store(item)
    outcome = asyncio.run(pc.apply_item(item, store=store))

    assert outcome.ok is False, f"{case}: a failing apply reported success"
    assert outcome.error, f"{case}: failure carried no error"
    assert item.status == ItemStatus.PENDING.value, f"{case}: failed apply moved the status"
    assert item.refs[pc.ERROR_KEY]["error"] == outcome.error
    assert pc.RESULT_KEY not in item.refs
    # Persisted, not just in memory: an error nobody wrote down is one nobody can act on.
    assert store.items["inbox-1"].refs[pc.ERROR_KEY]["error"] == outcome.error


def test_item_without_a_payload_is_refused():
    item = _row("x", refs={})
    assert asyncio.run(pc.apply_item(item)).error == "item carries no proposal payload"


# ------------------------------------------------------------------- edit-then-approve


def test_edit_then_approve_applies_the_EDITED_payload(monkeypatch):
    from personalclaw.workflows import service

    calls = {}

    async def _start_run(**kwargs):
        calls.update(kwargs)
        return {"ok": True, "run_id": "r1"}

    monkeypatch.setattr(service, "start_run", _start_run)
    item = _item({"workflow": {"ref": "original"}}, editable=True)
    edited = pc.Proposal.from_dict(item.refs[pc.REFS_KEY]).to_dict()
    edited["apply"] = {"workflow": {"ref": "edited", "inputs": {"n": 3}}}
    outcome = asyncio.run(pc.apply_item(item, store=_store(item), edited=edited))
    assert outcome.ok and calls["name"] == "edited" and calls["inputs"] == {"n": 3}
    # The row shows what was actually applied, not what was proposed.
    assert item.refs[pc.REFS_KEY]["apply"]["workflow"]["ref"] == "edited"


def test_edit_on_a_non_editable_proposal_is_refused_not_ignored():
    item = _item({"workflow": {"ref": "original"}}, editable=False)
    outcome = asyncio.run(pc.apply_item(item, store=_store(item), edited={"title": "x"}))
    assert outcome.ok is False and outcome.error == "this proposal is not editable"
    assert item.status == ItemStatus.PENDING.value
    assert item.refs[pc.REFS_KEY]["apply"] == {"workflow": {"ref": "original"}}


# ------------------------------------------------------------- T4.1 re-expressed as C6


def test_learning_queue_surfaces_the_c6_skill_promotion_payload(monkeypatch, tmp_path):
    """T4.1's skill path is now `apply.skill_promotion` — the first consumer of C6, not a
    bespoke wiring. The legacy ref stays so existing readers are untouched."""
    from personalclaw.learning import proposals as learning_proposals

    captured = {}

    def _emit(state, **kwargs):
        captured.update(kwargs)
        return "inbox-x"

    monkeypatch.setattr("personalclaw.inbox.emit_attention_item", _emit)
    prop = learning_proposals.Proposal(
        id="lp-42", kind="skill", title="Extract a skill", body="because"
    )
    learning_proposals._surface_in_inbox(prop)

    assert captured["kind"] == "proposal"
    assert captured["refs"]["learning_proposal"] == "lp-42"
    payload = pc.Proposal.from_dict(captured["refs"][pc.REFS_KEY])
    assert payload.apply_case() is pc.ApplyCase.SKILL_PROMOTION
    assert payload.payload() == {"pid": "lp-42"}
    assert payload.provenance == "learning"
