"""Skill sidecar overlays.

An accepted skill refinement applies as a SIDECAR OVERLAY rather than mutating the base
``SKILL.md``: a single file — ``<skills_dir>/.overlays/<name>.json`` — holds the accepted
refinements (few-shot exemplars + description notes) and is merged onto the base body at LOAD
time. Three properties fall out, all load-bearing:

* **The skill's own file holds only what its author wrote.** A refinement is applied once, from
  here, each time the skill loads, and never written into ``SKILL.md``: every writer of that file
  stores the author's text with any copy of an applied refinement left out of it
  (:func:`without_copies`, through ``SkillsLoader``'s one writer). Two writers used to save the
  skill as it LOADS back into its file — the Skills page's editor and the curator — and the
  refinement then loaded twice, and a revert removed only this copy of it.
* **Revert removes a refinement completely.** Reverting one takes it out of this file, and
  reverting the last (or all) deletes the file, so the skill loads as its author wrote it.
* **`install_guarded` locks stay intact.** The overlay lives OUTSIDE the skill directory, so
  ``verify_skill_integrity`` — which hashes every file *inside* ``<skill>/`` against
  ``.pclaw-lock.json`` and flags any extra file as ``added`` — never sees it. The base bytes and
  their hashes are never rewritten, so a marketplace-locked skill stays verifiable.

This mirrors the trivial-rollback property templates get from version pinning (``versions.py``):
the base is immutable, the accepted change is a separate, deletable layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.skills.loader import hold_library, skills_dir

logger = logging.getLogger(__name__)

_OVERLAYS_DIRNAME = ".overlays"

# How a stumble trigger reads in the skill body. Mapped rather than interpolated raw so an
# unknown/absent trigger renders as NOTHING instead of leaking a raw enum into the prompt.
_TRIGGER_PHRASE = {
    "correction": "from a correction",
    "failure_retry": "from a failed-then-retried step",
    "rejection": "from a rejected action",
}

# Where a skill's own text starts: after its frontmatter, found the way the loader's parser finds
# it (leading whitespace ignored, closed at the first ``\n---``).
_FRONTMATTER = re.compile(r"^\s*---\r?\n.*?\r?\n---", re.DOTALL)


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
    NOT a stored field.** Accepting appends and reverting one removes it, so the list stays dense
    and position already *is* the version: a refinement after a reverted one moves up a place,
    and its version with it. A second copy on each record could disagree with it, and a number
    maintained beside the collection it describes is the drift this codebase has been bitten by
    before. Readers derive it (:func:`applied`), writers return it (:func:`apply_overlay`). What
    names one refinement across a revert is its content (:attr:`Applied.id`).

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


@dataclass(frozen=True)
class Applied:
    """One accepted refinement as its skill loads it: its version, its id and its block.

    ``id`` is a digest of the stored record, so it names the same refinement after another one is
    reverted and its version moves; a revert by id can never take out the refinement that slid
    into a reverted one's place.
    """

    version: int
    id: str
    record: dict[str, Any]
    block: str

    def to_dict(self) -> dict[str, Any]:
        """The refinement as the Skills page shows it, ``text`` being the block that loads."""
        return {
            "id": self.id,
            "version": self.version,
            "description": str(self.record.get("description") or ""),
            "created_at": str(self.record.get("created_at") or ""),
            "trigger": str(self.record.get("trigger") or ""),
            "text": self.block,
        }


def refinement_id(record: dict[str, Any]) -> str:
    """The id of a stored refinement record: a digest of its canonical JSON."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def load_overlay(name: str) -> dict[str, Any] | None:
    path = overlay_path(name)
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("overlays: unreadable overlay for %s", name, exc_info=True)
        return None


def _records(name: str) -> list[Any]:
    data = load_overlay(name)
    refinements = data.get("refinements") if isinstance(data, dict) else None
    return refinements if isinstance(refinements, list) else []


def refinement_count(name: str) -> int:
    """How many refinements *name* has already accepted (0 when it has no overlay)."""
    return len(_records(name))


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
    refinements = _records(name)
    if not refinements:
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
    refinements a skill has taken, reverting them all is still the deletion of exactly one file.
    Never touches the skill directory or its ``.pclaw-lock.json``. Read and written under the
    library's lock (``loader.hold_library``), so a revert or another accept cannot land between.

    Returns the 1-based version assigned to this refinement (never 0 on success), which is how
    the accept path can say WHICH version it wrote. The old ``Path`` return had no reader: not
    one caller looked at it, so an accept could not report what it had done.
    """
    path = overlay_path(name)
    if path is None:
        raise ValueError(f"{name!r} is not a safe skill name")
    with hold_library():
        refinements = _records(name)
        refinements.append(Refinement(description, procedure_md, created_at, trigger).to_dict())
        _store(name, path, refinements)
    return len(refinements)


def _store(name: str, path: Path, refinements: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path, json.dumps({"skill": name, "refinements": refinements}, indent=2, ensure_ascii=False)
    )


def revert_overlay(name: str) -> int:
    """Revert every accepted refinement of *name*: delete its overlay file. Returns how many
    refinements that removed (0 when it had none).

    One ``unlink``. The base skill and its lock are untouched, so ``verify_skill_integrity`` reads
    exactly as it did before the overlay was ever applied. A copy an earlier save wrote into the
    skill's own file is put back to the author's text first, by ``SkillsLoader.revert_refinements``.
    """
    path = overlay_path(name)
    if path is None:
        return 0
    with hold_library():
        if not path.is_file():
            return 0
        count = len(_records(name))
        try:
            path.unlink()
        except OSError:
            logger.warning("overlays: could not revert overlay for %s", name, exc_info=True)
            return 0
    return count


def revert_refinement(name: str, ref_id: str) -> bool:
    """Revert ONE accepted refinement of *name*, the one :attr:`Applied.id` names. True when it
    was applied and is not any more; False when no applied refinement has that id.

    The others stay, in order, each a place earlier after it. Reverting the last one deletes the
    overlay file, so the skill loads exactly as its author wrote it again.
    """
    path = overlay_path(name)
    if path is None or not ref_id:
        return False
    with hold_library():
        refinements = _records(name)
        at = next(
            (
                i
                for i, r in enumerate(refinements)
                if isinstance(r, dict) and refinement_id(r) == ref_id
            ),
            None,
        )
        if at is None:
            return False
        del refinements[at]
        if any(isinstance(r, dict) for r in refinements):
            _store(name, path, refinements)
        else:
            path.unlink(missing_ok=True)
    return True


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


def applied(name: str) -> list[Applied]:
    """The refinements applied on top of *name* when it loads, in the order they load.

    Fault-tolerant by contract (a corrupt overlay can't break base loading): a missing or
    unreadable overlay applies nothing.
    """
    out: list[Applied] = []
    # `i` IS the version (1-based position in a dense list) — see `Refinement`.
    for i, record in enumerate(_records(name), start=1):
        if not isinstance(record, dict):
            continue
        block = render_block(record, i)
        if block.strip():
            out.append(Applied(i, refinement_id(record), record, block))
    return out


def without_copies(text: str, blocks: Sequence[str]) -> str:
    """*text*, a skill's own text, with every copy of one of *blocks* written into it left out.

    A copy is a block exactly as it loads, its generated ``## Refinement vN`` heading included,
    standing as a part of its own: after a blank line, and followed by a blank line or by nothing
    but the end. That is the shape a save of the skill as it loaded left behind, once or more,
    whatever the author typed before or after it. A block the author changed is the author's text
    and stays, and so does the text of a refinement that is no longer applied: nothing is left to
    tell it from the author's. Only the text after the frontmatter is searched.
    """
    units = [b.rstrip() for b in blocks if b.strip()]
    if not units:
        return text
    head = _FRONTMATTER.match(text)
    start = head.end() if head else 0
    out = text
    removed = True
    while removed:
        removed = False
        for unit in units:
            needle = "\n\n" + unit
            at = out.find(needle, start)
            while at != -1:
                end = at + len(needle)
                if out.startswith("\n\n", end) or not out[end:].strip():
                    out = out[:at] + out[end:]
                    removed = True
                    at = out.find(needle, at)
                else:
                    at = out.find(needle, at + 1)
    return out


def own_text(name: str, text: str) -> str:
    """*text*, the file of the skill *name*, as its author wrote it: :func:`without_copies` of the
    refinements applied on top of *name*."""
    return without_copies(text, [a.block for a in applied(name)])


def render(own: str, blocks: Sequence[str]) -> str:
    """A skill as it loads: its own text, then each applied refinement's block, once."""
    if not blocks:
        return own
    return own.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"


def render_with_overlay(name: str, body: str) -> str:
    """Append the skill's accepted refinements to ``body`` at load time — never mutating it.

    Each refinement is applied once: a copy of one that ``body`` already holds (a save of the
    skill as it loaded wrote it there) is left out first. A missing or unreadable overlay returns
    the base body unchanged. The base is the source of truth; the overlay is an additive layer
    rendered beneath it.
    """
    blocks = [a.block for a in applied(name)]
    return render(without_copies(body, blocks), blocks)
