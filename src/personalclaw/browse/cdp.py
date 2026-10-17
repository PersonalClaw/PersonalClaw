"""Gated CDP navigation for the browse loop.

A headless browser does its own DNS and its own connections, so it bypasses
``net/client.py``'s IP-pinning SSRF guard entirely (``web/render.py``'s docstring says the
same thing about Playwright, and names redirects as its residual gap). This module is the
chokepoint that closes that hole for the CDP path: **every** navigation goes through
``net/guard.py:evaluate`` against the BROWSE profile BEFORE a ``Page.navigate`` message is
written to the wire, and a client-side redirect is re-judged when the browser reports it.

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
   ``Page.frameNavigated`` is re-evaluated, and a denied one is **torn down** (see
   :meth:`GatedCdpSession._enforce`), not merely logged.
4. **The document a session inherits is judged too.** A page target outlives the sessions
   that drive it: a page can move itself after the last session closed, or a quarantined one
   can leave a denied document loaded. :meth:`start` judges every frame ``Page.getFrameTree``
   lists as a redirect is judged; a denied one is torn down, and a session that cannot tear it
   down, or cannot read the frames at all, refuses to start.
   A navigation the browser answers without loading a new document (``errorText``: a 204, an
   abort; or a download) is reported not-ok, because the page still holds what it held.

Everything fails CLOSED. An unparseable URL, a missing BROWSE profile, a guard that raises,
a transport that raises, an enforcement that cannot be delivered — none of them end in
"navigated anyway"; the worst case is a quarantined session that refuses to navigate at
all, and whose page is not read or acted on either: the page driver speaks through
:attr:`GatedCdpSession.page_transport`, which refuses every command once the session is
quarantined, and holds a command back while a teardown is still running. The one deliberate
fail-OPEN is the audit: a SEL write that raises must not turn a deny into a navigation.

A teardown command the browser refuses with "Not attached to an active page" is sent again,
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
  ``Page.frameNavigated``. Those are the in-page guard script's job (and, later, the
  ``Fetch``/``Network`` interception domains) — not this module's.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from personalclaw.net.guard import GuardDecision, evaluate
from personalclaw.net.policy import EgressPolicy, egress_policy_for
from personalclaw.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)

# CDP methods this module writes to the wire, named once so a test can assert on the
# exact strings the browser will see.
PAGE_ENABLE = "Page.enable"
ADD_SCRIPT = "Page.addScriptToEvaluateOnNewDocument"
NAVIGATE = "Page.navigate"
STOP_LOADING = "Page.stopLoading"
FRAME_NAVIGATED = "Page.frameNavigated"
GET_FRAME_TREE = "Page.getFrameTree"

#: Where a blocked page is sent. ``about:blank`` is not egress — it is the teardown.
BLANK_URL = "about:blank"

#: Frame URLs that are NOT a network document and therefore have nothing to gate: our own
#: teardown target, a same-document srcdoc frame, and Chrome's error page. Deliberately a
#: closed set rather than "anything without an http scheme": ``file:``, ``data:`` and
#: ``javascript:`` frames are judged (and denied) by the guard like any other URL.
_TEARDOWN_URLS = frozenset({BLANK_URL, "about:srcdoc"})

#: Chrome reports a failed/stopped load under this scheme; re-judging it would fight our
#: own enforcement.
_ERROR_URL_PREFIX = "chrome-error://"

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


class _PageStayedDetached(RuntimeError):
    """A teardown command was refused with :data:`PAGE_DETACHED` until the bound ran out."""


class CdpTransport(Protocol):
    """The CDP wire, injectable so the gate is testable without a browser.

    A real implementation is a WebSocket to the browser's debugger endpoint; the tests
    pass a fake that records every message. Keeping this a Protocol is what makes
    "blocked BEFORE ``Page.navigate`` fires" an assertion about the wire rather than about
    a return value.
    """

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one CDP command and return its result payload."""
        ...

    def set_event_listener(
        self, listener: Callable[[str, dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        """Register the coroutine that receives ``(method, params)`` for every CDP event."""
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host_of(url: str) -> str:
    """Best-effort host, for labelling a decision the guard itself could not reach."""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _ungated(url: str) -> bool:
    """A frame URL that is no network document: the teardown's own, srcdoc, Chrome's error page."""
    return url in _TEARDOWN_URLS or url.startswith(_ERROR_URL_PREFIX)


def _frame_urls(tree: Any) -> list[str]:
    """Every frame URL in a ``Page.getFrameTree`` reply's ``frameTree``, top frame first.

    Raises ``ValueError`` on a tree without a top frame: a page whose frames cannot be read
    cannot be judged, and the caller refuses rather than treat it as empty."""
    if not isinstance(tree, dict) or not isinstance(tree.get("frame"), dict):
        raise ValueError(f"the browser answered no frame tree ({tree!r:.120})")
    urls: list[str] = []
    stack = [tree]
    while stack:
        node = stack.pop(0)
        frame = node.get("frame") if isinstance(node, dict) else None
        if not isinstance(frame, dict):
            raise ValueError("the browser answered a frame with no frame record")
        urls.append(str(frame.get("url") or ""))
        stack.extend(node.get("childFrames") or [])
    return urls


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
    ) -> None:
        self._transport = transport
        self._caller_identity = caller_identity
        self._agent = agent
        self._source = source
        self._resolver = resolver
        self._sleep = sleep
        self._clock = clock
        self._started = False
        # How many teardowns are running now: start()'s, on the caller's task, can overlap the
        # dispatcher's, so the hold is lifted only when the last one ends.
        self._running = 0
        # Set while no teardown runs; a page command waits on it (`_PageTransport.send`).
        self._settled = asyncio.Event()
        self._settled.set()
        # How many teardowns have begun: a page reply that straddles one is discarded.
        self._teardowns = 0
        self._quarantine = ""
        self._blocks: list[NavigationOutcome] = []
        self._script_allow_hosts: tuple[str, ...] = ()

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
        """Every denial this session made, pre-flight and redirect alike."""
        return list(self._blocks)

    @property
    def tearing_down(self) -> bool:
        """Whether a teardown of a denied document is running now."""
        return not self._settled.is_set()

    async def _until_settled(self) -> None:
        """Return once no teardown is running. Loops, because a second teardown can start
        between the first one ending and this waiter running (back-to-back denied events, which
        the transport's dispatcher hands over without yielding)."""
        while not self._settled.is_set():
            await self._settled.wait()

    @property
    def page_transport(self) -> _PageTransport:
        """The wire the page driver reads and acts through: this session's transport, closed
        while the session is quarantined, and held while a teardown runs.

        Without it the driver held the raw transport, so a session quarantined with the denied
        document still loaded (a teardown the browser refused) went on refusing navigation while
        the page's text, its DOM, a screenshot, an evaluate and every action still reached the
        denied page.
        """
        return _PageTransport(self)

    # ── setup ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Install the in-page guard BEFORE anything can navigate.

        Order is the whole point: the event listener is registered first (so no
        ``Page.frameNavigated`` is missed), then ``Page.enable``, then the guard script via
        ``Page.addScriptToEvaluateOnNewDocument`` — which applies to every document created
        from here on, so the guard is in place before the first page's own scripts run.
        ``_started`` flips only after the script is acknowledged; if the injection fails the
        session is quarantined and :meth:`navigate` refuses, because a session that can
        navigate without the guard installed is the failure this ordering exists to prevent.

        Then every frame ``Page.getFrameTree`` lists is judged
        (:meth:`_judge_inherited_document`): ``Page.enable`` does not replay the tab's document
        as a ``Page.frameNavigated``, so without this a fresh session would read whatever the
        tab was left on. A denied frame is torn down; a session
        that cannot tear it down, or cannot read the frames, raises and is quarantined.

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
        await self._judge_inherited_document()
        self._started = True

    async def _judge_inherited_document(self) -> None:
        """Judge every frame ``Page.getFrameTree`` lists, as :meth:`handle_event` judges a redirect.

        The first denied frame tears the page down (the teardown blanks the top frame, which
        takes every child frame with it). Raises :class:`CdpSessionError` when the frames cannot
        be read, or when the teardown quarantined the session."""
        try:
            reply = await self._transport.send(GET_FRAME_TREE, {})
            urls = _frame_urls((reply or {}).get("frameTree"))
        except Exception as exc:
            self._quarantine = (
                f"the page's frames could not be read ({exc}); this session refuses to read or "
                "navigate a page whose document it could not judge"
            )
            raise CdpSessionError(self._quarantine) from exc
        for url in urls:
            if _ungated(url):
                continue
            decision = self._gate(url)
            if decision.allow:
                continue
            blocked = self._record_block(decision, operation=GET_FRAME_TREE, phase="start")
            await self._enforce(blocked)
            if self._quarantine:
                raise CdpSessionError(self._quarantine)
            return

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

    # ── navigation ───────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> NavigationOutcome:
        """Pre-flight ``url`` and, only if it is allowed, send ``Page.navigate``.

        Waits until no teardown runs, so a navigation is judged against the session as the
        teardowns leave it: quarantined, it is refused. A navigation the browser answers without
        loading a document (``errorText``, or a download) is not ok: the page still holds the
        document it held, which this navigation never replaced."""
        await self._until_settled()
        if self._quarantine:
            return NavigationOutcome(
                ok=False,
                allowed=False,
                url=url,
                host=_host_of(url),
                reason=self._quarantine,
                risk_level="destructive",
                recovery_hints=["Discard this session and open a new one."],
            )
        if not self._started:
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

        decision = self._gate(url)
        if not decision.allow:
            return self._record_block(decision, operation=NAVIGATE, phase="preflight")

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

    # ── redirects ────────────────────────────────────────────────────────────

    async def handle_event(self, method: str, params: dict[str, Any] | None = None) -> None:
        """CDP event sink. Re-judges every ``Page.frameNavigated``."""
        if method != FRAME_NAVIGATED:
            return

        # Judged even while a teardown runs: start()'s teardown runs on the caller's task, and a
        # denied commit reported meanwhile is a document of its own. The teardown's own
        # about:blank (and Chrome's error page) is ungated, so judging cannot recurse.
        frame = (params or {}).get("frame") or {}
        url = str(frame.get("url") or "")
        if _ungated(url):
            return

        decision = self._gate(url)
        if decision.allow:
            return
        blocked = self._record_block(decision, operation=FRAME_NAVIGATED, phase="frame_navigated")
        await self._enforce(blocked)

    async def _enforce(self, blocked: NavigationOutcome) -> None:
        """Tear the denied document down: ``Page.stopLoading`` then blank the page.

        WHY teardown and not a log line: by the time ``Page.frameNavigated`` arrives the
        browser is already ON the denied origin, so "record it" leaves the agent reading a
        page the policy forbade. WHY both steps: ``stopLoading`` alone kills the in-flight
        subresources but leaves the denied document (and its script context) in place, so
        the page is blanked as well — a same-process teardown that discards the DOM the
        agent would otherwise extract.

        WHY the whole page, even for a subframe: ``Page.navigate`` addresses the top frame,
        and a top document that pulled in a denied frame is itself not trustworthy. Tearing
        down the page is the decisive action available over this domain.

        WHY a resend on "Not attached to an active page" only: right after a cross-site
        navigation the page's session refuses commands for a few milliseconds, and the same
        command is accepted on the same socket once it is back (measured 6.5-25 ms). Each step is
        sent again every :data:`TEARDOWN_RETRY_INTERVAL` until :data:`TEARDOWN_RETRY_BOUND` has
        passed for the whole teardown. Only these two commands are ever resent, so a resend
        cannot take the page anywhere but ``about:blank``.

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
        """
        self._running += 1
        self._teardowns += 1
        self._settled.clear()
        deadline = self._clock() + TEARDOWN_RETRY_BOUND
        blanked = False
        try:
            await self._teardown_step(STOP_LOADING, {}, deadline)
            blanked = True
            reply = await self._teardown_step(NAVIGATE, {"url": BLANK_URL}, deadline)
            failed = _navigation_failed(reply)
            # With another teardown running, its later command superseded this blank (the browser
            # answers that net::ERR_ABORTED), and its own later blank is judged on its own reply.
            if failed and self._running == 1:
                raise RuntimeError(f"{NAVIGATE} to {BLANK_URL} loaded no document ({failed})")
        except Exception as exc:
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
            self._running -= 1
            if not self._running:
                self._settled.set()

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

    # ── audit ────────────────────────────────────────────────────────────────

    def _record_block(
        self, decision: GuardDecision, *, operation: str, phase: str
    ) -> NavigationOutcome:
        outcome = NavigationOutcome(
            ok=False,
            allowed=False,
            url=decision.url,
            host=decision.host,
            reason=decision.reason,
            risk_level=decision.risk_level or "destructive",
            recovery_hints=list(decision.recovery_hints),
        )
        self._blocks.append(outcome)
        self._audit(outcome, operation=operation, phase=phase)
        return outcome

    def _audit(self, outcome: NavigationOutcome, *, operation: str, phase: str) -> None:
        """Write ONE SEL row for a denial. Fails OPEN: audit never rescues a navigation."""
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
                    outcome="denied",
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
    is not enough while the page itself can still be read. A command waits while a teardown runs,
    and a reply that comes back after a teardown began is discarded rather than returned, because
    it may have been read off the denied document before it was blanked.
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
        await session._until_settled()
        self._refuse_if_quarantined(method)
        teardowns = session._teardowns
        reply = await session._transport.send(method, params)
        if session._teardowns != teardowns or not session._settled.is_set():
            raise CdpSessionError(
                f"{method}'s reply was discarded: a denied navigation was torn down while it ran"
            )
        self._refuse_if_quarantined(method)
        return reply
