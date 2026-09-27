"""A file-backed artifact never writes its file unless the request carries the text to write.

A file-backed artifact is a live pointer, and every save of its body writes that body through to
the file. Measured on ``main`` at 72d615f82, a request that named the file and NO text wiped it:

    POST /api/artifacts {"name": "Brief", "source_path": "<ws>/brief.md"}   If-Match: <its revision>
        -> 201, and brief.md is now 0 bytes

``api_artifacts_create`` read the missing ``content`` as ``""`` and the store wrote ``""`` through
(``native.create`` → ``_try_write_source_path``). The request named the file's revision, so the
stale-write check (#3690) passed, and a second request for a file already saved as an artifact
wiped it the same way through the dedup branch (``update(content="")``). The UI always sends the
file's text, so only an API or app caller hit it.

The same "absent means empty" reading sat in more writers of a file or a document:

* ``PUT /api/memory/{preferences,projects,history}`` wrote ``""`` over the document;
* the agent's ``write_file`` wrote an empty file when the call carried no ``content`` (and the
  chat's file-change chip showed it emptied);
* the agent's ``artifact_update`` / ``artifact_save`` wrote the four characters ``None`` for a
  ``content: null`` — into the file, for a file-backed artifact;
* ``POST /api/file-write`` defaulted a missing ``content`` to ``""`` before validating it. Its
  validator refuses an empty required string, so nothing was written, but the refusal said the
  field was "empty after sanitization" when the request never sent it.

An absent field (or ``null``) now means "leave it alone": an artifact made from a file with no
text of its own starts as the file and never writes it, and each document writer refuses a save
that carries no text instead of emptying the document. A save that does carry text still writes
it, through the same stale-write check. Renaming, retagging and every other metadata edit of a
file-backed artifact leave the file untouched — pinned here as controls, since they already did.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ORIGINAL = "# Launch brief\n\nThe owner's own words, written before the artifact existed.\n"


def revision_of(document):
    from personalclaw.stale_write import revision_of as _revision_of

    return _revision_of(document)


def _based_on(revision: str | None) -> dict[str, str]:
    return {"If-Match": f'"{revision}"'} if revision is not None else {}


def _code(body: dict) -> str:
    return body["error"]["code"] if isinstance(body.get("error"), dict) else ""


def _message(body: dict) -> str:
    err = body.get("error")
    return err.get("message", "") if isinstance(err, dict) else str(err or "")


class _Untouched:
    """What "the file on disk is byte-identical, and nothing wrote it" means, measured.

    Bytes alone would pass a write of the same bytes; every write here is an atomic replace
    (``atomic_write`` / ``os.replace``), which gives the path a new inode. So the witness is the
    bytes AND the inode AND the mtime.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        st = path.stat()
        self.data = path.read_bytes()
        self.ino, self.mtime_ns = st.st_ino, st.st_mtime_ns

    def check(self) -> None:
        st = self.path.stat()
        assert self.path.read_bytes() == self.data, "the file's bytes changed"
        assert (st.st_ino, st.st_mtime_ns) == (self.ino, self.mtime_ns), "the file was rewritten"


# ── fixtures: the explorer's roots are tmp_path, and so are an artifact's places ─────────────


@pytest.fixture
def explorer(tmp_path):
    real_realpath = os.path.realpath
    roots = [("Test", str(tmp_path))]
    with (
        patch("os.path.expanduser", side_effect=lambda p: p.replace("~", str(tmp_path))),
        patch("os.path.realpath", side_effect=real_realpath),
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
    ):
        yield tmp_path


@pytest.fixture
def artifacts(tmp_path):
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(root=tmp_path / "artifact-store")
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


@pytest.fixture
def brief(explorer) -> Path:
    f = explorer / "brief.md"
    f.write_text(ORIGINAL, encoding="utf-8")
    return f


def _app() -> web.Application:
    from personalclaw.artifacts.handlers import register_artifact_routes
    from personalclaw.dashboard.handlers import api_file_read, api_file_write

    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    register_artifact_routes(app)
    return app


async def _read_file(c: TestClient, path: Path) -> tuple[str, str]:
    """What the viewer paints, and the revision its read reported (the ``file-read`` ETag)."""
    resp = await c.get("/api/file-read", params={"path": str(path)})
    assert resp.status == 200, await resp.text()
    etag = resp.headers.get("ETag")
    assert etag, "the read reported no revision"
    return await resp.text(), etag.strip('"')


async def _save_as_artifact(c: TestClient, path: Path, content: str, base: str):
    """Files → Save as artifact, exactly as ``api.saveFileAsArtifact`` sends it."""
    return await c.post(
        "/api/artifacts",
        json={
            "name": "Brief",
            "content": content,
            "source": "manual",
            "source_path": str(path),
            "kind": "markdown",
        },
        headers=_based_on(base),
    )


async def _create_without_content(c: TestClient, path: Path, base: str | None, **extra):
    return await c.post(
        "/api/artifacts",
        json={"name": "Brief", "kind": "markdown", "source_path": str(path), **extra},
        headers=_based_on(base),
    )


# ── POST /api/artifacts with no text: the reported data loss ────────────────────────────────


class TestACreateWithoutContent:
    @pytest.mark.asyncio
    async def test_it_leaves_the_file_alone_and_starts_as_the_file(
        self, explorer, artifacts, brief
    ) -> None:
        untouched = _Untouched(brief)
        async with TestClient(TestServer(_app())) as c:
            _text, base = await _read_file(c, brief)
            resp = await _create_without_content(c, brief, base)
            body = await resp.json()
        assert resp.status == 201, body
        untouched.check()
        assert body["content"] == ORIGINAL
        art = artifacts.get(body["slug"])
        assert art is not None and art.source_path == str(brief.resolve())
        assert art.content == ORIGINAL
        assert artifacts.get(body["slug"], version=1).content == ORIGINAL

    @pytest.mark.asyncio
    async def test_a_null_content_is_the_same_as_none(self, explorer, artifacts, brief) -> None:
        untouched = _Untouched(brief)
        async with TestClient(TestServer(_app())) as c:
            _text, base = await _read_file(c, brief)
            resp = await _create_without_content(c, brief, base, content=None)
            body = await resp.json()
        assert resp.status == 201, body
        untouched.check()
        assert body["content"] == ORIGINAL

    @pytest.mark.asyncio
    async def test_after_save_as_artifact_it_changes_nothing(
        self, explorer, artifacts, brief
    ) -> None:
        """The drive the ledger names: Save as artifact, then an API create with no text. The
        second request reaches the dedup branch, which used to write ``""`` through."""
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, brief)
            saved = await _save_as_artifact(c, brief, text, base)
            assert saved.status == 201, await saved.text()
            slug = (await saved.json())["slug"]
            before = artifacts.get(slug)
            untouched = _Untouched(brief)
            _text, base = await _read_file(c, brief)
            again = await _create_without_content(c, brief, base)
            body = await again.json()
        assert again.status == 200, body
        untouched.check()
        assert body["slug"] == slug and body["content"] == ORIGINAL
        after = artifacts.get(slug)
        assert (after.version, len(after.events)) == (before.version, len(before.events))
        assert artifacts.list_versions(slug) == [1]

    @pytest.mark.asyncio
    async def test_it_still_names_the_copy_it_read(self, explorer, artifacts, brief) -> None:
        """A pointer at an existing file is taken only by a caller who read it — the revision is
        what shows that — and the refusal says what is true of THIS request: nothing replaces the
        file, so nothing of it could have been undone."""
        untouched = _Untouched(brief)
        async with TestClient(TestServer(_app())) as c:
            unnamed = await _create_without_content(c, brief, None)
            unnamed_body = await unnamed.json()
            stale = await _create_without_content(c, brief, "0000000000000000")
            stale_body = await stale.json()
        assert unnamed.status == 428 and _code(unnamed_body) == "revision_required"
        assert "An artifact made from the file" in _message(unnamed_body)
        assert stale.status == 409 and _code(stale_body) == "stale_write"
        stale_sentence = _message(stale_body)
        assert "the file is unchanged" in stale_sentence, stale_sentence
        assert "replaces" not in stale_sentence and "undone" not in stale_sentence
        untouched.check()
        assert artifacts.list() == []

    @pytest.mark.asyncio
    async def test_a_file_that_is_not_there_yet_is_still_never_created(
        self, explorer, artifacts
    ) -> None:
        later = explorer / "later.md"
        async with TestClient(TestServer(_app())) as c:
            resp = await _create_without_content(c, later, None)
            body = await resp.json()
        assert resp.status == 201, body
        assert not later.exists()
        assert body["content"] == ""


class TestACreateWithContentStillWrites:
    """Controls: a save that carries text is the owner's edit, and it still lands — through the
    stale-write check."""

    @pytest.mark.asyncio
    async def test_save_as_artifact_writes_the_draft(self, explorer, artifacts, brief) -> None:
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, brief)
            resp = await _save_as_artifact(c, brief, text + "An edit.\n", base)
            assert resp.status == 201, await resp.text()
        assert brief.read_text(encoding="utf-8") == ORIGINAL + "An edit.\n"

    @pytest.mark.asyncio
    async def test_an_explicit_empty_body_is_an_edit_the_check_still_guards(
        self, explorer, artifacts, brief
    ) -> None:
        async with TestClient(TestServer(_app())) as c:
            _text, base = await _read_file(c, brief)
            stale = await _save_as_artifact(c, brief, "", "0000000000000000")
            assert stale.status == 409
            assert brief.read_text(encoding="utf-8") == ORIGINAL
            resp = await _save_as_artifact(c, brief, "", base)
            assert resp.status == 201, await resp.text()
        assert brief.read_text(encoding="utf-8") == ""


# ── the store: an absent body is never written through ─────────────────────────────────────


class TestTheStore:
    def test_create_with_no_content_reads_the_file_and_never_writes_it(
        self, explorer, artifacts, brief
    ) -> None:
        """The store is reached without the route too (the loop watchdog, a test's provider)."""
        untouched = _Untouched(brief)
        art = artifacts.create(name="Brief", content=None, kind="markdown", source_path=str(brief))
        untouched.check()
        assert art.content == ORIGINAL
        assert artifacts.get(art.slug).content == ORIGINAL
        assert artifacts.get(art.slug, version=1).content == ORIGINAL


# ── renaming, retagging, describing, snapshotting: controls, they never wrote ──────────────


class TestMetadataEditsLeaveTheFileAlone:
    @pytest.mark.parametrize(
        "patch_body",
        [
            {"name": "Launch brief, renamed"},
            {"add_tags": ["launch"]},
            {"remove_tags": ["launch"]},
            {"description": "What we are launching"},
            {"collection": "Q4"},
            {"snapshot": True},
            {"content": None, "name": "Nulled body, renamed"},
        ],
        ids=["rename", "add-tag", "remove-tag", "describe", "collection", "snapshot", "null-body"],
    )
    @pytest.mark.asyncio
    async def test_a_patch_without_content(self, explorer, artifacts, brief, patch_body) -> None:
        async with TestClient(TestServer(_app())) as c:
            # A file-backed artifact, made the way the owner makes one.
            text, base = await _read_file(c, brief)
            saved = await _save_as_artifact(c, brief, text, base)
            assert saved.status == 201, await saved.text()
            slug = (await saved.json())["slug"]
            untouched = _Untouched(brief)
            resp = await c.patch(f"/api/artifacts/{slug}", json=patch_body)
            assert resp.status == 200, await resp.text()
        untouched.check()
        assert artifacts.get(slug).content == ORIGINAL


# ── the agent's artifact tools ─────────────────────────────────────────────────────────────


@pytest.fixture
def agent_tools(artifacts):
    with patch("personalclaw.mcp_artifacts._resolve_session_key", return_value="dashboard:chat-1"):
        from personalclaw.mcp_artifacts import _call_tool

        yield _call_tool


class TestTheAgentsArtifactTools:
    def test_artifact_update_with_null_content_leaves_the_file_and_the_body(
        self, artifacts, brief, agent_tools
    ) -> None:
        art = artifacts.create(
            name="Brief", content=ORIGINAL, kind="markdown", source_path=str(brief)
        )
        untouched = _Untouched(brief)
        out = agent_tools(
            "artifact_update", {"slug": art.slug, "content": None, "description": "for launch"}
        )
        assert "Updated artifact" in out, out
        untouched.check()
        got = artifacts.get(art.slug)
        assert got.content == ORIGINAL and got.description == "for launch"

    def test_artifact_update_with_no_content_is_metadata_only(
        self, artifacts, brief, agent_tools
    ) -> None:
        """Control: an absent ``content`` already meant a metadata-only update."""
        art = artifacts.create(
            name="Brief", content=ORIGINAL, kind="markdown", source_path=str(brief)
        )
        untouched = _Untouched(brief)
        out = agent_tools("artifact_update", {"slug": art.slug, "tags": ["launch"]})
        assert "Updated artifact" in out, out
        untouched.check()
        assert artifacts.get(art.slug).tags == ["launch"]

    def test_artifact_save_with_null_content_is_refused(self, artifacts, agent_tools) -> None:
        out = agent_tools("artifact_save", {"name": "Notes", "content": None, "kind": "markdown"})
        assert "provide content or content_file" in out, out
        assert artifacts.list() == []


# ── the other writers of a whole file or document ──────────────────────────────────────────


class TestTheExplorersWrite:
    @pytest.mark.parametrize("body", [{}, {"content": None}], ids=["absent", "null"])
    @pytest.mark.asyncio
    async def test_a_write_without_content_is_refused_and_the_file_kept(
        self, explorer, brief, body
    ) -> None:
        """The refusal names what is missing. ``main`` refused an ABSENT ``content`` only because
        it defaulted it to ``""`` before validating, and so said it was "empty after
        sanitization" — of a field the request never sent."""
        untouched = _Untouched(brief)
        async with TestClient(TestServer(_app())) as c:
            _text, base = await _read_file(c, brief)
            resp = await c.post(
                "/api/file-write", json={"path": str(brief), **body}, headers=_based_on(base)
            )
            refusal = await resp.json()
        assert resp.status == 400, refusal
        assert _message(refusal) == "content: required", refusal
        untouched.check()


class TestTheAgentsWriteFile:
    @pytest.mark.parametrize("args", [{}, {"content": None}], ids=["absent", "null"])
    @pytest.mark.asyncio
    async def test_a_write_without_content_is_refused_and_the_file_kept(
        self, tmp_path, args
    ) -> None:
        from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

        f = tmp_path / "notes.md"
        f.write_text(ORIGINAL, encoding="utf-8")
        tools = NativeBuiltinToolProvider(cwd=tmp_path)
        # The read gate refuses a write over a file this turn never read, which would refuse this
        # call for a different reason and hide the one under test.
        read = await tools.invoke("read_file", {"path": "notes.md"})
        assert read.success, read.error
        untouched = _Untouched(f)
        result = await tools.invoke("write_file", {"path": "notes.md", **args})
        assert result.success is False, result
        assert (result.error or "").startswith("write_file needs content"), result.error
        untouched.check()

    @pytest.mark.asyncio
    async def test_a_write_with_content_still_writes(self, tmp_path) -> None:
        """Control."""
        from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

        f = tmp_path / "notes.md"
        f.write_text(ORIGINAL, encoding="utf-8")
        tools = NativeBuiltinToolProvider(cwd=tmp_path)
        assert (await tools.invoke("read_file", {"path": "notes.md"})).success
        result = await tools.invoke("write_file", {"path": "notes.md", "content": "new"})
        assert result.success, result.error
        assert f.read_text(encoding="utf-8") == "new"

    def test_the_chat_shows_no_change_for_a_write_without_content(self, tmp_path) -> None:
        """The file-change chip is taken from the call, before the tool runs: a call that writes
        nothing must not show the file emptied."""
        from personalclaw.dashboard import chat_runner as cr
        from personalclaw.dashboard.state import _ChatSession

        (tmp_path / "notes.md").write_text(ORIGINAL, encoding="utf-8")
        session = _ChatSession(key="t", workspace_dir=str(tmp_path))
        session._file_changes = []
        cr._capture_file_change(session, "write_file", {"path": "notes.md"})
        cr._capture_file_change(session, "write_file", {"path": "notes.md", "content": None})
        assert session._file_changes == []


@pytest.fixture
def memory(tmp_path):
    from personalclaw.memory import MemoryStore

    mem = MemoryStore(workspace=tmp_path / "workspace")
    mem.init()
    return mem


def _memory_app(mem) -> web.Application:
    from personalclaw.dashboard.handlers.memory import (
        api_memory_history,
        api_memory_preferences,
        api_memory_projects,
    )

    app = web.Application()
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(memory=mem))
    for which, handler in (
        ("preferences", api_memory_preferences),
        ("projects", api_memory_projects),
        ("history", api_memory_history),
    ):
        app.router.add_get(f"/api/memory/{which}", handler)
        app.router.add_put(f"/api/memory/{which}", handler)
    return app


class TestTheMemoryDocuments:
    @pytest.mark.parametrize("which", ["preferences", "projects", "history"])
    @pytest.mark.parametrize("body", [{}, {"content": None}], ids=["absent", "null"])
    @pytest.mark.asyncio
    async def test_a_save_without_content_is_refused_and_the_document_kept(
        self, memory, which, body
    ) -> None:
        async with TestClient(TestServer(_memory_app(memory))) as c:
            base = await (await c.get(f"/api/memory/{which}")).json()
            seeded = await c.put(
                f"/api/memory/{which}",
                json={"content": base["content"] + "\n- the owner's line"},
                headers=_based_on(base["revision"]),
            )
            assert seeded.status == 200, await seeded.text()
            kept = (await seeded.json())["content"]
            resp = await c.put(
                f"/api/memory/{which}", json=body, headers=_based_on(revision_of(kept))
            )
            refusal = await resp.json()
            stored = (await (await c.get(f"/api/memory/{which}")).json())["content"]
        assert resp.status == 400, refusal
        assert "content" in _message(refusal), refusal
        assert stored == kept and "the owner's line" in stored
