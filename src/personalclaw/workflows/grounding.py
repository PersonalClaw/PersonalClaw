"""The grounding bundle — what a planner is allowed to know, read from the live system.

Generating a workflow spec is the most failure-prone step in planning, and the measured cause
is not model capability: it is that an ungrounded planner invents node kinds, provider names and
argument shapes that look plausible and do not exist. The plan's own measurement puts
first-try-valid at 0/5 ungrounded and 4/5 grounded, with silent spec misses going 3 → 0.

**Regenerated from the registries, never hand-written.** A hand-maintained reference is wrong the
first time a provider is added and nobody notices, because a stale reference fails the same way a
hallucination does — the planner emits something the validator rejects, and the error names the
spec rather than the reference. So node kinds come from `NodeKind`, providers from the action
registry, and argument shapes from `MCP_CORE_SCHEMAS` and the providers' own docstrings.

**Orient, then drill.** The bundle has an INDEX (every provider, one line each) and DETAIL
(full signatures, on request). Handing a planner every signature for sixteen providers spends
context on fifteen it will not use, and the index is what lets it choose before it reads.

**Two signature sources, joined, because neither is complete.** Measured: only some providers
document their `action_config` shape in a docstring, and `create-task` / `notify` have no
docstring at all — a docstring-only bundle would hand the planner an empty signature for exactly
the providers a plan most often reaches for. `MCP_CORE_SCHEMAS` has typed fields with required
flags for the tool-backed ones. Where both exist the typed schema wins; where neither does, the
bundle says so rather than implying the provider takes no arguments.
"""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Cap on providers detailed in one bundle. The index is always complete; DETAIL is bounded
#: because a planner that reads sixteen full signatures has spent its context before it starts.
MAX_DETAILED_PROVIDERS = 6

#: Cap on MCP tools surfaced. A user with 600 registered tools cannot have them all in a prompt,
#: and truncating silently would make the planner confident that the missing ones do not exist —
#: so the bundle states the count it dropped.
MAX_MCP_TOOLS = 40


@dataclass
class ProviderSignature:
    """One action provider as a planner needs to see it."""

    name: str
    display_name: str = ""
    summary: str = ""
    #: `(field, type, required)` triples. Empty means UNKNOWN, not "takes nothing" — see
    #: `arguments_known`.
    fields: list[tuple[str, str, bool]] = field(default_factory=list)
    #: Where the shape came from: `schema` (typed), `docstring` (parsed), or `""` (neither).
    source: str = ""
    supports_dry_run: bool = False

    @property
    def arguments_known(self) -> bool:
        """Did we actually learn this provider's arguments?

        Load-bearing distinction: a provider with no discoverable shape must be presented as
        "arguments undocumented", never as one taking no arguments. The second reads as a fact and
        produces a spec with an empty `with` block that fails at run time.
        """
        return bool(self.source)

    def index_line(self) -> str:
        """One line for the orient pass."""
        args = ""
        if self.fields:
            required = [n for n, _t, req in self.fields if req]
            if required:
                args = f" (requires: {', '.join(required)})"
            elif self.source == "source-scan":
                # NOT "all args optional" — this tier never learned requiredness, and asserting it
                # would be a claim the bundle cannot support.
                args = f" (args: {', '.join(n for n, _t, _r in self.fields[:5])})"
            else:
                args = " (all args optional)"
        elif not self.arguments_known:
            args = " (arguments undocumented — check the provider before using)"
        return f"- `{self.name}` — {self.summary or self.display_name}{args}"

    def detail_block(self) -> str:
        """The full signature, for the drill pass."""
        lines = [f"### `{self.name}` — {self.display_name or self.name}"]
        if self.summary:
            lines.append(self.summary)
        if self.fields:
            if self.source == "source-scan":
                lines.append(
                    "Arguments (under `config.with`) — read from the provider's source, so the "
                    "NAMES are reliable but which are required is not stated here:"
                )
            else:
                lines.append("Arguments (under `config.with`):")
            for fname, ftype, required in self.fields:
                mark = "required" if required else "optional"
                lines.append(f"  - `{fname}`: {ftype} ({mark})")
        elif self.arguments_known:
            lines.append("Takes no arguments.")
        else:
            lines.append(
                "Argument shape is NOT documented in this build. Do not guess it — either pick a "
                "provider whose shape is known, or leave the node out and say why."
            )
        if self.supports_dry_run:
            lines.append("Supports dry-run.")
        return "\n".join(lines)


@dataclass
class GroundingBundle:
    """Everything the planner may treat as true about this system.

    Assembled per plan rather than cached: a provider registered by an app install, or a model
    swapped in settings, changes what a valid spec looks like — and a cached bundle would keep
    planning against the previous system while reporting success.
    """

    node_kinds: list[str] = field(default_factory=list)
    container_kinds: list[str] = field(default_factory=list)
    llm_kinds: list[str] = field(default_factory=list)
    providers: list[ProviderSignature] = field(default_factory=list)
    mcp_tools: list[str] = field(default_factory=list)
    mcp_tools_dropped: int = 0
    templates: list[str] = field(default_factory=list)
    binding_roots: list[str] = field(default_factory=list)
    pipes: list[str] = field(default_factory=list)
    #: Whether the bound model can be held to a JSON schema. Decides schema-constrained emission
    #: versus prose-with-repair, and it is read from the BACKEND registries — the frontend's
    #: `capableModels` is a UI helper and does not know what the engine bound.
    structured_output: bool = False
    model_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_kinds": list(self.node_kinds),
            "container_kinds": list(self.container_kinds),
            "llm_kinds": list(self.llm_kinds),
            "providers": [
                {
                    "name": p.name,
                    "summary": p.summary,
                    "fields": [{"name": n, "type": t, "required": r} for n, t, r in p.fields],
                    "source": p.source,
                    "arguments_known": p.arguments_known,
                }
                for p in self.providers
            ],
            "mcp_tools": list(self.mcp_tools),
            "mcp_tools_dropped": self.mcp_tools_dropped,
            "templates": list(self.templates),
            "binding_roots": list(self.binding_roots),
            "pipes": list(self.pipes),
            "structured_output": self.structured_output,
            "model_notes": list(self.model_notes),
        }

    def index(self) -> str:
        """The ORIENT pass: everything, one line each, cheap enough to always include."""
        parts = [
            "## What this engine can actually run",
            "",
            f"Node kinds ({len(self.node_kinds)}, and no others): "
            + ", ".join(f"`{k}`" for k in self.node_kinds),
            "Containers (hold children, do no work themselves): "
            + ", ".join(f"`{k}`" for k in self.container_kinds),
            "Model-consuming kinds: " + ", ".join(f"`{k}`" for k in self.llm_kinds),
            "",
            "Binding roots (nothing else resolves): "
            + ", ".join(f"`{{{{{r}.*}}}}`" for r in self.binding_roots),
            "Binding pipes (a CLOSED set): " + ", ".join(f"`{p}`" for p in self.pipes),
            "",
            "### Action providers",
        ]
        parts.extend(p.index_line() for p in self.providers)
        if self.mcp_tools:
            parts.append("")
            parts.append("### MCP servers configured on this machine")
            parts.append(
                "Tool names must be discovered from the server, not guessed — reference the "
                "SERVER and say which capability you need."
            )
            parts.append(", ".join(f"`{t}`" for t in self.mcp_tools))
            if self.mcp_tools_dropped:
                # Stated, not silent: a planner told about 40 of 600 tools would otherwise
                # conclude the other 560 do not exist.
                parts.append(
                    f"({self.mcp_tools_dropped} more are registered but not listed here — ask if "
                    "you need one that is missing.)"
                )
        if self.templates:
            parts.append("")
            parts.append(
                "### Existing templates (prefer adapting one over generating from scratch)"
            )
            parts.append(", ".join(f"`{t}`" for t in self.templates))
        return "\n".join(parts)

    def detail(self, provider_names: list[str]) -> str:
        """The DRILL pass: full signatures for the providers the planner actually chose."""
        wanted = [p for p in self.providers if p.name in set(provider_names)]
        if not wanted:
            return ""
        return "\n\n".join(
            ["## Provider signatures"] + [p.detail_block() for p in wanted[:MAX_DETAILED_PROVIDERS]]
        )


# ── assembly from live registries ──


def build_bundle(*, include_mcp: bool = True) -> GroundingBundle:
    """Assemble the bundle from whatever this system actually has.

    Every source is wrapped: a bundle is an ENHANCEMENT to planning, so a registry that cannot be
    read degrades that section rather than failing the plan. The one thing it must never do is
    report a section as empty when it simply could not be read — see `mcp_tools_dropped` and
    `ProviderSignature.arguments_known` for how absence is distinguished from ignorance.
    """
    bundle = GroundingBundle()
    _add_node_taxonomy(bundle)
    _add_bindings(bundle)
    _add_providers(bundle)
    _add_templates(bundle)
    if include_mcp:
        _add_mcp_tools(bundle)
    _add_model_capabilities(bundle)
    return bundle


def _add_node_taxonomy(bundle: GroundingBundle) -> None:
    from personalclaw.workflows.models import CONTAINER_KINDS, LLM_KINDS, NodeKind

    bundle.node_kinds = [k.value for k in NodeKind]
    bundle.container_kinds = sorted(k.value for k in CONTAINER_KINDS)
    bundle.llm_kinds = sorted(k.value for k in LLM_KINDS)


def _add_bindings(bundle: GroundingBundle) -> None:
    """The binding roots and pipes, read from the resolver rather than listed by hand.

    Session 31 shipped five templates referencing `{{defaults.*}}`, which is not a root — the
    validator caught it, but only after the specs were written. Reading the real roots is what
    stops a planner making the same mistake at generation time.
    """
    from personalclaw.workflows.bindings import PIPES

    # The roots `as_root()` can produce. Kept as a literal list beside the module that defines
    # them rather than introspected: `as_root` builds them conditionally, so an empty context
    # would report that half of them do not exist.
    bundle.binding_roots = [
        "inputs",
        "nodes",
        "item",
        "iter",
        "last",
        "siblings",
        "previous",
        "brief",
    ]
    bundle.pipes = sorted(PIPES)


def _add_providers(bundle: GroundingBundle) -> None:
    try:
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            _providers,
        )

        _ensure_default_providers_registered()
        names = sorted(_providers)
    except Exception:
        logger.debug("action registry unreadable — bundle ships without providers", exc_info=True)
        return

    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    for name in names:
        if name not in ALLOWED_HOOK_PROVIDERS:
            # A provider the engine cannot dispatch must not be offered. Registered-but-not-
            # allowlisted is a real state, and a spec targeting one validates, saves, and fails
            # at run time — the exact failure the allowlist exists to prevent.
            continue
        try:
            bundle.providers.append(_signature_for(name, _providers[name]))
        except Exception:
            logger.debug("could not read signature for %s", name, exc_info=True)


def _signature_for(name: str, provider: Any) -> ProviderSignature:
    doc = inspect.getdoc(provider.__class__) or ""
    sig = ProviderSignature(
        name=name,
        display_name=str(getattr(provider, "display_name", "") or ""),
        summary=_first_sentence(doc),
        supports_dry_run=bool(getattr(provider, "supports_dry_run", False)),
    )

    typed = _typed_fields(name)
    if typed:
        sig.fields, sig.source = typed, "schema"
        return sig

    parsed = _docstring_fields(doc)
    if parsed:
        sig.fields, sig.source = parsed, "docstring"
        return sig

    # Third tier: read what the provider's own code actually pulls out of its config. Measured,
    # NINE of sixteen providers had neither a typed schema nor a documented shape — including
    # `bash`, `create-task` and `run-workflow`, the ones a generated plan reaches for most. A
    # bundle that stopped at two tiers would tell the planner those providers exist and nothing
    # about calling them, which is the ungrounded failure with extra steps.
    #
    # This tier is the most RELIABLE of the three about names (it is what the code reads) and the
    # least reliable about requiredness, so every field it yields is marked optional and the
    # source is labelled so a reader knows the difference.
    scraped = _source_fields(provider)
    if scraped:
        sig.fields, sig.source = scraped, "source-scan"
    return sig


def _typed_fields(name: str) -> list[tuple[str, str, bool]]:
    """Fields from `MCP_CORE_SCHEMAS`, which carries real types and required flags.

    Preferred over the docstring when both exist: the schema is what the validator enforces, so a
    docstring that has drifted from it would teach the planner the wrong shape.
    """
    try:
        from personalclaw.validation import MCP_CORE_SCHEMAS

        schema = MCP_CORE_SCHEMAS.get(name)
    except Exception:
        return []
    if schema is None:
        return []
    out: list[tuple[str, str, bool]] = []
    for spec in getattr(schema, "fields", []) or []:
        ftype = getattr(getattr(spec, "type", None), "__name__", "any")
        out.append((str(getattr(spec, "name", "")), ftype, bool(getattr(spec, "required", False))))
    return [(n, t, r) for n, t, r in out if n]


#: `"key": value,  # comment` inside a docstring's ``action_config`` block. Deliberately narrow:
#: a loose pattern would scrape prose and hand the planner invented field names, which is worse
#: than handing it none.
_DOC_FIELD_RE = re.compile(r"^\s*[\"'](?P<name>[a-z_]+)[\"']\s*:\s*(?P<value>[^,#\n]+)")


def _docstring_fields(doc: str) -> list[tuple[str, str, bool]]:
    """Parse an `action_config` shape out of a provider docstring.

    Only inside the block that follows an `action_config` mention — scanning the whole docstring
    picked up example JSON from unrelated prose in a measurement run.
    """
    if "action_config" not in doc:
        return []
    block = doc.split("action_config", 1)[1]
    out: list[tuple[str, str, bool]] = []
    for line in block.splitlines():
        match = _DOC_FIELD_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        raw = match.group("value").strip()
        required = "# required" in line.lower() or "required" in line.lower()
        out.append((name, _guess_type(raw), required))
    return out


#: `cfg.get("field")` / `action_config.get("field")` — what a provider actually reads.
#: Matches BOTH idioms: `cfg.get("x")` and `(action_config or {}).get("x")`. The second form was
#: missed at first, and `run-workflow` — which reads `workflow`, `inputs`, `idempotency_key` and
#: `dry_run` that way — was reported as taking NO arguments. A pattern miss that produces a
#: confident "takes no arguments" is worse than one that produces silence.
_SOURCE_FIELD_RE = re.compile(
    r"(?:action_config|cfg|config)\s*(?:or\s*\{\})?\s*\)?\s*\.get\("
    r"\s*[\"\']([a-z_][a-z0-9_]*)[\"\']"
)

#: Config keys every provider handles generically rather than as its own argument. Surfacing them
#: as arguments would have the planner set engine plumbing in a `with` block.
_GENERIC_KEYS = frozenset({"with", "config", "context", "payload", "provider"})


def _source_fields(provider: Any) -> list[tuple[str, str, bool]]:
    """Argument names scraped from the provider's own source.

    Requiredness is NOT inferred: a `cfg.get` tells you the name and nothing about whether the
    provider errors without it, and guessing would produce a confident wrong contract. Everything
    here is marked optional and the caller labels the source `source-scan` so the distinction
    survives into the prompt.
    """
    try:
        source = inspect.getsource(provider.__class__)
    except Exception:
        return []
    names = sorted(set(_SOURCE_FIELD_RE.findall(source)) - _GENERIC_KEYS)
    return [(n, "any", False) for n in names]


def _guess_type(raw: str) -> str:
    raw = raw.strip().rstrip(",")
    if raw.startswith("["):
        return "list"
    if raw.startswith("{"):
        return "object"
    if raw in ("true", "false"):
        return "bool"
    if raw.replace("_", "").replace(".", "").isdigit():
        return "number"
    return "string"


def _add_templates(bundle: GroundingBundle) -> None:
    try:
        from personalclaw.workflows import bundled_defs

        bundle.templates = list(bundled_defs.template_names())
    except Exception:
        logger.debug("template names unreadable", exc_info=True)


def _add_mcp_tools(bundle: GroundingBundle) -> None:
    """The user's own MCP tools, as first-class options.

    Without these a planner can only reach the built-in providers, and a spec that needed the
    user's Slack or Jira tool gets a hallucinated provider name instead. Best-effort: an
    unreachable MCP store degrades to no tools rather than failing the plan.
    """
    import json

    from personalclaw.config.loader import config_dir

    path = config_dir() / "mcp.json"
    if not path.is_file():
        # No file means no MCP servers configured — a real answer, and distinct from an
        # unreadable one. `_add_mcp_tools` never invents a note here because "you have no MCP
        # tools" is not something the planner needs told.
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("mcp.json unreadable", exc_info=True)
        bundle.model_notes.append("mcp.json present but unreadable — MCP tools omitted")
        return

    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict):
        return
    # SERVER names, not tool names: enumerating tools requires connecting to each server, which is
    # async and can hang, and the planning path must not block on a user's remote MCP endpoint.
    # A server name plus "ask for its tools" is honest; a hallucinated tool signature is not.
    names = sorted(str(k) for k in servers)
    bundle.mcp_tools = names[:MAX_MCP_TOOLS]
    bundle.mcp_tools_dropped = max(0, len(names) - MAX_MCP_TOOLS)


def _add_model_capabilities(bundle: GroundingBundle) -> None:
    """Can the bound model be held to a JSON schema?

    Read from the BACKEND capability registries. The plan is explicit that the frontend's
    `capableModels` must not be the source: it is a settings-UI helper that does not know which
    model the engine actually bound for a use case.
    """
    try:
        from personalclaw.llm.capabilities import StructuredOutput
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
    except Exception:
        logger.debug("llm registry unavailable", exc_info=True)
        bundle.model_notes.append("provider registry unreadable — schema-constrained emission off")
        return

    # Every registered provider type, because the engine binds per USE CASE and the bundle does not
    # know which type backs `orchestration` in this config. Any type advertising schema support
    # means the option exists; the dispatcher picks the actual model.
    modes: list[str] = []
    for type_ in sorted(getattr(registry, "_capabilities", {}) or {}):
        try:
            cap = registry.capability_of(type_)
        except Exception:
            continue
        mode = getattr(cap, "structured_output", StructuredOutput.NONE)
        value = getattr(mode, "value", str(mode))
        modes.append(f"{type_}={value}")
        if mode != StructuredOutput.NONE:
            bundle.structured_output = True

    if not modes:
        # UNKNOWN, not "no". An unbootstrapped process has no registered providers, and reporting
        # that as "this model cannot do structured output" would send every plan down the
        # prose-with-repair path on a model that handles schemas fine.
        bundle.model_notes.append(
            "no provider types registered (process not bootstrapped) — structured output UNKNOWN, "
            "treat schema-constrained emission as unavailable rather than unsupported"
        )
        return
    bundle.model_notes.append("structured output by provider type: " + ", ".join(modes))


def _first_sentence(doc: str) -> str:
    text = " ".join((doc or "").split())
    if not text:
        return ""
    for end in (". ", " — "):
        if end in text:
            return text.split(end, 1)[0].strip().rstrip(".")
    return text[:140].rstrip(".")
