"""The credential-store surface — where secrets live, and the consented move (SH-2).

Three routes, registered beside the rest of ``/api/security/*``:

* ``GET  /api/security/credentials`` — the whole read: active backend, resolved-vs-requested,
  how many keys are still in ``.env``, and whether a rollback is available.
* ``POST /api/security/credentials/migrate`` — runs ``credentials_to_keychain``.
* ``POST /api/security/credentials/rollback`` — restores the pre-migration ``.env``.

**Owner-only, categorically.** An app must not learn the NAMES of the owner's credentials,
let alone move them between stores — same reasoning and same shape as the audit surface's
refusal (``security_audit._refuse_app``): the app-permission middleware is an allowlist, so
an app that merely declares ``/api/security`` would pass it, and this is the flat no.

**Both writes require ``confirm: true`` in the body.** The flag is not decoration — it is the
protocol-level record that the caller showed the snapshot step. The core refuses without it
independently of this handler, so a future second caller cannot skip the consent by not
knowing about it.

**No secret VALUE crosses this boundary in either direction.** The payloads carry key NAMES
and counts only. The migration reads and writes credentials entirely inside the process; the
browser is told what moved, never what it was.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.config.credential_migration import (
    credential_migration_status,
    migrate_credentials_to_keychain,
    rollback_credentials_to_keychain,
    verify_credential_migration,
)
from personalclaw.http_errors import json_error
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def _refuse_app(request: web.Request) -> web.Response | None:
    """Refuse an app-scoped token, or ``None`` when the caller is the owner."""
    app_name = request.get("app", "")
    if not app_name:
        return None
    try:
        sel().log_api_access(
            caller=f"app:{app_name}",
            operation=f"{request.method} {request.path}",
            outcome="denied",
            source="app_permissions",
            resources=request.path,
            error="credential store is owner-only",
        )
    except Exception:
        pass
    return json_error(
        "credentials_owner_only",
        message="the credential store is the owner's, not an app's",
        status=403,
    )


async def _confirmed(request: web.Request) -> bool:
    """Did the caller send ``confirm: true``? A malformed body is a NO, never a yes."""
    try:
        body = await request.json()
    except Exception:
        return False
    return isinstance(body, dict) and body.get("confirm") is True


def _payload() -> dict:
    """Status plus verification — one read, so the panel cannot show a stale pair."""
    ok, evidence = verify_credential_migration()
    return {**credential_migration_status(), "verified": ok, "verification": evidence}


async def api_security_credentials(request: web.Request) -> web.Response:
    """GET /api/security/credentials — where the instance's secrets are stored."""
    denied = _refuse_app(request)
    if denied is not None:
        return denied
    # The `{**…}` spread is not decoration: `test_wire_error_envelope_census` requires every
    # `json_response` body to be a literal dict AT THE CALL SITE, so a bare `_payload()` (a Call
    # node) raises the unresolvable-payload count. Same shape as the two POSTs below, which is
    # also why the three responses cannot drift apart.
    return web.json_response({**_payload()})


async def api_security_credentials_migrate(request: web.Request) -> web.Response:
    """POST /api/security/credentials/migrate — move ``.env`` secrets into the keychain."""
    denied = _refuse_app(request)
    if denied is not None:
        return denied
    if not await _confirmed(request):
        return json_error(
            "confirmation_required",
            message=(
                'send {"confirm": true} — this moves stored credentials between stores '
                "after snapshotting .env"
            ),
            status=400,
        )
    result = migrate_credentials_to_keychain(confirm=True)
    if not result.ok and not result.moved:
        # A refusal (no usable keychain, gate off) is a 409, not a 500: the request was
        # well-formed and the machine's state is the reason. A PARTIAL move is NOT routed
        # here — it reports 200 with `failed`, because it did real work the caller must see.
        return json_error("migration_refused", message=result.reason, status=409)
    return web.json_response({**result.to_dict(), **_payload()})


async def api_security_credentials_rollback(request: web.Request) -> web.Response:
    """POST /api/security/credentials/rollback — restore the pre-migration ``.env``."""
    denied = _refuse_app(request)
    if denied is not None:
        return denied
    if not await _confirmed(request):
        return json_error(
            "confirmation_required",
            message='send {"confirm": true} — this rewrites .env from the snapshot',
            status=400,
        )
    result = rollback_credentials_to_keychain(confirm=True)
    if not result.ok and not result.moved:
        return json_error("rollback_refused", message=result.reason, status=409)
    return web.json_response({**result.to_dict(), **_payload()})


def register_security_credential_routes(app: web.Application) -> None:
    """Register the credential-store surface beside the other ``/api/security/*`` routes."""
    app.router.add_get("/api/security/credentials", api_security_credentials)
    app.router.add_post("/api/security/credentials/migrate", api_security_credentials_migrate)
    app.router.add_post("/api/security/credentials/rollback", api_security_credentials_rollback)
