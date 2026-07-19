"""Workflow lifecycle hooks (EVOLVE-WORKFLOWS #28).

End-of-session cleanup: when a chat session ends, sweep the ephemeral
SESSION-scoped workflows an agent authored for it (unless they were promoted to a
wider scope, which moves them off session scope). Composed onto the existing
session-expire callback so it runs ALONGSIDE consolidation, not instead of it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def with_session_workflow_cleanup(
    prior: Callable[[str], Awaitable[None]] | None,
) -> Callable[[str], Awaitable[None]]:
    """Wrap a session-expire callback so it also sweeps session-scoped workflows.

    Runs *prior* (e.g. consolidation) first, then deletes the ending session's
    SESSION-scoped workflows. Each step is independent + best-effort: a failure in
    one never blocks the other or the session reset that follows.
    """

    async def _expire(session_key: str) -> None:
        if prior is not None:
            try:
                await prior(session_key)
            except Exception:
                logger.warning("session-expire prior callback failed for %s", session_key, exc_info=True)
        try:
            from personalclaw.workflows.registry import delete_session_workflows

            await delete_session_workflows(session_key)
        except Exception:
            logger.debug("session workflow cleanup failed for %s", session_key, exc_info=True)

    return _expire
