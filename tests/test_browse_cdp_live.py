"""BA-2 live: the gate, the PRODUCTION transport, and a real browser.

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
would be a lie, and the plan's §6.3 already says so.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from personalclaw.config.loader import AppConfig
from personalclaw.net import policy as net_policy

ALLOWED_HOST = "allowed.local"
DENIED_HOST = "denied.local"

#: Named once so the skip/fail message says which proof stopped running.
PROOF = "LIVE PROOF"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolver(host: str) -> list[str]:
    """Both names resolve to the loopback server, so only POLICY can tell them apart."""
    if host in (ALLOWED_HOST, DENIED_HOST):
        return ["127.0.0.1"]
    raise socket.gaierror(f"no fake DNS entry for {host!r}")


# ── the site ──────────────────────────────────────────────────────────────────


class _Site:
    """A real loopback origin that counts real requests."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.hits: list[str] = []
        self._lock = threading.Lock()
        site = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:  # keep pytest output clean
                return

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
                with site._lock:
                    site.hits.append(self.path)
                body = site.body_for(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.hits)


# ── the browser ───────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _browser(chrome: str):
    """Launch a headless browser and yield ONE page target's WebSocket URL.

    Launching lives in the test on purpose: ``browse/transport.py`` deliberately owns no
    process, because per-site persistent profiles are BA-4's scope and §4.1 has not settled
    whether the gateway shares the interactive MCP's browser.
    """
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="ba2-live-profile-")
    # Chrome's own stderr, kept so a launch that never reaches CDP reports its real cause
    # rather than only the generic poll timeout. On GitHub's ubuntu-latest a headless launch
    # without --no-sandbox aborts at startup, and DEVNULL used to swallow that fatal line.
    stderr_log = tempfile.NamedTemporaryFile(prefix="ba2-live-stderr-", suffix=".log")
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
            # The two names the policy distinguishes; everything else is unreachable, so a
            # guard regression cannot dial a real host from this suite.
            "--host-resolver-rules="
            f"MAP {ALLOWED_HOST} 127.0.0.1,MAP {DENIED_HOST} 127.0.0.1,MAP * ~NOTFOUND",
            # chrome-headless-shell opens NO page target unless given a URL, and the browser
            # -level endpoint would then need a sessionId on every Page.* message.
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr_log,
    )
    try:
        page_ws = None
        deadline = time.time() + 30
        while time.time() < deadline and page_ws is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as r:
                    for target in json.load(r):
                        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                            page_ws = target["webSocketDebuggerUrl"]
                            break
            except Exception:
                pass
            if page_ws is None:
                time.sleep(0.2)
        if page_ws is None:
            stderr_log.seek(0)
            detail = stderr_log.read().decode("utf-8", "replace").strip()
            raise AssertionError(
                "chrome never exposed a page target over CDP; its stderr was:\n"
                + (detail or "(chrome wrote nothing to stderr)")
            )
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
    """Wraps the production transport so the wire is observable without changing it."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.methods: list[str] = []

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.methods.append(method)
        return await self.inner.send(method, params)  # type: ignore[attr-defined]

    def set_event_listener(self, listener: object) -> None:
        self.inner.set_event_listener(listener)  # type: ignore[attr-defined]

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
        await asyncio.sleep(0.75)  # let the teardown's two messages complete
        obs["redirect_denied"] = {
            "guard_fired": fired,
            "blocked_url": session.blocks[-1].url if session.blocks else "",
            "blocked_host": session.blocks[-1].host if session.blocks else "",
            "sel": [
                (row.operation, row.metadata.get("phase"), row.metadata.get("host"))
                for row in sel_rows
            ],
            "wire_tail": recorder.methods[-2:],
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
    return obs


@pytest.fixture(scope="module")
def live() -> dict:
    """One browser, one socket, the whole scenario — the launch is paid once.

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
        deny_hosts=[DENIED_HOST],
        allow_private=True,
    )
    fake_app = types.SimpleNamespace(security=types.SimpleNamespace(egress=egress))
    remembered = net_policy._LAST_DENY_HOSTS
    try:
        with (
            mock.patch.object(AppConfig, "load", classmethod(lambda cls: fake_app)),
            mock.patch.object(cdp, "SecurityEventLog", lambda: _Recorder()),
            _Site() as site,
            _browser(chrome) as page_ws,
        ):
            return asyncio.run(_scenario(page_ws, site, sel_rows))
    finally:
        net_policy._LAST_DENY_HOSTS = remembered


# ── the clauses ───────────────────────────────────────────────────────────────


def test_the_guard_script_is_installed_before_anything_navigates(live: dict) -> None:
    """Start's wire, from a real browser: enable then inject, and nothing else."""
    assert live["start_wire"] == [cdp.PAGE_ENABLE, cdp.ADD_SCRIPT]


def test_the_session_really_injected_the_real_safety_script(live: dict) -> None:
    """Clause 3 at the SESSION's call site: the guard the session injects is the shipped
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
        "order matters: blanking before stopping races the in-flight load; "
        f"wire was {red['wire_tail']}"
    )


def test_the_redirect_request_itself_already_left_the_browser(live: dict) -> None:
    """The honest limit, asserted so nobody later mistakes the teardown for prevention.

    A client-side redirect is dispatched by the page; ``Page.frameNavigated`` is the browser
    telling us it already happened. The guard's reach is the DOM, not the socket — §6.3.
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
