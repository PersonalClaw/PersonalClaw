"""A subagent's first prompt: what its model is handed when its run starts.

Its task, masked: a parent model, a trigger or a workflow step composed it from what it read. Under
the subagent prefix (the bundled ``subagent-system-prefix`` snippet, else :data:`SYSTEM_PREFIX`)
unless a named agent runs it, which brings its own instructions. Then, unless it is a text run,
handed the task alone (``subagent_tier``), the context the assembler builds for its session, which
reads your memory only where the agent's work may (:mod:`personalclaw.memory_reads`): never for a
Temporary chat's agent, nor for an app's that the app was not given your memory for, and none when
nothing can say whose work it is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from personalclaw.security import redact_for_model
from personalclaw.subagent_tier import CAPABILITY_TEXT

if TYPE_CHECKING:
    from personalclaw.memory_reads import Reach
    from personalclaw.subagent import SubagentInfo

#: What a subagent's task is put under when the prompt system has no ``subagent-system-prefix``.
SYSTEM_PREFIX = (
    "You are a focused sub-agent. Complete the following task concisely. "
    "Do NOT create other agents. Report your result directly.\n"
    "IMPORTANT: Do NOT narrate your own process, failures, retries, or "
    "orchestration decisions. The user does not care how you got the answer. "
    "Only output meaningful, actionable results. Never output greetings or filler.\n\n"
)


async def first_prompt(
    info: SubagentInfo,
    *,
    named_agent: str,
    assemble: Callable[..., tuple[str, Any]],
    client: Any,
    is_new: bool,
    session_key: str,
    reach: Callable[[str], Reach] | None,
) -> str:
    """The prompt the agent *info* starts with in its session *session_key* on *client*.
    *named_agent* is the agent the spawn asked for by name (``""`` for none), *assemble* the
    context builder's ``build_message`` already told that agent, and *reach* answers whose work a
    session's is (``memory_reads.reach_of`` over the gateway's state)."""
    task = redact_for_model(info._raw_task or info.task)
    if named_agent:
        message = task
    else:
        from personalclaw.prompt_providers.runtime import render_snippet_block

        prefix = render_snippet_block("subagent-system-prefix")
        message = ((prefix + "\n\n") if prefix else SYSTEM_PREFIX) + task
    if info.capability_class == CAPABILITY_TEXT:
        return message
    from personalclaw.context_headroom import resolve_window

    reads = bool(reach and reach(session_key).reads)
    # Off the event loop: building the message embeds it with the embedding model.
    build = partial(assemble, window=await resolve_window(serving=client), blocks_reads=not reads)
    built, _ = await asyncio.to_thread(build, message, is_new, session_key)
    return built
