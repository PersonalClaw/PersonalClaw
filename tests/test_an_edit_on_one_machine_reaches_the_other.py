"""An edit made on one machine reaches the other, for every store a sync merges record by record.

🔴 A merge keyed by id kept this home's copy of every record it already had. A record made on one
machine reached the other, and every later edit to it stayed where it was made: a renamed task, a
retitled project, a changed tag, an automation moved to another hour. A store whose rows carry
``updated_at`` let an edit through only when the machine that made it had the later clock.

Each record is now merged three ways, against the version this home and that peer last agreed on:
changed on one side only, that side's edit wins, in either direction and whatever the clocks say;
changed on both, it is a conflict for review, as before. The agreement is kept per peer, on each
home. One map in the shared registry held whichever agreement was published last, so a third
machine that had not caught up read its old copy as an edit and wrote it over the new one; and the
map named every record in the one object an encrypted sync leaves readable.

What is one home's stays that home's when the other's edit comes in: an automation's switch, what
happened to it there and its grant, which keeps only what the edited action still runs as it ran
here; and a new cadence re-arms its next fire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability.registry import REGISTRY_KEY
from personalclaw.durability.sync_cycle import run_sync_cycle
from personalclaw.hooks import ScriptHook
from personalclaw.triggers import grants
from personalclaw.triggers.arm import arm
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from tests.test_durability_conflict_review import _app
from tests.test_durability_sync_cycle import SharedStore

RID = "rec-1"


def _cycle(store: SharedStore, home: Path, name: str):
    report = run_sync_cycle(store, home, self_id=name, now="now")
    assert report.ok, report.error
    return report


def _agree(store: SharedStore, a: Path, b: Path) -> None:
    """A publishes, B takes it in and publishes, A pulls B's: the two now hold the same copy and
    each has recorded agreeing on it with the other."""
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)


# ── one record in each store ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _Record:
    """The record the round trip edits, as one store holds it. ``write`` puts a version of it in a
    home — changing only what a person edits, and leaving the rest of the home's copy as it is —
    and ``text`` reads that version back."""

    write: Callable[[Path, str], None]
    text: Callable[[Path], str]


def _in_entity_dir(entry: inv.StateEntry) -> _Record:
    """One file of the store's directory, which is one record."""

    def path(home: Path) -> Path:
        return home / entry.path / f"{RID}.json"

    def write(home: Path, text: str) -> None:
        target = path(home)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(target.read_text()) if target.exists() else {"id": RID}
        data["title"] = text
        target.write_text(json.dumps(data), encoding="utf-8")

    return _Record(write, lambda home: json.loads(path(home).read_text())["title"])


def _in_document(
    file: str, key: str, made: Callable[[str], dict], field: str, **around: Any
) -> _Record:
    """The record ``rec-1`` in a store's one file, under *key* (the file is the list for ``""``),
    with *around* the rest of a new file."""

    def listed(document: Any) -> list:
        return document if not key else document[key]

    def write(home: Path, text: str) -> None:
        target = home / file
        if target.exists():
            document = json.loads(target.read_text())
        else:
            document = [] if not key else {key: [], **around}
        records = listed(document)
        mine = [r for r in records if r.get("id") == RID]
        if mine:
            mine[0][field] = text
        else:
            records.append(made(text))
        home.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document), encoding="utf-8")

    def text(home: Path) -> str:
        records = [r for r in listed(json.loads((home / file).read_text())) if r.get("id") == RID]
        assert len(records) == 1, records
        return records[0][field]

    return _Record(write, text)


def _automation(name: str) -> dict:
    return Trigger(
        id=RID,
        name=name,
        kind="clock",
        spec={"kind": "cron", "expr": "0 2 * * *", "timezone": "UTC"},
        workflow={"inline": {"provider": "notify", "config": {"title": "nightly"}}},
    ).to_dict()


def _hook(name: str) -> dict:
    return ScriptHook(
        id=RID, name=name, provider="bash", provider_config={"command": "/nonexistent/pc-hook"}
    ).to_dict()


#: What every inbox item holds besides its id and message.
_ITEM = {
    "channel": "C1",
    "channel_name": "general",
    "thread_ts": None,
    "sender_id": "U1",
    "sender_name": "Someone",
}

RECORDS: dict[str, _Record] = {
    "doc_comments": _in_document(
        "doc_comments.json",
        "comments",
        lambda text: {"id": RID, "doc_id": "/notes.md", "comment": text, "ts": 1.0},
        "comment",
    ),
    "triggers": _in_document(
        "triggers.json", "triggers", _automation, "name", version=1, saved_at=1.0
    ),
    "hooks": _in_document("hooks.json", "hooks", _hook, "name"),
    "dashboard_views": _in_document(
        "dashboard_views.json",
        "views",
        lambda text: {"id": RID, "name": text, "tiles": []},
        "name",
        overlay={},
    ),
    "inbox": _in_document(
        "inbox.json", "items", lambda text: {"id": RID, "message": text, **_ITEM}, "message"
    ),
    "folders": _in_document("folders.json", "", lambda text: {"id": RID, "name": text}, "name"),
    "tags": _in_document("tags.json", "", lambda text: {"id": RID, "name": text}, "name"),
    "tag_boards": _in_document(
        "tag_boards.json", "", lambda text: {"id": RID, "name": text}, "name"
    ),
    "research_reports": _in_document(
        "research_reports.json",
        "",
        lambda text: {"id": RID, "name": text, "prompt": "What changed?", "enabled": True},
        "name",
    ),
}

#: Every store a sync merges record by record, by id: each file of an entity directory, and each
#: record of a store kept in one file. Read off the inventory, so a store added later is in here.
MERGED_BY_ID = sorted(
    e.id
    for e in inv.INVENTORY
    if e.merge in (inv.MERGE_UNION_BY_ID, inv.MERGE_LWW)
    and (e.kind == inv.KIND_JSON_ENTITY_DIR or e.records is not None)
)


def _record(entry_id: str) -> _Record:
    entry = inv.by_id(entry_id)
    assert entry is not None
    if entry.kind == inv.KIND_JSON_ENTITY_DIR:
        return _in_entity_dir(entry)
    return RECORDS[entry_id]


def test_every_store_of_records_has_one_here():
    """A store of records added later needs its record here, or the round trip below skips it."""
    documents = {i for i in MERGED_BY_ID if inv.by_id(i).kind != inv.KIND_JSON_ENTITY_DIR}
    assert documents == set(RECORDS)
    assert len(MERGED_BY_ID) >= 21, MERGED_BY_ID


@pytest.mark.parametrize("entry_id", MERGED_BY_ID)
def test_an_edit_on_either_machine_reaches_the_other(tmp_path, entry_id):
    record = _record(entry_id)
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    record.write(a, "made on A")
    _agree(store, a, b)
    assert record.text(b) == "made on A", "the record never reached B — the fixture is vacuous"

    record.write(a, "edited on A")
    _cycle(store, a, "A")
    report = _cycle(store, b, "B")
    assert record.text(b) == "edited on A", "A's edit did not reach B"
    assert report.rows_updated >= 1

    record.write(b, "edited on B")
    _cycle(store, b, "B")
    _cycle(store, a, "A")
    assert record.text(a) == "edited on B", "B's edit did not reach A"
    assert record.text(b) == "edited on B"

    # One side's edit is not a conflict: nothing waits for review on either machine.
    assert conflicts_mod.ConflictQueue(a).items() == []
    assert conflicts_mod.ConflictQueue(b).items() == []


# ── whatever the clocks say ──────────────────────────────────────────────────


def _tags(home: Path, name: str, updated_at: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "tags.json").write_text(
        json.dumps([{"id": RID, "name": name, "updated_at": updated_at}]), encoding="utf-8"
    )


def _tag(home: Path) -> str:
    return json.loads((home / "tags.json").read_text())[0]["name"]


def test_an_edit_wins_on_both_machines_when_its_machines_clock_is_behind(tmp_path):
    """Tags merge by ``updated_at``. B's clock is a week behind A's, so B's edit carries an older
    stamp than the copy both machines agreed on: by the stamps, the old name wins on both."""
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    _tags(a, "work", "2026-09-20T00:00:00Z")
    _agree(store, a, b)

    _tags(b, "work (renamed on B)", "2026-09-13T00:00:00Z")
    _cycle(store, a, "A")  # A publishes its copy again, the agreed one, stamped later
    _cycle(store, b, "B")
    assert _tag(b) == "work (renamed on B)", "B's own edit was undone by A's older copy"

    _cycle(store, a, "A")
    assert _tag(a) == "work (renamed on B)", "B's edit did not reach A"


# ── both sides edited ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_edit_on_both_machines_is_a_conflict_the_review_lists(tmp_path):
    from personalclaw.config.loader import config_dir

    store = SharedStore()
    a, b = tmp_path / "A", config_dir()
    tags = _record("tags")
    tags.write(a, "made on A")
    _agree(store, a, b)

    tags.write(a, "edited on A")
    tags.write(b, "edited on B")
    _cycle(store, a, "A")
    report = _cycle(store, b, "B")
    assert report.conflicts == 1
    assert tags.text(b) == "edited on B", "a conflict applies nothing until it is reviewed"

    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/durability/conflicts")
        assert resp.status == 200
        body = await resp.json()
    assert body["counts"]["needs_review"] == 1
    [listed] = body["conflicts"]
    assert (listed["entry_id"], listed["entity_id"]) == ("tags", RID)
    assert listed["local_row"]["name"] == "edited on B"
    assert listed["remote_row"]["name"] == "edited on A"

    # Held on the next cycle too, not taken in as an edit made on A.
    _cycle(store, b, "B")
    assert tags.text(b) == "edited on B"


def test_a_second_edit_before_the_first_comes_back_is_not_a_conflict(tmp_path):
    """A renames a tag, B takes the new name and publishes it, and A renames it again before it
    pulls that. B's copy is A's own first edit, which A published after the two last agreed:
    measured from the agreement it read as an edit made on B, and A's second edit was a conflict."""
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    tags = _record("tags")
    tags.write(a, "made on A")
    _agree(store, a, b)

    tags.write(a, "renamed on A")
    _cycle(store, a, "A")
    _cycle(store, b, "B")  # B takes it, and hands it back in its export
    tags.write(a, "renamed again on A")
    report = _cycle(store, a, "A")
    assert report.conflicts == 0, "A's own first edit, handed back, read as an edit made on B"
    assert tags.text(a) == "renamed again on A"

    _cycle(store, b, "B")
    _cycle(store, a, "A")
    assert tags.text(a) == tags.text(b) == "renamed again on A"
    assert conflicts_mod.ConflictQueue(a).items() == []
    assert conflicts_mod.ConflictQueue(b).items() == []


# ── an automation's own part stays this home's ───────────────────────────────


def _granted(name: str = "nightly backup", *, command: str = "/nonexistent/pc-backup") -> Trigger:
    return Trigger(
        id=RID,
        name=name,
        kind="clock",
        spec={"kind": "cron", "expr": "0 2 * * *", "timezone": "UTC"},
        workflow={"inline": {"provider": "bash", "config": {"command": command}}},
        capabilities={"providers": ["bash"]},
    )


def _switched_on_here(home: Path) -> Trigger:
    """What the owner does in B once A's automation arrives there switched off: switch it on,
    saying yes to what it runs, and the clock arms it."""
    store = TriggerStore(home)
    row = store.get(RID)
    assert row is not None and row.trigger.enabled is False, "it arrives switched off"
    row.trigger.enabled = True
    row.trigger.capabilities = {"providers": ["bash"]}
    row.trigger.next_fire_at = arm(row.trigger)
    store.upsert(row.trigger)
    return row.trigger


def _edit_on(home: Path, **fields: Any) -> None:
    store = TriggerStore(home)
    row = store.get(RID)
    assert row is not None
    for name, value in fields.items():
        setattr(row.trigger, name, value)
    store.upsert(row.trigger)


def _an_automation_both_machines_run(tmp_path: Path) -> tuple[SharedStore, Path, Path, Trigger]:
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    made = _granted()
    made.next_fire_at = arm(made)
    TriggerStore(a).upsert(made)
    _agree(store, a, b)
    ran_here = _switched_on_here(b)
    _cycle(store, b, "B")
    _cycle(store, a, "A")
    return store, a, b, ran_here


def test_a_renamed_automation_keeps_its_switch_grant_and_next_fire_on_the_other(tmp_path):
    store, a, b, before = _an_automation_both_machines_run(tmp_path)
    _edit_on(a, name="nightly backup (renamed)")
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    row = TriggerStore(b).get(RID)
    assert row is not None
    assert row.trigger.name == "nightly backup (renamed)"
    assert row.trigger.enabled is True
    assert row.trigger.capabilities == {"providers": ["bash"]}
    assert grants.missing(row.trigger) == []
    assert row.trigger.next_fire_at == before.next_fire_at


def test_a_changed_command_waits_for_the_owners_yes_on_the_other_machine(tmp_path):
    store, a, b, _ = _an_automation_both_machines_run(tmp_path)
    _edit_on(
        a, workflow={"inline": {"provider": "bash", "config": {"command": "/nonexistent/pc-other"}}}
    )
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    row = TriggerStore(b).get(RID)
    assert row is not None
    assert row.trigger.workflow["inline"]["config"]["command"] == "/nonexistent/pc-other"
    assert row.trigger.enabled is True, "the switch is this home's"
    assert grants.missing(row.trigger) == ["bash"], "the yes was to the old command"


def test_a_new_cadence_rearms_the_next_fire_on_the_other_machine(tmp_path):
    store, a, b, before = _an_automation_both_machines_run(tmp_path)
    _edit_on(a, spec={"kind": "cron", "expr": "30 5 * * *", "timezone": "UTC"})
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    row = TriggerStore(b).get(RID)
    assert row is not None
    assert row.trigger.spec["expr"] == "30 5 * * *"
    assert row.trigger.next_fire_at != before.next_fire_at
    fire = datetime.fromisoformat(row.trigger.next_fire_at).astimezone(timezone.utc)
    assert (fire.hour, fire.minute) == (5, 30), row.trigger.next_fire_at
    assert grants.missing(row.trigger) == [], "a new hour is not a new command"


def test_a_hooks_changed_command_loses_its_grant_on_the_other_machine(tmp_path):
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    hooks = RECORDS["hooks"]
    hooks.write(a, "before each prompt")
    _agree(store, a, b)
    document = json.loads((b / "hooks.json").read_text())
    [mine] = document["hooks"]
    assert mine["enabled"] is False, "it arrives switched off"
    mine.update(enabled=True, capabilities={"providers": ["bash"]})
    (b / "hooks.json").write_text(json.dumps(document), encoding="utf-8")
    _cycle(store, b, "B")
    _cycle(store, a, "A")

    hooks.write(a, "before each prompt (renamed)")
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    [renamed] = json.loads((b / "hooks.json").read_text())["hooks"]
    assert renamed["name"] == "before each prompt (renamed)"
    assert renamed["enabled"] is True
    assert grants.missing(ScriptHook.from_dict(renamed)) == []

    document = json.loads((a / "hooks.json").read_text())
    document["hooks"][0]["provider_config"] = {"command": "/nonexistent/pc-other-hook"}
    (a / "hooks.json").write_text(json.dumps(document), encoding="utf-8")
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    [changed] = json.loads((b / "hooks.json").read_text())["hooks"]
    assert changed["provider_config"] == {"command": "/nonexistent/pc-other-hook"}
    assert changed["enabled"] is True
    assert grants.missing(ScriptHook.from_dict(changed)) == ["bash"]


# ── agreement is between two machines ────────────────────────────────────────


def _task(home: Path, title: str) -> None:
    (home / "tasks").mkdir(parents=True, exist_ok=True)
    (home / "tasks" / f"{RID}.json").write_text(json.dumps({"id": RID, "title": title}))


def _title(home: Path) -> str:
    return json.loads((home / "tasks" / f"{RID}.json").read_text())["title"]


def test_a_machine_that_had_not_caught_up_takes_the_edit_instead_of_undoing_it(tmp_path):
    """Three machines. B takes A's edit before C has pulled it. With one agreement every machine
    shared, B's taking it moved the agreement past the copy C held, C published that copy as its
    own, and A and B took it over the edit."""
    store = SharedStore()
    homes = {name: tmp_path / name for name in "ABC"}
    _task(homes["A"], "v1")
    for name in "ABCABC":
        _cycle(store, homes[name], name)
    assert {_title(h) for h in homes.values()} == {"v1"}

    _task(homes["A"], "v2")
    for name in "ABCABC":
        _cycle(store, homes[name], name)
    assert {name: _title(h) for name, h in homes.items()} == {"A": "v2", "B": "v2", "C": "v2"}


def test_what_two_machines_agree_on_stays_on_each_and_names_no_record_in_the_registry(tmp_path):
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    _task(a, "a private title")
    _agree(store, a, b)
    registry = store.objects[REGISTRY_KEY].decode("utf-8")
    assert RID not in registry and "ancestors" not in registry

    from personalclaw.durability.ancestors import Ancestors

    agreed = Ancestors(b / "sync").of("A", "tasks")
    assert list(agreed) == [RID]
    assert Ancestors(a / "sync").of("B", "tasks") == agreed


def test_a_file_that_is_one_machines_own_account_is_not_taken_over(tmp_path):
    """``spend.json`` is what this machine spent, one row for the whole file: another machine's
    is not an edit to take in, whichever of the two moved."""
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    for home in (a, b):
        home.mkdir(parents=True)
        (home / "spend.json").write_text(json.dumps({"days": {}}), encoding="utf-8")
    _agree(store, a, b)
    (a / "spend.json").write_text(json.dumps({"days": {"2026-09-28": 1.5}}), encoding="utf-8")
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    assert json.loads((b / "spend.json").read_text()) == {"days": {}}
