"""A schedule's clock spec, from the body a request sends: one reading for a save and a preview.

The create, the edit and the preview each read ``every``, ``cron``, ``at``, ``timezone``,
``strict_schedule`` and ``skip_dates`` from a request body. They read them here, so a schedule
previewed and the same schedule saved are one spec. The store's spellings are ``interval_secs``,
``expr`` and ``at``; the request's are the legacy wire names, and only this module turns one into
the other.

A zone is carried as sent and not checked here: a save refuses an unknown zone before it builds
the spec, and a preview answers it as refused, in the clock's words.
"""

from __future__ import annotations

import math
import time
from typing import Any

#: The spec keys that survive a cadence change.
CARRIED_KEYS = ("timezone", "skip_dates", "strict")

NO_CADENCE = "every, cron, or at required"
EVERY_NOT_INTEGER = "'every' must be an integer"


def carried(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec keys that survive a CADENCE change.

    Replacing `{kind, expr}` wholesale would silently drop `timezone`/`skip_dates`/`strict` — the
    quietly-losable class, and the exact fields `verify-migration` exists to
    catch going missing. A user changing `0 9 * * *` to `0 10 * * *` must not lose their holidays.
    """
    return {k: v for k, v in spec.items() if k in CARRIED_KEYS}


def one_shot_time(value: Any, *, kept: Any = None, future_only: bool = True) -> tuple[float, str]:
    """A one-shot's ``at`` as a request sends it: epoch seconds still to come, or why not.

    One reading for the create and the edit, so a time is taken or refused alike wherever it is
    typed. A time already gone is refused rather than saved: the row would sit listed, switched on,
    and never fire, which is why the chat's one-time task refuses it too, in these words. *kept* is
    the time an edited row already has, which its form sends back with every save: taken as it is,
    so a one-shot whose time has passed can still be renamed. A preview (*future_only* false) takes
    a time that has passed, and says it never runs.
    """
    try:
        at = float(value)
    except (TypeError, ValueError):
        return 0.0, "'at' must be a Unix timestamp in seconds"
    if not math.isfinite(at):
        return 0.0, "'at' must be a Unix timestamp in seconds"
    try:
        unchanged = kept is not None and at == float(kept)
    except (TypeError, ValueError):
        unchanged = False
    if future_only and at <= time.time() and not unchanged:
        return 0.0, "That time has already passed. Give a time that is still to come."
    return at, ""


def spec_from_body(
    body: dict[str, Any],
    *,
    stored: dict[str, Any] | None = None,
    strict: bool | None = None,
    future_only: bool = True,
) -> tuple[dict[str, Any], str]:
    """The clock spec *body* asks for, and the problem with it ('' when there is none).

    With no *stored* spec (a new schedule) the body must name a cadence: ``every`` (seconds),
    ``cron`` or ``at`` (epoch seconds, still to come), first match wins. With *stored* (an edit, or
    a preview of one) the body changes only what it carries: a cadence it names replaces the stored
    one and keeps its zone, skip dates and strictness (:func:`carried`); a one-shot's time it sends
    back unchanged is taken even when it has passed. *strict* is the body's ``strict_schedule``
    switch as the caller read it (None when the body did not send one). *future_only* false is a
    preview's reading, which takes a one-shot time that has passed (see :func:`one_shot_time`).
    """
    spec: dict[str, Any] = dict(stored or {})
    was_once = str(spec.get("kind") or "") == "at"
    at, problem = 0.0, ""
    if body.get("at") and stored is not None:
        # An edit reads the time before the cadence is chosen, as it always did: a time that is
        # refused is refused whatever else the body names.
        at, problem = one_shot_time(
            body["at"], kept=spec.get("at") if was_once else None, future_only=future_only
        )
        if problem:
            return {}, problem
    # A new schedule takes `every` first and an edit takes `cron` first, as each path always did.
    # A form sends one cadence key, so the two orders meet only on a hand-made body.
    order = ("every", "cron", "at") if stored is None else ("cron", "every", "at")
    chosen = next((key for key in order if body.get(key)), "")
    if chosen == "every":
        try:
            cadence: dict[str, Any] = {"kind": "interval", "interval_secs": int(body["every"])}
        except (ValueError, TypeError):
            return {}, EVERY_NOT_INTEGER
        spec = {**cadence, **carried(spec)}
    elif chosen == "cron":
        spec = {"kind": "cron", "expr": str(body["cron"]).strip(), **carried(spec)}
    elif chosen == "at":
        if stored is None:
            at, problem = one_shot_time(body["at"], future_only=future_only)
            if problem:
                return {}, problem
        # A one-shot keeps what it was made as while its time moves: the page's leaves the list
        # after its run and the chat's stays (`service.retire_after_run`). A cadence changed into a
        # one-shot, or a new one, is the page's.
        leaves = bool(spec.get("delete_after_run")) if was_once else True
        spec = {"kind": "at", "at": at, "delete_after_run": leaves, **carried(spec)}
    elif stored is None:
        return {}, NO_CADENCE
    if stored is None:
        zone = str(body.get("timezone") or "").strip()
        if zone:
            spec["timezone"] = zone
        if strict:
            spec["strict"] = True
    else:
        if "timezone" in body:
            spec["timezone"] = str(body.get("timezone") or "").strip()
        if strict is not None:
            spec["strict"] = bool(strict)
    if isinstance(body.get("skip_dates"), list):
        spec["skip_dates"] = [str(d) for d in body["skip_dates"]]
    return spec, ""
