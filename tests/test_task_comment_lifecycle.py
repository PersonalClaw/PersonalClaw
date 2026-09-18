"""The task-comment sidecar's two remaining #554 defects: unbounded bodies, orphaned files.

Issue #554 filed four defects on the comment endpoint. Three were already closed — the
author is server-derived (`test_task_author_attribution.py`), a non-string `body` answers
400 instead of a bare 500, and a DELETE route exists. These are the two the issue
confirmed and nothing had covered:

* **No size cap.** A 100,000-character body answered 201; the sidecar grew to 102KB and
  every subsequent GET re-read and re-serialised the whole thing. The store reads the
  entire thread into memory on every access, so one comment sets the floor cost of every
  later one. Bounded at the app's settled ceiling for a free-text field
  (`dashboard/handlers/hooks.py:_HOOK_MESSAGE_MAX_LEN`), and REFUSED rather than
  truncated — a client that stored 100K and got a 201 believes it stored 100K.

* **Comments outlived their task.** `delete_task` unlinked `<id>.json` and left
  `_comments_<id>.json`, so `GET /api/tasks/<gone>/comments` still returned the thread of a
  task nobody could open, and the file was unreachable through any API (the issue's author
  had to hand-edit it). Asserted on the FILESYSTEM, because a handler that answers 200 and
  leaks a file passes a response-shape test.

Every assertion reads STORED state, and every refusal is paired with a vacuity floor: a
route that refused everything would satisfy the refusal half on its own.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import registry
from personalclaw.tasks.handlers import _COMMENT_MAX_LEN, register_task_routes

OWNER = "keyur-golani"


@asynccontextmanager
async def _client(tmp_path):
    """Task routes over an isolated store with a known acting identity — the same shape
    `test_task_author_attribution.py` uses, so the two files cannot disagree about what a
    comment write looks like."""
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.native._current_username", return_value=OWNER),
        patch("personalclaw.tasks.handlers._owner_username", return_value=OWNER),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


def _sidecar(tmp_path, task_id):
    return tmp_path / "tasks" / f"_comments_{task_id}.json"


async def _task(client, title="carrier"):
    return await (await client.post("/api/tasks", json={"title": title})).json()


# ── the size ceiling ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_oversized_comment_is_refused_and_nothing_is_written(tmp_path):
    async with _client(tmp_path) as client:
        t = await _task(client)
        r = await client.post(
            f"/api/tasks/{t['id']}/comments", json={"body": "z" * (_COMMENT_MAX_LEN + 1)}
        )
        assert r.status == 400
        # The limit is NAMED, so a client can act on the refusal instead of guessing.
        assert str(_COMMENT_MAX_LEN) in (await r.json())["error"]
        # Refused means absent — not truncated and stored under a 400.
        assert not _sidecar(tmp_path, t["id"]).exists()
        assert (await (await client.get(f"/api/tasks/{t['id']}/comments")).json())["comments"] == []


@pytest.mark.asyncio
async def test_a_comment_at_the_ceiling_still_lands_whole(tmp_path):
    """VACUITY FLOOR. A cap that refused ordinary comments, or silently shortened the
    accepted ones, would satisfy the test above and break the feature."""
    async with _client(tmp_path) as client:
        t = await _task(client)
        body = "y" * _COMMENT_MAX_LEN
        r = await client.post(f"/api/tasks/{t['id']}/comments", json={"body": body})
        assert r.status == 201
        assert (await r.json())["body"] == body
        stored = json.loads(_sidecar(tmp_path, t["id"]).read_text(encoding="utf-8"))
        assert [len(c["body"]) for c in stored] == [_COMMENT_MAX_LEN]


@pytest.mark.asyncio
async def test_the_cap_is_measured_after_trimming(tmp_path):
    """Whitespace is stripped before the length check, so padding cannot be counted
    against the user for text the store never keeps."""
    async with _client(tmp_path) as client:
        t = await _task(client)
        r = await client.post(
            f"/api/tasks/{t['id']}/comments",
            json={"body": "  " + "y" * _COMMENT_MAX_LEN + "  "},
        )
        assert r.status == 201


# ── the sidecar's lifetime ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_task_removes_its_comment_sidecar(tmp_path):
    async with _client(tmp_path) as client:
        t = await _task(client)
        assert (
            await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "note"})
        ).status == 201
        sidecar = _sidecar(tmp_path, t["id"])
        assert sidecar.exists()

        assert (await client.delete(f"/api/tasks/{t['id']}")).status == 200
        # The FILE, not the response: the thread used to survive as an unreachable orphan
        # that a recycled id would have inherited.
        assert not sidecar.exists()
        assert not (tmp_path / "tasks" / f"{t['id']}.json").exists()
        assert (await (await client.get(f"/api/tasks/{t['id']}/comments")).json())["comments"] == []


@pytest.mark.asyncio
async def test_deleting_an_uncommented_task_still_succeeds(tmp_path):
    """VACUITY FLOOR for the unlink: most tasks have no sidecar, so a delete that raised
    on the missing file would break the common path outright."""
    async with _client(tmp_path) as client:
        t = await _task(client)
        assert not _sidecar(tmp_path, t["id"]).exists()
        assert (await client.delete(f"/api/tasks/{t['id']}")).status == 200


@pytest.mark.asyncio
async def test_one_task_delete_does_not_touch_another_tasks_comments(tmp_path):
    """The unlink is keyed on the deleted id. A prefix-ish match would take a sibling's
    thread with it — `record_path` builds the name, but nothing else asserted the scope."""
    async with _client(tmp_path) as client:
        kept = await _task(client, "kept")
        doomed = await _task(client, "doomed")
        for t in (kept, doomed):
            await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "note"})

        assert (await client.delete(f"/api/tasks/{doomed['id']}")).status == 200
        assert _sidecar(tmp_path, kept["id"]).exists()
        body = await (await client.get(f"/api/tasks/{kept['id']}/comments")).json()
        assert [c["body"] for c in body["comments"]] == ["note"]
