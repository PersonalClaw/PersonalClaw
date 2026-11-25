"""A workflow is published to outside A2A clients only by its owner's switch.

Publishing a workflow (``metadata.a2a_published``) puts it on the A2A card, where an outside client
can start it: it changes who can start it, so it is the owner's to do, on the workflow's page.

* **The switch's route answers only her own session.** An app's token was already refused before
  the handler ran (`apps.permissions.ROUTE_AUTHZ`). 🔴 Before, the gateway's internal credential,
  which an agent's tools present, published it, saved as the agent and asking nothing. Now it is
  refused with a 403 in the words the app's refusal uses, and nothing is written: no version, no
  flag.
* **A pack's template arrives unpublished.** 🔴 Before, a pack whose template carried the flag put
  it on the card on install, with no switch pressed. Her switch then publishes it as any other.

Driven through the route (`handlers.api_def_a2a_publish`), the pack import (`packs.import_`) and
the card's own read (`inbound.a2a.published_skills`), over the real definition store in a scratch
home.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_an_automation_runs_the_workflow_version_its_owner_allowed import (  # noqa: F401
    _native_store,
    _reply,
    _request,
)

from personalclaw.apps.permissions import owner_only_refusal, route_authz
from personalclaw.inbound import a2a
from personalclaw.packs.build import build_pack
from personalclaw.packs.import_ import import_pack
from personalclaw.workflows import handlers as H
from personalclaw.workflows import service, versions

pytestmark = [pytest.mark.anyio, pytest.mark.security]

NAME = "weekly-digest"
ROOT = {
    "kind": "sequence",
    "id": "main",
    "children": [{"kind": "transform", "id": "tail", "config": {"expr": "done"}}],
}
ROUTE = "/api/workflows/{name}/a2a-publish"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def saved(tmp_path, monkeypatch) -> str:
    """Her workflow, saved in her editor, unpublished."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    out = await service.author_def(
        name=NAME, root=ROOT, description="d", saved_by=versions.OWNER, strict=False
    )
    assert out.get("ok") is True, out
    return NAME


def _press(published: bool, *, headers: dict[str, str] | None = None, app: str = "") -> Any:
    request = _request(
        "POST",
        ROUTE.format(name=NAME),
        {"published": published},
        match={"name": NAME},
        headers=headers,
    )
    if app:
        request["app"] = app
    return H.api_def_a2a_publish(request)


async def _on_card() -> list[str]:
    skills, problem = await a2a.published_skills()
    assert problem == "", problem
    return [str(s["id"]) for s in skills]


async def _stored(name: str) -> dict[str, Any]:
    got = await service.get_def(name)
    assert got.get("ok"), got
    return got["definition"]


# ── the switch's route ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "caller",
    [
        {"headers": {"X-Internal-Secret": "set", "X-Session-Key": "agent:chat:1"}},
        {"app": "notes-app"},
    ],
    ids=["internal-credential", "app-token"],
)
async def test_a_publish_not_from_her_session_is_refused_and_changes_nothing(saved, caller):
    """🔴 The internal credential published it, saved as the agent. An app's token is refused here
    too, behind the gate that refuses it before the handler."""
    before = await _stored(NAME)
    versions_before = versions.list_versions(NAME)

    response = await _press(True, **caller)

    assert response.status == 403
    error = _reply(response)["error"]
    capability = route_authz("POST", ROUTE).capability
    assert error == {"code": "owner_only", "message": owner_only_refusal(capability)}, error
    after = await _stored(NAME)
    assert (after.get("metadata") or {}).get("a2a_published") is not True
    assert after.get("version") == before.get("version"), "a version was written"
    assert versions.list_versions(NAME) == versions_before
    assert await _on_card() == []


async def test_her_own_switch_publishes_it_and_unpublishes_it(saved):
    """The control: her session's press publishes it, saved as the switch."""
    published = _reply(await _press(True))
    assert published["a2a_published"] is True, published
    assert await _on_card() == [NAME]
    assert versions.list_versions(NAME)[-1].saved_by == versions.PUBLISH

    unpublished = _reply(await _press(False))
    assert unpublished["a2a_published"] is False, unpublished
    assert await _on_card() == []


# ── a pack's template ─────────────────────────────────────────────────────────────────────────


def _pack_with_a_published_template(tmp_path: Path, monkeypatch) -> Path:
    author = tmp_path / "author"
    defs = author / "workflows" / "defs" / NAME
    defs.mkdir(parents=True)
    document = {
        "name": NAME,
        "description": "Writes the weekly digest.",
        "root": ROOT,
        "metadata": {"a2a_published": True, "tags": ["weekly"]},
    }
    (defs / "workflow.json").write_text(json.dumps(document))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(author))
    return build_pack([f"template:{NAME}"], name="digest", version="1.0.0", out_path=tmp_path / "d")


async def test_a_packs_template_arrives_unpublished_until_her_switch(tmp_path, monkeypatch):
    """🔴 A pack's template that carried the flag was on the card on install."""
    archive = _pack_with_a_published_template(tmp_path, monkeypatch)
    home = tmp_path / "importer"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    plan = import_pack(archive)

    (template,) = [c for c in plan.components if c.kind == "template"]
    landed = json.loads(
        (home / "workflows" / "defs" / template.target_id / "workflow.json").read_text()
    )
    assert "a2a_published" not in landed.get("metadata", {}), landed.get("metadata")
    assert landed["metadata"].get("tags") == ["weekly"], "the rest of its metadata is the pack's"
    assert await _on_card() == []

    request = _request(
        "POST",
        ROUTE.format(name=template.target_id),
        {"published": True},
        match={"name": template.target_id},
    )
    assert _reply(await H.api_def_a2a_publish(request))["a2a_published"] is True
    assert await _on_card() == [template.target_id]
