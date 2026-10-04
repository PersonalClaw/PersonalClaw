"""A Temporary chat's workflow runs end with it, and are removed.

A Temporary chat is forgotten when its session ends (``dashboard.chat_forget``): its transcript, its
working folder and the files attached to it are deleted. A workflow run it starts is the chat's own
work, not work of its own: it keeps the chat's mode and runs on the chat's model (``ownership``),
and its record holds the chat's words, the inputs it was started with, what its steps produced and
its journal. Kept after the chat, that record would be read later by the agents of your other chats
(``workflow_status``, ``workflow_output``) and by you, so it goes with the chat. So do the runs it
started in turn, a subworkflow's or a ``run-workflow`` step's, each one more run of the same tree
(``root_run_id``) that keeps what its root keeps.

The workflow supervisor asks this on every poll (``watchdog.WorkflowWatchdog``):

* a live run of a tree whose Temporary chat has ended is stopped, saying so, through a controller,
  which closes what the run holds (its waits, approvals, Inbox rows and leases): :func:`to_stop`;
* once every run of such a tree has ended, each is deleted with everything it produced, its
  workspace torn down first (``service.delete_run``): :func:`to_remove`.

A tree's chat is the chat at the top of the work its root run was started for
(``memory_reads.reach_of``). It has ended when the root was started before this gateway was, since
every session an earlier gateway ran ended with it, or when that chat is a dashboard chat this
gateway no longer holds: deleted, evicted as inactive, or one whose session ended with the last
gateway. A run started for a channel's Temporary thread ends with the gateway that ran the thread. A
tree whose root is gone is its chat's work with nothing left to say the chat runs, so it has ended.

An Incognito chat's runs are kept, as its transcript is, and so is a run whose chat's mode nothing
could say: it is not known to be a Temporary chat's.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from personalclaw.workflows import ownership, store
from personalclaw.workflows.models import WorkflowRun, stamp_epoch

logger = logging.getLogger(__name__)

#: How a run its chat's end stopped reads: "Stopped because its Temporary chat ended."
ENDED = "its Temporary chat ended"

_DASHBOARD = "dashboard:"


def chat_ended(run: WorkflowRun, state: Any) -> bool:
    """Whether the Temporary chat the root run *run* was started for has ended (see the module
    docstring). *state* is the gateway's dashboard state: the chats it holds, and when it
    started."""
    started = getattr(state, "start_time", None)
    if isinstance(started, (int, float)) and stamp_epoch(run.created_at) < math.floor(started):
        # Its stamp names a whole second, so only a run from a second this gateway had not reached
        # is known to be an earlier gateway's.
        return True
    chats = getattr(state, "_sessions", None)
    if not isinstance(chats, dict):
        return False
    from personalclaw import memory_reads

    keys = memory_reads.reach_of(state, run.origin.session_key).keys
    chat = keys[-1] if keys else ""
    if not chat or not (chat.startswith(_DASHBOARD) or ":" not in chat):
        return False
    return chat.removeprefix(_DASHBOARD) not in chats


def _ended_trees(state: Any) -> list[list[WorkflowRun]]:
    """Every tree of Temporary runs whose chat has ended, each as its runs."""
    trees: dict[str, list[WorkflowRun]] = {}
    for run in store.runs_with_mode(ownership.MemoryMode.TEMPORARY.value):
        trees.setdefault(run.root_run_id or run.id, []).append(run)
    ended = []
    for root_id, runs in trees.items():
        root = next((run for run in runs if run.id == root_id), None)
        if root is None or chat_ended(root, state):
            ended.append(runs)
    return ended


def to_stop(state: Any) -> list[WorkflowRun]:
    """The live runs whose Temporary chat has ended: each is stopped, saying :data:`ENDED`."""
    return [run for runs in _ended_trees(state) for run in runs if not run.is_terminal]


def to_remove(state: Any) -> list[WorkflowRun]:
    """The runs whose Temporary chat has ended, of each such tree whose every run has ended: each
    is deleted with what it produced. A tree with a run still going waits for it, so its root,
    which says whose chat it is, goes last of all."""
    return [
        run
        for runs in _ended_trees(state)
        if all(run.is_terminal for run in runs)
        for run in sorted(runs, key=lambda run: run.id == (run.root_run_id or run.id))
    ]
