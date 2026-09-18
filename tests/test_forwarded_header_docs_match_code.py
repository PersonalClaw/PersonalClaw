"""The forwarded headers we PROMISE must be the forwarded headers we READ (roadmap atom RUA-6).

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

**The rail is mandatory, per the atom.** Either direction closes the drift once; only a test keeps
it closed. This asserts the sets are EQUAL, so it reds whichever side moves next — a new
``X-Forwarded-For`` reader added to ``src/`` fails just as loudly as a doc line that re-promises
one.
"""

from __future__ import annotations

import pathlib
import re

import personalclaw

SRC = pathlib.Path(personalclaw.__file__).resolve().parent
REPO = SRC.parent.parent
DOC = REPO / "docs" / "guides" / "remote-access.md"
LOADER = SRC / "config" / "loader.py"

#: Header names that carry a FORWARDED client identity — the class `trusted_proxies` governs.
#: Scoped deliberately: `X-Local-Secret`, `X-Session-Key` and `X-Shell-Token` are credentials
#: this gateway itself mints and are not proxy-forwarded, so a bare `X-*` sweep would demand the
#: docs' proxy table name them and make the rail meaningless.
_FORWARDED_RE = re.compile(r"\b(X-Forwarded-[A-Za-z-]+|X-Real-IP|Forwarded)\b")

#: `request.headers.get("<name>")` — how the code reads one.
_HEADER_READ_RE = re.compile(r'headers\.get\(\s*"(X-[A-Za-z-]+|Forwarded)"')


def _forwarded_headers_read_by_code() -> set[str]:
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        for name in _HEADER_READ_RE.findall(path.read_text(encoding="utf-8")):
            if _FORWARDED_RE.fullmatch(name):
                found.add(name)
    return found


def _trusted_proxies_help() -> str:
    """The `_meta` help string on `dashboard.trusted_proxies`, as one blob."""
    text = LOADER.read_text(encoding="utf-8")
    start = text.index("trusted_proxies: list[str] = field(")
    end = text.index("restore_sessions", start)
    return text[start:end]


def _doc_trusted_proxy_section() -> str:
    """The `public_url` effects table plus the `trusted_proxies` prose that follows it."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index("Setting `public_url` changes three things:")
    end = text.index("### Step 3", start)
    return text[start:end]


def test_the_config_help_names_exactly_the_headers_the_code_reads():
    """`config/loader.py` is the surface a user configuring `trusted_proxies` reads in Settings."""
    read = _forwarded_headers_read_by_code()
    promised = set(_FORWARDED_RE.findall(_trusted_proxies_help()))
    assert read, "no forwarded header is read at all — the rail would be vacuous"
    honoured = {h for h in promised if h in read}
    assert honoured == read, (
        f"trusted_proxies help text does not name every forwarded header the code honours: "
        f"code reads {sorted(read)}, help names {sorted(promised)}"
    )
    # Anything named but NOT read must be named as explicitly IGNORED, never as honoured —
    # this is the exact shape of the original defect.
    for extra in promised - read:
        assert "ignored" in _trusted_proxies_help(), (
            f"trusted_proxies help names {extra!r}, which no code reads, without saying it is "
            f"ignored"
        )


def test_the_remote_access_guide_names_exactly_the_headers_the_code_reads():
    """The guide is what an operator configures their nginx/tunnel from."""
    read = _forwarded_headers_read_by_code()
    section = _doc_trusted_proxy_section()
    promised = set(_FORWARDED_RE.findall(section))
    assert read <= promised, (
        f"remote-access.md does not name every forwarded header the code honours: "
        f"code reads {sorted(read)}, guide names {sorted(promised)}"
    )
    for extra in promised - read:
        assert (
            "ignored" in section
        ), f"remote-access.md names {extra!r}, which no code reads, without saying it is ignored"


def test_x_forwarded_for_is_not_read_anywhere():
    """The measurement RUA-6 rests on, pinned. If a future change starts honouring
    `X-Forwarded-For`, this reds and forces the docs + the hop-count policy to land with it —
    rather than the header quietly becoming a second, list-shaped source of the client IP."""
    read = _forwarded_headers_read_by_code()
    assert read == {"X-Real-IP"}, (
        f"the set of forwarded headers read by src/ changed to {sorted(read)}; update "
        f"config/loader.py's trusted_proxies help AND docs/guides/remote-access.md in the "
        f"same change, and state the precedence rule between them"
    )


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
