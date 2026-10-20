"""The operator's blocking hooks, asked about a tool call at the one step every path asks them.

A ``PreToolUse`` hook bound to an agent (its id in the agent's ``triggers``) is the operator's own
rule about the calls that agent makes, and it holds only where it is asked. So every path that runs
a tool call asks it at the same step: past the refusals that need no hook (the task mode, the deny
list, a run's bounds, its grants and its tier), and before anything can approve the call or ask
anyone about it (an operator's pattern, what the call declares, Trust reads, Trust, YOLO, a
standing grant, a person).

* PersonalClaw's own runtime asks at that step about each call it is about to run
  (``NativeAgentRuntime._guard_and_invoke``), through the callable the bridge builds it
  (:func:`on_tool`). A call it then asks a host about says so (``AgentEvent.hooks_asked``), and
  no host asks its hooks again.
* A host asks about each call an agent CLI asks it about (:func:`on_request`): a chat's gate
  (``dashboard.chat_runner``), a subagent's (``subagent``), the background helper's
  (``llm_helpers``: a heartbeat task, a subagent's report, a room member's turn), an evaluation's
  (``eval.runner``), and a channel app's own turn (:func:`on_channel_request`, which
  ``personalclaw.sdk.channel`` offers as ``ask_pre_tool_hooks``).

The hooks asked are those bound to the agent the call's turn runs as, read at the call: the
profile of that name, or the default agent's for a turn that names none, as a chat's are.

A hook that blocks the call refuses it, in its own words. A hook that fails to run refuses it too:
the agent's bindings cannot be read, the store cannot fire its hooks, or one of them did not run to
an exit of its own (it could not start, it timed out, its action was held or refused, its provider
failed). A policy hook that did not run cannot say the call is safe. A hook that ran and exited with
another code lets the call go on, as a hook's non-blocking error does. Each path says a refusal the
same way (:class:`HooksSaid`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from personalclaw.audit_subject import log_title
from personalclaw.hooks import (
    HOOK_EVENT_PRE_TOOL_USE,
    ScriptHookResult,
    ScriptHookStore,
    get_global_hook_store,
)
from personalclaw.security import mask_child_output

logger = logging.getLogger(__name__)

#: The audit outcome of a call a pre-tool hook blocked, and of one whose hook failed to run.
OUTCOME_HOOK_BLOCKED = "hook_blocked"
OUTCOME_HOOK_ERROR = "hook_error"
#: Who decided, on the audit row of a call the hooks refused.
DECIDED_BY_HOOK = "hook"
#: What the model and the transcript are told of a call whose pre-tool hook failed to run.
HOOK_FAILED = "its pre-tool hook failed to run"
#: The exit a hook gives to refuse the call it was asked about.
_BLOCKS = 2
#: The exit of a hook that did not run to an exit of its own (``hooks.run_script_hook``).
_DID_NOT_RUN = -1


@dataclass(frozen=True)
class HooksSaid:
    """What the operator's blocking hooks said of one call: nothing against it, or why it is
    refused, in the words every path says it."""

    #: Why the call is refused, as its model is told: the blocking hook's name and its words, or
    #: :data:`HOOK_FAILED`. Empty when the hooks let the call go on.
    refusal: str = ""
    #: True when a hook blocked the call, False when one failed to run.
    blocked: bool = False
    #: What failed, masked, for a hook that did not run. Empty otherwise.
    error: str = ""

    @property
    def refused(self) -> bool:
        return bool(self.refusal)

    @property
    def note(self) -> str:
        """The refusal as a chat's transcript and a channel's thread say it."""
        return f"a pre-tool hook blocked it ({self.refusal})" if self.blocked else self.refusal

    def audit_row(self, **metadata: Any) -> dict[str, Any]:
        """The refusal's fields on the call's one audit row (``sel().log_tool_invocation``): its
        outcome, what failed, and *metadata* with who decided."""
        return {
            "outcome": OUTCOME_HOOK_BLOCKED if self.blocked else OUTCOME_HOOK_ERROR,
            "error": self.error,
            "metadata": {**metadata, "decided_by": DECIDED_BY_HOOK},
        }


#: The hooks let the call go on.
PASSED = HooksSaid()


def bound_hook_ids(agent: str | None) -> list[str]:
    """The ids of the hooks bound to the agent a turn runs as, read now: *agent* is a profile's
    name, or empty for the default agent, which a turn that names none runs as. Raises when the
    configuration cannot be read; the caller says what an unknown binding means."""
    from personalclaw.config.loader import AppConfig, resolve_agent_bindings

    return list(resolve_agent_bindings(AppConfig.load(), agent or None).triggers or [])


async def on_tool(
    tool_name: str,
    tool_input: Any,
    *,
    agent: str | None,
    store: ScriptHookStore | None = None,
    subagent_id: str = "",
    parent_session_key: str = "",
    on_results: Callable[[list[ScriptHookResult]], None] | None = None,
) -> HooksSaid:
    """The operator's blocking hooks on a call to *tool_name* with *tool_input* (its arguments, or
    their JSON text), made in a turn that runs as *agent*.

    *store* is the hook store to fire through, the gateway's own when ``None``; a process with none
    reads the home's (``ScriptHookStore``). *subagent_id* and *parent_session_key* say which
    subagent made the call, for the hooks to read. *on_results* is shown each hook's result, for a
    host that tells its owner what the hooks did (a chat's activity line)."""
    try:
        hook_ids = bound_hook_ids(agent)
    except Exception:  # noqa: BLE001 - see the module docstring: an unread binding refuses
        logger.warning(
            "the pre-tool hooks bound to %r could not be read; refusing %s",
            agent or "the default agent",
            log_title(tool_name),
            exc_info=True,
        )
        return HooksSaid(
            refusal=HOOK_FAILED, error="the hooks bound to its agent could not be read"
        )
    held = store if store is not None else get_global_hook_store()
    if held is None:
        if not hook_ids:
            return PASSED
        held = ScriptHookStore()
    try:
        results = list(
            await held.fire_for_ids(
                HOOK_EVENT_PRE_TOOL_USE,
                hook_ids,
                tool_name=tool_name,
                tool_input=_arguments(tool_input),
                subagent_id=subagent_id,
                parent_session_key=parent_session_key,
                agent_role=(agent or "") if subagent_id else "",
            )
        )
    except Exception as exc:  # noqa: BLE001 - a hook that cannot run refuses, as one that blocks
        logger.warning(
            "the pre-tool hooks could not run for %s; refusing", log_title(tool_name), exc_info=True
        )
        return HooksSaid(refusal=HOOK_FAILED, error=mask_child_output(str(exc), limit=300))
    if on_results is not None:
        try:
            on_results(results)
        except Exception:  # noqa: BLE001 - telling the owner never changes the verdict
            logger.debug("showing the pre-tool hooks' results failed", exc_info=True)
    return _verdict(tool_name, results)


async def on_request(
    event: Any,
    *,
    agent: str | None,
    store: ScriptHookStore | None = None,
    subagent_id: str = "",
    parent_session_key: str = "",
    on_results: Callable[[list[ScriptHookResult]], None] | None = None,
) -> HooksSaid:
    """The operator's blocking hooks on *event*, a call put to a host's gate (a permission
    request), made in a turn that runs as *agent*.

    The hooks are told the call by its title, read as the chat's gate reads it (``validation
    .sanitize_string``), and its input read as JSON. A call PersonalClaw's own runtime asks about
    met its hooks at the runtime's own step (``hooks_asked``), so it is not asked about again. The
    rest is :func:`on_tool`'s."""
    if getattr(event, "hooks_asked", False) is True:
        return PASSED
    from personalclaw.validation import sanitize_string

    return await on_tool(
        sanitize_string(str(getattr(event, "title", "") or "")),
        getattr(event, "tool_input", None),
        agent=agent,
        store=store,
        subagent_id=subagent_id,
        parent_session_key=parent_session_key,
        on_results=on_results,
    )


async def on_channel_request(event: Any, *, agent: str | None) -> HooksSaid:
    """The operator's blocking hooks on *event*, a call a channel app's own turn is asked about,
    made as *agent* (the agent the conversation runs as): :func:`on_request`, through the
    gateway's hook store. A channel names only the call and its agent; what core fires the hooks
    with stays core's."""
    return await on_request(event, agent=agent)


def _arguments(tool_input: Any) -> Any:
    """A call's arguments as its hooks read them: a JSON text read as JSON (``None`` when it is
    none), anything else as it is."""
    if isinstance(tool_input, (bytes, bytearray)):
        tool_input = bytes(tool_input).decode("utf-8", "replace")
    if isinstance(tool_input, str):
        if not tool_input.strip():
            return None
        try:
            return json.loads(tool_input)
        except ValueError:
            return None
    return tool_input


def _verdict(tool_name: str, results: Iterable[ScriptHookResult]) -> HooksSaid:
    """What the hooks' *results* say of the call: a block refuses it in the blocking hook's words,
    a hook that did not run refuses it as :data:`HOOK_FAILED`, and anything else lets it go on."""
    results = list(results)
    for result in results:
        if result.exit_code == _BLOCKS:
            said = mask_child_output(result.stderr, limit=200) if result.stderr else "hook denied"
            logger.warning(
                "pre-tool hook %s blocked %s: %s", result.hook_name, log_title(tool_name), said
            )
            return HooksSaid(refusal=f"{result.hook_name}:{said}", blocked=True)
    for result in results:
        if result.exit_code == _DID_NOT_RUN:
            failed = mask_child_output(result.error, limit=300) or "it did not run"
            logger.warning(
                "pre-tool hook %s did not run for %s; refusing: %s",
                result.hook_name,
                log_title(tool_name),
                failed,
            )
            return HooksSaid(refusal=HOOK_FAILED, error=failed)
    for result in results:
        if result.exit_code != 0 and result.stderr:
            logger.warning(
                "pre-tool hook %s warning: %s", result.hook_name, mask_child_output(result.stderr)
            )
    return PASSED
