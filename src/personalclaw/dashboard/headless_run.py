"""A headless ``personalclaw run``'s chat, and the Trust its ``--allow`` gives it for the run.

``run --allow`` trusts the run's own chat (``inbound:cli:<name>``, ``cli_run.grant_writes``) so
that its turn's calls are approved with nobody there to approve them. That Trust is the run's, and
nobody watches the turn it answers, so it lasts as long as the run and no longer:
:func:`end_the_runs_trust` withdraws it when the chat's turn ends, however it ended, and when the
turn is stopped, before its runtime is asked to stop, so no call is approved on it while the turn
winds down. It holds when the command could not say so itself (it was killed, or nothing reached
the gateway): the turn's own end withdraws the Trust.

A turn the chat runs after that (a helper's report the run did not wait for, the next run of a
named session) runs on what its own run grants it, or on nothing: its calls ask, and with nobody
there to answer they are declined. A posture the agent's approval floor seeded is the floor's,
and it ends with the floor (``chat_runner._apply_approval_floor``). Any other chat's posture is
its owner's, and stays as she set it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from personalclaw.cli_run import CLI_SESSION_PREFIX
from personalclaw.sel import sel

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)


def end_the_runs_trust(state: DashboardState, session: _ChatSession, *, why: str) -> bool:
    """Withdraw the Trust a headless run gave its chat *session*, and hand the chat's runtime the
    policy that leaves, so its next call asks. *why* is what the audit row says ended it.

    Returns whether a Trust stood. A chat that is not a headless run's is left as it is.
    """
    if not session.key.startswith(CLI_SESSION_PREFIX) or session._trust_from_floor:
        return False
    if not (session._trust or session._trust_reads):
        return False
    session._trust = False
    session._trust_reads = False
    state.push_chat_policy(session)
    state.push_sessions_update()
    try:
        sel().log_api_access(
            caller=f"dashboard:{session.key}",
            operation="mode_change:run_trust_ended",
            outcome="disabled",
            resources=session.key,
            metadata={"why": why},
        )
    except Exception:
        logger.warning("SEL audit failed for the end of %s's Trust", session.key, exc_info=True)
    return True
