"""Accept says what happened: a template change is applied before it is recorded as accepted.

A refiner's template change used to be applied AFTER its accept was recorded. So a change that
could not be applied (its workflow gone, an op that no longer fits) was recorded as accepted all
the same, and its re-file was then suppressed as "already accepted". Now it is applied first: a
failed apply answers 409 ``learning_proposal_not_applied``, nothing is recorded, and it still waits.

Also here:
- a proposal the full queue expires takes its Inbox row with it;
- the Inbox's risk tier reads the tier a change was filed with. It used to read the manifest as an
  object, though it is a dict, so every change went untiered.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import learning as L
from personalclaw.learning import proposals
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import native_defs, versions

ROOT = {
    "kind": "sequence",
    "id": "root",
    "children": [{"kind": "stage", "id": "build", "config": {"prompt": "go"}}],
}
OPS = [
    {"op": "update_node", "node_id": "build", "fields": {"prompt": "go, and say what you built"}}
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(proposals, "_dir", lambda: tmp_path / "proposals")
    monkeypatch.setattr(proposals, "_decisions_path", lambda: tmp_path / "decisions.json")
    (tmp_path / "proposals").mkdir()
    provider = native_defs.NativeWorkflowDefProvider()
    registered = defs_mod.get_provider(provider.name) is None
    if registered:
        defs_mod.register_provider(provider)
    yield tmp_path
    if registered:
        defs_mod.unregister_provider(provider.name)


def _diff(target: str) -> proposals.Proposal:
    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.TEMPLATE_DIFF.value,
        title=f"Refine {target}",
        body="Say what the build made.",
        target=target,
        provenance="agent",
        source_cadence="refiner",
        change_manifest={"component": target, "targeted_fix": OPS},
        tags=["refiner", "review"],
    )
    return prop


def _accept(pid: str):
    request = make_mocked_request(
        "POST",
        f"/api/learning/proposals/{pid}/accept",
        match_info={"id": pid},
        app=web.Application(),
    )
    request["user"] = "me"
    resp = asyncio.run(L.api_learning_proposal_accept(request))
    return resp.status, json.loads(resp.body.decode())


def test_a_change_to_a_workflow_that_is_gone_is_not_recorded_and_still_waits(home):
    prop = _diff("gone-workflow")

    status, body = _accept(prop.id)

    assert status == 409, body
    assert body["error"]["code"] == "learning_proposal_not_applied"
    assert "gone-workflow" in body["error"]["message"]
    assert proposals.get(prop.id).status == "pending"
    assert prop.fingerprint not in proposals.load_decisions()


def test_a_change_that_applies_is_the_workflows_next_version(home):
    asyncio.run(
        defs_mod.get_provider("native").save_def(name="refine-me", root=ROOT, description="d")
    )
    prop = _diff("refine-me")

    status, body = _accept(prop.id)

    assert status == 200, body
    assert body["applied"] == {"applied": True}
    assert versions.latest_version("refine-me") == 2
    assert versions.get_version("refine-me", 2).saved_by == versions.REFINER
    assert prop.fingerprint in proposals.load_decisions()


def test_an_expired_proposal_takes_its_inbox_row_with_it(home, monkeypatch):
    dismissed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        proposals, "_resolve_inbox_item", lambda pid, status: dismissed.append((pid, status))
    )
    oldest = proposals.Proposal(
        id="lp-old", kind="knowledge_draft", title="t", body="b", created_at="1"
    )
    proposals._path(oldest.id).write_text(json.dumps(oldest.to_dict()))
    proposals._expire_oldest([oldest])
    assert dismissed == [("lp-old", "dismissed")]
    assert not proposals._path("lp-old").exists()


@pytest.mark.parametrize(
    ("tags", "ops", "tier"),
    [
        (["refiner", "low"], [], "low"),
        (["low", "refiner"], [], "low"),
        (["refiner"], [{"op": "delete", "node_id": "x"}], "manual_only"),
    ],
)
def test_a_changes_tier_is_read_from_what_it_was_filed_with(tags, ops, tier):
    prop = proposals.Proposal(
        id="lp-t",
        kind="template_diff",
        title="t",
        body="b",
        tags=tags,
        change_manifest={"targeted_fix": ops},
    )
    assert L._tier_for(prop) == tier
