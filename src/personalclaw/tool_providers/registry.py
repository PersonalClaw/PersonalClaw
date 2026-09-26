"""Tool provider registry: the providers an agent's tools come from, and who serves each name.

One name, one provider
======================

An agent turn indexes its tools by name, and a call reaches the provider its name maps to under
the approval card that tool asks with. So a name has exactly one provider, and which one is a rule
rather than an accident of order:

1. The platform provider's names (``PLATFORM_TOOL_NAMES``: ``bash``, ``read_file`` and the rest)
   are its own. The platform is built per session and never registered, so its provider name is
   held here too.
2. A name under ``mcp/`` is the MCP Tool Servers app's (``mcp/<server>/<tool>``). No other
   provider may offer one, whatever order they registered in.
3. A provider core ships (its own factories, ``app-routes``, a ``builtin``-tier app) outranks one
   an installed app adds.
4. Otherwise the provider that claimed the name first keeps it. A provider claims its names when
   the registry reads its tool list: when it registers, and again on every read after, because a
   list is live (a remote tool server's is whatever it answers).

A registration that would take a name another provider holds is refused whole, not trimmed: the
provider leaves the surface and serves nothing, its status says why in a sentence, and the
security log has a ``refused`` row. When a core provider arrives for a name an app's provider
holds, the app's is the one refused. Nothing wins or loses silently.

Every reader goes through the rule: :func:`serve` is what an agent's index and the catalog are
built from, and :func:`resolve` is how "Try it" finds a tool.
"""

import asyncio
import itertools
import logging
import threading
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from personalclaw.tool_providers.base import ToolDefinition, ToolProvider

logger = logging.getLogger(__name__)

# Operator-visible load failures. A provider that raises while enumerating its
# tools used to fail silently (the tool just never appeared); we now record the
# (provider, error) so the Tools page can show "provider X failed to load: …"
# rather than leaving the operator to guess why a tool is missing. Best-effort,
# in-memory, refreshed each catalog build.
_load_failures: list[dict[str, str]] = []


def record_failure(provider: str, error: str) -> None:
    """Record a tool-source load failure for operator surfacing (dedup by provider)."""
    _load_failures[:] = [f for f in _load_failures if f.get("provider") != provider]
    _load_failures.append({"provider": provider, "error": str(error)[:300]})


def get_load_failures() -> list[dict[str, str]]:
    """The recorded load failures (a copy)."""
    return list(_load_failures)


def clear_load_failures() -> None:
    """Reset the failure list — called at the start of each catalog build."""
    _load_failures.clear()


def create_native_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-core`` tool surface.

    Returns an in-process provider wrapping ``mcp_core`` directly — the same
    working path the native loop uses.
    """
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider()


def create_automation_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-automation`` tool surface — in-process over
    ``mcp_automation`` (§4's `automation_*` namespace: create/list/update/pause/resume/run/
    history/delete over the unified trigger store)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_automation",
        provider_name="personalclaw-automation",
        display="PersonalClaw Automations",
    )


def create_computer_use_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-computer-use`` tool surface — in-process over
    ``computer_use.tools`` (`DCU-4`'s seven ``computer_*`` tools).

    Registered for the same reason every other aggregated category is: ``mcp_core``'s ACP
    surface and the in-process catalog must not diverge
    (``tests/test_native_tool_categories.py`` pins that in both directions), so a tool an ACP
    CLI can call must also be one the Tools page, the manifest and the offline reference name.
    A surface reachable by one agent and invisible to the operator is half a feature.

    The provider changes NO authority. ``computer_use.tools`` is the thin shim either way — it
    forwards to the gateway's ``/api/computer-use/dispatch`` over ``mcp_core._post``, so
    in-process invocation is a loopback round trip rather than a second, shorter path into the
    dispatch. That is deliberate: a branch on "am I inside the gateway" would give the one
    security-sensitive transport in this package two code paths, and only one of them would be
    exercised by whichever surface the next test happened to use. ``InProcessMcpToolProvider``
    runs ``_call_tool`` in an executor thread, so the loopback cannot stall the event loop.
    """
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.computer_use.tools",
        provider_name="personalclaw-computer-use",
        display="PersonalClaw Computer Use",
    )


def create_artifacts_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-artifacts`` tool surface — in-process
    over ``mcp_artifacts`` (the Artifacts entity tool group)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_artifacts",
        provider_name="personalclaw-artifacts",
        display="PersonalClaw Artifacts",
    )


def create_workflows_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-workflows`` tool surface — in-process
    over ``mcp_workflows`` (the v2 workflow engine's 19-tool chat surface)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_workflows",
        provider_name="personalclaw-workflows",
        display="PersonalClaw Workflows",
    )


def create_prompts_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-prompts`` tool surface — in-process
    over ``mcp_prompts`` (render the user's saved, parameterized Prompts)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_prompts",
        provider_name="personalclaw-prompts",
        display="PersonalClaw Prompts",
    )


def create_memory_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-memory`` tool surface — in-process
    over ``mcp_memory`` (persistent lessons + on-demand recall)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_memory",
        provider_name="personalclaw-memory",
        display="PersonalClaw Memory",
    )


def create_subagents_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-subagents`` tool surface — in-process
    over ``mcp_subagents`` (spawn + track background subagents)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_subagents",
        provider_name="personalclaw-subagents",
        display="PersonalClaw Subagents",
    )


def create_code_map_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the code-map tool surface — symbol lookup over the
    tree-sitter codebase index (Context-Economy §5.5), replacing several grep/read
    round-trips with one call. Registered as ``workflows-tools`` so its group derives
    to ``workflows``; fails soft to grep/read when no index exists."""
    from personalclaw.tool_providers.code_map import CodeMapToolProvider

    return CodeMapToolProvider()


# NOTE: ``personalclaw-ui-docs`` has NO factory here on purpose. It is the first bundled
# app to own its provider code under the native capability contract (APE-5): its manifest
# resolves ``provider:create_provider`` inside
# ``apps/native/personalclaw-ui-docs/provider.py``, which imports core only via
# ``personalclaw.sdk.*``. Adding a factory back would re-create the core dependency the
# contract removes — see ``apps/native_contract.py``.


_providers: dict[str, ToolProvider] = {}
#: provider name → the installed app that registered it. Recorded at registration because that
#: is the one place the app is known; the tool seam reads it to NAME the app when one of its
#: tools has to be repaired or left out of model requests.
_provider_app: dict[str, str] = {}

#: A registration's standing when it and another offer one name (rule 3): lower wins.
CORE = 0
APP = 1

#: Where every external MCP server's tools are named, ``mcp/<server>/<tool>`` (rule 2).
MCP_NAMESPACE = "mcp/"


class ToolRegistrationRefused(Exception):
    """A registration refused because it would serve a name another provider holds.

    The message is the sentence the provider's status shows."""


@dataclass
class _Registration:
    provider: ToolProvider
    app: str
    standing: int
    #: Called with the sentence when this registration is refused after it registered: its list
    #: came to offer a taken name, or a core provider arrived for a name it held.
    on_refused: Callable[[str], None] | None
    seq: int
    #: Whether the registry has read its tool list yet, so its names are claimed.
    admitted: bool = False


#: provider name → how it registered. Live only while its provider is still the one in
#: ``_providers`` under that name (see :func:`_live`).
_registrations: dict[str, _Registration] = {}
#: tool name → the provider serving it. A claim lapses when its provider leaves.
_claims: dict[str, ToolProvider] = {}
#: Every provider object ever registered, by identity. A surface is a snapshot, so one can still
#: carry a provider that has since been refused or unregistered; that one serves nothing, while
#: one that was never registered (the platform, a caller's own) is the caller's to serve.
_ever: dict[int, Callable[[], object]] = {}
_seq = itertools.count()
_lock = threading.RLock()
#: Admission passes still running after the caller that started them stopped waiting.
_background: set[asyncio.Task] = set()


def register_provider(
    provider: ToolProvider,
    *,
    app: str = "",
    core: bool | None = None,
    on_refused: Callable[[str], None] | None = None,
) -> None:
    """Put *provider* on the surface an agent's tools are built from.

    *app* is the installed app registering it ("" for core itself). *core* says whether it ships
    with core; it defaults to "no app registered it". *on_refused* is called with the sentence if
    the registration is refused later, once its tool names are read.

    Raises :class:`ToolRegistrationRefused` when the name it goes by is the platform's, or is
    already used by a provider it does not outrank. The names of its tools are checked when the
    registry first reads them (:func:`admit`, and every read after).
    """
    from personalclaw.agents.native.builtin_tools import (
        PLATFORM_DISPLAY_NAME,
        PLATFORM_PROVIDER_NAME,
    )

    standing = CORE if (not app if core is None else core) else APP
    name = provider.name
    with _lock:
        current = _providers.get(name)
        if current is provider:
            return
        if name == PLATFORM_PROVIDER_NAME:
            sentence = _named(provider, f"the name of PersonalClaw's own {PLATFORM_DISPLAY_NAME}")
            _audit(name, app, tool="", holder=PLATFORM_PROVIDER_NAME, sentence=sentence)
            raise ToolRegistrationRefused(sentence)
        if current is not None:
            if standing >= _standing_of(current):
                sentence = _named(provider, f"which {_who(current)} already uses")
                _audit(name, app, tool="", holder=name, sentence=sentence)
                raise ToolRegistrationRefused(sentence)
            _refuse(
                current,
                tool="",
                holder=name,
                sentence=_named(
                    current,
                    f"which {_who(provider)}, a provider PersonalClaw ships, uses",
                    verdict="was turned off",
                ),
            )
        _providers[name] = provider
        if app:
            _provider_app[name] = app
        else:
            _provider_app.pop(name, None)
        _registrations[name] = _Registration(provider, app, standing, on_refused, next(_seq))
        _remember(provider)


def unregister_provider(provider: ToolProvider) -> None:
    """Take *provider* off the surface, with the names it held.

    By object, not by name: a provider refused at registration never held its name, so removing
    "whatever is registered under it" would take the provider that does.
    """
    with _lock:
        if _providers.get(provider.name) is provider:
            _drop(provider)


def _drop(provider: ToolProvider) -> None:
    name = provider.name
    _providers.pop(name, None)
    _provider_app.pop(name, None)
    _registrations.pop(name, None)
    for tool in [t for t, holder in _claims.items() if holder is provider]:
        del _claims[tool]


def _remember(provider: ToolProvider) -> None:
    key = id(provider)

    def forget(_ref: object) -> None:
        _ever.pop(key, None)

    def held() -> object:
        return provider

    try:
        _ever[key] = weakref.ref(provider, forget)
    except TypeError:  # not weakly referenceable: hold it, a registration is not a hot path
        _ever[key] = held


def _was_registered(provider: ToolProvider) -> bool:
    ref = _ever.get(id(provider))
    return ref is not None and ref() is provider


def _live(provider: ToolProvider) -> _Registration | None:
    """How *provider* registered, while it is still registered."""
    reg = _registrations.get(provider.name)
    if reg is not None and reg.provider is provider and _providers.get(provider.name) is provider:
        return reg
    return None


def _standing_of(provider: ToolProvider) -> int:
    reg = _live(provider)
    return reg.standing if reg is not None else CORE


def _order(reg: _Registration) -> tuple[int, int]:
    return reg.standing, reg.seq


def _holder(tool: str) -> ToolProvider | None:
    provider = _claims.get(tool)
    if provider is not None and _live(provider) is None:
        del _claims[tool]
        return None
    return provider


def _who(provider: ToolProvider) -> str:
    return str(getattr(provider, "display_name", "") or provider.name)


def _named(provider: ToolProvider, holder: str, *, verdict: str = "was not added") -> str:
    who = _who(provider)
    return (
        f"{who}'s tool provider is named '{provider.name}', {holder}. Two providers cannot share "
        f"a name, so {who} {verdict} and none of its tools are available to agents."
    )


def _offers(
    provider: ToolProvider, tool: str, holder: str, *, verdict: str = "was not added"
) -> str:
    who = _who(provider)
    return (
        f"{who} offers a tool named '{tool}', which {holder}. A tool name belongs to one "
        f"provider, so {who} {verdict} and none of its tools are available to agents."
    )


def _audit(provider: str, app: str, *, tool: str, holder: str, sentence: str) -> None:
    """The security log's row for a refused registration: who asked, which name, who holds it."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"app:{app}" if app else "core",
            operation="tool_provider_register",
            outcome="refused",
            source="tools",
            resources=f"provider={provider} tool={tool or '-'} holder={holder}",
            error=sentence,
        )
    except Exception:  # noqa: BLE001 - the refusal stands whether or not it could be logged
        logger.warning("could not log the refused tool provider %r", provider, exc_info=True)


def _refuse(provider: ToolProvider, *, tool: str, holder: str, sentence: str) -> None:
    """Refuse a registered provider: off the surface, its names released, and it says why."""
    reg = _live(provider)
    if reg is None:
        return
    _drop(provider)
    _audit(provider.name, reg.app, tool=tool, holder=holder, sentence=sentence)
    logger.warning("tool provider %r refused: %s", provider.name, sentence)
    if reg.on_refused is not None:
        try:
            reg.on_refused(sentence)
        except Exception:  # noqa: BLE001 - its status is best-effort, the refusal is not
            logger.warning("could not record the refusal of %r", provider.name, exc_info=True)


def _claim(provider: ToolProvider, tools: list[ToolDefinition]) -> bool:
    """Claim the names *provider* offers now, or refuse it. True while it stays registered.

    A provider unregistered here (not one that was never registered) is not the rule's to judge.
    """
    from personalclaw.agents.native.builtin_tools import (
        PLATFORM_DISPLAY_NAME,
        PLATFORM_PROVIDER_NAME,
        PLATFORM_TOOL_NAMES,
    )
    from personalclaw.providers.mcp_instances import MCP_TOOLS_EXTENSION

    with _lock:
        reg = _live(provider)
        if reg is None:
            return False
        names = {t.name for t in tools}
        displaced: list[tuple[ToolProvider, str]] = []
        for tool in sorted(names):
            if tool in PLATFORM_TOOL_NAMES:
                refusal = _offers(
                    provider, tool, f"belongs to PersonalClaw's own {PLATFORM_DISPLAY_NAME}"
                )
                _refuse(provider, tool=tool, holder=PLATFORM_PROVIDER_NAME, sentence=refusal)
                return False
            if tool.startswith(MCP_NAMESPACE) and reg.app != MCP_TOOLS_EXTENSION:
                refusal = _offers(
                    provider,
                    tool,
                    "is under mcp/, where the tools of the MCP servers you connect are named",
                )
                _refuse(provider, tool=tool, holder="mcp", sentence=refusal)
                return False
            holder = _holder(tool)
            if holder is None or holder is provider:
                continue
            if reg.standing < _standing_of(holder):
                displaced.append((holder, tool))
                continue
            refusal = _offers(provider, tool, f"{_who(holder)} already provides")
            _refuse(provider, tool=tool, holder=holder.name, sentence=refusal)
            return False
        for holder, tool in displaced:
            _refuse(
                holder,
                tool=tool,
                holder=provider.name,
                sentence=_offers(
                    holder,
                    tool,
                    f"{_who(provider)}, a provider PersonalClaw ships, provides",
                    verdict="was turned off",
                ),
            )
        for tool in [t for t, holder in _claims.items() if holder is provider and t not in names]:
            del _claims[tool]
        for tool in names:
            _claims[tool] = provider
        reg.admitted = True
        return True


async def _admit_pending() -> None:
    with _lock:
        pending = sorted(
            (r for r in _registrations.values() if not r.admitted and _live(r.provider) is r),
            key=_order,
        )
    for reg in pending:
        if reg.admitted or _live(reg.provider) is not reg:
            continue
        try:
            tools = await reg.provider.list_tools()
        except Exception:  # noqa: BLE001 - unlisted now; its names are claimed when it lists
            logger.debug(
                "tool provider %r failed to list its tools", reg.provider.name, exc_info=True
            )
            continue
        _claim(reg.provider, list(tools))


async def admit(*, wait: float | None = None) -> None:
    """Read the tool list of every registered provider whose names are not claimed yet, in order
    of standing then registration, and claim them or refuse it.

    A registration path calls this so a refusal is on the provider's status before it answers.
    *wait* bounds how long the caller waits: a provider whose list takes longer (a remote tool
    server, the MCP servers) is admitted when its list arrives, and until then any reader that
    lists it applies the same rule. ``wait=0`` only starts the pass.
    """
    if wait is None:
        await _admit_pending()
        return
    task = asyncio.ensure_future(_admit_pending())
    _background.add(task)
    task.add_done_callback(_background.discard)
    if wait > 0:
        await asyncio.wait({task}, timeout=wait)


async def serve(
    surface: list[ToolProvider], *, skip: Iterable[str] = ()
) -> tuple[list[tuple[ToolProvider, list[ToolDefinition]]], list[tuple[ToolProvider, Exception]]]:
    """Each provider on *surface* with the tools it serves, every tool name exactly once.

    Returns ``(served, failures)``: ``served`` in *surface* order; ``failures`` are the providers
    whose list raised, with what it raised. A registered provider goes through the rule and a
    refused one serves nothing. A provider the caller built for this surface and never registered
    (the platform) serves first, and a name it serves is its own for the session. A provider
    named in *skip* (switched off) is not listed and serves nothing, and keeps the names it holds.
    """
    skipped = set(skip)
    own = [p for p in surface if not _was_registered(p)]
    with _lock:
        registered = sorted(
            ((reg, p) for p in surface if (reg := _live(p)) is not None),
            key=lambda rp: _order(rp[0]),
        )
    listed: dict[int, list[ToolDefinition]] = {}
    failures: list[tuple[ToolProvider, Exception]] = []
    taken: set[str] = set()
    for provider in [*own, *(p for _, p in registered)]:
        if provider.name in skipped:
            continue
        if _was_registered(provider) and _live(provider) is None:
            continue
        try:
            tools = list(await provider.list_tools())
        except Exception as exc:  # noqa: BLE001 - a broken provider must not empty the surface
            failures.append((provider, exc))
            continue
        if _was_registered(provider) and not _claim(provider, tools):
            continue
        mine = []
        for tool in tools:
            if tool.name in taken:
                logger.warning(
                    "tool %r from %r is left out: this surface already has it",
                    tool.name,
                    provider.name,
                )
                continue
            taken.add(tool.name)
            mine.append(tool)
        listed[id(provider)] = mine
    served = [
        (p, listed[id(p)])
        for p in surface
        if id(p) in listed and (not _was_registered(p) or _live(p) is not None)
    ]
    return served, failures


async def resolve(
    surface: list[ToolProvider], tool_name: str
) -> tuple[ToolProvider, ToolDefinition] | None:
    """The provider on *surface* serving *tool_name*, with that tool's definition, or None.

    The one an agent's index maps the name to (:func:`serve`): a provider the caller built for the
    surface first (the platform), then the provider holding the name, then, for a name nobody has
    claimed yet, each registered provider in order of standing, the first to offer it claiming it.
    Only the provider that serves a name is ever handed its call.
    """

    def find(tools: list[ToolDefinition]) -> ToolDefinition | None:
        return next((t for t in tools if t.name == tool_name), None)

    async def listed(provider: ToolProvider) -> list[ToolDefinition] | None:
        try:
            return list(await provider.list_tools())
        except Exception:  # noqa: BLE001 - a broken provider serves nothing on this call
            logger.debug("tool provider %r failed to list its tools", provider.name, exc_info=True)
            return None

    for provider in (p for p in surface if not _was_registered(p)):
        tool = find(await listed(provider) or [])
        if tool is not None:
            return provider, tool
    with _lock:
        registered = sorted(
            ((reg, p) for p in surface if (reg := _live(p)) is not None),
            key=lambda rp: _order(rp[0]),
        )
    holder = _holder(tool_name)
    if holder is not None and _standing_of(holder) != CORE:
        # A core provider registered since, whose names were not read yet, may outrank it.
        for reg, provider in registered:
            if reg.standing == CORE and not reg.admitted:
                tools = await listed(provider)
                if tools is not None:
                    _claim(provider, tools)
        holder = _holder(tool_name)
    candidates = [p for _, p in registered]
    if holder is not None and any(p is holder for p in candidates):
        candidates = [holder, *(p for p in candidates if p is not holder)]
    for provider in candidates:
        if _live(provider) is None:
            continue
        tools = await listed(provider)
        tool = find(tools or [])
        if tools is None or tool is None:
            continue
        if _claim(provider, tools) and _holder(tool_name) is provider:
            return provider, tool
    return None


def app_of(provider_name: str) -> str:
    """The app that registered ``provider_name`` ("" for core and unregistered providers)."""
    return _provider_app.get(provider_name, "")


def get_provider(name: str) -> ToolProvider | None:
    return _providers.get(name)


def list_providers() -> list[ToolProvider]:
    return list(_providers.values())


def tool_surface(platform: ToolProvider | None) -> list[ToolProvider]:
    """Every provider an agent turn can dispatch a tool to, in order: *platform* (the cwd-coupled
    filesystem and shell provider, built per session, never registered), then every registered one.

    The one definition of that surface. ``provider_bridge`` builds an agent's tools from it and
    ``POST /api/tools/invoke`` resolves a tool over it, so "Try it" reaches a tool exactly when an
    agent can: an external MCP server's tools are on it only through the registered provider that
    serves them (the ``mcp-tools`` app's ``mcp``), never through a lookup of the server itself.
    *platform* is ``None`` where no workspace resolved to confine it to.
    """
    return ([platform] if platform is not None else []) + list_providers()


async def list_all_tools() -> list[ToolDefinition]:
    """Aggregate tools from all registered providers, each name from the one provider serving it.

    Built through :func:`serve`, so a provider refused for a taken name contributes nothing, as
    it contributes nothing to an agent. A provider that raises while listing its tools is recorded
    as a load failure (operator-visible via :func:`get_load_failures`) rather than silently
    dropped, and the remaining providers still contribute. So is a tool whose
    schema has no portable form: it stays in this catalog (the Tools page can
    still show and invoke it), but no model request carries it, and the page
    has to be able to say why.
    """
    from personalclaw.tool_providers.portable_schema import (
        conform_parameters,
        exclusion_reason,
    )

    served, failures = await serve(tool_surface(None))
    for prov, exc in failures:
        logger.warning("Tool provider %r failed to list tools: %s", prov.name, exc, exc_info=exc)
        record_failure(prov.name, str(exc))
    all_tools: list[ToolDefinition] = []
    for prov, tools in served:
        for t in tools:
            t.provider = prov.name
        all_tools.extend(tools)
        unofferable = []
        for t in tools:
            verdict = conform_parameters(t.parameters)
            if verdict.parameters is None:
                unofferable.append(f"{t.name} ({exclusion_reason(verdict)})")
        if unofferable:
            record_failure(
                prov.name,
                "not offered to models because the parameter schema has no portable form: "
                + "; ".join(unofferable),
            )
    return all_tools
