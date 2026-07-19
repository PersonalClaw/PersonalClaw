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
class BackendConfig:
    """Backend process configuration for an app."""

    entryPoint: str = ""  # e.g. backend/app.py or dist/main.js  # noqa: N815
    port: str = "auto"  # "auto" or a specific port number
    healthCheck: str = "/health"  # health check endpoint path  # noqa: N815
    type: str = ""  # "python", "asgi", "node", or "" (auto-detect)

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
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackendConfig":
        return cls(
            entryPoint=str(data.get("entryPoint", "")),  # noqa: N815
            port=str(data.get("port", "auto")),
            healthCheck=str(data.get("healthCheck", "/health")),  # noqa: N815
            type=str(data.get("type", "")),
        )


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
        )


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
        marketplace = MarketplaceDependencies.from_dict(mkt_raw) if isinstance(mkt_raw, dict) else MarketplaceDependencies()
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
    postInstall: str = ""  # command to run after install (e.g. "open ~/Applications/MyApp.app")  # noqa: N815

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
        ci = ClientInstallConfig.from_dict(ci_raw) if isinstance(ci_raw, dict) else ClientInstallConfig()
        return cls(
            os=[str(o) for o in data.get("os", ["macos", "linux"])],
            arch=[str(a) for a in data.get("arch", [])],
            installMode=str(data.get("installMode", "server")),  # noqa: N815
            clientInstall=ci,  # noqa: N815
        )


# ---------------------------------------------------------------------------
# Provider declaration (extension system)
# ---------------------------------------------------------------------------

PROVIDER_TYPES = frozenset({
    "model", "agent", "task", "channel", "inbox",
    "skills", "knowledge", "memory", "notification", "tool", "workflow",
    "search", "action", "prompt",
})
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

    def validate(self) -> list[str]:
        errors: list[str] = []
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
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderConfig":
        return cls(
            type=str(data.get("type", "")),
            implementation=str(data.get("implementation", "")),
            multiInstance=bool(data.get("multiInstance", False)),  # noqa: N815
            settingsSchema=dict(data.get("settingsSchema", {})),  # noqa: N815
            capabilities=[str(c) for c in data.get("capabilities", [])],
            entity=str(data.get("entity", "")),
            providerType=str(data.get("providerType", "")),  # noqa: N815
        )


# ---------------------------------------------------------------------------
# Main AppManifest
# ---------------------------------------------------------------------------

# Fields that are parsed into typed dataclass attributes
_KNOWN_FIELDS = frozenset({
    "name", "version", "displayName", "description", "icon", "heroImage", "author",
    "license", "minPersonalClawVersion", "prompts",
    "mcpServers", "crons", "ui", "backend", "permissions", "setup", "tags",
    "platform", "dependencies", "provider", "providers", "native",
    # Legacy fields (stripped — no runtime consumer): parsed to extra for
    # forward-compat but no longer modeled as typed attributes.
    "agents", "skills", "sops",
})


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
                errors.append(
                    f"prompts path contains path traversal: {p!r}"
                )

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
                errors.append(
                    f"ui page entryPoint contains path traversal: {page.entryPoint!r}"
                )

        # Cron validation
        for cron in self.crons:
            if not cron.name:
                errors.append("cron entry missing required field: name")
            if not cron.every and not cron.cron_expr:
                errors.append(
                    f"cron entry {cron.name!r} must specify either 'every' or 'cron_expr'"
                )

        # Provider validation — the single ``provider`` and each of ``providers``.
        for prov in self.all_providers():
            errors.extend(prov.validate())

        return errors

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
            Permissions.from_dict(perms_raw)
            if isinstance(perms_raw, dict)
            else Permissions()
        )

        setup_raw = data.get("setup", {})
        setup = (
            SetupConfig.from_dict(setup_raw)
            if isinstance(setup_raw, dict)
            else SetupConfig()
        )

        deps_raw = data.get("dependencies", {})
        deps = (
            Dependencies.from_dict(deps_raw)
            if isinstance(deps_raw, dict)
            else Dependencies()
        )

        platform_raw = data.get("platform", {})
        platform_cfg = (
            PlatformConfig.from_dict(platform_raw)
            if isinstance(platform_raw, dict)
            else PlatformConfig()
        )

        provider_raw = data.get("provider")
        provider_cfg = (
            ProviderConfig.from_dict(provider_raw)
            if isinstance(provider_raw, dict)
            else None
        )

        providers_cfg = [
            ProviderConfig.from_dict(p)
            for p in data.get("providers", [])
            if isinstance(p, dict)
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
            mcpServers=dict(data.get("mcpServers", {})),  # noqa: N815
            crons=crons,
            ui=ui,
            backend=backend,
            permissions=permissions,
            setup=setup,
            dependencies=deps,
            platform=platform_cfg,
            provider=provider_cfg,
            providers=providers_cfg,
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
