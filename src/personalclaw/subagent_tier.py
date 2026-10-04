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
:func:`refused_every_call` is the error such a subagent ends with, naming the tools and why. A call
its owner answered with Deny is not one of those refusals: it is her decision for the work, kept as
hers (``SubagentInfo.declined_calls``, :mod:`personalclaw.declined_calls`), so what would run the
work again (a workflow loop's next cycle) does not put the same call to her by itself, and its
page says what she declined. Only her Deny is: an ask nobody answered, or one with nowhere to be
put to her, is refused as the run's own limits refuse a call.
One whose model ran into its output cap before it wrote its answer did nothing it was asked either:
two review subagents stopped at 8,192 output tokens on every call, wrote nothing, and ended
"completed" with "_No response._". :func:`ran_out_of_room` is that ending.
One started to read or change things on a model that can't use tools could only claim it had:
:func:`without_tools` is that ending, before its task is sent or, when its model refused the tools
mid-run, where it settles. :func:`couldnt_do_it` is how a reader of any of these endings (a workflow
step's settlement) tells it apart from any other failure, and :func:`out_of_room_ending` and
:func:`without_tools_ending` which one it is. A subagent
whose own limits refused SOME of its calls (its tier, or an approval nobody was there to give)
ran, but may not have done all it was asked: :meth:`SubagentTier.limited` names those calls, and
its trigger's history records the run as ``refused`` rather than as a success
(``triggers.settle``).

**How many calls it made.** :class:`CallBudget` counts each tool call once against the spawn's
budget (``max_turns``), at the first event that names it. Counted only when a call asked, a native
run's calls (which its runtime answers itself) counted nowhere: its live state read 0 turns and no
last tool while it worked, and its budget never held.

**Where it comes from.** :func:`tier_for` builds a subagent's tier from what its spawn was handed:
its capability class (§4.1), the files it may change and, for an automation's own agent, the
message it may send its owner. An automation's step is turned into those by
``automation_posture.agent_run_policy``, which its Allow is said from too.

**An app's run.** An app's agent work runs at the tier its manifest declares and its owner agreed
to at install (``apps.agent_tiers``; ``handlers/apps.api_app_agent_run``, and its scheduled jobs'
``apps/app_crons.start_job``): ``text`` at
:data:`CAPABILITY_TEXT`, ``read`` at the research class, ``tools`` at the mutating one, and the run
names its app (``SubagentInfo.app``), as does every agent the app's work starts, held to the same
tier (``apps.app_work``): a subagent its conversation, run or job spawns, each task of a batch it
starts and each step of a run it starts. That permission starts it (``approval_grants.APP``) and
approves none of its calls: the owner's standing grants (YOLO, a chat's Trust, the Approval mode, a
setting that approves every background call) are hers, for her own agents, so each call of an
app's agent that needs approval asks her, as its install consent says (``SubagentManager.
_grant_now``, ``GatewayOrchestrator._relay_grant``). A text run is shown no tool and may call
none, whatever the operator ceiling allows, and its model is handed the app's task alone: none of
her memory, lessons or history is put ahead of it, since what it answers goes back to the app. It
runs on the worker built with no tools (:func:`run_agent`). An agent CLI runs its own tools where
the host never sees them, so a run held to fewer tools than every one is refused there before its
task is sent (:func:`refuse_unheld`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from personalclaw.agents.tool_list import REFUSED_BY as AGENT_TOOLS
from personalclaw.approval_grants import YOU, ToolDecision
from personalclaw.declined_calls import declined_step
from personalclaw.guardrails.policy import (
    TOOL_READ_WRITE,
    SafetyProfile,
    granted_call_refusal,
    offer_refusal,
)
from personalclaw.llm.events import (
    TOOL_META_AUTO_DENIED,
    TOOL_META_NOT_RUN,
    TOOL_META_REFUSED_BY,
    AgentEvent,
    is_length_stop,
    out_of_room,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.stats import Stats
from personalclaw.subagent_persistence import update_state

if TYPE_CHECKING:
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.subagent import SubagentInfo

logger = logging.getLogger(__name__)

#: The class of a run that may use no tool at all, beside ``subagent.CAPABILITY_RESEARCH`` and
#: ``CAPABILITY_MUTATING``: an app's ``text`` tier. A subagent's class only; the workflow-leaf
#: vocabulary (``workflows.batch_compile.Capability``) has no leaf without tools.
CAPABILITY_TEXT = "text"

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
    "run_bounds": (
        "it reaches past the run's bounds (a host off the allowed hosts, or a file outside the "
        "folders the run works in), and nobody was there to allow it"
    ),
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


def ran_out_of_room(output_cap: int) -> str:
    """The error a subagent ends with when its model ran into its output cap (*output_cap*
    tokens, 0 when unknown) before it wrote its answer: the same task on the same model meets the
    same cap (on PersonalClaw's own loop, also after it was asked once more for a brief answer)."""
    return (
        f"{_COULDNT}: {out_of_room(output_cap)}. Give it a smaller task, or raise the model's "
        "output limit where its provider's settings have one."
    )


#: What :func:`without_tools` says after naming the model, by which its ending is told apart.
_STARTED_WITH_TOOLS = ", and it was started to work with them."


def without_tools(info: SubagentInfo, client: object) -> str:
    """The error a subagent ends with when it was started to read or change things and the model
    its runtime sends to can't use tools (``tool_less_model``), else ``""``: without them it could
    only claim it had. A run of the ``text`` class asked for no tools, and an agent CLI brings its
    own."""
    from personalclaw.llm.tool_use import cannot_use_tools, tool_less_model
    from personalclaw.subagent import resolve_capability_class

    model = tool_less_model(client)
    capability = resolve_capability_class(
        capability_class=info.capability_class, approval_mode=info.approval_mode
    )
    if not model or capability == CAPABILITY_TEXT:
        return ""
    return (
        f"{_COULDNT}: {cannot_use_tools(model)}{_STARTED_WITH_TOOLS} Choose a model that uses "
        "tools for Orchestration in Settings → Models, or start it on one."
    )


def refuse_without_tools(info: SubagentInfo, client: object) -> bool:
    """End *info*'s run refused, with why, when it was started to read or change things and the
    model its runtime sends to can't use tools (:func:`without_tools`); else leave it be and return
    ``False``. Called before the task is sent, so no call is made for it."""
    from personalclaw.llm.tool_use import tool_less_model

    refused = without_tools(info, client)
    if not refused:
        return False
    info.error, info.done = refused, True
    Stats().inc_subagent_failed()
    sel().log_tool_invocation(
        session_key=info.parent_session_key,
        source="subagent",
        tool_name="subagent_run",
        outcome="refused",
        metadata={
            "subagent_id": info.id,
            "model": tool_less_model(client),
            "reason": "model_cannot_use_tools",
        },
    )
    return True


def couldnt_do_it(error: str) -> bool:
    """Whether *error* is :func:`refused_every_call`'s, :func:`ran_out_of_room`'s or
    :func:`without_tools`'s: the subagent did nothing it was asked."""
    return str(error or "").startswith(_COULDNT)


def out_of_room_ending(error: str) -> bool:
    """Whether *error* is :func:`ran_out_of_room`'s: its model, not its tools, stopped it."""
    return str(error or "").startswith(f"{_COULDNT}: {out_of_room()}")


def without_tools_ending(error: str) -> bool:
    """Whether *error* is :func:`without_tools`'s: its model can't use the tools it was given."""
    text = str(error or "")
    return text.startswith(_COULDNT) and _STARTED_WITH_TOOLS in text


def ended_without_answering(
    tier: SubagentTier, ending: AgentEvent | None, reply: str
) -> tuple[str, str]:
    """How a subagent whose turn has ended did nothing it was asked, as ``(error, cause)``, or
    ``("", "")``: every call refused (``refused``), or its turn stopped at its model's output cap
    with nothing written (``output_cap``). *ending* is the turn's terminal event and *reply*
    everything it wrote."""
    refused = tier.verdict()
    if refused:
        return refused, "refused"
    if ending is not None and is_length_stop(ending.stop_reason) and not reply.strip():
        return ran_out_of_room(int(getattr(ending, "output_cap", 0) or 0)), "output_cap"
    return "", ""


class CallBudget:
    """One subagent's tool calls against its spawn's budget (``max_turns``), each counted once, by
    its id, at the first event that names it: the card PersonalClaw's own loop yields before its
    gates, or an agent CLI's ask."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counted: set[str] = set()

    async def past_it(
        self,
        info: Any,
        event: AgentEvent,
        call_id: str,
        *,
        fire: Callable[[str, Any, dict], Awaitable[None]],
        tombstone: Callable[[Any, str], None],
    ) -> bool:
        """Count *event*'s call, said live (``state.json``, and a ``subagent_tool`` event through
        *fire*); True when it is past the budget, and *info* has then ended failed for it."""
        if call_id in self._counted:
            return False
        self._counted.add(call_id)
        info.turns += 1
        info.last_tool = event.title or ""
        try:
            update_state(info.id, turns=info.turns, last_tool=info.last_tool)
        except Exception:  # noqa: BLE001 - the live state is a view; the count stands
            logger.debug("Failed to record the call for %s", info.id, exc_info=True)
        tool, _ = redact_exfiltration_urls(info.last_tool)
        tool, _ = redact_credentials(tool)
        await fire("subagent_tool", info, {"tool": tool, "tool_kind": event.tool_kind})
        if info.turns <= self.limit:
            return False
        info.error, info.done = f"turn_limit:{self.limit}", True
        Stats().inc_subagent_failed()
        logger.warning("Subagent %s hit turn limit (%d)", info.id, self.limit)
        tombstone(info, "turn_limit")
        return True


@dataclass
class CallTally:
    """What one subagent's tool calls came to: the ones refused, by call id, with the tool and why,
    the ones its owner declined, and how many ran. A call counts once, where it was decided:
    refused or declined when it was asked (:meth:`refuse`, :meth:`decline`), or at its result
    (:meth:`result`). :class:`SubagentTier` leaves out the runtime's own discovery calls
    (`tool_search`, `tool_schema`), which do none of the task."""

    refused: dict[str, tuple[str, str]] = field(default_factory=dict)
    ran: int = 0
    #: The refused calls the run's own limits refused: its tier, or an approval nobody was there to
    #: give. Not a call the model sent malformed, nor one the owner's answer declined.
    limited: list[str] = field(default_factory=list)
    #: The calls its owner answered with Deny, by call id, as `declined_calls.declined_step` keeps
    #: them: her answer, not a refusal, so they alone never make the run one that did nothing.
    declined: dict[str, dict[str, Any]] = field(default_factory=dict)

    def refuse(self, call_id: str, tool: str, why: str, *, limit: bool = False) -> None:
        self.refused[call_id] = (tool, why)
        if limit and call_id not in self.limited:
            self.limited.append(call_id)

    def decline(self, call_id: str, step: dict[str, Any]) -> None:
        self.declined[call_id] = step

    def result(self, call_id: str, tool: str, meta: dict[str, Any], *, grant_said: str) -> None:
        """Count a call at its result: refused when its runtime stamped a refusal on it, ran
        otherwise (a call that ran and failed ran). A dry run's write is not run by design, and a
        stopped call ends the agent, so neither is a refusal."""
        if call_id in self.refused or call_id in self.declined:
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
        elif refused_by == AGENT_TOOLS:
            # Its agent's own limit, as its tier is: a run held to it may not have done all of it.
            why, limit = "its agent's tool list does not include it", True
        elif refused_by and refused_by != "dry_run":
            why = _REFUSED_BY_WHY.get(refused_by, "it was refused")
        else:
            self.ran += 1
            return
        self.refuse(call_id, tool, why, limit=limit)

    def verdict(self) -> str:
        """:func:`refused_every_call` when none of the agent's calls ran and at least one was
        refused, else "". A call its owner declined is named in it as hers, but makes none: her
        Deny is her decision for the work, not something its tools could not do."""
        if self.refused and not self.ran:
            yours = [(step["tool"], "you declined it") for step in self.declined.values()]
            return refused_every_call([*self.refused.values(), *yours])
        return ""


#: Why a call was refused whose ask ended with nobody answering it, by how the ask ended.
_DECLINED_WHY = {
    "expired": "nobody answered it in time",
    "cancelled": "its approval ended before anyone answered",
}


class SubagentTier:
    """One subagent's tool tier, asked by its runtime and its approval loop alike, and the tally of
    what its calls came to.

    An automation's own agent (a trigger's fire, or its Run now) may also tell the owner something
    with `notify`, and nothing more (``owner_notices``): an automation that finds something has to
    be able to say so, and a research run changes nothing else. An app's agent may not, its
    scheduled job's included: its tier is the app's, and a read tier sends no message.
    ``may_change`` is the files an automation was given to change (``write_scope``).
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

    def offer(
        self,
        tool_name: str,
        declared: object = "",
        *,
        proposes: bool = False,
        tells_owner: bool = False,
    ) -> str:
        """Why the tier's model is not SHOWN *tool_name*, or ``""``:
        ``guardrails.policy.offer_refusal``."""
        return offer_refusal(
            self.profile,
            tool_name,
            declared,
            proposes=proposes,
            tells_owner=tells_owner,
            owner_notices=self._owner_notices,
            may_change=self._may_change,
        )

    def narrows(self) -> bool:
        """Whether the tier offers less than every tool."""
        return self.profile.tool_grants != TOOL_READ_WRITE

    def as_env(self) -> str:
        """The tier as the session of its run carries it to the tools served to it
        (``mcp_shared.TOOL_TIER_KEY``), which :func:`tier_from_env` reads back. Not the files an
        automation may change: only PersonalClaw's own file tools are held to them, and those read
        this tier itself (:meth:`hold`); a tool served to the session writes no file, and on an
        agent CLI the run gives them up (:func:`give_up_files_on_a_cli`)."""
        return json.dumps(
            {"capability": self.capability_class, "owner_notices": self._owner_notices}
        )

    def session_env(self, extra_env: Mapping[str, str]) -> dict[str, str]:
        """*extra_env*, with the tier when it narrows: the tools served to the run, PersonalClaw's
        own and an agent CLI's tool server alike, hold it to the tier from there
        (``mcp_shared.TOOL_TIER_KEY``)."""
        env = dict(extra_env)
        if self.narrows():
            from personalclaw.mcp_shared import TOOL_TIER_KEY

            env[TOOL_TIER_KEY] = self.as_env()
        return env

    def hold(self, runtime: NativeAgentRuntime) -> None:
        """Hold PersonalClaw's own loop to the tier: every call it makes is asked :meth:`refusal`
        before its own approval (it answers an ask itself while a standing grant stands, so its
        calls never reach the approval loop), and a tier that narrows is what its model is shown."""
        self._discovery = runtime.META_TOOLS
        runtime.set_tool_grants(self.refusal)
        if self.narrows():
            runtime.set_tool_offer(self.offer)

    def refused(self, call_id: str, tool: str, why: str, *, limit: bool = False) -> None:
        """A call that asked, refused before it ran; *limit* when the run's own limits refused it
        (its tier, or nobody to approve it)."""
        self._tally.refuse(call_id, tool, why, limit=limit)

    def declined(self, call_id: str, event: AgentEvent, decision: ToolDecision) -> None:
        """A call that asked, refused by how its ask ended (*decision*). Her Deny is kept as hers
        (:meth:`declined_steps`). An ask nobody answered in time, or one that ended first, is
        refused for that; one there was nowhere to put to her is refused by the run's own limits,
        as a call nobody could approve is."""
        tool = event.title or ""
        if decision.outcome == "rejected" and decision.decided_by == YOU:
            self._tally.decline(call_id, declined_step(tool, event.tool_input))
        elif decision.outcome in _DECLINED_WHY:
            self._tally.refuse(call_id, tool, _DECLINED_WHY[decision.outcome])
        else:
            self._tally.refuse(call_id, tool, "nobody could approve it", limit=True)

    def result(self, call_id: str, tool: str, meta: dict[str, Any]) -> None:
        """A call's result, with what its runtime stamped on it (nothing, for an agent CLI)."""
        if tool not in self._discovery:
            self._tally.result(call_id, tool, meta, grant_said=self._said.get(tool, ""))

    def verdict(self) -> str:
        """:meth:`CallTally.verdict`."""
        return self._tally.verdict()

    def limited(self) -> list[str]:
        """The tools of the calls the run's own limits refused, in the order they were made."""
        return [self._tally.refused[call_id][0] for call_id in self._tally.limited]

    def declined_steps(self) -> list[dict[str, Any]]:
        """The calls its owner declined, in the order she declined them."""
        return list(self._tally.declined.values())

    def settle(
        self, info: SubagentInfo, ending: AgentEvent | None, reply: str, *, no_tools: str = ""
    ) -> tuple[str, str]:
        """Put on *info* how its run ended: its error when it did nothing it was asked
        (:func:`ended_without_answering`, or *no_tools*: its model refused the tools it was
        offered during the run, :func:`without_tools`; an error it already had stands), the calls
        its own limits refused (``refused``) and the calls its owner declined (``declined_calls``).
        Returns the ``(error, cause)`` it ended without answering with, else ``("", "")``."""
        couldnt, cause = (
            (no_tools, "no_tools") if no_tools else ended_without_answering(self, ending, reply)
        )
        info.error = info.error or couldnt
        info.refused, info.declined_calls = self.limited(), self.declined_steps()
        return couldnt, cause


def tier_for(info: SubagentInfo) -> SubagentTier:
    """The tier *info*'s run is held to, from what its spawn was handed. A ceiling that will not
    resolve raises here, before the run starts, which fails the spawn CLOSED."""
    from personalclaw.subagent import resolve_capability_class

    capability = resolve_capability_class(
        capability_class=info.capability_class, approval_mode=info.approval_mode
    )
    # An automation's own agent may tell its owner what it found; an app's (its scheduled job's
    # included) is held to the app's tier, and a read tier sends no message.
    notices = bool(info.trigger_id) and not info.app
    return _tier(capability, owner_notices=notices, may_change=info.may_change)


def tier_from_env(raw: str) -> SubagentTier:
    """The tier a session carries (:meth:`SubagentTier.as_env`), read where the tools served to
    it run: an agent CLI's tool server, or PersonalClaw's own loop. Read fail-closed: a value that
    does not parse is a tier that may call no tool, the narrowest there is."""
    from personalclaw.subagent import CAPABILITY_MUTATING, CAPABILITY_RESEARCH

    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {}
    capability = str(data.get("capability") or "")
    if capability not in (CAPABILITY_TEXT, CAPABILITY_RESEARCH, CAPABILITY_MUTATING):
        capability = CAPABILITY_TEXT
    return _tier(capability, owner_notices=data.get("owner_notices") is True, may_change=())


def _tier(capability: str, *, owner_notices: bool, may_change: tuple[str, ...]) -> SubagentTier:
    from personalclaw.guardrails.policy import TOOL_CUSTOM, TOOL_READ, tool_grant_posture
    from personalclaw.subagent import CAPABILITY_RESEARCH

    # The class expressed as a TOOL-GRANT tier (§3 ``tool_grants``), intersected with the operator
    # ceiling. `research` → `read`, `mutating` → `read_write`; a ceiling's `tools` scope may narrow
    # either to `read` or to a `custom` allowlist, which is the only thing standing between a
    # composed ceiling value and a control nobody reads.
    #
    # `text` → `custom` with an EMPTY allowlist, which admits nothing
    # (`guardrails.policy.tool_grant_denial`), and it is NOT intersected with the ceiling: a ceiling
    # only narrows, nothing is narrower, and the ceiling's projection of a posture reads an empty
    # allowlist as no restriction (`ceiling._profile_gate`), which handed a text run the ceiling's
    # own allowlist, or its read-only tools.
    if capability == CAPABILITY_TEXT:
        profile = SafetyProfile(name=f"spawn_{capability}", tool_grants=TOOL_CUSTOM)
    else:
        profile = tool_grant_posture(
            f"spawn_{capability}",
            TOOL_READ if capability == CAPABILITY_RESEARCH else TOOL_READ_WRITE,
        )
    return SubagentTier(
        profile,
        owner_notices=owner_notices,
        may_change=may_change,
        capability_class=capability,
    )


def run_agent(capability_class: str | None, agent: str) -> str:
    """The agent a spawn at *capability_class* runs on: a text run's is the worker built with no
    tools to call (``agents.defaults.LITE_AGENT_NAME``), whichever agent asked for it, so every
    list of background agents names the agent that really runs it; any other's is *agent*."""
    from personalclaw.agents.defaults import LITE_AGENT_NAME

    return LITE_AGENT_NAME if (capability_class or "").strip().lower() == CAPABILITY_TEXT else agent


def give_up_files_on_a_cli(info: SubagentInfo, runtime: str) -> None:
    """Take back the files *info*'s run was handed to change when its runtime is an agent CLI
    (*runtime*, ``acp:<cli>``): a CLI changes files with its own tools, which no write scope can
    be held to (``write_scope.not_held_on``), so its run changes none of them and says why
    (``SubagentInfo.held_back``). PersonalClaw's own runtime (``native``) keeps them: its file
    tools are held to them. Called before the run's tier is built from what it may change; an agent
    that inherits a CLI from the session that started it is how such a run gets here."""
    if not (info.may_change and runtime.startswith("acp")):
        return
    from personalclaw import write_scope

    gave_up = write_scope.not_held_on(runtime, info.may_change)
    info.held_back = f"{info.held_back} {gave_up}".strip()
    info.may_change = ()


def refuse_unheld(info: SubagentInfo, agent: str) -> bool:
    """End *info*'s run refused, with why, when an app started it at fewer tools than every one
    and its runtime is an agent CLI (*agent*, or the default), which cannot be held to them; else
    leave it be and return ``False``. Called for a runtime that is not PersonalClaw's own, before
    the task is sent to it."""
    from personalclaw.subagent import CAPABILITY_MUTATING, resolve_capability_class

    capability = resolve_capability_class(
        capability_class=info.capability_class, approval_mode=info.approval_mode
    )
    if not info.app or capability == CAPABILITY_MUTATING:
        return False
    tools = "no tools" if capability == CAPABILITY_TEXT else "read-only tools"
    info.error = (
        f"{info.app}'s agent work may use {tools}, and only PersonalClaw's own agent can be held "
        f"to that: {agent or 'the default agent'} runs its own tools where PersonalClaw can't see "
        "them, so the task didn't run."
    )
    info.done = True
    Stats().inc_subagent_failed()
    sel().log_tool_invocation(
        session_key=info.parent_session_key,
        source="subagent",
        tool_name="subagent_run",
        outcome="refused",
        metadata={
            "subagent_id": info.id,
            "app": info.app,
            "capability_class": capability,
            "agent": agent or "",
            "reason": "runtime_cannot_be_held",
        },
    )
    return True
