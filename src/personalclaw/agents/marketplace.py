"""Agent marketplace — abstract base, local filesystem implementation, and registry.

Follows the same extensibility pattern as ``providers/registry.py``:
``AgentMarketplaceRegistry`` holds named ``AgentMarketplace`` implementations.
``LocalAgentMarketplace`` stores agent definitions as JSON files under
``~/.personalclaw/agents/<name>/agent.json`` and is registered as ``"local"`` on import.

Additional marketplaces (e.g. a remote catalog, a Git-backed store) register the
same ABC and appear transparently in the dashboard and CLI.
"""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd
        return _cd()
    except Exception:
        from pathlib import Path as _P
        return _P.home() / ".personalclaw"

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MAX_DESCRIPTION = 1024
_MAX_SYSTEM_PROMPT = 32_000


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class AgentDefinition:
    """A user-authored agent definition stored in the local marketplace.

    ``name`` is the stable identifier (``[a-z0-9-]``, max 64 chars).
    ``provider_entry`` optionally names a registry ``ProviderEntry``; when
    omitted the chat runner uses the configured default provider.
    ``skills`` lists skill names available to this agent (matched against
    ``_all_skill_paths()`` at session start).
    ``mcp_servers`` is a free-form dict written into the agent's MCP config.
    """

    name: str
    description: str = ""
    model: str = ""
    system_prompt: str = ""
    # Voice/soul layer (#42): WHO the agent is — tone, opinions, bluntness, persona
    # — kept SEPARATE from system_prompt (the operating rules / what it does), and
    # injected high-priority so personality survives long operating-rule prompts.
    voice: str = ""
    skills: list[str] = field(default_factory=list)
    provider_entry: str = ""
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    source: str = "local"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Agent-runtime axis: "native" (in-process loop, governed by Settings →
    # Models) or "acp:<cli>" (external CLI). Distinct from ``provider_entry``
    # (a ModelProvider entry name). Empty inherits the global ``agent.provider``
    # default — same semantics as AgentProfile.provider, so a marketplace-
    # imported agent is a first-class peer of a config-defined one.
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentDefinition":
        return cls(
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            model=str(d.get("model", "")),
            system_prompt=str(d.get("system_prompt", "")),
            # Voice layer (#42) — MUST be read here (the S6 loader-allowlist gotcha)
            # or it's silently dropped on every reload/round-trip.
            voice=str(d.get("voice", "")),
            skills=list(d.get("skills") or []),
            provider_entry=str(d.get("provider_entry", "")),
            mcp_servers=dict(d.get("mcp_servers") or {}),
            source=str(d.get("source", "local")),
            created_at=float(d.get("created_at") or time.time()),
            updated_at=float(d.get("updated_at") or time.time()),
            # Agent-runtime axis ("native" | "acp:<cli>"). MUST be read here or
            # an agent bound to an ACP runtime (claude-code / codex) silently
            # loses that binding on every load/round-trip and falls back to the
            # global default — so the connected ACP provider is never used.
            provider=str(d.get("provider", "")),
        )

    def validate(self) -> list[str]:
        """Return a list of validation error strings; empty means valid."""
        errors: list[str] = []
        if not _NAME_RE.match(self.name):
            errors.append(
                "name must match ^[a-z0-9][a-z0-9-]{0,62}$ "
                f"(got {self.name!r})"
            )
        if len(self.description) > _MAX_DESCRIPTION:
            errors.append(f"description exceeds {_MAX_DESCRIPTION} chars")
        if len(self.system_prompt) > _MAX_SYSTEM_PROMPT:
            errors.append(f"system_prompt exceeds {_MAX_SYSTEM_PROMPT} chars")
        for s in self.skills:
            if not isinstance(s, str) or ".." in s or "/" in s:
                errors.append(f"invalid skill name: {s!r}")
        return errors


# ── Abstract base ─────────────────────────────────────────────────────────────


class AgentMarketplace(ABC):
    """Abstract agent marketplace.  Implementations provide CRUD + list."""

    @abstractmethod
    def list(self) -> list[AgentDefinition]:
        """Return all agent definitions in this marketplace."""

    @abstractmethod
    def get(self, name: str) -> AgentDefinition | None:
        """Return the named agent definition, or ``None`` if not found."""

    @abstractmethod
    def create(self, defn: AgentDefinition) -> AgentDefinition:
        """Persist *defn* and return it (with any server-set fields populated)."""

    @abstractmethod
    def update(self, name: str, patch: dict[str, Any]) -> AgentDefinition:
        """Apply *patch* fields to the named agent and return the updated definition."""

    @abstractmethod
    def delete(self, name: str) -> None:
        """Delete the named agent.  Raises ``KeyError`` if not found."""

    @property
    def marketplace_type(self) -> str:
        """Short identifier for this marketplace type (e.g. ``'local'``)."""
        return "unknown"


# ── Local filesystem implementation ───────────────────────────────────────────


class LocalAgentMarketplace(AgentMarketplace):
    """Stores agent definitions as JSON under ``~/.personalclaw/agents/<name>/agent.json``.

    Each agent lives in its own directory so future tooling can co-locate
    skill files, prompt assets, or version history alongside ``agent.json``
    without touching the parent directory.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or (_path_home_pclaw() / "agents")

    @property
    def marketplace_type(self) -> str:
        return "local"

    def _agent_path(self, name: str) -> Path:
        return self._base / name / "agent.json"

    def _ensure_base(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)

    # ── Read ──────────────────────────────────────────────────────────────────

    def list(self) -> list[AgentDefinition]:
        self._ensure_base()
        agents: list[AgentDefinition] = []
        for entry in sorted(self._base.iterdir()):
            if not entry.is_dir():
                continue
            agent_file = entry / "agent.json"
            if not agent_file.is_file():
                continue
            try:
                data = json.loads(agent_file.read_text(encoding="utf-8"))
                agents.append(AgentDefinition.from_dict(data))
            except Exception as exc:
                logger.warning("Skipping malformed agent %s: %s", entry.name, exc)
        return agents

    def get(self, name: str) -> AgentDefinition | None:
        if not _NAME_RE.match(name):
            return None
        path = self._agent_path(name)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AgentDefinition.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to read agent %s: %s", name, exc)
            return None

    # ── Write ─────────────────────────────────────────────────────────────────

    def create(self, defn: AgentDefinition) -> AgentDefinition:
        errors = defn.validate()
        if errors:
            raise ValueError(f"Invalid agent definition: {'; '.join(errors)}")
        self._ensure_base()
        agent_dir = self._base / defn.name
        if agent_dir.exists():
            raise FileExistsError(f"Agent '{defn.name}' already exists")
        agent_dir.mkdir(parents=True, exist_ok=False)
        path = agent_dir / "agent.json"
        now = time.time()
        defn.created_at = now
        defn.updated_at = now
        self._write(path, defn)
        logger.info("Created local agent: %s", defn.name)
        return defn

    def update(self, name: str, patch: dict[str, Any]) -> AgentDefinition:
        existing = self.get(name)
        if existing is None:
            raise KeyError(f"Agent '{name}' not found")
        _UPDATABLE = {
            "description", "model", "system_prompt", "voice",
            "skills", "provider_entry", "mcp_servers",
            # Agent-runtime axis — must be updatable so an agent can be (re)bound
            # to an ACP runtime ("acp:<cli>") or back to "native".
            "provider",
        }
        for key, value in patch.items():
            if key not in _UPDATABLE:
                continue
            if key == "skills":
                existing.skills = [str(s) for s in (value or [])]
            elif key == "mcp_servers":
                existing.mcp_servers = dict(value or {})
            else:
                setattr(existing, key, str(value) if value is not None else "")
        existing.updated_at = time.time()
        errors = existing.validate()
        if errors:
            raise ValueError(f"Invalid agent definition: {'; '.join(errors)}")
        self._write(self._agent_path(name), existing)
        logger.info("Updated local agent: %s", name)
        return existing

    def delete(self, name: str) -> None:
        if not _NAME_RE.match(name):
            raise KeyError(f"Agent '{name}' not found")
        agent_dir = self._base / name
        if not agent_dir.is_dir():
            raise KeyError(f"Agent '{name}' not found")
        import shutil
        shutil.rmtree(agent_dir)
        logger.info("Deleted local agent: %s", name)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _write(path: Path, defn: AgentDefinition) -> None:
        from personalclaw.atomic_write import atomic_write

        data = json.dumps(defn.to_dict(), indent=2, ensure_ascii=False)
        atomic_write(path, data)


# ── Registry ──────────────────────────────────────────────────────────────────


class AgentMarketplaceRegistry:
    """Holds named ``AgentMarketplace`` implementations.

    Usage::

        registry = get_default_agent_registry()
        registry.register("my-store", MyMarketplace())
        marketplace = registry.get("my-store")
    """

    def __init__(self) -> None:
        self._marketplaces: dict[str, AgentMarketplace] = {}

    def register(self, name: str, marketplace: AgentMarketplace) -> None:
        if name in self._marketplaces:
            logger.debug("AgentMarketplaceRegistry: overwriting %r", name)
        self._marketplaces[name] = marketplace

    def get(self, name: str) -> AgentMarketplace:
        mp = self._marketplaces.get(name)
        if mp is None:
            raise KeyError(f"No agent marketplace registered as {name!r}")
        return mp

    def names(self) -> list[str]:
        return sorted(self._marketplaces)

    def info(self) -> list[dict[str, str]]:
        return [
            {"name": n, "type": mp.marketplace_type}
            for n, mp in sorted(self._marketplaces.items())
        ]


_DEFAULT_REGISTRY: AgentMarketplaceRegistry | None = None


def get_default_agent_registry() -> AgentMarketplaceRegistry:
    """Return the process-global ``AgentMarketplaceRegistry``, creating it on first call."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = AgentMarketplaceRegistry()
    return _DEFAULT_REGISTRY


# ── Auto-register local marketplace on import ─────────────────────────────────

get_default_agent_registry().register("local", LocalAgentMarketplace())

def create_provider(config=None):
    """Extension factory for native agent provider."""
    return None  # Agent provider uses config-based definitions, not an instance

