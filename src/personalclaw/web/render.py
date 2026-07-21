"""JS-render fetch path — render a client-side page in a headless browser.

Some pages return an empty shell over HTTP and build their content with JavaScript;
the plain ``net.fetch`` path sees only the shell. ``render_url`` drives a headless
Chromium (Playwright) to execute the page's JS and returns the *rendered* HTML for the
shared extractor.

A headless browser does its own DNS + connections, so it BYPASSES ``net.fetch``'s
IP-pinning SSRF guard. To keep the egress invariant ("nothing reaches an
attacker-influenced private host"), ``render_url`` pre-validates the URL through the
SAME egress guard (``net.guard.evaluate``) BEFORE navigating — a denied URL never
reaches the browser. Redirects during navigation remain a residual gap vs the pinned
HTTP path; documented, and acceptable because this path is opt-in for known sites.

Playwright is an OPTIONAL dependency (``pip install personalclaw[js-render]`` +
``playwright install chromium``). When absent, ``render_url`` returns a clear
unavailable result so ``web_fetch`` degrades to the plain HTTP fetch rather than failing.
"""

import logging
from dataclasses import dataclass

from personalclaw.net.guard import evaluate
from personalclaw.net.policy import STRICT, EgressPolicy

logger = logging.getLogger(__name__)

# Default time budget for a render (navigation + network-idle settle), ms.
_DEFAULT_NAV_TIMEOUT_MS = 20000


def is_available() -> bool:
    """Whether the Playwright dependency is importable (the browser binary is a
    separate `playwright install` step, surfaced as a runtime error if missing)."""
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


@dataclass
class RenderResult:
    """Outcome of a JS render. ``ok=False`` with ``unavailable=True`` means Playwright
    isn't installed (caller should fall back to plain fetch); other failures carry an
    error + recovery hints."""

    ok: bool
    url: str = ""
    html: str = ""
    status: int = 0
    unavailable: bool = False
    error: str = ""
    recovery_hints: list[str] | None = None
    risk_level: str = "safe"


async def render_url(
    url: str,
    *,
    policy: EgressPolicy = STRICT,
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
    resolver=None,
) -> RenderResult:
    """Render ``url`` in headless Chromium and return its post-JS HTML.

    Egress is pre-validated through the same guard the HTTP path uses, so a private/
    loopback/IMDS target is denied before the browser launches. ``resolver`` is
    injectable for testing the guard without real DNS.
    """
    if not is_available():
        return RenderResult(
            ok=False,
            url=url,
            unavailable=True,
            error="JS rendering is not available (Playwright is not installed).",
            recovery_hints=[
                "Install it: pip install 'personalclaw[js-render]' && playwright install chromium.",
                "Without it, web_fetch uses the plain HTTP fetch (no JS execution).",
            ],
        )

    # Egress pre-flight — a headless browser bypasses net.fetch's IP pinning, so the
    # guard MUST run here before any navigation. Denied → never reaches the browser.
    guard_kw = {"resolver": resolver} if resolver is not None else {}
    decision = evaluate(url, policy, **guard_kw)
    if not decision.allow:
        return RenderResult(
            ok=False,
            url=url,
            error=decision.reason,
            recovery_hints=list(decision.recovery_hints),
            risk_level=decision.risk_level,
        )

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - guarded by is_available()
        return RenderResult(
            ok=False, url=url, unavailable=True, error=f"Playwright import failed: {exc}"
        )

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                resp = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                html = await page.content()
                status = resp.status if resp else 0
            finally:
                await browser.close()
    except Exception as exc:
        logger.warning("render_url failed for %s: %s", url, exc, exc_info=True)
        return RenderResult(
            ok=False,
            url=url,
            error=f"render failed: {exc}",
            recovery_hints=[
                "The page may be slow or block automation; retry, or use web_fetch without render."
            ],
        )

    return RenderResult(ok=True, url=url, html=html, status=status)
