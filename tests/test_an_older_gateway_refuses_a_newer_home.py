"""An older PersonalClaw is refused a home a newer one served, unless the rollback is meant.

A home holds the files of the newest version that served it, in that version's shapes. An older
version started on it read them and wrote them back in its own older shapes, and nothing said so:
an app the owner had kept from months before started its bundled gateway on a home a newer
gateway had served the same evening.

Now each start's claim (``gateway_base.claim_home``) holds the home to the newest version that has
served it, kept in ``<home>/gateway.version.json``. A newer or equal start claims as before, and a
newer one raises the stamp. An older one ends at the claim, before it seeds, binds, publishes or
writes the local secret, with exit ``gateway_base.HOME_NEWER_EXIT`` and a sentence that names both
versions and the two ways on. ``PERSONALCLAW_ROLL_BACK_FROM=<the stamped version>`` is the owner
saying the rollback is meant, for that one start: the claim lowers the stamp to the version
starting and takes the variable out of the environment, so no child and no restart carries it.

Each start here is a process of its own running the real ``cli.main``, with a scratch ``HOME`` and
``PERSONALCLAW_HOME``. The stand-in after the claim (:data:`_STARTER`) says ``SERVING`` and what
it was started with, instead of booting a whole gateway, and the version it runs as is set per
start (``STARTER_VERSION``), in place of the installed one.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import pytest

from personalclaw import cli_server, gateway_base, restart_request, seed
from personalclaw.env import forget_launch_variable

#: How long a start may take to say it serves, or to exit.
_WAIT_SECS = 60.0

#: A start: ``cli.main`` with the command line it is given, running as ``STARTER_VERSION``, and in
#: place of the gateway's boot, ``SERVING <pid>`` and then ``CARRIES <json>``: whether the process's
#: environment and the environment a restart starts with still hold the rollback variable. It then
#: waits until it is ended.
_STARTER = textwrap.dedent("""
    import json, os, sys, time

    import personalclaw
    from personalclaw import cli, env, gateway_base

    personalclaw._version = lambda: os.environ["STARTER_VERSION"]


    async def _serve(**_kwargs):
        carries = {
            "environ": os.environ.get(gateway_base.ROLL_BACK_ENV, ""),
            "launch": env.launch_env().get(gateway_base.ROLL_BACK_ENV, ""),
        }
        print(f"SERVING {os.getpid()}", flush=True)
        print("CARRIES " + json.dumps(carries), flush=True)
        while True:
            time.sleep(3600)


    cli._gateway = _serve
    sys.argv = sys.argv[1:]
    cli.main()
    """)


class _Started:
    """A start, its output read as it comes."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self.out: list[str] = []
        self.err: list[str] = []
        self._readers = [
            threading.Thread(target=self._read, args=(proc.stdout, self.out), daemon=True),
            threading.Thread(target=self._read, args=(proc.stderr, self.err), daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    @staticmethod
    def _read(stream: IO[str] | None, into: list[str]) -> None:
        if stream is not None:
            for line in stream:
                into.append(line)

    def said(self, word: str) -> str:
        """What follows *word* on the first stdout line that starts with it."""
        deadline = time.monotonic() + _WAIT_SECS
        while time.monotonic() < deadline:
            for line in list(self.out):
                if line.startswith(f"{word} "):
                    return line[len(word) + 1 :].strip()
            if self.proc.poll() is not None and not any(r.is_alive() for r in self._readers):
                pytest.fail(
                    f"the start exited {self.proc.returncode} before it said {word}: "
                    f"{''.join(self.err)}"
                )
            time.sleep(0.05)
        pytest.fail(f"the start did not say {word} within {_WAIT_SECS:.0f}s")

    def ended(self) -> tuple[int, str]:
        """The start's exit status and stderr, once it has exited by itself."""
        try:
            self.proc.wait(timeout=_WAIT_SECS)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the start was still running after {_WAIT_SECS:.0f}s: it was not refused")
        for reader in self._readers:
            reader.join(timeout=10)
        return self.proc.returncode, "".join(self.err)

    def end(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=30)
        for reader in self._readers:
            reader.join(timeout=10)


@dataclass
class _World:
    """Scratch homes on one machine, and every start a test makes there, ended afterwards."""

    root: Path
    env: dict[str, str]
    starter: Path
    started: list[_Started] = field(default_factory=list)

    def home(self, name: str = "home") -> Path:
        home = self.root / name
        if not home.exists():
            home.mkdir(mode=0o700)
            (home / "config.json").write_text(
                json.dumps({"updates": {"check_enabled": False}}), encoding="utf-8"
            )
        return home.resolve()

    def start(self, home: Path, version: str, **env: str) -> _Started:
        proc = subprocess.Popen(
            [sys.executable, str(self.starter), "personalclaw", "gateway", "--port", "auto"],
            env={**self.env, "PERSONALCLAW_HOME": str(home), "STARTER_VERSION": version, **env},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        started = _Started(proc)
        self.started.append(started)
        return started

    def served(self, home: Path, version: str) -> None:
        """A start of *version* on *home* that claimed it, then ended: the home's stamp as it
        leaves one."""
        started = self.start(home, version)
        started.said("SERVING")
        started.end()


@pytest.fixture
def world(tmp_path: Path) -> Iterator[_World]:
    root = tmp_path / "world"
    for name in ("user", "claude", "codex"):
        (root / name).mkdir(parents=True)
    starter = root / "starter.py"
    starter.write_text(_STARTER, encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(root / "user"),
        "CLAUDE_CONFIG_DIR": str(root / "claude"),
        "CODEX_HOME": str(root / "codex"),
    }
    for name in (
        "PERSONALCLAW_PORT",
        "PERSONALCLAW_AUTH_MODE",
        "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
        gateway_base.ROLL_BACK_ENV,
    ):
        env.pop(name, None)
    made = _World(root=root, env=env, starter=starter)
    try:
        yield made
    finally:
        for started in made.started:
            started.end()


def _stamp(home: Path) -> dict:
    return json.loads((home / gateway_base.VERSION_FILE).read_text(encoding="utf-8"))


def _write_stamp(home: Path, newest: str) -> None:
    path = home / gateway_base.VERSION_FILE
    path.write_text(json.dumps({"newest": newest, "at": 1}), encoding="utf-8")
    path.chmod(0o600)


# ── the stamp ────────────────────────────────────────────────────────────────────────────────────


def test_a_start_stamps_its_home_with_its_version_privately(world: _World) -> None:
    home = world.home()
    world.served(home, "0.3.7")
    stamp = _stamp(home)
    assert stamp["newest"] == "0.3.7"
    assert "rolled_back_from" not in stamp
    assert stat.S_IMODE((home / gateway_base.VERSION_FILE).stat().st_mode) == 0o600


def test_a_newer_start_raises_the_stamp_and_an_equal_one_writes_nothing(world: _World) -> None:
    home = world.home()
    world.served(home, "0.3.6")
    world.served(home, "0.3.7")
    assert _stamp(home)["newest"] == "0.3.7"
    before = (home / gateway_base.VERSION_FILE).read_bytes()
    world.served(home, "0.3.7")
    assert (home / gateway_base.VERSION_FILE).read_bytes() == before


def test_a_link_in_the_stamps_place_fails_closed(world: _World) -> None:
    home = world.home()
    elsewhere = world.root / "elsewhere.json"
    elsewhere.write_text(json.dumps({"newest": "0.3.0"}), encoding="utf-8")
    (home / gateway_base.VERSION_FILE).symlink_to(elsewhere)
    code, err = world.start(home, "0.3.7").ended()
    assert code == 1
    assert gateway_base.VERSION_FILE in err
    assert json.loads(elsewhere.read_text(encoding="utf-8")) == {"newest": "0.3.0"}


@pytest.mark.parametrize("written", ["{not json", json.dumps({"newest": "soon"}), json.dumps([1])])
def test_a_stamp_that_cannot_be_read_fails_closed(world: _World, written: str) -> None:
    home = world.home()
    (home / gateway_base.VERSION_FILE).write_text(written, encoding="utf-8")
    code, err = world.start(home, "0.3.7").ended()
    assert code == 1
    path = home / gateway_base.VERSION_FILE
    assert f"{path} could not be used" in err
    assert f"move {path} aside and start again" in err, "the way out names the file"
    assert not (home / ".local_secret").exists()


# ── the refusal ──────────────────────────────────────────────────────────────────────────────────


def test_an_older_start_is_refused_with_both_versions_and_writes_nothing(world: _World) -> None:
    home = world.home()
    world.served(home, "0.3.7")
    before = sorted(p.name for p in home.iterdir())
    code, err = world.start(home, "0.3.6").ended()
    assert code == gateway_base.HOME_NEWER_EXIT == 4
    assert (
        "PersonalClaw 0.3.6 did not start: this home was last used by PersonalClaw 0.3.7, and an "
        "older version could lose what the newer one saved. Update to 0.3.7 or newer here. To roll "
        "back on purpose, take a snapshot first (`personalclaw snapshot`), then start once with "
        f"`{gateway_base.ROLL_BACK_ENV}=0.3.7`."
    ) in err
    assert sorted(p.name for p in home.iterdir()) == before, "a refused start wrote into the home"
    assert _stamp(home)["newest"] == "0.3.7"


def test_a_refused_start_lets_the_home_go_for_the_next_one(world: _World) -> None:
    home = world.home()
    world.served(home, "0.3.7")
    assert world.start(home, "0.3.6").ended()[0] == gateway_base.HOME_NEWER_EXIT
    newer = world.start(home, "0.3.7")
    newer.said("SERVING")


def test_a_refused_claim_holds_no_lock_in_its_process(stamped_home: Path, monkeypatch) -> None:
    """The claim takes the lock before it reads the stamp, so a refusal lets the lock go there and
    then, not only when its process ends."""
    import fcntl

    import personalclaw

    monkeypatch.setattr(personalclaw, "_version", lambda: "0.3.6")
    monkeypatch.setattr(gateway_base, "_held", {})
    with pytest.raises(gateway_base.HomeServedByNewer) as refused:
        gateway_base.claim_home()
    assert gateway_base._held == {}
    # The refusal is still held here, and the claim's frame with it, as a caller that logs it holds
    # them: so the lock goes because the claim closed it, not because that frame was collected.
    assert refused.tb is not None
    with open(stamped_home / gateway_base.LOCK_FILE, "a+b") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_UN)


@pytest.mark.parametrize(
    ("stamped", "starting", "refused"),
    [
        ("0.3.7", "0.3.7.dev1", True),
        ("0.3.7", "0.3.7rc1", True),
        ("0.3.7.post1", "0.3.7", True),
        ("0.3.7rc1", "0.3.7", False),
        ("0.3.7", "0.3.7.post1", False),
        ("0.3.10", "0.3.9", True),
    ],
)
def test_versions_are_ordered_as_they_ship(
    world: _World, stamped: str, starting: str, refused: bool
) -> None:
    """PEP 440, not text: a development build or a candidate is older than its release, a
    post-release newer, and 0.3.10 newer than 0.3.9."""
    home = world.home()
    _write_stamp(home, stamped)
    started = world.start(home, starting)
    if refused:
        assert started.ended()[0] == gateway_base.HOME_NEWER_EXIT
        assert _stamp(home)["newest"] == stamped
    else:
        started.said("SERVING")
        assert _stamp(home)["newest"] == starting


# ── a rollback meant ─────────────────────────────────────────────────────────────────────────────


def test_a_rollback_meant_claims_the_home_lowers_the_stamp_and_is_carried_no_further(
    world: _World,
) -> None:
    home = world.home()
    world.served(home, "0.3.7")
    started = world.start(home, "0.3.6", **{gateway_base.ROLL_BACK_ENV: "0.3.7"})
    started.said("SERVING")
    assert json.loads(started.said("CARRIES")) == {"environ": "", "launch": ""}
    stamp = _stamp(home)
    assert stamp["newest"] == "0.3.6"
    assert stamp["rolled_back_from"] == "0.3.7"
    assert any("rolled" in line and "back from 0.3.7" in line for line in started.err)
    assert _rolled_back_row(home, "from=0.3.7 to=0.3.6")


def _rolled_back_row(home: Path, resources: str) -> bool:
    """Whether the home's security log has its rollback row, once its writer has written it."""
    log = home / "security_events.jsonl"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                row = json.loads(line) if line.strip() else {}
                if row.get("operation") == "home_rolled_back" and resources in str(row):
                    return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("meant", ["0.3.6", "0.3.8", "yes", ""])
def test_a_rollback_naming_another_version_is_refused(world: _World, meant: str) -> None:
    """Only the stamped version opens the home: a variable left from an earlier rollback allows no
    other one."""
    home = world.home()
    world.served(home, "0.3.7")
    code, err = world.start(home, "0.3.6", **{gateway_base.ROLL_BACK_ENV: meant}).ended()
    assert code == gateway_base.HOME_NEWER_EXIT
    assert _stamp(home)["newest"] == "0.3.7"


def test_a_stale_rollback_on_a_start_it_does_not_concern_is_still_taken_out(world: _World) -> None:
    home = world.home()
    world.served(home, "0.3.6")
    started = world.start(home, "0.3.7", **{gateway_base.ROLL_BACK_ENV: "0.3.6"})
    started.said("SERVING")
    assert json.loads(started.said("CARRIES")) == {"environ": "", "launch": ""}
    assert "rolled_back_from" not in _stamp(home)


def test_forgetting_a_launch_variable_takes_it_from_both_environments(monkeypatch) -> None:
    from personalclaw import env

    monkeypatch.setattr(env, "_startup", {"KEEP": "1", "ONCE": "launched"})
    monkeypatch.setenv("ONCE", "now")
    assert forget_launch_variable("ONCE") == "now"
    assert "ONCE" not in os.environ
    assert env.launch_env() == {"KEEP": "1"}
    assert forget_launch_variable("ONCE") == ""


# ── the update and rollback paths ────────────────────────────────────────────────────────────────


@pytest.fixture
def stamped_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    _write_stamp(home, "0.3.7")
    return home


def test_a_restart_into_an_older_release_says_its_rollback_is_meant(stamped_home: Path) -> None:
    """Settings › Updates' Roll back, and an update to a pin set older, install an older release
    and restart into it: the new image's claim must hear that it is meant, or the gateway the
    owner asked to roll back would not come back at all."""
    assert gateway_base.rollback_into("0.3.5") == "0.3.7"
    assert gateway_base.rollback_into("v0.3.5") == "0.3.7", "a tag names its version too"
    assert gateway_base.rollback_into("0.3.7") == ""
    assert gateway_base.rollback_into("0.3.8") == ""
    assert gateway_base.rollback_into("") == ""


def test_the_restart_request_names_the_rollback_for_the_new_image_alone(monkeypatch) -> None:
    from personalclaw import shutdown_event

    monkeypatch.setattr(restart_request, "relaunch_argv", lambda: (sys.executable, "-c", "pass"))
    monkeypatch.setattr(shutdown_event, "set", lambda: None)
    monkeypatch.delenv(gateway_base.ROLL_BACK_ENV, raising=False)
    # Before the first request, so the teardown puts back the value this test found: a restart
    # left pending would make every later update in this process answer that it is restarting.
    monkeypatch.setattr(restart_request, "_pending", None)
    meant = restart_request.request_restart(roll_back_from="0.3.7")
    assert meant.env[gateway_base.ROLL_BACK_ENV] == "0.3.7"
    assert gateway_base.ROLL_BACK_ENV not in os.environ
    plain = restart_request.request_restart()
    assert gateway_base.ROLL_BACK_ENV not in plain.env


@pytest.mark.asyncio
async def test_the_dashboards_rollback_restarts_with_the_rollback_named(
    stamped_home: Path, monkeypatch
) -> None:
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import updates as upd

    restarts: list[dict] = []

    async def _installed(_state):
        return "0.3.5"

    async def _restart(_state, **kwargs):
        restarts.append(kwargs)

    class _State:
        def __init__(self) -> None:
            self._background_tasks: set = set()

        def push_refresh(self, *_args) -> None:
            pass

        def push_update_progress(self, *_args) -> None:
            pass

    monkeypatch.setattr(upd, "_upgrade_wheel", _installed)
    monkeypatch.setattr(upd, "_graceful_reexec", _restart)
    monkeypatch.setattr(upd, "_busy", lambda: None)
    monkeypatch.setattr(upd, "_installer_missing", lambda: "")
    monkeypatch.setattr(upd, "_live_auth_mode", lambda _r: "local_token")
    state = _State()
    request = make_mocked_request("POST", "/api/update")
    await upd._apply_pip_update(request, state)  # type: ignore[arg-type]
    for _ in range(100):
        if restarts:
            break
        await asyncio.sleep(0.01)
    assert restarts == [{"auth_mode": "local_token", "roll_back_from": "0.3.7"}]


def test_update_says_how_to_restart_into_an_older_release(stamped_home: Path) -> None:
    line = cli_server._restart_line("0.3.5")
    assert f"`{gateway_base.ROLL_BACK_ENV}=0.3.7 personalclaw restart`" in line
    assert "This home was last used by 0.3.7, newer than 0.3.5" in line
    assert cli_server._restart_line("0.3.8").strip() == (
        "↻ Restart the gateway to run the new code: personalclaw restart"
    )


def test_a_service_restart_rolls_the_home_back_itself(stamped_home: Path, monkeypatch) -> None:
    """launchd and systemd start the gateway with an environment of their own, so ``personalclaw
    restart`` lowers the stamp before it asks the service to restart."""
    import personalclaw

    monkeypatch.setattr(personalclaw, "_version", lambda: "0.3.6")
    with pytest.raises(gateway_base.HomeServedByNewer):
        gateway_base.roll_back_for_a_service("0.3.8")
    assert _stamp(stamped_home)["newest"] == "0.3.7"
    assert gateway_base.roll_back_for_a_service("0.3.7") == "0.3.7"
    assert _stamp(stamped_home) | {"at": 0} == {
        "newest": "0.3.6",
        "rolled_back_from": "0.3.7",
        "at": 0,
    }
    assert gateway_base.roll_back_for_a_service("0.3.7") == "", "nothing newer is left to undo"


def test_the_restart_command_applies_the_rollback_before_the_service_restarts(
    stamped_home: Path, monkeypatch
) -> None:
    import personalclaw

    monkeypatch.setattr(personalclaw, "_version", lambda: "0.3.6")
    order: list[str] = []
    service = type("Service", (), {"name": "personalclaw.test"})()
    monkeypatch.setattr(cli_server.service_controller, "this_homes_service", lambda: service)
    monkeypatch.setattr(cli_server.service_controller, "is_service_active", lambda: True)

    def _restart_service(_service):
        order.append(_stamp(stamped_home)["newest"])
        return ""

    monkeypatch.setattr(cli_server.service_controller, "restart_service", _restart_service)
    monkeypatch.setenv(gateway_base.ROLL_BACK_ENV, "0.3.7")
    cli_server._restart(None)
    assert order == ["0.3.6"], "the service restarted before the home was rolled back"


def test_the_restart_command_refuses_a_rollback_naming_another_version(
    stamped_home: Path, monkeypatch, capsys
) -> None:
    import personalclaw

    monkeypatch.setattr(personalclaw, "_version", lambda: "0.3.6")
    service = type("Service", (), {"name": "personalclaw.test"})()
    monkeypatch.setattr(cli_server.service_controller, "this_homes_service", lambda: service)
    restarted: list = []
    monkeypatch.setattr(
        cli_server.service_controller, "restart_service", lambda s: restarted.append(s) or ""
    )
    monkeypatch.setenv(gateway_base.ROLL_BACK_ENV, "0.3.5")
    with pytest.raises(SystemExit) as ended:
        cli_server._restart(None)
    assert ended.value.code == 1
    assert restarted == []
    said = capsys.readouterr().err
    assert f"{gateway_base.ROLL_BACK_ENV}=0.3.5 does not name 0.3.7" in said
    assert f"{gateway_base.ROLL_BACK_ENV}=0.3.7 personalclaw restart" in said
    assert _stamp(stamped_home)["newest"] == "0.3.7"


def test_a_seed_counts_the_stamp_as_the_claims_own(tmp_path: Path) -> None:
    """The claim writes the stamp before a gateway's ``--seed`` runs, so a home holding only the
    claim's files is still empty to the seed, and a replace leaves them where they are."""
    home = tmp_path / "home"
    home.mkdir()
    (home / gateway_base.LOCK_FILE).write_text("", encoding="utf-8")
    _write_stamp(home, "0.3.7")
    assert seed._held(home) == []
    (home / "memory.db").write_text("", encoding="utf-8")
    assert seed._held(home) == [home / "memory.db"]
