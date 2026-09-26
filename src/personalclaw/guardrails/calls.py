"""What each guarded model call used and how it sampled — read back by the code that made it.

:class:`~personalclaw.guardrails.model_call.ModelCallGuard` is the one chokepoint every
non-interactive model call passes, and it is the only layer that knows what a caller several
frames up needs and cannot see: which provider and model served the call, the usage the provider
reported, what that cost, and — through :attr:`ModelProvider.sampling_temperature` — the
temperature the adapter actually put on the request. ``one_shot_completion`` returns text, so
without a way back:

* a workflow step that made its model calls through an action provider (``best-of-n`` makes N
  samples plus a judge pass per survivor) journaled ``tokens: 0`` and no model, and Introspect
  told the user nothing was costing money;
* a cancelled step's in-flight generations were spend nobody counted;
* best-of-N could not tell a temperature ladder from N calls at the provider's default;
* a step whose model was streaming its answer looked silent to the workflow stall clock, and was
  killed mid-generation as "no progress".

Same shape as :mod:`personalclaw.guardrails.wire`, for the same reason: a caller-bound MUTABLE
record, because the guard runs inside tasks whose context is a copy (a copy of the reference, not
of the object). Unlike the wire recorder these bindings NEST — a best-of-N candidate binds its own
record inside the workflow step's — so the context var holds the stack of bound records and each
call is appended, as one shared object, to every one of them.

Nothing bound (a direct call, a test) means :func:`open_call` returns ``None`` and the guard
records nowhere: this is an observation channel and must never be able to stop a model call.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field

#: A call the guard started and has not settled. At the instant a node is cancelled, every one of
#: these is a generation the cancel cut off mid-flight.
OPEN = "open"
#: The provider's terminal event arrived.
DONE = "done"
#: The call raised (transport error, timeout, a provider error).
FAILED = "failed"
#: The caller walked away from the call (cancellation) before the provider finished.
ABANDONED = "abandoned"


@dataclass
class ModelCall:
    """One guarded model call, as the guard observed it."""

    provider: str
    model: str
    #: The temperature the adapter put on the request, or ``None`` when it sent none.
    temperature: float | None = None
    #: Options the adapter was given and left off the request, each with the reason
    #: (:attr:`ModelProvider.unsent_options`) — why ``temperature`` is ``None`` when one was asked.
    unsent: dict[str, str] = field(default_factory=dict)
    state: str = OPEN
    input_tokens: int = 0
    output_tokens: int = 0
    #: False when the terminal event carried no usage. A real completion always consumed input
    #: tokens, so a 0/0 report is "the provider did not say", never "it was free".
    usage_reported: bool = False
    cost_usd: float = 0.0
    #: False when neither the provider nor the price table can say what the call cost. A local
    #: model counts as priced — at zero — because nothing it does is billed.
    priced: bool = True
    #: When the provider last sent anything on this call (``time.time()``): the call opening, then
    #: every streamed event. What a workflow's stall clock reads, so a model that is still
    #: generating is not mistaken for one that went silent.
    last_event_at: float = field(default_factory=time.time)


@dataclass
class CallLog:
    """Every model call made while this log was bound, in the order they started."""

    calls: list[ModelCall] = field(default_factory=list)

    @property
    def cut_off(self) -> int:
        """Calls that never finished: still open, or abandoned by a cancel."""
        return sum(1 for c in self.calls if c.state in (OPEN, ABANDONED))

    @property
    def tokens(self) -> int | None:
        """Tokens the finished calls used, or ``None`` when that is not known.

        Unknown when a finished call's provider reported no usage, or when a call never finished
        (a generation cut off mid-stream has spent tokens nobody reported). A FAILED call adds
        nothing and does not make the total unknown: the provider reported no usage for it, and
        the dominant failure — a refused connection, an HTTP error — happens before any
        generation.
        """
        if self.cut_off:
            return None
        done = [c for c in self.calls if c.state == DONE]
        if any(not c.usage_reported for c in done):
            return None
        return sum(c.input_tokens + c.output_tokens for c in done)

    @property
    def cost_usd(self) -> float | None:
        """What the finished calls cost, or ``None`` when some of it cannot be priced."""
        if self.cut_off:
            return None
        done = [c for c in self.calls if c.state == DONE]
        if any(not c.priced for c in done):
            return None
        return round(sum(c.cost_usd for c in done), 6)

    @property
    def floor_tokens(self) -> int | None:
        """The tokens the finished calls' providers DID report, however much else is unknown.

        What a stopped step can still say: its cut-off generations spent something unmeasured, and
        the finished ones spent exactly this. A floor, never a total. ``None`` when no provider
        reported any, because a floor of zero reads as a measurement of nothing.
        """
        done = [c for c in self.calls if c.state == DONE and c.usage_reported]
        return sum(c.input_tokens + c.output_tokens for c in done) if done else None

    @property
    def floor_cost_usd(self) -> float | None:
        """What the finished, priceable calls cost — the same floor as :attr:`floor_tokens`."""
        priced = [c for c in self.calls if c.state == DONE and c.priced]
        return round(sum(c.cost_usd for c in priced), 6) if priced else None

    @property
    def models(self) -> list[str]:
        """The distinct models that served these calls, in first-use order."""
        return _distinct(c.model for c in self.calls)

    @property
    def providers(self) -> list[str]:
        """The distinct providers these calls went to, in first-use order."""
        return _distinct(c.provider for c in self.calls)

    @property
    def last_activity(self) -> float | None:
        """When any of these calls last heard from its provider, or ``None`` before the first."""
        return max((c.last_event_at for c in self.calls), default=None)


def _distinct(names: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


_BOUND: ContextVar[tuple[CallLog, ...]] = ContextVar("personalclaw_model_call_logs", default=())


@contextlib.contextmanager
def capture_model_calls() -> Iterator[CallLog]:
    """Bind a fresh :class:`CallLog` for everything awaited — or task-created — in this block.

    A task created inside the block copies the binding, which is how the workflow controller
    gives one node's dispatch task its own log. Nested blocks each see their own calls AND pass
    them to every enclosing log.
    """
    log = CallLog()
    token = _BOUND.set((*_BOUND.get(), log))
    try:
        yield log
    finally:
        _BOUND.reset(token)


def open_call(
    provider: str,
    model: str,
    *,
    temperature: float | None,
    unsent: dict[str, str] | None = None,
) -> ModelCall | None:
    """Record the start of one model call on every bound log. ``None`` when nothing is bound.

    The ONE object is appended to each log, so settling it (the guard mutates the returned call)
    is seen by all of them at once.
    """
    logs = _BOUND.get()
    if not logs:
        return None
    call = ModelCall(
        provider=provider, model=model, temperature=temperature, unsent=dict(unsent or {})
    )
    for log in logs:
        log.calls.append(call)
    return call
