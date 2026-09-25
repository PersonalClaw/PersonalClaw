"""Deployment parity tests — verifies the required endpoints respond
(without error) under both the service path (subprocess) and the Compose
path (docker/finch).

Both runtimes are skipped cleanly when the relevant runtime is absent:
- Service path: skipped when `personalclaw` is not on PATH
- Compose path: skipped when neither `docker` nor `finch` is on PATH, or when
  the Compose stack cannot be built/started

The Compose path auto-detects the container runtime (docker preferred, then
finch); there is no command-line selector.
"""

import ast
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

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

_PORT = 17777  # test port (avoid colliding with production 10000)
_BASE_URL = f"http://127.0.0.1:{_PORT}"
_STARTUP_TIMEOUT = 30  # seconds

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


def _wait_for_gateway(base_url: str, timeout: float = _STARTUP_TIMEOUT) -> bool:
    """Poll /api/system until it responds or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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


@pytest.fixture(scope="module")
def service_gateway():
    """Start the gateway via `personalclaw gateway` subprocess."""
    personalclaw = shutil.which("personalclaw")
    if not personalclaw:
        pytest.skip("personalclaw not on PATH — service path not available")

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "PERSONALCLAW_HOME": tmp, "PERSONALCLAW_PORT": str(_PORT)}
        proc = subprocess.Popen(
            [personalclaw, "gateway", "--no-browser"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            if not _wait_for_gateway(_BASE_URL):
                proc.terminate()
                proc.wait(timeout=5)
                pytest.skip(f"Gateway did not start within {_STARTUP_TIMEOUT}s")
            yield _BASE_URL
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


# ── Compose path fixture ──────────────────────────────────────────────────────


def _container_runtime() -> str | None:
    for rt in ("docker", "finch"):
        if shutil.which(rt):
            return rt
    return None


@pytest.fixture(scope="module")
def compose_gateway(request: pytest.FixtureRequest):
    """Start the gateway via `docker/finch compose up` using the build overlay."""
    runtime = _container_runtime()
    if not runtime:
        pytest.skip("Neither docker nor finch on PATH — Compose path not available")

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
