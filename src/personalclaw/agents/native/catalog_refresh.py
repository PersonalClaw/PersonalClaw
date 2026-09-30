"""A native session's tool catalog follows the tools on offer, turn by turn.

A mixin of :class:`~personalclaw.agents.native.runtime.NativeAgentRuntime`. A runtime lives as long
as its chat, and it builds its catalog (the tool schema, the dispatch index, the groups and the
retriever) from the tool surface: the providers registered, the tools switched off on the Tools
page, and the MCP servers ``mcp.json`` names. It built that catalog once, at the first turn, so a
tool installed while the chat was open never reached it, and one removed or switched off stayed on
offer.

Each turn now starts by comparing the surface's stamp with the one the catalog was built at
(:func:`~personalclaw.tool_providers.registry.surface_stamp`: a counter and two file stats), and
rebuilds the catalog when they differ, keeping what the session chose: the groups it switched on
and off, and the tools it called. A change reaches the next turn and never the middle of one, since
the tool block is part of the prompt the provider caches. So a call can still name a tool that left
while its turn was under way, and that call is refused, never run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from personalclaw.llm.events import TOOL_META_REFUSED_BY

if TYPE_CHECKING:
    from personalclaw.tool_providers.base import ToolProvider

logger = logging.getLogger(__name__)


class CatalogRefresh:
    """The catalog is rebuilt when the tool surface changed since it was built
    (:meth:`_follow_tool_surface`), and a call to a tool that left during its turn is refused
    (:meth:`_no_longer_offered`)."""

    # The runtime's own state, set in ``__init__`` and by its catalog build: declared here so this
    # is checked against the runtime's types rather than against what its own assignments imply.
    #: How the session's surface is read again when it changes (the registry's ``tool_surface``
    #: over the session's own platform provider), or None for a fixed list its caller built.
    _tool_surface: Callable[[], list[ToolProvider]] | None
    _tool_providers: list[ToolProvider]
    _tool_defs: list[Any]
    _tool_index: dict[str, ToolProvider]
    _provider_of: dict[str, str]
    _groups: list[Any]
    _group_seed: list[str] | None
    _surface: str
    #: What the tool surface was when the catalog was built, or None while no catalog was built:
    #: never started, or a model with no tools, which has none to follow.
    _surface_stamp: tuple[object, ...] | None = None

    if TYPE_CHECKING:

        async def _build_catalog(self, *, carry: bool) -> None:
            """Build the catalog from ``_tool_providers`` (the runtime's ``_build_catalog``)."""
            ...

    def _stamp_surface(self) -> None:
        """Note the surface a catalog is about to be built from. Taken BEFORE the providers are
        listed, so a change that lands while they are is one the next turn rebuilds for."""
        from personalclaw.tool_providers.registry import surface_stamp

        self._surface_stamp = surface_stamp()

    async def _follow_tool_surface(self) -> None:
        """Rebuild the catalog when the tools this session can use changed since it was built.

        Called as each turn starts. A turn with nothing changed pays a counter and two file stats.
        """
        if self._surface_stamp is None:
            return
        from personalclaw.tool_providers.registry import surface_stamp

        if surface_stamp() == self._surface_stamp:
            return
        before = {getattr(d, "name", "") or "" for d in self._tool_defs}
        if self._tool_surface is not None:
            self._tool_providers = list(self._tool_surface())
        await self._build_catalog(carry=True)
        after = {getattr(d, "name", "") or "" for d in self._tool_defs}
        logger.info(
            "native: the tool surface changed since this session's last turn — added %s, "
            "removed %s",
            sorted(after - before),
            sorted(before - after),
        )

    def _session_groups(self, previous: tuple[set[str], set[str] | None] | None) -> set[str] | None:
        """The groups active in the catalog just partitioned into ``_groups`` (None: every group).

        A first build (*previous* None) starts the way the session's seed, else its surface,
        starts. A rebuild is handed the group names it had and the ones active, and keeps the
        groups this session switched on and off, so a ``reset_tools`` choice stands; a group that
        is new since starts the way the surface starts it. With every group active before, every
        group, the new ones included, still is.
        """
        from personalclaw.tool_providers import groups as _groups

        default = (
            {_groups.CORE_GROUP, *self._group_seed}
            if self._group_seed is not None
            else _groups.resolve_default_groups(self._surface)
        )
        if previous is None:
            return default
        had, active = previous
        if active is None:
            return None
        names = {g.name for g in self._groups}
        new = names - had
        return (active & names) | {_groups.CORE_GROUP} | (new if default is None else new & default)

    def _no_longer_offered(self, tool_name: str, meta: dict) -> str | None:
        """The refusal for a call to a tool that left this turn's catalog after it was built, or
        None while the tool is still on offer.

        The catalog changes only when a turn starts, so a call can name a tool whose app was
        removed, switched off or updated while the turn was under way, or that was switched off on
        the Tools page. It is checked before the call's approval is asked for, and again before
        the call runs, since an approval can wait long enough for either. Such a call is refused,
        never run: the removed app's code must not run, and a switch that is off is off now.
        """
        from personalclaw.tool_providers import tool_prefs
        from personalclaw.tool_providers.registry import still_serves

        prov = self._tool_index.get(tool_name)
        if prov is None:
            return None
        if not still_serves(prov):
            why = (
                "the app that provides it was removed, switched off or updated after this turn "
                "began"
            )
        elif tool_prefs.is_disabled(self._provider_of.get(tool_name, "") or prov.name, tool_name):
            why = "it was switched off on the Tools page after this turn began"
        else:
            return None
        # The failure bit every refusal carries (``tool_meta["ok"]``, the runtime's ``_FAILED``).
        meta["ok"] = False
        meta[TOOL_META_REFUSED_BY] = "withdrawn"
        return f"Error: {tool_name!r} is no longer available: {why}, so the call was not run."
