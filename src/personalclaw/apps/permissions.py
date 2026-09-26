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
* ``can_use_config_field`` — ``/api/config/personalclaw`` (handlers/core.py): an app reads,
  and writes, only the settings its manifest names in ``permissions.config``.
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

import contextlib
import contextvars
import logging
from collections.abc import Iterator
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

    # -- the owner's settings --------------------------------------------
    def can_use_config_field(self, field: str) -> bool:
        """Whether the app may read and write the config field *field* (``voice.echo_filter_
        enabled``) through ``/api/config``. EXACT match, deny-by-default: the path grant
        ``/api/config`` reaches the route, and this list says which settings it reaches —
        the ones install consent named. A security setting can never be on it
        (``AppManifest.validate`` refuses the declaration), and the PATCH refuses one first
        anyway (``config/edit_spec.app_write_refusal``)."""
        return bool(field) and field in self.permissions.config

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
#: **The owner's security posture is in that class**, and it is the same argument: an app that
#: can switch auto-approve-everything on, trust a folder's scripts, mint itself a sign-in or an
#: inbound credential, or resume unattended work the owner stopped has made its own manifest
#: moot. The posture held in ``config.json`` is refused FIELD by field instead
#: (``config/edit_spec.app_write_refusal``), because ``/api/config`` also carries ordinary
#: settings an app may legitimately write; the routes below exist only to change the posture,
#: so the whole route is the owner's.
#:
#: **So is code that runs as the owner, and the owner's own access.** Defining an MCP server
#: is naming a command the gateway launches; restoring a backup rewrites every setting at once;
#: revoking a device, a paired sender or an autonomy grant takes something from the owner that
#: an app has no documented reason to take. An app ships its code in its MANIFEST, where install
#: consent shows it (``mcpServers``, ``backend``, ``setup``), and stops unattended work with
#: ``POST /api/incident`` — the one lever an app keeps. Routes that mix the owner's and an app's
#: business are declared per route in :data:`ROUTE_AUTHZ` instead.
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
    # ── The owner's security posture (see the class note above) ──
    # `{"mode": "yolo"}` auto-approves every tool call in every session; `trust` does it for
    # one. Reached with an ordinary `/api/chat` declaration before this row existed.
    "/api/chat/mode": "the approval mode for your chats, including auto-approve-everything",
    # The incident stop suspends every unattended run; resuming is the owner's decision. The
    # STOP (`POST /api/incident`) stays grantable — it is the one lever an app should have.
    "/api/incident/resume": "resuming unattended work after you stopped it",
    # `trusted: true` lets a folder run and write its own project scripts.
    "/api/guardrails/project-trust": "which folders may run their own scripts",
    # A promotion lets an action type run without asking.
    "/api/autonomy/grant": "letting an action type run without asking you",
    # Demote and undo only reduce autonomy, which is why they need no confirmation from the
    # owner — and why an app holding them is a denial of service: it can hand back every
    # grant the owner made (restarting each cooldown) or reverse actions the owner kept.
    "/api/autonomy/demote": "taking back the autonomy you granted an action type",
    "/api/autonomy/undo": "reversing an action your automation already took",
    # A taught `approve` rule auto-executes matching actions on your behalf.
    "/api/memory/approval-rules": "standing approve and deny rules for actions taken for you",
    # A pairing code redeems for a durable DEVICE session — the owner's sign-in, not an app's.
    # An app backend passes `check_origin` (loopback, no Origin), so it could mint one and redeem
    # it at the session-less `pair/complete`, leaving its sandbox entirely. `pair/complete` stays
    # declarable: it redeems a code the owner minted.
    "/api/devices/pair/start": "a new device signed in as you",
    # Each client is an inbound credential with a scope its creator chooses.
    "/api/external-access/clients": "credentials for reaching this gateway from outside",
    # The ACP agent's runtime config: `allowedTools` runs without asking, `mcpServers` are
    # commands it launches.
    "/api/agent/config": "the tools your agent runs without asking, and the servers it launches",
    # ── Code that runs as you (see the class note above) ──
    # Every MCP route, reads included. A write names a command the gateway spawns
    # (`PUT /api/mcp/servers/{name}`), enables one the owner switched off, or copies one out of
    # `~/.claude.json` (`/api/mcp/apply`); a read returns each server's arguments and, for a
    # remote server, its headers — where bearer tokens live. An app declares its own servers in
    # its manifest (`mcpServers`), registered at install and shown on the consent screen.
    "/api/mcp": (
        "the MCP servers this gateway launches — the commands it runs as you, and the "
        "credentials in their arguments and headers"
    ),
    # Export copies the whole home out; import and restore write every setting, automation
    # and MCP server back at once, which is every other row in this registry in one request.
    "/api/durability": "your backups — exporting your home, and restoring or importing one over it",
    # ── Your access ──
    # The list of people who may message the agent from a chat channel (sender ids are PII),
    # and revoking one — which cuts the owner off from their own agent on that channel.
    "/api/channels/trust": "who may message your agent from a chat channel, and revoking them",
}


@dataclass(frozen=True)
class OwnerOnly:
    """A route no app declaration reaches, and the capability it really grants (shown in the
    refusal, so a developer reads a policy rather than a bug)."""

    capability: str


@dataclass(frozen=True)
class AppMay:
    """A route an app reaches when it declared the path in ``permissions.api``, and why that is
    safe — what the handler screens, or why the route grants nothing."""

    reason: str


#: The verbs that write. A read under a security family is governed by the ordinary allowlist.
WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The route families whose WRITES decide what runs as the owner, whether it asks first, or who
#: may reach the owner — and the category each belongs to. Every write route under one of these
#: roots must be covered by :data:`OWNER_ONLY_API_PATHS` or declared in :data:`ROUTE_AUTHZ`
#: (``tests/test_security_posture_rail.py``), and one that is neither is refused to an app at
#: runtime (:func:`undeclared_security_write`) — so a route added tomorrow fails closed until
#: someone decides which it is.
SECURITY_ROUTE_FAMILIES: dict[str, str] = {
    "/api/mcp": "MCP servers — commands the gateway launches",
    "/api/apps": "installing and switching on app code",
    "/api/packs": "installing packs and deploying what they stage",
    "/api/triggers": "automations — what they run, and whether their agents ask you",
    "/api/workflows": "workflows — what their steps run, and whether their agents ask you",
    "/api/loops": "autonomous loops, whose workers approve their own tool calls",
    "/api/spawn": "background agents started on request",
    "/api/agents": "agents and their approval mode",
    "/api/agent": "the ACP agent's runtime config",
    "/api/config": "your settings",
    "/api/autonomy": "earned autonomy",
    "/api/incident": "the incident stop",
    "/api/guardrails": "guardrails",
    "/api/devices": "the devices signed in as you",
    "/api/channels": "chat channels and who may reach your agent through them",
    "/api/external-access": "credentials for reaching this gateway from outside",
    "/api/durability": "backups and restores",
}

_INSTALLS_APP = "installing an app — its backend, MCP servers and setup hooks run as you"
_APP_SOURCES = "where the Store installs apps from"
_INSTALLS_PACK = "installing a pack — the skills, agents and automations it brings"
#: Starting or steering work an app did not write. The work may run a command the owner defined
#: or an agent that approves its own tool calls, and an app's route to agent work is its own
#: `agent` permission (`POST /api/apps/{name}/agent-run`), which install consent names.
_STARTS_AGENT_WORK = (
    "starting or steering agent work — an app runs agents through its own `agent` permission"
)
_FIRES_AUTOMATION = (
    "firing an automation by hand, including one you switched off, one that runs a command, "
    "and one whose agent approves its own tool calls"
)
_STARTS_LOOP = "starting or steering an autonomous loop — its worker approves its own tool calls"
_STOPS_WORK = "stops work — stopping is never an escalation, and resuming is yours"
_GRANTS_NOTHING = "removes an agent; it grants nothing"
#: Defining an automation, not screening one: a step can run a shell command, start one of your
#: workflows, or prompt an agent that approves its own tool calls, and a step screen would have
#: to keep up with every provider. An app's scheduled work is the `crons` its manifest declares,
#: which install consent lists (`apps/app_crons.py`).
_DEFINES_AUTOMATION = (
    "defining an automation — its steps can run commands and agents that approve their own "
    "tool calls; an app schedules work with the crons its manifest declares"
)
_SCREENED_AGENT = (
    "screened: an app never writes an agent's approval mode (`agents._app_security_refusal`)"
)
_SCREENED_CONFIG = (
    "screened: an app reads and writes only the settings its manifest declares in "
    "`permissions.config`, and never a security setting"
)

#: Per-route authorization for the WRITE routes in :data:`SECURITY_ROUTE_FAMILIES` that no
#: :data:`OWNER_ONLY_API_PATHS` subtree covers — keyed ``"METHOD /canonical/{route}"``, the
#: form aiohttp reports as ``request.match_info.route.resource.canonical``. A per-route table,
#: not more subtree rows, because these families mix the owner's business with an app's:
#: ``POST /api/triggers`` is an app's to call (screened), ``POST /api/triggers/{id}/run`` is
#: not, and a path prefix cannot tell them apart.
ROUTE_AUTHZ: dict[str, OwnerOnly | AppMay] = {
    # ── apps ──
    "POST /api/apps": OwnerOnly(_INSTALLS_APP),
    # Installs nothing, but it is the first half of an install: it clones the git URL it is
    # given, or reads the local folder it is given, and answers with what it found there.
    "POST /api/apps/preview": OwnerOnly(
        "reviewing an install — it clones any git URL, or reads any folder, named as the source"
    ),
    "POST /api/apps/sources": OwnerOnly(_APP_SOURCES),
    "DELETE /api/apps/sources": OwnerOnly(_APP_SOURCES),
    "POST /api/apps/local-sources": OwnerOnly(_APP_SOURCES),
    "DELETE /api/apps/local-sources": OwnerOnly(_APP_SOURCES),
    "POST /api/apps/{name}/enable": OwnerOnly(
        "switching an app on — its backend and MCP servers start, as you"
    ),
    "POST /api/apps/{name}/disable": OwnerOnly("switching your apps off"),
    "POST /api/apps/{name}/update": OwnerOnly("replacing an app's code"),
    "DELETE /api/apps/{name}": OwnerOnly("uninstalling your apps"),
    "POST /api/apps/{name}/token": OwnerOnly("minting an app's token — only you mint one"),
    "POST /api/apps/{name}/agent-run": AppMay(
        "runs an agent under the CALLING app's own `agent` permission, which install consent "
        "names — `_agent_run_identity` gates on the token's app, never the path"
    ),
    "PUT /api/apps/{name}/config": AppMay(
        "an app writes only its own settings — the handler refuses a path naming another app"
    ),
    "POST /api/apps/message": AppMay(
        "the app-to-app broker — the target must be in the sender's `appMessaging`"
    ),
    # ── packs ──
    "POST /api/packs/bundled/{name}/install": OwnerOnly(_INSTALLS_PACK),
    "POST /api/packs/one-link": OwnerOnly(_INSTALLS_PACK),
    "POST /api/packs/{name}/update": OwnerOnly(_INSTALLS_PACK),
    "POST /api/packs/{name}/roster/deploy": OwnerOnly("deploying a pack's agents"),
    # A pack stages its automations switched off precisely so that the owner arms them.
    "POST /api/packs/{name}/triggers/deploy": OwnerOnly("deploying a pack's automations"),
    "POST /api/packs/{name}/bindings": OwnerOnly("which of your folders a pack reads"),
    "POST /api/packs/prompt-card": AppMay(
        "turns a pasted card into a proposal for you to review; it writes nothing else"
    ),
    "POST /api/packs/proposals/reject": AppMay(
        "declines a pending proposal; declining grants nothing"
    ),
    "POST /api/packs/{name}/finish-setup": AppMay(
        "returns a pack's setup interview to open in chat; it runs nothing"
    ),
    # ── triggers ──
    "POST /api/triggers": OwnerOnly(_DEFINES_AUTOMATION),
    "PUT /api/triggers/{id}": OwnerOnly(_DEFINES_AUTOMATION),
    "DELETE /api/triggers/{id}": OwnerOnly("deleting your automations"),
    # Arming is the owner's: a pack stages its automations switched off so that only you arm them.
    "POST /api/triggers/{id}/toggle": OwnerOnly("switching your automations on and off"),
    "POST /api/triggers/{id}/run": OwnerOnly(_FIRES_AUTOMATION),
    "POST /api/triggers/{id}/test": OwnerOnly(_FIRES_AUTOMATION),
    "POST /api/triggers/view/render": OwnerOnly(_FIRES_AUTOMATION),
    "POST /api/triggers/{id}/fire": AppMay(
        "the external webhook fire — admitted only by a client token you minted and scoped to "
        "this trigger; an app's identity adds nothing"
    ),
    "POST /api/triggers/{id}/to-chat": AppMay(
        "opens a chat holding a schedule's last result; it starts no agent turn"
    ),
    # ── workflows ──
    "POST /api/workflows": OwnerOnly(_DEFINES_AUTOMATION),
    "DELETE /api/workflows/{name}": OwnerOnly("deleting your workflows"),
    "POST /api/workflows/{name}/a2a-publish": OwnerOnly(
        "publishing a workflow to outside A2A clients — a new way into this gateway"
    ),
    "POST /api/workflows/{name}/refine": OwnerOnly(_STARTS_AGENT_WORK),
    "POST /api/workflows/{name}/versions/repin": OwnerOnly(
        "which version of a workflow runs — an older one can carry steps you removed"
    ),
    "POST /api/workflows/runs": OwnerOnly(_STARTS_AGENT_WORK),
    "POST /api/workflows/runs/{run_id}/start": OwnerOnly(_STARTS_AGENT_WORK),
    "POST /api/workflows/runs/{run_id}/steer": OwnerOnly(_STARTS_AGENT_WORK),
    "POST /api/workflows/runs/{run_id}/rewind": OwnerOnly(_STARTS_AGENT_WORK),
    "POST /api/workflows/runs/{run_id}/run-from": OwnerOnly(_STARTS_AGENT_WORK),
    "POST /api/workflows/runs/{run_id}/review/triage": OwnerOnly(_STARTS_AGENT_WORK),
    "POST /api/workflows/runs/{run_id}/resume": OwnerOnly("resuming a run you paused"),
    "POST /api/workflows/runs/{run_id}/edit": OwnerOnly("rewriting a run's steps"),
    "PUT /api/workflows/runs/{run_id}/policy-overrides": OwnerOnly(
        "how far an unattended run may go before it stops, and whether it waits for you"
    ),
    "POST /api/workflows/runs/{run_id}/fork": AppMay(
        "copies a run into a draft; nothing runs until you start it"
    ),
    "POST /api/workflows/runs/{run_id}/cancel": AppMay(_STOPS_WORK),
    "POST /api/workflows/runs/{run_id}/pause": AppMay(_STOPS_WORK),
    "DELETE /api/workflows/runs/{run_id}": OwnerOnly("deleting your runs and what they produced"),
    "POST /api/workflows/runs/{run_id}/confirm": AppMay(
        "answers one pending confirmation once — the relay a companion needs, like a one-off "
        "answer to a chat approval"
    ),
    "POST /api/workflows/runs/{run_id}/drop": AppMay(
        "adds a file to the run's approval-gated drop; the run reads it only after you approve"
    ),
    # ── loops (every worker session is trusted: `loop/manager.py` sets its approval to auto) ──
    "POST /api/loops": OwnerOnly(_STARTS_LOOP),
    "PUT /api/loops/{id}": OwnerOnly(_STARTS_LOOP),
    # One route starts, resumes, pauses and stops; an app stops unattended work with the incident.
    "PATCH /api/loops/{id}": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/autopilot": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/grill-tree": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/nudge": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/plan/approve": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/plan/comment": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/plan/edit": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/plan/retry": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/plan/start": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/{id}/queue": OwnerOnly(_STARTS_LOOP),
    "POST /api/loops/classify": AppMay("analyses a draft goal; it creates and starts nothing"),
    "POST /api/loops/validate": AppMay("checks a draft; it creates and starts nothing"),
    "DELETE /api/loops/{id}": OwnerOnly("deleting your loops"),
    # ── spawn ──
    "POST /api/spawn": OwnerOnly(_STARTS_AGENT_WORK),
    "DELETE /api/spawn": AppMay(_STOPS_WORK),
    "POST /api/spawn/cancel-fanout": AppMay(_STOPS_WORK),
    "DELETE /api/spawn/{agent_id}": AppMay(_STOPS_WORK),
    # ── agents ──
    "POST /api/agents": AppMay(_SCREENED_AGENT),
    "PUT /api/agents/{name}": AppMay(_SCREENED_AGENT),
    "PATCH /api/agents/detail/{name}": AppMay(_SCREENED_AGENT),
    "POST /api/agents/sync": AppMay(
        "screened: an app's sync refuses to fold in an agent file that sets an approval mode"
    ),
    "DELETE /api/agents/{name}": AppMay(_GRANTS_NOTHING),
    "DELETE /api/agents/detail/{name}": AppMay(_GRANTS_NOTHING),
    "POST /api/agents/routing/dismiss": AppMay("dismisses a routing suggestion"),
    "POST /api/agents/routing/unmute": AppMay("brings routing suggestions back"),
    # ── config ──
    "PATCH /api/config/personalclaw": AppMay(_SCREENED_CONFIG),
    "PUT /api/config/personalclaw": AppMay(_SCREENED_CONFIG),
    # The agent every new chat runs, instructions and all.
    "PUT /api/config/default-agent": OwnerOnly("which agent your chats run by default"),
    # ── incident ──
    "POST /api/incident": AppMay(
        "the stop — halting unattended work is the one lever an app keeps; resuming is yours"
    ),
    # ── devices ──
    "POST /api/devices/pair/complete": AppMay(
        "redeems a pairing code you minted (`browser-connector` pairs through it); minting "
        "one is yours"
    ),
    "POST /api/devices/{id}/revoke": OwnerOnly("signing out your devices"),
    # ── channels ──
    "POST /api/channels/{name}/connect": OwnerOnly(
        "connecting a chat channel — the people on it can then reach your agent, as its trust "
        "policy allows"
    ),
    "POST /api/channels/{name}/disconnect": OwnerOnly("disconnecting your chat channels"),
    "POST /api/channels/{name}/test": AppMay("probes a channel you connected; it changes nothing"),
}


def owner_only_api_reason(path: str, *, method: str = "", route: str = "") -> str:
    """The capability *path* really grants when it is owner-only, else ``""``.

    Matching is SEGMENT-aware, which is load-bearing in both directions: ``/api/auth``
    must cover ``/api/auth/password`` (the escalated child) and must NOT cover
    ``/api/auth-status`` (an unrelated sibling that a raw string prefix would have
    swallowed). The registry is the reason this cannot be folded into ``_matches_any``:
    that function answers "did the app ask for this", and this one answers "is this
    askable at all".

    *method* and *route* (the matched route's canonical template) also consult the per-route
    half, :data:`ROUTE_AUTHZ`. A caller with no route in hand — manifest validation, which
    judges a declared PATH — gets the subtree half only, which is the half that can make a
    declaration dishonest: a per-route row leaves the path's other methods reachable.
    """
    if not path:
        return ""
    for root, capability in OWNER_ONLY_API_PATHS.items():
        if path == root or path.startswith(root + "/"):
            return capability
    if method and route:
        authz = ROUTE_AUTHZ.get(f"{method.upper()} {route}")
        if isinstance(authz, OwnerOnly):
            return authz.capability
    return ""


def security_family(route: str) -> str:
    """The :data:`SECURITY_ROUTE_FAMILIES` root *route* sits under, or ``""``."""
    for root in SECURITY_ROUTE_FAMILIES:
        if route == root or route.startswith(root + "/"):
            return root
    return ""


def undeclared_security_write(method: str, route: str) -> str:
    """Why an app is refused a WRITE route in a security family that declares nothing, or ``""``.

    The fail-closed half of the rail: a route added under ``/api/triggers`` tomorrow is refused
    to every app until someone declares, in :data:`ROUTE_AUTHZ`, whether an app may reach it.
    Covered routes — a subtree row or a declaration of either kind — answer ``""`` here; the
    owner-only verdict itself comes from :func:`owner_only_api_reason`.
    """
    if method.upper() not in WRITE_METHODS or not route:
        return ""
    family = security_family(route)
    if not family or owner_only_api_reason(route):
        return ""
    if f"{method.upper()} {route}" in ROUTE_AUTHZ:
        return ""
    return (
        f"{method.upper()} {route} is a write under {family} ({SECURITY_ROUTE_FAMILIES[family]}) "
        "that has not declared whether an app may reach it"
    )


#: The app a request is scoped to, for the seams that decide reach without a request in hand.
#: Set by the gateway's app-permission middleware around the handler (:func:`scoped_to_app`) and
#: read by :func:`request_app` — today by the file explorer's root list, which must not hand an
#: app the PersonalClaw home (``dashboard/handlers/files._dashboard_roots``). ``asyncio.to_thread``
#: copies it, so a handler's offloaded work sees the same identity.
_REQUEST_APP: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_request_app", default=""
)


@contextlib.contextmanager
def scoped_to_app(app_name: str) -> Iterator[None]:
    """Mark the current request as the app *app_name*'s for everything it calls."""
    token = _REQUEST_APP.set(app_name or "")
    try:
        yield
    finally:
        _REQUEST_APP.reset(token)


def request_app() -> str:
    """The app the current request is scoped to, or ``""`` for the owner (and outside a request)."""
    return _REQUEST_APP.get()


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


def app_request_denial(app_name: str, path: str, *, method: str = "", route: str = "") -> str:
    """Why an app-scoped request must be refused, or ``""`` to allow it.

    *method* and *route* (the matched route's canonical template) are what the per-route
    declarations are keyed on; the gateway middleware always passes both.

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
    owner_only = owner_only_api_reason(path, method=method, route=route)
    if owner_only:
        return f"owner-only capability, not grantable to an app: {owner_only}"
    undeclared = undeclared_security_write(method, route)
    if undeclared:
        return undeclared
    if not checker.can_use_api(path):
        return "api path not in declared permissions"
    # A memory API path additionally requires the ``memory`` capability (sandbox P3) —
    # declaring the /api/memory path in permissions.api is necessary but not sufficient.
    if path.startswith("/api/memory") and not checker.can_use_memory():
        return "memory access not declared (permissions.memory)"
    return ""
