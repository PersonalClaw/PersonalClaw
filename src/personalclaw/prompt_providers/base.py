"""Abstract base for prompt providers + the on-the-wire prompt model.

A prompt is a named, variable-bearing template (``PromptTemplate``); a
``PromptSnippet`` is a reusable fragment a prompt includes via ``{{> name}}``.
Both share the same render grammar (see ``engine.py``). ``kind`` distinguishes a
``system`` prompt (bound to a use-case, injected as the agent system prompt) from
a ``user`` prompt (invoked in chat with filled-in variables).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

VariableType = Literal["text", "textarea", "number", "boolean", "select"]
ALLOWED_VARIABLE_TYPES: tuple[VariableType, ...] = (
    "text",
    "textarea",
    "number",
    "boolean",
    "select",
)
# Legacy → canonical type names, applied on read so old records load + are
# rewritten in the new shape (data migration, not a dual-support shim). The old
# vocabulary was string/text/number/boolean/select/file_path.
#   string    → text  (single-line, the common case)
#   file_path → text  (path hint lives in the description now)
# NOTE: the token "text" is unmappable — it's valid in BOTH vocabularies but meant
# multi-line before and single-line now. Since this runs on every load, remapping
# it would corrupt newly-written single-line vars. Old multi-line "text" therefore
# degrades to single-line "text" (a widget-height change, not data loss); authors
# re-pick "textarea" if they want the tall input back.
_LEGACY_VARIABLE_TYPES: dict[str, VariableType] = {
    "string": "text",
    "file_path": "text",
}


def normalize_variable_type(raw: Any) -> VariableType:
    """Map a (possibly legacy) type name to the canonical set. Raises on unknown."""
    t = str(raw or "text").strip().lower()
    if t in ALLOWED_VARIABLE_TYPES:
        return t  # type: ignore[return-value]
    if t in _LEGACY_VARIABLE_TYPES:
        return _LEGACY_VARIABLE_TYPES[t]
    raise ValueError(f"Unknown variable type: {raw!r}")


PromptKind = Literal["system", "user"]
ALLOWED_PROMPT_KINDS: tuple[PromptKind, ...] = ("system", "user")

# A prompt's storage origin. ``bundled`` records are shipped + read-only in the UI
# (editing duplicates to ``user``); ``marketplace`` are installed, also read-only.
PromptSource = Literal["user", "bundled", "marketplace"]


@dataclass
class PromptVariable:
    """Typed parameter declaration on a prompt template or snippet."""

    name: str
    type: VariableType = "text"
    description: str = ""
    required: bool = False
    default: Any = None
    options: list[str] = field(default_factory=list)  # only meaningful when type == "select"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
        }
        if self.default is not None:
            out["default"] = self.default
        if self.options:
            out["options"] = list(self.options)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromptVariable":
        return cls(
            name=str(data["name"]).strip(),
            type=normalize_variable_type(data.get("type")),
            description=str(data.get("description") or ""),
            required=bool(data.get("required", False)),
            default=data.get("default"),
            options=[str(o) for o in (data.get("options") or [])],
        )


def _humanize(name: str) -> str:
    """A readable title from a slug (``system-chat`` → ``System Chat``)."""
    return " ".join(w.capitalize() for w in name.replace("_", "-").split("-") if w)


@dataclass
class PromptTemplate:
    """A prompt: name + content with {{var}} placeholders + {{> snippet}} includes."""

    name: str
    kind: PromptKind = "user"
    title: str = ""
    description: str = ""
    content: str = ""
    variables: list[PromptVariable] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "user"  # "user" | "bundled" | "marketplace"
    package: str = ""
    # Runnable template (#17): when non-empty, this prompt is a "campaign template" —
    # its rendered content is a runnable task, and this blob carries the loop launch
    # config (kind, agent/roster, model, intake_rigor, granularity, …). Empty for a
    # plain text-only prompt. Persisted to YAML like the rest of the record.
    launch_spec: dict[str, Any] = field(default_factory=dict)
    # Runtime-only (NOT persisted to YAML): last-modified epoch seconds, set by the
    # provider from the file mtime so the UI can sort by "recently updated".
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.title:
            self.title = _humanize(self.name)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "content": self.content,
            "variables": [v.to_dict() for v in self.variables],
            "tags": list(self.tags),
            "source": self.source,
            "package": self.package,
            "updated_at": self.updated_at,
        }
        # Only emit launch_spec when set, so plain prompts' YAML stays unchanged.
        if self.launch_spec:
            out["launch_spec"] = dict(self.launch_spec)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromptTemplate":
        name = str(data.get("name") or "").strip()
        kind = str(data.get("kind") or "").strip().lower()
        if kind not in ALLOWED_PROMPT_KINDS:
            # Migration: a record predating `kind` is a system prompt if it looks
            # like one (bundled system-* name), else a user prompt. The storage
            # layer refines this with use-case-binding knowledge; this is the
            # type-local default so the model never carries an invalid kind.
            kind = "system" if name.startswith("system-") else "user"
        return cls(
            name=name,
            kind=kind,  # type: ignore[arg-type]
            title=str(data.get("title") or "").strip(),
            description=str(data.get("description") or ""),
            content=str(data.get("content") or ""),
            variables=[
                PromptVariable.from_dict(v)
                for v in (data.get("variables") or [])
                if isinstance(v, dict) and v.get("name")
            ],
            tags=[str(t) for t in (data.get("tags") or [])],
            source=str(data.get("source") or "user"),
            package=str(data.get("package") or ""),
            launch_spec=(
                dict(data["launch_spec"]) if isinstance(data.get("launch_spec"), dict) else {}
            ),
        )


@dataclass
class PromptSnippet:
    """A reusable prompt fragment, included by prompts/snippets via {{> name}}.

    Same render grammar + variable model as a prompt; the variables a snippet
    declares surface on every prompt that transitively includes it."""

    name: str
    title: str = ""
    description: str = ""
    content: str = ""
    variables: list[PromptVariable] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "user"  # "user" | "bundled" | "marketplace"
    package: str = ""
    # Runtime-only (NOT persisted): last-modified epoch seconds (file mtime).
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.title:
            self.title = _humanize(self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "content": self.content,
            "variables": [v.to_dict() for v in self.variables],
            "tags": list(self.tags),
            "source": self.source,
            "package": self.package,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromptSnippet":
        return cls(
            name=str(data.get("name") or "").strip(),
            title=str(data.get("title") or "").strip(),
            description=str(data.get("description") or ""),
            content=str(data.get("content") or ""),
            variables=[
                PromptVariable.from_dict(v)
                for v in (data.get("variables") or [])
                if isinstance(v, dict) and v.get("name")
            ],
            tags=[str(t) for t in (data.get("tags") or [])],
            source=str(data.get("source") or "user"),
            package=str(data.get("package") or ""),
        )


class PromptRenderError(Exception):
    """Raised when a template cannot be rendered (missing required vars, bad type,
    unknown function, include depth/loop overflow, malformed block)."""


class PromptProvider(ABC):
    """Pluggable storage backend for prompt templates + snippets."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier (e.g. ``native``)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable label."""
        ...

    @abstractmethod
    def list_prompts(self) -> list[PromptTemplate]:
        """Return every prompt this provider knows about."""
        ...

    @abstractmethod
    def get_prompt(self, name: str) -> PromptTemplate | None:
        """Look up by name. Return None when missing."""
        ...

    @abstractmethod
    def create_prompt(self, prompt: PromptTemplate) -> PromptTemplate:
        """Persist a new prompt. Raises if a prompt with that name already exists."""
        ...

    @abstractmethod
    def update_prompt(self, name: str, prompt: PromptTemplate) -> PromptTemplate:
        """Replace an existing prompt's content + metadata."""
        ...

    @abstractmethod
    def delete_prompt(self, name: str) -> bool:
        """Remove a prompt. Return True when it existed and was deleted."""
        ...

    # ── snippets ──────────────────────────────────────────────────────────
    # Reusable fragments included via {{> name}}. A provider that doesn't
    # support snippets returns an empty list / None and rejects writes.

    def list_snippets(self) -> list[PromptSnippet]:
        return []

    def get_snippet(self, name: str) -> PromptSnippet | None:
        return None

    def create_snippet(self, snippet: PromptSnippet) -> PromptSnippet:
        raise PromptRenderError(f"{self.name} provider does not support snippets")

    def update_snippet(self, name: str, snippet: PromptSnippet) -> PromptSnippet:
        raise PromptRenderError(f"{self.name} provider does not support snippets")

    def delete_snippet(self, name: str) -> bool:
        return False
