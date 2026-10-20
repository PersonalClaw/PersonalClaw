"""A Temporary or Incognito chat's workflow runs end with it, and are removed.

A Temporary chat is forgotten when its session ends (``dashboard.chat_forget``): its transcript, its
working folder and the files attached to it are deleted. An Incognito chat is kept, its transcript
with it, until you delete it. A workflow run either one starts is the chat's own work, not work of
its own: it keeps the chat's mode and runs on the chat's model (``ownership``), and its record holds
the chat's words, the inputs it was started with, what its steps produced and its journal; a
batch's run holds the chat's tasks (``batch_start``). While the chat lives, that record is the
chat's alone (``chat_runs``). Kept after the chat, it would outlive what it came from, so it goes
with the chat: a Temporary chat's runs when its session ends, an Incognito chat's when you delete
it. So do the runs it started in turn, a subworkflow's or a ``run-workflow`` step's, each one more
run of the same tree (``root_run_id``) that keeps what its root keeps.

The workflow supervisor asks this on every poll (``watchdog.WorkflowWatchdog``):

* a live run of a tree whose chat has ended is stopped, saying so (:func:`ended_because`), through
  a controller, which closes what the run holds (its waits, approvals, Inbox rows and leases):
  :func:`to_stop`;
* once every run of such a tree has ended, each is deleted with everything it produced, its
  workspace torn down first (``service.delete_run``): :func:`to_remove`.

A tree's chat is the chat at the top of the work its root run was started for
(``memory_reads.reach_of``). A Temporary chat has ended when the root was started before this
gateway was, since every session an earlier gateway ran ended with it, or when that chat is a
dashboard chat this gateway no longer holds: deleted, evicted as inactive, or one whose session
ended with the last gateway. A run started for a channel's Temporary thread ends with the gateway
that ran the thread. An Incognito chat has ended when it is a dashboard chat this gateway no longer
holds whose transcript is gone too: you deleted it (:func:`_transcript_kept`, which counts a
transcript it cannot check as kept). A tree whose root is gone is its chat's work with nothing left
to say the chat runs, so it has ended.

A run whose chat's mode nothing could say is kept: it is not known to be either chat's.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from personalclaw.workflows import ownership, store
from personalclaw.workflows.models import WorkflowRun, stamp_epoch

logger = logging.getLogger(__name__)

#: How a run its chat's end stopped reads ("Stopped because its Temporary chat ended."), by the mode
#: it keeps.
ENDED = {
    ownership.MemoryMode.TEMPORARY: "its Temporary chat ended",
    ownership.MemoryMode.INCOGNITO: "its Incognito chat was deleted",
}

_DASHBOARD = "dashboard:"


def ended_because(run: WorkflowRun) -> str:
    """Why the run *run* is stopped, its chat having ended: in the words of the mode it keeps."""
    return ENDED.get(ownership.run_mode(run), ENDED[ownership.MemoryMode.TEMPORARY])


def chat_ended(run: WorkflowRun, state: Any) -> bool:
    """Whether the Temporary or Incognito chat the root run *run* was started for has ended (see
    the module docstring). *state* is the gateway's dashboard state: the chats it holds, the
    transcripts it keeps, and when it started."""
    return has_ended(
        state,
        session_key=run.origin.session_key,
        mode=ownership.run_mode(run),
        since=stamp_epoch(run.created_at),
    )


def has_ended(state: Any, *, session_key: str, mode: ownership.MemoryMode, since: float) -> bool:
    """Whether the chat at the top of the work *session_key* names has ended, when it keeps
    nothing as *mode* says (Temporary or Incognito), for work started for it at *since* (an epoch):
    a run, or a batch waiting for its ask. Work of a chat in any other mode is kept."""
    if mode not in ENDED:
        return False
    temporary = mode is ownership.MemoryMode.TEMPORARY
    started = getattr(state, "start_time", None)
    if temporary and isinstance(started, (int, float)) and since < math.floor(started):
        # A run's stamp names a whole second, so only work from a second this gateway had not
        # reached is known to be an earlier gateway's.
        return True
    chats = getattr(state, "_sessions", None)
    if not isinstance(chats, dict):
        return False
    from personalclaw import memory_reads

    keys = memory_reads.reach_of(state, session_key).keys
    chat = keys[-1] if keys else ""
    if not chat or not (chat.startswith(_DASHBOARD) or ":" not in chat):
        return False
    name = chat.removeprefix(_DASHBOARD)
    if name in chats:
        return False
    return temporary or not _transcript_kept(state, name)


def _transcript_kept(state: Any, name: str) -> bool:
    """Whether the dashboard chat *name* still has its transcript, under either key a chat's log
    is kept by (the bare one and the ``dashboard:`` one, as ``chat_utils.candidate_history_keys``
    says). A log there is none of, or one that cannot be checked, counts as kept."""
    log = getattr(state, "conversation_log", None)
    if log is None:
        return True
    try:
        return any(log.has_log(key) for key in (name, f"{_DASHBOARD}{name}"))
    except Exception:  # noqa: BLE001 — a transcript that cannot be checked is not known gone
        logger.warning("could not check whether chat %s still has its transcript", name)
        return True


def _ended_trees(state: Any) -> list[list[WorkflowRun]]:
    """Every tree of a Temporary or Incognito chat's runs whose chat has ended, each as its
    runs."""
    trees: dict[str, list[WorkflowRun]] = {}
    for mode in ENDED:
        for run in store.runs_with_mode(mode.value):
            trees.setdefault(run.root_run_id or run.id, []).append(run)
    ended = []
    for root_id, runs in trees.items():
        root = next((run for run in runs if run.id == root_id), None)
        if root is None or chat_ended(root, state):
            ended.append(runs)
    return ended


def to_stop(state: Any) -> list[WorkflowRun]:
    """The live runs whose Temporary or Incognito chat has ended: each is stopped, saying
    :func:`ended_because`."""
    return [run for runs in _ended_trees(state) for run in runs if not run.is_terminal]


def to_remove(state: Any) -> list[WorkflowRun]:
    """The runs whose Temporary or Incognito chat has ended, of each such tree whose every run has
    ended: each is deleted with what it produced. A tree with a run still going waits for it, so
    its root, which says whose chat it is, goes last of all."""
    return [
        run
        for runs in _ended_trees(state)
        if all(run.is_terminal for run in runs)
        for run in sorted(runs, key=lambda run: run.id == (run.root_run_id or run.id))
    ]
