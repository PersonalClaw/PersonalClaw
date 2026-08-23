"""The layered admission gate every inbound surface passes through (§1.1, §1.3).

Four kill switches stack here, and a request must clear all four:

1. **master** — ``external_access.enabled``. Off unmounts all five surfaces.
2. **per-surface** — ``external_access.<surface>.enabled``.
3. **per-client** — the client record's own ``disabled`` flag, enforced in
   ``clients.lookup_by_token`` rather than here (see the note at the bottom).
4. **incident mode** — AUTONOMY-GUARDRAILS' ``~/.personalclaw/incident.json``. An
   active incident refuses every inbound request with 503, the same one-check
   pattern the other execution seams use.

Every one of them parses **fail-CLOSED**, which is the INVERSE of `guard_flag`.
Stated loudly because the inversion looks like a bug to anyone who learned the
guardrails convention first: a *guard* that cannot read its flag must keep
protecting (fail ON), while an *inbound surface* that cannot read its flag must
refuse to answer (fail OFF). Both rules say "ambiguity resolves toward safety";
safety points in opposite directions for the two. Do not "fix" this to be lenient.

Why a module rather than a method on each dialect: five dialects each re-deriving
"am I allowed to serve this?" is five chances for the fourth one to forget the
incident check. There is one function, and adding a surface means calling it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Returned by :func:`admission_problem` when incident mode is the blocker. The
#: caller maps this to 503 (retry later) rather than 404/403 (never) — an incident is
#: a temporary suspension, and telling a client "gone" makes it stop retrying.
INCIDENT_REASON = "incident mode is active — inbound suspended"


def surface_enablement_problem(surface: str) -> str | None:
    """Why ``surface`` must not mount, or None when it may. Layers 1, 2 and the token.

    Called at mount time AND per request, so flipping either config switch takes
    effect on the next call instead of needing a restart — a kill switch that needs
    a restart is not a kill switch.
    """
    from personalclaw.inbound import auth

    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        master = bool(getattr(cfg.external_access, "enabled", False))
        surface_cfg = getattr(cfg.external_access, surface, None)
        if surface_cfg is None:
            # An unknown surface name is a programming error, but it must not read as
            # "no restrictions found, therefore allowed" — the shape of bug that turns
            # a typo into an open port.
            return f"unknown surface {surface!r}"
        enabled = bool(getattr(surface_cfg, "enabled", False))
    except Exception:  # noqa: BLE001
        return "config unreadable (inbound stays off — the safe state)"
    if not master:
        return "external_access.enabled is off (master switch)"
    if not enabled:
        return f"external_access.{surface}.enabled is off"
    return auth.token_problem(surface)


def incident_problem() -> str | None:
    """``INCIDENT_REASON`` when unattended work is suspended, else None.

    An unreadable incident file reads as **ACTIVE** here. That is the fail-closed
    direction for this check specifically: `incident.json` exists to stop things, so
    "I could not tell whether we are in an incident" must not become "carry on".
    """
    try:
        from personalclaw.guardrails.incident import incident_active

        return INCIDENT_REASON if incident_active() else None
    except Exception:  # noqa: BLE001
        logger.warning(
            "inbound: incident state unreadable — refusing inbound (fail-closed)", exc_info=True
        )
        return INCIDENT_REASON


def admission_problem(surface: str) -> tuple[str | None, int]:
    """The surface-level verdict: ``(reason, http_status)``; reason None when clear.

    Statuses are deliberately different per layer, because they mean different
    things to a client: a disabled surface is **404** (it does not exist here — and
    404 rather than 403 so an off surface does not confirm its own existence to a
    prober), while an incident is **503** (it exists, come back later).
    """
    problem = surface_enablement_problem(surface)
    if problem:
        return f"disabled: {problem}", 404
    incident = incident_problem()
    if incident:
        return incident, 503
    return None, 200


# Layer 3 (the per-client `disabled` flag) deliberately has NO function here. It is
# enforced inside `clients.lookup_by_token`, which is the only place a client record
# comes into existence — so the check cannot be reached around, and there is nothing
# for a caller to forget. An earlier draft of this module exported a standalone
# `client_problem()` as well; it acquired no callers, which is the honest signal that
# it was a second implementation of a rule that already had one. Two spellings of one
# kill switch is how a kill switch ends up fired in one dialect and not the next.
