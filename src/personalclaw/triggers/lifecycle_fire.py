"""Firing the dormant lifecycle events (AUTO §7 criterion 5 — S82).

Criterion 5 has two clauses. S67 closed the first (event-kind API parity). This is the second:
"**the 8 dormant lifecycle events actually fire**".

**Measured before writing.** `triggers.events.configurable_but_dead()` reports seven events —
`ApprovalRequest`, `ContextCompact`, `MemoryWrite`, `PostResponse`, `PreResponse`, `SessionEnd`,
`SubagentSpawn` — and a grep for each name outside its own declaration finds exactly ONE hit: the
`validation.py` allowlist. They are selectable in the hook UI, they validate, they save, and nothing
ever calls them. A user can configure one and wait forever.

(The plan says "8"; the eighth was `TaskComplete`, which S61e/S61f wired — `dormant_events()` now
returns seven. The count in the criterion predates that session.)

**The payload shape is the existing one, deliberately.** `workflows/pool.lifecycle_payload` already
established that a lifecycle fire passes `event` + a `context` string rather than inventing hook
variables, because the hook UI renders a FIXED `vars` tuple per event and a variable the UI does not
list is one no user can discover. Each builder returns that same two-key dict, and a test asserts
every builder's `event` is a name the catalog declares — so a typo becomes a red test rather than an
event nobody receives.

**An observer never fails the thing it observed.** Every fire is wrapped: a broken hook script must
not turn a successful memory write into an exception, and the write has already happened by the time
the hook runs. That is the rule `tasks/native.py` set for `TaskComplete`, applied to seven sites.

**Two of the seven fire from SYNC code** (`MemoryService.write_lesson`, `SubagentManager.spawn`)
while `ScriptHookStore.fire` is a coroutine. `fire_sync` bridges that by scheduling onto the running
loop when there is one and skipping otherwise — measured rather than assumed, see its docstring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The events this module owns. Kept as data so `test_every_dormant_event_has_a_builder` can walk it
#: against `triggers.events.dormant_events()` — a new dormant event added to the catalog without a
#: fire site becomes a failing test instead of another silently dead option in the UI.
DORMANT_EVENTS: tuple[str, ...] = (
    "PreResponse",
    "PostResponse",
    "SessionEnd",
    "MemoryWrite",
    "ContextCompact",
    "SubagentSpawn",
    "ApprovalRequest",
)

#: Max characters of any single free-text field in a context string. Hook context reaches a shell
#: script's environment, and an unbounded assistant reply or memory body would blow the environment
#: size limit — `E2BIG` on exec, which surfaces as "the hook mysteriously stopped running".
FIELD_CAP = 120


def _kv(pairs: list[tuple[str, Any]]) -> str:
    """`k=v` joined by spaces, skipping empties, each value capped.

    The shape `pool.lifecycle_payload` uses. Empty values are DROPPED rather than rendered as `k=`:
    a script parsing the context cannot distinguish "absent" from "empty string" otherwise, and the
    difference matters for an optional field like `agent`.
    """
    out: list[str] = []
    for key, value in pairs:
        text = str(value or "").strip().replace("\n", " ")
        if not text:
            continue
        out.append(f"{key}={text[:FIELD_CAP]}")
    return " ".join(out)


# ── the payload builders, one per event ──


def pre_response_payload(*, session_key: str = "", agent: str = "") -> dict[str, str]:
    """Before the agent streams its reply."""
    return {"event": "PreResponse", "context": _kv([("session", session_key), ("agent", agent)])}


def post_response_payload(
    *, session_key: str = "", agent: str = "", reply_chars: int = 0, tool_calls: int = 0
) -> dict[str, str]:
    """After the agent finishes its reply.

    Carries SIZE rather than the reply text. A hook that wants the content can read the transcript;
    passing an entire assistant turn through the environment is the `E2BIG` failure above, and it
    would also hand untrusted model output to a shell script as an argument.
    """
    return {
        "event": "PostResponse",
        "context": _kv(
            [
                ("session", session_key),
                ("agent", agent),
                ("reply_chars", reply_chars),
                ("tool_calls", tool_calls),
            ]
        ),
    }


def session_end_payload(
    *, session_key: str = "", reason: str = "", turns: int = 0
) -> dict[str, str]:
    """A session ends.

    `reason` distinguishes a user closing a tab from an eviction or a shutdown — a cleanup hook that
    cannot tell those apart will either run too often or miss the case it was written for.
    """
    return {
        "event": "SessionEnd",
        "context": _kv([("session", session_key), ("reason", reason), ("turns", turns)]),
    }


def memory_write_payload(
    *, kind: str = "", key: str = "", scope: str = "", session_key: str = ""
) -> dict[str, str]:
    """The agent writes a memory/lesson.

    The KEY and KIND, never the body. A memory body is user content, and the whole point of the
    fencing work in S69/S79 is that untrusted text does not travel into places that execute.
    """
    return {
        "event": "MemoryWrite",
        "context": _kv([("kind", kind), ("key", key), ("scope", scope), ("session", session_key)]),
    }


def context_compact_payload(
    *, session_key: str = "", before_chars: int = 0, after_chars: int = 0
) -> dict[str, str]:
    """The conversation context is summarized.

    Both sizes, because the useful signal is the RATIO: a compaction that barely shrank anything is
    the interesting event (it means the window is genuinely full of irreducible content), and one
    number cannot say that.
    """
    return {
        "event": "ContextCompact",
        "context": _kv(
            [("session", session_key), ("before", before_chars), ("after", after_chars)]
        ),
    }


def subagent_spawn_payload(
    *, subagent_id: str = "", parent_session_key: str = "", agent_role: str = "", depth: int = 0
) -> dict[str, str]:
    """A subagent is spawned.

    The catalog declares `$subagent_id`, `$parent_session_key` and `$agent_role` for this event, so
    those three names appear verbatim in the context string as well as riding `fire()`'s dedicated
    parameters — a hook author reading the UI's variable list finds them where the list promises.
    """
    return {
        "event": "SubagentSpawn",
        "context": _kv(
            [
                ("subagent_id", subagent_id),
                ("parent_session_key", parent_session_key),
                ("agent_role", agent_role),
                ("depth", depth),
            ]
        ),
    }


def approval_request_payload(
    *, tool: str = "", source: str = "", session_key: str = "", approval_id: str = ""
) -> dict[str, str]:
    """A tool needs approval.

    NOT the tool's input. An approval prompt is exactly the moment a hook must not be handed
    attacker-influenced arguments: the payload names WHAT is being approved and WHERE it came from,
    and a hook that needs more reads the pending-approval record through the API.

    This fire is OBSERVATIONAL — it cannot approve or deny. The gate stays with the user (and the
    origin-aware timeout that fails closed); a hook that could answer it would be a remote-approval
    channel nobody designed.
    """
    return {
        "event": "ApprovalRequest",
        "context": _kv(
            [
                ("tool", tool),
                ("source", source),
                ("session", session_key),
                ("approval", approval_id),
            ]
        ),
    }


#: event name → builder, for the walk test and for a caller that has a name rather than a function.
BUILDERS = {
    "PreResponse": pre_response_payload,
    "PostResponse": post_response_payload,
    "SessionEnd": session_end_payload,
    "MemoryWrite": memory_write_payload,
    "ContextCompact": context_compact_payload,
    "SubagentSpawn": subagent_spawn_payload,
    "ApprovalRequest": approval_request_payload,
}


# ── firing ──


async def fire(payload: dict[str, str], **extra: Any) -> None:
    """Fire one lifecycle event. NEVER raises.

    `extra` forwards `fire()`'s dedicated keyword parameters (`subagent_id`, `parent_session_key`,
    `agent_role`, `tool_name`) for the events whose catalog entry declares them.

    A missing hook store is a no-op rather than an error: the store is absent in CLI runs and in
    most tests, and an observer that raised there would make every one of those paths fail on a
    feature they do not use.
    """
    try:
        from personalclaw.hooks import get_global_hook_store

        store = get_global_hook_store()
        if store is None:
            return
        await store.fire(payload["event"], context=payload.get("context", ""), **extra)
    except Exception:  # noqa: BLE001 - an observer never fails the thing it observed
        logger.debug("lifecycle hook fire failed for %s", payload.get("event"), exc_info=True)


def fire_sync(payload: dict[str, str], **extra: Any) -> None:
    """Fire from SYNCHRONOUS code. Never raises, never blocks.

    Two of the seven sites are sync (`MemoryService.write_lesson`, `SubagentManager.spawn`) while
    `ScriptHookStore.fire` is a coroutine. Measured before choosing how to bridge:

    * `asyncio.run()` from inside a running loop raises `RuntimeError` — and both call sites are
      reachable from the dashboard's loop, so that would turn a memory write into a crash.
    * `run_coroutine_threadsafe` needs a loop reference the sync caller does not have.

    So: schedule a task when a loop IS running, and skip when none is. Skipping is the honest answer
    for a CLI write — there is no event loop to run a hook on, and blocking a synchronous write to
    start one would make every `write_lesson` pay for a feature most users do not configure. The
    task reference is deliberately dropped; `fire` already swallows its own failures, and holding it
    would keep the payload alive for no reader.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        loop.create_task(fire(payload, **extra))
    except Exception:  # noqa: BLE001 - scheduling failure must not fail the observed write
        logger.debug("lifecycle hook scheduling failed for %s", payload.get("event"), exc_info=True)
