"""The two :class:`ProposerBackend` implementations.

* :class:`RunnerProposerBackend` — one per cataloged runner, fired one-shot through the
  **sandbox provider** so it inherits the isolation class of the run it is helping, or, when the
  handoff names none, the one the runner's own runtime is set up with (``_runtime_sandbox``), so a
  second opinion never runs an agent on the host that every other start of it runs sandboxed.
  This is the "different cataloged runner" the second-opinion handoff selects.
* :class:`SubagentProposerBackend` — a fresh PClaw subagent as the second brain. Zero external
  dependencies; the degradation path when only one runner is installed (or when the eligible
  runner's dialect has no declared non-interactive form).

Both funnel their verdict through :func:`normalise` so there is exactly ONE place that turns "the
proposer said something" into ``ProposerResult.diff_verified`` — a second place would be a second
acceptance rule, and the whole point is that acceptance has one definition.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timezone

from personalclaw.agents import runner_grants
from personalclaw.agents.runners import RunnerDefinition, resolve_runner_command
from personalclaw.proposer.brief import HandoffBrief
from personalclaw.proposer.contract import (
    InvocationRef,
    PreparedInvocation,
    ProposerResult,
    parse_claimed_paths,
)
from personalclaw.proposer.dialects import one_shot
from personalclaw.proposer.verify import DiskBaseline, rediff, snapshot_workspace

#: Backend id for the always-available fallback.
SUBAGENT_BACKEND = "subagent"

#: Backend id prefix for a cataloged runner.
RUNNER_BACKEND_PREFIX = "runner:"


class ProposerUnavailable(RuntimeError):
    """Raised by ``prepare`` when this backend cannot be fired at all (undeclared dialect,
    runner not on PATH). A refusal here is a clean fall-through to the next backend; a
    half-prepared invocation that fails at fire time is not."""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def normalise(
    *,
    backend: str,
    runner_id: str,
    ok: bool,
    text: str,
    baseline: DiskBaseline | None,
    raw_ref: str = "",
    error: str = "",
) -> ProposerResult:
    """Turn a proposer's raw answer into the one normalised record, re-diffing disk to decide.

    ``diff_verified`` is computed here and ONLY here. Note the ordering: a proposer whose run
    failed still gets its claims verified, because a crashed run that nonetheless landed the
    edits is a materially different situation from one that landed nothing, and flattening the
    two would throw away the only evidence that distinguishes them.
    """
    claimed = parse_claimed_paths(text)
    if baseline is None:
        return ProposerResult(
            backend=backend,
            runner_id=runner_id,
            ok=ok,
            summary=text.strip()[:4000],
            diff_verified=False,
            raw_ref=raw_ref,
            claimed_paths=claimed,
            error=error or "no disk baseline was captured, so no edit can be confirmed",
        )
    verdict = rediff(baseline, claimed)
    return ProposerResult(
        backend=backend,
        runner_id=runner_id,
        ok=ok,
        summary=text.strip()[:4000],
        diff_verified=verdict.verified,
        raw_ref=raw_ref,
        claimed_paths=claimed,
        verified_paths=verdict.verified_paths,
        missing_paths=verdict.missing,
        error=error or ("" if verdict.verified else verdict.reason),
    )


def _runtime_sandbox(defn: RunnerDefinition) -> tuple[str, str]:
    """The sandbox tier and OS sandbox level *defn*'s CLI is set up to run in: its runtime entry's
    own (``llm.acp_agent.options_sandbox`` and ``options_sandbox_mode``), the reading a session of
    it is started with. A runtime nothing registered here declares nothing, so the host tier at
    the default level, as a session of it would get."""
    from personalclaw.llm.acp_agent import options_sandbox, options_sandbox_mode
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    try:
        options = dict(get_default_registry().get_entry(defn.runtime_id).options or {})
    except ProviderResolutionError:
        options = {}
    return options_sandbox(options), options_sandbox_mode(options)


class RunnerProposerBackend:
    """Fire a cataloged runner one-shot, inside the stalled consumer's sandbox class."""

    def __init__(self, defn: RunnerDefinition, *, timeout_secs: float = 300.0) -> None:
        self._defn = defn
        self._timeout = timeout_secs
        self.name = f"{RUNNER_BACKEND_PREFIX}{defn.id}"
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._handles: dict[str, object] = {}

    @property
    def runner_id(self) -> str:
        return self._defn.id

    async def prepare(self, brief: HandoffBrief) -> PreparedInvocation:
        if not runner_grants.allowed(self._defn):
            # Selection already passes over it; this is where its binary would be resolved.
            raise ProposerUnavailable(f"runner {self._defn.id!r}: {runner_grants.WAITING_REASON}")
        dialect = one_shot(self._defn.dialect, self._defn.id)
        if dialect is None:
            raise ProposerUnavailable(
                f"runner {self._defn.id!r} has no declared non-interactive form — "
                "firing it would block on a TTY that does not exist"
            )
        command = resolve_runner_command(self._defn)
        if not command:
            raise ProposerUnavailable(
                f"runner {self._defn.id!r} does not resolve to an executable on this host"
            )
        prompt = dialect.render(brief.render())
        baseline = brief.baseline or snapshot_workspace(brief.workspace, paths=brief.files_touched)
        tier, mode = _runtime_sandbox(self._defn)
        return PreparedInvocation(
            backend=self.name,
            runner_id=self._defn.id,
            prompt=prompt,
            cwd=brief.workspace,
            sandbox=brief.sandbox or tier,
            sandbox_mode=mode,
            argv=dialect.argv(tuple(command), prompt),
            timeout_secs=self._timeout,
            baseline=baseline,
        )

    async def invoke(self, prepared: PreparedInvocation) -> InvocationRef:
        """Launch through the sandbox provider — never a bare ``create_subprocess_exec``.

        Resolving the provider by name is what gives the second opinion the SAME isolation as
        the stalled run. A tier that is not installed, or cannot run, is a launch that did not
        happen, with the tier's own reason: the proposer does not run on the host in its place.
        """
        from personalclaw.sandbox_providers.base import SandboxSpec, SandboxUnavailableError
        from personalclaw.sandbox_providers.registry import resolve_provider

        try:
            provider = resolve_provider(prepared.sandbox)
            handle = provider.wrap(
                SandboxSpec(mode=prepared.sandbox_mode, profile="build"), list(prepared.argv)
            )
        except SandboxUnavailableError as exc:
            return InvocationRef(
                backend=self.name,
                runner_id=self._defn.id,
                handle="",
                started_at=_now(),
                prepared=prepared,
                error=f"failed to launch {self._defn.id}: {exc}",
            )
        try:
            proc = await handle.exec(
                cwd=prepared.cwd or None,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            with contextlib.suppress(Exception):
                handle.cleanup()
            return InvocationRef(
                backend=self.name,
                runner_id=self._defn.id,
                handle="",
                started_at=_now(),
                prepared=prepared,
                error=f"failed to launch {self._defn.id}: {exc}",
            )
        key = f"pid:{proc.pid}"
        self._procs[key] = proc
        self._handles[key] = handle
        return InvocationRef(
            backend=self.name,
            runner_id=self._defn.id,
            handle=key,
            started_at=_now(),
            prepared=prepared,
        )

    async def collect(self, ref: InvocationRef) -> ProposerResult:
        if not ref.launched:
            return normalise(
                backend=self.name,
                runner_id=self._defn.id,
                ok=False,
                text="",
                baseline=ref.prepared.baseline,
                error=ref.error or "the proposer was never launched",
            )
        proc = self._procs.pop(ref.handle, None)
        handle = self._handles.pop(ref.handle, None)
        if proc is None:
            return normalise(
                backend=self.name,
                runner_id=self._defn.id,
                ok=False,
                text="",
                baseline=ref.prepared.baseline,
                error="the invocation handle is unknown to this backend",
            )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=ref.prepared.timeout_secs
            )
        except asyncio.TimeoutError:
            timed_out = True
            stdout, stderr = b"", b""
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        finally:
            if handle is not None:
                with contextlib.suppress(Exception):
                    handle.cleanup()  # type: ignore[attr-defined]
        text = (stdout or b"").decode("utf-8", "replace")
        err = (stderr or b"").decode("utf-8", "replace")
        rc = proc.returncode if proc.returncode is not None else -1
        if timed_out:
            return normalise(
                backend=self.name,
                runner_id=self._defn.id,
                ok=False,
                text=text,
                baseline=ref.prepared.baseline,
                raw_ref=ref.handle,
                error=f"{self._defn.id} exceeded the {ref.prepared.timeout_secs:.0f}s hard timeout",
            )
        return normalise(
            backend=self.name,
            runner_id=self._defn.id,
            ok=rc == 0,
            text=text or err,
            baseline=ref.prepared.baseline,
            raw_ref=ref.handle,
            error="" if rc == 0 else f"{self._defn.id} exited {rc}: {err.strip()[:400]}",
        )


class SubagentProposerBackend:
    """A fresh PClaw subagent as the second brain — the zero-dependency degradation path."""

    name = SUBAGENT_BACKEND

    def __init__(
        self,
        *,
        timeout_secs: float = 300.0,
        poll_secs: float = 1.0,
        manager: object | None = None,
    ) -> None:
        self._timeout = timeout_secs
        self._poll = poll_secs
        self._manager = manager

    def _resolve_manager(self) -> object | None:
        """The live :class:`SubagentManager`, via the action-service accessor.

        There is no module-global manager in this tree — it is owned by the gateway and handed
        to providers through ``ActionServices``. ``None`` here means no gateway is running, which
        is an honest refusal rather than a spawn into nothing.
        """
        if self._manager is not None:
            return self._manager
        from personalclaw.action_providers.services import get_action_services

        svc = get_action_services()
        return getattr(svc, "subagents", None) if svc is not None else None

    async def prepare(self, brief: HandoffBrief) -> PreparedInvocation:
        baseline = brief.baseline or snapshot_workspace(brief.workspace, paths=brief.files_touched)
        prompt = (
            "You are the second opinion on a task another agent could not finish. You get ONE "
            "turn. Read the brief below, make the smallest change that unblocks it, edit the "
            "files on disk, and report exactly which files you changed.\n\n" + brief.render()
        )
        return PreparedInvocation(
            backend=self.name,
            runner_id=self.name,
            prompt=prompt,
            cwd=brief.workspace,
            # Unnamed stays unnamed: the agent it starts runs in the tier its own runtime is set
            # up with, as an agent any other action starts does (`subagent_session`).
            sandbox=brief.sandbox,
            timeout_secs=self._timeout,
            baseline=baseline,
        )

    async def invoke(self, prepared: PreparedInvocation) -> InvocationRef:
        manager = self._resolve_manager()
        if manager is None:
            return InvocationRef(
                backend=self.name,
                runner_id=self.name,
                handle="",
                started_at=_now(),
                prepared=prepared,
                error="no subagent manager is available (the gateway is not running)",
            )
        info = manager.spawn(  # type: ignore[attr-defined]
            task=prepared.prompt,
            cwd=prepared.cwd,
            approval_mode="auto",
            silent=True,
            sandbox=prepared.sandbox,
        )
        if info is None:
            return InvocationRef(
                backend=self.name,
                runner_id=self.name,
                handle="",
                started_at=_now(),
                prepared=prepared,
                error="subagent capacity is exhausted — no second opinion could be spawned",
            )
        if getattr(info, "error", ""):
            return InvocationRef(
                backend=self.name,
                runner_id=self.name,
                handle="",
                started_at=_now(),
                prepared=prepared,
                error=str(info.error),
            )
        return InvocationRef(
            backend=self.name,
            runner_id=self.name,
            handle=str(getattr(info, "id", "") or ""),
            started_at=_now(),
            prepared=prepared,
        )

    async def collect(self, ref: InvocationRef) -> ProposerResult:
        if not ref.launched:
            return normalise(
                backend=self.name,
                runner_id=self.name,
                ok=False,
                text="",
                baseline=ref.prepared.baseline,
                error=ref.error or "the subagent was never spawned",
            )
        manager = self._resolve_manager()
        if manager is None:
            return normalise(
                backend=self.name,
                runner_id=self.name,
                ok=False,
                text="",
                baseline=ref.prepared.baseline,
                raw_ref=ref.handle,
                error="the subagent manager went away before the result could be collected",
            )
        deadline = time.monotonic() + ref.prepared.timeout_secs
        info = None
        while time.monotonic() < deadline:
            info = manager.get(ref.handle)  # type: ignore[attr-defined]
            if info is None:
                break
            if getattr(info, "done", False):
                break
            await asyncio.sleep(self._poll)
        if info is None:
            return normalise(
                backend=self.name,
                runner_id=self.name,
                ok=False,
                text="",
                baseline=ref.prepared.baseline,
                raw_ref=ref.handle,
                error="the spawned subagent disappeared before it reported",
            )
        if not getattr(info, "done", False):
            return normalise(
                backend=self.name,
                runner_id=self.name,
                ok=False,
                text=str(getattr(info, "result", "") or ""),
                baseline=ref.prepared.baseline,
                raw_ref=ref.handle,
                error=f"the subagent exceeded the {ref.prepared.timeout_secs:.0f}s hard timeout",
            )
        err = str(getattr(info, "error", "") or "")
        return normalise(
            backend=self.name,
            runner_id=self.name,
            ok=not err,
            text=str(getattr(info, "result", "") or ""),
            baseline=ref.prepared.baseline,
            raw_ref=ref.handle,
            error=err,
        )
