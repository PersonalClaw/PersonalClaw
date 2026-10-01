"""The tool tier one subagent is held to, and how its calls came out.

**What it is shown, and what it may use.** A subagent's tool grant (``spawn_research`` → ``read``,
``spawn_mutating`` → ``read_write``, narrowed by the operator ceiling) is one answer asked in
two places: by its runtime before its own approval, and by the approval loop for a call that asks
(:meth:`SubagentTier.refusal`, the grant said the way the tier is shown). On PersonalClaw's own
loop a tier that narrows what the agent may use also narrows what its model is SHOWN, from the
same grant (``guardrails.policy.offer_refusal``): a read-only agent handed write tools spent its
turns on calls that could only be refused.

**How it ended.** A subagent whose every tool call was refused did nothing it was asked, whatever
its reply says: a read-only review subagent that could not read the repository it was sent to
answered with an apology, and ended "completed". :class:`CallTally` counts each call once, where it
was decided (refused when it was asked, or refused or run at its result), and
:func:`refused_every_call` is the error such a subagent ends with, naming the tools and why.
:func:`couldnt_do_it` is how a reader of that ending (a workflow step's settlement) tells it apart
from any other failure. A subagent whose own limits refused SOME of its calls (its tier, or an
approval nobody was there to give) ran, but may not have done all it was asked:
:meth:`SubagentTier.limited` names those calls, and its trigger's history records the run as
``refused`` rather than as a success (``triggers.settle``).

**Where it comes from.** :func:`tier_for` builds a subagent's tier from what its spawn was handed:
its capability class (§4.1), the files it may change and, for an automation's own agent, the
message it may send its owner. An automation's step is turned into those by
``automation_posture.agent_run_policy``, which its Allow is said from too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from personalclaw.guardrails.policy import (
    TOOL_READ_WRITE,
    SafetyProfile,
    granted_call_refusal,
    offer_refusal,
)
from personalclaw.llm.events import TOOL_META_AUTO_DENIED, TOOL_META_NOT_RUN, TOOL_META_REFUSED_BY

if TYPE_CHECKING:
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.subagent import SubagentInfo

#: How a subagent ends that did nothing it was asked: every tool call it made was refused. It is
#: its ERROR, so every reader of how it ended (its workflow step, its completion note, the
#: background-agents list) reads it as not done, and its own reply stays its result.
_COULDNT = "Couldn't do its task"

#: How many refusals that sentence names before it counts the rest.
_REFUSALS_NAMED = 3

#: Why a call its runtime did not run was not run, by what the runtime stamped on its result
#: (`llm.events`): the gate that refused it, or what it lacked.
_REFUSED_BY_WHY = {
    "deny_list": "it is on the deny list",
    "task_mode": "the run's mode does not run it",
    "hook": "a hook blocked it",
    "unknown_tool": "there is no tool by that name",
}
_NOT_RUN_WHY = {
    "missing_arguments": "it was sent without arguments the tool requires",
    "refused_by_tool": "the tool refused it",
}


def refused_every_call(refusals: list[tuple[str, str]]) -> str:
    """The error a subagent ends with when every tool call it made was refused: the tools, each
    with why, each pair once."""
    distinct = list(dict.fromkeys(refusals))
    named = "; ".join(f"{tool}: {why}" for tool, why in distinct[:_REFUSALS_NAMED])
    more = len(distinct) - _REFUSALS_NAMED
    rest = f"; and {more} more" if more > 0 else ""
    return f"{_COULDNT}: every tool call it made was refused — {named}{rest}"


def couldnt_do_it(error: str) -> bool:
    """Whether *error* is :func:`refused_every_call`'s: the subagent did nothing it was asked."""
    return str(error or "").startswith(_COULDNT)


@dataclass
class CallTally:
    """What one subagent's tool calls came to: the ones refused, by call id, with the tool and why,
    and how many ran. A call counts once, where it was decided: refused when it was asked
    (:meth:`refuse`), or at its result (:meth:`result`). :class:`SubagentTier` leaves out the
    runtime's own discovery calls (`tool_search`, `tool_schema`), which do none of the task."""

    refused: dict[str, tuple[str, str]] = field(default_factory=dict)
    ran: int = 0
    #: The refused calls the run's own limits refused: its tier, or an approval nobody was there to
    #: give. Not a call the model sent malformed, nor one the owner's answer declined.
    limited: list[str] = field(default_factory=list)

    def refuse(self, call_id: str, tool: str, why: str, *, limit: bool = False) -> None:
        self.refused[call_id] = (tool, why)
        if limit and call_id not in self.limited:
            self.limited.append(call_id)

    def result(self, call_id: str, tool: str, meta: dict[str, Any], *, grant_said: str) -> None:
        """Count a call at its result: refused when its runtime stamped a refusal on it, ran
        otherwise (a call that ran and failed ran). A dry run's write is not run by design, and a
        stopped call ends the agent, so neither is a refusal."""
        if call_id in self.refused:
            return
        not_run = str(meta.get(TOOL_META_NOT_RUN) or "")
        refused_by = str(meta.get(TOOL_META_REFUSED_BY) or "")
        limit = False
        if not_run and not_run != "stopped":
            why = _NOT_RUN_WHY.get(not_run, "it could not be run")
        elif meta.get(TOOL_META_AUTO_DENIED):
            why, limit = "nobody could approve it", True
        elif refused_by == "tool_grants":
            why, limit = grant_said or "this run's tools do not include it", True
        elif refused_by and refused_by != "dry_run":
            why = _REFUSED_BY_WHY.get(refused_by, "it was refused")
        else:
            self.ran += 1
            return
        self.refuse(call_id, tool, why, limit=limit)

    def verdict(self) -> str:
        """:func:`refused_every_call` when the agent made calls and none of them ran, else ""."""
        if self.refused and not self.ran:
            return refused_every_call(list(self.refused.values()))
        return ""


#: Why the owner's answer refused a call, by how the ask ended.
_DECLINED_WHY = {
    "expired": "nobody answered it in time",
    "cancelled": "its approval ended before anyone answered",
}


class SubagentTier:
    """One subagent's tool tier, asked by its runtime and its approval loop alike, and the tally of
    what its calls came to.

    An automation's own agent (a trigger's fire, or its Run now) may also tell the owner something
    with `notify`, and nothing more (``owner_notices``): an automation that finds something has to
    be able to say so, and a research run changes nothing else. ``may_change`` is the files an
    automation was given to change (``write_scope``).
    """

    def __init__(
        self,
        profile: SafetyProfile,
        *,
        owner_notices: bool,
        may_change: tuple[str, ...],
        capability_class: str = "",
    ) -> None:
        self.profile = profile
        self.capability_class = capability_class
        self._owner_notices = owner_notices
        self._may_change = may_change
        # The last refusal of each tool, so the agent's ending can say why its calls were refused.
        self._said: dict[str, str] = {}
        # The runtime's own discovery calls (`tool_search`, …), which do none of the task.
        self._discovery: frozenset[str] = frozenset()
        self._tally = CallTally()

    def refusal(
        self,
        tool_name: str,
        declared: object = "",
        tool_kind: str = "",
        tool_input: object = None,
        *,
        proposes: bool = False,
        tells_owner: bool = False,
    ) -> str:
        """Why the tier refuses this call, or ``""``: ``guardrails.policy.granted_call_refusal``."""
        why = granted_call_refusal(
            self.profile,
            tool_name,
            declared,
            tool_kind,
            tool_input,
            proposes=proposes,
            tells_owner=tells_owner,
            owner_notices=self._owner_notices,
            may_change=self._may_change,
        )
        if why:
            self._said[tool_name] = why
        return why

    def hold(self, runtime: NativeAgentRuntime) -> None:
        """Hold PersonalClaw's own loop to the tier: every call it makes is asked :meth:`refusal`
        before its own approval (it answers an ask itself while a standing grant stands, so its
        calls never reach the approval loop), and a tier that narrows is what its model is shown."""
        self._discovery = runtime.META_TOOLS
        runtime.set_tool_grants(self.refusal)
        if self.profile.tool_grants != TOOL_READ_WRITE:
            runtime.set_tool_offer(
                partial(
                    offer_refusal,
                    self.profile,
                    owner_notices=self._owner_notices,
                    may_change=self._may_change,
                )
            )

    def refused(self, call_id: str, tool: str, why: str, *, limit: bool = False) -> None:
        """A call that asked, refused before it ran; *limit* when the run's own limits refused it
        (its tier, or nobody to approve it)."""
        self._tally.refuse(call_id, tool, why, limit=limit)

    def declined(self, call_id: str, tool: str, outcome: str) -> None:
        """A call that asked, refused by the answer to its ask (*outcome*, as the ask ended)."""
        self._tally.refuse(call_id, tool, _DECLINED_WHY.get(outcome, "it was declined"))

    def result(self, call_id: str, tool: str, meta: dict[str, Any]) -> None:
        """A call's result, with what its runtime stamped on it (nothing, for an agent CLI)."""
        if tool not in self._discovery:
            self._tally.result(call_id, tool, meta, grant_said=self._said.get(tool, ""))

    def verdict(self) -> str:
        """:func:`refused_every_call` when the agent made calls and none of them ran, else ""."""
        return self._tally.verdict()

    def limited(self) -> list[str]:
        """The tools of the calls the run's own limits refused, in the order they were made."""
        return [self._tally.refused[call_id][0] for call_id in self._tally.limited]


def tier_for(info: SubagentInfo) -> SubagentTier:
    """The tier *info*'s run is held to, from what its spawn was handed. A ceiling that will not
    resolve raises here, before the run starts, which fails the spawn CLOSED."""
    from personalclaw.guardrails.policy import TOOL_READ, tool_grant_posture
    from personalclaw.subagent import CAPABILITY_RESEARCH, resolve_capability_class

    capability = resolve_capability_class(
        capability_class=info.capability_class, approval_mode=info.approval_mode
    )
    # The class expressed as a TOOL-GRANT tier (§3 ``tool_grants``), intersected with the operator
    # ceiling. `research` → `read`, `mutating` → `read_write`; a ceiling's `tools` scope may narrow
    # either to `read` or to a `custom` allowlist, which is the only thing standing between a
    # composed ceiling value and a control nobody reads.
    profile = tool_grant_posture(
        f"spawn_{capability}", TOOL_READ if capability == CAPABILITY_RESEARCH else TOOL_READ_WRITE
    )
    return SubagentTier(
        profile,
        owner_notices=bool(info.trigger_id),
        may_change=info.may_change,
        capability_class=capability,
    )
