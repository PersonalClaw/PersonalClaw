"""Settings → External Access — the inbound seam's operator surface (EA-1, §1.5).

Read-mostly. The panel needs four things and this module is careful about which of
them it will hand back:

* **Per-surface state** — enabled, whether a valid token exists, and remote posture.
  Whether a token EXISTS is reported; the token itself never is. `token_problem`
  already returns a reason rather than a bool, so "why is this surface off?" is
  answerable without the credential ever entering a response body.
* **Per-client records** — labels and bindings, with the token *hash* elided too.
  A hash is not a credential, but publishing it over HTTP hands an offline
  guesser the exact target it needs, for no operator benefit.
* **Derived activity** — last-seen and request counts computed FROM
  `inbound_audit.jsonl`, not from a second counter kept beside it. The guardrails
  health-view pattern: a count maintained alongside a table is two things that can
  disagree, and the table is the one that is true.
* **Kill switches** — flipped through the existing `_EDITABLE_CONFIG` PATCH path,
  NOT here. `public_url`, `allow_remote` and the tokens are deliberately unreachable
  from any write path on this surface.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)


def _surface_rows() -> list[dict]:
    """One row per surface: its switches and whether a usable token is configured."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.inbound import auth

    try:
        ea = AppConfig.load().external_access
    except Exception:  # noqa: BLE001
        logger.debug("external-access: config unreadable", exc_info=True)
        return []
    rows: list[dict] = []
    for surface in auth.surfaces():
        surface_cfg = getattr(ea, surface, None)
        problem = auth.token_problem(surface)
        rows.append(
            {
                "surface": surface,
                "enabled": bool(getattr(surface_cfg, "enabled", False)),
                "allow_remote": bool(getattr(surface_cfg, "allow_remote", False)),
                # The REASON, not the token. An operator whose surface will not mount
                # needs the cause named; a bare `false` costs an hour.
                "token_configured": problem is None,
                "token_problem": problem or "",
                # The bridge's exception is reported as data so the FE does not have to
                # re-derive a rule the backend already enforces.
                "loopback_only": surface == auth.BRIDGE_SURFACE,
            }
        )
    return rows


def _client_rows() -> list[dict]:
    """One row per client, with activity derived from the audit trail."""
    from personalclaw.inbound import audit as audit_mod
    from personalclaw.inbound import clients as clients_mod

    counts: dict[str, int] = {}
    refusals: dict[str, int] = {}
    try:
        for row in audit_mod.recent(limit=2000):
            cid = str(row.get("client_id") or "")
            if not cid:
                continue
            counts[cid] = counts.get(cid, 0) + 1
            if row.get("refused_reason"):
                refusals[cid] = refusals.get(cid, 0) + 1
    except Exception:  # noqa: BLE001 — an unreadable trail means "no activity yet"
        logger.debug("external-access: audit read failed", exc_info=True)
    out: list[dict] = []
    for client in clients_mod.load_clients().values():
        out.append(
            {
                "client_id": client.client_id,
                "label": client.label,
                "surfaces": list(client.surfaces),
                "agent": client.agent,
                "tools": list(client.tools),
                "scope": dict(client.scope),
                "rate_overrides": dict(client.rate_overrides),
                "disabled": bool(client.disabled),
                "created_at": client.created_at,
                "last_seen_at": client.last_seen_at,
                # Derived, per §1.5 — never a stored counter.
                "requests_seen": counts.get(client.client_id, 0),
                "refusals_seen": refusals.get(client.client_id, 0),
                # 🔴 `token_hash` is deliberately ABSENT. See the module docstring.
            }
        )
    out.sort(key=lambda r: (r["label"].lower(), r["client_id"]))
    return out


async def api_external_access(request: web.Request) -> web.Response:
    """GET /api/external-access — the whole operator view of the inbound seam."""
    from personalclaw.config.loader import AppConfig

    try:
        ea = AppConfig.load().external_access
        master = bool(ea.enabled)
        caps = {
            "rate_rps": float(ea.rate_rps),
            "rate_burst": int(ea.rate_burst),
            "rate_concurrent": int(ea.rate_concurrent),
            "auto_disable_after_breaches": int(ea.auto_disable_after_breaches),
            "capture_retention_days": int(ea.capture_retention_days),
        }
        public_url = str(ea.public_url or "")
    except Exception:  # noqa: BLE001
        logger.debug("external-access: config unreadable", exc_info=True)
        master, caps, public_url = False, {}, ""
    incident = False
    try:
        from personalclaw.guardrails.incident import incident_active

        incident = bool(incident_active())
    except Exception:  # noqa: BLE001
        incident = True  # unreadable ⇒ report the refusing state, matching `gate.py`
    return web.json_response(
        {
            "enabled": master,
            # Reported so the panel can explain why an enabled surface is answering
            # 503 — otherwise the operator sees "on" and a dead endpoint.
            "incident_active": incident,
            "public_url": public_url,
            "caps": caps,
            "surfaces": _surface_rows(),
            "clients": _client_rows(),
        }
    )


async def api_external_access_client(request: web.Request) -> web.Response:
    """POST /api/external-access/clients — create; DELETE …/{client_id} — revoke.

    The token is in the CREATE response and nowhere else, ever: only its hash is
    stored, so this is the single moment it can be shown. Revocation deletes the
    record, which is what kills the token — there is no separate revocation list to
    fall out of sync with the registry.
    """
    from personalclaw.inbound import auth
    from personalclaw.inbound import clients as clients_mod

    if request.method == "DELETE":
        client_id = str(request.match_info.get("client_id", "") or "")
        if not clients_mod.revoke_client(client_id):
            return json_error("not_found", message=f"unknown client {client_id!r}", status=404)
        return web.json_response({"ok": True, "revoked": client_id})

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        return json_error("invalid_body", message="body must be a JSON object", status=400)
    label = str(body.get("label", "") or "").strip()
    if not label:
        return json_error("invalid_request", message="label is required", status=400)
    raw_surfaces = body.get("surfaces")
    if not isinstance(raw_surfaces, list) or not raw_surfaces:
        return json_error(
            "invalid_request", message="surfaces must be a non-empty list", status=400
        )
    known = set(auth.surfaces())
    requested = [str(s) for s in raw_surfaces]
    unknown = sorted(set(requested) - known)
    if unknown:
        # Refused rather than filtered: silently dropping an unknown surface would
        # create a client the operator believes is bound to something it is not.
        return json_error(
            "invalid_request",
            message=(f"unknown surfaces: {', '.join(unknown)} (known: {', '.join(sorted(known))})"),
            status=400,
        )
    tools = body.get("tools")
    scope = body.get("scope")
    rate_overrides = body.get("rate_overrides")
    client, token = clients_mod.create_client(
        label,
        surfaces=requested,
        agent=str(body.get("agent", "") or ""),
        tools=[str(t) for t in tools] if isinstance(tools, list) else None,
        scope=scope if isinstance(scope, dict) else None,
        rate_overrides=rate_overrides if isinstance(rate_overrides, dict) else None,
    )
    return web.json_response(
        {
            "ok": True,
            "client_id": client.client_id,
            "label": client.label,
            "surfaces": list(client.surfaces),
            # Shown ONCE. There is no endpoint that can return it again.
            "token": token,
            "token_notice": (
                "Copy this now — it is stored only as a hash and cannot be shown again."
            ),
        }
    )


async def api_external_access_client_toggle(request: web.Request) -> web.Response:
    """POST /api/external-access/clients/{client_id}/disabled — kill-switch layer (c).

    Body ``{disabled: bool}``. Separate from the create/revoke route because
    "switch this integration off for an hour" and "destroy its credential" are
    different decisions, and collapsing them makes the reversible one feel final.
    """
    from personalclaw.inbound import clients as clients_mod

    client_id = str(request.match_info.get("client_id", "") or "")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict) or not isinstance(body.get("disabled"), bool):
        return json_error("invalid_body", message="body must be {disabled: bool}", status=400)
    if not clients_mod.set_disabled(client_id, bool(body["disabled"]), reason="operator action"):
        return json_error("not_found", message=f"unknown client {client_id!r}", status=404)
    return web.json_response({"ok": True, "client_id": client_id, "disabled": body["disabled"]})
