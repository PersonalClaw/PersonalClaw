"""Workspace provisioning I/O — the performer for S49's plan and S52's decisions (WF2WOR-4).

S49 shipped `workspace.plan_provisioning` and S52 shipped `worktrees.pending_setup` /
`plan_teardown`, both deliberately pure: they decide, and the caller performs. Neither had a
production caller, which made the whole §4.1 mechanism a declared-but-inert layer — a run's
`workspace:` block was parsed nowhere, so every run silently ran in place regardless of what its
template declared. This module is the performer, and `controller._prepare` is its caller.

Three things are structural rather than incidental:

* **Order is the contract.** preserve → setup → run, and teardown → commit → delete. Both halves
  are enforced HERE rather than trusted to the caller, because the failure of either is silent:
  an `npm install` that runs before `.npmrc` is copied in reaches the wrong registry, and a
  teardown that runs after deletion reports success against a directory that no longer holds the
  services it was meant to stop.
* **The lock lives OUTSIDE the workspace.** A lock file inside a worktree would be deleted by
  the very teardown whose contention it guards, and would be invisible to a second process
  deciding whether the first is alive. It rides `concurrency.lock_path` (the same sanitized-
  prefix-plus-digest convention every other PClaw lock uses) with a PID line, so a stale holder
  self-heals on a `os.kill(pid, 0)` probe rather than wedging the run forever.
* **Setup NEVER blocks the run; teardown NEVER blocks deletion.** Both asymmetries are S49/S52
  contracts (`SetupResult.blocked_run` is False by construction) and both are honored by
  recording the failure and continuing. A setup block that could fail a run would be a liability
  to declare, and a user would stop declaring it.

Every subprocess goes through `effects.run_teardown`'s exact pattern — `shlex.split` (no shell),
the binary resolved up front for a typed not-found, and `sandbox.create_subprocess_limited` with
`PROFILE_TOOL` so the ceiling arrives via the post-exec shim. Never `preexec_fn`: a forked
`preexec_fn` in this threaded gateway wedges the event loop.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.workflows import worktrees
from personalclaw.workflows.workspace import (
    Mode,
    SpecIssue,
    WorkspaceSpec,
    parse_workspace,
    plan_provisioning,
)

logger = logging.getLogger(__name__)

#: Wall-clock cap for one setup or teardown step. Generous because `npm install` on a cold cache
#: is genuinely minutes, bounded because a step that hangs forever would park the run's whole
#: start path — and `_prepare` runs before the first node, so a hang there is a run that never
#: appears to begin.
STEP_TIMEOUT_SECS = 600.0

#: The `run.extra` keys this module owns. Named constants rather than literals at each site: the
#: watchdog's substrate check reads `worktree_path` and a second spelling would give it a live
#: reader of a key nobody writes — which is exactly the defect this atom exists to close.
WORKSPACE_KEY = "workspace"
WORKTREE_PATH_KEY = "worktree_path"
PRESERVED_PATH_KEY = "preserved_workspace_path"


# ── the PID-liveness lock (outside the workspace) ──


@dataclass
class WorkspaceLock:
    """An acquired workspace lock, or an honest refusal.

    `held_by` names the live PID on a refusal so the message can say WHO, not just "busy". A
    contention message with no owner is one a user cannot act on.
    """

    acquired: bool
    path: str = ""
    held_by: int = 0
    reason: str = ""
    #: The open descriptor. Kept on the dataclass so the flock outlives this function's frame —
    #: closing the fd releases the lock, so a lock whose handle was garbage-collected would be a
    #: lock that silently opened the door mid-run.
    _fd: Any = field(default=None, repr=False, compare=False)

    def release(self) -> None:
        """Drop the lock. Idempotent, and safe to call on a refusal."""
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            logger.debug("workspace lock unlock failed for %s", self.path, exc_info=True)
        try:
            self._fd.close()
        except OSError:
            pass
        self._fd = None


def lock_key(run_id: str, name: str = "") -> str:
    """The lock's job key. A NAMED workspace locks on its name, an unnamed one on the run.

    That distinction is the whole reason named workspaces need a lock at all: two runs sharing one
    named workspace are the contention case, while two runs with per-run workspaces cannot collide
    by construction. Keying both on the run id would make the shared case lockless.
    """
    return f"workflow-workspace:{name}" if name else f"workflow-run-workspace:{run_id}"


def pid_alive(pid: int) -> bool:
    """Whether a recorded lock holder is still running.

    `signal 0` is the established PClaw probe (`session_pid.py`, `subagent.py`,
    `resilience/doctor.py`). `EPERM` counts as ALIVE: a process we may not signal is still a
    process, and reading it as dead would let us steal a live workspace.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def acquire_workspace_lock(run_id: str, *, name: str = "") -> WorkspaceLock:
    """Take the workspace lock, fail-fast on live contention, self-heal on a stale PID.

    Two mechanisms, deliberately, because each covers the other's blind spot:

    * `fcntl.flock(LOCK_NB)` is the authority. It is released BY THE OS when the holder dies, so
      a crashed gateway can never leave the lock itself stuck — the property `concurrency.py`
      chose it for.
    * The PID line inside the file is the EXPLANATION. flock tells us "someone holds it" and
      nothing else; a refusal that cannot name the holder is one a user cannot act on. It also
      covers the case flock cannot: a file whose holder died leaves the recorded pid behind, and
      probing it is how the message distinguishes "another run is working here" from "a previous
      run died here" — which are different problems even though the lock resolves both.

    Fail-FAST, never wait in line: two runs in one worktree would interleave writes to the same
    files, and queueing the second one behind a run that may take an hour is worse than telling
    it now.
    """
    from personalclaw.concurrency import lock_path

    path = lock_path(lock_key(run_id, name))
    try:
        fd = path.open("a+")
    except OSError as exc:
        # A lock we cannot even open is reported as NOT acquired rather than ignored: proceeding
        # unlocked would be the one outcome the lock exists to prevent.
        return WorkspaceLock(False, path=str(path), reason=f"could not open the lock file: {exc}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = _recorded_pid(fd)
        fd.close()
        if holder and pid_alive(holder):
            return WorkspaceLock(
                False,
                path=str(path),
                held_by=holder,
                reason=f"another live run (pid {holder}) holds this workspace",
            )
        # flock refused but the recorded holder is gone. That is NOT a contradiction we may
        # resolve by stealing: flock is released on death, so a refusal here means a live process
        # holds it and simply never recorded (or already overwrote) its pid. Refusing is the
        # fail-closed reading — two runs writing one worktree is the corruption this prevents.
        return WorkspaceLock(
            False,
            path=str(path),
            reason="the workspace lock is held by another process",
        )
    # Held. Record OUR pid, replacing whatever a dead holder left — the self-healing half.
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(f"{os.getpid()}\n")
        fd.flush()
    except OSError:
        logger.debug("could not record the pid in %s", path, exc_info=True)
    return WorkspaceLock(True, path=str(path), held_by=os.getpid(), _fd=fd)


def _recorded_pid(fd: Any) -> int:
    """The PID a previous holder wrote, or 0. Never raises — a garbled lock file is not fatal."""
    try:
        fd.seek(0)
        first = (fd.read(64) or "").strip().splitlines()
        return int(first[0]) if first else 0
    except (OSError, ValueError, IndexError):
        return 0


# ── subprocess execution (setup/teardown steps) ──


async def run_step(
    command: str,
    cwd: str | Path,
    *,
    env: dict[str, str] | None = None,
    runner: Any = None,
    timeout: float = STEP_TIMEOUT_SECS,
) -> tuple[bool, str]:
    """Run one setup/teardown step in `cwd`. Returns `(ok, detail)`.

    Deliberately the same shape and the same guarantees as `effects.run_teardown`, which is the
    established pattern for executing an author-declared command: `shlex.split` with NO shell (a
    setup block is agent-influenced text and must not be a quoting-injection surface), the binary
    resolved up front so a typo stays a TYPED "not found" rather than a generic non-zero exit
    from the ceiling shim's own exec, and the spawn through `create_subprocess_limited` so the
    `tool` ceiling arrives post-exec.

    `runner` is the injection seam `EngineServices.teardown_runner` already established, so a
    controller test never runs a real subprocess.
    """
    if runner is not None:
        try:
            return await runner(command, str(cwd))
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:500]

    import asyncio

    argv = shlex.split(command or "")
    if not argv:
        return False, "empty command"
    binary = argv[0]
    found = os.path.exists(binary) if os.path.sep in binary else bool(shutil.which(binary))
    if not found:
        return False, f"command not found: {binary}"
    spawn_env = {**os.environ, **(env or {})}
    from personalclaw.sandbox import PROFILE_TOOL, create_subprocess_limited

    try:
        proc = await create_subprocess_limited(
            *argv,
            profile=PROFILE_TOOL,
            cwd=str(cwd),
            env=spawn_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"command not found: {argv[0]}"
    except OSError as exc:
        return False, f"could not start: {exc}"[:500]
    if proc.returncode == 0:
        return True, (out or b"").decode("utf-8", "replace")[:2000]
    detail = (err or b"").decode("utf-8", "replace")[:2000]
    return False, f"exited {proc.returncode}: {detail}"


# ── provisioning ──


@dataclass
class Provisioned:
    """What provisioning actually produced, for the run record and the cockpit.

    `ok=False` with `fatal=True` is a REFUSAL — the spec declared something that cannot be
    honored (an unknown mode, a greedy preserve pattern), and running anyway would run in a mode
    nobody chose. Everything else degrades: a worktree that could not be created falls back to
    the project workspace WITH the reason recorded, because refusing a run because git is
    unavailable would make `mode: worktree` unusable on a non-repo workspace.
    """

    mode: Mode = Mode.SCRATCH
    path: str = ""
    branch: str = ""
    isolated: bool = False
    preserved: list[str] = field(default_factory=list)
    preserve_skipped: list[str] = field(default_factory=list)
    setup_ran: list[str] = field(default_factory=list)
    setup_skipped: list[str] = field(default_factory=list)
    setup_failed: list[str] = field(default_factory=list)
    issues: list[SpecIssue] = field(default_factory=list)
    degraded_reason: str = ""
    ok: bool = True
    #: Live container name + the backend binary that owns it (WF2WOR-12). Empty when the
    #: run is not containerized — including the degraded asked-for-container-got-scratch
    #: case, which is how a reader tells the two apart.
    container_id: str = ""
    container_backend: str = ""

    @property
    def fatal(self) -> bool:
        return any(i.fatal for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "path": self.path,
            "branch": self.branch,
            "isolated": self.isolated,
            "preserved": list(self.preserved),
            "preserve_skipped": list(self.preserve_skipped),
            "setup": {
                "ran": list(self.setup_ran),
                "skipped": list(self.setup_skipped),
                "failed": list(self.setup_failed),
                # The S52 contract, stated on the wire so a cockpit never has to infer it: a
                # failed setup step is information, not a run outcome.
                "blocked_run": False,
            },
            "issues": [i.to_dict() for i in self.issues],
            "degraded_reason": self.degraded_reason,
            "ok": self.ok,
            "container_id": self.container_id,
            "container_backend": self.container_backend,
        }


def declares_workspace(spec: dict[str, Any]) -> bool:
    """Whether a spec asks for a managed workspace at all.

    **A spec with no `workspace:` block provisions NOTHING.** §4.1's framing is that the
    workspace is a DECLARATION rather than a convention, and the declaration is what opts a run
    in. Provisioning every run into a scratch dir instead would (a) create an unused directory
    under every run and (b) — measured, by
    `test_an_adopted_run_resumes_without_re_running_finished_work` — make every stale RUNNING run
    look like an isolated substrate to the boot sweep, so a crash-survivor whose journal-backed
    work is perfectly resumable would be SUSPENDED and await a manual Resume instead of being
    adopted. The sweep's DEVIATION note is explicit that inline runs stay owned by adoption; a
    default-on workspace would have silently taken every run out of that path.
    """
    return isinstance(spec, dict) and isinstance(spec.get(WORKSPACE_KEY), dict)


def resolve_spec(
    spec: dict[str, Any], *, default_mode: str = ""
) -> tuple[WorkspaceSpec, list[SpecIssue]]:
    """Read a workflow spec's top-level `workspace:` block, applying the config default.

    The default fills in the MODE for a block that declared one of the other fields — a template
    that says `{preserve_patterns: [...], setup: "npm ci"}` and nothing about isolation. A
    DECLARED mode always wins: a config knob that overrode an explicit declaration would make a
    template's own isolation statement advisory, and §4.1's whole point is that the declaration
    is binding.

    An unparseable `workspace_default_mode` falls back to `scratch` rather than to `in_place`:
    S49's ruling is that `in_place` is never a default, and a config typo must not be the thing
    that puts a destructive step against the user's real tree.
    """
    raw = spec.get(WORKSPACE_KEY) if isinstance(spec, dict) else None
    parsed, issues = parse_workspace(raw)
    declared_mode = isinstance(raw, dict) and str(raw.get("mode", "") or "").strip()
    if not declared_mode and default_mode:
        try:
            fallback = Mode(str(default_mode).strip().lower())
        except ValueError:
            fallback = Mode.SCRATCH
            issues.append(
                SpecIssue(
                    "unknown_default_mode",
                    f"workflows.workspace_default_mode is {default_mode!r}, which is not a "
                    f"workspace mode; using {Mode.SCRATCH.value}",
                )
            )
        parsed.mode = fallback
    return parsed, issues


async def provision(
    spec: WorkspaceSpec,
    *,
    run_id: str,
    project_id: str = "",
    workspace_dir: str = "",
    run_dir: Path | None = None,
    issues: list[SpecIssue] | None = None,
    runner: Any = None,
    from_snapshot: str = "",
) -> Provisioned:
    """Stand the workspace up: create → preserve → setup, in that order.

    The order is asserted by the code path, not by a comment, because both inversions are silent.
    `preserve` before `setup` is S49's measured rule; `setup` before the first node is what makes
    a marker-guarded block idempotent across resume (this whole function runs again on resume,
    and `pending_setup` is what makes the second pass cheap).

    A FATAL spec issue returns immediately with nothing provisioned. That is the one hard refusal
    here: `parse_workspace` marks an unknown mode and a greedy preserve pattern fatal precisely
    because honoring them is impossible, and running anyway would run in a mode nobody chose —
    the ignored-fatal-issue shape.
    """
    plan = plan_provisioning(spec, issues=issues)
    out = Provisioned(mode=spec.mode, isolated=spec.isolated, issues=list(plan.issues))
    if not plan.ok:
        out.ok = False
        out.degraded_reason = "; ".join(i.message for i in out.issues if i.fatal)
        return out

    root, branch, degraded = _create_workspace(
        spec,
        run_id=run_id,
        project_id=project_id,
        workspace_dir=workspace_dir,
        run_dir=run_dir,
    )
    out.path, out.branch, out.degraded_reason = root, branch, degraded
    if spec.mode is Mode.CONTAINER and root:
        await _provision_container(spec, out, run_id=run_id, from_snapshot=from_snapshot)
        degraded = out.degraded_reason
    if degraded:
        # An isolated mode that could not isolate is reported as NOT isolated. The board's
        # suspend/resume decision reads this: claiming isolation we do not have would make the
        # boot sweep offer a Resume into a substrate that was never separate from the process.
        out.isolated = False
    if not root:
        out.ok = False
        return out

    # preserve → setup. Both only for an isolated workspace: an in-place run is already IN the
    # tree the patterns would copy from, so copying would be a file onto itself.
    if out.isolated and spec.preserve_patterns and workspace_dir:
        copied = worktrees.preserve(workspace_dir, root, spec.preserve_patterns)
        out.preserved, out.preserve_skipped = copied.copied, copied.skipped

    if spec.setup:
        await _run_setup(spec, root, out, runner=runner)
    return out


async def _provision_container(
    spec: WorkspaceSpec, out: Provisioned, *, run_id: str, from_snapshot: str = ""
) -> None:
    """Wrap the scratch dir in the declared container, or record why not (WF2WOR-12 §4.4).

    Every non-success is a DEGRADATION, never a refusal: the pre-container behaviour — an
    isolated scratch dir with the reason on the run record — is what a template that declared
    `mode: container` gets on a machine with no backend, exactly as it did before this atom
    landed. The run stays startable; the cockpit says why it is not containerized.
    """
    from personalclaw.workflows.container_env import detect_backend, parse_manifest

    manifest, manifest_issues = parse_manifest(spec.container or None)
    fatal = [i for i in manifest_issues if i.fatal]
    if fatal or not manifest.declared:
        # parse_workspace already surfaced these at save time; runs stamped before that, or
        # specs written directly to disk, land here — same message, later surface.
        out.degraded_reason = (
            "; ".join(i.message for i in fatal)
            or "container mode declared with no environment manifest; using an isolated "
            "scratch dir"
        )
        return
    backend = detect_backend()
    if backend is None:
        out.degraded_reason = (
            "no container backend available (docker, nerdctl, or Apple's container CLI); "
            "using an isolated scratch dir"
        )
        return
    result = await backend.provision(
        manifest,
        workspace_dir=out.path,
        run_id=run_id,
        from_snapshot=from_snapshot,
        context_dir=out.path,
    )
    if not result.ok:
        out.degraded_reason = f"container provisioning failed ({backend.name}): {result.reason}"
        return
    out.container_id = result.value
    out.container_backend = backend.name


async def snapshot_workspace(run: Any, *, tag_suffix: str) -> str:
    """Commit the run's live container to an image ref — the checkpoint's workspace anchor.

    Returns "" when the run has no container or its backend cannot snapshot (Apple's CLI),
    which the checkpoint stores as "no anchor": fork then provisions fresh, the pre-container
    behaviour. Never raises — an anchor is an enhancement to a checkpoint, not a condition
    for taking one.
    """
    state = workspace_state(run)
    container_id = str(state.get("container_id", "") or "")
    binary = str(state.get("container_backend", "") or "")
    if not container_id or not binary:
        return ""
    from personalclaw.workflows.container_env import CliContainerBackend

    backend = CliContainerBackend(binary)
    if not backend.can_snapshot or not backend.available():
        return ""
    run_id = str(getattr(run, "id", "") or "")
    result = await backend.snapshot(container_id, tag=f"pclaw/run-{run_id}:{tag_suffix}")
    if not result.ok:
        logger.warning("run %s: workspace snapshot failed: %s", run_id, result.reason)
        return ""
    return result.value


def _create_workspace(
    spec: WorkspaceSpec,
    *,
    run_id: str,
    project_id: str,
    workspace_dir: str,
    run_dir: Path | None,
) -> tuple[str, str, str]:
    """Materialize the workspace directory. Returns `(path, branch, degraded_reason)`.

    Worktree mode goes through `loop.worktree.add_worktree` rather than a second git
    implementation — measured idempotent (an existing id returns the SAME path), which is what
    makes resume free rather than something to implement here.
    """
    if spec.mode is Mode.IN_PLACE:
        # No isolation by declaration. Returns the real tree, which is the honest answer: an
        # in_place run's workspace IS the project workspace, and inventing a path would hide
        # that from every surface that shows where a run worked.
        return workspace_dir, "", ""

    if spec.mode is Mode.CONTAINER:
        # The scratch dir is the container's HOST-SIDE workspace — the directory mounted at
        # container_env.WORKSPACE_MOUNT. Whether a container actually wraps it is decided in
        # `_provision_container` (backend detection is async and belongs to `provision()`);
        # no degradation is recorded here so a successful container run reads clean.
        return _scratch_dir(run_id, spec.name, run_dir), "", ""

    if spec.mode is Mode.WORKTREE:
        return _create_worktree(run_id, project_id=project_id, workspace_dir=workspace_dir)

    return _scratch_dir(run_id, spec.name, run_dir), "", ""


def _create_worktree(run_id: str, *, project_id: str, workspace_dir: str) -> tuple[str, str, str]:
    """A per-run git worktree under the project's own `worktrees/` dir.

    Every failure degrades to scratch WITH a reason rather than refusing the run. A user whose
    workspace is not a git repo declared `worktree` because they wanted isolation, and the
    isolation is deliverable without git — refusing would trade the property they asked for
    against the mechanism they did not.
    """
    from personalclaw.loop import worktree as loop_worktree

    if not workspace_dir:
        return _scratch_dir(run_id, "", None), "", "no workspace is bound to this run's project"
    if not loop_worktree.git_available():
        return _scratch_dir(run_id, "", None), "", "git is not on PATH; using a scratch dir"
    if not loop_worktree.is_git_repo(workspace_dir):
        return (
            _scratch_dir(run_id, "", None),
            "",
            "the bound workspace is not a git repo; using a scratch dir",
        )
    if not loop_worktree.ensure_base_commit(workspace_dir):
        return (
            _scratch_dir(run_id, "", None),
            "",
            "the repo has no commit to branch from; using a scratch dir",
        )
    path = loop_worktree.add_worktree(workspace_dir, run_id, project_id)
    if not path:
        return _scratch_dir(run_id, "", None), "", "git could not create a worktree"

    # `add_worktree` names the branch `pclaw/task-<id>`; a RUN's branch is `pclaw/run-<id>`
    # (S52's `run_branch`), because a user reading `git branch` should be able to tell which
    # subsystem made a branch without looking it up. Renamed rather than re-created: measured
    # that `git branch -m` on a branch checked out IN a live worktree succeeds and the worktree
    # follows it, so this costs nothing and avoids a second `worktree add`. `-M` (force) because
    # a retried run finds its own previous run-branch already there, and `-m` fails on that
    # collision with fatal 128 — which would leave the run on a task-shaped branch.
    branch = worktrees.run_branch(run_id)
    task_branch = loop_worktree.branch_name(run_id)
    if task_branch != branch:
        rc, _out = loop_worktree._git(workspace_dir, "branch", "-M", task_branch, branch)
        if rc != 0:
            # Keep the worktree; report the branch we actually have. A wrong branch name in the
            # record would send both reintegration verbs at a ref that does not exist.
            branch = task_branch
    return path, branch, ""


def _scratch_dir(run_id: str, name: str, run_dir: Path | None) -> str:
    """A per-run (or per-named-workspace) scratch directory under the run's own dir.

    Under the RUN dir on purpose: retention already sweeps it (`watchdog._sweep_run_dir`), so a
    scratch workspace cannot outlive the run that made it and become an orphan nobody can find.
    A NAMED workspace is the exception — it is shared across runs by definition, so it lives
    beside the runs rather than inside one.
    """
    from personalclaw.workflows import store

    if name:
        safe = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in name)[:64] or "named"
        root = store.workflows_dir() / "workspaces" / safe
    else:
        base = run_dir if run_dir is not None else store.run_dir(run_id)
        root = Path(base) / "workspace"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("could not create the scratch workspace %s", root, exc_info=True)
        return ""
    return str(root)


async def _run_setup(
    spec: WorkspaceSpec, root: str, out: Provisioned, *, runner: Any = None
) -> None:
    """Execute the pending setup steps, marking each done as it succeeds.

    Marker-guarded per step and content-addressed (S49's `setup_marker`), so an EDITED step
    re-runs while an unchanged one is skipped — the property that makes setup safe to re-run on
    every resume, which is the contract.

    A step is marked done only on SUCCESS. Marking a failure done would make the failure
    permanent across every subsequent resume, and the whole reason setup re-runs is that the
    condition it failed on (an offline registry, a missing binary) is usually transient.
    """
    to_run, done = worktrees.pending_setup(root, spec.setup)
    out.setup_skipped = list(done)
    env = worktrees.worktree_env(root)
    for step in to_run:
        ok, detail = await run_step(step, root, env=env, runner=runner)
        if ok:
            out.setup_ran.append(step)
            worktrees.mark_setup_done(root, step)
        else:
            # Recorded, never fatal. `SetupResult.blocked_run` is False by contract: refusing to
            # run the workflow because `npm install` failed would make declaring setup a
            # liability and a user would stop declaring it.
            out.setup_failed.append(f"{step}: {detail}"[:500])


# ── teardown ──


@dataclass
class TornDown:
    """What teardown actually did, in order. `steps` is the audit trail a deletion leaves behind.

    Returned rather than logged because both call sites (run deletion, retention expiry) delete
    the workspace right after — so this is the only record that the services were stopped and the
    work was committed before the directory went away.
    """

    ran: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    committed: bool = False
    removed: bool = False
    branch: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": list(self.ran),
            "failed": list(self.failed),
            "committed": self.committed,
            "removed": self.removed,
            "branch": self.branch,
        }


async def teardown(
    run: Any,
    *,
    workspace_dir: str = "",
    keep_open: bool = False,
    runner: Any = None,
) -> TornDown:
    """Run teardown, commit, then remove — in that order, BEFORE the workspace is deleted.

    The order is the contract and it is why this function exists at all: teardown's job is to
    stop services and sync work out, and both need the directory to still be there. A caller
    that deleted first would run its own teardown against nothing and report success.

    `keep_open` still runs teardown and still commits — keeping the directory is not keeping the
    processes, and a `docker compose` left up because the user wanted to inspect the files is a
    resource leak the override never asked for.

    Never raises. Both call sites are deletion paths, and a teardown failure must not leave a run
    row that cannot be deleted — that would be a run visible forever with no way to remove it.
    """
    state = workspace_state(run)
    out = TornDown(branch=str(state.get("branch", "") or ""))
    path = str(state.get("path", "") or "")
    spec_teardown = str(state.get("teardown", "") or "")
    isolated = bool(state.get("isolated", False))
    alive = bool(path) and Path(path).is_dir()

    container_id = str(state.get("container_id", "") or "")
    container_binary = str(state.get("container_backend", "") or "")
    if container_id and container_binary:
        # BEFORE any directory work: the container mounts the workspace dir, and removing the
        # dir under a live container is the deletion-first inversion this function's docstring
        # forbids for teardown commands. `--force` stops and removes in one verb; failure is
        # recorded, not raised — a gone backend must not leave an undeletable run row.
        from personalclaw.workflows.container_env import CliContainerBackend

        removal = await CliContainerBackend(container_binary).remove(container_id)
        (out.ran if removal.ok else out.failed).append(
            f"remove container {container_id}"
            if removal.ok
            else f"remove container {container_id}: {removal.reason}"[:500]
        )

    plan = worktrees.plan_teardown(
        teardown=spec_teardown, ephemeral=isolated and not state.get("name"), keep_open=keep_open
    )
    if alive and spec_teardown:
        for step in worktrees.setup_steps(spec_teardown):
            ok, detail = await run_step(step, path, runner=runner)
            (out.ran if ok else out.failed).append(step if ok else f"{step}: {detail}"[:500])

    if alive and plan.commits_first:
        out.committed = _commit_outstanding(
            path, out.branch, preserved=list(state.get("preserved") or [])
        )

    if alive and plan.deletes:
        out.removed = _remove_workspace(
            path,
            run_id=str(getattr(run, "id", "") or ""),
            project_id=str(getattr(run, "project_id", "") or ""),
            workspace_dir=workspace_dir,
            isolated=isolated,
        )
    return out


def _commit_outstanding(path: str, branch: str, *, preserved: list[str]) -> bool:
    """Commit the worktree's outstanding work to its own branch. True when a commit was made.

    Preserved files and the engine's own setup markers are EXCLUDED via git pathspecs. Measured:
    a plain `git add -A` committed the copied `.env` and `.pclaw-setup/` into the run branch, so
    the durable record of the run's work would carry the user's local credentials into git
    history — and both reintegration verbs would then offer to apply them. The exclusion runs at
    the `add`, not at review time, because a review filter cannot un-commit a secret.

    An empty commit is not an error: `git commit` exits 1 with "nothing added to commit"
    (measured), which means "there was nothing to save", not "saving failed".
    """
    from personalclaw.loop import worktree as loop_worktree

    excludes = [
        f":(exclude){worktrees.SETUP_MARKER_DIR}",
        f":(exclude){worktrees.SETUP_MARKER_DIR}/**",
    ]
    excludes += [f":(exclude){p}" for p in preserved if p]
    rc, _ = loop_worktree._git(path, "add", "-A", "--", ".", *excludes)
    if rc != 0:
        return False
    rc, out = loop_worktree._git(
        path,
        "-c",
        "user.name=PersonalClaw",
        "-c",
        "user.email=code@personalclaw.local",
        "commit",
        "-q",
        "-m",
        f"workflow run: work on {branch or 'the run branch'}",
    )
    if rc == 0:
        return True
    if "nothing added to commit" in out or "nothing to commit" in out:
        return False
    logger.debug("could not commit outstanding work in %s: %s", path, out.strip()[:200])
    return False


def _remove_workspace(
    path: str, *, run_id: str, project_id: str, workspace_dir: str, isolated: bool
) -> bool:
    """Remove the workspace directory. Worktrees go through git; scratch dirs go through rmtree.

    A worktree removed with `rmtree` alone leaves a stale registration in the repo's
    `worktrees/` metadata, and the next `worktree add` for the same id then needs `-f` to
    succeed — so the git path is used where it applies. The BRANCH is deliberately kept: it is
    what makes an ephemeral workspace's record reference git rather than a deleted directory,
    which is the whole reason the commit above happened.
    """
    if not isolated:
        # An in_place workspace is the user's real tree. Removing it would be the deleted-
        # real-model incident, exactly.
        return False
    target = Path(path)
    if workspace_dir and run_id:
        from personalclaw.loop import worktree as loop_worktree

        try:
            expected = loop_worktree.worktree_path(workspace_dir, run_id, project_id)
        except ValueError:
            expected = ""
        if expected and Path(expected) == target:
            # `remove_worktree` also deletes `pclaw/task-<id>`, which our rename freed — so the
            # run branch survives it. Verified by the rename measurement: after `branch -M`, the
            # task-shaped name no longer exists and the `-D` is a harmless no-op.
            loop_worktree.remove_worktree(workspace_dir, run_id, project_id)
            return not target.is_dir()
    try:
        shutil.rmtree(target, ignore_errors=True)
    except OSError:
        logger.debug("could not remove the workspace %s", target, exc_info=True)
    return not target.is_dir()


# ── the run record ──


def stamp_run(run: Any, provisioned: Provisioned, spec: WorkspaceSpec) -> None:
    """Write the provisioning result onto the run record.

    `worktree_path` is written for EVERY isolated mode, not only `worktree`. The watchdog's
    substrate check reads that one key to decide suspend-vs-abort, and a scratch workspace that
    survived a restart is just as recoverable as a git one — keying the decision on the mode name
    would abort recoverable work for the commoner mode.

    The env block goes through `WorkspaceSpec.to_dict`, which serializes PRESENCE only. A run
    record is read by the cockpit, the export archive and a bug report, and it must not be the
    thing that leaks a token.
    """
    extra = getattr(run, "extra", None)
    if extra is None:
        return
    block = spec.to_dict()
    block.update(provisioned.to_dict())
    # The teardown COMMAND has to survive on the record: deletion happens long after the run,
    # potentially in a different process, and re-reading the spec at deletion time would break
    # for a def that was edited or removed in between.
    block["teardown"] = spec.teardown
    block["name"] = spec.name
    extra[WORKSPACE_KEY] = block
    if provisioned.isolated and provisioned.path:
        extra[WORKTREE_PATH_KEY] = provisioned.path
    else:
        # Cleared rather than left stale: a run that degraded out of isolation on resume would
        # otherwise keep a path the sweep reads as a live substrate.
        extra.pop(WORKTREE_PATH_KEY, None)


def workspace_state(run: Any) -> dict[str, Any]:
    """The workspace block off a run record, or an empty dict. Never raises.

    One reader for the block so the key name lives in one place — `worktree_path` already
    demonstrated what a second spelling costs (a live reader of a key nothing writes).
    """
    extra = getattr(run, "extra", None) or {}
    block = extra.get(WORKSPACE_KEY) if isinstance(extra, dict) else None
    if isinstance(block, dict):
        return block
    path = str(extra.get(WORKTREE_PATH_KEY, "") or "") if isinstance(extra, dict) else ""
    # A run stamped before this atom shipped has the path and nothing else. Reporting it as an
    # isolated workspace with no teardown is the truthful reading of what we know.
    return {"path": path, "isolated": bool(path)} if path else {}


def inspect_run(run: Any) -> worktrees.WorktreeState:
    """The live worktree state for one run: alive, dirty, changed files, preserved path.

    Shells out for the porcelain ONCE, here, so `inspect_worktree` stays testable without a repo
    (S52's split) and the whole system has one place that asks git what changed.
    """
    run_id = str(getattr(run, "id", "") or "")
    state = workspace_state(run)
    path = str(state.get("path", "") or "")
    if not path or not Path(path).is_dir():
        return worktrees.inspect_worktree(run_id, path or "")
    from personalclaw.loop import worktree as loop_worktree

    rc, out = loop_worktree._git(path, "status", "--porcelain")
    porcelain = out if rc == 0 else ""
    return worktrees.inspect_worktree(
        run_id, path, porcelain=porcelain, preserved=list(state.get("preserved") or [])
    )


def stamp_preserved_path(run: Any, state: worktrees.WorktreeState) -> bool:
    """Record `preserved_workspace_path` on the run. True when the record changed.

    Non-empty only when the workspace is alive AND dirty (S52's rule): pointing a user at a
    clean directory is a false lead, and a record carrying a path that means nothing is worse
    than one carrying no path.
    """
    extra = getattr(run, "extra", None)
    if extra is None:
        return False
    want = state.preserved_workspace_path
    if want:
        changed = extra.get(PRESERVED_PATH_KEY) != want
        extra[PRESERVED_PATH_KEY] = want
        return changed
    return extra.pop(PRESERVED_PATH_KEY, None) is not None


def reintegration(run: Any, *, workspace_dir: str = "") -> dict[str, Any]:
    """The cockpit's diff panel + the two reintegration verbs for one run.

    Reintegration is OFFERED, never performed — the plan's explicit ruling. A run that
    auto-merged would decide for the user, and the decision is the whole reason the work was
    isolated.

    Conflicts are computed with `git merge-tree --write-tree`, which reports them WITHOUT
    touching either tree (measured: exit 1 plus the conflicted paths on a real conflict, exit 0
    otherwise). A probe that used a real merge and aborted would leave the user's index dirty for
    the duration of a read.
    """
    state = inspect_run(run)
    conflicts = _conflicts(run, state, workspace_dir=workspace_dir)
    offer = worktrees.reintegration_offer(
        state.run_id, branch=state.branch, changed=len(state.changed), conflicts=conflicts
    )
    return {
        "run_id": state.run_id,
        "workspace": state.to_dict(),
        "reintegration": offer,
        # The workspace declaration, so the panel can say what mode a run used and why its
        # diff is empty (in_place has no diff to show — it worked in the tree directly).
        "declared": {
            k: v
            for k, v in workspace_state(run).items()
            if k in ("mode", "isolated", "name", "degraded_reason", "setup", "issues")
        },
    }


def _conflicts(run: Any, state: worktrees.WorktreeState, *, workspace_dir: str) -> list[str]:
    """Files whose merge into the user's tree would conflict. Empty when nothing can be checked.

    Best-effort by design: an unanswerable probe returns NO conflicts rather than a guess, and
    the offer's `safe` flags degrade toward "checkout is safer" on their own. Reporting a
    fabricated conflict would push a user to the wrong verb.
    """
    if not workspace_dir or not state.branch:
        return []
    from personalclaw.loop import worktree as loop_worktree

    if not loop_worktree.is_git_repo(workspace_dir):
        return []
    rc, out = loop_worktree._git(workspace_dir, "merge-tree", "--write-tree", "HEAD", state.branch)
    if rc == 0:
        return []
    paths: list[str] = []
    for line in (out or "").splitlines():
        # The conflicted-file stanza is `<mode> <oid> <stage>\t<path>`; stage 1/2/3 all name the
        # SAME path, so the set is what matters rather than the count.
        if "\t" in line and line[:1].isdigit():
            candidate = line.split("\t", 1)[1].strip()
            if candidate and candidate not in paths:
                paths.append(candidate)
    return paths
