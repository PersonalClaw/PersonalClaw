"""``/api/healthz`` must say WHOSE gateway answered, not just that something did.

A gateway whose pinned worktree had been deleted from under it kept answering this route
``200 {"status": "ok", "version": ...}``. Two separate probes read that as their own gateway
being healthy, drew a conclusion about work that was in fact running somewhere else, and
killed the same two units of work twice. The payload carried nothing a caller could compare
against, so "a gateway answered" and "MY gateway answered" were indistinguishable.

Three fields close it, and each of these tests fails against the pre-fix two-key payload:

* ``pid`` — a caller that spawned the gateway asserts this is the child it holds.
* ``home_id`` — a fingerprint of the resolved ``PERSONALCLAW_HOME``, so a caller that knows
  only its OWN home can compare without the absolute path ever crossing an auth-exempt,
  possibly ``0.0.0.0``-bound route. Two gateways on two homes must not collide.
* ``root_ok`` — whether the directory this code is served FROM still exists. ``False`` is the
  zombie the incident was made of.

The auth-exemption is the reason ``home_id`` is a fingerprint rather than a path; the last
test pins that no absolute path leaks into this payload, so a future field cannot quietly
re-introduce the disclosure.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw
from personalclaw.dashboard import handlers_system


def _expected_home_id(home: Path) -> str:
    """The fingerprint recipe a caller reproduces, written out independently of the
    implementation so a change to either side is a visible failure, not a silent agreement."""
    return hashlib.sha256(str(home).encode("utf-8")).hexdigest()[:16]


async def _healthz(monkeypatch: pytest.MonkeyPatch | None = None) -> dict:
    app = web.Application()
    app.router.add_get("/api/healthz", handlers_system.api_healthz)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/healthz")
        assert resp.status == 200
        return await resp.json()


class TestIdentityIsAnswerable:
    @pytest.mark.asyncio
    async def test_payload_names_the_answering_process(self):
        body = await _healthz()
        assert body["pid"] == os.getpid(), (
            "healthz must name the process that answered — without it, a caller holding a "
            "spawned gateway's pid has no way to tell it apart from a stranger on the port"
        )
        assert body["status"] == "ok" and body["version"] == personalclaw.__version__

    @pytest.mark.asyncio
    async def test_home_id_matches_the_resolved_home_and_distinguishes_two_homes(
        self, tmp_path, monkeypatch
    ):
        mine, theirs = tmp_path / "mine", tmp_path / "theirs"
        mine.mkdir()
        theirs.mkdir()

        monkeypatch.setenv("PERSONALCLAW_HOME", str(mine))
        got_mine = (await _healthz())["home_id"]
        monkeypatch.setenv("PERSONALCLAW_HOME", str(theirs))
        got_theirs = (await _healthz())["home_id"]

        assert got_mine == _expected_home_id(
            mine.resolve()
        ), "a caller must be able to recompute this from the home it expects"
        assert got_mine != got_theirs, (
            "two gateways on two homes must be distinguishable — this is the whole assertion "
            "a probe needs to make before it believes a reading"
        )

    @pytest.mark.asyncio
    async def test_root_ok_is_false_when_the_serving_root_is_gone(self, monkeypatch):
        """The zombie: a live process serving code from a checkout that no longer exists.

        Simulated by pointing the package's resolved location at a path that is not there —
        which is exactly the state ``git worktree remove`` leaves a running gateway in.
        """
        assert (await _healthz())["root_ok"] is True

        monkeypatch.setattr(
            handlers_system,
            "_serving_root",
            lambda: Path("/nonexistent/removed-worktree/src/personalclaw"),
        )
        body = await _healthz()
        assert body["root_ok"] is False, (
            "a gateway serving from a deleted root must be able to say so — this is the "
            "reading that was silently unavailable"
        )
        # Deliberately still 200/ok: this route is a container healthcheck, and a deleted
        # serving root is a provenance fault, not an inability to serve. See api_healthz.
        assert body["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unknowable_serving_root_degrades_and_never_500s(self, monkeypatch):
        monkeypatch.setattr(handlers_system, "_serving_root", lambda: None)
        assert (await _healthz())["root_ok"] is False


class TestAuthExemptRouteLeaksNoPaths:
    @pytest.mark.asyncio
    async def test_no_absolute_path_appears_in_the_payload(self, tmp_path, monkeypatch):
        """``/api/healthz`` is in ``token_auth._BYPASS_EXACT`` and the gateway can bind
        ``0.0.0.0``, so this payload is readable by any unauthenticated LAN client. Identity
        has to be comparable without being disclosing."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "secret-home"))
        raw = json.dumps(await _healthz())
        assert str(tmp_path) not in raw and "secret-home" not in raw, raw
        assert str(Path.home()) not in raw, raw

    def test_fingerprint_is_stable_and_not_the_path(self):
        p = Path("/some/where/.personalclaw")
        first = handlers_system.home_fingerprint(p)
        assert first == handlers_system.home_fingerprint(p)
        assert first == _expected_home_id(p)
        assert str(p) not in first and len(first) == 16
