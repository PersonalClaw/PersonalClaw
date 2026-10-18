"""Gated CDP navigation for the browse loop.

A headless browser does its own DNS and its own connections, so it bypasses
``net/client.py``'s IP-pinning SSRF guard entirely (``web/render.py``'s docstring says the
same thing about Playwright, and names redirects as its residual gap). This module is the
chokepoint that closes that hole for the CDP path: **every** navigation goes through
``net/guard.py:evaluate`` against the BROWSE profile BEFORE a ``Page.navigate`` message is
written to the wire, and a client-side redirect, or a frame, is re-judged when the browser
reports it.

Three properties, in the order that makes them properties rather than logging:

1. **Pre-flight, not post-mortem.** :meth:`GatedCdpSession.navigate` evaluates first and
   returns without sending ``Page.navigate`` on a deny. A decision taken after the message
   is sent is not a gate, so the transport is injectable and the test asserts on the
   recorded wire messages (zero ``Page.navigate``), never on a return value.
2. **The in-page guard is installed before the first document.**
   ``Page.addScriptToEvaluateOnNewDocument`` is registered during :meth:`start`; a session
   that never started refuses to navigate, because the alternative is one page loading
   unguarded.
3. **A redirect the pre-flight never saw is re-judged.** ``location = …``, a meta refresh
   and a 302 chain all land the browser somewhere the pre-flight did not authorise. Every
   ``Page.frameNavigated`` is re-evaluated, and so is every frame:

   * a denied top-frame commit is torn down (:meth:`GatedCdpSession._enforce`);
   * a denied child frame is blanked on its own (:meth:`GatedCdpSession._block_frame`), so the
     page that embedded it keeps working;
   * a frame in the page's own process is caught when it commits;
   * a frame the browser moves into a process of its own (every cross-site frame on a browser
     that gives each site its own process) is caught when auto-attach reports it, paused before
     its first document there loads, and judged again whenever it commits;
   * a page restored from the back-forward cache has its frames judged again, and page commands
     wait while the frames inside one that comes back running are judged;
   * going back one page (:meth:`GatedCdpSession.go_back`) judges each history entry before it
     is loaded, as a navigation is pre-flighted, and skips a denied one.

   None of it is merely logged.
4. **The document a session inherits is judged too.** A page target outlives the sessions
   that drive it: a page can move itself after the last session closed, or a quarantined one
   can leave a denied document loaded. :meth:`start` judges every frame ``Page.getFrameTree``
   lists as a redirect is judged, and also the frames auto-attach reports, within
   :data:`FRAME_START_BOUND`: a denied top frame is torn down, a denied child is blanked, a page
   whose frames cannot all be judged in time is blanked, and a session that cannot tear a denied
   document down, cannot read the frames, or cannot watch them, refuses to start.
   A navigation the browser answers without loading a new document (``errorText``: a 204, an
   abort; or a download) is reported not-ok, because the page still holds what it held.

Everything fails CLOSED. An unparseable URL, a missing BROWSE profile, a guard that raises,
a transport that raises, an enforcement that cannot be delivered — none of them end in
"navigated anyway"; the worst case is a quarantined session that refuses to navigate at
all, and whose page is not read or acted on either: the page driver speaks through
:attr:`GatedCdpSession.page_transport`, which refuses every command once the session is
quarantined, and holds a command back while a teardown or a frame block runs, or while an event
read before it is still being judged. The one deliberate
fail-OPEN is the audit: a SEL write that raises must not turn a deny into a navigation.

A teardown or frame-blank command the browser refuses with "Not attached to an active page" is
sent again,
every :data:`TEARDOWN_RETRY_INTERVAL` seconds for up to :data:`TEARDOWN_RETRY_BOUND`: right
after a cross-site redirect the page's session refuses commands for a few milliseconds (measured
6.5-25 ms, in about 1 cross-site redirect in 80; never for a same-site one), and quarantining on
that would end a session for a transient. Any other refusal, or a page still detached past the
bound, quarantines as before, and the page is still sent to ``about:blank`` once, so a
quarantined session does not keep the denied document loaded when the browser would take the
navigation.

Residual gaps, stated rather than papered over:

* **DNS rebind.** The guard pins the resolved IPs, but CDP has no per-navigation "dial this
  IP" parameter — the browser resolves again itself. :attr:`NavigationOutcome.pinned_ips`
  carries the pinned set out so the launcher can pass Chrome's ``--host-resolver-rules``;
  wiring that belongs to the launcher scope, not here.
* **Subresources.** An image, an XHR or a WebSocket is not a navigation and produces no
  ``Page.frameNavigated``. Those are the in-page guard script's job where it runs (and, later,
  the ``Fetch``/``Network`` interception domains) — not this module's.
* **A denied frame's own document request reaches its host.** A frame is paused when its
  response arrives, so the request has been made; only intercepting requests would stop it.
* **A frame judged at commit runs for a moment first.** Only a frame's first move into a process
  of its own is paused before its document loads. A frame that shares its page's process (on a
  browser that does not isolate sites, every cross-site frame), is redirected, sits in a
  sandboxed srcdoc frame, or is already in a process of its own and is navigated again, by itself
  or by its page (the browser does not attach it again, so it is not paused), commits before it
  is judged, and its script can run until it is blanked; a capture answered before that commit
  was reported can include it.
* **No in-page guard in a frame of its own process.** The guard script is installed on the
  page, and reaches frames in the page's process only.
* **Paused frames load after the session.** A quarantined session's paused frames, a frame still
  being judged when the session closes, a fenced frame left paused, and a target left paused (a
  ``page`` target among them), load once the socket closes.
* **The hold ends with the blank's reply.** A frame in its own process commits the blank a few
  milliseconds after the browser answered.
* **A page that keeps naming new denied hosts for its frames records one block per host**: each
  is a block, an SEL row and a count in what the run is told, without a bound. Only a frame block
  of a host already recorded on the page is counted on that one.
* **A frame whose renderer does not answer for the command timeout is blanked**, though it may
  have been allowed.
* **Under a ``listed`` or ``registry`` egress tier** every frame from a host off the list is
  blanked, and the run is told.
* **A target of a type the session does not judge that attaches already running** (a ``page``
  target among them, such as a page prerendered before the session watched the tab) is audited,
  as ``ungated`` rather than denied, and left as it is: it was never paused, and nothing of it is
  judged or stopped.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse, urlsplit

from personalclaw.browse.transport import SESSION_DETACHED
from personalclaw.cancellation import cancel_and_wait
from personalclaw.net.guard import GuardDecision, evaluate
from personalclaw.net.policy import EgressPolicy, egress_policy_for
from personalclaw.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)

# CDP methods and events this module writes or reads (Page, Target and Runtime), named once so a
# test can assert on the exact strings the browser will see.
PAGE_ENABLE = "Page.enable"
ADD_SCRIPT = "Page.addScriptToEvaluateOnNewDocument"
NAVIGATE = "Page.navigate"
STOP_LOADING = "Page.stopLoading"
FRAME_NAVIGATED = "Page.frameNavigated"
GET_FRAME_TREE = "Page.getFrameTree"
SET_AUTO_ATTACH = "Target.setAutoAttach"
RUN_IF_WAITING = "Runtime.runIfWaitingForDebugger"
ATTACHED_TO_TARGET = "Target.attachedToTarget"
TARGET_INFO_CHANGED = "Target.targetInfoChanged"
FRAME_STARTED_NAVIGATING = "Page.frameStartedNavigating"
FRAME_REQUESTED_NAVIGATION = "Page.frameRequestedNavigation"
GET_NAVIGATION_HISTORY = "Page.getNavigationHistory"
NAVIGATE_TO_HISTORY_ENTRY = "Page.navigateToHistoryEntry"

#: Every frame and worker the page starts, attached and paused until judged. Service workers are
#: left out: one serves the owner's other tabs too, and one no session attaches is not paused, so
#: excluding it holds none of them back. Dedicated workers and worklets stay in: their renderer
#: pauses them whatever the filter says, and only an attached session can let them run. ``browser``
#: and ``tab`` are left out as the default filter leaves them out.
AUTO_ATTACH: dict[str, Any] = {
    "autoAttach": True,
    "waitForDebuggerOnStart": True,
    "flatten": True,
    "filter": [
        {"type": "service_worker", "exclude": True},
        {"type": "browser", "exclude": True},
        {"type": "tab", "exclude": True},
        {},
    ],
}

#: What the browser answers a command on a session it no longer has.
SESSION_GONE = "Session with given id not found"
#: What the browser answers a frame's blank when the frame is in no frame of the page any more.
FRAME_GONE = "No frame with given id found"

#: Targets that are not frames and are let run unjudged, as they ran before this session watched
#: the page's frames. Any other type is left paused and audited, ``page`` among them: a page the
#: browser attaches to the page, such as a prerendered one, would load a document of its own
#: unjudged, with nothing inside it watched. Leaving one paused shows nothing less: the browser
#: does not prerender for a session attached as this one is (measured on the headless shell and a
#: full browser build, 1248, which report the attempt as ``PrerenderingDisabledByDevTools``).
RESUMED_TARGETS = frozenset(
    {
        "worker",
        "shared_worker",
        "service_worker",
        "worklet",
        "auction_worklet",
        "webview",
        "other",
    }
)

#: How long the frames already running when the session first sees them are given to be judged
#: before the page is blanked rather than shown with one unjudged: the frames a tab holds when
#: :meth:`GatedCdpSession.start` begins, and the frames inside a frame that attaches running
#: after it (a page restored from the back-forward cache brings its frames back so).
FRAME_START_BOUND = 2.0

#: How long a frame's blank waits for another blank of the same frame still running before it is
#: sent anyway: two blanks of one frame in flight make the browser answer one of them "aborted".
FRAME_BLANK_WAIT = 2.0

PHASE_FRAME_ATTACHED = "frame_attached"
PHASE_FRAME_COMMITTED = "frame_committed"
PHASE_FRAME_UNJUDGED = "frame_unjudged"
PHASE_RESTORED = "restored"
PHASE_TARGET_LEFT_PAUSED = "target_left_paused"
PHASE_TARGET_LEFT_RUNNING = "target_left_running"

#: What was done about a denied frame (:attr:`NavigationOutcome.blanked`): it was blanked on its
#: own, the whole page was blanked in its place, or it was left paused and never loaded.
BLANKED_FRAME = "frame"
BLANKED_PAGE = "page"
LEFT_UNLOADED = "unloaded"

#: How many frames a session remembers (where each was going, what each was last judged as, the
#: lock its blanks take) and how many hosts it remembers blocking on the page; the oldest is
#: forgotten first.
_FRAME_MEMORY = 256
#: How many judgements a session makes at once, each of which may look a host up: as many for
#: frames that are paused, which show nothing until they are judged, as for the rest, whose
#: judgement page commands wait on, so a slow lookup of one kind never holds the other.
_JUDGEMENTS_IN_FLIGHT = 4

#: Where a blocked page is sent. ``about:blank`` is not egress — it is the teardown.
BLANK_URL = "about:blank"

#: Chrome reports a failed/stopped load under this scheme; re-judging it would fight our
#: own enforcement.
_ERROR_URL_PREFIX = "chrome-error://"

#: An installed extension's own page. The browser's own PDF viewer is one.
_EXTENSION_PREFIX = "chrome-extension://"

#: What the browser answers a command sent while the page's session is detached, as it is for a
#: few milliseconds after a cross-site navigation. The one refusal a teardown sends again.
PAGE_DETACHED = "Not attached to an active page"

#: How often a teardown command refused with :data:`PAGE_DETACHED` is sent again, and for how
#: long in all (one deadline for the whole teardown, both of its commands).
TEARDOWN_RETRY_INTERVAL = 0.01
TEARDOWN_RETRY_BOUND = 2.0

#: SEL ``event_type`` for a browse egress decision. ``operation`` carries the CDP method
#: that was blocked, so a preflight deny and a redirect deny are distinguishable rows.
SEL_EVENT_TYPE = "browse_egress"
SEL_TOOL_KIND = "browse_cdp"


class CdpSessionError(RuntimeError):
    """The session is unusable: its setup failed, or it is quarantined. It refuses to navigate,
    and its :attr:`GatedCdpSession.page_transport` refuses to read or act on the page."""


class PageReplyDiscarded(RuntimeError):
    """A page command's reply came back after a teardown began, so it was discarded: it may have
    been read off the denied document before the page was blanked.

    Not a :class:`CdpSessionError`: the session is still sound when that teardown succeeded
    (:attr:`GatedCdpSession.quarantine_reason` says which), and the same read sent again waits
    for the teardown and reads the blanked page. A caller may re-read; it should not resend an
    action, which is not idempotent."""


class _PageStayedDetached(RuntimeError):
    """A teardown command was refused with :data:`PAGE_DETACHED` until the bound ran out."""


class CdpTransport(Protocol):
    """The CDP wire, injectable so the gate is testable without a browser.

    A real implementation is a WebSocket to the browser's debugger endpoint; the tests
    pass a fake that records every message. Keeping this a Protocol is what makes
    "blocked BEFORE ``Page.navigate`` fires" an assertion about the wire rather than about
    a return value.
    """

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send one CDP command and return its result payload. ``session_id`` addresses a frame
        or worker the browser attached; the page's own commands never pass it."""
        ...

    def set_event_listener(self, listener: Callable[..., Awaitable[None]] | None) -> None:
        """Register the coroutine that receives ``(method, params, session_id)`` for every CDP
        event; ``session_id`` is ``None`` for the page's own."""
        ...

    async def drain_events(self) -> None:
        """Return once every event read so far has been handed to the listener and handled."""
        ...


@dataclass
class NavigationOutcome:
    """What happened to one navigation attempt.

    ``allowed`` is the GATE's verdict; ``ok`` is whether the navigation was actually
    dispatched. They are separate on purpose: an allowed URL whose ``Page.navigate`` send
    failed is ``allowed=True, ok=False`` — not a success, and not a policy denial either.
    """

    ok: bool
    allowed: bool
    url: str = ""
    host: str = ""
    reason: str = ""
    risk_level: str = "safe"
    pinned_ips: list[str] = field(default_factory=list)
    error: str = ""
    recovery_hints: list[str] = field(default_factory=list)
    #: A block of one frame of the page, rather than of the page's own document.
    frame: bool = False
    #: How many more times a frame from the same host was blocked on the same page after this
    #: block was recorded: each frame was blanked on its own, and none was recorded again. One
    #: whose own blank failed, so that the whole page was blanked, is a block of its own instead.
    repeats: int = 0
    #: For a frame's block, what was done about the frame: :data:`BLANKED_FRAME` (blanked on its
    #: own, or already gone from the page), :data:`BLANKED_PAGE` (its own blank failed, so the
    #: whole page was blanked in its place) or :data:`LEFT_UNLOADED` (left paused, never loaded).
    #: Empty for a block of the page's own document.
    blanked: str = ""
    #: Set on a frame's block that was counted on an earlier block of its host (:attr:`repeats`)
    #: rather than recorded: that block, and the CDP method and phase this one's own SEL row
    #: names if it is recorded after all, as it is when the whole page is blanked in its place.
    counted_on: tuple[NavigationOutcome, str, str] | None = field(
        default=None, repr=False, compare=False
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host_of(url: str) -> str:
    """Best-effort host, for labelling a decision the guard itself could not reach."""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _document_url(url: str) -> str | None:
    """What a committed document is judged as, or ``None`` when it is no network document.

    ``about:blank`` and ``about:srcdoc`` with any query or fragment are the same documents (the
    teardown's own target among them), and the browser's own error page is no site's: none is
    judged. A ``blob:`` is judged by the origin that wrote it, so a ``blob:`` with no origin is
    denied. Anything else is judged as itself: ``file:``, ``data:`` and ``javascript:`` frames
    are judged (and denied) by the guard like any other URL, and so is an extension's page at the
    top."""
    if url.startswith(_ERROR_URL_PREFIX):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    scheme = parts.scheme.lower()
    if scheme == "about" and parts.path in ("blank", "srcdoc"):
        return None
    if scheme == "blob":
        return url[len("blob:") :]
    return url


def _is_extension(url: str) -> bool:
    """A frame of an installed extension's own page: not a web host's document, so not judged as
    a frame, but still watched so the frames inside it are judged."""
    return url.lower().startswith(_EXTENSION_PREFIX)


def _frame_records(tree: Any) -> list[tuple[str, bool, str]]:
    """Every frame in a ``Page.getFrameTree`` reply's ``frameTree`` as ``(id, is_top, url)``,
    breadth first, the top frame first.

    Raises ``ValueError`` on a tree without a top frame: a page whose frames cannot be read
    cannot be judged, and the caller refuses rather than treat it as empty."""
    if not isinstance(tree, dict) or not isinstance(tree.get("frame"), dict):
        raise ValueError(f"the browser answered no frame tree ({tree!r:.120})")
    records: list[tuple[str, bool, str]] = []
    stack: list[tuple[Any, bool]] = [(tree, True)]
    while stack:
        node, top = stack.pop(0)
        frame = node.get("frame") if isinstance(node, dict) else None
        if not isinstance(frame, dict):
            raise ValueError("the browser answered a frame with no frame record")
        records.append((str(frame.get("id") or ""), top, str(frame.get("url") or "")))
        stack.extend((child, False) for child in (node.get("childFrames") or []))
    return records


def _deny(url: str, reason: str) -> GuardDecision:
    """A deny this session reached without the guard: a frame it cannot judge or watch."""
    return GuardDecision(
        allow=False, url=url, host=_host_of(url), reason=reason, risk_level="destructive"
    )


def _gone(exc: BaseException) -> bool:
    """Whether a frame's or a worker's session went away under a command sent on it."""
    text = str(exc)
    return SESSION_GONE in text or SESSION_DETACHED in text


def _auto_attach() -> dict[str, Any]:
    return {**AUTO_ATTACH, "filter": [dict(f) for f in AUTO_ATTACH["filter"]]}


def _navigation_failed(reply: Any) -> str:
    """Why a ``Page.navigate`` reply loaded no new document, or ``""`` when it did."""
    if not isinstance(reply, dict):
        return ""
    if reply.get("errorText"):
        return str(reply["errorText"])
    if reply.get("isDownload"):
        return "the navigation became a download"
    return ""


class GatedCdpSession:
    """One CDP page session whose navigation path is gated by the egress guard.

    ``resolver`` is handed to :func:`personalclaw.net.guard.evaluate` (fake DNS in tests);
    ``caller_identity`` / ``agent`` / ``source`` land on the SEL rows this session writes.
    ``sleep`` and ``clock`` pace a teardown's resends (:data:`TEARDOWN_RETRY_INTERVAL`, within
    :data:`TEARDOWN_RETRY_BOUND`); a test passes its own so no wall-clock time is involved.
    ``blocking`` is how a judgement, which may look a host up and so blocks, is run: on a thread
    by default, so a slow lookup holds neither the event loop nor the other frames; a test passes
    one that runs it inline. ``frame_start_bound`` is how long the frames already running when
    the session first sees them are given to be judged (:data:`FRAME_START_BOUND`).

    Whoever closes the transport calls :meth:`close` first, so nothing the session started for a
    frame runs on into the closed socket.
    """

    def __init__(
        self,
        transport: CdpTransport,
        *,
        caller_identity: str = "browse",
        agent: str = "personalclaw",
        source: str = "background",
        resolver: Callable[[str], list[str]] | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        blocking: Callable[..., Awaitable[Any]] = asyncio.to_thread,
        frame_start_bound: float = FRAME_START_BOUND,
    ) -> None:
        self._transport = transport
        self._caller_identity = caller_identity
        self._agent = agent
        self._source = source
        self._resolver = resolver
        self._sleep = sleep
        self._clock = clock
        self._blocking = blocking
        self._frame_start_bound = frame_start_bound
        self._started = False
        # How many blocks are running now, a whole-page teardown or one frame's blank: start()'s,
        # the dispatcher's and a frame task's can overlap, so the hold lifts when the last ends.
        self._running = 0
        # Set while no block runs; a page command and a navigation wait on it.
        self._settled = asyncio.Event()
        self._settled.set()
        # How many blocks of a document that had committed have begun, and how many run now: a
        # page reply that straddles one is discarded, since it may have been read off that
        # document. A frame blanked before its document loaded showed nothing and counts in
        # neither.
        self._teardowns = 0
        self._exposing = 0
        # How many whole-page teardowns run now. Counted on their own: a frame block beside one
        # sends no top blank, so it never excuses one whose blank loaded nothing.
        self._page_running = 0
        self._quarantine = ""
        self._blocks: list[NavigationOutcome] = []
        self._script_allow_hosts: tuple[str, ...] = ()
        # frame id -> where its latest navigation started for (``Page.frameStartedNavigating``),
        # read when the frame attaches with no URL of its own.
        self._frame_starts: OrderedDict[str, str] = OrderedDict()
        # frame id -> the URL last judged or seen for it: one commit the browser reports twice is
        # judged, blanked and recorded once. Forgotten at every top-level commit.
        self._judged: OrderedDict[str, str] = OrderedDict()
        # host (or URL, for a block that names no host) -> the frame block recorded first for it
        # on this page, which counts the ones after it. Forgotten at every top-level commit.
        self._frame_blocks: OrderedDict[str, NavigationOutcome] = OrderedDict()
        # The session's own tasks (a paused frame's judgement, a frame being watched, a worker
        # let run), each forgotten when it ends.
        self._tasks: set[asyncio.Task[Any]] = set()
        # Frames that were already running when start() began, still being watched.
        self._start_arming: set[asyncio.Task[Any]] = set()
        self._starting = False
        self._closed = False
        # Judgements a page command waits on, and judgements of paused frames, which hold
        # nothing: each kind has slots of its own (_JUDGEMENTS_IN_FLIGHT).
        self._slots = asyncio.Semaphore(_JUDGEMENTS_IN_FLIGHT)
        self._paused_slots = asyncio.Semaphore(_JUDGEMENTS_IN_FLIGHT)
        # frame id -> the lock its blanks take, so one frame is blanked once at a time: a second
        # blank of the same frame sent while the first runs makes the browser answer the first
        # "aborted", which reads as a blank that loaded nothing.
        self._frame_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def started(self) -> bool:
        """Whether the in-page guard script is installed (the navigate precondition)."""
        return self._started

    @property
    def quarantine_reason(self) -> str:
        """Non-empty once the session can no longer be trusted; navigation is refused."""
        return self._quarantine

    @property
    def blocks(self) -> list[NavigationOutcome]:
        """Every denial this session made: pre-flight, redirect and frame alike; a frame's has
        ``frame=True``."""
        return list(self._blocks)

    @property
    def tearing_down(self) -> bool:
        """Whether a teardown of a denied document, or a frame's blank, is running now."""
        return not self._settled.is_set()

    async def _quiesce(self) -> None:
        """Return once every event read so far is judged and no block runs. Loops, because a
        block can start between one ending and this waiter running."""
        while True:
            await self._transport.drain_events()
            if self._settled.is_set():
                return
            await self._settled.wait()

    async def wait_idle(self) -> None:
        """Return once every event read so far is judged, every task the session started for a
        frame or worker has ended, and no block runs."""
        while True:
            await self._transport.drain_events()
            pending = [task for task in self._tasks if not task.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
                continue
            if not self._settled.is_set():
                await self._settled.wait()
                continue
            return

    async def close(self) -> None:
        """End the session's own work, before its transport closes: every task it started for a
        frame or a worker is cancelled, and one started after this is cancelled before it runs.
        A command of the dispatcher's own step that the closing transport then fails (a restored
        page's frames being read, a blank or a teardown in flight) is the session ending, not a
        page it cannot judge: it records nothing, falls back to nothing and quarantines nothing.
        A denial already recorded stays recorded.

        WHY before the transport: a frame still being judged when the run ends would otherwise go
        on, fail on the closed socket, and record a denial for a frame on an allowed host (a
        block, an SEL row, a quarantine and its errors) after the run that opened the session
        was over. A paused frame left so shows nothing; it loads once the socket closes, as a
        quarantined session's paused frames do. Idempotent.

        The wait for the cancelled tasks is bounded (:func:`cancel_and_wait`): one that outlives
        its cancel is logged and left, so closing a session never holds its closer."""
        self._closed = True
        await cancel_and_wait(list(self._tasks), what="browse: closing a session")

    @property
    def page_transport(self) -> _PageTransport:
        """The wire the page driver reads and acts through: this session's transport, closed
        while the session is quarantined, and held while a teardown or a frame's blank runs.

        Without it the driver held the raw transport, so a session quarantined with the denied
        document still loaded (a teardown the browser refused) went on refusing navigation while
        the page's text, its DOM, a screenshot, an evaluate and every action still reached the
        denied page.
        """
        return _PageTransport(self)

    # ── setup ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Install the in-page guard BEFORE anything can navigate, then watch every frame.

        Order is the whole point: the event listener is registered first (so no
        ``Page.frameNavigated`` is missed), then ``Page.enable``, then the guard script via
        ``Page.addScriptToEvaluateOnNewDocument`` — which applies to every document created
        from here on, so the guard is in place before the first page's own scripts run.
        ``_started`` flips only after the script is acknowledged; if the injection fails the
        session is quarantined and :meth:`navigate` refuses, because a session that can
        navigate without the guard installed is the failure this ordering exists to prevent.

        Then ``Target.setAutoAttach`` (:data:`AUTO_ATTACH`) asks the browser to report every frame
        and worker the page starts, paused until judged. It reports the frames already running
        in a process of their own before it answers; each is judged, and watched or blanked,
        within :data:`FRAME_START_BOUND`. Past it the page is blanked rather than shown with a
        frame unjudged; the session still starts. A session that cannot watch the frames at all
        refuses to start.

        Then every frame ``Page.getFrameTree`` lists is judged
        (:meth:`_judge_inherited_document`): ``Page.enable`` does not replay the tab's document
        as a ``Page.frameNavigated``, so without this a fresh session would read whatever the
        tab was left on. A denied top frame is torn down and a denied child is blanked; a session
        that cannot do either, or cannot read the frames, raises and is quarantined.

        Idempotent: a second call is a no-op rather than a second injection.
        """
        if self._started:
            return
        self._transport.set_event_listener(self.handle_event)
        try:
            # An unavailable BROWSE profile yields an EMPTY allow-list, which is the
            # strictest input the in-page guard can be given, not a wider one.
            self._script_allow_hosts = tuple(self._layered_policy().allow_hosts)
        except Exception as exc:
            logger.warning("browse: no BROWSE allow_hosts for the guard script (%s)", exc)
            self._script_allow_hosts = ()
        try:
            from personalclaw.browse.safety_script import safety_script

            source = safety_script(allow_hosts=self._script_allow_hosts)
            await self._transport.send(PAGE_ENABLE, {})
            await self._transport.send(ADD_SCRIPT, {"source": source})
        except Exception as exc:
            self._quarantine = (
                f"the in-page guard script was not installed ({exc}); this session refuses "
                "to navigate rather than load a page unguarded"
            )
            raise CdpSessionError(self._quarantine) from exc
        self._starting = True
        try:
            try:
                await self._transport.send(SET_AUTO_ATTACH, _auto_attach())
                await self._start_barrier()
            except Exception as exc:
                self._quarantine = self._quarantine or (
                    f"the page's frames could not be watched ({exc}); this session refuses to "
                    "navigate rather than show a frame it cannot judge"
                )
                raise CdpSessionError(self._quarantine) from exc
        finally:
            self._starting = False
        if self._quarantine:
            raise CdpSessionError(self._quarantine)
        await self._judge_inherited_document()
        self._started = True

    async def _start_barrier(self) -> None:
        """Wait, within the bound, until the frames already running when start() began are
        judged and every block they caused has ended. A paused frame is not waited for: it shows
        nothing until it is let run. On the bound the page is blanked whole: a blank of one frame
        whose renderer is that slow would wait on the same renderer.

        The bound is read off the session's clock, and holds for the drain too: the dispatcher
        judges a frame already running, and blanks a denied one, before it takes the next event,
        so a drain waits on that, and a slow lookup or a blank the browser never answers would
        otherwise hold start() past the bound."""
        deadline = self._clock() + self._frame_start_bound
        settled: asyncio.Future[Any] | None = None
        try:
            while True:
                remaining = deadline - self._clock()
                pending: list[asyncio.Task[Any]] = []
                if remaining > 0:
                    try:
                        await asyncio.wait_for(self._transport.drain_events(), remaining)
                    except asyncio.TimeoutError:
                        remaining = 0.0
                    else:
                        pending = [task for task in self._start_arming if not task.done()]
                        if not pending and self._settled.is_set():
                            return
                        remaining = deadline - self._clock()
                if remaining <= 0:
                    blocked = self._record_block(
                        _deny(
                            "",
                            f"the page's frames could not all be judged within "
                            f"{self._frame_start_bound:g}s; the page is blanked rather than "
                            "shown unjudged",
                        ),
                        operation=SET_AUTO_ATTACH,
                        phase="start",
                    )
                    await self._enforce(blocked)
                    return
                waiters: list[asyncio.Future[Any]] = list(pending)
                if not self._settled.is_set():
                    settled = asyncio.ensure_future(self._settled.wait())
                    waiters.append(settled)
                await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                if settled is not None:
                    settled.cancel()
                    settled = None
        finally:
            if settled is not None:
                settled.cancel()

    async def _judge_inherited_document(self) -> None:
        """Judge every frame ``Page.getFrameTree`` lists, as :meth:`handle_event` judges a commit.

        A denied top frame tears the page down (the teardown blanks the top frame, which takes
        every child frame with it); a denied child frame is blanked on its own. Raises
        :class:`CdpSessionError` when the frames cannot be read, or when a block quarantined the
        session."""
        try:
            reply = await self._transport.send(GET_FRAME_TREE, {})
            records = _frame_records((reply or {}).get("frameTree"))
        except Exception as exc:
            self._quarantine = (
                f"the page's frames could not be read ({exc}); this session refuses to read or "
                "navigate a page whose document it could not judge"
            )
            raise CdpSessionError(self._quarantine) from exc
        await self._judge_frames(records, operation=GET_FRAME_TREE, phase="start")
        if self._quarantine:
            raise CdpSessionError(self._quarantine)

    # ── the gate ─────────────────────────────────────────────────────────────

    def _layered_policy(self) -> EgressPolicy:
        """The BROWSE posture WITH the operator's ``security.egress`` layered on.

        ``egress_policy_for`` — never the bare profile — so a self-hoster's ``deny_hosts``
        still bans a host the profile would otherwise allow, and their ``allow_hosts``
        still reaches their LAN. The import is function-local so this module stays
        importable (and its tests runnable) before BROWSE lands in ``net/policy.py``, and
        so the operator's config is re-read per navigation instead of frozen at import.
        """
        from personalclaw.net.policy import BROWSE

        return egress_policy_for(BROWSE)

    def _gate(self, url: str) -> GuardDecision:
        """Judge ``url``. NEVER raises — every failure mode returns a DENY."""
        try:
            policy = self._layered_policy()
        except Exception as exc:
            return GuardDecision(
                allow=False,
                url=url,
                host=_host_of(url),
                reason=f"no BROWSE egress policy is available ({exc}); browsing fails closed",
                risk_level="destructive",
                recovery_hints=["This build cannot resolve the BROWSE egress profile."],
            )
        try:
            kwargs: dict[str, Any] = {} if self._resolver is None else {"resolver": self._resolver}
            return evaluate(url, policy, **kwargs)
        except Exception as exc:
            return GuardDecision(
                allow=False,
                url=url,
                host=_host_of(url),
                reason=f"the egress guard raised while judging this URL ({exc}); failing closed",
                risk_level="destructive",
            )

    async def _judge(self, url: str, *, paused: bool = False) -> GuardDecision:
        """Judge *url* off the event loop, at most :data:`_JUDGEMENTS_IN_FLIGHT` at a time of
        each kind: for a frame that is ``paused``, and for everything else.

        The guard may look the host up, and a lookup blocks: on the loop it would hold every
        other frame, every read of the page and the owner's other work behind one slow resolver.
        A paused frame shows nothing until it is judged, so its judgement holds nothing; the
        others hold page commands (the dispatcher's, through a drain), so they never wait for a
        slot behind slow lookups of paused frames. The run the session was opened for goes with
        the call, so the run's own egress tier still decides."""
        async with self._paused_slots if paused else self._slots:
            return await self._blocking(self._gate, url)

    # ── navigation ───────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> NavigationOutcome:
        """Pre-flight ``url`` and, only if it is allowed, send ``Page.navigate``.

        Waits until every event read so far is judged and no block runs, so a navigation is
        judged against the session as the blocks leave it: quarantined, it is refused. The wait
        is made again after the judgement, which yields. A navigation the browser answers without
        loading a document (``errorText``, or a download) is not ok: the page still holds the
        document it held, which this navigation never replaced."""
        await self._quiesce_or_quarantine()
        if self._quarantine:
            return self._refused(url, self._quarantine)
        if not self._started:
            return self._unstarted(url)

        decision = await self._judge(url)
        if not decision.allow:
            return self._record_block(decision, operation=NAVIGATE, phase="preflight")
        await self._quiesce_or_quarantine()
        if self._quarantine:
            return self._refused(url, self._quarantine)

        try:
            reply = await self._transport.send(NAVIGATE, {"url": url})
        except Exception as exc:
            # The message may or may not have reached the browser, so the page's state is
            # unknown. Quarantine rather than retry: an unknown page under a gate that
            # cannot see it is exactly the state this module exists to prevent.
            self._quarantine = (
                f"the CDP transport failed during {NAVIGATE} ({exc}); the browser's state "
                "is unknown, so this session no longer navigates"
            )
            return NavigationOutcome(
                ok=False,
                allowed=True,
                url=url,
                host=decision.host,
                error=str(exc),
                reason=self._quarantine,
                risk_level="caution",
                recovery_hints=["Discard this session and open a new one."],
            )

        failed = _navigation_failed(reply)
        if failed:
            return NavigationOutcome(
                ok=False,
                allowed=True,
                url=url,
                host=decision.host,
                error=failed,
                reason=(
                    f"{NAVIGATE} loaded no document ({failed}), so the page still holds what it "
                    "held before, which this navigation did not replace"
                ),
                risk_level="caution",
                recovery_hints=["Check that the URL serves a page, then navigate again."],
            )

        return NavigationOutcome(
            ok=True,
            allowed=True,
            url=url,
            host=decision.host,
            pinned_ips=list(decision.pinned_ips),
            risk_level=decision.risk_level,
        )

    async def go_back(self) -> NavigationOutcome:
        """Go back one page, each history entry pre-flighted before it is loaded.

        One page back is the latest earlier history entry whose address differs from the page's
        own: a frame's own entries (a frame that navigated, or was blanked) are skipped, so going
        back leaves the page rather than going back inside one of its frames. Each such entry is
        judged as a committed document is (:func:`_document_url`) before it is loaded; a denied
        one is recorded, as a refused navigation is, and skipped for the entry before it.

        WHY: an entry is loaded from its host as a navigation is, so going back without it would
        ask a denied host for its document. The page a teardown left (the denied document, then
        the blank) was requested again, torn down again once it loaded, and the run kept landing
        on it. The commit is still judged when the browser reports it, as every commit is.

        Not ok, with nothing loaded, when there is no earlier page (``allowed``) or every earlier
        page is denied (not ``allowed``, with the reason)."""
        await self._quiesce_or_quarantine()
        if self._quarantine:
            return self._refused("", self._quarantine)
        if not self._started:
            return self._unstarted("")
        history = await self._transport.send(GET_NAVIGATION_HISTORY, {})
        entries = [e if isinstance(e, dict) else {} for e in (history or {}).get("entries") or []]
        current = (history or {}).get("currentIndex")
        if not isinstance(current, int) or not 0 <= current < len(entries):
            return NavigationOutcome(ok=False, allowed=True, reason="there is no earlier page")
        here = entries[current].get("url")
        denied: NavigationOutcome | None = None
        for entry in reversed(entries[:current]):
            if entry.get("url") == here or entry.get("id") is None:
                continue
            url = str(entry.get("url") or "")
            judged = _document_url(url)
            if judged is not None:
                decision = await self._judge(judged)
                if not decision.allow:
                    denied = self._record_block(
                        decision, operation=NAVIGATE_TO_HISTORY_ENTRY, phase="preflight"
                    )
                    continue
            await self._quiesce_or_quarantine()
            if self._quarantine:
                return self._refused(url, self._quarantine)
            await self._transport.send(NAVIGATE_TO_HISTORY_ENTRY, {"entryId": entry["id"]})
            return NavigationOutcome(ok=True, allowed=True, url=url, host=_host_of(url))
        if denied is None:
            return NavigationOutcome(ok=False, allowed=True, reason="there is no earlier page")
        return NavigationOutcome(
            ok=False,
            allowed=False,
            url=denied.url,
            host=denied.host,
            reason=f"every earlier page is one this run may not reach ({denied.reason})",
            risk_level=denied.risk_level,
        )

    @staticmethod
    def _unstarted(url: str) -> NavigationOutcome:
        return NavigationOutcome(
            ok=False,
            allowed=False,
            url=url,
            host=_host_of(url),
            reason=(
                "the session was never started, so the in-page guard script is not "
                "installed and the first document would load unguarded"
            ),
            risk_level="destructive",
            recovery_hints=["await session.start() before navigating."],
        )

    @staticmethod
    def _refused(url: str, reason: str) -> NavigationOutcome:
        return NavigationOutcome(
            ok=False,
            allowed=False,
            url=url,
            host=_host_of(url),
            reason=reason,
            risk_level="destructive",
            recovery_hints=["Discard this session and open a new one."],
        )

    async def _quiesce_or_quarantine(self) -> None:
        """:meth:`_quiesce`; a page whose events cannot be judged quarantines the session."""
        try:
            await self._quiesce()
        except Exception as exc:
            self._quarantine = self._quarantine or (
                f"the page's events could not be judged ({exc}); this session no longer reads "
                "or navigates a page it cannot judge"
            )

    # ── what the browser reports ─────────────────────────────────────────────

    async def handle_event(
        self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None
    ) -> None:
        """CDP event sink: judges every commit of the page and of each of its frames, and every
        frame and worker the browser attaches.

        ``session_id`` is the session the event came on: ``None`` for the page's own, or the
        session the browser attached for a frame of the page. Never raises: a frame event this
        session cannot judge blanks the page (:meth:`_fail_closed`), because the transport logs
        and drops a listener that raises.

        Judged even while a teardown runs: start()'s teardown runs on the caller's task, and a
        denied commit reported meanwhile is a document of its own. The teardown's own
        about:blank (and the browser's error page) is no network document, so judging cannot
        recurse."""
        params = params or {}
        if method == FRAME_NAVIGATED:
            frame = params.get("frame") or {}
            if not isinstance(frame, dict):
                frame = {}
            if session_id is None and not frame.get("parentId"):
                await self._top_commit(frame, params)
                return
            await self._fail_closed(method, self._frame_commit(frame))
            return
        if method in (FRAME_STARTED_NAVIGATING, FRAME_REQUESTED_NAVIGATION):
            self._navigation_started(method, params)
            return
        if method == ATTACHED_TO_TARGET:
            await self._fail_closed(method, self._on_attached(params))
            return
        if method == TARGET_INFO_CHANGED:
            await self._fail_closed(method, self._info_changed(params))

    async def _top_commit(self, frame: dict[str, Any], params: dict[str, Any]) -> None:
        """A commit of the page's own document. A new document: every frame the session knew,
        and every frame block it recorded, belonged to the one it replaced. A page restored from
        the back-forward cache comes back with the frames it had, so they are judged again."""
        self._judged.clear()
        self._frame_blocks.clear()
        url = str(frame.get("url") or "")
        judged = _document_url(url)
        if judged is None:
            return
        decision = await self._judge(judged)
        if not decision.allow:
            blocked = self._record_block(
                decision, operation=FRAME_NAVIGATED, phase="frame_navigated"
            )
            await self._enforce(blocked)
            return
        if params.get("type") != "BackForwardCacheRestore":
            return
        try:
            reply = await self._transport.send(GET_FRAME_TREE, {})
            records = _frame_records((reply or {}).get("frameTree"))
        except Exception as exc:
            if self._closed:
                logger.debug("browse: a restored page's frames were left as the session closed")
                return
            blocked = self._record_block(
                _deny(
                    url, f"the frames of a page restored from the cache could not be read ({exc})"
                ),
                operation=GET_FRAME_TREE,
                phase=PHASE_RESTORED,
            )
            await self._enforce(blocked)
            return
        await self._judge_frames(records[1:], operation=GET_FRAME_TREE, phase=PHASE_RESTORED)

    def _navigation_started(self, method: str, params: dict[str, Any]) -> None:
        """Remember where a frame's navigation is going, for the attach that reports the frame
        with no URL of its own. Only a navigation for the frame itself counts
        (``frameRequestedNavigation`` names a new tab or window too). Never raises."""
        try:
            if method == FRAME_REQUESTED_NAVIGATION and params.get("disposition") != "currentTab":
                return
            fid, url = params.get("frameId"), params.get("url")
            if isinstance(fid, str) and fid and isinstance(url, str) and url:
                self._remember(self._frame_starts, fid, url)
        except Exception:
            logger.warning("browse: a frame's navigation start could not be read", exc_info=True)

    async def _fail_closed(self, method: str, step: Awaitable[Any]) -> None:
        """Run one frame step; any failure blanks the whole page. The dispatcher logs and drops
        a listener that raises, which would leave the frame as the browser loaded it."""
        try:
            await step
        except Exception as exc:
            logger.warning("browse: a frame event could not be judged (%s)", exc)
            blocked = self._record_block(
                _deny("", f"the browser reported a frame this session could not judge ({exc})"),
                operation=method,
                phase=PHASE_FRAME_UNJUDGED,
            )
            await self._enforce(blocked)

    async def _frame_commit(self, frame: dict[str, Any]) -> None:
        """A child frame committed a document: in the page's own process (the page session
        reports it with a ``parentId``), or in a process of its own (its session reports it)."""
        fid = str(frame.get("id") or "")
        url = str(frame.get("url") or "")
        if fid and self._judged.get(fid) == url:
            return
        if fid:
            self._remember(self._judged, fid, url)
        judged = _document_url(url)
        if judged is None or _is_extension(url):
            return
        decision = await self._judge(judged)
        if decision.allow:
            return
        blocked = self._record_block(
            decision,
            operation=FRAME_NAVIGATED,
            phase=PHASE_FRAME_COMMITTED,
            frame=True,
        )
        await self._block_frame(fid, blocked, exposing=True)

    async def _info_changed(self, params: dict[str, Any]) -> None:
        """A frame in a process of its own moved: it committed a new document."""
        info = params.get("targetInfo")
        if not isinstance(info, dict):
            raise ValueError("the browser reported a target change with no target")
        if info.get("type") != "iframe":
            return
        url = str(info.get("url") or "")
        tid = str(info.get("targetId") or "")
        if not url or (tid and self._judged.get(tid) == url):
            return
        if tid:
            self._remember(self._judged, tid, url)
        judged = _document_url(url)
        if judged is None or _is_extension(url):
            return
        decision = await self._judge(judged)
        if decision.allow:
            return
        blocked = self._record_block(
            decision,
            operation=TARGET_INFO_CHANGED,
            phase=PHASE_FRAME_COMMITTED,
            frame=True,
        )
        await self._block_frame(tid, blocked, exposing=True)

    async def _on_attached(self, params: dict[str, Any]) -> None:
        """The browser attached a frame or a worker of the page, paused when it is new.

        Validated first: an attach this session cannot read raises, and the page is blanked. A
        worker is let run, as it ran before; a target of a type this session does not know is
        left paused. A paused frame is judged in a task of its own, since it shows nothing until
        it is let run; a running one is judged here."""
        if not isinstance(params, dict):
            raise ValueError("the browser reported an attach with no parameters")
        sid = params.get("sessionId")
        info = params.get("targetInfo")
        if not isinstance(sid, str) or not sid or not isinstance(info, dict):
            raise ValueError("the browser reported an attach with no session or no target")
        kind = info.get("type")
        tid = info.get("targetId")
        if not isinstance(kind, str) or not isinstance(tid, str) or not tid:
            raise ValueError("the browser reported an attach with no target type or id")
        waiting = params.get("waitingForDebugger") is True
        info_url = str(info.get("url") or "")
        if kind != "iframe":
            if kind in RESUMED_TARGETS:
                if waiting:
                    self._spawn(self._resume(sid))
                return
            # One that attached already running was never paused, and its row says so: it runs
            # unjudged, so its row is no denial.
            left = "left paused" if waiting else "left running, unjudged"
            logger.warning("browse: a %s target is %s", kind, left)
            self._audit(
                NavigationOutcome(
                    ok=False,
                    allowed=False,
                    url=info_url,
                    host=_host_of(info_url),
                    reason=f"a {kind!r} target this session cannot judge is {left}",
                    risk_level="caution",
                ),
                operation=ATTACHED_TO_TARGET,
                phase=PHASE_TARGET_LEFT_PAUSED if waiting else PHASE_TARGET_LEFT_RUNNING,
                unjudged=not waiting,
            )
            return
        started = self._frame_starts.pop(tid, "")
        if info.get("subtype") == "fenced":
            # A fenced frame is a frame tree of its own: it cannot be reached by id, so it is
            # never blanked by id or watched. Paused, it is left unloaded; running, the page goes.
            blocked = self._record_block(
                _deny(info_url or started, "a fenced frame cannot be judged or reached by id"),
                operation=ATTACHED_TO_TARGET,
                phase=PHASE_FRAME_ATTACHED,
                frame=True,
            )
            if waiting:
                blocked.blanked = LEFT_UNLOADED
            else:
                await self._blank_page_for(blocked)
            return
        if waiting:
            self._spawn(self._judge_paused(sid, tid, started, info_url))
            return
        await self._judge_attach(sid, tid, False, started, info_url)

    async def _judge_paused(self, sid: str, tid: str, started: str, info_url: str) -> None:
        """Judge a paused frame in its own task; any failure blanks it, never lets it run."""
        try:
            await self._judge_attach(sid, tid, True, started, info_url)
        except Exception as exc:
            blocked = self._record_block(
                _deny(info_url or started, f"the frame could not be judged ({exc})"),
                operation=ATTACHED_TO_TARGET,
                phase=PHASE_FRAME_UNJUDGED,
                frame=True,
            )
            await self._block_frame(tid, blocked, exposing=False)

    async def _judge_attach(
        self, sid: str, tid: str, waiting: bool, started: str, info_url: str
    ) -> None:
        """Judge a frame the browser attached by every URL known for it: where its navigation
        started, and the URL it reports. No URL at all is a deny: the frame's first document
        would load unjudged. Allowed, it is watched (:meth:`_arm`); denied, it is blanked.

        A frame that attached running after start may already show frames of its own, so page
        commands wait while they are judged (:meth:`_watch_running`)."""
        candidates = [url for url in (started, info_url) if url]
        denied: GuardDecision | None = None
        if not candidates:
            denied = _deny(
                "", "the browser did not say where this frame was going before it loaded"
            )
        for url in candidates:
            judged = _document_url(url)
            if judged is None or _is_extension(url):
                continue
            decision = await self._judge(judged, paused=waiting)
            if not decision.allow:
                denied = decision
                break
        if denied is not None:
            # Judged once: the same commit reported again (by the frame's session, or as a target
            # change) is not blanked or recorded a second time.
            self._remember(self._judged, tid, info_url or started)
            blocked = self._record_block(
                denied,
                operation=ATTACHED_TO_TARGET,
                phase=PHASE_FRAME_ATTACHED,
                frame=True,
            )
            await self._block_frame(tid, blocked, exposing=not waiting)
            return
        self._remember(self._judged, tid, info_url or started)
        if waiting or self._starting:
            # start()'s barrier waits for a frame that was running when it began, within its
            # bound; a paused frame shows nothing until it is let run.
            self._spawn(self._arm(sid, tid, waiting), barrier=not waiting)
            return
        # Taken here, in the dispatcher's step, so no page command sent after this event goes
        # out before the hold; lifted when the watch ends, however it ends.
        self._hold()
        watch = self._spawn(self._watch_running(sid, tid))
        watch.add_done_callback(lambda _task: self._release())

    async def _watch_running(self, sid: str, tid: str) -> None:
        """Watch a frame that attached already running after start, under a hold that keeps page
        commands back until the frames inside it are judged, within :data:`FRAME_START_BOUND`.

        WHY a hold: such a frame (a page restored from the back-forward cache brings its frames
        back so) may already show a denied frame inside it, which only its own frame tree lists,
        so a capture sent meanwhile would show it. Non-exposing: the frame itself was judged
        allowed; a denied frame inside it is blanked as having loaded, which discards a reply
        that straddles that blank. WHY the page is blanked past the bound, as at start: a frame
        whose renderer is that slow would otherwise hold every page command until the command
        timeout."""
        arm = self._spawn(self._arm(sid, tid, False))
        await asyncio.wait({arm}, timeout=self._frame_start_bound)
        if arm.done():
            return
        blocked = self._record_block(
            _deny(
                self._judged.get(tid, ""),
                f"the frames inside a frame that was already running could not be judged within "
                f"{self._frame_start_bound:g}s; the page is blanked rather than shown unjudged",
            ),
            operation=ATTACHED_TO_TARGET,
            phase=PHASE_FRAME_UNJUDGED,
        )
        await self._enforce(blocked)

    async def _arm(self, sid: str, tid: str, waiting: bool) -> None:
        """Watch an allowed frame in a process of its own, then let it run if it was paused.

        Its page events first, then its own frames reported: the other order drops the start of
        a nested navigation that begins between the two, and that frame would then be blanked
        for want of a URL. A frame that was already running has its frame tree judged: the frames
        inside it, and its own document as a frame, since a commit between its attach and its page
        events being enabled is reported nowhere else (unchanged, it is not judged again). A frame
        that went away meanwhile needs nothing; any other failure blanks it rather than let it
        load unwatched."""
        try:
            await self._transport.send(PAGE_ENABLE, {}, session_id=sid)
            await self._transport.send(SET_AUTO_ATTACH, _auto_attach(), session_id=sid)
            await self._transport.drain_events()
            if not waiting:
                tree = await self._transport.send(GET_FRAME_TREE, {}, session_id=sid)
                records = _frame_records((tree or {}).get("frameTree"))
                await self._judge_frames(
                    [(fid, False, url) for fid, _top, url in records],
                    operation=GET_FRAME_TREE,
                    phase=PHASE_FRAME_ATTACHED,
                )
        except Exception as exc:
            if _gone(exc):
                return
            blocked = self._record_block(
                _deny(
                    self._judged.get(tid, ""),
                    f"the frame could not be watched ({exc}); it is blanked rather than loaded "
                    "unjudged",
                ),
                operation=ATTACHED_TO_TARGET,
                phase=PHASE_FRAME_ATTACHED,
                frame=True,
            )
            await self._block_frame(tid, blocked, exposing=not waiting)
            return
        if waiting:
            await self._resume(sid)

    async def _resume(self, sid: str) -> None:
        """Let a paused target run. A failure leaves it paused, which shows nothing."""
        try:
            await self._transport.send(RUN_IF_WAITING, {}, session_id=sid)
        except Exception as exc:
            logger.info("browse: a paused target could not be let run (%s)", exc)

    async def _judge_frames(
        self, records: list[tuple[str, bool, str]], *, operation: str, phase: str
    ) -> bool:
        """Judge each ``(id, is_top, url)`` frame record. A denied top frame tears the page down;
        a denied child is blanked on its own. Returns False once a block reached the whole page.

        A record still on its first empty document (url ``""``) is skipped: nothing of it has
        loaded, and its next commit is reported."""
        for fid, top, url in records:
            if not url or (fid and self._judged.get(fid) == url):
                continue
            if fid:
                self._remember(self._judged, fid, url)
            judged = _document_url(url)
            if judged is None or (not top and _is_extension(url)):
                continue
            decision = await self._judge(judged)
            if decision.allow:
                continue
            if top:
                await self._enforce(self._record_block(decision, operation=operation, phase=phase))
                return False
            blocked = self._record_block(decision, operation=operation, phase=phase, frame=True)
            if not await self._block_frame(fid, blocked, exposing=True):
                return False
        return True

    # ── blocks ───────────────────────────────────────────────────────────────

    def _hold(self, *, exposing: bool = False) -> None:
        """Take the hold every page command and navigation waits on. Synchronous, so nothing can
        be sent between deciding to block and the hold being in place."""
        self._running += 1
        if exposing:
            self._teardowns += 1
            self._exposing += 1
        self._settled.clear()

    def _release(self, *, exposing: bool = False) -> None:
        self._running -= 1
        if exposing:
            self._exposing -= 1
        if not self._running:
            self._settled.set()

    async def _block_frame(self, fid: str, blocked: NavigationOutcome, *, exposing: bool) -> bool:
        """Blank one denied frame of the page: ``Page.navigate`` to ``about:blank`` with its
        ``frameId``, sent on the PAGE's session. True when only the frame went, or it was gone.

        WHY the page's session: it looks the frame up across every frame of the page, in every
        process, and always answers; a frame's own session detaches as the blank lands, before it
        answers. WHY no ``Page.stopLoading``: it is the whole tab's, and stops the allowed page's
        own loads with it. WHY "No frame with given id found" counts as blocked: the browser
        looked through every frame of the page and the frame is in none of them, so nothing of it
        can be shown. Any other failure, a blank that loaded nothing, or no frame id at all,
        blanks the whole page (:meth:`_enforce`), which quarantines if that fails too.

        ``exposing`` says the frame's document had committed: a page reply that straddles the
        blank may have been read off it, and is discarded. A frame blanked before its document
        loaded showed nothing, so it holds page commands but discards nothing. A denied frame is
        never let run."""
        self._hold(exposing=exposing)
        lock: asyncio.Lock | None = None
        try:
            if not fid:
                await self._blank_page_for(blocked)
                return False
            # Wait for another blank of this frame still running, within a bound: sent beside it,
            # either blank would make the browser answer the other "aborted".
            lock = self._frame_lock(fid)
            try:
                await asyncio.wait_for(lock.acquire(), FRAME_BLANK_WAIT)
            except asyncio.TimeoutError:
                lock = None
            deadline = self._clock() + TEARDOWN_RETRY_BOUND
            try:
                reply = await self._teardown_step(
                    NAVIGATE, {"url": BLANK_URL, "frameId": fid}, deadline
                )
            except Exception as exc:
                if FRAME_GONE in str(exc):
                    return True
                if self._closed:
                    logger.debug("browse: a denied frame's blank ended with the session (%s)", exc)
                    return False
                logger.warning("browse: a denied frame could not be blanked on its own (%s)", exc)
                await self._blank_page_for(blocked)
                return False
            failed = _navigation_failed(reply)
            if failed:
                logger.warning("browse: a denied frame's blank loaded nothing (%s)", failed)
                await self._blank_page_for(blocked)
                return False
            return True
        finally:
            if lock is not None:
                lock.release()
            self._release(exposing=exposing)

    async def _blank_page_for(self, blocked: NavigationOutcome) -> None:
        """Blank the whole page in place of a denied frame that could not be blanked on its own,
        and say so on the frame's block, so the run is told the page went, not the frame.

        A block counted on an earlier one of its host (:meth:`_record_block`) is recorded after
        all, with the SEL row it would have had: the earlier block says only that its own frame
        was blanked, and the page going is what the run and the audit log are owed. It costs a
        row only when the whole page goes, which starts a new document and so a new count."""
        blocked.blanked = BLANKED_PAGE
        if blocked.counted_on is not None:
            first, operation, phase = blocked.counted_on
            blocked.counted_on = None
            first.repeats -= 1
            self._blocks.append(blocked)
            self._audit(blocked, operation=operation, phase=phase)
        await self._enforce(blocked)

    async def _enforce(self, blocked: NavigationOutcome) -> None:
        """Tear the denied document down: ``Page.stopLoading`` then blank the page.

        The whole-page teardown: for a denied top document, and the fallback when a frame's own
        blank fails.

        WHY teardown and not a log line: by the time ``Page.frameNavigated`` arrives the
        browser is already ON the denied origin, so "record it" leaves the agent reading a
        page the policy forbade. WHY both steps: ``stopLoading`` alone kills the in-flight
        subresources but leaves the denied document (and its script context) in place, so
        the page is blanked as well — a same-process teardown that discards the DOM the
        agent would otherwise extract.

        WHY a resend on "Not attached to an active page" only: right after a cross-site
        navigation the page's session refuses commands for a few milliseconds, and the same
        command is accepted on the same socket once it is back (measured 6.5-25 ms). Each step is
        sent again every :data:`TEARDOWN_RETRY_INTERVAL` until :data:`TEARDOWN_RETRY_BOUND` has
        passed for the whole teardown. Only these two commands and a frame's blank are ever
        resent, and none can take the page or a frame anywhere but ``about:blank``.

        If a step cannot be delivered (any other refusal, or a page still detached past the
        bound), the block is unenforceable: the session is quarantined, so no further navigation
        happens through it and :attr:`page_transport` refuses every read. The page is still sent
        to ``about:blank`` once if it was not yet, so a browser that would take that navigation
        does not keep the denied document loaded. A blank the browser answers with ``errorText``
        loaded no document, so the denied one may still be there: that quarantines too.

        Teardowns can overlap (start()'s and the dispatcher's), so the running ones are counted
        and the hold is lifted only when the last one ends. While they overlap, one teardown's
        command supersedes the other's pending blank, so an ``errorText`` blank quarantines only
        a teardown running alone: every other one still sends a later blank and judges its reply.
        Whole-page teardowns are counted on their own: a frame block beside one sends no top
        blank, so it never excuses one.
        """
        self._hold(exposing=True)
        self._page_running += 1
        deadline = self._clock() + TEARDOWN_RETRY_BOUND
        blanked = False
        try:
            await self._teardown_step(STOP_LOADING, {}, deadline)
            blanked = True
            reply = await self._teardown_step(NAVIGATE, {"url": BLANK_URL}, deadline)
            failed = _navigation_failed(reply)
            # With another teardown running, its later command superseded this blank (the browser
            # answers that net::ERR_ABORTED), and its own later blank is judged on its own reply.
            if failed and self._page_running == 1:
                raise RuntimeError(f"{NAVIGATE} to {BLANK_URL} loaded no document ({failed})")
        except Exception as exc:
            if self._closed:
                logger.debug("browse: a teardown ended with the session (%s)", exc)
                return
            self._quarantine = (
                f"could not enforce the block on {blocked.host or blocked.url!r} ({exc}); "
                "this session no longer navigates"
            )
            logger.error("browse: %s", self._quarantine)
            if not blanked:
                try:
                    await self._transport.send(NAVIGATE, {"url": BLANK_URL})
                except Exception as last:
                    logger.error("browse: the quarantined page was not blanked either (%s)", last)
        finally:
            self._page_running -= 1
            self._release(exposing=True)

    async def _teardown_step(
        self, method: str, params: dict[str, Any], deadline: float
    ) -> dict[str, Any]:
        """Send one teardown command, sent again while the page is detached, until *deadline*.
        Returns the browser's reply."""
        while True:
            try:
                return await self._transport.send(method, params)
            except Exception as exc:
                if PAGE_DETACHED not in str(exc):
                    raise
                if self._clock() >= deadline:
                    raise _PageStayedDetached(
                        f"{method} was refused while the page stayed detached for "
                        f"{TEARDOWN_RETRY_BOUND:g}s: {exc}"
                    ) from exc
            await self._sleep(TEARDOWN_RETRY_INTERVAL)

    # ── bookkeeping ──────────────────────────────────────────────────────────

    def _spawn(self, step: Awaitable[Any], *, barrier: bool = False) -> asyncio.Task[Any]:
        """Run *step* as one of the session's own tasks; once the session is closed it is
        cancelled before it runs (:meth:`close`)."""
        task: asyncio.Task[Any] = asyncio.ensure_future(step)
        if self._closed:
            task.cancel()
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if barrier:
            self._start_arming.add(task)
            task.add_done_callback(self._start_arming.discard)
        return task

    def _frame_lock(self, fid: str) -> asyncio.Lock:
        lock = self._frame_locks.get(fid)
        if lock is None:
            lock = asyncio.Lock()
        self._remember(self._frame_locks, fid, lock)
        return lock

    @staticmethod
    def _remember(memory: OrderedDict[Any, Any], key: Any, value: Any) -> None:
        memory[key] = value
        memory.move_to_end(key)
        while len(memory) > _FRAME_MEMORY:
            memory.popitem(last=False)

    # ── audit ────────────────────────────────────────────────────────────────

    def _record_block(
        self,
        decision: GuardDecision,
        *,
        operation: str,
        phase: str,
        frame: bool = False,
    ) -> NavigationOutcome:
        """Record one denial: a block, and its SEL row.

        A frame's block on a page that already recorded one for the same host (the same frame
        re-pointed there, or another frame from it) is counted on that one
        (:attr:`NavigationOutcome.repeats`) and recorded no more: the frame is still blanked every
        time, but a page that keeps adding or re-pointing denied frames grows neither the blocks,
        nor the audit log, nor what the run is told. One whose own blank then fails is recorded
        after all (:meth:`_blank_page_for`), since the whole page went. A new host is a block of
        its own, and a new document starts over."""
        outcome = NavigationOutcome(
            ok=False,
            allowed=False,
            url=decision.url,
            host=decision.host,
            reason=decision.reason,
            risk_level=decision.risk_level or "destructive",
            recovery_hints=list(decision.recovery_hints),
            frame=frame,
            blanked=BLANKED_FRAME if frame else "",
        )
        if frame:
            key = outcome.host or outcome.url
            first = self._frame_blocks.get(key)
            if first is not None:
                first.repeats += 1
                outcome.counted_on = (first, operation, phase)
                return outcome
            self._remember(self._frame_blocks, key, outcome)
        self._blocks.append(outcome)
        self._audit(outcome, operation=operation, phase=phase)
        return outcome

    def _audit(
        self, outcome: NavigationOutcome, *, operation: str, phase: str, unjudged: bool = False
    ) -> None:
        """Write ONE SEL row for a denial, or, ``unjudged``, for a target that runs without being
        judged. Fails OPEN: audit never rescues a navigation.

        WHY ``ungated`` for the second: nothing was refused, and a ``denied`` row is filed under
        the audit surface's Denied family, which would assert a refusal that never happened. The
        word is the one a tool call that ran without asking the host writes, and no family claims
        it."""
        try:
            SecurityEventLog().log(
                SecurityEvent(
                    event_id=uuid.uuid4().hex[:16],
                    timestamp=_now_iso(),
                    event_type=SEL_EVENT_TYPE,
                    caller_identity=self._caller_identity,
                    agent=self._agent,
                    source=self._source,
                    operation=operation,
                    tool_kind=SEL_TOOL_KIND,
                    outcome="ungated" if unjudged else "denied",
                    resources=f"host={outcome.host}" if outcome.host else "host=",
                    metadata={
                        "host": outcome.host,
                        "url": outcome.url,
                        "reason": outcome.reason,
                        "risk_level": outcome.risk_level,
                        "cdp_method": operation,
                        "phase": phase,
                    },
                )
            )
        except Exception:  # audit must never turn a deny into a navigation
            logger.warning("browse: SEL write failed for a blocked navigation", exc_info=True)


class _PageTransport:
    """The page driver's view of a :class:`GatedCdpSession`'s transport.

    Every read and every action on the page (an evaluate for its text, DOM or URL, a screenshot,
    a click) goes through :meth:`send`, which refuses once the session is quarantined: a session
    that could not tear a denied document down may still have it loaded, and refusing to navigate
    is not enough while the page itself can still be read. A command waits until every event read
    before it is judged and no block runs, and its reply is discarded
    (:class:`PageReplyDiscarded`) when a block of a document that had committed began while it was
    in flight, because it may have been read off that document before it was blanked: that is
    judged after the events read with the reply, and the holds they took, so a frame that attached
    running has the frames inside it judged before the reply is handed over. A frame blocked
    before its document loaded discards nothing.
    """

    def __init__(self, session: GatedCdpSession) -> None:
        self._session = session

    def _refuse_if_quarantined(self, method: str) -> None:
        reason = self._session._quarantine
        if reason:
            raise CdpSessionError(f"{method} refused: the browse session is quarantined ({reason})")

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self._session
        self._refuse_if_quarantined(method)
        await session._quiesce_or_quarantine()
        self._refuse_if_quarantined(method)
        teardowns = session._teardowns
        # Positional: the page's own commands never name a session.
        reply = await session._transport.send(method, params)
        await session._quiesce_or_quarantine()
        # The discard is reported first, as it always was: a run reads it to know the reply was
        # thrown away, and a quarantined session refuses the read sent after it either way.
        if session._teardowns != teardowns or session._exposing:
            raise PageReplyDiscarded(
                f"{method}'s reply was discarded: a denied document was torn down or blanked "
                "while it ran"
            )
        self._refuse_if_quarantined(method)
        return reply

    async def go_back(self) -> NavigationOutcome:
        """Go back one page through the session's pre-flight (:meth:`GatedCdpSession.go_back`),
        refused once the session is quarantined, as every page command is."""
        self._refuse_if_quarantined(NAVIGATE_TO_HISTORY_ENTRY)
        return await self._session.go_back()
