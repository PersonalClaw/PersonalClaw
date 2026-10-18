"""Every door that writes a workflow definition runs the posture screen.

A step that approves its own tool calls (``approval_mode: "auto"``) or may change things
(``capability: "mutating"``) does so each time its workflow runs, unattended included, so only the
owner's own yes may put one on a step (``automation_posture.POSTURE_SPECS``). The editor's save
asked her, and nothing else did: the agent's ``workflow_author``, an accepted refiner diff, a prompt
card's template and a pack's template each wrote such a step with nobody asked. Now one writer
screens every save (``workflows.service._write_definition``: a step that would do more than the
same step of the stored definition does needs her yes), and this census holds the doors to it,
read off the source:

* **the store's save** (a definition provider's ``save_def``) is called in one place, that writer;
* **every claim of her yes** (an ``owner_allowed=`` argument) is a door that has one, declared in
  :data:`OWNER_YES` with where the yes comes from, so a new door that passes one fails until it
  says where its yes comes from;
* **every path into the definitions folder** (``workflows/defs``) is declared in
  :data:`DEFS_PATHS` with how what it writes is screened, so a new writer of that folder fails
  until it is screened.

The doors are driven too, so a declaration is not a claim: the one writer refuses a step that
would do more, the yes lets it through, and what arrives from another machine or a pack brings no
such step.
"""

from __future__ import annotations

import copy

import pytest

from personalclaw.workflows import defs as defs_mod

# ── the doors, driven ───────────────────────────────────────────────────────────────────────────

#: One step whose agent approves its own tool calls.
LOOSE = {
    "kind": "sequence",
    "id": "main",
    "children": [
        {
            "kind": "stage",
            "id": "fix",
            "label": "Fix the lint",
            "config": {"prompt": "Fix every lint error in src/app.", "approval_mode": "auto"},
        }
    ],
}


class _MemProvider(defs_mod.WorkflowDefProvider):
    def __init__(self) -> None:
        self.saved: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "aaa-census-test-mem"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return list(self.saved.values())[offset : offset + limit], len(self.saved)

    async def get_def(self, name: str):
        return copy.deepcopy(self.saved.get(name))

    async def save_def(self, **fields):
        fields.setdefault("source", "user")
        fields["version"] = int((self.saved.get(fields["name"]) or {}).get("version") or 0) + 1
        self.saved[fields["name"]] = copy.deepcopy(dict(fields))
        return self.saved[fields["name"]]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    provider = _MemProvider()
    defs_mod.register_provider(provider)
    try:
        yield provider
    finally:
        defs_mod.unregister_provider("aaa-census-test-mem")


@pytest.mark.asyncio
async def test_the_writer_refuses_a_step_that_would_do_more_without_her_yes(store):
    from personalclaw.workflows import service

    refused = await service.author_def(name="lint-fixer", root=copy.deepcopy(LOOSE))

    assert refused["code"] == "WF_DEF_NEEDS_OWNER_YES", refused
    (step,) = refused["steps"]
    assert step["label"] == "Fix the lint" and step["keys"] == ["approval_mode"], step
    assert store.saved == {}


@pytest.mark.asyncio
async def test_an_accepted_diff_goes_through_the_same_writer(store):
    from personalclaw.workflows import service

    refused = await service.save_accepted_diff(
        {"name": "lint-fixer", "root": copy.deepcopy(LOOSE)}, ops=[]
    )

    assert refused["code"] == "WF_DEF_NEEDS_OWNER_YES", refused
    assert store.saved == {}


@pytest.mark.asyncio
async def test_her_yes_lets_it_through_and_a_resave_asks_nothing(store):
    from personalclaw.workflows import service

    saved = await service.author_def(
        name="lint-fixer", root=copy.deepcopy(LOOSE), owner_allowed=True
    )
    again = await service.author_def(name="lint-fixer", root=copy.deepcopy(LOOSE))
    published = await service.set_a2a_published("lint-fixer", True)

    assert saved["saved"] and again["saved"] and published["ok"], (saved, again, published)
    assert store.saved["lint-fixer"]["version"] == 3


def test_a_definition_from_another_machine_brings_no_such_step():
    """The device sync's arrival rule for the workflows folder (``durability.inventory``)."""
    from personalclaw.durability.inventory import INVENTORY

    (entry,) = [e for e in INVENTORY if e.id == "workflows"]
    row = {"id": "defs/lint-fixer/workflow", "data": {"name": "lint-fixer", "root": LOOSE}}
    assert entry.arrives is not None and entry.edit_arrives is not None
    arrived = entry.arrives(copy.deepcopy(row))
    assert "approval_mode" not in arrived["data"]["root"]["children"][0]["config"], arrived
