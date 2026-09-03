"""Abstract base for sandbox providers (EXECUTION-ISOLATION EI-1).

A **sandbox provider** owns the seam between "here is a command to launch" and "here is a
running child process", composing two host primitives that until now were called inline at
every spawn site (``acp/transport.py``):

* the OS-level filesystem sandbox (:func:`personalclaw.sandbox.wrap_argv` — macOS seatbelt /
  Linux user-namespace / none), which HIDES sensitive paths from an agent-influenced child; and
* the post-exec resource ceilings (:class:`personalclaw.sandbox.ResourceCeilings` delivered by
  the stdlib shim), which BOUND a child's open files / process count / address space.

The contract is deliberately two-phase — :meth:`SandboxProvider.wrap` produces a
:class:`SandboxHandle`, then :meth:`SandboxHandle.exec` launches it — so a caller can inspect
the wrapped argv before spawning, and a provider can own any temp state (a seatbelt profile, a
launcher script) that must be cleaned up AFTER the child exits (:meth:`SandboxHandle.cleanup`).

The in-core ``none`` provider (:mod:`personalclaw.sandbox_providers.none`) composes the two
existing primitives with no new isolation — it is the default and is behaviour-identical to the
inline logic it replaces. A stronger provider (a container/VM tier) is an installable ``sandbox``
app registering through :class:`personalclaw.providers.registry.SandboxTypeHandler`.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from personalclaw.sandbox import PROFILE_SESSION_HOST, ResourceCeilings


class SandboxUnavailableError(RuntimeError):
    """A requested provider cannot run on this host and MUST NOT silently downgrade.

    Carried by a container/VM tier's :meth:`SandboxProvider.wrap` when its runtime is absent
    (no Docker daemon, stopped Lima instance …). The message is WHAT/WHY/FIX-shaped so a
    consumer can surface it verbatim: unattended code runs park needs-input on it rather than
    dropping to the host ``none`` provider (EXECUTION-ISOLATION §1.1 failure-honesty rule —
    a silent host downgrade would run agent-influenced code with none of the isolation the
    caller asked for).
    """

    def __init__(self, what: str, why: str, fix: str) -> None:
        self.what = what
        self.why = why
        self.fix = fix
        super().__init__(f"{what} — {why} Fix: {fix}")


@dataclass
class SandboxSpec:
    """The isolation policy for one launch — pure data, no behaviour.

    ``mode`` is the :func:`personalclaw.sandbox.wrap_argv` level: ``"auto"``/``"standard"``
    (expose .aws/.ssh/.kube), ``"cc"`` (hide .aws, expose .aws/config for credential_process),
    ``"strict"`` (hide everything), or ``"off"`` (no OS sandbox).

    ``profile`` names the resource-ceiling profile applied by the post-exec shim
    (``tool``/``session_host``/``build``/``none`` — see :class:`ResourceCeilings`). It defaults
    to ``session_host`` because the first consumer is the ACP backend, which multiplexes many
    MCP stdio pipes and must not be OOM-biased.

    ``ceilings`` is an explicit :class:`ResourceCeilings`; ``None`` means "load from the live
    ``sandbox.*`` config at exec time" — the same fail-open-to-defaults behaviour a raw
    ``create_subprocess_limited`` has, so a broken config never blocks a spawn.

    The remaining fields are the **confinement policy** a container/VM tier (EI-2 ``docker``,
    EI-4 ``lima``) translates to its native knobs; the in-core ``none`` provider ignores them,
    so every existing caller that constructs ``SandboxSpec(mode=…, profile=…)`` is unchanged.

    * ``workspace_dir`` — the WORK-R3 worktree/scratch dir the tier bind-mounts. Empty means
      "use the launch ``cwd``" (what the live ACP/subagent consumer already passes to
      :meth:`SandboxHandle.exec`), so ``docker`` works with zero consumer changes.
    * ``allowed_write_paths`` — host paths (besides the workspace) the child may write. A tier
      mounts exactly these read-write; anything else is outside the boundary and a write to it
      fails because the path is not mounted.
    * ``egress_tier`` — ``off``/``listed``/``registry``/``all`` (AUTONOMY-GUARDRAILS §4.2).
      ``off`` maps to ``--network none``; ``all`` leaves the default network. The finer tiers
      are advisory at the docker layer (host egress-rail allowlisting is net/policy scope).
    * ``env`` — the container's environment, already secret-filtered by WORK-R19. This is the
      ONLY environment the container receives; the host's environment is never copied in.
    * ``safety_profile`` — AUTONOMY-GUARDRAILS §3 profile name (drives the §5.2 tool surface).
    * ``expose_ports`` — container ports mapped to the host for the §6.2 localhost preview.
    * ``grant_paths`` — host paths explicitly granted into the sandbox (e.g. a model dir). NOT
      mounted unless listed, so an ungranted sandbox cannot see — let alone delete — them.
    """

    mode: str = "auto"
    profile: str = PROFILE_SESSION_HOST
    ceilings: ResourceCeilings | None = None
    workspace_dir: str = ""
    allowed_write_paths: tuple[str, ...] = ()
    egress_tier: str = "all"
    env: dict[str, str] = field(default_factory=dict)
    safety_profile: str = ""
    expose_ports: tuple[int, ...] = ()
    grant_paths: tuple[str, ...] = ()


class SandboxHandle(ABC):
    """A wrapped, ready-to-launch command plus the state its launch owns.

    Produced by :meth:`SandboxProvider.wrap`. :attr:`argv` is the fully wrapped command (OS
    sandbox applied); :meth:`exec` launches it (resource ceilings applied post-exec); and
    :meth:`cleanup` releases any temp files the wrap created, to be called AFTER the child exits.
    """

    @property
    @abstractmethod
    def argv(self) -> list[str]:
        """The wrapped launch argv (OS-sandbox prefix already applied)."""

    @abstractmethod
    async def exec(self, **kwargs: object) -> asyncio.subprocess.Process:
        """Launch :attr:`argv` and return the running process.

        Resource ceilings are delivered by the post-exec shim (never ``preexec_fn``), so the
        parent stays on ``posix_spawn`` and the event loop is never blocked on a fork. Extra
        kwargs (``stdin``/``stdout``/``stderr``/``cwd``/``env``/``start_new_session``/``limit`` …)
        pass straight through to the underlying ``create_subprocess_exec``.
        """

    @abstractmethod
    def cleanup(self) -> None:
        """Remove any temp state the wrap created (seatbelt profile / launcher script).

        Idempotent and best-effort — safe to call more than once and never raises.
        """


class SandboxProvider(ABC):
    """One isolation backend. The in-core ``none`` provider is the default; stronger tiers
    install as ``sandbox`` provider apps and register through ``SandboxTypeHandler``."""

    #: Stable identifier, matched to the app name; the registry keys on it.
    name: str = ""
    #: Human label for the Store / doctor.
    display_name: str = ""

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider can run on the current host (the doctor's green/red dot).

        ``none`` is always available; a container tier would probe for its runtime here.
        """

    @abstractmethod
    def wrap(self, spec: SandboxSpec, argv: list[str]) -> SandboxHandle:
        """Apply *spec* to *argv* and return a launchable :class:`SandboxHandle`."""
