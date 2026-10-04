"""Sessions of a chosen age and door, and an app behind the real sign-in check, for the tests of
what a sign-in may do (``test_minting_a_credential_needs_a_recent_sign_in.py``,
``test_changing_the_password_asks_for_the_current_one.py``, ``test_owner_presence_census.py``).

A session's age is the time its device signed in, as the session store records it — what the
gateway reads, and what Settings → Devices shows — so a test makes an old sign-in by moving that
time back, never by waiting. Requests go through the production token middleware, so a test
cannot pass by handing a handler a request no real client could send.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard import session_store, token_auth

PORT = 10000
ORIGIN = f"http://localhost:{PORT}"
COOKIE = f"pc_token_{PORT}"
#: The local machine secret of the test gateway (what ``.local_secret`` holds on a real one).
LOCAL_SECRET = "a" * 32
#: Three days: a sign-in left on a phone over a weekend.
OLD = 3 * 86400
#: An address on the home network, for a request that does not come from this computer.
LAN_ADDRESS = "192.168.1.40"


def _from(address: str) -> Callable[..., Any]:
    """A middleware that makes every request arrive from *address*, as from another device."""

    @web.middleware
    async def middleware(request: web.Request, handler: Any) -> web.StreamResponse:
        return await handler(request.clone(remote=address))

    return middleware


def guarded_app(*registers: Callable[[web.Application], None], remote: str = "") -> web.Application:
    """An app with the production sign-in middleware in front of the routes *registers* add, and
    behind it the one that answers a question as a question to a page that asks one — in the
    order the gateway runs them.

    *remote* makes every request arrive from that address instead of this computer's loopback.
    """
    from personalclaw.auth.modes import AuthConfig, AuthMode
    from personalclaw.dashboard.consent_ask import consent_ask_middleware
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    middlewares = [
        token_auth.token_auth_middleware(port=PORT),
        consent_ask_middleware,
        request_boundary_middleware(),
    ]
    if remote:
        middlewares.insert(0, _from(remote))
    app = web.Application(middlewares=middlewares)
    app["port"] = PORT
    app["allowed_origins"] = {ORIGIN}
    app["local_secret"] = LOCAL_SECRET
    app["auth_cfg"] = AuthConfig(mode=AuthMode.LOCAL_TOKEN)
    for register in registers:
        register(app)
    return app


def without_sign_in(app: web.Application) -> web.Application:
    """*app*, declared as what it is: a gateway that serves without authentication.

    For a test of a route's own behaviour on an app with no sign-in middleware in front of it.
    Such an app checks no sign-in, so a write that needs a recent one (``owner_presence``) has
    none to ask for — as on a gateway started with ``PERSONALCLAW_AUTH_MODE=none``. Without the
    declaration that write is refused, because a gateway that does not say its authentication is
    off is never taken to have it off. What a sign-in may do is
    ``test_minting_a_credential_needs_a_recent_sign_in``'s subject.
    """
    from personalclaw.auth.modes import AuthConfig, AuthMode

    app["auth_cfg"] = AuthConfig(mode=AuthMode.NONE)
    return app


def client_for(app: web.Application) -> TestClient:
    """A client that keeps no cookies of its own, so each request carries exactly the session
    the test hands it — a cookie a response set never rides along unasked."""
    return TestClient(TestServer(app), cookie_jar=DummyCookieJar())


@dataclass(frozen=True)
class SignIn:
    """One minted session, and what a browser holding it sends."""

    token: str
    nonce: str
    session_id: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Cookie": f"{COOKIE}={self.token}", "Origin": ORIGIN}


def sign_in(
    *,
    issuer: str = token_auth.ISSUER_LOGIN,
    age: float = 0.0,
    kind: str = "browser",
    name: str = "Firefox on Linux",
    user: str = "jordan",
) -> SignIn:
    """A session through *issuer*'s door whose device signed in *age* seconds ago."""
    device = session_store.DeviceInfo(id=session_store.new_device_id(), name=name, kind=kind)
    minted = token_auth.mint_session(
        user, token_auth.browser_session_ttl(), issuer=issuer, device=device
    )
    if age:
        records = session_store.load_session_records()
        records[minted.nonce].device.minted_at -= age
        session_store.save_session_records(records)
    return SignIn(minted.token, minted.nonce, minted.session_id)


def owner_presence_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The security log rows the presence check wrote, from a captured ``log_api_access`` list."""
    return [e for e in events if e.get("operation") == "owner_presence"]
