"""Document comments are SERVER-SIDE state, and the durability inventory declares them (#429).

🔴 WHAT THIS PINS. The annotation layer over files, artifacts and planning docs shipped
browser-only: ``web/src/pages/files/comments/commentStore.ts`` read and wrote one global
``localStorage`` key (``doc-comments-v1``) and nothing else. Measured consequences: clearing
site data destroyed the only copy, ``personalclaw snapshot`` could not carry them (the
server never saw them, which is also why they were absent from ``durability/inventory.py``),
and they were invisible on a second device. Task comments next door
(``tasks/_comments_<id>.json``) were a real store the whole time, so the two comment systems
had opposite durability guarantees and nothing told the user which one they were using.

The frontend half is pinned in ``web/src/pages/files/comments/commentStoreDurability.test.ts``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import doc_comments


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home. NEVER the real one — this module writes on every test."""
    pc = tmp_path / ".personalclaw"
    pc.mkdir()
    monkeypatch.setattr(doc_comments, "config_dir", lambda: pc)
    return pc


# ── the store ───────────────────────────────────────────────────────────────────


class TestTheStorePersists:
    def test_an_added_comment_lands_on_disk(self, home: Path):
        row = doc_comments.add(doc_id="rates.md", doc_label="rates.md", quote="q", comment="mine")
        written = home / "doc_comments.json"
        assert written.is_file(), "the comment never reached the filesystem"
        data = json.loads(written.read_text(encoding="utf-8"))
        assert [c["id"] for c in data["comments"]] == [row.id]
        assert data["comments"][0]["comment"] == "mine"

    def test_it_survives_a_fresh_read(self, home: Path):
        """The property `localStorage` could not give: the state outlives the browser."""
        doc_comments.add(doc_id="a.md", comment="first")
        doc_comments.add(doc_id="b.md", comment="second")
        assert [c.comment for c in doc_comments.load()] == ["first", "second"]

    def test_a_missing_store_reads_as_empty_rather_than_raising(self, home: Path):
        assert doc_comments.load() == []
        assert doc_comments.list_comments() == []

    def test_an_unreadable_store_reads_as_empty(self, home: Path):
        """FAIL-OPEN, the storage convention: a corrupt file costs the deck, not the page."""
        (home / "doc_comments.json").write_text("{not json", encoding="utf-8")
        assert doc_comments.load() == []

    def test_a_row_with_no_id_or_doc_id_is_dropped_not_crashed(self, home: Path):
        (home / "doc_comments.json").write_text(
            json.dumps(
                {
                    "comments": [
                        {"id": "", "doc_id": "a"},
                        {"id": "c-1", "doc_id": ""},
                        {"id": "c-2", "doc_id": "a.md", "comment": "kept"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert [c.id for c in doc_comments.load()] == ["c-2"]

    def test_the_anchor_travels(self, home: Path):
        """`line`/`column`/`context` are what let the assistant find a repeated quote."""
        row = doc_comments.add(
            doc_id="a.md", quote="Torque tables", comment="c", line=11, column=30, context="…x…"
        )
        back = doc_comments.load()[0]
        assert (back.line, back.column, back.context) == (11, 30, "…x…")
        assert back.id == row.id

    def test_a_blank_comment_is_refused(self, home: Path):
        with pytest.raises(ValueError):
            doc_comments.add(doc_id="a.md", comment="   ")

    def test_a_blank_doc_id_is_refused(self, home: Path):
        with pytest.raises(ValueError):
            doc_comments.add(doc_id="", comment="c")

    def test_an_over_long_comment_is_clamped_not_stored_whole(self, home: Path):
        row = doc_comments.add(doc_id="a.md", comment="x" * (doc_comments.MAX_COMMENT_CHARS + 500))
        assert len(row.comment) == doc_comments.MAX_COMMENT_CHARS

    def test_update_edits_the_body_and_returns_none_for_an_unknown_id(self, home: Path):
        row = doc_comments.add(doc_id="a.md", comment="before")
        assert doc_comments.update(row.id, comment="after") is not None
        assert doc_comments.load()[0].comment == "after"
        assert doc_comments.update("nope", comment="x") is None

    def test_remove_reports_what_it_actually_removed(self, home: Path):
        a = doc_comments.add(doc_id="a.md", comment="a")
        doc_comments.add(doc_id="b.md", comment="b")
        assert doc_comments.remove([a.id, "never-existed"]) == 1
        assert [c.comment for c in doc_comments.load()] == ["b"]

    def test_clear_empties_the_deck(self, home: Path):
        doc_comments.add(doc_id="a.md", comment="a")
        doc_comments.add(doc_id="b.md", comment="b")
        assert doc_comments.clear() == 2
        assert doc_comments.load() == []


# ── the HTTP surface ────────────────────────────────────────────────────────────


async def _client(monkeypatch: pytest.MonkeyPatch, pc: Path) -> TestClient:
    """A client over the doc-comment routes WITH the request boundary installed.

    The middleware is not optional scaffolding here: the handlers read their body through
    ``request_validation.json_object_body``, whose refusal is a ``RequestValidationError``
    that :mod:`personalclaw.dashboard.request_boundary` turns into the wire envelope once
    for the whole app. A bare ``web.Application()`` — which is what this fixture used to
    build — has no such gate, so a malformed body reached the client as a 500 and this
    file would have "proved" the shape refusals were broken while the real gateway served
    them correctly. Mirror the gateway's middleware stack, or the test measures the
    fixture.
    """
    from personalclaw.dashboard.handlers import doc_comments as handlers
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    monkeypatch.setattr(handlers.doc_comments, "config_dir", lambda: pc)
    app = web.Application(middlewares=[request_boundary_middleware()])
    handlers.register_doc_comment_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_the_routes_round_trip_a_comment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pc = tmp_path / ".personalclaw"
    pc.mkdir()
    client = await _client(monkeypatch, pc)
    try:
        created = await client.post(
            "/api/doc-comments", json={"doc_id": "a.md", "doc_label": "a.md", "comment": "mine"}
        )
        assert created.status == 201
        cid = (await created.json())["comment"]["id"]

        listed = await client.get("/api/doc-comments")
        assert [c["comment"] for c in (await listed.json())["comments"]] == ["mine"]

        edited = await client.patch(f"/api/doc-comments/{cid}", json={"comment": "edited"})
        assert edited.status == 200
        assert (await edited.json())["comment"]["comment"] == "edited"

        gone = await client.delete(f"/api/doc-comments/{cid}")
        assert gone.status == 200
        assert (await (await client.get("/api/doc-comments")).json())["comments"] == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_bulk_delete_is_one_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pc = tmp_path / ".personalclaw"
    pc.mkdir()
    client = await _client(monkeypatch, pc)
    try:
        ids = []
        for n in ("a", "b", "c"):
            r = await client.post("/api/doc-comments", json={"doc_id": f"{n}.md", "comment": n})
            ids.append((await r.json())["comment"]["id"])
        r = await client.post("/api/doc-comments/delete", json={"ids": ids[:2]})
        assert (await r.json())["removed"] == 2
        left = (await (await client.get("/api/doc-comments")).json())["comments"]
        assert [c["comment"] for c in left] == ["c"]
        assert (await (await client.delete("/api/doc-comments")).json())["removed"] == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_every_refusal_uses_the_one_wire_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A new surface must not add to the flat `{"error": "<prose>"}` population, which is
    shrink-only (`tests/test_wire_error_envelope_census.py`)."""
    pc = tmp_path / ".personalclaw"
    pc.mkdir()
    client = await _client(monkeypatch, pc)
    try:
        cases = [
            await client.post("/api/doc-comments", data="not json"),
            await client.post("/api/doc-comments", json=["not", "an", "object"]),
            await client.post("/api/doc-comments", json={"doc_id": "a.md", "comment": "  "}),
            await client.post("/api/doc-comments", json={"doc_id": 5, "comment": "c"}),
            await client.patch("/api/doc-comments/nope", json={"comment": "x"}),
            await client.delete("/api/doc-comments/nope"),
            await client.post("/api/doc-comments/delete", json={"ids": "not-a-list"}),
        ]
        for resp in cases:
            assert resp.status in (400, 404), resp.status
            body = await resp.json()
            assert isinstance(body.get("error"), dict), f"flat envelope: {body}"
            assert body["error"].get("code"), body
            assert body["error"].get("message"), body
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_404_delete_is_not_a_silent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The swallowed-write shape this issue is a member of: answering success for a row the
    store never held means an honest client never learns its id was wrong."""
    pc = tmp_path / ".personalclaw"
    pc.mkdir()
    client = await _client(monkeypatch, pc)
    try:
        resp = await client.delete("/api/doc-comments/never-existed")
        assert resp.status == 404
    finally:
        await client.close()


# ── the durability half ─────────────────────────────────────────────────────────


def test_the_inventory_declares_doc_comments():
    """The second half of the issue: absent from the inventory, a snapshot cannot carry it."""
    from personalclaw.durability import inventory as inv

    entry = next((e for e in inv.INVENTORY if e.id == "doc_comments"), None)
    assert entry is not None, "document comments are not declared state"
    assert entry.path == "doc_comments.json"
    assert not entry.secret and not entry.derived, "annotations are real, non-rebuildable state"


def test_it_is_carried_by_the_backup_and_export_projections():
    """Both are inventory-derived, so declaring the entry is what puts it in a snapshot AND an
    export. Asserted rather than assumed: a `secret`/`derived` flag would silently drop it."""
    from personalclaw.durability import inventory as inv

    assert any(e.id == "doc_comments" for e in inv.backup_entries())
    assert any(e.id == "doc_comments" for e in inv.export_entries())


def test_an_export_carries_the_comments_and_an_import_returns_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End to end, because the inventory entry only matters if the archive really holds it."""
    import os
    from unittest.mock import patch

    from personalclaw.portability import apply_import_zip, create_export_zip

    pc = tmp_path / "home"
    pc.mkdir()
    (pc / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(doc_comments, "config_dir", lambda: pc)
    doc_comments.add(doc_id="rates.md", doc_label="rates.md", quote="q", comment="annotation")

    with patch("personalclaw.portability.config_dir", return_value=pc):
        zip_bytes, _ = create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(zip_bytes)

    target = tmp_path / "target"
    target.mkdir()
    with patch("personalclaw.portability.config_dir", return_value=target):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
            apply_import_zip(archive, mode="merge")

    monkeypatch.setattr(doc_comments, "config_dir", lambda: target)
    assert [c.comment for c in doc_comments.load()] == [
        "annotation"
    ], "the annotation did not survive an export/import round trip"
