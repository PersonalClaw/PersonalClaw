"""A real gateway that asks for a sign-in, in a home of its own.

For a test that drives the gateway the way an outside program and its owner meet it: every
middleware the gateway runs, the sign-in included, in front of the route under test. A route that
works only when the sign-in is switched off is the failure this exists to catch, so every shortcut
that switches it off is taken out of the environment first.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import fire_dispatch
import pytest

#: Every shortcut a test process might inherit; each one admits a request before the gateway's own
#: sign-in is asked.
AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_SESSION_KEY",
)


@dataclass
class Gateway:
    home: Path
    port: int
    state: Any

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    @property
    def internal_secret(self) -> str:
        return (self.home / ".local_secret").read_text(encoding="utf-8").strip()

    async def owner_token(self) -> str:
        """What `personalclaw token` does: trade `.local_secret` for a session token."""
        async with aiohttp.ClientSession() as http:
            resp = await http.get(
                self.url("/api/token/local"), headers={"X-Local-Secret": self.internal_secret}
            )
            assert resp.status == 200, await resp.text()
            return (await resp.json())["token"]

    async def as_owner(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        """One request as the signed-in owner: the status and the JSON answer."""
        headers = {"Authorization": f"Bearer {await self.owner_token()}"}
        headers.update(kwargs.pop("headers", {}) or {})
        async with aiohttp.ClientSession() as http:
            resp = await http.request(method, self.url(path), headers=headers, **kwargs)
            return resp.status, await resp.json(content_type=None)


@contextlib.asynccontextmanager
async def signed_in_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Gateway]:
    """Start the gateway (``start_dashboard``) on a free loopback port, asking for a sign-in.

    Its home is a folder of the test's, ``HOME`` is another, and the Triggers routes read the
    gateway's own store (``conftest`` points them at one of their own).
    """
    home = tmp_path / "home"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: home)

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)

    runner, state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    # What the gateway hands its dashboard at boot: the dispatch every automation's fire runs
    # through, a webhook's and a view's included, in the same home.
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: home)
    fire_dispatch.attach(state)
    port = runner.addresses[0][1]
    # What the gateway exports to every child it starts (`gateway_base.publish`).
    monkeypatch.setenv("PERSONALCLAW_PORT", str(port))
    try:
        yield Gateway(home=home, port=port, state=state)
    finally:
        await runner.cleanup()
