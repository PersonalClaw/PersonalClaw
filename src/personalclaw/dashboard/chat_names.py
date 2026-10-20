"""The name a new chat is given: ``chat-<number>-<second>``, and never one a chat already has.

A chat is known by its name: its transcript is saved under it, and every save rewrites that file
whole. The number in a new chat's name was a counter each gateway kept in memory, starting again at
1 every time it started, and the name was never checked against the chats already kept. A chat
opened in the second an earlier run opened one with the same number (a quick restart, or the clock
set back across one) was therefore given that chat's name, and its saves replaced that chat's
transcript.

So a new chat's number carries on past the highest number any kept chat has, and a name that is
taken, by a chat open here or by a transcript kept under it (an archived one included), is stepped
past. Names already given are kept as they are, and nothing renames a chat.

Two gateways running on one home at once can still open the same name in the same second, before
either has saved, since neither can see the other's unsaved chat. The save is what refuses that
case: it never replaces a transcript that is another chat's (``chat_persistence``). A deleted chat
leaves no transcript, so its name is free again: only a restart within the second it was opened in
(the clock set back that far) could give it to a new chat.

Every new chat is named here, through ``DashboardState.get_or_create_session`` with no name: a
chat opened in the dashboard, a Temporary or Incognito chat, a channel's new thread, a fork, an
investigation, a delivered result.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from personalclaw import session_keys
from personalclaw.dashboard.chat_utils import candidate_history_keys

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

#: A kept chat's transcript as the chat list names it: its key made a file name
#: (``dashboard_chat-<number>-<second>``), under any ``dashboard_`` prefixes an older version
#: stacked onto it.
_KEPT_CHAT = re.compile(r"(?:dashboard_)*chat-(\d+)-\d+")


def _highest_kept_number(state: DashboardState) -> int:
    """The highest number a chat kept in this home carries, or 0 when it keeps none."""
    log = state.conversation_log
    if log is None:
        return 0
    try:
        listed = log.list_sessions()
    except Exception:  # noqa: BLE001 — the names below are still each checked against the disk
        logger.warning("the kept chats could not be listed to number a new one", exc_info=True)
        return 0
    numbers = (_KEPT_CHAT.fullmatch(str(entry.get("key", ""))) for entry in listed)
    return max((int(found.group(1)) for found in numbers if found), default=0)


def _taken(state: DashboardState, name: str) -> bool:
    """Whether *name* is a chat's: one open here, or one whose transcript is kept under either key
    the name can be saved under. A disk that cannot answer counts it as taken, since stepping
    past a free name costs nothing and taking a kept one costs its transcript."""
    if name in state._sessions:
        return True
    log = state.conversation_log
    if log is None:
        return False
    try:
        return any(log.has_log(key) for key in candidate_history_keys(name))
    except OSError:
        logger.warning("could not tell whether chat name %s is taken", name, exc_info=True)
        return True


def new_chat_name(state: DashboardState) -> str:
    """A name for a chat opened now that no chat has: the next number, and this second."""
    if not state._session_counter:
        state._session_counter = _highest_kept_number(state)
    while True:
        state._session_counter += 1
        name = session_keys.CHAT.key(f"{state._session_counter}-{int(time.time())}")
        if not _taken(state, name):
            return name
