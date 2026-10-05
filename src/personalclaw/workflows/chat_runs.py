"""Which workflow runs are a chat's own, and who reads them.

A workflow run is yours: each of your agents reads it, is told of it while it runs, and works on it
with the workflow tools. Two kinds of run are a chat's own instead, because what they hold is that
chat's:

* **A batch**, the tasks a chat's agent started at once (``subagent_run`` with two or more). They
  are that chat's subagents (``subagent_reach``): each task is made of what was said in the chat,
  and each step's report of what it read for it.
* **A run a Temporary or Incognito chat started.** Its record keeps the chat's mode
  (``ownership.run_mode``), and its inputs, what its steps produced and its journal are the chat's:
  nothing of such a chat goes to another chat, or to any model but the one it runs on.

A run started from one of them belongs to its tree's root, which says whose it is: a fork keeps the
origin of the run it forks, and a run one of its steps started is its root's.

A chat's own run is read by that chat's work, by the walk every read of a chat's subagents makes
(``subagent_reach.Reader``: its agent, its subagents and the steps of its runs), and by you. To
anyone else it is no run at all, answered in the words an id that never existed is answered in, and
the refused read leaves an audit row. Every door asks :func:`reads`: the workflow tools a native
agent calls in the gateway and the spec they echo (``mcp_workflows``), the gateway's run routes,
which the tool server an agent CLI runs calls (``workflows.handlers``), the runs a chat's turn is
told of (``context_block``) and the repair of the run store (``audit``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import ownership, store
from personalclaw.workflows.models import OriginKind

if TYPE_CHECKING:
    from personalclaw.subagent_reach import Reader

logger = logging.getLogger(__name__)


def whose(run: Any) -> dict[str, Any] | None:
    """Whose chat's own run *run* is, judged at its tree's root, ``None`` for a run of yours: the
    ``session`` it was started for, the ``mode`` its chat keeps (``"temporary"``, ``"incognito"``,
    ``"unreadable"`` for one nothing could say, ``""`` for an ordinary chat), and whether it is a
    ``batch`` of the chat's subagents. Your Workflows page marks such a run with it."""
    root = run
    if run.root_run_id and run.root_run_id != run.id:
        root = store.get(run.root_run_id) or run
    batch = OriginKind.SUBAGENT_TOOL in (run.origin.kind, root.origin.kind)
    mode = next(
        (m for m in map(ownership.run_mode, (run, root)) if m is not ownership.MemoryMode.NORMAL),
        ownership.MemoryMode.NORMAL,
    )
    if not batch and mode is ownership.MemoryMode.NORMAL:
        return None
    return {
        "session": str(root.origin.session_key or run.origin.session_key or ""),
        "mode": "" if mode is ownership.MemoryMode.NORMAL else mode.value,
        "batch": batch,
    }


def reads(reader: Reader, run: Any) -> bool:
    """Whether *reader* reads the run *run*: every run for you, and a chat's own only for the work
    of that chat (:func:`whose`). One whose chat cannot be told is read by you alone."""
    if reader.everyone:
        return True
    chat = whose(run)
    return chat is None or reader.reads(str(chat["session"]))


def reads_id(reader: Reader, run_id: str) -> bool:
    """Whether *reader* reads the run *run_id*, or there is no such run to read: a read of it is
    then answered that there is none."""
    if reader.everyone or not run_id:
        return True
    run = store.get(run_id)
    return run is None or reads(reader, run)


def hidden(reader: Reader, run_id: str, *, operation: str) -> bool:
    """Whether the call *reader* makes for *operation* names a run that is another chat's own,
    which it is answered as one that does not exist. Such a call is audited, naming who asked and
    for which run, whether or not the row could be written."""
    if reads_id(reader, run_id):
        return False
    from personalclaw.sel import sel

    try:
        sel().log_api_access(
            caller=reader.work or "unknown",
            operation=operation,
            outcome="denied",
            resources=f"workflow_run:{run_id}",
            error="the run is another chat's",
        )
    except Exception:  # noqa: BLE001 - the answer stands whether or not it is written down
        logger.warning("could not audit a refused read of workflow run %s", run_id, exc_info=True)
    return True


def live_runs_read(reader: Reader) -> frozenset[str]:
    """The ids of the live runs *reader* reads: what a repair of the run store it asks for scans
    (``audit``)."""
    return frozenset(run.id for run in store.active_runs() if reads(reader, run))
