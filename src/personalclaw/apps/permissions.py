"""App permission enforcement (A5) — make declared ``Permissions`` real.

An app's manifest declares a permission scope (``permissions``: api / events /
mcpTools / storage / network / memory / cron). Until now that was declarative
only. This module turns it into an enforced boundary, server-side — the
defense-in-depth half of the plan (the SDK enforces client-side too in A6, but a
client check is bypassable, so the gateway must reject independently).

Identity: a request carrying an app-scoped token has ``request["app"]`` set to
the app name (minted in :mod:`token_auth`). When that's present, the app
enforcement middleware checks the request path against the app's
``permissions.api`` allowlist — an undeclared path is rejected ``403`` before the
handler runs. A request with no app identity (the owner/dashboard) is unaffected.

The checker is the seam every capability enforcement consults (untrusted-app
sandbox). Enforcement status of each method:

* ``can_use_api``   — app-permission middleware (server.py), 403 on undeclared path, and
  403 on an :data:`OWNER_ONLY_API_PATHS` path whatever the app declared. The second half
  exists because ``permissions.api`` is a path-PREFIX allowlist and a prefix says nothing
  about power: ``/api/ws`` prefix-matched ``/api/ws/terminal/{id}``, so declaring the event
  socket also handed the app an owner shell (#2964). See that registry.
* ``can_use_agent`` — the app agent-run endpoints (handlers/apps.py), checked
  against the CALLING app's identity rather than the ``{name}`` path segment, plus
  a per-run ownership check so an app only reads runs it spawned.
* ``can_use_event`` — WS fan-out (state.broadcast_ws) filters an app connection's
  events to its declared set.
* ``can_receive_platform_event`` — the platform event registry (``apps/app_events.emit``)
  fans a core fact out ONLY to apps that named it exactly, and that dispatch is the only
  path such an event reaches an app by, so this is the whole gate. A SEPARATE axis from
  ``can_use_event`` above: a WS grant is not a platform subscription and vice versa.
* ``can_use_mcp_tool`` — the direct tool-invoke endpoint (handlers/tools.py).
* ``can_use_memory`` — app-permission middleware gates any ``/api/memory`` path. ONE
  boolean grant, no tier: the ``"app-scoped"``/``"shared"`` vocabulary was deleted in
  #3501 because ``app-scoped`` granted nothing anywhere and never said so. See that
  method.
* ``can_use_cron``  — app-declared manifest crons are registered only when held
  (apps/app_crons.reconcile_app_crons).
* ``can_use_storage`` — the backend launcher hands the app its DATA_DIR only when
  held (apps/backend_runtime).
* ``can_use_app_messaging`` — the gateway broker (apps/messaging.send_message) refuses
  an undeclared sender→target pair 403 + SEL, and it is the only app-to-app path, so
  this is the whole gate. Enforced, and (APE-12) disclosed at install consent as such.
* ``can_use_desktop`` — the desktop seam (``handlers/desktop.py``) refuses an app
  identity that did not declare the capability, 403 + SEL ``desktop.capability_denied``.
  Apps have no other path to the shell (Electron IPC is renderer-only and the
  gateway mediates every call), so this is the whole gate. Enforced, and disclosed
  at install consent among the enforced bullets.
* ``can_use_network`` — **DECLARATION-ONLY (unenforced by design)**, and the consent
  surface says so rather than implying otherwise (EI-12 D2). There is no per-app
  egress chokepoint to enforce at: an app's provider code is imported **in-process**
  by the gateway (``providers/loader.py``), so its own ``httpx``/``requests`` calls
  are the gateway's egress, and an app with a backend owns a separate OS process with
  its own network stack. Confining either would take an OS-level isolation layer
  (cgroups/nftables/seccomp) or routing all app egress through a guarded seam — an
  architecture change, not a flag. So the flag is DISCLOSURE, and the Store discloses
  it as such: ``PermissionList`` (``web/src/pages/apps/AppsSection.tsx``) renders the
  network claim OUTSIDE the enforced-permission bullets, labelled advisory, and
  renders it whether or not the app declares it — so neither its presence nor its
  absence reads as containment. Every gateway-MEDIATED reach is separately bounded by
  ``can_use_api``. Treat ``network: true`` as an honest declaration, not a boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from personalclaw.apps.manifest import Permissions

logger = logging.getLogger(__name__)


@dataclass
class PermissionChecker:
    """Decides whether an app may reach a given resource, per its declared scope.

    Matching is prefix-based for API paths and exact/prefix for the list scopes,
    mirroring how the manifest declares them (``api`` = allowed path prefixes,
    ``events``/``mcpTools`` = allowed names, with a trailing ``*`` wildcard)."""

    app_name: str
    permissions: Permissions

    # -- API path allowlist ----------------------------------------------
    def can_use_api(self, path: str) -> bool:
        """An app may call an API path only if it matches a declared prefix.

        An app with no declared ``api`` scope can reach NO gateway API (deny by
        default). Its own backend proxy route (``/apps/{name}/api/*``) is always
        allowed — that's the app talking to itself, not the gateway API.

        🔴 An :data:`OWNER_ONLY_API_PATHS` path is refused REGARDLESS of the declaration,
        including ``"*"`` — see that registry for why prefix matching alone could not
        express this.
        """
        if path.startswith(f"/apps/{self.app_name}/api"):
            return True
        if owner_only_api_reason(path):
            return False
        return _matches_any(path, self.permissions.api)

    # -- event subscriptions ---------------------------------------------
    def can_use_event(self, event_type: str) -> bool:
        return _matches_any(event_type, self.permissions.events)

    # -- MCP tools --------------------------------------------------------
    def can_use_mcp_tool(self, tool_name: str) -> bool:
        return _matches_any(tool_name, self.permissions.mcpTools)

    # -- app-to-app messaging (APE-9) ------------------------------------
    def can_use_app_messaging(self, target_app: str) -> bool:
        """Whether this app may send a brokered message TO ``target_app``.

        Deny-by-default: an app that declares no ``appMessaging`` scope can message
        NO app. A declared entry is an exact target name or a trailing-``*`` prefix
        (``_matches_any``), mirroring ``can_use_mcp_tool``. The gateway broker
        (``POST /api/apps/message``) is the only app-to-app path, so this is the sole
        gate on the sender→target edge; an undeclared pair is refused there."""
        return _matches_any(target_app, self.permissions.appMessaging)

    # -- platform event subscriptions (APE-2) ----------------------------
    def can_receive_platform_event(self, event: str) -> bool:
        """Whether the platform registry delivers ``event`` to this app.

        A DIFFERENT axis from :meth:`can_use_event` and it must stay one: ``events`` is
        the gateway's WS event-type allowlist, while ``eventSubscriptions`` is the closed
        set of core-emitted platform facts owned by ``apps/app_events.py``. Declaring one
        grants nothing about the other (``test_event_subscriptions_do_not_widen_the_ws_
        event_allowlist``).

        Deny-by-default and EXACT-match only — like :meth:`can_use_desktop` and unlike
        ``api``/``events``: no prefix, no trailing ``*``. So a subscription to
        ``task.completed`` never matches ``task.completed.extra`` or ``task.*``, and a
        typo denies rather than widens. Enforced at DISPATCH (``app_events.emit``), which
        is the only path a platform event reaches an app by."""
        return bool(event) and event in self.permissions.eventSubscriptions

    # -- consented cross-app read-only storage (APE-10) ------------------
    def can_expose_shared_storage(self) -> bool:
        """Whether THIS app (a would-be SHARER) opts INTO exposing its data dir.

        The sharer half of APE-10's double-declaration: an app must set
        ``storageShared: true`` before any other app that names it in ``storageRead``
        is handed a read-only mount of its data. Deny-by-default (a false flag exposes
        nothing)."""
        return self.permissions.storageShared

    def can_run_background_tasks(self) -> bool:
        """Whether this app may have a long-lived supervised worker (APE-3's host).

        ``manifest.py``'s comment on the flag called it "NOT ENFORCED TODAY, and honestly
        so: nothing in core hosts an app worker yet". That is no longer true — this is the
        accessor the host consults, so the declaration APE-1 disclosed at install consent
        now denies as well as declares.

        Boolean, deny-by-default, and read off the INSTALLED manifest by every caller that
        goes through :func:`checker_for`: the supervisor re-asks at every spawn, so revoking
        the grant in an app update stops the next revival rather than only the first launch.
        """
        return self.permissions.backgroundTasks

    def can_read_shared_storage(self, target_app: str) -> bool:
        """Whether THIS app (the CONSUMER) may read ``target_app``'s data dir read-only.

        Double-declaration, deny-by-default (the file-sharing mirror of
        ``can_use_app_messaging``): the grant holds ONLY when the consumer names
        ``target_app`` in its ``storageRead`` (exact or trailing-``*``, ``_matches_any``)
        AND ``target_app``'s OWN manifest declares ``storageShared: true``. Either half
        missing → no grant, so neither app can create a one-sided share. The read is
        mounted where storage is granted (``backend_runtime``); writes stay broker-only
        (APE-9)."""
        if not target_app:
            return False
        if not _matches_any(target_app, self.permissions.storageRead):
            return False
        target = checker_for(target_app)
        return target is not None and target.can_expose_shared_storage()

    # -- native desktop capabilities (DC-2) ------------------------------
    def can_use_desktop(self, capability: str) -> bool:
        """Whether this app may reach ``capability`` on the desktop shell.

        Deny-by-default and EXACT-match only: unlike ``api``/``events`` there is no
        prefix or ``*`` wildcard here, because the capability vocabulary is closed
        (``dashboard.desktop_registry.CAPABILITIES``) and "everything native this
        host can do" is not a grant a user should be able to click through. An app
        must name each capability it wants, and the Store consent surface names
        them back. The gateway is the only path — apps never reach Electron IPC —
        so ``/api/desktop/*`` consulting this is the whole gate."""
        return bool(capability) and capability in self.permissions.desktop

    # -- coarse capability flags -----------------------------------------
    def can_use_memory(self) -> bool:
        """Whether this app may reach the memory API at all. Deny-by-default.

        ONE grant, no scope argument, deliberately (#3501). ``memory`` used to be a tier
        vocabulary — ``""`` / ``"app-scoped"`` / ``"shared"`` — and this method took the
        scope being asked about, answering True for a declared ``app-scoped`` only when
        asked about ``app-scoped``. The single enforcement call site
        (:func:`app_request_denial`, the ``/api/memory`` gate) asked about ``"shared"``, so
        the narrower declaration — the responsible one, the one an author reading two tiers
        would reasonably pick — failed closed on every path and said nothing.

        That is worse than an ordinary inert control: a permission is rendered to the user
        at install as *consent*, so the user approved a capability that could not happen.
        The tier was deleted rather than implemented because nothing in core partitions
        memory per app — ``memory_record.MemoryScope`` is the reach axis
        (``session|workspace|agent|global``) and has no app dimension for ``app-scoped`` to
        mean anything against — so implementing it would have been new architecture, not a
        wiring fix. With one boolean there is no second answer for the gate to disagree
        with, which is the property the rail in ``tests/test_app_permissions.py`` pins.
        """
        return self.permissions.memory

    def can_use_cron(self) -> bool:
        return self.permissions.cron

    def can_use_network(self) -> bool:
        return self.permissions.network

    def can_use_storage(self) -> bool:
        return self.permissions.storage

    def can_use_agent(self) -> bool:
        """May the app run background agent tasks (headless subagent runs)?"""
        return self.permissions.agent


def _matches_any(value: str, patterns: list[str]) -> bool:
    """Prefix/wildcard match: ``"a/b/*"`` matches any ``"a/b/..."``; an exact
    string matches itself or anything under it as a path prefix."""
    for pat in patterns:
        if pat == "*":
            return True
        if pat.endswith("*"):
            if value.startswith(pat[:-1]):
                return True
        elif value == pat or value.startswith(pat.rstrip("/") + "/"):
            return True
    return False


#: API paths NO app declaration can reach, and the capability each one really is (#2964).
#:
#: **Why a registry and not a matcher tweak.** ``permissions.api`` is a path-PREFIX
#: allowlist, and a prefix is a claim about the URL space, not about power. ``/api/ws`` is
#: a prefix of ``/api/ws/terminal/{session_id}`` — the built-in CLI panel's PTY — so an app
#: declaring the event socket was handed an interactive shell running as the owner, in the
#: owner's ``$HOME``, with the real ``~/.personalclaw`` credential store readable from it.
#: The shipped first-party ``menu-bar-companion`` declares exactly ``["/api/loops",
#: "/api/approvals", "/api/ws"]``, so this was not a third-party hypothetical. Install
#: consent showed the user the string ``/api/ws`` and nothing on that screen said "and a
#: terminal": a consent-integrity defect, not merely an over-broad grant.
#:
#: No adjustment to the matching grammar fixes that. Making prefixes segment-aware does
#: not help (``/api/ws/terminal/x`` IS under the ``/api/ws`` segment); requiring the
#: terminal be named exactly does not help either, because the Store would then render
#: "API: /api/ws/terminal" as one more path string beside ``/api/loops`` — a bullet that
#: silently means "and everything you can do", which is not a grant a user can evaluate.
#:
#: **So the rule is a CLASS rule, stated once here.** A path belongs in this registry when
#: holding it makes every other permission in the manifest moot — arbitrary code as the
#: owner, the owner's credentials, the owner's authentication material, the integrity of
#: the audit trail, or the gateway's own lifecycle. There is nothing to scope: an app with
#: a shell does not have "some" access. Deny-by-construction, not deny-by-default.
#:
#: **The way OUT** (a refusal with no escape becomes the outage). None of these removes a
#: capability an app can legitimately need:
#:
#: * Run commands → an app ships a BACKEND (its own OS process, ``app.json``) or declares
#:   ``permissions.desktop`` capabilities; both are separately disclosed at install.
#: * Reach a secret → the app's own config/credential surface, not the owner's vault.
#: * The owner keeps every one of these paths at full strength from the dashboard; the
#:   refusal is scoped to requests carrying an app identity.
#:
#: The refusal names the capability, so a developer reads why rather than "403".
#:
#: Three of these were ALREADY refused inside their handlers
#: (``security_credentials._refuse_app``, ``secrets._refuse_app``,
#: ``security_audit._refuse_app``, ``computer_use``) — that idiom, copied four times, is
#: the evidence the class is real and the reason it now lives in ONE place that the
#: middleware consults for every route. Those handler checks stay as defence in depth at
#: the seam; this registry is what makes a route that forgot to write one still safe.
OWNER_ONLY_API_PATHS: dict[str, str] = {
    # #2964's measured hole: `GET /api/ws/terminal/{id}` + an app token returned 101 and
    # ran `whoami` as the owner. No handler check existed — `api_terminal_ws` authorizes on
    # `request.get("user")` plus the feature flag, both of which an app-scoped request
    # satisfies.
    "/api/ws/terminal": "an interactive shell running as you",
    # The PTY's other half: `POST /api/terminal/sessions` chooses the `cwd` the next WS
    # spawn opens in, and the list/delete routes enumerate and kill the owner's own
    # sessions. Same class as the socket, and the same prefix shape would have reached it
    # from a declared `/api/terminal`.
    "/api/terminal": "your terminal sessions",
    # Synthetic keyboard/mouse into the owner's session — shell-equivalent by another road.
    "/api/computer-use": "control of your keyboard, mouse and screen",
    # The credential store and the secrets vault.
    "/api/security/credentials": "your stored provider credentials",
    "/api/secrets": "your secrets vault",
    # The signed audit trail: reading it is the owner's, and rotating it destroys evidence.
    "/api/security/audit": "your security audit log",
    "/api/sel/rotate": "rotation of your security event log",
    # The owner's own authentication material: password set, TOTP enrollment, session.
    # Segment-aware on purpose — `/api/auth-status` is a DIFFERENT path and stays reachable.
    "/api/auth": "your login password and second factor",
    # Gateway lifecycle. An app that can restart the gateway can deny service to every
    # other app and to the owner.
    "/api/system/restart": "restarting the gateway",
    # Mints a gateway token from the loopback secret — the bootstrap identity itself.
    "/api/token/local": "a gateway token minted as you",
}


def owner_only_api_reason(path: str) -> str:
    """The capability *path* really grants when it is owner-only, else ``""``.

    Matching is SEGMENT-aware, which is load-bearing in both directions: ``/api/auth``
    must cover ``/api/auth/password`` (the escalated child) and must NOT cover
    ``/api/auth-status`` (an unrelated sibling that a raw string prefix would have
    swallowed). The registry is the reason this cannot be folded into ``_matches_any``:
    that function answers "did the app ask for this", and this one answers "is this
    askable at all".
    """
    if not path:
        return ""
    for root, capability in OWNER_ONLY_API_PATHS.items():
        if path == root or path.startswith(root + "/"):
            return capability
    return ""


def checker_for(app_name: str) -> PermissionChecker | None:
    """Build a :class:`PermissionChecker` for an installed app, or ``None`` if the
    app/manifest can't be resolved.

    ⚠️ ``None`` means "this app could not be authorized", NOT "there is no app
    identity". Those read the same at a call site guarded by ``if checker is not
    None`` and they are opposites: the second means an owner request that needs no
    scoping, the first means an app-scoped request that must be REFUSED. Route
    request authorization through :func:`app_request_denial`, which fails closed,
    rather than deciding it from this return value.
    """
    if not app_name:
        return None
    try:
        from personalclaw.apps.app_manager import _manifest_of

        manifest = _manifest_of(app_name)
    except Exception:
        logger.debug("permission checker: manifest load failed for %s", app_name, exc_info=True)
        return None
    if manifest is None:
        return None
    return PermissionChecker(app_name=app_name, permissions=manifest.permissions)


#: Path prefixes the gateway scopes by app identity. ``/api/`` is the API surface and
#: ``/apps/`` is an app's own UI/proxy surface; anything else is not app-scoped.
APP_SCOPED_PREFIXES: tuple[str, ...] = ("/api/", "/apps/")


def app_lifecycle_denial(app_name: str) -> str:
    """Why this app may not act AT ALL, or ``""`` when it is installed and enabled.

    Separate from the capability checks because it answers a different question: not
    "may this app do this" but "is this app a thing that may do anything". Every
    capability grant is downstream of it, and it is defined once here so a second
    enforcement point cannot decide "active" differently.

    ``installed.json`` is the record ``enable``/``disable`` write, and ``app.json`` —
    which is what :func:`checker_for` reads — is untouched by either. So a permission
    check alone cannot see a disabled app, which is exactly how a live token kept
    working after the owner flipped the switch off.
    """
    from personalclaw.apps.manager import _read_installed

    meta = _read_installed(app_name)
    if meta is None:
        return "app is not installed"
    if not meta.enabled:
        return "app is disabled"
    return ""


def app_request_denial(app_name: str, path: str) -> str:
    """Why an app-scoped request must be refused, or ``""`` to allow it.

    **The one authorization decision for a request carrying an app identity**, so the
    gateway middleware and its tests exercise the same code. It previously lived inline
    in ``server.py``'s ``app_permission_middleware`` closure, which cannot be imported —
    so the two test files that cover it each re-implemented it as a mirror, and a mirror
    is free to drift from the boundary it claims to test.

    🔴 FAILS CLOSED, which is the fix. Every check inline was written as
    ``if checker is not None and not checker.can_use_...``, so an app whose manifest
    could not be resolved skipped ALL of them and reached any path at all. Measured
    against the shipped predicate, an app-scoped token for a name with no readable
    manifest was allowed ``/api/apps/<other>/agent-run``, ``/api/memory/all`` AND
    ``/api/security/credentials`` — strictly more reach than the app's own declared
    allowlist. Two ordinary situations produce it:

    * **The owner uninstalls the app.** The directory goes, so ``_manifest_of`` returns
      ``None``. App tokens live an hour (``_APP_TOKEN_TTL_SECS``) and carry the claim in
      the token itself, so there is nothing to revoke — meaning the owner's remediation
      handed a misbehaving app MORE access than it had before, for up to an hour.
    * **The manifest stops parsing.** An app that can write its own install directory
      escapes its sandbox by corrupting its own ``app.json``.

    Disabling had the mirror-image problem: ``_manifest_of`` reads ``app.json`` from
    disk and never consults ``installed.json``, so a disabled app's checker granted
    everything it always had. Minting a token already refuses a disabled app
    (``api_app_token``), which only covers tokens minted AFTER the flip — the live one
    kept working. Checking per request is what makes "disable" mean disable now.

    Ordering is deliberate: lifecycle (installed → enabled → readable) before capability,
    so the reason names the actual problem rather than "path not declared" for an app
    that is simply gone.
    """
    if not app_name:
        return ""  # No app identity — an owner/dashboard request, scoped by nothing here.
    lifecycle = app_lifecycle_denial(app_name)
    if lifecycle:
        return lifecycle
    checker = checker_for(app_name)
    if checker is None:
        return "app manifest could not be read"
    # #2964. Named BEFORE the allowlist so the reason is the true one. "api path not in
    # declared permissions" would be a lie for an app that declared `/api/ws` and was
    # refused the terminal under it: the path IS in its permissions by the prefix grammar,
    # and what refused it is that the capability is not app-grantable at all. A developer
    # who reads the wrong reason adds `/api/ws/terminal` to the manifest and files a bug.
    owner_only = owner_only_api_reason(path)
    if owner_only:
        return f"owner-only capability, not grantable to an app: {owner_only}"
    if not checker.can_use_api(path):
        return "api path not in declared permissions"
    # A memory API path additionally requires the ``memory`` capability (sandbox P3) —
    # declaring the /api/memory path in permissions.api is necessary but not sufficient.
    if path.startswith("/api/memory") and not checker.can_use_memory():
        return "memory access not declared (permissions.memory)"
    return ""
