"""One propagated stop signal for a turn.

A stop is not a flag each layer checks when convenient. It is a single signal, held in
one place, that every layer of a turn reads — and that carries the obligations a stop
implies: an in-flight provider request is ABORTED rather than awaited-and-discarded, a
subprocess a tool started is terminated AND REAPED (so a stopped turn leaves no orphan
holding a lock or a file handle), and calls still queued behind the running one are
DROPPED without executing.

Why one object instead of a boolean per layer. Before this module the native runtime
carried ``self._cancelled``, each model provider carried its own in-flight future, the
bash tool carried nothing at all, and the subagent manager carried only a reaper
deadline. Four unrelated notions of "cancelled" meant a stop reached whichever of them
the caller happened to know about — which is why pressing stop killed the *stream* and
left the *work* running. :class:`CancelScope` is the single notion; the layers read it.

Reason, not just a bit. :data:`CANCEL_USER` (the user pressed stop) is deliberately
distinct from :data:`CANCEL_INTERNAL` (a circuit breaker tripped, a watchdog fired, the
host is shutting down). They produce different turn outcomes —
``STOP_REASON_STOPPED_BY_USER`` vs ``STOP_REASON_CANCELLED`` — because "you stopped
this" and "we gave up on this" are different facts about a turn, and a surface that
renders them identically is why users stop trusting the button.

Idempotence is in the primitive, not in each caller. :meth:`CancelScope.request` is
total: ``"no_turn"`` when no turn is in flight (a stop arriving after the turn already
finished is a NO-OP, never a failure), ``"first"`` for the call that flips the signal,
``"repeat"`` for every later one. Callers run the side effects — aborting the request,
reaping children — only on ``"first"``, so pressing stop twice cannot double-kill or
double-record.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Collection, Iterable, Iterator

logger = logging.getLogger(__name__)

# ── Cancel causes ──
# The two are NOT interchangeable: they select the turn's recorded outcome.
CANCEL_USER = "user"
CANCEL_INTERNAL = "internal"

# ── request() answers ──
REQUEST_NO_TURN = "no_turn"  # nothing was in flight — a stop here is a no-op
REQUEST_FIRST = "first"  # this call flipped the signal; run the side effects
REQUEST_REPEAT = "repeat"  # already cancelled; side effects already ran

# How long a child gets to honour SIGTERM before SIGKILL. Short on purpose: the user
# pressed stop, so a child that ignores a term signal is not owed a long goodbye.
REAP_GRACE_SECS = 2.0


@dataclass
class StopReport:
    """What a stop actually REACHED — the shape the stop card consumes.

    Counted rather than asserted. "Cancelled" on its own is a claim; these fields are
    the evidence, and they are what a surface can honestly show a user who wants to
    know whether the button did anything.
    """

    reason: str = ""
    model_request_aborted: bool = False
    children_reaped: int = 0
    children_escaped: int = 0
    tool_calls_dropped: int = 0
    subagents_stopped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CancelScope:
    """The single propagated stop signal for one turn.

    Lives on the thing that owns a turn (the agent runtime). ``cancel()`` on the
    provider seam reaches it directly; the tool layer reaches it through the ambient
    binding (:func:`current_scope`), because a spawn site eight frames down should not
    have to take a cancellation parameter to be stoppable.
    """

    def __init__(self) -> None:
        self._reason: str = ""
        self._turn_active: bool = False
        # pid → process handle. Only children registered here are ever signalled: the
        # kill path must never target a pid this scope did not spawn.
        self._children: dict[int, Any] = {}
        self._report = StopReport()
        # The marker this turn's commands carry (``run_processes``), whether they are its loop's
        # rather than its own, and how many carried it: a turn that ran none has nothing to find.
        self._run_mark = ""
        self._run_kept = False
        self._commands = 0

    # ── turn lifecycle ──

    def begin_turn(self, run_owner: str = "turn") -> None:
        """Arm the scope for a fresh turn. Clears the previous turn's signal+report.

        *run_owner* says whose its commands are (``run_processes.turn_owner``): the turn's own, or
        a loop worker's loop's."""
        from personalclaw import run_processes

        self._reason = ""
        self._turn_active = True
        self._children.clear()
        self._report = StopReport()
        self._run_mark = run_processes.mark(run_owner)
        self._run_kept = run_owner != run_processes.TURN
        self._commands = 0

    def end_turn(self) -> None:
        """The turn is over.

        Deliberately does NOT clear ``reason``/``report``: the turn's own terminal
        event and the surface that renders the stop both read them AFTER the turn
        ends. What it does clear is ``_turn_active``, which is what makes a later
        stop a no-op instead of a lie.

        What the turn's commands left running ends with it, unless they are its loop's and the
        turn was not stopped: a loop's ending ends those (``run_processes``)."""
        self._turn_active = False
        self._children.clear()
        if self._commands and (not self._run_kept or self.cancelled):
            from personalclaw import run_processes

            run_processes.end_soon(self._run_mark)

    def command_started(self) -> str:
        """The marker a command this turn starts carries, counted; ``""`` outside a turn."""
        if not self._turn_active:
            return ""
        self._commands += 1
        return self._run_mark

    # ── state ──

    @property
    def turn_active(self) -> bool:
        return self._turn_active

    @property
    def cancelled(self) -> bool:
        return self._reason != ""

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def stopped_by_user(self) -> bool:
        return self._reason == CANCEL_USER

    @property
    def report(self) -> StopReport:
        return self._report

    @property
    def child_count(self) -> int:
        return len(self._children)

    # ── the signal ──

    def request(self, reason: str = CANCEL_USER) -> str:
        """Raise the stop signal. Returns one of the ``REQUEST_*`` answers.

        Total and side-effect-free, so a caller can ask "is there anything to stop?"
        and act on the answer without having already half-stopped something.
        """
        if not self._turn_active:
            return REQUEST_NO_TURN
        if self._reason:
            return REQUEST_REPEAT
        self._reason = reason or CANCEL_USER
        self._report.reason = self._reason
        return REQUEST_FIRST

    # ── children ──

    def register_child(self, proc: Any) -> None:
        """Track a subprocess this turn started, so a stop can reap it."""
        pid = getattr(proc, "pid", None)
        if pid is None:
            return
        self._children[int(pid)] = proc

    def unregister_child(self, proc: Any) -> None:
        """Untrack a child that finished on its own."""
        pid = getattr(proc, "pid", None)
        if pid is None:
            return
        self._children.pop(int(pid), None)

    async def reap_children(self) -> int:
        """Terminate AND reap every tracked child, and set going the end of what the turn's
        commands started that left their process group (``run_processes``). Returns the number of
        children reaped.

        That end is not waited for: a stop answers as soon as the work it reached is stopped, and
        the turn's own end (its transcript among it) must come after that answer, not race it.

        The children are popped BEFORE the first signal, so a concurrent second stop
        finds nothing to kill — the idempotence a stop needs lives here, not in
        the callers.
        """
        procs = list(self._children.values())
        self._children.clear()
        reaped = 0
        for proc in procs:
            try:
                ok = await terminate_and_reap(proc)
            except Exception:
                logger.warning("cancel: reaping child failed", exc_info=True)
                ok = False
            if ok:
                reaped += 1
            else:
                self._report.children_escaped += 1
        self._report.children_reaped += reaped
        if procs:
            logger.info("cancel: reaped %d/%d child process(es) on stop", reaped, len(procs))
        if self._commands:
            from personalclaw import run_processes

            run_processes.end_soon(self._run_mark)
        return reaped

    # ── report accumulation ──

    def note_model_request_aborted(self) -> None:
        self._report.model_request_aborted = True

    def note_tool_call_dropped(self, count: int = 1) -> None:
        self._report.tool_calls_dropped += count

    def note_subagents_stopped(self, count: int) -> None:
        self._report.subagents_stopped += count


# ── the kill path ──


def _is_group_leader(pid: int) -> bool:
    """True when *pid* leads its own process group.

    This is THE safety rail on the kill path. A child that is not its own group leader
    shares the gateway's group, so ``killpg`` on it would signal the gateway itself.
    Every spawn a stop must reach is started with ``start_new_session=True`` precisely
    so this returns True and the whole tree (a shell and its grandchildren) can go.
    """
    try:
        return os.getpgid(pid) == pid
    except OSError:
        return False


def _signal_child(proc: Any, sig: int) -> None:
    """Signal a child's group when it leads one, else the single pid.

    Never signals pid 0 or 1: ``killpg(0, …)`` means "my own process group", which
    would take the gateway down with the child.
    """
    pid = getattr(proc, "pid", None)
    if pid is None or int(pid) <= 1:
        return
    pid = int(pid)
    if _is_group_leader(pid):
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            return
        except OSError:
            logger.debug("cancel: killpg(%d) failed; falling back to pid", pid, exc_info=True)
    try:
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()
    except (ProcessLookupError, OSError, ValueError):
        pass


async def terminate_and_reap(proc: Any, *, grace: float = REAP_GRACE_SECS) -> bool:
    """SIGTERM → grace → SIGKILL → **wait**. True once the child is reaped.

    Reaping, not signalling, is the deliverable: a signalled-but-unwaited child stays
    a zombie still owning its end of the pipe, which is the orphan-holding-a-handle
    failure this exists to prevent. So every path here ends in an awaited ``wait()``.
    """
    if getattr(proc, "pid", None) is None:
        return False
    if proc.returncode is not None:
        # Already exited — still wait() so the transport is closed and the entry reaped.
        with contextlib.suppress(Exception):
            await proc.wait()
        return True

    _signal_child(proc, signal.SIGTERM)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return True

    _signal_child(proc, signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return True
    except Exception:
        logger.warning("cancel: child %s survived SIGKILL", getattr(proc, "pid", "?"))
        return False


async def kill_timed_out(proc: Any, *, grace: float = REAP_GRACE_SECS) -> bool:
    """SIGKILL a child whose **timeout already expired**, then reap under a BOUND.

    The timeout-path counterpart of :func:`terminate_and_reap`. Use it wherever an
    ``asyncio.wait_for(...)`` just raised: the deadline is spent, so there is no grace
    to give, and the only job left is to leave nothing running and nothing blocked.

    Two things distinguish it from the ``proc.kill()`` / ``await proc.communicate()``
    pair it replaces, and both were measured rather than reasoned:

    * **It signals the group.** ``proc.kill()`` reaches only the direct child, so a
      grandchild (``git``'s remote helper, a ``pip`` build backend, a bundler worker)
      survives — and keeps the *inherited* stdout/stderr pipe open. :func:`_signal_child`
      takes the whole group when the child leads one, and falls back to the single pid
      when it does not, so this is safe at a site that shares the gateway's group.
    * **The reap is bounded.** ``asyncio``'s ``Process.wait()`` resolves only once every
      inherited pipe has disconnected, not when the child is reaped — so an *unbounded*
      drain after a kill waits for the grandchild's full runtime instead of the child's
      exit. A 1s timeout over a ``sleep 30`` grandchild took **30.0s** to return that
      way, and **1.0s** once the group was signalled. An un-bounded drain is not a
      timeout; it is the child's own duration wearing a timeout's name.

    A child that must lead its own group for the group branch to fire has to be spawned
    with ``start_new_session=True``. Without it ``getpgid(pid) != pid`` and this falls
    back to the single pid — correct, but it cannot reach the grandchild, which is why
    the caller adds the flag rather than this function assuming it.

    Returns True once the child is reaped, False if it outlived *grace*.
    """
    if getattr(proc, "pid", None) is None:
        return False
    _signal_child(proc, signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return True
    except Exception:
        logger.warning(
            "cancel: timed-out child %s not reaped within %ss", getattr(proc, "pid", "?"), grace
        )
        return False


#: How long a stop waits for the tasks it cancelled before it carries on without them. It clears
#: both of :func:`terminate_and_reap`'s waits (SIGTERM, then SIGKILL), the longest a task that is
#: putting its child away should need. Read when a wait starts, so a test can shorten it.
CANCEL_GRACE_SECS = 5.0


async def cancel_and_wait(
    tasks: Iterable[asyncio.Future[Any] | None], *, what: str, grace: float | None = None
) -> set[asyncio.Future[Any]]:
    """Cancel *tasks*, wait at most *grace* seconds for them to finish, and return the ones that
    did not. Each of those is named in the log, and the caller carries on without it.

    A stop that awaited a task it had cancelled waited for the task to LEAVE, and nothing bounded
    how long that took. A task can outlive its cancel in ways it cannot help: on Python 3.12 a
    cancel that lands while asyncio starts a child process and connects its pipes, together with
    asyncio's own task connecting them, leaves the start waiting for a wake-up that never comes
    (3.13 fixed it); a task whose cleanup waits for its child's pipes waits for as long as a
    grandchild that inherited them runs; and a task can catch the cancel. Any of them held the
    stop forever. What did not finish here keeps running until the process exits.

    ``None`` entries are skipped, and a task already done is not cancelled. A finished task's
    exception is taken, so it is never reported as unretrieved, and logged at debug: a stop is
    best-effort. The CALLER being cancelled while it waits is not swallowed.
    """
    if grace is None:
        grace = CANCEL_GRACE_SECS
    given = [task for task in tasks if task is not None]
    pending = {task for task in given if not task.done()}
    for task in pending:
        task.cancel()
    left: set[asyncio.Future[Any]] = set()
    if pending:
        _finished, left = await asyncio.wait(pending, timeout=grace)
    for task in given:
        error = task.exception() if task.done() and not task.cancelled() else None
        if error is not None:
            logger.debug("%s: a task ended with an error as it stopped", what, exc_info=error)
    if left:
        logger.warning(
            "%s: %d task(s) had not finished %.1fs after being cancelled; carrying on without "
            "them: %s",
            what,
            len(left),
            grace,
            ", ".join(sorted(_task_label(task) for task in left)),
        )
    return left


async def settle(held: Collection[asyncio.Future[Any]]) -> None:
    """Wait until every task in *held* has finished, one added to it while this waits included.

    *held* is a collection its tasks leave by a done callback (``task.add_done_callback(
    held.discard)``), the way the gateway holds the work it starts in the background. This waits
    for the tasks, never for *held* to empty: a task that has just finished is still in it until
    its callback runs, on the loop's next pass, and gathering only finished tasks returns without
    giving the loop that pass (asyncio completes such a gather at once). A wait that looped until
    *held* emptied never let the callbacks it waited for run, and spun forever. A task's error is
    its own and is not raised here; the caller being cancelled while it waits is.
    """
    while pending := [task for task in held if not task.done()]:
        await asyncio.gather(*pending, return_exceptions=True)


#: How often :func:`wait_for_unpaused` reads its clock. Short, because a tick is also how it sees
#: time the event loop could not run: a freeze that begins inside a tick is charged to the work
#: for at most the rest of that tick.
PAUSED_CLOCK_TICK_SECS = 0.25


class OutOfTime(asyncio.TimeoutError):
    """The work used all of its own time and was stopped (:func:`wait_for_unpaused`).

    A ``TimeoutError``, so a caller that catches that still catches this, and a separate class, so
    a caller can tell its bound running out from the work's own ``TimeoutError`` (a request inside
    it that timed out), which is the work's failure, not the bound's."""


async def wait_for_unpaused(
    awaitable: Awaitable[Any],
    timeout: float,
    *,
    paused: Callable[[], bool] | None = None,
    what: str,
    on_timeout: Callable[[], None] | None = None,
) -> Any:
    """``asyncio.wait_for`` on the work's own clock: it runs only while the work could run.

    Two kinds of time are not the work's. Time the event loop could not run at all: the process
    was frozen (a library call holding the interpreter lock in another thread), or the loop was
    busy with other work. A wall-clock bound counted that, and failed a frame extraction whose
    ffmpeg had finished in 13 seconds as "did not finish within 2 minutes" because another step
    froze the process for two. And time *paused* says the work is waiting on a person: a loop
    worker's turn is bounded so a wedged one cannot hold its session forever, and an Attended
    worker's turn waits on its owner for each call it asks about, for as long as the approval
    window says. That wait is not the turn running long.

    The clock is read every :data:`PAUSED_CLOCK_TICK_SECS`: a tick that wakes late counts as one
    tick (the rest is the loop's), and a tick *paused* reads paused counts as none. Out of time,
    *on_timeout* is called, the work is cancelled (:func:`cancel_and_wait`, bounded) and
    :class:`OutOfTime` is raised. The call comes first, so the work can tell, as it ends, that
    its bound ended it rather than any other cancel.

    Cancelled itself, it cancels the work and waits for it to end (bounded the same way) before
    the cancel goes on, as ``asyncio.wait_for`` does: a caller that cancels a turn and then acts
    on how it ended (the gateway's stop saves the chats after it ends their turns) must find what
    the turn said as it ended already said.
    """
    task = asyncio.ensure_future(awaitable)
    clock = asyncio.get_running_loop()
    spent = 0.0
    try:
        while spent < timeout:
            tick = min(PAUSED_CLOCK_TICK_SECS, timeout - spent)
            began = clock.time()
            done, _pending = await asyncio.wait({task}, timeout=tick)
            if task in done:
                return task.result()
            if paused is None or not paused():
                spent += min(clock.time() - began, tick)
    except asyncio.CancelledError:
        await cancel_and_wait([task], what=what)
        raise
    if on_timeout is not None:
        on_timeout()
    await cancel_and_wait([task], what=what)
    raise OutOfTime


def _task_label(task: asyncio.Future[Any]) -> str:
    """``Task-12 (AvailabilityBoard._drain)``: the task's name and what it runs."""
    coro = task.get_coro() if isinstance(task, asyncio.Task) else None
    runs = getattr(coro, "__qualname__", "") or type(task).__name__
    name = task.get_name() if isinstance(task, asyncio.Task) else "future"
    return f"{name} ({runs})"


# ── ambient binding (so a deep spawn site is stoppable without a parameter) ──

_CURRENT_SCOPE: contextvars.ContextVar["CancelScope | None"] = contextvars.ContextVar(
    "personalclaw_cancel_scope", default=None
)


def bind_scope(scope: CancelScope | None) -> Any:
    """Bind *scope* for the current async context; returns a reset token."""
    return _CURRENT_SCOPE.set(scope)


def reset_scope(token: Any) -> None:
    try:
        _CURRENT_SCOPE.reset(token)
    except (ValueError, LookupError):
        pass


def current_scope() -> CancelScope | None:
    """The scope bound for this dispatch, or None outside a turn."""
    return _CURRENT_SCOPE.get()


@contextlib.contextmanager
def track_child(proc: Any) -> Iterator[None]:
    """Register *proc* with the ambient scope for the duration of the block.

    A spawn site wraps its child in this and becomes stoppable. Outside a turn (a
    test, a CLI probe) there is no scope and the block is a no-op, so the helper is
    safe to use at every spawn regardless of caller.
    """
    scope = current_scope()
    if scope is not None:
        scope.register_child(proc)
    try:
        yield
    finally:
        if scope is not None:
            scope.unregister_child(proc)
