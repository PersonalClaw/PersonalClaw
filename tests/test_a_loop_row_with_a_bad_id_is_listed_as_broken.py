"""A loops row whose id the store can't open is listed as broken, and deleted by that id alone.

`get()` and `delete()` refuse an id that isn't 8 hex characters (`files.valid_loop_id`), while
`list_all()` used to return every row. A row with such an id (one reached a real home through a test
seed) showed in the app, opened as not found, and the app's Delete answered "Invalid loop id".

Now the store is one thing about it:
- `list_all()` and `list_for_project()` hold only rows it can open, so nothing that walks them (the
  watchdog's resume, the running list, the project pages) opens, runs or resumes a broken row;
- `list_broken()` names each other row with the reason, and the listing shows it as broken;
- `delete_broken()` deletes one such row by its exact id, touches no file, and refuses an id the
  store can open, which goes through `delete()` and its teardown as before.
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
from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus

BAD_ID = "c0ckp1t0"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    return home


def _code(name: str, project_id: str = "p-1") -> Loop:
    return store.create(
        Loop(
            id="",
            name=name,
            kind="code",
            task="add oauth login to the app",
            project_id=project_id,
            kind_config={"entry_stage": "design", "queued_task_ids": []},
        )
    )


def _plant_bad_row(like: Loop, bad_id: str = BAD_ID, *, status: str = "running") -> None:
    """Copy *like*'s row under *bad_id*, the way a seed that wrote the table directly would."""
    conn = store._connect()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(loops)").fetchall()]
        row = dict(conn.execute("SELECT * FROM loops WHERE id = ?", (like.id,)).fetchone())
        row.update(id=bad_id, name="Smoke cockpit", status=status)
        conn.execute(
            f"INSERT INTO loops ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
        conn.commit()
    finally:
        conn.close()


def _ids_in_table() -> set[str]:
    conn = store._connect()
    try:
        return {r[0] for r in conn.execute("SELECT id FROM loops").fetchall()}
    finally:
        conn.close()


class _FakeState:
    def __init__(self) -> None:
        self.workflows = None
        self._sessions: dict[str, Any] = {}
        self._sse = None

    def push_refresh(self, *kinds: str) -> None:
        pass


def _call(
    handler: Any, method: str, path: str, match: dict[str, str] | None = None
) -> tuple[int, Any]:
    app = web.Application()
    app["state"] = _FakeState()
    request = make_mocked_request(method, path, app=app, match_info=match or {})
    request["user"] = "owner"
    response = asyncio.run(handler(request))
    return response.status, json.loads(response.body.decode())


def test_the_store_lists_only_rows_it_can_open() -> None:
    before, after = _code("before"), _code("after", project_id="p-2")
    _plant_bad_row(before)

    assert {lp.id for lp in store.list_all()} == {before.id, after.id}
    assert {lp.id for lp in store.list_for_project("p-1")} == {before.id}
    assert store.get(BAD_ID) is None


def test_a_bad_id_row_is_named_with_its_reason() -> None:
    _plant_bad_row(_code("neighbour"))

    broken = store.list_broken()
    assert [b.id for b in broken] == [BAD_ID]
    assert broken[0].name == "Smoke cockpit"
    assert "can't be opened" in broken[0].reason
    assert store.list_broken(project_id="p-1")[0].id == BAD_ID
    assert store.list_broken(project_id="p-9") == []


def test_the_listing_shows_it_as_broken_and_never_as_running() -> None:
    good = _code("neighbour")
    _plant_bad_row(good, status=LoopStatus.RUNNING.value)

    status, body = _call(H.api_loop_list, "GET", "/api/loops")
    assert status == 200, body
    rows = {r["id"]: r for r in body["loops"]}
    assert set(rows) == {good.id, BAD_ID}
    shown = rows[BAD_ID]
    assert shown["broken"] and "can't be opened" in shown["broken"]
    # Failed, so no surface counts it as work that runs, and the lists offer Delete for it.
    assert shown["status"] == LoopStatus.FAILED.value
    assert shown["name"] == "Smoke cockpit" and shown["kind"] == "code"
    assert "broken" not in rows[good.id]


def test_delete_broken_removes_only_that_row_and_touches_no_file(_isolated: Path) -> None:
    left, right = _code("left"), _code("right")
    _plant_bad_row(left)
    # A folder named like the row: a delete by a bad id must never reach the disk.
    decoy = _isolated / "loop" / BAD_ID
    decoy.mkdir(parents=True)
    (decoy / "keep.txt").write_text("not the store's to remove")

    assert store.delete_broken(BAD_ID) is True
    assert _ids_in_table() == {left.id, right.id}
    assert store.get(left.id) is not None and store.get(right.id) is not None
    assert (decoy / "keep.txt").exists()
    assert store.list_broken() == []
    # Once gone, a second delete finds nothing.
    assert store.delete_broken(BAD_ID) is False


def test_delete_broken_refuses_an_id_the_store_can_open() -> None:
    good = _code("good")
    assert store.delete_broken(good.id) is False
    assert store.get(good.id) is not None


def test_a_path_shaped_bad_id_is_deleted_as_a_row_only(_isolated: Path) -> None:
    good = _code("good")
    (_isolated / "loop").mkdir(exist_ok=True)
    sneaky = "../loop"
    _plant_bad_row(good, sneaky)
    assert [b.id for b in store.list_broken()] == [sneaky]

    assert store.delete_broken(sneaky) is True
    assert _ids_in_table() == {good.id}
    assert (_isolated / "loop").exists()


def test_the_delete_route_removes_a_listed_broken_row() -> None:
    good = _code("good")
    _plant_bad_row(good)

    status, body = _call(H.api_loop_delete, "DELETE", f"/api/loops/{BAD_ID}", {"id": BAD_ID})
    assert status == 200, body
    assert body == {"ok": True, "broken": True}
    assert _ids_in_table() == {good.id}


def test_the_delete_route_still_refuses_a_bad_id_no_row_has() -> None:
    status, body = _call(H.api_loop_delete, "DELETE", "/api/loops/nothere", {"id": "nothere"})
    assert status == 400, body
