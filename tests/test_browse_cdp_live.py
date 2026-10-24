"""Live: the browse gate, the PRODUCTION transport, and a real browser.

``test_browse_cdp_preflight.py`` proves the gate against a recording fake. A fake can show
that ``navigate`` declines to call ``send``; it cannot show that Chrome's own
``Page.frameNavigated`` ever reaches ``handle_event``, because the fake is what synthesizes
that event in the first place. So the redirect clause — "client-side redirects re-evaluated
per ``Page.frameNavigated``" — was, until this file, asserted against a dict the test wrote
itself. Here a real page really redirects itself and the guard really tears it down.

Two things make the evidence stronger than the fake-transport version:

* **The server is the ordering oracle.** A denied navigation is asserted to leave NO request
  in a live HTTP server's hit ledger. "Zero ``Page.navigate`` messages" can be satisfied by a
  gate that decides late but happens to return early; "the socket was never dialled" cannot.
  Its vacuity partner is the allowed navigation immediately after, whose hit MUST appear.
* **Two hostnames, one loopback server.** ``--host-resolver-rules`` maps ``allowed.local``
  and ``denied.local`` to 127.0.0.1 and everything else to ``~NOTFOUND``, so the deny is a
  policy decision about a name rather than a network fact, a regression cannot reach any real
  site from this suite, and both names are equally reachable — which is what makes "the
  denied one produced no request" mean something.

HONEST LIMIT, asserted rather than glossed: for a *client-side* redirect the request has
ALREADY left the browser by the time ``Page.frameNavigated`` reports it. The guard cannot
un-send it; what it can do is ensure the agent never reads the denied document. So the
redirected-request hit IS expected in the ledger, and what this file asserts is the teardown —
the page ends on ``about:blank`` with the denied DOM gone. Claiming the request was prevented
would be a lie.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import itertools
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import types
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import browse_chrome
import pytest

from personalclaw.browse import cdp
from personalclaw.browse.transport import CdpTransportError
from personalclaw.config.loader import AppConfig
from personalclaw.net import policy as net_policy

pytestmark = pytest.mark.security


ALLOWED_HOST = "allowed.local"
DENIED_HOST = "denied.local"
#: A second and third site for frames in a process of their own, and two denied hosts that share
#: a site with an allowed one, so a denied frame can sit in its embedder's process.
OTHER_HOST = "other.local"
THIRD_HOST = "third.local"
SAME_SITE_DENIED = "secret.allowed.local"
OTHER_SITE_DENIED = "secret.other.local"
DENY_HOSTS = [DENIED_HOST, SAME_SITE_DENIED, OTHER_SITE_DENIED]
_HOSTS = (ALLOWED_HOST, DENIED_HOST, OTHER_HOST, THIRD_HOST, SAME_SITE_DENIED, OTHER_SITE_DENIED)

#: The two browsers the frame proof runs on: as launched, where a cross-site frame shares its
#: page's process, and site-isolated, as a browser that gives each site its own process is, where
#: it runs in a process of its own and is reported only as a target of its own.
VARIANTS = {"default": (), "site-per-process": ("--site-per-process",)}

#: Named once so the skip/fail message says which proof stopped running.
PROOF = "LIVE PROOF"

#: The CDP reply Chrome sends to a ``Page.*`` command while the page's session is detached.
#: The fixture proves the page attached before the scenario starts; right after a cross-site
#: redirect the page's session is detached for a few milliseconds, which ``cdp._enforce``
#: rides out by sending the teardown command again (``cdp.TEARDOWN_RETRY_BOUND``).
_NOT_ATTACHED = cdp.PAGE_DETACHED


def _refuse_unmeasured(obs: dict) -> None:
    """Fail with the ENVIRONMENT cause when the page stayed detached past the teardown's bound.

    The page attached (the fixture proved it, and the scenario's first two steps drove it);
    a quarantine naming a detached page then means it stayed detached for the whole of
    ``cdp.TEARDOWN_RETRY_BOUND`` while the denied redirect was torn down. That does not weaken
    the policy assertions — it makes all three of them WRONG:

    * the quarantine check reads as a teardown regression,
    * the blanked-DOM check reads as ``stopLoading`` having been used on its own,
    * and the allowed-redirect check reads as the guard eating a legal navigation.

    So it is named as what it is, once, instead of three wrong policy conclusions. The
    session's behaviour is still the fail-closed one ``cdp._enforce`` documents: it
    quarantined, refused all further navigation and every read of the page.

    Deliberately a FAILURE and never a skip, and never a reason to re-run: a skip that reads
    as a pass is the shape this repo keeps rediscovering, and this module's whole job is to
    prove a denied document is torn down — an unproven teardown must not look proven.
    """
    reason = (obs.get("redirect_denied") or {}).get("quarantined") or ""
    if _NOT_ATTACHED in reason:
        raise AssertionError(
            f"{PROOF}: ENVIRONMENT, NOT POLICY — the page attached, then stayed detached past "
            f"the teardown's {cdp.TEARDOWN_RETRY_BOUND:g}s bound while a denied redirect was torn "
            f"down, so the module's teardown clauses were not measured. Quarantine reason: "
            f"{reason!r}. The session failed closed as documented; this is a red, not a "
            "candidate for a re-run."
        )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolver(host: str) -> list[str]:
    """Both names resolve to the loopback server, so only POLICY can tell them apart."""
    if host in _HOSTS or host == "localhost":
        return ["127.0.0.1"]
    raise socket.gaierror(f"no fake DNS entry for {host!r}")


# ── the site ──────────────────────────────────────────────────────────────────


class _Site:
    """A real loopback origin that counts real requests."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.hits: list[str] = []
        self.hit_times: list[tuple[float, str]] = []
        #: path -> (status, headers, body) for the frame pages; anything else is ``body_for``'s.
        self.routes: dict[str, tuple[int, dict, bytes]] = {}
        self._lock = threading.Lock()
        site = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:  # keep pytest output clean
                return

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
                with site._lock:
                    site.hits.append(self.path)
                    site.hit_times.append((time.monotonic(), self.path))
                route = site.routes.get(self.path.split("?", 1)[0])
                if route is not None:
                    status, headers, body = route
                else:
                    status, headers = 200, {"Content-Type": "text/html; charset=utf-8"}
                    body = site.body_for(self.path)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def url(self, host: str, path: str) -> str:
        return f"http://{host}:{self.port}{path}"

    def body_for(self, path: str) -> bytes:
        if path.startswith("/jump-denied"):
            target = self.url(DENIED_HOST, "/secret")
            return self._jump(target)
        if path.startswith("/jump-allowed"):
            target = self.url(ALLOWED_HOST, "/landed")
            return self._jump(target)
        return b"<!doctype html><html><body><h1>page</h1></body></html>"

    @staticmethod
    def _jump(target: str) -> bytes:
        """A CLIENT-side redirect: a script assigning ``location``, which is exactly the
        case the pre-flight in front of ``Page.navigate`` never sees."""
        return (
            "<!doctype html><html><body><h1>jump</h1><script>"
            f"location = {json.dumps(target)};"
            "</script></body></html>"
        ).encode("utf-8")

    def __enter__(self) -> _Site:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    def clear(self) -> None:
        with self._lock:
            self.hits.clear()
            self.hit_times.clear()

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.hits)


# ── the browser ───────────────────────────────────────────────────────────────


async def _prove_page_attachment(page_ws: str) -> None:
    """Require one successful Page-domain round trip before the fixture yields."""
    from personalclaw.browse.transport import WebSocketCdpTransport

    inner = await WebSocketCdpTransport.connect(page_ws)
    try:
        await inner.send(cdp.PAGE_ENABLE)
    finally:
        await inner.close()


def _wait_for_attached_page(port: int, deadline: float, stderr_log) -> str:
    """Poll Chrome until a page target answers a Page command under one deadline."""
    while time.time() < deadline:
        page_ws = None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as r:
                for target in json.load(r):
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        page_ws = target["webSocketDebuggerUrl"]
                        break
        except Exception:
            pass

        if page_ws is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                asyncio.run(asyncio.wait_for(_prove_page_attachment(page_ws), timeout=remaining))
            except TimeoutError:
                break
            except CdpTransportError as exc:
                if _NOT_ATTACHED not in str(exc):
                    raise
            else:
                return page_ws

        time.sleep(0.2)

    stderr_log.seek(0)
    detail = stderr_log.read().decode("utf-8", "replace").strip()
    raise AssertionError(
        "chrome never exposed a page target over CDP; its stderr was:\n"
        + (detail or "(chrome wrote nothing to stderr)")
    )


@contextlib.contextmanager
def _browser(chrome: str, flags: tuple[str, ...] = ()):
    """Launch a headless browser and yield ONE page target's WebSocket URL.

    Launching lives in the test on purpose: ``browse/transport.py`` deliberately owns no
    process, because per-site persistent profiles are the scope and it is not yet settled
    whether the gateway shares the interactive MCP's browser.
    """
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="browse-live-profile-")
    # Chrome's own stderr, kept so a launch that never reaches CDP reports its real cause
    # rather than only the generic poll timeout. On GitHub's ubuntu-latest a headless launch
    # without --no-sandbox aborts at startup, and DEVNULL used to swallow that fatal line.
    stderr_log = tempfile.NamedTemporaryFile(prefix="browse-live-stderr-", suffix=".log")
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--headless",
            # Required on GitHub Actions Linux runners: the sandbox cannot initialise there and
            # /dev/shm is too small for Chrome's default; without these the process aborts before
            # opening the CDP port. Harmless on macOS, where the suite also runs locally.
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            *flags,
            # The names the policy distinguishes; everything else is unreachable, so a guard
            # regression cannot dial a real host from this suite.
            "--host-resolver-rules="
            + ",".join(f"MAP {host} 127.0.0.1" for host in (*_HOSTS, "localhost"))
            + ",MAP * ~NOTFOUND",
            # chrome-headless-shell opens NO page target unless given a URL, and the browser
            # -level endpoint would then need a sessionId on every Page.* message.
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr_log,
    )
    try:
        deadline = time.time() + 30
        page_ws = _wait_for_attached_page(port, deadline, stderr_log)
        yield page_ws
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        # Closing the NamedTemporaryFile unlinks it (POSIX); Chrome's inherited fd keeps the
        # inode alive until it exits, which the terminate/wait above has already ensured.
        stderr_log.close()
        # The profile dir is ~10 MB of cache per launch and this fixture may run once per
        # xdist worker, so leaving them behind fills the temp dir over a few full-suite runs.
        shutil.rmtree(profile, ignore_errors=True)


class _RecordingTransport:
    """Wraps the production transport so the wire is observable without changing it.

    ``methods`` and ``sent`` are the page's own commands; ``child`` the ones sent on a frame's or
    a worker's session; ``events`` every event the session was handed."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.methods: list[str] = []
        self.sent: list[tuple[str, dict]] = []
        self.child: list[tuple[str, str]] = []
        self.events: list[tuple[str, dict, str | None]] = []

    async def send(
        self, method: str, params: dict | None = None, *, session_id: str | None = None
    ) -> dict:
        if session_id is not None:
            self.child.append((session_id, method))
            return await self.inner.send(  # type: ignore[attr-defined]
                method, params, session_id=session_id
            )
        self.methods.append(method)
        self.sent.append((method, dict(params or {})))
        return await self.inner.send(method, params)  # type: ignore[attr-defined]

    def set_event_listener(self, listener) -> None:
        if listener is None:
            self.inner.set_event_listener(None)  # type: ignore[attr-defined]
            return

        async def recorded(method, params, sid=None):
            self.events.append((method, params, sid))
            if sid is None:
                await listener(method, params)
            else:
                await listener(method, params, sid)

        self.inner.set_event_listener(recorded)  # type: ignore[attr-defined]

    async def drain_events(self) -> None:
        await self.inner.drain_events()  # type: ignore[attr-defined]

    def count(self, method: str) -> int:
        return self.methods.count(method)


async def _settle(predicate, *, timeout: float = 15.0) -> bool:
    """Poll until ``predicate`` holds. Returns whether it ever did."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def _location(inner) -> str:
    """Read ``location.href`` off the live page, straight down the inner transport so the
    recorded wire stays a statement about the gate rather than about the probe."""
    result = await inner.send(
        "Runtime.evaluate", {"expression": "location.href", "returnByValue": True}
    )
    return str((result.get("result") or {}).get("value") or "")


async def _marker(inner) -> dict:
    result = await inner.send(
        "Runtime.evaluate",
        {
            "expression": ("JSON.stringify(window.__personalclawSafety || null)"),
            "returnByValue": True,
        },
    )
    raw = (result.get("result") or {}).get("value")
    return json.loads(raw) if raw else {}


async def _scenario(page_ws: str, site: _Site, sel_rows: list) -> dict:
    from personalclaw.browse.transport import WebSocketCdpTransport

    inner = await WebSocketCdpTransport.connect(page_ws)
    recorder = _RecordingTransport(inner)
    session = cdp.GatedCdpSession(recorder, resolver=_resolver)
    obs: dict = {}
    try:
        await session.start()
        obs["start_wire"] = list(recorder.methods)

        # ── A. a denied host: nothing on the wire, nothing on the server ──────
        recorder.methods.clear()
        site.clear()
        sel_rows.clear()
        denied = await session.navigate(site.url(DENIED_HOST, "/secret"))
        # Give a hypothetical navigate-then-check implementation time to actually land.
        await asyncio.sleep(0.75)
        obs["deny"] = {
            "allowed": denied.allowed,
            "ok": denied.ok,
            "host": denied.host,
            "reason": denied.reason,
            "navigate_count": recorder.count(cdp.NAVIGATE),
            "server_hits": site.snapshot(),
            "sel": [
                (row.operation, row.metadata.get("phase"), row.metadata.get("host"))
                for row in sel_rows
            ],
        }

        # ── B. an allowed host: the vacuity partner for every "zero" above ────
        recorder.methods.clear()
        site.clear()
        sel_rows.clear()
        allowed = await session.navigate(site.url(ALLOWED_HOST, "/allowed"))
        await _settle(lambda: any(h.startswith("/allowed") for h in site.snapshot()))
        obs["allow"] = {
            "allowed": allowed.allowed,
            "ok": allowed.ok,
            "navigate_count": recorder.count(cdp.NAVIGATE),
            "server_hits": site.snapshot(),
            "sel": list(sel_rows),
            "final_url": await _location(inner),
        }
        obs["marker"] = await _marker(inner)

        # ── C. a CLIENT-SIDE redirect to a denied host: torn down ─────────────
        before = len(session.blocks)
        recorder.methods.clear()
        site.clear()
        sel_rows.clear()
        await session.navigate(site.url(ALLOWED_HOST, "/jump-denied"))
        fired = await _settle(lambda: len(session.blocks) > before)
        # A teardown resends a step while the page is detached, for up to
        # cdp.TEARDOWN_RETRY_BOUND, so it is sampled only once it has ended, quarantined or not.
        await _settle(lambda: not session.tearing_down)
        await asyncio.sleep(0.75)  # let the teardown's about:blank navigation land
        obs["redirect_denied"] = {
            "guard_fired": fired,
            "blocked_url": session.blocks[-1].url if session.blocks else "",
            "blocked_host": session.blocks[-1].host if session.blocks else "",
            "sel": [
                (row.operation, row.metadata.get("phase"), row.metadata.get("host"))
                for row in sel_rows
            ],
            # The whole teardown, a resent step counted once; [1:] drops the scenario's own
            # Page.navigate, the first message recorded after the clear above.
            "wire_tail": [method for method, _ in itertools.groupby(recorder.methods[1:])],
            "final_url": await _location(inner),
            "server_hits": site.snapshot(),
            "quarantined": session.quarantine_reason,
        }

        # ── D. a client-side redirect to an ALLOWED host: left alone ──────────
        before = len(session.blocks)
        recorder.methods.clear()
        site.clear()
        sel_rows.clear()
        await session.navigate(site.url(ALLOWED_HOST, "/jump-allowed"))
        landed = await _settle(lambda: any(h.startswith("/landed") for h in site.snapshot()))
        await asyncio.sleep(0.75)
        obs["redirect_allowed"] = {
            "landed": landed,
            "new_blocks": len(session.blocks) - before,
            "sel": list(sel_rows),
            "final_url": await _location(inner),
            "server_hits": site.snapshot(),
        }
    finally:
        await inner.close()
    # Attribute an unattached session before any test reads these observations, so one
    # accurate environment failure replaces three wrong policy conclusions.
    _refuse_unmeasured(obs)
    return obs


@pytest.fixture(scope="module", params=list(VARIANTS))
def browser(request):
    """One browser per variant, one launch for every scenario on it, and the audit rows its
    sessions write.

    ``mock.patch`` rather than ``monkeypatch`` because this fixture is module-scoped.
    ``AppConfig.load`` is faked so the real home is never read, ``SecurityEventLog`` is
    replaced so nothing writes a real audit log, and ``net_policy._LAST_DENY_HOSTS`` is
    restored on the way out — it is module state that would otherwise carry this test's
    denials into whatever else this xdist worker runs.
    """
    chrome = browse_chrome.chrome_or_skip(PROOF)
    browse_chrome.websockets_or_skip(PROOF)

    sel_rows: list = []

    class _Recorder:
        def log(self, event: object) -> None:
            sel_rows.append(event)

    egress = types.SimpleNamespace(
        # Loopback is private, so the BROWSE profile's public-only stance would deny BOTH
        # names. The operator layer is the real mechanism that opens a LAN/loopback host,
        # and `deny_hosts` is the real mechanism that shuts one again — so this exercises
        # `egress_policy_for`, not a hand-built policy.
        allow_hosts=[],
        deny_hosts=list(DENY_HOSTS),
        allow_private=True,
    )
    fake_app = types.SimpleNamespace(security=types.SimpleNamespace(egress=egress))
    remembered = net_policy._LAST_DENY_HOSTS
    try:
        with (
            mock.patch.object(AppConfig, "load", classmethod(lambda cls: fake_app)),
            mock.patch.object(cdp, "SecurityEventLog", lambda: _Recorder()),
            _Site() as site,
            _browser(chrome, VARIANTS[request.param]) as page_ws,
        ):
            yield types.SimpleNamespace(
                variant=request.param, page_ws=page_ws, site=site, sel_rows=sel_rows
            )
    finally:
        net_policy._LAST_DENY_HOSTS = remembered


@pytest.fixture(scope="module")
def live(browser) -> dict:
    """The navigation and redirect scenario, on each browser."""
    return asyncio.run(_scenario(browser.page_ws, browser.site, browser.sel_rows))


# ── the clauses ───────────────────────────────────────────────────────────────


def test_the_guard_script_is_installed_before_anything_navigates(live: dict) -> None:
    """Start's wire, from a real browser: enable, inject, then read the frames the tab already
    holds (a blank tab here, so nothing is torn down), and nothing else."""
    assert live["start_wire"] == [
        cdp.PAGE_ENABLE,
        cdp.ADD_SCRIPT,
        cdp.SET_AUTO_ATTACH,
        cdp.GET_FRAME_TREE,
    ]


def test_the_session_really_injected_the_real_safety_script(live: dict) -> None:
    """The injected script at the SESSION's call site: the guard the session injects is the shipped
    one, and it installed in the live page. The sibling file proves what the script DOES;
    this proves the session is what put it there."""
    marker = live["marker"]
    assert marker, "window.__personalclawSafety is absent, so nothing was injected"
    assert marker.get("failed") in ([], None), f"guard steps failed in-page: {marker.get('failed')}"
    for step in ("fetch", "XMLHttpRequest", "media", "deviceApis"):
        assert step in marker.get("applied", []), f"{step} was not guarded: {marker}"


def test_a_denied_host_never_reaches_the_network(live: dict) -> None:
    """THE ordering clause, against a live server rather than a return value.

    Three independent statements of the same ordering: the gate said no, no ``Page.navigate``
    was written, and — the one only a real browser can make — the HTTP server recorded no
    request. A check that ran after the send would show a hit here.
    """
    deny = live["deny"]
    assert deny["allowed"] is False and deny["ok"] is False
    assert deny["host"] == DENIED_HOST
    assert "deny list" in deny["reason"]
    assert deny["navigate_count"] == 0, "a denied host must not produce a Page.navigate"
    assert deny["server_hits"] == [], (
        "the browser dialled the denied origin, so the gate ran too late: " f"{deny['server_hits']}"
    )


def test_a_denied_navigation_is_recorded_in_the_sel(live: dict) -> None:
    """The audit half of the clause, on the live path."""
    assert live["deny"]["sel"] == [(cdp.NAVIGATE, "preflight", DENIED_HOST)]


def test_an_allowed_host_does_navigate_and_is_not_audited_as_a_denial(live: dict) -> None:
    """The vacuity floor for all three "zero" assertions above.

    Without this, "no wire message" and "no server hit" are satisfied by a session that
    cannot navigate at all — which is exactly what a broken transport looks like.
    """
    allow = live["allow"]
    assert allow["allowed"] is True and allow["ok"] is True
    assert allow["navigate_count"] == 1
    assert any(h.startswith("/allowed") for h in allow["server_hits"]), allow["server_hits"]
    assert allow["sel"] == [], "an allowed navigation is not a denial"
    assert allow["final_url"].startswith(f"http://{ALLOWED_HOST}:")


def test_a_real_client_side_redirect_to_a_denied_host_is_re_evaluated(live: dict) -> None:
    """The redirect clause, with a real page really assigning ``location``.

    The pre-flight authorised ``allowed.local`` and never saw ``denied.local``; only the
    ``Page.frameNavigated`` re-evaluation can catch it. Asserted on the teardown, because a
    log line would leave the agent reading the denied document.
    """
    red = live["redirect_denied"]
    assert red["guard_fired"], "the guard never re-evaluated the redirect"
    assert red["blocked_host"] == DENIED_HOST
    assert red["blocked_url"].startswith(f"http://{DENIED_HOST}:")
    assert red["sel"] == [(cdp.FRAME_NAVIGATED, "frame_navigated", DENIED_HOST)]
    assert not red["quarantined"], "the teardown was deliverable, so nothing should quarantine"


def test_the_denied_document_is_torn_down_not_merely_stopped(live: dict) -> None:
    """``stopLoading`` alone is not enforcement — the DOM claim, measured on a real page.

    This is the assertion no fake transport can make. A fake can record that two messages
    were written; only a browser can say whether the denied document is still the one the
    agent would extract. Measured with the second teardown message removed: the page stays on
    ``http://denied.local:PORT/secret``, fully loaded.
    """
    red = live["redirect_denied"]
    assert red["final_url"] == cdp.BLANK_URL, (
        f"the denied document is still loaded ({red['final_url']!r}); stopLoading alone "
        "leaves the DOM the agent would extract"
    )
    assert red["wire_tail"] == [cdp.STOP_LOADING, cdp.NAVIGATE], (
        "order matters: blanking before stopping races the in-flight load, and nothing else "
        f"belongs in a teardown (a resent step counts once); wire was {red['wire_tail']}"
    )


def test_the_redirect_request_itself_already_left_the_browser(live: dict) -> None:
    """The honest limit, asserted so nobody later mistakes the teardown for prevention.

    A client-side redirect is dispatched by the page; ``Page.frameNavigated`` is the browser
    telling us it already happened. The guard's reach is the DOM, not the socket.
    """
    assert any(h.startswith("/secret") for h in live["redirect_denied"]["server_hits"]), (
        "if the denied request never reached the server, this test's premise is wrong and "
        "the limit it documents should be re-measured"
    )


def test_a_real_client_side_redirect_to_an_allowed_host_is_left_alone(live: dict) -> None:
    """Vacuity floor for the redirect teardown: it is the DENY that tore the page down.

    Same mechanism, same event, allowed destination — no block, no SEL row, and the page is
    left on the redirected URL. Without this, the test above passes for a guard that blanks
    the page on every ``Page.frameNavigated``, which would be a browser that cannot browse.
    """
    red = live["redirect_allowed"]
    assert red["landed"], "the allowed redirect never completed, so nothing was measured"
    assert red["new_blocks"] == 0, "an allowed redirect must not be blocked"
    assert red["sel"] == []
    assert red["final_url"].endswith("/landed"), red["final_url"]


# ── the attribution rail itself, which needs no browser ───────────────────────


def test_the_browser_repolls_when_a_page_target_is_not_attached() -> None:
    target_lists = iter(
        [
            [{"type": "page", "webSocketDebuggerUrl": "ws://stale"}],
            [{"type": "page", "webSocketDebuggerUrl": "ws://attached"}],
        ]
    )

    def _open_targets(*_args: object, **_kwargs: object):
        return contextlib.nullcontext(io.StringIO(json.dumps(next(target_lists))))

    probe = mock.AsyncMock(
        side_effect=[
            CdpTransportError(f"the browser rejected the command: {_NOT_ATTACHED}"),
            None,
        ]
    )
    with (
        mock.patch.object(urllib.request, "urlopen", side_effect=_open_targets) as poll,
        mock.patch(f"{__name__}._prove_page_attachment", probe),
        mock.patch.object(time, "sleep"),
    ):
        page_ws = _wait_for_attached_page(9222, time.time() + 1, io.BytesIO())

    assert page_ws == "ws://attached"
    assert poll.call_count == 2
    assert [call.args[0] for call in probe.await_args_list] == ["ws://stale", "ws://attached"]


def test_chrome_never_exposed_a_page_target_still_fails() -> None:
    def _no_targets(*_args: object, **_kwargs: object):
        return contextlib.nullcontext(io.StringIO("[]"))

    stderr_log = io.BytesIO(b"forced no page target")
    with (
        mock.patch.object(urllib.request, "urlopen", side_effect=_no_targets),
        mock.patch.object(time, "time", side_effect=[0.0, 1.0]),
        mock.patch.object(time, "sleep"),
        pytest.raises(AssertionError, match="chrome never exposed a page target") as exc_info,
    ):
        _wait_for_attached_page(9222, 0.5, stderr_log)

    assert "forced no page target" in str(exc_info.value)


def test_an_unattached_session_is_attributed_to_the_environment() -> None:
    """The real CDP payload must be named as environment, not read as a guard defect.

    Text taken from an actual failing run rather than paraphrased: a paraphrase is what
    lets the discriminator drift away from the message Chrome actually sends.
    """
    obs = {
        "redirect_denied": {
            "quarantined": (
                "could not enforce the block on 'denied.local' (the browser rejected the "
                "command: {'code': -32000, 'message': 'Not attached to an active page'}); "
                "this session no longer navigates"
            )
        }
    }
    with pytest.raises(AssertionError, match="ENVIRONMENT, NOT POLICY"):
        _refuse_unmeasured(obs)


def test_a_measured_run_and_a_real_guard_failure_both_pass_the_rail() -> None:
    """The other direction, or the rail would swallow the defect it exists to expose.

    A clean run has no quarantine at all, and a quarantine from a DIFFERENT cause is a
    real finding this module must still be allowed to report — so neither may be
    re-labelled as an environment problem.
    """
    _refuse_unmeasured({"redirect_denied": {"quarantined": ""}})
    _refuse_unmeasured({"redirect_denied": {}})
    _refuse_unmeasured({})
    _refuse_unmeasured(
        {
            "redirect_denied": {
                "quarantined": (
                    "could not enforce the block on 'denied.local' (the CDP transport "
                    "closed mid-teardown); this session no longer navigates"
                )
            }
        }
    )


# ── every frame of the page, on both browsers ────────────────────────────────

GREEN, MAGENTA, BLUE, TEAL = (0, 200, 0), (200, 0, 200), (0, 0, 200), (0, 160, 160)
#: Where the top page puts its one frame, and where a frame puts the frame it nests.
FRAME_RECT, NESTED_RECT, BOX_RECT = (20, 120, 620, 520), (70, 170, 470, 420), (20, 20, 180, 80)
_TOP_STYLE = (
    "html,body{margin:0;background:#fff}"
    "#box{position:absolute;left:20px;top:20px;width:160px;height:60px;background:rgb(0,200,0)}"
    "#f{position:absolute;left:20px;top:120px;width:600px;height:400px;border:0}"
)
_NESTED_STYLE = "#n{position:absolute;left:50px;top:50px;width:400px;height:250px;border:0}"


def _html(title: str, body: str, style: str = "") -> bytes:
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<title>{title}</title><style>html,body{{margin:0;padding:0}}{style}</style>"
        f"</head><body>{body}</body></html>"
    ).encode("utf-8")


def _fill(rgb: tuple[int, int, int]) -> str:
    return "html,body{height:100%%;background:rgb(%d,%d,%d)}" % rgb


def _tiny_pdf() -> bytes:
    """One page, one red rectangle: what the browser's viewer must still draw."""
    content = b"1 0 0 rg 50 50 400 300 re f"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 400] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


def _frame_pages(site: _Site) -> None:
    """The pages the frame cases load; each beacons to the ledger when its script runs."""
    u = site.url

    def top(src: str, extra: str = "", script: str = "") -> bytes:
        return _html(
            "top",
            "<div id=box onclick=\"new Image().src='/clicked-allowed'\"></div>"
            f'<iframe id=f src="{src}"{extra}></iframe><script>{script}</script>',
            _TOP_STYLE,
        )

    def nest(src: str, beacon: str) -> bytes:
        return _html(
            "mid",
            f'<iframe id=n src="{src}"></iframe><script>new Image().src="{beacon}"</script>',
            _fill(BLUE) + _NESTED_STYLE,
        )

    page = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
    routes = {
        "/frame-secret": _html(
            "secret",
            "<img src='/frame-secret-sub.png' style='display:none'>"
            "<script>new Image().src='/frame-secret-ran';document.documentElement.onclick="
            "function(){new Image().src='/frame-clicked-in-secret'}</script>",
            _fill(MAGENTA),
        ),
        "/okframe": _html(
            "ok", "<script>new Image().src='/frame-allowed-ran'</script>", _fill(TEAL)
        ),
        "/l1": top(u(DENIED_HOST, "/frame-secret")),
        "/l2": top(u(SAME_SITE_DENIED, "/frame-secret")),
        "/l3": top(u(OTHER_HOST, "/mid")),
        "/mid": nest(u(DENIED_HOST, "/frame-secret"), "/mid-ran"),
        "/l4": top(u(OTHER_HOST, "/mid-ok")),
        "/mid-ok": nest(u(ALLOWED_HOST, "/okframe"), "/mid-ran"),
        "/l5": top(u(OTHER_HOST, "/hop")),
        "/hop": _html(
            "hop",
            "<script>setTimeout(function(){location="
            + json.dumps(u(DENIED_HOST, "/frame-secret"))
            + "},300)</script>",
            _fill(BLUE),
        ),
        "/l6": top(u(OTHER_HOST, "/worker-frame")),
        "/worker-frame": _html(
            "wf",
            "<script>new Image().src='/worker-guard?present='+(window.__personalclawSafety?1:0);"
            "try{var w=new Worker(URL.createObjectURL(new Blob([\"postMessage('ok')\"],"
            "{type:'text/javascript'})));w.onmessage=function(){new Image().src='/worker-ok'};}"
            "catch(e){new Image().src='/worker-threw'}</script>",
            _fill(BLUE),
        ),
        "/l7": top(u(OTHER_HOST, "/mid-ss")),
        "/mid-ss": nest(u(OTHER_SITE_DENIED, "/frame-secret"), "/mid-ran"),
        "/l9": _html(
            "sandboxed",
            '<div id=box></div><iframe id=f sandbox="allow-scripts" srcdoc="'
            + (
                "<!doctype html><body style='margin:0;background:rgb(0,0,200)'><iframe src='"
                + u(ALLOWED_HOST, "/okframe")
                + "' style='position:absolute;left:50px;top:50px;width:400px;height:250px;"
                "border:0'></iframe></body>"
            )
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            + '"></iframe>',
            _TOP_STYLE,
        ),
        "/l10-removed": top(
            u(ALLOWED_HOST, "/okframe"),
            script=(
                "var d=+(location.search.slice(1)||0);var f=document.createElement('iframe');"
                f"f.src={json.dumps(u(DENIED_HOST, '/frame-secret'))};document.body.appendChild(f);"
                "setTimeout(function(){f.remove();new Image().src='/removed'},d)"
            ),
        ),
        "/l10-repoint": top(
            u(DENIED_HOST, "/frame-secret"),
            script=(
                "var n=0;setInterval(function(){document.getElementById('f').src="
                f"{json.dumps(u(DENIED_HOST, '/frame-secret'))}+'?'+(n++)}},50)"
            ),
        ),
        "/l10-dest": _html("dest", "<div id=box></div>", _TOP_STYLE),
        "/l11-a": _html("A", "<div id=box></div>", _TOP_STYLE),
        "/l11-b": top(u(SAME_SITE_DENIED, "/frame-secret")),
        "/l11-x": top(u(DENIED_HOST, "/frame-secret")),
        "/l12": _html(
            "busy",
            f'<iframe src="{u(OTHER_HOST, "/busy")}" style="width:200px;height:100px"></iframe>'
            "<script>setTimeout(function(){var f=document.createElement('iframe');"
            f"f.src={json.dumps(u(THIRD_HOST, '/third'))};document.body.appendChild(f)}},350)"
            "</script>",
        ),
        "/busy": _html(
            "busy",
            "<script>setTimeout(function(){var e=Date.now()+4000;while(Date.now()<e){}},50)"
            "</script>busy",
        ),
        "/third": _html("third", "<script>new Image().src='/third-ran'</script>"),
        "/l13-sw": _html(
            "sw",
            "<script>if(navigator.serviceWorker.controller){new Image().src='/sw-controls'}"
            "else{navigator.serviceWorker.register('/sw.js').then(function(){"
            "new Image().src='/sw-registered'})}"
            "CSS.paintWorklet.addModule('/pw.js').then(function(){new Image().src='/paint-ok'})"
            "</script>",
        ),
        "/l14-embed": _html(
            "pdf",
            "<div id=box></div><iframe id=f src='/doc.pdf'></iframe>",
            _TOP_STYLE,
        ),
    }
    for path, body in routes.items():
        site.routes[path] = (200, page, body)
    site.routes["/sw.js"] = (
        200,
        {"Content-Type": "application/javascript", "Cache-Control": "no-store"},
        b"self.addEventListener('install',e=>self.skipWaiting());"
        b"self.addEventListener('activate',e=>e.waitUntil(clients.claim()));",
    )
    site.routes["/pw.js"] = (
        200,
        {"Content-Type": "application/javascript"},
        b"registerPaint('x', class{paint(){}});",
    )
    site.routes["/doc.pdf"] = (200, {"Content-Type": "application/pdf"}, _tiny_pdf())
    # Without no-store, so a browser that keeps a back-forward cache keeps this page in it.
    site.routes["/l11-cached"] = (
        200,
        {"Content-Type": "text/html; charset=utf-8"},
        top(u(DENIED_HOST, "/frame-secret")),
    )


def _count(png: bytes, rgb, rect: tuple[int, int, int, int]) -> int:
    """Pixels in *rect* of colour *rgb* (within 14 per channel), or matching *rgb* when it is a
    predicate."""
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    x0, y0, x1, y1 = rect
    w, h = image.size
    box = image.crop((max(0, x0), max(0, y0), min(w, x1), min(h, y1)))
    if callable(rgb):
        return sum(1 for px in box.getdata() if rgb(px))
    return sum(1 for px in box.getdata() if all(abs(px[i] - rgb[i]) <= 14 for i in range(3)))


def _red(px) -> bool:
    """The PDF's red, as the viewer draws it: any strong red, edges and scaling included."""
    return px[0] > 200 and px[1] < 60 and px[2] < 60


async def _png(inner) -> bytes:
    import base64

    reply = await inner.send("Page.captureScreenshot", {"format": "png"})
    return base64.b64decode(reply.get("data") or "")


async def _evaluate(inner, expression: str):
    result = await inner.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    return (result.get("result") or {}).get("value")


async def _tree(inner) -> list[str]:
    tree = (await inner.send(cdp.GET_FRAME_TREE, {})).get("frameTree") or {}
    urls, stack = [], [tree]
    while stack:
        node = stack.pop(0)
        urls.append(str((node.get("frame") or {}).get("url") or ""))
        stack.extend(node.get("childFrames") or [])
    return urls


async def _case(page_ws: str, site: _Site, sel_rows: list, name: str, path: str, *, wait=2.0):
    """One frame case on a fresh session: load *path*, let it settle, and read what is shown."""
    from personalclaw.browse.page import CdpPageDriver
    from personalclaw.browse.transport import WebSocketCdpTransport

    inner = await WebSocketCdpTransport.connect(page_ws)
    recorder = _RecordingTransport(inner)
    session = cdp.GatedCdpSession(recorder, resolver=_resolver)
    obs: dict = {"case": name}
    try:
        await session.start()
        site.clear()
        sel_rows.clear()
        recorder.methods.clear()
        recorder.sent.clear()
        nav = await session.navigate(site.url(ALLOWED_HOST if name != "l13" else "localhost", path))
        obs["nav_ok"] = nav.ok
        await asyncio.sleep(wait)
        await _settle(lambda: not session.tearing_down, timeout=5)
        await session.wait_idle()
        png = await _png(inner)
        obs["px"] = {
            "magenta": _count(png, MAGENTA, FRAME_RECT),
            "green": _count(png, GREEN, BOX_RECT),
            "blue": _count(png, BLUE, FRAME_RECT),
            "teal": _count(png, TEAL, FRAME_RECT),
            "nested_magenta": _count(png, MAGENTA, NESTED_RECT),
            "red": _count(png, _red, (0, 0, 1000, 800)),
        }
        if name == "l1":
            driver = CdpPageDriver(session.page_transport)
            await driver.click_at(320.0, 320.0)
            await driver.click_at(100.0, 50.0)
            await asyncio.sleep(0.5)
        obs["top"] = await _evaluate(inner, "location.href")
        obs["tree"] = await _tree(inner)
        obs["hits"] = sorted({h.split("?")[0] for h in site.snapshot()})
        obs["raw_hits"] = site.snapshot()
        obs["blocks"] = [(b.url, b.host, b.reason) for b in session.blocks]
        obs["sel"] = [
            (r.operation, r.metadata.get("phase"), r.metadata.get("host")) for r in sel_rows
        ]
        obs["frame_blanks"] = [
            p["frameId"] for m, p in recorder.sent if m == cdp.NAVIGATE and "frameId" in p
        ]
        obs["stop_loading"] = recorder.count(cdp.STOP_LOADING)
        obs["top_blanks"] = recorder.sent.count((cdp.NAVIGATE, {"url": cdp.BLANK_URL}))
        obs["attached"] = [
            (p.get("targetInfo") or {}).get("type")
            for m, p, _s in recorder.events
            if m == cdp.ATTACHED_TO_TARGET
        ]
        obs["quarantine"] = session.quarantine_reason
        if name in _GO_BACK:
            await CdpPageDriver(session.page_transport).go_back()
            await asyncio.sleep(1.5)
            await _settle(lambda: not session.tearing_down, timeout=5)
            obs["after_back"] = await _evaluate(inner, "location.href")
            await _evaluate(inner, "history.forward(); 'ok'")
            await asyncio.sleep(1.5)
            await _settle(lambda: not session.tearing_down, timeout=5)
            await session.wait_idle()
            obs["forward_magenta"] = _count(await _png(inner), MAGENTA, FRAME_RECT)
        if name == "l14":
            obs["pdf_viewer"] = await _evaluate(inner, "navigator.pdfViewerEnabled")
    finally:
        await inner.close()
    return obs


async def _l10(page_ws: str, site: _Site, sel_rows: list) -> dict:
    """A denied frame removed 0-20 ms after it was added, and a page left while its denied frame is
    re-pointed every 50 ms: the page stays, or the destination does, every time."""
    import logging

    from personalclaw.browse.transport import WebSocketCdpTransport

    said: list[str] = []

    class _Said(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            said.append(record.getMessage())

    heard = _Said(level=logging.INFO)
    logging.getLogger(cdp.__name__).addHandler(heard)
    recorder = _RecordingTransport(inner := await WebSocketCdpTransport.connect(page_ws))
    session = cdp.GatedCdpSession(recorder, resolver=_resolver)
    tops: list[str] = []
    try:
        await session.start()
        for delay in (0, 10, 20):
            await session.navigate(site.url(ALLOWED_HOST, f"/l10-removed?{delay}"))
            await asyncio.sleep(1.0)
            await session.wait_idle()
            tops.append(await _evaluate(inner, "location.pathname"))
        for _ in range(3):
            await session.navigate(site.url(ALLOWED_HOST, "/l10-repoint"))
            await asyncio.sleep(0.6)
            await session.navigate(site.url(ALLOWED_HOST, "/l10-dest"))
            await asyncio.sleep(0.6)
            await session.wait_idle()
            tops.append(await _evaluate(inner, "location.pathname"))
    finally:
        await inner.close()
        logging.getLogger(cdp.__name__).removeHandler(heard)
    return {
        "tops": tops,
        "quarantine": session.quarantine_reason,
        # What the session said and did, so a page lost to the whole-page fallback says why.
        "said": said,
        "top_blanks": recorder.sent.count((cdp.NAVIGATE, {"url": cdp.BLANK_URL})),
        "sel": [(r.operation, r.metadata.get("phase")) for r in sel_rows],
    }


async def _l12(page_ws: str, site: _Site) -> dict:
    """A frame whose renderer is busy for 4 s: a frame of another site added at 350 ms, and a read
    of the page, are not held behind it."""
    from personalclaw.browse.page import CdpPageDriver
    from personalclaw.browse.transport import WebSocketCdpTransport

    inner = await WebSocketCdpTransport.connect(page_ws)
    session = cdp.GatedCdpSession(_RecordingTransport(inner), resolver=_resolver)
    try:
        await session.start()
        site.clear()
        began = time.monotonic()
        await session.navigate(site.url(ALLOWED_HOST, "/l12"))
        await asyncio.sleep(max(0.0, 0.4 - (time.monotonic() - began)))
        read_began = time.monotonic()
        await CdpPageDriver(session.page_transport).html()
        read = time.monotonic() - read_began
        await _settle(lambda: any(p == "/third-ran" for _t, p in site.hit_times), timeout=6)
        third = [t - began for t, p in site.hit_times if p == "/third-ran"]
        await asyncio.sleep(4.5)
        await session.wait_idle()
    finally:
        await inner.close()
    return {"read_s": read, "third_s": third[0] if third else None, "q": session.quarantine_reason}


async def _l15(page_ws: str, site: _Site, sel_rows: list) -> dict:
    """The page a teardown left, then GO_BACK: the page moves itself to a denied host (straight
    down the socket, as a link it follows would), the session tears that document down to the
    blank, and the run goes back. The denied entry lies between the blank and the page before."""
    from personalclaw.browse.page import CdpPageDriver
    from personalclaw.browse.transport import WebSocketCdpTransport

    inner = await WebSocketCdpTransport.connect(page_ws)
    recorder = _RecordingTransport(inner)
    session = cdp.GatedCdpSession(recorder, resolver=_resolver)
    obs: dict = {}
    try:
        await session.start()
        await session.navigate(site.url(ALLOWED_HOST, "/l11-a"))
        await _settle(lambda: "/l11-a" in site.snapshot(), timeout=8)
        await asyncio.sleep(0.3)
        await inner.send(cdp.NAVIGATE, {"url": site.url(DENIED_HOST, "/secret")})
        obs["torn_down"] = await _settle(
            lambda: any(b.host == DENIED_HOST for b in session.blocks), timeout=8
        )
        await _settle(lambda: not session.tearing_down, timeout=5)
        await asyncio.sleep(0.5)
        await session.wait_idle()
        obs["before_back"] = await _evaluate(inner, "location.href")
        site.clear()
        sel_rows.clear()
        await CdpPageDriver(session.page_transport).go_back()
        await asyncio.sleep(1.5)
        await _settle(lambda: not session.tearing_down, timeout=5)
        await session.wait_idle()
        obs["top"] = await _evaluate(inner, "location.href")
        obs["hits"] = site.snapshot()
        obs["sel"] = [
            (r.operation, r.metadata.get("phase"), r.metadata.get("host")) for r in sel_rows
        ]
        obs["quarantine"] = session.quarantine_reason
    finally:
        await session.close()
        await inner.close()
    return obs


async def _l13_prime(page_ws: str, site: _Site) -> None:
    """Register the service worker on its page before any session watches the tab, as a page the
    owner visited earlier would have."""
    from personalclaw.browse.transport import WebSocketCdpTransport

    inner = await WebSocketCdpTransport.connect(page_ws)
    try:
        await inner.send(cdp.PAGE_ENABLE, {})
        await inner.send(cdp.NAVIGATE, {"url": site.url("localhost", "/l13-sw")})
        await _settle(lambda: "/sw-registered" in site.snapshot(), timeout=10)
    finally:
        await inner.close()


async def _l11_cached(page_ws: str, site: _Site, sel_rows: list) -> dict:
    """A page that went into the back-forward cache with a denied cross-site frame live, as a page
    the whole-page fallback left would have: loaded and left before any session watched the tab.
    A session then goes back to it.

    A full browser build restores it from the cache, and the frame, in a process of its own, is
    attached again already running. The headless shell keeps no back-forward cache, so there the
    page loads again and its frame is judged as a new one."""
    from personalclaw.browse.page import CdpPageDriver
    from personalclaw.browse.transport import WebSocketCdpTransport

    raw = await WebSocketCdpTransport.connect(page_ws)
    try:
        await raw.send(cdp.PAGE_ENABLE, {})
        site.clear()
        await raw.send(cdp.NAVIGATE, {"url": site.url(ALLOWED_HOST, "/l11-cached")})
        ran = await _settle(lambda: "/frame-secret-ran" in site.snapshot(), timeout=8)
        await raw.send(cdp.NAVIGATE, {"url": site.url(ALLOWED_HOST, "/l11-a")})
        await _settle(lambda: "/l11-a" in site.snapshot(), timeout=8)
        await asyncio.sleep(0.5)
    finally:
        await raw.close()
    inner = await WebSocketCdpTransport.connect(page_ws)
    recorder = _RecordingTransport(inner)
    session = cdp.GatedCdpSession(recorder, resolver=_resolver)
    obs: dict = {"ran_unwatched": ran}
    try:
        await session.start()
        sel_rows.clear()
        recorder.methods.clear()
        recorder.sent.clear()
        recorder.events.clear()
        await CdpPageDriver(session.page_transport).go_back()
        await asyncio.sleep(1.5)
        await _settle(lambda: not session.tearing_down, timeout=5)
        await session.wait_idle()
        png = await _png(inner)
        obs["px"] = {
            "magenta": _count(png, MAGENTA, FRAME_RECT),
            "green": _count(png, GREEN, BOX_RECT),
        }
        obs["top"] = await _evaluate(inner, "location.href")
        obs["restored"] = any(
            m == cdp.FRAME_NAVIGATED
            and s is None
            and not (p.get("frame") or {}).get("parentId")
            and p.get("type") == "BackForwardCacheRestore"
            for m, p, s in recorder.events
        )
        obs["attached"] = [
            ((p.get("targetInfo") or {}).get("type"), p.get("waitingForDebugger"))
            for m, p, _s in recorder.events
            if m == cdp.ATTACHED_TO_TARGET
        ]
        obs["frame_blanks"] = [
            p["frameId"] for m, p in recorder.sent if m == cdp.NAVIGATE and "frameId" in p
        ]
        obs["stop_loading"] = recorder.count(cdp.STOP_LOADING)
        obs["top_blanks"] = recorder.sent.count((cdp.NAVIGATE, {"url": cdp.BLANK_URL}))
        obs["sel"] = [
            (r.operation, r.metadata.get("phase"), r.metadata.get("host")) for r in sel_rows
        ]
        obs["quarantine"] = session.quarantine_reason
    finally:
        await session.close()
        await inner.close()
    return obs


_CASES = {
    "l1": "/l1",
    "l2": "/l2",
    "l3": "/l3",
    "l4": "/l4",
    "l5": "/l5",
    "l6": "/l6",
    "l7": "/l7",
    "l9": "/l9",
    "l11": "/l11-b",
    "l11x": "/l11-x",
    "l13": "/l13-sw",
    "l14": "/doc.pdf",
    "l14-embed": "/l14-embed",
}

#: The cases that go back a page after the frame was blanked, then forward again. Each loads
#: ``/l11-a`` first, so there is a page to go back to.
_GO_BACK = ("l11", "l11x")


@pytest.fixture(scope="module")
def frames(browser, live) -> dict:
    """Every frame case on this browser, after the navigation scenario, one session each."""
    site = browser.site
    _frame_pages(site)

    async def run() -> dict:
        out: dict = {"variant": browser.variant}
        for name, path in _CASES.items():
            if name in _GO_BACK:
                out[f"{name}-a"] = await _case(
                    browser.page_ws, site, browser.sel_rows, f"{name}-a", "/l11-a", wait=0.8
                )
            if name == "l13":
                await _l13_prime(browser.page_ws, site)
            out[name] = await _case(
                browser.page_ws,
                site,
                browser.sel_rows,
                name,
                path,
                wait=3.0 if name == "l5" else 2.0,
            )
        out["l10"] = await _l10(browser.page_ws, site, browser.sel_rows)
        out["l11-cached"] = await _l11_cached(browser.page_ws, site, browser.sel_rows)
        out["l15"] = await _l15(browser.page_ws, site, browser.sel_rows)
        if browser.variant == "site-per-process":
            out["l12"] = await _l12(browser.page_ws, site)
        return out

    return asyncio.run(run())


def _isolated(frames: dict, case: dict) -> None:
    """On the site-isolated browser a cross-site frame must have run as a target of its own (the
    browser attached it); anything else means the browser kept it in the page's process and
    measured nothing. Read off the events, not the audit row: a frame blanked as its navigation
    started and again when it attached is one denial, recorded under the first."""
    if frames["variant"] == "site-per-process":
        assert "iframe" in case["attached"], (
            "the browser kept the frame in the page's process; nothing was measured: "
            f"{case['attached']}"
        )


def _blanked_alone(case: dict) -> None:
    assert case["nav_ok"], case
    assert case["frame_blanks"], f"no frame was blanked by its id: {case}"
    assert case["stop_loading"] == 0 and case["top_blanks"] == 0, case
    assert case["quarantine"] == "", case["quarantine"]
    assert case["top"].startswith(f"http://{ALLOWED_HOST}:"), case["top"]


@pytest.mark.timeout(130)
def test_a_denied_cross_site_frame_is_blanked_and_the_page_keeps_working(frames: dict) -> None:
    """🔴 Red before, on a site-isolated browser: the frame was never seen, stayed in the
    screenshot and took the click. On the other browser the whole page was torn down."""
    case = frames["l1"]
    _blanked_alone(case)
    _isolated(frames, case)
    assert case["px"]["magenta"] == 0 and case["px"]["green"] > 0, case["px"]
    assert "/frame-clicked-in-secret" not in case["hits"], case["hits"]
    assert "/clicked-allowed" in case["hits"], case["hits"]
    assert "about:blank" in case["tree"], case["tree"]
    # The honest limit: the frame's own document was asked for before it could be judged.
    assert "/frame-secret" in case["hits"], case["hits"]
    if frames["variant"] == "site-per-process":
        assert "/frame-secret-ran" not in case["hits"], "the denied frame's script ran"
        assert "/frame-secret-sub.png" not in case["hits"], "the denied frame loaded its images"


def _one_denial(case: dict, host: str, caught: set[tuple[str, str]]) -> None:
    """One frame of *host* denied once: one SEL row, recorded where it was caught first."""
    rows = [(op, phase) for op, phase, h in case["sel"] if h == host]
    assert len(rows) == 1 and rows[0] in caught, case["sel"]


@pytest.mark.timeout(130)
def test_a_denied_same_site_frame_is_blanked_as_it_starts_or_when_it_commits(frames: dict) -> None:
    """Blanked as its navigation starts, and again when it commits if that blank did not land
    first: one denial either way, recorded under the first."""
    case = frames["l2"]
    _blanked_alone(case)
    assert case["px"]["magenta"] == 0 and case["px"]["green"] > 0, case["px"]
    caught = {
        (cdp.FRAME_STARTED_NAVIGATING, "frame_started"),
        (cdp.FRAME_NAVIGATED, "frame_committed"),
    }
    _one_denial(case, SAME_SITE_DENIED, caught)


@pytest.mark.timeout(130)
def test_a_denied_frame_nested_in_an_allowed_frame_is_blanked(frames: dict) -> None:
    case = frames["l3"]
    _blanked_alone(case)
    _isolated(frames, case)
    assert case["px"]["blue"] > 0 and case["px"]["nested_magenta"] == 0, case["px"]
    if frames["variant"] == "site-per-process":
        assert "/frame-secret-ran" not in case["hits"], "the denied frame's script ran"


@pytest.mark.timeout(130)
def test_an_allowed_frame_nested_in_an_allowed_frame_runs(frames: dict) -> None:
    """Vacuity partner: watching nested frames must not cost an allowed one."""
    case = frames["l4"]
    assert case["blocks"] == [] and case["frame_blanks"] == [], case
    assert "/frame-allowed-ran" in case["hits"] and case["px"]["teal"] > 0, case


@pytest.mark.timeout(130)
def test_a_frame_that_moves_itself_to_a_denied_host_is_blanked(frames: dict) -> None:
    case = frames["l5"]
    _blanked_alone(case)
    # A frame of another site moving to a host of a third: no early blank, caught at its commit.
    committed = {
        (cdp.FRAME_NAVIGATED, "frame_committed"),
        (cdp.TARGET_INFO_CHANGED, "frame_committed"),
    }
    _one_denial(case, DENIED_HOST, committed)
    assert case["px"]["magenta"] == 0, case["px"]


@pytest.mark.timeout(130)
def test_a_dedicated_worker_runs_exactly_where_the_page_guard_does_not_reach(frames: dict) -> None:
    case = frames["l6"]
    present = [h for h in case["raw_hits"] if h.startswith("/worker-guard")]
    assert present, case["raw_hits"]
    guarded = present[0].endswith("present=1")
    assert ("/worker-ok" in case["hits"]) == (not guarded), case["raw_hits"]
    if frames["variant"] == "site-per-process":
        assert not guarded, "the frame shared its page's process, so nothing was measured"
    assert case["blocks"] == [], case["blocks"]


@pytest.mark.timeout(130)
def test_a_denied_frame_in_an_allowed_frames_process_is_blanked(frames: dict) -> None:
    case = frames["l7"]
    _blanked_alone(case)
    assert case["px"]["blue"] > 0 and case["px"]["nested_magenta"] == 0, case["px"]


@pytest.mark.timeout(130)
def test_an_allowed_frame_inside_a_sandboxed_frame_runs(frames: dict) -> None:
    case = frames["l9"]
    assert case["blocks"] == [] and case["frame_blanks"] == [], case
    assert "/frame-allowed-ran" in case["hits"], case["hits"]


@pytest.mark.timeout(130)
def test_a_denied_frame_removed_or_re_pointed_never_costs_the_page(frames: dict) -> None:
    case = frames["l10"]
    assert case["tops"] == ["/l10-removed"] * 3 + ["/l10-dest"] * 3, case
    assert case["quarantine"] == "", case


@pytest.mark.timeout(130)
def test_going_back_after_a_frame_was_blanked_leaves_the_page(frames: dict) -> None:
    case = frames["l11"]
    _blanked_alone(case)
    assert case["after_back"].endswith("/l11-a"), case["after_back"]
    assert case["forward_magenta"] == 0, "going forward showed the denied frame"


@pytest.mark.timeout(130)
def test_going_back_after_a_cross_site_frame_was_blanked_leaves_the_page(frames: dict) -> None:
    """The same with a frame from another site, which the site-isolated browser runs in a process
    of its own and blanks before it loads: its blank is a history entry of the frame's own too."""
    case = frames["l11x"]
    _blanked_alone(case)
    _isolated(frames, case)
    assert case["after_back"].endswith("/l11-a"), case["after_back"]
    assert case["forward_magenta"] == 0, "going forward showed the denied frame"


@pytest.mark.timeout(130)
def test_a_page_back_from_the_cache_with_a_denied_frame_live_has_it_blanked(frames: dict) -> None:
    """A page that went into the back-forward cache with a denied frame from another site still
    running comes back with that frame blanked by its id, and the page kept. Restored from the
    cache, the frame in a process of its own is attached again already running, which is when it
    is judged; a browser that keeps no cache loads the page again, and judges the frame as a new
    one."""
    case = frames["l11-cached"]
    assert case["ran_unwatched"], "the frame never ran before the session; nothing was measured"
    assert case["top"].endswith("/l11-cached"), case["top"]
    assert case["px"]["magenta"] == 0 and case["px"]["green"] > 0, case["px"]
    assert case["frame_blanks"], f"no frame was blanked by its id: {case}"
    assert case["stop_loading"] == 0 and case["top_blanks"] == 0, case
    assert case["quarantine"] == "", case["quarantine"]
    if case["restored"]:
        assert ("iframe", False) in case["attached"], case["attached"]
        assert (cdp.ATTACHED_TO_TARGET, "frame_attached", DENIED_HOST) in case["sel"], case["sel"]
    elif frames["variant"] == "site-per-process":
        assert ("iframe", True) in case["attached"], case["attached"]


@pytest.mark.timeout(130)
def test_going_back_past_the_page_a_teardown_left_never_asks_its_host_again(frames: dict) -> None:
    """🔴 Red before: GO_BACK loaded the denied entry the teardown left behind, so its host was
    asked for the document again, the session tore it down again, and the run was back on the
    blank. Judged before it is loaded, the denied entry is skipped and the page before it loads.
    The server's ledger is the oracle: no request for the denied document after going back."""
    case = frames["l15"]
    assert case["torn_down"], "the denied document was never torn down; nothing was measured"
    assert case["before_back"] == cdp.BLANK_URL, case["before_back"]
    assert case["top"].startswith(f"http://{ALLOWED_HOST}:"), case["top"]
    assert case["top"].endswith("/l11-a"), case["top"]
    assert "/secret" not in case["hits"], case["hits"]
    assert ("Page.navigateToHistoryEntry", "preflight", DENIED_HOST) in case["sel"], case["sel"]
    assert case["quarantine"] == "", case["quarantine"]


@pytest.mark.timeout(130)
def test_a_busy_frame_holds_neither_a_new_frame_nor_a_read(frames: dict) -> None:
    if frames["variant"] != "site-per-process":
        assert "l12" not in frames
        return
    case = frames["l12"]
    assert case["third_s"] is not None and case["third_s"] < 1.5, case
    assert case["read_s"] < 1.5, case
    assert case["q"] == "", case


@pytest.mark.timeout(130)
def test_the_run_tab_never_holds_a_service_worker_and_lets_worklets_run(frames: dict) -> None:
    case = frames["l13"]
    assert "/sw-controls" in case["hits"], "the service worker never controlled its page"
    assert "service_worker" not in case["attached"], case["attached"]
    assert "/paint-ok" in case["hits"], case["hits"]


@pytest.mark.timeout(130)
def test_a_pdf_and_an_embedded_pdf_are_shown(frames: dict) -> None:
    case, embed = frames["l14"], frames["l14-embed"]
    if case["pdf_viewer"]:
        assert case["px"]["red"] > 0 and embed["px"]["red"] > 0, (case["px"], embed["px"])
    else:
        # The headless shell has no viewer: nothing could be blanked, and the browser says so.
        assert case["pdf_viewer"] is False, case
    assert case["blocks"] == [] and embed["blocks"] == [], (case["blocks"], embed["blocks"])
    assert case["quarantine"] == "" and embed["quarantine"] == ""
