"""A time the gateway stores or serves names one instant, whoever reads it and wherever they are.

Measured: seven watched folders, each set to every 5 minutes, read "polled just now · next in 3h"
right after their first poll. The gateway ran in America/Toronto and wrote
``datetime.now().isoformat()``, a wall-clock reading with no offset (``2026-10-01T01:22:09``);
the browser, in America/Los_Angeles, parsed that as ITS local time, three hours later. Any split
between the gateway's zone and the reader's does it: a phone abroad, a gateway on another machine.

So every stamp is written in UTC with its offset, and the stamps already on disk are read as the
local times they were. Each test here runs the gateway in one zone and reads the result the way a
client in another zone does.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore

GATEWAY_ZONE = "America/Toronto"
CLIENT_ZONE = "America/Los_Angeles"


@pytest.fixture()
def gateway_zone(monkeypatch):
    """Run this process (the gateway) in Toronto, and put the C library's zone back after."""
    with monkeypatch.context() as m:
        m.setenv("TZ", GATEWAY_ZONE)
        time.tzset()
        yield
    time.tzset()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


def read_elsewhere(stamp: str, zone: str = CLIENT_ZONE) -> float:
    """The instant a client in *zone* reads *stamp* as, in epoch seconds.

    What a browser's ``Date.parse`` does: a date-time with an offset is that instant, and one
    without is read as the reader's OWN local time.
    """
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(zone))
    return parsed.timestamp()


def has_offset(stamp: str) -> bool:
    return datetime.fromisoformat(stamp).tzinfo is not None


class _Queue:
    def enqueue(self, item_id: str) -> None:
        pass

    def enqueue_background(self, item_id: str) -> None:
        pass


def _cfg():
    from personalclaw.config.loader import SourcesConfig

    return SourcesConfig(
        enabled=True,
        poll_interval_default_secs=300,
        network_floor_secs=900,
        max_sources=100,
        max_items_per_poll=50,
    )


# ── a polled folder's two times ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_folder_polled_now_is_due_in_five_minutes_for_a_reader_in_another_zone(
    gateway_zone, tmp_path
):
    from personalclaw.knowledge_providers.dir_source import DirSourceProvider

    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "a.md").write_text("# A\n", encoding="utf-8")
    sid = store.create_source(
        name="notes",
        provider="watched-dir",
        kind="dir",
        spec={"path": str(folder)},
        poll_interval_secs=300,
    )
    polled = time.time()
    engine = SourceEngine(
        store,
        _Queue(),
        providers_lister=lambda: [DirSourceProvider(store, now_fn=lambda: polled)],
        config_loader=_cfg,
        now_fn=lambda: polled,
    )

    await engine.poll_source(store.get_source(sid), _cfg())

    row = store.get_source(sid)
    for field in ("created_at", "updated_at", "last_poll_at", "next_poll_at"):
        assert has_offset(row[field]), (field, row[field])
    # "next in 5m", not "next in 3h"; "polled just now" for the next five minutes, not three hours.
    assert read_elsewhere(row["next_poll_at"]) == pytest.approx(polled + 300, abs=1)
    assert read_elsewhere(row["last_poll_at"]) == pytest.approx(polled, abs=5)


# ── every knowledge write ──────────────────────────────────────────────────────────────────────


def _zone_less_stamps(store: KnowledgeStore) -> list[str]:
    """Every ``*_at`` value in the store that is a date-time with no offset."""
    found = []
    tables = [
        r[0]
        for r in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE '%VIRTUAL%'"
        ).fetchall()
    ]
    for table in tables:
        for col in [r[1] for r in store.db.execute(f'PRAGMA table_info("{table}")').fetchall()]:
            if not col.endswith("_at"):
                continue
            for (value,) in store.db.execute(f'SELECT "{col}" FROM "{table}"').fetchall():
                if isinstance(value, str) and "T" in value and not has_offset(value):
                    found.append(f"{table}.{col} = {value}")
    return found


def test_every_time_the_library_writes_carries_its_offset(gateway_zone, tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item = store.create_typed_item(item_type="note", title="Plan", content="body", tags=["work"])
    store.update_item(item, content="body, edited")
    store.record_mention_sweep(item)
    store.record_similarity_sweep(item)
    entity = store.add_entity("Ada", "person")
    other = store.add_entity("Analytical Engine", "thing")
    store.backfill_entity_description(entity, "mathematician")
    store.merge_entity_aliases(entity, ["Countess of Lovelace"])
    store.add_entity_relation(entity, other, "designed_for")
    store.add_mention(item, entity, "Ada wrote it")
    collection = store.create_collection(name="Reading")
    store.add_to_collection(collection, item)
    sid = store.create_source(name="s", provider="watched-dir", kind="dir", spec={"path": "/x"})
    store.update_source(sid, name="renamed")
    store.record_poll(sid, cursor="c1", new_count=1, next_poll_at="")

    written = _zone_less_stamps(store)

    assert store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1, "the scan saw rows"
    assert written == []


def test_an_item_whose_folder_copy_vanished_records_when_with_its_offset(gateway_zone, tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item = store.create_typed_item(item_type="note", title="Gone", content="body")

    assert store.archive_source_item(item)

    deleted_at = store.get_item(item)["file_metadata"]["source_deleted_at"]
    assert has_offset(deleted_at), deleted_at


# ── the times already on disk ──────────────────────────────────────────────────────────────────


def test_a_library_written_before_offsets_reads_as_the_instants_it_recorded(gateway_zone, tmp_path):
    """The old writer's naive local times become the UTC instants they were, once, and stay."""
    path = str(tmp_path / "knowledge.db")
    store = KnowledgeStore(path)
    sid = store.create_source(name="s", provider="watched-dir", kind="dir", spec={"path": "/x"})
    item = store.create_typed_item(item_type="note", title="Old", content="body")
    # What the gateway wrote in Toronto before: local wall-clock readings with no offset, beside
    # a value another writer already stored with one and an expiry that is a day, not a time.
    store.db.execute(
        "UPDATE sources SET last_poll_at = '2026-10-01T01:17:09.493101', "
        "next_poll_at = '2026-10-01T01:22:09', created_at = '2026-10-01T01:05:00' WHERE id = ?",
        (sid,),
    )
    store.db.execute(
        "UPDATE items SET created_at = '2026-10-01T01:15:00', "
        "updated_at = '2026-10-01T05:16:00+00:00', expires_at = '2026-12-01' WHERE id = ?",
        (item,),
    )
    store.db.commit()
    store.db.close()

    reopened = KnowledgeStore(path)
    row = reopened.get_source(sid)
    stored = reopened.db.execute(
        "SELECT created_at, updated_at, expires_at FROM items WHERE id = ?", (item,)
    ).fetchone()

    assert row["last_poll_at"] == "2026-10-01T05:17:09.493101+00:00"
    assert row["next_poll_at"] == "2026-10-01T05:22:09+00:00"
    assert row["created_at"] == "2026-10-01T05:05:00+00:00"
    assert stored["created_at"] == "2026-10-01T05:15:00+00:00"
    assert stored["updated_at"] == "2026-10-01T05:16:00+00:00", "an instant keeps its text"
    assert stored["expires_at"] == "2026-12-01", "a day is not an instant"
    assert _zone_less_stamps(reopened) == []
    reopened.db.close()

    again = KnowledgeStore(path)
    assert again.get_source(sid)["last_poll_at"] == "2026-10-01T05:17:09.493101+00:00"
    again.db.close()


def test_what_changed_since_a_local_time_is_measured_against_the_stored_instants(
    gateway_zone, tmp_path
):
    """ "Changed since 09:00" compares text against the stored UTC instants, so the bound is put
    in their form first; compared as typed, 08:30 in Toronto (12:30 UTC) read as after 09:00."""
    from personalclaw.knowledge import structural

    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    before = store.create_typed_item(item_type="note", title="Before nine", content="a")
    after = store.create_typed_item(item_type="note", title="After nine", content="b")
    for item, stamp in (
        (before, "2026-10-01T12:30:00+00:00"),
        (after, "2026-10-01T13:30:00+00:00"),
    ):
        store.db.execute("UPDATE items SET updated_at = ? WHERE id = ?", (stamp, item))
    store.db.commit()

    answer = structural.StructuralRetriever(store).query(
        structural.CHANGED_SINCE, since="2026-10-01T09:00:00"
    )

    assert [hit.item_id for hit in answer.hits] == [after]
    assert answer.hits[0].path[0].detail["since"] == "2026-10-01T09:00:00", "as the caller said it"


# ── a day, not an instant: the journal's editing window ────────────────────────────────────────


@pytest.fixture()
def zone_where_today_is_not_utcs_today(monkeypatch):
    """A zone whose calendar day differs from UTC's at this moment: twelve hours either side."""
    zone = "Etc/GMT+12" if datetime.now(timezone.utc).hour < 12 else "Etc/GMT-12"
    with monkeypatch.context() as m:
        m.setenv("TZ", zone)
        time.tzset()
        yield zone
    time.tzset()


def test_a_journal_written_today_is_editable_today_wherever_the_gateway_is(
    zone_where_today_is_not_utcs_today, tmp_path
):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    jid = store.create_typed_item(item_type="journal", title="J", content="today's entry")
    # Written now, with its offset: how every library write records it.
    _set_created(store, jid, datetime.now(timezone.utc))

    resp = _patch(store, jid, {"content": "edited today"})

    assert resp.status == 200, resp.body
    assert store.get_item(jid)["content"] == "edited today"


def test_a_journal_from_yesterday_stays_locked_wherever_the_gateway_is(
    zone_where_today_is_not_utcs_today, tmp_path
):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    jid = store.create_typed_item(item_type="journal", title="J", content="yesterday's entry")
    _set_created(store, jid, datetime.now(timezone.utc) - timedelta(days=1))

    resp = _patch(store, jid, {"content": "rewrite"})

    assert resp.status == 403
    assert store.get_item(jid)["content"] == "yesterday's entry"


def test_the_agent_tool_applies_the_same_journal_day(zone_where_today_is_not_utcs_today, tmp_path):
    from unittest.mock import patch

    import personalclaw.agents.native.builtin_tools as bt
    import personalclaw.knowledge as K

    with (
        patch.object(K, "_store", None),
        patch("personalclaw.knowledge.knowledge_db_path", lambda: str(tmp_path / "k.db")),
    ):
        store = K.get_knowledge_store()
        jid = store.create_typed_item(item_type="journal", title="J", content="entry")
        _set_created(store, jid, datetime.now(timezone.utc))

        result = asyncio.run(
            bt.NativeBuiltinToolProvider().invoke("knowledge_update", {"id": jid, "content": "x"})
        )

    assert result.success, result.error


def _set_created(store: KnowledgeStore, item_id: str, when: datetime) -> None:
    store.db.execute("UPDATE items SET created_at = ? WHERE id = ?", (when.isoformat(), item_id))
    store.db.commit()


def _patch(store: KnowledgeStore, item_id: str, body: dict):
    """PATCH the item through the handler, naming the revision it replaces as the page does."""
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import knowledge as H
    from personalclaw.knowledge_providers.native import create_native_provider

    sink: list[str] = []
    provider = create_native_provider(store, enqueue=sink.append)
    app = web.Application()
    app["state"] = SimpleNamespace(
        knowledge_store=store,
        knowledge_provider=lambda: provider,
        knowledge_ingest_queue=lambda: SimpleNamespace(
            enqueue=sink.append, enqueue_background=sink.append
        ),
    )

    def request(method: str, headers: dict, payload: dict | None):
        req = make_mocked_request(method, "/", app=app, match_info={"id": item_id}, headers=headers)
        if payload is not None:

            async def _json():
                return payload

            req.json = _json  # type: ignore[method-assign]
        return req

    read = asyncio.run(H.get_item(request("GET", {}, None)))
    base = json.loads(read.body)["content_revision"]
    return asyncio.run(H.update_item(request("PATCH", {"If-Match": f'"{base}"'}, body)))


# ── chat transcripts and rooms ─────────────────────────────────────────────────────────────────


def test_a_message_the_gateway_logs_names_its_instant_for_a_reader_elsewhere(
    gateway_zone, tmp_path
):
    from personalclaw.history import ConversationLog

    log = ConversationLog(base_dir=tmp_path / "sessions")
    before = time.time()
    log.append("channel:general", "user", "hello")

    first, message = (
        json.loads(line)
        for line in (tmp_path / "sessions" / "channel_general.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert has_offset(first["created_at"]), first["created_at"]
    assert has_offset(message["ts"]), message["ts"]
    assert read_elsewhere(message["ts"]) == pytest.approx(before, abs=5)


def test_a_transcript_written_before_offsets_reads_as_the_instants_it_recorded(
    gateway_zone, tmp_path
):
    from personalclaw.history import ConversationLog

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "channel_general.jsonl").write_text(
        json.dumps({"_type": "metadata", "created_at": "2026-10-01T01:10:00"})
        + "\n"
        + json.dumps({"role": "user", "content": "hi", "ts": "2026-10-01T01:17:09.5"})
        + "\n"
        + json.dumps({"role": "assistant", "content": "yo", "ts": "2026-10-01T05:18:00+00:00"})
        + "\n",
        encoding="utf-8",
    )
    log = ConversationLog(base_dir=sessions)

    old, new = log.read_messages("channel:general")
    listed = next(s for s in log.list_sessions() if s["key"] == "channel_general")

    assert old["ts"] == "2026-10-01T05:17:09.500000+00:00"
    assert new["ts"] == "2026-10-01T05:18:00+00:00", "a stamp with an offset keeps its text"
    assert log.get_metadata("channel:general")["created_at"] == "2026-10-01T05:10:00+00:00"
    assert listed["created"] == "2026-10-01T05:10:00+00:00"


@pytest.fixture()
def rooms(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    from personalclaw.rooms import store

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return store


def test_a_room_says_when_it_was_created_for_a_reader_elsewhere(gateway_zone, rooms):
    before = time.time()
    room = rooms.create_room("Planning")

    assert has_offset(room.created_at), room.created_at
    assert read_elsewhere(room.created_at) == pytest.approx(before, abs=5)


def test_a_room_created_before_offsets_reads_as_the_instant_it_recorded(gateway_zone, rooms):
    room = rooms.create_room("Planning")
    index = rooms._index_path()
    data = json.loads(index.read_text(encoding="utf-8"))
    data["rooms"][0]["created_at"] = "2026-10-01T01:00:00"
    index.write_text(json.dumps(data), encoding="utf-8")

    (listed,) = [r for r in rooms.list_rooms() if r.id == room.id]

    assert listed.created_at == "2026-10-01T05:00:00+00:00"


# ── the consolidation floor reads the library's own times ──────────────────────────────────────


def test_the_consolidation_floor_measures_from_the_last_consolidated_write(gateway_zone, tmp_path):
    """`knowledge.consolidate_min_hours` gates a pass on the hours since the last one, read off
    the newest consolidated item's ``updated_at``. That read expected ``…:SSZ`` and the library
    has never written that shape, so every pass read 10,000 hours and the floor never held."""
    from personalclaw.action_providers import knowledge_maintain_provider as kmp

    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item = store.create_typed_item(item_type="note", title="Merged", content="body")
    an_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.db.execute(
        "UPDATE items SET updated_at = ?, file_metadata = ? WHERE id = ?",
        (an_hour_ago, json.dumps({"consolidated": True}), item),
    )
    store.db.commit()

    assert kmp._hours_since_last_pass(store) == pytest.approx(1.0, abs=0.05)


# ── the writers that drop the zone, census ─────────────────────────────────────────────────────

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _is_zone_less_clock(node: ast.AST) -> bool:
    """``datetime.now()`` or ``datetime.fromtimestamp(x)``: this machine's wall clock, no zone."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.keywords:
        return False
    if node.func.attr == "now":
        return not node.args
    if node.func.attr == "fromtimestamp":
        return len(node.args) == 1
    return False


def _receivers(node: ast.AST) -> list[ast.AST]:
    """The values an expression can evaluate to: both sides of an ``or``, both arms of an ``if``."""
    if isinstance(node, ast.BoolOp):
        return [v for value in node.values for v in _receivers(value)]
    if isinstance(node, ast.IfExp):
        return _receivers(node.body) + _receivers(node.orelse)
    return [node]


def zone_less_writers(tree: ast.AST) -> list[int]:
    """Lines that turn a zone-less clock reading into text: ``….isoformat()`` on one, or a
    ``strftime`` that spells a date-time with no zone."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "isoformat" and any(
            _is_zone_less_clock(r) for r in _receivers(node.func.value)
        ):
            lines.append(node.lineno)
        elif (
            node.func.attr == "strftime"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("%Y-%m-%dT%H:%M")
            and not node.args[0].value.endswith(("Z", "%z"))
        ):
            lines.append(node.lineno)
    return lines


def test_the_census_sees_a_writer_that_drops_the_zone():
    snippet = (
        "from datetime import datetime\n"
        "import time\n"
        "a = datetime.now().isoformat()\n"
        "b = (now or datetime.now()).isoformat()\n"
        "c = datetime.fromtimestamp(t).isoformat(timespec='seconds')\n"
        "d = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())\n"
        "ok = datetime.now(timezone.utc).isoformat()\n"
        "ok2 = datetime.fromtimestamp(t, timezone.utc).isoformat()\n"
        "ok3 = datetime.now().astimezone().isoformat()\n"
        "ok4 = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())\n"
        "ok5 = datetime.now().strftime('%Y-%m-%d')\n"
    )
    assert zone_less_writers(ast.parse(snippet)) == [3, 4, 5, 6]


def test_no_writer_turns_a_zone_less_clock_reading_into_a_stored_or_served_time():
    found = {
        path.relative_to(SRC).as_posix(): lines
        for path in sorted(SRC.rglob("*.py"))
        if (lines := zone_less_writers(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert len(list(SRC.rglob("*.py"))) > 500, "the census read the tree"
    assert found == {}
