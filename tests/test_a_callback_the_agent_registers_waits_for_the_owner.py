"""A callback the agent registers has its own file, and runs only once the owner allows it.

Measured on `main` (ddc21e05f) before any of this was written:

* **The chat's ``hook_register`` wrote into the lifecycle trigger store's file.** Its registrations
  were extra top-level keys in ``hooks.json``, so ``hook_id="hooks"`` replaced the owner's
  lifecycle triggers with an object, and every later load of the store raised ``AttributeError``
  — no lifecycle trigger loaded, or ran. Any other registration vanished the next time the store
  saved (creating, editing or toggling a lifecycle trigger).
* **A callback ran on nobody's yes.** ``POST /api/hooks/agent`` started an agent turn with its
  tools from the context the agent saved, as soon as an outside system called it back.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader
from personalclaw import mcp_core
from personalclaw.dashboard.handlers import hooks as hooks_mod
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.hooks import ScriptHookStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    from personalclaw.inbound import caps

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_core, "_resolve_session_key", lambda: "test", raising=False)
    monkeypatch.setattr(mcp_core, "_api_base", lambda: "http://127.0.0.1:10000")
    # The webhook's rate is held per caller across the process; every call here is one caller's.
    caps.reset_for_tests()
    return tmp_path


@pytest.fixture
def audit(monkeypatch):
    rows: list[dict] = []

    class _Sel:
        def log_api_access(self, **kwargs):
            rows.append(kwargs)

        def log_tool_invocation(self, **kwargs):
            rows.append(kwargs)

    monkeypatch.setattr("personalclaw.sel.sel", lambda: _Sel())
    return rows


def _register(hook_id: str, context: str) -> str:
    return mcp_core._call_tool_inner(
        "hook_register", {"hook_id": hook_id, "context_summary": context}
    )


def _owner_hook(home) -> None:
    ScriptHookStore(config_dir=home).create(
        {"name": "Log stops", "event": "Stop", "provider": "notify", "provider_config": {}}
    )


class _Runs:
    """Stands in for the turn a callback starts, and records what it was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def __call__(self, *args):
        self.calls.append(args)
        hooks_mod._hook_semaphore.release()


@pytest.fixture
def runs(monkeypatch):
    recorder = _Runs()
    monkeypatch.setattr(hooks_mod, "_run_hook_agent", recorder)
    monkeypatch.setattr(hooks_mod, "_hook_token_refusal", lambda _request: "")
    return recorder


def _call_back(session_key: str) -> web.Response:
    """An outside system posting its results from this machine, holding the webhook token."""
    app = web.Application()
    app["state"] = types.SimpleNamespace(_background_tasks=set())
    req = make_mocked_request("POST", "/api/hooks/agent", app=app).clone(remote="127.0.0.1")

    async def _json():
        return {"message": "CI passed", "sessionKey": session_key, "deliver": False}

    req.json = _json  # type: ignore[assignment]

    async def _go():
        resp = await hooks_mod.api_hooks_agent(req)
        # Let the fire-and-forget task the handler started run to its recorder.
        for task in list(app["state"]._background_tasks):
            await task
        return resp

    return asyncio.run(_go())


def _listed(kind: str = "callback") -> list[dict]:
    app = web.Application()
    app["state"] = types.SimpleNamespace()
    req = make_mocked_request("GET", f"/api/triggers?type={kind}", app=app)
    return json.loads(asyncio.run(T.api_triggers(req)).body)["triggers"]


def _toggle(trigger_id: str, **body) -> web.Response:
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None)
    req = make_mocked_request(
        "POST", f"/api/triggers/{trigger_id}/toggle", match_info={"id": trigger_id}, app=app
    )
    req["user"] = "owner"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return asyncio.run(T.api_trigger_toggle(req))


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body)


# ── 🔴 one owner, one path ──


def test_a_registration_named_hooks_leaves_the_owners_lifecycle_triggers_alone(home):
    """🔴 Red on main: the registration replaced the list with an object, and the store's next
    load raised ``AttributeError``."""
    _owner_hook(home)
    before = (home / "hooks.json").read_text(encoding="utf-8")

    _register("hooks", "watch the deploy")

    assert (home / "hooks.json").read_text(encoding="utf-8") == before
    assert [h.name for h in ScriptHookStore(config_dir=home).list_all()] == ["Log stops"]


def test_a_registration_survives_the_lifecycle_store_saving(home):
    """🔴 Red on main: creating a lifecycle trigger saved ``{"hooks": [...]}`` alone, and the
    registration was gone."""
    _register("review:pr-1", "check the PR's CI")

    _owner_hook(home)

    (row,) = _listed()
    assert row["raw_id"] == "review:pr-1"
    assert row["context_summary"] == "check the PR's CI"


def test_a_hooks_file_with_entries_that_are_not_triggers_still_loads_the_rest(home):
    """🔴 Red on main: one entry that was not an object stopped the whole store loading, and so did
    a ``hooks`` value that was not a list."""
    (home / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {"id": "a1", "name": "Kept", "event": "Stop", "provider": "notify"},
                    "not a trigger",
                    7,
                ]
            }
        ),
        encoding="utf-8",
    )
    assert [h.id for h in ScriptHookStore(config_dir=home).list_all()] == ["a1"]

    (home / "hooks.json").write_text(json.dumps({"hooks": {"session_key": "hook:x"}}))
    assert ScriptHookStore(config_dir=home).list_all() == []


# ── 🔴 a callback runs only on the owner's yes ──


def test_a_callback_the_agent_registered_is_refused_until_the_owner_allows_it(home, runs, audit):
    """🔴 Red on main: the call back was accepted and the turn started, from the context the agent
    wrote, with nobody asked."""
    _register("review:pr-1", "when CI answers, merge the PR")

    refused = _call_back("hook:review:pr-1")

    assert refused.status == 403
    assert _body(refused)["error"]["code"] == "not_allowed"
    assert runs.calls == []
    assert ("POST /api/hooks/agent", "denied") in [
        (r.get("operation"), r.get("outcome")) for r in audit
    ]


def test_the_owner_is_asked_and_the_yes_lets_it_run(home, runs, audit):
    """🔴 Red on main: nothing listed a callback, so nothing could ask."""
    _register("review:pr-1", "when CI answers, merge the PR")
    (row,) = _listed()
    assert row["enabled"] is False and row["needs_grant"] == ["Agent turn"]

    asked = _toggle("callback:review:pr-1", enabled=True, seal=row["seal"])

    assert asked.status == 400
    detail = _body(asked)["error"]["detail"]
    assert detail["title"] == "Allow this callback to run?"
    assert "start an agent turn with its tools" in detail["consent"]
    assert _call_back("hook:review:pr-1").status == 403

    assert (
        _toggle("callback:review:pr-1", enabled=True, seal=row["seal"], confirm=True).status == 200
    )

    assert _call_back("hook:review:pr-1").status == 200
    ((_state, key, _message, _name, _agent, _deliver, _timeout, restored),) = runs.calls
    assert key == "hook:review:pr-1" and "when CI answers, merge the PR" in restored
    assert ("trigger.grant", "success") in [(r.get("operation"), r.get("outcome")) for r in audit]


def test_a_yes_is_for_the_context_the_owner_read(home, runs):
    """🔴 Red on main. A yes to the context on the page is not a yes to context the agent saved
    afterwards: switching on with the old seal is refused, and a re-registration with other
    context after the yes waits for the owner again."""
    _register("review:pr-1", "check CI")
    (row,) = _listed()
    _register("review:pr-1", "check CI, then delete the release branch")

    stale = _toggle("callback:review:pr-1", enabled=True, seal=row["seal"], confirm=True)
    assert stale.status == 409

    (row,) = _listed()
    assert (
        _toggle("callback:review:pr-1", enabled=True, seal=row["seal"], confirm=True).status == 200
    )
    assert _call_back("hook:review:pr-1").status == 200

    _register("review:pr-1", "check CI, then push to main")

    assert _call_back("hook:review:pr-1").status == 403
    (row,) = _listed()
    assert row["enabled"] is False


def test_switching_a_callback_off_takes_the_yes_back(home, runs):
    _register("review:pr-1", "check CI")
    (row,) = _listed()
    _toggle("callback:review:pr-1", enabled=True, seal=row["seal"], confirm=True)

    assert _toggle("callback:review:pr-1", enabled=False).status == 200

    assert _call_back("hook:review:pr-1").status == 403


def test_deleting_a_callback_forgets_it(home, runs):
    """What the owner deletes is gone, and so is the yes it had: a later registration under the
    same id waits for them again."""
    _register("review:pr-1", "check CI")
    (row,) = _listed()
    _toggle("callback:review:pr-1", enabled=True, seal=row["seal"], confirm=True)
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None)
    req = make_mocked_request(
        "DELETE",
        "/api/triggers/callback:review:pr-1",
        match_info={"id": "callback:review:pr-1"},
        app=app,
    )

    assert asyncio.run(T.api_trigger_detail(req)).status == 200
    assert _listed() == []
    _register("review:pr-1", "check CI")
    assert _call_back("hook:review:pr-1").status == 403


def test_hook_register_tells_the_agent_it_waits(home):
    """🔴 Red on main: the tool said only that the hook was registered."""
    text = _register("review:pr-1", "check CI")

    assert "does not run until the owner allows it on the Triggers page" in text


def test_a_key_nobody_registered_is_the_owners_own_integration(home, runs):
    """The floor: an outside system holding the owner's webhook token, calling a session nobody
    registered, starts from nothing an agent wrote — it runs as it always did."""
    assert _call_back("hook:default:1700000000").status == 200
    ((_state, key, *_rest),) = runs.calls
    assert key == "hook:default:1700000000"


def test_an_allowed_callbacks_turn_starts_from_its_context(home, monkeypatch):
    """The turn itself: the context the owner allowed is what it starts from, and a key nobody
    registered starts from nothing."""
    from personalclaw import webhook_callbacks

    seen: list[str] = []

    async def _inner(_state, _key, message, _agent):
        seen.append(message)
        return ""

    class _Sessions:
        def release(self, _key):
            return None

        async def reset(self, _key):
            return None

        async def record_failure(self, _key):
            return None

    monkeypatch.setattr(hooks_mod, "_run_hook_inner", _inner)
    state = types.SimpleNamespace(sessions=_Sessions())
    callback = webhook_callbacks.register("review:pr-1", "merge when green")

    async def _turn(restored):
        await hooks_mod._hook_semaphore.acquire()
        await hooks_mod._run_hook_agent(
            state, "hook:review:pr-1", "CI passed", "n", None, False, 60, restored
        )

    asyncio.run(_turn(webhook_callbacks.restored_context(callback).text))
    asyncio.run(_turn(""))

    assert "merge when green" in seen[0] and seen[0].endswith("CI passed")
    assert seen[1] == "CI passed"
