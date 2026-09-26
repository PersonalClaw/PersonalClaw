"""One vocabulary for what a remote-provider failure SAYS.

Raw exception text is operator diagnostics, not user copy: a truncated
``ClientConnectorError(ConnectionKey(...))`` in a Settings toast tells the user
nothing and reads as a crash. Every dashboard surface that relays a provider
failure routes the exception through :func:`connectivity_guidance` so the same
failure produces the same sentence everywhere — the classification (and its
wording) started life in ``instance_routes._probe_failure`` and was extracted
verbatim so the probe endpoint and the catalog endpoints cannot drift apart.

Two deliberate boundaries:

* **Authored refusals keep their own words.** A ``ValueError`` raised with a
  human-written message ("model card missing 'name'") is designed copy, not a
  leak; callers that expect them pass ``str(exc)`` through and only consult
  this module for the connectivity/unexpected classes.
* **This module never picks an envelope.** The probe endpoint speaks
  ``json_error`` (nested ``{"error": {code, message}}``); the catalog
  endpoints keep their historical flat shapes (``{"models": [], "error"}``,
  ``{"error"}``). Copy is shared; wire contracts stay each surface's own.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

import aiohttp

__all__ = [
    "connectivity_guidance",
    "endpoint_problem",
    "relayed_failure_copy",
    "UNEXPECTED_FAILURE_COPY",
]

#: The fallback sentence for a failure that is none of the connectivity classes —
#: unexpected, so the honest copy is "unexpected", plus the one action that helps.
UNEXPECTED_FAILURE_COPY = (
    "The request failed unexpectedly. Check the provider's logs and try again."
)

#: Appended when a loopback endpoint refuses inside the container image. There, localhost is
#: the container itself, so a server running on the user's computer is never reachable at it —
#: the one refused connection whose fix is a different hostname, not a running server. The
#: aliases are the container runtimes' own names for the host (measured working from the
#: image: ``host.docker.internal`` under Docker, ``host.lima.internal`` under Lima/Finch).
CONTAINER_LOCALHOST_HINT = (
    " PersonalClaw is running in a container, where localhost is the container itself — to "
    "reach a server on your computer, use host.docker.internal (Docker) or host.lima.internal "
    "(Lima, Colima, Finch) in place of localhost."
)


def relayed_failure_copy(exc: BaseException, *, endpoint: str = "") -> str:
    """The user-facing text for relaying a provider-operation failure.

    Connectivity classes get their guidance sentence (naming ``endpoint`` when the
    caller knows it); a model-discovery failure and a ``ValueError`` carrying a
    message keep their own words (authored copy — "model card missing 'name'" is
    meant for the user); anything else is an internal crash whose text belongs in
    the log, so the wire gets :data:`UNEXPECTED_FAILURE_COPY`.
    """
    guidance = connectivity_guidance(exc, endpoint=endpoint)
    if guidance is not None:
        return guidance
    from personalclaw.llm.catalog import ModelDiscoveryError

    if isinstance(exc, (ModelDiscoveryError, ValueError)) and str(exc):
        return str(exc)
    return UNEXPECTED_FAILURE_COPY


def endpoint_problem(value: str) -> str | None:
    """Why ``value`` cannot be an endpoint URL, or ``None`` when it can.

    Checked when an endpoint is WRITTEN, because a malformed one used to be saved and only
    fail later as ``not%20a%20url/api/tags`` — the HTTP client's percent-encoded echo of it.
    """
    text = (value or "").strip()
    example = "for example http://localhost:11434"
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 — raises ValueError for an out-of-range or non-numeric port
    except ValueError:
        return f"“{text}” has an invalid port — use a number from 1 to 65535 ({example})."
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return (
            f"“{text}” isn't a URL — enter the full address, starting with http:// or "
            f"https:// ({example})."
        )
    return None


def _host_of(endpoint: str) -> str:
    try:
        return urlsplit(endpoint).hostname or ""
    except ValueError:
        return ""


def _is_loopback(host: str) -> bool:
    if host in ("localhost", "0.0.0.0"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _in_container() -> bool:
    try:
        from personalclaw.self_update import detect_install_kind

        return detect_install_kind() == "container"
    except Exception:  # noqa: BLE001 — a hint must never break the sentence it decorates
        return False


def connectivity_guidance(exc: BaseException, *, endpoint: str = "") -> str | None:
    """The guidance sentence for a connectivity failure, or ``None``.

    ``None`` means the exception is NOT one of the connectivity classes and the
    caller decides what its text is (an authored ``ValueError`` flows as-is; an
    internal crash gets :data:`UNEXPECTED_FAILURE_COPY`). The raw exception is
    the CALLER's to log — this function classifies, it does not report.

    ``endpoint`` — the address that was being reached, when the caller knows it — is
    named in the sentence, and a refused loopback address inside the container image
    gets :data:`CONTAINER_LOCALHOST_HINT`. Without it every sentence reads as before.
    """
    target = endpoint or "that endpoint"
    # aiohttp wraps the OS-level cause on its connector errors; inspect it when present.
    os_error = getattr(exc, "os_error", None)
    root: BaseException = os_error if isinstance(os_error, BaseException) else exc
    if isinstance(root, ConnectionRefusedError):
        hint = (
            CONTAINER_LOCALHOST_HINT
            if endpoint and _is_loopback(_host_of(endpoint)) and _in_container()
            else ""
        )
        return (
            f"Could not reach {target} — the connection was refused. Check the URL and that "
            f"the service is running.{hint}"
        )
    if isinstance(root, TimeoutError):
        at = f" to {endpoint}" if endpoint else ""
        return (
            f"The connection{at} timed out. Check the URL and that the service is reachable "
            "from here."
        )
    if isinstance(root, socket.gaierror):
        host = _host_of(endpoint)
        return (
            f"The host {host} could not be resolved. Check the endpoint's hostname."
            if host
            else "That host could not be resolved. Check the endpoint's hostname."
        )
    if isinstance(root, ssl.SSLError) or isinstance(exc, aiohttp.ClientSSLError):
        at = f" with {endpoint}" if endpoint else ""
        return (
            f"The TLS handshake{at} failed. Check the endpoint's certificate and that it "
            "expects HTTPS."
        )
    if isinstance(exc, aiohttp.InvalidURL):
        return f"“{endpoint or exc}” isn't a valid URL. Edit the endpoint to a full address."
    if isinstance(exc, aiohttp.ClientError):
        return f"Could not reach {target}. Check the URL and that the service is running."
    return None
