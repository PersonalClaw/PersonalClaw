"""An action is rated by what it declares it does, never by its name.

🔴 THE DEFECTS (measured on ``origin/main``):

* ``triggers.screen`` listed ``call-app-route`` as read-only, so a trigger driving an app route that
  writes or deletes fired with no grant, like one that only reads.
* A workflow plan rated an action node by its provider's NAME (``workflows.autonomy``): one ending
  in ``-retrieve``, ``-health`` or ``-gaps`` was a read and anything else a write. So an action
  named like a read skipped the confirmation a write gets, and an app route that deletes was
  asked about as a routine write, which unattended mode approves.

The contract now: an action provider declares its effect (``ActionProvider.effect``, the
``RiskLevel`` a tool declares): a read, a change (what an undeclared action is), or destructive.
``call-app-route`` declares the effect of the route it drives: a read only when the app declares
the route ``readOnly``, destructive for a ``DELETE``. The trigger fence lets a read fire without a
grant and holds every other call to one; a plan asks about each step by what it declares.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.apps import app_manager, manager


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.skills.loader as skloader

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(skloader, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")
    return tmp_path


@pytest.fixture
def notes_app(tmp_path) -> str:
    """An installed app declaring a route that reads, one that writes and one that deletes."""
    d = tmp_path / "src" / "notes"
    d.mkdir(parents=True)
    route = {"agentCallable": True}
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": "notes",
                "version": "1.0.0",
                "displayName": "Notes",
                "description": "declares a read, a write and a delete",
                "backend": {
                    "entryPoint": "backend/server.py",
                    "type": "python",
                    "routes": [
                        {
                            **route,
                            "op": "list_notes",
                            "method": "GET",
                            "path": "/notes",
                            "summary": "List notes.",
                            "readOnly": True,
                        },
                        {
                            **route,
                            "op": "add_note",
                            "method": "POST",
                            "path": "/notes",
                            "summary": "Add a note.",
                            "body": {"text": {"type": "string"}},
                        },
                        {
                            **route,
                            "op": "drop_note",
                            "method": "DELETE",
                            "path": "/notes/{id}",
                            "summary": "Delete a note.",
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    result = app_manager.install(d, confirm=True)
    assert result.ok, result.error
    return "notes"


def _route_action(app: str, op: str) -> dict[str, Any]:
    return {"provider": "call-app-route", "config": {"app": app, "op": op, "args": {}}}


def _trigger(action: dict[str, Any]) -> Any:
    return SimpleNamespace(id="t1", name="t1", workflow={"inline": action}, capabilities={})


# ── the trigger fence ──


@pytest.mark.parametrize(
    "op,needs_grant", [("list_notes", False), ("add_note", True), ("drop_note", True)]
)
def test_a_trigger_driving_an_app_route_needs_a_grant_unless_the_route_reads(
    notes_app, op, needs_grant
):
    from personalclaw.triggers.screen import capabilities_for_action, ungranted_providers

    trigger = _trigger(_route_action(notes_app, op))
    expected = ["call-app-route"] if needs_grant else []
    assert capabilities_for_action(trigger).get("providers", []) == expected
    assert ungranted_providers(trigger) == expected


@pytest.mark.parametrize("op,allowed", [("list_notes", True), ("add_note", False)])
def test_the_fence_at_fire_time_reads_the_same_declaration(notes_app, op, allowed):
    from personalclaw.triggers.firepath import FireContext, evaluate

    action = _route_action(notes_app, op)
    decision = asyncio.run(
        evaluate(
            FireContext(
                trigger_id="t1",
                requested={"providers": ["call-app-route"]},
                capabilities={},
                action_config=action["config"],
            )
        )
    )
    assert decision.allowed is allowed, decision
    if not allowed:
        assert decision.gate == "capability"


def test_a_route_nobody_declares_is_a_change(notes_app):
    from personalclaw.triggers.screen import capabilities_for_action

    trigger = _trigger(_route_action(notes_app, "no_such_op"))
    assert capabilities_for_action(trigger) == {"providers": ["call-app-route"]}


# ── a plan's confirmations ──


def _plan(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"root": {"kind": "sequence", "id": "root", "children": list(nodes)}}


def _step(node_id: str, provider: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": "action", "id": node_id, "config": {"provider": provider, "with": config or {}}}


@pytest.fixture
def named_like_a_read():
    """An installed app's action named like a knowledge read that declares nothing."""
    from personalclaw.action_providers.base import ActionProvider, ActionResult
    from personalclaw.action_providers.registry import _providers, register_action_provider

    class _Undeclared(ActionProvider):
        name = "notes-retrieve"  # type: ignore[assignment]
        display_name = "Retrieve and Tidy Notes"  # type: ignore[assignment]

        async def execute(self, action_config, ctx, timeout=30):  # pragma: no cover - not run
            return ActionResult(success=True)

    register_action_provider(_Undeclared())
    yield "notes-retrieve"
    _providers.pop("notes-retrieve", None)


def test_a_plan_asks_about_each_step_by_what_it_declares(notes_app, named_like_a_read):
    from personalclaw.workflows.autonomy import ConfirmationType, Mode, build_confirmations

    spec = _plan(
        _step("retrieve", "knowledge-retrieve", {"query": "x"}),
        _step("list", "call-app-route", {"app": notes_app, "op": "list_notes"}),
        _step("add", "call-app-route", {"app": notes_app, "op": "add_note"}),
        _step("drop", "call-app-route", {"app": notes_app, "op": "drop_note"}),
        _step("tidy", named_like_a_read),
    )
    asked = {r.node_id: r.confirmation_type for r in build_confirmations(spec, Mode.PER_STAGE)}
    assert asked == {
        # An app route that is not a declared read is still an outward write, as before.
        "add": ConfirmationType.OUTWARD,
        "drop": ConfirmationType.DESTRUCTIVE,
        "tidy": ConfirmationType.WRITE,
    }
    unattended = {r.node_id for r in build_confirmations(spec, Mode.UNATTENDED)}
    assert unattended == {"drop"}, "unattended mode approves everything short of destruction"


def test_the_read_providers_declare_a_read():
    from personalclaw.action_providers.registry import action_effect
    from personalclaw.tool_providers.base import RiskLevel

    reads = {
        "knowledge-retrieve",
        "knowledge-health",
        "knowledge-gaps",
        "artifact_inspect",
        "check-work",
        "selfqa-triage",
    }
    assert {name for name in reads if action_effect(name, {}) == RiskLevel.SAFE} == reads
    assert action_effect("bash", {"command": "ls"}) == RiskLevel.CAUTION
    assert action_effect("knowledge-persist", {}) == RiskLevel.CAUTION
    assert action_effect("no-such-action", {}) == RiskLevel.CAUTION
