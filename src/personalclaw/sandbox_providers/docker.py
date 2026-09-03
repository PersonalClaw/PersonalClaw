"""The ``docker`` sandbox provider (EXECUTION-ISOLATION EI-2).

The first *confinement* tier: where the ``none`` builtin (EI-1) only hides credential paths and
applies resource ceilings on the host, ``docker`` runs an agent-influenced process tree inside a
bind-mount container over its WORK-R3 worktree, so the isolation is a real filesystem +
process + (optional) network boundary rather than a seatbelt profile.

It is a ``bind_mount``-kind tier (sandcastle's taxonomy): the host owns the workspace and the
provider mounts it in — no copy-in/out sync. UID alignment uses ``--user <uid>:<gid>`` so files
written to the mount are owned by the host user with **no runtime ``chown -R``** (the permission
hazard the plan calls out); on Linux the mount carries an SELinux ``:z`` relabel. Resource
ceilings map to docker's native knobs — ``max_pids`` → ``--pids-limit`` (a fork bomb dies at
the limit, enforced by the container runtime on Linux and inside Docker Desktop's Linux VM on
macOS), ``max_rss_mb`` → ``--memory``. The workspace is the only writable host path unless
``allowed_write_paths``/``grant_paths`` add more, so a write outside the boundary fails because
the path was never mounted — and a model dir that was not granted cannot be deleted.

**Failure honesty (§1.1).** ``available()`` probes the docker CLI + daemon (cached). When Docker
is absent, :meth:`DockerSandboxProvider.wrap` raises :class:`SandboxUnavailableError` instead of
falling back to the host — the registry only fails open for an *unknown* name, and a caller that
asked for ``docker`` on a no-Docker machine must get a typed refusal (which an unattended run
parks needs-input on), never a silent host downgrade.

This tier is core-native (registered at boot beside ``none``), not an installable app; the app
provider type + ``SandboxTypeHandler`` seam stays for a future ``podman``/``byoi`` contribution.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time

from personalclaw.sandbox import ResourceCeilings, create_subprocess_limited
from personalclaw.sandbox_providers.base import (
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
    SandboxUnavailableError,
)

DOCKER_PROVIDER_NAME = "docker"

#: Base image for the sandbox. Overridable per host (a UID-baked or dependency-baked image); the
#: default is a small stock image that exists on any Docker install. UID alignment is done at
#: RUN time via ``--user``, so the default image needs no PersonalClaw-specific build.
DEFAULT_SANDBOX_IMAGE = "python:3.12-slim"

_IMAGE_ENV = "PERSONALCLAW_SANDBOX_DOCKER_IMAGE"

# Cached availability probe (the plan's short-TTL cached probe — a per-spawn ``docker version``
# would add ~100ms to every launch). ``(checked_monotonic, ok)``.
_PROBE_TTL_SECS = 30.0
_probe_cache: tuple[float, bool] | None = None


def sandbox_image() -> str:
    """The configured base image (env override → default)."""
    return os.environ.get(_IMAGE_ENV, "").strip() or DEFAULT_SANDBOX_IMAGE


def _daemon_ping() -> bool:
    """True when the docker CLI is on PATH AND its daemon answers. Never raises."""
    if not shutil.which("docker"):
        return False
    try:
        # ``docker version --format {{.Server.Version}}`` is empty/non-zero when the daemon is
        # unreachable even though the client binary exists (Docker Desktop stopped, socket gone).
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def docker_available(*, refresh: bool = False) -> bool:
    """Cached daemon probe. ``refresh=True`` forces a re-check (the doctor's live dot)."""
    global _probe_cache
    now = time.monotonic()
    if not refresh and _probe_cache is not None and (now - _probe_cache[0]) < _PROBE_TTL_SECS:
        return _probe_cache[1]
    ok = _daemon_ping()
    _probe_cache = (now, ok)
    return ok


def _mount_flags(host_path: str, *, read_only: bool) -> list[str]:
    """A ``-v`` bind-mount of *host_path* at the same path inside the container.

    Same-path mounting keeps host and guest paths identical so a worktree-relative cwd resolves
    unchanged. On Linux the ``:z`` suffix relabels the mount for SELinux (a no-op on non-SELinux
    hosts); macOS/Windows Docker Desktop ignore the label.
    """
    mode = "ro" if read_only else "rw"
    if sys.platform.startswith("linux"):
        mode = f"{mode},z"
    return ["-v", f"{host_path}:{host_path}:{mode}"]


def build_docker_argv(
    inner_argv: list[str],
    *,
    workspace_dir: str,
    ceilings: ResourceCeilings,
    spec: SandboxSpec,
    container_name: str,
) -> list[str]:
    """Compose the full ``docker run`` argv wrapping *inner_argv* (pure — unit-testable).

    Order: ``docker run`` + isolation flags + image + the inner command. Nothing here touches
    the daemon, so tests assert the exact command construction without Docker installed.
    """
    uid = os.getuid() if hasattr(os, "getuid") else 0
    gid = os.getgid() if hasattr(os, "getgid") else 0

    argv: list[str] = [
        "docker",
        "run",
        "--rm",
        "--init",  # PID 1 reaps zombies + forwards signals, so teardown kills the whole tree.
        "--name",
        container_name,
        "--user",
        f"{uid}:{gid}",  # UID alignment WITHOUT chown -R: mount writes are host-user owned.
    ]

    # Network: only ``off`` is enforced docker-side (``none``); finer egress tiers are advisory
    # here and belong to the host egress rail (net/policy), recorded as such in SandboxSpec.
    if (spec.egress_tier or "all").lower() == "off":
        argv += ["--network", "none"]

    # Resource ceilings → native container limits. A fork bomb hits ``pids.max`` and dies
    # contained; RSS is bounded by the cgroup memory controller.
    if ceilings.max_pids > 0:
        argv += ["--pids-limit", str(ceilings.max_pids)]
    if ceilings.max_rss_mb > 0:
        argv += ["--memory", f"{ceilings.max_rss_mb}m"]

    # The workspace is the writable root of the boundary; extra allowed_write_paths join it rw.
    # Every mount is same-path so a worktree-relative cwd is identical host- and guest-side.
    mounted: set[str] = set()
    for path in (workspace_dir, *spec.allowed_write_paths, *spec.grant_paths):
        norm = os.path.abspath(os.path.expanduser(path)) if path else ""
        if not norm or norm in mounted:
            continue
        argv += _mount_flags(norm, read_only=False)
        mounted.add(norm)

    if workspace_dir:
        argv += ["--workdir", os.path.abspath(os.path.expanduser(workspace_dir))]

    # Container environment is spec.env ONLY — the host environment is never copied in, so a
    # secret in the gateway process cannot reach the sandbox (WORK-R19 filters spec.env upstream).
    for key in sorted(spec.env):
        argv += ["--env", f"{key}={spec.env[key]}"]

    # Ports mapped to the host for the localhost preview (§6.2); ephemeral, torn down with --rm.
    for port in spec.expose_ports:
        argv += ["-p", f"{int(port)}:{int(port)}"]

    argv.append(sandbox_image())
    argv += list(inner_argv)
    return argv


class _DockerHandle(SandboxHandle):
    """A ``docker run`` command wrapping the inner argv; ceilings become native docker flags.

    The workspace mount is finalized at :meth:`exec` from the launch ``cwd`` when ``spec`` does
    not name one, so the live ACP/subagent consumer — which passes ``cwd`` to ``exec`` and never
    populates ``workspace_dir`` — bind-mounts the right worktree with no consumer change.
    """

    def __init__(self, inner_argv: list[str], spec: SandboxSpec, ceilings: ResourceCeilings):
        self._inner = list(inner_argv)
        self._spec = spec
        self._ceilings = ceilings
        self._name = f"pclaw-sbx-{os.getpid()}-{int(time.monotonic() * 1000) % 1_000_000}"
        # Best-effort finalized argv for ``.argv`` introspection before exec: uses the spec's
        # workspace_dir (or "") — exec re-derives it from cwd if empty.
        self._argv = build_docker_argv(
            self._inner,
            workspace_dir=spec.workspace_dir,
            ceilings=ceilings,
            spec=spec,
            container_name=self._name,
        )

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    async def exec(self, **kwargs: object) -> asyncio.subprocess.Process:
        # The consumer passes the run dir as ``cwd`` and the worker env as ``env``. For a
        # container neither goes to the docker CLIENT: ``cwd`` becomes the bind-mount + workdir,
        # and the container env is spec.env only (host env stays out). The docker client inherits
        # the real process environment so it finds the daemon socket / PATH.
        cwd = kwargs.pop("cwd", None)
        kwargs.pop("env", None)
        workspace = self._spec.workspace_dir or (str(cwd) if cwd else os.getcwd())
        self._argv = build_docker_argv(
            self._inner,
            workspace_dir=workspace,
            ceilings=self._ceilings,
            spec=self._spec,
            container_name=self._name,
        )
        # Ceilings on the docker CLIENT are harmless and keep the seam uniform with ``none``;
        # the container's OWN limits are the ``--pids-limit``/``--memory`` flags built above.
        return await create_subprocess_limited(
            *self._argv, profile=self._spec.profile, ceilings=self._ceilings, **kwargs
        )

    def cleanup(self) -> None:
        # ``--rm`` removes the container on exit; this is the belt-and-suspenders teardown for a
        # container that outlived an abnormal exit. Best-effort + idempotent — never raises.
        name = self._name
        if not name:
            return
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        self._name = ""


class DockerSandboxProvider(SandboxProvider):
    """Bind-mount container tier. Available only when the docker daemon answers; otherwise
    :meth:`wrap` refuses with a typed error rather than downgrading to the host."""

    name = DOCKER_PROVIDER_NAME
    display_name = "Docker (bind-mount container)"

    def available(self) -> bool:
        return docker_available()

    def wrap(self, spec: SandboxSpec, argv: list[str]) -> _DockerHandle:
        if not self.available():
            raise SandboxUnavailableError(
                what="Docker sandbox requested but unavailable",
                why="the docker CLI is not on PATH or its daemon is not responding.",
                fix="start Docker (Desktop / `dockerd`) or choose a different sandbox tier.",
            )
        ceilings = spec.ceilings or ResourceCeilings.from_config()
        return _DockerHandle(list(argv), spec, ceilings)


def create_provider(config: object | None = None) -> DockerSandboxProvider:
    """Factory mirroring the installable-provider entry-point shape (unused for the builtin)."""
    return DockerSandboxProvider()
