"""Egress enforcement plane — the ONE sanctioned outbound HTTP path.

``fetch(url, policy=...)`` is the single function tools/connectors/actions call. It:
  1. evaluates the URL (deny → uniform error + SEL audit, never silent),
  2. connects to the PINNED resolved IP so the name cannot rebind to a private IP
     between check and connect (closes the validate-then-reconnect TOCTOU window),
  3. re-evaluates EVERY redirect hop (a redirect to 169.254.169.254 is re-checked),
  4. enforces the byte cap (streamed) + timeout + scheme,
  5. emits a SEL audit event on allow + deny.

A blocked fetch raises :class:`EgressBlocked`, carrying the guard's reason +
recovery hints so callers surface it through the uniform tool-result contract rather
than as an opaque stall.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from personalclaw.net.guard import GuardDecision, evaluate
from personalclaw.net.policy import STRICT, EgressPolicy

logger = logging.getLogger(__name__)


class EgressBlocked(Exception):
    """A fetch was denied by the egress guard (pre-flight or a redirect hop)."""

    def __init__(self, decision: GuardDecision) -> None:
        super().__init__(decision.reason or "egress blocked")
        self.decision = decision
        self.recovery_hints = decision.recovery_hints
        self.risk_level = decision.risk_level


@dataclass
class FetchResponse:
    """A completed, guarded fetch."""

    url: str            # final URL (after redirects)
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    truncated: bool = False  # body hit max_bytes

    @property
    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("Content-Type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")


def _audit(url: str, policy: EgressPolicy, *, outcome: str, reason: str = "") -> None:
    """Emit a SEL audit event for an egress allow/deny (best-effort)."""
    try:
        from personalclaw.sel import sel
        sel().log_api_access(
            caller=f"net.fetch:{policy.name}", operation="egress_fetch",
            outcome=outcome, source="net", resources=url[:200], error=reason[:200],
        )
    except Exception:
        logger.debug("egress SEL audit failed", exc_info=True)


def _pinned_resolver(host_to_ips: dict[str, list[str]]):
    """An aiohttp AbstractResolver that returns ONLY the pre-validated pinned IPs for
    a host — so the connector dials those exact IPs (no second DNS lookup = no rebind).
    Falls back to a normal resolve for any host not pinned (e.g. an allowed redirect
    target re-pinned on the next hop)."""
    from aiohttp.abc import AbstractResolver
    from aiohttp.resolver import ThreadedResolver

    class _PinnedResolver(AbstractResolver):
        def __init__(self) -> None:
            self._fallback = ThreadedResolver()

        async def resolve(self, host, port=0, family=0):
            ips = host_to_ips.get(host)
            if not ips:
                return await self._fallback.resolve(host, port, family)
            import socket as _s
            hosts = []
            for ip in ips:
                fam = _s.AF_INET6 if ":" in ip else _s.AF_INET
                if family in (0, fam):
                    hosts.append({"hostname": host, "host": ip, "port": port,
                                  "family": fam, "proto": 0, "flags": 0})
            return hosts or await self._fallback.resolve(host, port, family)

        async def close(self) -> None:
            await self._fallback.close()

    return _PinnedResolver()


async def fetch(
    url: str,
    *,
    policy: EgressPolicy = STRICT,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    resolver=None,
) -> FetchResponse:
    """Perform a guarded outbound HTTP request. Raises :class:`EgressBlocked` if the
    URL (or any redirect hop) is denied. ``resolver`` is injectable for testing the
    guard without real DNS."""
    import aiohttp

    guard_kw = {"resolver": resolver} if resolver is not None else {}
    decision = evaluate(url, policy, **guard_kw)
    if not decision.allow:
        _audit(url, policy, outcome="denied", reason=decision.reason)
        raise EgressBlocked(decision)
    _audit(url, policy, outcome="allowed")

    timeout = aiohttp.ClientTimeout(total=policy.timeout_s)
    cur_url = url
    cur_decision = decision

    for _hop in range(policy.max_redirects + 1):
        host = urlparse(cur_url).hostname or ""
        # Pin this hop's host to its validated IPs (when the policy pins).
        connector = None
        if policy.pin_resolved_ip and cur_decision.pinned_ips:
            connector = aiohttp.TCPConnector(resolver=_pinned_resolver({host: cur_decision.pinned_ips}))
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.request(
                method, cur_url, headers=headers, data=data, allow_redirects=False,
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    # Re-evaluate the redirect target against the SAME policy — a
                    # redirect to a private IP / IMDS is blocked here, the gap an
                    # allow_redirects=True client leaves open.
                    nxt = str(resp.headers["Location"])
                    nxt = _absolutize(cur_url, nxt)
                    hop_decision = evaluate(nxt, policy, **guard_kw)
                    if not hop_decision.allow:
                        _audit(nxt, policy, outcome="denied", reason=f"redirect: {hop_decision.reason}")
                        raise EgressBlocked(hop_decision)
                    # 303 (and commonly 301/302 from POST) → GET; 307/308 preserve method.
                    if resp.status == 303:
                        method, data = "GET", None
                    cur_url, cur_decision = nxt, hop_decision
                    continue

                body, truncated = await _read_capped(resp, policy.max_bytes)
                return FetchResponse(
                    url=cur_url, status=resp.status,
                    headers={k: v for k, v in resp.headers.items()},
                    body=body, truncated=truncated,
                )

    # Exhausted the redirect budget.
    blocked = GuardDecision(allow=False, url=cur_url, reason=f"too many redirects (> {policy.max_redirects})",
                            risk_level="caution", recovery_hints=["The URL redirects too many times; fetch the final URL directly."])
    _audit(cur_url, policy, outcome="denied", reason=blocked.reason)
    raise EgressBlocked(blocked)


async def _read_capped(resp, max_bytes: int) -> tuple[bytes, bool]:
    """Stream the body up to ``max_bytes``; returns (body, truncated)."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in resp.content.iter_chunked(65536):
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            truncated = True
            break
    body = b"".join(chunks)[:max_bytes]
    return body, truncated


def _absolutize(base: str, location: str) -> str:
    """Resolve a (possibly relative) redirect Location against the current URL."""
    from urllib.parse import urljoin
    return urljoin(base, location)
