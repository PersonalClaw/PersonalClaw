"""An artifact's ``source_path`` stays inside the places it may point to.

A file-backed artifact is a live pointer: every read of it reads ``source_path`` and every save,
snapshot and revert writes it. Measured on ``main`` at 8350abebe, ``POST /api/artifacts`` took
``source_path`` from the body as written and the store's only gate was ``is_sensitive_path``. A
save naming the file's revision in ``If-Match``, as Save as artifact does, overwrote any existing
file the gateway could write, and every GET read it back:

    POST /api/artifacts {"source_path": "<home>/mcp.json", "content": "PWNED"}  -> 201
    <home>/mcp.json                                                            -> "PWNED"

The revision is a digest of the file's text, so knowing a file's content is enough, and a default
``mcp.json`` has known content. Without one the answer was ``428`` naming the file, and with a
wrong one ``409``: both say the file exists, and the pair confirms a guess at its content. A path
that did not exist yet was taken as it was (``201``), and read once the file appeared. The same
held for a file outside the workspace, a ``..`` path out of it, and the target of a symlink
planted in it. The places an artifact may point to are the ones the file explorer opens
(``file_roots``: the workspace and the home's work folders, never the home itself — #3675), plus
each loop's own folder, where an unbound loop keeps the deliverable its completion graduates.
Anything else is refused when it is set, and every read and write re-checks it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.file_view import read_head, whole_text
from personalclaw.stale_write import revision_of

ORIGINAL = "the file's own bytes"
POSTED = "what the request tried to write"


@pytest.fixture
def places(tmp_path, monkeypatch):
    """A home, a workspace outside it, and a folder that is neither."""
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    for d in (home, ws, outside):
        d.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    return home, ws, outside


@pytest.fixture
def provider(places):
    home, _ws, _outside = places
    prov = NativeArtifactProvider(root=home / "artifacts")
    with patch.object(registry, "get_provider", return_value=prov):
        yield prov


async def _client() -> TestClient:
    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _based_on(source_path: str) -> dict[str, str]:
    """The ``If-Match`` Save as artifact sends: the revision of the file as the explorer reads it.

    Anyone who knows a file's content can compute it, so every refusal below is measured against
    the most favourable request, the one that names the file's real revision.
    """
    path = Path(source_path)
    if not path.is_absolute() or not path.is_file():
        return {}
    return {"If-Match": revision_of(whole_text(read_head(str(path))))}


async def _post(source_path: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    client = await _client()
    try:
        resp = await client.post(
            "/api/artifacts",
            json={"name": "Doc", "content": POSTED, "kind": "markdown", "source_path": source_path},
            headers=_based_on(source_path) if headers is None else headers,
        )
        return resp.status, await resp.json()
    finally:
        await client.close()


def _refused(status: int, body: dict, raw: str) -> None:
    assert status == 400, body
    sentence = body.get("error", "")
    assert raw in sentence and "can't be an artifact's source" in sentence, sentence


def _loop(**kind_config):
    """A goal loop with no bound workspace, stored in the test home, and its own folder."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    loop = store.create(
        Loop(
            id="",
            name="Goal",
            kind="goal",
            task="write the report",
            kind_config={"goal_type": "open_ended", **kind_config},
        )
    )
    folder = loop_files.loop_dir(loop.id)
    assert folder is not None
    return loop, folder


# ── refused when it is set ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_file_outside_the_workspace_is_refused_and_left_alone(places, provider):
    _home, _ws, outside = places
    target = outside / "notes.md"
    target.write_text(ORIGINAL)
    status, body = await _post(str(target))
    _refused(status, body, str(target))
    assert target.read_text() == ORIGINAL
    assert provider.list() == []


@pytest.mark.asyncio
async def test_a_dotdot_path_out_of_the_workspace_is_refused(places, provider):
    _home, ws, outside = places
    target = outside / "notes.md"
    target.write_text(ORIGINAL)
    climbing = f"{ws}/../outside/notes.md"
    status, body = await _post(climbing)
    _refused(status, body, climbing)
    assert target.read_text() == ORIGINAL
    assert provider.list() == []


@pytest.mark.asyncio
async def test_a_symlink_in_the_workspace_that_leaves_it_is_refused(places, provider):
    _home, ws, outside = places
    target = outside / "notes.md"
    target.write_text(ORIGINAL)
    link = ws / "notes.md"
    link.symlink_to(target)
    status, body = await _post(str(link))
    _refused(status, body, str(link))
    assert target.read_text() == ORIGINAL
    assert provider.list() == []


@pytest.mark.asyncio
async def test_the_homes_own_settings_file_is_refused(places, provider):
    """The home is never a place an artifact points: ``config.json`` and ``mcp.json`` are plain
    files there, and writing one would bypass every refusal the config and MCP routes make."""
    home, _ws, _outside = places
    config = home / "config.json"
    config.write_text(json.dumps({"security": {"approval": "ask"}}))
    before = config.read_text()
    status, body = await _post(str(config))
    _refused(status, body, str(config))
    assert config.read_text() == before


def _repository(ws: Path) -> Path:
    """A repository in the workspace, laid out as git lays one out (no git is run to make it)."""
    repo = ws / "site"
    for folder in (repo / ".git" / "hooks", repo / ".git" / "objects", repo / ".git" / "refs"):
        folder.mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git" / "config").write_text(ORIGINAL)
    (repo / ".git" / "hooks" / "pre-commit").write_text(ORIGINAL)
    (repo / ".gitmodules").write_text(ORIGINAL)
    (repo / ".gitignore").write_text(ORIGINAL)
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [".git/config", ".git/hooks/pre-commit", ".gitmodules"])
async def test_gits_own_settings_and_hooks_are_refused(places, provider, target):
    """🔴 Before: a pointer at a repository's git settings or hook script in the workspace was
    taken, and every save of the artifact then wrote what the owner's own git runs."""
    _home, ws, _outside = places
    file = _repository(ws) / target
    status, body = await _post(str(file))
    _refused(status, body, str(file))
    assert "git's own settings or hook scripts" in body["error"]
    assert file.read_text() == ORIGINAL
    assert provider.list() == []


@pytest.mark.asyncio
async def test_an_ordinary_file_of_a_repository_is_a_live_pointer(places, provider):
    """The control: the repository's own files, its ignore rules among them, are sources."""
    _home, ws, _outside = places
    ignore = _repository(ws) / ".gitignore"
    status, body = await _post(str(ignore))
    assert status == 201, body
    assert ignore.read_text() == POSTED


def test_a_pointer_at_gits_settings_recorded_before_neither_reads_nor_writes_them(places, provider):
    """A pointer recorded before the rule held git's files reads its own copy and writes nothing
    there, however the save is made."""
    _home, ws, _outside = places
    config = _repository(ws) / ".git" / "config"
    art = provider.create(name="Doc", content="saved copy", kind="markdown")
    meta_path = provider.root / art.slug / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["source_path"] = str(config)
    meta_path.write_text(json.dumps(meta))

    assert provider.get(art.slug).content == "saved copy"
    provider.update(art.slug, content=POSTED, snapshot=True)
    assert config.read_text() == ORIGINAL


@pytest.mark.asyncio
async def test_a_path_that_does_not_exist_yet_is_refused(places, provider):
    """A pointer is a live read, so one taken now reads whatever file appears there later."""
    _home, _ws, outside = places
    later = outside / "later.md"
    status, body = await _post(str(later))
    _refused(status, body, str(later))
    later.write_text(ORIGINAL)
    assert provider.list() == []


@pytest.mark.asyncio
async def test_a_refused_path_is_refused_before_the_file_is_opened(places, provider):
    """The answer is the same whatever the file holds, so it says nothing about the file: no
    ``428`` for a file that exists, no ``409`` for a revision that does not match it."""
    _home, _ws, outside = places
    target = outside / "notes.md"
    target.write_text(ORIGINAL)
    for headers in ({}, {"If-Match": "0000000000000000"}, _based_on(str(target))):
        status, body = await _post(str(target), headers)
        _refused(status, body, str(target))
    assert target.read_text() == ORIGINAL


@pytest.mark.asyncio
async def test_a_relative_path_is_refused(places, provider):
    status, body = await _post("notes/doc.md")
    _refused(status, body, "notes/doc.md")
    assert provider.list() == []


def test_the_store_refuses_it_too(places, provider):
    """The route is not the only writer (the loop watchdog calls the store directly), so the
    store refuses on its own rather than trusting its caller."""
    _home, _ws, outside = places
    target = outside / "notes.md"
    target.write_text(ORIGINAL)
    with pytest.raises(ValueError, match="can't be an artifact's source"):
        provider.create(name="Doc", content=POSTED, kind="markdown", source_path=str(target))
    assert target.read_text() == ORIGINAL
    art = provider.create(name="Plain", content="body", kind="markdown")
    with pytest.raises(ValueError, match="can't be an artifact's source"):
        provider.update(art.slug, content=POSTED, source_path=str(target))
    assert target.read_text() == ORIGINAL
    assert provider.get(art.slug).source_path == ""


def test_an_app_cannot_point_an_artifact_into_a_loops_folder(places, provider):
    """A loop's folder holds the brief its worker reads every cycle. Steering a loop is the
    owner's, so an app's request gets only the folders the file explorer shows it."""
    from personalclaw.apps.permissions import scoped_to_app

    _loop_row, folder = _loop()
    brief = folder / "brief.md"
    brief.write_text(ORIGINAL)
    with scoped_to_app("probe-app"):
        with pytest.raises(ValueError, match="An app can point an artifact only at"):
            provider.create(name="Brief", content=POSTED, kind="markdown", source_path=str(brief))
    assert brief.read_text() == ORIGINAL


# ── admitted where it belongs ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_file_in_the_workspace_is_a_live_pointer(places, provider):
    _home, ws, _outside = places
    doc = ws / "brief.md"
    doc.write_text(ORIGINAL)
    status, body = await _post(str(doc))
    assert status == 201, body
    assert body["source_path"] == os.path.realpath(doc)
    assert doc.read_text() == POSTED
    doc.write_text("edited in the editor")
    assert provider.get(body["slug"]).content == "edited in the editor"


@pytest.mark.parametrize(
    "how", ["the default one", "named in the home", "named outside it", "the one setup saved"]
)
def test_a_workspace_file_is_a_source_however_the_workspace_was_chosen(
    places, provider, monkeypatch, how
):
    """The workspace is wherever the owner chose it, the default one included, which is made the
    first time something asks for it. A file in it is a live pointer in each case."""
    from personalclaw.config.loader import workspace_root

    home, ws, _outside = places
    monkeypatch.delenv("PERSONALCLAW_WORKSPACE")
    chosen = {
        "the default one": home / "workspace",
        "named in the home": home / "ws",
        "named outside it": ws,
        "the one setup saved": ws.parent / "saved",
    }[how]
    if how.startswith("named"):
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(chosen))
    elif how == "the one setup saved":
        (home / "workspace_dir").write_text(f"{chosen}\n", encoding="utf-8")
    assert os.path.realpath(workspace_root()) == os.path.realpath(chosen), "vacuity floor"
    doc = chosen / "notes" / "doc.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(ORIGINAL)
    art = provider.create(name="Doc", content=ORIGINAL, kind="markdown", source_path=str(doc))
    assert art.source_path == os.path.realpath(doc)
    doc.write_text("edited in the editor")
    assert provider.get(art.slug).content == "edited in the editor"


def test_a_workspace_that_is_the_home_opens_none_of_it(places, provider, monkeypatch):
    """The home itself is never a place an artifact points, and naming it the workspace does not
    make it one: ``config.json`` and ``mcp.json`` are plain files there."""
    home, _ws, _outside = places
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(home))
    doc = home / "notes" / "doc.md"
    doc.parent.mkdir()
    doc.write_text(ORIGINAL)
    with pytest.raises(ValueError, match="can't be an artifact's source"):
        provider.create(name="Doc", content=POSTED, kind="markdown", source_path=str(doc))
    assert doc.read_text() == ORIGINAL


def test_a_loops_own_folder_is_a_place_the_owner_may_point_at(places, provider):
    """An unbound goal loop keeps REPORT.md in its own folder, and its completion graduates that
    file as a live pointer (``loop/watchdog._register_deliverable_artifact``)."""
    _loop_row, folder = _loop()
    report = folder / "REPORT.md"
    report.write_text(ORIGINAL)
    art = provider.create(name="Report", content=ORIGINAL, kind="markdown", source_path=str(report))
    report.write_text("the finished report")
    assert provider.get(art.slug).content == "the finished report"


# ── re-checked on every read and write ──────────────────────────────────────────────────────


def test_a_pointer_whose_file_became_a_symlink_out_is_neither_read_nor_written(places, provider):
    _home, ws, outside = places
    doc = ws / "brief.md"
    doc.write_text("saved copy")
    art = provider.create(name="Doc", content="saved copy", kind="markdown", source_path=str(doc))
    target = outside / "private.md"
    target.write_text(ORIGINAL)
    doc.unlink()
    doc.symlink_to(target)

    assert provider.get(art.slug).content == "saved copy"
    provider.update(art.slug, content=POSTED, snapshot=True)
    assert target.read_text() == ORIGINAL
    provider.revert(art.slug, 1)
    assert target.read_text() == ORIGINAL


def test_an_artifact_saved_before_the_rule_neither_reads_nor_writes_outside(places, provider):
    """A pointer recorded before this check existed (or written into meta.json by hand)."""
    _home, _ws, outside = places
    target = outside / "private.md"
    target.write_text(ORIGINAL)
    art = provider.create(name="Doc", content="saved copy", kind="markdown")
    meta_path = provider.root / art.slug / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["source_path"] = str(target)
    meta_path.write_text(json.dumps(meta))

    assert provider.get(art.slug).content == "saved copy"
    provider.update(art.slug, content=POSTED)
    assert target.read_text() == ORIGINAL


def test_a_loop_deliverable_named_out_of_its_folder_is_not_graduated(places, provider):
    """The watchdog's pointer comes from the loop's ``primary_deliverable``, a name the owner
    types. One that climbs out of the loop's folder reaches the store, which refuses it."""
    from personalclaw.loop.watchdog import LoopWatchdog

    _home, _ws, outside = places
    target = outside / "notes.md"
    target.write_text(ORIGINAL)
    climbing = "../../../outside/notes.md"
    loop, folder = _loop(primary_deliverable=climbing)
    assert (folder / climbing).resolve() == target.resolve()

    LoopWatchdog(MagicMock(), MagicMock())._register_deliverable_artifact(loop.id)

    assert provider.list() == []
    assert target.read_text() == ORIGINAL


def test_a_body_search_resolves_the_places_once(places, provider, monkeypatch):
    """Every body read re-checks its pointer, so a search re-checks one per artifact. The
    places are resolved once per search, not once per row: resolving them walks every loop
    and project."""
    from personalclaw.artifacts import source_files

    _home, ws, _outside = places
    for i in range(3):
        doc = ws / f"doc{i}.md"
        doc.write_text(f"quarterly body {i}")
        provider.create(
            name=f"Doc {i}", content=f"quarterly body {i}", kind="markdown", source_path=str(doc)
        )
    calls: list[int] = []
    real = source_files.places
    monkeypatch.setattr(source_files, "places", lambda: calls.append(1) or real())
    assert len(provider.list(q="quarterly")) == 3
    assert len(calls) == 1
