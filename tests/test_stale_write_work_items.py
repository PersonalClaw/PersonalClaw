"""A whole-form save from a stale page is refused instead of overwriting a change made elsewhere
— the work-item routes: automations (schedule and lifecycle triggers), tasks, loops, plan steps.

Each of these pages saves a WHOLE record built from the copy it read. Two tabs read the same
record; one saves; the other — still holding the old copy — saves too, and on the old code that
second save put every field back the way its page last saw it, erasing the first save without a
word. The same happened when the gateway wrote the record itself between a page's read and its
save: the agent's `automation_update` / `task_update` tools, the planner's finalize and redraft.

The contract (`personalclaw/stale_write.py`): every read carries the record's ``revision``, a
whole-form save names its base in ``If-Match``, a stale base is refused with ``409 stale_write``
and nothing is written, and a save that names no base is ``428 revision_required``. Where the
edit is ONE item — ticking one task criterion — it is a per-item operation applied to what is
stored, and needs no revision at all.

Every assertion on the outcome reads STORED state, not the response: a route that answered 409
and wrote anyway would pass a status-only test.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request


def revision_of(document):
    """Imported per call, so this file collects on a tree that predates the module and each test
    reports its own verdict there."""
    from personalclaw.stale_write import revision_of as _revision_of

    return _revision_of(document)


def _based_on(revision: str) -> dict[str, str]:
    """The header a whole-form save names its base in. Empty when the read carried no revision
    (a tree that predates the contract), so what such a tree DOES with the save is what the
    assertions below measure."""
    return {"If-Match": f'"{revision}"'} if revision else {}


def _run(coro):
    return asyncio.run(coro)


# ── automations: schedule + lifecycle triggers (PUT /api/triggers/{id}) ─────────────────────────


@pytest.fixture
def automations(tmp_path, monkeypatch):
    """A real trigger store and hook store under a tmp home, behind the real routes."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.handlers import triggers as T
    from personalclaw.hooks import ScriptHookStore

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    hooks = ScriptHookStore(config_dir=tmp_path)
    monkeypatch.setattr(T, "_hook_store", lambda _state: hooks)
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    app = web.Application()
    app["state"] = MagicMock()
    T.register_trigger_routes(app)
    return app, tmp_path, hooks


NOW = 1_800_000_000.0


def _seed_schedule(home, *, next_fire_at: str = ""):
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=home).upsert(
        Trigger(
            id="nightly",
            name="Nightly digest",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600, "skip_dates": ["2026-12-25"]},
            workflow={
                "inline": {
                    "provider": "invoke-agent",
                    "config": {"task_template": "summarise my inbox", "agent": "", "model": ""},
                }
            },
            next_fire_at=next_fire_at,
        )
    )


def _stored_trigger(home):
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=home).get("nightly").trigger


async def _schedule_row(c: TestClient) -> dict:
    """What the Triggers page paints the schedule from."""
    data = await (await c.get("/api/triggers?type=schedule")).json()
    return next(t for t in data["triggers"] if t["raw_id"] == "nightly")


def _form_save(*, name: str, message: str, skip_dates: list[str]) -> dict:
    """Exactly the body the schedule edit form sends (`draftToPayload` → `_scheduleBodyToWire`):
    every field, the action rebuilt from the form's four agent fields."""
    return {
        "name": name,
        "timezone": "",
        "silent": False,
        "strict_schedule": False,
        "channel": "",
        "skip_dates": skip_dates,
        "failure_delivery": "inbox",
        "failure_dedupe": False,
        "every": 3600,
        "action": {
            "provider": "invoke-agent",
            "config": {"task_template": message, "agent": "", "model": "", "approval_mode": ""},
        },
    }


def _prompt(trigger) -> str:
    return trigger.workflow["inline"]["config"]["task_template"]


class TestAScheduleSaveFromAStaleCopy:
    @pytest.mark.asyncio
    async def test_the_second_save_from_the_same_base_is_refused(self, automations) -> None:
        app, home, _ = automations
        _seed_schedule(home)
        async with TestClient(TestServer(app)) as c:
            row = await _schedule_row(c)  # both tabs paint this
            base = row.get("revision", "")
            tab_a = _form_save(name="Nightly digest", message="tab A", skip_dates=["2026-12-25"])
            tab_b = _form_save(name="Nightly digest", message="tab B", skip_dates=[])

            first = await c.put(
                "/api/triggers/schedule:nightly", json=tab_a, headers=_based_on(base)
            )
            assert first.status == 200, await first.text()
            second = await c.put(
                "/api/triggers/schedule:nightly", json=tab_b, headers=_based_on(base)
            )
            assert second.status == 409, await second.text()
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert "the automation 'Nightly digest'" in err["message"]
        # Tab A's prompt and holiday survived; tab B's copy was never written over them.
        stored = _stored_trigger(home)
        assert _prompt(stored) == "tab A"
        assert stored.spec["skip_dates"] == ["2026-12-25"]

    @pytest.mark.asyncio
    async def test_a_form_save_that_names_no_base_is_refused(self, automations) -> None:
        app, home, _ = automations
        _seed_schedule(home)
        async with TestClient(TestServer(app)) as c:
            resp = await c.put(
                "/api/triggers/schedule:nightly",
                json=_form_save(name="x", message="no base", skip_dates=[]),
            )
            assert resp.status == 428
            assert (await resp.json())["error"]["code"] == "revision_required"
        assert _prompt(_stored_trigger(home)) == "summarise my inbox"

    @pytest.mark.asyncio
    async def test_the_agents_automation_update_between_read_and_save_is_not_undone(
        self, automations
    ) -> None:
        from personalclaw.triggers import tools
        from personalclaw.triggers.store import TriggerStore

        app, home, _ = automations
        _seed_schedule(home)
        async with TestClient(TestServer(app)) as c:
            row = await _schedule_row(c)  # the page opens the editor on this copy
            # The agent's `automation_update` tool — the same call `mcp_automation` routes to —
            # rewrites the prompt and the skip dates while the editor is open.
            done = tools.update(
                TriggerStore(base_dir=home),
                trigger_id="nightly",
                patch={
                    "workflow": {
                        "inline": {
                            "provider": "invoke-agent",
                            "config": {"task_template": "the agent's prompt"},
                        }
                    },
                    "spec": {**_stored_trigger(home).spec, "skip_dates": ["2027-01-01"]},
                },
            )
            assert done.ok, done.text
            resp = await c.put(
                "/api/triggers/schedule:nightly",
                json=_form_save(name="Renamed", message=row["message"], skip_dates=["2026-12-25"]),
                headers=_based_on(row.get("revision", "")),
            )
            assert resp.status == 409, await resp.text()
            assert (await resp.json())["error"]["code"] == "stale_write"
        stored = _stored_trigger(home)
        assert _prompt(stored) == "the agent's prompt"
        assert stored.spec["skip_dates"] == ["2027-01-01"]
        assert stored.name == "Nightly digest"

    @pytest.mark.asyncio
    async def test_the_scheduler_advancing_a_fire_does_not_refuse_the_save(
        self, automations
    ) -> None:
        """The run state is not part of the document: the tick rewrites `next_fire_at` on every
        fire, and a revision that covered it would refuse every save made across a fire for a
        change the user never made."""
        from personalclaw.triggers import service
        from personalclaw.triggers.store import TriggerStore

        app, home, _ = automations
        _seed_schedule(home, next_fire_at=service.to_iso(NOW - 1))
        async with TestClient(TestServer(app)) as c:
            row = await _schedule_row(c)
            ticked = await service.tick(
                TriggerStore(base_dir=home), now=NOW, persist=True, base_dir=home
            )
            # Positive control: the tick really rewrote the row between the read and the save.
            assert "nightly" in ticked.rescheduled
            assert _stored_trigger(home).next_fire_at != service.to_iso(NOW - 1)
            resp = await c.put(
                "/api/triggers/schedule:nightly",
                json=_form_save(name="Renamed", message="kept", skip_dates=[]),
                headers=_based_on(row.get("revision", "")),
            )
            assert resp.status == 200, await resp.text()
        stored = _stored_trigger(home)
        assert (stored.name, _prompt(stored)) == ("Renamed", "kept")

    @pytest.mark.asyncio
    async def test_the_named_channels_own_setup_does_not_move_the_revision(
        self, automations, monkeypatch
    ) -> None:
        """`channel_problem` reads the named channel's setup, not the automation: setting that
        channel up (or removing it) is no edit of this schedule, and a revision that covered the
        reading would refuse the next save for a change the form never sends."""
        from personalclaw.triggers import schedule_view

        app, home, _ = automations
        _seed_schedule(home)
        async with TestClient(TestServer(app)) as c:
            monkeypatch.setattr(schedule_view, "_channel_problem", lambda _trigger: "")
            before = await _schedule_row(c)
            monkeypatch.setattr(
                schedule_view,
                "_channel_problem",
                lambda _trigger: "No channel named ops is set up.",
            )
            after = await _schedule_row(c)
        assert after["channel_problem"] and not before["channel_problem"], "the control"
        assert after["revision"] == before["revision"]

    @pytest.mark.asyncio
    async def test_a_rename_alone_needs_no_base_and_the_answer_carries_the_new_revision(
        self, automations
    ) -> None:
        app, home, _ = automations
        _seed_schedule(home)
        async with TestClient(TestServer(app)) as c:
            renamed = await c.put("/api/triggers/schedule:nightly", json={"name": "Just a name"})
            assert renamed.status == 200
            answered = (await renamed.json())["trigger"]
            # A page that stays open saves again over the revision the write answered with.
            assert answered["revision"] == (await _schedule_row(c))["revision"]
            again = await c.put(
                "/api/triggers/schedule:nightly",
                json=_form_save(name="Just a name", message="second save", skip_dates=[]),
                headers=_based_on(answered["revision"]),
            )
            assert again.status == 200, await again.text()
        assert _prompt(_stored_trigger(home)) == "second save"


def _seed_hook(hooks) -> str:
    hook = hooks.create(
        {
            "name": "Guard writes",
            "event": "PreToolUse",
            "matcher": "write_file",
            "provider": "notify",
            "provider_config": {"title": "a write is about to run", "body": "check it"},
        }
    )
    return hook.id


async def _hook_row(c: TestClient, hook_id: str) -> dict:
    data = await (await c.get("/api/triggers?type=lifecycle")).json()
    return next(t for t in data["triggers"] if t["raw_id"] == hook_id)


def _hook_save(title: str) -> dict:
    """What the lifecycle edit form sends (`api.updateHook`): every field plus the action."""
    config = {"title": title, "body": "check it"}
    return {
        "name": "Guard writes",
        "event": "PreToolUse",
        "matcher": "write_file",
        "provider": "notify",
        "provider_config": config,
        "action": {"provider": "notify", "config": config},
    }


class TestALifecycleSaveFromAStaleCopy:
    @pytest.mark.asyncio
    async def test_the_second_save_from_the_same_base_is_refused(self, automations) -> None:
        app, _home, hooks = automations
        hook_id = _seed_hook(hooks)
        async with TestClient(TestServer(app)) as c:
            base = (await _hook_row(c, hook_id)).get("revision", "")
            first = await c.put(
                f"/api/triggers/lifecycle:{hook_id}",
                json=_hook_save("tab A"),
                headers=_based_on(base),
            )
            assert first.status == 200, await first.text()
            second = await c.put(
                f"/api/triggers/lifecycle:{hook_id}",
                json=_hook_save("tab B"),
                headers=_based_on(base),
            )
            assert second.status == 409, await second.text()
            assert (await second.json())["error"]["code"] == "stale_write"
        assert hooks.get(hook_id).provider_config["title"] == "tab A"

    @pytest.mark.asyncio
    async def test_a_form_save_that_names_no_base_is_refused(self, automations) -> None:
        app, _home, hooks = automations
        hook_id = _seed_hook(hooks)
        async with TestClient(TestServer(app)) as c:
            resp = await c.put(f"/api/triggers/lifecycle:{hook_id}", json=_hook_save("no base"))
            assert resp.status == 428
            assert (await resp.json())["error"]["code"] == "revision_required"
        assert hooks.get(hook_id).provider_config["title"] == "a write is about to run"

    @pytest.mark.asyncio
    async def test_toggling_it_off_elsewhere_does_not_refuse_the_save(self, automations) -> None:
        """`enabled` belongs to the toggle route, and the form never sends it — so a toggle in
        another tab is not a change this save could undo, and it survives the save."""
        app, _home, hooks = automations
        hook_id = _seed_hook(hooks)
        async with TestClient(TestServer(app)) as c:
            base = (await _hook_row(c, hook_id)).get("revision", "")
            assert hooks.toggle(hook_id).enabled is False  # the toggle route's own store call
            resp = await c.put(
                f"/api/triggers/lifecycle:{hook_id}",
                json=_hook_save("edited"),
                headers=_based_on(base),
            )
            assert resp.status == 200, await resp.text()
        hook = hooks.get(hook_id)
        assert (hook.provider_config["title"], hook.enabled) == ("edited", False)


# ── tasks (PUT /api/tasks/{id}) ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _task_client(home):
    from personalclaw.tasks import registry
    from personalclaw.tasks.handlers import register_task_routes

    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=home),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


def _stored_task(home, task_id: str) -> dict:
    return json.loads((home / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))


async def _new_task(c: TestClient) -> dict:
    r = await c.post(
        "/api/tasks",
        json={
            "title": "Ship the release",
            "labels": ["release"],
            "exit_criteria": [
                {"description": "Changelog written", "status": "incomplete"},
                {"description": "Tag pushed", "status": "incomplete"},
            ],
        },
    )
    assert r.status == 201
    return await r.json()


def _task_form(task: dict, **changes) -> dict:
    """What the task page's Save sends (`TaskForm.draftToPayload`): every field, lists whole."""
    body = {
        "title": task["title"],
        "description": task.get("description", ""),
        "status": task["status"],
        "priority": task["priority"],
        "task_list_id": task.get("task_list_id", ""),
        "assignee": task.get("assignee", ""),
        "due": task.get("due", ""),
        "labels": task["labels"],
        "exit_criteria": task["exit_criteria"],
        "action_plan": task.get("action_plan", []),
        "notes": task.get("notes", []),
        "research_notes": task.get("research_notes", []),
        "execution_notes": task.get("execution_notes", []),
        "agent_instructions_template": task.get("agent_instructions_template", ""),
        "dependencies": [],
    }
    return {**body, **changes}


class TestATaskSaveFromAStaleCopy:
    @pytest.mark.asyncio
    async def test_the_second_save_from_the_same_base_is_refused(self, tmp_path) -> None:
        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            task = await (await c.get(f"/api/tasks/{created['id']}")).json()  # both tabs
            base = task.get("revision", "")
            first = await c.put(
                f"/api/tasks/{task['id']}",
                json=_task_form(task, labels=["release", "tab-a"]),
                headers=_based_on(base),
            )
            assert first.status == 200, await first.text()
            second = await c.put(
                f"/api/tasks/{task['id']}",
                json=_task_form(task, title="Tab B's title"),
                headers=_based_on(base),
            )
            assert second.status == 409, await second.text()
            err = (await second.json())["error"]
            assert err["code"] == "stale_write"
            assert "the task 'Ship the release'" in err["message"]
        stored = _stored_task(tmp_path, created["id"])
        assert stored["labels"] == ["release", "tab-a"]
        assert stored["title"] == "Ship the release"

    @pytest.mark.asyncio
    async def test_a_form_save_that_names_no_base_is_refused(self, tmp_path) -> None:
        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            resp = await c.put(
                f"/api/tasks/{created['id']}", json=_task_form(created, labels=["replaced"])
            )
            assert resp.status == 428
            assert (await resp.json())["error"]["code"] == "revision_required"
        assert _stored_task(tmp_path, created["id"])["labels"] == ["release"]

    @pytest.mark.asyncio
    async def test_a_status_change_alone_needs_no_base(self, tmp_path) -> None:
        # The board's drag and the dashboard's "complete" send one scalar: the edit itself.
        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            resp = await c.put(f"/api/tasks/{created['id']}", json={"status": "in_progress"})
            assert resp.status == 200, await resp.text()
        assert _stored_task(tmp_path, created["id"])["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_the_agents_task_update_between_read_and_save_is_not_undone(
        self, tmp_path
    ) -> None:
        from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

        ws = tmp_path / "ws"
        ws.mkdir()
        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            task = await (await c.get(f"/api/tasks/{created['id']}")).json()  # the page's copy
            # The agent's `task_update` tool, invoked the way a turn invokes it.
            agent = await NativeBuiltinToolProvider(ws).invoke(
                "task_update", {"id": task["id"], "labels": ["release", "agent-flagged"]}
            )
            assert agent.success, agent.error
            resp = await c.put(
                f"/api/tasks/{task['id']}",
                json=_task_form(task, description="the user's notes"),
                headers=_based_on(task.get("revision", "")),
            )
            assert resp.status == 409, await resp.text()
            assert (await resp.json())["error"]["code"] == "stale_write"
        stored = _stored_task(tmp_path, created["id"])
        assert stored["labels"] == ["release", "agent-flagged"]
        assert stored["description"] == ""

    @pytest.mark.asyncio
    async def test_every_read_reports_the_same_revision_and_the_save_answers_the_next(
        self, tmp_path
    ) -> None:
        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            detail = await (await c.get(f"/api/tasks/{created['id']}")).json()
            listed = next(
                t
                for t in (await (await c.get("/api/tasks")).json())["tasks"]
                if t["id"] == created["id"]
            )
            # The page paints from the LIST; the detail read is the same document.
            assert listed["revision"] == detail["revision"] == created["revision"]
            saved = await c.put(
                f"/api/tasks/{created['id']}",
                json=_task_form(detail, labels=["release", "one"]),
                headers=_based_on(detail["revision"]),
            )
            answered = await saved.json()
            assert answered["revision"] != detail["revision"]
            again = await c.put(
                f"/api/tasks/{created['id']}",
                json=_task_form(answered, labels=["release", "one", "two"]),
                headers=_based_on(answered["revision"]),
            )
            assert again.status == 200, await again.text()
        assert _stored_task(tmp_path, created["id"])["labels"] == ["release", "one", "two"]


def _criteria(home, task_id: str) -> dict[str, str]:
    return {c["description"]: c["status"] for c in _stored_task(home, task_id)["exit_criteria"]}


class TestTickingOneCriterion:
    """The inline tick is ONE item, applied to what is stored — so it needs no revision and
    cannot undo a change another writer made to the rest of the list."""

    @pytest.mark.asyncio
    async def test_a_tick_keeps_what_the_agent_changed_in_the_same_list(self, tmp_path) -> None:
        from personalclaw.tasks import registry

        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            # While the page shows the task, the agent ticks the OTHER criterion and adds one.
            await registry.update_task(
                created["id"],
                exit_criteria=[
                    {"description": "Changelog written", "status": "incomplete"},
                    {"description": "Tag pushed", "status": "complete"},
                    {"description": "Announced", "status": "incomplete"},
                ],
            )
            resp = await c.put(
                f"/api/tasks/{created['id']}",
                json={
                    "tick": {
                        "list": "exit_criteria",
                        "index": 0,
                        "text": "Changelog written",
                        "done": True,
                    }
                },
            )
            assert resp.status == 200, await resp.text()
        assert _criteria(tmp_path, created["id"]) == {
            "Changelog written": "complete",
            "Tag pushed": "complete",
            "Announced": "incomplete",
        }

    @pytest.mark.asyncio
    async def test_a_tick_finds_its_item_after_another_writer_moved_it(self, tmp_path) -> None:
        from personalclaw.tasks import registry

        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            await registry.update_task(
                created["id"],
                exit_criteria=[
                    {"description": "Inserted above", "status": "incomplete"},
                    {"description": "Changelog written", "status": "incomplete"},
                    {"description": "Tag pushed", "status": "incomplete"},
                ],
            )
            # The page saw "Tag pushed" at index 1; it is at 2 now.
            resp = await c.put(
                f"/api/tasks/{created['id']}",
                json={
                    "tick": {
                        "list": "exit_criteria",
                        "index": 1,
                        "text": "Tag pushed",
                        "done": True,
                    }
                },
            )
            assert resp.status == 200, await resp.text()
        assert _criteria(tmp_path, created["id"]) == {
            "Inserted above": "incomplete",
            "Changelog written": "incomplete",
            "Tag pushed": "complete",
        }

    @pytest.mark.asyncio
    async def test_a_tick_of_an_item_removed_elsewhere_is_refused(self, tmp_path) -> None:
        from personalclaw.tasks import registry

        async with _task_client(tmp_path) as c:
            created = await _new_task(c)
            await registry.update_task(
                created["id"], exit_criteria=[{"description": "Tag pushed", "status": "incomplete"}]
            )
            resp = await c.put(
                f"/api/tasks/{created['id']}",
                json={
                    "tick": {
                        "list": "exit_criteria",
                        "index": 0,
                        "text": "Changelog written",
                        "done": True,
                    }
                },
            )
            assert resp.status == 409, await resp.text()
            assert (await resp.json())["error"]["code"] == "stale_write"
        # Nothing was ticked in its place.
        assert _criteria(tmp_path, created["id"]) == {"Tag pushed": "incomplete"}


# ── loops (PUT /api/loops/{id}) ────────────────────────────────────────────────────────────────


class _LoopState:
    conversation_log = None

    def push_refresh(self, *kinds):
        pass

    def loop_sse(self):
        return MagicMock()


class _NudgeSvc:
    async def add(self, **kw):
        return None

    async def update(self, *a, **kw):
        pass

    async def remove(self, *a, **kw):
        pass

    def get_by_session(self, key):
        return None


@pytest.fixture
def loops_home(monkeypatch, tmp_path):
    """Every loop row, file dir and task under tmp_path — never the real home."""
    import personalclaw.tasks.native as nat

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: _NudgeSvc())
    return tmp_path


def _loop_req(method, path, *, body=None, match_info=None, headers=None):
    app = web.Application()
    app["state"] = _LoopState()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app, headers=headers)
    req["user"] = "alice"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    return json.loads(resp.body.decode())


def _new_loop() -> str:
    from personalclaw.dashboard.handlers import loop_routes as H

    r = _run(
        H.api_loop_create(
            _loop_req(
                "POST",
                "/api/loops",
                body={
                    "kind": "goal",
                    "task": "investigate the latency regression in checkout",
                    "kind_config": {"goal_type": "open_ended", "sub_goals": ["measure"]},
                },
            )
        )
    )
    assert r.status == 201, _body(r)
    return _body(r)["id"]


def _loop_read(cid: str) -> dict:
    """What Plan Review paints from (`api.uLoop`)."""
    from personalclaw.dashboard.handlers import loop_routes as H

    r = _run(H.api_loop_get(_loop_req("GET", f"/api/loops/{cid}", match_info={"id": cid})))
    assert r.status == 200
    return _body(r)


def _loop_save(cid: str, body: dict, base: str | None):
    """A Plan Review launch write: the plan, the capability lists and its `kind_config` keys."""
    from personalclaw.dashboard.handlers import loop_routes as H

    return _run(
        H.api_loop_update(
            _loop_req(
                "PUT",
                f"/api/loops/{cid}",
                body=body,
                match_info={"id": cid},
                headers=_based_on(base) if base is not None else None,
            )
        )
    )


def _plan_review(sub_goals: list[str]) -> dict:
    return {
        "name": "Latency",
        "plan": [{"title": s} for s in sub_goals],
        "skill_ids": [],
        "workflow_ids": [],
        "kind_config": {"goal_type": "open_ended", "sub_goals": sub_goals},
    }


class TestALoopSaveFromAStaleCopy:
    def test_the_second_save_from_the_same_base_is_refused(self, loops_home) -> None:
        from personalclaw.loop import store

        cid = _new_loop()
        base = _loop_read(cid).get("revision", "")
        first = _loop_save(cid, _plan_review(["tab A"]), base)
        assert first.status == 200, _body(first)
        second = _loop_save(cid, _plan_review(["tab B"]), base)
        assert second.status == 409, _body(second)
        assert _body(second)["error"]["code"] == "stale_write"
        assert store.get(cid).kind_config["sub_goals"] == ["tab A"]
        assert [row["title"] for row in store.get(cid).plan] == ["tab A"]

    def test_a_save_that_names_no_base_is_refused(self, loops_home) -> None:
        from personalclaw.loop import store

        cid = _new_loop()
        resp = _loop_save(cid, _plan_review(["no base"]), None)
        assert resp.status == 428, _body(resp)
        assert _body(resp)["error"]["code"] == "revision_required"
        assert store.get(cid).kind_config["sub_goals"] == ["measure"]

    def test_a_rename_alone_needs_no_base(self, loops_home) -> None:
        from personalclaw.loop import store

        cid = _new_loop()
        resp = _loop_save(cid, {"name": "Just a name"}, None)
        assert resp.status == 200, _body(resp)
        assert store.get(cid).name == "Just a name"

    def test_the_planners_finalize_between_read_and_save_is_not_undone(self, loops_home) -> None:
        from personalclaw.loop import files as loop_files
        from personalclaw.loop import plan_walkthrough as pw
        from personalclaw.loop import store
        from personalclaw.planning import session as PS

        cid = _new_loop()
        base = _loop_read(cid).get("revision", "")  # the review screen opens on this copy
        # The planner's walkthrough finishes: its approved sub-goals project into the spec.
        sess = PS.PlanSession(project_id=cid, created_at=time.time())
        step = PS.PlanStep(id="step-1", kind="sub_goals", title="Sub-goals")
        step.status = PS.StepStatus.APPROVED.value
        step.artifact = {"sub_goals": ["profile the hot path", "compare against last week"]}
        sess.steps = [step]
        loop_files.write_plan_session(sess)
        assert _run(pw.finalize_plan(cid)) is True
        planned = ["profile the hot path", "compare against last week"]
        assert store.get(cid).kind_config["sub_goals"] == planned  # positive control

        resp = _loop_save(cid, _plan_review(["measure", "the user's goal"]), base)
        assert resp.status == 409, _body(resp)
        assert _body(resp)["error"]["code"] == "stale_write"
        assert store.get(cid).kind_config["sub_goals"] == planned

    def test_the_answer_carries_the_revision_the_next_save_names(self, loops_home) -> None:
        cid = _new_loop()
        first = _loop_save(cid, _plan_review(["one"]), _loop_read(cid).get("revision", ""))
        answered = _body(first)
        assert answered["revision"] == _loop_read(cid)["revision"]
        again = _loop_save(cid, _plan_review(["one", "two"]), answered["revision"])
        assert again.status == 200, _body(again)


# ── plan steps (POST …/plan/edit) ───────────────────────────────────────────────────────────────


def _seed_step(cid: str, markdown: str = "ORIGINAL") -> None:
    from personalclaw.loop import files as loop_files
    from personalclaw.planning import session as PS

    sess = PS.PlanSession(project_id=cid, created_at=time.time())
    step = PS.PlanStep(id="step-0", kind="intent", title="Intent", objective="restate the goal")
    step.status = PS.StepStatus.AWAITING_REVIEW.value
    step.artifact = {"markdown": markdown}
    sess.steps = [step]
    loop_files.write_plan_session(sess)


def _loop_step(cid: str) -> dict:
    """The step as the walkthrough's poll paints it."""
    from personalclaw.dashboard.handlers import loop_routes as H

    r = _run(
        H.api_loop_plan_session(
            _loop_req("GET", f"/api/loops/{cid}/plan-session", match_info={"id": cid})
        )
    )
    return _body(r)["session"]["steps"][0]


def _loop_edit(cid: str, markdown: str, base: str | None):
    from personalclaw.dashboard.handlers import loop_routes as H

    return _run(
        H.api_loop_plan_edit(
            _loop_req(
                "POST",
                f"/api/loops/{cid}/plan/edit",
                body={"step_id": "step-0", "markdown": markdown},
                match_info={"id": cid},
                headers=_based_on(base) if base is not None else None,
            )
        )
    )


def _loop_markdown(cid: str) -> str:
    from personalclaw.loop import files as loop_files

    return loop_files.read_plan_session(cid).steps[0].artifact["markdown"]


class TestALoopPlanEditFromAStaleDraft:
    def test_the_second_edit_from_the_same_draft_is_refused(self, loops_home) -> None:
        cid = _new_loop()
        _seed_step(cid)
        base = _loop_step(cid).get("revision", "")
        first = _loop_edit(cid, "ORIGINAL, edited in tab A", base)
        assert first.status == 200, _body(first)
        second = _loop_edit(cid, "ORIGINAL, edited in tab B", base)
        assert second.status == 409, _body(second)
        assert _body(second)["error"]["code"] == "stale_write"
        assert _loop_markdown(cid) == "ORIGINAL, edited in tab A"

    def test_an_edit_that_names_no_draft_is_refused(self, loops_home) -> None:
        cid = _new_loop()
        _seed_step(cid)
        resp = _loop_edit(cid, "no base", None)
        assert resp.status == 428, _body(resp)
        assert _body(resp)["error"]["code"] == "revision_required"
        assert _loop_markdown(cid) == "ORIGINAL"

    def test_an_edit_of_the_draft_a_redraft_replaced_is_refused(
        self, loops_home, monkeypatch
    ) -> None:
        """The redraft returns the step to awaiting review — the only thing the route checked —
        so an editor still open on the previous draft used to replace the new plan with it."""
        from personalclaw.dashboard.handlers import loop_routes as H
        from personalclaw.loop import plan_walkthrough as pw

        async def _planner_writes_a_new_draft(*_a, **_k):
            return json.dumps({"markdown": "REDRAFTED by the planner"})

        monkeypatch.setattr(pw, "_run_pass", _planner_writes_a_new_draft)
        monkeypatch.setattr(pw, "advance_plan", lambda *a, **k: asyncio.sleep(0, "gated"))
        cid = _new_loop()
        _seed_step(cid)
        base = _loop_step(cid).get("revision", "")  # the editor opens on "ORIGINAL"
        commented = _run(
            H.api_loop_plan_comment(
                _loop_req(
                    "POST",
                    f"/api/loops/{cid}/plan/comment",
                    body={"step_id": "step-0", "text": "tighten it"},
                    match_info={"id": cid},
                )
            )
        )
        assert commented.status == 202, _body(commented)
        # The planner's real redraft pass, with only the model call replaced.
        assert _run(pw.run_step_pass(_LoopState(), _NudgeSvc(), cid, "step-0")) is not None
        assert _loop_step(cid)["status"] == "awaiting_review"  # back at the gate

        resp = _loop_edit(cid, "ORIGINAL, as the stale editor had it", base)
        assert resp.status == 409, _body(resp)
        assert _body(resp)["error"]["code"] == "stale_write"
        assert _loop_markdown(cid) == "REDRAFTED by the planner"


def _chat_app(state) -> web.Application:
    from chat_test_helpers import _make_app

    from personalclaw.dashboard import chat_plan

    app = _make_app(state)
    app.router.add_get("/api/chat/sessions/{session}/plan-session", chat_plan.api_chat_plan_session)
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/activate", chat_plan.api_chat_plan_activate
    )
    app.router.add_post("/api/chat/sessions/{session}/plan/edit", chat_plan.api_chat_plan_edit)
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/comment", chat_plan.api_chat_plan_comment
    )
    return app


def _chat_with_a_draft(tmp_path, monkeypatch, markdown: str = "# Plan\n1. read\n2. write"):
    """A chat whose plan step sits at the review gate holding *markdown*."""
    from chat_test_helpers import _make_state

    async def _no_turn(state, session, msg, **kw):
        return None

    monkeypatch.setattr("personalclaw.dashboard.chat_runner.run_chat", _no_turn)
    state = _make_state(tmp_path)
    chat = state.get_or_create_session("c1")
    chat.append("user", "plan my week", "msg msg-u")
    chat.drain()
    return state, chat, markdown


async def _chat_step(c: TestClient) -> dict:
    return (await (await c.get("/api/chat/sessions/c1/plan-session")).json())["session"]["steps"][0]


async def _chat_edit(c: TestClient, markdown: str, base: str | None):
    return await c.post(
        "/api/chat/sessions/c1/plan/edit",
        json={"step_id": "chat-plan-1", "markdown": markdown},
        headers=_based_on(base) if base is not None else None,
    )


def _chat_markdown() -> str:
    from personalclaw.dashboard import chat_plan

    return chat_plan.read("c1")[0].steps[0].artifact["markdown"]


async def _draft_lands(state, chat, markdown: str) -> None:
    """The plan-mode turn's reply arrives: the real turn-end hook submits it as the draft."""
    from personalclaw.dashboard import chat_plan

    chat.append("assistant", markdown, "msg msg-a")
    chat.drain()
    assert chat_plan.maybe_submit_plan_draft(state, chat) is True


class TestAChatPlanEditFromAStaleDraft:
    @pytest.mark.asyncio
    async def test_the_second_edit_from_the_same_draft_is_refused(self, tmp_path, monkeypatch):
        state, chat, markdown = _chat_with_a_draft(tmp_path, monkeypatch)
        async with TestClient(TestServer(_chat_app(state))) as c:
            await c.post("/api/chat/sessions/c1/plan/activate")
            await _draft_lands(state, chat, markdown)
            base = (await _chat_step(c)).get("revision", "")
            first = await _chat_edit(c, markdown + "\n3. tab A's step", base)
            assert first.status == 200, await first.text()
            second = await _chat_edit(c, markdown + "\n3. tab B's step", base)
            assert second.status == 409, await second.text()
            assert (await second.json())["error"]["code"] == "stale_write"
        assert _chat_markdown().endswith("tab A's step")

    @pytest.mark.asyncio
    async def test_an_edit_that_names_no_draft_is_refused(self, tmp_path, monkeypatch):
        state, chat, markdown = _chat_with_a_draft(tmp_path, monkeypatch)
        async with TestClient(TestServer(_chat_app(state))) as c:
            await c.post("/api/chat/sessions/c1/plan/activate")
            await _draft_lands(state, chat, markdown)
            resp = await _chat_edit(c, "no base", None)
            assert resp.status == 428
            assert (await resp.json())["error"]["code"] == "revision_required"
        assert _chat_markdown() == markdown

    @pytest.mark.asyncio
    async def test_an_edit_of_the_draft_a_redraft_replaced_is_refused(self, tmp_path, monkeypatch):
        state, chat, markdown = _chat_with_a_draft(tmp_path, monkeypatch)
        async with TestClient(TestServer(_chat_app(state))) as c:
            await c.post("/api/chat/sessions/c1/plan/activate")
            await _draft_lands(state, chat, markdown)
            base = (await _chat_step(c)).get("revision", "")  # an editor opens on draft 1
            commented = await c.post(
                "/api/chat/sessions/c1/plan/comment",
                json={"step_id": "chat-plan-1", "text": "add a review step"},
            )
            assert commented.status == 200
            await _draft_lands(state, chat, "# Plan\n1. read\n2. review\n3. write")
            assert (await _chat_step(c))["status"] == "awaiting_review"  # back at the gate

            resp = await _chat_edit(c, markdown + "\n3. the stale editor's step", base)
            assert resp.status == 409, await resp.text()
            assert (await resp.json())["error"]["code"] == "stale_write"
        assert _chat_markdown() == "# Plan\n1. read\n2. review\n3. write"
