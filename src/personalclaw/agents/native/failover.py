"""Falling back down a turn's model chain when its model fails before it says anything.

An interactive turn runs on one model: the chat's own pick, else the agent's pin, else the head
of the use case's chain in Settings → Models (``provider_bridge._build_native_runtime``). When
that model fails with a provider error or a timeout before anything of the turn reached the
user, the native loop tries the models after it in that same order, once each. The one that
answers serves the turn IN ITS PLACE, and the turn says so before that model's reply, in the
words every other substitution uses (``ModelSubstitution``). Never a silent swap.

Nothing here decides what a ref builds into or how a failure reads: that is the resolution
seam's, so both are handed in (``build``, ``describe``), the way hook firing is, and this
package keeps importing only contracts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from personalclaw.guardrails.failure import FailureMode, failed_before_replying
from personalclaw.llm.base import ModelSubstitution

#: The failures a fallback may answer: the model's provider refused or broke, or did not answer
#: in time. Not a prompt too large to run (the same prompt is as large for the next model), not a
#: guard's refusal (a budget, a secret, an injection: moving to another model defeats it), and
#: not an open breaker (a turn resolved past that already).
FAILOVER_MODES = frozenset({FailureMode.PROVIDER_ERROR, FailureMode.TIMEOUT})


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

    def substitution(self, served: str, failures: list[tuple[str, str]]) -> ModelSubstitution:
        """What a turn that fell back says: ``served`` answered after ``failures``, in order."""
        return ModelSubstitution(
            requested=self.requested,
            served=served,
            why=failed_before_replying(failures),
            who=self.who,
        )
