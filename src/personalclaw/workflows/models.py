"""Canonical workflow entity — a stateless, scoped, ordered SOP definition."""

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class WorkflowScope(str, enum.Enum):
    """Eligibility gate for a workflow. See E4 plan §0.1."""

    GLOBAL = "global"
    WORKSPACE = "workspace"  # scope_ref = absolute working-directory path (cwd)
    AGENT = "agent"  # scope_ref = agent binding id (the workflow scopes itself to the agent)
    SESSION = "session"  # scope_ref = session_key


@dataclass
class WorkflowStep:
    """One ordered checklist line. Pure definition — no status.

    A step is either INLINE (a ``title`` + optional ``instruction``) or a
    REFERENCE to another workflow (``ref`` = that workflow's id, no title needed),
    so SOPs compose. A valid step has ``ref`` XOR a non-empty ``title``. Refs are
    by id and resolved live (not snapshot); the composition graph must stay
    acyclic (enforced server-side on write).
    """

    id: str  # stable within the workflow, e.g. "s1" (assigned positionally)
    title: str = ""  # the checklist line (imperative: "Run the tests")
    instruction: str = ""  # optional how-to detail injected with the step
    ref: str = ""  # workflow id this step runs (composition); empty for inline steps

    def is_ref(self) -> bool:
        return bool(self.ref)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowStep":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            instruction=d.get("instruction", ""),
            ref=d.get("ref", ""),
        )


@dataclass
class Workflow:
    id: str
    name: str  # ^[a-z0-9][a-z0-9-]{0,62}$ (reuses the skill name rule)
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    scope: WorkflowScope = WorkflowScope.GLOBAL
    scope_ref: str = ""  # cwd path | session_key | "" (global/agent)
    match_text: str = ""  # natural-language intent this SOP answers
    match_embedding: list[float] = field(default_factory=list)  # cached vector
    embedding_model: str = ""  # "provider:model" the embedding was computed with
    provider: str = ""  # which provider owns it (set on read)
    enabled: bool = True
    version: str = "1"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self, *, include_embedding: bool = True) -> dict[str, Any]:
        d = asdict(self)
        d["scope"] = self.scope.value
        d["steps"] = [s.to_dict() for s in self.steps]
        if not include_embedding:
            d.pop("match_embedding", None)  # API responses omit the vector
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Workflow":
        try:
            scope = WorkflowScope(d.get("scope", "global"))
        except ValueError:
            scope = WorkflowScope.GLOBAL
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            description=d.get("description", ""),
            steps=[WorkflowStep.from_dict(s) for s in d.get("steps", [])],
            tags=d.get("tags", []),
            scope=scope,
            scope_ref=d.get("scope_ref", ""),
            match_text=d.get("match_text", ""),
            match_embedding=d.get("match_embedding", []),
            embedding_model=d.get("embedding_model", ""),
            provider=d.get("provider", ""),
            enabled=d.get("enabled", True),
            version=d.get("version", "1"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
