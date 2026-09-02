"""Container workspace mode (WF2WOR-12 / WORK-R20) — the typed manifest and the backends.

Two halves, both deliberately small:

* **The manifest** (`parse_manifest`) — a typed declaration `{image XOR build, user, mounts,
  capabilities}` validated at parse time with the same `SpecIssue` vocabulary as
  `workspace.parse_workspace`, because a container that provisions from a malformed manifest
  fails at run time in a subprocess where the author cannot see the mistake.

* **The backends** (`detect_backend`) — Docker, containerd (via `nerdctl`) and Apple's
  `container` CLI on macOS, all driven by SHELLING OUT to the CLI the user already has.
  There is deliberately **no hard Docker dependency and no SDK import**: the plan's posture
  is local-first and opt-in, and an import-time dependency on `docker-py` would tax every
  install for a mode most runs never use. A machine with no backend at all keeps the
  existing graceful degradation in `provisioning._create_workspace` (isolated scratch dir,
  reason recorded) — a template that declares `mode: container` must stay RUNNABLE, just
  visibly not containerized.

**The engine owns runtime semantics** (§4.4): the workspace is always mounted at
:data:`WORKSPACE_MOUNT`, the entrypoint is overridden to an idle keep-alive so stages exec
into a stable container, and the working directory is the workspace mount. A manifest mount
targeting the reserved mount point is refused at parse time — silently shadowing the
workspace is the kind of misconfiguration that reports success while every stage writes
into a directory nobody will ever read.

**Snapshots** anchor fork-from-checkpoint to workspace state: `snapshot()` commits the
running container to an image ref recorded on the checkpoint, and a fork provisions the
child's container FROM that ref — the thing journal-only fork structurally cannot give a
code-kind run. A backend that cannot snapshot (Apple's CLI has no commit verb) says so in
its result rather than pretending; the checkpoint simply carries no anchor and fork falls
back to fresh provisioning, which is the pre-container behaviour and not an error.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.workspace import SpecIssue

logger = logging.getLogger(__name__)

#: Where the host-side workspace directory appears inside every container. Engine-owned:
#: manifests may not mount over it, and stages treat it as the working directory.
WORKSPACE_MOUNT = "/workspace"

#: The keep-alive the engine runs as the container's process. Stages `exec` into the
#: container; the container itself just stays up between them.
_KEEPALIVE = ("sleep", "infinity")

#: Subprocess ceiling for CLI probes and lifecycle verbs. Image pulls and builds get the
#: longer budget — a cold `docker build` legitimately takes minutes.
_VERB_TIMEOUT_SECS = 60
_PROVISION_TIMEOUT_SECS = 900


@dataclass
class EnvironmentManifest:
    """§4.4's typed environment manifest.

    `image` XOR `build` is the load-bearing rule: both is ambiguous about which wins,
    neither provisions nothing, and both cases are authoring mistakes better named at
    save time than discovered as a subprocess error.
    """

    image: str = ""
    #: `{dockerfile, context}` — both paths, resolved by the backend relative to the
    #: project workspace when relative.
    build: dict[str, str] = field(default_factory=dict)
    user: str = ""
    #: Each `{source, target, readonly}`. The workspace mount is NOT declared here —
    #: the engine adds it unconditionally.
    mounts: list[dict[str, Any]] = field(default_factory=list)
    #: Linux capability names handed to the backend (`--cap-add`). `privileged` is not a
    #: capability and is refused — a privileged container is not "an option" at personal
    #: scale, it is the isolation being switched off with extra steps.
    capabilities: list[str] = field(default_factory=list)

    @property
    def declared(self) -> bool:
        return bool(self.image or self.build)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "build": dict(self.build),
            "user": self.user,
            "mounts": [dict(m) for m in self.mounts],
            "capabilities": list(self.capabilities),
        }


def parse_manifest(raw: Any) -> tuple[EnvironmentManifest, list[SpecIssue]]:
    """Read a `workspace.container:` block, returning the manifest and everything wrong with it.

    Same tolerant-reader posture as the rest of the workspace parsing: unknown fields pass
    silently, and every refusal names the rule it enforces.
    """
    issues: list[SpecIssue] = []
    if raw is None:
        return EnvironmentManifest(), issues
    if not isinstance(raw, dict):
        return EnvironmentManifest(), [
            SpecIssue("container_not_object", "workspace.container must be an object", fatal=True)
        ]

    image = str(raw.get("image", "") or "").strip()
    build_raw = raw.get("build")
    build: dict[str, str] = {}
    if isinstance(build_raw, dict):
        build = {
            "dockerfile": str(build_raw.get("dockerfile", "") or "").strip(),
            "context": str(build_raw.get("context", "") or ".").strip() or ".",
        }
        if not build["dockerfile"]:
            issues.append(
                SpecIssue(
                    "container_build_no_dockerfile",
                    "container.build needs a `dockerfile` path",
                    fatal=True,
                )
            )
    elif build_raw is not None:
        issues.append(
            SpecIssue(
                "container_build_not_object",
                "container.build must be an object with dockerfile/context",
                fatal=True,
            )
        )

    if image and build:
        issues.append(
            SpecIssue(
                "container_image_xor_build",
                "declare container.image OR container.build, not both — with both declared "
                "it is ambiguous which environment the run actually gets",
                fatal=True,
            )
        )
    if not image and not build:
        issues.append(
            SpecIssue(
                "container_no_environment",
                "container mode needs an environment: declare container.image or "
                "container.build",
                fatal=True,
            )
        )

    mounts: list[dict[str, Any]] = []
    for entry in raw.get("mounts") or []:
        if not isinstance(entry, dict):
            issues.append(
                SpecIssue(
                    "container_mount_not_object",
                    f"mount entry {entry!r} must be an object with source/target",
                )
            )
            continue
        source = str(entry.get("source", "") or "").strip()
        target = str(entry.get("target", "") or "").strip()
        if not source or not target:
            issues.append(
                SpecIssue(
                    "container_mount_incomplete",
                    "a mount needs both `source` and `target`",
                    fatal=True,
                )
            )
            continue
        normalized_target = target.rstrip("/") or "/"
        if normalized_target == WORKSPACE_MOUNT or normalized_target == "/":
            issues.append(
                SpecIssue(
                    "container_mount_reserved",
                    f"mount target {target!r} is engine-owned — the run workspace is always "
                    f"mounted at {WORKSPACE_MOUNT}, and shadowing it would make every stage "
                    "write into a directory nobody reads back",
                    fatal=True,
                )
            )
            continue
        mounts.append({"source": source, "target": target, "readonly": bool(entry.get("readonly"))})

    capabilities: list[str] = []
    for cap in raw.get("capabilities") or []:
        name = str(cap or "").strip()
        if not name:
            continue
        if name.lower() in ("privileged", "all"):
            issues.append(
                SpecIssue(
                    "container_privileged_refused",
                    f"capability {name!r} is refused — a privileged container is the isolation "
                    "switched off, not a capability grant",
                    fatal=True,
                )
            )
            continue
        capabilities.append(name)

    manifest = EnvironmentManifest(
        image=image,
        build=build,
        user=str(raw.get("user", "") or "").strip(),
        mounts=mounts,
        capabilities=capabilities,
    )
    return manifest, issues


# ── backends ──────────────────────────────────────────────────────────────────


@dataclass
class BackendResult:
    """One lifecycle verb's outcome. `ok=False` carries the reason; nothing here raises."""

    ok: bool
    value: str = ""
    reason: str = ""


async def _run_cli(
    argv: list[str], *, timeout: float = _VERB_TIMEOUT_SECS, cwd: str = ""
) -> BackendResult:
    """One subprocess, no shell, bounded. The pattern `effects.run_teardown` set."""
    try:
        from personalclaw.sandbox import create_subprocess_limited

        proc = await create_subprocess_limited(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except FileNotFoundError:
        return BackendResult(False, reason=f"{argv[0]} is not installed")
    except asyncio.TimeoutError:
        return BackendResult(False, reason=f"{argv[0]} timed out after {timeout:.0f}s")
    except Exception as exc:  # noqa: BLE001 - a backend failure is a result, not a crash
        return BackendResult(False, reason=str(exc))
    if proc.returncode != 0:
        detail = (err or out or b"").decode(errors="replace").strip()
        return BackendResult(False, reason=detail[-500:] or f"exit {proc.returncode}")
    return BackendResult(True, value=(out or b"").decode(errors="replace").strip())


class CliContainerBackend:
    """Docker-compatible CLI backend. `nerdctl` (containerd) speaks the same verbs, so the
    containerd backend is this class with a different binary — one implementation, not two
    copies that drift."""

    #: Verbs this backend can perform. Apple's subclass narrows it.
    can_snapshot = True

    def __init__(self, binary: str) -> None:
        self.binary = binary

    @property
    def name(self) -> str:
        return self.binary

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    async def provision(
        self,
        manifest: EnvironmentManifest,
        *,
        workspace_dir: str,
        run_id: str,
        from_snapshot: str = "",
        context_dir: str = "",
    ) -> BackendResult:
        """Create + start the run's container, workspace mounted, keep-alive as PID 1.

        `from_snapshot` wins over the manifest's image/build: a fork anchored to a
        checkpoint provisions the child from the PARENT's committed state, which is the
        whole point of the anchor.
        """
        image = from_snapshot
        if not image and manifest.build:
            tag = f"pclaw/run-{run_id}:build"
            build = await _run_cli(
                [
                    self.binary,
                    "build",
                    "-t",
                    tag,
                    "-f",
                    manifest.build.get("dockerfile", ""),
                    manifest.build.get("context", ".") or ".",
                ],
                timeout=_PROVISION_TIMEOUT_SECS,
                cwd=context_dir,
            )
            if not build.ok:
                return build
            image = tag
        if not image:
            image = manifest.image
        if not image:
            return BackendResult(False, reason="manifest declares no image and no build")

        argv = [
            self.binary,
            "run",
            "--detach",
            "--name",
            f"pclaw-run-{run_id}",
            # Engine-owned runtime semantics (§4.4): fixed workspace mount + workdir; the
            # image's own entrypoint is overridden so stages exec into a stable process.
            "--entrypoint",
            _KEEPALIVE[0],
            "--volume",
            f"{workspace_dir}:{WORKSPACE_MOUNT}",
            "--workdir",
            WORKSPACE_MOUNT,
        ]
        if manifest.user:
            argv += ["--user", manifest.user]
        for mount in manifest.mounts:
            suffix = ":ro" if mount.get("readonly") else ""
            argv += ["--volume", f"{mount['source']}:{mount['target']}{suffix}"]
        for cap in manifest.capabilities:
            argv += ["--cap-add", cap]
        argv += [image, *_KEEPALIVE[1:]]
        result = await _run_cli(argv, timeout=_PROVISION_TIMEOUT_SECS)
        if result.ok:
            # `run --detach` prints the container id; the NAME is what teardown uses, so a
            # truncated id can never orphan the container.
            return BackendResult(True, value=f"pclaw-run-{run_id}")
        return result

    async def snapshot(self, container_id: str, *, tag: str) -> BackendResult:
        """Commit the container's filesystem to an image ref — the fork anchor."""
        result = await _run_cli(
            [self.binary, "commit", container_id, tag], timeout=_PROVISION_TIMEOUT_SECS
        )
        if result.ok:
            return BackendResult(True, value=tag)
        return result

    async def remove(self, container_id: str) -> BackendResult:
        return await _run_cli([self.binary, "rm", "--force", container_id])


class AppleContainerBackend(CliContainerBackend):
    """Apple's `container` CLI (macOS 15+, Apple Virtualization framework).

    Speaks the same core verbs (`run`, `rm`) but has NO commit — so it declares
    `can_snapshot = False` and its `snapshot` names the limitation instead of failing
    inside a subprocess. A checkpoint on this backend simply carries no workspace anchor,
    and fork falls back to fresh provisioning — degraded honestly, not broken quietly.
    """

    can_snapshot = False

    def __init__(self) -> None:
        super().__init__("container")

    async def snapshot(self, container_id: str, *, tag: str) -> BackendResult:
        return BackendResult(
            False,
            reason="Apple's container CLI has no commit verb — checkpoints on this backend "
            "carry no workspace snapshot, and fork provisions the child fresh",
        )


#: Probe order: Docker is the overwhelmingly common install; nerdctl covers containerd
#: setups (Rancher Desktop, Lima); Apple's CLI is the no-Docker macOS path.
_BACKENDS = (
    lambda: CliContainerBackend("docker"),
    lambda: CliContainerBackend("nerdctl"),
    lambda: AppleContainerBackend(),
)


def detect_backend() -> CliContainerBackend | None:
    """The first available backend, or None — in which case container mode degrades to the
    isolated scratch dir `provisioning._create_workspace` already provides."""
    for factory in _BACKENDS:
        backend = factory()
        if backend.available():
            return backend
    return None
