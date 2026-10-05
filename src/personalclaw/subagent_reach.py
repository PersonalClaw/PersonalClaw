"""Which subagents a call reads: its own chat's, and every one for you.

A subagent (a helper) works for the chat that started it: its task is made of what was said in that
chat, and its report of what it read for it. So a chat's agent reads only that chat's helpers: the
list ``subagent_list`` shows, and the status and the report ``subagent_status`` reads, whether the
gateway still holds the helper, only its folder is left (a helper a restart stopped), or only the
copy of its report kept in the chat it was handed to (``subagent_report.kept``). One rule answers
all three (:func:`reader_of`, :meth:`Reader.reads`):

* **A call reads as the chat at the top of the work it is for.** Whose work the call is, is what its
  sign-in proves (``approval_answer.work_of_request``): an agent's tools name their chat with the
  gateway's internal credential, in the gateway and in the tool server an agent CLI runs alike, and
  an app's token is the app's own work, whatever chat the request names. The chat at the top is the
  one walk every memory read asks (``memory_reads.reach_of``): a helper's own call works for its
  chat, a nested helper's for its parent's, and a workflow step's for the chat that started its run.
* **A helper is the chat's at the top of the work it was started for,** by the same walk from the
  session it was started for: a nested helper is its parent's chat's, and a batch's task is the
  chat's whose batch started it.
* **Your own pages are no chat** (``dashboard:ui``): a helper started there is no chat's, and work a
  run started there does is its own.
* **You read every helper:** your signed-in session (the Background agents page, ``/api/spawn``,
  ``personalclaw spawn``), and a tool you run from your own pages.
* **A call whose chat cannot be known reads none,** never everyone's: one that proved no sign-in,
  and one whose walk cannot be read.

Another chat's helper reads as not found, in the words an id that never existed reads in, so the
answer does not say that it exists.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

from personalclaw import approval_answer, session_keys

if TYPE_CHECKING:
    from personalclaw.subagent import SubagentInfo

logger = logging.getLogger(__name__)

#: Your own pages: the owner at the dashboard, in no chat.
_YOUR_PAGES = session_keys.DASHBOARD_UI


def chat_of(state: Any, session_key: str) -> str:
    """The chat the work *session_key* names is done for: the last session of what it works for
    (``memory_reads.reach_of``), your own pages left out, so work started there is its own. ``""``
    for no work, and for work whose walk cannot be read: it reads no helper, and none reads it.

    *state* is the gateway's dashboard state, whose subagents and live chats the walk reads."""
    from personalclaw.memory_reads import reach_of

    try:
        keys = [key for key in reach_of(state, session_key).keys if key != _YOUR_PAGES]
    except Exception:  # noqa: BLE001 - security surface: an unreadable walk is no chat's (closed)
        logger.warning("could not tell which chat the work %r is for", session_key, exc_info=True)
        return ""
    return keys[-1] if keys else ""


@dataclass
class Reader:
    """Who reads a subagent: you, who read every one (:attr:`everyone`), or the work :attr:`work`
    names, which reads its own chat's (:attr:`chat`)."""

    state: Any
    work: str = ""
    everyone: bool = False
    _chats: dict[str, str] = field(default_factory=dict, repr=False)

    @cached_property
    def chat(self) -> str:
        """The chat whose helpers this reads, and whose kept reports (``subagent_report.kept``):
        for you, the chat your page names, if it names one."""
        if self.everyone:
            return "" if self.work == _YOUR_PAGES else self.work
        return chat_of(self.state, self.work)

    def reads(self, parent_session_key: str) -> bool:
        """Whether this reads the helper started for the session *parent_session_key*."""
        if self.everyone:
            return True
        if not self.chat:
            return False
        parent = parent_session_key or ""
        if parent not in self._chats:
            self._chats[parent] = chat_of(self.state, parent)
        return self._chats[parent] == self.chat

    def of(self, helpers: Iterable[SubagentInfo]) -> list[SubagentInfo]:
        """The helpers of *helpers* this reads, in their order."""
        return [info for info in helpers if self.reads(info.parent_session_key)]


def reader_of(request: Any, state: Any) -> Reader:
    """Who reads the subagents *request* asks about, from the principal it proved
    (``approval_answer.of_request``): you, for your signed-in session and for a tool you run from
    your own pages (one PersonalClaw's own process runs naming ``dashboard:ui``); else the work its
    sign-in proves it does (``approval_answer.work_of_request``), which reads its own chat's.

    Cheap: the chat at the top is walked when it is first read, so call :meth:`Reader.reads` off
    the event loop."""
    by = approval_answer.of_request(request)
    work = approval_answer.work_of_request(request)
    if by.kind == approval_answer.OWNER:
        return Reader(state, work=work, everyone=True)
    if by.kind == approval_answer.AGENT and work == _YOUR_PAGES:
        return Reader(state, work=work, everyone=True)
    return Reader(state, work=work)
