"""The queue in front of a model that runs on this machine.

A model on this machine has one machine's compute: requests sent to it at once take turns, and
its server takes them in the order they arrive (``loop.kinds.sdlc._pool_cap`` runs a code loop's
workers one at a time on such a model for the same reason). So a call somebody was waiting for,
sent behind a run of background work, waited out every call ahead of it. Measured: a chat's
schedule took six minutes and a task analysis eight and a half, each behind knowledge enrichment
calls of 100 to 390 seconds on the same local model.

So every call to such a model takes its turn here first, in the guard (``ModelCallGuard``) or, for
a chat's reply, in the native loop, one call at a time per model, in two lanes:

* a call somebody is waiting for (a chat tool's step, a page waiting on its answer) is bound with
  :func:`attending`, which ``one_shot_completion(attended=…)`` does, and is given the model before
  any background call. A chat's own reply is one: the native loop takes its turn for a model no
  guard queues (``NativeAgentRuntime``), ahead of that chat's own title and consolidation;
* background work waits while one is waiting, so it gives way at every turn and never holds the
  model for more than the one call it is already making.

A wait counts against the call's own limits. The guard's clock starts before the call asks for its
turn, and a provider's Request Timeout, which is how long a request may wait to start, bounds the
wait for one. A call somebody is waiting for that has another model to try gives a busy local model
at most the wait set in Settings → Models → Background (``background.busy_model_wait_secs``,
read as each call asks for its turn), then moves on
(:class:`~personalclaw.guardrails.failure.LocalModelBusy`).

While somebody waits, the wait is published (:func:`waits`): what is waiting, what holds the
model, what happens next and when. The page that is waiting says why, and can move on now
(:func:`move_on`).

Calls run on more than one event loop (a sync bridge runs its own), so the state is guarded by a
thread lock and each waiter is woken on its own loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from personalclaw.guardrails.audit import current_caller
from personalclaw.guardrails.failure import LocalModelBusy

logger = logging.getLogger(__name__)

#: How many calls one local model is sent at once. One: its server serves them one after another
#: anyway, and a call that is already there is a call the next one waits behind, out of reach of
#: the lanes.
TURNS_PER_MODEL = 1

#: What a model is busy with, by the subsystem that holds it (``guardrails.audit.CALLERS``), in
#: the words the waiting page shows.
BUSY_WITH: dict[str, str] = {
    "conflict_merge": "merging a sync conflict",
    "inbox_triage": "sorting your inbox",
    "knowledge": "knowledge processing",
    "nl_to_cron": "reading a schedule",
    "skill_ladder": "reviewing a chat for skills",
    "triage_gate": "your triage digest",
    "triage_propose": "your triage digest",
}
#: A holder no subsystem names.
BACKGROUND_WORK = "background work"
#: A holder somebody else is waiting for.
ANOTHER_WAIT = "another request you are waiting for"


@dataclass(frozen=True)
class Attended:
    """A model call somebody is waiting for: what is waiting, and the chat it waits in.

    ``step`` is the step as the person reads it ("Working out the schedule"). ``session`` is the
    session key of the chat turn that is waiting, or ``""`` when a page is.
    """

    step: str
    session: str = ""


_ATTENDED: contextvars.ContextVar[Attended | None] = contextvars.ContextVar(
    "personalclaw_attended_model_call", default=None
)
_NEXT_ENTRY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_model_call_next_entry", default=""
)


@contextlib.contextmanager
def attending(who: Attended | None) -> Iterator[None]:
    """Mark every guarded model call inside this block as one *who* is waiting for.

    ``None`` leaves the calls where they are. Bind it inside the coroutine that makes the call: a
    sync bridge that runs the coroutine on another thread starts with an empty context.
    """
    if who is None:
        yield
        return
    token = _ATTENDED.set(who)
    try:
        yield
    finally:
        _ATTENDED.reset(token)


def current_attended() -> Attended | None:
    """Who is waiting for the calls made here, or ``None`` for background work."""
    return _ATTENDED.get()


@contextlib.contextmanager
def next_entry(ref: str) -> Iterator[None]:
    """The model the chain walk tries if this attempt does not serve (``""`` when none): what a
    waiting call moves on to, and what its page names."""
    token = _NEXT_ENTRY.set(ref or "")
    try:
        yield
    finally:
        _NEXT_ENTRY.reset(token)


def moving_on_to() -> str:
    """The model the chain walk asks if this attempt does not serve, ``""`` when none."""
    return _NEXT_ENTRY.get()


def queue_key(provider: str, model: str) -> str:
    """The queue calls to *model* on the entry *provider* take turns in: one per model on this
    machine, whichever entry or endpoint sends to it (``llm.registry.model_server_here`` says
    whether *provider* runs here), or ``""`` for a model that runs anywhere else, which needs none.
    Keyed by the model alone because the machine's compute is one: two entries pointed at one
    local runtime through two addresses still send it one model's requests, measured as a chat
    turn on one address sent behind a background call on the other. An unreadable registry
    answers ``""``: the call goes out as it always did rather than not at all."""
    try:
        from personalclaw.llm.registry import model_server_here

        server = model_server_here(provider)
    except Exception:  # noqa: BLE001 — fail open: a queue is an ordering, not a permission
        logger.debug("local queue: no server for %r", provider, exc_info=True)
        return ""
    return f"here|{model}" if server else ""


def busy_with(caller: str) -> str:
    """What a call made for *caller* keeps a model busy with, in a person's words."""
    return BUSY_WITH.get(caller, BACKGROUND_WORK)


@dataclass(eq=False)
class _Holder:
    """One call holding a turn, and what it keeps the model busy with."""

    busy_with: str


class _Waiter:
    """One call waiting for its turn, woken on the loop it waits on."""

    def __init__(
        self,
        *,
        key: str,
        loop: asyncio.AbstractEventLoop,
        attended: Attended | None,
        holder: _Holder,
        provider: str,
        model: str,
        next_ref: str,
        within: float | None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.key = key
        self.loop = loop
        self.future: asyncio.Future[None] = loop.create_future()
        self.attended = attended
        self.holder = holder
        self.provider = provider
        self.model = model
        self.next_ref = next_ref
        self.since = time.time()
        self.until = time.monotonic() + within if within is not None else None
        self.granted = False
        self.moved_on = False


class _ModelQueue:
    def __init__(self) -> None:
        self.holders: list[_Holder] = []
        self.attended: deque[_Waiter] = deque()
        self.background: deque[_Waiter] = deque()

    def idle(self) -> bool:
        return not (self.holders or self.attended or self.background)

    def busy_with(self) -> str:
        return self.holders[0].busy_with if self.holders else BACKGROUND_WORK


_LOCK = threading.Lock()
_QUEUES: dict[str, _ModelQueue] = {}
_LISTENERS: list[Callable[[], None]] = []


def subscribe(listener: Callable[[], None]) -> None:
    """Call *listener* whenever a wait somebody is watching starts or ends. From any thread."""
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


def unsubscribe(listener: Callable[[], None]) -> None:
    if listener in _LISTENERS:
        _LISTENERS.remove(listener)


def _notify() -> None:
    for listener in list(_LISTENERS):
        try:
            listener()
        except Exception:  # noqa: BLE001 — telling a page never breaks a call
            logger.debug("local queue listener failed", exc_info=True)


def _grant(q: _ModelQueue) -> list[_Waiter]:
    """Give free turns to waiters, attended lane first. Holds :data:`_LOCK`."""
    granted: list[_Waiter] = []
    while len(q.holders) < TURNS_PER_MODEL and (q.attended or q.background):
        w = (q.attended or q.background).popleft()
        w.granted = True
        q.holders.append(w.holder)
        granted.append(w)
    return granted


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def _wake(waiters: list[_Waiter]) -> None:
    for w in waiters:
        try:
            w.loop.call_soon_threadsafe(_resolve, w.future)
        except RuntimeError:  # its loop has closed: nobody will use this turn or give it back
            _give_back(w.key, w.holder)


def _give_back(key: str, holder: _Holder) -> None:
    with _LOCK:
        q = _QUEUES.get(key)
        if q is None or holder not in q.holders:
            return
        q.holders.remove(holder)
        granted = _grant(q)
        if q.idle():
            _QUEUES.pop(key, None)
    _wake(granted)
    if any(w.attended is not None for w in granted):
        _notify()


class Turn:
    """A call's turn on a local model. Give it back with :meth:`release` when the call's answer
    is complete or the call ended; giving it back twice is a no-op."""

    def __init__(self, key: str, holder: _Holder) -> None:
        self._key = key
        self._holder = holder
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        _give_back(self._key, self._holder)


async def take_turn(key: str, *, provider: str, model: str, within: float | None) -> Turn:
    """Wait for this call's turn on the local model *key* names, for at most *within* seconds
    (``None``: as long as it takes), and hold it.

    A call somebody is waiting for (:func:`attending`) goes before every background call, and
    when its chain has another model to try (:func:`next_entry`) it waits at most
    ``background.busy_model_wait_secs``, as Settings has it now: long enough for a call about to
    finish to hand over, short enough that a person is not left watching a spinner for minutes of
    somebody else's work, and the owner's to change for a machine or a model that needs another
    balance. A wait that runs out, or that the person moves on from, raises
    :class:`~personalclaw.guardrails.failure.LocalModelBusy`.
    """
    who = current_attended()
    after = moving_on_to()
    next_ref = after if who is not None else ""
    if who is not None and next_ref:
        from personalclaw.config.loader import background_limits

        wait = max(0.0, float(background_limits().busy_model_wait_secs))
        within = wait if within is None else min(within, wait)
    holder = _Holder(busy_with=ANOTHER_WAIT if who is not None else busy_with(current_caller()))
    loop = asyncio.get_running_loop()
    with _LOCK:
        q = _QUEUES.setdefault(key, _ModelQueue())
        if len(q.holders) < TURNS_PER_MODEL and not q.attended and not q.background:
            q.holders.append(holder)
            return Turn(key, holder)
        w = _Waiter(
            key=key,
            loop=loop,
            attended=who,
            holder=holder,
            provider=provider,
            model=model,
            next_ref=next_ref,
            within=within,
        )
        (q.attended if who is not None else q.background).append(w)
    if who is not None:
        _notify()

    ended: BaseException | None = None
    try:
        if within is None:
            await w.future
        else:
            await asyncio.wait_for(w.future, max(0.0, within))
    except (TimeoutError, asyncio.CancelledError) as exc:
        ended = exc
    with _LOCK:
        granted: list[_Waiter] = []
        if not w.granted:
            lane = q.attended if who is not None else q.background
            if w in lane:
                lane.remove(w)
            granted = _grant(q)
            if q.idle():
                _QUEUES.pop(key, None)
        held_by = q.busy_with()
    _wake(granted)
    if who is not None or any(g.attended is not None for g in granted):
        _notify()

    if w.granted:
        turn = Turn(key, holder)
        if isinstance(ended, asyncio.CancelledError):
            turn.release()
            raise ended
        return turn
    if isinstance(ended, asyncio.CancelledError):
        raise ended
    raise LocalModelBusy(
        provider=provider,
        model=model,
        busy_with=held_by,
        waited_secs=time.time() - w.since,
        moved_on=w.moved_on or bool(after),
    )


def waits() -> list[dict]:
    """Every wait somebody is watching, oldest first: what waits, on which model, what holds it,
    what is tried next and in how long (``left_secs``, ``None`` when it waits as long as it takes).
    """
    now, mono = time.time(), time.monotonic()
    out: list[dict] = []
    with _LOCK:
        for q in _QUEUES.values():
            for w in q.attended:
                if w.attended is None:
                    continue
                out.append(
                    {
                        "id": w.id,
                        "step": w.attended.step,
                        "session": w.attended.session,
                        "model": f"{w.provider}:{w.model}",
                        "busy_with": q.busy_with(),
                        "next": w.next_ref,
                        "waited_secs": round(max(0.0, now - w.since), 1),
                        "left_secs": (
                            round(max(0.0, w.until - mono), 1) if w.until is not None else None
                        ),
                    }
                )
    out.sort(key=lambda row: -row["waited_secs"])
    return out


def move_on(wait_id: str) -> bool:
    """Stop the wait *wait_id* now, so its chain's next model answers. ``False`` when no such wait
    is in progress, or it has no next model to move on to (its only model is this one)."""
    target: _Waiter | None = None
    with _LOCK:
        for q in _QUEUES.values():
            for w in q.attended:
                if w.id == wait_id and w.next_ref and not w.granted:
                    w.moved_on = True
                    target = w
    if target is None:
        return False
    try:
        target.loop.call_soon_threadsafe(_resolve, target.future)
    except RuntimeError:
        return False
    return True
