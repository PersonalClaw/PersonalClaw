"""How every session a loop runs is answered and paid for — decided once, from the loop's Mode.

A loop runs several kinds of session: its planner (the walkthrough's design pass and each step
pass), its stage worker, a worker per task, and the merges the scheduler makes of their work. Each
one takes its posture from :func:`of` and is armed by :func:`arm`, and its cycle message is framed
by :func:`frame`, so none of them can run looser than the Mode its loop was started under:

* **Attended** — a person answers each tool call the way a chat's calls are answered: the card on
  the loop's page, the bell, and the channel approvals go to. The session is told nothing about an
  autonomous run. Its model spend is the owner's own, like a chat's, so the daily cap for
  unattended work does not count it. The scheduler merges a task's work into the workspace only
  once its owner approves the merge (``kinds.sdlc``).
* **Unattended** — nobody is there to ask, so the session runs on a standing grant: its calls go
  ahead inside the deny-list, the hooks and the operator ceiling. Its cycle message says no person
  is there to reply, and its spend counts against the daily cap. The scheduler merges each finished
  task's work by itself.

A Mode that cannot be read — no loop, or an ``attended`` that is neither ``True`` nor ``False`` —
is the cautious reading of both halves: its sessions ask a person, and their spend still counts
against the cap.

Every check of whether anybody watches a piece of work reads the Mode too, by the key the work
runs under (:func:`unattended_by_key`, which ``session_keys.is_unattended`` asks for a loop's
sessions): an Unattended loop's sessions are refused a command that would stop or restart
PersonalClaw, resolve the safety profile of work nobody watches and are held to the autonomy
ladder's ceiling for it, as a scheduled job's are, and an Attended loop's are judged as a chat
someone is in. There the cautious reading of a Mode that cannot be read is the other one: nobody is
watching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Posture:
    """Who answers a loop session's tool calls (``asks``: a person), and whether its model spend
    counts against the daily cap for unattended work (``metered``)."""

    asks: bool
    metered: bool


ATTENDED = Posture(asks=True, metered=False)
UNATTENDED = Posture(asks=False, metered=True)
#: A Mode nothing can read: a person answers, and the spend still counts.
UNREADABLE = Posture(asks=True, metered=True)


def of(loop: Any) -> Posture:
    """The posture every session of *loop* runs under. Only an explicit ``attended`` of ``True`` or
    ``False`` is read as a Mode; anything else (no loop, a missing field, a value the store never
    writes) is :data:`UNREADABLE`, the cautious reading, and is logged."""
    mode = getattr(loop, "attended", None)
    if mode is True:
        return ATTENDED
    if mode is False:
        return UNATTENDED
    logger.warning(
        "loop %s: its Mode cannot be read (%r), so its sessions ask and their spend counts",
        getattr(loop, "id", "?"),
        mode,
    )
    return UNREADABLE


def unattended_by_key(key: str) -> bool:
    """Whether nobody watches the work of the loop session *key* (its stage worker's, a task
    worker's or its planner's, bare), by its loop's Mode read from the store now: ``False`` for an
    Attended loop, ``True`` for an Unattended one.

    A key that names no loop, a loop that is gone and a store that cannot be read are all judged
    unattended. Each check that asks this (the self-stop rule, the safety profile, the autonomy
    ladder's ceiling, the rules a subagent's report is handed back under, the check of an agent
    CLI's adapter) is stricter for work nobody watches, so that is the cautious reading here."""
    from personalclaw.loop import store
    from personalclaw.loop.manager import session_loop

    loop_id = session_loop(key)
    if not loop_id:
        return True
    try:
        loop = store.get(loop_id)
    except Exception:
        logger.warning(
            "loop %s: its Mode could not be read, so its work is judged unattended",
            loop_id,
            exc_info=True,
        )
        return True
    return getattr(loop, "attended", None) is not True


def arm(session: Any, posture: Posture, *, granted: bool = False) -> None:
    """Set who answers *session*'s tool calls, and whose spend it is, from *posture*.

    Set in full each time a loop arms a session, the per-session grants included, because a
    loop's Mode can change between its runs: a run must never carry an unattended run's grant into
    an attended one. ``granted`` is "This loop" from one of this run's own approval cards
    (``manager.grant_every_worker``), which an attended session keeps for the rest of the run. The
    agent's own standing grants re-seed at the session's next turn
    (``chat_runner._apply_approval_floor``).

    An agent CLI is told the mode that stops it asking only when nobody is there to ask.
    """
    unattended = not posture.asks
    session._unattended = unattended
    session._trust = unattended or granted
    session._trust_reads = False
    session._trust_from_floor = ""
    session._agent_floor_seeded = False
    session._spend_metered = posture.metered
    if getattr(session, "acp_provider", ""):
        session.acp_mode = "bypassPermissions" if unattended else ""


def frame(posture: Posture, message: str) -> str:
    """*message*, told that no person is there to reply when nobody answers the session's calls.

    A session a person answers is not framed: it may ask, and the person is there to answer it."""
    if posture.asks:
        return message
    from personalclaw.autonomous_framing import with_autonomous_framing

    return with_autonomous_framing(message)


def spend_metered(session: Any) -> bool:
    """Whether *session*'s model spend counts against the daily cap for unattended work, as its
    loop armed it (:func:`arm`). Only a session an Attended loop armed is the owner's own; any
    other session (one no loop armed, or one armed before this was decided) counts as it always
    did, by the axis it runs on."""
    return getattr(session, "_spend_metered", True) is not False
