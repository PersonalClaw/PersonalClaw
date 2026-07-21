"""Native filesystem-backed workflow provider.

Markdown-first: one ``WORKFLOW.md`` per workflow directory under
``~/.personalclaw/workflows/<name>/``, so a workflow reads like an SOP and edits
in the same structured/raw drawer as skills. The ``match_embedding`` vector is
NOT stored in the markdown (a 384-float array is unreadable); it lives in a
sibling ``WORKFLOW.embedding.json`` sidecar, recomputed on write when the
match text or active embedding model changes.

The embedding is computed here on every create/update (write-path), but it is
not consumed until the surfacing engine lands (E4-P2).
"""

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir
from personalclaw.workflows.models import Workflow, WorkflowScope, WorkflowStep
from personalclaw.workflows.provider import WorkflowProvider

logger = logging.getLogger(__name__)

# Starter SOPs shipped with the package, synced into the user's workflows dir on
# first use (mtime-copy, mirroring skills.loader._ensure_builtin_skills).
_BUNDLED_DIR = Path(__file__).parent / "bundled"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# Scalar frontmatter keys (everything else on the entity is derived/list-typed).
_SCALAR_KEYS = {
    "id",
    "name",
    "description",
    "scope",
    "scope_ref",
    "match_text",
    "embedding_model",
    "enabled",
    "version",
    "created_at",
    "updated_at",
}


def create_provider(config: dict[str, Any] | None = None) -> "NativeWorkflowProvider":
    storage_dir = (config or {}).get("storage_dir") or ""
    return NativeWorkflowProvider(storage_dir=storage_dir or None)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_name(name: str) -> bool:
    return bool(name) and ".." not in name and "\\" not in name and "/" not in name


def slugify(name: str) -> str:
    """Normalize a display name to a directory-safe workflow name."""
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")
    return s[:63] or "workflow"


def _ensure_bundled_workflows(base: Path) -> None:
    """Sync starter SOPs from the package's ``bundled/`` dir into *base*.

    Mirrors ``skills.loader._ensure_builtin_skills``: copy a bundled
    ``<name>/WORKFLOW.md`` into the user's workflows dir when it's new or the
    source is newer, leaving user-authored workflows untouched. The embedding
    sidecar is intentionally not shipped — the surfacing engine recomputes it on
    first write, and degrades to the keyword match until then.
    """
    if not _BUNDLED_DIR.exists():
        return
    for src_md in sorted(_BUNDLED_DIR.glob("*/WORKFLOW.md")):
        name = src_md.parent.name
        dest_dir = base / name
        dest_md = dest_dir / "WORKFLOW.md"
        try:
            if not dest_md.exists() or src_md.stat().st_mtime > dest_md.stat().st_mtime:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_md, dest_md)
                logger.info("Synced bundled workflow: %s", name)
        except Exception:
            logger.warning("Failed to sync bundled workflow: %s", name, exc_info=True)


# ── Markdown (de)serialization ──────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split ``--- key: value --- body`` into (scalars, body). Clones the skill
    loader's tolerant ``key: value`` parser."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body


# A reference step serializes as an ordered-list item ``@ref:<workflow-id>`` so a
# composed SOP still reads as markdown and round-trips losslessly.
_REF_RE = re.compile(r"^@ref:\s*(\S+)\s*$")


def parse_steps(body: str) -> list[WorkflowStep]:
    """Parse ordered-list items into steps; an indented ``>`` blockquote (or
    sub-bullet) under an item becomes that step's instruction. An item of the form
    ``@ref:<id>`` is a workflow-reference step. Ids are assigned positionally
    (s1, s2, …)."""
    steps: list[WorkflowStep] = []
    item_re = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*)$")
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # Heading line (the "# name" title) is not a step.
        if line.lstrip().startswith("#"):
            continue
        m = item_re.match(line)
        if m:
            content = m.group(1).strip()
            ref_m = _REF_RE.match(content)
            if ref_m:
                steps.append(WorkflowStep(id=f"s{len(steps) + 1}", ref=ref_m.group(1)))
            else:
                steps.append(WorkflowStep(id=f"s{len(steps) + 1}", title=content))
            continue
        # Continuation: blockquote / indented detail attaches to the last step.
        stripped = line.strip()
        if steps and (line.startswith((" ", "\t")) or stripped.startswith(">")):
            detail = stripped.lstrip("> ").strip()
            detail = re.sub(r"^Detail:\s*", "", detail, flags=re.IGNORECASE)
            if detail:
                prev = steps[-1]
                prev.instruction = (
                    (prev.instruction + " " + detail).strip() if prev.instruction else detail
                )
    return steps


def assemble_markdown(wf: Workflow) -> str:
    """Render a Workflow back to WORKFLOW.md (frontmatter + numbered list)."""
    fm_lines = [
        f"id: {wf.id}",
        f"name: {wf.name}",
        f"description: {wf.description}",
        f"scope: {wf.scope.value}",
        f"scope_ref: {wf.scope_ref}",
        f"tags: {', '.join(wf.tags)}",
        f"match_text: {wf.match_text}",
        f"embedding_model: {wf.embedding_model}",
        f"enabled: {'true' if wf.enabled else 'false'}",
        f"version: {wf.version}",
        f"created_at: {wf.created_at}",
        f"updated_at: {wf.updated_at}",
    ]
    out = ["---", *fm_lines, "---", "", f"# {wf.name}", ""]
    for i, step in enumerate(wf.steps, 1):
        if step.is_ref():
            out.append(f"{i}. @ref:{step.ref}")
            continue
        out.append(f"{i}. {step.title}")
        if step.instruction:
            out.append(f"   > {step.instruction}")
    return "\n".join(out) + "\n"


def _coerce(fm: dict[str, str], body: str) -> Workflow:
    """Build a Workflow from parsed frontmatter + body (embedding from sidecar
    is merged by the caller)."""
    tags = [t.strip() for t in fm.get("tags", "").split(",") if t.strip()]
    try:
        scope = WorkflowScope(fm.get("scope", "global"))
    except ValueError:
        scope = WorkflowScope.GLOBAL
    return Workflow(
        id=fm.get("id", ""),
        name=fm.get("name", ""),
        description=fm.get("description", ""),
        steps=parse_steps(body),
        tags=tags,
        scope=scope,
        scope_ref=fm.get("scope_ref", ""),
        match_text=fm.get("match_text", ""),
        embedding_model=fm.get("embedding_model", ""),
        enabled=fm.get("enabled", "true").lower() != "false",
        version=fm.get("version", "1"),
        created_at=fm.get("created_at", ""),
        updated_at=fm.get("updated_at", ""),
    )


# ── Embedding (write-path; consumed in P2) ───────────────────────────────────


def _embed_source(wf: Workflow) -> str:
    """The text to embed: match_text, falling back to name + description."""
    return wf.match_text.strip() or f"{wf.name} {wf.description}".strip()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _compute_embedding(wf: Workflow) -> tuple[list[float], str]:
    """Embed the match source with the active model. Returns (vector, model_spec).
    Degrades to ([], "") when no embedding model is active — surfacing then uses
    the keyword fallback (P2)."""
    try:
        from personalclaw.embedding_providers.registry import (
            _active_embedding_spec,
            get_active_embed_fn,
        )
    except Exception:
        return [], ""
    source = _embed_source(wf)
    if not source:
        return [], ""
    try:
        fn = get_active_embed_fn()
    except Exception:
        fn = None
    if fn is None:
        return [], ""
    try:
        vec = fn(source)
    except Exception:
        vec = None
    if not vec:
        return [], ""
    model = ""
    try:
        spec = _active_embedding_spec()
        if spec:
            model = f"{spec[0]}:{spec[1]}"
    except Exception:
        model = ""
    return list(vec), model


class NativeWorkflowProvider(WorkflowProvider):
    """Filesystem workflow provider — one WORKFLOW.md per directory."""

    def __init__(self, storage_dir: str | None = None) -> None:
        self._override_dir = Path(storage_dir).expanduser() if storage_dir else None
        self._bundled_synced = False

    @property
    def name(self) -> str:
        return "native"

    def _root(self) -> Path:
        d = self._override_dir or (config_dir() / "workflows")
        d.mkdir(parents=True, exist_ok=True)
        # Sync starter SOPs into the default dir once per instance. Skipped for an
        # explicit override dir (tests author their own fixtures).
        if not self._bundled_synced and self._override_dir is None:
            self._bundled_synced = True
            _ensure_bundled_workflows(d)
        return d

    def _md_path(self, wf_dir: Path) -> Path:
        return wf_dir / "WORKFLOW.md"

    def _sidecar_path(self, wf_dir: Path) -> Path:
        return wf_dir / "WORKFLOW.embedding.json"

    def _dir_for(self, workflow_id: str) -> Path | None:
        """Find the directory whose WORKFLOW.md has this id."""
        for child in self._root().iterdir():
            if not child.is_dir():
                continue
            md = self._md_path(child)
            if not md.exists():
                continue
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            if fm.get("id") == workflow_id:
                return child
        return None

    def _read_dir(self, wf_dir: Path) -> Workflow | None:
        md = self._md_path(wf_dir)
        if not md.exists():
            return None
        try:
            fm, body = parse_frontmatter(md.read_text(encoding="utf-8"))
            wf = _coerce(fm, body)
            wf.provider = self.name
            sidecar = self._sidecar_path(wf_dir)
            if sidecar.exists():
                try:
                    data = json.loads(sidecar.read_text(encoding="utf-8"))
                    if data.get("text_hash") == _text_hash(_embed_source(wf)):
                        wf.match_embedding = data.get("vector", [])
                        wf.embedding_model = data.get("model", wf.embedding_model)
                except Exception:
                    pass
            return wf
        except Exception:
            return None

    def _write_dir(self, wf: Workflow) -> None:
        wf_dir = self._root() / wf.name
        wf_dir.mkdir(parents=True, exist_ok=True)
        # Compute + persist embedding sidecar (write-path).
        vector, model = _compute_embedding(wf)
        if vector:
            wf.match_embedding = vector
            wf.embedding_model = model
            sidecar = {
                "model": model,
                "vector": vector,
                "text_hash": _text_hash(_embed_source(wf)),
            }
            self._sidecar_path(wf_dir).write_text(json.dumps(sidecar), encoding="utf-8")
        self._md_path(wf_dir).write_text(assemble_markdown(wf), encoding="utf-8")

    def _all(self) -> list[Workflow]:
        out: list[Workflow] = []
        for child in sorted(self._root().iterdir()):
            if child.is_dir():
                wf = self._read_dir(child)
                if wf:
                    out.append(wf)
        return out

    async def list_workflows(
        self,
        scope: WorkflowScope | None = None,
        scope_ref: str | None = None,
        tag: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[Workflow], int]:
        def _list() -> tuple[list[Workflow], int]:
            wfs = self._all()
            if scope is not None:
                wfs = [w for w in wfs if w.scope == scope]
            if scope_ref is not None:
                wfs = [w for w in wfs if w.scope_ref == scope_ref]
            if tag:
                wfs = [w for w in wfs if tag in w.tags]
            total = len(wfs)
            return wfs[offset : offset + limit], total

        return await asyncio.to_thread(_list)

    async def get_workflow(self, workflow_id: str) -> Workflow | None:
        def _get() -> Workflow | None:
            wf_dir = self._dir_for(workflow_id)
            return self._read_dir(wf_dir) if wf_dir else None

        return await asyncio.to_thread(_get)

    async def create_workflow(self, **fields: Any) -> Workflow:
        def _create() -> Workflow:
            name = slugify(fields.get("name") or fields.get("title") or "")
            if not _NAME_RE.match(name):
                raise ValueError(f"Invalid workflow name: {name!r}")
            # Avoid clobbering an existing dir of the same name.
            if (self._root() / name).exists():
                name = f"{name}-{uuid.uuid4().hex[:4]}"
            now = _now_iso()
            try:
                scope = WorkflowScope(fields.get("scope", "global"))
            except ValueError:
                scope = WorkflowScope.GLOBAL
            steps = [
                WorkflowStep(
                    id=f"s{i + 1}",
                    title=s.get("title", ""),
                    instruction=s.get("instruction", ""),
                    ref=s.get("ref", ""),
                )
                for i, s in enumerate(fields.get("steps", []))
                if s.get("title") or s.get("ref")
            ]
            wf = Workflow(
                id=f"wf-{uuid.uuid4().hex[:8]}",
                name=name,
                description=fields.get("description", ""),
                steps=steps,
                tags=fields.get("tags", []),
                scope=scope,
                scope_ref=fields.get("scope_ref", ""),
                match_text=fields.get("match_text", ""),
                enabled=fields.get("enabled", True),
                provider=self.name,
                created_at=now,
                updated_at=now,
            )
            self._write_dir(wf)
            return wf

        return await asyncio.to_thread(_create)

    async def update_workflow(self, workflow_id: str, **fields: Any) -> Workflow | None:
        def _update() -> Workflow | None:
            wf_dir = self._dir_for(workflow_id)
            if not wf_dir:
                return None
            wf = self._read_dir(wf_dir)
            if not wf:
                return None
            renamed = False
            for key, val in fields.items():
                if key == "scope":
                    try:
                        wf.scope = WorkflowScope(val)
                    except ValueError:
                        pass
                elif key == "steps":
                    wf.steps = [
                        WorkflowStep(
                            id=f"s{i + 1}",
                            title=s.get("title", ""),
                            instruction=s.get("instruction", ""),
                            ref=s.get("ref", ""),
                        )
                        for i, s in enumerate(val)
                        if s.get("title") or s.get("ref")
                    ]
                elif key == "name":
                    new_name = slugify(val)
                    if new_name and new_name != wf.name and _NAME_RE.match(new_name):
                        wf.name = new_name
                        renamed = True
                elif hasattr(wf, key) and key not in (
                    "id",
                    "provider",
                    "created_at",
                    "match_embedding",
                ):
                    setattr(wf, key, val)
            wf.updated_at = _now_iso()
            # On rename the directory changes; remove the old one after writing.
            if renamed:
                import shutil

                self._write_dir(wf)
                if wf_dir.name != wf.name:
                    shutil.rmtree(wf_dir, ignore_errors=True)
            else:
                self._write_dir(wf)
            return wf

        return await asyncio.to_thread(_update)

    async def delete_workflow(self, workflow_id: str) -> bool:
        def _delete() -> bool:
            wf_dir = self._dir_for(workflow_id)
            if not wf_dir:
                return False
            import shutil

            shutil.rmtree(wf_dir, ignore_errors=True)
            return True

        return await asyncio.to_thread(_delete)


Provider = NativeWorkflowProvider
