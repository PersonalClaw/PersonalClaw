"""A configuration that can carry a credential leaves the export as soon as it is removed.

Tools › Remove of an MCP server took it out of ``mcp.json`` and the agent config at once, but the
shard export (``shards/mcp/value.jsonl``, ``shards/agents/entities.jsonl``) is rewritten by the
hourly job only, so the removed server's definition, and the token its arguments carried, stayed
there until the next export, up to an hour later. A sync had already carried the shard off the
machine by then.

A store the inventory marks ``exported_on_write`` (``durability.export_follow``) is re-exported as
soon as a write to it lands; one that finds the hourly export running is exported once it ends;
the gateway's start catches the export up with what it moved before it listened; and a sync
cycle's export, taken from the live stores, carries the removal too.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest
from aiohttp.test_utils import make_mocked_request
from mcp_owner_allowed import confirmed

from personalclaw.config import loader as config_loader
from personalclaw.durability import inventory as inv
from personalclaw.durability.shards import default_shard_dir, export_shards, validate

TOKEN = "fixture-todo-token-0000aaaa1111bbbb2222"


class Later:
    """The follower's ``later``, driven by the test: what it was asked to run, run on demand."""

    def __init__(self) -> None:
        self.asked: list[tuple[float, Callable[[], object]]] = []

    def __call__(self, delay: float, work: Callable[[], object]) -> None:
        self.asked.append((delay, work))

    def run(self) -> None:
        asked, self.asked = self.asked, []
        for _delay, work in asked:
            work()


@pytest.fixture
def home():
    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []})
    )
    return home


@pytest.fixture
def later():
    return Later()


@pytest.fixture
def follower(home, later):
    """A follower listening to every write, as the durability service installs one."""
    from personalclaw.atomic_write import register_post_write_hook, unregister_post_write_hook
    from personalclaw.durability.export_follow import ExportFollower

    follower = ExportFollower(home, later=later)
    register_post_write_hook(follower.notify)
    yield follower
    unregister_post_write_hook(follower.notify)


def _put(name: str, body: dict) -> None:
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("PUT", f"/api/mcp/servers/{name}", match_info={"name": name})

    async def _json():
        return confirmed(body)

    req.json = _json
    assert asyncio.run(mcp_mod.api_mcp_server_detail(req)).status == 200


def _delete(name: str) -> None:
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("DELETE", f"/api/mcp/servers/{name}", match_info={"name": name})
    assert asyncio.run(mcp_mod.api_mcp_server_detail(req)).status == 200


def _shard_text(home, entry_id: str) -> str:
    folder = default_shard_dir(home) / entry_id
    return "".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.jsonl")))


def _every_byte(home) -> bytes:
    return b"".join(p.read_bytes() for p in default_shard_dir(home).rglob("*") if p.is_file())


def test_removing_an_mcp_server_rewrites_its_shards_at_once(home, follower):
    _put("todo", {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]})
    _put("notes", {"command": "uvx", "args": ["notes-mcp"]})
    export_shards(home, default_shard_dir(home))  # the hourly job's export
    assert '"todo"' in _shard_text(home, "mcp") and '"todo"' in _shard_text(home, "agents")

    _delete("todo")

    # Gone from both shards as the Remove returns, not at the next hourly export.
    for entry_id in ("mcp", "agents"):
        assert '"todo"' not in _shard_text(home, entry_id)
        assert '"notes"' in _shard_text(home, entry_id)
    assert TOKEN.encode() not in _every_byte(home)
    assert validate(default_shard_dir(home)).ok


def test_a_write_that_finds_the_hourly_export_running_is_exported_once_it_ends(
    home, follower, later
):
    from personalclaw.concurrency import single_flight

    _put("todo", {"command": "uvx", "args": ["todo-mcp"]})
    export_shards(home, default_shard_dir(home))
    with single_flight("durability:export") as held:
        assert held
        _delete("todo")
        # The hourly export holds it: nothing is written over it, and a retry is asked for.
        assert '"todo"' in _shard_text(home, "mcp")
        assert [delay for delay, _work in later.asked] == [2.0]
    later.run()
    assert '"todo"' not in _shard_text(home, "mcp")
    assert validate(default_shard_dir(home)).ok


def test_only_configuration_that_can_carry_a_credential_is_followed(home, follower):
    followed = {e.id for e in inv.shard_entries() if e.exported_on_write}
    assert {"mcp", "agents", "config", "hooks", "triggers"} <= followed
    assert {e.id for e in inv.INVENTORY if e.exported_on_write} == followed
    assert all(
        e.kind in (inv.KIND_JSON_FILE, inv.KIND_JSON_ENTITY_DIR)
        for e in inv.INVENTORY
        if e.exported_on_write
    )
    assert follower.entry_of(home / "mcp.json").id == "mcp"
    assert follower.entry_of(home / "config.json").id == "config"
    assert follower.entry_of(home / "agents" / "personalclaw.json").id == "agents"
    assert follower.entry_of(home / "tasks" / "t-1.json") is None
    assert follower.entry_of(default_shard_dir(home) / "mcp" / "value.jsonl") is None


def test_with_no_export_to_keep_up_nothing_is_written(home, follower):
    _put("todo", {"command": "uvx", "args": ["todo-mcp"]})
    _delete("todo")
    assert not default_shard_dir(home).exists()
    assert follower.exports == 0


def test_the_gateways_start_catches_up_with_what_it_moved_before_it_listened(home, later):
    from personalclaw.config.secret_refs import migrate_plaintext_secrets
    from personalclaw.durability.export_follow import ExportFollower

    # An export taken before the upgrade holds the token as an earlier release wrote it.
    imported = {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]}
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"todo": imported}}))
    export_shards(home, default_shard_dir(home))
    assert TOKEN.encode() in _every_byte(home)

    migrate_plaintext_secrets()  # the gateway's start, before the follower listens
    ExportFollower(home, later=later).catch_up()
    later.run()

    assert TOKEN.encode() not in _every_byte(home)
    assert '"todo"' in _shard_text(home, "mcp")
    assert validate(default_shard_dir(home)).ok


def test_a_followed_store_that_is_gone_leaves_the_export(home, later):
    from personalclaw.durability.export_follow import ExportFollower

    (home / "agent.json").write_text(json.dumps({"note": "an override"}))
    export_shards(home, default_shard_dir(home))
    assert (default_shard_dir(home) / "agent_overrides").is_dir()

    (home / "agent.json").unlink()
    ExportFollower(home, later=later).catch_up()
    later.run()

    assert not (default_shard_dir(home) / "agent_overrides").exists()
    report = validate(default_shard_dir(home))
    assert report.ok, report.problems


def test_the_next_sync_carries_the_removal_and_no_cycle_carries_the_token(home):
    from personalclaw.durability.sync_cycle import run_sync_cycle
    from tests.test_durability_sync_cycle import SharedStore

    store = SharedStore()
    _put("todo", {"command": "uvx", "args": ["todo-mcp", f"--api-token={TOKEN}"]})
    assert run_sync_cycle(store, home, self_id="A", now="t1").seq_published == 1
    first = dict(store.objects)
    _delete("todo")
    assert run_sync_cycle(store, home, self_id="A", now="t2").seq_published == 2

    assert b'"todo"' in first["machines/A/seq-0001/mcp/value.jsonl"]
    assert b'"todo"' not in store.objects["machines/A/seq-0002/mcp/value.jsonl"]
    for published in (first, store.objects):
        assert not [k for k, data in published.items() if TOKEN.encode() in data]


def test_the_durability_service_follows_configuration_writes_with_auto_backup(home):
    from personalclaw.atomic_write import post_write_hooks
    from personalclaw.durability import export_follow
    from personalclaw.durability.service import DurabilityService

    service = DurabilityService()
    try:
        service._install_export_follower()
        installed = export_follow._installed
        assert installed is not None and installed.notify in post_write_hooks()
    finally:
        service.stop()
    assert export_follow._installed is None


def test_a_stopped_follower_leaves_no_re_export_waiting(home, monkeypatch):
    """A write that finds the export busy is re-exported on a timer, ``RETRY_SECS`` later. The
    gateway's stop uninstalls the follower, and a retry it left waiting used to run after the
    stop, in whatever home was current by then (in the suite, another test's: its guard refused
    it). Uninstalling takes the waiting re-export back."""
    import threading

    from personalclaw.concurrency import single_flight
    from personalclaw.durability import export_follow

    started: list[threading.Timer] = []

    class _Recorded(threading.Timer):
        def start(self) -> None:
            started.append(self)
            super().start()

    monkeypatch.setattr(threading, "Timer", _Recorded)
    try:
        # The hourly export holds it from before the gateway starts following, so its catch-up
        # and the write after it both find it busy, and the re-export waits for its timer.
        with single_flight("durability:export") as holding:
            assert holding, "premise: the export is held"
            follower = export_follow.install(home=home)
            follower.flush()
        assert follower.retries >= 1 and started, "premise: a re-export waits on a timer"
    finally:
        export_follow.uninstall()
    assert [timer for timer in started if timer.is_alive()] == [], "a re-export still waits"


def test_a_stop_that_comes_while_a_retry_is_put_on_its_timer_waits_for_that_timer(
    home, monkeypatch
):
    """A re-export that finds the export busy puts its retry on a timer from its own thread, and a
    loaded machine takes a moment to start that timer's thread. A stop that came in that moment
    found a timer that had not started, and waiting for one that has not started raises: the
    gateway's stop broke off there, its other timers neither taken back nor waited for."""
    import threading
    import time

    from personalclaw.concurrency import single_flight
    from personalclaw.durability.export_follow import ExportFollower

    starting = threading.Event()
    timers: list[threading.Timer] = []

    class _SlowToStart(threading.Timer):
        def start(self) -> None:
            timers.append(self)
            starting.set()
            time.sleep(0.2)  # what a loaded machine takes to start a thread
            super().start()

    monkeypatch.setattr(threading, "Timer", _SlowToStart)
    follower = ExportFollower(home)
    with single_flight("durability:export") as holding:
        assert holding, "premise: the export is held"
        busy = threading.Thread(target=follower.flush)
        busy.start()
        assert starting.wait(5), "premise: the retry is put on a timer"
        follower.stop()
    busy.join(5)
    assert timers and [timer for timer in timers if timer.is_alive()] == []


def test_a_stop_waits_for_a_timer_whose_re_export_has_run_until_its_thread_has_ended(
    home, monkeypatch
):
    """A timer's work having run is not its thread having ended. The follower let a timer go once
    its work had run, so a stop had nothing to wait for while that thread still ran."""
    import threading
    import time

    from personalclaw.durability.export_follow import ExportFollower

    ran = threading.Event()
    timers: list[threading.Timer] = []

    class _SlowToEnd(threading.Timer):
        def start(self) -> None:
            timers.append(self)
            super().start()

        def run(self) -> None:
            super().run()
            ran.set()
            time.sleep(0.2)  # what a loaded machine takes to end a thread

    monkeypatch.setattr(threading, "Timer", _SlowToEnd)
    follower = ExportFollower(home)
    follower.catch_up()  # with no export to keep up, its re-export has nothing to write
    assert ran.wait(5), "premise: the catch-up's timer ran"
    follower.stop()
    assert timers and [timer for timer in timers if timer.is_alive()] == []
