"""The forwarded headers we PROMISE must be the forwarded headers we READ.

``config/loader.py``'s ``trusted_proxies`` help text and ``docs/guides/remote-access.md``'s
``public_url`` table both promised ``X-Forwarded-Proto`` / ``X-Forwarded-For`` were "honored only
from a configured trusted proxy". Measured on ``origin/main`` 804a9a9bf: **nothing in ``src/``
reads either header.** The one forwarded header the code honours is ``X-Real-IP``
(``dashboard/token_auth.py``'s ``_resolved_client_ip``). So an operator who followed the guide
configured their proxy to send headers the gateway ignores, and every request kept binding to the
tunnel's address — silently, because the config help said otherwise.

**Which direction this was closed, and why.** The promises were corrected to name ``X-Real-IP``,
rather than teaching the code to honour ``X-Forwarded-*``:

* Two headers carrying the same fact (the client address) needs a precedence rule, and a
  precedence rule between a trusted and a forgeable source is a spoofing surface. One source is
  the security property, not a convenience.
* ``X-Forwarded-For`` is a *list* (``client, proxy1, proxy2``). Reading it safely needs a hop-count
  policy tied to how many proxies actually sit in front of the gateway; getting that wrong is the
  classic IP-spoof, and nothing in the tree needs it.
* ``X-Forwarded-Proto`` has no consumer at all. The ``Secure`` cookie and the ``wss://`` CSP come
  from ``dashboard.public_url`` — the operator's own statement, which is strictly better evidence
  than a header any upstream hop can set.

Narrowing a promise to what is enforced is also the fail-closed direction: honouring more headers
would have widened the trust surface to fix a documentation defect.

**The rail is mandatory, per the change.** Either direction closes the drift once; only a test keeps
it closed. This asserts the sets are EQUAL, so it reds whichever side moves next — a new
``X-Forwarded-For`` reader added to ``src/`` fails just as loudly as a doc line that re-promises
one.
"""

from __future__ import annotations

from forwarded_header_docs_match_code_shared import SRC


def test_the_header_reader_is_gated_on_trusted_proxies_when_exposed():
    """The promise is not just WHICH header, it is *honored only from a trusted proxy*. Asserted
    against the source of `_resolved_client_ip` because the closure is built inside
    `token_auth_middleware` and is not importable on its own."""
    text = (SRC / "dashboard" / "token_auth.py").read_text(encoding="utf-8")
    start = text.index("def _resolved_client_ip(")
    body = text[start : text.index("\n    def ", start + 10)]
    assert 'headers.get("X-Real-IP"' in body
    assert "is_exposed()" in body and "is_trusted_proxy(" in body, (
        "the forwarded header must be gated on exposure + trusted_proxies, which is the half of "
        "the promise a header-name check cannot see"
    )
