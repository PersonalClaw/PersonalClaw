"""Bundled first-party Domain OS packs — the authoring path (AGENT-PACKS §4.1, AP-4).

Two packs ship in the wheel: **Personal CFO** (budget-review template + a spending-digest
trigger + finance skills + a finance-category connector requirement + a CFO roster) and
**Health OS** (checkup-cadence trigger + journaling template + health skills + a health
roster). They are the reference for third-party authors and the acceptance test for the whole
mechanism (§Success 1): each one exports, wipes, and imports onto a fresh
``PERSONALCLAW_HOME`` with its skills locked, its template runnable, its trigger DISABLED,
its connector prompting configure-or-substitute, and its setup interview asking for a folder.

**Why a source tree and not a checked-in ``.pclaw``.** A binary ZIP in the repo is a file
nobody can review in a diff and nobody can regenerate from anything. So the packs live as
plain source trees under ``packs/bundled/<name>/`` and :func:`build_bundled` assembles one on
demand. That assembly IS the export leg of the round trip: it runs the same content-layer
secret scan (:func:`packs.build._scan_component`) over every shipped member and derives the
same ``sha256``/``content_hash`` provenance :func:`packs.build.build_pack` derives, so the
archive an importer verifies is produced by one derivation, not two.

**The author declares edges; the builder derives hashes.** Each pack's ``pack.json`` lists
``components`` with ``kind``/``id``/``path``/``depends_on`` and NO hashes — hand-maintained
hashes drift the moment someone edits a skill. The builder fills them in, and fails closed
in both directions: a declared component with no file, or a component-shaped file nobody
declared, raises rather than shipping a pack that quietly differs from its manifest.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

#: Where the authored source trees live inside the package (wheel package-data): one
#: directory per pack, right here beside this module — the ``workflows/bundled`` and
#: ``skills/bundled`` convention, so a fresh ``pip install`` has the packs with no network.
BUNDLED_DIR = Path(__file__).parent

#: Pack-relative path prefixes that carry a manifest COMPONENT. A file under one of these
#: that no manifest row claims is an authoring error (fail closed) rather than dead weight.
_COMPONENT_PREFIXES = ("skills/", "templates/", "prompts/", "triggers/")

#: Members that legitimately ship WITHOUT a component row — they describe components
#: (roster/runbooks), declare requirements (connectors), propose config, or are the setup
#: interview. ``agents/`` is special: ``agents/<slug>.md`` is a component but
#: ``agents/catalog.json`` and ``agents/runbooks/*`` are not.
_NON_COMPONENT_MEMBERS = (
    "pack.json",
    "connectors.json",
    "config_subset.json",
    "agents/catalog.json",
)
_NON_COMPONENT_PREFIXES = ("setup/", "agents/runbooks/")


class BundledPackError(Exception):
    """A bundled pack's source tree and its manifest disagree — refuse, never ship."""


@dataclass(frozen=True)
class BundledPack:
    """One shipped pack's identity, as read from its authored ``pack.json``."""

    name: str
    version: str
    display_name: str
    description: str
    source: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "displayName": self.display_name,
            "description": self.description,
        }


def _read_source_manifest(source: Path) -> dict[str, Any]:
    raw = (source / "pack.json").read_text(encoding="utf-8")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise BundledPackError(f"{source.name}: pack.json must be a JSON object")
    return manifest


def bundled_packs() -> list[BundledPack]:
    """Every pack shipped in the wheel, sorted by name.

    A source dir whose ``pack.json`` is missing or unreadable is SKIPPED with a warning
    rather than raising: one broken authored pack must not make the whole Store unlistable.
    :func:`build_bundled` is where a broken pack fails loudly, because that is the moment it
    matters.
    """
    out: list[BundledPack] = []
    if not BUNDLED_DIR.is_dir():
        return out
    for source in sorted(p for p in BUNDLED_DIR.iterdir() if p.is_dir()):
        # A pack name is kebab-case; `__pycache__` and any other dunder dir is this module's
        # own baggage, not a pack, so it is skipped WITHOUT a warning (warning on it would
        # cry wolf on every call in a normal installed tree).
        if source.name.startswith("_"):
            continue
        try:
            manifest = _read_source_manifest(source)
        except (OSError, ValueError, BundledPackError):
            logger.warning("bundled pack %s has no readable pack.json", source.name)
            continue
        name = str(manifest.get("name", "") or source.name)
        out.append(
            BundledPack(
                name=name,
                version=str(manifest.get("version", "") or "0.0.0"),
                display_name=str(manifest.get("displayName", "") or name),
                description=str(manifest.get("description", "") or ""),
                source=source,
            )
        )
    return out


def get_bundled(name: str) -> BundledPack | None:
    """One shipped pack by name, or None."""
    return next((p for p in bundled_packs() if p.name == name), None)


def _collect_members(source: Path) -> dict[str, bytes]:
    """Every file in the source tree as pack-relative member name → bytes.

    Symlinks are refused outright: a bundled pack is authored content, and a symlink in it
    would let a member's bytes come from outside the tree the reviewer read.
    """
    members: dict[str, bytes] = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise BundledPackError(
                f"{source.name}: symlinked member {path.relative_to(source).as_posix()!r}"
            )
        if not path.is_file():
            continue
        members[path.relative_to(source).as_posix()] = path.read_bytes()
    return members


def _is_component_member(name: str) -> bool:
    if name in _NON_COMPONENT_MEMBERS or name.startswith(_NON_COMPONENT_PREFIXES):
        return False
    if name.startswith("agents/") and name.endswith(".md"):
        return True
    return name.startswith(_COMPONENT_PREFIXES)


def build_bundled(name: str, out_path: Path) -> Path:
    """Assemble bundled pack ``name`` into a ``.pclaw`` at ``out_path``; return the path.

    The export leg of §Success 1's round trip. Steps, all fail-closed:

    1. read the authored ``pack.json`` and collect every member;
    2. resolve each declared component to its member bytes (a missing one raises);
    3. assert no component-shaped member is undeclared (a silent drop raises);
    4. run the CONTENT redaction layer over every shipped member — a credential-bearing
       bundled pack is a release blocker, not a warning;
    5. derive per-component ``sha256`` + ``provenance.content_hash`` exactly as
       :func:`packs.build.build_pack` does, and write the ZIP.
    """
    from personalclaw import __version__ as pclaw_version
    from personalclaw.packs.build import SCHEMA_VERSION, _scan_component

    pack = get_bundled(name)
    if pack is None:
        raise BundledPackError(f"no bundled pack named {name!r}")
    manifest = _read_source_manifest(pack.source)
    members = _collect_members(pack.source)

    declared = manifest.get("components") or []
    if not isinstance(declared, list) or not declared:
        raise BundledPackError(f"{name}: pack.json declares no components")

    components: list[dict[str, Any]] = []
    claimed: set[str] = set()
    for row in declared:
        if not isinstance(row, dict):
            raise BundledPackError(f"{name}: a components row is not an object")
        path = str(row.get("path", "") or "")
        raw = members.get(path)
        if raw is None:
            raise BundledPackError(
                f"{name}: component {row.get('kind')}:{row.get('id')} declares "
                f"{path!r} but the source tree has no such file"
            )
        claimed.add(path)
        # A skill component's row names its SKILL.md, but its whole dir ships — mark every
        # sibling claimed so a progressive-disclosure reference file is not "undeclared".
        if str(row.get("kind")) == "skill":
            prefix = f"skills/{row.get('id')}/"
            claimed.update(m for m in members if m.startswith(prefix))
        components.append(
            {
                "kind": str(row.get("kind", "")),
                "id": str(row.get("id", "")),
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "depends_on": [str(d) for d in (row.get("depends_on") or [])],
            }
        )

    undeclared = sorted(m for m in members if _is_component_member(m) and m not in claimed)
    if undeclared:
        raise BundledPackError(
            f"{name}: component-shaped member(s) no manifest row claims: {', '.join(undeclared)}"
        )

    for member, raw in sorted(members.items()):
        reason, categories = _scan_component(raw)
        if reason:
            raise BundledPackError(
                f"{name}: member {member!r} blocked by the content layer: {reason} "
                f"[{', '.join(categories)}]"
            )

    content_hash = hashlib.sha256(
        "".join(sorted(c["sha256"] for c in components)).encode("utf-8")
    ).hexdigest()
    out_manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": pack.name,
        "version": pack.version,
        "displayName": pack.display_name,
        "description": pack.description,
        "author": str(manifest.get("author", "") or ""),
        "license": str(manifest.get("license", "") or ""),
        "pclaw_requires": str(manifest.get("pclaw_requires", "") or ""),
        "components": components,
        "requirements": [r for r in (manifest.get("requirements") or []) if isinstance(r, dict)],
        "pack_owned": [str(p) for p in (manifest.get("pack_owned") or [])],
        "provenance": {
            "exported_by_version": pclaw_version,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "content_hash": content_hash,
        },
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("pack.json", json.dumps(out_manifest, indent=2, ensure_ascii=False))
        for member, raw in sorted(members.items()):
            if member == "pack.json":
                continue  # the derived manifest replaces the authored one
            zf.writestr(str(PurePosixPath(member)), raw)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(buf.getvalue())
    logger.info("bundled pack %s built at %s (%d component(s))", name, out_path, len(components))
    return out_path
