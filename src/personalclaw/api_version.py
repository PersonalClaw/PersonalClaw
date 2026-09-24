"""The ONE origin of the HTTP API version, its supported window, and the negotiation.

**Why this module exists.** ``API_VERSION`` shipped as a write-only number: it was
defined once in :mod:`personalclaw.manifest` and only ever *emitted* — into
``GET /api/manifest``'s ``apiVersion`` and the two generated reference documents.
Nothing anywhere COMPARED it. A client built against version 1 and a gateway that
had moved on negotiated nothing and failed later, at whichever field quietly
changed shape — a ``KeyError`` deep in a handler, or a blank screen in a stale
cached SPA bundle. This module is the comparison the constant never had: a client
*declares* the version it was built for, the gateway compares it at ONE
chokepoint (:mod:`personalclaw.dashboard.api_version_gate`), and a mismatch
outside the declared window is refused through the PL-8 error envelope naming
both versions and which side to upgrade.

**The bump rule — stated here, and only here.**

Bump :data:`API_VERSION` when a change would make a client written against the
previous version *wrong* rather than merely *incomplete*. Concretely, bump for:

* a field REMOVED or RENAMED in any ``/api/**`` response body, the
  ``GET /api/manifest`` document, or the WebSocket event envelope;
* a field whose TYPE or units change (``str`` → ``int``, seconds → milliseconds,
  a scalar becoming a list, a naive timestamp becoming tz-aware);
* a field whose MEANING changes while its name and type stay the same (the
  worst kind, because no deserializer notices);
* a required request parameter ADDED, or an existing one becoming required;
* an enum member REMOVED from a response, or an existing member's semantics
  narrowed such that an old client's branch on it now takes the wrong arm;
* a route MOVED or DELETED (a route *added* is not a bump — see below).

Do NOT bump for: a new route, a new optional response field, a new optional
request parameter, a new enum member appended to a response vocabulary, a new
tool or provider appearing in the manifest's generated sections, or any change
to prose (``description``, ``message``). Those are additive: a client written
against the previous version keeps working, it just does not use the new thing.
Wire *error codes* are governed separately and more strictly — they are
append-only forever (:data:`personalclaw.http_errors.HTTP_ERROR_CODES`), so a
code addition never bumps this number and a code removal is simply not allowed.

When you bump, decide :data:`MIN_SUPPORTED_API_VERSION` in the same change: raise
it only when carrying the older shape is no longer worth the code, and say so in
the CHANGELOG, because raising it is what turns "still works" into a refusal.

**Why an absent declaration means the OLDEST supported version.** Treating a
missing declaration as *current* is precisely the hole this atom exists to close:
every unversioned client — including the ones that predate versioning, which are
exactly the ones most likely to be wrong — would sail through the gate claiming
to be up to date. Pinning absence to :data:`MIN_SUPPORTED_API_VERSION` inverts
that: an unversioned caller is treated as the oldest thing still tolerated, and
the gate echoes that resolution back in the response's :data:`VERSION_HEADER`, so
"you are being treated as version N" is a fact on the wire rather than an
assumption. Any later version-conditional behavior reads the NEGOTIATED number,
which for an undeclared caller is the floor — so it gets the conservative shape,
not the newest one.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── The window. Both numbers live HERE and nowhere else ────────────────────────
#
# `tests/test_api_version_one_origin.py` is the rail: it asserts these are the only
# integer literals bound to an api-version name anywhere in `src/personalclaw` or
# `web/src`, that the manifest's emitted `apiVersion` is this value, and that the
# SPA's `web/src/lib/apiVersion.ts` declares this same number and header name. The
# three values (emitted, declared, negotiated) therefore cannot drift apart.

#: The wire shape this build speaks. Bump per the rule in this module's docstring.
API_VERSION = 1

#: The DECLARED floor of the supported window — an explicit minimum, deliberately
#: not "whatever the code happens to tolerate". A client declaring anything below
#: this is refused. It is also what an *absent* declaration resolves to.
MIN_SUPPORTED_API_VERSION = 1

#: The header a client declares its version in, and the header the gate echoes the
#: NEGOTIATED version back in. A browser ``WebSocket`` cannot set request headers,
#: which is why the WS upgrade is a documented exemption in the gate rather than a
#: second declaration site per socket.
VERSION_HEADER = "X-PersonalClaw-API-Version"


def supported_window() -> tuple[int, int]:
    """``(minimum, current)`` — the inclusive window this build accepts."""
    return (MIN_SUPPORTED_API_VERSION, API_VERSION)


@dataclass(frozen=True)
class ApiVersionRefusal:
    """A negotiation failure, in the terms the client needs to act on it.

    :param client_version: What the client declared, as it will be reported back
        — an ``int`` when it parsed, the raw string (truncated) when it did not,
        and the resolved floor when nothing was declared at all.
    :param server_version: :data:`API_VERSION`.
    :param min_supported_version: :data:`MIN_SUPPORTED_API_VERSION`.
    :param upgrade: ``"client"`` or ``"server"`` — which side moves. Never
        omitted: a refusal that names two numbers but not the direction leaves
        the reader to guess which of them is stale.
    :param message: The human sentence, already naming both numbers.
    """

    client_version: int | str
    server_version: int
    min_supported_version: int
    upgrade: str
    message: str

    def as_error_extra(self) -> dict[str, object]:
        """The actionable half, for ``json_error(..., error_extra=...)``."""
        return {
            "client_version": self.client_version,
            "server_version": self.server_version,
            "min_supported_version": self.min_supported_version,
            "upgrade": self.upgrade,
        }


@dataclass(frozen=True)
class ApiVersionOutcome:
    """What the chokepoint concluded about one request.

    :param negotiated: The version this exchange is treated as — the client's
        declaration when it parsed, the window's FLOOR when the client declared
        nothing. ``None`` only when the declaration was unreadable, which is
        always a refusal. The gate echoes this back in :data:`VERSION_HEADER`, so
        a caller can see which version it was credited with instead of guessing.
    :param refusal: ``None`` when the request proceeds; otherwise the mismatch.
    """

    negotiated: int | None
    refusal: ApiVersionRefusal | None


# A declared version is reported back verbatim when it did not parse, so it is
# truncated: the value is client-controlled and echoing it unbounded would let a
# caller inflate its own error body.
_MAX_ECHOED_DECLARATION = 32


def _window_phrase(floor: int, srv: int) -> str:
    """The window as a sentence fragment a person reads, not a range a parser reads.

    A one-version-wide window rendered mechanically says "speaks 1-1", which is the
    machine-shaped phrasing this plan exists to remove — and it is the *shipped*
    window, so it would be the only phrasing anyone ever saw.
    """
    if floor >= srv:
        return f"speaks version {srv}"
    return f"speaks versions {floor}-{srv}"


def negotiate(
    declared: str | None,
    *,
    server: int | None = None,
    minimum: int | None = None,
) -> ApiVersionOutcome:
    """Compare a client's declared version against the supported window.

    The ONE comparison. ``tests/test_api_version_one_origin.py`` asserts this
    function has exactly one non-test caller
    (:mod:`personalclaw.dashboard.api_version_gate`).

    ``server``/``minimum`` default to :data:`API_VERSION` /
    :data:`MIN_SUPPORTED_API_VERSION` and are read at CALL time, not bound at
    definition time, so a test (or a future per-entity window) can drive a
    different window and observe the absent-declaration rule — which is otherwise
    invisible while the shipped window is a single version wide.

    :param declared: The raw header value, or ``None``/``""`` when the client
        declared nothing. Absence resolves to ``minimum`` (see the module
        docstring for why it must not resolve to ``server``).
    """
    srv = API_VERSION if server is None else server
    floor = MIN_SUPPORTED_API_VERSION if minimum is None else minimum

    raw = (declared or "").strip()
    if not raw:
        # NO declaration ⇒ the OLDEST supported version, never the current one.
        # See the module docstring: assuming current is what lets an unversioned
        # old client through, and would credit it with a shape it cannot read.
        resolved = floor
        if floor <= resolved <= srv:
            return ApiVersionOutcome(negotiated=resolved, refusal=None)
        return ApiVersionOutcome(
            negotiated=resolved,
            refusal=ApiVersionRefusal(
                client_version=resolved,
                server_version=srv,
                min_supported_version=floor,
                upgrade="client",
                message=(
                    f"This client declared no API version, so it is treated as the oldest "
                    f"supported version ({floor}); this gateway {_window_phrase(floor, srv)}. "
                    f"Upgrade the client — reload the page to fetch the current build."
                ),
            ),
        )

    try:
        client = int(raw)
    except ValueError:
        return ApiVersionOutcome(
            negotiated=None,
            refusal=ApiVersionRefusal(
                client_version=raw[:_MAX_ECHOED_DECLARATION],
                server_version=srv,
                min_supported_version=floor,
                upgrade="client",
                message=(
                    f"This client declared API version "
                    f"{raw[:_MAX_ECHOED_DECLARATION]!r}, which is not an integer; this "
                    f"gateway {_window_phrase(floor, srv)}. Upgrade the client — reload the "
                    f"page to fetch the current build."
                ),
            ),
        )

    if client < floor:
        return ApiVersionOutcome(
            negotiated=client,
            refusal=ApiVersionRefusal(
                client_version=client,
                server_version=srv,
                min_supported_version=floor,
                upgrade="client",
                message=(
                    f"This client was built for API version {client}; this gateway "
                    f"{_window_phrase(floor, srv)}. Upgrade the client — reload the page to "
                    f"fetch the current build."
                ),
            ),
        )
    if client > srv:
        return ApiVersionOutcome(
            negotiated=client,
            refusal=ApiVersionRefusal(
                client_version=client,
                server_version=srv,
                min_supported_version=floor,
                upgrade="server",
                message=(
                    f"This client was built for API version {client}; this gateway "
                    f"{_window_phrase(floor, srv)}. Upgrade the gateway — run "
                    f"`personalclaw update`."
                ),
            ),
        )
    return ApiVersionOutcome(negotiated=client, refusal=None)
