"""Falling back down a turn's model chain when its model fails before it says anything.

An interactive turn runs on one model: the chat's own pick, else the agent's pin, else the head
of the use case's chain in Settings → Models (``provider_bridge._build_native_runtime``). When
that model fails before anything of the turn reached the user (:data:`FAILOVER_MODES`), the
native loop tries the models after it in that same order, once each. The one that
answers serves the turn IN ITS PLACE, and the turn says so before that model's reply, in the
words every other substitution uses (``ModelSubstitution``). Never a silent swap.

Nothing here decides what a ref builds into or how a failure reads: that is the resolution
seam's, so both are handed in (``build``, ``describe``), the way hook firing is, and this
package keeps importing only contracts.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from personalclaw.guardrails.failure import (
    FailureMode,
    FirstTokenTimeout,
    LocalModelBusy,
    ModelCallTimeout,
    NoModelAnswered,
    failed_before_replying,
)
from personalclaw.llm.base import ModelSubstitution
from personalclaw.llm.tool_use import uses_tools

logger = logging.getLogger(__name__)

#: The failures a fallback may answer, as a one-shot call's chain walk moves on from them
#: (``llm_helpers.run_over_use_case_chain``): the model's provider refused or broke, it did not
#: answer in time, or its breaker is open. An open breaker is one because a runtime keeps the
#: model it was built on: a session built while its first model answered fails every turn in
#: microseconds once that model's breaker opens, while the next model of the chain is never asked.
#: Not a prompt too large to run (the same prompt is as large for the next model), and not a
#: guard's refusal (a budget, a secret, an injection: moving to another model defeats it).
FAILOVER_MODES = frozenset(
    {
        FailureMode.PROVIDER_ERROR,
        FailureMode.TIMEOUT,
        FailureMode.CIRCUIT_OPEN,
    }
)


@dataclass(frozen=True)
class ModelFailover:
    """Where a turn may go when the model it runs on fails before any output."""

    #: The ``"<entry>:<model>"`` ref the runtime was built on.
    requested: str
    #: Whose choice that was, for the sentence ("this chat's model", "Researcher's model"); ""
    #: for the head of the use case's own chain.
    who: str
    #: The refs after it, in the order the turn chose models by.
    candidates: tuple[str, ...]
    #: A ref as ``(provider, model id to send)``. Raises when that ref cannot be built now.
    build: Callable[[str], tuple[Any, str]]
    #: A failure as a short clause: "the model provider is rate-limiting or overloaded right now".
    describe: Callable[[BaseException], str]
    #: Whether a ref takes images, from the platform's record: a turn carrying images falls back
    #: only to a model that reads them.
    takes_images: Callable[[str], Awaitable[bool]]
    #: Whether the turn may be moved to a ref at all (``memory_writes.model_may_read``): a turn
    #: of an Incognito or Temporary chat stays on the model it runs on.
    admits: Callable[[str], bool]

    def substitution(self, served: str, failures: list[tuple[str, str]]) -> ModelSubstitution:
        """What a turn that fell back says: ``served`` answered after ``failures``, in order."""
        return ModelSubstitution(
            requested=self.requested,
            served=served,
            why=failed_before_replying(failures),
            who=self.who,
        )


def resending_cannot_help(exc: BaseException, *, can_move_on: bool) -> bool:
    """Whether the turn's one blind retry of the same request on the same model is no use for
    *exc*, so the failure goes straight to the next model (*can_move_on*) or stands.

    A model that did not START answering in time reads the identical request again from its first
    token and takes as long again. A local model that stayed busy for as long as the turn waits is
    busy still. A call cut at its ceiling runs as long again, while the next model can answer.
    """
    if isinstance(exc, (FirstTokenTimeout, LocalModelBusy)):
        return True
    return isinstance(exc, ModelCallTimeout) and can_move_on


@dataclass
class TurnFallback:
    """One runtime's fallback state: whether its caller asked that the next turn may fall back
    (``announced`` by ``NativeAgentRuntime.announce_failover``), and for the turn in flight the
    models still to try, the ``(ref, why)`` of each that failed, and the sentence the model that
    answers says before its reply (``pending``)."""

    announced: bool = False
    queue: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    pending: str = ""

    def begin(self, failover: ModelFailover | None) -> None:
        """Start a turn. What the caller asked is taken as the turn starts, so the late cleanup
        of a turn its caller stopped reading cannot take it away. The models the turn may move to
        are those the failover admits, so a turn that may move nowhere names no next model either
        (:meth:`next_ref`)."""
        announced, self.announced = self.announced and failover is not None, False
        self.queue = (
            [ref for ref in failover.candidates if failover.admits(ref)]
            if announced and failover is not None
            else []
        )
        self.failures, self.pending = [], ""

    def end(self) -> None:
        self.queue, self.pending = [], ""

    def next_ref(self) -> str:
        """The model the turn moves on to if its model fails now, ``""`` when none."""
        return self.queue[0] if self.queue else ""

    def take_pending(self) -> str:
        pending, self.pending = self.pending, ""
        return pending

    async def move_on(
        self,
        failover: ModelFailover | None,
        exc: BaseException,
        *,
        current: str,
        tools: bool,
        images: bool,
    ) -> tuple[Any, str] | None:
        """``(provider, model id)`` of the model a turn moves to after *current* failed with *exc*
        before any output: the first left to try that can be built now, can use the turn's tools
        and, on a turn carrying *images*, takes them (the platform's record, as the turn's own model
        was asked). Its sentence is :attr:`pending`, said before anything it streams. ``None`` when
        nothing is left to try, and then the failure stands. Raises :class:`NoModelAnswered` when
        models were tried in its place and failed too: then no single provider's error says what
        happened."""
        if failover is None or (not self.queue and not self.failures):
            return None
        self.failures.append((current or failover.requested, failover.describe(exc)))
        while self.queue:
            ref = self.queue.pop(0)
            try:
                provider, model_id = failover.build(ref)
            except Exception as build_exc:  # noqa: BLE001 — a fallback that cannot build is passed
                logger.warning(
                    "native: fallback %s cannot be built, passed over: %r", ref, build_exc
                )
                continue
            if tools and not uses_tools(provider):
                logger.info("native: fallback %s cannot use tools, passed over", ref)
                continue
            if images and not await failover.takes_images(ref):
                logger.info("native: fallback %s does not take images, passed over", ref)
                continue
            logger.warning(
                "native: %s failed before any output (%r) — falling back to %s",
                self.failures[-1][0],
                exc,
                ref,
            )
            # The ref it answers as: its build stamp's entry with the model id it is sent.
            entry = str(getattr(provider, "served_ref", "") or "").partition(":")[0]
            served = f"{entry}:{model_id}" if entry and model_id else ref
            self.pending = failover.substitution(served, self.failures).notice()
            return provider, model_id
        if len(self.failures) > 1:
            raise NoModelAnswered(self.failures) from exc
        return None
