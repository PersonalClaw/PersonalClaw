"""Which chat a channel thread continues, kept in one place.

A chat can be linked to a channel thread (a Telegram or Discord DM, a Slack thread, an email
thread), so that a message there continues it through the inbound door
(``channel_inbound._route_to_session``) and its answers go back there. The link is kept in ONE
place, the session store (``session_map.json``, through ``SessionManager``): the door asks it
which chat a thread continues, a chat reads its own link from it, and linking a thread writes it.
Nothing holds a copy in memory, so a restart loses no link and none goes stale. A map the
dashboard used to keep in memory, filled only when a link was made, was empty after every
restart, and the next message on a thread started a new chat with none of the conversation in it.

Which channel a chat answers on is not kept here: it is the chat's origin tag
(``DashboardState.channel_provider_for``), which rides the chat's meta line across restarts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from personalclaw.constants import DASHBOARD_SESSION_PREFIX, dashboard_history_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)


def chat_link(state: DashboardState, name: str) -> tuple[str, str]:
    """The channel thread the chat *name* continues on, as ``(thread, channel id)``, or
    ``("", "")`` when it is on none.

    Read from the store each time it is asked, whoever wrote the link: :func:`link`, or a channel
    app through the SDK's ``SessionManager``. A store that cannot be read answers no link."""
    if state.sessions is None:
        return "", ""
    try:
        thread, channel_id = state.sessions.get_channel_link(dashboard_history_key(name))
    except Exception:  # noqa: BLE001 - see the docstring
        return "", ""
    if not isinstance(thread, str) or not thread:
        return "", ""
    return thread, channel_id if isinstance(channel_id, str) else ""


def linked_chat(state: DashboardState, thread_key: str) -> _ChatSession | None:
    """The chat a message on the channel thread *thread_key* continues, or None.

    A chat that is not resident comes back from disk, so the answer is the same after a restart.
    None when the thread is linked to no chat here, or to one that is gone (deleted, archived, a
    Temporary chat that ended), and the message then starts a new chat, as it did before the
    restart. None, too, for a chat that carries no channel to answer on
    (``channel_provider_for``), since its answer could not reach the thread: a channel thread's
    own conversation opened from the chat list, which the channel's app answers itself, or a
    chat a channel app linked without saying it is that channel's.
    """
    if not thread_key or state.sessions is None:
        return None
    try:
        key = state.sessions.get_session_for_thread(thread_key)
    except Exception:  # noqa: BLE001 - an unreadable store links nothing: a new chat answers
        logger.warning("channel thread %s: its chat could not be read", thread_key, exc_info=True)
        return None
    if not isinstance(key, str) or not key.startswith(DASHBOARD_SESSION_PREFIX):
        return None
    name = key.removeprefix(DASHBOARD_SESSION_PREFIX)
    from personalclaw.dashboard.chat_persistence import resolve_session

    session = resolve_session(state, name)
    if session is None or not state.channel_provider_for(name):
        return None
    return session


def link(state: DashboardState, name: str, thread_ts: str, channel_id: str) -> None:
    """Link the resident chat *name* to a channel thread, so a message there continues it.

    A thread continues one chat, so the chat that had the thread loses it, found in the store
    whether or not it is resident. The thread's own conversation, which a channel app keeps under
    the thread's id, keeps its entry: it is the app's.
    """
    if name not in state._sessions or state.sessions is None:
        return
    key = dashboard_history_key(name)
    held_by = state.sessions.get_session_for_thread(thread_ts)
    if isinstance(held_by, str) and held_by != key and held_by.startswith(DASHBOARD_SESSION_PREFIX):
        state.sessions.set_channel_link(held_by, "", "")
    state.sessions.set_channel_link(key, thread_ts, channel_id)
    state.push_sessions_update()
