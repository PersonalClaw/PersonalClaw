"""A command a run starts keeps to the run's egress tier: it is launched without the network.

A run's egress tier held for every request the egress guard is asked about, but a command the run
starts is a program of its own, and nothing it reaches asks the guard: a bash or a script action, a
loop's or a workflow's check, a workflow's setup and teardown steps, an effect's teardown and the
agent's shell all reached the network whatever the tier said. The command sandbox hid credential
paths and never the network. Now a command started for a run whose tier takes the network away is
launched in the OS sandbox without it: a network namespace of its own on Linux, a profile that
denies the network on macOS. ``off`` takes it away, and so do ``listed`` and ``registry``: a program
cannot be held to a list of hosts without a proxy, so a command in such a run reaches no network
either. Where the sandbox cannot take the network away the command is refused, in a sentence that
says why and what to change, with a row in the audit log. A command of a run whose tier is ``all``,
a command made for no run, and an agent CLI's own process (which reaches its model itself) keep the
network.

Every endpoint is a stand-in on this machine, so nothing leaves it.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shlex
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw import mcp_core, sandbox
from personalclaw.config.loader import config_dir
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.guardrails.ceiling import ceiling_path, reset_ceiling
from personalclaw.guardrails.policy import unattended_dispatch_key
from personalclaw.net.policy import egress_held_to
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.sel import sel

#: A chat's session, as the built-in agent binds it around each tool call it dispatches.
CHAT = "dashboard:research-chat"
#: A loop's stage worker, a run nobody watches.
LOOP_WORKER = "loop-a1b2c3"

#: What a command run with no network prints, and the exit it ends with.
UNREACHED = 3
#: How a failed command's result says it ran with no network, and why, for a run whose tier is off.
NO_NETWORK = "It ran with no network access: "
OFF = " (safety profile egress tier 'off')"

#: A client of the stand-in: one GET, no proxy, and an exit that says whether it got an answer.
_CLIENT = """\
import sys
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    opener.open(sys.argv[1], timeout=5).read()
except Exception as exc:
    print("unreached:", type(exc).__name__, exc)
    sys.exit(%d)
print("reached")
""" % (UNREACHED,)


# ── the stand-ins ────────────────────────────────────────────────────────────


class _Service(http.server.ThreadingHTTPServer):
    """A service on this machine that records every request it is sent."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ServiceHandler)
        self.paths: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _ServiceHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server's name
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def service(monkeypatch):
    for name in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    server = _Service()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def reach(tmp_path, service):
    """The command that asks the stand-in for *path*, as a command line."""
    client = tmp_path / "reach.py"
    client.write_text(_CLIENT, encoding="utf-8")

    def command(path: str) -> str:
        return " ".join(
            shlex.quote(part) for part in (sys.executable, str(client), f"{service.url}/{path}")
        )

    return command


def _os_sandbox_takes_the_network() -> bool:
    """Whether this host's command sandbox can take a command's network away: macOS sandbox-exec,
    or Linux user and mount namespaces that allow a network namespace too."""
    sandbox.reset_backend()
    backend = sandbox.detect_backend("auto")
    if backend == "namespace":
        return getattr(sandbox, "_probe_unshare_net", sandbox._probe_unshare)()
    return backend == "sandbox-exec"


needs_the_os_sandbox = pytest.mark.skipif(
    not _os_sandbox_takes_the_network(),
    reason="needs a command sandbox that can take the network away (macOS sandbox-exec, or Linux "
    "user, mount and network namespaces)",
)


@pytest.fixture
def no_os_sandbox(monkeypatch):
    """A host that offers no command sandbox at all."""
    monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": "none")


def _ceiling(egress: str) -> None:
    """The operator ceiling bounding every run's egress tier to *egress*."""
    path = ceiling_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "scopes": {"egress": {"value": egress}}}), encoding="utf-8"
    )
    reset_ceiling()


def _owner_allows(*hosts: str) -> None:
    """The owner's Allowed hosts in Settings → Security → Network egress."""
    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"egress": {"allow_hosts": list(hosts)}}}), encoding="utf-8"
    )


@contextmanager
def _in_run(session_key: str):
    """A call made inside a run, bound as every seam that dispatches a tool call binds it."""
    token = mcp_core.set_current_session_key(session_key)
    try:
        yield
    finally:
        mcp_core.reset_current_session_key(token)


def _trigger(tid: str, provider: str, config: dict) -> SimpleNamespace:
    """A store trigger whose action is *provider* with *config*, its action allowed."""
    return SimpleNamespace(
        id=tid,
        kind="file",
        workflow={"inline": {"provider": provider, "config": config}},
        capabilities={"providers": [provider]},
    )


def _fire(trigger) -> None:
    """The trigger fires, through the dispatch every unattended fire shares."""
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(
            trigger, {"trigger_id": trigger.id, "kind": trigger.kind}, event="file.changed"
        )
    )


def _runs(tid: str) -> list[dict]:
    rows, _total = asyncio.run(ScheduleRunStore(config_dir()).list_for_job(tid, 0, 20))
    return rows


def _rows(**match: str) -> list[dict]:
    return [
        row
        for row in reversed(sel().recent(300))
        if all(row.get(k) == v or (row.get("metadata") or {}).get(k) == v for k, v in match.items())
    ]


# ── the bash action ──────────────────────────────────────────────────────────


@needs_the_os_sandbox
def test_a_bash_action_whose_egress_is_off_cannot_reach_a_service_on_this_machine(service, reach):
    """🔴 Red on integration: the command reached the stand-in, though no run may reach any host."""
    _ceiling("off")

    _fire(_trigger("file:report", "bash", {"command": reach("report")}))

    assert service.paths == [], "a command whose run has no network reached a host"
    (run,) = _runs("file:report")
    assert run["status"] == "failure", run
    assert run["error"].endswith(f"{NO_NETWORK}egress is off for this run{OFF}."), run["error"]
    (row,) = _rows(operation="egress_launch", outcome="denied")
    assert row["caller_identity"] == unattended_dispatch_key("trigger:file:report"), row
    assert "egress is off for this run" in row["error"], row


@needs_the_os_sandbox
def test_a_bash_action_on_the_default_tier_still_reaches_it(service, reach):
    _fire(_trigger("file:report", "bash", {"command": reach("report")}))

    assert service.paths == ["/report"]
    (run,) = _runs("file:report")
    assert run["status"] == "success", run
    assert _rows(operation="egress_launch") == []


@needs_the_os_sandbox
def test_a_command_in_a_run_held_to_its_listed_hosts_has_no_network_either(service, reach):
    """A per-host limit cannot be kept by an arbitrary program without a proxy, so a run whose tier
    lists hosts launches its commands with no network, even toward a host on Allowed hosts.

    🔴 Red on integration: the command reached the host the run's list names, and would have
    reached any other as well."""
    _owner_allows("127.0.0.1")
    _ceiling("listed")

    _fire(_trigger("file:report", "bash", {"command": reach("report")}))

    assert service.paths == []
    (run,) = _runs("file:report")
    assert f"{NO_NETWORK}this run reaches only the hosts it lists" in run["error"], run
    (row,) = _rows(operation="egress_launch", outcome="denied")
    assert "reaches only the hosts" in row["error"], row


def test_with_no_sandbox_a_bash_action_whose_egress_is_off_is_refused(
    service, reach, no_os_sandbox
):
    """🔴 Red on integration: with no sandbox to take the network away, the command ran with it."""
    _ceiling("off")

    _fire(_trigger("file:report", "bash", {"command": reach("report")}))

    assert service.paths == []
    (run,) = _runs("file:report")
    said = run["error"]
    assert said.startswith("This command was not run: egress is off for this run"), said
    assert "no network access" in said, said
    assert str(ceiling_path()) in said, "the refusal does not say where the tier is set"
    (row,) = _rows(event_type="command_refused")
    assert row["metadata"]["control"] == "sandbox", row
    assert row["outcome"] == "refused"


def test_with_no_sandbox_a_bash_action_on_the_default_tier_still_runs(
    service, reach, no_os_sandbox
):
    _fire(_trigger("file:report", "bash", {"command": reach("report")}))

    assert service.paths == ["/report"]
    assert _rows(event_type="command_refused") == []


# ── the script action ────────────────────────────────────────────────────────


_SCRIPT = """\
import urllib.request


def run(ctx):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(%r, timeout=5).read()
    except Exception as exc:
        raise Report("unreached: %%s" %% type(exc).__name__)
    return "reached"
"""


@pytest.fixture
def script(service, monkeypatch):
    """A script action's script under the crons folder, asking the stand-in for one page."""
    from personalclaw import gateway_base

    monkeypatch.setattr(gateway_base, "resolve_port", lambda: 1)
    crons = config_dir() / "crons"
    crons.mkdir(parents=True, exist_ok=True)
    probe = crons / "probe.py"
    probe.write_text(_SCRIPT % f"{service.url}/script", encoding="utf-8")
    return f"{probe}:run"


@needs_the_os_sandbox
def test_a_script_action_whose_egress_is_off_cannot_reach_it(service, script):
    """🔴 Red on integration: the script reached the stand-in. Its launch is made on a worker
    thread, which must carry the run the fire holds it to."""
    _ceiling("off")

    _fire(_trigger("file:digest", "run-script", {"script": script}))

    assert service.paths == []
    (run,) = _runs("file:digest")
    assert "unreached" in json.dumps(run), run


def test_with_no_sandbox_a_script_action_whose_egress_is_off_is_refused(
    service, script, no_os_sandbox
):
    _ceiling("off")

    _fire(_trigger("file:digest", "run-script", {"script": script}))

    assert service.paths == []
    (run,) = _runs("file:digest")
    assert "This command was not run: egress is off for this run" in run["error"], run
    (row,) = _rows(event_type="command_refused")
    assert row["metadata"]["control"] == "sandbox", row


@needs_the_os_sandbox
def test_a_script_action_on_the_default_tier_still_reaches_it(service, script):
    _fire(_trigger("file:digest", "run-script", {"script": script}))

    assert service.paths == ["/script"]


# ── the agent's shell ────────────────────────────────────────────────────────


def _bash(tmp_path: Path, command: str, *, mode: str = "auto"):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(cwd=tmp_path, sandbox_mode=mode)
    return asyncio.run(tools._t_bash({"command": command}))


@needs_the_os_sandbox
def test_the_agents_shell_in_a_run_whose_egress_is_off_cannot_reach_it(tmp_path, service, reach):
    """🔴 Red on integration: a loop worker's shell reached the stand-in."""
    _ceiling("off")

    with _in_run(LOOP_WORKER):
        result = _bash(tmp_path, reach("shell"))

    assert service.paths == []
    assert not result.success and f"exit {UNREACHED}" in (result.error or ""), result
    assert result.recovery_hints[0].startswith(f"{NO_NETWORK}egress is off"), result


@needs_the_os_sandbox
def test_an_attended_chats_shell_on_the_default_tier_is_unchanged(tmp_path, service, reach):
    with _in_run(CHAT):
        result = _bash(tmp_path, reach("shell"))

    assert service.paths == ["/shell"]
    assert result.success, result
    assert _rows(operation="egress_launch") == []


def test_with_its_sandbox_off_the_agents_shell_whose_egress_is_off_is_refused(
    tmp_path, service, reach
):
    """🔴 Red on integration: with its sandbox off, the shell ran the command with the network."""
    _ceiling("off")

    with _in_run(LOOP_WORKER):
        result = _bash(tmp_path, reach("shell"), mode="off")

    assert service.paths == []
    assert not result.success
    assert "This command was not run: egress is off for this run" in (result.error or ""), result
    assert "its sandbox is set to off" in (result.error or ""), result


# ── a check, a workflow's step and an effect's teardown ──────────────────────


def _check(command: str, cwd: Path) -> tuple[bool | None, str]:
    """A loop's check: whether it passed, and what its verdict shows of what it printed."""
    from personalclaw.loop.gates import CheckReport, run_verify_command

    report = CheckReport()
    passed = asyncio.run(run_verify_command(command, str(cwd), report=report))
    return passed, report.output or report.not_run


def _step(command: str, cwd: Path) -> tuple[bool, str]:
    from personalclaw.workflows.provisioning import run_step

    return asyncio.run(run_step(command, cwd, run_id="run-1"))


def _teardown(command: str, _cwd: Path) -> tuple[bool, str]:
    from personalclaw.workflows.effects import run_teardown

    return asyncio.run(run_teardown(command, "resource-1"))


_UNSANDBOXED = pytest.mark.parametrize(
    "launch", [_check, _step, _teardown], ids=["check", "setup-step", "teardown"]
)


@needs_the_os_sandbox
@_UNSANDBOXED
def test_a_command_a_workflow_or_a_loop_runs_has_no_network_when_egress_is_off(
    launch, tmp_path, service, reach
):
    """🔴 Red on integration: each reached the stand-in, launched with no sandbox at all."""
    _ceiling("off")

    ran, said = launch(reach("work"), tmp_path)

    assert service.paths == []
    assert not ran and said.endswith(f"{NO_NETWORK}egress is off for this run{OFF}."), said
    assert len(_rows(operation="egress_launch", outcome="denied")) == 1


@needs_the_os_sandbox
@_UNSANDBOXED
def test_a_command_a_workflow_or_a_loop_runs_on_the_default_tier_still_reaches_it(
    launch, tmp_path, service, reach
):
    launch(reach("work"), tmp_path)

    assert service.paths == ["/work"]


@_UNSANDBOXED
def test_with_no_sandbox_a_command_a_workflow_or_a_loop_runs_is_refused(
    launch, tmp_path, service, reach, no_os_sandbox
):
    """🔴 Red on integration: with no sandbox, each ran with the network."""
    _ceiling("off")

    launch(reach("work"), tmp_path)

    assert service.paths == []
    (row,) = _rows(event_type="command_refused")
    assert row["metadata"]["control"] == "sandbox", row
    assert "egress is off for this run" in row["resources"], row


def test_a_refused_check_says_why_on_its_report(tmp_path, service, reach, no_os_sandbox):
    _ceiling("off")

    passed, said = _check(reach("work"), tmp_path)

    assert passed is None
    assert said.startswith("egress is off for this run"), said
    assert "so it may run only with no network access" in said, said


# ── what keeps the network ───────────────────────────────────────────────────


@needs_the_os_sandbox
def test_an_agent_clis_own_process_keeps_the_network(tmp_path, service, reach):
    """An agent CLI reaches its model itself, as the built-in agent does, so its process is not a
    command of the run and is not cut off; what it runs without asking is its own (its tools are
    still held where they ask PersonalClaw)."""
    from personalclaw.sandbox_providers import SandboxSpec, resolve_provider

    _ceiling("off")

    async def launch() -> int:
        handle = resolve_provider("none").wrap(SandboxSpec(mode="auto"), shlex.split(reach("cli")))
        try:
            proc = await handle.exec()
            return await proc.wait()
        finally:
            handle.cleanup()

    with egress_held_to(unattended_dispatch_key("trigger:file:agent")):
        code = asyncio.run(launch())

    assert code == 0 and service.paths == ["/cli"]


@needs_the_os_sandbox
def test_a_command_made_for_no_run_keeps_the_network(tmp_path, service, reach):
    """The owner's own action in the app (a hook's Test, Tools → Try it with no run) is no run's."""
    _ceiling("off")

    wrapped, cleanup = sandbox.wrap_argv(shlex.split(reach("own")))
    try:
        assert subprocess.run(wrapped, capture_output=True, timeout=60).returncode == 0
    finally:
        if cleanup:
            os.unlink(cleanup)

    assert service.paths == ["/own"]


# ── the network cut beside the memory fence ──────────────────────────────────


def _launched(wrap, command: str) -> subprocess.CompletedProcess:
    argv, cleanup = wrap(["/bin/sh", "-c", command], "standard")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        if cleanup:
            os.unlink(cleanup)


@needs_the_os_sandbox
def test_a_private_chats_command_with_no_network_writes_no_memory_either(tmp_path, service, reach):
    """One launch carries both of the sandbox's fences: a command of an Incognito chat whose tier
    is off reaches no host, and the memory folders are read-only to it, while a folder outside
    them stays writable. An agent CLI started for that chat keeps the network and the memory fence.

    🔴 Red on integration: the command reached the stand-in."""
    from personalclaw import memory, memory_writes

    _ceiling("off")
    documents, partitions = memory.memory_folders()
    partitions.mkdir(parents=True, exist_ok=True)
    documents.mkdir(parents=True, exist_ok=True)
    note = documents / "preferences.md"
    note.write_text("kept\n", encoding="utf-8")
    elsewhere = tmp_path / "notes.txt"

    def command(path: str) -> str:
        return (
            f"{reach(path)}; echo net=$?; "
            f"printf x >> {shlex.quote(str(note))}; echo memory=$?; "
            f"printf x >> {shlex.quote(str(elsewhere))}; echo elsewhere=$?"
        )

    with _in_run(CHAT), memory_writes.derived_from(CHAT, memory_mode="incognito"):
        shell = _launched(sandbox.wrap_argv, command("shell"))
        assert f"net={UNREACHED}" in shell.stdout and service.paths == [], shell
        cli = _launched(sandbox.wrap_program_argv, command("cli"))

    assert "net=0" in cli.stdout and service.paths == ["/cli"], cli
    for run in (shell, cli):
        assert "memory=" in run.stdout and "memory=0" not in run.stdout, run
        assert "elsewhere=0" in run.stdout, run
    assert note.read_text(encoding="utf-8") == "kept\n"
    assert elsewhere.read_text(encoding="utf-8") == "xx"
    (row,) = _rows(operation="egress_launch", outcome="denied")
    assert row["caller_identity"] == CHAT, row


# ── the Linux launcher, driven with its syscalls stood in for ────────────────

_LAUNCHER = """
import builtins, ctypes, json, os, sys, types

cfg = json.loads(sys.argv[1])
calls = []


class _Unshare:
    def __call__(self, flags):
        calls.append(flags)
        return -1 if flags == cfg["refuse"] else 0


class _Mount:
    def __call__(self, *_args):
        return 0


class StoodIn:
    def __init__(self, *_args, **_kwargs):
        self.unshare = _Unshare()
        self.mount = _Mount()


def _open(path, *args, **kwargs):
    if str(path).startswith("/proc/"):
        return builtins.open(os.devnull, "w")
    return builtins.open(path, *args, **kwargs)


ctypes.CDLL = StoodIn
launcher = types.ModuleType("launcher_under_test")
launcher.open = _open
exec(compile(open(cfg["script"]).read(), cfg["script"], "exec"), launcher.__dict__)
launcher.RUNTIME_DIRS = cfg["runtime_dirs"]
launcher.OWNER_HOME = ""
launcher.PINNED = []
launcher._home_device = lambda: None
real_exec = os.execvp


def _exec(file, args):
    sys.stderr.write("unshare " + " ".join(hex(f) for f in calls) + "\\n")
    sys.stderr.flush()
    real_exec(file, args)


os.execvp = _exec
sys.argv = [cfg["script"], *cfg["argv"]]
launcher.main()
"""

_NEWNET = 0x40000000


def _stood_in_launch(tmp_path: Path, *, network: bool, refuse: int = 0):
    script = tmp_path / "launcher.py"
    script.write_text(sandbox._build_launcher_script("standard", network=network), "utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    marker = tmp_path / "ran"
    cfg = {
        "script": str(script),
        "runtime_dirs": [str(runtime)],
        "argv": ["/bin/sh", "-c", f'echo ran > "{marker}"'],
        "refuse": refuse,
    }
    run = subprocess.run(
        [sys.executable, "-c", _LAUNCHER, json.dumps(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return run, marker.exists()


def test_the_linux_launcher_gives_a_command_with_no_network_a_network_namespace(tmp_path):
    run, ran = _stood_in_launch(tmp_path, network=False)

    assert run.returncode == 0 and ran, run.stderr
    assert hex(_NEWNET) in run.stderr.split("unshare ", 1)[1], run.stderr


def test_the_linux_launcher_runs_nothing_when_it_cannot_take_the_network_away(tmp_path):
    run, ran = _stood_in_launch(tmp_path, network=False, refuse=_NEWNET)

    assert run.returncode != 0 and not ran, run.stderr
    assert "unshare(NEWNET) failed" in run.stderr, run.stderr


def test_the_linux_launcher_for_a_command_that_keeps_the_network_makes_no_namespace(tmp_path):
    run, ran = _stood_in_launch(tmp_path, network=True)

    assert run.returncode == 0 and ran, run.stderr
    assert hex(_NEWNET) not in run.stderr, run.stderr


# ── the browser's page loads and its reader ──────────────────────────────────


class _PageSocket:
    """A page target's debugger socket, standing in for the browser: every command is answered,
    and an event can be sent the way the browser sends one."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, payload: str) -> None:
        message = json.loads(payload)
        self.sent.append(message["method"])
        await self._inbox.put(json.dumps({"id": message["id"], "result": {}}))

    def event(self, method: str, params: dict) -> None:
        self._inbox.put_nowait(json.dumps({"method": method, "params": params}))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        raw = await self._inbox.get()
        if raw is None:
            raise StopAsyncIteration
        return raw

    async def close(self) -> None:
        self._inbox.put_nowait(None)


def _browse(*, run: str) -> tuple[list[str], list]:
    """A browse session opened for *run*, as the browse action opens one inside its dispatch: one
    navigation it makes, then a redirect the page takes on its own, which only the reader sees."""
    from personalclaw.browse.cdp import GatedCdpSession
    from personalclaw.browse.transport import WebSocketCdpTransport

    async def scenario() -> tuple[list[str], list]:
        socket = _PageSocket()
        with egress_held_to(run):
            transport = WebSocketCdpTransport(socket)
            transport.start_reading()
            session = GatedCdpSession(transport, resolver=lambda _host: ["93.184.216.34"])
            await session.start()
            await session.navigate("https://www.example.com/")
        socket.event("Page.frameNavigated", {"frame": {"url": "https://docs.example.com/next"}})
        for _ in range(100):
            if "Page.stopLoading" in socket.sent or len(session.blocks) > 1:
                break
            await asyncio.sleep(0.01)
        await transport.close()
        return socket.sent, session.blocks

    return asyncio.run(scenario())


def test_the_browsers_reader_judges_a_redirect_for_the_run_that_opened_it():
    """The browse action connects its page inside its dispatch, so the reader and the dispatcher
    the transport starts carry the run: a redirect the page takes after the run's own navigation
    is refused by the run's tier and the page is torn down, though no call of the run is waiting."""
    _ceiling("off")

    sent, blocks = _browse(run=unattended_dispatch_key("trigger:clock:browse"))

    assert "Page.navigate" not in sent[: sent.index("Page.stopLoading")], sent
    assert [b.reason for b in blocks] == [
        "egress is off for this run (safety profile egress tier 'off')"
    ] * 2, blocks


def test_the_browsers_reader_on_the_default_tier_lets_the_redirect_through():
    sent, blocks = _browse(run=unattended_dispatch_key("trigger:clock:browse"))

    assert blocks == [] and "Page.stopLoading" not in sent, (sent, blocks)


# ── every command a run starts is launched that way ──────────────────────────

#: The sandbox's launch of a command a run starts, which reads the run's tier: every site that runs
#: a command someone wrote calls one of these before it spawns.
_THE_LAUNCH = ("wrap_argv", "egress_bound_argv")

#: A site that runs a command someone wrote, but never for a run, and why.
_FOR_NO_RUN = {
    "apps/app_manager.py::_run_hook::subprocess.run": (
        "an app's lifecycle hook, run when the owner installs, updates, enables, disables or "
        "uninstalls the app"
    ),
}

#: A command a run starts that the denylist rail files under what its script's tool calls reach.
_ALSO_A_RUNS_COMMAND = {
    "schedule_script.py::run_script_sandboxed::subprocess.run": "a script action's script",
}


def _launch_lines(node, functions: dict) -> list[int]:
    """The lines where *node* asks the sandbox to launch, itself or through a function of its own
    module that does so directly (one hop)."""
    import ast

    def named(call: ast.Call) -> str:
        f = call.func
        return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""

    def launches(fn) -> bool:
        return any(isinstance(c, ast.Call) and named(c) in _THE_LAUNCH for c in ast.walk(fn))

    lines = []
    for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
        helper = functions.get(named(call))
        if named(call) in _THE_LAUNCH or (
            helper is not None and helper is not node and launches(helper)
        ):
            lines.append(call.lineno)
    return lines


def test_every_command_a_run_starts_is_launched_by_the_sandbox():
    """🔴 Red on integration: a loop's and a workflow's check, a workflow's setup and teardown steps
    and an effect's teardown ran with no sandbox at all, so no tier could take their network."""
    from test_every_command_path_asks_the_denylist import _ASKS, _functions, _parsed, _spawn_lines

    assert set(_FOR_NO_RUN) <= set(_ASKS), "a site this rail exempts is no longer a command site"
    for key in sorted((set(_ASKS) | set(_ALSO_A_RUNS_COMMAND)) - set(_FOR_NO_RUN)):
        rel, qualname, callee = key.split("::")
        functions = _functions(_parsed(rel))
        node = functions[qualname]
        launched = _launch_lines(node, functions)
        spawned = _spawn_lines(node, callee)
        assert launched, f"{key} runs a command a run starts without the sandbox's launch"
        assert min(launched) < min(spawned), f"{key} spawns before the sandbox launches it"
