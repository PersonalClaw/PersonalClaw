"""`/api/triggers` surfaces store-only kinds (file/web_watch/idle/…) — §6 additive slice (S94).

S92 made file/web_watch/idle/… automations creatable in chat and S93 made `file` ones fire, but
`GET /api/triggers` read only the three LEGACY backends (schedule crons, lifecycle hooks, event
triggers). So a chat-created file automation was **present and inert on the Automations page**:
created, fired, and unlistable — the user could not see, pause, run, or delete it in the UI.

These tests pin the additive read-plus-safe-mutation slice: the `store` namespace lists those
kinds and routes toggle/run/delete through S92's `tools.py`. This is NOT the §6 class-B re-point of
the schedule/event backends onto the store — those legacy paths are untouched here.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp home whose store the handler reads — patch both the loader and the handler helper."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


@pytest.fixture
def state(home):
    from unittest.mock import MagicMock

    st = MagicMock()
    st.crons.list_jobs.return_value = []
    st._hook_store = None
    return st


@pytest.fixture(autouse=True)
def _patch_legacy(monkeypatch):
    # Silence the legacy backends so the list contains only what we put in the store.
    monkeypatch.setattr(T, "_hook_store", lambda s: _EmptyStore())
    monkeypatch.setattr(T, "_event_store", lambda: _EmptyStore())
    monkeypatch.setattr(T, "_used_by_index", lambda: {})


class _EmptyStore:
    def list_all(self):
        return []

    def load(self):
        return []


def _store(home):
    return TriggerStore(base_dir=home)


def _file_automation(home, name="Summarize notes", when="when a file in ~/notes changes"):
    Tools.create(_store(home), name=name, when=when, message="go")


def _req(method, path, state, *, body=None, match_info=None, query=None):
    app = web.Application()
    app["state"] = state
    full = path + ("?" + query if query else "")
    req = make_mocked_request(method, full, match_info=match_info or {}, app=app)
    req["user"] = "tester"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    return json.loads(resp.body.decode())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ── 🔴 the present-and-inert gap this closes ──


def test_a_chat_created_file_automation_is_LISTED(home, state):
    """🔴 THE gap. Before this slice, a file automation created via the chat tools fired (S93) but
    never appeared in `GET /api/triggers` — invisible on its own management page."""
    _file_automation(home)
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    data = _body(resp)
    ids = {t["id"] for t in data["triggers"]}
    assert "store:file:summarize-notes" in ids
    row = next(t for t in data["triggers"] if t["id"] == "store:file:summarize-notes")
    assert row["kind"] == "store"
    assert row["store_kind"] == "file"
    assert row["enabled"] is True


def test_the_type_filter_selects_store_kinds(home, state):
    _file_automation(home)
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state, query="type=store")))
    data = _body(resp)
    assert data["triggers"] and all(t["kind"] == "store" for t in data["triggers"])


def test_a_clock_trigger_in_the_store_is_NOT_double_listed(home, state):
    """🔴 A `clock` trigger belongs to the schedule backend's namespace; listing it under `store`
    too would show every migrated cron twice once the store is populated."""
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="clock:x",
            name="C",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"provider": "run-prompt", "config": {}},
        )
    )
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state, query="type=store")))
    assert _body(resp)["triggers"] == []


def test_a_broken_store_row_is_listed_not_hidden(home, state):
    _store(home).path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "file:x", "name": "X", "kind": "file"}]})
    )
    # A file row with no paths still parses as a file kind; make it genuinely broken instead.
    _store(home).path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    {"id": "web_watch:x", "name": "X", "kind": "web_watch", "spec": "not-a-dict"}
                ],
            }
        )
    )
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    rows = [t for t in _body(resp)["triggers"] if t["kind"] == "store"]
    assert rows and rows[0]["broken"]


# ── id round-trip ──


def test_split_id_round_trips_a_store_id():
    """🔴 A store id is itself `<kind>:<slug>`. Splitting on the first colon would hand the store
    `file` as the raw id and lose `summarize-notes`."""
    assert T._split_id("store:file:summarize-notes") == ("store", "file:summarize-notes")


def test_a_bare_id_still_defaults_to_schedule():
    assert T._split_id("job1") == ("schedule", "job1")


# ── toggle ──


def test_toggle_pauses_and_resumes_a_store_trigger(home, state):
    _file_automation(home)
    resp = _run(
        T.api_trigger_toggle(
            _req(
                "POST",
                "/api/triggers/x/toggle",
                state,
                body={"enabled": False},
                match_info={"id": "store:file:summarize-notes"},
            )
        )
    )
    assert _body(resp)["ok"] is True
    assert _store(home).get("file:summarize-notes").trigger.enabled is False


def test_toggling_an_unknown_store_id_is_404(home, state):
    resp = _run(
        T.api_trigger_toggle(
            _req(
                "POST",
                "/api/triggers/x/toggle",
                state,
                body={},
                match_info={"id": "store:file:ghost"},
            )
        )
    )
    assert resp.status == 404


def test_resuming_a_broken_row_reports_400_not_a_silent_disable(home, state):
    """🔴 `set_enabled` refuses a broken row (S87); the API must surface that, not answer ok while
    leaving it disabled."""
    _store(home).path.write_text(
        json.dumps(
            {"version": 1, "triggers": [{"id": "web_watch:x", "name": "X", "kind": "nonsense"}]}
        )
    )
    resp = _run(
        T.api_trigger_toggle(
            _req(
                "POST",
                "/api/triggers/x/toggle",
                state,
                body={"enabled": True},
                match_info={"id": "store:web_watch:x"},
            )
        )
    )
    assert resp.status == 400


# ── delete ──


def test_delete_removes_a_store_trigger(home, state):
    _file_automation(home)
    resp = _run(
        T.api_trigger_detail(
            _req(
                "DELETE", "/api/triggers/x", state, match_info={"id": "store:file:summarize-notes"}
            )
        )
    )
    assert _body(resp)["ok"] is True
    assert _store(home).get("file:summarize-notes") is None


def test_deleting_an_unknown_store_id_is_404(home, state):
    resp = _run(
        T.api_trigger_detail(
            _req("DELETE", "/api/triggers/x", state, match_info={"id": "store:file:ghost"})
        )
    )
    assert resp.status == 404


# ── run ──


def test_a_dry_run_reports_the_gate_plan_and_executes_nothing(home, state):
    """🔴 Reuses `tools.run` so the API and the chat tool report identically; a dry run must fire
    nothing."""
    _file_automation(home)
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST",
                "/api/triggers/x/run",
                state,
                query="dry_run=1",
                match_info={"id": "store:file:summarize-notes"},
            )
        )
    )
    data = _body(resp)
    assert data["ok"] is True
    assert data["result"]["plan"]["executes"] is False
    # The manual bypass boundary is preserved.
    assert set(data["result"]["plan"]["bypassed"]) == {"quiet", "duty"}


def test_a_real_run_dispatches_the_action(home, state, monkeypatch):
    """A Run button fires through the same action-provider registry the autonomous path uses."""
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="file:notes",
            name="Notes",
            kind="file",
            enabled=True,
            spec={"paths": ["~/x/**"]},
            workflow={"provider": "notify", "config": {"title_template": "t"}},
        )
    )
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    prov = get_action_provider("notify")
    seen = {}

    async def spy(action_config, ctx, timeout=30):
        seen["event"] = ctx.event
        seen["trigger_id"] = ctx.payload.get("trigger_id")

    monkeypatch.setattr(prov, "execute", spy)
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST", "/api/triggers/x/run", state, body={}, match_info={"id": "store:file:notes"}
            )
        )
    )
    assert _body(resp)["ok"] is True
    assert seen["trigger_id"] == "file:notes"
    assert seen["event"] == "manual.run"


def test_a_paused_store_trigger_still_runs_by_hand(home, state, monkeypatch):
    """Pausing means "stop firing on its own"; a hand-driven run is how you test before re-enabling.
    The result notes it does not re-enable."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="file:notes",
            name="N",
            kind="file",
            enabled=False,
            spec={"paths": ["~/x/**"]},
            workflow={"provider": "notify", "config": {"title_template": "t"}},
        )
    )
    _ensure_default_providers_registered()
    monkeypatch.setattr(
        get_action_provider("notify"), "execute", lambda ac, ctx, timeout=30: _noop()
    )
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST", "/api/triggers/x/run", state, body={}, match_info={"id": "store:file:notes"}
            )
        )
    )
    data = _body(resp)
    assert data["ok"] is True
    assert "does not re-enable" in data["result"]
    assert _store(home).get("file:notes").trigger.enabled is False


def test_running_a_broken_row_is_400(home, state):
    _store(home).path.write_text(
        json.dumps(
            {"version": 1, "triggers": [{"id": "web_watch:x", "name": "X", "kind": "nonsense"}]}
        )
    )
    resp = _run(
        T.api_trigger_run(
            _req("POST", "/api/triggers/x/run", state, match_info={"id": "store:web_watch:x"})
        )
    )
    assert resp.status == 400


async def _noop():
    return None


# ── the boundary: legacy paths untouched ──


def test_store_only_kinds_excludes_clock_and_event():
    """🔴 The set must not claim `clock` or `event` — those belong to the schedule and event
    backends. Claiming them here would double-list every cron and event trigger."""
    assert "clock" not in T._STORE_ONLY_KINDS
    assert "event" not in T._STORE_ONLY_KINDS
    assert "file" in T._STORE_ONLY_KINDS
    assert "web_watch" in T._STORE_ONLY_KINDS
