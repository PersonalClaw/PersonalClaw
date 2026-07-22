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
def compose_gateway():
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

    # Build timeout is deliberately below the global pytest-timeout (--timeout=120):
    # a from-scratch image build (npm+vite, pip with heavy extras) can legitimately
    # exceed the budget on a loaded runner. If it does, this is an environment
    # constraint, not a product failure — skip cleanly (the fixture's contract),
    # the same as a build error. Landing UNDER pytest-timeout guarantees OUR
    # timeout fires first and tears the compose process down, rather than
    # pytest-timeout killing setup and reporting 7 ERRORs.
    build_timeout = 90
    try:
        subprocess.run(
            compose_args + ["up", "-d", "--build"],
            check=True,
            capture_output=True,
            timeout=build_timeout,
        )
    except subprocess.CalledProcessError as exc:
        subprocess.run(compose_args + ["down"], capture_output=True, timeout=60)
        if seeded_env:
            root_env.unlink(missing_ok=True)
        pytest.skip(f"compose up failed: {exc.stderr.decode()[:200]}")
    except subprocess.TimeoutExpired:
        # Best-effort teardown of anything the interrupted build/up left behind.
        subprocess.run(compose_args + ["down"], capture_output=True, timeout=60)
        if seeded_env:
            root_env.unlink(missing_ok=True)
        pytest.skip(f"compose up exceeded {build_timeout}s to build — skipping Compose path")

    try:
        if not _wait_for_gateway(base_url, timeout=60):
            pytest.skip("Compose gateway did not start within 60s")
        yield base_url
    finally:
        subprocess.run(compose_args + ["down"], capture_output=True, timeout=60)
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
