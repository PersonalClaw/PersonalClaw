"""Which tools an agent may use: its tool list (``agents.<name>.tools``, Tools on the Agents page).

An agent with no list (the default, and the default agent's) may use every tool PersonalClaw
offers it. A list is the whole of what the agent may use. Each entry is a tool's name as the Tools
page lists it (``read_file``, ``memory_recall``, ``mcp/<server>/<tool>``) or a pattern over that
name: ``*`` stands for any run of characters, ``?`` for one, ``[…]`` for one of a set, matched
against the whole name and case-sensitively (``mcp/files/*`` is every tool of the MCP server
``files``). PersonalClaw's own runtime shows the agent's model nothing else, and refuses a call to
anything else before anyone is asked about it, so a model that names a tool it was not shown
cannot reach it.

One tool is kept on every list: ``tool_result_get``, which reads back the rest of a long answer one
of the agent's own calls gave (a cut answer names it as the way to the rest). The runtime's own
tools for finding the agent's tools (``tool_search``, ``tool_schema``, ``reset_tools``) are not
tools of the catalog at all, and search only what the list allows.

A list that cannot be read (a hand-edited value that is not a list of names) allows nothing but
that one, since what it was meant to allow is unknown, and says so. While the configuration file
cannot be read, no list in it can, so the same holds for every agent.

A built-in agent's list is PersonalClaw's own: the Agents page shows it and does not edit it, so
a refusal says PersonalClaw gives that agent its tools rather than pointing at a setting there is
no way to change. The template refiner's (``agents.defaults.TEMPLATE_REFINER_TOOLS``) is the one
built-in list today.

This is the one reader of the setting (:func:`agent_tools`), the one matcher
(:meth:`AgentTools.allows`) and the one refusal (:meth:`AgentTools.refuse`), so what the model is
shown, what is refused and what the log says cannot disagree.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.config.loader import AppConfig

logger = logging.getLogger(__name__)

#: The tools every list keeps: reading back the rest of an answer a call of the agent's own gave,
#: which every cut answer names, adds nothing the agent did not already reach.
ALWAYS_KEPT: frozenset[str] = frozenset({"tool_result_get"})

#: What a turn's tool result says refused a call outside the agent's list (``refused_by``).
REFUSED_BY = "agent_tools"

#: How many of the tools a list keeps its log line names before it counts the rest.
_NAMED = 20

#: Why a list could not be read when the configuration file it is set in could not be.
NO_CONFIG = "the configuration file could not be read"


@dataclass(frozen=True)
class AgentTools:
    """What one agent's tool list allows. The default allows every tool."""

    #: The agent, as the sentences below name it.
    agent: str = ""
    #: The list's entries: names and patterns. Empty with ``listed`` False is every tool.
    patterns: tuple[str, ...] = ()
    #: Whether the agent has a list at all.
    listed: bool = False
    #: Why the list could not be read, when it could not; such a list allows only
    #: :data:`ALWAYS_KEPT`.
    unreadable: str = ""
    #: Whether the list is PersonalClaw's own, a built-in agent's, which no page edits.
    fixed: bool = False

    @classmethod
    def of(cls, agent: str, value: Any, *, fixed: bool = False) -> "AgentTools":
        """The list *value* (an agent profile's ``tools``) of *agent*; *fixed* when the agent is a
        built-in one, whose list is PersonalClaw's own.

        Absent or empty is no list. A list keeps its entries that are names, so a stray entry
        that is not one can only narrow it; a list with none, or a value that is not a list at all,
        could not be read, and allows nothing it does not keep on every list.
        """
        if value is None or value == [] or value == ():
            return cls(agent=agent, fixed=fixed)
        if not isinstance(value, (list, tuple)):
            return cls.cannot_read(
                agent, "it is not a list of tool names", type(value).__name__, fixed=fixed
            )
        names = tuple(str(v).strip() for v in value if isinstance(v, str) and str(v).strip())
        if not names:
            return cls.cannot_read(agent, "none of its entries is a tool name", fixed=fixed)
        return cls(agent=agent, patterns=tuple(dict.fromkeys(names)), listed=True, fixed=fixed)

    @classmethod
    def cannot_read(
        cls, agent: str, why: str, detail: str = "", *, fixed: bool = False
    ) -> "AgentTools":
        """A list of *agent*'s that could not be read, for *why* (said to its model too); *detail*
        is for the gateway log alone."""
        held = cls(agent=agent, listed=True, unreadable=why, fixed=fixed)
        logger.warning(
            "the tool list of the agent %s could not be read (%s%s): it may use no tool until %s",
            agent,
            why,
            f": {detail}" if detail else "",
            held._until,
        )
        return held

    @property
    def _until(self) -> str:
        """What lets an agent whose list could not be read use its tools again."""
        if self.unreadable == NO_CONFIG:
            return "the configuration file is repaired"
        if self.fixed:
            return "its entry in the configuration file is fixed"
        return "the list is fixed on the Agents page"

    def allows(self, tool_name: str) -> bool:
        """Whether the agent may use the tool named *tool_name*: by the name matcher the operator
        ceiling's tool allowlist is matched with, so a pattern means the same in both."""
        if not self.listed or tool_name in ALWAYS_KEPT:
            return True
        from personalclaw.guardrails.registries import name_glob

        return any(name_glob(tool_name, pattern) for pattern in self.patterns)

    def refusal(self, tool_name: str) -> str:
        """Why a call to *tool_name*, which the list does not allow, is refused: the tool and the
        agent, and where the list is set."""
        if self.unreadable:
            return (
                f"the tool list of the agent {self.agent} could not be read ({self.unreadable}), "
                f"so it may use no tool until {self._until}, {tool_name} included"
            )
        if self.fixed:
            return (
                f"the built-in agent {self.agent} may use only the tools PersonalClaw gives it, "
                f"and {tool_name} is not one of them"
            )
        return (
            f"the agent {self.agent} may use only the tools on its tool list, set on the Agents "
            f"page, and {tool_name} is not one of them"
        )

    def refuse(self, tool_name: str, meta: dict[str, Any]) -> str:
        """The answer to a call to *tool_name*, which the list does not allow: the refusal a call
        a policy blocks is answered with (``security.classify_denial``), naming the tool and the
        agent, with *meta* marked failed and refused by :data:`REFUSED_BY`, the bits the tool card
        and the call's audit row read."""
        from personalclaw import security
        from personalclaw.audit_subject import log_title
        from personalclaw.llm.events import TOOL_META_REFUSED_BY

        _, observation = security.classify_denial(
            security.DENY_KIND_POLICY, self.refusal(tool_name), tool_name
        )
        meta["ok"] = False
        meta[TOOL_META_REFUSED_BY] = REFUSED_BY
        # One WARNING line, as each refusal of a control writes one. The name is the model's own
        # text, so the line writes it masked, as every line naming a call does.
        logger.warning(
            "native: refused %s, which the agent %s may not use", log_title(tool_name), self.agent
        )
        return observation

    def say_narrowed(self, *, kept: list[str], withheld: list[str]) -> None:
        """The gateway log line of a catalog this list narrowed: what it keeps of what is on offer,
        and the entries that match nothing on offer (a tool not installed here, or a typo). INFO,
        as routine as the catalog it describes, unless an entry matches nothing: the list then
        allows less than it says, which is a WARNING."""
        shown = ", ".join(kept[:_NAMED]) or "none"
        if len(kept) > _NAMED:
            shown += f" and {len(kept) - _NAMED} more"
        unmatched = self.unmatched([*kept, *withheld])
        total = len(kept) + len(withheld)
        logger.log(
            logging.WARNING if unmatched else logging.INFO,
            "native: the agent %s may use %d of the %d %s on offer, as its tool list says: %s%s",
            self.agent,
            len(kept),
            total,
            "tool" if total == 1 else "tools",
            shown,
            f"; its entries {', '.join(unmatched)} match no tool on offer" if unmatched else "",
        )

    def unmatched(self, names: Iterable[str]) -> list[str]:
        """The list's entries that match none of *names* (the tools on offer): a name that is not
        installed here, or a typo."""
        from personalclaw.guardrails.registries import name_glob

        offered = list(names)
        return [p for p in self.patterns if not any(name_glob(n, p) for n in offered)]


def agent_tools(agent: str | None, cfg: "AppConfig | None") -> AgentTools:
    """The tool list of the agent a turn runs as, read from *cfg*: the configuration the turn is
    built from, or ``None`` when it could not be loaded.

    *agent* as a turn names it: a profile's name, the default native agent under any of its
    spellings, or empty for the default agent (``default_agent``), which a chat that names no agent
    runs as. The same agent its instructions are read for (``agents.instructions``), so a list set
    on the default agent holds its chats too.

    A configuration file that could not be read (*cfg* ``None``, or a ``config.json`` the loader
    discarded for its defaults, :func:`~personalclaw.config.loader.config_discard`) says nothing of
    the lists set in it, and a list is a limit, not a grant: every agent may then use nothing but
    :data:`ALWAYS_KEPT` until the file is repaired.
    """
    from personalclaw.agents.defaults import (
        default_agent_name,
        is_reserved_agent,
        normalize_agent_name,
    )
    from personalclaw.config.loader import config_discard

    wanted = (agent or "").strip()
    key = normalize_agent_name(wanted) if wanted else default_agent_name(cfg)
    discard = config_discard()
    if cfg is None or discard is not None:
        return AgentTools.cannot_read(
            key, NO_CONFIG, discard.reason if discard else "", fixed=is_reserved_agent(key)
        )
    profile = (cfg.agents or {}).get(key)
    return AgentTools.of(
        key,
        getattr(profile, "tools", None) if profile is not None else None,
        fixed=is_reserved_agent(key),
    )
