"""What another machine's owner allowed arrives here waiting for this machine's owner.

🔴 A device sync carried workflows and runner definitions, and the agent CLI's runtime config, as
the other machine wrote them. A workflow step's ``approval_mode: auto`` (its agent approves its own
tool calls) or ``capability: mutating`` (write access) is the owner's yes, given where a save shows
it; a runner definition names the program PersonalClaw runs; the agent runtime config lists the
tools the agent CLI runs without asking and the servers it starts. Each ran here as the other
machine's owner had allowed it there.

Now each gets the rule automations and hooks have, through a real sync between two homes:

* a workflow from another machine arrives with neither key on any step; another machine's edit to
  one this home has keeps what this home allowed only on the steps the edit left as they were, and
  never brings the other machine's;
* a runner definition syncs, and its CLI runs here only once this home's owner allowed what it
  runs, sealed to it (``agents.runner_grants``): one from another machine waits, and so does
  another machine's edit to one allowed here;
* ``agents/personalclaw.json`` stays on each machine (``test_what_stays_on_each_machine``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability.sync_cycle import run_sync_cycle
from tests.test_durability_sync_cycle import SharedStore

_DEF = Path("workflows") / "defs" / "nightly" / "workflow.json"


def _definition(*, plan: dict, write: dict) -> dict:
    """A definition of two stages, each carrying the step config given."""
    return {
        "name": "nightly",
        "version": 1,
        "root": {
            "kind": "sequence",
            "id": "main",
            "children": [
                {"kind": "stage", "id": "plan", "config": {"prompt": "plan it", **plan}},
                {"kind": "stage", "id": "write", "config": {"prompt": "write it", **write}},
            ],
        },
    }


def _stages(home: Path) -> dict[str, dict]:
    document = json.loads((home / _DEF).read_text())
    return {child["id"]: child["config"] for child in document["root"]["children"]}


def _save(home: Path, document: dict) -> None:
    path = home / _DEF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def _cycle(store: SharedStore, home: Path, name: str) -> None:
    report = run_sync_cycle(store, home, self_id=name, now="now")
    assert report.ok, report.error
    assert report.conflicts == 0, f"{name} recorded a conflict"


@pytest.fixture
def homes(tmp_path):
    return SharedStore(), tmp_path / "A", tmp_path / "B"


def test_a_workflow_from_another_machine_arrives_asking_and_without_write_access(homes):
    store, a, b = homes
    _save(a, _definition(plan={"approval_mode": "auto"}, write={"capability": "mutating"}))
    b.mkdir()
    _cycle(store, a, "A")
    _cycle(store, b, "B")

    stages = _stages(b)
    assert stages == {"plan": {"prompt": "plan it"}, "write": {"prompt": "write it"}}
    # CONTROL: the other machine keeps what its owner allowed there.
    assert _stages(a)["plan"]["approval_mode"] == "auto"


def test_a_tightening_value_arrives(homes):
    store, a, b = homes
    _save(a, _definition(plan={"capability": "research"}, write={}))
    b.mkdir()
    _cycle(store, a, "A")
    _cycle(store, b, "B")

    assert _stages(b)["plan"] == {"prompt": "plan it", "capability": "research"}


def test_an_edit_keeps_what_you_allowed_only_on_the_steps_it_left_as_they_were(homes):
    store, a, b = homes
    _save(a, _definition(plan={"approval_mode": "auto"}, write={"capability": "mutating"}))
    b.mkdir()
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)
    # This home's owner allows both steps here, as a save with their yes writes it.
    _save(b, _definition(plan={"approval_mode": "auto"}, write={"capability": "mutating"}))
    for home, name in ((b, "B"), (a, "A")):
        _cycle(store, home, name)

    edited = _definition(plan={"approval_mode": "auto"}, write={"capability": "mutating"})
    edited["root"]["children"][1]["config"]["prompt"] = "write it, then delete the drafts"
    _save(a, edited)
    for home, name in ((a, "A"), (b, "B")):
        _cycle(store, home, name)

    stages = _stages(b)
    assert stages["write"] == {
        "prompt": "write it, then delete the drafts"
    }, "a step the edit changed keeps nothing this home allowed of it: it asks again"
    assert stages["plan"] == {
        "prompt": "plan it",
        "approval_mode": "auto",
    }, "a step the edit left as it was keeps this home's yes"
    assert conflicts_mod.ConflictQueue(b).items() == []


def test_another_machines_yes_to_a_step_never_arrives(homes):
    """A peer that allows a step there changes nothing here: it is not an edit to what runs."""
    store, a, b = homes
    _save(a, _definition(plan={}, write={}))
    b.mkdir()
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)
    before = (b / _DEF).read_bytes()

    _save(a, _definition(plan={"approval_mode": "auto"}, write={"capability": "mutating"}))
    for home, name in ((a, "A"), (b, "B")):
        _cycle(store, home, name)

    assert (b / _DEF).read_bytes() == before


# ── runner definitions ───────────────────────────────────────────────────────────────────────


def _as_home(monkeypatch, home: Path) -> None:
    """Read and write the runner catalog and the grant books as *home* does."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)


def _runner_file(home: Path, **fields: object) -> Path:
    path = home / "runners" / "my-cli.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"id": "my-cli", "display_name": "My CLI", "bin_names": ["/nonexistent/my-cli"]}
    path.write_text(json.dumps({**document, **fields}))
    return path


def _waiting(monkeypatch, home: Path) -> bool:
    from personalclaw.agents import runners

    _as_home(monkeypatch, home)
    return runners.runner_row(runners.catalog()["my-cli"]).waiting


def _allow(monkeypatch, home: Path) -> None:
    from personalclaw.agents import runner_grants, runners

    _as_home(monkeypatch, home)
    runner_grants.give(runners.catalog()["my-cli"])


def test_a_runner_definition_from_another_machine_waits_for_you(homes, monkeypatch):
    store, a, b = homes
    _runner_file(a)
    _allow(monkeypatch, a)
    b.mkdir()
    _cycle(store, a, "A")
    _cycle(store, b, "B")

    assert (b / "runners" / "my-cli.json").is_file(), "the definition itself syncs"
    assert _waiting(monkeypatch, b), "the other machine's yes is not this one's"
    assert not (b / "grants").exists()
    # CONTROL: allowed where its owner allowed it.
    assert not _waiting(monkeypatch, a)


def test_another_machines_edit_to_what_a_runner_runs_waits_for_you_again(homes, monkeypatch):
    store, a, b = homes
    _runner_file(a)
    _allow(monkeypatch, a)
    b.mkdir()
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)
    _allow(monkeypatch, b)
    assert not _waiting(monkeypatch, b)

    edited = json.loads((a / "runners" / "my-cli.json").read_text())
    _runner_file(a, **{**copy.deepcopy(edited), "bin_names": ["/nonexistent/other"]})
    for home, name in ((a, "A"), (b, "B")):
        _cycle(store, home, name)

    stored = json.loads((b / "runners" / "my-cli.json").read_text())
    assert stored["bin_names"] == ["/nonexistent/other"], "the edit reached this machine"
    assert _waiting(monkeypatch, b), "and what it runs now waits for this machine's owner"
