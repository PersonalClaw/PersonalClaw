"""The `view` kind — pull-on-view refresh (R10 / §7 item 8 — S123).

**🔴 THE DEFECT THIS CLOSES.** `view` is the FOURTH declared kind found with no runtime, after
`file` (S93), `web_watch` (S121) and `run_completed` (S122). It is in `KINDS`, `SPEC_KEYS`
accepts `{surface_binding, ttl_secs}`, the store persists it, `/api/triggers` lists it and the
Automations page renders it. Measured: `surface_binding` is referenced by **exactly one** line
in the entire tree — its own declaration in `SPEC_KEYS`. Nothing reads it, so nothing could
ever fire a `view` trigger.

**Why this one is not a poll, and why that is the point.** §3's own words: *"Pull-on-view (R10):
fires when a bound surface (dashboard tile, artifact open) renders past TTL; within TTL serve
cache … Sidesteps the 1440-run-dirs critique by never firing unviewed."* A minutely clock
trigger produces 1440 run directories a day whether or not anyone looks; a `view` trigger
fires only when a human actually opens the thing. So the runtime is a function a RENDER calls,
not a background loop — adding a poll here would reintroduce exactly the cost R10 exists to
avoid.

**The TTL is the whole control.** Two renders inside the window must serve cache and cost
nothing; the first render past it refreshes. That makes the trigger's expense proportional to
attention rather than to wall-clock time.

**Rate-capped independently of the TTL**, because they answer different questions. A TTL of 60
on a tile someone leaves open in a dashboard that re-renders on every websocket nudge would
refresh once a minute forever — so `MIN_REFRESH_INTERVAL_SECS` is a floor beneath any
author-supplied TTL, the same reasoning `web_poll` applies to `poll_interval` and for the same
reason S109 recorded: a declared floor that no code reads is not a floor.

**Freshness is a SIDECAR**, matching `file_poll` and `web_poll`: last-refresh state is
high-churn, and writing it onto the trigger entity would rewrite `triggers.json` on every
render and race every unrelated edit.

**What this does NOT own:** the refresh's own execution (the caller hands the payload to the
shared dispatch, so a `view` fire passes the same gates as every other kind), and the
per-refresh token cost §3 mentions for the runs-inbox freshness column — that number comes
from the executor's run record, not from the decision to refresh.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The default TTL when an author names a surface but no window. Ten minutes: long enough that
#: opening a dashboard twice while reading costs one refresh, short enough to not look stale.
DEFAULT_TTL_SECS = 600.0

#: The floor beneath any author-supplied TTL. A dashboard re-renders on every websocket nudge, so a
#: TTL of 1 would mean a refresh per nudge — an LLM turn per keystroke elsewhere in the UI. S109
#: recorded the R1 interval floor being declared but read by no code; this one applies at the
#: point of decision.
MIN_REFRESH_INTERVAL_SECS = 60.0

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Freshness:
    """When a bound surface last refreshed, and how often it has.

    `refreshes` is carried for the runs-inbox freshness column §3 asks for: "this tile has refreshed
    12 times" is the number that tells a user whether a binding is worth its cost.
    """

    last_refresh_at: float = 0.0
    refreshes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"last_refresh_at": self.last_refresh_at, "refreshes": self.refreshes}

    @classmethod
    def from_dict(cls, raw: Any) -> Freshness:
        if not isinstance(raw, dict):
            return cls()
        try:
            return cls(
                last_refresh_at=float(raw.get("last_refresh_at", 0.0) or 0.0),
                refreshes=int(raw.get("refreshes", 0) or 0),
            )
        except (TypeError, ValueError):
            return cls()


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    from personalclaw.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    safe = _SAFE_RE.sub("-", trigger_id) or "view"
    return root / "trigger-view" / f"{safe}.json"


def load_freshness(trigger_id: str, *, base_dir: Path | str | None = None) -> Freshness:
    """This binding's freshness, or a never-refreshed one. Never raises.

    A corrupt sidecar reads as never-refreshed, which costs one extra refresh — strictly better than
    a render that raises because a JSON file was truncated.
    """
    try:
        return Freshness.from_dict(
            json.loads(_state_path(trigger_id, base_dir).read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError):
        return Freshness()


def save_freshness(
    trigger_id: str, state: Freshness, *, base_dir: Path | str | None = None
) -> None:
    """Persist atomically. A failure is logged, never raised: a render must not fail on this."""
    from personalclaw.atomic_write import atomic_write

    path = _state_path(trigger_id, base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n")
    except OSError:
        logger.debug("pull_on_view: could not persist freshness for %s", trigger_id, exc_info=True)


def ttl_for(trigger: Any) -> float:
    """The trigger's TTL, floored at `MIN_REFRESH_INTERVAL_SECS`.

    Floored rather than refused, matching `web_poll.poll_interval_for`: a user who typed 5 wants a
    fresh tile, and the floor gives them the freshest one that is not an LLM turn per re-render.
    """
    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    try:
        requested = float(spec.get("ttl_secs", 0) or 0)
    except (TypeError, ValueError):
        requested = 0.0
    return max(requested or DEFAULT_TTL_SECS, MIN_REFRESH_INTERVAL_SECS)


def bound_triggers(store: Any, *, surface: str) -> list[Any]:
    """Every enabled `view` trigger bound to `surface`.

    A trigger with no `surface_binding` matches NOTHING rather than every surface: a binding left
    blank would otherwise refresh on every render in the product, which is the opposite of what
    pull-on-view is for.
    """
    if not surface:
        return []
    out: list[Any] = []
    for row in store.load():
        trigger = row.trigger
        if not getattr(row, "ok", True) or trigger.kind != "view" or not trigger.enabled:
            continue
        spec = trigger.spec if isinstance(trigger.spec, dict) else {}
        if str(spec.get("surface_binding", "") or "").strip() == surface:
            out.append(trigger)
    return out


@dataclass
class ViewDecision:
    """Whether a render should refresh, with the reason either way.

    A REASON in both directions, because "served cache" is the answer most of the time and a surface
    that could not explain why it did not refresh is indistinguishable from a broken binding.
    """

    refresh: bool
    reason: str = ""
    payload: dict[str, Any] | None = None
    age_secs: float = 0.0


def on_render(
    trigger: Any,
    *,
    now: float,
    base_dir: Path | str | None = None,
    persist: bool = True,
) -> ViewDecision:
    """Decide one render: refresh past TTL, else serve cache. THE runtime for the `view` kind.

    Called by a surface as it renders — never by a loop. That is R10's whole point: a `view` trigger
    must cost nothing when nobody is looking, and a background poll would reintroduce the 1440-run
    directories a day that this kind exists to avoid.

    `persist=False` lets a caller ASK without consuming the window (a dry-run, or a freshness column
    that reports staleness without triggering work). Without it, merely rendering the answer would
    change it.
    """
    freshness = load_freshness(trigger.id, base_dir=base_dir)
    ttl = ttl_for(trigger)
    age = now - freshness.last_refresh_at if freshness.last_refresh_at else float("inf")

    if freshness.last_refresh_at and age < ttl:
        return ViewDecision(
            refresh=False,
            reason=f"served cache ({int(age)}s old, TTL {int(ttl)}s)",
            age_secs=age,
        )

    if persist:
        save_freshness(
            trigger.id,
            Freshness(last_refresh_at=now, refreshes=freshness.refreshes + 1),
            base_dir=base_dir,
        )

    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    return ViewDecision(
        refresh=True,
        reason=(
            "first render"
            if not freshness.last_refresh_at
            else f"stale ({int(age)}s > {int(ttl)}s)"
        ),
        age_secs=0.0 if age == float("inf") else age,
        payload={
            "trigger_id": trigger.id,
            "trigger_name": trigger.name,
            "kind": "view",
            "surface_binding": str(spec.get("surface_binding", "") or ""),
            # The refresh COUNT rides along for §3's freshness column: "refreshed 12 times" is what
            # tells a user whether a binding earns its cost.
            "refresh_number": freshness.refreshes + 1,
        },
    )


def renders(
    store: Any,
    *,
    surface: str,
    now: float,
    base_dir: Path | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """`(payloads, served_cache)` for one render of `surface`.

    Both returned, so the caller can dispatch the refreshes AND report the cache hits — §7 criterion
    8's zero-silent-drops rule applies to a skipped refresh exactly as to a skipped fire.
    """
    payloads: list[dict[str, Any]] = []
    cached: list[dict[str, str]] = []
    for trigger in bound_triggers(store, surface=surface):
        try:
            decision = on_render(trigger, now=now, base_dir=base_dir)
        except Exception:  # noqa: BLE001 - a render must never fail on one bad binding
            logger.warning("view trigger %s failed on render", trigger.id, exc_info=True)
            cached.append({"trigger_id": trigger.id, "reason": "the binding raised"})
            continue
        if decision.refresh and decision.payload is not None:
            payloads.append(decision.payload)
        else:
            cached.append({"trigger_id": trigger.id, "reason": decision.reason})
    return payloads, cached
