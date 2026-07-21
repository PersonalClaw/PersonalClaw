"""Web URL remote source connector — fetches any publicly accessible page."""

import hashlib
import logging

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

from personalclaw.knowledge.connectors.base import BaseConnector, extract_html_metadata
from personalclaw.web.extract import extract_main_content

logger = logging.getLogger(__name__)


def _friendly_fetch_error(exc: Exception) -> tuple[str, str]:
    """Map a raw fetch exception to (human-readable reason, kind) for the bookmark's
    processing_error — so the UI shows 'Couldn't reach the site' instead of a bare
    '[Errno 8] nodename nor servname provided'.

    kind is ``"unreachable"`` for environmental/network problems the user can simply
    retry (DNS/connect/timeout/refused/5xx/429) — these are NOT an unexpected processing
    failure, so the pipeline marks the item ``unreachable`` (retryable) rather than
    ``failed``. kind is ``"error"`` for anything else (a real, unexpected fetch fault)."""
    if _httpx is not None:
        status_err = getattr(_httpx, "HTTPStatusError", None)
        if status_err and isinstance(exc, status_err):
            code = getattr(getattr(exc, "response", None), "status_code", None)
            # 5xx / 429 are transient (server-side / rate-limit) → retryable; other 4xx
            # (404/403/410) mean the page genuinely isn't there for us, also retryable as
            # 'unreachable' (the URL is still saved; the user may fix it or try later).
            msg = (
                f"The site returned HTTP {code}."
                if code
                else "The site returned an error response."
            )
            return msg, "unreachable"
        timeout_err = getattr(_httpx, "TimeoutException", None)
        if timeout_err and isinstance(exc, timeout_err):
            return "The site took too long to respond (timed out).", "unreachable"
        connect_err = getattr(_httpx, "ConnectError", None)
        if connect_err and isinstance(exc, connect_err):
            return "Couldn't reach the site (it may not exist or is unreachable).", "unreachable"
    msg = str(exc).lower()
    if "nodename nor servname" in msg or "name or service not known" in msg or "getaddrinfo" in msg:
        return "Couldn't reach the site (the address could not be resolved).", "unreachable"
    if "timed out" in msg or "timeout" in msg:
        return "The site took too long to respond (timed out).", "unreachable"
    if "connection refused" in msg or "refused" in msg:
        return "The site refused the connection.", "unreachable"
    return f"Couldn't fetch the page: {exc}", "error"


class WebUrlConnector(BaseConnector):
    """Connector that fetches and stores text content from any web URL."""

    _HEADERS = {
        "User-Agent": "PersonalClaw-KnowledgeBot/1.0 (compatible; +https://github.com/personalclaw/personalclaw)"  # noqa: E501
    }

    def source_type(self) -> str:
        return "web_url"

    def validate_config(self, config: dict) -> tuple[bool, str]:
        url = (config.get("uri") or config.get("url") or "").strip()
        if not url:
            return False, "URL is required"
        if not url.startswith(("http://", "https://")):
            return False, "URL must start with http:// or https://"
        return True, ""

    async def fetch(self, source: dict) -> tuple[str, dict]:
        url = (source.get("uri") or source.get("url") or "").strip()
        if not url:
            return "", {"error": "No URL configured"}
        # Fetch through the ONE egress chokepoint (net.fetch) — the connector previously
        # fetched arbitrary user/agent-supplied URLs with raw httpx and NO SSRF guard, so
        # a bookmark of http://169.254.169.254/ or an internal host was fetched unguarded.
        # CONNECTOR policy blocks non-public destinations, pins the resolved IP (no
        # rebind), re-checks every redirect hop, and caps bytes/timeout. An operator can
        # allow-list an internal host via security.egress.
        from personalclaw.net import CONNECTOR, EgressBlocked, egress_policy_for
        from personalclaw.net import fetch as net_fetch

        try:
            resp = await net_fetch(url, policy=egress_policy_for(CONNECTOR), headers=self._HEADERS)
            if resp.status >= 400:
                # net.fetch returns the status (unlike httpx.raise_for_status); a 4xx/5xx
                # means the page isn't retrievable for us now — retryable 'unreachable'
                # (the URL stays saved; the user may fix it or try later).
                return "", {
                    "error": f"The site returned HTTP {resp.status}.",
                    "error_kind": "unreachable",
                    "url": url,
                }
            content_type = resp.headers.get("Content-Type", "") or resp.headers.get(
                "content-type", ""
            )
            raw = resp.text
            page_meta: dict = {}
            if "html" in content_type:
                # Shared extractor (web/extract.py): boilerplate-free main content via
                # trafilatura → markdown, sanitized first. extract_html_metadata still
                # reads the <head> for the bookmark link-card title/description.
                text = extract_main_content(raw, url=resp.url).text
                page_meta = extract_html_metadata(raw)
            else:
                text = raw
            content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
            meta = {
                "url": resp.url,
                "etag": resp.headers.get("ETag", "") or resp.headers.get("etag", ""),
                "last_modified": resp.headers.get("Last-Modified", "")
                or resp.headers.get("last-modified", ""),
                "content_hash": content_hash,
                "content_type": content_type,
                # Real page title/description from the HTML head (preferred over the
                # body-text heuristic for bookmark link-cards).
                "page_title": page_meta.get("title", ""),
                "page_description": page_meta.get("description", ""),
            }
            return text, meta
        except EgressBlocked as e:
            # A blocked fetch (SSRF/private/redirect-to-IMDS) is a security refusal, not a
            # transient network error — surface it clearly, non-retryable.
            logger.warning("WebUrl fetch blocked by egress guard for %s: %s", url, e)
            return "", {
                "error": f"Blocked by the network security guard: {e}",
                "error_kind": "blocked",
                "url": url,
            }
        except Exception as e:
            logger.error("WebUrl fetch failed for %s: %s", url, e)
            reason, kind = _friendly_fetch_error(e)
            return "", {"error": reason, "error_kind": kind, "url": url}

    async def detect_changes(self, source: dict) -> bool:
        url = (source.get("uri") or source.get("url") or "").strip()
        if not url:
            return False
        stored_meta = source.get("metadata") or {}
        # A scheduled bookmark-refresh HEAD is the same SSRF surface as fetch() — route
        # it through the guarded chokepoint too (a bookmark of an internal host must not
        # be probed on a cron just because it's a HEAD).
        from personalclaw.net import CONNECTOR, egress_policy_for
        from personalclaw.net import fetch as net_fetch

        try:
            r = await net_fetch(url, policy=egress_policy_for(CONNECTOR), method="HEAD")
            etag = r.headers.get("ETag", "") or r.headers.get("etag", "")
            last_modified = r.headers.get("Last-Modified", "") or r.headers.get("last-modified", "")
            if etag and etag != stored_meta.get("etag"):
                return True
            if last_modified and last_modified != stored_meta.get("last_modified"):
                return True
            # No cache headers — always re-fetch
            if not etag and not last_modified:
                return True
            return False
        except Exception as e:
            logger.error("WebUrl detect_changes failed for %s: %s", url, e)
            return False
