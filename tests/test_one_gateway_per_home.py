"""One gateway serves a home: a start on a home another gateway serves exits, naming that gateway.

A second gateway on a served home used to start. With ``--port auto`` it bound a port of its own
and wrote the home's runtime record and local secret over the first gateway's, so every command
and child then reached the second while both wrote the same stores. With ``--seed-replace`` it
first stopped the home's tmux server and removed the home from under the running gateway. Only
``restart`` checked for a running gateway.

Now every start claims its home first (``gateway_base.claim_home``): an exclusive lock on
``<home>/gateway.lock``, held until the gateway exits. Each start here is a process of its own, as
a person, the desktop, a service or a tool starts one. It runs the real ``cli.main``, and only what
comes after the claim and the seed is a stand-in (:data:`_STARTER`): a start that gets past the
claim writes the local secret and the runtime record as a gateway does once it listens, answers
``/api/healthz`` for its home, and says ``SERVING``, instead of booting a whole gateway. Every
process gets a scratch ``HOME`` and ``PERSONALCLAW_HOME`` of its own, and a stand-in ``tmux`` that
writes down what it was asked.
"""

from __future__ import annotations

import json
import os
import signal
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

from personalclaw import cli_server, gateway_base, self_update, tmux_substrate

#: How long a start may take to say it serves, or to exit.
_WAIT_SECS = 60.0

#: A start: ``cli.main`` with the command line it is given, and in place of the gateway's boot,
#: what a gateway does once it listens: a stand-in ``/api/healthz`` on a port of its own, the
#: home's local secret, the runtime record (unless ``STARTER_PUBLISHES=0``), then
#: ``SERVING <pid> <port>`` on stdout. A SIGTERM ends it the way a gateway's stop does, withdrawing
#: its record. ``STARTER_CHILD=1`` makes it start a child that outlives it, and
#: ``STARTER_EXEC_ONCE=1`` makes it replace its own image with a fresh start on the command line a
#: restart reads (``restart_request.relaunch_argv``), as a restart does (``restart_request.start``),
#: having first written ``STARTER_MARK`` into the home, as a gateway's work would.
_STARTER = textwrap.dedent("""
    import http.server, json, os, signal, subprocess, sys, threading, time

    from personalclaw import cli, gateway_base, home_gateway, restart_request, self_update
    from personalclaw.config.loader import config_dir


    def _healthz() -> int:
        body = json.dumps(
            {"status": "ok", "pid": os.getpid(), "home_id": home_gateway.home_id(), "root_ok": True}
        ).encode()

        class Answer(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        # The port the command line names, as a gateway binds it, else one of the system's.
        named = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else ""
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", int(named) if named.isdigit() else 0), Answer
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_address[1]


    async def _serve(**_kwargs):
        if os.environ.pop("STARTER_EXEC_ONCE", "") == "1":
            mark = os.environ.pop("STARTER_MARK", "")
            if mark:
                (config_dir() / mark).write_text("made while it served", encoding="utf-8")
            # What a restart starts: this gateway's command line, as the restart reads it.
            tail = restart_request.relaunch_argv()[len(self_update.cli_argv()) :]
            print("RELAUNCH " + json.dumps(tail, separators=(",", ":")), flush=True)
            image = [sys.executable, __file__, "personalclaw", *tail]
            os.execve(sys.executable, image, dict(os.environ))
        port = _healthz()
        fd = os.open(config_dir() / ".local_secret", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, os.urandom(16).hex().encode())
        os.close(fd)
        if os.environ.get("STARTER_PUBLISHES", "1") == "1":
            gateway_base.publish(port)
        if os.environ.get("STARTER_CHILD") == "1":
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(600)"], close_fds=False
            )
            print(f"CHILD {child.pid}", flush=True)

        def _stop(*_args):
            gateway_base.unpublish()
            os._exit(0)

        signal.signal(signal.SIGTERM, _stop)
        print(f"SERVING {os.getpid()} {port}", flush=True)
        while True:
            time.sleep(3600)


    cli._gateway = _serve
    sys.argv = sys.argv[1:]
    cli.main()
    """)


class _Started:
    """A start, its output read as it comes, line by line."""

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

    @property
    def pid(self) -> int:
        return self.proc.pid

    def said(self, word: str) -> list[str]:
        """The words after *word* on the first stdout line that starts with it. Fails when the
        start exits without saying it, or says nothing of it in time."""
        deadline = time.monotonic() + _WAIT_SECS
        while time.monotonic() < deadline:
            for line in list(self.out):
                if line.startswith(f"{word} "):
                    return line.split()[1:]
            if self.proc.poll() is not None and not any(r.is_alive() for r in self._readers):
                pytest.fail(
                    f"the start exited {self.proc.returncode} before it said {word}: "
                    f"{''.join(self.err)}"
                )
            time.sleep(0.05)
        pytest.fail(f"the start did not say {word} within {_WAIT_SECS:.0f}s")

    def serving(self) -> tuple[int, int]:
        """The pid and port of a start that got past its claim and serves."""
        pid, port = self.said("SERVING")
        return int(pid), int(port)

    def ended(self) -> tuple[int, str, str]:
        """The start's exit status, stdout and stderr, once it has exited by itself."""
        try:
            self.proc.wait(timeout=_WAIT_SECS)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the start was still running after {_WAIT_SECS:.0f}s: it was not refused")
        for reader in self._readers:
            reader.join(timeout=10)
        return self.proc.returncode, "".join(self.out), "".join(self.err)

    def end(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=30)
        for reader in self._readers:
            reader.join(timeout=10)


@dataclass
class _World:
    """Scratch homes on one machine: a ``HOME``, the importers' folders, a stand-in ``tmux`` on
    ``PATH``, the starter, and every process a test starts here, ended by its pid afterwards."""

    root: Path
    env: dict[str, str]
    starter: Path
    tmux_calls: Path
    started: list[_Started] = field(default_factory=list)
    pids: list[int] = field(default_factory=list)

    def home(self, name: str = "home") -> Path:
        home = self.root / name
        if not home.exists():
            home.mkdir(mode=0o700)
            # Before anything starts on it: no start here may check for updates.
            (home / "config.json").write_text(
                json.dumps({"updates": {"check_enabled": False}}), encoding="utf-8"
            )
        return home.resolve()

    def start(self, home: Path, *args: str, **env: str) -> _Started:
        proc = subprocess.Popen(
            [sys.executable, str(self.starter), "personalclaw", "gateway", *args],
            env={**self.env, "PERSONALCLAW_HOME": str(home), **env},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        started = _Started(proc)
        self.started.append(started)
        return started


@pytest.fixture
def world(tmp_path: Path) -> Iterator[_World]:
    root = tmp_path / "world"
    for name in ("user", "claude", "codex", "bin"):
        (root / name).mkdir(parents=True)
    starter = root / "starter.py"
    starter.write_text(_STARTER, encoding="utf-8")
    calls = root / "tmux-calls.txt"
    tmux = root / "bin" / "tmux"
    tmux.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\nexit 0\n', encoding="utf-8")
    tmux.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(root / "user"),
        "CLAUDE_CONFIG_DIR": str(root / "claude"),
        "CODEX_HOME": str(root / "codex"),
        "PATH": f"{root / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    for name in (
        "PERSONALCLAW_PORT",
        "PERSONALCLAW_AUTH_MODE",
        "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    ):
        env.pop(name, None)
    made = _World(root=root, env=env, starter=starter, tmux_calls=calls)
    try:
        yield made
    finally:
        for started in made.started:
            started.end()
        for pid in made.pids:
            if gateway_base.pid_is_alive(pid):
                os.kill(pid, signal.SIGKILL)


def _tree(home: Path) -> dict[str, tuple[int, int, int, int]]:
    """Every entry of *home* and the home itself: kind, size, modification time and inode. Two
    equal trees mean nothing was made, removed, replaced or written."""
    found = {}
    for path in [home, *sorted(home.rglob("*"))]:
        st = path.lstat()
        found[path.relative_to(home).as_posix()] = (
            stat.S_IFMT(st.st_mode),
            st.st_size,
            st.st_mtime_ns,
            st.st_ino,
        )
    return found


def _last_line(err: str) -> str:
    lines = err.strip().splitlines()
    return lines[-1] if lines else ""


def _refusal(home: Path, pid: int, port: int) -> str:
    return (
        f"PersonalClaw did not start: another gateway already serves {home} (pid {pid}, "
        f"http://127.0.0.1:{port}). Use that one, or stop it first: PERSONALCLAW_HOME={home} "
        "personalclaw stop"
    )


def _still_starting(home: Path) -> str:
    return (
        f"PersonalClaw did not start: another gateway already serves {home} (it is still "
        f"starting). Use that one, or stop it first: PERSONALCLAW_HOME={home} personalclaw stop"
    )


#: How each launcher starts a gateway: a person (and a service), a fixed port (the one the first
#: gateway listens on), ``personalclaw run``'s transient gateway, the desktop's bundled backend and
#: a harness's ``--test-mode``. ``{port}`` is the first gateway's port.
_LAUNCHES = {
    "a person": [],
    "a fixed port": ["--port", "{port}", "--no-open"],
    "run": ["--port", "auto", "--no-open", "--json-ready"],
    "the desktop": ["--port", "auto", "--json-ready", "--no-open"],
    "test mode": ["--test-mode"],
}


@pytest.mark.parametrize("launch", sorted(_LAUNCHES))
def test_a_second_start_exits_naming_the_first(world: _World, launch: str) -> None:
    home = world.home()
    first = world.start(home)
    pid, port = first.serving()
    record = (home / gateway_base.RUNTIME_FILE).read_bytes()
    secret = (home / ".local_secret").read_bytes()
    before = _tree(home)

    second = world.start(home, *(arg.format(port=port) for arg in _LAUNCHES[launch]))
    status, out, err = second.ended()

    assert status == gateway_base.HOME_SERVED_EXIT, (status, err)
    assert _last_line(err) == _refusal(home, pid, port), err
    assert "SERVING" not in out
    assert (home / gateway_base.RUNTIME_FILE).read_bytes() == record
    assert (home / ".local_secret").read_bytes() == secret
    assert _tree(home) == before, "the refused start wrote into the home"
    assert first.proc.poll() is None, "the first gateway stopped"


def test_seed_replace_refuses_a_served_home_before_any_write(world: _World) -> None:
    home = world.home()
    (home / "notes").mkdir()
    (home / "notes" / "keep.md").write_text("what the served home holds", encoding="utf-8")
    first = world.start(home)
    pid, port = first.serving()
    before = _tree(home)

    second = world.start(home, "--seed", "empty", "--seed-replace", "--port", "auto", "--no-open")
    status, _out, err = second.ended()

    assert status == gateway_base.HOME_SERVED_EXIT, (status, err)
    assert _last_line(err) == _refusal(home, pid, port), err
    assert _tree(home) == before, "the refused seed touched the served home"
    assert (home / "notes" / "keep.md").read_text(encoding="utf-8") == "what the served home holds"
    assert not world.tmux_calls.exists(), "the served home's tmux server was asked to stop"
    assert first.proc.poll() is None


@pytest.mark.parametrize("whose", ["its process is gone", "its process holds no claim"])
def test_a_dead_record_does_not_block_a_start(world: _World, whose: str) -> None:
    home = world.home()
    if whose == "its process is gone":
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(timeout=30)
        recorded_pid = gone.pid
    else:
        recorded_pid = os.getpid()  # alive, and holding no claim on this home
    (home / gateway_base.RUNTIME_FILE).write_text(
        json.dumps({"port": 9, "pid": recorded_pid}), encoding="utf-8"
    )
    (home / gateway_base.LOCK_FILE).touch()  # a lock a gateway left, which nothing holds now

    started = world.start(home, "--port", "auto", "--no-open")
    pid, port = started.serving()

    assert pid == started.pid
    assert json.loads((home / gateway_base.RUNTIME_FILE).read_text("utf-8")) == {
        "port": port,
        "pid": pid,
    }


def test_seed_replace_keeps_the_claim(world: _World) -> None:
    """A start that seeds holds its claim while it empties the home: the lock it holds stays the
    one at the home's ``gateway.lock``, so a second start is still refused."""
    home = world.home()
    (home / "old.txt").write_text("from before the seed", encoding="utf-8")
    (home / "old").mkdir()
    (home / "old" / "deep.txt").write_text("also from before", encoding="utf-8")

    seeded = world.start(home, "--seed", "empty", "--seed-replace", "--port", "auto", "--no-open")
    pid, port = seeded.serving()

    assert (home / gateway_base.LOCK_FILE).is_file()
    assert not (home / "old.txt").exists() and not (home / "old").exists()
    assert (home / "fixture.yaml").is_file()
    stopped = " ".join([*tmux_substrate.server_flags(home), "kill-server"])
    assert stopped in world.tmux_calls.read_text("utf-8").splitlines()
    second = world.start(home, "--port", "auto", "--no-open")
    status, _out, err = second.ended()
    assert status == gateway_base.HOME_SERVED_EXIT, (status, err)
    assert _last_line(err) == _refusal(home, pid, port), err


def test_restart_still_replaces_the_running_gateway(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``personalclaw restart`` with no service installed: it stops this home's gateway, and the
    fresh one it starts claims the home once the old one has exited."""
    home = world.home()
    for name in ("HOME", "PATH", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.setenv(name, world.env[name])
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.setattr(cli_server.service_controller, "this_homes_service", lambda: None)
    # The fresh gateway `restart` starts is a start of this test's, on the port the old one had.
    monkeypatch.setattr(
        self_update, "cli_argv", lambda: [sys.executable, str(world.starter), "personalclaw"]
    )
    old = world.start(home)
    old_pid, old_port = old.serving()
    threading.Thread(target=old.proc.wait, daemon=True).start()  # reaped as it exits

    cli_server._restart(None)

    deadline = time.monotonic() + _WAIT_SECS
    fresh = gateway_base.live_gateway()
    while (fresh is None or fresh.pid == old_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
        fresh = gateway_base.live_gateway()
    assert fresh is not None and fresh.pid != old_pid, "no fresh gateway took the home"
    world.pids.append(fresh.pid)
    assert old.proc.wait(timeout=30) == 0, "the old gateway did not end the way a stop ends it"
    assert fresh.port == old_port
    probe = world.start(home, "--port", "auto", "--no-open")
    status, _out, err = probe.ended()
    assert status == gateway_base.HOME_SERVED_EXIT, (status, err)
    assert f"(pid {fresh.pid}, http://127.0.0.1:{old_port})" in err, err
    os.kill(fresh.pid, signal.SIGTERM)
    try:
        os.waitpid(fresh.pid, 0)  # `restart` started it, from this process
    except ChildProcessError:
        pass  # already reaped by subprocess's own clean-up of the Popen `restart` let go


def test_the_claim_ends_with_its_process(world: _World) -> None:
    """A gateway killed outright lets go of its home, and a child it started that outlives it does
    not keep the claim: a fresh start serves, though the killed one's record is still there."""
    home = world.home()
    first = world.start(home, STARTER_CHILD="1")
    (child,) = first.said("CHILD")
    world.pids.append(int(child))
    first.serving()
    first.proc.send_signal(signal.SIGKILL)
    first.proc.wait(timeout=30)
    assert gateway_base.pid_is_alive(int(child)), "the child should outlive the gateway"
    assert (home / gateway_base.RUNTIME_FILE).exists(), "a killed gateway leaves its record"

    again = world.start(home, "--port", "auto", "--no-open")
    pid, _port = again.serving()
    assert pid == again.pid


def test_a_restart_image_claims_again(world: _World) -> None:
    """A restart replaces the gateway's process image (``os.execve``) in the same process. The
    lock is never handed across an exec, so the new image claims the home as any start does,
    rather than finding it held by the image it replaced."""
    home = world.home()
    restarted = world.start(home, "--port", "auto", "--no-open", STARTER_EXEC_ONCE="1")
    pid, _port = restarted.serving()
    assert pid == restarted.pid, "the new image runs in the same process"


@pytest.mark.parametrize(
    "seeding",
    [["--seed", "empty"], ["--seed", "empty", "--seed-replace"]],
    ids=["seed", "seed-replace"],
)
def test_a_restart_never_seeds_the_home_it_serves(world: _World, seeding: list[str]) -> None:
    """A start that seeds its home seeds it once. Its restart starts on the command line it was
    started with, less the seed: replayed there, ``--seed-replace`` would empty the home the
    gateway serves, and a plain ``--seed`` would find the home in use and refuse it, so the
    gateway would not come back."""
    home = world.root / "fresh"
    home.mkdir(mode=0o700)
    home = home.resolve()

    started = world.start(
        home,
        *seeding,
        "--port",
        "auto",
        "--no-open",
        STARTER_EXEC_ONCE="1",
        STARTER_MARK="made-while-serving.txt",
    )
    (relaunch,) = started.said("RELAUNCH")
    pid, _port = started.serving()

    assert json.loads(relaunch) == ["gateway", "--port", "auto", "--no-open"]
    assert pid == started.pid, "the restarted image runs in the same process"
    assert (home / "made-while-serving.txt").read_text("utf-8") == "made while it served"
    assert (home / "fixture.yaml").is_file()


#: The options that seed the home, by the names the gateway's parser files them under.
_SEEDING = {
    "seed",
    "seed_replace",
    "seed_local_model",
    "local_model_endpoint",
    "local_model",
    "local_model_apps_dir",
}


@pytest.mark.parametrize(
    ("argv", "kept"),
    [
        (
            ["pc", "gateway", "--seed", "demo-home", "--seed-replace", "--port", "19000"],
            ["pc", "gateway", "--port", "19000"],
        ),
        (
            ["pc", "-v", "gateway", "--seed=empty", "--json-ready", "--seed-r", "--no-open"],
            ["pc", "-v", "gateway", "--json-ready", "--no-open"],
        ),
        (
            [
                "pc",
                "gateway",
                "--seed",
                "demo-home",
                "--seed-local-model",
                "--local-model",
                "small:1b",
                "--local-model-endpoint",
                "http://models.example.test",
                "--local-model-apps-dir",
                "/srv/apps",
                "--test-mode",
            ],
            ["pc", "gateway", "--test-mode"],
        ),
        (
            ["pc", "gateway", "--port", "auto", "--json-ready", "--no-open"],
            ["pc", "gateway", "--port", "auto", "--json-ready", "--no-open"],
        ),
    ],
    ids=["value as the next word", "value after = and a prefix", "local model", "no seed"],
)
def test_a_restart_keeps_every_option_but_the_seed(argv: list[str], kept: list[str]) -> None:
    """The restart's command line is the start's, read by the gateway's own parser, without the
    seed: every other option the gateway was started with means what it meant."""
    from personalclaw import cli

    assert cli._without_the_seed(argv) == kept
    parser = cli.build_parser()
    before, after = vars(parser.parse_args(argv[1:])), vars(parser.parse_args(kept[1:]))
    assert {k: v for k, v in before.items() if k not in _SEEDING} == {
        k: v for k, v in after.items() if k not in _SEEDING
    }
    assert not any(after[name] for name in _SEEDING)


def test_two_homes_are_each_claimed_at_once(world: _World) -> None:
    """Control: the claim is the home's, so gateways of two homes serve side by side."""
    one, two = world.home("one"), world.home("two")
    first = world.start(one, "--port", "auto", "--no-open")
    second = world.start(two, "--port", "auto", "--no-open")
    assert first.serving()[0] == first.pid
    assert second.serving()[0] == second.pid


def test_a_start_before_the_first_one_says_where_it_listens_is_told_it_is_still_starting(
    world: _World,
) -> None:
    home = world.home()
    first = world.start(home, STARTER_PUBLISHES="0")
    first.serving()
    second = world.start(home, "--port", "auto", "--no-open")
    status, _out, err = second.ended()
    assert status == gateway_base.HOME_SERVED_EXIT, (status, err)
    assert _last_line(err) == _still_starting(home), err


def test_a_record_the_claims_holder_did_not_write_is_never_named(world: _World) -> None:
    """The gateway holding the claim has not said where it listens yet, and the record in the home
    is an older one, naming a live process and a port where another home's gateway answers. The
    refusal asks who answers there before it names anyone, and names no one."""
    home, other = world.home(), world.home("other")
    elsewhere = world.start(other, "--port", "auto", "--no-open")
    _other_pid, other_port = elsewhere.serving()
    holder = world.start(home, STARTER_PUBLISHES="0")
    holder.serving()
    (home / gateway_base.RUNTIME_FILE).write_text(
        json.dumps({"port": other_port, "pid": os.getpid()}), encoding="utf-8"
    )

    second = world.start(home, "--port", "auto", "--no-open")
    status, _out, err = second.ended()

    assert status == gateway_base.HOME_SERVED_EXIT, (status, err)
    assert _last_line(err) == _still_starting(home), err


def test_a_home_whose_lock_cannot_be_taken_is_not_served(world: _World) -> None:
    """A link where the lock belongs is never followed: the lock is not opened, no file is made
    where it points, and the start is refused, since without the lock nothing keeps a second
    gateway off the home."""
    home = world.home()
    target = world.root / "elsewhere" / "gateway.lock"
    (home / gateway_base.LOCK_FILE).symlink_to(target)
    before = _tree(home)

    started = world.start(home, "--port", "auto", "--no-open")
    status, out, err = started.ended()

    assert status == 1, (status, err)
    assert "SERVING" not in out
    lock = home / gateway_base.LOCK_FILE
    assert _last_line(err) == (
        f"PersonalClaw did not start: {lock} could not be locked (a symbolic link in this home, "
        "which no lock is opened through). That lock is what keeps a second gateway off "
        f"{home}, so the home is not served without it."
    ), err
    assert not target.parent.exists(), "a file was made where the link points"
    assert _tree(home) == before


def test_a_start_refused_for_its_command_line_touches_nothing(world: _World) -> None:
    """The command line is read before the home is claimed or seeded: a start it refuses leaves
    the home as it was, a ``--seed-replace`` it carried included."""
    home = world.home()
    (home / "keep.md").write_text("still here", encoding="utf-8")
    before = _tree(home)

    started = world.start(home, "--seed", "empty", "--seed-replace", "--port", "not-a-port")
    status, _out, err = started.ended()

    assert status == 2, (status, err)
    assert "--port must be an integer or 'auto'" in err
    assert _tree(home) == before
    assert not world.tmux_calls.exists()


def test_a_process_asked_again_for_its_home_keeps_its_one_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway's own process may ask again (a second caller in it, a test driving ``cli.main``
    twice): it gets the claim it holds, and does not find its own lock held against it."""
    monkeypatch.setattr(gateway_base, "_held", {})
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    first = gateway_base.claim_home()
    again = gateway_base.claim_home()

    assert again is first
    assert first.home == home.resolve()
    assert first.lock == home.resolve() / gateway_base.LOCK_FILE
    assert stat.S_IMODE(first.lock.stat().st_mode) == 0o600
    assert first.lock.read_bytes() == b""
