"""The gateway as it runs (``start_dashboard``, every middleware in front of the routes) pairs and
attaches a browser connector from its extension's own origin, and refuses that origin elsewhere.

``test_the_browser_connector_pairs_from_its_own_origin.py`` holds every rule on the routes behind a
copy of the origin check. This drives the gateway's own one, so the dashboard's origin check is the
one that takes the connector's origin (``connector_origin.admits``).
"""

from __future__ import annotations

import aiohttp
import pytest
from signed_in_gateway import signed_in_gateway

from personalclaw.browse import target as bt
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER

EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


@pytest.mark.asyncio
async def test_the_gateway_pairs_and_attaches_a_connector_from_its_own_origin(
    tmp_path, monkeypatch
):
    """🔴 Red before: the gateway's origin check refused the extension's pairing (403)."""
    monkeypatch.setattr(bt, "user_browser_enabled", lambda: True)
    bt.clear_connector()
    try:
        async with signed_in_gateway(tmp_path, monkeypatch) as gw:
            owner = {"Authorization": f"Bearer {await gw.owner_token()}"}
            owner["X-Local-Secret"] = gw.internal_secret
            async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as http:
                started = await http.post(gw.url("/api/devices/pair/start"), json={}, headers=owner)
                assert started.status == 200, await started.text()
                code = (await started.json())["code"]

                done = await http.post(
                    gw.url("/api/devices/pair/complete"),
                    json={"code": code},
                    headers={"Origin": EXT},
                )
                assert done.status == 200, await done.text()
                paired = await done.json()
                own = {
                    "Origin": EXT,
                    "Authorization": f"Bearer {paired['token']}",
                    PAIR_HEADER: paired["pair"],
                }

                attached = await http.post(gw.url("/api/browse/connector"), json={}, headers=own)
                assert attached.status == 200, await attached.text()
                assert bt.connector_status().connected is True

                # Its origin is the connector's alone, and its session reaches nothing else.
                elsewhere = await http.post(
                    gw.url("/api/devices/revoke-others"), json={}, headers=own
                )
                assert elsewhere.status == 403
                listed = await http.get(gw.url("/api/devices"), headers=own)
                assert listed.status == 403
    finally:
        bt.clear_connector()
