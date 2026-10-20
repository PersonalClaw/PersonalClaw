"""A home restored at another path keeps what its chats kept in the shared memory.

Every chat starts in the gateway's own workspace, and a chat working there shares the global memory
with every other chat and with the Memory page. A chat records the folder it works in, and it
recorded that workspace as a path, which is true only in the home, at the place, that wrote it. Its
defects, pinned here:

* a snapshot restored into a home at another path (another account's name, a Linux ``/home``), and
  an upgrade from a release whose workspace was another folder, made every such chat read as one
  working in a folder of its own: the first start moved what each had kept out of the shared
  memory into a folder memory named for the old workspace, which no chat there works in, so recall
  and the Memory page lost it, and the only trace was one line in the log;
* the restored chats went on working in the old home's workspace: on the same machine, another
  home's files.

Asserted against the real stores and the code paths the command line and the gateway's start run:

* a snapshot restored, replacing or merging, into a home at another path keeps every chat that
  worked in the workspace in the shared memory after the first start, wherever the old home was
  and whichever workspace it had, and each such chat works in the new home's workspace; a chat in
  a folder of its own still has its earlier records moved to that folder's memory;
* an export archive imported into a home at another path does the same;
* a chat in the workspace records no folder, so a copy of it reads the workspace of the home it is
  in; a chat in a folder of its own records that folder;
* a chat in the workspace an earlier release made, or in the home's own workspace folder after the
  workspace was moved, keeps the shared memory;
* what an earlier start moved out for a workspace comes back to the shared memory once, and the
  chats that recorded another home's workspace work in this one's; a folder's own memory is left
  as it is;
* each start's move of records says what it moved, in each memory's history on the Memory page.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import memory_locality, memory_writes, snapshot
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder, memory_at
from personalclaw.context_engine import assemble_context
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.memory import MemoryStore
from personalclaw.memory_service import MemoryService
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore

#: What the chats kept, invented. Each chat's own episode, and the summary of its session.
SEED_ORDER = "The seed order for the raised beds goes out in the first week of March."
BIKE_SERVICE = "The bike's chain was replaced at the shop on Elm Street in September."
GARDEN_PLAN = "The garden plan keeps tomatoes in the two beds by the south fence."
SUMMARIES = {
    SEED_ORDER: "Planned the seed order.",
    BIKE_SERVICE: "Logged the bike service.",
    GARDEN_PLAN: "Drew the bed plan.",
}


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    """No gateway of these homes is running: a replace restore asks."""
    monkeypatch.setattr(snapshot, "_running_gateway", lambda: None)


def _use_home(monkeypatch, home: Path) -> Path:
    """Make *home* the active PersonalClaw home, as ``PERSONALCLAW_HOME`` names one."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return config_loader.config_dir()


def _start() -> SimpleNamespace:
    """What a gateway's start wires over the active home, in its order: the memory database and
    the shared memory, the context builder, the conversation log, the memory settle
    (``memory_locality.settle_at_start``), then the chat state that restores kept chats."""
    main = MemoryStore()
    main.init()
    store = VectorMemoryStore(confidence_threshold=0.0)
    store.init()
    main.vector_store = store
    builder = ContextBuilder(
        memory=main,
        skills=SkillsLoader(
            skills_path=config_loader.config_dir() / "skills", install_builtins=False
        ),
    )
    log = ConversationLog()
    log.init()
    builder.conversation_log = log
    memory_locality.settle_at_start(store, log)
    return SimpleNamespace(main=main, store=store, builder=builder, log=log, state=_state(builder))


def _state(builder: ContextBuilder) -> DashboardState:
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.reset = AsyncMock()
    return DashboardState(
        sessions=sessions,
        start_time=0.0,
        context_builder=builder,
        conversation_log=builder.conversation_log,
    )


def _stop(gw: SimpleNamespace) -> None:
    gw.store.close()


def _chat(gw: SimpleNamespace, folder: str, episode: str) -> tuple[str, str]:
    """A dashboard chat working in *folder* ("" for the workspace a new chat starts in, as
    ``api_chat_session_create`` gives it), saved as a running chat is, with what consolidation
    kept of it in the shared memory: its episode and its session summary. Returns its session's
    name and its history key."""
    session = gw.state.get_or_create_session(name=None)
    session.workspace_dir = (
        os.path.realpath(folder) if folder else config_loader.default_workspace_dir()
    )
    session.append("user", "Here is what I worked out today.", broadcast=False)
    session.append("assistant", "Noted, I will keep it in mind.", broadcast=False)
    session.drain()
    save_session_to_history(gw.state, session, force=True)
    key = _history_key_for(session.key)
    _kept_in(gw.store, key, episode)
    return session.key, key


def _kept_in(store: VectorMemoryStore, key: str, episode: str) -> None:
    svc = MemoryService.over_vector_store(store)
    with memory_writes.derived_from(key, memory_mode="persistent"):
        assert svc.write_episodic(episode, conversation_id=key, source=f"consolidation:{key}")
        svc.write_working_memory(key, SUMMARIES[episode])


def _recorded_as(gw: SimpleNamespace, key: str, folder: str) -> None:
    """What an earlier version wrote in chat *key*'s record: *folder*, as a path."""
    gw.log.update_metadata(key, {"workspace_dir": folder})


def _episodes(store: VectorMemoryStore) -> set[str]:
    rows = store.db.execute("SELECT text FROM episodic_memories WHERE is_deleted = 0").fetchall()
    return {str(r["text"]) for r in rows}


def _summaries(store: VectorMemoryStore) -> set[str]:
    rows = store.db.execute(
        "SELECT value_json FROM semantic_memory WHERE is_deleted = 0 AND scope = 'session'"
    ).fetchall()
    return {str(json.loads(r["value_json"])) for r in rows}


def _counts(store: VectorMemoryStore) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("episodic_memories", "semantic_memory", "memory_events")
    )


def _folders() -> list[str]:
    """The folder each folder's memory in the active home is for, as Settings → Memory lists."""
    return [p.folder or p.id for p in memory_locality.partitions() if not p.is_global]


def _moves(store: VectorMemoryStore) -> list[str]:
    """What the Memory page's history of *store* says was moved."""
    return [str(e["memory_key"]) for e in store.get_events(limit=200) if e["event_type"] == "move"]


def _folder_store(folder: str) -> VectorMemoryStore:
    store = memory_at(config_loader.memory_dir_for_cwd(folder)).vector_store
    assert store is not None, f"no memory database for {folder}"
    return store


def _recalled_in_a_new_chat(gw: SimpleNamespace, text: str) -> str:
    """The prompt a new chat's first turn in the workspace is assembled with."""
    return assemble_context(
        gw.builder,
        text,
        is_new_session=True,
        session_key="dashboard:a-new-chat",
        cwd=config_loader.default_workspace_dir(),
        memory_store=None,
    ).message


# ── a snapshot restored into a home at another path ─────────────────────────────────────────


@pytest.mark.parametrize("mode", ["replace", "merge"])
@pytest.mark.parametrize(
    "first_home, own_workspace",
    [
        pytest.param(".personalclaw", False, id="first-home-at-the-default-place"),
        pytest.param("pc-home", False, id="first-home-elsewhere"),
        pytest.param("pc-home", True, id="first-home-with-a-workspace-of-its-own"),
    ],
)
def test_a_snapshot_restored_at_another_path_keeps_the_workspace_chats_in_the_shared_memory(
    tmp_path, monkeypatch, mode, first_home, own_workspace
) -> None:
    garden = tmp_path / "repos" / "garden"
    garden.mkdir(parents=True)
    _use_home(monkeypatch, tmp_path / "alice" / first_home)
    if own_workspace:
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "alice" / "notes"))
    old = _start()
    old_workspace = config_loader.default_workspace_dir()
    seeds, seeds_key = _chat(old, "", SEED_ORDER)
    bike, bike_key = _chat(old, "", BIKE_SERVICE)
    _plan, plan_key = _chat(old, str(garden), GARDEN_PLAN)
    # As every release before this one wrote it, and as a chat not opened since still holds it.
    _recorded_as(old, bike_key, old_workspace)
    _stop(old)
    snaps = tmp_path / "snapshots"
    assert snapshot.snapshot_main([str(snaps)]) == 0
    archive = next(snaps.glob("personalclaw-snapshot-*.tar.gz"))

    monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)
    home = _use_home(monkeypatch, tmp_path / "bob-mini" / "pc")
    assert snapshot.restore_main([str(archive), "--mode", mode]) == 0
    gw = _start()

    assert {SEED_ORDER, BIKE_SERVICE} <= _episodes(gw.store), "kept in the shared memory"
    assert {SUMMARIES[SEED_ORDER], SUMMARIES[BIKE_SERVICE]} <= _summaries(gw.store)
    assert _folders() == [str(garden.resolve())], "no memory named for the old workspace"
    assert _episodes(_folder_store(str(garden))) == {GARDEN_PLAN}, "a folder's chat moves still"
    assert GARDEN_PLAN not in _episodes(gw.store)
    context = _recalled_in_a_new_chat(gw, "When does the seed order go out?")
    assert SEED_ORDER in context, "a new chat on the new machine recalls it"
    here = config_loader.default_workspace_dir()
    assert here.startswith(os.path.realpath(home)) and here != old_workspace
    for name in (seeds, bike):
        assert gw.state.get_or_create_session(name).workspace_dir == here, "works here now"
    assert gw.state.get_or_create_session(_plan).workspace_dir == str(garden.resolve())
    for key in (seeds_key, bike_key):
        assert not gw.log.get_metadata(key).get("workspace_dir"), "it records no folder"
    assert gw.log.get_metadata(plan_key)["workspace_dir"] == str(garden.resolve())
    _stop(gw)


@pytest.mark.parametrize("mode", ["replace", "merge"])
def test_an_export_archive_imported_at_another_path_keeps_the_workspace_chats_shared(
    tmp_path, monkeypatch, mode
) -> None:
    from personalclaw.portability import create_export_zip

    _use_home(monkeypatch, tmp_path / "alice" / "pc-home")
    old = _start()
    old_workspace = config_loader.default_workspace_dir()
    seeds, seeds_key = _chat(old, "", SEED_ORDER)
    _recorded_as(old, seeds_key, old_workspace)
    _stop(old)
    exported, _ = create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(exported)

    _use_home(monkeypatch, tmp_path / "bob-mini" / "pc")
    assert snapshot.restore_main([str(archive), "--mode", mode]) == 0
    gw = _start()

    assert SEED_ORDER in _episodes(gw.store), "kept in the shared memory"
    assert _folders() == []
    assert gw.state.get_or_create_session(seeds).workspace_dir == (
        config_loader.default_workspace_dir()
    )
    _stop(gw)
    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        manifest = json.loads(
            next(zf.read(n) for n in zf.namelist() if n.endswith("MANIFEST.json"))
        )
    assert old_workspace in manifest["workspaces"], "the archive names its home's workspace"


# ── what a chat records ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_in_the_workspace_records_no_folder_and_comes_back_in_the_workspace(
    tmp_path, monkeypatch
) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.chat import api_chat_session_workspace_dir

    garden = tmp_path / "repos" / "garden"
    garden.mkdir(parents=True)
    _use_home(monkeypatch, tmp_path / "alice" / "pc-home")
    gw = _start()
    seeds, seeds_key = _chat(gw, "", SEED_ORDER)
    plan, plan_key = _chat(gw, str(garden), GARDEN_PLAN)
    assert not gw.log.get_metadata(seeds_key).get("workspace_dir")
    assert gw.log.get_metadata(plan_key)["workspace_dir"] == str(garden.resolve())

    # Put back in the workspace from its folder: Working directory, emptied, then Set.
    app = web.Application()
    app["state"] = gw.state
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir", api_chat_session_workspace_dir
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            f"/api/chat/sessions/{plan}/workspace-dir", json={"workspace_dir": ""}
        )
        assert resp.status == 200
    assert not gw.log.get_metadata(plan_key).get("workspace_dir"), "recorded as a new chat's is"

    _stop(gw)
    gw = _start()
    workspace = config_loader.default_workspace_dir()
    assert gw.state.get_or_create_session(seeds).workspace_dir == workspace
    assert gw.state.get_or_create_session(plan).workspace_dir == workspace
    _stop(gw)


# ── a workspace another release or another home had ─────────────────────────────────────────


def test_a_chat_in_the_workspace_an_earlier_release_made_keeps_the_shared_memory(
    tmp_path, monkeypatch
) -> None:
    """Earlier releases started every chat in ``~/workplace/personalclaw-workspace``, and the chat
    still works there after the upgrade, where its files are; what it kept stays shared. So does
    what a chat kept that worked in this home's own workspace folder before the owner chose
    another workspace, and one restored from another machine's earlier release."""
    user = tmp_path / "user"
    monkeypatch.setenv("HOME", str(user))
    earlier = user / "workplace" / "personalclaw-workspace"
    earlier.mkdir(parents=True)
    _use_home(monkeypatch, user / ".personalclaw")
    gw = _start()
    upgraded, upgraded_key = _chat(gw, "", SEED_ORDER)
    _recorded_as(gw, upgraded_key, str(earlier.resolve()))
    _moved, moved_key = _chat(gw, "", BIKE_SERVICE)
    _recorded_as(gw, moved_key, str(config_loader.memory_root().resolve()))
    _restored, restored_key = _chat(gw, "", GARDEN_PLAN)
    _recorded_as(gw, restored_key, "/home/user/workplace/personalclaw-workspace")
    _stop(gw)
    chosen = tmp_path / "chosen-workspace"
    chosen.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(chosen))

    gw = _start()

    assert {SEED_ORDER, BIKE_SERVICE, GARDEN_PLAN} <= _episodes(gw.store)
    assert set(SUMMARIES.values()) <= _summaries(gw.store)
    assert _folders() == [] and _moves(gw.store) == []
    assert gw.state.get_or_create_session(upgraded).workspace_dir == str(earlier.resolve())
    _stop(gw)


@pytest.mark.parametrize(
    "old_home",
    [
        pytest.param(None, id="from-another-machine-at-the-default-place"),
        pytest.param("alice/pc-home", id="from-a-home-elsewhere-on-this-machine"),
    ],
)
def test_what_an_earlier_start_moved_out_for_a_workspace_comes_back_once(
    tmp_path, monkeypatch, old_home
) -> None:
    """A home an earlier version restored at another path, and started: what its chats in the old
    home's workspace kept was moved to a memory named for that workspace. It comes back to the
    shared memory, and those chats work in this home's workspace; a folder's own memory, and what
    a chat in that folder keeps, are left as they are. The old home's workspace is known by its
    place, or, for a home elsewhere on this machine, by the home's memory it holds."""
    garden = tmp_path / "repos" / "garden"
    garden.mkdir(parents=True)
    old_workspace = "/home/user/.personalclaw/workspace"
    if old_home is not None:
        # What a home keeps there: its memory's documents, and its chats beside the folder.
        held = config_loader.memory_root(tmp_path / old_home)
        (held / "memory").mkdir(parents=True)
        (tmp_path / old_home / "sessions").mkdir()
        old_workspace = os.path.realpath(held)
    _use_home(monkeypatch, tmp_path / "bob-mini" / "pc")
    gw = _start()
    seeds, seeds_key = _chat(gw, "", SEED_ORDER)
    bike, bike_key = _chat(gw, "", BIKE_SERVICE)
    _plan, plan_key = _chat(gw, str(garden), GARDEN_PLAN)
    for key in (seeds_key, bike_key):
        _recorded_as(gw, key, old_workspace)
    # What that start did: each chat's records handed to the memory of the folder it records.
    moved_to = config_loader.memory_dir_for_cwd(old_workspace)
    in_garden = config_loader.memory_dir_for_cwd(str(garden))
    away = memory_at(moved_to, writes=True).vector_store
    garden_store = memory_at(in_garden, writes=True).vector_store
    assert away is not None and garden_store is not None
    gw.store.hand_over_chat_records([(away, [seeds_key, bike_key]), (garden_store, [plan_key])])
    memory_locality.record_folder(moved_to, old_workspace)
    memory_locality.record_folder(in_garden, str(garden))
    garden_counts = _counts(garden_store)
    assert _episodes(gw.store) == set()
    _stop(gw)

    gw = _start()

    assert {SEED_ORDER, BIKE_SERVICE} <= _episodes(gw.store)
    assert {SUMMARIES[SEED_ORDER], SUMMARIES[BIKE_SERVICE]} <= _summaries(gw.store)
    assert _folders() == [str(garden.resolve())] and not moved_to.exists()
    assert _counts(_folder_store(str(garden))) == garden_counts, "a folder's memory is its own"
    # As a folder's memory names its folder: resolved (``/home`` is a link on some systems).
    shown = os.path.realpath(old_workspace)
    assert _moves(gw.store) == [f"4 records back from the memory of {shown}"]
    here = config_loader.default_workspace_dir()
    for name, key in ((seeds, seeds_key), (bike, bike_key)):
        assert not gw.log.get_metadata(key).get("workspace_dir")
        assert gw.state.get_or_create_session(name).workspace_dir == here
    assert gw.log.get_metadata(plan_key)["workspace_dir"] == str(garden.resolve())
    counts = _counts(gw.store)
    _stop(gw)

    gw = _start()
    assert _counts(gw.store) == counts, "a second start changes nothing"
    assert _counts(_folder_store(str(garden))) == garden_counts
    _stop(gw)


# ── what the start's move says ──────────────────────────────────────────────────────────────


def test_the_start_says_in_each_memorys_history_what_it_moved(tmp_path, monkeypatch) -> None:
    """What an earlier version filed in the shared memory for a chat in a folder of its own moves
    to that folder's memory at the start, and both memories' history on the Memory page says so:
    one line each, with what moved and where."""
    garden = tmp_path / "repos" / "garden"
    garden.mkdir(parents=True)
    _use_home(monkeypatch, tmp_path / "alice" / "pc-home")
    gw = _start()
    _plan, plan_key = _chat(gw, str(garden), GARDEN_PLAN)
    _seeds, _ = _chat(gw, "", SEED_ORDER)
    _stop(gw)

    gw = _start()

    shown = memory_locality.Partition("", garden, str(garden.resolve())).shown
    assert _moves(gw.store) == [f"2 records to the memory of {shown}"]
    assert _moves(_folder_store(str(garden))) == ["2 records from the shared memory"]
    assert SEED_ORDER in _episodes(gw.store) and GARDEN_PLAN not in _episodes(gw.store)
    _stop(gw)

    gw = _start()
    assert len(_moves(gw.store)) == 1, "a start that moves nothing says nothing"
    _stop(gw)
