"""BA-2 §3: the per-page safety script, proved in a real browser.

Two layers, both load-bearing:

**Content/structure** (always runs) — the builder's escaping, allow-list baking and default
agreement. These are string assertions and they are *honest about being string assertions*:
none of them can tell you the guard works.

**Real execution** (skipped only when no Chromium binary is present) — the clause
*"the injected safety script makes a test page's fetch() / media.play() / navigator.bluetooth
throw or return blocked"* is a behavioural claim, so it is proved behaviourally: a real
``chrome-headless-shell`` driven over raw CDP (Playwright's Python package is deliberately NOT
installed here), with the script injected exactly as production will inject it, through
``Page.addScriptToEvaluateOnNewDocument``.

Three design decisions in here exist because the obvious test would have been vacuous:

1. **A local HTTP server is the network oracle, and there is a BASELINE run.** "The promise
   rejected" does not prove no packet left. So every run drives a page served from a local
   ``http.server`` and counts server-side hits on ``/beacon``. The uninjected baseline run must
   record hits — that is the positive control proving the harness can *see* a network reach.
   Only then does "zero hits under injection" mean anything.
2. **Whether ``navigator.bluetooth === undefined`` is vacuous depends on the page's ORIGIN.**
   Web Bluetooth &c. are ``[SecureContext]``: measured on a ``data:`` URL, of
   bluetooth/usb/serial/hid/mediaDevices/xr/clipboard *none* exist, so that assertion would
   pass with zero guard installed; measured on this suite's ``http://127.0.0.1`` origin
   (localhost is "potentially trustworthy", hence a secure context) all eight natively exist as
   *configurable accessors*. The assertion therefore reads the property DESCRIPTOR our guard
   installs — a non-writable, **non-configurable data** property on ``Navigator.prototype`` —
   which is what neither native state looks like, on either origin.
3. **Errors are asserted by IDENTITY, not by "it threw".** ``play()`` already rejects headlessly
   when autoplay is blocked, and a ``fetch`` to an unresolvable host already rejects with
   ``TypeError``. Only ``err.name === "PersonalClawBlockedError"`` distinguishes *our* guard
   from the browser refusing on its own.

The browser is launched with ``--host-resolver-rules=MAP * ~NOTFOUND`` so that a guard
regression cannot reach any real site from this suite; the local server is the only reachable
origin.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import http.server
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import pytest

from personalclaw.browse.safety_script import (
    GUARD_STEPS,
    GUARDED_NAVIGATOR_KEYS,
    SAFETY_MARKER,
    SAFETY_SCRIPT,
    safety_script,
)

# --------------------------------------------------------------------------------------------
# Layer 1: content / structure. No browser.
# --------------------------------------------------------------------------------------------

#: A host built to break out of every embedding we might ever put the script in.
HOSTILE_HOST = "ev\"il'\\x.test</script><script>alert(1)</script>"


def _baked_allow_literal(script: str) -> str:
    """The JS array literal ``safety_script`` substituted for its allow-hosts placeholder."""
    prefix = "var ALLOW_HOSTS = "
    start = script.index(prefix) + len(prefix)
    return script[start : script.index(";", start)]


def test_default_script_and_builder_agree() -> None:
    assert SAFETY_SCRIPT == safety_script()
    assert safety_script() == safety_script(allow_hosts=())


def test_script_names_every_api_it_claims_to_guard() -> None:
    """Each guarded surface is actually mentioned in the emitted source.

    This is a *necessary* condition, not a sufficient one — see the behavioural tests. It
    catches the specific regression of a guard being dropped from the template wholesale.
    """
    for api in (
        "fetch",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "Worker",
        "SharedWorker",
        "RTCPeerConnection",
        "serviceWorker",
        "HTMLMediaElement",
        "play",
    ):
        assert api in SAFETY_SCRIPT, f"guard for {api} is missing from the script"
    for key in GUARDED_NAVIGATOR_KEYS:
        assert f'"{key}"' in SAFETY_SCRIPT, f"navigator.{key} is not in the hardened key list"
    assert "bluetooth" in GUARDED_NAVIGATOR_KEYS


def test_guards_are_non_writable_and_non_configurable() -> None:
    """The re-assignment defence is a property descriptor, so assert the descriptor is asked for."""
    assert "writable: false" in SAFETY_SCRIPT
    assert "configurable: false" in SAFETY_SCRIPT
    assert "Object.defineProperty" in SAFETY_SCRIPT or "_defineProperty" in SAFETY_SCRIPT


def test_script_is_wrapped_so_injection_cannot_throw() -> None:
    """An injected script that raises leaves the page unguarded while looking injected.

    The behavioural half of this claim is ``test_injection_leaves_the_page_guarded``: this one
    only asserts the outer wrapper is structurally present.
    """
    lines = SAFETY_SCRIPT.splitlines()
    in_comment = False
    first_statement = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if in_comment:
            if "*/" in stripped:
                in_comment = False
            continue
        if stripped.startswith("/*"):
            in_comment = "*/" not in stripped
            continue
        first_statement = stripped
        break
    assert first_statement == "try {", f"the script's first statement is {first_statement!r}"
    assert "} catch (err) {" in SAFETY_SCRIPT, "the outer swallow is gone"
    assert SAFETY_SCRIPT.rstrip().endswith("}")
    assert SAFETY_MARKER in SAFETY_SCRIPT


def test_allow_hosts_are_baked_in_normalised() -> None:
    script = safety_script(allow_hosts=("Example.COM", "  cdn.example.com  ", "example.com", ""))
    assert '["example.com", "cdn.example.com"]' in script
    assert "Example.COM" not in script


def test_empty_allow_list_denies_everything() -> None:
    assert "var ALLOW_HOSTS = [];" in SAFETY_SCRIPT
    # The deny-by-default branch: an empty list short-circuits before any URL parsing.
    assert "if (!ALLOW_HOSTS.length) {" in SAFETY_SCRIPT


def test_hostile_allow_host_is_escaped_and_cannot_break_out() -> None:
    """An unescaped allow-list value would be a script-injection bug in our own tooling."""
    script = safety_script(allow_hosts=(HOSTILE_HOST,))

    # The dangerous SYNTAX must not survive anywhere in the emitted source. Note the
    # assertion is deliberately about syntax, not about the payload text: `alert(1)` is
    # inert *inside* a quoted JS string literal, so demanding its absence would be demanding
    # the wrong property — the bug being tested for is breaking OUT of the literal.
    assert "</script>" not in script
    assert "<script>" not in script
    assert '"ev"' not in script, "the value's own quote escaped the literal"

    # Scoped to what is actually measured: the baked literal carries no character that could
    # terminate a string, a statement or an enclosing HTML element.
    literal = _baked_allow_literal(script)
    for dangerous in ("<", ">", "&", "\n", "\r"):
        assert dangerous not in literal, f"{dangerous!r} survived into the baked allow-list"

    # And the value must still round-trip, so the escaping is not just deletion.
    assert json.loads(literal) == [HOSTILE_HOST.lower()]


def test_hostile_host_survives_html_script_embedding() -> None:
    """Escaping <, > and & keeps the script safe even inlined into a <script> element."""
    script = safety_script(allow_hosts=(HOSTILE_HOST,))
    literal = _baked_allow_literal(script)
    assert "\\u003c" in literal and "\\u003e" in literal
    assert "</" not in literal


def test_bare_string_allow_hosts_is_rejected() -> None:
    """``allow_hosts="example.com"`` would otherwise become eleven single-character hosts."""
    with pytest.raises(TypeError):
        safety_script(allow_hosts="example.com")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        safety_script(allow_hosts=(1234,))  # type: ignore[arg-type]


def test_no_implicit_same_origin_carve_out() -> None:
    """The page's own origin is the one origin we know we do not control."""
    assert "NO implicit same-origin carve-out" in SAFETY_SCRIPT


def test_module_import_pulls_in_no_http_client() -> None:
    """The browse package invariant: this module is pure, with no network dependency at all.

    Measured by loading the file *in isolation* (``spec_from_file_location``), not via
    ``import personalclaw.browse.safety_script``. That is not a dodge, it is the only way to
    measure THIS module: importing it through the package first runs
    ``personalclaw/browse/__init__.py``, whose BA-1 chain reaches
    ``extraction.py`` -> ``personalclaw.knowledge.connectors.base``, and *that* closure
    contains ``httpx``/``urllib3``/``http.client``. So the package-level import is already not
    HTTP-client-free on ``main``; asserting on it here would measure BA-1's dependency, not
    this atom's. What is in scope, and what this asserts, is that safety_script.py adds
    nothing and depends on no ``personalclaw`` module whatsoever.
    """
    import importlib.util
    import sys

    module_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src",
        "personalclaw",
        "browse",
        "safety_script.py",
    )
    spec = importlib.util.spec_from_file_location("_ba2_isolated_safety_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    before = set(sys.modules)
    try:
        spec.loader.exec_module(module)
        added = set(sys.modules) - before
    finally:
        sys.modules.pop("_ba2_isolated_safety_script", None)

    network = {"aiohttp", "httpx", "requests", "urllib3", "websockets", "http.client", "ssl"}
    assert not (added & network), f"safety_script's own import closure reaches {added & network}"
    assert not {name for name in added if name.startswith("personalclaw")}, (
        "safety_script imported a personalclaw module, so it can inherit a network dependency: "
        f"{sorted(name for name in added if name.startswith('personalclaw'))}"
    )
    # Loading it with no package context still produces a usable script — proof the isolation
    # above is real and not an artifact of a partially-initialised module.
    assert module.SAFETY_SCRIPT == SAFETY_SCRIPT


# --------------------------------------------------------------------------------------------
# Layer 2: real execution. A live browser, over raw CDP.
# --------------------------------------------------------------------------------------------

_CHROME_CANDIDATES = (
    os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium_headless_shell-1234"
        "/chrome-headless-shell-mac-arm64/chrome-headless-shell"
    ),
    os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-1234"
        "/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium"
    ),
)

#: Names our guard uses for its rejections/throws. Asserting on this — not merely on "it
#: threw" — is what separates our guard from the browser refusing on its own.
BLOCKED_ERROR = "PersonalClawBlockedError"


def _chrome_path() -> str | None:
    for candidate in _CHROME_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _silent_wav_data_uri() -> str:
    """A 0.1s 8-bit mono WAV, so media.play() has a REAL decodable source.

    Without a playable source ``play()`` rejects on its own (``NotSupportedError``) and the
    baseline stops being a positive control. A ``data:`` URI keeps the media test network-free.
    """
    frames = b"\x80" * 800
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(frames))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
        + b"data"
        + struct.pack("<I", len(frames))
    )
    return "data:audio/wav;base64," + base64.b64encode(header + frames).decode("ascii")


_PAGE_HTML = (
    "<!doctype html><html><head><title>BA-2 safety probe</title></head>"
    "<body><h1>probe</h1></body></html>"
).encode("ascii")

#: Runs in a Worker realm — a realm addScriptToEvaluateOnNewDocument never reaches. If the
#: Worker constructor guard fails, THIS is what silently reaches the network.
_WORKER_JS = b"self.fetch('/beacon?via=worker').catch(function () {});"


class _LocalSite:
    """The network oracle: a real origin, counting real server-side hits."""

    def __init__(self) -> None:
        self.hits: collections.Counter[str] = collections.Counter()
        self._lock = threading.Lock()
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:  # noqa: A003 - stdlib hook
                pass

            def _respond(self, status: int, body: bytes = b"", ctype: str = "text/plain") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                # So CORS can never mask a hit we are trying to observe.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _route(self) -> None:
                path, _, query = self.path.partition("?")
                if path == "/beacon":
                    via = "unknown"
                    for part in query.split("&"):
                        if part.startswith("via="):
                            via = part[4:]
                    site.record(via)
                    self._respond(200, b"beacon")
                elif path == "/page":
                    self._respond(200, _PAGE_HTML, "text/html; charset=utf-8")
                elif path == "/worker.js":
                    self._respond(200, _WORKER_JS, "text/javascript")
                else:
                    self._respond(404, b"nope")

            def do_GET(self) -> None:  # noqa: N802 - stdlib hook
                self._route()

            def do_POST(self) -> None:  # noqa: N802 - stdlib hook (sendBeacon posts)
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self._route()

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request: object, client_address: object) -> None:
                # Chrome tears sockets down abruptly on shutdown. A reset is not a failure,
                # but the stdlib's default traceback on stderr reads exactly like one.
                if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
                    return
                super().handle_error(request, client_address)  # type: ignore[arg-type]

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def record(self, via: str) -> None:
        with self._lock:
            self.hits[via] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.hits)

    def reset(self) -> None:
        with self._lock:
            self.hits.clear()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Cdp:
    """A minimal raw-CDP client: `websockets` is in the venv, `playwright` is not."""

    def __init__(self, ws: object) -> None:
        self._ws = ws
        self._next_id = 0

    async def send(self, method: str, params: dict | None = None, session: str | None = None):
        self._next_id += 1
        message: dict = {"id": self._next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        await self._ws.send(json.dumps(message))  # type: ignore[attr-defined]
        while True:
            raw = json.loads(await self._ws.recv())  # type: ignore[attr-defined]
            if raw.get("id") == self._next_id:
                if "error" in raw:
                    raise AssertionError(f"CDP {method} failed: {raw['error']}")
                return raw.get("result", {})


#: Runs INSIDE the page. Exercises every guarded surface and reports what happened, so the
#: Python side can compare it against the server's own hit ledger.
_PROBE_JS = """
(async () => {
  const out = {marker: null, errors: {}};
  const name = (e) => (e && e.name) || String(e);

  try {
    out.marker = window.__personalclawSafety
      ? JSON.parse(JSON.stringify(window.__personalclawSafety))
      : null;
  } catch (e) { out.marker = "unreadable"; }

  // --- fetch: to our own origin (the load-bearing probe) and to a real host (the clause).
  try { const r = await fetch('/beacon?via=fetch'); out.fetch = {ok: true, status: r.status}; }
  catch (e) { out.fetch = {ok: false, name: name(e), message: String(e.message)}; }

  try { await fetch('https://example.com'); out.fetchExternal = {ok: true}; }
  catch (e) { out.fetchExternal = {ok: false, name: name(e), message: String(e.message)}; }

  // --- XMLHttpRequest
  try {
    await new Promise((res, rej) => {
      const x = new XMLHttpRequest();
      x.onload = () => res(); x.onerror = () => rej(new Error('xhr-network-error'));
      x.open('GET', '/beacon?via=xhr'); x.send();
    });
    out.xhr = {ok: true};
  } catch (e) { out.xhr = {ok: false, name: name(e), message: String(e.message)}; }

  // --- WebSocket / EventSource: the handshake GET reaches the server even when it fails.
  try { new WebSocket('ws://' + location.host + '/beacon?via=ws'); out.ws = {ok: true}; }
  catch (e) { out.ws = {ok: false, name: name(e)}; }

  try { new EventSource('/beacon?via=es'); out.es = {ok: true}; }
  catch (e) { out.es = {ok: false, name: name(e)}; }

  // --- sendBeacon: returns a boolean, never throws.
  try { out.beacon = {ok: navigator.sendBeacon('/beacon?via=beacon', 'x')}; }
  catch (e) { out.beacon = {ok: false, name: name(e)}; }

  // --- Worker: a realm the injection never reaches, so the constructor is the chokepoint.
  try { new Worker('/worker.js'); out.worker = {ok: true}; }
  catch (e) { out.worker = {ok: false, name: name(e)}; }

  try { new RTCPeerConnection(); out.rtc = {ok: true}; }
  catch (e) { out.rtc = {ok: false, name: name(e)}; }

  // --- media.play() on a REAL decodable source.
  try {
    const el = document.createElement('audio');
    el.muted = true;
    el.src = %(WAV)s;
    await el.play();
    out.play = {ok: true};
  } catch (e) { out.play = {ok: false, name: name(e), message: String(e.message)}; }

  // --- device APIs: the DESCRIPTOR, because the APIs are natively absent here.
  out.navigator = {};
  for (const key of %(KEYS)s) {
    const d = Object.getOwnPropertyDescriptor(Navigator.prototype, key);
    out.navigator[key] = d === undefined ? null : {
      isData: !('get' in d) || d.get === undefined,
      valueIsUndefined: d.value === undefined,
      writable: d.writable === true,
      configurable: d.configurable === true
    };
  }
  out.bluetoothReachable = typeof navigator.bluetooth !== 'undefined';
  try {
    out.bluetoothViaReflect =
      typeof Reflect.get(Navigator.prototype, 'bluetooth', navigator);
  } catch (e) { out.bluetoothViaReflect = 'threw:' + name(e); }

  // --- re-assignment: can a page put the real fetch back?
  const guardedFetch = window.fetch;
  try { window.fetch = function () { return Promise.resolve('escaped'); }; } catch (e) {}
  let deleted;
  try { deleted = delete window.fetch; } catch (e) { deleted = 'threw'; }
  let redefined = 'no';
  try {
    Object.defineProperty(window, 'fetch', {value: function () {}, configurable: true});
    redefined = 'yes';
  } catch (e) { redefined = 'threw:' + name(e); }
  out.reassign = {
    survivedAssignment: window.fetch === guardedFetch,
    deleteReturned: deleted,
    redefine: redefined
  };

  // --- child frames: a fresh realm is a fresh, unguarded copy of everything.
  async function frameProbe(kind) {
    try {
      const f = document.createElement('iframe');
      if (kind === 'src') { f.src = '/page'; }
      else if (kind === 'srcdoc') { f.srcdoc = '<p>child</p>'; }
      document.body.appendChild(f);
      await new Promise((r) => { f.onload = r; setTimeout(r, 1500); });
      const w = f.contentWindow;
      const guarded = !!w.__personalclawSafety;
      let fetchResult;
      try {
        await w.fetch('/beacon?via=iframe-' + kind);
        fetchResult = 'reached';
      } catch (e) { fetchResult = name(e); }
      return {guarded: guarded, fetch: fetchResult};
    } catch (e) { return {error: name(e) + ': ' + String(e.message)}; }
  }
  out.frameSrc = await frameProbe('src');
  out.frameBlank = await frameProbe('blank');
  out.frameSrcdoc = await frameProbe('srcdoc');

  return JSON.stringify(out);
})()
"""


async def _run_probe(chrome: str, site: _LocalSite, script: str | None) -> dict:
    """Drive one page: optionally inject ``script``, load the local page, run the probe."""
    import websockets

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="ba2-safety-profile-")
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--headless",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--autoplay-policy=no-user-gesture-required",
            # A guard regression must not be able to reach any REAL site from this suite.
            # The local server is deliberately the only reachable origin.
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1
                ) as response:
                    ws_url = json.load(response)["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.2)
        assert ws_url, "chrome never exposed a CDP endpoint"

        async with websockets.connect(ws_url, max_size=None) as ws:
            cdp = _Cdp(ws)
            target = await cdp.send("Target.createTarget", {"url": "about:blank"})
            attached = await cdp.send(
                "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
            )
            session = attached["sessionId"]
            await cdp.send("Page.enable", session=session)
            await cdp.send("Runtime.enable", session=session)

            if script is not None:
                added = await cdp.send(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": script},
                    session=session,
                )
                assert added.get("identifier"), "CDP refused the safety script"

            await cdp.send("Page.navigate", {"url": f"{site.origin}/page"}, session=session)
            await asyncio.sleep(0.6)

            probe = _PROBE_JS % {
                "WAV": json.dumps(_silent_wav_data_uri()),
                "KEYS": json.dumps(list(GUARDED_NAVIGATOR_KEYS)),
            }
            result = await cdp.send(
                "Runtime.evaluate",
                {
                    "expression": probe,
                    "awaitPromise": True,
                    "returnByValue": True,
                    "timeout": 30000,
                },
                session=session,
            )
            if "exceptionDetails" in result:
                raise AssertionError(f"probe threw in-page: {result['exceptionDetails']}")
            # Let the async paths (WebSocket/EventSource/Worker/beacon) reach the server.
            await asyncio.sleep(1.5)
            return json.loads(result["result"]["value"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


@pytest.fixture(scope="module")
def browser_runs() -> dict:
    """One browser, two runs: an uninjected BASELINE and the injected run.

    The baseline is not decoration. Without it, "zero server hits under injection" is
    indistinguishable from a harness that never reached the network at all.
    """
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip(
            "BEHAVIOURAL PROOF NOT RUN: no Chromium binary found at "
            f"{_CHROME_CANDIDATES[0]} (nor the full Chromium). The safety script's whole "
            "value is behavioural, so the content tests above do NOT substitute for this."
        )
    try:
        import websockets  # noqa: F401
    except ImportError:  # pragma: no cover - environment guard
        pytest.skip(
            "BEHAVIOURAL PROOF NOT RUN: no `websockets` in the venv and `playwright` is "
            "absent, so there is no way to speak CDP."
        )

    site = _LocalSite()
    try:
        site.reset()
        baseline = asyncio.run(_run_probe(chrome, site, None))
        baseline_hits = site.snapshot()

        site.reset()
        guarded = asyncio.run(_run_probe(chrome, site, SAFETY_SCRIPT))
        guarded_hits = site.snapshot()
    finally:
        site.close()

    return {
        "baseline": baseline,
        "baseline_hits": baseline_hits,
        "guarded": guarded,
        "guarded_hits": guarded_hits,
    }


def test_harness_can_observe_a_network_reach(browser_runs: dict) -> None:
    """POSITIVE CONTROL. Everything below is meaningless if this does not hold.

    Uninjected, the page's own fetch/XHR/beacon must actually hit the local server. This is
    the assertion that makes "zero hits" evidence rather than an artifact.
    """
    hits = browser_runs["baseline_hits"]
    assert browser_runs["baseline"]["marker"] is None, "baseline run was accidentally injected"
    assert hits.get("fetch", 0) >= 1, f"harness never saw a fetch reach the server: {hits}"
    assert hits.get("xhr", 0) >= 1, f"harness never saw an XHR reach the server: {hits}"
    assert sum(hits.values()) >= 3, f"baseline reached the server too rarely to trust: {hits}"


def test_injection_leaves_the_page_guarded(browser_runs: dict) -> None:
    """The marker is the only proof that a swallowed injection error did not happen."""
    marker = browser_runs["guarded"]["marker"]
    assert marker is not None, (
        "window.__personalclawSafety is absent: the script threw during injection and the "
        "page is UNGUARDED while looking injected"
    )
    assert marker["failed"] == [], f"guards failed to install: {marker['failed']}"
    installed = set(marker["applied"]) | set(marker["skipped"])
    assert installed == set(GUARD_STEPS), f"guard steps drifted: {installed ^ set(GUARD_STEPS)}"
    # Everything except the APIs this engine genuinely does not expose must be APPLIED.
    assert set(marker["applied"]) >= {
        "fetch",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "workers",
        "peerConnection",
        "media",
        "deviceApis",
    }, f"a guard silently degraded to skipped: {marker}"
    assert marker["allowHosts"] == []


def test_fetch_is_blocked_and_never_reaches_the_network(browser_runs: dict) -> None:
    """The clause's fetch() leg, proved on BOTH sides: the page's view and the wire."""
    guarded = browser_runs["guarded"]
    assert guarded["fetch"]["ok"] is False
    assert guarded["fetch"]["name"] == BLOCKED_ERROR, guarded["fetch"]
    assert browser_runs["guarded_hits"].get("fetch", 0) == 0, (
        "fetch() was rejected in the page but the request still reached the server: "
        f"{browser_runs['guarded_hits']}"
    )


def test_fetch_to_an_external_host_is_blocked_by_our_guard(browser_runs: dict) -> None:
    """Asserting the error IDENTITY, not just rejection: DNS failure also rejects."""
    external = browser_runs["guarded"]["fetchExternal"]
    assert external["ok"] is False
    assert external["name"] == BLOCKED_ERROR, (
        "fetch('https://example.com') rejected, but not from our guard — this test would "
        f"pass vacuously on a DNS failure: {external}"
    )


def test_every_other_network_path_is_blocked(browser_runs: dict) -> None:
    """fetch alone is not the network. Each of these is an independent way out."""
    guarded = browser_runs["guarded"]
    hits = browser_runs["guarded_hits"]

    assert guarded["xhr"]["ok"] is False
    assert guarded["xhr"]["name"] == BLOCKED_ERROR, guarded["xhr"]
    assert guarded["ws"]["ok"] is False and guarded["ws"]["name"] == BLOCKED_ERROR
    assert guarded["es"]["ok"] is False and guarded["es"]["name"] == BLOCKED_ERROR
    assert guarded["beacon"]["ok"] is False, "sendBeacon reported success"
    assert guarded["worker"]["ok"] is False and guarded["worker"]["name"] == BLOCKED_ERROR
    assert guarded["rtc"]["ok"] is False and guarded["rtc"]["name"] == BLOCKED_ERROR

    for via in ("fetch", "xhr", "ws", "es", "beacon", "worker"):
        assert hits.get(via, 0) == 0, f"{via} reached the server despite the guard: {hits}"


def test_worker_realm_cannot_reach_the_network(browser_runs: dict) -> None:
    """The worker guard's whole justification: a Worker is a realm we never inject into.

    The baseline proves the worker really would have fetched, so the guarded zero is a
    measurement of the constructor guard and not of a broken worker script.
    """
    assert browser_runs["baseline_hits"].get("worker", 0) >= 1, (
        "the worker never reached the server even UNGUARDED, so the guarded run proves "
        f"nothing about workers: {browser_runs['baseline_hits']}"
    )
    assert browser_runs["guarded_hits"].get("worker", 0) == 0


def test_media_play_rejects(browser_runs: dict) -> None:
    """The clause's media.play() leg — asserted by error identity, not by "it rejected"."""
    baseline = browser_runs["baseline"]["play"]
    guarded = browser_runs["guarded"]["play"]
    assert baseline.get("name") != BLOCKED_ERROR, "baseline play() hit our guard"
    assert guarded["ok"] is False
    assert guarded["name"] == BLOCKED_ERROR, (
        "play() rejected, but not from our guard — headless autoplay policy rejects too, so "
        f"this must be checked by identity: {guarded}"
    )


def test_navigator_device_apis_are_unreachable_and_hard_defined(browser_runs: dict) -> None:
    """The clause's navigator.bluetooth leg.

    Whether ``navigator.bluetooth === undefined`` is a vacuous assertion depends on the page's
    ORIGIN, which is a trap worth spelling out. Web Bluetooth &c. are ``[SecureContext]``, so:

    * on a ``data:`` URL or plain-http page the attribute does not exist at all and
      ``=== undefined`` passes with **zero guard installed**;
    * on this suite's ``http://127.0.0.1`` origin — localhost is "potentially trustworthy", so
      it *is* a secure context — the attribute natively exists.

    Rather than depend on either, the load-bearing assertion is on the property DESCRIPTOR our
    guard installs: a non-writable, **non-configurable data** property. Natively these are
    *configurable accessors* (measured), so the two states can never be confused. The baseline
    is asserted to be distinguishable, which is what keeps the guarded assertions honest.
    """
    baseline = browser_runs["baseline"]
    for key, descriptor in baseline["navigator"].items():
        if descriptor is None:
            continue  # natively absent (insecure-context origins) — nothing to distinguish
        assert descriptor["isData"] is False or descriptor["configurable"] is True, (
            f"navigator.{key} natively looks EXACTLY like our guard's descriptor, so the "
            "guarded assertion below would be vacuous"
        )
    assert baseline["bluetoothReachable"] is True, (
        "navigator.bluetooth was already unreachable before injection, so the guarded "
        "assertion proves nothing on this origin — check the page is a secure context"
    )
    assert baseline["bluetoothViaReflect"] == "object"

    guarded = browser_runs["guarded"]
    for key in GUARDED_NAVIGATOR_KEYS:
        descriptor = guarded["navigator"][key]
        assert descriptor is not None, f"navigator.{key} has no guard descriptor"
        assert descriptor["isData"] is True, f"navigator.{key} is still an accessor"
        assert descriptor["valueIsUndefined"] is True, f"navigator.{key} is not undefined"
        assert descriptor["writable"] is False, f"navigator.{key} is re-assignable"
        assert descriptor["configurable"] is False, f"navigator.{key} is re-definable"

    assert guarded["bluetoothReachable"] is False
    # The receiver trick that an instance-level shadow would have left open.
    assert guarded["bluetoothViaReflect"] == "undefined", guarded["bluetoothViaReflect"]


def test_a_page_cannot_put_the_real_fetch_back(browser_runs: dict) -> None:
    """The re-assignment defence, measured. Residual limits are in the module docstring."""
    reassign = browser_runs["guarded"]["reassign"]
    assert reassign["survivedAssignment"] is True, "window.fetch = … replaced the guard"
    assert reassign["deleteReturned"] is False, "delete window.fetch succeeded"
    assert str(reassign["redefine"]).startswith(
        "threw:"
    ), f"Object.defineProperty re-defined the guard: {reassign['redefine']}"


def test_child_frames_are_guarded_too(browser_runs: dict) -> None:
    """A fresh realm is a fresh, unguarded copy of everything — unless injection covers it.

    Measured, not assumed: ``addScriptToEvaluateOnNewDocument`` runs per new document, so
    same-origin, ``about:blank`` and ``srcdoc`` children all get the script. This test exists
    so a future engine change that reopens that hole reds CI instead of passing quietly.
    """
    guarded = browser_runs["guarded"]
    for label in ("frameSrc", "frameBlank", "frameSrcdoc"):
        frame = guarded[label]
        assert "error" not in frame, f"{label} probe failed: {frame}"
        assert frame["guarded"] is True, f"{label} is an UNGUARDED realm: {frame}"
        assert frame["fetch"] == BLOCKED_ERROR, f"{label} reached the network: {frame}"
    for via in ("iframe-src", "iframe-blank", "iframe-srcdoc"):
        assert browser_runs["guarded_hits"].get(via, 0) == 0
