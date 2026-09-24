"""Tests for the ``docker`` sandbox provider (EXECUTION-ISOLATION EI-2).

Two layers:

* **Pure-unit** (run everywhere, no Docker): command construction, registry wiring, the typed
  no-Docker refusal, and the write-boundary / model-grant / port / env argv properties. These are
  the SC1/SC2 command-construction guarantees, asserted without a daemon.
* **Integration** (skipped unless the docker CLI + daemon are present): a real container proving
  UID alignment and that a host path outside the mounted workspace is not visible.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from personalclaw.sandbox import ResourceCeilings
from personalclaw.sandbox_providers import (
    SandboxUnavailableError,
    list_providers,
    resolve_provider,
)
from personalclaw.sandbox_providers.base import SandboxSpec
from personalclaw.sandbox_providers.docker import (
    DOCKER_PROVIDER_NAME,
    DockerSandboxProvider,
    build_docker_argv,
    docker_available,
    sandbox_image,
)

_MODELS_DIR = os.path.expanduser("~/.personalclaw/models")


def _argv(spec: SandboxSpec, inner=("echo", "hi"), *, workspace="/ws", ceilings=None):
    return build_docker_argv(
        list(inner),
        workspace_dir=workspace,
        ceilings=ceilings or ResourceCeilings(nofile=1024, max_pids=0, max_rss_mb=0),
        spec=spec,
        container_name="pclaw-sbx-test",
    )


# ── registry wiring ───────────────────────────────────────────────────────────


def test_docker_is_registered_as_a_builtin():
    """``docker`` self-registers at boot beside ``none`` and resolves by name."""
    assert DOCKER_PROVIDER_NAME in list_providers()
    p = resolve_provider("docker")
    assert p.name == "docker"
    assert isinstance(p, DockerSandboxProvider)


def test_unknown_name_still_fails_open_to_none():
    """EI-1's fail-open is unchanged: only an UNKNOWN name drops to ``none`` — ``docker`` does
    not, so an explicit docker request cannot silently become a host run."""
    assert resolve_provider("does-not-exist").name == "none"


# ── command construction (SC1/SC2) ──────────────────────────────────────────────


def test_argv_uid_aligned_bind_mount_over_workspace():
    argv = _argv(SandboxSpec(), workspace="/work/tree")
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--init" in argv
    uid, gid = os.getuid(), os.getgid()
    assert argv[argv.index("--user") + 1] == f"{uid}:{gid}"
    # Same-path bind mount + workdir over the worktree.
    joined = " ".join(argv)
    assert "/work/tree:/work/tree" in joined
    assert argv[argv.index("--workdir") + 1] == "/work/tree"
    # The image precedes the inner command, which comes last verbatim.
    assert sandbox_image() in argv
    assert argv[-2:] == ["echo", "hi"]


def test_argv_maps_ceilings_to_native_docker_limits():
    ceilings = ResourceCeilings(nofile=1024, max_pids=128, max_rss_mb=512)
    argv = _argv(SandboxSpec(), ceilings=ceilings)
    assert argv[argv.index("--pids-limit") + 1] == "128"
    assert argv[argv.index("--memory") + 1] == "512m"


def test_argv_no_ceiling_flags_when_unset():
    argv = _argv(SandboxSpec(), ceilings=ResourceCeilings(nofile=1024, max_pids=0, max_rss_mb=0))
    assert "--pids-limit" not in argv
    assert "--memory" not in argv


def test_egress_off_isolates_network_and_all_does_not():
    off = _argv(SandboxSpec(egress_tier="off"))
    assert off[off.index("--network") + 1] == "none"
    assert "--network" not in _argv(SandboxSpec(egress_tier="all"))


def test_model_dir_not_mounted_unless_granted():
    """SC2: an ungranted docker sandbox cannot see — let alone delete — the local model dir."""
    ungranted = _argv(SandboxSpec(), workspace="/ws")
    assert _MODELS_DIR not in " ".join(ungranted)
    granted = _argv(SandboxSpec(grant_paths=(_MODELS_DIR,)), workspace="/ws")
    assert f"{_MODELS_DIR}:{_MODELS_DIR}" in " ".join(granted)


def test_allowed_write_paths_are_mounted_and_others_are_not():
    """SC1: the workspace + allowed_write_paths are the only writable host paths; a path not in
    that set is never mounted, so a write to it fails at the boundary."""
    spec = SandboxSpec(allowed_write_paths=("/data/out",))
    joined = " ".join(_argv(spec, workspace="/ws"))
    assert "/ws:/ws" in joined
    assert "/data/out:/data/out" in joined
    assert "/etc/passwd" not in joined  # nothing outside the declared set is mounted


def test_expose_ports_and_env_are_threaded():
    spec = SandboxSpec(expose_ports=(8080,), env={"FOO": "bar"})
    argv = _argv(spec)
    assert "-p" in argv and "8080:8080" in argv
    assert "--env" in argv and "FOO=bar" in argv


def test_host_env_is_never_copied_in():
    """The container env is spec.env only — a secret in the host process must not appear."""
    os.environ["PCLAW_TEST_SECRET_XYZ"] = "leaked"
    try:
        argv = _argv(SandboxSpec(env={"SAFE": "1"}))
        assert "leaked" not in " ".join(argv)
        assert "PCLAW_TEST_SECRET_XYZ" not in " ".join(argv)
    finally:
        os.environ.pop("PCLAW_TEST_SECRET_XYZ", None)


def test_image_env_override(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_SANDBOX_DOCKER_IMAGE", "ghcr.io/acme/sbx:1")
    assert sandbox_image() == "ghcr.io/acme/sbx:1"


# ── failure honesty (SC1: no silent host downgrade) ─────────────────────────────


def test_wrap_refuses_with_typed_error_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.sandbox_providers.docker.docker_available", lambda *a, **k: False
    )
    provider = DockerSandboxProvider()
    assert provider.available() is False
    with pytest.raises(SandboxUnavailableError) as ei:
        provider.wrap(SandboxSpec(), ["echo", "hi"])
    # WHAT/WHY/FIX shape so a consumer can surface it verbatim (unattended parks needs-input).
    err = ei.value
    assert err.what and err.why and err.fix
    assert "Docker" in str(err) and "Fix:" in str(err)


# ── integration (real daemon) ───────────────────────────────────────────────────

_HAS_DOCKER = shutil.which("docker") is not None and docker_available(refresh=True)
_docker_only = pytest.mark.skipif(not _HAS_DOCKER, reason="docker CLI/daemon unavailable")


@pytest.fixture
def _sandbox_image_pulled():
    """Pre-pull the sandbox base image so a cold-cache runner does not emit ``docker pull``
    progress into the container's merged stdout/stderr and corrupt the strict output assertions.

    The integration tests below merge stderr into stdout (``stderr=STDOUT``) so a container
    failure shows up in the assertion message. On a runner without the image cached, ``docker
    run`` first prints "Unable to find image ... locally" + pull progress to stderr, which the
    merge would fold into the captured output ahead of the real command result. Pulling first is
    a no-op once the image is local. Only requested by ``@_docker_only`` tests, so docker is
    present when this runs.
    """
    subprocess.run(
        ["docker", "pull", sandbox_image()],
        capture_output=True,
        timeout=300,
        check=False,
    )


@_docker_only
@pytest.mark.usefixtures("_sandbox_image_pulled")
@pytest.mark.asyncio
async def test_container_runs_uid_aligned(tmp_path):
    """A real container reports the host uid — proving ``--user`` UID alignment end to end."""
    provider = DockerSandboxProvider()
    handle = provider.wrap(SandboxSpec(), ["id", "-u"])
    proc = await handle.exec(
        cwd=str(tmp_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    handle.cleanup()
    assert proc.returncode == 0, out.decode()
    assert out.decode().strip() == str(os.getuid())


@_docker_only
@pytest.mark.usefixtures("_sandbox_image_pulled")
@pytest.mark.asyncio
async def test_host_path_outside_workspace_is_not_visible(tmp_path):
    """SC1 boundary: a host file outside the mounted workspace cannot be read in the container."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top-secret")
    ws = tmp_path / "ws"
    ws.mkdir()

    provider = DockerSandboxProvider()
    handle = provider.wrap(
        SandboxSpec(workspace_dir=str(ws)),
        ["sh", "-c", f"cat {secret} 2>/dev/null || echo BLOCKED"],
    )
    proc = await handle.exec(
        cwd=str(ws),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    handle.cleanup()
    assert "top-secret" not in out.decode()
    assert "BLOCKED" in out.decode()
