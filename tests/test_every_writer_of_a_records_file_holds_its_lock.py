"""Every writer of a file of records holds the file's one lock, so none writes over another.

🔴 A sync, a restore's merge and an import wrote ``triggers.json`` with no lock at all, beside a
trigger store whose every mutation re-read the file under ``.triggers.lock``: a sync that read the
file and wrote it back after the store had added an automation wrote that automation out of
existence, and nothing said so. The other stores of records took no lock of their own either, so
the same was true of a comment, a report, a pinned tile or an inbox item written while a sync ran.

Now each such file has one lock, ``.<name>.lock`` beside it, and the store's own writes and every
path that brings another machine's records in hold it, re-reading the file under it. Each case below
holds that lock as another writer would, shows the writer waiting for it, writes what the other
writer writes while holding it, and shows both writes in the file once it is let go.
"""

from __future__ import annotations

import fcntl
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

#: Long enough for an unlocked write to have landed; a locked one waits as long as it is held.
_WAIT = 1.0


def _home() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir()


@contextmanager
def _held(path: Path) -> Iterator[None]:
    """The file's lock, held as another writer holds it: ``.<stem>.lock`` beside it."""
    with (path.parent / f".{path.stem}.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _while_held(path: Path, write: Callable[[], Any], meanwhile: Callable[[], None]) -> None:
    """Start *write* while *path*'s lock is held, show it waits, do *meanwhile* under the lock as
    the other writer, then let go and let *write* finish."""
    failed: list[BaseException] = []

    def _run() -> None:
        try:
            write()
        except BaseException as exc:  # noqa: BLE001 — reported on the test's thread
            failed.append(exc)

    with _held(path):
        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(_WAIT)
        assert worker.is_alive(), f"{path.name} was written while another writer held its lock"
        meanwhile()
    worker.join(10)
    assert not worker.is_alive(), "the writer never finished once the lock was let go"
    assert not failed, failed


def _rewrite(path: Path, change: Callable[[Any], None]) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


# ── the automations: a sync and a restore's merge wait for the trigger store's own lock ─────


def _automation(tid: str, name: str) -> dict:
    return {"id": tid, "name": name, "kind": "clock", "enabled": True, "spec": {}}


def _automations() -> dict[str, dict]:
    doc = json.loads((_home() / "triggers.json").read_text(encoding="utf-8"))
    return {t["id"]: t for t in doc["triggers"]}


@pytest.fixture
def automations() -> Path:
    path = _home() / "triggers.json"
    path.write_text(
        json.dumps({"version": 1, "triggers": [_automation("mine", "Mine")]}), encoding="utf-8"
    )
    return path


def _the_store_adds_one() -> None:
    """What ``TriggerStore.upsert`` does inside its lock: re-read the rows, add one, write."""
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(_home())
    rows = store._read_rows()
    store._write([*rows, _automation("made-here", "Made here")])


def test_a_sync_of_automations_waits_for_the_trigger_stores_lock(automations):
    from personalclaw.durability import inventory as inv
    from personalclaw.durability.reconcile import reconcile_entry

    peer = {"version": 1, "triggers": [_automation("theirs", "Theirs")]}

    _while_held(
        automations,
        lambda: reconcile_entry(
            _home(), inv.by_id("triggers"), [{"id": "triggers.json", "data": peer}]
        ),
        _the_store_adds_one,
    )

    rows = _automations()
    assert set(rows) == {"mine", "made-here", "theirs"}, "no automation was written away"
    assert rows["theirs"]["enabled"] is False, "the other machine's arrives switched off"


def test_a_restores_merge_of_automations_waits_for_the_trigger_stores_lock(automations, tmp_path):
    from personalclaw.snapshot import _merge_triggers

    archived = tmp_path / "triggers.json"
    archived.write_text(
        json.dumps({"version": 1, "triggers": [_automation("archived", "Archived")]}),
        encoding="utf-8",
    )

    _while_held(automations, lambda: _merge_triggers(archived, automations), _the_store_adds_one)

    assert set(_automations()) == {"mine", "made-here", "archived"}


# ── every other store of records: a sync waits for the lock its store writes under ─────────


@dataclass(frozen=True)
class _Store:
    entry_id: str
    file: str
    here: Any
    there: Any
    #: What another writer adds under the lock while the sync waits for it.
    add: Callable[[Any], None]
    ids: Callable[[Any], set[str]]
    expected: frozenset[str]


def _listed(doc: Any) -> set[str]:
    return {r["id"] for r in doc}


def _keyed(key: str) -> Callable[[Any], set[str]]:
    return lambda doc: _listed(doc[key])


def _append(record: dict, key: str = "") -> Callable[[Any], None]:
    return lambda doc: (doc[key] if key else doc).append(record)


_ITEM = {
    "channel": "C1",
    "channel_name": "general",
    "thread_ts": None,
    "sender_id": "U1",
    "sender_name": "Someone",
}

SYNCED = [
    _Store(
        "inbox",
        "inbox.json",
        {"items": [{"id": "i_here_1.0", "message": "mine", **_ITEM}]},
        {"items": [{"id": "i_there_2.0", "message": "theirs", **_ITEM}]},
        _append({"id": "i_new_3.0", "message": "new", **_ITEM}, "items"),
        _keyed("items"),
        frozenset({"i_here_1.0", "i_there_2.0", "i_new_3.0"}),
    ),
    _Store(
        "doc_comments",
        "doc_comments.json",
        {"comments": [{"id": "c-here", "doc_id": "/a", "comment": "mine"}]},
        {"comments": [{"id": "c-there", "doc_id": "/b", "comment": "theirs"}]},
        _append({"id": "c-new", "doc_id": "/c", "comment": "new"}, "comments"),
        _keyed("comments"),
        frozenset({"c-here", "c-there", "c-new"}),
    ),
    _Store(
        "research_reports",
        "research_reports.json",
        [{"id": "r-here", "name": "Mine"}],
        [{"id": "r-there", "name": "Theirs"}],
        _append({"id": "r-new", "name": "New"}),
        _listed,
        frozenset({"r-here", "r-there", "r-new"}),
    ),
    _Store(
        "tags",
        "tags.json",
        [{"id": "t-here"}],
        [{"id": "t-there"}],
        _append({"id": "t-new"}),
        _listed,
        frozenset({"t-here", "t-there", "t-new"}),
    ),
    _Store(
        "tag_boards",
        "tag_boards.json",
        [{"id": "b-here"}],
        [{"id": "b-there"}],
        _append({"id": "b-new"}),
        _listed,
        frozenset({"b-here", "b-there", "b-new"}),
    ),
    _Store(
        "folders",
        "folders.json",
        [{"id": "f-here"}],
        [{"id": "f-there"}],
        _append({"id": "f-new"}),
        _listed,
        frozenset({"f-here", "f-there", "f-new"}),
    ),
    _Store(
        "dashboard_views",
        "dashboard_views.json",
        {"views": [{"id": "v-here", "name": "Mine"}], "overlay": {}},
        {"views": [{"id": "v-there", "name": "Theirs"}], "overlay": {}},
        _append({"id": "v-new", "name": "New"}, "views"),
        _keyed("views"),
        frozenset({"v-here", "v-there", "v-new"}),
    ),
    _Store(
        "hooks",
        "hooks.json",
        {"hooks": [{"id": "h-here", "name": "Mine"}]},
        {"hooks": [{"id": "h-there", "name": "Theirs"}]},
        _append({"id": "h-new", "name": "New"}, "hooks"),
        _keyed("hooks"),
        frozenset({"h-here", "h-there", "h-new"}),
    ),
]


@pytest.mark.parametrize("store", SYNCED, ids=lambda s: s.entry_id)
def test_a_sync_waits_for_the_lock_the_store_writes_under(store):
    from personalclaw.durability import inventory as inv
    from personalclaw.durability.reconcile import reconcile_entry

    path = _home() / store.file
    path.write_text(json.dumps(store.here), encoding="utf-8")

    _while_held(
        path,
        lambda: reconcile_entry(
            _home(), inv.by_id(store.entry_id), [{"id": store.file, "data": store.there}]
        ),
        lambda: _rewrite(path, store.add),
    )

    assert store.ids(json.loads(path.read_text(encoding="utf-8"))) == store.expected


# ── and each store's own write waits for it too, keeping what the other writer wrote ────────


def _doc_comment() -> None:
    from personalclaw import doc_comments

    doc_comments.add(doc_id="/plan.md", comment="mine, new")


def _view() -> None:
    from personalclaw.dashboard import views_store

    views_store.create_view("Mine, new")


def _report() -> None:
    from personalclaw.knowledge.research_reports import ReportDefinition, save_report
    from personalclaw.schedule import ScheduleDefinition

    save_report(
        ReportDefinition(
            id="r-new", name="New", prompt="What changed?", schedule=ScheduleDefinition(kind="")
        )
    )


def _inbox_item() -> Callable[[], None]:
    from personalclaw.inbox import InboxItem, InboxStore

    live = InboxStore()
    live.load()

    def _write() -> None:
        live.add(InboxItem(id="i_new_3.0", message="new", **_ITEM))
        live.save()

    return _write


def _tag(tmp_path: Path) -> Callable[[], None]:
    from chat_test_helpers import _make_state

    state = _make_state(tmp_path)
    state.load_tags()

    def _write() -> None:
        state._tags.append({"id": "t-new"})
        state.save_tags()

    return _write


def _hook() -> Callable[[], None]:
    from personalclaw.hooks import ScriptHookStore

    live = ScriptHookStore()

    def _write() -> None:
        live.create({"id": "h-new", "name": "New"})

    return _write


@dataclass(frozen=True)
class _Writer:
    name: str
    file: str
    here: Any
    #: The store's own write; for one that holds its records in memory, built after it loaded.
    write: Callable[[Path], Callable[[], None]]
    #: What another writer — a sync — puts in the file while the store waits for the lock.
    theirs: Callable[[Any], None]
    ids: Callable[[Any], set[str]]
    #: What the file must hold once both wrote, besides whatever the store's write added.
    kept: frozenset[str]


WRITERS = [
    _Writer(
        "a document comment",
        "doc_comments.json",
        {"comments": [{"id": "c-here", "doc_id": "/a", "comment": "mine"}]},
        lambda _tmp: _doc_comment,
        _append({"id": "c-there", "doc_id": "/b", "comment": "theirs"}, "comments"),
        _keyed("comments"),
        frozenset({"c-here", "c-there"}),
    ),
    _Writer(
        "a dashboard view",
        "dashboard_views.json",
        {"views": [{"id": "v-here", "name": "Mine"}], "overlay": {}},
        lambda _tmp: _view,
        _append({"id": "v-there", "name": "Theirs"}, "views"),
        _keyed("views"),
        frozenset({"v-here", "v-there"}),
    ),
    _Writer(
        "a research report",
        "research_reports.json",
        [{"id": "r-here", "name": "Mine"}],
        lambda _tmp: _report,
        _append({"id": "r-there", "name": "Theirs"}),
        _listed,
        frozenset({"r-here", "r-there", "r-new"}),
    ),
    _Writer(
        "an inbox item",
        "inbox.json",
        {"items": [{"id": "i_here_1.0", "message": "mine", **_ITEM}]},
        lambda _tmp: _inbox_item(),
        _append({"id": "i_there_2.0", "message": "theirs", **_ITEM}, "items"),
        _keyed("items"),
        frozenset({"i_here_1.0", "i_there_2.0", "i_new_3.0"}),
    ),
    _Writer(
        "a tag",
        "tags.json",
        [{"id": "t-here"}],
        _tag,
        _append({"id": "t-there"}),
        _listed,
        frozenset({"t-here", "t-there", "t-new"}),
    ),
    _Writer(
        "a hook",
        "hooks.json",
        {"hooks": [{"id": "h-here", "name": "Mine"}]},
        lambda _tmp: _hook(),
        _append({"id": "h-there", "name": "Theirs"}, "hooks"),
        _keyed("hooks"),
        frozenset({"h-here", "h-there", "h-new"}),
    ),
]


@pytest.mark.parametrize("writer", WRITERS, ids=lambda w: w.name)
def test_the_store_writes_under_the_lock_and_keeps_what_the_other_writer_wrote(writer, tmp_path):
    path = _home() / writer.file
    path.write_text(json.dumps(writer.here), encoding="utf-8")
    write = writer.write(tmp_path)

    _while_held(path, write, lambda: _rewrite(path, writer.theirs))

    assert writer.kept <= writer.ids(json.loads(path.read_text(encoding="utf-8")))
