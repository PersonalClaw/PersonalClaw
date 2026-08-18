"""App manifest — static metadata for PersonalClaw apps.

An app manifest (``app.json``) declares an app's identity, resources, and
requirements without executing any app code.  PersonalClaw reads it during
install to register agents, skills, crons, UI pages, and backend config.

Design follows the same pattern as :class:`backend.plugins.manifest.PluginManifest`
(dataclass + ``from_dict`` / ``to_dict`` / ``validate`` / round-trip) but with
app-specific fields.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Nested manifest types
# ---------------------------------------------------------------------------

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([+-]|$)")
# A backend-route ``op`` id (§4.2) — snake_case identifier; it becomes the tool
# suffix ``app_<name>_<op>``, so keep it to a clean identifier shape.
ROUTE_OP_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def version_tuple(v: str) -> tuple[int, ...]:
    """Parse an app semver string to a numeric tuple for comparison (best-effort).

    Manifests are validated against ``SEMVER_RE`` (``MAJOR.MINOR.PATCH`` with an optional
    ``+build``/``-pre`` suffix), so the core is the dotted release. The pre-release/build
    suffix is dropped (SemVer pre-release ordering is out of scope for "is a newer release
    available"), and a leading ``v`` is tolerated. A value that can't be parsed sorts as
    ``(0,)`` so a malformed version never falsely reads as an available update.

    This is the ONE app-version comparator (``apps.catalog`` reuses it) — the version
    field and ``SEMVER_RE`` both live here, so the comparator does too, rather than
    inverting the apps→dashboard layering to borrow the self-updater's tag comparator.
    """
    core = (v or "").strip()
    core = core[1:] if core[:1] == "v" else core
    core = core.split("+", 1)[0].split("-", 1)[0]
    try:
        return tuple(int(x) for x in core.split("."))
    except (ValueError, AttributeError):
        return (0,)


@dataclass
class CronEntry:
    """A scheduled agent job declared by an app."""

    name: str = ""
    every: int = 0  # seconds between runs (0 = use cron_expr)
    cron_expr: str = ""  # cron expression (alternative to every)
    agent: str = ""  # agent name to run
    message: str = ""  # prompt message for the agent
    # Extended fields for advanced scheduling
    agent_sequence: list[str] = field(default_factory=list)  # ordered list of agents to run
    env: dict[str, str] = field(default_factory=dict)  # environment variables for the job
    persistent_session: bool = True  # whether to carry context between runs
    silent: bool = False  # suppress dashboard notifications

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.every:
            d["every"] = self.every
        if self.cron_expr:
            d["cron_expr"] = self.cron_expr
        if self.agent:
            d["agent"] = self.agent
        if self.message:
            d["message"] = self.message
        if self.agent_sequence:
            d["agent_sequence"] = self.agent_sequence
        if self.env:
            d["env"] = self.env
        if not self.persistent_session:
            d["persistent_session"] = False
        if self.silent:
            d["silent"] = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronEntry":
        return cls(
            name=str(data.get("name", "")),
            every=int(data.get("every", 0)),
            cron_expr=str(data.get("cron_expr", "")),
            agent=str(data.get("agent", "")),
            message=str(data.get("message", "")),
            agent_sequence=[str(a) for a in data.get("agent_sequence", [])],
            env={str(k): str(v) for k, v in data.get("env", {}).items()},
            persistent_session=bool(data.get("persistent_session", True)),
            silent=bool(data.get("silent", False)),
        )


@dataclass
class UIPage:
    """A frontend page contributed by an app."""

    route: str = ""  # URL path, e.g. /apps/note-keeper
    label: str = ""  # sidebar display text
    icon: str = ""  # lucide icon name or emoji
    iconUrl: str = ""  # custom icon image path relative to ui/ dir  # noqa: N815
    entryPoint: str = ""  # path to JS bundle relative to app root  # noqa: N815
    mountFunction: str = "mount"  # exported function name  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "route": self.route,
            "label": self.label,
        }
        if self.icon:
            d["icon"] = self.icon
        if self.iconUrl:
            d["iconUrl"] = self.iconUrl
        if self.entryPoint:
            d["entryPoint"] = self.entryPoint
        if self.mountFunction != "mount":
            d["mountFunction"] = self.mountFunction
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UIPage":
        return cls(
            route=str(data.get("route", "")),
            label=str(data.get("label", "")),
            icon=str(data.get("icon", "")),
            iconUrl=str(data.get("iconUrl", "")),  # noqa: N815
            entryPoint=str(data.get("entryPoint", "")),  # noqa: N815
            mountFunction=str(data.get("mountFunction", "mount")),  # noqa: N815
        )


@dataclass
class UISidebar:
    """Sidebar placement config for app pages."""

    section: str = "Apps"
    order: int = 10

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.section != "Apps":
            d["section"] = self.section
        if self.order != 10:
            d["order"] = self.order
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UISidebar":
        return cls(
            section=str(data.get("section", "Apps")),
            order=int(data.get("order", 10)),
        )


@dataclass
class UIConfig:
    """Frontend configuration for an app."""

    entry: str = ""  # ESM bundle path relative to app root, e.g. "dist/index.mjs"
    pages: list[UIPage] = field(default_factory=list)
    sidebar: UISidebar = field(default_factory=UISidebar)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.entry:
            d["entry"] = self.entry
        if self.pages:
            d["pages"] = [p.to_dict() for p in self.pages]
        sidebar_d = self.sidebar.to_dict()
        if sidebar_d:
            d["sidebar"] = sidebar_d
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UIConfig":
        pages = [UIPage.from_dict(p) for p in data.get("pages", []) if isinstance(p, dict)]
        sidebar_raw = data.get("sidebar", {})
        sidebar = UISidebar.from_dict(sidebar_raw) if isinstance(sidebar_raw, dict) else UISidebar()
        return cls(entry=str(data.get("entry", "")), pages=pages, sidebar=sidebar)


@dataclass
class AppSkill:
    """One SKILL.md skill directory an app ships and OWNS (§4.1).

    Declared as ``{path: "skills/my-skill/"}`` (dir path relative to the app root,
    containing a ``SKILL.md``). On enable / startup discovery the dir is installed
    into the user skills tree through the supply-chain chokepoint
    (:meth:`SkillsRegistry.install_scanned` → quarantine → ``scan_dir`` at the app's
    trust tier → ``.pclaw-lock.json``) — an app skill never bypasses the gate just
    because it arrived inside an app. Idempotent + non-clobbering; removed on
    disable/uninstall keyed by the app's own declaration. See apps.skill_seed.
    """

    path: str = ""  # dir path relative to the app root, e.g. "skills/deploy-site/"

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSkill":
        return cls(path=str(data.get("path", "")))


@dataclass
class RouteEntry:
    """One agent-callable backend route an app declares in its manifest.

    Declared statically in ``backend.routes[]`` so the agent-callable surface is
    readable WITHOUT executing app code (the manifest module's design rule). Each
    entry names a stable ``op`` (the tool suffix ``app_<name>_<op>``), the HTTP
    ``method`` + ``path`` on the app backend, a human ``summary``, and optional
    JSON-schema-ish ``params`` (query/path) / ``body`` hints. ``agentCallable``
    (default True) gates whether the route is exposed as an agent tool + through
    ``call-app-route`` — a declared-but-not-callable route documents the surface
    without surfacing it. See :class:`~personalclaw.tool_providers` AppRoutesToolProvider.
    """

    op: str = ""  # stable operation id → tool suffix, e.g. "list_artifacts"
    method: str = "GET"  # HTTP verb
    path: str = ""  # path on the app backend, e.g. "/artifacts"
    summary: str = ""  # one-line human description
    params: dict[str, Any] = field(default_factory=dict)  # query/path param hints
    body: dict[str, Any] = field(default_factory=dict)  # request-body shape hint
    agentCallable: bool = True  # expose as agent tool + call-app-route  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.op, "method": self.method, "path": self.path}
        if self.summary:
            d["summary"] = self.summary
        if self.params:
            d["params"] = self.params
        if self.body:
            d["body"] = self.body
        if not self.agentCallable:
            d["agentCallable"] = False
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouteEntry":
        return cls(
            op=str(data.get("op", "")),
            method=str(data.get("method", "GET")).upper() or "GET",
            path=str(data.get("path", "")),
            summary=str(data.get("summary", "")),
            params=dict(data.get("params", {})) if isinstance(data.get("params"), dict) else {},
            body=dict(data.get("body", {})) if isinstance(data.get("body"), dict) else {},
            agentCallable=bool(data.get("agentCallable", True)),  # noqa: N815
        )


@dataclass
class BackendConfig:
    """Backend process configuration for an app."""

    entryPoint: str = ""  # e.g. backend/app.py or dist/main.js  # noqa: N815
    port: str = "auto"  # "auto" or a specific port number
    healthCheck: str = "/health"  # health check endpoint path  # noqa: N815
    type: str = ""  # "python", "asgi", "node", or "" (auto-detect)
    # Declared agent-callable route surface (§4.2). Read without executing app
    # code; surfaced as ``app_<name>_<op>`` tools + drivable via ``call-app-route``.
    routes: list[RouteEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.entryPoint:
            d["entryPoint"] = self.entryPoint
        if self.port != "auto":
            d["port"] = self.port
        if self.healthCheck != "/health":
            d["healthCheck"] = self.healthCheck
        if self.type:
            d["type"] = self.type
        if self.routes:
            d["routes"] = [r.to_dict() for r in self.routes]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackendConfig":
        return cls(
            entryPoint=str(data.get("entryPoint", "")),  # noqa: N815
            port=str(data.get("port", "auto")),
            healthCheck=str(data.get("healthCheck", "/health")),  # noqa: N815
            type=str(data.get("type", "")),
            routes=[RouteEntry.from_dict(r) for r in data.get("routes", []) if isinstance(r, dict)],
        )


@dataclass
class ProposalKind:
    """One proposal kind an app declares it may emit (INU-7, ``permissions.proposals[]``).

    ``kind_suffix`` is namespaced under the app at registration
    (``("app:<name>", "proposal:<kind_suffix>")``), so two apps declaring ``draft`` never
    collide and the user's notification rules address each app's kind separately. It is
    slug-shaped on purpose: a suffix carrying ``/`` or whitespace would break the
    ``<source>/<kind>`` rules-store key.
    """

    kind_suffix: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind_suffix": self.kind_suffix, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProposalKind":
        suffix = str(data.get("kind_suffix") or "")
        return cls(kind_suffix=suffix, label=str(data.get("label") or suffix))

    def is_valid(self) -> bool:
        return bool(_PROPOSAL_SUFFIX_RE.match(self.kind_suffix))


_PROPOSAL_SUFFIX_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


@dataclass
class Permissions:
    """Declared permissions for an app."""

    api: list[str] = field(default_factory=list)  # allowed API path prefixes
    events: list[str] = field(default_factory=list)  # allowed WebSocket event types
    mcpTools: list[str] = field(default_factory=list)  # noqa: N815
    storage: bool = False
    network: bool = False
    memory: str = ""  # "", "app-scoped", or "shared"
    cron: bool = False
    agent: bool = False  # may run background agent tasks (headless subagent runs)
    # APE-9: target app names this app may send a brokered message to (via
    # POST /api/apps/message). Same list-of-names shape as ``events``/``mcpTools``
    # — an exact name or a trailing-``*`` prefix. Empty → may message NO app (deny
    # by default): the gateway broker is the ONLY app-to-app path and refuses an
    # undeclared pair with 403 + a SEL audit row.
    # APE-12: it reaches install consent over the WHOLE leg — ``to_dict`` here →
    # ``GET /api/apps`` / the catalog entry → ``AppPermissionsWire`` (web/src/lib/
    # api.ts) → ``PermissionList`` (web/src/pages/apps/AppsSection.tsx), which names
    # each target (a trailing-``*`` entry as the prefix pattern it is) among the
    # ENFORCED permissions, and states the deny-by-default case when this is empty.
    # APE-9 shipped the broker without that last mile, so this comment claimed a
    # consent surface that did not exist; ``test_app_messaging.py`` now pins the
    # server leg and ``permissionConsent.test.tsx`` the rendering.
    appMessaging: list[str] = field(default_factory=list)  # noqa: N815
    # APE-10: consented cross-app read-only file sharing — the file-sharing mirror of
    # APE-9's messaging broker, and it deliberately reuses APE-9's DOUBLE-DECLARATION
    # shape rather than inventing a third. ``storageShared`` is the SHARER opting IN to
    # exposing its own ``app_data_dir`` to a reader; ``storageRead`` is the CONSUMER
    # naming the apps whose data it reads (an exact name or a trailing-``*`` prefix, same
    # grammar as ``appMessaging``). A read is granted only when BOTH halves hold
    # (consumer names the sharer AND the sharer set ``storageShared: true``) — no silent
    # one-sided grant. Deny-by-default: an empty ``storageRead`` reads nothing.
    # Enforcement is where storage is granted (``backend_runtime``): each granted sharer
    # is mounted READ-ONLY into the consumer's backend env as
    # ``PERSONALCLAW_APP_SHARED_DIR_<SHARER>`` (writes stay broker-only, APE-9), and the
    # SDK hands the consumer a read-only handle (``sdk.util.shared_app_data_dir``). Both
    # reach install consent via ``to_dict`` → ``catalog._manifest_consent`` → the Store.
    storageShared: bool = False  # noqa: N815
    storageRead: list[str] = field(default_factory=list)  # noqa: N815
    # DC-2: native desktop capabilities this app may read/use through the gateway
    # (``["audio_capture", "native_notifications"]``). Names come from
    # ``dashboard.desktop_registry.CAPABILITIES``; anything else never matches, so a
    # typo denies rather than widens. Empty → the app may reach NO desktop
    # capability (deny by default) and ``/api/desktop/*`` refuses it 403 + SEL
    # ``desktop.capability_denied``. Apps never touch Electron IPC — the gateway
    # mediates every call — so this list plus ``api`` is the whole reach.
    desktop: list[str] = field(default_factory=list)
    # INU-7: proposal kinds this app may emit into the inbox. Each entry registers as the
    # notification pair ``("app:<name>", "proposal:<kind_suffix>")`` at ENABLE time, and
    # ``POST /api/inbox/proposals`` refuses (403) a suffix that is not declared here — so
    # the manifest, not the request body, decides what an app may raise. Deny by default:
    # an empty list means the app may emit NO proposal. Reaches install consent the same
    # way ``appMessaging``/``storageRead`` do (``to_dict`` → the Store's permission list).
    # Like APE-9's messaging broker this is a DOUBLE declaration, on purpose and by
    # precedent: reaching the route at all still requires ``/api/inbox/proposals`` in
    # ``api`` (the middleware's path gate), and this list decides WHICH kinds may be
    # raised. Neither half alone grants anything.
    proposals: list["ProposalKind"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.api:
            d["api"] = self.api
        if self.events:
            d["events"] = self.events
        if self.mcpTools:
            d["mcpTools"] = self.mcpTools
        if self.storage:
            d["storage"] = True
        if self.network:
            d["network"] = True
        if self.memory:
            d["memory"] = self.memory
        if self.cron:
            d["cron"] = True
        if self.agent:
            d["agent"] = True
        if self.appMessaging:
            d["appMessaging"] = self.appMessaging
        if self.storageShared:
            d["storageShared"] = True
        if self.storageRead:
            d["storageRead"] = self.storageRead
        if self.desktop:
            d["desktop"] = self.desktop
        if self.proposals:
            d["proposals"] = [p.to_dict() for p in self.proposals]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Permissions":
        return cls(
            api=[str(p) for p in data.get("api", []) if p],
            events=[str(e) for e in data.get("events", []) if e],
            mcpTools=[str(t) for t in data.get("mcpTools", []) if t],  # noqa: N815
            storage=bool(data.get("storage", False)),
            network=bool(data.get("network", False)),
            memory=str(data.get("memory", "")),
            cron=bool(data.get("cron", False)),
            agent=bool(data.get("agent", False)),
            appMessaging=[str(t) for t in data.get("appMessaging", []) if t],  # noqa: N815
            storageShared=bool(data.get("storageShared", False)),  # noqa: N815
            storageRead=[str(t) for t in data.get("storageRead", []) if t],  # noqa: N815
            desktop=[str(c) for c in data.get("desktop", []) if c],
            proposals=[
                ProposalKind.from_dict(p) for p in data.get("proposals", []) if isinstance(p, dict)
            ],
        )

    def proposal_kind(self, kind_suffix: str) -> "ProposalKind | None":
        """The declared kind for *kind_suffix*, or None — the 403 check reads THIS."""
        for entry in self.proposals:
            if entry.kind_suffix == kind_suffix:
                return entry
        return None


@dataclass
class SetupConfig:
    """Installation and setup configuration for an app."""

    onInstall: str = ""  # shell command run after first install  # noqa: N815
    onUpdate: str = ""  # shell command run after update (new code in place)  # noqa: N815
    onUninstall: str = ""  # shell command run before removing app files  # noqa: N815
    onEnable: str = ""  # shell command run when app is enabled  # noqa: N815
    onDisable: str = ""  # shell command run when app is disabled  # noqa: N815
    onEnableTimeout: int = 30  # seconds; configurable per-app  # noqa: N815
    onDisableTimeout: int = 30  # seconds; configurable per-app  # noqa: N815
    configSchema: dict[str, Any] = field(default_factory=dict)  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.onInstall:
            d["onInstall"] = self.onInstall
        if self.onUpdate:
            d["onUpdate"] = self.onUpdate
        if self.onUninstall:
            d["onUninstall"] = self.onUninstall
        if self.onEnable:
            d["onEnable"] = self.onEnable
        if self.onDisable:
            d["onDisable"] = self.onDisable
        if self.onEnableTimeout != 30:
            d["onEnableTimeout"] = self.onEnableTimeout
        if self.onDisableTimeout != 30:
            d["onDisableTimeout"] = self.onDisableTimeout
        if self.configSchema:
            d["configSchema"] = self.configSchema
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SetupConfig":
        return cls(
            onInstall=str(data.get("onInstall", "")),  # noqa: N815
            onUpdate=str(data.get("onUpdate", "")),  # noqa: N815
            onUninstall=str(data.get("onUninstall", "")),  # noqa: N815
            onEnable=str(data.get("onEnable", "")),  # noqa: N815
            onDisable=str(data.get("onDisable", "")),  # noqa: N815
            onEnableTimeout=int(data.get("onEnableTimeout", 30)),  # noqa: N815
            onDisableTimeout=int(data.get("onDisableTimeout", 30)),  # noqa: N815
            configSchema=dict(data.get("configSchema", {})),  # noqa: N815
        )


@dataclass
class CliConfig:
    """App-contributed CLI seams (residue #3, #4 — Plan 32).

    An app may hook into the two core CLI commands without living in core:

    - ``setup`` — a ``"module:function"`` entry point (relative to the app dir)
      run during ``personalclaw setup`` after the core steps. The function
      receives a :class:`personalclaw.sdk.cli.SetupContext` and runs its own
      interactive step (e.g. collecting provider tokens).
    - ``doctor`` — a ``"module:function"`` entry point run during
      ``personalclaw doctor``; it returns a ``list[DoctorLine]`` that the doctor
      renderer prints as a per-app section.

    Both are optional and default to empty (the app contributes nothing).
    Static data only — the module path is stored, never imported, at parse time.
    """

    setup: str = ""  # "module:function" run during `personalclaw setup`
    doctor: str = ""  # "module:function" returning list[DoctorLine] for `doctor`

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.setup:
            d["setup"] = self.setup
        if self.doctor:
            d["doctor"] = self.doctor
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CliConfig":
        return cls(
            setup=str(data.get("setup", "")),
            doctor=str(data.get("doctor", "")),
        )


@dataclass
class MarketplaceDependencies:
    """Marketplace-managed dependencies (MCP servers, skills, agents)."""

    mcp: list[Any] = field(default_factory=list)  # str or {"id": str, "managedBy": str}
    skills: list[Any] = field(default_factory=list)
    agents: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.mcp:
            d["mcp"] = self.mcp
        if self.skills:
            d["skills"] = self.skills
        if self.agents:
            d["agents"] = self.agents
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketplaceDependencies":
        return cls(
            mcp=list(data.get("mcp", [])),
            skills=list(data.get("skills", [])),
            agents=list(data.get("agents", [])),
        )


@dataclass
class Dependencies:
    """External dependencies that PersonalClaw should resolve during install.

    ``managedBy`` controls the default installation strategy:
      - ``"gateway"``: PersonalClaw runs the skills CLI for each dependency
      - ``"app"``: PersonalClaw only checks existence, does not install

    Individual entries can override via object format:
    ``{"id": "some-mcp", "managedBy": "app"}``

    ``pythonDependencies`` are pip requirement specifiers (e.g.
    ``"faster-whisper>=1.0"``) the app needs at runtime. Core ships LEAN — heavy
    ML/provider libs (sentence-transformers, faster-whisper, boto3, …) are NOT
    core deps; the app that needs one declares it here and the installer pip-installs
    it into the shared core venv at install/update time. A newly-introduced dep
    requires a gateway RESTART to import (the running process already imported its
    modules) — surfaced to the user via the install result's ``restart_required``.
    """

    managedBy: str = "gateway"  # noqa: N815
    marketplace: "MarketplaceDependencies" = field(default_factory=MarketplaceDependencies)
    commands: list[str] = field(default_factory=list)
    pythonDependencies: list[str] = field(default_factory=list)  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.managedBy != "gateway":
            d["managedBy"] = self.managedBy
        mkt_d = self.marketplace.to_dict()
        if mkt_d:
            d["marketplace"] = mkt_d
        if self.commands:
            d["commands"] = self.commands
        if self.pythonDependencies:
            d["pythonDependencies"] = self.pythonDependencies
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Dependencies":
        mkt_raw = data.get("marketplace", {})
        marketplace = (
            MarketplaceDependencies.from_dict(mkt_raw)
            if isinstance(mkt_raw, dict)
            else MarketplaceDependencies()
        )
        return cls(
            managedBy=str(data.get("managedBy", "gateway")),  # noqa: N815
            marketplace=marketplace,
            commands=[str(c) for c in data.get("commands", [])],
            pythonDependencies=[str(p) for p in data.get("pythonDependencies", [])],  # noqa: N815
        )


@dataclass
class ClientInstallConfig:
    """Instructions for installing an app on the user's local machine.

    Used when PersonalClaw runs on a remote host and the app requires a
    specific local platform (e.g. macOS for Electron apps).
    """

    shell: str = ""  # one-liner for the user to run in their terminal
    postInstall: str = (
        ""  # command to run after install (e.g. "open ~/Applications/MyApp.app")  # noqa: N815
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.shell:
            d["shell"] = self.shell
        if self.postInstall:
            d["postInstall"] = self.postInstall
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClientInstallConfig":
        return cls(
            shell=str(data.get("shell", "")),
            postInstall=str(data.get("postInstall", "")),  # noqa: N815
        )


@dataclass
class PlatformConfig:
    """Platform requirements and install mode for an app.

    ``os`` declares which platforms the app can run on.
    ``installMode`` controls how the App Store handles installation:

    - ``"server"`` (default): PersonalClaw clones + installs on the server.
    - ``"client"``: Must be installed on the user's local machine.
      When PersonalClaw is on an incompatible platform, the App Store shows
      copy-paste terminal instructions instead of running the install.
    """

    os: list[str] = field(default_factory=lambda: ["macos", "linux"])
    arch: list[str] = field(default_factory=list)  # empty = any arch
    installMode: str = "server"  # "server" | "client"  # noqa: N815
    clientInstall: ClientInstallConfig = field(default_factory=ClientInstallConfig)  # noqa: N815

    # Map user-friendly OS names to sys.platform values
    _OS_TO_PLATFORM = {"macos": "darwin", "linux": "linux"}
    _PLATFORM_TO_OS = {"darwin": "macos", "linux": "linux"}

    def supports_platform(self, sys_platform: str) -> bool:
        """Check if this platform config supports the given sys.platform value."""
        return sys_platform in {self._OS_TO_PLATFORM.get(o, o) for o in self.os}

    @staticmethod
    def current_os() -> str:
        """Return the user-friendly OS name for the current platform."""
        import sys

        return PlatformConfig._PLATFORM_TO_OS.get(sys.platform, sys.platform)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.os != ["macos", "linux"]:
            d["os"] = self.os
        if self.arch:
            d["arch"] = self.arch
        if self.installMode != "server":
            d["installMode"] = self.installMode
        ci = self.clientInstall.to_dict()
        if ci:
            d["clientInstall"] = ci
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformConfig":
        ci_raw = data.get("clientInstall", {})
        ci = (
            ClientInstallConfig.from_dict(ci_raw)
            if isinstance(ci_raw, dict)
            else ClientInstallConfig()
        )
        return cls(
            os=[str(o) for o in data.get("os", ["macos", "linux"])],
            arch=[str(a) for a in data.get("arch", [])],
            installMode=str(data.get("installMode", "server")),  # noqa: N815
            clientInstall=ci,  # noqa: N815
        )


# ---------------------------------------------------------------------------
# Connector-pack source scripts (WATCHED-SOURCES §7.1)
# ---------------------------------------------------------------------------

#: The HTTP methods a connector pack's ``fetchSpec`` may name. READ-ONLY on purpose: a
#: connector pack exists to *watch* something, and a pack that could declare a POST would be
#: an unattended write to somebody else's service on a timer, authorized once at install.
#: A pack that genuinely needs a write graduates to a full ``KnowledgeSourceProvider``, where
#: the code is reviewable instead of being a URL template in a manifest.
PACK_FETCH_METHODS = frozenset({"GET", "HEAD"})

#: The arg types a pack's ``argsSchema`` may declare. Flat and scalar BY DESIGN, not as a
#: shortcut: an arg's only job is to be substituted into a URL template, and there is no
#: substitution of a nested object into a URL. A flatter grammar than
#: ``web_source.SPEC_SCHEMA``'s is therefore the correct grammar here, not a smaller one.
PACK_ARG_TYPES = frozenset({"string", "integer", "boolean"})

#: Header names whose value IS a credential. These must reference a ``{{secret:KEY}}``;
#: a literal there is a secret committed into a manifest that ships to a Store, which is the
#: same rule ``packs/connectors.py`` states as "schema-banned from carrying a value-bearing
#: field". Kept in sync with ``knowledge_providers.pack_parse.SECRET_HEADERS``, which renders
#: them (``test_connector_pack.py`` asserts the two sets are equal).
PACK_SECRET_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key"}
)

#: Any ``{{...}}`` in a fetch spec. Matched so an UNKNOWN placeholder is an error rather
#: than a literal brace pair silently fetched as part of a URL.
PACK_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")
_PACK_ARG_REF_RE = re.compile(r"^args\.([a-z][a-z0-9_]*)$")
_PACK_SECRET_REF_RE = re.compile(r"^secret:([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass
class PackSourceEntry:
    """One parse-only source a connector pack contributes (WATCHED-SOURCES §7.1).

    ``script`` parses; it does not fetch. ``fetchSpec`` is a URL template plus method and
    headers that the ENGINE renders and requests through ``net.fetch``, piping the body to
    ``script`` on stdin. ``argsSchema`` declares the per-source variables a user supplies
    (a repo name, a subreddit) that the template interpolates.

    The split is the whole security story: the pack's contribution is a parser, and a parser
    holds no network capability. See ``knowledge_providers/pack_parse.py`` for what enforces
    that at runtime.
    """

    name: str = ""  # kebab-case; a WatchedSource's spec.pack_source references it
    script: str = ""  # path relative to the app dir, .py
    displayName: str = ""  # noqa: N815
    description: str = ""
    fetchSpec: dict[str, Any] = field(default_factory=dict)  # noqa: N815
    argsSchema: dict[str, Any] = field(default_factory=dict)  # noqa: N815

    def declared_args(self) -> list[str]:
        """The arg names ``argsSchema`` declares (the only names a template may reference)."""
        return [str(k) for k in self.argsSchema] if isinstance(self.argsSchema, dict) else []

    def _validate_placeholders(self, raw: str, where: str) -> list[str]:
        """Every ``{{...}}`` in ``raw`` must be a declared arg or a secret reference."""
        errors: list[str] = []
        declared = set(self.declared_args())
        for token in PACK_PLACEHOLDER_RE.findall(raw):
            token = token.strip()
            arg = _PACK_ARG_REF_RE.match(token)
            if arg:
                if arg.group(1) not in declared:
                    errors.append(
                        f"source {self.name!r} {where} references undeclared arg "
                        f"{arg.group(1)!r} (declare it in argsSchema)"
                    )
                continue
            if _PACK_SECRET_REF_RE.match(token):
                if where == "fetchSpec.url":
                    # A secret in a URL lands in the egress audit row, the server's access
                    # log and any redirect's Referer. Headers are the only place for one.
                    errors.append(
                        f"source {self.name!r} must not put a secret in fetchSpec.url "
                        f"({{{{{token}}}}}); use a header"
                    )
                continue
            errors.append(
                f"source {self.name!r} {where} has unknown placeholder {{{{{token}}}}} "
                f"(only {{{{args.<name>}}}} and {{{{secret:<KEY>}}}} exist)"
            )
        return errors

    def validate(self) -> list[str]:
        """Errors in this entry (empty means valid). Static — no app code is executed."""
        errors: list[str] = []
        if not self.name:
            errors.append("source entry missing required field: name")
        elif not KEBAB_RE.match(self.name):
            errors.append(f"source name must be kebab-case, got: {self.name!r}")
        if not self.script:
            errors.append(f"source {self.name!r} missing required field: script")
        else:
            if ".." in self.script:
                errors.append(f"source script contains path traversal: {self.script!r}")
            if self.script.startswith("/"):
                errors.append(f"source script must be relative to the app dir: {self.script!r}")
            if not self.script.endswith(".py"):
                errors.append(f"source script must be a .py file: {self.script!r}")
        if not isinstance(self.argsSchema, dict):
            errors.append(f"source {self.name!r} argsSchema must be an object")
        else:
            for arg_name, decl in self.argsSchema.items():
                if not isinstance(decl, dict):
                    errors.append(f"source {self.name!r} argsSchema.{arg_name} must be an object")
                    continue
                kind = str(decl.get("type", "string") or "string")
                if kind not in PACK_ARG_TYPES:
                    errors.append(
                        f"source {self.name!r} argsSchema.{arg_name}.type must be one of "
                        f"{sorted(PACK_ARG_TYPES)}, got: {kind!r}"
                    )
        if not isinstance(self.fetchSpec, dict):
            errors.append(f"source {self.name!r} fetchSpec must be an object")
            return errors
        url = str(self.fetchSpec.get("url", "") or "")
        if not url:
            errors.append(f"source {self.name!r} missing required field: fetchSpec.url")
        elif not url.lower().startswith(("http://", "https://")):
            errors.append(f"source {self.name!r} fetchSpec.url must be http(s): {url[:80]!r}")
        else:
            errors.extend(self._validate_placeholders(url, "fetchSpec.url"))
        method = str(self.fetchSpec.get("method", "GET") or "GET").upper()
        if method not in PACK_FETCH_METHODS:
            errors.append(
                f"source {self.name!r} fetchSpec.method must be one of "
                f"{sorted(PACK_FETCH_METHODS)}, got: {method!r}"
            )
        headers = self.fetchSpec.get("headers", {})
        if not isinstance(headers, dict):
            errors.append(f"source {self.name!r} fetchSpec.headers must be an object")
        else:
            for header, value in headers.items():
                text = str(value)
                errors.extend(self._validate_placeholders(text, f"fetchSpec.headers.{header}"))
                if str(header).lower() in PACK_SECRET_HEADERS and "{{secret:" not in text:
                    errors.append(
                        f"source {self.name!r} fetchSpec.headers.{header} must reference a "
                        f"{{{{secret:KEY}}}} rather than an inline value — a manifest ships "
                        f"to a Store, so a literal there is a published credential"
                    )
        unknown = sorted(
            set(self.fetchSpec) - {"url", "method", "headers", "accept", "description"}
        )
        if unknown:
            errors.append(f"source {self.name!r} fetchSpec has unknown key(s) {unknown}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "script": self.script}
        if self.displayName:
            d["displayName"] = self.displayName
        if self.description:
            d["description"] = self.description
        if self.fetchSpec:
            d["fetchSpec"] = self.fetchSpec
        if self.argsSchema:
            d["argsSchema"] = self.argsSchema
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackSourceEntry":
        return cls(
            name=str(data.get("name", "")),
            script=str(data.get("script", "")),
            displayName=str(data.get("displayName", "")),  # noqa: N815
            description=str(data.get("description", "")),
            fetchSpec=(  # noqa: N815
                dict(data["fetchSpec"]) if isinstance(data.get("fetchSpec"), dict) else {}
            ),
            argsSchema=(  # noqa: N815
                dict(data["argsSchema"]) if isinstance(data.get("argsSchema"), dict) else {}
            ),
        )


# ---------------------------------------------------------------------------
# Provider declaration (extension system)
# ---------------------------------------------------------------------------

PROVIDER_TYPES = frozenset(
    {
        "model",
        "agent",
        "task",
        "channel",
        "inbox",
        "skills",
        "knowledge",
        "memory",
        "notification",
        "tool",
        "workflow",
        "search",
        "action",
        "prompt",
        # AUTOMATION-SUBSTRATE AUTO-A2: an app-contributed is-the-user-on-duty predicate. Its
        # `DutyGateTypeHandler` lands in the same commit (the #47 rule).
        "duty_gate",
        # DURABILITY-AND-SYNC §4.3: an app-contributed sync transport (git-sync, dir-sync). Its
        # `SyncTypeHandler` lands in the same commit (the #47 rule).
        "sync",
        # EXECUTION-ISOLATION EI-1: an app-contributed sandbox provider (container/VM isolation
        # tier). Its `SandboxTypeHandler` lands in the same commit (the #47 rule). The `none`
        # provider is a core builtin, not an app.
        "sandbox",
        # AUTOMATION-SUBSTRATE AUTO-A4: an app-contributed ORIGIN of trigger events. The app emits
        # typed events onto the one event bus under a namespaced source (`app:<name>:<event>`),
        # which `kind: event` triggers match with the existing `{source, pattern}` spec — no new
        # trigger kind. Its `TriggerSourceTypeHandler` lands in the same commit (the #47 rule).
        "trigger_source",
        # TEAM-SHARED-ENTITIES §3 (TSE-4): an app-contributed STORE of trigger ROWS — a shared or
        # team trigger backend. NOT `trigger_source` above: that supplies the STIMULUS (live
        # observer, pushes events), this supplies the RULE (passive store, serves definitions).
        # Rows only, never execution: the local TriggerService fires, and only rows whose `author`
        # is the owner. Its `TriggerTypeHandler` lands in the same commit (the #47 rule).
        "trigger",
    }
)
# NOTE: this set MUST equal the runtime type-handler registry
# (providers/registry.py register_type_handler(...) calls). ``prompt`` was a
# registered handler (PromptTypeHandler) but was missing here (#47, the split-era
# #1-'action'-rejected class) — so ProviderConfig.validate() rejected any prompt
# provider manifest, blocking reinstall/update + third-party prompt providers.
# native-prompts is native (auto-seeded, bypasses install-time validation), which
# masked it. test_manifest_types_match_handlers guards this equality going forward.

_HOOK_OR_ENTRYPOINT_RE = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$"
)

# LOCAL-MODEL-MANAGER-V2 §3.1 — where a provider's heavy work runs. This is a field on
# the EXISTING ``model`` type, deliberately NOT a new provider type: PROVIDER_TYPES above
# and the ``_TypeHandler`` set are untouched, so ``test_manifest_types_match_handlers``
# stays green by construction and every registration seam is unchanged.
EXECUTION_IN_PROCESS = "in-process"
EXECUTION_SIDECAR = "sidecar"
#: In-process is the DEFAULT and stays it. A sidecar is earned by a crash history
#: (§10 "no blanket sidecar migration"): flipping the default would silently change the
#: runtime of every provider already installed on every machine.
EXECUTION_MODES = frozenset({EXECUTION_IN_PROCESS, EXECUTION_SIDECAR})


@dataclass
class AutonomyConfig:
    """An app-contributed action's declared autonomy bounds (AUTONOMY-GUARDRAILS §5.2).

    ``floor`` is the rung the action starts at; ``ceiling`` is the rung it can never pass
    however much track record accrues. Both name a rung from
    ``guardrails.autonomy.RUNGS`` (``draft_only`` → ``one_tap`` → ``auto_with_undo`` →
    ``autonomous``). Empty means UNDECLARED, and an undeclared action behaves exactly as it
    did before this block existed — the block is purely additive, and a manifest without
    one round-trips byte-identically.

    Two things this deliberately does NOT let an app say:

    * **``leaves_machine``.** Core derives it from the app's own ``permissions.network``
      declaration. An app that could self-certify "my action stays on this machine" would
      be self-certifying its way to the top of the ladder.
    * **``autonomous`` for an action that reaches the network.** ``ceiling`` is clamped to
      ``clamp_untrusted_ceiling``'s bound at registration, LOUDLY (a log line and a SEL
      row naming both the declared and the granted ceiling). A manifest's claim has had no
      in-tree review, and a silent downgrade is a recorded finding in this codebase.
    """

    floor: str = ""
    ceiling: str = ""

    def validate(self) -> list[str]:
        from personalclaw.guardrails.autonomy import RUNGS

        errors: list[str] = []
        for label, value in (("floor", self.floor), ("ceiling", self.ceiling)):
            if value and value not in RUNGS:
                errors.append(
                    f"provider.autonomy.{label} must be one of {list(RUNGS)}, got: {value!r}"
                )
        if self.floor and self.ceiling and self.floor in RUNGS and self.ceiling in RUNGS:
            if RUNGS.index(self.ceiling) < RUNGS.index(self.floor):
                errors.append(
                    f"provider.autonomy.ceiling {self.ceiling!r} is below its floor {self.floor!r}"
                )
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.floor:
            d["floor"] = self.floor
        if self.ceiling:
            d["ceiling"] = self.ceiling
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutonomyConfig":
        return cls(floor=str(data.get("floor", "")), ceiling=str(data.get("ceiling", "")))


@dataclass
class ProviderConfig:
    """Declares that this extension provides a pluggable provider implementation.

    ``type`` identifies the entity class (model, agent, task, etc.).
    ``implementation`` is a Python entry point in ``module.path:factory_fn``
    format, resolved relative to the extension's directory.  The factory
    receives the extension's current config dict and returns a provider instance.
    ``settingsSchema`` is a JSON Schema (Draft-07 + x-meta) describing
    user-configurable settings for this provider.
    """

    type: str = ""
    implementation: str = ""
    multiInstance: bool = False  # noqa: N815
    settingsSchema: dict[str, Any] = field(default_factory=dict)  # noqa: N815
    capabilities: list[str] = field(default_factory=list)
    # The CONCRETE provider type this model app registers into the LLM registry
    # (e.g. "bedrock", "openai", "google") — distinct from ``type`` above, which is
    # the entity CLASS ("model"). Used by the Add-instance UI to submit the right
    # type. Empty for non-model providers (agent/task/…) that don't register an
    # LLM provider type.
    providerType: str = ""  # noqa: N815
    # Optional entity sub-grouping within a provider type. Hook providers, for
    # example, are all ``type: "hook"`` but each acts on a distinct entity
    # (task, agent, comms, …). The Settings UI sub-groups cards of one type by
    # this value so "Create Task Hook" sits under a "Task Hook Provider" group.
    # Empty → the UI treats the provider as belonging to its type's default group.
    entity: str = ""
    # AUTONOMY-GUARDRAILS §5.2: the rung bounds for an ``action`` provider, keyed
    # ``app:<app>.<action>`` when it registers. Additive and optional — a manifest with no
    # ``autonomy`` block declares no action type, and its action keeps exactly the
    # pre-ladder behaviour (denylist + capability fence + creation-time grant).
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    # LOCAL-MODEL-MANAGER-V2 §3.1: where this provider's heavy work RUNS.
    # ``in-process`` (the default, and what every existing app keeps) imports the
    # provider into the gateway. ``sidecar`` runs it in a child process with its own
    # venv (``local_models/sidecar.py``), so a native-lib crash kills the child instead
    # of the gateway. Deliberately NOT a new ``type``: ``PROVIDER_TYPES`` and the
    # ``_TypeHandler`` set are untouched, so registration, the app-name registry key and
    # the duck-typed local-model contract all still hold (§9).
    execution: str = EXECUTION_IN_PROCESS

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.execution not in EXECUTION_MODES:
            errors.append(
                f"provider.execution must be one of {sorted(EXECUTION_MODES)}, "
                f"got: {self.execution!r}"
            )
        if not self.type:
            errors.append("provider.type is required")
        elif self.type not in PROVIDER_TYPES:
            errors.append(
                f"provider.type must be one of {sorted(PROVIDER_TYPES)}, got: {self.type!r}"
            )
        if not self.implementation:
            errors.append("provider.implementation is required")
        elif not _HOOK_OR_ENTRYPOINT_RE.match(self.implementation):
            errors.append(
                f"provider.implementation must be 'module.path:factory_fn', "
                f"got: {self.implementation!r}"
            )
        errors.extend(self.autonomy.validate())
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.type:
            d["type"] = self.type
        if self.implementation:
            d["implementation"] = self.implementation
        if self.multiInstance:
            d["multiInstance"] = True
        if self.settingsSchema:
            d["settingsSchema"] = self.settingsSchema
        if self.capabilities:
            d["capabilities"] = self.capabilities
        if self.entity:
            d["entity"] = self.entity
        if self.providerType:
            d["providerType"] = self.providerType
        autonomy_d = self.autonomy.to_dict()
        if autonomy_d:
            d["autonomy"] = autonomy_d
        # Only emitted when it is NOT the default, so an in-process manifest round-trips
        # byte-identically and no existing app.json grows a key it never declared.
        if self.execution != EXECUTION_IN_PROCESS:
            d["execution"] = self.execution
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderConfig":
        autonomy_raw = data.get("autonomy", {})
        return cls(
            type=str(data.get("type", "")),
            implementation=str(data.get("implementation", "")),
            multiInstance=bool(data.get("multiInstance", False)),  # noqa: N815
            settingsSchema=dict(data.get("settingsSchema", {})),  # noqa: N815
            capabilities=[str(c) for c in data.get("capabilities", [])],
            entity=str(data.get("entity", "")),
            providerType=str(data.get("providerType", "")),  # noqa: N815
            autonomy=(
                AutonomyConfig.from_dict(autonomy_raw)
                if isinstance(autonomy_raw, dict)
                else AutonomyConfig()
            ),
            execution=str(data.get("execution", EXECUTION_IN_PROCESS) or EXECUTION_IN_PROCESS),
        )


# ---------------------------------------------------------------------------
# Main AppManifest
# ---------------------------------------------------------------------------

# Fields that are parsed into typed dataclass attributes
_KNOWN_FIELDS = frozenset(
    {
        "name",
        "version",
        "displayName",
        "description",
        "icon",
        "heroImage",
        "author",
        "license",
        "minPersonalClawVersion",
        "prompts",
        "mcpServers",
        "crons",
        "ui",
        "backend",
        "permissions",
        "setup",
        "tags",
        "platform",
        "dependencies",
        "provider",
        "providers",
        # Connector-pack parse-only source scripts (WATCHED-SOURCES §7.1).
        "sources",
        "native",
        "cli",
        "loggerRoots",
        # App-owned SKILL.md skills (§4.1) — a typed field again, seeded through
        # the supply-chain chokepoint on enable. See apps.skill_seed.
        "skills",
        # Legacy fields (stripped — no runtime consumer): parsed to extra for
        # forward-compat but no longer modeled as typed attributes.
        "agents",
        "sops",
    }
)


@dataclass
class AppManifest:
    """Static metadata for a PersonalClaw app — readable without executing app code.

    Parsed from ``app.json`` at the root of an app package.  Follows the same
    pattern as :class:`~backend.plugins.manifest.PluginManifest`: dataclass
    with ``validate`` / ``to_dict`` / ``from_dict`` / round-trip support.
    """

    # --- Required ---
    name: str = ""  # unique identifier, kebab-case
    version: str = ""  # semver string
    displayName: str = ""  # human-readable name  # noqa: N815
    description: str = ""  # short summary

    # --- Recommended ---
    # A lucide icon NAME (e.g. "Sparkles", "SquareTerminal") shown on the app's
    # Store/Library card + detail panel. Per the no-emoji tenet, apps declare
    # icons by lucide name, never an emoji glyph. Empty → the Blocks fallback.
    icon: str = ""
    # An OPTIONAL hero/banner image — a path RELATIVE to the app dir (e.g.
    # "assets/hero.png"). When present the Store/Library card renders it as a
    # banner and the detail panel shows it at the top. The card adapts across all
    # four states: hero+icon, hero-only, icon-only, neither. The API resolves this
    # to a ``heroUrl`` (a data: URI) so it works for installed AND not-yet-installed
    # catalog entries without a per-file serving route.
    heroImage: str = ""  # noqa: N815
    author: str = ""
    license: str = ""
    minPersonalClawVersion: str = ""  # noqa: N815

    # --- App-owned prompts ---
    # Prompt/snippet DEFINITION files (paths relative to the app dir) the app SHIPS
    # and OWNS. Each is a YAML with the same shape as a bundled prompt/snippet on
    # disk PLUS a top-level ``_entity`` (``prompt``|``snippet``) discriminator and,
    # for a prompt, a ``use_case``. Seeded into the native prompt store on enable
    # (idempotent, non-clobbering) and removed on disable. See apps.prompt_seed.
    prompts: list[str] = field(default_factory=list)

    # --- App-owned skills (§4.1) ---
    # SKILL.md skill dirs (paths relative to the app dir) the app SHIPS and OWNS.
    # Seeded into the user skills tree on enable THROUGH the supply-chain chokepoint
    # (quarantine → scan at the app's trust tier → lock), idempotent + non-clobbering,
    # removed on disable. See apps.skill_seed.
    skills: list[AppSkill] = field(default_factory=list)

    mcpServers: dict[str, Any] = field(default_factory=dict)  # MCP server configs  # noqa: N815

    # --- Scheduling ---
    crons: list[CronEntry] = field(default_factory=list)

    # --- Frontend ---
    ui: UIConfig = field(default_factory=UIConfig)

    # --- Backend ---
    backend: BackendConfig = field(default_factory=BackendConfig)

    # --- Permissions ---
    permissions: Permissions = field(default_factory=Permissions)

    # --- Setup ---
    setup: SetupConfig = field(default_factory=SetupConfig)

    # --- CLI seams (Plan 32) ---
    # App-contributed hooks into the two core CLI commands: a setup step and a
    # doctor probe. Both are optional "module:function" entry points resolved
    # from the installed app dir at command time (never imported at parse time).
    cli: CliConfig = field(default_factory=CliConfig)

    # --- Logger roots (Plan 32) ---
    # Logger namespaces this app logs under (e.g. ["slack_runtime"]). Static data
    # read WITHOUT importing app code so core log setup + the log-level handler
    # can apply levels to the app's loggers. Replaces constants.APP_LOGGER_ROOTS.
    loggerRoots: list[str] = field(default_factory=list)  # noqa: N815

    # --- Dependencies ---
    dependencies: Dependencies = field(default_factory=Dependencies)

    # --- Platform ---
    platform: PlatformConfig = field(default_factory=PlatformConfig)

    # --- Provider(s) (extension system) ---
    # An app may register ONE provider via ``provider`` (the common case) or
    # SEVERAL — of the same or different kinds — via ``providers``. Both feed the
    # registry; use :meth:`all_providers` to iterate the full set. ``provider``
    # stays the canonical single-provider field so existing one-provider apps and
    # the per-app registry keying are unchanged.
    provider: ProviderConfig | None = None
    providers: list[ProviderConfig] = field(default_factory=list)

    # --- Connector-pack sources (WATCHED-SOURCES §7.1) ---
    # Parse-only source scripts this app contributes. Each declares a fetch spec the ENGINE
    # performs (through ``net.fetch`` under the ``SOURCE`` egress policy) and a script that
    # reads the body on stdin and emits ``SourceItem`` JSON lines on stdout. Static data: the
    # scripts are never imported by core, only spawned under the parse fence. Present only on
    # a connector pack; ``validate`` requires the matching ``knowledge``/``source`` provider
    # declaration and the ``network`` permission, so the Store's consent surface tells the
    # truth about what installing one licenses.
    sources: list[PackSourceEntry] = field(default_factory=list)

    # --- Native — the ONE app-category flag ---
    # A ``native`` app ships INSIDE core (personalclaw/apps/native/) and is the
    # baseline for first-boot operability. On first run it's SEEDED as a real
    # installed app (``seed_builtin_apps``) — visible + CONFIGURABLE in the Apps
    # UI, backends managed — but LOCKED ON: disable / uninstall / force-uninstall are
    # refused; only its settings are editable. Everything else — first-party apps in
    # the workspace ``apps/`` dir, and third-party apps from user sources — is
    # ``native:false`` → shown in the Store, never auto-installed, fully user-managed.
    # (This single flag replaced the old ``installByDefault`` + always-on-invisible-
    # bundled-provider split.) The three app categories: native / first-party / third-party.
    native: bool = False

    # --- Discovery ---
    tags: list[str] = field(default_factory=list)

    # --- Forward compatibility ---
    extra: dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return list of validation errors (empty list means valid)."""
        errors: list[str] = []

        # Required fields
        if not self.name:
            errors.append("missing required field: name")
        elif not KEBAB_RE.match(self.name):
            errors.append(
                f"name must be kebab-case (lowercase alphanumeric + hyphens), got: {self.name!r}"
            )

        if not self.version:
            errors.append("missing required field: version")
        elif not SEMVER_RE.match(self.version):
            errors.append(f"version must be semver (e.g. 1.0.0), got: {self.version!r}")

        if not self.displayName:
            errors.append("missing required field: displayName")

        if not self.description:
            errors.append("missing required field: description")

        # Path traversal check on prompt paths
        for p in self.prompts:
            if ".." in str(p):
                errors.append(f"prompts path contains path traversal: {p!r}")

        # Path traversal check on app-owned skill dir paths (§4.1)
        for sk in self.skills:
            if ".." in str(sk.path):
                errors.append(f"skills path contains path traversal: {sk.path!r}")

        # UI entry path traversal check
        if self.ui.entry and ".." in self.ui.entry:
            errors.append(f"ui.entry contains path traversal: {self.ui.entry!r}")

        # UI page validation
        for page in self.ui.pages:
            if not page.route:
                errors.append("ui page missing required field: route")
            if not page.label:
                errors.append("ui page missing required field: label")
            if page.entryPoint and ".." in page.entryPoint:
                errors.append(f"ui page entryPoint contains path traversal: {page.entryPoint!r}")

        # Cron validation
        for cron in self.crons:
            if not cron.name:
                errors.append("cron entry missing required field: name")
            if not cron.every and not cron.cron_expr:
                errors.append(
                    f"cron entry {cron.name!r} must specify either 'every' or 'cron_expr'"
                )

        # Declared proposal kinds (INU-7) — validated HERE so a bad suffix is an install
        # error, not a broken rules-store key discovered at enable time.
        seen_suffixes: set[str] = set()
        for pk in self.permissions.proposals:
            if not pk.kind_suffix:
                errors.append("permissions.proposals entry missing required field: kind_suffix")
            elif not pk.is_valid():
                errors.append(
                    f"proposal kind_suffix must be a slug (lowercase alphanumeric, "
                    f"'-' or '_'), got: {pk.kind_suffix!r}"
                )
            elif pk.kind_suffix in seen_suffixes:
                errors.append(f"duplicate proposal kind_suffix: {pk.kind_suffix!r}")
            else:
                seen_suffixes.add(pk.kind_suffix)

        # Declared backend routes (§4.2) — statically checkable without app code.
        seen_ops: set[str] = set()
        for r in self.backend.routes:
            if not r.op:
                errors.append("backend route missing required field: op")
            elif not ROUTE_OP_RE.match(r.op):
                errors.append(
                    f"backend route op must be a valid identifier "
                    f"(lowercase alphanumeric + underscores), got: {r.op!r}"
                )
            elif r.op in seen_ops:
                errors.append(f"backend route op declared more than once: {r.op!r}")
            else:
                seen_ops.add(r.op)
            if not r.path:
                errors.append(f"backend route {r.op!r} missing required field: path")
            elif not r.path.startswith("/"):
                errors.append(f"backend route {r.op!r} path must start with '/': {r.path!r}")
            if ".." in r.path:
                errors.append(f"backend route path contains path traversal: {r.path!r}")

        # Provider validation — the single ``provider`` and each of ``providers``.
        for prov in self.all_providers():
            errors.extend(prov.validate())

        errors.extend(self._validate_sources())

        return errors

    def _validate_sources(self) -> list[str]:
        """Errors in the connector-pack ``sources`` block (WATCHED-SOURCES §7.1).

        Two cross-field rules make the kind COHERENT rather than merely well-formed, and both
        are install-time refusals because both failures are otherwise silent:

        * a ``sources`` block with no ``knowledge``/``source`` provider is a set of scripts
          nothing can ever drive — the declared-but-inert shape this codebase keeps finding;
        * a ``sources`` block without ``permissions.network`` makes the install-consent card
          read "Network access: not declared" for an app whose entire purpose is scheduled
          outbound fetching. ``network`` is disclosure and not containment — the card says so
          in as many words (``installConsent.tsx``, and app-platform.md §permissions) — but
          disclosure that reads the wrong way is worse than none, and the fetch happens
          BECAUSE the pack asked for it even though core is what performs it.
        """
        errors: list[str] = []
        if not self.sources:
            return errors
        seen: set[str] = set()
        for entry in self.sources:
            errors.extend(entry.validate())
            if entry.name:
                if entry.name in seen:
                    errors.append(f"duplicate source name: {entry.name!r}")
                seen.add(entry.name)
        has_source_provider = any(
            p.type == "knowledge" and "source" in p.capabilities for p in self.all_providers()
        )
        if not has_source_provider:
            errors.append(
                "sources[] requires a provider with type 'knowledge' and the 'source' "
                "capability — without one the scripts are declared and unreachable"
            )
        if not self.permissions.network:
            errors.append(
                "sources[] requires permissions.network: core performs the fetch, but it "
                "happens because this pack asked, and without the declaration the install "
                "card reads 'Network access: not declared'"
            )
        return errors

    def pack_source(self, name: str) -> "PackSourceEntry | None":
        """The declared source named ``name``, or None — what a WatchedSource spec resolves."""
        for entry in self.sources:
            if entry.name == name:
                return entry
        return None

    def all_providers(self) -> list[ProviderConfig]:
        """Every provider this app registers — the single ``provider`` (if any)
        followed by ``providers`` — so callers iterate one list regardless of how
        the manifest declared them. An app may register multiple providers of the
        same or different kinds."""
        out: list[ProviderConfig] = []
        if self.provider:
            out.append(self.provider)
        out.extend(self.providers)
        return out

    # -----------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict, including extra fields."""
        d: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "displayName": self.displayName,
            "description": self.description,
        }
        if self.icon:
            d["icon"] = self.icon
        if self.heroImage:
            d["heroImage"] = self.heroImage
        if self.author:
            d["author"] = self.author
        if self.license:
            d["license"] = self.license
        if self.minPersonalClawVersion:
            d["minPersonalClawVersion"] = self.minPersonalClawVersion
        if self.prompts:
            d["prompts"] = self.prompts
        if self.skills:
            d["skills"] = [s.to_dict() for s in self.skills]
        if self.mcpServers:
            d["mcpServers"] = self.mcpServers
        if self.crons:
            d["crons"] = [c.to_dict() for c in self.crons]
        ui_d = self.ui.to_dict()
        if ui_d:
            d["ui"] = ui_d
        backend_d = self.backend.to_dict()
        if backend_d:
            d["backend"] = backend_d
        perms_d = self.permissions.to_dict()
        if perms_d:
            d["permissions"] = perms_d
        setup_d = self.setup.to_dict()
        if setup_d:
            d["setup"] = setup_d
        cli_d = self.cli.to_dict()
        if cli_d:
            d["cli"] = cli_d
        if self.loggerRoots:
            d["loggerRoots"] = self.loggerRoots
        deps_d = self.dependencies.to_dict()
        if deps_d:
            d["dependencies"] = deps_d
        platform_d = self.platform.to_dict()
        if platform_d:
            d["platform"] = platform_d
        if self.provider:
            provider_d = self.provider.to_dict()
            if provider_d:
                d["provider"] = provider_d
        if self.providers:
            providers_d = [p.to_dict() for p in self.providers]
            providers_d = [p for p in providers_d if p]
            if providers_d:
                d["providers"] = providers_d
        if self.sources:
            d["sources"] = [s.to_dict() for s in self.sources]
        if self.native:
            d["native"] = True
        if self.tags:
            d["tags"] = self.tags
        # Preserve unknown fields for forward compatibility
        d.update(self.extra)
        return d

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    # -----------------------------------------------------------------
    # Parsing
    # -----------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppManifest":
        """Parse from dict, preserving unknown fields in ``extra``."""
        extra = {k: v for k, v in data.items() if k not in _KNOWN_FIELDS}

        crons_raw = data.get("crons", [])
        crons = [CronEntry.from_dict(c) for c in crons_raw if isinstance(c, dict)]

        ui_raw = data.get("ui", {})
        ui = UIConfig.from_dict(ui_raw) if isinstance(ui_raw, dict) else UIConfig()

        backend_raw = data.get("backend", {})
        backend = (
            BackendConfig.from_dict(backend_raw)
            if isinstance(backend_raw, dict)
            else BackendConfig()
        )

        perms_raw = data.get("permissions", {})
        permissions = (
            Permissions.from_dict(perms_raw) if isinstance(perms_raw, dict) else Permissions()
        )

        setup_raw = data.get("setup", {})
        setup = SetupConfig.from_dict(setup_raw) if isinstance(setup_raw, dict) else SetupConfig()

        cli_raw = data.get("cli", {})
        cli = CliConfig.from_dict(cli_raw) if isinstance(cli_raw, dict) else CliConfig()

        deps_raw = data.get("dependencies", {})
        deps = Dependencies.from_dict(deps_raw) if isinstance(deps_raw, dict) else Dependencies()

        platform_raw = data.get("platform", {})
        platform_cfg = (
            PlatformConfig.from_dict(platform_raw)
            if isinstance(platform_raw, dict)
            else PlatformConfig()
        )

        provider_raw = data.get("provider")
        provider_cfg = (
            ProviderConfig.from_dict(provider_raw) if isinstance(provider_raw, dict) else None
        )

        providers_cfg = [
            ProviderConfig.from_dict(p) for p in data.get("providers", []) if isinstance(p, dict)
        ]

        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            displayName=str(data.get("displayName", "")),  # noqa: N815
            description=str(data.get("description", "")),
            icon=str(data.get("icon", "")),
            heroImage=str(data.get("heroImage", "")),  # noqa: N815
            author=str(data.get("author", "")),
            license=str(data.get("license", "")),
            minPersonalClawVersion=str(data.get("minPersonalClawVersion", "")),  # noqa: N815
            prompts=[str(p) for p in data.get("prompts", []) if p],
            skills=[
                AppSkill.from_dict(s)
                for s in data.get("skills", [])
                if isinstance(s, dict) and s.get("path")
            ],
            mcpServers=dict(data.get("mcpServers", {})),  # noqa: N815
            crons=crons,
            ui=ui,
            backend=backend,
            permissions=permissions,
            setup=setup,
            cli=cli,
            loggerRoots=[str(r) for r in data.get("loggerRoots", []) if r],
            dependencies=deps,
            platform=platform_cfg,
            provider=provider_cfg,
            providers=providers_cfg,
            sources=[
                PackSourceEntry.from_dict(s)
                for s in data.get("sources", [])
                if isinstance(s, dict) and s.get("name")
            ],
            native=bool(data.get("native", False)),
            tags=[str(t) for t in data.get("tags", []) if t],
            extra=extra,
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "AppManifest":
        """Parse from an ``app.json`` file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"app.json must be a JSON object, got {type(data).__name__}")
        return cls.from_dict(data)
