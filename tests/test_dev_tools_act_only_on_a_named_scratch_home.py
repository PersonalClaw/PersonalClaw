"""A committed dev tool acts only on a scratch home it was NAMED, and finds that home's gateway
from the record the gateway keeps in its home: never the default home, never a default port.

Measured before this change, the tools beside the suite reached the install the moment they were
run with nothing set. ``scripts/seed_tasks.py`` wiped the tasks of whatever answered
``http://127.0.0.1:10000``, the install's own port. ``scripts/memory_validate.py`` wrote and deleted
a probe row through that port and opened the default home's ``memory.db`` itself, read-write.
``session_validate.py`` and ``tool_surface_validate.py`` toggled providers off and on there. The
classify smoke told its reader to point it at the default home, and ran on it when nothing was set.
The screenshot capture defaulted to ``localhost:10000`` too, and the workflow exemplars, run
standalone as their headers said they could be, wrote synthetic runs into the default home.

Now every one of them asks ``harness/named_home.py`` (the JavaScript ones through
``scripts/lib/named_home.mjs``) and either gets the home it was named, or refuses with a
sentence before it opens a socket, a database or a browser.

Each refusal below runs the tool the way a developer does, as a program, with a scratch ``HOME``
(so "the default home" is a scratch folder too) and with every socket connection and SQLite open in
that program trapped. Against the code before this change, every one of these tests fails on the
trap or on the default home appearing, and none of them can reach a real gateway or database.
The positive runs drive the same tools against a real gateway this module starts on a scratch home.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fakes import home_fingerprint
from port_guard import GUARD

from harness import named_home

REPO = Path(__file__).resolve().parents[1]

#: Runs a tool's file as ``__main__`` with every socket connection and SQLite open refused, so a
#: tool that does not refuse first FAILS here instead of reaching anything. The trap raises a
#: RuntimeError, which no tool's retry loop catches, so it surfaces at once.
_TRAPPED = """
import runpy, socket, sqlite3, sys
def _trap(kind):
    def refuse(*args, **kwargs):
        raise RuntimeError(f"TRAPPED {kind} {args[1:] if kind == 'connect' else args}")
    return refuse
socket.socket.connect = _trap("connect")
socket.socket.connect_ex = _trap("connect")
sqlite3.connect = _trap("sqlite")
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""

#: Every committed Python tool that acts on a home, run as a program.
_PY_TOOLS = (
    "scripts/seed_tasks.py",
    "scripts/memory_validate.py",
    "scripts/session_validate.py",
    "scripts/tool_surface_validate.py",
    "scripts/smoke_unified_loop_classify.py",
)

#: The JavaScript tools that drive a gateway, with what each is given besides the home.
_JS_TOOLS = (
    ("docs/screenshots/capture.mjs", []),
    ("docs/demo/capture_demo.mjs", []),
    ("scripts/motion_frame_budget.mjs", []),
)

_NOTHING_NAMED = "Name the scratch home this runs against"
_DEFAULT_NAMED = "resolves to the default home"


@dataclass
class _Machine:
    """A scratch user's machine: their HOME, the default home inside it, and the environment a
    program run there gets (no PersonalClaw setting from the shell that runs the suite)."""

    root: Path

    @property
    def user_home(self) -> Path:
        return self.root / "user-home"

    @property
    def default_home(self) -> Path:
        return self.user_home / ".personalclaw"

    def env(self, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_")}
        env.update(HOME=str(self.user_home), **extra)
        return env


@pytest.fixture
def machine(tmp_path: Path) -> _Machine:
    m = _Machine(tmp_path)
    m.user_home.mkdir()
    return m


def _run(machine: _Machine, argv: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=REPO,
        env=machine.env(**env),
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _trapped(machine: _Machine, tool: str, *args: str, **env: str):
    return _run(machine, [sys.executable, "-c", _TRAPPED, str(REPO / tool), *args], **env)


# ── the helper: what it accepts, refuses, and exports ──────────────────────────────────────────


@pytest.fixture
def no_named_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset_env) -> Path:
    """A process whose HOME is a scratch folder and which names no PersonalClaw home, and no port.
    Returns the default home that HOME implies. The environment is put back after the test, a name
    the helper exported included."""
    user = tmp_path / "user-home"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    unset_env("PERSONALCLAW_HOME", "PERSONALCLAW_PORT")
    return user / ".personalclaw"


def test_a_tool_with_no_home_named_is_refused(no_named_home: Path) -> None:
    with pytest.raises(named_home.Refused, match=_NOTHING_NAMED):
        named_home.scratch_home()
    assert "PERSONALCLAW_HOME" not in os.environ
    assert not no_named_home.exists()


def test_the_default_home_is_refused_created_by_nothing_and_never_exported(
    no_named_home: Path,
) -> None:
    with pytest.raises(named_home.Refused, match=_DEFAULT_NAMED):
        named_home.scratch_home(no_named_home)
    assert "PERSONALCLAW_HOME" not in os.environ
    assert not no_named_home.exists(), "a refusal made the home it refused"


def test_a_link_to_the_default_home_is_refused(no_named_home: Path, tmp_path: Path) -> None:
    no_named_home.mkdir()
    link = tmp_path / "looks-like-scratch"
    link.symlink_to(no_named_home)
    with pytest.raises(named_home.Refused, match=_DEFAULT_NAMED):
        named_home.scratch_home(link)


def test_a_directory_the_resolver_replaces_with_the_default_home_is_refused(
    no_named_home: Path,
) -> None:
    """A system directory is never a home: the resolver warns and uses the default home instead,
    so a guard that compared the NAME with the default home would let the tool run on it."""
    with pytest.raises(named_home.Refused, match=_DEFAULT_NAMED):
        named_home.scratch_home("/usr/pclaw-dev-tool-probe-home")


def test_a_named_scratch_home_is_taken_and_exported(no_named_home: Path, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    assert named_home.scratch_home(scratch) == scratch.resolve()
    assert os.environ["PERSONALCLAW_HOME"] == str(scratch)
    assert not scratch.exists(), "naming a home is not making it"


def test_the_flag_names_the_home_before_the_environment(
    no_named_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "from-env"))
    assert named_home.scratch_home(tmp_path / "from-flag") == (tmp_path / "from-flag").resolve()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "from-env"))
    assert named_home.scratch_home() == (tmp_path / "from-env").resolve()


class _FakeGateway(http.server.BaseHTTPRequestHandler):
    """The three doors a dev tool uses: the health route that says whose gateway it is, the
    loopback token handshake, and one owner-only route."""

    secret = ""
    home_id = ""
    minted = "minted-owner-token"
    seen: list[dict[str, str]] = []

    def do_GET(self) -> None:  # noqa: N802 - the http.server method name
        self.seen.append({"path": self.path, **dict(self.headers.items())})
        if self.path == "/api/healthz":
            self._answer(200, {"status": "ok", "pid": os.getpid(), "home_id": self.home_id})
        elif self.path.startswith("/api/token/local"):
            ok = self.headers.get("X-Local-Secret") == self.secret
            self._answer(200 if ok else 403, {"token": self.minted} if ok else {"error": "nope"})
        elif self.headers.get("Authorization") == f"Bearer {self.minted}":
            self._answer(200, {"you": "owner"})
        else:
            self._answer(401, {"error": {"code": "auth_required", "message": "sign in first"}})

    def _answer(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def fake_gateway(tmp_path: Path) -> Iterator[tuple[Path, http.server.ThreadingHTTPServer]]:
    """A scratch home whose runtime record names a gateway this test serves, and that home's
    local secret, as a gateway writes both once it listens."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeGateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home = tmp_path / "scratch-home"
    home.mkdir()
    _FakeGateway.secret = "the-scratch-homes-secret"
    _FakeGateway.home_id = home_fingerprint(home)
    _FakeGateway.seen = []
    (home / ".local_secret").write_text(_FakeGateway.secret, encoding="utf-8")
    port = server.server_address[1]
    (home / "gateway.runtime.json").write_text(
        json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8"
    )
    try:
        yield home, server
    finally:
        server.shutdown()
        server.server_close()


def test_the_gateway_the_named_home_records_signs_the_tool_in(no_named_home, fake_gateway) -> None:
    home, server = fake_gateway
    gateway = named_home.scratch_gateway(home)
    assert gateway.url == f"http://127.0.0.1:{server.server_address[1]}"
    assert (gateway.home, gateway.pid, gateway.token) == (
        home.resolve(),
        os.getpid(),
        "minted-owner-token",
    )
    assert gateway.get("/api/whoami") == {"you": "owner"}
    identity, handshake, call = _FakeGateway.seen
    assert identity["path"] == "/api/healthz" and "X-Local-Secret" not in identity
    assert handshake["path"] == "/api/token/local?ttl=1h"
    assert handshake["X-Local-Secret"] == "the-scratch-homes-secret"
    assert call["Authorization"] == "Bearer minted-owner-token"


def test_a_gateway_of_another_home_is_refused_before_the_secret_is_sent(
    no_named_home, fake_gateway, tmp_path: Path
) -> None:
    """The record is inside the home, but a recycled pid can leave it naming a port another
    home's gateway now holds. The gateway is asked whose it is first, and is sent nothing more:
    the secret handshake used to be how it showed it, which handed the secret to whoever
    answered."""
    home, _server = fake_gateway
    _FakeGateway.home_id = home_fingerprint(tmp_path / "another-home")
    with pytest.raises(named_home.Refused, match="not one this tool may drive"):
        named_home.scratch_gateway(home)
    assert [seen["path"] for seen in _FakeGateway.seen] == ["/api/healthz"]
    assert not [seen for seen in _FakeGateway.seen if "X-Local-Secret" in seen]


def test_the_homes_gateway_that_does_not_know_its_secret_is_refused(
    no_named_home, fake_gateway
) -> None:
    """A gateway of the home that will not sign the tool in (the home's secret is not the one it
    holds: a second gateway was started on the home since) is said so."""
    home, _server = fake_gateway
    (home / ".local_secret").write_text("a-secret-the-gateway-does-not-hold", encoding="utf-8")
    with pytest.raises(named_home.Refused, match="did not sign this tool in"):
        named_home.scratch_gateway(home)


def test_a_home_whose_gateway_is_not_running_is_refused(no_named_home, tmp_path: Path) -> None:
    home = tmp_path / "stopped"
    home.mkdir()
    with pytest.raises(named_home.Refused, match="No gateway of"):
        named_home.scratch_gateway(home)
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    (home / "gateway.runtime.json").write_text(
        json.dumps({"port": 9, "pid": ended.pid}), encoding="utf-8"
    )
    with pytest.raises(named_home.Refused, match="names a process that has ended"):
        named_home.scratch_gateway(home)


def test_a_home_that_does_not_exist_is_refused_and_not_made(no_named_home, tmp_path: Path) -> None:
    missing = tmp_path / "typo"
    with pytest.raises(named_home.Refused, match="does not exist"):
        named_home.scratch_gateway(missing)
    assert not missing.exists()


# ── every tool refuses before it reaches anything ──────────────────────────────────────────────


@pytest.mark.parametrize("tool", _PY_TOOLS)
def test_a_tool_run_with_no_home_named_refuses_before_it_reaches_anything(machine, tool) -> None:
    done = _trapped(machine, tool)
    said = done.stdout + done.stderr
    assert "TRAPPED" not in said, f"{tool} reached for a gateway or a database:\n{said}"
    assert done.returncode != 0 and _NOTHING_NAMED in said, said
    assert not machine.default_home.exists(), f"{tool} made the default home"


@pytest.mark.parametrize("tool", _PY_TOOLS)
def test_a_tool_pointed_at_the_default_home_refuses_before_it_reaches_anything(
    machine, tool
) -> None:
    done = _trapped(machine, tool, PERSONALCLAW_HOME=str(machine.default_home))
    said = done.stdout + done.stderr
    assert "TRAPPED" not in said, f"{tool} reached for a gateway or a database:\n{said}"
    assert done.returncode != 0 and _DEFAULT_NAMED in said, said
    assert not machine.default_home.exists(), f"{tool} made the default home"


@pytest.mark.parametrize("slice_", ["slice_1", "slice_2", "slice_3", "slice_4", "slice_5"])
def test_an_exemplar_run_standalone_writes_no_run_into_the_default_home(machine, slice_) -> None:
    """The exemplar headers say each runs standalone. One that names no scratch home refuses; its
    smoke script names one (``tests/test_harness_exemplars.py`` runs those)."""
    done = _run(machine, [sys.executable, "-m", f"harness.exemplars.{slice_}.exemplar"])
    said = done.stdout + done.stderr
    assert done.returncode != 0 and _NOTHING_NAMED in said, said
    assert not machine.default_home.exists(), f"{slice_} wrote into the default home"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH: the JavaScript tools' refusals cannot be checked")
    return node


@pytest.mark.parametrize("tool, args", _JS_TOOLS, ids=[t for t, _ in _JS_TOOLS])
def test_a_javascript_tool_refuses_before_a_browser_starts(machine, tool, args) -> None:
    """No Playwright browser is reachable from this environment (an empty browsers path and a
    scratch HOME), so a tool that did not refuse first fails launching one, not by capturing."""
    nothing = machine.root / "no-browsers"
    nothing.mkdir()
    for named, expected in (
        ({}, _NOTHING_NAMED),
        ({"PERSONALCLAW_HOME": str(machine.default_home)}, _DEFAULT_NAMED),
    ):
        done = _run(
            machine,
            [_node(), str(REPO / tool), *args],
            PERSONALCLAW_PY=sys.executable,
            PLAYWRIGHT_BROWSERS_PATH=str(nothing),
            **named,
        )
        said = done.stdout + done.stderr
        assert done.returncode != 0 and expected in said, f"{tool} {named}:\n{said}"
        assert not machine.default_home.exists(), f"{tool} made the default home"


def test_the_render_smoke_pointed_at_the_default_home_refuses(machine) -> None:
    """With no home it serves the built bundle itself and needs no gateway; a gateway is driven
    only when a home is named, and the default home is refused."""
    nothing = machine.root / "no-browsers"
    nothing.mkdir()
    done = _run(
        machine,
        [_node(), str(REPO / "scripts/render_smoke.mjs"), "--home", str(machine.default_home)],
        PERSONALCLAW_PY=sys.executable,
        PLAYWRIGHT_BROWSERS_PATH=str(nothing),
    )
    said = done.stdout + done.stderr
    assert done.returncode != 0 and _DEFAULT_NAMED in said, said


# ── and works on a scratch home it was named ───────────────────────────────────────────────────


@dataclass
class _ScratchGateway:
    home: Path
    port: int
    token: str
    machine: _Machine

    def get(self, path: str) -> dict:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers={"Authorization": f"Bearer {self.token}"}
        )
        with opener.open(request, timeout=30) as resp:
            return json.loads(resp.read())

    def tool(self, tool: str) -> subprocess.CompletedProcess[str]:
        """Run *tool* on this gateway's home, named by flag, the way the headers say."""
        return subprocess.run(
            [sys.executable, str(REPO / tool), "--home", str(self.home)],
            cwd=REPO,
            env=self.machine.env(),
            capture_output=True,
            text=True,
            timeout=240,
            stdin=subprocess.DEVNULL,
        )


@pytest.fixture(scope="module")
def scratch_gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_ScratchGateway]:
    """A real gateway on a scratch home: a scratch HOME, its own PersonalClaw home with the update
    check off, empty importer sources, a port it picks itself, and no app backends or workers."""
    machine = _Machine(tmp_path_factory.mktemp("dev-tools-gateway"))
    machine.user_home.mkdir()
    home = machine.root / "pc-home"
    (home / "workspace").mkdir(parents=True)
    (home / "config.json").write_text(json.dumps({"updates": {"check_enabled": False}}), "utf-8")
    nothing = machine.root / "nothing"
    nothing.mkdir()
    out = machine.root / "gateway.out"
    env = machine.env(
        PERSONALCLAW_HOME=str(home),
        PERSONALCLAW_WORKSPACE=str(home / "workspace"),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        PERSONALCLAW_SKIP_APP_BACKENDS="1",
        PERSONALCLAW_SKIP_APP_WORKERS="1",
        PERSONALCLAW_ACP_NO_PROVISION="1",
        CLAUDE_CONFIG_DIR=str(nothing),
        CODEX_HOME=str(nothing),
    )
    with open(out, "w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "personalclaw",
                "gateway",
                "--port",
                "auto",
                "--no-open",
                "--json-ready",
            ],
            cwd=machine.root,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    try:
        ready = None
        deadline = time.monotonic() + 120
        while ready is None and time.monotonic() < deadline and proc.poll() is None:
            for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PERSONALCLAW_READY:"):
                    ready = json.loads(line.split(":", 1)[1])
            time.sleep(0.3)
        assert ready is not None, "the scratch gateway never said it was up:\n" + out.read_text()
        GUARD.own(int(ready["port"]))  # the port this module's own gateway chose, and said which
        yield _ScratchGateway(home, int(ready["port"]), str(ready["token"]), machine)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.timeout(300)
def test_seed_tasks_seeds_the_named_scratch_home(scratch_gateway) -> None:
    done = scratch_gateway.tool("scripts/seed_tasks.py")
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"into {scratch_gateway.home.resolve()} through" in done.stdout
    tasks = scratch_gateway.get("/api/tasks?limit=10000")
    assert tasks["total"] >= 20, tasks
    assert f"Seeded: {tasks['total']} tasks" in done.stdout


@pytest.mark.timeout(300)
@pytest.mark.parametrize("tool", ["scripts/memory_validate.py", "scripts/session_validate.py"])
def test_a_validator_is_clean_on_the_named_scratch_home(scratch_gateway, tool) -> None:
    done = scratch_gateway.tool(tool)
    assert done.returncode == 0 and done.stdout.startswith("CLEAN"), done.stdout + done.stderr


@pytest.mark.timeout(300)
def test_the_tool_universe_validator_runs_its_checks_on_the_named_scratch_home(
    scratch_gateway,
) -> None:
    """What this change owes is that it reaches the named home's gateway and runs its checks; the
    verdict on them is its own (two of its checks predate the current tool surface)."""
    done = scratch_gateway.tool("scripts/tool_surface_validate.py")
    assert done.stdout.startswith(("CLEAN", "FAIL:")), done.stdout + done.stderr


@pytest.mark.timeout(300)
def test_the_classify_smoke_reads_the_named_scratch_homes_models(scratch_gateway) -> None:
    """The scratch home binds no model, so the smoke says so about THAT home and stops."""
    done = scratch_gateway.tool("scripts/smoke_unified_loop_classify.py")
    assert done.returncode == 2, done.stdout + done.stderr
    assert f"No provider entries registered in {scratch_gateway.home.resolve()}" in done.stdout
