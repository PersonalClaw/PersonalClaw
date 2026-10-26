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

import asyncio
import gc
import hashlib
import json
import os
import shutil
import threading
import time
import weakref
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw
from personalclaw import home_gateway
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
    @pytest.mark.parametrize("resolver", ["config_dir", "resolve_config_dir"])
    async def test_home_id_follows_the_home_however_it_is_named(
        self, tmp_path, monkeypatch, resolver
    ):
        """``home_id`` is kept for each home named, not only each ``PERSONALCLAW_HOME``: a home
        resolved another way (a test swaps a resolver, as the CLI's tests do, either of the two
        ``config_dir`` reaches) is read again, so a command never takes its own gateway for
        another home's."""
        mine, theirs = tmp_path / "mine", tmp_path / "theirs"
        mine.mkdir()
        theirs.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(mine))
        assert (await _healthz())["home_id"] == _expected_home_id(mine.resolve())
        swapped = (lambda: theirs) if resolver == "config_dir" else (lambda env=None: theirs)
        monkeypatch.setattr(handlers_system.config_loader, resolver, swapped)
        assert (await _healthz())["home_id"] == _expected_home_id(theirs.resolve())

    @pytest.mark.asyncio
    async def test_root_ok_is_false_when_the_serving_root_is_gone(self, tmp_path, monkeypatch):
        """The zombie: a live process serving code from a checkout that no longer exists.

        A serving root that is there, then removed from under the running gateway, as ``git
        worktree remove`` removes it. ``root_ok`` is live within about a second
        (``handlers_system.ROOT_TTL_S``), so it reads false within 2 s of the removal.
        """
        assert (await _healthz())["root_ok"] is True

        root = tmp_path / "worktree" / "src" / "personalclaw"
        root.mkdir(parents=True)
        monkeypatch.setattr(handlers_system, "_serving_root", lambda: root)
        assert (await _healthz())["root_ok"] is True, "premise: the checkout is there"

        shutil.rmtree(tmp_path / "worktree")
        body, deadline = await _healthz(), time.monotonic() + 2.0
        while body["root_ok"] is not False and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            body = await _healthz()
        assert body["root_ok"] is False, (
            "a gateway serving from a deleted root must be able to say so, within 2 s — this is "
            "the reading that was silently unavailable"
        )
        # Deliberately still 200/ok: this route is a container healthcheck, and a deleted
        # serving root is a provenance fault, not an inability to serve. See api_healthz.
        assert body["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unknowable_serving_root_degrades_and_never_500s(self, monkeypatch):
        monkeypatch.setattr(handlers_system, "_serving_root", lambda: None)
        assert (await _healthz())["root_ok"] is False


class TestAnAnswerWaitsOnNoDisk:
    """After the first, a health check answers from the last reading of the home and the serving
    root (``handlers_system._Identity``), and never waits on the disk or a worker thread. Even in
    a worker thread, the read held answers: 0.12 s on macOS CI, beside an upload, while the event
    loop turned every 18 ms."""

    @pytest.mark.asyncio
    async def test_after_the_first_check_a_check_hands_no_read_to_a_worker(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(handlers_system, "ROOT_TTL_S", 3600.0)  # no reading grows old here
        handed = _count_handed(monkeypatch)
        first = await _healthz()
        assert handed == ["_identity"], f"premise: the first check reads both; {handed}"
        for _ in range(20):
            assert await _healthz() == first
        assert handed == ["_identity"], f"a check after the first read the disk: {handed}"

    @pytest.mark.asyncio
    async def test_a_reading_grown_old_is_read_again_once_for_every_check_meanwhile(
        self, tmp_path, monkeypatch
    ):
        """Every check answers from the last reading at once; a reading older than the TTL
        starts one fresh read, which every check that comes meanwhile shares."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        app = web.Application()
        app.router.add_get("/api/healthz", handlers_system.api_healthz)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/healthz")).status == 200  # the first read
            monkeypatch.setattr(handlers_system, "ROOT_TTL_S", 0.0)  # every reading is old
            handed = _count_handed(monkeypatch)
            held, reads, real = threading.Event(), [], handlers_system._root_ok

            def held_read() -> bool:
                reads.append(threading.get_ident())
                assert held.wait(10), "the read was never let go"
                return real()

            monkeypatch.setattr(handlers_system, "_root_ok", held_read)
            answers = await asyncio.wait_for(
                asyncio.gather(*(client.get("/api/healthz") for _ in range(10))), 5
            )
            assert [a.status for a in answers] == [200] * 10, "a check waited on the read"
            assert handed == ["held_read"], f"one read for the ten checks; {handed}"
            held.set()
            reading = handlers_system._IDENTITY._reading
            assert reading is not None
            await asyncio.wait_for(reading, 5)
        assert len(reads) == 1, reads

    @pytest.mark.asyncio
    async def test_a_fresh_read_in_flight_is_held_until_it_is_done(self, tmp_path, monkeypatch):
        """No check awaits a fresh read, and asyncio holds a task only weakly: the reading holds
        the read through a collection while it runs, and lets it go once it is done."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        app = web.Application()
        app.router.add_get("/api/healthz", handlers_system.api_healthz)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/healthz")).status == 200  # the first read
            monkeypatch.setattr(handlers_system, "ROOT_TTL_S", 0.0)
            held, real = threading.Event(), handlers_system._root_ok
            monkeypatch.setattr(handlers_system, "_root_ok", lambda: held.wait(10) and real())
            assert (await client.get("/api/healthz")).status == 200  # starts the fresh read
            in_flight = weakref.ref(handlers_system._IDENTITY._reading)
            gc.collect()
            assert in_flight() is not None and not in_flight().done(), "the read was let go"
            held.set()
            await asyncio.wait_for(in_flight(), 5)
            await asyncio.sleep(0)  # the read's done callback
            assert handlers_system._IDENTITY._reading is None, "a read done is still held"

    @pytest.mark.asyncio
    async def test_a_fresh_read_that_fails_is_logged_and_never_left_unretrieved(
        self, tmp_path, monkeypatch, caplog
    ):
        """The fresh read is held until the test has it, as the tests above hold theirs: let go at
        once, it failed and was let go while the client still read the answer, and the reading
        held nothing by the time the test looked (breadth's random order, seed 92)."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        loop, unretrieved = asyncio.get_running_loop(), []
        loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
        app = web.Application()
        app.router.add_get("/api/healthz", handlers_system.api_healthz)
        try:
            async with TestClient(TestServer(app)) as client:
                first = await (await client.get("/api/healthz")).json()  # the first read
                monkeypatch.setattr(handlers_system, "ROOT_TTL_S", 0.0)
                held = threading.Event()

                def broken() -> bool:
                    assert held.wait(10), "the read was never let go"
                    raise RuntimeError("the disk went away")

                monkeypatch.setattr(handlers_system, "_root_ok", broken)
                answer = await (await client.get("/api/healthz")).json()
                assert answer["root_ok"] == first["root_ok"], "the check answers from the last"
                reading = handlers_system._IDENTITY._reading
                assert reading is not None and not reading.done(), "premise: the read is held"
                held.set()
                await asyncio.wait({reading}, timeout=5)
                # Done, its error left to the reading's own callback: taken here, it would be
                # retrieved by the test, and the check below would pass on that.
                assert reading.done(), reading
                await asyncio.sleep(0)  # the read's done callback
                assert handlers_system._IDENTITY._reading is None, "a read done is still held"
                del reading
                gc.collect()
        finally:
            loop.set_exception_handler(None)
        assert not unretrieved, unretrieved
        assert "the health check's read failed" in caplog.text


def _count_handed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The name of each function a health check hands to a worker thread, from now."""
    handed: list[str] = []
    real = asyncio.to_thread

    async def counted(func, /, *args, **kwargs):
        handed.append(func.__name__)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(handlers_system.asyncio, "to_thread", counted)
    return handed


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
        first = home_gateway.home_id(p)
        assert first == home_gateway.home_id(p)
        assert first == _expected_home_id(p)
        assert str(p) not in first and len(first) == 16
