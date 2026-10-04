"""Which chat a channel thread continues, and on which channel, kept in one place.

A chat can be linked to a channel thread (a Telegram or Discord DM, a Slack thread, an email
thread), so that a message there continues it through the inbound door
(``channel_inbound._route_to_session``) and its answers go back there. The link is kept in ONE
place, the session store (``session_map.json``, through ``SessionManager``): the door asks it
which chat a thread continues, a chat reads its own link from it, and linking a thread writes it.
Nothing holds a copy in memory, so a restart loses no link and none goes stale. A map the
dashboard used to keep in memory, filled only when a link was made, was empty after every
restart, and the next message on a thread started a new chat with none of the conversation in it.

A link names the channel its thread is on, and that is the channel the chat answers on
(:func:`chat_channel`): a thread's id means nothing without the channel that issued it. Which
channel answered used to be read from the chat's origin tag, the channel it came from, which a
handoff set only for a chat that had none. So a chat that came from Telegram and was continued in
a Slack thread kept answering on Telegram with the Slack thread's ids, and nothing reached the
thread it was moved to. The origin tag still says where a chat came from; where it answers is
its link's.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

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


def chat_channel(state: DashboardState, name: str) -> str:
    """The channel the chat *name* continues on, as its link names it (``"slack"``), or ``""``
    when it is on no thread or its link names no channel.

    Where its answers, its notices and its approval prompts go
    (``DashboardState.channel_provider_for``), read from the store each time it is asked, like the
    link. A store that cannot be read answers none, and nothing is sent anywhere."""
    if state.sessions is None:
        return ""
    try:
        provider = state.sessions.get_channel_provider(dashboard_history_key(name))
    except Exception:  # noqa: BLE001 - see the docstring
        return ""
    return provider if isinstance(provider, str) else ""


def linked_chat(state: DashboardState, thread_key: str) -> _ChatSession | None:
    """The chat a message on the channel thread *thread_key* continues, or None.

    A chat that is not resident comes back from disk, so the answer is the same after a restart.
    None when the thread is linked to no chat here, or to one that is gone (deleted, archived, a
    Temporary chat that ended), and the message then starts a new chat, as it did before the
    restart. None, too, for a chat whose link names no channel (:func:`chat_channel`), since its
    answer could not reach the thread: a channel thread's own conversation opened from the chat
    list, which the channel's app answers itself, or a chat a channel app linked through the
    session store without naming its channel.
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
    if session is None or not chat_channel(state, name):
        return None
    return session


def link(state: DashboardState, name: str, thread_ts: str, channel_id: str, provider: str) -> None:
    """Link the chat *name* to the thread *thread_ts* on the channel *provider*, so a message
    there continues it and its answers go there.

    A chat that is not resident comes back from disk first, as the inbound door brings one back;
    a chat that is not there at all is linked to nothing. A thread continues one chat, so the
    chat that had the thread loses it, found in the store whether or not it is resident. The
    thread's own conversation, which a channel app keeps under the thread's id, keeps its entry:
    it is the app's. And a chat continues in one place, so the thread it was on loses it: a
    message there no longer reaches it, and when that thread is the owner's own DM it is told
    where the chat went (:func:`_tell_the_thread_it_left`). Linked again where it already is,
    nothing changes and nothing is said.
    """
    if state.sessions is None:
        return
    if name not in state._sessions:
        from personalclaw.dashboard.chat_persistence import resolve_session

        if resolve_session(state, name) is None:
            return
    key = dashboard_history_key(name)
    left = (*chat_link(state, name), chat_channel(state, name))
    held_by = state.sessions.get_session_for_thread(thread_ts)
    if isinstance(held_by, str) and held_by != key and held_by.startswith(DASHBOARD_SESSION_PREFIX):
        state.sessions.set_channel_link(held_by, "", "")
    state.sessions.set_channel_link(key, thread_ts, channel_id, channel_provider=provider)
    if left[0] and left[2] and left != (thread_ts, channel_id, provider):
        _tell_the_thread_it_left(state, name, left, provider)
    state.push_sessions_update()


#: Said on the owner's own DM a chat left, for another channel and for another conversation on the
#: same channel. A message there now starts a chat of its own (or reaches the channel's own
#: conversation for the thread), never the one that moved.
MOVED_TO_ANOTHER_CHANNEL = "This chat continues on {channel} now. Messages here no longer reach it."
MOVED_WITHIN_THE_CHANNEL = (
    "This chat continues in another {channel} conversation now. Messages here no longer reach it."
)


def _tell_the_thread_it_left(
    state: DashboardState, name: str, left: tuple[str, str, str], provider: str
) -> None:
    """Say on the thread the chat *name* was on (*left*: thread, channel id, channel) where it
    continues now, on the channel *provider*, when that thread is the owner's own DM there.

    Only there. A group, a shared channel, a DM with someone else or a correspondent's mail hears
    nothing: the note would tell other people where the owner went on with the conversation, and
    on a channel that speaks as the owner it would go out in the owner's name. The owner's DM is
    the one the channel itself opens for the owner's id (``open_dm(owner_id_for(channel))``), as
    every message for the owner finds it. Sent through the handle of the channel the thread is on,
    which masks the text, and only that one: another channel's handle given this thread's ids
    would post nowhere, or to someone else. With that channel not connected nothing is said. Sent
    as a task on the gateway's loop, so the link never waits on a channel, and a send that fails
    is logged and fails nothing else."""
    from personalclaw.channel_delivery import chat_channel_names, delivery_for

    thread, channel_id, was_on = left
    delivery = delivery_for(was_on)
    if delivery is None:
        logger.info("chat %s left a %s thread, which is not connected to be told", name, was_on)
        return
    shown = chat_channel_names().get(provider, provider)
    note = (MOVED_WITHIN_THE_CHANNEL if was_on == provider else MOVED_TO_ANOTHER_CHANNEL).format(
        channel=shown
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.info("chat %s left a %s thread with no loop here to tell it from", name, was_on)
        return

    async def _say(handle: Any) -> None:
        from personalclaw.config.credentials import owner_id_for

        try:
            owner = owner_id_for(was_on)
            theirs = str(await handle.open_dm(owner) or "") if owner else ""
            if not theirs or theirs != channel_id:
                logger.info(
                    "chat %s left a %s conversation that is not the owner's own DM: nothing is "
                    "said there",
                    name,
                    was_on,
                )
                return
            await handle.deliver_text(channel_id, note, thread)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning("could not tell the %s thread chat %s left", was_on, name, exc_info=True)

    task = loop.create_task(_say(delivery))
    tasks = getattr(state, "_background_tasks", None)
    if isinstance(tasks, set):
        tasks.add(task)
        task.add_done_callback(tasks.discard)
