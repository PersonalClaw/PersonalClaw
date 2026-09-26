"""ONE loop listing, whatever backs the loop — a run-backed loop IS a loop.

Measured 2026-09-25: a General loop was working while the Loops list said "No loops yet", Home said
"0 loops running" / "No active work", and Mission Control's Working lane said "Nothing is running".
Every one of those surfaces reads `GET /api/loops`, and that route read the loops TABLE alone —
while a ported kind (`general`) had stopped writing a loops row at PP-16 and become a `WorkflowRun`.

So the route now answers for both homes, and a run-backed row carries `run_id` so a surface can tell
which cockpit and which lifecycle it holds. The detail, action, delete and nudge routes answer for a
run-backed id too, each through the RUN's own verb and its own guard.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import loop_view, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.models import RunStatus, WorkflowRun


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    return home


def _loop_run(
    *,
    status: RunStatus = RunStatus.RUNNING,
    title: str = "Weekly checklist",
    overrides: dict[str, Any] | None = None,
    project_id: str = "",
    loop_kind: str = "general",
    created_at: str = "2026-09-25T10:00:00Z",
) -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="general-project",
            status=status,
            inputs={"task": "write a three-item checklist", "exit_condition": "it exists"},
            policy_overrides=dict(overrides if overrides is not None else {"attended": False}),
            project_id=project_id,
            loop_kind=loop_kind,
            title=title,
            created_at=created_at,
            started_at="2026-09-25T10:00:05Z",
        )
    )
    store.write_spec(run.id, read_template("general-project").to_dict())
    return run


class _FakeState:
    def __init__(self) -> None:
        self.workflows = None
        self._sessions: dict[str, Any] = {}
        self._restricted_keys: set[str] = set()
        self._sse = None
        self.refreshed: list[str] = []

    def push_refresh(self, *kinds: str) -> None:
        self.refreshed.extend(kinds)


def _call(
    handler: Any,
    method: str,
    path: str,
    *,
    match: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    app = web.Application()
    app["state"] = _FakeState()
    request = make_mocked_request(method, path, app=app, match_info=match or {})

    async def _json() -> dict[str, Any]:
        return body or {}

    request.json = _json  # type: ignore[assignment]
    response = asyncio.run(handler(request))
    return response.status, json.loads(response.body.decode())


def _list(query: str = "") -> list[dict[str, Any]]:
    status, body = _call(H.api_loop_list, "GET", f"/api/loops{query}")
    assert status == 200, body
    return body["loops"]


# ── the listing ──


def test_a_run_backed_loop_is_in_the_loop_listing() -> None:
    """At `origin/main` this list is EMPTY: the route read `loop.store` only."""
    run = _loop_run(overrides={"attended": False, "max_cycles": 30})
    rows = _list()
    assert [r["id"] for r in rows] == [run.id], rows
    row = rows[0]
    assert row["run_id"] == run.id
    assert row["kind"] == "general"
    assert row["name"] == "Weekly checklist"
    assert row["status"] == LoopStatus.RUNNING.value
    assert row["attended"] is False
    assert row["max_cycles"] == 30


def test_both_homes_share_one_newest_first_listing_and_its_filters() -> None:
    older = _loop_run(created_at="2026-09-20T00:00:00Z", project_id="p1")
    goal = loop_store.create(
        Loop(id="", name="Packing", kind="goal", task="research the best packing strategy")
    )
    rows = _list()
    assert [r["id"] for r in rows] == [goal.id, older.id], [r["id"] for r in rows]
    assert "run_id" not in rows[0] and rows[1]["run_id"] == older.id
    assert [r["id"] for r in _list("?kind=general")] == [older.id]
    assert [r["id"] for r in _list("?kind=goal")] == [goal.id]
    assert [r["id"] for r in _list("?project_id=p1")] == [older.id]


def test_a_run_that_was_not_started_as_a_loop_is_not_listed() -> None:
    """A template run from the Workflows page is a run, not a loop — `loop_kind` is empty."""
    _loop_run(loop_kind="")
    assert _list() == []


def test_listing_does_not_create_the_run_store(tmp_path: Path) -> None:
    """Home polls this; asking must not mint an empty `runs.db` on a gateway with no runs."""
    assert _list() == []
    assert not (tmp_path / "home" / "workflows" / "runs.db").exists()
    assert not list((tmp_path / "home").rglob("runs.db"))


# ── the projection ──


def test_every_run_status_projects_onto_the_loop_vocabulary() -> None:
    assert set(loop_view._STATUS) == set(RunStatus), "a run status reaches the loop list unnamed"


@pytest.mark.parametrize(
    ("status", "attention", "expected_status", "expected_reason"),
    [
        (RunStatus.COMPLETE, None, "complete", "done"),
        (RunStatus.CANCELLED, None, "stopped", "user"),
        (
            RunStatus.ESCALATED,
            {"reason": "max_iterations", "detail": "reached 30"},
            "complete",
            "cycle_budget",
        ),
        (
            RunStatus.ESCALATED,
            {"reason": "identical_output", "detail": "byte-identical 3x"},
            "complete",
            "worker_failed",
        ),
        (RunStatus.PAUSED, None, "paused", ""),
    ],
)
def test_the_projected_status_tells_the_truth(
    status: RunStatus, attention: Any, expected_status: str, expected_reason: str
) -> None:
    run = _loop_run(status=status)
    run.attention = attention
    view = loop_view.run_loop_view(run)
    assert (view["status"], view["stop_reason"]) == (expected_status, expected_reason)
    if attention:
        assert view["error_message"] == attention["detail"]


def test_the_cycle_budget_and_count_are_the_ones_the_engine_uses() -> None:
    """Budget = the run's override, else the template's declared cap. Count = DISTINCT finished
    iterations: a tripped breaker writes two rows for one iteration."""
    run = _loop_run(overrides={"attended": False})
    j = journal_mod.Journal(run.id)
    j.iteration("root", "project", iteration=0, outcome="continue")
    j.iteration("root", "project", iteration=1, outcome="breaker:identical_output")
    j.iteration("root", "project", iteration=1, outcome="continue")
    view = loop_view.run_loop_view(run)
    assert view["total_cycles"] == 2, view["total_cycles"]
    declared = read_template("general-project").to_dict()["root"]["config"]["max_iterations"]
    assert view["max_cycles"] == declared
    assert loop_view.run_loop_view(_loop_run(overrides={"max_cycles": 30}))["max_cycles"] == 30


# ── detail / action / delete / nudge ──


def test_the_detail_route_answers_for_a_run_backed_loop() -> None:
    """A `#/loops/<id>` link to one used to 404 into "This loop no longer exists"."""
    run = _loop_run()
    status, body = _call(H.api_loop_get, "GET", f"/api/loops/{run.id}", match={"id": run.id})
    assert status == 200, body
    assert body["run_id"] == run.id


def test_pause_resume_and_stop_are_the_runs_own_verbs() -> None:
    run = _loop_run()
    status, body = _call(
        H.api_loop_action,
        "PATCH",
        f"/api/loops/{run.id}",
        match={"id": run.id},
        body={"action": "pause"},
    )
    assert status == 200, body
    assert store.pause_requested(run.id), "the loop route's pause never reached the run"

    # A running run cannot be resumed — the same 409 sentence a loops row gets.
    status, body = _call(
        H.api_loop_action,
        "PATCH",
        f"/api/loops/{run.id}",
        match={"id": run.id},
        body={"action": "resume"},
    )
    assert status == 409, body
    assert body["error"] == "Cannot resume a loop in 'running' state"

    status, body = _call(
        H.api_loop_action,
        "PATCH",
        f"/api/loops/{run.id}",
        match={"id": run.id},
        body={"action": "stop"},
    )
    assert status == 200, body
    assert store.cancel_requested(run.id)


def test_a_failed_run_backed_loop_is_not_resumable() -> None:
    """A loops row resumes from `failed`; a run has one attempt and cannot."""
    run = _loop_run(status=RunStatus.FAILED)
    status, body = _call(
        H.api_loop_action,
        "PATCH",
        f"/api/loops/{run.id}",
        match={"id": run.id},
        body={"action": "resume"},
    )
    assert status == 409, body


def test_delete_refuses_a_live_run_and_removes_a_finished_one() -> None:
    live = _loop_run()
    status, body = _call(
        H.api_loop_delete, "DELETE", f"/api/loops/{live.id}", match={"id": live.id}
    )
    assert status == 409, body
    assert store.get(live.id) is not None

    done = _loop_run(status=RunStatus.COMPLETE)
    status, body = _call(
        H.api_loop_delete, "DELETE", f"/api/loops/{done.id}", match={"id": done.id}
    )
    assert status == 200 and body == {"ok": True}, body
    assert store.get(done.id) is None


def test_a_nudge_steers_the_run() -> None:
    run = _loop_run()
    status, body = _call(
        H.api_loop_nudge,
        "POST",
        f"/api/loops/{run.id}/nudge",
        match={"id": run.id},
        body={"text": "use verbs only"},
    )
    assert status == 200, body
    queued = (store.get(run.id).extra or {}).get("steering_queue") or []
    assert [q["text"] for q in queued] == ["use verbs only"], queued
