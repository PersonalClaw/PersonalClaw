"""App-owned skill seeding — an app ships and OWNS SKILL.md skills.

An app declares the skill DIRECTORIES it ships in its manifest's ``skills`` list
(``{path: "skills/my-skill/"}``, dir paths relative to the app dir, each holding a
``SKILL.md``). This mirrors :mod:`personalclaw.apps.prompt_seed` for prompts, with
one hard rule the prompt path doesn't need: **a skill never bypasses the
supply-chain gate just because it arrived inside an app.**

On ``enable`` (and the always-on bundled-discovery path at startup),
:func:`seed_app_skills` installs each declared skill dir into the user skills tree
THROUGH the shared chokepoint (:func:`personalclaw.skills.marketplace.install_scanned`
→ quarantine → ``scan_dir`` at the app's trust tier → ``.pclaw-lock.json`` with
per-file sha256 + SEL audit — DANGEROUS refuses always). Idempotent +
non-clobbering: a skill dir of the same name that already exists (possibly a
user's own, possibly a prior seed) is left untouched, exactly the prompt-seed
contract. On ``/update`` the old app's skills are removed pre-swap and the new
ones re-seeded post-swap, so a changed skill re-passes the scan.

On ``disable``/uninstall, :func:`remove_app_skills` removes ONLY the skills this
app shipped — keyed by the ``.pclaw-lock.json`` provenance (``source ==
app:<name>``), never a user's own skill (no lock, or a different source). And only
one still exactly as the app seeded it: a seeded skill its owner changed is theirs,
by the rule :mod:`personalclaw.skills.shipped` keeps for the skills PersonalClaw
ships. It is kept, and its record re-owned (:data:`KEPT_SOURCE`), so neither this
removal nor a later one takes it, and a later seed never writes over it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from personalclaw.skills.marketplace import (
    LOCK_FILENAME,
    DamagedInstallRecord,
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    file_digests,
    files_digest,
    install_record,
    read_skill_file_entry,
)

logger = logging.getLogger(__name__)

#: The ``source`` a seeded skill's record names once its owner changed it and its app went: theirs.
KEPT_SOURCE = "kept"


def _app_source(app_name: str) -> str:
    """The ``.pclaw-lock.json`` source tag for an app-seeded skill."""
    return f"app:{app_name}"


def _tier_string_for_origin(origin: str) -> str:
    """The scanner trust-tier STRING for an app install origin.

    Reuses the single mapping in :mod:`personalclaw.apps.app_manager` (imported
    lazily to avoid an import cycle) so app skills scan at the same tier the app
    itself installed at — first-party/native content is trusted (scan advisory),
    a local/external app is community (the full gate)."""
    try:
        from personalclaw.apps.app_manager import _tier_for_origin

        return _tier_for_origin(origin).value
    except Exception:
        from personalclaw.supply_chain import TrustTier

        return TrustTier.COMMUNITY.value


def _resolve_skill_dir(app_dir: Path, rel: str) -> Path | None:
    """Resolve a declared skill dir path, guarding traversal + requiring SKILL.md.

    Returns the resolved dir path, or None if it escapes the app dir, is missing,
    or has no ``SKILL.md``."""
    rel = rel.strip().strip("/")
    if not rel:
        return None
    target = (app_dir / rel).resolve()
    if not str(target).startswith(str(app_dir.resolve())):
        logger.warning("app skill path escapes app dir: %r", rel)
        return None
    if not target.is_dir() or not (target / "SKILL.md").is_file():
        logger.debug("app skill dir missing or has no SKILL.md: %r", rel)
        return None
    return target


class _AppSkillsMarketplace(SkillsMarketplace):
    """A transient, single-app skills source rooted at the app dir.

    Not registered in the shared :class:`SkillsRegistry` — an app is not a public
    marketplace. It exists only so app-owned skill dirs flow through the exact same
    :func:`install_scanned` gate as any other install (quarantine → scan → commit →
    lock), inheriting the app's trust tier. ``fetch`` reads one declared dir into a
    :class:`SkillDetail`; ``search`` is unused (seeding never searches)."""

    def __init__(self, app_dir: Path, trust_tier: str) -> None:
        self._app_dir = Path(app_dir)
        self._trust_tier = trust_tier

    @property
    def marketplace_type(self) -> str:
        return "app"

    @property
    def trust_tier(self) -> str:
        return self._trust_tier

    def search(self, query: str, limit: int = 20) -> list[SkillEntry]:  # pragma: no cover - unused
        return []

    def fetch(self, skill_id: str) -> SkillDetail:
        skill_dir = _resolve_skill_dir(self._app_dir, skill_id)
        if skill_dir is None:
            raise RuntimeError(f"app skill not found: {skill_id!r}")
        files: list[dict[str, Any]] = []
        for f in sorted(skill_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(skill_dir).as_posix()
            try:
                files.append(read_skill_file_entry(f, rel))
            except OSError:
                logger.warning("skipping unreadable file in app skill %s: %s", skill_id, rel)
                continue
        return SkillDetail(id=skill_id, name=skill_dir.name, files=files, audit_status="pass")


def seed_app_skills(manifest, app_dir: str | Path, *, origin: str) -> None:
    """Seed an app's declared skills into the user skills tree via the chokepoint.

    Idempotent + non-clobbering (an existing skill dir of the same name is left
    untouched). ``origin`` selects the scanner trust tier. ``manifest`` is an
    :class:`~personalclaw.apps.manifest.AppManifest`."""
    skills = list(getattr(manifest, "skills", None) or [])
    if not skills:
        return
    # Honour a dedicated opt-out (mirrors PERSONALCLAW_SKIP_PROMPT_SEED) so a test
    # asserting on a clean skills tree isn't polluted by an app's skills.
    if os.environ.get("PERSONALCLAW_SKIP_SKILL_SEED"):
        return

    from personalclaw.skills.loader import skills_dir
    from personalclaw.skills.marketplace import SkillInstallRefused, install_scanned

    base = Path(app_dir)
    app_name = getattr(manifest, "name", "") or ""
    tier = _tier_string_for_origin(origin)
    target = skills_dir()
    mp = _AppSkillsMarketplace(base, tier)
    for sk in skills:
        rel = str(getattr(sk, "path", "") or "").strip().strip("/")
        skill_dir = _resolve_skill_dir(base, rel)
        if skill_dir is None:
            continue
        dest = target / skill_dir.name
        if dest.exists():
            # Non-clobbering: a user-edited skill (or a prior seed) of this name is
            # left untouched. An /update removes the old app skill first, so the
            # new version re-seeds cleanly through the scan.
            continue
        try:
            install_scanned(mp, _app_source(app_name), rel, target, force=False)
            logger.info("Seeded app %r skill %r", app_name, skill_dir.name)
        except SkillInstallRefused as exc:
            # The gate refused (DANGEROUS always, WARNING without force). An app
            # skill is never force-installed here — a flagged skill simply doesn't
            # seed; the SEL audit already recorded the refusal.
            logger.warning("app %r skill %r refused by scan: %s", app_name, skill_dir.name, exc)
        except (ValueError, OSError):
            logger.debug("failed to seed app %r skill %r", app_name, rel, exc_info=True)


def remove_app_skills(manifest, app_dir: str | Path) -> list[str]:
    """Remove an app's shipped skill dirs — provenance-keyed, never a user's skill.

    A declared skill's installed dir is removed only when its ``.pclaw-lock.json``
    records ``source == app:<name>`` (proof this app installed it) and its files are
    still exactly what that record says was installed. A dir with no lock, a lock
    naming a different source, a lock that cannot be read, or a link is left alone.

    One whose files differ from the record is its owner's now: kept, and its record
    re-owned (:data:`KEPT_SOURCE`). Returns the names kept that way, so the disable,
    update or uninstall that called this can say so."""
    skills = list(getattr(manifest, "skills", None) or [])
    if not skills:
        return []

    from personalclaw.skills.loader import skills_dir

    app_name = getattr(manifest, "name", "") or ""
    src = _app_source(app_name)
    target = skills_dir()
    kept: list[str] = []
    for sk in skills:
        rel = str(getattr(sk, "path", "") or "").strip().strip("/")
        if not rel:
            continue
        leaf = Path(rel).name  # installed dir name == the declared dir's leaf
        dest = target / leaf
        if dest.is_symlink() or not dest.is_dir():
            continue
        try:
            record = install_record(dest)
        except DamagedInstallRecord:
            continue  # nothing can say what was installed there: left as it is
        if record is None or record.source != src:
            continue  # not ours — leave it
        if files_digest(file_digests(dest)) != record.digest:
            _reown(dest)
            kept.append(leaf)
            logger.info("Kept app %r skill %r: its owner changed it", app_name, leaf)
            continue
        shutil.rmtree(dest, ignore_errors=True)
        logger.info("Removed app %r skill %r", app_name, leaf)
    return kept


def _reown(dest: Path) -> None:
    """Rewrite *dest*'s install record so it no longer names the app that seeded it. The digests
    stay what the app installed, so the Skills page still reads the copy as changed from it. A
    record that cannot be rewritten still names the app, and the next removal keeps the copy
    again by its files."""
    from personalclaw.atomic_write import atomic_json_write

    lock = dest / LOCK_FILENAME
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        data["source"] = KEPT_SOURCE
        atomic_json_write(lock, data)
    except (OSError, ValueError, TypeError):
        logger.warning("could not re-own the record of %s", dest, exc_info=True)
