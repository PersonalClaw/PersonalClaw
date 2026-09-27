"""A save from the workflow editor never replaces a change made since the page read it.

The dashboard editor (`web/src/pages/workflows/WorkflowDefEditor.tsx`) reads a definition with
`GET /api/workflows/{name}` and saves the WHOLE of it back with `POST /api/workflows`. Measured on
`main` before this change: two editors opened on the same definition both saved, and the second
save — built from the older copy — became the newest version, which is the one a run executes.
The first editor's change survived only as a history entry nobody was told about. The same held
when the gateway wrote the definition between the page's read and its save: the agent's
`workflow_author`, the A2A publish toggle.

The contract is the one every whole-document write has (`personalclaw/stale_write.py`): the read
carries the definition's `revision`, a save over a definition of yours names it in `If-Match`, a
stale one is `409 stale_write` and none is `428 revision_required` — before anything is written.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.stale_write import revision_of
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import handlers as H
from personalclaw.workflows import service
from personalclaw.workflows.bundled_defs import BundledWorkflowDefProvider
from personalclaw.workflows.native_defs import NativeWorkflowDefProvider, defs_root


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


@pytest.fixture(autouse=True)
def _providers():
    """The two providers the dashboard sees: the shipped library and the user's own defs."""
    registered = []
    for provider in (BundledWorkflowDefProvider(), NativeWorkflowDefProvider()):
        if defs_mod.get_provider(provider.name) is None:
            defs_mod.register_provider(provider)
            registered.append(provider.name)
    yield
    for name in registered:
        defs_mod.unregister_provider(name)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _req(method: str, path: str, *, match_info=None, body=None, headers=None):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    app = web.Application()
    app["state"] = None
    req = make_mocked_request(
        method, path, app=app, match_info=match_info or {}, headers=headers or {}
    )
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    return req


def _body(resp) -> dict[str, Any]:
    return json.loads(resp.body.decode())


async def _opened(name: str) -> tuple[dict[str, Any], str]:
    """What the editor opens: the (stripped) definition and the revision the read reports — ``""``
    for a read that reports none, so a save from it sends no ``If-Match`` and the test goes on to
    measure what that save did rather than stopping at the read."""
    resp = await H.api_def_detail(_req("GET", f"/api/workflows/{name}", match_info={"name": name}))
    assert resp.status == 200, _body(resp)
    body = _body(resp)
    return body["definition"], str(body.get("revision") or "")


def _editable(definition: dict[str, Any]) -> dict[str, Any]:
    """The save body the editor sends: everything the definition has except its bookkeeping."""
    skip = {"name", "version", "spec_semver", "source", "provenance", "created_at", "updated_at"}
    return {k: v for k, v in definition.items() if k not in skip}


async def _save(name: str, doc: dict[str, Any], *, base: str = "", **extra: Any):
    headers = {"If-Match": f'"{base}"'} if base else None
    return await H.api_def_save(
        _req("POST", "/api/workflows", body={"name": name, **doc, **extra}, headers=headers)
    )


def _stored(name: str) -> dict[str, Any]:
    """The definition as it sits on disk — what a run executes."""
    return json.loads((defs_root() / name / "workflow.json").read_text(encoding="utf-8"))


ROOT = {"kind": "stage", "id": "draft", "config": {"prompt": "Draft the weekly note."}}


async def _create(name: str = "weekly-note", description: str = "Drafts the weekly note.") -> None:
    resp = await _save(name, {"description": description, "root": ROOT})
    assert resp.status == 201, _body(resp)


def _edited(definition: dict[str, Any], description: str) -> dict[str, Any]:
    return {**_editable(definition), "description": description}


# ── the read carries a revision, and a save names it ────────────────────────────


@pytest.mark.anyio
async def test_the_read_reports_the_revision_of_the_definition_it_hands_out() -> None:
    await _create()
    resp = await H.api_def_detail(
        _req("GET", "/api/workflows/weekly-note", match_info={"name": "weekly-note"})
    )
    body = _body(resp)
    # Of the STRIPPED definition the editor holds: a revision never encodes a hidden value.
    assert body.get("revision") == revision_of(body["definition"])


@pytest.mark.anyio
async def test_two_saves_from_one_read_the_second_is_refused_and_the_first_is_what_runs() -> None:
    await _create()
    opened, revision = await _opened("weekly-note")

    tab_a = await _save("weekly-note", _edited(opened, "Tab A's note."), base=revision)
    tab_b = await _save("weekly-note", _edited(opened, "Tab B's note."), base=revision)

    assert tab_a.status == 201, _body(tab_a)
    assert tab_b.status == 409, _body(tab_b)
    assert _body(tab_b)["error"]["code"] == "stale_write"
    _, now = await _opened("weekly-note")
    # Only a read hands out a revision: a refusal that carried it could be "fixed" by resending
    # the stale copy with it — the overwrite this refusal exists to stop.
    assert now not in tab_b.body.decode()
    stored = _stored("weekly-note")
    assert stored["description"] == "Tab A's note."
    assert stored["version"] == 2, "the refused save wrote no version"


@pytest.mark.anyio
async def test_a_save_over_your_definition_that_names_no_copy_is_refused() -> None:
    await _create()
    opened, _revision = await _opened("weekly-note")

    resp = await _save("weekly-note", _edited(opened, "Saved blind."))

    assert resp.status == 428, _body(resp)
    assert _body(resp)["error"]["code"] == "revision_required"
    assert _stored("weekly-note")["description"] == "Drafts the weekly note."


@pytest.mark.anyio
async def test_the_saved_response_carries_the_revision_the_next_save_names() -> None:
    await _create()
    opened, revision = await _opened("weekly-note")

    first = await _save("weekly-note", _edited(opened, "Once."), base=revision)
    handed_out = _body(first).get("revision")
    reread, now = await _opened("weekly-note")

    assert handed_out == now
    again = await _save("weekly-note", _edited(reread, "Twice."), base=handed_out)
    assert again.status == 201, _body(again)
    assert _stored("weekly-note")["description"] == "Twice."


@pytest.mark.anyio
async def test_a_save_over_a_workflow_deleted_since_is_refused_as_stale() -> None:
    """Deleted in another tab after this editor read it. On `main` the save either re-created it
    from the editor's copy or — the read hides `defaults.budget.max_tokens`, so every copy holds a
    flag — failed as a hidden value with nothing to restore it from, which says nothing about the
    delete."""
    await _create()
    opened, revision = await _opened("weekly-note")
    deleted = await H.api_def_delete(
        _req("DELETE", "/api/workflows/weekly-note", match_info={"name": "weekly-note"})
    )
    assert deleted.status == 200, _body(deleted)

    resp = await _save("weekly-note", _edited(opened, "Still editing."), base=revision)

    assert resp.status == 409, _body(resp)
    assert _body(resp)["error"]["code"] == "stale_write"
    assert not (defs_root() / "weekly-note" / "workflow.json").exists()


# ── what replaces nothing needs no revision ─────────────────────────────────────


@pytest.mark.anyio
async def test_a_new_name_and_a_copy_of_a_shipped_template_need_no_revision() -> None:
    fresh = await _save("brand-new", {"root": ROOT})
    template, _revision = await _opened("paper-ingest")
    copy = await _save("my-paper-ingest", _editable(template), based_on="paper-ingest")

    assert fresh.status == 201, _body(fresh)
    assert copy.status == 201, _body(copy)


@pytest.mark.anyio
async def test_a_dry_run_over_your_definition_needs_no_revision_and_writes_nothing() -> None:
    await _create()
    opened, _revision = await _opened("weekly-note")

    resp = await _save("weekly-note", _edited(opened, "Only checked."), save=False)

    assert resp.status == 200, _body(resp)
    assert _stored("weekly-note")["description"] == "Drafts the weekly note."
    assert _stored("weekly-note")["version"] == 1


# ── the gateway writing the definition between the read and the save ────────────


@pytest.mark.anyio
async def test_the_agent_authoring_it_after_the_page_read_it_is_not_undone() -> None:
    await _create()
    opened, revision = await _opened("weekly-note")
    # What the agent's `workflow_author` tool runs (`mcp_workflows._dispatch`).
    agent = await service.author_def(
        name="weekly-note",
        root={"kind": "stage", "id": "draft", "config": {"prompt": "Draft it with the numbers."}},
        description="The agent's rewrite.",
    )
    assert agent.get("saved"), agent

    resp = await _save("weekly-note", _edited(opened, "The page's edit."), base=revision)

    assert resp.status == 409, _body(resp)
    stored = _stored("weekly-note")
    assert stored["description"] == "The agent's rewrite."
    assert stored["root"]["config"]["prompt"] == "Draft it with the numbers."


@pytest.mark.anyio
async def test_the_publish_toggle_after_the_page_read_it_is_not_undone() -> None:
    await _create()
    opened, revision = await _opened("weekly-note")
    published = await service.set_a2a_published("weekly-note", True)
    assert published.get("ok"), published

    stale = await _save("weekly-note", _edited(opened, "Edited."), base=revision)
    assert stale.status == 409, _body(stale)
    assert _stored("weekly-note")["metadata"]["a2a_published"] is True

    # Re-read and re-apply — what the editor's Reload and reapply sends — lands and keeps it.
    reread, now = await _opened("weekly-note")
    resp = await _save("weekly-note", _edited(reread, "Edited."), base=now)
    assert resp.status == 201, _body(resp)
    stored = _stored("weekly-note")
    assert stored["description"] == "Edited."
    assert stored["metadata"]["a2a_published"] is True


@pytest.mark.anyio
async def test_a_stale_save_is_refused_before_it_is_asked_about_a_loosening() -> None:
    """A consent question about a copy that is about to be refused asks about the wrong
    definition — and a yes would resend it, to be refused anyway."""
    await _create()
    opened, revision = await _opened("weekly-note")
    elsewhere = await _save("weekly-note", _edited(opened, "Saved elsewhere."), base=revision)
    assert elsewhere.status == 201, _body(elsewhere)

    loosening = _edited(opened, "Approves itself.")
    loosening["root"] = {**ROOT, "config": {**ROOT["config"], "approval_mode": "auto"}}
    resp = await _save("weekly-note", loosening, base=revision)

    assert resp.status == 409, _body(resp)
    assert _body(resp)["error"]["code"] == "stale_write"
    assert "approval_mode" not in _stored("weekly-note")["root"]["config"]
