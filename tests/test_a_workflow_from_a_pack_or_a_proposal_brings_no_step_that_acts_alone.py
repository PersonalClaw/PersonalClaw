"""A workflow that comes from elsewhere brings no step that acts alone or changes things.

A step's ``approval_mode: "auto"`` (its agent approves its own tool calls) or ``capability:
"mutating"`` (it may change things) is the owner's yes, given where she is shown the step, so
none that arrives from elsewhere is hers:

* **A pack's template** lands with every step asking before it acts and only reading, and allowing
  either asks her in its editor. Before, a pack's template kept both, and its steps ran that way
  the first time it was started.
* **An accepted refiner diff** that would let a step do more is not applied, and says which step
  and what: accepting a model's proposal is no yes to that. A diff that does no more is applied.
* **A prompt card's template** is saved through the save every other one goes through.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from personalclaw.packs.build import build_pack
from personalclaw.packs.import_ import import_pack
from personalclaw.workflows import defs as defs_mod

#: A template whose first step fixes things on its own, and whose second only reads.
ROOT = {
    "kind": "sequence",
    "id": "main",
    "children": [
        {
            "kind": "stage",
            "id": "fix",
            "label": "Fix the lint",
            "config": {
                "prompt": "Fix every lint error in src/app.",
                "approval_mode": "auto",
                "capability": "mutating",
            },
        },
        {
            "kind": "stage",
            "id": "review",
            "label": "Review",
            "config": {"prompt": "Read the diff.", "capability": "research"},
        },
    ],
}


# ── a pack's template ───────────────────────────────────────────────────────────────────────────


def test_a_packs_template_arrives_with_every_step_asking_before_it_acts(tmp_path, monkeypatch):
    author = tmp_path / "author"
    defs = author / "workflows" / "defs" / "lint-fixer"
    defs.mkdir(parents=True)
    (defs / "workflow.json").write_text(
        json.dumps({"name": "lint-fixer", "description": "Fixes the lint.", "root": ROOT})
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(author))
    archive = build_pack(
        ["template:lint-fixer"], name="tidy", version="1.0.0", out_path=tmp_path / "t.pclaw"
    )
    home = tmp_path / "importer"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    plan = import_pack(archive)

    (template,) = [c for c in plan.components if c.kind == "template"]
    landed = json.loads(
        (home / "workflows" / "defs" / template.target_id / "workflow.json").read_text()
    )
    fix, review = landed["root"]["children"]
    assert "approval_mode" not in fix["config"], "a pack's step approves its own calls"
    assert "capability" not in fix["config"], "a pack's step was given write access"
    assert fix["config"]["prompt"] == "Fix every lint error in src/app."
    # A tightening value is the pack's to keep.
    assert review["config"]["capability"] == "research"


# ── an accepted refiner diff ────────────────────────────────────────────────────────────────────


class _MemProvider(defs_mod.WorkflowDefProvider):
    def __init__(self) -> None:
        self.saved: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "aaa-arrival-test-mem"

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
def stored(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    provider = _MemProvider()
    quiet = copy.deepcopy(ROOT)
    quiet["children"][0]["config"] = {"prompt": "Fix every lint error in src/app."}
    provider.saved["lint-fixer"] = {"name": "lint-fixer", "root": quiet, "version": 1}
    defs_mod.register_provider(provider)
    try:
        yield provider
    finally:
        defs_mod.unregister_provider("aaa-arrival-test-mem")


def _proposal(fields: dict) -> SimpleNamespace:
    return SimpleNamespace(
        target="lint-fixer",
        change_manifest={
            "targeted_fix": [{"op": "update_node", "node_id": "fix", "fields": fields}]
        },
    )


@pytest.mark.asyncio
async def test_an_accepted_diff_that_lets_a_step_act_alone_is_not_applied(stored):
    from personalclaw.dashboard.handlers.learning import _apply_accepted_template_diff

    applied = await _apply_accepted_template_diff(_proposal({"approval_mode": "auto"}))

    assert applied["applied"] is False, applied
    assert "Fix the lint" in applied["reason"], applied
    assert "approves its own tool calls" in applied["reason"], applied
    assert stored.saved["lint-fixer"]["version"] == 1, "the diff was written"
    assert "approval_mode" not in stored.saved["lint-fixer"]["root"]["children"][0]["config"]


@pytest.mark.asyncio
async def test_an_accepted_diff_that_does_no_more_is_applied(stored):
    from personalclaw.dashboard.handlers.learning import _apply_accepted_template_diff

    applied = await _apply_accepted_template_diff(_proposal({"prompt": "Fix every lint error."}))

    assert applied == {"applied": True, "version": 2}, applied
    fix = stored.saved["lint-fixer"]["root"]["children"][0]
    assert fix["config"]["prompt"] == "Fix every lint error."


# ── a prompt card's template ────────────────────────────────────────────────────────────────────


def test_a_cards_template_is_saved_through_the_one_save(stored):
    from personalclaw.packs import prompt_cards

    payload = {
        "target": "template",
        "name": "card-template",
        "steps": [{"id": "a", "prompt": "First."}, {"id": "b", "prompt": "Then."}],
    }
    body = f"Import as a template:\n\n```json\n{json.dumps(payload)}\n```\n"

    written = prompt_cards.install_accepted_prompt_card({"kind": "template", "body": body})

    assert written == "template:card-template"
    assert stored.saved["card-template"]["provenance"] == "user"
