"""A remote MCP server's OAuth sign-in: how it signs you in, the authorization code flow with PKCE,
and the bearer token every request of its connection carries.

🔴 **THE GAP.** A server at a URL was sent only the static ``headers`` its spec declares (#3629). A
server that authenticates with OAuth — Sentry's, Linear's, Notion's — answers every request with
``401`` and ``WWW-Authenticate: Bearer resource_metadata=…``, and no static header answers that: its
token is issued to a client, for a person, after that person consents in a browser, and it expires.

**The flow** is the MCP authorization spec's, from the client side:

1. **Discovery.** An unauthenticated first request to the server returns the challenge. Its
   ``resource_metadata`` names the server's protected resource metadata (RFC 9728); without it the
   well-known URIs are tried. That document names the authorization server, whose own metadata
   (RFC 8414, or OpenID discovery) names its endpoints. A server that publishes no resource metadata
   is read the 2025-03-26 way: its own origin is its authorization server.
2. **The client.** Dynamic client registration (RFC 7591) when the authorization server offers it,
   as a public client (``token_endpoint_auth_method: none``). Otherwise the owner registers an app
   there and types its client ID (and the secret, when it issued one); a registration is reused
   while the issuer and the redirect address stay the same.
3. **Authorization.** The code flow with PKCE — S256 only; an authorization server that does not
   advertise it is refused, as the spec requires — a 256-bit ``state``, and ``resource`` (RFC 8707)
   naming the server, so the token is good for this server and no other. The browser comes back to
   the gateway itself: ``http://127.0.0.1:<port>/api/mcp/oauth/callback`` (RFC 8252's loopback
   redirect), or the dashboard's own address when it is served over HTTPS. An ``iss`` in the answer
   (RFC 9207) must name the authorization server the sign-in started with.
4. **Tokens.** The code is exchanged with the PKCE verifier, and the tokens go to the credential
   store under the server's own sign-in owner (``secret_refs.mcp_sign_in_owner``). The spec's
   ``signIn`` block holds references and what the grant is for — issuer, token endpoint, client id,
   resource — never a token.
5. **Each request.** :func:`connection_auth` is the ``httpx.Auth`` the transport clients are given.
   It reads the tokens from the store at each request, renews them when they are known to have
   expired or the server answers 401, and never sends a token to a URL outside the resource it was
   issued for. A renewal the authorization server refuses ENDS the sign-in: the dead tokens are
   deleted, the security log records it, the owner gets a notification, and the connection fails
   with :class:`SignInRequired`, whose sentence says to sign in again — on the Tools page, and to an
   agent whose tool call failed.

**Every request here goes through the egress guard** (``net.fetch`` under
``net.policy.mcp_sign_in_egress_policy``), because apart from the server itself every URL is one its
answers named. The server's own host is the owner's choice; every other host must be public, and
the authorization server's endpoints must be HTTPS unless the server itself is plain HTTP on this
machine.

**A sign-in in progress lives in this process only** (``_PENDING``): its PKCE verifier and a client
secret typed for it are never written anywhere, and one not finished within ten minutes is dropped.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import time
import weakref
from collections.abc import AsyncGenerator, Generator, Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from personalclaw.config.secret_refs import MCP_SIGN_IN

logger = logging.getLogger(__name__)

#: The gateway route an authorization server sends the browser back to.
CALLBACK_PATH = "/api/mcp/oauth/callback"

#: How long a started sign-in may take: the owner signs in and consents in another tab.
_PENDING_TTL_SECS = 600.0
#: At most this many sign-ins in progress at once; starting one more drops the oldest.
_PENDING_CAP = 16
#: A token this close to its expiry is renewed before it is sent rather than after it is refused.
_EXPIRY_SKEW_SECS = 60.0
#: The name PersonalClaw registers under, which the authorization server shows on its consent page.
_CLIENT_NAME = "PersonalClaw"
#: Token-endpoint answers that mean the grant itself is no longer good. Anything else a renewal
#: meets (the network, a 5xx) is the world's condition and leaves the tokens where they are.
_GRANT_ENDED_STATUSES = frozenset({400, 401})


class SignInRequired(Exception):
    """The server needs its owner to sign in, or to sign in again, before it can be used.

    Raised by the connection's auth and read by ``mcp_client`` as the connection's error, so the
    sentence is what the Tools page shows and what an agent's failed tool call says.
    """


class SignInFailed(Exception):
    """Starting or finishing a sign-in failed. ``code`` is the wire code the route answers with,
    ``status`` its HTTP status, ``extra`` keys for the error object, and the message the sentence
    the owner reads."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "mcp_sign_in_failed",
        status: int = 502,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = dict(extra or {})


class _TokenRefused(Exception):
    """The token endpoint answered, and not with a token."""

    def __init__(self, status: int, error: str, description: str) -> None:
        detail = description or error or f"HTTP {status}"
        super().__init__(detail)
        self.status = status
        self.error = error


# ── addresses ───────────────────────────────────────────────────────────────


def canonical_resource(url: str) -> str:
    """The server's resource identifier (RFC 8707, as the MCP spec uses it): its URL with the
    scheme and host lower-cased and no fragment."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def covers(resource: str, url: str) -> bool:
    """Whether a token issued for ``resource`` may be sent to ``url``: the same origin, and the
    URL's path at or under the resource's."""
    have, want = urlsplit(canonical_resource(resource)), urlsplit(canonical_resource(url))
    if not have.scheme or (have.scheme, have.netloc) != (want.scheme, want.netloc):
        return False
    base = have.path.rstrip("/")
    path = want.path.rstrip("/")
    return path == base or path.startswith(base + "/")


def _is_loopback_host(host: str) -> bool:
    host = (host or "").lower().strip("[]")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def redirect_uri_for(dashboard_origin: str) -> str | None:
    """Where an authorization server sends the browser back to, for a dashboard served at
    ``dashboard_origin`` (``scheme://host[:port]``), or ``None`` when it cannot be sent back there.

    The MCP spec allows a loopback or an HTTPS redirect. A dashboard on this machine over plain
    HTTP is sent back to ``127.0.0.1`` on the port the browser used (RFC 8252 §7.3: the literal
    loopback address, not ``localhost``), which also reaches a gateway behind a port mapping or the
    dev server's proxy. One served over HTTPS is sent back to that same address. A plain-HTTP
    address on another machine is neither, and gets ``None``.
    """
    parts = urlsplit(dashboard_origin.strip())
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    if parts.scheme == "https":
        return f"https://{parts.netloc}{CALLBACK_PATH}"
    if parts.scheme == "http" and _is_loopback_host(parts.hostname):
        return f"http://127.0.0.1:{port or 80}{CALLBACK_PATH}"
    return None


def _endpoint_problem(url: str, server_url: str, what: str) -> str | None:
    """Why ``url`` (``what`` names it) cannot take part in signing in to ``server_url``, or
    ``None``. HTTPS, which the MCP spec requires of an authorization server — unless the server
    itself is plain HTTP on this machine, where an authorization server beside it is a server
    under development, and a loopback address never leaves the machine."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return None
    server = urlsplit(server_url)
    if (
        parts.scheme == "http"
        and _is_loopback_host(parts.hostname or "")
        and server.scheme == "http"
        and _is_loopback_host(server.hostname or "")
    ):
        return None
    return f"its {what} ({url}) is not an HTTPS address, and a sign-in is sent only over HTTPS"


def _issuers_match(a: str, b: str) -> bool:
    """RFC 8414 §3.3's simple string comparison, with one tolerance: a root issuer written with
    and without its trailing slash names the same server."""
    if a == b:
        return True
    return a.rstrip("/") == b.rstrip("/") and urlsplit(a).path in ("", "/")


# ── the challenge ───────────────────────────────────────────────────────────

_AUTH_PARAM_RE = re.compile(
    r'([A-Za-z0-9!#$%&\'*+.^_`|~-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]*))'
)
_AUTH_TOKEN_RE = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+")
_SEPARATORS_RE = re.compile(r"[\s,]+")


def challenges(header: str) -> dict[str, dict[str, str]]:
    """Each challenge in a ``WWW-Authenticate`` value (RFC 9110 §11.6.1), by lower-cased scheme,
    with its parameters. One header may carry several (``Basic realm="x", Bearer scope="y"``)."""
    found: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    text = header or ""
    pos = 0
    while pos < len(text):
        gap = _SEPARATORS_RE.match(text, pos)
        if gap:
            pos = gap.end()
            continue
        param = _AUTH_PARAM_RE.match(text, pos)
        if param and current is not None:
            quoted, bare = param.group(2), param.group(3)
            value = re.sub(r"\\(.)", r"\1", quoted) if quoted is not None else bare or ""
            current[param.group(1).lower()] = value
            pos = param.end()
            continue
        token = _AUTH_TOKEN_RE.match(text, pos)
        if not token:
            break
        current = found.setdefault(token.group(0).lower(), {})
        pos = token.end()
    return found


def bearer_challenge(response: Any) -> dict[str, str] | None:
    """The ``Bearer`` challenge of a 401 (or a 403) ``response``, or ``None``: the one answer that
    says an OAuth sign-in would let the request through. Read off httpx's and aiohttp's
    responses alike (``status_code`` / ``status``, ``headers``)."""
    status = getattr(response, "status_code", None) or getattr(response, "status", None)
    if status not in (401, 403):
        return None
    headers = getattr(response, "headers", None) or {}
    return challenges(str(headers.get("WWW-Authenticate") or "")).get("bearer")


# ── the gateway's own HTTP, through the egress guard ────────────────────────


@dataclass(frozen=True)
class _Answer:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.body or b"null")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None


def _policy(server_url: str) -> Any:
    from personalclaw.net.policy import mcp_sign_in_egress_policy

    return mcp_sign_in_egress_policy(urlsplit(server_url).hostname or "")


async def _request(
    url: str,
    server_url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
) -> _Answer:
    """One guarded request (``net.fetch``). A refusal or an unreachable host is
    :class:`SignInFailed`, naming the address."""
    import aiohttp

    from personalclaw.guardrails.writes import LiveWriteDisabled
    from personalclaw.net.client import EgressBlocked, fetch

    try:
        got = await fetch(
            url, policy=_policy(server_url), method=method, headers=dict(headers or {}), data=data
        )
    except EgressBlocked as exc:
        raise SignInFailed(f"PersonalClaw will not connect to {url}: {exc}") from exc
    except LiveWriteDisabled as exc:
        raise SignInFailed(f"Writes to other hosts are turned off, so {url} was not asked") from exc
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise SignInFailed(
            f"PersonalClaw could not reach {url}: {exc or type(exc).__name__}"
        ) from exc
    return _Answer(got.status, got.headers, got.body)


async def _get_json(url: str, server_url: str) -> dict[str, Any] | None:
    """A metadata document, or ``None`` when ``url`` does not serve one."""
    got = await _request(url, server_url, headers={"Accept": "application/json"})
    return got.json() if got.status == 200 else None


# ── discovery ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuthorizationServer:
    """What discovery found: the authorization server, and what to ask it for."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    token_auth_methods: tuple[str, ...]
    iss_parameter: bool
    resource: str
    scope: str


def _initialize_body() -> bytes:
    from mcp.types import LATEST_PROTOCOL_VERSION

    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _CLIENT_NAME, "version": "1.0.0"},
            },
        }
    ).encode("utf-8")


async def _challenge(
    server: str, url: str, transport: str, headers: Mapping[str, str]
) -> dict[str, str]:
    """The server's answer to the first request its transport makes, sent without a token: the
    parameters of its Bearer challenge (``{}`` when it refused with no challenge at all)."""
    sent = {k: v for k, v in headers.items() if k.lower() != "authorization"}
    if transport == "sse":
        got = await _request(url, url, headers={**sent, "Accept": "text/event-stream"})
    else:
        got = await _request(
            url,
            url,
            method="POST",
            headers={
                **sent,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            data=_initialize_body(),
        )
    if 200 <= got.status < 300:
        raise SignInFailed(
            f"{server} answered without asking you to sign in, so there is nothing to sign in to.",
            code="mcp_sign_in_not_offered",
            status=409,
        )
    if got.status not in (401, 403):
        raise SignInFailed(f"{server} answered HTTP {got.status} instead of asking you to sign in.")
    offered = challenges(str(got.headers.get("WWW-Authenticate") or ""))
    if "bearer" in offered:
        return offered["bearer"]
    if offered:
        schemes = ", ".join(sorted(offered))
        raise SignInFailed(
            f"{server} asks for a kind of sign-in PersonalClaw does not do ({schemes}). If it "
            "takes an API key, add it to the server as a header instead.",
            code="mcp_sign_in_not_offered",
            status=409,
        )
    # A refusal with no challenge at all: a server on the 2025-03-26 spec still publishes metadata.
    return {}


def _resource_metadata_urls(server_url: str, named: str) -> list[str]:
    parts = urlsplit(server_url)
    base = f"{parts.scheme}://{parts.netloc}"
    urls = [named] if named else []
    if parts.path and parts.path != "/":
        urls.append(f"{base}/.well-known/oauth-protected-resource{parts.path.rstrip('/')}")
    urls.append(f"{base}/.well-known/oauth-protected-resource")
    return list(dict.fromkeys(urls))


def _server_metadata_urls(issuer: str) -> list[str]:
    parts = urlsplit(issuer)
    base = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if path:
        return [
            f"{base}/.well-known/oauth-authorization-server{path}",
            f"{base}/.well-known/openid-configuration{path}",
            f"{base}{path}/.well-known/openid-configuration",
        ]
    return [
        f"{base}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration",
    ]


async def discover(
    server: str, url: str, transport: str, headers: Mapping[str, str] | None = None
) -> AuthorizationServer:
    """How server ``server`` at ``url`` signs a person in. :class:`SignInFailed` says why not."""
    offered = await _challenge(server, url, transport, headers or {})

    resource = canonical_resource(url)
    issuer = ""
    scopes_supported: list[str] = []
    named = offered.get("resource_metadata", "")
    for candidate in _resource_metadata_urls(url, named):
        problem = _endpoint_problem(candidate, url, "resource metadata")
        if problem is not None:
            raise SignInFailed(f"{server} cannot be signed in to: {problem}.")
        document = await _get_json(candidate, url)
        servers = document.get("authorization_servers") if document else None
        if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
            continue
        published = document.get("resource") if document else None
        if isinstance(published, str) and published:
            if not covers(published, url):
                raise SignInFailed(
                    f"{server} cannot be signed in to: its resource metadata is for {published}, "
                    f"not for {resource}."
                )
            resource = canonical_resource(published)
        issuer = servers[0]
        listed = document.get("scopes_supported") if document else None
        if isinstance(listed, list):
            scopes_supported = [s for s in listed if isinstance(s, str) and s]
        break
    if not issuer:
        # The 2025-03-26 spec: no resource metadata, so the server's own origin is its issuer.
        parts = urlsplit(url)
        issuer = f"{parts.scheme}://{parts.netloc}"

    problem = _endpoint_problem(issuer, url, "authorization server")
    if problem is not None:
        raise SignInFailed(f"{server} cannot be signed in to: {problem}.")
    metadata: dict[str, Any] | None = None
    for candidate in _server_metadata_urls(issuer):
        metadata = await _get_json(candidate, url)
        if metadata is not None:
            break
    if metadata is None:
        raise SignInFailed(
            f"{server} refused the connection but does not publish how to sign in: {issuer} "
            "serves no authorization server metadata. If it takes an API key, add it to the "
            "server as a header instead.",
            code="mcp_sign_in_not_offered",
            status=409,
        )
    named_issuer = metadata.get("issuer")
    if not isinstance(named_issuer, str) or not _issuers_match(named_issuer, issuer):
        raise SignInFailed(
            f"{issuer}'s metadata names a different authorization server ({named_issuer}), so it "
            "was not used."
        )
    authorize, token = metadata.get("authorization_endpoint"), metadata.get("token_endpoint")
    if not isinstance(authorize, str) or not isinstance(token, str) or not authorize or not token:
        raise SignInFailed(f"{issuer} does not name its authorization and token endpoints.")
    methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or "S256" not in methods:
        raise SignInFailed(
            f"{issuer} does not say it supports PKCE with S256, which PersonalClaw signs in with."
        )
    register = metadata.get("registration_endpoint")
    register = register if isinstance(register, str) else ""
    for what, endpoint in (
        ("authorization endpoint", authorize),
        ("token endpoint", token),
        ("registration endpoint", register),
    ):
        problem = _endpoint_problem(endpoint, url, what) if endpoint else None
        if problem is not None:
            raise SignInFailed(f"{server} cannot be signed in to: {problem}.")
    auth_methods = metadata.get("token_endpoint_auth_methods_supported")
    scope = offered.get("scope") or " ".join(scopes_supported)
    return AuthorizationServer(
        issuer=named_issuer,
        authorization_endpoint=authorize,
        token_endpoint=token,
        registration_endpoint=register,
        token_auth_methods=(
            tuple(m for m in auth_methods if isinstance(m, str))
            if isinstance(auth_methods, list)
            else ()
        ),
        iss_parameter=metadata.get("authorization_response_iss_parameter_supported") is True,
        resource=resource,
        scope=scope,
    )


# ── the client ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Client:
    id: str
    secret: str
    method: str  # none | client_secret_basic | client_secret_post
    registered: bool


def _typed_client(client_id: str, client_secret: str, found: AuthorizationServer) -> _Client:
    """The app the owner registered themselves. With a secret, the way to send it is the first of
    Basic (RFC 6749's default) and form post the authorization server lists."""
    if not client_secret:
        return _Client(client_id, "", "none", registered=False)
    listed = found.token_auth_methods or ("client_secret_basic",)
    method = next(
        (m for m in ("client_secret_basic", "client_secret_post") if m in listed),
        "client_secret_basic",
    )
    return _Client(client_id, client_secret, method, registered=False)


async def _register(
    server: str, url: str, found: AuthorizationServer, redirect_uri: str
) -> _Client:
    """Dynamic client registration (RFC 7591), as a public client."""
    request = {
        "client_name": _CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if found.scope:
        request["scope"] = found.scope
    got = await _request(
        found.registration_endpoint,
        url,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        data=json.dumps(request).encode("utf-8"),
    )
    answer = got.json() or {}
    if got.status not in (200, 201):
        why = answer.get("error_description") or answer.get("error") or f"HTTP {got.status}"
        raise SignInFailed(f"{found.issuer} would not register PersonalClaw for {server}: {why}.")
    client_id = answer.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise SignInFailed(f"{found.issuer} registered PersonalClaw without giving it a client ID.")
    secret = answer.get("client_secret")
    secret = secret if isinstance(secret, str) else ""
    method = answer.get("token_endpoint_auth_method")
    if not isinstance(method, str) or not method:
        method = "client_secret_basic" if secret else "none"
    return _Client(client_id, secret, method, registered=True)


# ── tokens ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tokens:
    """One grant's tokens, as the credential store holds them (one JSON value)."""

    access_token: str
    refresh_token: str = ""
    expires_at: float | None = None
    scope: str = ""

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) >= self.expires_at - _EXPIRY_SKEW_SECS

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "scope": self.scope,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> Tokens | None:
        try:
            data = json.loads(text)
        except ValueError:
            return None
        access = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(access, str) or not access:
            return None
        expires = data.get("expires_at")
        refresh, scope = data.get("refresh_token"), data.get("scope")
        return cls(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) else "",
            expires_at=float(expires) if isinstance(expires, (int, float)) else None,
            scope=scope if isinstance(scope, str) else "",
        )


async def _token_request(
    *, issuer: str, token_endpoint: str, server_url: str, client: _Client, form: dict[str, str]
) -> Tokens:
    """POST ``form`` to the token endpoint with the client's authentication. :class:`_TokenRefused`
    when it answers with an error, :class:`SignInFailed` when it cannot be asked or answers
    something that is not a bearer token."""
    body = dict(form)
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if client.method == "client_secret_basic" and client.secret:
        pair = f"{quote(client.id, safe='')}:{quote(client.secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode("utf-8")).decode("ascii")
    else:
        body["client_id"] = client.id
        if client.method == "client_secret_post" and client.secret:
            body["client_secret"] = client.secret
    got = await _request(
        token_endpoint, server_url, method="POST", headers=headers, data=urlencode(body).encode()
    )
    answer = got.json() or {}
    if got.status != 200:
        error, description = answer.get("error"), answer.get("error_description")
        raise _TokenRefused(
            got.status,
            error if isinstance(error, str) else "",
            description if isinstance(description, str) else "",
        )
    access = answer.get("access_token")
    if not isinstance(access, str) or not access:
        raise SignInFailed(f"{issuer} answered without an access token.")
    kind = answer.get("token_type")
    if isinstance(kind, str) and kind.lower() != "bearer":
        raise SignInFailed(
            f"{issuer} issued a {kind} token; PersonalClaw sends bearer tokens only."
        )
    lifetime = answer.get("expires_in")
    refresh, scope = answer.get("refresh_token"), answer.get("scope")
    return Tokens(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) else "",
        expires_at=(
            time.time() + float(lifetime)
            if isinstance(lifetime, (int, float)) and not isinstance(lifetime, bool)
            else None
        ),
        scope=scope if isinstance(scope, str) else "",
    )


def _tokens_key(server: str) -> str:
    from personalclaw.config.secret_refs import mcp_sign_in_owner

    return mcp_sign_in_owner(server).key("tokens")


def _client_secret_key(server: str) -> str:
    from personalclaw.config.secret_refs import mcp_sign_in_owner

    return mcp_sign_in_owner(server).key("clientSecret")


def _keep(key: str, value: str) -> None:
    """Store ``value`` under ``key`` and prove the store kept it."""
    from personalclaw.config.credentials import get_credential, save_credential

    save_credential(key, value)
    if get_credential(key) != value:
        raise OSError("the credential store did not keep the sign-in; nothing was written")


def _stored_tokens(server: str, grant: Mapping[str, Any]) -> Tokens | None:
    """The grant's tokens from the store, or ``None`` when it holds none."""
    from personalclaw.config.secret_refs import resolve_mcp_sign_in_value

    text = resolve_mcp_sign_in_value(server, grant.get("tokens"))
    return Tokens.from_json(text) if text else None


def _grant_client(server: str, grant: Mapping[str, Any]) -> _Client:
    from personalclaw.config.secret_refs import resolve_mcp_sign_in_value

    secret = (
        resolve_mcp_sign_in_value(server, grant["clientSecret"])
        if grant.get("clientSecret")
        else ""
    )
    return _Client(
        str(grant.get("clientId") or ""),
        secret,
        str(grant.get("tokenAuthMethod") or "none"),
        registered=bool(grant.get("registered")),
    )


def _audit(
    server: str, operation: str, outcome: str, error: str = "", *, caller: str = "system"
) -> None:
    """One security-log row. ``caller`` is ``dashboard`` for what the owner started from the
    Tools page (a sign-in and its answer), ``system`` for what a connection did by itself."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=caller,
            operation=operation,
            outcome=outcome,
            source="mcp_oauth",
            resources=server,
            error=error[:300],
        )
    except Exception:  # noqa: BLE001 — a log that cannot write must not change what happened
        logger.warning("SEL audit failed for MCP sign-in %s", operation, exc_info=True)


def sign_in_needed_text(server: str) -> str:
    """What a server that wants its owner to sign in, and has no sign-in, says."""
    return f"{server} needs you to sign in. Use Sign in on its card on the Tools page."


def token_or_sign_in_text(server: str) -> str:
    """What a server says that refused the connection with a bare Bearer challenge: it wants a
    token, and its answer does not say whether a sign-in gives one or it takes a static token (an
    API key), so the sentence claims neither. The Tools page's probe finds out, and its card says
    which (`mcp_discovery._probe_remote`)."""
    return (
        f"{server} refused the connection: it wants a sign-in or a token it was not sent. Its card "
        "on the Tools page says which, and what to do."
    )


def _say_signed_out(server: str) -> str:
    return f"You are signed out of {server}. Sign in again on the Tools page."


def _say_ended(server: str, reason: str) -> str:
    return f"Your sign-in to {server} has ended: {reason}. Sign in again on the Tools page."


def _end_sign_in(server: str, reason: str) -> None:
    """A grant the authorization server no longer honours: its tokens are deleted (they cannot be
    used, and must not be sent again), the security log records it, and the owner is told."""
    from personalclaw.config.credentials import delete_credential

    delete_credential(_tokens_key(server))
    logger.warning("MCP server %r: the sign-in ended: %s", server, reason)
    _audit(server, "mcp_sign_in_ended", "ended", reason)
    try:
        from personalclaw import notification_kinds
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        state = get_dashboard_state()
        if state is not None:
            state.notify(
                notification_kinds.WARNING,
                f"Sign in to {server} again",
                f"{_say_ended(server, reason)} Its tools cannot be used until you do.",
                meta={"event": "mcp.sign_in_ended", "server": server},
            )
    except Exception:  # noqa: BLE001 — the Tools page and the failed call still say it
        logger.warning("could not notify that the sign-in to %r ended", server, exc_info=True)


# ── starting and finishing a sign-in ────────────────────────────────────────


@dataclass(frozen=True)
class _Pending:
    server: str
    server_url: str
    found: AuthorizationServer
    client: _Client
    redirect_uri: str
    verifier: str
    started: float


_PENDING: dict[str, _Pending] = {}


def _forget_stale(now: float) -> None:
    for state in [s for s, p in _PENDING.items() if now - p.started > _PENDING_TTL_SECS]:
        del _PENDING[state]


def _remember(state: str, pending: _Pending) -> None:
    _forget_stale(pending.started)
    for other in [s for s, p in _PENDING.items() if p.server == pending.server]:
        del _PENDING[other]  # one sign-in per server: the newest is the one the owner is in
    while len(_PENDING) >= _PENDING_CAP:
        del _PENDING[min(_PENDING, key=lambda s: _PENDING[s].started)]
    _PENDING[state] = pending


def _take(state: str) -> _Pending | None:
    _forget_stale(time.time())
    return _PENDING.pop(state, None) if state else None


def _pkce() -> tuple[str, str]:
    """A PKCE verifier (RFC 7636: 43-128 unreserved characters; 86 here) and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def start_sign_in(
    server: str,
    spec: Mapping[str, Any],
    *,
    redirect_uri: str,
    client_id: str = "",
    client_secret: str = "",
) -> str:
    """Begin signing in to ``server``: the authorization URL the owner's browser opens.

    ``client_id`` (and ``client_secret``) is an app the owner registered themselves; without one, a
    registration this server's sign-in already holds is reused, else PersonalClaw registers itself.
    """
    from personalclaw.config.secret_refs import ForeignSecretReference, resolve_mcp_values
    from personalclaw.mcp_discovery import mcp_transport

    url = str(spec.get("url") or "")
    transport = mcp_transport(spec)
    if transport not in ("http", "sse") or not url:
        raise SignInFailed(
            f"{server} is started with a command, so it has no sign-in: only a server at a URL "
            "signs in.",
            code="mcp_sign_in_unsupported",
            status=409,
        )
    try:
        headers = resolve_mcp_values(server, "headers", spec.get("headers"))
    except ForeignSecretReference as exc:
        raise SignInFailed(str(exc), code="secret_owned_elsewhere", status=400) from exc
    found = await discover(server, url, transport, headers)

    earlier = spec.get(MCP_SIGN_IN)
    earlier = earlier if isinstance(earlier, Mapping) else {}
    if client_id:
        client = _typed_client(client_id, client_secret, found)
    elif (
        earlier.get("clientId")
        and _issuers_match(str(earlier.get("issuer") or ""), found.issuer)
        and earlier.get("redirectUri") == redirect_uri
    ):
        client = _grant_client(server, earlier)
    elif found.registration_endpoint:
        client = await _register(server, url, found, redirect_uri)
    else:
        raise SignInFailed(
            f"{_host(found.issuer)} does not let PersonalClaw register itself. Register an app "
            f"there with the redirect URL {redirect_uri}, then enter its client ID.",
            code="mcp_sign_in_needs_client_id",
            status=400,
            extra={"detail": {"redirectUri": redirect_uri, "issuer": found.issuer}},
        )

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    _remember(
        state,
        _Pending(server, url, found, client, redirect_uri, verifier, started=time.time()),
    )
    query = {
        "response_type": "code",
        "client_id": client.id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": found.resource,
    }
    if found.scope:
        query["scope"] = found.scope
    _audit(server, "mcp_sign_in_started", "started", caller="dashboard")
    joiner = "&" if urlsplit(found.authorization_endpoint).query else "?"
    return f"{found.authorization_endpoint}{joiner}{urlencode(query)}"


@dataclass(frozen=True)
class FinishedSignIn:
    """A sign-in whose tokens are stored: ``block`` is the ``signIn`` value for the server's spec,
    and ``server_url`` the URL it was signed in for (the spec must still have it)."""

    server: str
    server_url: str
    block: dict[str, Any]


async def finish_sign_in(params: Mapping[str, str]) -> FinishedSignIn:
    """Complete the sign-in an authorization server sent the browser back from (``params`` is the
    callback's query). The tokens are stored before this returns; the caller writes ``block``."""
    from personalclaw.config.secret_refs import make_ref

    pending = _take(str(params.get("state") or ""))
    if pending is None:
        raise SignInFailed(
            "This sign-in has expired or was already finished. Start it again from the Tools "
            "page.",
            code="mcp_sign_in_expired",
            status=400,
        )
    server, found = pending.server, pending.found
    if params.get("error"):
        detail = params.get("error_description") or params.get("error")
        _audit(server, "mcp_sign_in", "refused", str(detail), caller="dashboard")
        raise SignInFailed(
            f"{_host(found.issuer)} did not sign you in to {server}: {detail}.",
            code="mcp_sign_in_refused",
            status=400,
        )
    iss = params.get("iss")
    if (iss is not None and not _issuers_match(iss, found.issuer)) or (
        iss is None and found.iss_parameter
    ):
        _audit(
            server,
            "mcp_sign_in",
            "refused",
            f"answer from {iss!r}, not {found.issuer}",
            caller="dashboard",
        )
        raise SignInFailed(
            f"The answer did not come from {found.issuer}, the authorization server this sign-in "
            "started with, so it was not used.",
            code="mcp_sign_in_refused",
            status=400,
        )
    code = str(params.get("code") or "")
    if not code:
        raise SignInFailed(
            f"{_host(found.issuer)} sent no authorization code.",
            code="mcp_sign_in_refused",
            status=400,
        )
    try:
        tokens = await _token_request(
            issuer=found.issuer,
            token_endpoint=found.token_endpoint,
            server_url=pending.server_url,
            client=pending.client,
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "code_verifier": pending.verifier,
                "resource": found.resource,
            },
        )
    except _TokenRefused as exc:
        _audit(server, "mcp_sign_in", "refused", str(exc), caller="dashboard")
        raise SignInFailed(f"{_host(found.issuer)} would not finish the sign-in: {exc}.") from exc

    _keep(_tokens_key(server), tokens.to_json())
    block: dict[str, Any] = {
        "issuer": found.issuer,
        "tokenEndpoint": found.token_endpoint,
        "clientId": pending.client.id,
        "tokenAuthMethod": pending.client.method,
        "registered": pending.client.registered,
        "redirectUri": pending.redirect_uri,
        "resource": found.resource,
        "tokens": make_ref(_tokens_key(server)),
    }
    if found.scope:
        block["scope"] = found.scope
    if pending.client.secret:
        _keep(_client_secret_key(server), pending.client.secret)
        block["clientSecret"] = make_ref(_client_secret_key(server))
    _audit(server, "mcp_sign_in", "completed", caller="dashboard")
    return FinishedSignIn(server, pending.server_url, block)


def forget_sign_in_values(server: str) -> int:
    """Delete every value server ``server``'s sign-in stored — a sign-out, or a sign-in whose
    server changed before it could be saved. Returns how many were deleted."""
    from personalclaw.config.secret_refs import mcp_sign_in_owner, purge

    return purge([mcp_sign_in_owner(server).prefix])


# ── what the Tools page shows ───────────────────────────────────────────────

SIGNED_IN = "signed_in"
SIGNED_OUT = "signed_out"
REQUIRED = "required"


def sign_in_state(server: str, spec: Mapping[str, Any], status: str) -> dict[str, str] | None:
    """The server's sign-in as the Tools page shows it, or ``None`` for a server that has none and
    did not ask for one. ``status`` is its probe status (``signin`` when it asked).

    Presence only: whether the store HOLDS the grant's tokens is read from its key names, so no
    value is read to draw a list.
    """
    from personalclaw.config.credentials import credential_names
    from personalclaw.config.secret_refs import ref_key

    grant = spec.get(MCP_SIGN_IN)
    if isinstance(grant, Mapping):
        key = ref_key(grant.get("tokens"))
        held = key is not None and key in credential_names()
        fits = covers(str(grant.get("resource") or ""), str(spec.get("url") or ""))
        return {"method": "oauth", "state": SIGNED_IN if held and fits else SIGNED_OUT}
    if status == "signin":
        return {"method": "oauth", "state": REQUIRED}
    return None


# ── each request of the connection ──────────────────────────────────────────

#: One renewal at a time per server (a stateful server has a connection per session, all sharing
#: one grant), per event loop — an asyncio lock belongs to the loop it was first used on.
_RENEWAL_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _renewal_lock(server: str) -> asyncio.Lock:
    locks = _RENEWAL_LOCKS.setdefault(asyncio.get_running_loop(), {})
    return locks.setdefault(server, asyncio.Lock())


class _SignedInAuth(httpx.Auth):
    """The bearer token of server ``server``'s sign-in on every request of its connection."""

    requires_request_body = True  # a request refused with 401 is sent again after a renewal

    def __init__(self, server: str, grant: Mapping[str, Any], url: str) -> None:
        self._server = server
        self._grant = dict(grant)
        self._url = url

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        raise RuntimeError("an MCP server's sign-in is sent by the async client only")
        yield request  # pragma: no cover — makes this a generator, as httpx calls it

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        tokens = await self._usable()
        request.headers["Authorization"] = f"Bearer {tokens.access_token}"
        response = yield request
        if response.status_code != 401:
            return
        tokens = await self._renew(tokens)
        request.headers["Authorization"] = f"Bearer {tokens.access_token}"
        response = yield request
        if response.status_code == 401:
            reason = "it refused the token its authorization server had just renewed"
            await asyncio.to_thread(_end_sign_in, self._server, reason)
            raise SignInRequired(_say_ended(self._server, reason))

    async def _usable(self) -> Tokens:
        resource = str(self._grant.get("resource") or "")
        if not covers(resource, self._url):
            # A token is never sent to an address outside the resource it was issued for.
            raise SignInRequired(
                f"{self._server} was signed in for {resource or 'another address'}, and its URL "
                "has changed since. Sign in again on the Tools page."
            )
        tokens = await asyncio.to_thread(_stored_tokens, self._server, self._grant)
        if tokens is None:
            raise SignInRequired(_say_signed_out(self._server))
        if tokens.expired():
            return await self._renew(tokens)
        return tokens

    async def _renew(self, stale: Tokens) -> Tokens:
        async with _renewal_lock(self._server):
            current = await asyncio.to_thread(_stored_tokens, self._server, self._grant)
            if current is not None and current.access_token != stale.access_token:
                if not current.expired():
                    return current  # another connection to this server renewed it meanwhile
            if current is None:
                raise SignInRequired(_say_signed_out(self._server))
            if not current.refresh_token:
                reason = "it expired, and its authorization server gave no way to renew it"
                await asyncio.to_thread(_end_sign_in, self._server, reason)
                raise SignInRequired(_say_ended(self._server, reason))
            client = await asyncio.to_thread(_grant_client, self._server, self._grant)
            issuer = str(self._grant.get("issuer") or "")
            try:
                fresh = await _token_request(
                    issuer=issuer,
                    token_endpoint=str(self._grant.get("tokenEndpoint") or ""),
                    server_url=self._url,
                    client=client,
                    form={
                        "grant_type": "refresh_token",
                        "refresh_token": current.refresh_token,
                        "resource": str(self._grant.get("resource") or ""),
                    },
                )
            except _TokenRefused as exc:
                if exc.status not in _GRANT_ENDED_STATUSES:
                    raise RuntimeError(
                        f"{_host(issuer)} could not renew the sign-in to {self._server} right now "
                        f"({exc}); PersonalClaw will try again on the next call."
                    ) from exc
                reason = f"{_host(issuer)} refused to renew it ({exc.error or exc})"
                await asyncio.to_thread(_end_sign_in, self._server, reason)
                raise SignInRequired(_say_ended(self._server, reason)) from exc
            except SignInFailed as exc:
                raise RuntimeError(
                    f"PersonalClaw could not renew the sign-in to {self._server} right now: {exc}"
                ) from exc
            if not fresh.refresh_token:
                fresh = replace(fresh, refresh_token=current.refresh_token)
            await asyncio.to_thread(_keep, _tokens_key(self._server), fresh.to_json())
            _audit(self._server, "mcp_sign_in_renewed", "completed")
            return fresh


def connection_auth(server: str, spec: Mapping[str, Any]) -> httpx.Auth | None:
    """The auth the transport client of server ``server``'s connection is given, or ``None`` for a
    server with no sign-in (it is sent only its spec's ``headers``)."""
    grant = spec.get(MCP_SIGN_IN)
    if not isinstance(grant, Mapping):
        return None
    return _SignedInAuth(server, grant, str(spec.get("url") or ""))


def sign_in_identity(spec: Mapping[str, Any]) -> dict[str, str]:
    """What names a sign-in in the connection pool's key: who issued it, to which client, for which
    resource. Never a token — a renewal replaces those while the connection stays open."""
    grant = spec.get(MCP_SIGN_IN)
    if not isinstance(grant, Mapping):
        return {}
    return {k: str(grant.get(k) or "") for k in ("issuer", "clientId", "resource")}
