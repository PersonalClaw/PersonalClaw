"""Skill sidecar overlays — WF2LEA-6 (§3.1 "Skill application substrate").

An accepted skill refinement applies as a SIDECAR OVERLAY rather than mutating the base
``SKILL.md``: a single file — ``<skills_dir>/.overlays/<name>.json`` — holds the accepted
refinements (few-shot exemplars + description notes) and is merged onto the base body at LOAD
time. Two properties fall out, both load-bearing:

* **Revert = delete ONE file.** Removing the overlay restores the base exactly, so a bad
  refinement is undone without touching (or being able to corrupt) the skill it refines.
* **`install_guarded` locks stay intact.** The overlay lives OUTSIDE the skill directory, so
  ``verify_skill_integrity`` — which hashes every file *inside* ``<skill>/`` against
  ``.pclaw-lock.json`` and flags any extra file as ``added`` — never sees it. The base bytes and
  their hashes are never rewritten, so a marketplace-locked skill stays verifiable.

This mirrors the trivial-rollback property templates get from version pinning (``versions.py``):
the base is immutable, the accepted change is a separate, deletable layer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.skills.loader import skills_dir

logger = logging.getLogger(__name__)

_OVERLAYS_DIRNAME = ".overlays"


def _safe_parts(name: str) -> list[str] | None:
    """The path components of a skill name, or None if it is unsafe.

    Skill names legitimately carry a namespace slash (``auto/release-flow``), so the overlay
    mirrors that under ``.overlays/``. Traversal (``..``), empty, or absolute components are
    refused — the overlay path must never escape the overlays directory.
    """
    if not name or name.startswith("/") or "\\" in name:
        return None
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return parts


def overlays_dir() -> Path:
    return skills_dir() / _OVERLAYS_DIRNAME


def overlay_path(name: str) -> Path | None:
    parts = _safe_parts(name)
    if parts is None:
        return None
    return overlays_dir().joinpath(*parts).with_suffix(".json")


@dataclass
class Refinement:
    description: str = ""
    procedure_md: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "description": self.description,
            "procedure_md": self.procedure_md,
            "created_at": self.created_at,
        }


def load_overlay(name: str) -> dict[str, Any] | None:
    path = overlay_path(name)
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("overlays: unreadable overlay for %s", name, exc_info=True)
        return None


def apply_overlay(
    name: str, *, description: str = "", procedure_md: str = "", created_at: str = ""
) -> Path | None:
    """Append an accepted refinement to the skill's ONE overlay file, creating it if absent.

    Accumulating into a single file per skill keeps the revert primitive honest: however many
    refinements a skill has taken, reverting is still the deletion of exactly one file. Never
    touches the skill directory or its ``.pclaw-lock.json``.
    """
    path = overlay_path(name)
    if path is None:
        raise ValueError(f"{name!r} is not a safe skill name")
    data = load_overlay(name) or {"skill": name, "refinements": []}
    refinements = data.get("refinements")
    if not isinstance(refinements, list):
        refinements = []
    refinements.append(Refinement(description, procedure_md, created_at).to_dict())
    data["skill"] = name
    data["refinements"] = refinements
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def revert_overlay(name: str) -> int:
    """Delete a skill's overlay file. Returns the number of files removed (0 or 1).

    This is the whole rollback: one ``unlink``. The base skill and its lock are untouched, so
    ``verify_skill_integrity`` reads exactly as it did before the overlay was ever applied.
    """
    path = overlay_path(name)
    if path is None or not path.is_file():
        return 0
    try:
        path.unlink()
        return 1
    except OSError:
        logger.warning("overlays: could not revert overlay for %s", name, exc_info=True)
        return 0


def _render_block(ref: dict[str, str]) -> str:
    stamp = (ref.get("created_at") or "").split("T", 1)[0]
    heading = f"## Refinement ({stamp})" if stamp else "## Refinement"
    lead = re.sub(r"\s+", " ", ref.get("description") or "").strip()
    lines = [heading, ""]
    if lead:
        lines += [f"_{lead}_", ""]
    lines.append((ref.get("procedure_md") or "").replace("\r\n", "\n").strip())
    return "\n".join(lines)


def render_with_overlay(name: str, body: str) -> str:
    """Append the skill's accepted refinements to ``body`` at load time — never mutating it.

    Fault-tolerant by contract (§3.1: "a corrupt overlay can't break base loading"): a missing
    or unreadable overlay returns the base body unchanged. The base is the source of truth; the
    overlay is an additive layer rendered beneath it.
    """
    data = load_overlay(name)
    if not data:
        return body
    refinements = data.get("refinements")
    if not isinstance(refinements, list) or not refinements:
        return body
    blocks = [_render_block(r) for r in refinements if isinstance(r, dict)]
    blocks = [b for b in blocks if b.strip()]
    if not blocks:
        return body
    return body.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"
