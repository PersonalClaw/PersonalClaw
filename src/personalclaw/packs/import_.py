"""Pack import core — inspect-without-write, leaves-first commit, journaled rollback (AP-2).

The exact inverse of :mod:`packs.build`. Where the exporter walks a closure and writes a
``.pclaw`` ZIP, the importer opens one and installs its components onto THIS machine —
and it is the more dangerous half: a pack is untrusted third-party content that ships
executable skills, so a malicious or corrupt pack must not be able to write a dangerous
skill, corrupt state, or leave a half-written mess. The whole module is built around one
property: **atomicity** — a fault at any point unwinds to byte-identical pre-import state.

The pipeline (AGENT-PACKS §3.1), every step fail-closed and SEL-audited:

1. **inspect (dry-run, NO home writes)** — :func:`inspect_pack` opens the ZIP, extracts to
   a SYSTEM tempdir (never the home), re-derives ``content_hash`` from the ACTUAL member
   bytes and compares to the manifest (integrity recompute — the manifest's value is never
   trusted), runs the referential-integrity + parse lint (:mod:`packs.lint`), and scans
   every component with :class:`supply_chain.SkillScanner` at the origin's trust tier. It
   returns an :class:`ImportPlan` — a preview of exactly what a commit would install,
   including each component's fresh local id and scan verdict — WITHOUT touching home state.
2. **commit (leaves-first, journaled)** — :func:`import_pack` runs the same inspect, then
   refuses on any blocking condition (integrity mismatch, lint error, a DANGEROUS component
   regardless of consent, a WARNING component without consent). Only then does it commit,
   leaves-first (skills → prompts → agents → templates → triggers → config), journaling every
   write to ``packs/.installing/<id>.json`` BEFORE it happens. Skills commit through
   :class:`PackMarketplace` → ``install_guarded`` (the shared gate → ``.pclaw-lock.json``).
   Fresh local ids are assigned on collision and every intra-pack reference is rewritten on
   the PARSED object (never string-replaced over raw bytes — a byte splice corrupts). Any
   exception triggers rollback: every journaled write is unwound leaves-last (reverse order),
   restoring byte-identical pre-import state — no partial packs.

Triggers land DISABLED and config_subset lands STAGED (never applied): a pack cannot arm
automation or edit config on install — those are human-enabled from their own surfaces later
(§3.1 propose-don't-write applied to distribution).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from personalclaw.config.loader import config_dir
from personalclaw.packs import lint as pack_lint
from personalclaw.packs import roster as pack_roster
from personalclaw.packs.build import SCHEMA_VERSION
from personalclaw.skills.marketplace import SkillDetail, SkillEntry, SkillsMarketplace

logger = logging.getLogger(__name__)

#: Leaves-first commit order (§3.1): a component is written only after every in-pack
#: component it can reference. Skills/prompts are leaves; agents reference skills;
#: templates reference agents+skills; triggers reference templates/prompts.
_COMMIT_RANK: dict[str, int] = {"skill": 0, "prompt": 1, "agent": 2, "template": 3, "trigger": 4}

#: The component kinds AP-2 commits. A manifest naming an unknown kind is skipped with a
#: report note rather than hard-failing (best-effort forward import, .ovsvoice rule).
_KNOWN_KINDS = frozenset(_COMMIT_RANK)


# ── public data model ─────────────────────────────────────────────────────────


@dataclass
class PlannedComponent:
    """One component the plan would install: its fresh local id + its scan verdict."""

    kind: str
    orig_id: str
    target_id: str  # fresh id after collision remap (== orig_id when no collision)
    path: str
    verdict: str  # supply_chain.Verdict value ("clean" | "warning" | "dangerous" | ...)
    depends_on: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.orig_id}"


@dataclass
class ImportPlan:
    """The dry-run preview (:func:`inspect_pack`) — what a commit WOULD do, no writes made.

    The importer's decision inputs live here: ``integrity_ok`` (recomputed hash matched),
    ``lint`` (referential integrity), and per-component verdicts. ``blocked`` folds the
    non-overridable refusals; ``needs_consent`` is the overridable WARNING gate.
    """

    name: str
    version: str
    components: list[PlannedComponent] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    lint: pack_lint.LintReport = field(default_factory=pack_lint.LintReport)
    integrity_ok: bool = True
    integrity_detail: str = ""
    schema_version_ahead: bool = False
    staged_triggers: list[str] = field(default_factory=list)
    staged_config_keys: list[str] = field(default_factory=list)
    #: The pack's ``connectors.json`` declarations (§3.3) — resolved on commit via
    #: configure/substitute/skip. Empty when the pack declares no connectors.
    connectors: list[dict[str, Any]] = field(default_factory=list)
    #: The committed id of the pack's ``setup/SKILL.md`` interview skill (§3.4) once a commit
    #: runs, or "" when the pack ships none. On the inspect plan it is the id a commit WOULD
    #: assign; the skill itself is scanned like any other skill (a DANGEROUS setup skill
    #: blocks the whole import).
    setup_skill: str = ""
    #: The per-connector resolution outcomes recorded by a commit (each a
    #: :meth:`ConnectorResolution.to_dict`). Empty on the dry-run plan.
    connector_resolutions: list[dict[str, Any]] = field(default_factory=list)
    #: The pack's roster rows (§4.2) — each a :meth:`RosterEntry.to_dict`. Empty when the
    #: pack ships no ``agents/catalog.json``. A commit stages these; only the ``always`` tier
    #: is deployed, and only when a human asks (:func:`packs.roster.deploy_roster`).
    roster: list[dict[str, Any]] = field(default_factory=list)
    #: The pack's scenario runbooks (§4.2), each a :meth:`Runbook.to_dict`.
    runbooks: list[dict[str, Any]] = field(default_factory=list)
    #: The setup interview's declared bindings (§3.4/§4.1) — the questions the "Finish setup"
    #: chip has to get answered, each ``{key, kind, label, required}``.
    bindings: list[dict[str, Any]] = field(default_factory=list)
    #: The manifest's ``pack_owned`` path patterns (§1) — which components a later UPDATE may
    #: overwrite. Carried on the plan (not re-read from the archive) so :mod:`packs.update`
    #: decides from the same parse the integrity check verified.
    pack_owned: list[str] = field(default_factory=list)

    @property
    def has_dangerous(self) -> bool:
        return any(c.verdict == "dangerous" for c in self.components)

    @property
    def needs_consent(self) -> bool:
        """A WARNING component that a commit installs only with explicit consent."""
        return any(c.verdict == "warning" for c in self.components)

    @property
    def blocked(self) -> bool:
        """The non-overridable refusals: bad integrity, a lint error, or a DANGEROUS
        component. ``force``/consent never clears any of these — they are terminal."""
        return not self.integrity_ok or not self.lint.ok or self.has_dangerous

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "integrity_ok": self.integrity_ok,
            "integrity_detail": self.integrity_detail,
            "schema_version_ahead": self.schema_version_ahead,
            "blocked": self.blocked,
            "needs_consent": self.needs_consent,
            "lint": self.lint.to_dict(),
            "components": [
                {
                    "kind": c.kind,
                    "orig_id": c.orig_id,
                    "target_id": c.target_id,
                    "verdict": c.verdict,
                    "depends_on": list(c.depends_on),
                }
                for c in self.components
            ],
            "requirements": list(self.requirements),
            "staged_triggers": list(self.staged_triggers),
            "staged_config_keys": list(self.staged_config_keys),
            "connectors": list(self.connectors),
            "setup_skill": self.setup_skill,
            "connector_resolutions": list(self.connector_resolutions),
            "roster": list(self.roster),
            "runbooks": list(self.runbooks),
            "bindings": list(self.bindings),
            "pack_owned": list(self.pack_owned),
        }


class PackImportRefused(Exception):
    """A pack import was refused before (or unwound after) any lasting write.

    ``reason`` is a stable code the UI branches on: ``"integrity"`` (recomputed hash
    mismatch), ``"lint"`` (unresolved reference / unparseable component), ``"dangerous"``
    (a component the scanner flagged — never overridable), ``"needs_consent"`` (a WARNING
    component and consent not given), or ``"fault"`` (a mid-commit fault, fully rolled back).
    ``plan`` carries the inspect result so the caller can show exactly why.
    """

    def __init__(self, reason: str, message: str, plan: "ImportPlan | None" = None) -> None:
        self.reason = reason
        self.plan = plan
        super().__init__(message)


# ── the pack skills marketplace (done_when 5) ──────────────────────────────────


class PackMarketplace(SkillsMarketplace):
    """A transient, single-pack skills SOURCE that adapts one extracted pack skill dir to
    the ``SkillDetail{name, files}`` shape the shared install gate expects (§3.1).

    Skills never bypass the supply-chain gate just because they arrived in a pack — this
    exists ONLY so pack skill dirs flow through the exact same ``install_guarded`` →
    ``install_scanned`` chokepoint (quarantine → scan → commit → ``.pclaw-lock.json``) as
    any other install (mirrors ``apps.skill_seed._AppSkillsMarketplace``). It is registered
    under a unique per-import name for the duration of one commit and unregistered after; it
    is not a public marketplace.
    """

    def __init__(self, files_by_skill: dict[str, list[dict[str, Any]]], tier: str) -> None:
        # Maps a skill's committed (fresh) name → its already-rewritten file entries.
        self._files = files_by_skill
        self._tier = tier

    @property
    def marketplace_type(self) -> str:
        return "pack"

    @property
    def trust_tier(self) -> str:
        return self._tier

    def search(self, query: str, limit: int = 20) -> list["SkillEntry"]:  # pragma: no cover
        return []

    def fetch(self, skill_id: str) -> "SkillDetail":
        files = self._files.get(skill_id)
        if files is None:
            raise RuntimeError(f"pack skill not found: {skill_id!r}")
        return SkillDetail(id=skill_id, name=skill_id, files=files, audit_status="pass")


# ── ZIP open + integrity ────────────────────────────────────────────────────


def _safe_member(name: str) -> bool:
    """A pack member name that cannot build a traversing/absolute path (zip-slip guard)."""
    if not name or name.startswith("/") or ".." in name or "\\" in name or "\x00" in name:
        return False
    return all(part not in ("", ".", "..") for part in PurePosixPath(name).parts)


def _extract_quarantine(zf: zipfile.ZipFile, dest: Path) -> dict[str, bytes]:
    """Extract every member into ``dest`` (a SYSTEM tempdir, never the home), path-safe.

    Returns a map of member name → raw bytes. A member with an unsafe name aborts the whole
    extraction — a pack that even ATTEMPTS traversal is not one we finish reading (fail
    closed), mirroring ``skills.marketplace._stage_files``.
    """
    members: dict[str, bytes] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        if not _safe_member(info.filename):
            raise PackImportRefused("integrity", f"unsafe pack member path: {info.filename!r}")
        raw = zf.read(info)
        members[info.filename] = raw
        out = dest / PurePosixPath(info.filename)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
    return members


def _recompute_content_hash(components: list[dict[str, Any]], members: dict[str, bytes]) -> str:
    """Re-derive ``content_hash`` from the ACTUAL member bytes (never the manifest's value).

    Each component's sha256 is recomputed over the real bytes at its declared path; the
    content hash is the sha256 over the sorted per-component hashes — identical to
    :func:`packs.build.build_pack`'s derivation, so a pack claiming a component it does not
    carry (or carrying mutated bytes) fails this check, not the reader's trust.
    """
    per: list[str] = []
    for comp in components:
        raw = members.get(str(comp.get("path", "")))
        if raw is None:
            # A manifest row with no backing member can't be hashed — the mismatch will be
            # caught here (empty contributes nothing) and named precisely by the lint.
            continue
        per.append(hashlib.sha256(raw).hexdigest())
    return hashlib.sha256("".join(sorted(per)).encode("utf-8")).hexdigest()


# ── parsed-component representation + fresh-id rewriting ──────────────────────


@dataclass
class _Comp:
    """A parsed component being planned/committed. Duck-types :class:`packs.lint._Component`
    for the linter (``kind``/``id``/``path``/``depends_on``/``ref``)."""

    kind: str
    id: str  # original id (the linter resolves edges against original refs)
    path: str
    depends_on: list[str]
    target_id: str  # fresh local id after collision remap
    obj: Any = None  # dict (template/prompt/trigger) | AgentDefinition (agent)
    skill_files: list[dict[str, Any]] | None = None  # for skills only
    verdict: str = "clean"
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"


def _parse_agent_markdown(text: str):
    """Parse persona markdown (frontmatter + body) back to an :class:`AgentDefinition`.

    The inverse of ``packs.build._render_agent_markdown``: the YAML frontmatter carries
    name/description/model/skills; the body is the operating prompt. The body is loaded as
    ``system_prompt`` (the render fused system_prompt+voice with a blank line, so a lossless
    split isn't recoverable — the whole body as the operating prompt is the honest import).
    """
    from personalclaw.agents.marketplace import AgentDefinition

    front: dict[str, Any] = {}
    body = text
    stripped = text.lstrip("﻿")
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        if end != -1:
            import yaml  # type: ignore

            front = yaml.safe_load(stripped[3:end]) or {}
            if not isinstance(front, dict):
                front = {}
            body = stripped[end + 4 :].lstrip("\n")
    return AgentDefinition(
        name=str(front.get("name", "") or ""),
        description=str(front.get("description", "") or ""),
        model=str(front.get("model", "") or ""),
        skills=[str(s) for s in (front.get("skills") or []) if str(s).strip()],
        system_prompt=body.rstrip() + "\n" if body.strip() else "",
    )


def _rewrite_skill_frontmatter_name(files: list[dict[str, Any]], new_name: str) -> None:
    """Rewrite the ``name:`` frontmatter line in a skill's SKILL.md to ``new_name``.

    Operates on the PARSED file entry's text (frontmatter is line-structured), never a
    blind byte splice — a fresh-id'd skill's manifest name must match the dir it lands in
    or the loader would surface a name/dir mismatch.
    """
    for entry in files:
        if not str(entry.get("path", "")).endswith("SKILL.md"):
            continue
        text = entry.get("contents")
        if not isinstance(text, str):
            continue
        out_lines: list[str] = []
        in_front = False
        replaced = False
        for i, line in enumerate(text.splitlines(keepends=False)):
            if i == 0 and line.strip() == "---":
                in_front = True
                out_lines.append(line)
                continue
            if in_front and line.strip() == "---":
                in_front = False
                out_lines.append(line)
                continue
            if in_front and not replaced and line.lstrip().startswith("name:"):
                indent = line[: len(line) - len(line.lstrip())]
                out_lines.append(f"{indent}name: {new_name}")
                replaced = True
                continue
            out_lines.append(line)
        trailing = "\n" if text.endswith("\n") else ""
        entry["contents"] = "\n".join(out_lines) + trailing


def _rewrite_refs(comp: _Comp, remap: dict[tuple[str, str], str]) -> None:
    """Rewrite a component's intra-pack references to fresh ids, on the PARSED object.

    ``remap`` maps ``(kind, orig_id)`` → fresh id for every collided component. Each kind's
    references are rewritten structurally (never over raw bytes): an agent's ``skills``
    list, a template stage's ``agent``/``skills`` and a subworkflow's ``ref``, a trigger's
    ``kind:id`` action refs. A component whose id itself was remapped also has its own id
    field set to the fresh id here.
    """
    if comp.kind == "skill":
        if comp.target_id != comp.id and comp.skill_files is not None:
            _rewrite_skill_frontmatter_name(comp.skill_files, comp.target_id)
        return

    if comp.kind == "agent":
        defn = comp.obj
        defn.name = comp.target_id
        defn.skills = [remap.get(("skill", s), s) for s in defn.skills]
        return

    if comp.kind == "template":
        obj = comp.obj
        obj["name"] = comp.target_id
        _rewrite_template_nodes(obj.get("root"), remap)
        return

    if comp.kind == "prompt":
        comp.obj["name"] = comp.target_id
        return

    if comp.kind == "trigger":
        comp.obj["name"] = comp.target_id
        # A trigger cannot arm automation on install (§3.1): it lands DISABLED, always.
        comp.obj["enabled"] = False
        _rewrite_ref_strings(comp.obj.get("action"), remap)
        # A `Trigger` row points at its workflow definition by BARE SLUG under
        # ``workflow.def``/``workflow.name`` (`triggers.calendar` resolves exactly those two
        # keys), so the `kind:id` rewriter above cannot see it. Without this, a trigger whose
        # template collided on import would still name the author's slug and fire nothing.
        wf = comp.obj.get("workflow")
        if isinstance(wf, dict):
            for key in ("def", "name"):
                slug = wf.get(key)
                if isinstance(slug, str) and slug.strip():
                    wf[key] = remap.get(("template", slug.strip()), slug)


def _rewrite_template_nodes(node: Any, remap: dict[tuple[str, str], str]) -> None:
    """Rewrite agent/skill/subworkflow refs on a WorkflowDef node tree (bare slugs)."""
    if not isinstance(node, dict):
        return
    cfg = node.get("config")
    if isinstance(cfg, dict):
        agent = cfg.get("agent")
        if isinstance(agent, str) and agent.strip():
            cfg["agent"] = remap.get(("agent", agent.strip()), agent)
        skills = cfg.get("skills")
        if isinstance(skills, list):
            cfg["skills"] = [
                remap.get(("skill", s), s) if isinstance(s, str) else s for s in skills
            ]
        ref = cfg.get("ref")
        if isinstance(ref, str) and ref.strip():
            base, _, ver = ref.partition("@")
            new = remap.get(("template", base.strip()), base.strip())
            cfg["ref"] = f"{new}@{ver}" if ver else new
    for child in node.get("children") or []:
        _rewrite_template_nodes(child, remap)
    for case in (node.get("cases") or {}).values():
        _rewrite_template_nodes(case, remap)
    for key in ("body", "default_case"):
        if node.get(key) is not None:
            _rewrite_template_nodes(node.get(key), remap)


def _rewrite_ref_strings(obj: Any, remap: dict[tuple[str, str], str]) -> None:
    """Rewrite any ``"kind:id"`` string value in-place to its fresh ``"kind:target"``.

    Used for a trigger's action payload, whose reference shape is ``kind:id`` strings
    (``{"ref": "template:cfo-monthly"}``) rather than bare slugs."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str) and ":" in v:
                kind, _, cid = v.partition(":")
                new = remap.get((kind, cid))
                if new is not None:
                    obj[k] = f"{kind}:{new}"
            else:
                _rewrite_ref_strings(v, remap)
    elif isinstance(obj, list):
        for item in obj:
            _rewrite_ref_strings(item, remap)


# ── local-existence probes (fresh-id collision + lint local resolution) ───────


def _agents_base(home: Path) -> Path:
    return home / "agents"


def _local_exists(home: Path, kind: str, cid: str) -> bool:
    """Whether a component ``kind:cid`` is already installed in THIS home.

    Drives both fresh-id collision detection and the lint's local-reference resolution: a
    dependent may reference a component the pack doesn't carry but the home already has.
    """
    if kind == "skill":
        return (home / "skills" / cid / "SKILL.md").is_file()
    if kind == "template":
        return (home / "workflows" / "defs" / cid / "workflow.json").is_file()
    if kind == "prompt":
        return (home / "prompts" / f"{cid}.yaml").is_file()
    if kind == "agent":
        return (_agents_base(home) / cid / "agent.json").is_file()
    return False


def _fresh_id(home: Path, kind: str, orig_id: str, taken: set[tuple[str, str]]) -> str:
    """A local id that collides with neither a live component nor an already-assigned one.

    Returns ``orig_id`` when free; otherwise the WORK-R15 ``<id>-imported-<N>`` slot,
    incrementing past any live OR in-this-import collision (``taken``)."""

    def _busy(cid: str) -> bool:
        return _local_exists(home, kind, cid) or (kind, cid) in taken

    if not _busy(orig_id):
        return orig_id
    n = 1
    while _busy(f"{orig_id}-imported-{n}"):
        n += 1
    return f"{orig_id}-imported-{n}"


# ── parse + plan ──────────────────────────────────────────────────────────────


def _parse_component(kind: str, cid: str, path: str, depends_on: list[str], raw: bytes) -> _Comp:
    """Parse one component's bytes into its structured object (never trusted raw at commit).

    A parse failure raises ``ValueError``; the caller turns that into a lint parse_error so
    the whole import refuses before any write.
    """
    comp = _Comp(kind=kind, id=cid, path=path, depends_on=list(depends_on), target_id=cid)
    text = raw.decode("utf-8")  # non-UTF-8 already flagged by the lint's parse pass
    if kind == "skill":
        # The skill's whole file set is rebuilt from the extracted members by the caller;
        # here we only mark it a skill (files attached during extraction planning).
        comp.skill_files = []
    elif kind == "template":
        comp.obj = json.loads(text)
    elif kind == "prompt":
        import yaml  # type: ignore

        comp.obj = yaml.safe_load(text) or {}
    elif kind == "agent":
        comp.obj = _parse_agent_markdown(text)
    elif kind == "trigger":
        comp.obj = json.loads(text)
    return comp


def _skill_files_from_members(
    quarantine: Path, members: dict[str, bytes], skill_id: str
) -> list[dict[str, Any]]:
    """Collect a skill's file entries (``{path, contents|data}``) from the extracted members.

    The pack ships a skill as ``skills/<id>/…`` members; ``install_scanned`` wants entries
    relative to the skill dir, so the ``skills/<id>/`` prefix is stripped."""
    from personalclaw.skills.marketplace import read_skill_file_entry

    prefix = f"skills/{skill_id}/"
    files: list[dict[str, Any]] = []
    for name in sorted(members):
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix) :]
        if not rel:
            continue
        files.append(read_skill_file_entry(quarantine / PurePosixPath(name), rel))
    return files


def _build_plan(
    manifest: dict[str, Any],
    members: dict[str, bytes],
    quarantine: Path,
    home: Path,
    tier: "Any",
    in_place: bool = False,
) -> tuple[ImportPlan, list[_Comp]]:
    """Parse + plan a pack against ``home`` WITHOUT writing anything. Returns the plan and
    the parsed components (in commit order) so :func:`import_pack` can reuse them.

    ``in_place`` turns OFF fresh-id collision remapping, and exists for exactly one caller:
    :func:`packs.update.apply_update`. On a first install a collision means "someone else
    already owns this slug", so the WORK-R15 ``<id>-imported-<N>`` slot is right. On an UPDATE
    the colliding entity IS this pack's own previous copy, and remapping would install a second
    parallel component beside it instead of replacing it — the update would silently never
    happen while reporting success."""
    from personalclaw.supply_chain import SkillScanner

    raw_components = manifest.get("components") or []
    schema_ahead = int(manifest.get("schema_version", SCHEMA_VERSION)) > SCHEMA_VERSION

    # ── parse every component (a parse failure becomes a lint error, never a write) ──
    parsed: list[_Comp] = []
    lint_parse_errors: list[pack_lint.LintFinding] = []
    for row in raw_components:
        kind = str(row.get("kind", ""))
        cid = str(row.get("id", ""))
        path = str(row.get("path", ""))
        depends_on = [str(d) for d in (row.get("depends_on") or [])]
        if kind not in _KNOWN_KINDS:
            # Best-effort forward import: an unknown kind is noted, not fatal.
            continue
        raw = members.get(path)
        if raw is None:
            lint_parse_errors.append(
                pack_lint.LintFinding(
                    "error", "missing_payload", f"{kind}:{cid}", f"no member at {path!r}"
                )
            )
            continue
        try:
            comp = _parse_component(kind, cid, path, depends_on, raw)
        except (ValueError, TypeError) as exc:
            lint_parse_errors.append(
                pack_lint.LintFinding("error", "parse_error", f"{kind}:{cid}", str(exc))
            )
            continue
        if kind == "skill":
            comp.skill_files = _skill_files_from_members(quarantine, members, cid)
        parsed.append(comp)

    # ── fresh-id assignment (reads home, writes nothing) + reference rewriting ──
    taken: set[tuple[str, str]] = set()
    remap: dict[tuple[str, str], str] = {}
    for comp in parsed:
        fresh = comp.id if in_place else _fresh_id(home, comp.kind, comp.id, taken)
        comp.target_id = fresh
        taken.add((comp.kind, fresh))
        if fresh != comp.id:
            remap[(comp.kind, comp.id)] = fresh
    for comp in parsed:
        _rewrite_refs(comp, remap)

    # ── supply-chain scan every component at the origin's trust tier (§3.5) ──
    scanner = SkillScanner()
    for comp in parsed:
        if comp.kind == "skill":
            skill_dir = quarantine / "skills" / comp.id
            scan_report = scanner.scan(skill_dir, tier)
        else:
            # Single-file component: scan its text on the appropriate surface (a template/
            # trigger JSON or agent/prompt body — injection + invisible-char rules apply).
            surface = "frontmatter" if comp.kind in ("agent", "prompt") else "manifest"
            scan_report = scanner.scan_text(
                members.get(comp.path, b"").decode("utf-8", "replace"), surface=surface
            )
        comp.verdict = scan_report.verdict.value
        comp.findings = [f.to_dict() for f in scan_report.findings]

    # ── roster (§4.2): catalog.json + runbooks are members that DESCRIBE components, so
    # they are linted against the agent components actually carried. An unresolved slug is
    # an ERROR, which the refusal gate below turns into a blocked import naming the ref. ──
    roster_entries = pack_roster.parse_catalog(members.get(pack_roster.CATALOG_MEMBER))
    runbooks = pack_roster.parse_runbooks(members)
    roster_findings = pack_roster.lint_roster(
        roster_entries, runbooks, {c.id for c in parsed if c.kind == "agent"}
    )
    pack_roster.remap_entries(roster_entries, remap)

    # ── referential-integrity + parse lint (dry-run) ──
    lint_report = pack_lint.lint_pack(
        parsed,
        manifest.get("requirements") or [],
        {c.path: members.get(c.path, b"") for c in parsed},
        local_resolver=lambda ref: _local_exists(home, *ref.split(":", 1)) if ":" in ref else False,
    )
    lint_report.findings.extend(lint_parse_errors)
    lint_report.findings.extend(roster_findings)

    # ── integrity recompute (never trust the manifest's content_hash) ──
    claimed = str((manifest.get("provenance") or {}).get("content_hash", ""))
    actual = _recompute_content_hash(raw_components, members)
    integrity_ok = bool(claimed) and claimed == actual
    integrity_detail = (
        "" if integrity_ok else f"content_hash mismatch: manifest={claimed!r} actual={actual!r}"
    )

    # ── connector declarations (§3.3): a top-level connectors.json member ──
    connectors = _parse_connectors(members.get("connectors.json"))

    # ── setup skill (§3.4): a pack's setup/SKILL.md installs through the SAME guarded path
    # as every other skill. It is NOT a manifest component (it lives under setup/), so we
    # build a skill _Comp for it here and fold it into `parsed` — that way its scan verdict
    # feeds the plan's DANGEROUS/WARNING gates AND the commit loop installs it through
    # install_guarded with no special-casing. A DANGEROUS setup interview blocks the whole
    # import, exactly as §3.5 requires. ──
    setup_files = _setup_skill_files(quarantine, members)
    setup_id = ""
    if setup_files:
        pack_slug = str(manifest.get("name", "") or "pack")
        setup_id = _fresh_id(home, "skill", f"{pack_slug}-setup", taken)
        taken.add(("skill", setup_id))
        _rewrite_skill_frontmatter_name(setup_files, setup_id)
        setup_report = scanner.scan(quarantine / "setup", tier)
        parsed.append(
            _Comp(
                kind="skill",
                id=setup_id,
                path="setup/SKILL.md",
                depends_on=[],
                target_id=setup_id,
                skill_files=setup_files,
                verdict=setup_report.verdict.value,
                findings=[f.to_dict() for f in setup_report.findings],
            )
        )

    parsed.sort(key=lambda c: (_COMMIT_RANK.get(c.kind, 99), c.target_id))
    plan = ImportPlan(
        name=str(manifest.get("name", "") or ""),
        version=str(manifest.get("version", "") or ""),
        components=[
            PlannedComponent(
                kind=c.kind,
                orig_id=c.id,
                target_id=c.target_id,
                path=c.path,
                verdict=c.verdict,
                depends_on=list(c.depends_on),
                findings=c.findings,
            )
            for c in parsed
        ],
        requirements=[dict(r) for r in (manifest.get("requirements") or [])],
        lint=lint_report,
        integrity_ok=integrity_ok,
        integrity_detail=integrity_detail,
        schema_version_ahead=schema_ahead,
        staged_triggers=[c.target_id for c in parsed if c.kind == "trigger"],
        staged_config_keys=_editable_config_keys(members.get("config_subset.json")),
        connectors=connectors,
        setup_skill=setup_id,
        roster=[e.to_dict() for e in roster_entries],
        runbooks=[b.to_dict() for b in runbooks],
        bindings=_parse_bindings(members.get("setup/bindings.json")),
        pack_owned=[str(p) for p in (manifest.get("pack_owned") or []) if str(p).strip()],
    )
    return plan, parsed


#: A setup binding's declared kinds (§3.4). ``folder`` is validated as an existing directory
#: on answer; ``text`` is stored verbatim. Closed — an unknown kind is dropped rather than
#: stored unvalidated, because a binding nobody validates is a binding nobody can trust.
_BINDING_KINDS = ("folder", "text")


def _parse_bindings(raw: bytes | None) -> list[dict[str, Any]]:
    """Parse ``setup/bindings.json`` — the questions the setup interview must get answered.

    Each row is ``{key, kind, label, required}``. This is what makes "Finish setup" a state
    rather than a suggestion: the ledger records which keys are still unbound, so the chip
    can say what is missing instead of merely existing."""
    if not raw:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key", "") or "").strip()
        kind = str(row.get("kind", "") or "").strip()
        if not key or key in seen or kind not in _BINDING_KINDS:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "kind": kind,
                "label": str(row.get("label", "") or key),
                "required": bool(row.get("required", True)),
            }
        )
    return out


def _parse_connectors(raw: bytes | None) -> list[dict[str, Any]]:
    """Parse a pack's top-level ``connectors.json`` into a list of declarations (§3.3).

    Each declaration is ``{name, category, ...}`` and carries NO credential value (§2.2: the
    schema bans value-bearing auth fields). A missing/unparseable/mis-shaped file yields no
    declarations (a pack without connectors is the common case)."""
    if not raw:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and str(d.get("name", "")).strip()]


def _setup_skill_files(quarantine: Path, members: dict[str, bytes]) -> list[dict[str, Any]] | None:
    """A pack's ``setup/SKILL.md`` file set (§3.4), rebased to skill-dir-relative entries.

    The pack ships the setup interview as ``setup/…`` members (``setup/SKILL.md`` required).
    We collect them the same way :func:`_skill_files_from_members` collects a component
    skill, stripping the ``setup/`` prefix so ``install_guarded`` writes a normal skill dir.
    Returns ``None`` when the pack ships no ``setup/SKILL.md`` (nothing to install)."""
    from personalclaw.skills.marketplace import read_skill_file_entry

    if "setup/SKILL.md" not in members:
        return None
    files: list[dict[str, Any]] = []
    for name in sorted(members):
        if not name.startswith("setup/"):
            continue
        rel = name[len("setup/") :]
        if not rel:
            continue
        files.append(read_skill_file_entry(quarantine / PurePosixPath(name), rel))
    return files or None


def _editable_config_keys(raw: bytes | None) -> list[str]:
    """The subset of a pack's ``config_subset.json`` keys that are user-editable (§3.1).

    A pack cannot edit config fields the user couldn't edit through the UI: only keys in the
    dashboard's ``_EDITABLE_CONFIG`` PATCH allowlist are staged as proposals; the rest are
    dropped. Returns the accepted keys (for the plan preview + the staged proposals file)."""
    if not raw:
        return []
    try:
        proposed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(proposed, dict):
        return []
    try:
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG
    except Exception:  # noqa: BLE001 — no allowlist reachable ⇒ stage nothing (fail closed)
        return []
    return sorted(k for k in proposed if k in _EDITABLE_CONFIG)


# ── SEL audit ─────────────────────────────────────────────────────────────────


def _audit(operation: str, outcome: str, *, resources: str = "", error: str = "") -> None:
    """Emit one pack-import security event (best-effort; audit never breaks the import)."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="packs.import",
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=resources,
            error=error,
        )
    except Exception:  # pragma: no cover - audit is best-effort
        logger.debug("pack import SEL audit failed for %s", operation, exc_info=True)


# ── the journal (crash-safe rollback ledger) ─────────────────────────────────


class _Journal:
    """Records every home write BEFORE it happens so a fault unwinds to pre-import state.

    Entries are one of: ``file`` (a component file created — undo: unlink), ``skill`` (a
    skill dir committed via ``install_scanned`` — undo: rmtree), ``mkdir`` (a directory this
    import created — undo: rmdir if empty). The ledger is flushed to
    ``packs/.installing/<id>.json`` after every append, so a process crash mid-commit still
    leaves an on-disk record a later run could unwind. :meth:`rollback` unwinds leaves-last
    (reverse order); :meth:`discard` removes the ledger on success.
    """

    def __init__(self, home: Path, import_id: str) -> None:
        self._dir = home / "packs" / ".installing"
        self._path = self._dir / f"{import_id}.json"
        self._entries: list[dict[str, str]] = []
        # Track dirs THIS import created so rollback removes only those (never a pre-existing
        # one) and success can prune the empty journal scaffold cleanly.
        self._created_installing = not self._dir.exists()
        self._created_packs = not (home / "packs").exists()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._home = home

    def _flush(self) -> None:
        self._path.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")

    def record_mkdir(self, path: Path) -> None:
        self._entries.append({"op": "mkdir", "path": str(path)})
        self._flush()

    def record_file(self, path: Path) -> None:
        self._entries.append({"op": "file", "path": str(path)})
        self._flush()

    def record_skill(self, skill_dir: Path) -> None:
        self._entries.append({"op": "skill", "path": str(skill_dir)})
        self._flush()

    def rollback(self) -> None:
        """Unwind every recorded write leaves-last (reverse), restoring pre-import state."""
        for entry in reversed(self._entries):
            path = Path(entry["path"])
            try:
                if entry["op"] == "file":
                    if path.is_file():
                        path.unlink()
                elif entry["op"] == "skill":
                    shutil.rmtree(path, ignore_errors=True)
                elif entry["op"] == "mkdir":
                    # Only remove a dir this import created, and only if now empty (a
                    # pre-existing sibling must survive).
                    if path.is_dir() and not any(path.iterdir()):
                        path.rmdir()
            except OSError:
                logger.warning("pack import rollback: could not unwind %s", path, exc_info=True)
        self.discard()

    def discard(self) -> None:
        """Remove the journal ledger + the ``.installing``/``packs`` scaffold this import
        created (never a pre-existing one), so a clean commit leaves no import residue."""
        try:
            if self._path.is_file():
                self._path.unlink()
            if self._created_installing and self._dir.is_dir() and not any(self._dir.iterdir()):
                self._dir.rmdir()
            packs_dir = self._home / "packs"
            if self._created_packs and packs_dir.is_dir() and not any(packs_dir.iterdir()):
                packs_dir.rmdir()
        except OSError:
            logger.debug("pack import: could not prune journal scaffold", exc_info=True)


def _mkdir_journaled(journal: _Journal, path: Path) -> None:
    """``mkdir -p`` while journaling each directory level this import newly creates."""
    to_create: list[Path] = []
    cur = path
    while not cur.exists():
        to_create.append(cur)
        cur = cur.parent
    for d in reversed(to_create):
        d.mkdir()
        journal.record_mkdir(d)


def _write_component_file(path: Path, text: str) -> None:
    """The single text-component write primitive (template/prompt/agent/trigger/config).

    A named seam so the whole commit path funnels through one call — the atomicity test
    injects a fault by patching THIS to raise, exercising the real rollback."""
    from personalclaw.atomic_write import atomic_write

    atomic_write(path, text)


# ── per-kind commit ───────────────────────────────────────────────────────────


def _staged_dir(home: Path, stage: str) -> Path:
    """The pack-scoped staging area for proposals a pack may NOT apply on install —
    disabled triggers + validated config_subset entries. Human-enabled from their own
    surfaces later (§3.1 propose-don't-write)."""
    return home / "packs" / "staged" / stage


def _commit_file_component(comp: _Comp, home: Path, journal: _Journal, stage: str) -> Path:
    """Serialize + write a single-file component (all but skills), journaling the write.

    Returns the written path so the caller can stamp the component's ledger lock (§1
    ``pack_owned`` update flow) from the bytes that actually landed — deriving the lock from
    anything other than the committed file would let the two disagree."""
    if comp.kind == "template":
        path = home / "workflows" / "defs" / comp.target_id / "workflow.json"
        text = json.dumps(comp.obj, indent=2, ensure_ascii=False)
    elif comp.kind == "prompt":
        import yaml  # type: ignore

        path = home / "prompts" / f"{comp.target_id}.yaml"
        text = yaml.safe_dump(comp.obj, sort_keys=False, allow_unicode=True)
    elif comp.kind == "agent":
        errors = comp.obj.validate()
        if errors:
            raise PackImportRefused(
                "lint", f"agent {comp.target_id!r} invalid after import: {'; '.join(errors)}"
            )
        path = _agents_base(home) / comp.target_id / "agent.json"
        text = json.dumps(comp.obj.to_dict(), indent=2, ensure_ascii=False)
    elif comp.kind == "trigger":
        # Staged + disabled: never into the live trigger store — a pack cannot arm
        # automation on install (§3.1). The user enables it later from Automations.
        path = _staged_dir(home, stage) / "triggers" / f"{comp.target_id}.json"
        text = json.dumps(comp.obj, indent=2, ensure_ascii=False)
    else:  # pragma: no cover - guarded by caller
        raise PackImportRefused("lint", f"cannot commit component kind {comp.kind!r}")

    _mkdir_journaled(journal, path.parent)
    journal.record_file(path)
    _write_component_file(path, text)
    return path


def _commit_skill(comp: _Comp, home: Path, marketplace_name: str, journal: _Journal) -> Path:
    """Commit a skill through ``install_guarded`` → ``.pclaw-lock.json`` (done_when 5).

    Returns the skill's committed dir (for journaling). DANGEROUS is refused by the gate
    even here (defense-in-depth); a WARNING passes only because the plan-level consent gate
    already cleared the whole import (``force`` reflects that consent).

    Journals the skills-tree parent dir BEFORE delegating: ``install_guarded`` creates
    ``skills/`` if absent, which rollback must also unwind or a faulted import would leave an
    empty ``skills/`` behind (not byte-identical). ``record_skill`` handles the skill dir; the
    journaled ``mkdir`` handles the parent."""
    from personalclaw.skills.loader import skills_dir
    from personalclaw.skills.marketplace import get_default_skills_registry

    target = skills_dir()
    _mkdir_journaled(journal, target)
    get_default_skills_registry().install_guarded(
        marketplace_name, comp.target_id, target, force=True
    )
    return target / comp.target_id


def _stage_roster(plan: ImportPlan, home: Path, journal: _Journal, stage: str) -> None:
    """Stage the pack's roster (§4.2) — installed, not deployed.

    The rows carry the FRESH ids the commit assigned, so a deploy months later resolves the
    personas that actually landed. Nothing here makes an agent live: that is
    :func:`packs.roster.deploy_roster`, and only for the ``always`` tier."""
    if not plan.roster and not plan.runbooks:
        return
    entries = [pack_roster.RosterEntry.from_dict(r) for r in plan.roster]
    books = [
        pack_roster.Runbook(
            slug=str(r.get("slug", "")),
            name=str(r.get("name", "")),
            description=str(r.get("description", "")),
            roster=[str(s) for s in (r.get("roster") or [])],
        )
        for r in plan.runbooks
    ]
    path = pack_roster.roster_path(home, stage)
    _mkdir_journaled(journal, path.parent)
    journal.record_file(path)
    _write_component_file(path, pack_roster.serialize_roster(entries, books))


def _stage_config_subset(
    members: dict[str, bytes], home: Path, journal: _Journal, stage: str
) -> None:
    """Stage the accepted ``config_subset.json`` proposals (validated, never applied)."""
    keys = _editable_config_keys(members.get("config_subset.json"))
    if not keys:
        return
    raw = members.get("config_subset.json")
    proposed = json.loads(raw.decode("utf-8")) if raw else {}
    staged = {k: proposed[k] for k in keys}
    path = _staged_dir(home, stage) / "config_subset.json"
    _mkdir_journaled(journal, path.parent)
    journal.record_file(path)
    _write_component_file(path, json.dumps(staged, indent=2, ensure_ascii=False))


# ── public API ────────────────────────────────────────────────────────────────


def inspect_pack(path: Path | str, *, tier: "Any" = None) -> ImportPlan:
    """Dry-run: compute what importing ``path`` WOULD install — with NO writes to home state.

    Opens the ZIP, extracts to a SYSTEM tempdir (never the home), recomputes ``content_hash``
    from the actual bytes, runs the referential-integrity + parse lint, and scans every
    component at ``tier`` (default COMMUNITY — an imported pack is untrusted origin, §3.5).
    Returns an :class:`ImportPlan`; raises :class:`PackImportRefused` only for an unopenable
    or path-unsafe archive (structural refusals a plan can't represent)."""
    from personalclaw.supply_chain import TrustTier

    tier = tier if tier is not None else TrustTier.COMMUNITY
    home = config_dir()
    quarantine = Path(tempfile.mkdtemp(prefix="pclaw-pack-inspect-"))
    try:
        try:
            zf = zipfile.ZipFile(str(path))
        except (zipfile.BadZipFile, OSError) as exc:
            raise PackImportRefused("integrity", f"not a readable .pclaw archive: {exc}") from exc
        with zf:
            members = _extract_quarantine(zf, quarantine)
            manifest = _read_manifest(members)
            plan, _ = _build_plan(manifest, members, quarantine, home, tier)
        _audit(
            "pack_inspect",
            "blocked" if plan.blocked else ("needs_confirm" if plan.needs_consent else "clean"),
            resources=f"{plan.name}@{plan.version}",
        )
        return plan
    finally:
        shutil.rmtree(quarantine, ignore_errors=True)


def import_pack(
    path: Path | str,
    *,
    consent: bool = False,
    tier: "Any" = None,
    connector_choices: dict[str, dict[str, Any]] | None = None,
) -> ImportPlan:
    """Import ``path`` onto this machine: inspect → refuse-or-commit, atomically.

    Runs :func:`inspect_pack`'s pipeline, then REFUSES (raising :class:`PackImportRefused`)
    on any blocking condition — integrity mismatch (``"integrity"``), a lint error
    (``"lint"``), a DANGEROUS component regardless of ``consent`` (``"dangerous"``), or a
    WARNING component without ``consent`` (``"needs_consent"``). Only a fully-clearing plan
    commits, leaves-first, journaled. Any mid-commit fault unwinds every journaled write to
    byte-identical pre-import state and re-raises as ``reason="fault"``.

    ``connector_choices`` maps a declared connector's name → its resolution input
    ``{mode, credentials?, substitute?}`` (§3.3); a connector with no choice degrades to
    ``skip`` with a ``connector_missing:<name>`` marker. Connector resolution runs AFTER the
    component commit fully succeeds — so a component-commit fault rolls back to byte-identical
    state without ever having written a credential or a server. Returns the
    :class:`ImportPlan` on success (with ``connector_resolutions`` + ``setup_skill`` filled)."""
    from personalclaw.skills.marketplace import get_default_skills_registry
    from personalclaw.supply_chain import TrustTier

    tier = tier if tier is not None else TrustTier.COMMUNITY
    tier_str = getattr(tier, "value", str(tier))
    home = config_dir()
    quarantine = Path(tempfile.mkdtemp(prefix="pclaw-pack-import-"))
    try:
        try:
            zf = zipfile.ZipFile(str(path))
        except (zipfile.BadZipFile, OSError) as exc:
            raise PackImportRefused("integrity", f"not a readable .pclaw archive: {exc}") from exc
        with zf:
            members = _extract_quarantine(zf, quarantine)
            manifest = _read_manifest(members)
            plan, parsed = _build_plan(manifest, members, quarantine, home, tier)

        # ── refusals, fail-closed, before any write ──
        if not plan.integrity_ok:
            _audit("pack_import", "refused", resources=plan.name, error=plan.integrity_detail)
            raise PackImportRefused("integrity", plan.integrity_detail, plan)
        if not plan.lint.ok:
            # The DETAIL is included, not just code+ref: for an unresolved reference the exact
            # ref that failed to resolve lives only in the detail, and a refusal that named
            # only the component reporting the problem leaves the author guessing which of its
            # N references is broken (AP-4 §4.2 requires the exact ref be named).
            detail = "; ".join(f"{f.code}:{f.ref}: {f.detail}" for f in plan.lint.errors)
            _audit("pack_import", "refused", resources=plan.name, error=f"lint: {detail}")
            raise PackImportRefused("lint", f"referential-integrity lint failed: {detail}", plan)
        if plan.has_dangerous:
            bad = ", ".join(c.ref for c in plan.components if c.verdict == "dangerous")
            _audit("pack_import", "refused", resources=plan.name, error=f"dangerous: {bad}")
            raise PackImportRefused(
                "dangerous", f"refused — DANGEROUS component(s), never overridable: {bad}", plan
            )
        if plan.needs_consent and not consent:
            warn = ", ".join(c.ref for c in plan.components if c.verdict == "warning")
            _audit("pack_import", "needs_confirm", resources=plan.name, error=f"warning: {warn}")
            raise PackImportRefused(
                "needs_consent", f"needs explicit consent — WARNING component(s): {warn}", plan
            )

        # ── commit, leaves-first, journaled ──
        stage = plan.name or "pack"
        import_id = uuid.uuid4().hex[:16]
        journal = _Journal(home, import_id)
        registry = get_default_skills_registry()
        mp_name = f"pack-import:{plan.name}:{import_id}"
        skill_files = {c.target_id: (c.skill_files or []) for c in parsed if c.kind == "skill"}
        registry.register(mp_name, PackMarketplace(skill_files, tier_str))
        committed: dict[str, Path] = {}
        try:
            for comp in parsed:  # already sorted leaves-first
                if comp.kind == "skill":
                    skill_dir = _commit_skill(comp, home, mp_name, journal)
                    journal.record_skill(skill_dir)
                    committed[f"{comp.kind}:{comp.id}"] = skill_dir
                else:
                    committed[f"{comp.kind}:{comp.id}"] = _commit_file_component(
                        comp, home, journal, stage
                    )
            _stage_config_subset(members, home, journal, stage)
            _stage_roster(plan, home, journal, stage)
        except Exception as exc:
            # A fault at ANY point unwinds every journaled write leaves-last — no partial pack.
            journal.rollback()
            _audit("pack_import", "rolled_back", resources=plan.name, error=str(exc))
            if isinstance(exc, PackImportRefused):
                raise
            raise PackImportRefused("fault", f"import faulted and was rolled back: {exc}", plan)
        finally:
            registry.unregister(mp_name)

        journal.discard()

        # ── requirements resolution (§3.3) + durable ledger (§9), post-commit ──
        # Runs only after the component commit fully succeeded (the journal is discarded), so
        # a rolled-back import never writes a credential or a server. Connector resolution is
        # fail-soft: a configure/substitute that can't complete degrades to a skip marker, so
        # a bad credential can't undo an already-committed pack.
        _resolve_and_record(plan, home, connector_choices, committed)

        _audit(
            "pack_import",
            "installed",
            resources=f"{plan.name}@{plan.version} ({len(plan.components)} component(s))",
        )
        return plan
    finally:
        shutil.rmtree(quarantine, ignore_errors=True)


def _resolve_and_record(
    plan: ImportPlan,
    home: Path,
    connector_choices: dict[str, dict[str, Any]] | None,
    committed: dict[str, Path] | None = None,
) -> None:
    """Resolve the pack's connector requirements and write the installed-pack ledger.

    Connector resolution uses the fail-soft importer path (:func:`connectors.resolve_for_import`)
    so a failed configure degrades to a ``connector_missing:<name>`` marker rather than
    aborting an already-committed pack. The ledger (:mod:`packs.installed`) is the durable
    reader surface: the connector markers a feature checks for availability, and the
    ``setup_pending`` flag the re-runnable "Finish setup" chip reads.
    """
    from datetime import datetime, timezone

    from personalclaw.packs import connectors as pack_connectors
    from personalclaw.packs import update as pack_update
    from personalclaw.packs.installed import InstalledPack, record_install

    resolutions = pack_connectors.resolve_for_import(plan.connectors, connector_choices, home=home)
    plan.connector_resolutions = [r.to_dict() for r in resolutions]
    markers = [r.marker for r in resolutions if r.marker]

    for r in resolutions:
        _audit(
            "pack_connector_resolve",
            r.mode if not r.error else "skipped",
            resources=f"{plan.name}:{r.name}",
            error=r.error,
        )

    record_install(
        InstalledPack(
            name=plan.name or "pack",
            version=plan.version,
            components=[c.ref for c in plan.components],
            connectors=plan.connector_resolutions,
            connector_markers=markers,
            setup_skill=plan.setup_skill,
            setup_pending=bool(plan.setup_skill),
            installed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            bindings=[dict(b) for b in plan.bindings],
            roster=[dict(r) for r in plan.roster],
            pack_owned=list(plan.pack_owned),
            component_locks=pack_update.stamp_locks(plan, home, committed or {}),
        ),
        home,
    )


def _read_manifest(members: dict[str, bytes]) -> dict[str, Any]:
    raw = members.get("pack.json")
    if raw is None:
        raise PackImportRefused("integrity", "pack has no pack.json manifest")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PackImportRefused("integrity", f"pack.json is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PackImportRefused("integrity", "pack.json must be a JSON object")
    return manifest
