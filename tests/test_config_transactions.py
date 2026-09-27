"""One writer at a time for ``config.json``, across processes.

The gateway, the CLI, the setup wizard and a second terminal all write ``config.json``, and none
of them held a lock another process could see. ``AppConfig.save()`` wrote the whole in-memory
object, so a process that loaded the config before another process saved it put the other
process's field back to what it had loaded. Seven handlers and six CLI paths read the raw file,
changed a key and wrote it back with at most an in-process ``asyncio.Lock``.

The contract under test:

* every writer takes the same OS lock from the READ of the current document to its atomic
  replace (``config.transactions.mutate_config``), so two processes changing the same key both
  land, in lock order, and two changing different keys both survive;
* a loaded ``AppConfig`` saves only what changed since it was loaded, so a stale object cannot
  put another process's field back;
* an unreadable file is refused, not written over — by every writer, several of which used to
  read it as ``{}`` and write their one key over it; a crash releases the lock and leaves the
  file whole; a nested transaction fails at once instead of deadlocking;
* secrets still go to the credential store, inside the transaction, and the gateway's two boot
  writes (the plaintext-secret move and the config migration) take the lock too;
* ``config edit`` edits a copy and commits only the edit, so a change made while the editor was
  open survives, and it still repairs a file that cannot be read;
* nothing writes ``config.json`` around the transaction.

Every cross-process test runs REAL subprocesses (``sys.executable -c``) against a scratch home,
started together behind a barrier so their writes actually overlap.
"""

from __future__ import annotations

import ast
import json
import os
import stat
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest

# ── helpers: real processes, started together ───────────────────────────────────────────────

#: Each worker imports first, then says it is ready and waits for `go`, so the timed loops of
#: all workers overlap instead of running one after another behind their import time.
_BARRIER = r"""
import os, sys, time
from pathlib import Path
home = Path(os.environ["PERSONALCLAW_HOME"])
name = sys.argv[1]
def wait_for_go():
    (home / f"ready-{name}").touch()
    deadline = time.monotonic() + 60
    while not (home / "go").exists():
        if time.monotonic() > deadline:
            raise SystemExit("never told to go")
        time.sleep(0.002)
"""

#: A stale-object writer: load, bump ONE modeled field, save — the shape of every settings
#: handler and CLI path that goes through ``AppConfig``.
_SAVE_WORKER = _BARRIER + r"""
from personalclaw.config.loader import AppConfig
field, rounds = sys.argv[2], int(sys.argv[3])
wait_for_go()
for _ in range(rounds):
    cfg = AppConfig.load()
    setattr(cfg, field, getattr(cfg, field) + 1)
    cfg.save()
"""

#: A raw-document writer through the transaction: read the counter, add one, write it back.
_INCREMENT_WORKER = _BARRIER + r"""
from personalclaw.config.transactions import mutate_config
key, rounds = sys.argv[2], int(sys.argv[3])
wait_for_go()
def bump(document):
    document[key] = int(document.get(key, 0)) + 1
for _ in range(rounds):
    mutate_config(bump)
"""

#: Holds the lock for a while, then writes `value` to `winner`.
_HOLDING_WORKER = _BARRIER + r"""
from personalclaw.config.transactions import mutate_config
hold, value = float(sys.argv[2]), sys.argv[3]
wait_for_go()
def hold_then_write(document):
    (home / f"holding-{name}").touch()
    time.sleep(hold)
    document["winner"] = value
mutate_config(hold_then_write, timeout=10)
"""

#: Dies inside the transaction, after the lock is taken and before anything is written.
_CRASHING_WORKER = _BARRIER + r"""
from personalclaw.config.transactions import mutate_config
wait_for_go()
def crash(document):
    document["half_written"] = True
    (home / f"holding-{name}").touch()
    os._exit(23)
mutate_config(crash)
"""


def _spawn(home: Path, script: str, name: str, *args: object) -> subprocess.Popen:
    # The home is passed explicitly: a process that is not pytest is outside the suite's
    # real-home guard, so an unset home would be the owner's.
    env = {**os.environ, "PERSONALCLAW_HOME": str(home)}
    return subprocess.Popen(
        [sys.executable, "-c", script, name, *map(str, args)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(path: Path, procs: list[subprocess.Popen], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        for proc in procs:
            if proc.poll() is not None:
                _, err = proc.communicate()
                raise AssertionError(f"worker exited early ({proc.returncode}): {err}")
        assert time.monotonic() < deadline, f"timed out waiting for {path.name}"
        time.sleep(0.01)


def _release(home: Path, procs: list[subprocess.Popen], names: list[str]) -> None:
    for name in names:
        _wait_for(home / f"ready-{name}", procs)
    (home / "go").touch()


def _finish(procs: list[subprocess.Popen], *, expect: int = 0) -> None:
    for proc in procs:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == expect, err


def _document(home: Path) -> dict:
    return json.loads((home / "config.json").read_text(encoding="utf-8"))


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


# ── 1. two processes, no lost update ─────────────────────────────────────────────────────────


def test_two_processes_saving_different_fields_lose_no_update(home):
    """The measured failure: each process loads, changes its own field and saves. A save wrote
    the whole object it had loaded, so the other process's field went back to a stale value."""
    from personalclaw.config.loader import AppConfig

    AppConfig.load().save()
    rounds = 60
    workers = {
        "a": ("observe_max_messages", 200),
        "b": ("observe_ttl_hours", 168.0),
    }
    procs = [_spawn(home, _SAVE_WORKER, n, field, rounds) for n, (field, _) in workers.items()]
    _release(home, procs, list(workers))
    _finish(procs)

    final = AppConfig.load()
    assert final.observe_max_messages == 200 + rounds, "process a lost updates"
    assert final.observe_ttl_hours == 168.0 + rounds, "process b lost updates"


def test_two_processes_incrementing_one_key_lose_no_update(home):
    rounds = 40
    procs = [_spawn(home, _INCREMENT_WORKER, n, "counter", rounds) for n in ("a", "b")]
    _release(home, procs, ["a", "b"])
    _finish(procs)

    assert _document(home)["counter"] == 2 * rounds


def test_the_same_key_lands_in_lock_order(home):
    first = _spawn(home, _HOLDING_WORKER, "first", 0.5, "first")
    _release(home, [first], ["first"])
    _wait_for(home / "holding-first", [first])
    second = _spawn(home, _HOLDING_WORKER, "second", 0.0, "second")
    _wait_for(home / "ready-second", [first, second])
    _finish([first, second])

    assert _document(home)["winner"] == "second"


def test_a_writer_that_waits_too_long_gives_up_and_the_next_one_succeeds(home):
    from personalclaw.config.transactions import ConfigLockTimeout, mutate_config

    holder = _spawn(home, _HOLDING_WORKER, "holder", 1.0, "holder")
    _release(home, [holder], ["holder"])
    _wait_for(home / "holding-holder", [holder])

    started = time.monotonic()
    with pytest.raises(ConfigLockTimeout, match="waited"):
        mutate_config(lambda document: document.update(after="timeout"), timeout=0.1)
    assert time.monotonic() - started < 0.9
    _finish([holder])

    mutate_config(lambda document: document.update(after="recovered"), timeout=5)
    assert _document(home) == {"winner": "holder", "after": "recovered"}


def test_a_crash_inside_the_transaction_releases_the_lock_and_writes_nothing(home):
    from personalclaw.config.transactions import mutate_config

    mutate_config(lambda document: document.update(stable="before"))
    before = (home / "config.json").read_bytes()
    crashing = _spawn(home, _CRASHING_WORKER, "crash")
    _release(home, [crashing], ["crash"])
    _finish([crashing], expect=23)

    assert (home / "config.json").read_bytes() == before
    mutate_config(lambda document: document.update(stable="after"), timeout=5)
    assert _document(home) == {"stable": "after"}


# ── 2. a stale AppConfig saves only what it changed ──────────────────────────────────────────


def test_stale_objects_saving_different_fields_both_survive(home):
    from personalclaw.config.loader import AppConfig

    AppConfig.load().save()
    first, second = AppConfig.load(), AppConfig.load()
    first.timezone = "UTC"
    second.dashboard.user_name = "Ada"
    first.save()
    second.save()

    final = AppConfig.load()
    assert (final.timezone, final.dashboard.user_name) == ("UTC", "Ada")


def test_stale_objects_saving_the_same_field_is_last_writer_wins(home):
    from personalclaw.config.loader import AppConfig

    AppConfig.load().save()
    first, second = AppConfig.load(), AppConfig.load()
    first.timezone = "UTC"
    second.timezone = "America/Los_Angeles"
    first.save()
    second.save()

    assert AppConfig.load().timezone == "America/Los_Angeles"


def test_a_save_keeps_keys_the_model_does_not_know(home):
    from personalclaw.config.loader import AppConfig

    (home / "config.json").write_text(
        json.dumps(
            {
                "agent": {"log_level": "INFO", "future_agent_option": {"enabled": True}},
                "future_platform_block": {"version": 2},
                "providers": [{"name": "p1", "type": "ollama", "model": "m"}],
            }
        ),
        encoding="utf-8",
    )
    cfg = AppConfig.load()
    cfg.timezone = "UTC"
    cfg.save()

    document = _document(home)
    assert document["agent"]["future_agent_option"] == {"enabled": True}
    assert document["future_platform_block"] == {"version": 2}
    assert document["providers"] == [{"name": "p1", "type": "ollama", "model": "m"}]
    assert document["timezone"] == "UTC"


def test_an_unchanged_save_writes_nothing(home):
    from personalclaw.config.loader import AppConfig

    AppConfig.load().save()  # creates the file from the defaults
    AppConfig.load().save()  # persists what a parsed load migrates (the built-in agents)
    before = (home / "config.json").read_bytes()
    AppConfig.load().save()
    assert (home / "config.json").read_bytes() == before, "a save that changed nothing wrote"


def test_a_second_save_on_one_object_writes_only_its_second_change(home):
    """After a save, the object's baseline is what it just wrote: a later save of an unrelated
    field must not put back a value another process changed in between."""
    from personalclaw.config.loader import AppConfig

    AppConfig.load().save()
    mine = AppConfig.load()
    mine.timezone = "UTC"
    mine.save()
    other = AppConfig.load()
    other.dashboard.user_name = "Ada"
    other.save()
    mine.observe_max_messages = 7
    mine.save()

    final = AppConfig.load()
    assert (final.timezone, final.dashboard.user_name, final.observe_max_messages) == (
        "UTC",
        "Ada",
        7,
    )


# ── 3. what the transaction refuses ──────────────────────────────────────────────────────────


def test_an_unreadable_file_is_refused_and_left_as_it_was(home):
    from personalclaw.config.loader import ConfigPreserveError, ConfigWriteError
    from personalclaw.config.transactions import mutate_config

    path = home / "config.json"
    original = b'{"providers": ['
    path.write_bytes(original)

    with pytest.raises(ConfigPreserveError, match="not valid JSON") as caught:
        mutate_config(lambda document: document.update(timezone="UTC"))
    assert isinstance(caught.value, ConfigWriteError)
    assert path.read_bytes() == original


def test_a_nested_transaction_fails_at_once_and_the_outer_one_writes_nothing(home):
    from personalclaw.config.transactions import NestedConfigTransaction, mutate_config

    def outer(document: dict) -> None:
        document["outer"] = True
        mutate_config(lambda nested: nested.update(inner=True), timeout=5)

    started = time.monotonic()
    with pytest.raises(NestedConfigTransaction):
        mutate_config(outer)
    assert time.monotonic() - started < 1.0, "a nested transaction must not wait for itself"
    assert not (home / "config.json").exists()


def test_an_absent_file_is_created_private_and_unknown_keys_survive(home):
    from personalclaw.config.transactions import mutate_config

    mutate_config(lambda document: document.update(external_extension={"future": 1}))
    mutate_config(lambda document: document.update(timezone="UTC"))
    assert _document(home) == {"external_extension": {"future": 1}, "timezone": "UTC"}
    if os.name != "nt":
        assert stat.S_IMODE((home / "config.json").stat().st_mode) == 0o600
        assert stat.S_IMODE((home / "config.json.lock").stat().st_mode) == 0o600


def test_the_transaction_writes_only_the_config_and_its_lock(home):
    from personalclaw.config.transactions import mutate_config

    mutate_config(lambda document: document.update(timezone="UTC"))
    assert {path.name for path in home.iterdir()} == {"config.json", "config.json.lock"}


# ── 4. secrets stay in the credential store ──────────────────────────────────────────────────


def test_a_secret_written_through_the_transaction_lands_in_the_store_not_the_file(home):
    from personalclaw.config.secret_refs import reveal_stored_values
    from personalclaw.config.transactions import mutate_config

    mutate_config(lambda document: document.update(hooks={"webhook_token": "s3cr3t-token"}))

    raw = (home / "config.json").read_text(encoding="utf-8")
    assert "s3cr3t-token" not in raw, "the secret was written into config.json"
    revealed, unresolved = reveal_stored_values(_document(home))
    assert unresolved == []
    assert revealed["hooks"]["webhook_token"] == "s3cr3t-token"


def test_an_app_config_save_moves_a_secret_too(home):
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.hooks = {"webhook_token": "an0ther-token"}
    cfg.save()

    assert "an0ther-token" not in (home / "config.json").read_text(encoding="utf-8")


def test_the_boot_move_of_a_plaintext_secret_waits_for_the_lock_and_keeps_the_other_write(home):
    """The gateway moves a hand-typed secret out of config.json at boot. It read and wrote the
    file around every lock, so a settings save another process was making at that moment either
    reverted the move (the secret back in plaintext) or was reverted by it."""
    import threading

    from personalclaw.config.secret_refs import migrate_plaintext_secrets, reveal_stored_values
    from personalclaw.config.transactions import mutate_config

    path = home / "config.json"
    path.write_text(json.dumps({"hooks": {"webhook_token": "b00t-token"}}), encoding="utf-8")
    entered, release = threading.Event(), threading.Event()

    def another_writer(document: dict) -> None:
        entered.set()
        release.wait(10)
        document["timezone"] = "UTC"

    holder = threading.Thread(target=mutate_config, args=(another_writer,), kwargs={"timeout": 10})
    holder.start()
    assert entered.wait(5)
    mover = threading.Thread(target=migrate_plaintext_secrets)
    mover.start()
    time.sleep(0.3)
    assert "b00t-token" in path.read_text(encoding="utf-8"), "the move wrote around the lock"
    release.set()
    holder.join(10)
    mover.join(10)

    document = _document(home)
    assert document["timezone"] == "UTC", "the move put back what the other writer changed"
    assert "b00t-token" not in path.read_text(encoding="utf-8"), "the secret is still plaintext"
    assert reveal_stored_values(document)[0]["hooks"]["webhook_token"] == "b00t-token"


def test_the_boot_migration_persists_as_a_change_and_keeps_a_setting_written_meanwhile(
    home, monkeypatch
):
    """The gateway loads the config at boot, migrates it in memory, and persists it. A setting
    another process wrote between that load and the persist was reverted: the persist saved the
    whole object it had loaded. Now the migration is applied as the change it is."""
    from personalclaw.config import migrations
    from personalclaw.config.loader import AppConfig

    path = home / "config.json"
    # Pre-migration: no built-in agents yet, so the load seeds them in memory.
    path.write_text(json.dumps({"agent": {"log_level": "INFO"}}), encoding="utf-8")
    loaded = AppConfig.load_with_migration_state

    def load_then_another_process_writes():
        result = loaded()
        document = json.loads(path.read_text(encoding="utf-8"))
        document["timezone"] = "UTC"  # a CLI in another terminal, while the gateway boots
        path.write_text(json.dumps(document), encoding="utf-8")
        return result

    monkeypatch.setattr(AppConfig, "load_with_migration_state", load_then_another_process_writes)
    cfg = migrations.load_and_persist_migrations()

    document = _document(home)
    assert document["timezone"] == "UTC", "persisting the migration reverted another write"
    assert set(cfg.agents) <= set(document["agents"]), "the migration was not persisted"
    assert (home / "config.json.bak").is_file(), "no backup of the pre-migration file"


# ── 5. `config edit` edits a copy and commits the edit ───────────────────────────────────────


def _no_exec(*_args, **_kwargs):
    raise AssertionError("`config edit` ran the editor on the live config file")


def test_config_edit_keeps_a_change_made_while_the_editor_was_open(home, monkeypatch):
    from personalclaw import cli_config
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.timezone = "UTC"
    cfg.save()

    def editor(argv, **_kwargs):
        # Another process changes an unrelated field while the file is being edited…
        other = AppConfig.load()
        other.dashboard.user_name = "Ada"
        other.save()
        # …and the user changes the timezone in the editor.
        staged = Path(argv[-1])
        document = json.loads(staged.read_text(encoding="utf-8"))
        document["timezone"] = "America/Los_Angeles"
        staged.write_text(json.dumps(document), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("EDITOR", "test-editor --wait")
    monkeypatch.setattr(cli_config.os, "execvp", _no_exec)
    monkeypatch.setattr(cli_config.subprocess, "run", editor, raising=False)
    cli_config._config_cmd(Namespace(config_action="edit", key=None, value=None, file=None))

    final = AppConfig.load()
    assert final.timezone == "America/Los_Angeles"
    assert final.dashboard.user_name == "Ada", "the edit put back a change made elsewhere"
    assert not list(home.glob(".config.edit.*")), "the staged copy was left behind"


def test_config_edit_that_leaves_invalid_json_changes_nothing(home, monkeypatch):
    from personalclaw import cli_config
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.timezone = "UTC"
    cfg.save()
    before = (home / "config.json").read_bytes()

    def editor(argv, **_kwargs):
        Path(argv[-1]).write_text("{", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("EDITOR", "test-editor")
    monkeypatch.setattr(cli_config.os, "execvp", _no_exec)
    monkeypatch.setattr(cli_config.subprocess, "run", editor, raising=False)
    with pytest.raises(SystemExit) as exc:
        cli_config._config_cmd(Namespace(config_action="edit", key=None, value=None, file=None))

    assert exc.value.code == 1
    assert (home / "config.json").read_bytes() == before
    assert not list(home.glob(".config.edit.*"))


def test_config_edit_an_editor_that_fails_changes_nothing(home, monkeypatch):
    from personalclaw import cli_config
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.timezone = "UTC"
    cfg.save()
    before = (home / "config.json").read_bytes()

    def editor(argv, **_kwargs):
        Path(argv[-1]).write_text('{"timezone": "Asia/Tokyo"}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setenv("EDITOR", "test-editor")
    monkeypatch.setattr(cli_config.os, "execvp", _no_exec)
    monkeypatch.setattr(cli_config.subprocess, "run", editor, raising=False)
    with pytest.raises(SystemExit):
        cli_config._config_cmd(Namespace(config_action="edit", key=None, value=None, file=None))

    assert (home / "config.json").read_bytes() == before


def test_config_edit_still_repairs_a_file_that_cannot_be_read(home, monkeypatch):
    """Opening a truncated config in the editor is how it gets fixed. With nothing to diff
    against, the repaired document replaces the file."""
    from personalclaw import cli_config

    (home / "config.json").write_bytes(b'{"timezone": "UTC",')

    def editor(argv, **_kwargs):
        assert Path(argv[-1]).read_bytes() == b'{"timezone": "UTC",', "not the file's own bytes"
        Path(argv[-1]).write_text('{"timezone": "UTC"}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("EDITOR", "test-editor")
    monkeypatch.setattr(cli_config.os, "execvp", _no_exec)
    monkeypatch.setattr(cli_config.subprocess, "run", editor, raising=False)
    cli_config._config_cmd(Namespace(config_action="edit", key=None, value=None, file=None))

    assert _document(home) == {"timezone": "UTC"}


def test_config_edit_does_not_repair_over_a_file_replaced_while_it_was_open(home, monkeypatch):
    from personalclaw import cli_config

    path = home / "config.json"
    path.write_bytes(b'{"timezone": "UTC",')
    replaced = b'{"timezone": "Asia/Tokyo"}'

    def editor(argv, **_kwargs):
        path.write_bytes(replaced)  # another process repaired it first
        Path(argv[-1]).write_text('{"timezone": "UTC"}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("EDITOR", "test-editor")
    monkeypatch.setattr(cli_config.os, "execvp", _no_exec)
    monkeypatch.setattr(cli_config.subprocess, "run", editor, raising=False)
    with pytest.raises(SystemExit):
        cli_config._config_cmd(Namespace(config_action="edit", key=None, value=None, file=None))

    assert path.read_bytes() == replaced
    assert not list(home.glob(".config.edit.*"))


# ── 6. every writer refuses a file it cannot read ────────────────────────────────────────────
#
# The transaction reads through `read_config_for_merge`, which refuses a file whose content
# cannot be known. Most of the raw writers it replaced read an unreadable file as `{}` and wrote
# their one key over it — every other setting and every configured model provider gone, and the
# write reported as a success.

_UNREADABLE = b'{"providers": [{"name": "keep-me"'


def _handler_status(route: str, method: str, handler, body: dict) -> int:
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def call() -> int:
        app = web.Application()
        app["state"] = type("_S", (), {"consolidator": None})()
        app.router.add_route(method, route, handler)
        async with TestClient(TestServer(app)) as client:
            return (await client.request(method, route, json=body)).status

    return asyncio.run(call())


def _memory_settings_put(home: Path) -> bool:
    from personalclaw.dashboard.handlers import api_memory_settings

    status = _handler_status(
        "/api/memory/settings", "PUT", api_memory_settings, {"active_recall": False}
    )
    return status == 200


def _default_agent_put(home: Path) -> bool:
    from personalclaw.dashboard.handlers import api_default_agent

    status = _handler_status("/api/config/default-agent", "PUT", api_default_agent, {"agent": ""})
    return status == 200


def _provider_create(home: Path) -> bool:
    from personalclaw.dashboard.handlers import providers
    from personalclaw.llm.branded_specs import BrandedProviderSpec
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.sdk.provider_helpers import register_branded_app

    register_branded_app(
        BrandedProviderSpec(
            type="fixture-txn-openai", protocol="openai", default_base_url="https://txn.invalid/v1"
        )
    )
    try:
        status = _handler_status(
            "/api/model-providers",
            "POST",
            providers.api_provider_create,
            {"name": "fx-txn", "type": "fixture-txn-openai", "model": ""},
        )
    finally:
        get_default_registry().unregister_entry("fx-txn")
    return status == 200


def _setup_default_agent(home: Path) -> bool:
    from personalclaw.cli_chat import _ensure_default_agent_in_config

    _ensure_default_agent_in_config()
    return b'"default_agent"' in (home / "config.json").read_bytes()


def _seed_local_model(home: Path) -> bool:
    from personalclaw import seed_local_model as seed

    fake_models = [{"model": "llama3.2:3b", "details": {"family": "llama"}}]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(seed, "_probe_models", lambda endpoint, **_kw: fake_models)
        mp.setattr(seed, "_installed_provider_app", lambda: True)
        mp.setattr(seed, "_write_active_models", lambda **_kw: None)
        return seed.bind_local_model(model="llama3.2:3b").ok


def _eval_overlay(home: Path) -> bool:
    from personalclaw.evals.overlay import patch_child_config

    try:
        patch_child_config("agent.log_level", "DEBUG")
    except Exception:  # noqa: BLE001 — a refusal is the outcome under test
        return False
    return True


def _config_patch(home: Path) -> bool:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    status = _handler_status(
        "/api/config/personalclaw",
        "PATCH",
        api_personalclaw_config_patch,
        {"path": "local_models.pressure_warn_pct", "value": 70},
    )
    return status == 200


def _updates_fields(home: Path) -> bool:
    from personalclaw.self_update import write_updates_fields

    return write_updates_fields({"last_version": "0.1.0"})


def _config_set(home: Path) -> bool:
    from personalclaw import cli_config

    try:
        cli_config._config_cmd(
            Namespace(config_action="set", key="timezone", value="UTC", file=None)
        )
    except SystemExit:
        return False
    return True


@pytest.mark.parametrize(
    "writer",
    [
        # Each of these wrote over the file on main.
        _memory_settings_put,
        _default_agent_put,
        _provider_create,
        _setup_default_agent,
        _seed_local_model,
        _eval_overlay,
        # These already refused; they must still.
        _config_patch,
        _updates_fields,
        _config_set,
    ],
    ids=lambda writer: writer.__name__.strip("_"),
)
def test_no_writer_writes_over_a_file_it_cannot_read(home, writer):
    path = home / "config.json"
    path.write_bytes(_UNREADABLE)

    reported_success = writer(home)

    assert path.read_bytes() == _UNREADABLE, f"{writer.__name__} wrote over an unreadable file"
    assert not reported_success, f"{writer.__name__} reported a write that did not happen"


# ── 7. nothing writes config.json around the transaction ─────────────────────────────────────

#: The one module allowed to replace ``config.json`` itself.
_THE_WRITER = "config/transactions.py"


def _calls(node: ast.AST | None, name: str) -> bool:
    """``name()`` or ``module.name()`` with no arguments — the home's own resolver.

    Zero arguments is the discriminator: ``ProviderSettings.config_path(extension)`` is a
    different file.
    """
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    func = node.func
    return (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")) == name


class _Scope:
    """The names one function (or the module body) binds to the home and to its config file."""

    def __init__(self, tree: ast.AST) -> None:
        self.homes: set[str] = set()
        self.configs: set[str] = set()
        # Twice, so a name bound from an earlier name (`home = config_dir()` then
        # `path = home / "config.json"`) is seen whatever order the walk visits them in.
        for _ in range(2):
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                bound = {t.id for t in targets if isinstance(t, ast.Name)}
                if _calls(node.value, "config_dir"):
                    self.homes |= bound
                elif self.is_config(node.value):
                    self.configs |= bound

    def is_home(self, node: ast.AST | None) -> bool:
        return _calls(node, "config_dir") or (isinstance(node, ast.Name) and node.id in self.homes)

    def is_config(self, node: ast.AST | None) -> bool:
        # `config_path()`, a name bound to it, or `<the home> / "config.json"`
        if _calls(node, "config_path") or (isinstance(node, ast.Name) and node.id in self.configs):
            return True
        return (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and self.is_home(node.left)
            and isinstance(node.right, ast.Constant)
            and node.right.value == "config.json"
        )


def _raw_config_writes(source: str) -> list[int]:
    """Lines that write the config file's path without going through the transaction.

    Scoped per function, so ``path = config_path()`` in one function does not make an
    unrelated ``atomic_write(path, …)`` in another an offender; a nested function sees the
    names of the function it is written in. A path that arrives as a parameter is out of a
    static scan's reach — the boot-time secret move is one, and it has its own behavioural test.
    """
    tree = ast.parse(source)
    functions = [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    module_level = ast.Module(
        body=[
            n
            for n in tree.body
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ],
        type_ignores=[],
    )
    offenders: set[int] = set()
    for tree_scope in [module_level, *functions]:
        scope = _Scope(tree_scope)
        for node in ast.walk(tree_scope):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"write_text", "write_bytes"}:
                if scope.is_config(func.value):
                    offenders.add(node.lineno)
                continue
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in {"atomic_write", "atomic_write_bytes", "atomic_json_write"}:
                if any(scope.is_config(arg) for arg in node.args):
                    offenders.add(node.lineno)
    return sorted(offenders)


def test_nothing_writes_the_config_file_around_the_transaction():
    root = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    offenders = {
        str(path.relative_to(root)): lines
        for path in sorted(root.rglob("*.py"))
        if str(path.relative_to(root)) != _THE_WRITER
        and (lines := _raw_config_writes(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}, f"raw config.json writers: {offenders}"


def test_the_raw_writer_rail_sees_every_shape_it_names():
    # Vacuity floor: each shape the rail claims to catch, caught.
    shapes = {
        "path = config_path()\natomic_write(path, '{}')\n": [2],
        "p = _h.config_path()\np.write_text('{}')\n": [2],
        "cfg = config_dir() / 'config.json'\ncfg.write_text('{}')\n": [2],
        "atomic_write(config_path(), '{}')\n": [1],
        "p = config_path()\natomic_json_write(p, {})\n": [2],
        # a nested function writes through its parent's name
        "def a():\n    path = config_path()\n    def inner():\n        atomic_write(path, '')\n": [
            4
        ],
        # the same name bound to something else in another function is not the config
        "def a():\n    path = config_path()\n    return path\n"
        "def b(path):\n    atomic_write(path, '')\n": [],
        # the home bound to a name first
        "home = config_dir()\natomic_write(home / 'config.json', '')\n": [2],
        "home = config_dir()\npath = home / 'config.json'\npath.write_text('')\n": [3],
        # a different file behind a same-named resolver, and an app's own config.json
        "atomic_write(ProviderSettings.config_path(name), '')\n": [],
        "p = app_dir / 'data' / 'config.json'\natomic_write(p, '')\n": [],
    }
    for source, lines in shapes.items():
        assert _raw_config_writes(source) == lines, source
    # A reader is not a writer.
    assert _raw_config_writes("p = config_path()\njson.loads(p.read_text())\n") == []
