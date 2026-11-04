"""What each agent did in a window of time, read off its model calls.

Home's "Your agents today" band draws each agent's work as blocks on a time axis around now. The
usage ledger (:mod:`personalclaw.usage_ledger`) already writes one row per model call, naming the
session it was for, the agent that made it (``""`` for the default agent), when it ended (``ts``)
and how long it took (``duration_ms``). So a call is ``[ts - duration, ts]``, and calls in one
session that sit less than :data:`GAP_SECS` apart are one block: the time between them is the
agent running the tools the model asked for. Nothing here is stored; every read folds the ledger
again, so the band and the usage pages read one record.

``before`` is the end of the latest block an agent had earlier today (your day,
:mod:`personalclaw.spend_day`), for the band's "← 13:10" edge. Work from an earlier day is no part
of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from personalclaw import spend_day
from personalclaw import usage_ledger as ul

#: Two calls of one session less than this far apart are one block of work.
GAP_SECS = 120

#: The widest window one read folds. The band asks for four hours; a day bounds the work a caller
#: can make one request do.
MAX_WINDOW = timedelta(hours=24)

#: The most blocks one read answers, newest kept: a day of one agent working in many chats at once
#: is still one answer the band can draw.
MAX_BLOCKS = 500


def clock() -> datetime:
    """Now, in UTC: the one clock the band's window is measured by."""
    return datetime.now(timezone.utc)


def parse_time(ts: Any) -> datetime | None:
    """*ts* as an aware time (a naive one is UTC), or ``None`` when it is no ISO time."""
    try:
        at = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _calls(since: datetime) -> list[tuple[str, str, str, datetime, datetime]]:
    """Every call ending at or after *since*, as ``(agent, session, source, start, end)``, in the
    order they ended."""
    out = []
    for row in ul.calls(since=since.isoformat()):
        end = parse_time(row.get("ts"))
        if end is None:
            continue
        try:
            secs = max(0.0, float(row.get("duration_ms", 0) or 0) / 1000)
        except (TypeError, ValueError):
            secs = 0.0
        out.append(
            (
                str(row.get("agent", "") or ""),
                str(row.get("session_key", "") or ""),
                str(row.get("source", "") or ""),
                end - timedelta(seconds=secs),
                end,
            )
        )
    out.sort(key=lambda c: c[4])
    return out


def _blocks(calls: list[tuple[str, str, str, datetime, datetime]]) -> list[dict[str, Any]]:
    """*calls* of one agent as blocks: one session's calls under :data:`GAP_SECS` apart merge."""
    open_by_session: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for _agent, session, source, start, end in calls:
        block = open_by_session.get(session)
        if block is not None and (start - block["_end"]).total_seconds() < GAP_SECS:
            block["_end"] = max(block["_end"], end)
            continue
        block = {"_start": start, "_end": end, "session": session, "source": source}
        open_by_session[session] = block
        out.append(block)
    out.sort(key=lambda b: (b["_start"], b["session"]))
    return out


def _said(block: Mapping[str, Any]) -> dict[str, Any]:
    """*block* as the route answers it: its times, its session and the kind of work."""
    return {
        "start": block["_start"].isoformat(),
        "end": block["_end"].isoformat(),
        "session": block["session"],
        "source": block["source"],
    }


def read(
    *, since: datetime, until: datetime, now: datetime | None = None, limit: int = MAX_BLOCKS
) -> tuple[list[dict[str, Any]], int]:
    """Each agent that did something today, with its blocks in ``[since, until)`` and ``before``,
    and how many blocks were left out to keep to *limit*: the newest are kept.

    A block is in the window when its last call ended in it, as the ledger's own windows count a
    row. Agents come back in the order of their latest work, newest first.
    """
    now = now or clock()
    day_start = parse_time(spend_day.start_of(spend_day.day_of(now.isoformat()))) or now
    # Read from your midnight (or the window's start, if it began before it), so earlier work is
    # today's and nothing older.
    by_agent: dict[str, list[tuple[str, str, str, datetime, datetime]]] = {}
    for c in _calls(min(day_start, since)):
        by_agent.setdefault(c[0], []).append(c)
    out: list[dict[str, Any]] = []
    for agent, calls in by_agent.items():
        inside = [c for c in calls if since <= c[4] < until]
        earlier = [c for c in calls if c[4] < since]
        if not inside and not earlier:
            continue
        out.append(
            {
                "agent": agent,
                "blocks": _blocks(inside),
                "before": max(c[4] for c in earlier).isoformat() if earlier else None,
                "_latest": max(c[4] for c in inside + earlier),
            }
        )
    out.sort(key=lambda a: a["_latest"], reverse=True)
    every = [(b["_end"], i, j) for i, a in enumerate(out) for j, b in enumerate(a["blocks"])]
    kept = {(i, j) for _, i, j in sorted(every, key=lambda e: e[0], reverse=True)[:limit]}
    for i, a in enumerate(out):
        a["blocks"] = [_said(b) for j, b in enumerate(a["blocks"]) if (i, j) in kept]
        del a["_latest"]
    return out, len(every) - len(kept)
