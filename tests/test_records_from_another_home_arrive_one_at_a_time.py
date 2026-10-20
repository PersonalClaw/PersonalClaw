"""Another machine's records arrive in a store that already has its own, one record at a time.

🔴 Every store kept in one JSON file of user records — the inbox, the document comments, the
research reports, the tags, the tag boards, the folders and the dashboard's views — was merged as
ONE record, the whole file. A sync kept the file a home already had and dropped the other
machine's whole, so another machine's records reached only a home that had none; a restore's merge
and an import copied the archive's file only where the home had none, and dropped every record of
it anywhere else. Now each record is one of its own, told apart by its id, by all three: a record
this home lacks arrives, after the ones it has, which stay exactly as they are.

A record that carries a grant or runs unattended arrives the way an automation does: a research
report runs on its cadence, spending a model call each time, so one from elsewhere arrives switched
off, without what happened to it there. A store whose every record IS a grant — which projects run
their scripts, which actions run without asking, which integrations may connect — is never merged
from another machine by a sync at all, and a store restored only whole (the configuration) is left
exactly as it is, rather than failing the whole pull.

And a store that holds its records in memory between writes — the inbox, the tags, the folders,
the boards, the hooks — keeps what a sync wrote while it ran, instead of writing the file back as it
was when it last read it, and shows it.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest


def _home() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir()


def _entry(entry_id: str):
    from personalclaw.durability import inventory as inv

    entry = inv.by_id(entry_id)
    assert entry is not None, entry_id
    return entry


def _listed(doc: Any) -> set[str]:
    return {r["id"] for r in doc if isinstance(r, dict) and "id" in r}


def _keyed(key: str) -> Callable[[Any], set[str]]:
    return lambda doc: _listed(doc.get(key, []))


def _views(doc: Any) -> set[str]:
    """A view by its id, and a pinned tile by the view it is on and what it shows."""
    views = {f"view:{v['id']}" for v in doc.get("views", [])}
    tiles = {
        f"tile:{view}:{t['ref']}" for view, pinned in doc.get("overlay", {}).items() for t in pinned
    }
    return views | tiles


#: What every inbox item holds besides its id and message.
_ITEM = {
    "channel": "C1",
    "channel_name": "general",
    "thread_ts": None,
    "sender_id": "U1",
    "sender_name": "Someone",
}


@dataclass(frozen=True)
class _Store:
    entry_id: str
    file: str
    here: Any
    there: Any
    ids: Callable[[Any], set[str]]
    #: What only the other machine's file holds, and must arrive.
    theirs: frozenset[str]
    mine: frozenset[str]


STORES = [
    _Store(
        "inbox",
        "inbox.json",
        {"items": [{"id": "message_here_1.0", "message": "mine", **_ITEM}]},
        {"items": [{"id": "message_there_2.0", "message": "theirs", **_ITEM}]},
        _keyed("items"),
        frozenset({"message_there_2.0"}),
        frozenset({"message_here_1.0"}),
    ),
    _Store(
        "doc_comments",
        "doc_comments.json",
        {"comments": [{"id": "c-here", "doc_id": "/notes.md", "comment": "mine", "ts": 1.0}]},
        {"comments": [{"id": "c-there", "doc_id": "/plan.md", "comment": "theirs", "ts": 2.0}]},
        _keyed("comments"),
        frozenset({"c-there"}),
        frozenset({"c-here"}),
    ),
    _Store(
        "research_reports",
        "research_reports.json",
        [{"id": "rpt-here", "name": "Mine", "enabled": True}],
        [{"id": "rpt-there", "name": "Theirs", "enabled": True}],
        _listed,
        frozenset({"rpt-there"}),
        frozenset({"rpt-here"}),
    ),
    _Store(
        "tags",
        "tags.json",
        [{"id": "t-here", "name": "Mine"}],
        [{"id": "t-there", "name": "Theirs"}],
        _listed,
        frozenset({"t-there"}),
        frozenset({"t-here"}),
    ),
    _Store(
        "tag_boards",
        "tag_boards.json",
        [{"id": "col-here", "name": "Mine"}],
        [{"id": "col-there", "name": "Theirs"}],
        _listed,
        frozenset({"col-there"}),
        frozenset({"col-here"}),
    ),
    _Store(
        "folders",
        "folders.json",
        [{"id": "f-here", "name": "Mine"}],
        [{"id": "f-there", "name": "Theirs"}],
        _listed,
        frozenset({"f-there"}),
        frozenset({"f-here"}),
    ),
    _Store(
        "dashboard_views",
        "dashboard_views.json",
        {
            "views": [{"id": "v-here", "name": "Mine", "tiles": []}],
            "overlay": {"overview": [{"ref": "artifact:mine", "order": 0}]},
        },
        {
            "views": [{"id": "v-there", "name": "Theirs", "tiles": []}],
            "overlay": {"overview": [{"ref": "artifact:theirs", "order": 0}]},
        },
        _views,
        frozenset({"view:v-there", "tile:overview:artifact:theirs"}),
        frozenset({"view:v-here", "tile:overview:artifact:mine"}),
    ),
]

#: The stores a restore's merge copied only into a home without one. The inbox, tags and boards had
#: a per-file union there already; the sync and the import dropped theirs everywhere.
COPIED_BY_A_RESTORE = [s for s in STORES if s.entry_id not in ("inbox", "tags", "tag_boards")]


def _seed(store: _Store) -> Path:
    path = _home() / store.file
    path.write_text(json.dumps(store.here), encoding="utf-8")
    return path


def _held(store: _Store, path: Path) -> set[str]:
    return store.ids(json.loads(path.read_text(encoding="utf-8")))


def _by_sync(store: _Store) -> None:
    """A peer's export of the file, as a sync pulls it: one row, the whole file."""
    from personalclaw.durability.reconcile import reconcile_entry

    result = reconcile_entry(
        _home(), _entry(store.entry_id), [{"id": store.file, "data": store.there}]
    )
    assert result.verdict == "consumed", result.detail


def _by_restore(store: _Store, tmp_path: Path) -> None:
    from personalclaw.snapshot import _do_merge

    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "config.json").write_text("{}", encoding="utf-8")
    (snap / store.file).write_text(json.dumps(store.there), encoding="utf-8")
    _do_merge(snap, _home(), None)


def _by_import(store: _Store, tmp_path: Path) -> None:
    from personalclaw.portability import apply_import_zip

    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"personalclaw-export/{store.file}", json.dumps(store.there))
    apply_import_zip(archive, mode="merge")


@pytest.mark.parametrize("store", STORES, ids=lambda s: s.entry_id)
def test_a_sync_brings_another_machines_records_into_a_store_this_home_has(store):
    path = _seed(store)

    _by_sync(store)

    assert store.theirs | store.mine <= _held(store, path), "their records arrived beside mine"


@pytest.mark.parametrize("store", COPIED_BY_A_RESTORE, ids=lambda s: s.entry_id)
def test_a_restores_merge_brings_the_archives_records_into_a_store_this_home_has(store, tmp_path):
    path = _seed(store)

    _by_restore(store, tmp_path)

    assert store.theirs | store.mine <= _held(store, path)


@pytest.mark.parametrize("store", STORES, ids=lambda s: s.entry_id)
def test_an_import_brings_the_archives_records_into_a_store_this_home_has(store, tmp_path):
    path = _seed(store)

    _by_import(store, tmp_path)

    assert store.theirs | store.mine <= _held(store, path)


@pytest.mark.parametrize("store", STORES, ids=lambda s: s.entry_id)
def test_a_record_this_home_has_stays_as_it_is(store):
    """The merge fills in what the home lacks; a record both hold keeps this home's version."""
    path = _seed(store)
    edited = json.loads(json.dumps(store.there))
    # The other machine holds this home's record too, edited there.
    if isinstance(edited, list):
        edited.insert(0, {**store.here[0], "name": "edited there"})
    elif "views" in edited:
        edited["views"].insert(0, {**store.here["views"][0], "name": "edited there"})
    else:
        key = next(iter(edited))
        edited[key].insert(0, {**store.here[key][0], "comment": "edited", "message": "edited"})

    from personalclaw.durability.reconcile import reconcile_entry

    reconcile_entry(_home(), _entry(store.entry_id), [{"id": store.file, "data": edited}])

    after = json.loads(path.read_text(encoding="utf-8"))
    assert "edited" not in json.dumps(after), "this home's version stands"
    assert store.theirs | store.mine <= store.ids(after), "and theirs arrived beside it"


def test_a_report_from_another_machine_arrives_switched_off_without_its_runs(tmp_path):
    """A standing report runs on its cadence, unattended, and each run is a model call: from
    elsewhere it arrives switched off, without when it ran there, how that went or how far it read,
    by every way one arrives."""
    theirs = {
        "id": "rpt-there",
        "name": "Theirs",
        "prompt": "What changed?",
        "enabled": True,
        "last_run_ts": 1700000000.0,
        "last_status": "error",
        "last_error": "their provider was down",
        "watermark_ts": 1699999000.0,
    }
    store = _Store(
        "research_reports",
        "research_reports.json",
        [],
        [theirs],
        _listed,
        frozenset({"rpt-there"}),
        frozenset(),
    )
    from personalclaw.knowledge.research_reports import load_reports

    for arrive in (lambda: _by_sync(store), lambda: _by_restore(store, tmp_path / "r")):
        (tmp_path / "r").mkdir(exist_ok=True)
        path = _seed(store)
        arrive()
        rows = json.loads(path.read_text(encoding="utf-8"))
        assert [r["id"] for r in rows] == ["rpt-there"], "their report arrived"
        (row,) = rows
        assert row["enabled"] is False and row["name"] == "Theirs", row
        for one_homes in ("last_run_ts", "last_status", "last_error", "watermark_ts"):
            assert one_homes not in row, (one_homes, row)
        (report,) = load_reports()
        assert (report.enabled, report.last_run_ts, report.watermark_ts) == (False, None, 0.0)
        path.unlink()


@pytest.mark.parametrize(
    "entry_id, grant",
    [
        ("project_trust", {"/Users/me/project": {"mode": "trust"}}),
        ("autonomy_rungs", {"rungs": {"email.send": "auto"}}),
        ("autonomy_reversals", {"reversals": [{"id": "r1", "action": "email.send"}]}),
        ("inbound_clients", {"clients": [{"id": "c1", "token_sha256": "ab" * 32}]}),
        ("inbound_tokens", {"ab" * 32: {"issued_at": 1.0, "expires_at": 9e9}}),
    ],
)
def test_a_grant_another_machine_gave_never_arrives_by_a_sync(entry_id, grant):
    """Every record in these stores is a yes the owner gave on that machine — a project's scripts
    run, an action runs without asking, an integration's token opens the gateway. A sync never
    brings one in, into a home without the store either."""
    from personalclaw.durability.reconcile import reconcile_entry

    entry = _entry(entry_id)
    result = reconcile_entry(_home(), entry, [{"id": Path(entry.path).name, "data": grant}])

    assert result.verdict == "consumed", result.detail
    assert not (_home() / entry.path).exists(), "another machine's grant arrived"


def test_a_store_restored_only_whole_is_left_as_it_is_and_the_pull_goes_on():
    """The configuration is restored whole or not at all. A sync used to hand it to the row merge,
    which refuses such a store, so every pull that carried one read as a bad payload."""
    from personalclaw.durability.reconcile import reconcile_entry

    config = _home() / "config.json"
    config.write_text(json.dumps({"theme": "mine"}), encoding="utf-8")

    result = reconcile_entry(
        _home(), _entry("config"), [{"id": "config.json", "data": {"theme": "theirs"}}]
    )

    assert result.verdict == "consumed", result.detail
    assert json.loads(config.read_text()) == {"theme": "mine"}


# ── a store that holds its records in memory keeps what a sync wrote while it ran ──────────


def _sync_brings(store: _Store) -> None:
    """Another machine's records, written into the file by a sync while the store runs."""
    from personalclaw.durability.reconcile import reconcile_entry

    reconcile_entry(_home(), _entry(store.entry_id), [{"id": store.file, "data": store.there}])


def test_the_running_inbox_keeps_and_shows_an_item_a_sync_brought():
    from personalclaw.inbox import InboxItem, InboxStore

    store = next(s for s in STORES if s.entry_id == "inbox")
    mine = InboxItem(
        id="message_here_1.0",
        channel="C1",
        channel_name="general",
        thread_ts=None,
        message="mine",
        sender_id="U1",
        sender_name="Me",
    )
    live = InboxStore()
    live.add(mine)
    live.save()
    _sync_brings(store)

    live.update("message_here_1.0", draft="a reply")  # the running service writes the file

    on_disk = _held(store, _home() / "inbox.json")
    assert {"message_here_1.0", "message_there_2.0"} <= on_disk, "the service wrote it away"
    live.refresh()
    assert set(live.items) == {"message_here_1.0", "message_there_2.0"}, "and shows it"
    assert live.items["message_here_1.0"].draft == "a reply"


@pytest.mark.parametrize(
    "entry_id, held, save, refresh",
    [
        ("tags", "_tags", "save_tags", "refresh_tags"),
        ("tag_boards", "_tag_boards", "save_tag_boards", "refresh_tag_boards"),
        ("folders", "_folders", "save_folders", "refresh_folders"),
    ],
)
def test_the_running_dashboard_keeps_and_shows_what_a_sync_brought(
    entry_id, held, save, refresh, tmp_path
):
    from chat_test_helpers import _make_state

    store = next(s for s in STORES if s.entry_id == entry_id)
    _seed(store)
    state = _make_state(tmp_path)
    state.load_folders()
    state.load_tags()
    _sync_brings(store)

    getattr(state, held).append({"id": "made-here-now", "name": "New"})
    getattr(state, save)()  # a tag, board or folder made in the dashboard meanwhile

    on_disk = _held(store, _home() / store.file)
    assert store.mine | store.theirs | {"made-here-now"} <= on_disk, "the dashboard wrote it away"
    getattr(state, refresh)()
    assert store.theirs <= _listed(getattr(state, held)), "and shows it"


def test_the_running_hook_store_keeps_and_shows_a_hook_a_sync_brought():
    from personalclaw.hooks import ScriptHookStore

    (_home() / "hooks.json").write_text(
        json.dumps({"hooks": [{"id": "h-here", "name": "Mine", "event": "Stop"}]}),
        encoding="utf-8",
    )
    live = ScriptHookStore()
    from personalclaw.durability.reconcile import reconcile_entry

    reconcile_entry(
        _home(),
        _entry("hooks"),
        [{"id": "hooks.json", "data": {"hooks": [{"id": "h-there", "name": "Theirs"}]}}],
    )

    live.create({"id": "h-new", "name": "New", "event": "Stop"})

    on_disk = json.loads((_home() / "hooks.json").read_text())["hooks"]
    assert {h["id"] for h in on_disk} == {"h-here", "h-there", "h-new"}, on_disk
    arrived = {h.id: h for h in live.list_all()}
    assert set(arrived) == {"h-here", "h-there", "h-new"}
    assert arrived["h-there"].enabled is False, "it arrived switched off, as a sync brings one"
