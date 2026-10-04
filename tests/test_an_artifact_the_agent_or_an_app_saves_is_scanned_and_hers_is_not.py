"""An artifact the agent, an app or a workflow saves is scanned before it is kept; hers is not.

An artifact's text was kept as its writer sent it: the agent's ``artifact_save`` and
``artifact_update`` (often text it read on the web), an app's request, a workflow's publish, its
artifact-update step and its render-report step. Knowledge's mirror of the artifact library then
made that text searchable and recalled it into prompts, though nothing had scanned it. Now the
text Knowledge keeps of an artifact (its name, its description and its body's words) is read by
the content scan, by the rules an upload's text is read by, before anything is written. Each of
these doors has someone to answer, so a refusal answers that one in the scan's words, and nothing
is made or changed, so the mirror never holds it.

What the owner writes herself in the app is her own words, as what she writes in the chat is: an
artifact she saves or edits is kept as she wrote it. An artifact that points at a file holds the
file's text, which any program on the machine can write, so the mirror scans that text before it
keeps it, whoever saved the artifact; what the scan refuses, or could not check, is a mirror that
keeps no text and says why.

Each test drives the real artifact store, the real knowledge store and the real content scan. The
refused text is the scanner's own example of content it refuses: a shopping list with an invisible
right-to-left override character in it. The same list without it is ordinary content, and each
refusal sits beside it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps.permissions import scoped_to_app
from personalclaw.artifacts import changes
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.knowledge.artifact_ingest import ArtifactIndexer, find_source
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.sel import sel
from personalclaw.stale_write import revision_of
from personalclaw.uploads import content_scan

#: The scanner's own example of content it refuses: a list with a right-to-left override in it.
OVERRIDE_LIST = "Shopping list for Saturday: \u202eeggs\u202c, flour, apples."
#: The same list without the override: ordinary content.
CLEAN_LIST = "Shopping list for Saturday: eggs, flour, apples."
#: What a new artifact whose text the content scan refused says.
REFUSED = "Its text failed the content safety scan, so nothing was made from it."
#: What an edit whose new text the content scan refused says.
NOT_CHANGED = "Its text failed the content safety scan, so the item was not changed."
#: What a new artifact whose text the content scan could not check says.
UNCHECKED = "Its text could not be checked, so nothing was made from it."


@pytest.fixture(autouse=True)
def _no_stray_listeners():
    """Every test starts and ends with no artifact listener: the change seam is module state."""
    before = list(changes._listeners)
    changes._listeners.clear()
    yield
    changes._listeners.clear()
    changes._listeners.extend(before)


@pytest.fixture
def places(tmp_path, monkeypatch):
    """A home and a workspace, the folder a file-backed artifact may point into."""
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    return home, ws


@pytest.fixture
def artifacts(places):
    home, _ws = places
    provider = NativeArtifactProvider(root=home / "artifacts")
    with (
        patch("personalclaw.artifacts.registry.get_provider", return_value=provider),
        patch("personalclaw.mcp_artifacts._resolve_session_key", return_value="dashboard:chat-1"),
    ):
        yield provider


@pytest.fixture
def library(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


@pytest.fixture
def mirror(library, artifacts) -> ArtifactIndexer:
    """The gateway's artifact mirror, following every write to the store."""

    class _On:
        auto_ingest_artifacts = True

    indexer = ArtifactIndexer(
        library, enqueue=lambda _id: None, provider_factory=lambda: artifacts, config_loader=_On
    )
    changes.subscribe(indexer.listener)
    return indexer


def _mirrored(library: KnowledgeStore, slug: str) -> dict | None:
    source = find_source(library)
    return library.find_source_item(str(source["id"]), slug) if source else None


def _found(library: KnowledgeStore, word: str) -> list[str]:
    """What the library's own search finds for *word*: the titles a user sees."""
    return [row["title"] for row in library.search_items_fts(word, limit=50)]


def _scan_rows() -> list[tuple[str, str]]:
    return [
        (r["caller_identity"], r["outcome"])
        for r in sel().recent(limit=200)
        if r.get("operation") == "upload_scan"
    ]


# ── the agent's artifact tools ───────────────────────────────────────────────
#
# A tool call's string arguments are stripped of hidden characters before the tool runs
# (``validation.strip_hidden_unicode``), so the agent hands the override in the way it reaches an
# artifact: in a file it saves with ``content_file`` (a page it downloaded, say), or as the JSON
# escape a sheet's rows carry.


def _tool(name: str, args: dict) -> str:
    from personalclaw.mcp_artifacts import _call_tool

    return _call_tool(name, args)


def _file(places, text: str, name: str = "page.txt") -> str:
    _home, ws = places
    path = ws / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    "kind, body",
    [
        ("markdown", OVERRIDE_LIST),
        ("text", OVERRIDE_LIST),
        ("html", f"<html><body><p>{OVERRIDE_LIST}</p></body></html>"),
        ("document", f"<article><h1>Errands</h1><p>{OVERRIDE_LIST}</p></article>"),
        ("json", json.dumps({"list": OVERRIDE_LIST}, ensure_ascii=False)),
    ],
)
def test_an_artifact_the_agent_saves_is_scanned_before_it_is_kept(
    places, artifacts, library, mirror, kind, body
):
    saving = {"name": "Saturday", "kind": kind}

    said = _tool("artifact_save", {**saving, "content_file": _file(places, body)})

    assert said == f"Error: {REFUSED}"
    assert artifacts.list() == []
    assert _mirrored(library, "saturday") is None
    assert _scan_rows() == [("uploads.content_scan:artifact", "rejected")]

    clean = body.replace("\u202e", "").replace("\u202c", "")
    saved = _tool("artifact_save", {**saving, "content_file": _file(places, clean)})
    assert "slug: saturday" in saved
    assert "Saturday" in _found(library, "flour")


def test_an_edit_the_agent_makes_is_scanned_before_it_is_kept(places, artifacts, library, mirror):
    _tool("artifact_save", {"name": "Errands", "content": CLEAN_LIST, "kind": "markdown"})

    said = _tool(
        "artifact_update", {"slug": "errands", "content_file": _file(places, OVERRIDE_LIST)}
    )

    assert said == f"Error: {NOT_CHANGED}"
    assert artifacts.get("errands").content == CLEAN_LIST
    assert artifacts.get("errands").version == 1
    assert _mirrored(library, "errands")["content"] == CLEAN_LIST

    edited = _tool("artifact_update", {"slug": "errands", "content": CLEAN_LIST + " And seeds."})
    assert "version 2" in edited
    assert _mirrored(library, "errands")["content"].endswith("And seeds.")


def test_a_csv_the_agent_makes_is_scanned_before_it_is_kept(artifacts, library, mirror):
    def rows(text: str) -> str:
        return json.dumps([["item"], [text]])  # the override travels as its JSON escape

    said = _tool("sheet_create", {"name": "Errands", "rows": rows(OVERRIDE_LIST), "format": "csv"})

    assert said == f"Error: {REFUSED}"
    assert artifacts.list() == []

    made = _tool("sheet_create", {"name": "Errands", "rows": rows(CLEAN_LIST), "format": "csv"})
    assert made.startswith("Created csv: errands")
    assert "Errands" in _found(library, "flour")


def test_a_save_the_scan_could_not_check_keeps_nothing(artifacts, library, mirror):
    """A check that did not run is never read as a pass."""
    with patch.object(content_scan, "scan_argv", return_value=["/nonexistent/pc-content-scan"]):
        said = _tool("artifact_save", {"name": "Errands", "content": CLEAN_LIST, "kind": "text"})

    assert said == f"Error: {UNCHECKED}"
    assert artifacts.list() == []
    assert _scan_rows() == [("uploads.content_scan:artifact", "error")]


# ── an app's requests, and hers ──────────────────────────────────────────────


def _request(method: str, body: dict, *, slug: str = "", base: str = "") -> web.Request:
    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    headers = {"If-Match": f'"{base}"'} if base else {}
    req = make_mocked_request(
        method, "/api/artifacts", app=app, match_info={"slug": slug}, headers=headers
    )

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return req


async def _save(body: dict, *, as_app: str = "") -> tuple[int, dict]:
    from personalclaw.artifacts.handlers import api_artifacts_create

    with scoped_to_app(as_app):
        resp = await api_artifacts_create(_request("POST", body))
    return resp.status, json.loads(resp.body)


async def _edit(slug: str, body: dict, base: str, *, as_app: str = "") -> tuple[int, dict]:
    from personalclaw.artifacts.handlers import api_artifact_update

    with scoped_to_app(as_app):
        resp = await api_artifact_update(_request("PATCH", body, slug=slug, base=base))
    return resp.status, json.loads(resp.body)


@pytest.mark.asyncio
async def test_an_apps_artifact_the_scan_refuses_is_refused_and_hers_is_kept(
    artifacts, library, mirror
):
    note = {"name": "Saturday", "content": OVERRIDE_LIST, "kind": "markdown"}

    status, said = await _save(note, as_app="growth")

    assert status == 422
    assert said["error"] == {"code": "upload_content_refused", "message": REFUSED}
    assert artifacts.list() == []
    assert _mirrored(library, "saturday") is None
    assert _scan_rows() == [("uploads.content_scan:artifact", "rejected")]

    status, made = await _save({**note, "content": CLEAN_LIST}, as_app="growth")
    assert status == 201 and made["content"] == CLEAN_LIST
    assert "Saturday" in _found(library, "flour")

    # Her own words, as in the chat: the composer never scans what she writes, and neither does
    # the artifact she saves.
    status, hers = await _save({**note, "name": "Sunday"})
    assert status == 201 and hers["content"] == OVERRIDE_LIST
    assert _mirrored(library, hers["slug"])["content"] == OVERRIDE_LIST
    assert _scan_rows() == [("uploads.content_scan:artifact", "rejected")]


@pytest.mark.asyncio
async def test_an_apps_edit_the_scan_refuses_changes_nothing_and_hers_is_kept(
    artifacts, library, mirror
):
    art = artifacts.create(name="Errands", content=CLEAN_LIST, kind="markdown", actor="user")
    base = revision_of(CLEAN_LIST)

    for change in ({"content": OVERRIDE_LIST}, {"name": OVERRIDE_LIST}):
        status, said = await _edit(art.slug, change, base, as_app="growth")
        assert status == 422
        assert said["error"] == {"code": "upload_content_refused", "message": NOT_CHANGED}
    assert artifacts.get(art.slug).content == CLEAN_LIST
    assert artifacts.get(art.slug).name == "Errands"
    assert _mirrored(library, art.slug)["content"] == CLEAN_LIST

    status, edited = await _edit(art.slug, {"content": CLEAN_LIST + " Seeds."}, base, as_app="g")
    assert status == 200 and edited["content"] == CLEAN_LIST + " Seeds."

    status, hers = await _edit(art.slug, {"content": OVERRIDE_LIST}, edited["content_revision"])
    assert status == 200 and hers["content"] == OVERRIDE_LIST


# ── a workflow's steps ───────────────────────────────────────────────────────


def _ctx():
    from personalclaw.action_providers.base import ActionContext

    return ActionContext(event="workflow_node", payload={})


@pytest.mark.asyncio
async def test_an_artifact_a_workflow_step_writes_is_scanned_before_it_is_written(
    artifacts, library, mirror
):
    from personalclaw.action_providers.artifact_update_provider import (
        ArtifactUpdateActionProvider,
    )

    async def write(content: str):
        return await ArtifactUpdateActionProvider().execute(
            {"slug": "market-board", "content": content, "kind": "document"}, _ctx()
        )

    refused = await write(OVERRIDE_LIST)

    assert not refused.success and refused.error == REFUSED
    assert artifacts.get("market-board") is None

    written = await write(CLEAN_LIST)
    assert written.success, written.error
    refused = await write(OVERRIDE_LIST)
    assert not refused.success and refused.error == NOT_CHANGED
    assert artifacts.get("market-board").content == CLEAN_LIST


@pytest.mark.asyncio
async def test_a_workflows_publish_is_scanned_before_it_is_published(artifacts, library, mirror):
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch
    from personalclaw.workflows.models import Node

    def node(text: str) -> Node:
        return Node.from_dict(
            {"kind": "transform", "id": "t", "config": {"expr": text, "publish": "Garden notes"}}
        )

    refused = await dispatch(node(OVERRIDE_LIST), BindingContext())

    assert refused.published == {"action": "error", "reason": REFUSED}
    assert artifacts.list() == []

    published = await dispatch(node(CLEAN_LIST), BindingContext())
    assert published.published["action"] == "create"
    assert artifacts.get(published.published["slug"]).content == CLEAN_LIST
    assert "Garden notes" in _found(library, "flour")


@pytest.mark.asyncio
async def test_a_report_a_workflow_renders_is_scanned_before_either_artifact_is_written(
    artifacts, library, mirror
):
    from personalclaw.action_providers.knowledge_render_provider import (
        KnowledgeRenderReportActionProvider,
    )

    async def render(text: str):
        spec = {"title": "Market brief", "blocks": [{"type": "markdown", "text": text}]}
        return await KnowledgeRenderReportActionProvider().execute(
            {"slug": "market-brief", "spec": spec}, _ctx()
        )

    refused = await render(OVERRIDE_LIST)

    assert not refused.success and refused.error == REFUSED
    assert artifacts.list() == []

    rendered = await render(CLEAN_LIST)
    assert rendered.success, rendered.error
    assert {a.slug for a in artifacts.list()} == {"market-brief", "market-brief-report"}


# ── an artifact that points at a file ────────────────────────────────────────


def test_the_text_of_a_file_an_artifact_points_at_is_scanned_before_knowledge_keeps_it(
    places, artifacts, library, mirror, caplog
):
    """A file any program can write is read as a file is, whoever saved the artifact: her own
    file-backed artifact too, since what it holds is the file's text."""
    _home, ws = places
    plan = ws / "plan.md"
    plan.write_text(CLEAN_LIST, encoding="utf-8")
    art = artifacts.create(name="Plan", kind="markdown", source_path=str(plan), actor="user")
    assert _mirrored(library, art.slug)["content"] == CLEAN_LIST

    plan.write_text(OVERRIDE_LIST, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.artifact_ingest"):
        artifacts.update(art.slug, name="Spring plan", actor="user")

    held = _mirrored(library, art.slug)
    assert (held["processing_status"], held["processing_error"]) == ("failed", REFUSED)
    assert (held["title"], held["content"], held["summary"]) == (art.slug, "", "")
    assert _found(library, "flour") == []
    assert ("uploads.content_scan:artifact_file", "rejected") in _scan_rows()
    assert f"artifact {art.slug}: the text of the file it points at was not kept" in caplog.text
    # The artifact itself is the file, as it always was: only Knowledge's copy is withheld.
    assert artifacts.get(art.slug).content == OVERRIDE_LIST

    plan.write_text(CLEAN_LIST + " And seeds.", encoding="utf-8")
    artifacts.update(art.slug, name="Plan", actor="user")
    assert _mirrored(library, art.slug)["content"] == CLEAN_LIST + " And seeds."
    assert _mirrored(library, art.slug)["processing_error"] is None


def test_a_file_the_scan_could_not_check_is_read_again(places, artifacts, library, mirror):
    _home, ws = places
    plan = ws / "plan.md"
    plan.write_text(CLEAN_LIST, encoding="utf-8")
    with patch.object(content_scan, "scan_argv", return_value=["/nonexistent/pc-content-scan"]):
        art = artifacts.create(name="Plan", kind="markdown", source_path=str(plan), actor="user")

    held = _mirrored(library, art.slug)
    assert (held["processing_status"], held["processing_error"]) == ("failed", UNCHECKED)
    assert held["content"] == ""

    assert mirror.index(art.slug) == ArtifactIndexer.INDEXED
    assert _mirrored(library, art.slug)["content"] == CLEAN_LIST


@pytest.mark.asyncio
async def test_a_file_backed_artifact_saved_on_the_event_loop_is_scanned_in_a_task(
    places, artifacts, library, mirror
):
    """The scan is a child process: on the event loop it runs beside the save, never inside it,
    and the mirror is written once it answers."""
    _home, ws = places
    plan = ws / "plan.md"
    plan.write_text(OVERRIDE_LIST, encoding="utf-8")

    art = artifacts.create(name="Plan", kind="markdown", source_path=str(plan), actor="user")

    held = await _settled(library, art.slug)
    assert (held["processing_status"], held["processing_error"]) == ("failed", REFUSED)
    assert held["content"] == ""

    plan.write_text(CLEAN_LIST, encoding="utf-8")
    artifacts.update(art.slug, name="Spring plan", actor="user")
    held = await _settled(library, art.slug, until=lambda row: row["content"] == CLEAN_LIST)
    assert held["title"] == "Spring plan"


async def _settled(library, slug, *, until=lambda row: True, within: float = 60.0) -> dict:
    """The mirror row of *slug* once it exists and satisfies *until*, waiting on the scan."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while loop.time() < deadline:
        row = _mirrored(library, slug)
        if row is not None and until(row):
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"the mirror of {slug} never settled: {_mirrored(library, slug)}")
