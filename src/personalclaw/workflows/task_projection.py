"""Projecting settled nodes into Tasks.

`materialize` owns every decision — which nodes earn a task, the dedup keys, the fan-out cap — so
this module is the plumbing: it asks, schedules the task-provider write on the running loop, and
runs a projected node's done-criterion to emit `task_verified`. Every failure is swallowed: a board
row must never fail a node whose work already succeeded.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


def _projected_tasks(ctl: RunController) -> list[Any]:
    """What this run has already projected, for `plan_materialization`'s dedup.

    Held in memory on the controller rather than read from the task store per node: the store is
    per-entity JSON, so a read per settled node is one file scan per node, and the controller is
    the single writer for its own run. A restart re-reads from the store via the projection
    rebuild (full recompute is the normal path), so nothing is lost by not persisting the
    cache itself.
    """
    return [type("_T", (), {"workflow_binding": b})() for b in ctl._projected]


def _schedule_verification(ctl: RunController, spec: Any, path: str, node_id: str) -> None:
    """Run a projected node's done-criterion and emit `task_verified`.

    Scheduled, not inline: a criterion is a shell command or a file read (`pytest -q` is the
    canonical authoring shape), and running it inside the sync settle path would block the whole
    tick on someone else's test suite.

    A node with NO criterion schedules nothing. `Task.can_mark_complete`'s rule is that a task
    with no exit criteria is freely completable, and emitting a `task_verified(passed=True)` for
    a node nobody wrote a check for would manufacture evidence that does not exist.

    The emptiness test asks the PARSER, not truthiness. Measured: `"   "` is truthy, so a
    whitespace-only criterion passed a `if not criterion` guard and then parsed to zero checks —
    which the evaluator correctly reports as UNRUNNABLE, so the node showed a scary "could not
    verify" for a field its author had effectively left blank.
    """
    criterion = getattr(spec, "done_criterion", "")
    from personalclaw.workflows import verified_done as _vd

    checks, problems = _vd.parse_criterion(criterion)
    if not checks and not problems:
        return

    async def _verify_and_emit() -> None:
        passed = await _run_criterion(ctl, spec.done_criterion)
        ctl.publish_task_verified(
            path,
            node_id,
            task_id="",
            # NOT `bool(passed)`: `None` means the check could not run, and collapsing it to
            # False reports a failure that never happened.
            passed=passed,
            criterion=str(spec.done_criterion)[:200],
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_verify_and_emit())
        return
    handle = loop.create_task(_verify_and_emit())
    ctl._projection_writes.add(handle)
    handle.add_done_callback(ctl._projection_writes.discard)


async def _run_criterion(ctl: RunController, raw: Any) -> bool | None:
    """Evaluate a done-criterion to the TRISTATE `verified_done` expects.

    `None` (could not run) is preserved all the way out, never collapsed to False. The whole
    point of the tristate is that a missing binary is not a failing test: reporting "the check
    failed" for a criterion that never executed sends the user to debug their code when the
    problem is their environment, and the two project to different blocked kinds for exactly
    that reason.
    """
    from personalclaw.workflows import verified_done as _vd

    checks, problems = _vd.parse_criterion(raw)
    if problems or not checks:
        # An unparseable criterion is UNRUNNABLE, not failed. The author wrote something the
        # engine could not read, which is a different problem from the work being wrong.
        return None
    verdict = _vd.Verdict()
    for check in checks:
        if check.kind is _vd.CheckKind.COMMAND:
            from personalclaw.loop.gates import run_verify_command

            outcome = await run_verify_command(check.command, None, label=f"criterion:{ctl.run.id}")
            verdict.results.append(
                _vd.CheckResult(
                    kind=check.kind.value,
                    passed=outcome,
                    weight=check.weight,
                    detail=check.command[:120],
                )
            )
        else:
            verdict.results.append(_vd.evaluate_file_phrase(check, _read_criterion_file))
    return verdict.passed


def _read_criterion_file(path: str) -> str | None:
    """Read a file for a `file_phrase` check, or None when it cannot be read.

    None rather than "" — `evaluate_file_phrase` treats an unreadable file as UNRUNNABLE, and an
    empty string would read as "the file exists and the phrase is absent", which is a
    claim about
    content nobody read.
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _schedule_task_write(ctl: RunController, spec: Any, path: str, node_id: str) -> None:
    """Schedule the projected Task write on the running loop, then emit the event.

    The settle path (`_apply`) is SYNC but runs inside the async tick, and the task provider's
    `create_task` is async — so this follows the controller's established idiom for that shape
    (`asyncio.create_task`, as the tick loop and node dispatch already do) rather than blocking
    the tick on a filesystem write.

    The EVENT fires from the write's completion, not before it, so `task_id` is the real id. An
    event with an empty id would tell a board to render a row it cannot open.

    No running loop (a synchronous unit test, a replay) still projects: the write runs
    inline via
    `asyncio.run`, because a projection that only worked inside a live gateway would be
    untestable exactly where it matters.
    """

    async def _write_and_emit() -> None:
        task_id = await _write_projected_task(ctl, spec)
        ctl.publish_task_materialized(
            path,
            node_id,
            task_id=task_id,
            fingerprint=spec.binding.fingerprint,
            refreshed=False,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_write_and_emit())
        return
    handle = loop.create_task(_write_and_emit())
    # Tracked so a controller teardown does not leave the write as an orphaned task warning, and
    # so a test can await settlement rather than sleeping.
    ctl._projection_writes.add(handle)
    handle.add_done_callback(ctl._projection_writes.discard)


async def _write_projected_task(ctl: RunController, spec: Any) -> str:
    """Write one projected Task through the task provider. Returns its id, or "" on failure.

    The engine is the ENGINE actor in the three-actor matrix, so the write carries
    `managed=True` on the binding and sets the engine-owned fields directly — which is exactly
    what `materialize.reject_write` refuses when anyone ELSE attempts it. The asymmetry is the
    point: one writer for a managed task's status, and a refusal (naming the alternative) for
    every other path. Those other paths are the three doors listed on
    `tasks.registry.engine_owned_refusal`, which is where the refusal is applied; this sentence
    described an unwired guard until #390 wired it.

    Failures return "" rather than raising: the event still fires with an empty task id, which
    is honest (the projection was attempted and did not land) and leaves the next rebuild to
    recover. Raising would fail a node whose work already succeeded.
    """
    try:
        from personalclaw.tasks.registry import create_task

        fields: dict[str, Any] = {
            "title": spec.title or "Untitled step",
            "description": spec.body or "",
            "workflow_binding": {
                "run_id": spec.binding.run_id,
                "node_id": spec.binding.node_id,
                "node_path": spec.binding.node_path,
                "managed": True,
                "fingerprint": spec.binding.fingerprint,
            },
        }
        if spec.status:
            fields["status"] = spec.status
        if spec.done_criterion:
            fields["done_criterion"] = spec.done_criterion
        if spec.blocked_kind:
            fields["blocked_kind"] = spec.blocked_kind
        if spec.preview:
            fields["preview"] = spec.preview
        task = await create_task("native", **fields)
        return str(getattr(task, "id", "") or "")
    except Exception:  # noqa: BLE001 - a board row must never fail a successful node
        logger.debug("workflow %s: projected task write failed", ctl.run.id, exc_info=True)
        return ""


def project_task(ctl: RunController, item: Any, inst: Any, result: Any) -> None:
    """Project a settled leaf node into a Task, and emit the event.

    THE call site the projection modules were built for. `materialize` owns every decision here
    — which nodes earn a task, the dedup keys, the fan-out cap — so this method is the plumbing
    and nothing else: it assembles the node dict, asks, and writes.

    Swallows everything. A projection failure must not fail the RUN: the node has already
    succeeded and its output is already journaled, so turning a board-row problem into a run
    failure would lose real work over a presentation concern. The projection is idempotent by
    construction (fingerprint dedup), so the next tick or a rebuild recovers it.

    A run that keeps nothing (an Incognito or Temporary chat's work) is put on no board: a task is
    a lasting record your other chats and loops read, which its store refuses (``lasting_work``),
    so none is planned, written or verified, and no refusal is recorded for a write the run's work
    never asked for.
    """
    from personalclaw import memory_writes

    if memory_writes.writes_refused():
        return
    try:
        from personalclaw.workflows import materialize as _materialize

        # The keys `should_materialize`/`plan_materialization` actually read are `id`, `label`,
        # `kind`, `path` and `config` — measured against their source. A `node_id` key (the name
        # the BINDING uses) is silently ignored by both, which would make every node fail the
        # has-an-id refusal and project nothing at all. `label` is the TITLE source (#382): omit
        # it here and the projection falls back to the raw node id even though the author wrote
        # a name, which is the half of that defect this dict owns — the other half was
        # `plan_materialization` reading `config.label`, where no definition puts it.
        node_dict = {
            "id": item.node.id,
            "label": item.node.label,
            "path": item.path,
            "kind": item.node.kind.value,
            "config": dict(item.node.config or {}),
        }
        wanted, _why = _materialize.should_materialize(node_dict)
        if not wanted:
            return
        plan = _materialize.plan_materialization(
            ctl.run.id, [node_dict], existing_tasks=_projected_tasks(ctl)
        )
        for spec in plan.create:
            # 🔴 BORN WITH ITS STEP'S STATE. The node has settled (this runs on the success branch
            # only), and its task was written `open` anyway, with nothing to move it after — so a
            # step that had finished read on the board, on Home and on the phone as work to do.
            spec.status = _materialize.project_status(inst.state)
            # Recorded BEFORE the write is scheduled: the dedup set must reflect the intent
            # immediately, or a second settle in the same tick would plan the same task again
            # while the first write is still in flight.
            ctl._projected.append(spec.binding)
            _schedule_task_write(ctl, spec, item.path, item.node.id)
            # Verification rides the same scheduled path: a criterion is a command or a file
            # read, and running it inline would block the tick on someone else's test suite.
            _schedule_verification(ctl, spec, item.path, item.node.id)
        if plan.existing:
            # A rewind's dedup-merge. Emitted as a REFRESH rather than skipped silently:
            # "did my rewind re-create the board" is a question only the event answers.
            ctl.publish_task_materialized(
                item.path,
                item.node.id,
                task_id="",
                fingerprint=_materialize.fingerprint(source_ref="", title=item.node.id, body=""),
                refreshed=True,
            )
    except Exception:  # noqa: BLE001 - a board row must never fail a successful node
        logger.debug("workflow %s: task projection failed", ctl.run.id, exc_info=True)
