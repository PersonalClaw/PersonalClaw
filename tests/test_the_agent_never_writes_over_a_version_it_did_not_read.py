"""The agent never replaces a change someone made to a document behind its back.

The agent wrote a document's next version blind. ``artifact_get`` handed it a Word document, a
spreadsheet or a deck as the address of its file, never its text, and every write over an existing
artifact (``artifact_update``, ``artifact_save`` on its slug, and ``document_create``,
``sheet_create`` and ``deck_create`` naming it by slug or by name) replaced the live version with
whatever the agent sent. An edit the owner saved in the Artifacts editor after the agent last saw
the document was gone without a word, and an edit saved without a new version (a plain Save) was
in no version at all.

Now ``artifact_get`` returns the text of every document kind, in the markup its tool writes it
from, part by part when it is long, fenced as data, with its version and its base. Every write over
an existing artifact names the base of the version it was made from. A base older than the live
version is refused with a sentence naming both versions and who made the newer one, a write with no
base is refused with the read to make first, and nothing is written either way; a new artifact
needs no base. A workflow's step writes what its run made, from no copy of the artifact, so it names
no base: the store reads the live version itself, inside the write. And whoever writes over text
that no version holds (her plain Save, an app's), the store first keeps it as a version of its own,
credited to whoever made it: a current base proves the agent read her edit, not that what it wrote
kept it. Only her own save from her editor replaces what the editor showed her as it is.

Each test drives the real artifact store, the real tools and, for the owner's own edits, the real
routes the Artifacts editor saves through.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps.permissions import scoped_to_app
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.stale_write import revision_of

CHAT = "dashboard:chat-1"
OTHER_CHAT = "dashboard:chat-2"

#: The owner's text, as she typed it into the editor.
HERS = "# Launch brief\n\nShip on Friday.\nThe owner's own line.\n"


@pytest.fixture
def store(tmp_path):
    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    with (
        patch("personalclaw.artifacts.registry.get_provider", return_value=provider),
        patch("personalclaw.mcp_artifacts._resolve_session_key", return_value=CHAT),
    ):
        yield provider


def _tool(name: str, args: dict, *, chat: str = CHAT) -> str:
    from personalclaw.mcp_artifacts import _call_tool

    with patch("personalclaw.mcp_artifacts._resolve_session_key", return_value=chat):
        return _call_tool(name, args)


def _base(reply: str) -> str:
    found = re.search(r"\bbase:? '?(v\d+-[0-9a-f]{16})", reply, re.IGNORECASE)
    assert found, reply
    return found.group(1)


def _request(method: str, body: dict, *, slug: str, if_match: str) -> web.Request:
    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    req = make_mocked_request(
        method,
        f"/api/artifacts/{slug}",
        app=app,
        match_info={"slug": slug},
        headers={"If-Match": f'"{if_match}"'},
    )

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return req


async def _editor_save(slug: str, text: str, *, snapshot: bool = False, app: str = "") -> dict:
    """A save in the Artifacts editor (or, with *app*, an app's PATCH): the whole body, over the
    revision the page read, as ``api.saveArtifactBody`` / ``snapshotArtifactBody`` send it."""
    from personalclaw.artifacts.handlers import api_artifact_detail, api_artifact_update

    with scoped_to_app(app):
        read = await api_artifact_detail(_request("GET", {}, slug=slug, if_match=""))
        shown = json.loads(read.body)
        resp = await api_artifact_update(
            _request(
                "PATCH",
                {"content": text, "snapshot": snapshot},
                slug=slug,
                if_match=shown["content_revision"],
            )
        )
    assert resp.status == 200, resp.body
    return json.loads(resp.body)


def _docx_text(store, slug: str) -> str:
    """The text of the live version of the docx *slug*, read from its bytes."""
    from personalclaw.documents.docx_parser import parse_docx

    data, _mime = store.raw_bytes(slug)
    model = parse_docx(data)[0]
    return "\n".join([model.title, *(block.text for block in model.blocks)])


# ── a text artifact: the owner's edit survives the agent's write ─────────────────────────────


@pytest.mark.asyncio
async def test_the_agents_write_over_an_edit_the_owner_saved_is_refused_and_hers_survives(store):
    """🔴 Red before: the agent's ``artifact_update`` replaced the owner's plain Save, which no
    version held, so her line was gone; a ``base`` was a field the tool did not take."""
    saved = _tool(
        "artifact_save",
        {
            "name": "Launch brief",
            "content": "# Launch brief\n\nShip on Friday.\n",
            "kind": "markdown",
        },
    )
    read = _tool("artifact_get", {"slug": "launch-brief"})
    assert "version 1" in read and "Ship on Friday." in read
    base = _base(read)
    assert _base(saved) == base

    await _editor_save("launch-brief", HERS)  # a plain Save: still version 1

    refused = _tool(
        "artifact_update",
        {"slug": "launch-brief", "content": "# Launch brief\n\nShip Monday.\n", "base": base},
    )
    assert refused.startswith(
        "Error: 'Launch brief' changed after you read it, so nothing was "
        "written: you read version 1, and the owner changed it since"
    ), refused
    assert "still version 1" in refused
    art = store.get("launch-brief")
    assert art.content == HERS
    assert art.version == 1


@pytest.mark.asyncio
async def test_a_snapshot_the_owner_cut_after_the_read_is_named_as_the_newer_version(store):
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    base = _base(_tool("artifact_get", {"slug": "notes"}))

    await _editor_save("notes", "hers, as a version", snapshot=True)

    refused = _tool("artifact_update", {"slug": "notes", "content": "the agent's", "base": base})
    assert "you read version 1, and it is now version 2, made by the owner" in refused, refused
    assert store.get("notes").content == "hers, as a version"


@pytest.mark.asyncio
async def test_a_write_that_drops_an_edit_the_agent_read_keeps_her_text_as_her_version(store):
    """🔴 Red before: an edit the owner saved without a new version lived only in the live body,
    so the agent's next version, made from its older copy, erased it from every version. A current
    base proves the agent read her edit, not that what it wrote kept it, and a chat's Trust can
    approve the write with nobody looking: her text is kept as a version of hers first."""
    _tool(
        "artifact_save",
        {
            "name": "Launch brief",
            "content": "# Launch brief\n\nShip on Friday.\n",
            "kind": "markdown",
        },
    )
    await _editor_save("launch-brief", HERS)  # a plain Save: still version 1
    base = _base(_tool("artifact_get", {"slug": "launch-brief"}))

    done = _tool(
        "artifact_update",
        {"slug": "launch-brief", "content": "# Launch brief\n\nShip Monday.\n", "base": base},
    )

    assert done.startswith("Updated artifact 'Launch brief' → version 3"), done
    assert done.endswith(
        "Version 2 keeps what the owner saved without a new version, as it was before this write."
    ), done
    assert store.get("launch-brief", version=2).content == HERS
    assert store.get("launch-brief").content.rstrip("\n") == "# Launch brief\n\nShip Monday."
    assert [(e.type, e.by, e.version) for e in store.get("launch-brief").events[-2:]] == [
        ("edited", "user", 2),
        ("iterated", "agent", 3),
    ]


@pytest.mark.asyncio
async def test_an_apps_save_over_an_edit_of_hers_keeps_hers_as_a_version(store):
    """🔴 Red before: an app's save replaced her plain Save, which no version held."""
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    await _editor_save("notes", "hers")

    await _editor_save("notes", "the app's text", app="weekly-digest")

    art = store.get("notes")
    assert art.content == "the app's text" and art.version == 2
    assert store.get("notes", version=2).content == "hers"
    assert [(e.type, e.by, e.version) for e in art.events[-2:]] == [
        ("edited", "user", 2),
        ("edited", "app:weekly-digest", 2),
    ]


@pytest.mark.asyncio
async def test_her_own_save_over_an_apps_edit_keeps_the_version_number(store):
    """The control: her Save names the revision her editor showed her and replaces that text as
    it is, so a plain Save never turns into a new version."""
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    await _editor_save("notes", "the app's text", app="weekly-digest")

    saved = await _editor_save("notes", "hers")

    assert saved["version"] == 1
    assert store.get("notes").content == "hers"
    assert store.get("notes", version=2) is None


def test_a_write_with_the_current_base_lands_as_the_next_version(store):
    """The control: a base of the version that is live writes its next version."""
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    base = _base(_tool("artifact_get", {"slug": "notes"}))

    done = _tool("artifact_update", {"slug": "notes", "content": "second", "base": base})

    assert done.startswith("Updated artifact 'Notes' → version 2"), done
    assert store.get("notes").content == "second"
    # Its reply names the new base, so a next change needs no second read.
    again = _tool("artifact_update", {"slug": "notes", "content": "third", "base": _base(done)})
    assert again.startswith("Updated artifact 'Notes' → version 3"), again


def test_a_write_with_no_base_is_refused_with_the_read_to_make_first(store):
    """🔴 Red before: a write with no base replaced the artifact."""
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})

    refused = _tool("artifact_update", {"slug": "notes", "content": "blind"})

    assert refused == (
        "Error: 'Notes' (slug: notes) already exists, and this writes over its whole text, so it "
        "takes the base of the version you read: call artifact_get with slug='notes', make your "
        "change to what it returns, and call artifact_update again with the base that read names. "
        "Nothing was written."
    )
    assert store.get("notes").content == "first"


def test_a_base_that_is_not_one_is_refused(store):
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    refused = _tool("artifact_update", {"slug": "notes", "content": "x", "base": "the latest"})
    assert "'the latest' is not a base artifact_get names" in refused
    assert store.get("notes").content == "first"


def test_metadata_alone_needs_no_base_and_cuts_no_version(store):
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    store.update("notes", content="hers, unsaved as a version", actor="user")

    done = _tool("artifact_update", {"slug": "notes", "description": "weekly notes"})

    assert "version 1" in done, done
    art = store.get("notes")
    assert art.description == "weekly notes" and art.content == "hers, unsaved as a version"
    # No version filed her text as the agent's.
    assert [e.by for e in art.events if e.type in {"iterated", "edited"}] == ["user"]


@pytest.mark.asyncio
async def test_the_refusal_names_who_made_the_newer_version(store):
    """An app's write is the app's, and another chat's agent is named so, never 'the owner'."""
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    base = _base(_tool("artifact_get", {"slug": "notes"}))

    await _editor_save("notes", "the app's text", app="weekly-digest")
    refused = _tool("artifact_update", {"slug": "notes", "content": "x", "base": base})
    assert "the app weekly-digest changed it since" in refused, refused
    assert store.get("notes").events[-1].by == "app:weekly-digest"

    base = _base(_tool("artifact_get", {"slug": "notes"}))
    elsewhere = _base(_tool("artifact_get", {"slug": "notes"}, chat=OTHER_CHAT))
    _tool(
        "artifact_update",
        {"slug": "notes", "content": "from the other chat", "base": elsewhere},
        chat=OTHER_CHAT,
    )
    refused = _tool("artifact_update", {"slug": "notes", "content": "x", "base": base})
    # Version 2 keeps the app's text, which no version held; the other chat's write is version 3.
    assert "it is now version 3, made by the agent in another chat" in refused, refused
    assert store.get("notes", version=2).content == "the app's text"


def test_a_write_the_base_does_not_allow_is_refused_before_anyone_is_asked(store):
    """The owner is never asked to approve a write that cannot land."""
    from personalclaw.mcp_artifacts import _preflight

    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    base = _base(_tool("artifact_get", {"slug": "notes"}))
    store.update("notes", content="hers", actor="user")

    refused = _preflight("artifact_update", {"slug": "notes", "content": "x", "base": base})
    assert refused is not None and "changed after you read it" in refused.reason
    assert _preflight("artifact_update", {"slug": "notes", "content": "x"}) is not None
    current = _base(_tool("artifact_get", {"slug": "notes"}))
    assert _preflight("artifact_update", {"slug": "notes", "content": "x", "base": current}) is None


def test_artifact_save_on_an_existing_slug_writes_its_next_version_over_the_base(store):
    """🔴 Red before: it made a twin under ``-2``, though its words said it re-saves the slug."""
    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})

    refused = _tool("artifact_save", {"name": "Notes", "slug": "notes", "content": "x"})
    assert "already exists" in refused and "Nothing was written" in refused
    base = _base(_tool("artifact_get", {"slug": "notes"}))
    done = _tool(
        "artifact_save", {"name": "Notes", "slug": "notes", "content": "second", "base": base}
    )

    assert "as its next version (slug: notes, version 2" in done, done
    assert [a.slug for a in store.list()] == ["notes"]
    assert store.get("notes").content == "second"


def test_a_new_artifact_needs_no_base(store):
    made = _tool(
        "artifact_save", {"name": "Fresh", "slug": "fresh", "content": "new", "kind": "text"}
    )
    assert made.startswith("Saved artifact 'Fresh' (slug: fresh, version 1"), made


# ── a document: the owner's edit in the document editor survives ─────────────────────────────


def test_a_document_written_over_a_version_the_owner_saved_is_refused_and_hers_survives(store):
    """🔴 Red before: document_create with the slug made a version from the agent's memory over
    the one the owner saved in the document editor."""
    from personalclaw.documents.from_markup import document_from_markdown
    from personalclaw.documents.writers.docx_writer import render_docx

    _tool("document_create", {"name": "Plan", "markdown": "# Plan\n\nThe agent's draft.\n"})
    base = _base(_tool("artifact_get", {"slug": "plan"}))

    # The document editor's save (``PUT …/model``): a re-render over the version it opened.
    hers = render_docx(document_from_markdown("# Plan\n\nThe owner's rewrite.\n"))
    store.update_binary("plan", data=hers, actor="user", expect_version=1)

    refused = _tool(
        "document_create",
        {"name": "Plan", "slug": "plan", "markdown": "# Plan\n\nThe agent's v2.\n", "base": base},
    )
    assert "you read version 1, and it is now version 2, made by the owner" in refused, refused
    assert store.get("plan").version == 2
    assert "The owner's rewrite." in _docx_text(store, "plan")

    current = _base(_tool("artifact_get", {"slug": "plan"}))
    done = _tool(
        "document_create",
        {"name": "Plan", "slug": "plan", "markdown": "# Plan\n\nBoth, merged.\n", "base": current},
    )
    assert done.startswith("Updated docx: plan (v3"), done


def test_a_repeat_by_name_needs_the_base_and_makes_no_twin(store):
    _tool("sheet_create", {"name": "Sales", "rows": '[["Region", "Q1"], ["West", 120]]'})

    refused = _tool("sheet_create", {"name": "Sales", "rows": '[["Region", "Q1"], ["West", 99]]'})
    assert refused.startswith("Error: A xlsx named 'Sales' already exists (slug: sales, version 1)")
    assert [a.slug for a in store.list()] == ["sales"] and store.get("sales").version == 1

    base = _base(_tool("artifact_get", {"slug": "sales"}))
    done = _tool(
        "sheet_create", {"name": "Sales", "rows": '[["Region", "Q1"], ["West", 99]]', "base": base}
    )
    assert done.startswith("Updated xlsx: sales (v2"), done
    assert [a.slug for a in store.list()] == ["sales"]


def test_a_value_hidden_in_the_reading_is_kept_in_the_next_version(store):
    """The agent reads a document masked; a marker it writes back is the value it stood for."""
    key = "sk-ant-api03-" + "a1B2c3D4e5F6g7H8i9J0" * 3
    _tool("document_create", {"name": "Runbook", "markdown": f"# Runbook\n\nKey: {key}\n"})
    read = _tool("artifact_get", {"slug": "runbook"})
    assert key not in read and "[REDACTED" in read
    marker = re.search(r"\[REDACTED:[^\]]*\]", read).group(0)

    done = _tool(
        "document_create",
        {
            "name": "Runbook",
            "slug": "runbook",
            "base": _base(read),
            "markdown": f"# Runbook\n\nKey: {marker}\n\nRotate it monthly.\n",
        },
    )
    assert done.startswith("Updated docx: runbook (v2"), done
    text = _docx_text(store, "runbook")
    assert key in text and "Rotate it monthly." in text and "REDACTED" not in text


# ── reading: every document kind is its text ────────────────────────────────────────────────


def _pdf_available() -> bool:
    from personalclaw.documents import available_formats

    return "pdf" in available_formats()


@pytest.mark.parametrize(
    "tool, args, slug, kind, words",
    [
        (
            "document_create",
            {"name": "Plan", "markdown": "# Plan\n\n- ship\n- tell\n"},
            "plan",
            "docx",
            ["# Plan", "- ship"],
        ),
        (
            "sheet_create",
            {"name": "Sales", "sheets": '{"Q1": [["Region", "Amount"], ["West", 120]]}'},
            "sales",
            "xlsx",
            ['"Q1"', '["West", 120]'],
        ),
        (
            "deck_create",
            {"name": "Pitch", "markdown": "# Pitch\n\n## Why\n- speed\n<!-- notes: smile -->\n"},
            "pitch",
            "pptx",
            ["## Why", "- speed", "<!-- notes: smile -->"],
        ),
        (
            "sheet_create",
            {"name": "Rows", "format": "csv", "rows": '[["a", "b"], [1, 2]]'},
            "rows",
            "csv",
            ["a,b", "1,2"],
        ),
        (
            "artifact_save",
            {"name": "Memo", "content": "# Memo\n\nHello.", "kind": "markdown"},
            "memo",
            "markdown",
            ["# Memo", "Hello."],
        ),
        (
            "artifact_save",
            {"name": "Page", "content": "<p>Hi</p>", "kind": "html"},
            "page",
            "html",
            ["<p>Hi</p>"],
        ),
    ],
)
def test_artifact_get_returns_the_text_and_version_of_each_document_kind(
    store, tool, args, slug, kind, words
):
    """🔴 Red before for the binary kinds: the reply was the address of the file."""
    _tool(tool, args)

    read = _tool("artifact_get", {"slug": slug})

    assert read.startswith(
        f"[Artifact '{args['name']}' (slug: {slug}, kind: {kind}), version 1."
    ), read
    assert "/api/artifacts/" not in read.split("\n", 1)[1]
    for word in words:
        assert word in read, (word, read)
    assert "<untrusted_content source=artifact" in read
    assert re.search(r"Base: v1-[0-9a-f]{16}\]", read)


@pytest.mark.skipif(not _pdf_available(), reason="no PDF writer in this build")
def test_a_pdf_reads_as_the_text_of_its_pages(store):
    _tool(
        "document_create",
        {"name": "Letter", "format": "pdf", "markdown": "# Letter\n\nDear reader, hello.\n"},
    )
    read = _tool("artifact_get", {"slug": "letter"})
    assert "the text of the PDF's pages" in read and "Dear reader, hello." in read


def test_a_long_text_comes_in_parts_and_a_part_of_a_changed_version_is_refused(store):
    from personalclaw.mcp_artifacts import _READ_PAGE_CHARS

    # Longer than a tool call's inline content, as a document the owner keeps can be.
    body = "".join(f"line {n:05d} of the long report\n" for n in range(4000))
    store.create(name="Report", content=body, kind="text", actor="user")

    first = _tool("artifact_get", {"slug": "report"})
    found = re.search(
        r"call artifact_get with slug='report', offset=(\d+) and base='([^']+)'", first
    )
    assert found, first[-400:]
    offset, base = int(found.group(1)), found.group(2)
    assert 0 < offset <= _READ_PAGE_CHARS and body[offset - 1] == "\n"

    second = _tool("artifact_get", {"slug": "report", "offset": offset, "base": base})
    assert body[offset : offset + 30] in second

    store.update("report", content="the owner cut it down\n", actor="user")
    stale = _tool("artifact_get", {"slug": "report", "offset": offset, "base": base})
    assert "changed after the part you read first" in stale and "made by the owner" in stale


def test_an_earlier_version_says_a_write_based_on_it_is_refused(store):
    _tool("artifact_save", {"name": "Notes", "content": "one", "kind": "text"})
    _tool(
        "artifact_update",
        {
            "slug": "notes",
            "content": "two",
            "base": _base(_tool("artifact_get", {"slug": "notes"})),
        },
    )

    old = _tool("artifact_get", {"slug": "notes", "version": 1})

    assert "one" in old and "the live one is version 2" in old
    refused = _tool("artifact_update", {"slug": "notes", "content": "x", "base": _base(old)})
    assert "you read version 1, and it is now version 2" in refused


# ── the Iterate panel and a chat's reference read the same way ───────────────────────────────


@pytest.mark.asyncio
async def test_the_iterate_snapshot_carries_a_documents_text_and_its_base(store):
    import personalclaw.investigate as inv

    _tool("document_create", {"name": "Plan", "markdown": "# Plan\n\nThe agent's draft.\n"})
    base = _base(_tool("artifact_get", {"slug": "plan"}))

    ctx = await inv._resolve_artifact("plan", None)

    assert "The agent's draft." in ctx.snapshot and f"Base: {base}" in ctx.snapshot
    assert "Binary artifact" not in ctx.snapshot
    assert f"call document_create with slug='plan', base='{base}'" in ctx.opening_prompt


def test_a_chat_reference_reads_a_document_as_its_text_with_its_base(store):
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    _tool("document_create", {"name": "Plan", "markdown": "# Plan\n\nThe agent's draft.\n"})
    base = _base(_tool("artifact_get", {"slug": "plan"}))

    class _Chat:
        key = "chat-1-test"
        messages = [{"role": "user", "content": "look", "meta": {"artifacts": ["plan"]}}]

    out = _inject_artifact_content(None, _Chat(), "Tighten it.")

    assert "The agent's draft." in out and base in out
    assert "/api/artifacts/plan/raw" not in out
    assert "<untrusted_content source=artifact" in out


# ── a workflow step: the store keeps what the step would erase ───────────────────────────────


def _ctx(run_id: str = "run-7"):
    from personalclaw.action_providers.base import ActionContext

    return ActionContext(event="workflow_node", payload={"run_id": run_id})


@pytest.mark.asyncio
async def test_a_workflow_refresh_keeps_the_owners_unsaved_edit_as_a_version(store):
    """🔴 Red before: the step's write replaced the owner's plain Save, which no version held."""
    from personalclaw.action_providers.artifact_update_provider import (
        ArtifactUpdateActionProvider,
    )

    step = ArtifactUpdateActionProvider()
    made = await step.execute({"slug": "status", "content": "<p>run 1</p>"}, _ctx("run-1"))
    assert made.success, made.error
    again = await step.execute({"slug": "status", "content": "<p>run 2</p>"}, _ctx("run-2"))
    assert json.loads(again.stdout) == {"slug": "status", "version": 1, "created": False}

    await _editor_save("status", "<p>run 2</p><p>The owner's note.</p>")

    refreshed = await step.execute({"slug": "status", "content": "<p>run 3</p>"}, _ctx("run-3"))

    assert refreshed.success, refreshed.error
    assert json.loads(refreshed.stdout) == {
        "slug": "status",
        "version": 2,
        "created": False,
        "kept_version": 2,
    }
    assert store.get("status").content == "<p>run 3</p>"
    kept = store.get("status", version=2)
    assert kept.content == "<p>run 2</p><p>The owner's note.</p>"
    assert [(e.type, e.by, e.version) for e in store.get("status").events[-2:]] == [
        ("edited", "user", 2),
        ("iterated", "workflow", 2),
    ]
    # Its own last refresh is replaced as it is: no version per refresh.
    quiet = await step.execute({"slug": "status", "content": "<p>run 4</p>"}, _ctx("run-4"))
    assert json.loads(quiet.stdout) == {"slug": "status", "version": 2, "created": False}


@pytest.mark.asyncio
async def test_the_agent_is_told_which_run_wrote_the_version_it_missed(store):
    from personalclaw.action_providers.artifact_update_provider import (
        ArtifactUpdateActionProvider,
    )

    step = ArtifactUpdateActionProvider()
    await step.execute(
        {"slug": "status", "content": "<p>run 1</p>", "kind": "widget"}, _ctx("run-1")
    )
    base = _base(_tool("artifact_get", {"slug": "status"}))
    await step.execute({"slug": "status", "content": "<p>run 2</p>"}, _ctx("run-2"))

    refused = _tool("artifact_update", {"slug": "status", "content": "<p>x</p>", "base": base})
    assert "a workflow (run run-2) changed it since" in refused, refused

    # Read again, its write keeps the run's refresh, which no version held, and says whose it is.
    base = _base(_tool("artifact_get", {"slug": "status"}))
    done = _tool("artifact_update", {"slug": "status", "content": "<p>x</p>", "base": base})
    assert done.endswith(
        "Version 2 keeps what a workflow (run run-2) saved without a new version, as it was "
        "before this write."
    ), done
    assert store.get("status", version=2).content == "<p>run 2</p>"


# ── the owner's editor already guards her own saves ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_editors_save_over_a_version_the_agent_wrote_meanwhile_is_refused(store):
    """Unchanged, pinned: the Artifacts editor names the revision it opened, so a save over a
    version the agent wrote while the page was open is refused, and her draft stays on the page."""
    from personalclaw.artifacts.handlers import api_artifact_update

    _tool("artifact_save", {"name": "Notes", "content": "first", "kind": "text"})
    opened = revision_of("first")
    _tool(
        "artifact_update",
        {
            "slug": "notes",
            "content": "the agent's",
            "base": _base(_tool("artifact_get", {"slug": "notes"})),
        },
    )

    resp = await api_artifact_update(
        _request("PATCH", {"content": "her draft"}, slug="notes", if_match=opened)
    )
    assert resp.status == 409
    assert json.loads(resp.body)["error"]["code"] == "stale_write"
    assert store.get("notes").content == "the agent's"


def test_the_document_reading_round_trips_through_its_tool():
    """What the agent reads is what it writes back: a document's reading, rendered by its tool,
    reads back the same."""
    from personalclaw.documents.from_markup import deck_from_markdown, document_from_markdown
    from personalclaw.documents.to_markup import document_text
    from personalclaw.documents.writers.docx_writer import render_docx
    from personalclaw.documents.writers.pptx_writer import render_pptx

    doc = (
        "# Plan\n\nIntro **bold** and `code`.\n\n## Steps\n\n1. one\n2. two\n\n"
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n---\n\n```\nprint(1)\n```"
    )
    once = document_text("docx", render_docx(document_from_markdown(doc))).text
    twice = document_text("docx", render_docx(document_from_markdown(once))).text
    assert once == twice and "Intro **bold** and `code`." in once

    deck = "# Pitch\n\n## Why\n- speed\n  - and care\n<!-- notes: smile -->\n\n## How\n- daily"
    once = document_text("pptx", render_pptx(deck_from_markdown(deck))).text
    assert document_text("pptx", render_pptx(deck_from_markdown(once))).text == once
    assert "  - and care" in once


def test_a_damaged_document_says_its_text_could_not_be_read(store):
    store.create_binary(
        name="Broken",
        data=b"PK\x03\x04 not really a document",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        kind="docx",
    )
    read = _tool("artifact_get", {"slug": "broken"})
    assert "Its text could not be read: its docx file could not be read" in read
    assert re.search(r"Base: v1-[0-9a-f]{16}", read)


def test_a_csv_rewrite_keeps_the_csv_rule(store):
    _tool("sheet_create", {"name": "Rows", "format": "csv", "rows": '[["a"], ["1"]]'})
    base = _base(_tool("artifact_get", {"slug": "rows"}))
    done = _tool("artifact_update", {"slug": "rows", "content": "a\n=1+1\n", "base": base})
    assert "version 2" in done, done
    assert store.get("rows").content.splitlines()[1] == "'=1+1"
