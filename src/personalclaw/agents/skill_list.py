"""Which skills an agent may use: its skill list (``agents.<name>.skills``, on the Agents page).

An agent with no list (the default, and the default agent's) may use every skill. A list is the
whole of what the agent may use, and each entry is a skill's name as the Skills page lists it
(``tiny-url``, ``auto/release``). On PersonalClaw's own runtime a list holds in both places a skill
reaches an agent's model:

* **Where a turn is assembled** (``ContextBuilder``): the turn is offered the agent's skills, the
  always-on ones in full and an index of the rest when its session starts, and the ones that fit
  the message on every turn. An agent with no list is offered what it always was: the default agent
  every skill, and another agent, which carries its own instructions, none (it can look one up with
  ``skill_search``).
* **Its skill tools** (``skill_search``, ``skill_invoke``, ``skill_resource``): a search finds only
  the agent's skills, and a load of any other is refused, naming the skill and the agent. The
  runtime holds the list it was built with (:func:`hold`) while it dispatches a call, which is how
  those tools know whose call it is.

A skill in the agent's own folder (``<home>/agents/<agent>/skills``) is its own, and a list does not
narrow it. A loop's own skills, the ones its plan gives the phase it works and that you confirm when
you review the plan (``loop.kinds.worker_turn``), load on its turns beside the agent's, and its
skill tools reach them (:meth:`AgentSkills.beside`).

An agent CLI (``acp:<cli>``) loads its own skills, where no list of PersonalClaw's can hold them, so
the list does not apply to an agent that runs on one, and the Agents page says so.

A list that cannot be read (a hand-edited value that is not a list of names) limits nothing: a skill
is instructions, not a capability, so an unreadable list fails OPEN, to what the agent is offered
with no list, and the gateway log says so. The same holds while ``config.json`` itself cannot be
read. What an agent can do is limited by its tool list (``agents.tool_list``).

This is the one reader of the setting (:func:`agent_skills`) and the one matcher
(:meth:`AgentSkills.allows`), so what a turn is offered, what its skill tools reach and what the
Skills page says an agent uses cannot disagree.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.config.loader import AppConfig
    from personalclaw.skills.loader import SkillsLoader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentSkills:
    """What one agent's skill list allows. The default allows every skill."""

    #: The agent, as the sentences below name it.
    agent: str = ""
    #: The list's skill names. Empty with ``listed`` False is every skill.
    names: tuple[str, ...] = ()
    #: Whether the agent has a list that holds it.
    listed: bool = False
    #: Whether the list is PersonalClaw's own, a built-in agent's, which no page edits.
    fixed: bool = False

    @classmethod
    def of(cls, agent: str, value: Any, *, fixed: bool = False) -> "AgentSkills":
        """The list *value* (an agent profile's ``skills``) of *agent*; *fixed* when the agent is a
        built-in one, whose list is PersonalClaw's own.

        Absent or empty is no list. A list keeps its entries that are names; a value that is not a
        list, or a list with no name in it, could not be read, and holds the agent to nothing.
        """
        if value is None or value == [] or value == ():
            return cls(agent=agent, fixed=fixed)
        if isinstance(value, (list, tuple)):
            names = tuple(
                dict.fromkeys(
                    str(v).strip() for v in value if isinstance(v, str) and str(v).strip()
                )
            )
            if names:
                return cls(agent=agent, names=names, listed=True, fixed=fixed)
        # Fail OPEN, as the module docstring says: an unreadable list names no skill it limits.
        logger.warning(
            "the skill list of the agent %s could not be read (%s): its turns are offered skills "
            "as an agent with no list is",
            agent,
            type(value).__name__,
        )
        return cls(agent=agent, fixed=fixed)

    def allows(self, name: str) -> bool:
        """Whether the agent may use the skill named *name*."""
        return not self.listed or name in self.names

    def refusal(self, name: str) -> str:
        """Why the skill *name*, which the list does not allow, is not the agent's to load: the
        skill and the agent, and where the list is set."""
        if self.fixed:
            return (
                f"the built-in agent {self.agent} may use only the skills PersonalClaw gives it, "
                f"and {name} is not one of them"
            )
        return (
            f"the agent {self.agent} may use only the skills on its skill list, set on the Agents "
            f"page, and {name} is not one of them"
        )

    def beside(self, names: Iterable[str]) -> "AgentSkills":
        """This list with the skills *names* allowed beside it: a loop's own skills on its turns.
        No list stays no list, which allows them already."""
        extra = tuple(
            dict.fromkeys(
                n.strip()
                for n in names
                if isinstance(n, str) and n.strip() and n.strip() not in self.names
            )
        )
        if not self.listed or not extra:
            return self
        return replace(self, names=self.names + extra)

    def library(self, loader: "SkillsLoader") -> "SkillsLoader":
        """*loader* as this agent sees it: narrowed to the skills its list allows, or *loader*
        itself when it has no list."""
        if not self.listed:
            return loader
        from personalclaw.skills.loader import narrowed

        return narrowed(loader, self.allows)


def agent_skills(agent: str | None, cfg: "AppConfig | None") -> AgentSkills:
    """The skill list of the agent a turn runs as, read from *cfg*: the configuration the turn is
    built from, or ``None`` when it could not be loaded.

    *agent* as a turn names it: a profile's name, the default native agent under any of its
    spellings, or empty for the default agent (``default_agent``), which a chat that names no agent
    runs as. The same agent its instructions and its tool list are read for, so a list set on the
    default agent holds its chats too.

    No list holds a name no profile has (an agent CLI's own agent, picked in a chat), nor an agent
    that runs on an agent CLI (its ``provider``, else the global ``agent.provider``, is ``acp…``),
    nor any agent while the configuration cannot be read.
    """
    from personalclaw.agents.defaults import (
        default_agent_name,
        is_reserved_agent,
        normalize_agent_name,
    )

    wanted = (agent or "").strip()
    key = normalize_agent_name(wanted) if wanted else default_agent_name(cfg)
    if cfg is None:
        return AgentSkills(agent=key)
    profile = (getattr(cfg, "agents", None) or {}).get(key)
    if profile is None:
        return AgentSkills(agent=key)
    runtime = str(
        getattr(profile, "provider", "") or getattr(getattr(cfg, "agent", None), "provider", "")
    )
    if runtime.startswith("acp"):
        return AgentSkills(agent=key)
    return AgentSkills.of(key, getattr(profile, "skills", None), fixed=is_reserved_agent(key))


#: The skills of the agent whose tool call PersonalClaw's own runtime is dispatching, held for the
#: call (:func:`hold`). ``None`` where no runtime holds one: an agent CLI's tool server, which no
#: list of PersonalClaw's holds, and a call made outside any turn.
_HELD: contextvars.ContextVar[AgentSkills | None] = contextvars.ContextVar(
    "personalclaw_agent_skills", default=None
)


def hold(skills: AgentSkills) -> contextvars.Token:
    """Hold *skills* for the tool calls dispatched in this context; returns the token
    :func:`let_go` takes."""
    return _HELD.set(skills)


def let_go(token: contextvars.Token) -> None:
    """Give back what :func:`hold` held."""
    try:
        _HELD.reset(token)
    except (ValueError, LookupError):
        pass


def held() -> AgentSkills | None:
    """The skills held for the call being dispatched, or ``None`` when no runtime holds any."""
    return _HELD.get()
