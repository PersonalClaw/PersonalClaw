"""Event-kind parity and lifecycle-event dormancy (AUTOMATION-SUBSTRATE §2/§7 — S67).

Two problems, one root cause: a user can configure something the code never delivers, and nothing
tells them. Both are made QUERYABLE here rather than fixed by faking the missing behaviour.

**Dormancy, measured.** `hooks.HOOK_EVENTS` declares 15 lifecycle events and
`validation.ALLOWED_HOOK_EVENTS` allows all 15, so the hook UI offers all 15 as equals. Only some
are ever fired. A user configures a `SessionEnd` hook, the API accepts it, and it never runs.

Measuring this correctly took three attempts, and the failures are the reason `DORMANT_EVENTS` below
is a reviewed constant rather than a scan:

* Counting `HOOK_EVENT_*` text hits calls `Stop` live off `autonudge.py`'s **docstring** and calls
  seven events live off `chat_runner.py`'s **import block** — text that fires nothing.
* The real `TaskComplete` fire (`tasks/native.py`) passes `payload["event"]` from
  `pool.lifecycle_payload`, so it contains **no constant reference at all** and a scan calls the one
  event this program deliberately wired DORMANT.

Both directions are harmful, and the second is worse: telling someone their working hook is dead. So
dormancy is derived from the fire sites reached through `ScriptHookStore.fire`/`fire_for_ids`, which
is the only path that runs a hook, and `verify_dormancy()` re-derives the live set from the running
store's own catalog so a wired event cannot leave this list stale and lying.

**Parity, measured.** §2 says the `event` kind's facade is uneven. It is worse than uneven: in
`dashboard/handlers/triggers.py` the `event` kind is handled in `list`, `create` and `DELETE`, and
`toggle`/`run`/`test`/`history`/`PUT` have **no `event` branch**, so an `event:`-prefixed id falls
through to the `schedule` branch and is looked up among cron jobs. It is not there, so the user gets
`404 {"error": "not found"}` — the API says their trigger does not exist. `PARITY_OPERATIONS` plus
`missing_operations()` turn that into an assertion a facade test can make per kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Every operation a trigger kind must support to be a first-class citizen of the API (§2's parity
#: fix). Data rather than prose so a facade test asserts it per kind — the alternative is learning
#: about the gap when a user asks why their event trigger has no history.
PARITY_OPERATIONS: tuple[str, ...] = (
    "list",
    "get",
    "create",
    "update",
    "delete",
    "toggle",
    "run",
    "test",
    "history",
)

#: Operations that are genuinely meaningless for a kind, with the reason. Declared rather than
#: inferred, so "this kind is missing `toggle`" stays a real finding everywhere else. A refusal with
#: a reason is a different thing from a 404: the first explains, the second denies the row exists.
PARITY_EXEMPTIONS: dict[str, dict[str, str]] = {
    "lifecycle": {
        # Measured: `api_trigger_run` refuses lifecycle with 400 "use /test". That is correct — a
        # lifecycle trigger has no standalone fire, it runs when its event happens.
        "run": "a lifecycle trigger fires on an agent event; /test executes its action once",
    },
    "schedule": {
        # Measured: `api_trigger_test` refuses schedule with 400 "use /run". Also correct: a
        # schedule trigger's action IS its run, and /run already has a dry-run mode.
        "test": "a schedule trigger's action is its run; /run?dry_run=1 previews it",
    },
}


class EventStatus(str, Enum):
    """Whether a declared lifecycle event actually reaches a hook.

    `DORMANT` is the point of the enum. It is not an error state — the catalog row is real and the
    event fires once its subsystem wires it — but a user must see the difference BEFORE building an
    automation on it.
    """

    LIVE = "live"
    DORMANT = "dormant"


#: Lifecycle events nothing fires, measured against the `fire`/`fire_for_ids` call sites (S67).
#:
#: A reviewed constant, not a scan, because every automatic derivation measured wrong — see the
#: module docstring. `verify_dormancy()` is the guard that keeps it honest: it re-derives the LIVE
#: set from the running hook store and reports disagreement, so wiring an event and forgetting this
#: list is a caught test failure rather than a lie in the UI.
#:
#: `TaskComplete` is deliberately ABSENT: it was dormant when AUTOMATION-SUBSTRATE was written (the
#: plan says 8) and this program wired it in S60 via `tasks/native.py`. Seven remain.
DORMANT_EVENTS: frozenset[str] = frozenset(
    {
        "ApprovalRequest",
        "ContextCompact",
        "MemoryWrite",
        "PostResponse",
        "PreResponse",
        "SessionEnd",
        "SubagentSpawn",
    }
)

#: Why each dormant event does not fire, and what would wire it. Per-event rather than one generic
#: sentence: "no code fires this" tells a user nothing actionable, while naming the subsystem that
#: would own the fire site makes the gap legible and the fix findable.
DORMANCY_NOTES: dict[str, str] = {
    "ApprovalRequest": "the approval path resolves without emitting a lifecycle event",
    "ContextCompact": "context compaction does not announce itself on the hook bus",
    "MemoryWrite": "memory writes notify the event-trigger engine, not the hook store",
    "PostResponse": "the response path fires Stop, not the finer Pre/PostResponse pair",
    "PreResponse": "the response path fires Stop, not the finer Pre/PostResponse pair",
    "SessionEnd": "session teardown has no fire site; SessionStart does",
    "SubagentSpawn": "the subagent manager has its own on_event bus, separate from the hook store",
}

_DORMANT_SUFFIX = (
    "configurable, but nothing fires it yet — a hook on this event saves and never runs"
)


@dataclass
class EventInfo:
    """One lifecycle event and whether it fires."""

    name: str
    status: str
    note: str = ""

    @property
    def dormant(self) -> bool:
        return self.status == EventStatus.DORMANT.value

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "note": self.note}


def event_status() -> list[EventInfo]:
    """Every declared lifecycle event with its live/dormant status.

    Walks `hooks.HOOK_EVENTS` — the declaration is what the UI offers, so the declaration is what
    must be annotated. An event missing from it cannot be configured at all and needs no badge.
    """
    from personalclaw.hooks import HOOK_EVENTS

    out: list[EventInfo] = []
    for event in sorted(HOOK_EVENTS):
        if event in DORMANT_EVENTS:
            reason = DORMANCY_NOTES.get(event, "")
            note = f"{reason}; {_DORMANT_SUFFIX}" if reason else _DORMANT_SUFFIX
            out.append(EventInfo(name=event, status=EventStatus.DORMANT.value, note=note))
        else:
            out.append(EventInfo(name=event, status=EventStatus.LIVE.value))
    return out


def dormant_events() -> list[str]:
    """The declared events nothing fires, in catalog order."""
    return [i.name for i in event_status() if i.dormant]


def live_events() -> list[str]:
    return [i.name for i in event_status() if not i.dormant]


def configurable_but_dead() -> list[str]:
    """Events the API ACCEPTS but nothing fires — the user-visible half of the problem.

    Distinct from `dormant_events()` because the two can differ. Dormant AND disallowed is honest:
    nobody can configure it, so nobody is misled. Dormant AND allowed is the trap. This names only
    the trap, so a test can assert the allowlist and the fire sites stay reconciled.
    """
    from personalclaw.validation import ALLOWED_HOOK_EVENTS

    return [name for name in dormant_events() if name in ALLOWED_HOOK_EVENTS]


def verify_dormancy(
    declared: set[str] | frozenset[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Reconcile `DORMANT_EVENTS` against the events the catalog declares.

    Returns `(unknown, missing)`: names listed dormant that the catalog no longer declares, and
    declared events absent from BOTH the dormant list and the catalog — either of which means this
    module has drifted from `hooks.py`.

    The guard that lets dormancy be a reviewed constant instead of a scan. A hand-maintained list is
    only safe when something fails loudly on drift; without this, wiring `SessionEnd` and forgetting
    to update the list would leave the UI telling users a working hook is dead.
    """
    from personalclaw.hooks import HOOK_EVENTS

    catalog = set(declared) if declared is not None else set(HOOK_EVENTS)
    unknown = sorted(name for name in DORMANT_EVENTS if name not in catalog)
    missing = sorted(name for name in DORMANCY_NOTES if name not in DORMANT_EVENTS)
    return unknown, missing


def missing_operations(kind: str, supported: set[str] | list[str]) -> list[str]:
    """Parity operations a kind does not support, minus its declared exemptions.

    What a facade test asserts against. §2 records the event kind shipping with less surface than
    the clock kind; a per-kind check is what stops the next kind from repeating it.
    """
    exempt = set(PARITY_EXEMPTIONS.get(kind, {}))
    have = set(supported)
    return sorted(op for op in PARITY_OPERATIONS if op not in have and op not in exempt)


def parity_report(support: dict[str, set[str] | list[str]]) -> dict[str, list[str]]:
    """Per-kind parity gaps. Returns only kinds WITH gaps.

    An empty dict is the passing state, so a test asserts on the whole report rather than walking
    mostly-good news and hoping the interesting row was not skipped.
    """
    gaps: dict[str, list[str]] = {}
    for kind, ops in support.items():
        missing = missing_operations(kind, ops)
        if missing:
            gaps[kind] = missing
    return gaps


def unsupported_response(kind: str, operation: str) -> tuple[str, int]:
    """The honest refusal for an operation a kind genuinely cannot do. Returns `(message, status)`.

    400 with a reason, never 404. The measured bug this fixes: an `event:`-prefixed id sent to
    `toggle`/`run` falls through to the schedule branch, gets looked up among cron jobs, and returns
    `404 not found` — the API telling a user the trigger they are looking at does not exist. A 400
    that names the kind and the reason is the difference between "you cannot do that to this" and
    "that does not exist".
    """
    reason = PARITY_EXEMPTIONS.get(kind, {}).get(operation, "")
    if reason:
        return f"{kind} triggers do not support /{operation}: {reason}", 400
    return f"{kind} triggers do not support /{operation}", 400
