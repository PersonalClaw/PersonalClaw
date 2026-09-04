"""The ``lima`` sandbox provider (EXECUTION-ISOLATION EI-4).

The VM confinement tier: where ``docker`` (EI-2) runs an agent-influenced process tree in a
bind-mount container, ``lima`` runs it inside a Lima-managed guest VM via ``limactl shell``, so
the isolation is a full hardware-virtualised boundary (the Firecracker-class model ARCC's
SAX-05 isolation guidance describes) rather than a shared-kernel container. Lima is the
macOS-first path — a stopped or missing Lima instance is the exact ``needs-input`` parking case
EI-2 established for a no-Docker machine.

**Unlike ``docker``, this is NOT a core builtin.** It ships as an installable ``sandbox`` app
(``apps/lima-sandbox``) and is registered/unregistered on enable/disable through
:class:`personalclaw.providers.registry.SandboxTypeHandler` — the extension lifecycle the
registry docstring reserves for a container/VM app. :func:`create_provider` is the manifest
factory that handler calls. The registry therefore does NOT self-register ``lima`` at boot; a
spawn site only resolves it when the tier app is installed and enabled.

**Path translation (§2).** A Lima guest mounts host directories at a guest mount point. By
default Lima maps the host home at the SAME path inside the guest (identity), so a
worktree-relative cwd resolves unchanged — but an instance configured with a distinct
``mountPoint`` needs the host path rewritten to its guest location before it becomes the
``--workdir``. :func:`translate_path` is that pure host↔guest map, configurable via
``PERSONALCLAW_SANDBOX_LIMA_HOST_MOUNT`` / ``PERSONALCLAW_SANDBOX_LIMA_GUEST_MOUNT``; identity
when the guest mount is unset.

**Failure honesty (§1.1).** :meth:`LimaSandboxProvider.available` probes ``limactl`` + the
instance status (cached, short TTL). When ``limactl`` is missing or the instance is not
``Running``, :meth:`LimaSandboxProvider.wrap` raises :class:`SandboxUnavailableError` carrying a
WHAT/WHY/FIX reason that distinguishes the two cases, so the caller greys the tier out with that
reason and an unattended run parks ``needs-input`` — never a silent host downgrade.

**Honest about what one ``limactl shell`` can and cannot bound.** A Lima guest's network reach
and its pids/memory limits are properties of the INSTANCE (fixed at ``limactl create``), not of
an individual ``limactl shell`` invocation. So ``egress_tier`` and the resource ceilings are
enforced at instance creation (a locked-down instance) and are advisory at this layer — this
provider does not fabricate a per-exec flag that Lima has no equivalent for (the EI-11 rule: no
surface claims confinement the code does not provide). What this provider DOES enforce per exec
is the guest working directory (path translation) and the declared environment (set explicitly
on the guest command); the routed ``create_subprocess_limited`` ceiling bounds the ``limactl``
CLIENT, mirroring the ``none``/``docker`` seam.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time

from personalclaw.sandbox import ResourceCeilings, create_subprocess_limited
from personalclaw.sandbox_providers.base import (
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
    SandboxUnavailableError,
)

LIMA_PROVIDER_NAME = "lima"

#: The Lima instance the provider drives. Overridable per host; the default matches the
#: ``apps/lima-sandbox`` app's provisioned instance name.
DEFAULT_LIMA_INSTANCE = "personalclaw"

_INSTANCE_ENV = "PERSONALCLAW_SANDBOX_LIMA_INSTANCE"
_HOST_MOUNT_ENV = "PERSONALCLAW_SANDBOX_LIMA_HOST_MOUNT"
_GUEST_MOUNT_ENV = "PERSONALCLAW_SANDBOX_LIMA_GUEST_MOUNT"

# Cached availability probe (the plan's short-TTL cached probe — a per-spawn ``limactl list``
# would add latency to every launch). Keyed by instance name → ``(checked_monotonic, ok, reason)``
# so two instances do not share a verdict.
_PROBE_TTL_SECS = 30.0
_probe_cache: dict[str, tuple[float, bool, str]] = {}


def lima_instance() -> str:
    """The configured Lima instance name (env override → default)."""
    return os.environ.get(_INSTANCE_ENV, "").strip() or DEFAULT_LIMA_INSTANCE


def _host_mount() -> str:
    """Host directory that maps into the guest. Defaults to the user home (Lima's default)."""
    return os.environ.get(_HOST_MOUNT_ENV, "").strip() or os.path.expanduser("~")


def _guest_mount() -> str:
    """Guest mount point the host mount appears at. Empty → identity (same path in the guest)."""
    return os.environ.get(_GUEST_MOUNT_ENV, "").strip()


def translate_path(host_path: str, *, host_mount: str = "", guest_mount: str = "") -> str:
    """Map a *host_path* to its location inside the Lima guest (pure — unit-testable).

    A path within the mounted host tree is rewritten from ``host_mount`` to ``guest_mount``;
    the mount root itself maps to the guest mount root; a path OUTSIDE the mounted tree is
    returned unchanged (Lima cannot see it, which is the caller's concern, not this map's). When
    ``guest_mount`` is empty the mapping is identity — Lima's default same-path behaviour — so an
    unconfigured instance needs no translation and a worktree-relative cwd resolves unchanged.
    """
    if not host_path:
        return host_path
    hp = os.path.abspath(os.path.expanduser(host_path))
    hm = os.path.abspath(os.path.expanduser(host_mount)) if host_mount else _host_mount()
    gm = os.path.abspath(os.path.expanduser(guest_mount)) if guest_mount else (_guest_mount() or hm)
    if not hm or hp == hm:
        return gm
    prefix = hm.rstrip("/") + "/"
    if hp.startswith(prefix):
        return os.path.join(gm, hp[len(prefix) :])
    return hp


def _probe(instance: str) -> tuple[bool, str]:
    """``(ok, reason)`` for *instance*. ``reason`` is the WHY when not ok. Never raises.

    Distinguishes ``limactl`` missing from a non-``Running`` instance so the degradation dialog
    can name the actual cause (and the right FIX).
    """
    if not shutil.which("limactl"):
        return False, "the `limactl` CLI is not on PATH (Lima is not installed)."
    try:
        # ``limactl list <name> --format {{.Status}}`` prints the instance status
        # (``Running``/``Stopped``); an unknown instance prints nothing with a zero exit.
        result = subprocess.run(
            ["limactl", "list", instance, "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "querying the Lima instance status failed (`limactl list` did not return)."
    status = (result.stdout or "").strip()
    if result.returncode != 0:
        return False, f"`limactl list {instance}` exited {result.returncode}."
    if not status:
        return False, f"the Lima instance {instance!r} does not exist."
    if status != "Running":
        return False, f"the Lima instance {instance!r} is {status!r}, not Running."
    return True, ""


def _cached_probe(instance: str, *, refresh: bool) -> tuple[bool, str]:
    now = time.monotonic()
    cached = _probe_cache.get(instance)
    if not refresh and cached is not None and (now - cached[0]) < _PROBE_TTL_SECS:
        return cached[1], cached[2]
    ok, reason = _probe(instance)
    _probe_cache[instance] = (now, ok, reason)
    return ok, reason


def lima_available(*, instance: str = "", refresh: bool = False) -> bool:
    """Cached availability probe. ``refresh=True`` forces a re-check (the doctor's live dot)."""
    return _cached_probe(instance or lima_instance(), refresh=refresh)[0]


def lima_unavailable_reason(*, instance: str = "", refresh: bool = False) -> str:
    """The WHY when the instance is unavailable (empty string when it is available)."""
    return _cached_probe(instance or lima_instance(), refresh=refresh)[1]


def build_lima_argv(
    inner_argv: list[str],
    *,
    workspace_dir: str,
    spec: SandboxSpec,
    instance: str,
    host_mount: str = "",
    guest_mount: str = "",
) -> list[str]:
    """Compose the full ``limactl shell`` argv wrapping *inner_argv* (pure — unit-testable).

    Order: ``limactl shell`` + ``--workdir <guest workspace>`` + instance + the (env-prefixed)
    inner command. Nothing here touches ``limactl``, so tests assert the exact command without
    Lima installed. The guest workdir is the host workspace run through :func:`translate_path`;
    the declared ``spec.env`` is set explicitly on the guest command via an ``env K=V`` prefix so
    the guest process runs with exactly the declared variables regardless of what ``limactl``
    forwards (the launcher additionally hands ``limactl`` a clean allowlisted client environment).
    """
    argv: list[str] = ["limactl", "shell"]

    if workspace_dir:
        guest_ws = translate_path(workspace_dir, host_mount=host_mount, guest_mount=guest_mount)
        argv += ["--workdir", guest_ws]

    argv.append(instance)

    # The declared environment, set on the guest command. ``env K=V …`` overrides whatever the
    # guest login shell / forwarded client env would otherwise provide, so the guest process
    # runs with exactly the declared variables. Empty env → no prefix (inherit the guest base).
    guest_cmd = list(inner_argv)
    if spec.env:
        guest_cmd = ["env", *[f"{k}={spec.env[k]}" for k in sorted(spec.env)], *guest_cmd]

    argv += guest_cmd
    return argv


class _LimaHandle(SandboxHandle):
    """A ``limactl shell`` command wrapping the inner argv.

    The guest workspace is finalized at :meth:`exec` from the launch ``cwd`` when ``spec`` does
    not name a ``workspace_dir``, so a consumer that passes ``cwd`` to ``exec`` (the ACP/subagent
    path) runs in the right guest directory with no consumer change — mirroring ``_DockerHandle``.
    """

    def __init__(self, inner_argv: list[str], spec: SandboxSpec, ceilings: ResourceCeilings):
        self._inner = list(inner_argv)
        self._spec = spec
        self._ceilings = ceilings
        self._instance = lima_instance()
        self._argv = build_lima_argv(
            self._inner,
            workspace_dir=spec.workspace_dir,
            spec=spec,
            instance=self._instance,
        )

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    async def exec(self, **kwargs: object) -> asyncio.subprocess.Process:
        # The consumer passes the run dir as ``cwd``; for a VM that becomes the guest ``--workdir``
        # (path-translated), not the ``limactl`` client's cwd. ``env`` is not forwarded to the
        # guest here — the declared env is baked onto the guest command by build_lima_argv; the
        # client inherits the parent env so it can find ``limactl``/its socket (mirrors docker).
        cwd = kwargs.pop("cwd", None)
        kwargs.pop("env", None)
        workspace = self._spec.workspace_dir or (str(cwd) if cwd else os.getcwd())
        self._argv = build_lima_argv(
            self._inner,
            workspace_dir=workspace,
            spec=self._spec,
            instance=self._instance,
        )
        # Ceilings bound the ``limactl`` CLIENT and keep the seam uniform with ``none``/``docker``;
        # the guest's OWN pids/memory bounds are instance-creation config, not a per-exec flag.
        return await create_subprocess_limited(
            *self._argv, profile=self._spec.profile, ceilings=self._ceilings, **kwargs
        )

    def cleanup(self) -> None:
        # ``limactl shell`` runs the command and exits with it — there is no per-exec container or
        # temp file to reclaim (the guest VM is a long-lived instance the app owns). No-op.
        return None


class LimaSandboxProvider(SandboxProvider):
    """Lima VM tier. Available only when ``limactl`` is present and the instance is ``Running``;
    otherwise :meth:`wrap` refuses with a typed, reasoned error rather than downgrading."""

    name = LIMA_PROVIDER_NAME
    display_name = "Lima (VM)"

    def available(self) -> bool:
        return lima_available()

    def wrap(self, spec: SandboxSpec, argv: list[str]) -> _LimaHandle:
        instance = lima_instance()
        ok, reason = _cached_probe(instance, refresh=False)
        if not ok:
            raise SandboxUnavailableError(
                what="Lima sandbox requested but unavailable",
                why=reason or "the Lima instance is not running.",
                fix=f"install Lima and start the instance (`limactl start {instance}`), "
                "or choose a different sandbox tier.",
            )
        ceilings = spec.ceilings or ResourceCeilings.from_config()
        return _LimaHandle(list(argv), spec, ceilings)


def create_provider(config: object | None = None) -> LimaSandboxProvider:
    """Manifest factory — the entry point ``SandboxTypeHandler`` calls on enable of the
    ``apps/lima-sandbox`` tier app (mirrors the installable-provider factory shape)."""
    return LimaSandboxProvider()
