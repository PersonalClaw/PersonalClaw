"""No other machine gives an agent here a tool back: what an agent is kept from is each machine's.

🔴 An agent file's ``managedToolPolicy.exclude`` lists the tools its sessions are kept from, and a
device sync took another machine's edit to an agent file in as written. So a tool the owner kept
from an agent on this machine came back to it the moment the other machine's copy lost the
exclusion — by an edit there, or by taking the other machine's version in the conflict review.

The list is now each machine's own (the ``agents`` inventory entry's ``compared`` and
``edit_arrives``): an agent another machine makes arrives with the list it has there, and another
machine's edit to an agent this home has takes in everything but the list, which stays as it is
here. Two homes never compare it, so a difference in it is never an edit, never a conflict, and
never undone by a later pull.

Why not let another machine's additions flow and only its removals stop: both ways to do it fail.
Compared as written (tried first), the two lists differ from the first removal on, so the machines
never agree on the agent again, and the next edit to it was a conflict to review. Merged as a set
that only grows, an exclusion taken off on one machine comes back from the other at its next pull,
so none could ever be taken off.
"""

from __future__ import annotations

import json
from pathlib import Path

from personalclaw.durability import conflict_resolve as resolver
from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability.sync_cycle import run_sync_cycle
from tests.test_durability_sync_cycle import SharedStore

AGENTS = inv.by_id("agents")
_AGENT = Path("agents") / "helper.json"


def _agent(description: str, exclude: list[str]) -> dict:
    return {
        "name": "helper",
        "description": description,
        "managedToolPolicy": {"exclude": exclude},
    }


def _save(home: Path, document: dict) -> None:
    path = home / _AGENT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _read(home: Path) -> dict:
    return json.loads((home / _AGENT).read_text())


def _kept_from(home: Path) -> list[str]:
    return _read(home)["managedToolPolicy"]["exclude"]


def _cycle(store: SharedStore, home: Path, name: str) -> None:
    report = run_sync_cycle(store, home, self_id=name, now="now")
    assert report.ok, report.error
    assert report.conflicts == 0, f"{name} recorded a conflict"


def _both_hold_it(tmp_path: Path) -> tuple[SharedStore, Path, Path]:
    store, a, b = SharedStore(), tmp_path / "A", tmp_path / "B"
    _save(a, _agent("helps", ["shell", "web_fetch"]))
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)
    assert _kept_from(b) == ["shell", "web_fetch"], "a new agent arrives with its list"
    return store, a, b


def test_an_edit_there_that_drops_an_exclusion_does_not_drop_it_here(tmp_path):
    store, a, b = _both_hold_it(tmp_path)
    _save(b, _agent("helps with releases", ["web_fetch"]))
    for home, name in ((b, "B"), (a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)
    assert _read(a)["description"] == "helps with releases", "the rest of the edit is taken in"
    assert _kept_from(a) == ["shell", "web_fetch"]
    assert _kept_from(b) == ["web_fetch"], "there, the removal stands: it was made there"


def test_a_change_to_the_list_alone_is_neither_an_edit_nor_a_conflict(tmp_path):
    store, a, b = _both_hold_it(tmp_path)
    _save(a, _agent("helps", ["shell", "web_fetch", "run_command"]))
    _save(b, _agent("helps", []))
    for home, name in ((a, "A"), (b, "B"), (a, "A"), (b, "B")):
        _cycle(store, home, name)
    assert _kept_from(a) == ["shell", "web_fetch", "run_command"]
    assert _kept_from(b) == []


def test_an_edit_on_both_to_what_the_agent_is_is_still_a_conflict(tmp_path):
    # CONTROL: the list is out of what two homes compare, and nothing else is.
    store, a, b = _both_hold_it(tmp_path)
    _save(a, _agent("helps with the release", ["shell", "web_fetch"]))
    _save(b, _agent("helps with the roadmap", ["shell", "web_fetch"]))
    _cycle(store, a, "A")
    report = run_sync_cycle(store, b, self_id="B", now="now")
    assert report.ok and report.conflicts == 1


def test_taking_the_other_machines_version_keeps_this_machines_list(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _save(tmp_path, _agent("helps", ["shell"]))
    record = conflicts_mod.ConflictRecord(
        entry_id="agents",
        entity_id="helper",
        domain=AGENTS.domain,
        surface=conflicts_mod.SURFACE_DURABILITY,
        ancestor_sha="a",
        local_sha="l",
        remote_sha="r",
        local_row={"id": "helper", "data": _agent("helps", ["shell"])},
        remote_row={"id": "helper", "data": _agent("helps with releases", [])},
    )
    assert conflicts_mod.ConflictQueue(tmp_path).record(record)
    outcome = resolver.resolve_conflict(tmp_path, record.id, resolver.CHOICE_TAKE_REMOTE)
    assert outcome.ok, outcome.message
    assert _read(tmp_path)["description"] == "helps with releases"
    assert _kept_from(tmp_path) == ["shell"]


def test_an_agent_with_no_list_here_takes_none_from_the_edit(tmp_path):
    store, a, b = SharedStore(), tmp_path / "A", tmp_path / "B"
    _save(a, {"name": "helper", "description": "helps"})
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)
    _save(b, _agent("helps more", ["shell"]))
    for home, name in ((b, "B"), (a, "A")):
        _cycle(store, home, name)
    assert _read(a) == {"name": "helper", "description": "helps more"}
