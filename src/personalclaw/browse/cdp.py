"""Gated CDP navigation for the browse loop (BA-2).

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

Everything fails CLOSED. An unparseable URL, a missing BROWSE profile, a guard that raises,
a transport that raises, an enforcement that cannot be delivered — none of them end in
"navigated anyway"; the worst case is a quarantined session that refuses to navigate at
all. The one deliberate fail-OPEN is the audit: a SEL write that raises must not turn a
deny into a navigation.

Residual gaps, stated rather than papered over:

* **DNS rebind.** The guard pins the resolved IPs, but CDP has no per-navigation "dial this
  IP" parameter — the browser resolves again itself. :attr:`NavigationOutcome.pinned_ips`
  carries the pinned set out so the launcher can pass Chrome's ``--host-resolver-rules``;
  wiring that belongs to BA-2's launcher scope, not here.
* **Subresources.** An image, an XHR or a WebSocket is not a navigation and produces no
  ``Page.frameNavigated``. Those are the in-page guard script's job (and, later, the
  ``Fetch``/``Network`` interception domains) — not this module's.
"""

from __future__ import annotations

import logging
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

#: SEL ``event_type`` for a browse egress decision. ``operation`` carries the CDP method
#: that was blocked, so a preflight deny and a redirect deny are distinguishable rows.
SEL_EVENT_TYPE = "browse_egress"
SEL_TOOL_KIND = "browse_cdp"


class CdpSessionError(RuntimeError):
    """Session setup failed, so the session is unusable (and refuses to navigate)."""


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


class GatedCdpSession:
    """One CDP page session whose navigation path is gated by the egress guard.

    ``resolver`` is handed to :func:`personalclaw.net.guard.evaluate` (fake DNS in tests);
    ``caller_identity`` / ``agent`` / ``source`` land on the SEL rows this session writes.
    """

    def __init__(
        self,
        transport: CdpTransport,
        *,
        caller_identity: str = "browse",
        agent: str = "personalclaw",
        source: str = "background",
        resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self._transport = transport
        self._caller_identity = caller_identity
        self._agent = agent
        self._source = source
        self._resolver = resolver
        self._started = False
        self._enforcing = False
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
        self._started = True

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
        """Pre-flight ``url`` and, only if it is allowed, send ``Page.navigate``."""
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
            await self._transport.send(NAVIGATE, {"url": url})
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
        if self._enforcing:
            # Our own teardown navigation reports itself; judging it would recurse.
            return

        frame = (params or {}).get("frame") or {}
        url = str(frame.get("url") or "")
        if url in _TEARDOWN_URLS or url.startswith(_ERROR_URL_PREFIX):
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

        If neither message can be delivered, the block is unenforceable — the session is
        quarantined so no further navigation happens through it.
        """
        self._enforcing = True
        try:
            await self._transport.send(STOP_LOADING, {})
            await self._transport.send(NAVIGATE, {"url": BLANK_URL})
        except Exception as exc:
            self._quarantine = (
                f"could not enforce the block on {blocked.host or blocked.url!r} ({exc}); "
                "this session no longer navigates"
            )
            logger.error("browse: %s", self._quarantine)
        finally:
            self._enforcing = False

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
