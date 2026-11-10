"""An app's log lines reach every place the gateway's log is shown, from the moment it loads.

The gateway's log is shown in four places: the console (the stream a service manager keeps),
``gateway.log`` (what ``personalclaw logs`` prints for a foreground gateway), and Settings ›
Diagnostics' buffer and live stream. The file handler was attached once, at process start, to
``personalclaw`` and to the logger names the apps installed at that moment declared; the
Diagnostics handlers to ``personalclaw`` alone. So an app installed after the gateway started —
from onboarding, say — logged to the console and to none of the rest: a model provider's
"check the gateway log" pointed at an empty file, an empty Live logs and an empty
``personalclaw logs``. And a module that logs under its own name (``logging.getLogger(__name__)``,
which the loader makes a private name) reached none of them even at startup.

These drive the gateway's own start (``cli.main``), the dashboard's ring buffer and the real
``/api/logs`` route, and install a local app after both, the way the Store does.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported before the test runs: the probe imports the SDK at install.
import personalclaw.sdk.tool  # noqa: F401
from personalclaw import cli
from personalclaw.apps import app_manager, app_runtime, native_contract
from personalclaw.config.loader import config_dir
from personalclaw.dashboard.handlers import updates
from personalclaw.providers import registry as registry_module

APP = "log-probe"

_PROVIDER_PY = '''
"""The probe: a tool app whose code logs under three kinds of logger name."""

import logging

from log_probe_runtime import talk
from personalclaw.sdk.tool import ToolDefinition, ToolProvider, ToolResult

#: Named, the way an app names its own root.
named = logging.getLogger("log_probe")
#: The module's own name, which the loader makes a private one.
own = logging.getLogger(__name__)


class ProbeTools(ToolProvider):
    @property
    def name(self):
        return "log-probe"

    @property
    def display_name(self):
        return "Log Probe"

    async def list_tools(self):
        return [ToolDefinition(name="log_probe_ping", description="Answers pong.")]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="pong")


def create_tools(config=None):
    return ProbeTools()


def say(kind, level, text):
    """Log *text* at *level* from this app's code, under the logger *kind* names."""
    if kind == "package":
        talk.say(level, text)
    else:
        {"named": named, "module": own}[kind].log(level, text)
'''

_TALK_PY = """
import logging

#: A sibling package's module name, the way every channel app's runtime package logs.
logger = logging.getLogger(__name__)


def say(level, text):
    logger.log(level, text)
"""

KINDS = ("named", "module", "package")


def _write_probe(parent: Path) -> Path:
    root = parent / APP
    (root / "log_probe_runtime").mkdir(parents=True)
    (root / "log_probe_runtime" / "__init__.py").write_text("")
    (root / "log_probe_runtime" / "talk.py").write_text(textwrap.dedent(_TALK_PY))
    (root / "provider.py").write_text(textwrap.dedent(_PROVIDER_PY))
    (root / "README.md").write_text("# Log Probe\n")
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Log Probe",
        "description": "Logs from its own code, on request.",
        "provider": {"type": "tool", "implementation": "provider:create_tools"},
    }
    (root / "app.json").write_text(json.dumps(manifest, indent=1))
    return root


@pytest.fixture
def start_gateway(tmp_path, monkeypatch) -> Iterator[Callable[[], Path]]:
    """Start a gateway's logging — ``cli.main`` for ``gateway``, up to the point it would serve
    — and the dashboard's ring buffer, in an isolated home; the home. Called from the test's own
    body, so the console is the stderr the test reads. Teardown unloads the probe."""

    async def _serve(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(cli, "_resolve_gateway_args", lambda _args: {})
    monkeypatch.setattr(cli, "_gateway", _serve)
    monkeypatch.setattr(sys, "argv", ["personalclaw", "gateway", "--no-open"])
    monkeypatch.setattr("personalclaw.apps.app_manager.seed_builtin_apps", lambda: [])
    monkeypatch.setattr("personalclaw.apps.app_manager.repair_app_packages", lambda: [])
    registry_module.reset_provider_registry()

    def start() -> Path:
        cli.main()
        updates.install_log_ring_handler()
        return config_dir()

    yield start
    app_runtime.unload(APP, app_manager._manifest_of(APP), forget=True)
    registry_module.reset_provider_registry()
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", "") or "").startswith(str(config_dir())):
            sys.modules.pop(name, None)


def _install_after_start(tmp_path: Path) -> Callable[[str, int, str], None]:
    """Install the probe the way the Store does, after the gateway started; its ``say``."""
    res = app_manager.install(_write_probe(tmp_path / "src"), confirm=True)
    assert res.ok, res.error
    module = sys.modules.get(native_contract.namespaced_module_name(APP, "provider"))
    assert module is not None, "installing the probe did not load its code"
    return module.say


def _gateway_log(home: Path) -> str:
    path = home / "gateway.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _ring() -> str:
    return "\n".join(json.loads(entry)["msg"] for entry in list(updates._log_ring))


def _diagnostics_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/logs", updates.api_logs)
    return app


async def _read_until(resp: Any, wanted: set[str], timeout: float = 5.0) -> set[str]:
    """The *wanted* texts the SSE stream carried before *timeout*."""
    seen: set[str] = set()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while seen != wanted:
        left = deadline - loop.time()
        if left <= 0:
            break
        try:
            raw = await asyncio.wait_for(resp.content.readline(), left)
        except asyncio.TimeoutError:
            break
        line = raw.decode("utf-8").strip()
        if line.startswith("data: "):
            msg = json.loads(line[len("data: ") :])["msg"]
            seen |= {w for w in wanted if w in msg}
    return seen


async def _until_streaming(timeout: float = 5.0) -> None:
    """Wait until the stream's live handler is attached, so a record logged now is live."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        attached = [*logging.getLogger().handlers, *logging.getLogger("personalclaw").handlers]
        if any(isinstance(h, updates._QueueLogHandler) for h in attached):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the live log stream never attached its handler")


def test_an_app_installed_after_the_gateway_started_logs_to_every_surface(
    start_gateway, tmp_path
) -> None:
    gateway = start_gateway()
    texts = {kind: f"log-probe {kind} warning {uuid.uuid4().hex[:8]}" for kind in KINDS}

    async def _diagnostics() -> tuple[set[str], set[str]]:
        """Diagnostics open while the app is installed and logs, then opened again after."""
        async with TestClient(TestServer(_diagnostics_app())) as client:
            live = await client.get("/api/logs?lines=1000")
            await _until_streaming()

            say = _install_after_start(tmp_path)
            for kind, text in texts.items():
                say(kind, logging.WARNING, text)

            streamed = await _read_until(live, set(texts.values()))
            live.close()
            replay = await client.get("/api/logs?lines=1000")
            replayed = await _read_until(replay, set(texts.values()), timeout=2.0)
            replay.close()
        return streamed, replayed

    streamed, replayed = asyncio.run(_diagnostics())
    log = _gateway_log(gateway)
    missing = {
        surface: sorted(kind for kind, text in texts.items() if text not in body)
        for surface, body in {
            "gateway.log": log,
            "Diagnostics live stream": "\n".join(streamed),
            "Diagnostics replay": "\n".join(replayed),
            "Diagnostics ring": _ring(),
        }.items()
    }
    assert not any(
        missing.values()
    ), f"an app installed after the gateway started is missing from: {missing}"


def test_an_app_logs_at_the_level_the_owner_chose_until_it_is_uninstalled(
    start_gateway, tmp_path
) -> None:
    gateway = start_gateway()
    assert updates.apply_log_level("INFO")
    say = _install_after_start(tmp_path)
    loaded = f"log-probe info while installed {uuid.uuid4().hex[:8]}"
    say("named", logging.INFO, loaded)
    assert loaded in _gateway_log(gateway), "an installed app's INFO line missed gateway.log"
    assert loaded in _ring(), "an installed app's INFO line missed Diagnostics"

    assert app_manager.uninstall(APP)
    gone = f"log-probe info after uninstall {uuid.uuid4().hex[:8]}"
    warned = f"log-probe warning after uninstall {uuid.uuid4().hex[:8]}"
    say("named", logging.INFO, gone)
    say("named", logging.WARNING, warned)
    # Unloaded, the app's code is held to WARNING like any library's: its INFO line is not the
    # product's any more, and a warning from code still running is shown as any warning is.
    assert gone not in _gateway_log(gateway) and gone not in _ring()
    assert warned in _gateway_log(gateway) and warned in _ring()


def test_every_sink_shows_the_same_lines_a_library_held_to_warning(start_gateway, capsys) -> None:
    """One rule for all of them: a library's records below WARNING are shown nowhere, even with
    the level at DEBUG, and its warnings are shown everywhere, the console included."""
    gateway = start_gateway()
    assert updates.apply_log_level("DEBUG")
    library = logging.getLogger("some_library.client")
    quiet = f"library info {uuid.uuid4().hex[:8]}"
    loud = f"library warning {uuid.uuid4().hex[:8]}"
    own = f"core debug {uuid.uuid4().hex[:8]}"
    library.info(quiet)
    library.warning(loud)
    logging.getLogger("personalclaw.some_module").debug(own)

    surfaces = {
        "gateway.log": _gateway_log(gateway),
        "Diagnostics": _ring(),
        "console": capsys.readouterr().err,
    }
    for surface, body in surfaces.items():
        assert quiet not in body, f"{surface} showed a library's INFO line"
        assert loud in body, f"{surface} missed a library's warning"
        assert own in body, f"{surface} missed PersonalClaw's own DEBUG line at DEBUG"


def _a_reader(
    monkeypatch, cli_server, printed: bytes | None = None, status: int = 0
) -> list[list[str]]:
    """The log reader ``personalclaw logs`` runs, as ``tail`` would print the files it is handed
    (or *printed*, when given): each run's command line is recorded, and so is a ``terminate``.
    It must be started with its stdout a byte pipe and nothing else."""
    runs: list[list[str]] = []

    class _Reader:
        def __init__(self, cmd, **kwargs):
            assert kwargs == {"stdout": subprocess.PIPE}, kwargs
            runs.append(list(cmd))
            shown = [Path(a) for a in cmd if a.startswith("/")]
            self.stdout = io.BytesIO(
                printed if printed is not None else b"".join(f.read_bytes() for f in shown)
            )

        def wait(self, timeout=None) -> int:
            return status

        def terminate(self) -> None:
            runs.append(["terminate"])

    def _exec(*args) -> None:
        pytest.fail(f"the log reader replaced this process instead of being relayed: {args!r}")

    monkeypatch.setattr(cli_server.subprocess, "Popen", _Reader)
    monkeypatch.setattr(cli_server.os, "execvp", _exec)
    return runs


def test_personalclaw_logs_shows_a_line_written_as_it_came_as_text(
    tmp_path, monkeypatch, capsys
) -> None:
    """A line an older version wrote into the service's log as it came is shown as text: the
    reader's output is relayed through the rule, not handed to the terminal."""
    from personalclaw import cli_server
    from personalclaw.service import macos as svc_macos
    from personalclaw.service.common import Platform

    log_dir = tmp_path / "Logs"
    log_dir.mkdir()
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", log_dir / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", log_dir / "gateway.err")
    svc_macos.STDERR_LOG.write_text(
        "12:00:00 WARNING log_probe: ok\x1b\rdone\u202eend\n", encoding="utf-8"
    )
    monkeypatch.setattr(cli_server, "current_platform", lambda: Platform.LAUNCHD)
    _a_reader(monkeypatch, cli_server)
    with pytest.raises(SystemExit) as ended:
        cli_server._logs_cmd(type("Args", (), {"follow": False, "lines": 20})())
    assert ended.value.code == 0
    out = capsys.readouterr().out
    assert "log_probe: ok\\x1b\\rdone\\u202eend\n" in out, repr(out)
    assert not any(ch in out for ch in "\x1b\r\u202e"), repr(out)


def test_personalclaw_logs_of_a_foreground_gateway_shows_its_lines_as_text(
    tmp_path, monkeypatch, capsys
) -> None:
    """With no service, the home's gateway.log is read, and relayed the same way."""
    from personalclaw import cli_server
    from personalclaw.service.common import Platform

    (tmp_path / "gateway.log").write_bytes(b"12:00:00 WARNING log_probe: ok\x1b\rdone\n")
    monkeypatch.setattr(cli_server, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_server, "current_platform", lambda: Platform.UNSUPPORTED)
    runs = _a_reader(monkeypatch, cli_server)
    with pytest.raises(SystemExit) as ended:
        cli_server._logs_cmd(type("Args", (), {"follow": False, "lines": 20})())
    assert ended.value.code == 0
    assert runs == [["tail", "-n", "20", str(tmp_path / "gateway.log")]], runs
    out = capsys.readouterr().out
    assert "log_probe: ok\\x1b\\rdone\n" in out and "\x1b" not in out, repr(out)


def test_personalclaw_logs_relays_a_real_readers_bytes_and_its_status(capfd) -> None:
    """A real reader's output comes through a byte pipe a line at a time: a lone carriage return
    stays its escape, a byte that is not UTF-8 is the replacement character, and the command ends
    with the reader's own status."""
    from personalclaw import cli_server

    code = (
        "import sys; sys.stdout.buffer.write(bytes.fromhex('6f6b0d646f6e651b0a')"
        " + chr(0x202E).encode() + b'end' + bytes([0xFF])); sys.exit(3)"
    )
    with pytest.raises(SystemExit) as ended:
        cli_server._relay([sys.executable, "-I", "-c", code])
    assert ended.value.code == 3
    out, err = capfd.readouterr()
    assert out == "ok\\rdone\\x1b\n\\u202eend\ufffd", repr(out)
    assert err == "", repr(err)


@pytest.mark.parametrize(
    ("probed", "follow", "reader"),
    [
        ("row\n", False, ["journalctl", "--no-pager", "-u", "personalclaw.service", "-n", "20"]),
        (
            "",
            True,
            ["sudo", "journalctl", "--no-pager", "-u", "personalclaw.service", "-n", "20", "-f"],
        ),
    ],
    ids=["journalctl", "sudo"],
)
def test_personalclaw_logs_on_a_systemd_service_relays_the_journal_as_text(
    tmp_path, monkeypatch, capsys, probed, follow, reader
) -> None:
    """Under systemd the journal is read, through sudo when this user cannot read it, and what it
    prints is relayed through the rule too."""
    from personalclaw import cli_server
    from personalclaw.service import linux as svc_linux
    from personalclaw.service.common import Platform

    unit = tmp_path / "personalclaw.service"
    unit.write_text("[Unit]\n", encoding="utf-8")
    monkeypatch.setattr(svc_linux, "UNIT_PATH", unit)
    monkeypatch.setattr(cli_server, "current_platform", lambda: Platform.SYSTEMD)
    monkeypatch.setattr(
        cli_server.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, stdout=probed, stderr=""),
    )
    monkeypatch.setattr(sys, "stdin", type("Tty", (), {"isatty": lambda self: True})())
    runs = _a_reader(monkeypatch, cli_server, printed=b"x\x1bz\n")
    with pytest.raises(SystemExit) as ended:
        cli_server._logs_cmd(type("Args", (), {"follow": follow, "lines": 20})())
    assert ended.value.code == 0
    assert runs == [reader], runs
    out = capsys.readouterr().out
    assert "x\\x1bz\n" in out and "\x1b" not in out, repr(out)


def test_personalclaw_logs_into_a_pipe_that_closed_ends_quietly(tmp_path, monkeypatch) -> None:
    """Output whose reader went away (``personalclaw logs | head``) ends the log reader and the
    command with 141, and what Python flushes as it exits goes nowhere."""
    from personalclaw import cli_server

    log = tmp_path / "gateway.log"
    log.write_bytes(b"a line\n")
    runs = _a_reader(monkeypatch, cli_server)
    with open(tmp_path / "stdout", "wb") as kept:

        class _Closed:
            def write(self, text: str) -> int:
                raise BrokenPipeError(32, "Broken pipe")

            def flush(self) -> None:
                pass

            def fileno(self) -> int:
                return kept.fileno()

        monkeypatch.setattr(sys, "stdout", _Closed())
        with pytest.raises(SystemExit) as ended:
            cli_server._relay(["tail", "-n", "20", str(log)])
        assert os.path.samestat(os.fstat(kept.fileno()), os.stat(os.devnull))
    assert ended.value.code == 141
    assert runs == [["tail", "-n", "20", str(log)], ["terminate"]], runs


def test_a_ctrl_c_ends_the_log_reader_and_the_command_with_130(monkeypatch) -> None:
    """A Ctrl-C ends the reader and the command with 130, a sudo this user cannot signal too."""
    from personalclaw import cli_server

    ended_with: list[str] = []

    class _Interrupted(io.BytesIO):
        def readline(self, *args) -> bytes:
            raise KeyboardInterrupt

    class _Sudo:
        def __init__(self, cmd, **kwargs) -> None:
            self.stdout = _Interrupted()

        def terminate(self) -> None:
            ended_with.append("terminate")
            raise PermissionError(1, "Operation not permitted")

        def wait(self, timeout=None) -> int:
            ended_with.append("wait")
            return -2

    monkeypatch.setattr(cli_server.subprocess, "Popen", _Sudo)
    try:
        with pytest.raises(SystemExit) as ended:
            cli_server._relay(["sudo", "journalctl", "-f"])
    except KeyboardInterrupt:
        pytest.fail("a Ctrl-C went past the relay")
    assert ended.value.code == 130
    assert ended_with == ["terminate", "wait"]


def test_personalclaw_logs_ends_with_the_status_a_signal_gives(tmp_path, monkeypatch) -> None:
    """A reader a signal ended gives the command 128 and the signal's number, as a shell does."""
    from personalclaw import cli_server

    log = tmp_path / "gateway.log"
    log.write_bytes(b"a line\n")
    _a_reader(monkeypatch, cli_server, status=-15)
    with pytest.raises(SystemExit) as ended:
        cli_server._relay(["tail", "-n", "20", str(log)])
    assert ended.value.code == 143


def test_personalclaw_logs_on_a_macos_service_reads_the_stream_the_gateway_logs_to(
    tmp_path, monkeypatch
) -> None:
    """Under launchd the gateway's log lines are its stderr (the console handler's stream);
    its stdout file holds only what it printed. ``personalclaw logs`` read the stdout file."""
    from personalclaw import cli_server
    from personalclaw.service import macos as svc_macos
    from personalclaw.service.common import Platform

    log_dir = tmp_path / "Logs"
    log_dir.mkdir()
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", log_dir / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", log_dir / "gateway.err")
    svc_macos.STDOUT_LOG.write_text("Dashboard:\n", encoding="utf-8")
    svc_macos.STDERR_LOG.write_text("12:00:00 WARNING log_probe: a line\n", encoding="utf-8")
    monkeypatch.setattr(cli_server, "current_platform", lambda: Platform.LAUNCHD)
    execs = _a_reader(monkeypatch, cli_server)
    with pytest.raises(SystemExit):
        cli_server._logs_cmd(type("Args", (), {"follow": False, "lines": 20})())
    assert execs and execs[0][-1] == str(svc_macos.STDERR_LOG), execs


@pytest.mark.parametrize(
    ("follow", "lines", "options"), [(False, 20, ["-n", "20"]), (True, 5, ["-n", "5", "-f"])]
)
def test_personalclaw_logs_on_a_macos_service_shows_what_it_printed_too(
    tmp_path, monkeypatch, follow: bool, lines: int, options: list[str]
) -> None:
    """What a service printed (where the dashboard is, how to get a sign-in link) is launchd's
    stdout file, which no command showed: a healthy service logs nothing at the default level, so
    ``personalclaw logs`` showed nothing at all. It shows both files, as the journal does."""
    from personalclaw import cli_server
    from personalclaw.service import macos as svc_macos
    from personalclaw.service.common import Platform

    log_dir = tmp_path / "Logs"
    log_dir.mkdir()
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", log_dir / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", log_dir / "gateway.err")
    svc_macos.STDOUT_LOG.write_text("Dashboard:\n", encoding="utf-8")
    svc_macos.STDERR_LOG.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_server, "current_platform", lambda: Platform.LAUNCHD)
    execs = _a_reader(monkeypatch, cli_server)
    with pytest.raises(SystemExit):
        cli_server._logs_cmd(type("Args", (), {"follow": follow, "lines": lines})())
    assert execs == [
        ["tail", *options, str(svc_macos.STDOUT_LOG), str(svc_macos.STDERR_LOG)]
    ], execs


def test_personalclaw_logs_on_a_macos_service_leaves_out_a_stdout_file_that_is_not_there(
    tmp_path, monkeypatch
) -> None:
    """``tail`` handed a file that is not there says it cannot open it and exits 1, though the
    log lines are in the other file, so without launchd's stdout file the stderr file is shown
    alone."""
    from personalclaw import cli_server
    from personalclaw.service import macos as svc_macos
    from personalclaw.service.common import Platform

    log_dir = tmp_path / "Logs"
    log_dir.mkdir()
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", log_dir / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", log_dir / "gateway.err")
    svc_macos.STDERR_LOG.write_text("12:00:00 WARNING log_probe: a line\n", encoding="utf-8")
    monkeypatch.setattr(cli_server, "current_platform", lambda: Platform.LAUNCHD)
    execs = _a_reader(monkeypatch, cli_server)
    with pytest.raises(SystemExit):
        cli_server._logs_cmd(type("Args", (), {"follow": False, "lines": 20})())
    assert execs == [["tail", "-n", "20", str(svc_macos.STDERR_LOG)]], execs
