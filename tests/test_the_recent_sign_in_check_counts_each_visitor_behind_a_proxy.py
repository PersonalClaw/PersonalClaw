"""Behind a trusted proxy, the recent-sign-in check counts each visitor's wrong passwords alone.

The recent-sign-in check (``dashboard/owner_presence.py``) shares the sign-in page's lockout, and
counts a wrong password under the client address sessions read: behind a proxy listed in
``dashboard.trusted_proxies``, its ``X-Real-IP``. A proxy on the same machine connects from
127.0.0.1 for every visitor, so a check that counted the connection would let one visitor's wrong
passwords lock every visitor out of it, the owner with the right password included.
``test_auth_login.py`` holds the same for the sign-in page; this holds it for the check.

Every request goes through the production token middleware (``signed_in_sessions``).
"""

from __future__ import annotations

import json

import pytest
from signed_in_sessions import OLD, client_for, guarded_app, sign_in

from personalclaw.auth import credentials as creds
from personalclaw.config import loader
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h

pytestmark = pytest.mark.security

PASSWORD = "correct-horse-battery-staple"
#: Two visitors behind the proxy, as its X-Real-IP names them.
GUESSER, OWNER = "203.0.113.7", "198.51.100.9"


@pytest.fixture(autouse=True)
def _clean_state():
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


def _behind_a_proxy(trusted: list[str]) -> None:
    """Password sign-in on, a lockout after three wrong passwords, and the dashboard exposed
    behind a proxy on this computer, trusted or not."""
    creds.set_password("jordan", PASSWORD)
    (loader.config_dir() / "config.json").write_text(
        json.dumps(
            {
                "auth": {"login_enabled": True, "lockout_threshold": 3},
                "dashboard": {"public_url": "https://claw.example.com", "trusted_proxies": trusted},
            }
        ),
        encoding="utf-8",
    )


def _routes(app) -> None:
    app.router.add_post("/api/auth/confirm", auth_h.api_auth_confirm)


async def _confirm(client, visitor: str, password: str):
    session = sign_in(age=OLD)
    return await client.post(
        "/api/auth/confirm",
        json={"password": password},
        headers={**session.headers, "X-Real-IP": visitor},
    )


@pytest.mark.asyncio
async def test_one_visitors_wrong_passwords_do_not_lock_another_out_of_the_check() -> None:
    """🔴 Red with the check counting the connection's address: the owner's right password is
    refused 429 after the guesser's three wrong ones."""
    _behind_a_proxy(["127.0.0.1"])
    async with client_for(guarded_app(_routes)) as client:
        for _ in range(3):
            assert (await _confirm(client, GUESSER, "not-the-password")).status == 401
        assert (await _confirm(client, GUESSER, PASSWORD)).status == 429
        assert (await _confirm(client, OWNER, PASSWORD)).status == 200


@pytest.mark.asyncio
async def test_with_no_trusted_proxy_a_visitors_x_real_ip_changes_nothing() -> None:
    """A caller cannot choose the address it is counted under: from a peer that is not a trusted
    proxy the header is ignored, so every caller through it shares one count."""
    _behind_a_proxy([])
    async with client_for(guarded_app(_routes)) as client:
        for _ in range(3):
            assert (await _confirm(client, GUESSER, "not-the-password")).status == 401
        assert (await _confirm(client, OWNER, PASSWORD)).status == 429
