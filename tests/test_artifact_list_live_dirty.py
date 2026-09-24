"""#630 — a list-shaped ``live_dirty`` was a fabricated False; now it is absent.

``get()`` computes ``live_dirty`` against the live source; ``list()`` rows come off
``_read_meta``, where the field is never persisted (``to_dict(persist=True)`` drops
it) — so the list value was ALWAYS the dataclass default, and the same artifact
reported False in the list and True in the detail. Every consumer already takes the
flag from a content-bearing response (one says so in a comment); these rails make
the wire honest so the next reader cannot mistake the default for an answer:
absent in the list, computed in the detail.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider


@pytest.fixture
def prov(tmp_path, monkeypatch):
    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    monkeypatch.setitem(registry._providers, "native", provider)
    return provider


def _make_app() -> web.Application:
    app = web.Application()
    state = MagicMock()
    state.is_restricted_session.return_value = False
    app["state"] = state
    register_artifact_routes(app)
    return app


@pytest.fixture
def dirty_artifact(prov, tmp_path):
    """A file-backed artifact whose live source has moved past its snapshot."""
    src = tmp_path / "notes.md"
    src.write_text("# v1", encoding="utf-8")
    art = prov.create(
        name="notes", content="# v1", kind="markdown", source="chat", source_path=str(src)
    )
    # Mutate the live source AFTER the snapshot — get() must now report dirty.
    src.write_text("# v2 drifted", encoding="utf-8")
    return art


class TestListLiveDirtyIsAbsentNotFabricated:
    @pytest.mark.asyncio
    async def test_list_rows_carry_no_live_dirty_key(self, dirty_artifact):
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.get("/api/artifacts")
            assert resp.status == 200
            rows = (await resp.json())["artifacts"]
        assert rows, "the fixture artifact must be listed"
        for row in rows:
            # The measured bug: this key was present and False while the detail
            # said True. Absent beats wrong.
            assert "live_dirty" not in row
            # content stays list-omitted too — the pair travels together.
            assert "content" not in row

    @pytest.mark.asyncio
    async def test_detail_still_computes_it_and_reports_the_drift(self, dirty_artifact):
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.get(f"/api/artifacts/{dirty_artifact.slug}")
            assert resp.status == 200
            body = await resp.json()
        # The acceptance half: the computed flag survives on the content-bearing
        # response, and it is TRUE for the drifted source (not merely present).
        assert body["live_dirty"] is True
        assert body["content"] == "# v2 drifted"

    @pytest.mark.asyncio
    async def test_detail_reports_false_for_an_undrifted_artifact(self, prov):
        prov.create(name="clean", content="x", kind="text", source="chat")
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.get("/api/artifacts/clean")
            assert resp.status == 200
            body = await resp.json()
        # False-by-computation is still served — only the fabricated list value died.
        assert body["live_dirty"] is False
