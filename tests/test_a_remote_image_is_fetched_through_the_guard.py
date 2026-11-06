"""A remote image a shown text names is fetched by the gateway, through the guard, on a press.

The dashboard's page loads no image from another host. Its placeholder's Load button posts the
address to ``POST /api/images/remote`` (``dashboard/handlers/remote_image.py``), and the gateway
fetches it under the ``REMOTE_IMAGE`` profile: public hosts only, the owner's Denied hosts
applied, the request audited under the route's own door with every refusal, and only a raster
image, told by its first bytes, under a size cap. These drive the real gateway
(``start_dashboard``) with its sign-in on. The far host is a stand-in for aiohttp's client
session; the guard runs for real, with a stand-in for DNS.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest
from shared_app_rows import recording_sel
from signed_in_gateway import Gateway, signed_in_gateway

from personalclaw.dashboard.handlers import remote_image
from personalclaw.net import guard
from personalclaw.net.policy import REMOTE_IMAGE

ROUTE = "/api/images/remote"
IMAGE = "https://images.example.com/chart.png?q=1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PUBLIC = {"images.example.com": ["93.184.216.34"], "blocked.example": ["93.184.216.35"]}
#: A name that looks public and resolves to a private address.
INSIDE = {"nas.example": ["10.0.0.7"]}


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard's DNS, for the far hosts only."""

    def resolve(host: str) -> list[str]:
        if host in PUBLIC:
            return PUBLIC[host]
        if host in INSIDE:
            return INSIDE[host]
        if host in ("127.0.0.1", "10.0.0.7"):
            return [host]
        import socket

        raise socket.gaierror(host)

    monkeypatch.setattr(guard, "_resolve", resolve)


class _Far:
    """The far host's one answer, as aiohttp's client hands it to ``net.client.fetch_as``."""

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes,
        *,
        delay: float = 0.0,
        gate: threading.Event | None = None,
        hold: threading.Event | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.status, self.headers, self._body, self.content = status, headers, body, self
        self.delay = delay
        #: When given, the answer starts only once the test sets it. Each load runs on a thread
        #: and a loop of its own, so it is a thread's event.
        self.gate = gate
        #: When given, the answer blocks the worker's own thread until the test sets it, so
        #: nothing on that thread's loop can cut it: the worker stays taken.
        self.hold = hold
        #: When given, raised as the answer starts: the far site's connection failing.
        self.raises = raises
        #: Set when the gateway gave up on this answer while it was still coming.
        self.cut = False

    async def __aenter__(self) -> _Far:
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            while self.gate is not None and not self.gate.is_set():
                await asyncio.sleep(0.01)
            if self.hold is not None:
                self.hold.wait(timeout=10)
            if self.raises is not None:
                raise self.raises
        except asyncio.CancelledError:
            self.cut = True
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def iter_chunked(self, size: int):  # type: ignore[no-untyped-def]
        for at in range(0, len(self._body), size):
            yield self._body[at : at + size]


@contextlib.contextmanager
def _far_host(answer: _Far | list[_Far] | None, asked: list[str]) -> Iterator[None]:
    """aiohttp's client session, for the gateway's own fetch only: the test's session is made
    before this. Records each address asked; *answer* None fails the test if one is, and a list
    answers one request each, in order."""
    queue = list(answer) if isinstance(answer, list) else None

    class Session:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> _Far:
            asked.append(url)
            assert answer is not None, f"the gateway reached {url}"
            if queue is not None:
                return queue.pop(0)
            assert isinstance(answer, _Far)
            return answer

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(aiohttp, "ClientSession", Session)
        mp.setattr(aiohttp, "TCPConnector", lambda **kwargs: None)
        yield


async def _load(
    gw: Gateway,
    url: str,
    answer: _Far | list[_Far] | None,
    *,
    token: str = "",
    headers: dict | None = None,
) -> tuple[aiohttp.ClientResponse, bytes, list[str], MagicMock]:
    owner = token or await gw.owner_token()
    asked: list[str] = []
    recorder = MagicMock()
    async with aiohttp.ClientSession() as http:
        sent = {"Authorization": f"Bearer {owner}", **(headers or {})}
        with recording_sel(recorder), _far_host(answer, asked):
            resp = await http.post(gw.url(ROUTE), json={"url": url}, headers=sent)
            body = await resp.read()
    return resp, body, asked, recorder


async def _post(gw: Gateway, body: object, asked: list[str]) -> tuple[int, dict[str, Any]]:
    """*body* posted as the owner, as JSON, with a far host that fails the test if asked."""
    owner = await gw.owner_token()
    async with aiohttp.ClientSession() as http:
        with _far_host(None, asked):
            resp = await http.post(
                gw.url(ROUTE), json=body, headers={"Authorization": f"Bearer {owner}"}
            )
            return resp.status, json.loads(await resp.read())["error"]


def _egress_rows(recorder: MagicMock) -> list[dict[str, Any]]:
    return [
        c.kwargs
        for c in recorder.log_api_access.call_args_list
        if c.kwargs.get("operation") == "egress_fetch"
    ]


def _code(body: bytes) -> str:
    return str(json.loads(body)["error"]["code"])


@pytest.mark.asyncio
async def test_an_image_comes_back_with_its_type_unkept_and_audited_under_the_route(
    tmp_path, monkeypatch
) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, recorder = await _load(
            gw, IMAGE, _Far(200, {"Content-Type": "image/png"}, PNG)
        )
        assert resp.status == 200, body
        assert body == PNG and resp.headers["Content-Type"] == "image/png"
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert asked == [IMAGE]
        rows = _egress_rows(recorder)
        assert [r["caller"] for r in rows] == [f"{remote_image.DOOR}:{REMOTE_IMAGE.name}"], rows
        assert rows[0]["outcome"] == "allowed"


@pytest.mark.asyncio
async def test_a_denied_host_is_refused_and_logged(tmp_path, monkeypatch) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        (gw.home / "config.json").write_text(
            json.dumps({"security": {"egress": {"deny_hosts": ["blocked.example"]}}}),
            encoding="utf-8",
        )
        resp, body, asked, recorder = await _load(gw, "https://blocked.example/a.png", None)
        assert resp.status == 403, body
        assert _code(body) == "remote_image_refused"
        assert "Denied hosts" in json.loads(body)["error"]["message"]
        assert asked == []
        assert [r["outcome"] for r in _egress_rows(recorder)] == ["denied"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://127.0.0.1/a.png", "https://10.0.0.7/a.png"])
async def test_a_loopback_or_private_address_is_refused(tmp_path, monkeypatch, url: str) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, _ = await _load(gw, url, None)
        assert resp.status == 403, body
        assert _code(body) == "remote_image_refused"
        assert asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "content"),
    [
        ("text/html", b"<!doctype html><img src=x>"),
        ("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'><image href='x'/></svg>"),
        ("image/png", b"<!doctype html>not the png it says it is"),
    ],
    ids=["html", "svg", "html called png"],
)
async def test_anything_but_a_raster_image_is_refused(
    tmp_path, monkeypatch, kind: str, content: bytes
) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, _ = await _load(gw, IMAGE, _Far(200, {"Content-Type": kind}, content))
        assert resp.status == 415, body
        assert _code(body) == "remote_image_not_an_image"
        assert asked == [IMAGE]


@pytest.mark.asyncio
async def test_an_image_over_the_cap_is_refused(tmp_path, monkeypatch) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        big = PNG + b"\x00" * REMOTE_IMAGE.max_bytes
        resp, body, _, _ = await _load(gw, IMAGE, _Far(200, {"Content-Type": "image/png"}, big))
        assert resp.status == 413, body
        assert _code(body) == "remote_image_too_large"


@pytest.mark.asyncio
async def test_only_a_post_with_a_session_is_answered(tmp_path, monkeypatch) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        owner = await gw.owner_token()
        asked: list[str] = []
        async with aiohttp.ClientSession() as http:
            with _far_host(None, asked):
                got = await http.get(
                    gw.url(ROUTE),
                    params={"url": IMAGE},
                    headers={"Authorization": f"Bearer {owner}"},
                )
                assert got.status == 405
                # No session: the gateway's own refusal of every API request without one.
                nobody = await http.post(gw.url(ROUTE), json={"url": IMAGE})
                assert nobody.status == 403 and nobody.headers.get("X-Auth-Required") == "true"
                assert (await nobody.json())["error"]["code"] == "session_required"
        assert asked == []


@pytest.mark.asyncio
async def test_an_apps_token_is_refused(tmp_path, monkeypatch) -> None:
    from personalclaw.dashboard import token_auth
    from personalclaw.dashboard.session_store import ISSUER_APP

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        await gw.owner_token()  # the gateway's own secret is in place
        app = token_auth.mint_session("owner", 600, issuer=ISSUER_APP, app="weather").token
        resp, body, asked, _ = await _load(gw, IMAGE, None, token=app)
        assert resp.status == 403, body
        assert asked == []


@pytest.mark.asyncio
async def test_the_route_itself_refuses_an_app_the_sign_in_admitted() -> None:
    """Whatever an app's permissions come to allow, the route answers only the owner's own press:
    a request the sign-in admitted for an app is refused here, before anything is fetched."""
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request("POST", ROUTE, headers={"Content-Type": "application/json"})
    request["app"] = "weather"
    asked: list[str] = []
    with _far_host(None, asked):
        resp = await remote_image.api_remote_image(request)
    assert resp.status == 403
    assert json.loads(resp.body)["error"]["code"] == "remote_image_owner_only"
    assert asked == []


@pytest.mark.asyncio
async def test_a_post_from_another_site_is_refused(tmp_path, monkeypatch) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, _ = await _load(
            gw, IMAGE, None, headers={"Origin": "http://evil.example"}
        )
        assert resp.status == 403, body
        assert _code(body) == "auth_origin_not_allowed"
        assert asked == []


@pytest.mark.asyncio
async def test_what_the_owner_allows_for_its_own_connections_is_not_taken(
    tmp_path, monkeypatch
) -> None:
    """Allowed hosts and private networks are the owner's for the gateway's own connections. An
    address from text someone else wrote reaches a public host or nothing."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        egress = {"allow_private": True, "allow_hosts": ["10.0.0.7", "127.0.0.1"]}
        (gw.home / "config.json").write_text(
            json.dumps({"security": {"egress": egress}}), encoding="utf-8"
        )
        for url in ("https://10.0.0.7/a.png", "https://127.0.0.1/a.png"):
            resp, body, asked, _ = await _load(gw, url, None)
            assert resp.status == 403, (url, body)
            assert _code(body) == "remote_image_refused"
            assert asked == [], url


@pytest.mark.asyncio
async def test_a_public_name_for_a_private_address_is_refused_and_logged(
    tmp_path, monkeypatch
) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, recorder = await _load(gw, "https://nas.example/a.png", None)
        assert resp.status == 403, body
        assert "a private network" in json.loads(body)["error"]["message"]
        assert asked == []
        assert [r["outcome"] for r in _egress_rows(recorder)] == ["denied"]


@pytest.mark.asyncio
async def test_a_redirect_to_this_computer_is_refused(tmp_path, monkeypatch) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        moved = _Far(302, {"Location": "https://127.0.0.1/a.png"}, b"")
        resp, body, asked, _ = await _load(gw, IMAGE, [moved])
        assert resp.status == 403, body
        assert _code(body) == "remote_image_refused"
        assert asked == [IMAGE]


@pytest.mark.asyncio
async def test_one_deadline_holds_the_whole_load(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(remote_image, "DEADLINE_S", 0.3)
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        slow = _Far(200, {"Content-Type": "image/png"}, PNG, delay=3.0)
        resp, body, _, _ = await _load(gw, IMAGE, slow)
        assert resp.status == 502, body
        assert _code(body) == "remote_image_unreachable"
        assert "took too long" in json.loads(body)["error"]["message"]


def test_the_profile_keeps_the_strict_posture() -> None:
    assert not REMOTE_IMAGE.allow_private and not REMOTE_IMAGE.allow_only
    assert REMOTE_IMAGE.pin_resolved_ip and REMOTE_IMAGE.allow_schemes == ("https",)
    assert REMOTE_IMAGE.max_bytes == 8_000_000 and REMOTE_IMAGE.timeout_s == 15.0


@pytest.mark.asyncio
async def test_the_deadline_ends_the_work_not_only_the_answer(tmp_path, monkeypatch) -> None:
    """Past the deadline the gateway stops fetching: the answer still coming is cut, and no
    redirect it would have led to is asked."""
    monkeypatch.setattr(remote_image, "DEADLINE_S", 0.3)
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        # The far site's redirect would arrive at 0.6 s: past the deadline, and inside the watch
        # below, so a fetch that ran on would ask the hop while the stand-in still answers.
        slow = _Far(302, {"Location": "https://images.example.com/next.png"}, b"", delay=0.6)
        owner = await gw.owner_token()
        asked: list[str] = []
        async with aiohttp.ClientSession() as http:
            with _far_host([slow, _Far(200, {"Content-Type": "image/png"}, PNG)], asked):
                posted = time.monotonic()
                resp = await http.post(
                    gw.url(ROUTE), json={"url": IMAGE}, headers={"Authorization": f"Bearer {owner}"}
                )
                assert resp.status == 502, await resp.read()
                while time.monotonic() - posted < 1.2 and len(asked) < 2:
                    await asyncio.sleep(0.05)
        assert asked, "the far host was never asked: the load did not start"
        assert asked == [IMAGE], "a hop was asked after the deadline"
        assert slow.cut, "the fetch ran on past the deadline"


@pytest.mark.asyncio
async def test_slow_loads_wait_on_a_pool_of_their_own(tmp_path, monkeypatch) -> None:
    """Five slow loads at once: four run and the fifth waits, on the image fetches' own workers,
    while other work the gateway hands to a thread still runs."""
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=4))
    monkeypatch.setattr(remote_image, "_pool", None)
    held = threading.Event()
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        slow = _Far(200, {"Content-Type": "image/png"}, PNG, gate=held)
        owner = await gw.owner_token()
        asked: list[str] = []
        async with aiohttp.ClientSession() as http:
            with _far_host(slow, asked):
                loads = [
                    asyncio.create_task(
                        http.post(
                            gw.url(ROUTE),
                            json={"url": IMAGE},
                            headers={"Authorization": f"Bearer {owner}"},
                        )
                    )
                    for _ in range(5)
                ]
                try:
                    # Four loads hold the route's four workers at the far host, and the fifth
                    # is queued behind them. A pool that never gets there fails the test here
                    # rather than hanging it.
                    until = time.monotonic() + 5.0
                    queued = 0
                    while time.monotonic() < until:
                        pool = remote_image._pool
                        queued = pool._work_queue.qsize() if pool else 0
                        if len(asked) == 4 and queued == 1:
                            break
                        await asyncio.sleep(0.05)
                    else:
                        pytest.fail(
                            f"after 5 s, {len(asked)} loads had reached the far host (4 should) "
                            f"and {queued} waited in the route's pool (1 should)"
                        )
                    other = await asyncio.wait_for(asyncio.to_thread(lambda: "ran"), timeout=0.5)
                    assert other == "ran"
                    assert len(asked) == 4, "a fifth load ran beside the four"
                finally:
                    # Every load ends while the stand-in still answers, red or green.
                    held.set()
                    answers = await asyncio.wait_for(
                        asyncio.gather(*loads, return_exceptions=True), timeout=10
                    )
        assert [getattr(a, "status", a) for a in answers] == [200] * 5


@pytest.mark.asyncio
async def test_too_many_redirects_says_so(tmp_path, monkeypatch) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        hops = [
            _Far(302, {"Location": f"https://images.example.com/{n}.png"}, b"") for n in range(6)
        ]
        resp, body, _, _ = await _load(gw, IMAGE, hops)
        assert resp.status == 403, body
        assert "redirect" in json.loads(body)["error"]["message"]


@pytest.mark.asyncio
async def test_a_fault_of_its_own_is_answered_in_the_envelope(
    tmp_path, monkeypatch, caplog
) -> None:
    """A fault of the route's own is answered 500 in the envelope, and logged once, with its
    traceback."""

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("a fault of the route's own")

    monkeypatch.setattr(remote_image, "_fetch_on_a_thread", broken)
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        with caplog.at_level(logging.WARNING, logger=remote_image.__name__):
            resp, body, _, _ = await _load(gw, IMAGE, None)
        assert resp.status == 500, body
        assert _code(body) == "internal_error"
    warned = [
        r
        for r in caplog.records
        if r.name == remote_image.__name__ and r.levelno == logging.WARNING
    ]
    assert len(warned) == 1 and warned[0].exc_info, warned


@pytest.mark.asyncio
async def test_a_body_that_is_not_an_object_is_refused_as_every_route_refuses_it(
    tmp_path, monkeypatch
) -> None:
    """The body is read by the gateway's one reader, so a malformed body and one that isn't an
    object are refused with its codes, and nothing is fetched."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        owner = await gw.owner_token()
        headers = {"Authorization": f"Bearer {owner}", "Content-Type": "application/json"}
        asked: list[str] = []
        async with aiohttp.ClientSession() as http:
            with _far_host(None, asked):
                bad = await http.post(gw.url(ROUTE), data=b"{not json", headers=headers)
                assert (bad.status, _code(await bad.read())) == (400, "invalid_json")
                listed = await http.post(gw.url(ROUTE), data=json.dumps([IMAGE]), headers=headers)
                assert (listed.status, _code(await listed.read())) == (400, "invalid_body")
        assert asked == []


def test_the_route_is_registered_at_its_name() -> None:
    """The route is registered at its name, POST only. That the path is a literal, which the route
    references read, is held by the published reference's rail (``test_docs_api_reference``)."""
    from aiohttp import web

    app = web.Application()
    remote_image.register_remote_image_routes(app)
    paths = {(r.method, r.resource.canonical) for r in app.router.routes() if r.resource}
    assert paths == {("POST", "/api/images/remote")}
    assert remote_image.ROUTE == "/api/images/remote"


# ── the deadline, on the thread (items the route can't reach) ───────────────────────────────


def test_a_load_whose_turn_came_after_its_deadline_starts_nothing(monkeypatch) -> None:
    """A load that waited for one of the route's workers until past its deadline looks no host
    up, asks no host and writes no row: its answer has already been given."""

    def looked_up(host: str) -> list[str]:
        raise AssertionError(f"{host} was looked up")

    monkeypatch.setattr(guard, "_resolve", looked_up)
    asked: list[str] = []
    recorder = MagicMock()
    began = threading.Event()
    with recording_sel(recorder), _far_host(None, asked):
        with pytest.raises(TimeoutError):
            remote_image._fetch_on_a_thread(IMAGE, REMOTE_IMAGE, time.monotonic() - 0.01, began)
    assert asked == [] and _egress_rows(recorder) == []
    assert not began.is_set(), "a load that started nothing said it began"


def test_a_load_ends_at_the_deadline_it_was_given() -> None:
    """The thread's fetch ends at the deadline the handler took at the press, not one counted
    from when a worker took the load."""
    slow = _Far(200, {"Content-Type": "image/png"}, PNG, delay=3.0)
    asked: list[str] = []
    began = time.monotonic()
    with recording_sel(MagicMock()), _far_host(slow, asked):
        with pytest.raises(TimeoutError):
            remote_image._fetch_on_a_thread(IMAGE, REMOTE_IMAGE, began + 0.2)
    assert time.monotonic() - began < 1.5, "the fetch ran on past the deadline it was given"
    assert asked == [IMAGE] and slow.cut


# ── what the page is told ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"url": ""}, {"url": "   "}, {"url": 7}],
    ids=["no address", "an empty one", "a blank one", "not a string"],
)
async def test_a_body_with_no_address_to_load_is_refused(tmp_path, monkeypatch, body) -> None:
    asked: list[str] = []
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        status, error = await _post(gw, body, asked)
    assert (status, error["code"]) == (400, "invalid_request"), error
    assert asked == []


@pytest.mark.asyncio
async def test_an_address_longer_than_it_loads_is_refused_in_words(tmp_path, monkeypatch) -> None:
    """The page shows the message as it is, so it is a sentence for the owner."""
    long = "https://images.example.com/" + "a" * remote_image.MAX_URL_CHARS
    asked: list[str] = []
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        status, error = await _post(gw, {"url": long}, asked)
    assert (status, error["code"]) == (400, "invalid_request"), error
    assert "`url`" not in error["message"] and "longer" in error["message"], error
    assert asked == []


@pytest.mark.asyncio
async def test_an_address_the_guard_cannot_read_is_refused(tmp_path, monkeypatch) -> None:
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, _ = await _load(gw, "https://[::1/a.png", None)
    assert resp.status == 403 and _code(body) == "remote_image_refused", body
    assert json.loads(body)["error"]["message"] == (
        "The image was not loaded: its address is not one PersonalClaw loads."
    )
    assert asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location,where",
    [
        ("http://images.example.com/p.png", "a plain-http address"),
        ("ftp://images.example.com/p.png", "an address PersonalClaw doesn't load"),
    ],
    ids=["plain http", "another scheme"],
)
async def test_a_redirect_it_does_not_follow_says_where_its_site_sent_it(
    tmp_path, monkeypatch, location: str, where: str
) -> None:
    hop = _Far(302, {"Location": location}, b"")
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, _ = await _load(gw, IMAGE, [hop])
    assert resp.status == 403 and _code(body) == "remote_image_refused", body
    message = json.loads(body)["error"]["message"]
    assert message == f"The image was not loaded: its site sent it on to {where}.", message
    assert asked == [IMAGE]


@pytest.mark.asyncio
async def test_a_load_still_waiting_for_a_worker_at_its_deadline_is_told_to_press_again(
    tmp_path, monkeypatch
) -> None:
    """Four loads hold the route's four workers past the deadline. A fifth, still waiting its
    turn, asked nothing of its site: it is told so, as a failure a press can clear."""
    monkeypatch.setattr(remote_image, "DEADLINE_S", 0.3)
    monkeypatch.setattr(remote_image, "_pool", None)
    held = threading.Event()
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        stuck = _Far(200, {"Content-Type": "image/png"}, PNG, hold=held)
        owner = await gw.owner_token()
        asked: list[str] = []
        sent = {"Authorization": f"Bearer {owner}"}
        async with aiohttp.ClientSession() as http:
            with _far_host(stuck, asked):
                four = [
                    asyncio.create_task(http.post(gw.url(ROUTE), json={"url": IMAGE}, headers=sent))
                    for _ in range(4)
                ]
                try:
                    until = time.monotonic() + 5.0
                    while len(asked) < 4 and time.monotonic() < until:
                        await asyncio.sleep(0.05)
                    reached = len(asked)
                    assert reached == 4, f"after 5 s, {reached} loads had reached the far host"
                    fifth = await http.post(gw.url(ROUTE), json={"url": IMAGE}, headers=sent)
                    status, error = fifth.status, json.loads(await fifth.read())["error"]
                finally:
                    held.set()
                    await asyncio.wait_for(asyncio.gather(*four, return_exceptions=True), 10)
    assert (status, error["code"]) == (503, "remote_image_busy"), error
    assert "took too long" not in error["message"] and "Load" in error["message"], error
    assert len(asked) == 4, "the fifth load asked its site"


@pytest.mark.asyncio
async def test_a_host_that_cannot_be_found_is_a_failure_a_press_can_clear(
    tmp_path, monkeypatch
) -> None:
    """A name that doesn't resolve now may resolve later, so it is a 5xx, which keeps Load."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, asked, recorder = await _load(gw, "https://gone.example/a.png", None)
    assert resp.status == 502 and _code(body) == "remote_image_unreachable", body
    assert json.loads(body)["error"]["message"] == (
        "The image was not loaded: gone.example could not be found."
    )
    assert asked == [] and [r["outcome"] for r in _egress_rows(recorder)] == ["denied"]


@pytest.mark.asyncio
async def test_a_site_that_cannot_be_reached_is_told_in_words(tmp_path, monkeypatch) -> None:
    down = _Far(200, {}, b"", raises=aiohttp.ClientConnectionError("refused"))
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        resp, body, _, _ = await _load(gw, IMAGE, down)
    assert resp.status == 502 and _code(body) == "remote_image_unreachable", body
    assert json.loads(body)["error"]["message"] == (
        "The image's site could not be reached, so nothing was shown."
    )


@pytest.mark.asyncio
async def test_an_address_is_read_once_its_spaces_are_stripped(tmp_path, monkeypatch) -> None:
    """The blank and length checks read the address that is fetched: the one stripped of the
    spaces around it."""
    at_most = "https://images.example.com/"
    at_most += "a" * (remote_image.MAX_URL_CHARS - len(at_most))
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        ok = _Far(200, {"Content-Type": "image/png"}, PNG)
        resp, body, asked, _ = await _load(gw, f"  {at_most}  ", ok)
        assert resp.status == 200, body
        assert asked == [at_most]
        resp, body, asked, _ = await _load(gw, at_most + "a", None)
        assert resp.status == 400 and _code(body) == "invalid_request", body
        assert asked == []
