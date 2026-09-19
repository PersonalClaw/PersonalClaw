"""Web URL remote source connector — fetches any publicly accessible page."""

import hashlib
import logging

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

from personalclaw.knowledge.connectors.base import BaseConnector, extract_html_metadata
from personalclaw.web.extract import extract_main_content, meta_refresh_target

logger = logging.getLogger(__name__)

#: How many meta-refresh hops to follow. Bounded because the hops come from fetched content, so
#: an unbounded chain is a page's choice to make us fetch forever. Three covers the real case (a
#: docs site that moved a page, sometimes twice) without becoming a crawler.
_MAX_META_REFRESH_HOPS = 3


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
        "User-Agent": "PersonalClaw-KnowledgeBot/1.0 (compatible; +https://github.com/PersonalClaw/PersonalClaw)"  # noqa: E501
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

        # Resolved ONCE and reused for every meta-refresh hop: a hop that re-derived its own
        # policy could drift from the one the first fetch was judged against.
        policy = egress_policy_for(CONNECTOR)
        try:
            resp = await net_fetch(url, policy=policy, headers=self._HEADERS)
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
            final_url = resp.url
            # A meta-refresh stub is a redirect the HTTP layer never sees, so `net.fetch`'s own
            # redirect following cannot help: the response is a 200 whose BODY is the redirect.
            # Without this the ~167-byte "You should have been redirected" stub was stored,
            # summarized and indexed as the article the user asked to save (#265).
            hops: list[str] = []
            if "html" in content_type:
                raw, content_type, final_url, hops = await self._follow_meta_refresh(
                    raw, content_type, final_url, policy
                )
            page_meta: dict = {}
            if "html" in content_type:
                # Shared extractor (web/extract.py): boilerplate-free main content via
                # trafilatura → markdown, sanitized first. extract_html_metadata still
                # reads the <head> for the bookmark link-card title/description.
                text = extract_main_content(raw, url=final_url).text
                page_meta = extract_html_metadata(raw)
            else:
                text = raw
            content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
            meta = {
                "url": final_url,
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
            if hops:
                # Recorded, not silent: `url` now differs from the URL the user bookmarked, and a
                # reader comparing the two needs to know the fetcher followed the page's own
                # redirect rather than that the bookmark was wrong.
                meta["meta_refresh_chain"] = hops
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

    async def _follow_meta_refresh(
        self, raw: str, content_type: str, url: str, policy
    ) -> tuple[str, str, str, list[str]]:
        """Follow `<meta http-equiv="refresh">` hops, returning the destination's
        ``(body, content_type, url, chain)``.

        🔴 **Every hop goes back through `net.fetch` with the SAME policy.** The target is parsed
        out of untrusted fetched content, so it is precisely the shape an SSRF attempt takes: a
        public page whose body says ``url=http://169.254.169.254/``. Re-entering the shared guard
        is what makes that refusal automatic — it re-resolves and re-pins the host, re-applies the
        operator's deny/allow lists and the private-range block, and re-checks the HTTP redirects
        of the new request too. A hand-rolled "is this target safe" check here would be a second,
        weaker copy of a guard that already exists, and would not pin the IP it validated.

        Conservative on every failure: a blocked, unreachable, non-HTML or looping hop STOPS the
        walk and keeps the last good body. That is never worse than today's behaviour (which kept
        the stub), so following can only improve what gets stored — a redirect target that 404s
        must not turn a working bookmark into a failed one.
        """
        from personalclaw.net import EgressBlocked
        from personalclaw.net import fetch as net_fetch

        chain: list[str] = []
        seen = {url}
        for _ in range(_MAX_META_REFRESH_HOPS):
            target = meta_refresh_target(raw, url=url)
            if not target or target in seen:
                # A stub pointing at itself (or back up the chain) is a loop, not a redirect.
                break
            seen.add(target)
            try:
                hop = await net_fetch(target, policy=policy, headers=self._HEADERS)
            except EgressBlocked as exc:
                # NOT re-raised: the bookmark the user saved was allowed: only the page's own
                # redirect target is refused. Failing the whole ingest here would let any page
                # turn a legitimate bookmark into an error by advertising a private redirect.
                logger.warning(
                    "WebUrl meta-refresh target blocked by egress guard (%s → %s): %s",
                    url,
                    target,
                    exc,
                )
                break
            except Exception as exc:
                logger.info("WebUrl meta-refresh hop failed (%s → %s): %s", url, target, exc)
                break
            if hop.status >= 400:
                logger.info(
                    "WebUrl meta-refresh target returned HTTP %s (%s → %s)",
                    hop.status,
                    url,
                    target,
                )
                break
            hop_type = hop.headers.get("Content-Type", "") or hop.headers.get("content-type", "")
            chain.append(hop.url or target)
            raw, content_type, url = hop.text, hop_type, (hop.url or target)
            if "html" not in hop_type:
                # A non-HTML destination is the real content and cannot carry another refresh.
                break
        return raw, content_type, url, chain

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
