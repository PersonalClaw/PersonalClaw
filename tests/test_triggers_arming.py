"""G40 — a blocking lifecycle hook must SAY whether it is armed.

Measured defect (AAP-3's sweep, 2026-08-17). Six lifecycle hooks were created through
``POST /api/triggers``, the ``PreToolUse`` one exiting 2 — the documented block signal. Driven
twice against the same hooks:

* **Unbound** (no agent profile referenced the hook ids): the hook fired **three times** and the
  write **still landed** — the target file contained the written content.
* **Bound** (the ids on the session agent's ``triggers``): the tool line read
  ``(hook blocked: aap3-pretool:hook denied)`` and the file was never created.

The mechanism is by design — ``chat_runner._fire`` is agent-scoped
(``ScriptHookStore.fire_for_ids``), while ``hooks.fire_tool_hooks`` →
``ScriptHookStore.fire`` is informational and its docstring says results "cannot block
execution". The defect was that a user who created a blocking hook got a silently inert safety
control: ``used_by: []`` was the only clue, and ``run_count: 3`` argued against it.

These tests pin the read model that closes it: ``GET /api/triggers`` reports ``enforcement`` per
lifecycle row, derived from the SAME agent-profile binding the firing path reads. Deliberately
driven through the handler with the REAL ``_used_by_index`` over a real ``config.json`` — patching
that index would exercise the serializer while leaving the config→binding→enforcement chain
(the thing that was wrong) unasserted.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.hooks import (
    BLOCKING_EVENTS,
    ENFORCEMENT_ADVISORY,
    ENFORCEMENT_ENFORCING,
    ENFORCEMENT_NOT_ENFORCING,
    ENFORCEMENT_STATES,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_STOP,
    ScriptHookStore,
    hook_enforcement,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home + workspace. Both are set: an isolated home alone does not confine the
    workspace, and this fixture writes a real ``config.json``."""
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def store(home, monkeypatch):
    hook_store = ScriptHookStore(config_dir=home)
    monkeypatch.setattr(T, "_hook_store", lambda _s: hook_store)
    return hook_store


def _bind(home, hook_ids: list[str]) -> None:
    """Write the agent profile the FIRING path reads (``AgentProfile.triggers``)."""
    (home / "config.json").write_text(
        json.dumps({"default_agent": "coder", "agents": {"coder": {"triggers": hook_ids}}}),
        encoding="utf-8",
    )


def _rows(store) -> dict[str, dict]:
    """``GET /api/triggers?type=lifecycle`` → {hook name: row}."""
    app = web.Application()
    app["state"] = object()
    req = make_mocked_request("GET", "/api/triggers?type=lifecycle", app=app)
    req["user"] = "tester"
    resp = asyncio.run(T.api_triggers(req))
    body = json.loads(resp.body.decode())
    return {t["name"]: t for t in body["triggers"] if t["kind"] == "lifecycle"}


def _seed(store) -> dict[str, str]:
    """The sweep's shape: one blocking hook and one non-blocking one. Returns name → hook id."""
    ids = {}
    for name, event in (("pretool", HOOK_EVENT_PRE_TOOL_USE), ("onstop", HOOK_EVENT_STOP)):
        hook = store.create(
            {
                "name": name,
                "event": event,
                "provider": "bash",
                "provider_config": {"command": "exit 2"},
            }
        )
        ids[name] = hook.id
    return ids


# ── the rail ──


def test_unbound_blocking_hook_is_reported_not_enforcing(store, home):
    """🔴 The measured state: a PreToolUse hook no agent references cannot block, so the row must
    say so rather than leaving `used_by: []` for the user to interpret."""
    ids = _seed(store)
    assert len(ids) == 2, "vacuity: the fixture must create hooks for this to measure anything"
    _bind(home, [])  # a real config with a real agent that references nothing

    rows = _rows(store)
    assert len(rows) == 2, "vacuity: the handler must return the seeded rows"
    assert rows["pretool"]["blocking"] is True
    assert rows["pretool"]["enforcement"] == ENFORCEMENT_NOT_ENFORCING
    assert rows["pretool"]["used_by"] == []


def test_bound_blocking_hook_is_reported_enforcing(store, home):
    """The other half of the same measurement — binding the id to the agent profile is what makes
    the hook able to block, so it is what flips the reported state."""
    ids = _seed(store)
    _bind(home, [ids["pretool"]])

    rows = _rows(store)
    assert rows["pretool"]["used_by"] == ["coder"], "the real used_by index must see the profile"
    assert rows["pretool"]["enforcement"] == ENFORCEMENT_ENFORCING


def test_the_bound_and_unbound_states_are_genuinely_different(store, home):
    """Vacuity floor. A pass where both cases report the same value — or where both report an
    "unknown"/absent field — would satisfy the two tests above one at a time while telling a user
    nothing. Assert the difference itself, over one seeded hook, in one process."""
    ids = _seed(store)
    _bind(home, [])
    unbound = _rows(store)["pretool"]["enforcement"]
    _bind(home, [ids["pretool"]])
    bound = _rows(store)["pretool"]["enforcement"]

    assert unbound != bound, "the armed/unarmed distinction must be observable"
    assert {unbound, bound} == {ENFORCEMENT_NOT_ENFORCING, ENFORCEMENT_ENFORCING}
    assert "unknown" not in {unbound, bound}
    assert unbound in ENFORCEMENT_STATES and bound in ENFORCEMENT_STATES


def test_a_non_blocking_hook_is_advisory_not_unarmed(store, home):
    """An unbound `Stop` hook is not a disarmed safety control — it has no blocking seam at all.
    Reporting it `not_enforcing` would cry wolf on 14 of the 15 events."""
    _seed(store)
    _bind(home, [])
    row = _rows(store)["onstop"]
    assert row["blocking"] is False
    assert row["enforcement"] == ENFORCEMENT_ADVISORY


def test_disabling_a_bound_blocking_hook_stops_it_enforcing(store, home):
    """A disabled hook never fires, so it never blocks. The field is the EFFECTIVE state — it must
    not read `enforcing` for a control that is switched off."""
    ids = _seed(store)
    _bind(home, [ids["pretool"]])
    store.toggle(ids["pretool"])

    row = _rows(store)["pretool"]
    assert row["enabled"] is False
    assert row["enforcement"] == ENFORCEMENT_NOT_ENFORCING


def test_a_freshly_created_blocking_hook_reports_not_enforcing(store, home):
    """POST /api/triggers answers with `_serialize_lifecycle(hook, [])`, so the creation response
    itself is the first place the user is told. A blocking hook cannot be bound at create time."""
    app = web.Application()
    app["state"] = object()
    body = {
        "trigger_type": "lifecycle",
        "name": "auditor",
        "event": HOOK_EVENT_PRE_TOOL_USE,
        "action": {"provider": "bash", "config": {"command": "exit 2"}},
    }
    req = make_mocked_request("POST", "/api/triggers", app=app)
    req["user"] = "tester"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    resp = asyncio.run(T.api_trigger_create(req))
    created = json.loads(resp.body.decode())["trigger"]
    assert created["blocking"] is True
    assert created["enforcement"] == ENFORCEMENT_NOT_ENFORCING


# ── the derivation ──


def test_blocking_events_is_derived_and_non_empty():
    """Vacuity: `BLOCKING_EVENTS` comes from the catalog's `blocking` flag. An empty set would make
    every row `advisory` and this whole rail vacuously green — the exact way `DORMANT_EVENTS`
    silently retired the "Never fires" chip."""
    assert BLOCKING_EVENTS, "no blocking event in the catalog — every check above would be vacuous"
    assert HOOK_EVENT_PRE_TOOL_USE in BLOCKING_EVENTS
    assert HOOK_EVENT_STOP not in BLOCKING_EVENTS


def test_enforcement_never_claims_enforcing_without_a_binding():
    """The secure default, at the unit. No combination without `bound` may return `enforcing`."""
    for enabled in (True, False):
        assert (
            hook_enforcement(HOOK_EVENT_PRE_TOOL_USE, enabled=enabled, bound=False)
            == ENFORCEMENT_NOT_ENFORCING
        )
    assert (
        hook_enforcement(HOOK_EVENT_PRE_TOOL_USE, enabled=True, bound=True) == ENFORCEMENT_ENFORCING
    )
