"""A browser connector's own origin: taken back only beside its own device's session.

The browser connector is a browser extension, and its requests carry the extension's own origin,
not a web page's, so the origin check (``origin.check_origin``) refused every call it makes. Nothing
here makes that check weaker. Instead:

* **Pairing records it.** ``pair/complete`` takes a request from an extension's origin
  (:func:`~personalclaw.dashboard.origin.extension_origin`) only with a valid single-use code, under
  the same attempt limit as every pairing. The device it pairs is a connector
  (``session_store.CONNECTOR_KIND``), whose row keeps that exact origin, and whose session holds the
  connector scope (``automation_scopes.CONNECTOR_SCOPE``), so the sign-in check admits it on the
  connector's own routes and nowhere else (``device_scopes``), whatever origin a request carries.
* **The origin check takes it back on those routes only** (:func:`admits`): the request carries
  that same origin, compared whole, and a session of the paired device that recorded it.
* **Revoking the device forgets it,** since the origin lives on the row the revoke deletes.

The origin is never parsed for a vendor, only compared whole, so core names no browser.
"""

from __future__ import annotations

import hmac
import logging
import re

from aiohttp import web

logger = logging.getLogger(__name__)

#: The connector's own routes, as their paths are written: the only ones its origin is taken on.
_PATHS = re.compile(r"/api/browse/connector(?:/tabs(?:/[^/]+)?)?")
#: Where a connector pairs. Its handler takes an extension origin only with a valid code, under the
#: pairing attempt limit, so the origin check hands it on rather than deciding for it.
PAIR_COMPLETE_PATH = "/api/devices/pair/complete"


def is_connector_path(path: str) -> bool:
    """Whether *path* is one of the connector's own routes (``handlers.browse_connector``)."""
    return bool(_PATHS.fullmatch(path or ""))


def admits(request: web.Request, port: int) -> bool:
    """Whether the origin check may take this refused request, which carries an extension origin:
    its pairing (``pair/complete``, whose handler takes it only with a valid code), or a connector
    route with a session of the paired connector whose recorded origin is that one, exactly.

    Only decides the origin. The sign-in check still judges the session in full (its pair, its
    scope, its expiry) after this, as it does for any request.
    """
    from personalclaw.dashboard.origin import extension_origin

    presented = extension_origin(request.headers.get("Origin", ""))
    if not presented:
        return False
    if request.method == "POST" and request.path == PAIR_COMPLETE_PATH:
        return True
    if not is_connector_path(request.path):
        return False
    device = _connector_device(request, port)
    if device is None or not device.origin:
        return False
    return hmac.compare_digest(device.origin.encode(), presented.encode())


def _connector_device(request: web.Request, port: int):
    """The paired connector device behind the credential the sign-in check reads for *request*,
    or ``None``.

    The same choice, by the same function, so the two checks never judge different credentials:
    in the owner's browser a connector's fetch can carry her cookie beside its own bearer, and the
    sign-in check takes the bearer.
    """
    from personalclaw.dashboard import session_store, token_auth

    try:
        chosen = token_auth._select_request_credentials(request, port)
    except Exception:  # noqa: BLE001 — a credential this can't read admits nothing
        logger.debug("connector origin: the session could not be read", exc_info=True)
        return None
    if not chosen.valid or chosen.app:
        return None
    record = session_store.paired_sessions().get(token_auth.token_nonce(chosen.token))
    if record is None or record.device.kind != session_store.CONNECTOR_KIND:
        return None
    return record.device
