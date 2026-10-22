"""How the Automations list states its rows: their named order, and where each one reports (#3816).

The list is one list of everything that runs without the owner, read from ``GET /api/triggers``,
and the order is the route's to state, once. A page shows it as served, the way the notification
feed is newest first (``tests/test_notifications_are_listed_newest_first.py``). It puts first what
the owner must act on, then what runs next:

1. **Needs you** (``needs_you``): its last run failed, a run a restart missed or interrupted waits
   for the owner's decision (``triggers.review``), or a change waits for her Allow (an import
   waiting for review, an action it may not run yet, a newer workflow version, a restore's hold).
   A row whose spec cannot arm needs her too: it cannot run until she fixes it. Oldest first, since
   the oldest has waited longest; a wait with no recorded start is the oldest of all.
2. **Next to run** (``next_to_run``): a clock that will fire here, soonest first.
3. **Not on a clock** (``not_on_a_clock``): started by an event, a file, a webhook or by hand, or
   paused, switched off, or someone else's, which this machine never runs. By name, ignoring case.

The tie-breaker in every group is the row's id, so the order is total: a row moves only when its
state changes, never because the stores were read in another order.

It reads the states the automations already record, as the rows carry them on the wire: what
counts as failed is ``triggers.history``'s translation of the run's status, the same one the run
history shows. Pure functions over the serialized rows, so every kind is ordered by one rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from personalclaw.bounded_log import UNKNOWN, instant
from personalclaw.triggers.history import HOOK_STATUS_TO_OUTCOME, SCHEDULE_STATUS_TO_OUTCOME
from personalclaw.triggers.models import TRUE_FAILURE_OUTCOMES, Outcome

NEEDS_YOU = "needs_you"
NEXT_TO_RUN = "next_to_run"
NOT_ON_A_CLOCK = "not_on_a_clock"

#: The groups, in the order the list shows them.
GROUPS: tuple[str, ...] = (NEEDS_YOU, NEXT_TO_RUN, NOT_ON_A_CLOCK)

#: The lifecycle states in which a clock fires. Paused, autopaused, parked, quarantined and retired
#: rows wait for something else first.
_RUNNING_STATES = frozenset({"", "active"})


def _failed(row: Mapping[str, Any]) -> bool:
    """Whether the row's last run failed, by the translation its run history uses. A status the
    table does not name reads as a failure there, so it does here; no status is no run."""
    if row.get("kind") == "lifecycle":
        status = str(row.get("last_status") or "").lower()
        table = HOOK_STATUS_TO_OUTCOME
    else:
        status = str(row.get("last_run_status") or "")
        table = SCHEDULE_STATUS_TO_OUTCOME
    if not status:
        return False
    return table.get(status, Outcome.FAILED.value) in TRUE_FAILURE_OUTCOMES


def _last_run_at(row: Mapping[str, Any]) -> float:
    return instant(row.get("last_run" if row.get("kind") == "lifecycle" else "last_run_ts"))


def waiting_since(row: Mapping[str, Any], *, decisions: Mapping[str, float]) -> float | None:
    """When the row began to need the owner, or None when it does not.

    *decisions* maps a row's id to the oldest slot of a review card that waits on it. A row with
    several reasons has waited since the oldest of them. Someone else's row never needs this owner:
    it runs on their machine, and she can change nothing about it here.
    """
    if row.get("read_only"):
        return None
    since: list[float] = []
    if str(row.get("id") or "") in decisions:
        since.append(decisions[str(row["id"])])
    allow = (
        bool(row.get("needs_review"))
        or bool(row.get("needs_grant"))
        or bool((row.get("workflow_version") or {}).get("use"))
        or bool(row.get("restore_hold"))
    )
    if allow:
        since.append(instant(row.get("registered_at")))
    if row.get("broken"):
        since.append(UNKNOWN)
    if row.get("enabled") and _failed(row):
        since.append(_last_run_at(row))
    return min(since) if since else None


def _next_run(row: Mapping[str, Any]) -> float:
    """When the row's clock fires next here, or 0 when it is not a clock that will."""
    if row.get("kind") != "schedule" or not row.get("enabled") or row.get("read_only"):
        return 0.0
    if str(row.get("state") or "") not in _RUNNING_STATES:
        return 0.0
    at = instant(row.get("next_run_ts"))
    return at if at > 0 else 0.0


def place(row: Mapping[str, Any], *, decisions: Mapping[str, float]) -> tuple[str, tuple[Any, ...]]:
    """The row's group and its sort key within the whole list."""
    rid = str(row.get("id") or "")
    since = waiting_since(row, decisions=decisions)
    if since is not None:
        return NEEDS_YOU, (0, since, "", rid)
    upcoming = _next_run(row)
    if upcoming > 0:
        return NEXT_TO_RUN, (1, upcoming, "", rid)
    return NOT_ON_A_CLOCK, (2, 0.0, str(row.get("name") or "").casefold(), rid)


def ordered(
    rows: Iterable[dict[str, Any]], *, decisions: Mapping[str, float] | None = None
) -> list[dict[str, Any]]:
    """*rows* in the list's named order, each stamped with its ``group`` and its ``rank``, its place
    in the whole list: a page that lays out the same rows read in another order lays them out the
    same."""
    placed = []
    for row in rows:
        group, key = place(row, decisions=decisions or {})
        row["group"] = group
        placed.append((key, row))
    placed.sort(key=lambda pair: pair[0])
    for rank, (_key, row) in enumerate(placed):
        row["rank"] = rank
    return [row for _key, row in placed]


def reports_to(*, session: str = "", delivery: str = "") -> dict[str, str]:
    """Where a run's result goes, as the list names it: the thread it reports to, when its fire
    renders into one of the owner's conversations; else the chat channel, the Inbox, or nowhere.

    One rule for every kind that runs an action: a row bound to a conversation
    (``conversation:<key>``, as an auto-nudge is) or a callback resuming one reports to that
    thread; otherwise its result route (``delivery``) says.
    """
    from personalclaw.triggers.delivery import INBOX_ROUTE, parse_channel_route

    binding = str(session or "").strip()
    if binding.startswith("conversation:") and binding.split(":", 1)[1]:
        return {"kind": "thread", "session": binding.split(":", 1)[1]}
    route = str(delivery or "").strip()
    channel = parse_channel_route(route)
    if channel is not None:
        return {"kind": "channel", "channel": channel[0]}
    if route == "none":
        return {"kind": "none"}
    if route in ("", INBOX_ROUTE):
        return {"kind": "inbox"}
    return {}
