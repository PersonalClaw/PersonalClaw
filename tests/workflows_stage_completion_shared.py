"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_workflows_stage_completion.py``

Its checks:
``tests/rails/test_workflows_stage_completion_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from typing import Any


class _Info:
    """The subset of `SubagentInfo` (``subagent.py:306``) that a completion is read from."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _FakeSubagents:
    """Stands in for `SubagentManager` on exactly the two methods this path uses.

    `spawn` is what `engine.dispatch_stage` already calls; `get` is the SHIPPED lookup at
    ``subagent.py:1632``, already used by ``dashboard/handlers/sessions.py:388``. A fake
    exposing a third method would be testing a registry this fix must not add.
    """

    def __init__(self, *, finish_after: int = 1, error: str = "", known: bool = True) -> None:
        self.infos: dict[str, _Info] = {}
        self.spawns: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self._finish_after = finish_after
        self._error = error
        self._known = known

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.spawns) + 1}")
        self.spawns.append(kw)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        """Report the spawn as finished once the controller has asked `finish_after` times.

        Keyed on LOOKUPS, not on elapsed time: a fixed sleep would measure the tick cadence
        and a frozen clock would make every tick read "not yet" — both pass against a
        controller that never reconciles at all.
        """
        self.gets.append(agent_id)
        if not self._known:
            return None
        info = self.infos.get(agent_id)
        if info is not None and len(self.gets) >= self._finish_after:
            info.done = True
            info.error = self._error
            info.result = "" if self._error else "the stage's answer"
            info.reaped = bool(self._error)
        return info
