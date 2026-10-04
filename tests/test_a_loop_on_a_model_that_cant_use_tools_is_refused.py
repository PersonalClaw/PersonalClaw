"""A loop whose model can't use tools is refused before any of it runs, naming the model.

A loop's worker does all its work with tools: it writes each cycle's finding into the loop's folder,
and the supervisor credits only what is written there. On a model that can't use them (the bundled
default model is one, ``supports_tools = False``), the native runtime started tool-less with one
INFO line and nothing in the loop engine checked, so its worker "wrote" findings in prose that
never reached a file and the loop ran on.

These tests drive the real routes and the real resolution seam over a provider registry of two
fake model types, one that declares tools and one that does not; no model is called. Each refusal
is asked of the one reader (``llm.tool_use.uses_tools``) through the model the worker would really
run on, so a positive control on the model that uses tools sits beside every refusal.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.loop import store
from personalclaw.loop.loop import LoopStatus

TASK = "investigate the latency regression in checkout"
#: The model that can't use tools, and the one that can, as Settings → Models names them.
WITHOUT = "Pocket:tiny-1"
WITH = "Workshop:able-1"


class _Model:
    """A model provider that declares what its type says about tools, and is never called."""

    def __init__(self, uses_tools: bool) -> None:
        self.supports_tools = uses_tools
        self.closed = False

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        self.closed = True

    async def complete(self, messages: list[dict], **_kw: Any):  # pragma: no cover - not called
        raise AssertionError("a pre-flight never calls the model")
        yield

    async def stream(self, message: str):  # pragma: no cover - not called
        raise AssertionError("a pre-flight never calls the model")
        yield


def _capability(type_: str) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT, Capability.CODE_TOOLS}),
        supports_streaming=True,
        supports_tools=True,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
    )


@pytest.fixture
def built(monkeypatch) -> list[_Model]:
    """Settings → Providers holds Pocket (its model can't use tools) and Workshop (its can).
    Returns every model the resolution seam built, so a test can see each was closed again."""
    from personalclaw.guardrails.breaker import reset_breakers

    models: list[_Model] = []
    registry = ProviderRegistry()

    def _factory(uses_tools: bool):
        def build(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
            model = _Model(uses_tools)
            models.append(model)
            return model

        return build

    registry.register_type(_capability("pocket"), _factory(False))
    registry.register_type(_capability("workshop"), _factory(True))
    registry.register_entry(ProviderEntry(name="Pocket", type="pocket", model="tiny-1"))
    registry.register_entry(ProviderEntry(name="Workshop", type="workshop", model="able-1"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    reset_breakers()
    yield models
    reset_breakers()


@pytest.fixture
def bindings(monkeypatch) -> dict[str, list[str]]:
    """Settings → Models: Loops and Orchestration borrow Chat until a test binds them."""
    active: dict[str, list[str]] = {"chat": [WITH]}
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: active)
    return active


class _FakeSvc:
    def __init__(self) -> None:
        self.added: list[str] = []

    async def add(self, **kw):
        self.added.append(kw.get("session_name", ""))

    async def update(self, *a, **kw):
        pass

    async def remove(self, *a, **kw):
        pass

    def get_by_session(self, key):
        return None

    def list_all(self):
        return []


class _FakeSse:
    def publish(self, *a, **k):
        pass


class _FakeSession:
    def __init__(self, name: str) -> None:
        self.key = name
        self._extra_tool_roots: list[str] = []


class _FakeState:
    conversation_log = None

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}

    def push_refresh(self, *kinds):
        pass

    def push_sessions_update(self):
        pass

    def loop_sse(self):
        return _FakeSse()

    def get_or_create_session(self, name: str = "", **_kw):
        return self._sessions.setdefault(name, _FakeSession(name))


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    svc = _FakeSvc()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: svc)
    return svc


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _req(method: str, path: str, *, body: dict | None = None, match_info: dict | None = None):
    app = web.Application()
    app["state"] = _FakeState()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    req["user"] = "alice"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


def _refusal(resp: web.Response) -> str:
    assert resp.status == 422, _body(resp)
    error = _body(resp)["error"]
    assert error["code"] == "loop_model_cannot_use_tools"
    return error["message"]


def _create(kind: str = "goal", **extra: Any) -> web.Response:
    return _run(
        H.api_loop_create(_req("POST", "/api/loops", body={"kind": kind, "task": TASK, **extra}))
    )


# ── create, and the composer's dry run ─────────────────────────────────────────────────────


def test_a_loop_on_a_model_that_cant_use_tools_is_not_created(built, bindings):
    bindings["loops"] = [WITHOUT]

    message = _refusal(_create())

    assert "“Pocket:tiny-1” can't use tools" in message
    assert "Choose a model that uses tools for Loops in Settings → Models." in message
    assert store.list_all() == [], "nothing was stored for a loop that could not work"
    assert built and all(m.closed for m in built), "every model the check built was closed"


def test_the_same_loop_on_a_model_that_uses_tools_is_created(built, bindings):
    bindings["loops"] = [WITH]

    resp = _create()

    assert resp.status == 201, _body(resp)
    assert [loop.id for loop in store.list_all()] == [_body(resp)["id"]]


def test_the_composers_dry_run_says_it_before_create(built, bindings):
    bindings["loops"] = [WITHOUT]
    body = {"kind": "code", "task": TASK}

    result = _body(_run(H.api_loop_validate(_req("POST", "/api/loops/validate", body=body))))

    assert result["can_start"] is False
    assert any("“Pocket:tiny-1” can't use tools" in e for e in result["errors"])
    bindings["loops"] = [WITH]
    again = _body(_run(H.api_loop_validate(_req("POST", "/api/loops/validate", body=body))))
    assert not any("can't use tools" in e for e in again["errors"]), again


def test_a_loop_borrows_chats_model_when_loops_is_unbound(built, bindings):
    """Loops falls back to the Chat chain while it is unbound, and that is the model asked."""
    bindings["chat"] = [WITHOUT]

    assert "“Pocket:tiny-1”" in _refusal(_create())


def test_its_own_model_is_the_one_asked_and_the_fix_says_so(built, bindings):
    """A model chosen for the loop serves it when it is one of the chat models set up (as a chat's
    own pick does); then it is the one asked, and the fix names the loop."""
    bindings["chat"] = [WITH, WITHOUT]
    bindings["loops"] = [WITH]

    message = _refusal(_create(model=WITHOUT))

    assert "“Pocket:tiny-1” can't use tools" in message
    assert "Choose a model that uses tools for this loop." in message


def test_a_loop_on_an_agent_cli_brings_its_own_tools(built, bindings, monkeypatch):
    """An agent CLI runs its own tools, so its loop is not refused for PersonalClaw's model."""
    from personalclaw.loop import validation

    bindings["loops"] = [WITHOUT]
    monkeypatch.setattr(validation, "runtime_errors", lambda config, *, kind: [])

    assert _create(provider="acp:demo-cli").status == 201


def test_a_general_loop_is_refused_for_the_model_its_steps_run_on(built, bindings):
    """A General loop runs as a workflow: its steps run on Orchestration, and a step works with
    tools, so the sentence sends its owner there."""
    from personalclaw.workflows.bundled_defs import PROVIDER_NAME, register_bundled_provider
    from personalclaw.workflows.defs import get_provider, unregister_provider

    preexisting = get_provider(PROVIDER_NAME) is not None
    register_bundled_provider()
    try:
        bindings["orchestration"] = [WITHOUT]
        message = _refusal(_create(kind="general"))
        assert "“Pocket:tiny-1” can't use tools" in message
        assert "for Orchestration in Settings → Models" in message
    finally:
        if not preexisting:
            unregister_provider(PROVIDER_NAME)


# ── a loop that already exists: start, resume, and a change before launch ──────────────────


def _ready_loop(bindings) -> str:
    bindings["loops"] = [WITH]
    resp = _create()
    assert resp.status == 201, _body(resp)
    return _body(resp)["id"]


def _act(cid: str, action: str) -> web.Response:
    req = _req("PATCH", f"/api/loops/{cid}", body={"action": action}, match_info={"id": cid})
    return _run(H.api_loop_action(req))


def test_a_start_after_loops_moved_to_a_model_without_tools_is_refused(built, bindings, _home):
    cid = _ready_loop(bindings)
    bindings["loops"] = [WITHOUT]

    message = _refusal(_act(cid, "start"))

    assert "“Pocket:tiny-1” can't use tools" in message
    assert store.get(cid).status == LoopStatus.READY.value, "the loop did not start"
    assert _home.added == [], "no worker was armed"


def test_a_start_on_a_model_that_uses_tools_starts(built, bindings, _home, monkeypatch):
    cid = _ready_loop(bindings)
    monkeypatch.setattr("personalclaw.loop.manager.write_brief", lambda loop: None)

    resp = _act(cid, "start")

    assert resp.status == 200, _body(resp)
    assert store.get(cid).status == LoopStatus.RUNNING.value
    assert _home.added == [f"loop-{cid}"]


def test_a_resume_onto_a_model_without_tools_is_refused(built, bindings):
    cid = _ready_loop(bindings)
    store.update_status(cid, LoopStatus.RUNNING)
    store.update_status(cid, LoopStatus.PAUSED)
    bindings["loops"] = [WITHOUT]

    assert "can't use tools" in _refusal(_act(cid, "resume"))
    assert store.get(cid).status == LoopStatus.PAUSED.value


def _steer(cid: str) -> web.Response:
    req = _req("POST", f"/api/loops/{cid}/nudge", body={"text": "carry on"}, match_info={"id": cid})
    return _run(H.api_loop_nudge(req))


def test_a_steer_that_would_start_a_waiting_loop_on_such_a_model_is_refused(
    built, bindings, _home, monkeypatch
):
    """A steer on a loop that waits starts its worker again, as a resume does."""
    monkeypatch.setattr("personalclaw.loop.manager.write_brief", lambda loop: None)
    cid = _ready_loop(bindings)
    store.update_status(cid, LoopStatus.RUNNING)
    store.update_status(cid, LoopStatus.NEEDS_INPUT)
    bindings["loops"] = [WITHOUT]

    assert "“Pocket:tiny-1” can't use tools" in _refusal(_steer(cid))
    assert store.get(cid).status == LoopStatus.NEEDS_INPUT.value
    assert _home.added == [], "no worker was armed"

    bindings["loops"] = [WITH]
    assert _steer(cid).status == 200
    assert store.get(cid).status == LoopStatus.RUNNING.value
    assert _home.added == [f"loop-{cid}"]


def test_a_change_before_launch_onto_such_a_model_is_refused(built, bindings):
    cid = _ready_loop(bindings)
    bindings["chat"] = [WITH, WITHOUT]
    put = _req("PUT", f"/api/loops/{cid}", body={"model": WITHOUT}, match_info={"id": cid})

    assert "“Pocket:tiny-1” can't use tools" in _refusal(_run(H.api_loop_update(put)))
    assert store.get(cid).model == "", "the change was not saved"
    rename = _req("PUT", f"/api/loops/{cid}", body={"name": "Checkout"}, match_info={"id": cid})
    bindings["loops"] = [WITHOUT]
    assert (
        _run(H.api_loop_update(rename)).status == 200
    ), "an edit that moves no model is asked nothing"


def test_planning_on_a_model_that_cant_use_tools_is_refused(built, bindings, monkeypatch):
    planned: list[str] = []

    async def _advance(state, svc, lid):
        planned.append(lid)
        return "gated"

    monkeypatch.setattr("personalclaw.loop.plan_walkthrough.advance_plan", _advance)
    cid = _ready_loop(bindings)
    bindings["loops"] = [WITHOUT]
    req = _req("POST", f"/api/loops/{cid}/plan/start", match_info={"id": cid})

    message = _refusal(_run(H.api_loop_plan_start(req)))

    assert message.startswith("This loop can't be planned: “Pocket:tiny-1” can't use tools")
    assert planned == []
    bindings["loops"] = [WITH]
    assert _run(H.api_loop_plan_start(req)).status == 202


# ── the agent's own loop tools ──────────────────────────────────────────────────────────────


def test_the_agents_create_tool_makes_no_draft_on_such_a_model(built, bindings):
    """A draft the agent creates in chat is a loops-table loop whatever its kind, whose worker
    runs on the Loops chain."""
    from personalclaw.agents.native import sdlc_tools

    bindings["loops"] = [WITHOUT]
    for kind in ("goal", "general"):
        refused = _run(sdlc_tools.goal_loop_create({"goal": TASK, "kind": kind}))
        assert refused.success is False
        assert "“Pocket:tiny-1” can't use tools" in refused.error, refused.error
    assert store.list_all() == []

    bindings["loops"] = [WITH]
    assert _run(sdlc_tools.goal_loop_create({"goal": TASK})).success is True


def test_the_agents_start_tool_refuses_a_draft_now_on_such_a_model(built, bindings, _home):
    from personalclaw.agents.native import sdlc_tools

    cid = _ready_loop(bindings)
    bindings["loops"] = [WITHOUT]

    refused = _run(sdlc_tools.goal_loop_start({"loop_id": cid}))

    assert refused.success is False
    assert "“Pocket:tiny-1” can't use tools" in refused.error, refused.error
    assert store.get(cid).status == LoopStatus.READY.value
    assert _home.added == []


# ── the binding itself ──────────────────────────────────────────────────────────────────────


def _bind(use_case: str, models: list[str]) -> web.Response:
    from personalclaw.dashboard.handlers import model_registry as M
    from personalclaw.stale_write import REVISION_HEADER, revision_of

    req = make_mocked_request(
        "PUT",
        f"/api/models/active/{use_case}",
        match_info={"use_case": use_case},
        headers={REVISION_HEADER: revision_of([])},
        app=web.Application(),
    )

    async def _json():
        return {"models": models}

    req.json = _json  # type: ignore[assignment]
    return _run(M.api_models_active_set(req))


@pytest.fixture
def saved(monkeypatch, tmp_path) -> dict[str, list[str]]:
    """The binding store the route writes, with every provider name known and listed for chat."""
    from personalclaw.dashboard.handlers import model_registry as M

    stored: dict[str, list[str]] = {}
    monkeypatch.setattr(M, "load_active_models", lambda: dict(stored))
    monkeypatch.setattr(M, "save_active_models", lambda active: stored.update(active))
    monkeypatch.setattr(
        "personalclaw.providers.use_cases._known_provider_names", lambda: {"Pocket", "Workshop"}
    )

    async def _listed(refs):
        return {}

    monkeypatch.setattr(M, "_listed_jobs", _listed)
    monkeypatch.setattr(M, "_sel_log", lambda *a, **k: None)
    return stored


def test_loops_is_not_bound_to_a_model_that_cant_use_tools(built, saved):
    resp = _bind("loops", [WITHOUT])

    assert resp.status == 400
    error = _body(resp)["error"]
    assert error["code"] == "model_cannot_use_tools"
    assert error["message"].startswith(
        "“Pocket:tiny-1” can't use tools, so it can't run your loops"
    )
    assert "loops" not in saved


def test_loops_is_bound_to_a_model_that_uses_tools(built, saved):
    assert _bind("loops", [WITH]).status == 200
    assert saved["loops"] == [WITH]


def test_chat_may_still_be_bound_to_a_model_that_cant_use_tools(built, saved):
    """A chat can be answered without tools; only Loops does all its work with them."""
    assert _bind("chat", [WITHOUT]).status == 200
