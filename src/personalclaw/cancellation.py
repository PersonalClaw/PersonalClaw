"""One propagated stop signal for a turn (PR2-12).

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
from dataclasses import asdict, dataclass
from typing import Any, Iterator

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
    """What a stop actually REACHED — the shape PR2-13 consumes.

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

    # ── turn lifecycle ──

    def begin_turn(self) -> None:
        """Arm the scope for a fresh turn. Clears the previous turn's signal+report."""
        self._reason = ""
        self._turn_active = True
        self._children.clear()
        self._report = StopReport()

    def end_turn(self) -> None:
        """The turn is over.

        Deliberately does NOT clear ``reason``/``report``: the turn's own terminal
        event and the surface that renders the stop both read them AFTER the turn
        ends. What it does clear is ``_turn_active``, which is what makes a later
        stop a no-op instead of a lie.
        """
        self._turn_active = False
        self._children.clear()

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
        """Terminate AND reap every tracked child. Returns the number reaped.

        The children are popped BEFORE the first signal, so a concurrent second stop
        finds nothing to kill — the idempotence the atom asks for lives here, not in
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
