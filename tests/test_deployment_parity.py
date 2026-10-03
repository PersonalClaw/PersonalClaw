"""Deployment parity tests — verifies the required endpoints respond
(without error) under both the service path (subprocess) and the Compose
path (docker/finch).

Both runtimes are skipped cleanly when the relevant runtime is absent:
- Service path: skipped when `personalclaw` is not on PATH. A gateway that exits
  before it answers FAILS, with what it printed: there is then nothing to skip for.
- Compose path: skipped when neither `docker` nor `finch` is on PATH, when the run
  does not set `PERSONALCLAW_TEST_CONTAINER_RUNTIME=1` (it builds two images and
  starts the stack on this machine's own runtime: `tests/container_runtime.py`), or
  when the Compose stack cannot be built/started

The Compose path auto-detects the container runtime (docker preferred, then
finch); there is no command-line selector.
"""

import ast
import contextlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import container_runtime
import pytest
import yaml

from personalclaw.security import mask_child_output
from tools.docker_single_container_smoke import redact_secrets

_REPO = Path(__file__).resolve().parents[1]

# Endpoints that must respond identically on both runtimes
_REQUIRED_ENDPOINTS = [
    "/api/system",
    "/api/auth-status",
    "/api/providers",
    "/api/use-cases",
    "/api/credentials",
    "/api/sessions",
    "/api/agents",
]

_STARTUP_TIMEOUT = 30  # seconds

#: How the service path starts the gateway, after `personalclaw` and before its `--port`.
#: `test_every_gateway_command_the_deployments_run_is_one_the_cli_accepts` holds it to the CLI's
#: own parser: it once passed a flag the CLI does not have, so the gateway exited at once and
#: the service path measured nothing.
_SERVICE_ARGS = ("gateway", "--no-open")

#: The flags every test runs under (`conftest`), given to the gateway child too: a module
#: fixture is set up before the function-scoped fixtures that set them, and a child process
#: has its own environment anyway.
_SUITE_FLAGS = {
    "PERSONALCLAW_DISABLE_LIVE_WRITES": "1",
    "PERSONALCLAW_ACP_NO_PROVISION": "1",
    "PERSONALCLAW_SKIP_APP_BACKENDS": "1",
    "PERSONALCLAW_SKIP_APP_WORKERS": "1",
}
_SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

# ── The Compose fixture's share of one item's pytest-timeout ──────────────────
# pytest-timeout charges `--timeout` PER TEST ITEM, and a module-scoped fixture's
# setup is charged to the first item that requests it. So the image build, the
# readiness poll, AND the teardown that runs on the way back out all have to fit
# inside ONE item's budget — otherwise pytest-timeout kills setup part-way through
# and the clean skip this module promises is simply unreachable.
#
# The build gets whatever is left over, derived from the live `--timeout` rather
# than hardcoded beside it so the two cannot drift apart again:
#
#     build = --timeout - _STARTUP_TIMEOUT - _COMPOSE_DOWN_TIMEOUT - _TIMEOUT_MARGIN
#
# At this repo's `--timeout=120` that is 120 - 30 - 15 - 20 = 55s, and every way
# setup can end lands inside 120s:
#
#     build blows its budget   55 + 15 (down)              =  70s
#     built, never came up     55 + 30 (poll) + 15 (down)  = 100s
#     healthy                  55 + 30 (poll) + the test itself
#
# leaving >= 20s for interpreter start-up, the container CLI's own latency and
# raising the skip. A cold from-scratch build (npm+vite, then pip with the heavy
# extras) does NOT fit in 55s and is not meant to: a host that cannot build the
# image inside one test's budget is exactly the "Compose stack cannot be
# built/started" case this module skips.
_COMPOSE_DOWN_TIMEOUT = 15  # `compose down` of a partial or a running stack
_TIMEOUT_MARGIN = 20  # reserved so pytest-timeout is never the thing that fires
_DEFAULT_GLOBAL_TIMEOUT = 120.0  # pyproject's addopts value, if --timeout is unset


def _compose_build_timeout(config: pytest.Config) -> int:
    """Seconds the image build may take without putting the clean skip out of reach."""
    global_timeout = config.getoption("timeout", None) or _DEFAULT_GLOBAL_TIMEOUT
    return int(global_timeout) - _STARTUP_TIMEOUT - _COMPOSE_DOWN_TIMEOUT - _TIMEOUT_MARGIN


def _wait_for_gateway(
    base_url: str,
    timeout: float = _STARTUP_TIMEOUT,
    proc: "subprocess.Popen[bytes] | None" = None,
) -> bool:
    """Poll /api/system until it responds or timeout expires — or, given the gateway's own
    *proc*, until that process exits, since nothing will answer then."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            req = urllib.request.Request(f"{base_url}/api/system")
            with urllib.request.urlopen(req, timeout=2):
                return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return True  # Gateway is up, just requires auth
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _fetch(url: str) -> dict:
    """GET *url* and return parsed JSON. Returns {"_error": ...} on failure."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"_auth_required": True, "status": e.code}
        return {"_error": f"HTTP {e.code}"}
    except Exception as exc:
        return {"_error": str(exc)}


# ── Service path fixture ──────────────────────────────────────────────────────


def _free_port() -> int:
    """A loopback port nothing listens on. Binding it is what makes it this test's own to the
    suite's port guard (`tests/port_guard.py`), so polling the gateway it is handed to is a
    connection to a server this test started, not one the guard refuses."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _service_env(scratch: Path, personalclaw: str) -> dict[str, str]:
    """The gateway child's whole environment, built rather than inherited.

    No guard this suite installs sees what a child does: they wrap this process's own sockets
    and files, and the programs it starts, not what those programs do. So the child gets nothing
    of the machine it runs on to act on: a home of its own (the agent histories and agent CLIs a
    gateway can find live under the real one), a PATH of the folder holding `personalclaw` and
    the system's own, and a PersonalClaw home whose automatic update check is off, since in a
    source checkout that check fetches the checkout's remote."""
    home = scratch / "home"
    personalclaw_home = scratch / "personalclaw-home"
    home.mkdir()
    personalclaw_home.mkdir()
    (personalclaw_home / "config.json").write_text(
        json.dumps({"updates": {"check_enabled": False}}), encoding="utf-8"
    )
    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join([str(Path(personalclaw).parent), *_SYSTEM_PATH]),
        "PERSONALCLAW_HOME": str(personalclaw_home),
        **_SUITE_FLAGS,
    }
    for name in ("TMPDIR", "LANG", "LC_ALL"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def _exit_output(log: Path) -> str:
    """What the gateway printed before it exited, fit for a public CI log: the dashboard URL's
    session token redacted, and every other secret masked as the product masks a child's."""
    text = log.read_text(encoding="utf-8", errors="replace")
    return mask_child_output(redact_secrets(text), limit=4000, tail=True, one_line=False)


@contextlib.contextmanager
def _service_gateway_at(personalclaw: str) -> Iterator[str]:
    """*personalclaw* serving a scratch home on a port of this test's own: its URL once it
    answers. A gateway that exits first fails here at once, with what it printed; one still
    starting after the startup budget is skipped, as a host too slow to measure on."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        output = scratch / "gateway.log"
        with output.open("wb") as log:
            proc = subprocess.Popen(
                [personalclaw, *_SERVICE_ARGS, "--port", str(port)],
                env=_service_env(scratch, personalclaw),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        try:
            if not _wait_for_gateway(base_url, proc=proc):
                if proc.poll() is not None:
                    pytest.fail(
                        f"`personalclaw {' '.join(_SERVICE_ARGS)}` exited ({proc.returncode}) "
                        f"before it answered, so the service path measured nothing. It printed:"
                        f"\n{_exit_output(output)}",
                        pytrace=False,
                    )
                pytest.skip(f"Gateway did not start within {_STARTUP_TIMEOUT}s")
            yield base_url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


@pytest.fixture(scope="module")
def service_gateway():
    """Start the gateway via `personalclaw gateway` subprocess."""
    personalclaw = shutil.which("personalclaw")
    if not personalclaw:
        pytest.skip("personalclaw not on PATH — service path not available")
    with _service_gateway_at(personalclaw) as base_url:
        yield base_url


# ── Compose path fixture ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def compose_gateway(request: pytest.FixtureRequest):
    """Start the gateway via `docker/finch compose up` using the build overlay."""
    runtime = container_runtime.require("docker", "finch")

    repo_root = Path(__file__).resolve().parent.parent
    compose_dir = repo_root / "deploy" / "compose"
    compose_file = compose_dir / "compose.yaml"
    build_overlay = compose_dir / "compose.build.yaml"
    if not compose_file.exists():
        pytest.skip("deploy/compose/compose.yaml not found")

    # See the budget note above: a build share this small cannot be attempted at all
    # without pytest-timeout, not us, deciding how setup ends.
    build_timeout = _compose_build_timeout(request.config)
    if build_timeout <= 0:
        pytest.skip(f"--timeout leaves {build_timeout}s for the image build — too small to attempt")

    # The stack reads repo-root ../../.env via each service's env_file. Seed it
    # from .env.example only when absent, so a developer's real .env is never
    # clobbered by the test run.
    root_env = repo_root / ".env"
    env_example = repo_root / ".env.example"
    seeded_env = False
    if not root_env.exists() and env_example.exists():
        root_env.write_text(env_example.read_text())
        seeded_env = True

    # The stack binds the gateway on a fixed 127.0.0.1:10000.
    base_url = "http://127.0.0.1:10000"
    compose_args = [runtime, "compose", "-f", str(compose_file), "-f", str(build_overlay)]

    # ONE cleanup path for every way out — build error, build over budget, gateway
    # never came up, or a clean run. It used to be copy-pasted into each of those
    # branches, and the copy in the over-budget branch never ran: the budget was
    # 90s build + 60s teardown against a 120s per-item timeout, so pytest-timeout
    # killed setup mid-teardown, the module reported 7 setup ERRORs instead of the
    # promised skip, and the seeded .env was left behind on disk. A `finally` is
    # what makes that unrepeatable — it does not depend on the arithmetic above
    # being right.
    try:
        try:
            subprocess.run(
                compose_args + ["up", "-d", "--build"],
                check=True,
                capture_output=True,
                timeout=build_timeout,
            )
        except subprocess.CalledProcessError as exc:
            pytest.skip(f"compose up failed: {exc.stderr.decode()[:200]}")
        except subprocess.TimeoutExpired:
            pytest.skip(f"compose up exceeded {build_timeout}s to build — skipping Compose path")

        if not _wait_for_gateway(base_url, timeout=_STARTUP_TIMEOUT):
            pytest.skip(f"Compose gateway did not start within {_STARTUP_TIMEOUT}s")
        yield base_url
    finally:
        try:
            subprocess.run(
                compose_args + ["down"], capture_output=True, timeout=_COMPOSE_DOWN_TIMEOUT
            )
        except (subprocess.SubprocessError, OSError) as exc:
            # Teardown is best-effort BY DESIGN: a hung or broken container CLI must
            # not convert this module's clean skip into an ERROR, and must not be able
            # to skip the .env removal below.
            print(f"compose down did not complete cleanly: {exc!r}")
        if seeded_env:
            root_env.unlink(missing_ok=True)


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("endpoint", _REQUIRED_ENDPOINTS)
def test_service_path_endpoint_responds(service_gateway, endpoint):
    """Every required endpoint responds on the service path."""
    data = _fetch(f"{service_gateway}{endpoint}")
    assert "_error" not in data or data.get(
        "_auth_required"
    ), f"Endpoint {endpoint} returned error on service path: {data}"


@pytest.mark.parametrize("endpoint", _REQUIRED_ENDPOINTS)
def test_compose_path_endpoint_responds(compose_gateway, endpoint):
    """Every required endpoint responds on the Compose path."""
    data = _fetch(f"{compose_gateway}{endpoint}")
    assert "_error" not in data or data.get(
        "_auth_required"
    ), f"Endpoint {endpoint} returned error on Compose path: {data}"


# ── The Compose fixture's own contract ────────────────────────────────────────
# The tests above need a container runtime, so on a host without one they skip and
# say nothing about the fixture itself. These two need nothing: they pin the budget
# and the cleanup shape that made `compose_gateway`'s advertised clean skip
# unreachable, on every host and in CI.


def _compose_fixture_ast() -> ast.FunctionDef:
    """The `compose_gateway` fixture as source, so its SHAPE can be asserted."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "compose_gateway"
    )


def _cleanup_actions(node: ast.AST) -> list[ast.AST]:
    """Both of the fixture's cleanup actions: `compose down`, and removing our .env."""
    return [
        n
        for n in ast.walk(node)
        if (isinstance(n, ast.Constant) and n.value == "down")
        or (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "unlink"
        )
    ]


def test_the_whole_fixture_fits_inside_one_items_pytest_timeout(
    request: pytest.FixtureRequest,
) -> None:
    """The arithmetic that put the fixture's own clean skip out of reach.

    It budgeted a 90s build and then a 60s `compose down` on the way out — 150s of
    setup against a 120s per-item `--timeout`. pytest-timeout therefore killed setup
    part-way through the teardown, so `pytest.skip()` was never reached and the
    module reported 7 setup ERRORs instead. The old comment claimed the opposite was
    guaranteed; this asserts it instead of claiming it.
    """
    global_timeout = request.config.getoption("timeout", None) or _DEFAULT_GLOBAL_TIMEOUT
    build = _compose_build_timeout(request.config)
    assert build > 0, f"no budget left for the image build (--timeout={global_timeout})"

    slowest_setup = build + _STARTUP_TIMEOUT + _COMPOSE_DOWN_TIMEOUT
    assert slowest_setup + _TIMEOUT_MARGIN <= global_timeout, (
        f"the fixture's slowest setup path is {build}s build + {_STARTUP_TIMEOUT}s poll "
        f"+ {_COMPOSE_DOWN_TIMEOUT}s down = {slowest_setup}s, which leaves less than "
        f"{_TIMEOUT_MARGIN}s of the {global_timeout}s per-item budget — pytest-timeout "
        "will fire before the fixture can skip cleanly"
    )


def test_every_cleanup_action_lives_in_a_finally() -> None:
    """A budget mistake must not be able to leak a seeded .env again.

    The cleanup used to be copy-pasted into three branches. The copy in the
    over-budget branch never ran, and the proof was physical: a `.env` seeded from
    `.env.example` was left behind in the repo root (gitignored, so the tree still
    read clean). Keeping both cleanup actions in a `finally` — and NOWHERE else —
    is what makes that independent of the arithmetic being right.
    """
    fixture = _compose_fixture_ast()
    everywhere = _cleanup_actions(fixture)
    assert everywhere, "the fixture no longer tears the stack down or removes its .env"

    in_finally = [
        action
        for node in ast.walk(fixture)
        if isinstance(node, ast.Try)
        for stmt in node.finalbody
        for action in _cleanup_actions(stmt)
    ]
    assert len(in_finally) == len(everywhere), (
        f"{len(everywhere) - len(in_finally)} of {len(everywhere)} cleanup actions sit "
        "outside a `finally`, so a path that exits early skips them"
    )


# ── Every command a deployment runs is one the CLI has ────────────────────────
# A flag the CLI does not have makes `personalclaw` print its usage and exit 2 before it
# starts anything, which reads as a gateway that never came up. The service path above
# passed one (`--no-browser`) and so never measured anything, and the dev Compose overlay
# ran the same command, so its gateway exited on every `up`. Neither needs a runtime to see.


def _deployment_commands() -> list[tuple[str, list[str]]]:
    """Each `personalclaw` command a shipped Compose file or image runs, with where it is."""
    found = []
    for compose in sorted((_REPO / "deploy" / "compose").glob("compose*.yaml")):
        services = (yaml.safe_load(compose.read_text(encoding="utf-8")) or {}).get("services")
        for name, service in (services or {}).items():
            for key in ("entrypoint", "command"):
                argv = (service or {}).get(key)
                argv = shlex.split(argv) if isinstance(argv, str) else argv
                if argv and argv[0] == "personalclaw":
                    found.append((f"{compose.name}: {name}.{key}", [str(a) for a in argv]))
    for dockerfile in sorted((_REPO / "deploy" / "docker").glob("Dockerfile*")):
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            exec_form = re.match(r"\s*(CMD|ENTRYPOINT)\s+(\[.*\])\s*$", line)
            if exec_form and (argv := json.loads(exec_form.group(2))) and argv[0] == "personalclaw":
                found.append((f"{dockerfile.name}: {exec_form.group(1)}", argv))
    return found


def test_every_gateway_command_the_deployments_run_is_one_the_cli_accepts() -> None:
    from personalclaw import cli

    commands = [
        ("the service path above", ["personalclaw", *_SERVICE_ARGS, "--port", "10000"]),
        *_deployment_commands(),
    ]
    # The floor: the image's own command is found, so a reader that finds nothing cannot pass.
    assert any(where == "Dockerfile.backend: CMD" for where, _ in commands), commands
    refused = []
    for where, argv in commands:
        try:
            cli.build_parser().parse_args(argv[1:])
        except SystemExit:
            refused.append(f"{where}: {shlex.join(argv)}")
    assert not refused, "commands the CLI refuses, so they start nothing:\n" + "\n".join(refused)


def test_a_gateway_that_exits_fails_the_service_path_at_once_with_what_it_printed(
    tmp_path: Path,
) -> None:
    """The service fixture's own contract, with a stand-in for `personalclaw` that exits 2 the
    way a refused flag does. It used to poll a fixed port for the whole startup budget, which
    the suite's port guard refused because the test never held that port, then skip: the
    refusals failed whichever test ran next on the worker, and the exit itself was never seen.
    Now the poll is to a port the test holds, and the exit fails the fixture at once with what
    the gateway printed, its session token redacted."""
    stand_in = tmp_path / "personalclaw"
    stand_in.write_text(
        "#!/bin/sh\n"
        "echo 'Dashboard: http://127.0.0.1:1?token=synthetic.placeholder.not-a-real-token'\n"
        "echo 'personalclaw: error: unrecognized arguments: --not-a-flag' >&2\n"
        "exit 2\n",
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception) as failed:
        with _service_gateway_at(str(stand_in)):
            pass
    assert time.monotonic() - started < _STARTUP_TIMEOUT / 3, "it waited out the startup budget"
    message = str(failed.value)
    assert "exited (2) before it answered" in message, message
    assert "unrecognized arguments: --not-a-flag" in message, message
    assert "synthetic.placeholder.not-a-real-token" not in message, message
    assert "token=<redacted>" in message, message
