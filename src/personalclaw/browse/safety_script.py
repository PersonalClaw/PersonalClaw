"""Per-page safety script for the autonomous browse loop (BROWSE-AUTOMATION BA-2, §3/§4).

This module is a **pure builder**: it emits a JavaScript source STRING and nothing else. It
makes no network call, launches no browser and reads no config — the package invariant stated
in ``personalclaw/browse/__init__.py``. A sibling atom injects the string via CDP
``Page.addScriptToEvaluateOnNewDocument``; no CDP code lives here.

Why the script exists
---------------------
The egress pre-flight in front of ``Page.navigate`` decides *which URL the agent opens*. It
cannot constrain what that page then does on its own: its ``fetch()``, an autoplaying video, a
``navigator.bluetooth`` prompt. This script is the in-page half of that gap (the plan's "§6.3
headless bypass gap"). The bar it has to clear: **a page must not be able to reach the network,
play media, or touch the user's devices behind the agent's back.**

``fetch()``: rejected, not a synthetic blocked ``Response``
----------------------------------------------------------
Both shapes stop the packet, so the choice is about what the *page* and the *agent* then see.
We reject, with a named ``PersonalClawBlockedError``, because:

* A rejection is the failure ``fetch()`` **already** produces for a network error, so the
  page's existing ``.catch()`` path handles it. A resolved-but-synthetic ``403`` is the
  unusual shape: callers branch on ``res.ok``, often by retrying, which turns one blocked
  request into a hot loop against the guard.
* Decisively: a synthetic ``Response`` is *fabricated content*. The agent reads the page after
  the page reads that response, so a fake body can end up in the agent's own perception as if
  it were real. A rejection can never be mistaken for data.
* The named error is greppable in the console, which is where an operator looks to find out
  what the page wanted — the legibility the blocked-response option is usually argued for.

The residual cost is accepted and real: a page that treats a fetch rejection as fatal renders
an error state. That is the *better* outcome here — the agent's next snapshot shows the error
instead of silently proceeding on invented data.

Every guard therefore speaks one vocabulary: ``PersonalClawBlockedError`` for the throw/reject
paths, and ``sendBeacon`` returning ``false`` (its own spec-shaped failure — callers never wrap
it, so throwing there would break pages for no gain).

What is COVERED
---------------
Network paths (each honours ``allow_hosts``, except where noted):

* ``window.fetch`` — rejects.
* ``XMLHttpRequest.prototype.open`` **and** ``.send`` — both, so an XHR holding a pre-injection
  ``open`` reference still cannot put bytes on the wire. ``send`` demands a stamp that only our
  ``open`` adds, in a closure-held ``WeakSet`` a page cannot write to.
* ``WebSocket`` / ``EventSource`` constructors — throw.
* ``navigator.sendBeacon`` — returns ``false``.
* ``Worker`` / ``SharedWorker`` constructors — throw, with **no allow-list carve-out**. A
  worker runs in a realm this script is not injected into at all
  (``addScriptToEvaluateOnNewDocument`` covers documents, not worker globals), so any worker —
  even one whose script came from an allowed host — would get a pristine ``fetch``/``XHR``. The
  constructor is the only in-page chokepoint, so it closes unconditionally.
* ``RTCPeerConnection`` (+ vendor aliases) — throws, also unconditionally: it is both an
  exfiltration path and a local-IP leak.
* ``navigator.serviceWorker.register`` — rejects, unconditionally: a service worker outlives
  the page and runs in its own realm.

Media: ``HTMLMediaElement.prototype.play`` returns a rejected promise — which is exactly what
a browser returns when it blocks autoplay (``NotAllowedError``), so every player already has
that catch path.

Device / sensor / location / capture / clipboard: ``bluetooth``, ``usb``, ``serial``, ``hid``,
``geolocation``, ``mediaDevices``, ``xr``, ``clipboard``. The clause names only bluetooth; we
widened deliberately, because a guard that closes ``navigator.bluetooth`` and leaves
``navigator.usb`` open is a control with a hole in it. ``clipboard`` is in the list because
reading the user's clipboard is a real leak on the user's own machine.

Two mechanism choices there, both load-bearing:

* They are defined as ``undefined``, not as a throwing getter. ``if (navigator.bluetooth)`` is
  the near-universal feature-detection idiom; a throwing getter turns that into an uncaught
  error that takes the page's script down and *hides* what the page wanted. ``undefined`` is a
  state real browsers genuinely produce (Safari has no ``navigator.bluetooth``).
* They are defined on ``Navigator.prototype``, not on the ``navigator`` instance. An own
  property on the instance merely *shadows* the real accessor, and
  ``Reflect.get(Navigator.prototype, name, navigator)`` recovers it. Measured: unguarded, that
  ``Reflect.get`` yields an ``object``; guarded, it yields ``undefined``.

They are defined whether or not the engine currently exposes the API, which matters more than
it looks: these attributes are ``[SecureContext]``, so the *same browser* exposes none of them
on a ``data:``/plain-http page and all of them on ``https:`` or ``http://127.0.0.1``. A guard
that only hardened what it found would silently do nothing on the insecure page and then be
absent when the loop navigated somewhere secure.

What is NOT covered — the enumerated gap
----------------------------------------
An unenumerated gap is a false sense of safety, so:

1. **Markup- and CSS-driven subresource loads.** ``<img src>``, ``<script src>``,
   ``<link rel=stylesheet/preload/prefetch>``, ``<iframe src>``, ``<video src>``, CSS
   ``url()``, ``@font-face``, form submission, and navigation itself are the *browser's*
   fetches, not JS APIs, so no in-page script can intercept them. This is the biggest gap by
   far and it is structural: closing it needs the CDP network layer
   (``Network.setBlockedURLs`` / ``Fetch.enable`` request interception) or an injected CSP.
   Media bytes reached via ``el.src = …`` / ``el.load()`` fall in here too — we close
   ``play()``, not the fetch of the media resource.
2. **``WebTransport``**, ``navigator.getGamepads()``, ``navigator.credentials`` (WebAuthn — its
   risk is a *prompt*, which is auto-dismissed headlessly, and blocking it breaks legitimate
   logins the agent may need), ``navigator.permissions``, ``Notification``, the sensor
   constructors (``Accelerometer`` &c. — permission-gated and absent headlessly), and
   ``AudioContext`` (can synthesise audio without an ``HTMLMediaElement``; not a network path,
   and headless has no audio device).
3. **An already-registered service worker** from a persisted profile can intercept before our
   guard exists. Drive the browser with an ephemeral profile.
4. **Anything a page captured before this script ran.** See below.

Re-assignment: the residual limit
---------------------------------
Every guard is installed with ``Object.defineProperty`` as ``writable: false,
configurable: false``, so ``window.fetch = savedFetch`` fails silently in sloppy mode and
throws in strict mode, ``Object.defineProperty`` over it throws, and ``delete`` fails. What
that does **not** buy:

* **A page that already holds a reference to the real function cannot be stopped.** That is
  precisely why the injection point is ``Page.addScriptToEvaluateOnNewDocument`` (runs before
  any page script in the document) and *not* ``Runtime.evaluate`` (runs after, by which time
  the page may have stashed ``const f = fetch``). The injection mechanism, not the property
  descriptor, is what closes this.
* **A fresh realm has a fresh, unguarded copy of everything.** A same-origin ``<iframe>`` is a
  new document, so whether it is guarded depends on the injection covering child frames.
  Measured on Chromium 1234 via raw CDP: ``addScriptToEvaluateOnNewDocument`` **does** run in
  child frames, including ``srcdoc`` and ``about:blank`` ones, so ``frame.contentWindow.fetch``
  is guarded — ``tests/test_browse_safety_script.py`` asserts this against a live browser so a
  future engine change that silently reopens the hole reds CI rather than passing quietly.
* A page can still detect the guard (a non-configurable ``fetch`` that always rejects is a
  giveaway, and ``toString()`` shows the source). Full stealth (§4) and a hard in-page policy
  guard are in tension by construction: §4 can hide the *automation*, not the *policy*. The
  ``window.__personalclawSafety`` marker is therefore non-enumerable but not secret; it adds no
  new class of detectability, and it is what makes "the injection actually took" assertable.

Never throws during injection
-----------------------------
A script that errors leaves the page **unguarded while looking injected**. So each guard runs
inside a ``step()`` that records ``applied`` / ``skipped`` / ``failed``, the whole body is
wrapped, and the outermost layer swallows. Silence is not evidence, so the script publishes
``window.__personalclawSafety = {version, applied, skipped, failed, allowHosts}`` (frozen,
non-enumerable, non-configurable) as the assertable proof that it ran and what it managed to
install. An absent marker, or a non-empty ``failed``, means the page is not guarded.
"""

from __future__ import annotations

import json

__all__ = [
    "SAFETY_SCRIPT",
    "SAFETY_MARKER",
    "GUARD_STEPS",
    "GUARDED_NAVIGATOR_KEYS",
    "safety_script",
]

#: The property the script publishes on ``window`` as proof of a successful injection.
SAFETY_MARKER = "__personalclawSafety"

#: Every guard step name the script reports in ``applied`` / ``skipped`` / ``failed``.
GUARD_STEPS = (
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "sendBeacon",
    "workers",
    "peerConnection",
    "serviceWorker",
    "media",
    "deviceApis",
)

#: ``Navigator.prototype`` keys hard-defined to ``undefined``.
GUARDED_NAVIGATOR_KEYS = (
    "bluetooth",
    "usb",
    "serial",
    "hid",
    "geolocation",
    "mediaDevices",
    "xr",
    "clipboard",
)

_ALLOW_HOSTS_TOKEN = "__PERSONALCLAW_ALLOW_HOSTS__"

_TEMPLATE = """\
/* PersonalClaw per-page browse safety script (BROWSE-AUTOMATION BA-2 §3).
   Injected with Page.addScriptToEvaluateOnNewDocument so it runs BEFORE any page script in
   every new document. It must never throw out of itself: a script that errors would leave the
   page unguarded while looking injected. Rationale, coverage and the enumerated gaps live in
   personalclaw/browse/safety_script.py. */
try {
  (function () {
    "use strict";

    var ALLOW_HOSTS = __PERSONALCLAW_ALLOW_HOSTS__;
    var MARKER = "__personalclawSafety";

    var applied = [];
    var skipped = [];
    var failed = [];

    /* Captured before any page script exists, so later shadowing of the globals cannot
       redirect our own plumbing. */
    var _URL = URL;
    var _Promise = Promise;
    var _WeakSet = WeakSet;
    var _defineProperty = Object.defineProperty;
    var _freeze = Object.freeze;
    var _getPrototypeOf = Object.getPrototypeOf;
    var _slice = Array.prototype.slice;
    var _bind = Function.prototype.bind;

    function note(list, text) {
      try {
        list.push(text);
      } catch (ignored) {
        /* nothing left to do: bookkeeping must not be able to break a guard */
      }
    }

    /* A step returning false means "this engine does not expose the API" — recorded as
       skipped, never as applied, so the marker cannot claim a guard it did not install. */
    function step(name, fn) {
      try {
        if (fn() === false) {
          note(skipped, name);
        } else {
          note(applied, name);
        }
      } catch (err) {
        note(failed, name + ": " + ((err && err.message) || err));
      }
    }

    /* Non-writable + non-configurable: a page can neither re-assign, redefine nor delete it.
       The residual limits are in the module docstring. */
    function hardDefine(target, name, value, enumerable) {
      _defineProperty(target, name, {
        value: value,
        writable: false,
        enumerable: !!enumerable,
        configurable: false
      });
    }

    function blocked(what, url) {
      var e = new Error(
        "PersonalClaw browse safety guard blocked " + what + (url ? " -> " + url : "")
      );
      e.name = "PersonalClawBlockedError";
      return e;
    }

    function urlOf(input) {
      try {
        if (typeof input === "string") {
          return input;
        }
        if (input && typeof input.url === "string") {
          return input.url; /* Request */
        }
        if (input && typeof input.href === "string") {
          return input.href; /* URL */
        }
        return String(input);
      } catch (err) {
        return "";
      }
    }

    /* Fail closed: an empty allow-list, an unparseable URL or a missing base all deny.
       Note there is deliberately NO implicit same-origin carve-out — the page's own origin is
       the one origin we know we do not control, so allowing it by default would gut the
       guard. A caller that wants the page's host must name it in allow_hosts. */
    function isAllowed(input) {
      if (!ALLOW_HOSTS.length) {
        return false;
      }
      var host;
      try {
        var base;
        try {
          base = (typeof document !== "undefined" && document.baseURI) || location.href;
        } catch (err) {
          base = undefined;
        }
        host = new _URL(urlOf(input), base).hostname.toLowerCase();
      } catch (err) {
        return false;
      }
      if (!host) {
        return false;
      }
      for (var i = 0; i < ALLOW_HOSTS.length; i++) {
        if (ALLOW_HOSTS[i] === host) {
          return true;
        }
      }
      return false;
    }

    function denyConstructor(name) {
      if (typeof window[name] !== "function") {
        return false;
      }
      var Real = window[name];
      function Guarded(url) {
        throw blocked("new " + name + "()", urlOf(url));
      }
      try {
        Guarded.prototype = Real.prototype;
      } catch (err) {
        /* a frozen prototype slot is not worth failing the guard over */
      }
      hardDefine(window, name, Guarded, true);
      return true;
    }

    function guardUrlConstructor(name) {
      if (typeof window[name] !== "function") {
        return false;
      }
      var Real = window[name];
      function Guarded(url) {
        if (!isAllowed(url)) {
          throw blocked("new " + name + "()", urlOf(url));
        }
        return new (_bind.apply(Real, [null].concat(_slice.call(arguments))))();
      }
      try {
        Guarded.prototype = Real.prototype;
      } catch (err) {
        /* see denyConstructor */
      }
      /* Carry the interface constants across so feature detection still works. */
      for (var key in Real) {
        try {
          Guarded[key] = Real[key];
        } catch (err) {
          /* a non-copyable static is not a guard failure */
        }
      }
      hardDefine(window, name, Guarded, true);
      return true;
    }

    /* --- network path: fetch ---------------------------------------------------------- */
    step("fetch", function () {
      var real = typeof fetch === "function" ? _bind.call(fetch, window) : null;
      hardDefine(window, "fetch", function fetch(input, init) {
        if (real && isAllowed(input)) {
          return real(input, init);
        }
        /* Rejected, not a synthetic blocked Response: see the module docstring. */
        return _Promise.reject(blocked("fetch()", urlOf(input)));
      }, true);
      return real !== null;
    });

    /* --- network path: XMLHttpRequest ------------------------------------------------- */
    step("XMLHttpRequest", function () {
      if (typeof XMLHttpRequest !== "function") {
        return false;
      }
      var proto = XMLHttpRequest.prototype;
      var realOpen = proto.open;
      var realSend = proto.send;
      var permitted = new _WeakSet();
      hardDefine(proto, "open", function open(method, url) {
        if (!isAllowed(url)) {
          throw blocked("XMLHttpRequest.open()", urlOf(url));
        }
        permitted.add(this);
        return realOpen.apply(this, _slice.call(arguments));
      }, true);
      /* send() is guarded too — it is where bytes leave, so an XHR that captured open()
         before injection still cannot transmit. The stamp lives in a closure-held WeakSet
         no page code can add to. */
      hardDefine(proto, "send", function send(body) {
        if (!permitted.has(this)) {
          throw blocked("XMLHttpRequest.send()", "");
        }
        return realSend.apply(this, _slice.call(arguments));
      }, true);
      return true;
    });

    /* --- network paths: WebSocket, EventSource ---------------------------------------- */
    step("WebSocket", function () {
      return guardUrlConstructor("WebSocket");
    });
    step("EventSource", function () {
      return guardUrlConstructor("EventSource");
    });

    /* --- network path: navigator.sendBeacon ------------------------------------------- */
    step("sendBeacon", function () {
      if (typeof Navigator !== "function") {
        return false;
      }
      var real = Navigator.prototype.sendBeacon;
      if (typeof real !== "function") {
        return false;
      }
      /* false is sendBeacon()'s own spec-shaped failure; callers treat it as
         fire-and-forget and never wrap it, so throwing here would break pages for nothing. */
      hardDefine(Navigator.prototype, "sendBeacon", function sendBeacon(url, data) {
        if (!isAllowed(url)) {
          return false;
        }
        return real.apply(this, _slice.call(arguments));
      }, true);
      return true;
    });

    /* --- network path: realms this script is never injected into ---------------------- */
    step("workers", function () {
      /* No allow-list carve-out: a Worker global is a realm addScriptToEvaluateOnNewDocument
         does not reach, so ANY worker would hold a pristine fetch/XHR. */
      var closed = denyConstructor("Worker");
      if (denyConstructor("SharedWorker")) {
        closed = true;
      }
      return closed;
    });
    step("peerConnection", function () {
      /* Exfiltration path and a local-IP leak, so also unconditional. */
      var names = ["RTCPeerConnection", "webkitRTCPeerConnection", "mozRTCPeerConnection"];
      var closed = false;
      for (var i = 0; i < names.length; i++) {
        if (denyConstructor(names[i])) {
          closed = true;
        }
      }
      return closed;
    });
    step("serviceWorker", function () {
      var container = typeof navigator !== "undefined" ? navigator.serviceWorker : null;
      if (!container || typeof container.register !== "function") {
        return false;
      }
      /* A service worker outlives the page and runs in its own realm: unconditional. */
      hardDefine(_getPrototypeOf(container), "register", function register(url) {
        return _Promise.reject(blocked("serviceWorker.register()", urlOf(url)));
      }, true);
      return true;
    });

    /* --- media ------------------------------------------------------------------------ */
    step("media", function () {
      if (typeof HTMLMediaElement !== "function") {
        return false;
      }
      /* A rejected promise is exactly what a browser returns when it blocks autoplay
         (NotAllowedError), so every player already has this catch path. */
      hardDefine(HTMLMediaElement.prototype, "play", function play() {
        return _Promise.reject(blocked("HTMLMediaElement.play()", ""));
      }, true);
      return true;
    });

    /* --- device / sensor / location / capture / clipboard ----------------------------- */
    step("deviceApis", function () {
      if (typeof Navigator !== "function") {
        return false;
      }
      /* undefined rather than a throwing getter, and on Navigator.PROTOTYPE rather than the
         navigator instance — both reasons are in the module docstring. Defined whether or not
         the engine exposes the API, so a future engine that starts exposing one is already
         closed, and so the descriptor itself is positive proof the guard ran. */
      var keys = __PERSONALCLAW_NAVIGATOR_KEYS__;
      for (var i = 0; i < keys.length; i++) {
        hardDefine(Navigator.prototype, keys[i], undefined, true);
      }
      return true;
    });

    /* Silence is not evidence: publish what actually got installed so a caller can assert
       the injection took. Non-enumerable (it stays out of Object.keys(window)) but not
       secret — see the stealth-tension note in the module docstring. */
    try {
      hardDefine(window, MARKER, _freeze({
        version: 1,
        applied: _freeze(applied.slice()),
        skipped: _freeze(skipped.slice()),
        failed: _freeze(failed.slice()),
        allowHosts: _freeze(ALLOW_HOSTS.slice())
      }), false);
    } catch (err) {
      /* an un-publishable marker reads to the caller as "not guarded", which is correct */
    }
  })();
} catch (err) {
  /* Injection must never raise. The absent window.__personalclawSafety marker is the
     caller's signal that the page is NOT guarded. */
}
"""


def _js_string(value: str) -> str:
    """Encode ``value`` as a JS string literal that is safe in every embedding context.

    ``json.dumps`` with ``ensure_ascii=True`` (the default, pinned here because this depends
    on it) handles quotes, backslashes, control characters and every non-ASCII code point --
    including U+2028 / U+2029, which are legal in JSON but are JS line terminators. The one
    thing it does not handle is the HTML-embedding case, so we add it:

    ``<``/``>``/``&`` -- a host containing ``</script>`` would otherwise close an enclosing
    ``<script>`` element. We inject over CDP, where that cannot bite, but a value that is only
    safe because of where it currently happens to be used is a latent injection bug in our own
    tooling. ``\\u003c`` inside a JS string literal still evaluates to ``<``, so escaping costs
    nothing at runtime.
    """
    encoded = json.dumps(value, ensure_ascii=True)
    for raw, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        encoded = encoded.replace(raw, escape)
    return encoded


def _js_array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_js_string(v) for v in values) + "]"


def _normalise_hosts(allow_hosts: tuple[str, ...]) -> tuple[str, ...]:
    """Lower-case, strip and de-duplicate, preserving caller order.

    A bare ``str`` is rejected rather than iterated: ``allow_hosts="example.com"`` would
    otherwise silently become an allow-list of eleven single characters.
    """
    if isinstance(allow_hosts, (str, bytes)):
        raise TypeError("allow_hosts must be a tuple of hosts, not a bare string")
    seen: set[str] = set()
    hosts: list[str] = []
    for host in allow_hosts:
        if not isinstance(host, str):
            raise TypeError(f"allow_hosts entries must be str, got {type(host).__name__}")
        cleaned = host.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        hosts.append(cleaned)
    return tuple(hosts)


def safety_script(*, allow_hosts: tuple[str, ...] = ()) -> str:
    """Return the safety script, with an optional allow-list carve-out baked in.

    ``allow_hosts`` names the hosts whose ``fetch``/``XHR``/``WebSocket``/``EventSource``/
    ``sendBeacon`` traffic is permitted, matched on the *exact* lower-cased hostname of the
    request URL resolved against ``document.baseURI``. Everything else denies. There is no
    implicit same-origin carve-out: the page's origin is the one we do not control, so a caller
    that wants it must name it. Worker / RTCPeerConnection / service-worker / media / device
    guards ignore the list entirely — see the module docstring for why each is unconditional.

    The result is a self-contained expression statement suitable for
    ``Page.addScriptToEvaluateOnNewDocument``; it never throws.
    """
    hosts = _normalise_hosts(allow_hosts)
    source = _TEMPLATE
    if source.count(_ALLOW_HOSTS_TOKEN) != 1:
        # A drifting template would otherwise ship an un-substituted placeholder, which is a
        # JS syntax error — i.e. an entirely unguarded page that still looks injected.
        raise AssertionError("safety script template lost its allow-hosts placeholder")
    source = source.replace(_ALLOW_HOSTS_TOKEN, _js_array(hosts))
    source = source.replace("__PERSONALCLAW_NAVIGATOR_KEYS__", _js_array(GUARDED_NAVIGATOR_KEYS))
    return source


#: The script with no carve-out — everything denied. The default the loop should use.
SAFETY_SCRIPT: str = safety_script()
