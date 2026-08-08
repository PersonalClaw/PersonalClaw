"""Pack export core — dependency-closure walker + two-layer redaction (AGENT-PACKS §2.1-2.2).

``build_pack(seeds)`` writes a ``schema_version=1`` ``.pclaw`` ZIP; ``preview_pack(seeds)``
returns the same plan WITHOUT writing a byte, so the export surface can render a
look-before-write tree (§2.1: "rendered as a preview tree before writing anything").

The closure walker (§2.1) starts from user-selected seeds (``"kind:id"`` refs) and follows
only DECLARED references — each edge is a real, greppable reference in today's stores
(agent→skill via ``AgentDefinition.skills``; template→agent via a stage node's
``config.agent``; template→template via a subworkflow node's ``config.ref``). An edge that
cannot be resolved is NOT dropped: it demotes to a ``requirements`` row so the pack names
what it needs but could not include, and the recipient satisfies or substitutes it.

Redaction is TWO independent layers, both fail-closed:

* **Structural** (:mod:`packs.deny`): every candidate read passes ``deny.is_denied`` before
  the file is opened, so ``.env`` / ``.local_secret`` / ``memory.db`` / ``knowledge.db`` /
  ``sessions/`` etc. are NEVER read into a pack — not merely dropped after reading.
* **Content**: every component's SHIPPED bytes are scanned with ``security.redact`` AND
  ``guardrails.scan_outbound``; a credential-bearing component is BLOCKED (recorded, never
  shipped) rather than redacted-and-shipped — a mangled secret is still a leak.

``build_pack`` refuses (raises :class:`PackSecretBlocked`) if the closure contains a blocked
component: a pack that silently omitted a component the user asked for would be worse than a
loud failure. ``preview_pack`` never raises — it reports the blocked rows so the user fixes
or excludes the component before writing.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from personalclaw.config.loader import config_dir
from personalclaw.packs import deny

logger = logging.getLogger(__name__)

#: The pack format version. Integer, present from the first export — a manifest with no
#: version is one a later reader has to guess the shape of (AGENT-PACKS §1, .ovsvoice rule).
SCHEMA_VERSION = 1

#: The component kinds Session 1 walks. Apps/triggers/connectors are later AP atoms; a seed
#: naming an unknown kind resolves to nothing and becomes a requirements row.
_KINDS = ("skill", "template", "prompt", "agent")

#: A component id: nested-skill slugs use ``/`` (``utils/tiny-url``), so ``/`` is allowed,
#: but ``..`` / backslash / a leading dot are refused — the id builds a filesystem path, so
#: this is the first zip-slip / traversal guard (the deny check is the second).
_SAFE_ID_SEG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


# ── public data model ─────────────────────────────────────────────────────────


@dataclass
class PackComponent:
    """One resolved component that WILL ship, with the edges it declared."""

    kind: str
    id: str
    path: str  # pack-relative, e.g. "skills/foo/SKILL.md"
    sha256: str
    depends_on: list[str] = field(default_factory=list)  # ["agent:bar", ...]

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "path": self.path,
            "sha256": self.sha256,
            "depends_on": list(self.depends_on),
        }


@dataclass
class Requirement:
    """A named thing the pack needs but could not include (§2.1).

    An unresolvable edge lands here rather than being silently dropped — a pack that
    forgot what it depends on produces an import that fails on first run for a reason
    nobody can name.
    """

    # skill|template|agent|prompt for an unresolved component; later AP atoms add
    # credential|connector|app once those edges exist.
    kind: str
    id: str
    description: str
    required: bool = True
    env_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "description": self.description,
            "required": self.required,
            "env_hint": self.env_hint,
        }


@dataclass
class BlockedComponent:
    """A component the content layer refused because its shipped text carried a secret."""

    kind: str
    id: str
    reason: str
    categories: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "reason": self.reason,
            "categories": list(self.categories),
        }


@dataclass
class PackPreview:
    """The full export plan, rendered BEFORE any write (§2.1 look-before-write).

    ``payloads`` maps each shipping component's pack-relative path to its exact bytes, so
    :func:`build_pack` writes precisely what the preview showed — the tree and the archive
    can never disagree.
    """

    seeds: list[str]
    name: str
    version: str
    components: list[PackComponent] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    blocked: list[BlockedComponent] = field(default_factory=list)
    payloads: dict[str, bytes] = field(default_factory=dict)

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocked)

    def tree(self) -> str:
        """A human-readable closure tree for the export UI (the preview surface)."""
        lines = [f"pack: {self.name} v{self.version}  (seeds: {', '.join(self.seeds) or 'none'})"]
        lines.append(f"├─ components ({len(self.components)})")
        for comp in self.components:
            lines.append(f"│  ├─ {comp.ref} → {comp.path}")
            for dep in comp.depends_on:
                lines.append(f"│  │     depends_on: {dep}")
        lines.append(f"├─ requirements ({len(self.requirements)})")
        for req in self.requirements:
            lines.append(f"│  ├─ {req.kind}:{req.id} — {req.description}")
        lines.append(f"└─ blocked ({len(self.blocked)})")
        for blk in self.blocked:
            cats = f" [{', '.join(blk.categories)}]" if blk.categories else ""
            lines.append(f"   ├─ {blk.ref} — {blk.reason}{cats}")
        return "\n".join(lines)


class PackSecretBlocked(Exception):
    """Raised by :func:`build_pack` when the closure contains a credential-bearing component.

    Carries the blocked rows so the caller can tell the user exactly which component to fix
    or exclude. A blocked component is never written — the pack build refuses rather than
    ship a pack silently missing a requested component.
    """

    def __init__(self, blocked: list[BlockedComponent]):
        self.blocked = blocked
        refs = ", ".join(f"{b.ref} ({b.reason})" for b in blocked)
        super().__init__(f"pack build refused — credential-bearing component(s): {refs}")


# ── the closure walker ──────────────────────────────────────────────────────


@dataclass
class _Resolved:
    """One store read: what ships + the edges it declares. Internal to the walker."""

    kind: str
    id: str
    pack_path: str
    pack_bytes: bytes
    edges: list[str]


def _config_dir() -> Path:
    return config_dir()


def _safe_id(cid: str) -> bool:
    """A component id that cannot build a traversing path (segments validated, no ``..``)."""
    if not cid or ".." in cid or "\\" in cid:
        return False
    return all(_SAFE_ID_SEG.match(seg) for seg in cid.split("/"))


def _read_denied_safe(home: Path, rel: str) -> str | None:
    """Read a home-relative text file ONLY if the structural layer permits it.

    The single choke point every store reader routes through: ``deny.is_denied`` runs
    BEFORE the open, so a denied path is never read. Returns None when denied, missing,
    or unreadable — a pack build treats all three the same (the component is simply not
    present to include).
    """
    if deny.is_denied(rel):
        # A component store should never resolve to a denied path; if it does, that is the
        # structural guarantee doing its job — refuse the read, don't "just this once".
        logger.debug("pack: refusing denied path %s", rel)
        return None
    path = home / rel
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _iter_nodes(node) -> list:
    """Flatten a WorkflowDef node tree into every node, for edge extraction."""
    out = [node]
    for child in node.children or []:
        out.extend(_iter_nodes(child))
    if node.body is not None:
        out.extend(_iter_nodes(node.body))
    for case in (node.cases or {}).values():
        out.extend(_iter_nodes(case))
    if node.default_case is not None:
        out.extend(_iter_nodes(node.default_case))
    return out


def _resolve_skill(home: Path, cid: str) -> _Resolved | None:
    text = _read_denied_safe(home, f"skills/{cid}/SKILL.md")
    if text is None:
        return None
    # SKILL.md MCP-tool mentions are advisory (§2.1) — a later AP atom demotes missing ones
    # to connector requirements; in Session 1 a skill is a closure LEAF.
    return _Resolved("skill", cid, f"skills/{cid}/SKILL.md", text.encode("utf-8"), [])


def _resolve_template(home: Path, cid: str) -> _Resolved | None:
    text = _read_denied_safe(home, f"workflows/defs/{cid}/workflow.json")
    if text is None:
        return None
    edges: list[str] = []
    try:
        from personalclaw.workflows.models import NodeKind, WorkflowDef

        wf = WorkflowDef.from_dict(json.loads(text))
        for node in _iter_nodes(wf.root):
            cfg = node.config or {}
            # template → agent slug: a stage node names its subagent persona (WORK-R16).
            agent = str(cfg.get("agent", "") or "").strip()
            if node.kind == NodeKind.STAGE and agent:
                edges.append(f"agent:{agent}")
            # template → skill: a stage node's declared skill deps (post-flywheel shape).
            for sk in cfg.get("skills") or []:
                if isinstance(sk, str) and sk.strip():
                    edges.append(f"skill:{sk.strip()}")
            # template → template: a subworkflow node references a def by name (`name@version`).
            if node.kind == NodeKind.SUBWORKFLOW:
                ref = str(cfg.get("ref", "") or "").strip()
                if ref:
                    edges.append(f"template:{ref.split('@', 1)[0]}")
    except Exception:  # noqa: BLE001 — a template whose spec won't parse still SHIPS as bytes;
        # we just can't read edges out of it, which is a smaller failure than refusing it.
        logger.debug("pack: could not extract edges from template %s", cid, exc_info=True)
    # De-dup while preserving order, so the manifest's depends_on is stable.
    edges = list(dict.fromkeys(edges))
    return _Resolved("template", cid, f"templates/{cid}.json", text.encode("utf-8"), edges)


def _resolve_prompt(home: Path, cid: str) -> _Resolved | None:
    text = _read_denied_safe(home, f"prompts/{cid}.yaml")
    if text is None:
        return None
    return _Resolved("prompt", cid, f"prompts/{cid}.yaml", text.encode("utf-8"), [])


def _resolve_agent(home: Path, cid: str) -> _Resolved | None:
    text = _read_denied_safe(home, f"agents/{cid}/agent.json")
    if text is None:
        return None
    from personalclaw.agents.marketplace import AgentDefinition

    try:
        defn = AgentDefinition.from_dict(json.loads(text))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    # Ships as persona markdown (§1: agents/<slug>.md), the shape an importer maps back to
    # an AgentDefinition. Scanning THIS rendered body is scanning exactly what leaves.
    body = _render_agent_markdown(defn)
    edges = [f"skill:{sk}" for sk in defn.skills if isinstance(sk, str) and sk.strip()]
    return _Resolved("agent", cid, f"agents/{cid}.md", body.encode("utf-8"), edges)


def _render_agent_markdown(defn) -> str:
    """Render an AgentDefinition as persona markdown (frontmatter + body).

    Credentials never live in these fields structurally, but the content layer scans this
    rendered output regardless — the SHIPPED bytes, not the source object.
    """
    lines = ["---", f"name: {defn.name}"]
    if defn.description:
        lines.append(f"description: {defn.description}")
    if defn.model:
        lines.append(f"model: {defn.model}")
    if defn.skills:
        lines.append("skills:")
        lines.extend(f"  - {sk}" for sk in defn.skills)
    lines.append("---")
    parts = [defn.system_prompt.strip()] if defn.system_prompt.strip() else []
    if defn.voice.strip():
        parts.append(defn.voice.strip())
    return "\n".join(lines) + "\n\n" + "\n\n".join(parts) + "\n"


_RESOLVERS = {
    "skill": _resolve_skill,
    "template": _resolve_template,
    "prompt": _resolve_prompt,
    "agent": _resolve_agent,
}


def _scan_component(pack_bytes: bytes) -> tuple[str, tuple[str, ...]]:
    """The CONTENT layer — two independent secret detectors over the SHIPPED bytes.

    Returns ``(reason, categories)`` where a non-empty reason means BLOCK. Both detectors
    fail closed and are secret-specific (a pack may legitimately mention an email, so PII
    alone does not block — the contract is "credential-bearing", §2.2):

    * ``security.redact`` — if redacting the text CHANGES it, a credential/exfil-URL pattern
      matched. That is the same redactor every output path already uses.
    * ``guardrails.scan_outbound(mode="block")`` — flags ``credential`` / ``exfil_url``
      categories using the SAME shared rules the model-call seam uses (not a fork).

    A hit blocks the component rather than shipping the redacted text: a redacted secret is
    a mangled secret, and the audit trail would falsely read "handled".
    """
    from personalclaw.guardrails.scan import scan_outbound
    from personalclaw.security import redact

    try:
        text = pack_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # A component that is not valid UTF-8 text is not a §1 component shape — refuse it
        # rather than scan garbage (fail closed).
        return "component is not valid UTF-8 text", ()

    if redact(text) != text:
        return "credential/exfil pattern detected (security.redact)", ("credential",)

    result = scan_outbound(text, mode="block")
    secret_cats = tuple(c for c in result.categories if c in ("credential", "exfil_url"))
    if secret_cats:
        return "credential/exfil pattern detected (guardrails scan)", secret_cats
    return "", ()


def _req_kind(ref: str) -> str:
    kind = ref.split(":", 1)[0]
    return kind if kind in _KINDS else "component"


def _walk_closure(
    seeds: list[str], home: Path
) -> tuple[list[PackComponent], list[Requirement], list[BlockedComponent], dict[str, bytes]]:
    """BFS the declared-reference closure from ``seeds``.

    A seed or edge that resolves and passes the content layer becomes a shipping component;
    one that cannot be resolved becomes a requirements row (recorded, not dropped); one whose
    shipped text carries a secret becomes a blocked row (recorded, never shipped). Each
    accepted component's declared edges are enqueued, so a multi-hop reference (template →
    agent → skill) is followed transitively.
    """
    components: list[PackComponent] = []
    requirements: list[Requirement] = []
    blocked: list[BlockedComponent] = []
    payloads: dict[str, bytes] = {}

    queue: list[str] = list(seeds)
    seen: set[str] = set()
    req_seen: set[str] = set()

    while queue:
        ref = queue.pop(0)
        if ref in seen:
            continue
        seen.add(ref)

        kind, _, cid = ref.partition(":")
        resolver = _RESOLVERS.get(kind)
        if resolver is None or not cid or not _safe_id(cid):
            if ref not in req_seen:
                req_seen.add(ref)
                requirements.append(
                    Requirement(
                        kind=_req_kind(ref),
                        id=cid or ref,
                        description="referenced but not a resolvable component "
                        "(satisfy or substitute)",
                    )
                )
            continue

        resolved = resolver(home, cid)
        if resolved is None:
            if ref not in req_seen:
                req_seen.add(ref)
                requirements.append(
                    Requirement(
                        kind=kind,
                        id=cid,
                        description="referenced but not present in this home "
                        "(satisfy or substitute)",
                    )
                )
            continue

        reason, categories = _scan_component(resolved.pack_bytes)
        if reason:
            blocked.append(BlockedComponent(kind, cid, reason, categories))
            continue  # never shipped

        components.append(
            PackComponent(
                kind=resolved.kind,
                id=resolved.id,
                path=resolved.pack_path,
                sha256=hashlib.sha256(resolved.pack_bytes).hexdigest(),
                depends_on=list(resolved.edges),
            )
        )
        payloads[resolved.pack_path] = resolved.pack_bytes
        for edge in resolved.edges:
            if edge not in seen:
                queue.append(edge)

    # Stable output order — the manifest and the tree read the same on every run.
    components.sort(key=lambda c: (c.kind, c.id))
    requirements.sort(key=lambda r: (r.kind, r.id))
    blocked.sort(key=lambda b: (b.kind, b.id))
    return components, requirements, blocked, payloads


# ── public API ────────────────────────────────────────────────────────────────


def preview_pack(
    seeds: list[str],
    *,
    name: str = "pack",
    version: str = "0.0.0",
) -> PackPreview:
    """Compute the full export plan WITHOUT writing anything (§2.1 look-before-write).

    Drives both redaction layers exactly as :func:`build_pack` will, so the tree a user
    sees is the pack they would get — including the requirements it demoted and any
    component it would block. Never raises on a blocked component; that is reported.
    """
    home = _config_dir()
    components, requirements, blocked, payloads = _walk_closure(list(seeds), home)
    return PackPreview(
        seeds=list(seeds),
        name=name,
        version=version,
        components=components,
        requirements=requirements,
        blocked=blocked,
        payloads=payloads,
    )


def build_pack(
    seeds: list[str],
    *,
    name: str = "pack",
    version: str = "0.0.0",
    display_name: str = "",
    description: str = "",
    author: str = "",
    license: str = "",
    out_path: Path | None = None,
) -> Path:
    """Write a ``schema_version=1`` ``.pclaw`` ZIP for the closure of ``seeds``; return its path.

    Refuses (raises :class:`PackSecretBlocked`) if the content layer blocked any component
    in the closure — a pack must not ship silently missing a requested component. The
    structural layer has already guaranteed no denied file was ever opened.

    Writes to ``out_path`` if given, else ``<home>/packs/<name>.pclaw``.
    """
    preview = preview_pack(seeds, name=name, version=version)
    if preview.has_blocking:
        raise PackSecretBlocked(preview.blocked)

    from personalclaw import __version__ as pclaw_version

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # content_hash: sha256 over the sorted component hashes — non-forgeable provenance an
    # importer re-derives from actual bundle contents (§1), not a value it trusts.
    content_hash = hashlib.sha256(
        "".join(sorted(c.sha256 for c in preview.components)).encode("utf-8")
    ).hexdigest()

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "version": version,
        "displayName": display_name or name,
        "description": description,
        "author": author,
        "license": license,
        "components": [c.to_dict() for c in preview.components],
        "requirements": [r.to_dict() for r in preview.requirements],
        "provenance": {
            "exported_by_version": pclaw_version,
            "exported_at": now,
            "content_hash": content_hash,
        },
    }

    out = out_path if out_path is not None else (_config_dir() / "packs" / f"{name}.pclaw")
    out.parent.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("pack.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for comp in preview.components:
            # Member names come from validated component ids, never from a denied path — the
            # closure only ever put allowlisted-store bytes into `payloads`.
            zf.writestr(str(PurePosixPath(comp.path)), preview.payloads[comp.path])

    out.write_bytes(buf.getvalue())
    logger.info(
        "pack: wrote %s (%d component(s), %d requirement(s))",
        out,
        len(preview.components),
        len(preview.requirements),
    )
    return out
