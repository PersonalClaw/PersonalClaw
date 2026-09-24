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

# How a stumble trigger reads in the skill body. Mapped rather than interpolated raw so an
# unknown/absent trigger renders as NOTHING instead of leaking a raw enum into the prompt.
_TRIGGER_PHRASE = {
    "correction": "from a correction",
    "failure_retry": "from a failed-then-retried step",
    "rejection": "from a rejected action",
}


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
    """One accepted refinement — the overlay's unit of VERSION.

    **A refinement's version is its 1-based POSITION in ``refinements``, and is deliberately
    NOT a stored field.** The list is append-only and dense, so position already *is* the
    version; a second copy on each record could disagree with it, and a number maintained
    beside the collection it describes is the drift this codebase has been bitten by before.
    Readers derive it (:func:`render_block`), writers return it (:func:`apply_overlay`).

    ``trigger`` is the one thing position cannot derive: which stumble produced this
    refinement (``after_turn_review.STUMBLE_TRIGGERS``), or ``""`` for a model-proposed refine.
    """

    description: str = ""
    procedure_md: str = ""
    created_at: str = ""
    trigger: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "procedure_md": self.procedure_md,
            "created_at": self.created_at,
            "trigger": self.trigger,
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


def refinement_count(name: str) -> int:
    """How many refinements *name* has already accepted (0 when it has no overlay)."""
    data = load_overlay(name)
    if not data:
        return 0
    refinements = data.get("refinements")
    return len(refinements) if isinstance(refinements, list) else 0


def next_version(name: str) -> int:
    """The version the NEXT accepted refinement of *name* will carry (1-based).

    Public because the refine proposal's diff has to name the version it would create
    BEFORE it is accepted — a diff that showed an unnumbered block would be a diff of
    something other than what accept writes.
    """
    return refinement_count(name) + 1


def last_refinement(name: str) -> dict[str, Any] | None:
    """The most recently accepted refinement record, or None.

    The daily refine cap reads this: an accepted proposal is DELETED from the queue, so the
    queue alone cannot answer "did this skill already take a refinement today?".
    """
    data = load_overlay(name)
    if not data:
        return None
    refinements = data.get("refinements")
    if not isinstance(refinements, list) or not refinements:
        return None
    last = refinements[-1]
    return last if isinstance(last, dict) else None


def apply_overlay(
    name: str,
    *,
    description: str = "",
    procedure_md: str = "",
    created_at: str = "",
    trigger: str = "",
) -> int:
    """Append an accepted refinement to the skill's ONE overlay file; return its VERSION.

    Accumulating into a single file per skill keeps the revert primitive honest: however many
    refinements a skill has taken, reverting is still the deletion of exactly one file. Never
    touches the skill directory or its ``.pclaw-lock.json``.

    Returns the 1-based version assigned to this refinement (never 0 on success), which is how
    the accept path can say WHICH version it wrote. The old ``Path`` return had no reader: not
    one caller looked at it, so an accept could not report what it had done.
    """
    path = overlay_path(name)
    if path is None:
        raise ValueError(f"{name!r} is not a safe skill name")
    data = load_overlay(name) or {"skill": name, "refinements": []}
    refinements = data.get("refinements")
    if not isinstance(refinements, list):
        refinements = []
    refinements.append(Refinement(description, procedure_md, created_at, trigger).to_dict())
    data["skill"] = name
    data["refinements"] = refinements
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
    return len(refinements)


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


def render_block(ref: dict[str, Any], version: int) -> str:
    """Render ONE refinement as the markdown block that lands in the loaded skill body.

    The heading carries the version and, when known, the stumble that produced it —
    ``## Refinement v2 (2026-08-25, from a correction)``. That heading is the provenance:
    it is the only place the *reader of the skill* (the model, and the user looking at the
    prompt preview) can tell two accepted refinements apart. Before it, two refinements
    accepted on the same day rendered byte-identical headings.

    Public because the refine proposal's diff must be built from the SAME renderer that
    accept will run; two renderers would let the previewed diff differ from the applied one.
    """
    stamp = (ref.get("created_at") or "").split("T", 1)[0]
    trigger = str(ref.get("trigger") or "").strip()
    label = f"v{version}" if version > 0 else ""
    detail = ", ".join(p for p in (stamp, _TRIGGER_PHRASE.get(trigger, "")) if p)
    heading = " ".join(p for p in ("## Refinement", label) if p)
    if detail:
        heading = f"{heading} ({detail})"
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
    # `i` IS the version (1-based position in an append-only, dense list) — see `Refinement`.
    blocks = [render_block(r, i) for i, r in enumerate(refinements, start=1) if isinstance(r, dict)]
    blocks = [b for b in blocks if b.strip()]
    if not blocks:
        return body
    return body.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"
