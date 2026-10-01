"""In-process tool-approval gate for the native loop.

Mirrors ACP's interactive permission flow, but in-process: the loop calls
:meth:`ApprovalGate.request`, which parks on an ``asyncio.Future`` keyed by
request id; the chat runner's existing approve/reject plumbing resolves it via
:meth:`ApprovalGate.resolve`. The :class:`~personalclaw.agents.native.runtime.NativeAgentRuntime`
wires its ``approve_tool``/``reject_tool`` (the same surface the dashboard
``/approve`` handler already calls on the session provider) to ``resolve``.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Decision strings the loop branches on.
APPROVE = "approve"
REJECT = "reject"


class Refusal(str):
    """A REJECT a host gave before anyone was asked (a hook, the task mode, the deny-list), for a
    reason no answer could change. It equals :data:`REJECT`, and carries the reason and its kind
    (``security.DENY_KIND_*``), so the model is told why instead of that the user declined."""

    reason: str
    kind: str

    def __new__(cls, reason: str, kind: str) -> "Refusal":
        self = super().__new__(cls, REJECT)
        self.reason, self.kind = reason, kind
        return self


def refusal_of(decision: str) -> tuple[str, str]:
    """``(kind, reason)`` a rejected call is told: the host's own reason for a :class:`Refusal`,
    and the user's decline for every other REJECT."""
    if isinstance(decision, Refusal) and decision.reason:
        return decision.kind, decision.reason
    from personalclaw import security

    return security.DENY_KIND_USER, "the user declined this tool call"


class ApprovalGate:
    """One gate per native session; one pending Future per in-flight request."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[str]] = {}

    def register(self, request_id: str) -> asyncio.Future[str]:
        """Create + register the pending Future for a request.

        MUST be called *before* the caller surfaces the permission request to
        the UI, so an approve/reject that arrives immediately (or even before
        the caller awaits) still finds a pending entry — otherwise the resolve
        is a no-op and the wait deadlocks.
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = fut
        return fut

    async def wait(
        self, request_id: str, fut: asyncio.Future[str], *, timeout: float = 300.0
    ) -> str:
        """Await a previously-:meth:`register`ed Future; fail-closed on timeout."""
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            logger.info("approval %s timed out after %.0fs → reject", request_id, timeout)
            return REJECT
        finally:
            self._pending.pop(request_id, None)

    async def request(self, request_id: str, *, timeout: float = 300.0) -> str:
        """Register + wait in one call (for callers with no yield in between)."""
        fut = self.register(request_id)
        return await self.wait(request_id, fut, timeout=timeout)

    def resolve(self, request_id: str, decision: str) -> bool:
        """Resolve a pending request. Returns False if nothing was waiting."""
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    def approve(self, request_id: str) -> bool:
        return self.resolve(request_id, APPROVE)

    def reject(self, request_id: str) -> bool:
        return self.resolve(request_id, REJECT)

    def refuse(self, request_id: str, reason: str, kind: str) -> bool:
        """Reject a pending request for a host's own *reason* (:class:`Refusal`)."""
        return self.resolve(request_id, Refusal(reason, kind))

    def cancel_all(self) -> None:
        """Reject every pending request (session shutdown / cancel)."""
        for request_id in list(self._pending):
            self.resolve(request_id, REJECT)
