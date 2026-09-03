"""BA-6 §(d)/A3 — the production ``TickRunner`` binding for scheduled ``watch_page`` ticks.

:func:`~personalclaw.browse.plans.execute_tick` (BA-6 core) is kind-agnostic machinery: it
owns the idempotent cursor and the rung floor and calls an injected
:data:`~personalclaw.browse.plans.TickRunner` to actually touch the page. This module supplies
the production runner the WATCHED-SOURCES escalation uses — the read-only *content* path. It
drives the gateway browser through BA-2's egress-fenced
:class:`~personalclaw.browse.cdp.GatedCdpSession`, reads the settled DOM, and returns the
extracted main-text body as the tick's ``content``.

This is the tier the §8.1 chain escalates to when even the headless render tier
(``web/render.py``) still sees a JavaScript shell: the gateway browser carries the real profile
and the full safety fence a plain headless render does not, so a source behind a profile-scoped
or login-walled SPA reaches text here that the render tier could not.

``walk_flow`` ticks (the agentic form-walk path) need a model-backed decider and belong with
their own consumer (a scheduled-flow trigger). This content runner REFUSES them rather than
shipping an unexercised CDP-drive path: a runner is a *policy*, and this one is the read-only
content policy. The gateway wiring that resolves a live ``cdp_url`` and opens the CDP stack is
supplied by the escalation call site (the source config carries the endpoint); this module is
the transport-agnostic core, injectable end-to-end so the idempotency it feeds is testable
without a live browser.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from personalclaw.browse.extraction import extract_page
from personalclaw.browse.plans import (
    KIND_WATCH_PAGE,
    BrowsePlan,
    PlanError,
    TickOutcome,
    TickRunner,
)

#: Open a gateway CDP session for ``cdp_url`` and return ``(session, page, closer)`` — the same
#: triple ``browse_provider._open`` yields (a startable/navigable session, a page driver, and an
#: awaitable that tears the transport down). Injected so the runner drives a recorder in a test
#: exactly as it drives a live browser in production.
CdpSessionOpener = Callable[[str], Awaitable[tuple[Any, Any, Callable[[], Awaitable[None]]]]]


def make_content_tick_runner(
    *,
    open_session: CdpSessionOpener,
    resolve_url: Callable[[], str],
    extract: Callable[..., Any] = extract_page,
    settle: Callable[[Any], Awaitable[None]] | None = None,
) -> TickRunner:
    """Build a :data:`TickRunner` that renders a ``watch_page`` plan's URL in the gateway browser
    and returns its extracted text.

    ``open_session`` / ``resolve_url`` are the two seams to the browser: the first yields a
    session + page + closer for a resolved ``cdp_url``, the second produces the current gateway
    ``cdp_url`` (empty when no gateway is configured — the tick then fails soft rather than
    raising). ``extract`` defaults to :func:`extract_page`, the same chrome-stripping text
    pipeline the connectors use, so the browse tier and the fetch tier hand ``execute_tick`` the
    same shape of text to hash and diff. ``settle`` is an optional post-navigate wait for a page
    that finishes rendering asynchronously.

    A browser fault is a SOFT tick failure — ``ok=False`` with a note, never a raise — because a
    scheduled actuator must survive a transiently-down browser to run on its next tick; the
    session is always torn down (the closer runs in a ``finally``) even when the drive raises.
    """

    async def _run(plan: BrowsePlan) -> TickOutcome:
        # A runner is a policy: this one reads pages, it does not act on them. Refusing walk_flow
        # here (rather than half-driving it) keeps the agentic path from shipping unexercised.
        if plan.kind != KIND_WATCH_PAGE:
            raise PlanError(
                f"the content tick runner drives {KIND_WATCH_PAGE!r} only; {plan.kind!r} needs "
                "an agentic decider — wire it with its own runner"
            )
        cdp_url = (resolve_url() or "").strip()
        if not cdp_url:
            return TickOutcome(note="no gateway browser is configured (cdp_url is empty)")
        try:
            session, page, closer = await open_session(cdp_url)
            try:
                await session.start()
                await session.navigate(plan.start_url)
                if settle is not None:
                    await settle(page)
                html = await page.html()
                final_url = (await page.current_url()) or plan.start_url
            finally:
                # Always release the transport, even on a mid-drive fault: a leaked CDP session
                # would pin the gateway browser and starve the next scheduled tick.
                await closer()
        except Exception as exc:  # noqa: BLE001 — a browser fault is a soft tick failure
            return TickOutcome(note=f"browse tick failed: {type(exc).__name__}: {exc}"[:200])
        text = (getattr(extract(html, url=plan.start_url), "text", "") or "").strip()
        # `verified` mirrors `ok` for a read: extracting real text IS the confirmation, so a tick
        # that rendered an empty shell reports ok=False and does NOT advance the content cursor.
        # `html` carries the raw markup regardless, so a caller running its own DOM detectors (the
        # WATCHED-SOURCES browse tier) still gets the page even when no main-text body was found.
        return TickOutcome(
            content=text,
            html=html,
            ok=bool(text),
            verified=bool(text),
            final_url=final_url,
            note="" if text else "browse tick rendered no extractable text",
        )

    return _run


def make_gateway_opener(
    *, screenshot_dir: str | None = None, source: str = "background"
) -> CdpSessionOpener:
    """A :data:`CdpSessionOpener` that connects to the gateway browser's CDP endpoint.

    Lazy-imports the CDP transport/driver/session (exactly as ``browse_provider._open`` does) so
    importing this module never drags in the browser stack — the WATCHED-SOURCES poll path imports
    it, and most polls never escalate to the browse tier. Returns ``(session, page, closer)`` for a
    resolved ``cdp_url``; the closer tears the transport down.
    """

    async def _open(cdp_url: str) -> tuple[Any, Any, Callable[[], Awaitable[None]]]:
        from pathlib import Path

        from personalclaw.browse.cdp import GatedCdpSession
        from personalclaw.browse.page import CdpPageDriver
        from personalclaw.browse.transport import WebSocketCdpTransport

        transport = await WebSocketCdpTransport.connect(cdp_url)
        driver = CdpPageDriver(
            transport, screenshot_dir=Path(screenshot_dir) if screenshot_dir else None
        )
        session = GatedCdpSession(transport, caller_identity="browse:web-source", source=source)
        return session, driver, transport.close

    return _open
