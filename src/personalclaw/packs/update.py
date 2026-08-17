"""Pack UPDATE — overwrite only what the pack owns, never a copy the user edited (§1, AP-7).

Installing a pack is a one-way write; *updating* one is the dangerous direction, because the
files a new version wants to replace may be files the user has since been editing. The §1
``pack_owned`` rule (the ``distribution_owned`` pattern) makes that decidable rather than a
judgement call, and this module is that rule's whole implementation:

* **Only ``pack_owned`` components are candidates.** A component whose pack-relative path no
  ``pack_owned`` pattern matches is never touched by an update — the pack shipped it once and
  disclaimed ongoing ownership. The patterns used are the ones the **installed** pack
  declared, not the ones the incoming archive declares, so a new version cannot widen its own
  ownership and thereby acquire the right to clobber a file the user has been living in.
* **A drifted copy is skipped with a visible note.** Install stamps a
  ``{source, computedHash}`` lock per component (§1, the LEARNING-FLYWHEEL convention). An
  update re-derives the digest: equal means the on-disk copy is still the pack's own bytes and
  is safe to replace; different means the user edited it, and it is skipped with a
  :attr:`ComponentUpdate.reason` a UI shows. Absent means unverifiable — also skipped, because
  "I cannot tell whether you edited this" must not resolve to "so I'll overwrite it".

The digest is ONE algorithm for files and directories alike (:func:`component_digest`), so a
skill and a template cannot drift into two different notions of "changed". It answers a
different question from :func:`skills.marketplace.verify_skill_integrity` — that one reports
per-file tamper for a security audit; this one is a single value per component ref, which is
what a ledger row can hold — so neither is a fork of the other.

An update runs the SAME refusal gates as an install (integrity recompute, referential lint,
supply-chain scan by origin) through :func:`packs.import_.inspect_pack`: a malicious "update"
is just a malicious pack, and it gets no softer treatment for arriving second.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.packs.import_ import ImportPlan

logger = logging.getLogger(__name__)

#: Excluded from a skill's digest: install provenance, not content. Including it would make
#: every skill read as drifted the moment ``install_guarded`` re-stamped a timestamp into it.
_DIGEST_EXCLUDE = frozenset({".pclaw-lock.json"})

#: The four update actions. ``overwrite`` is the only one that writes.
ACTION_OVERWRITE = "overwrite"
ACTION_SKIP_NOT_OWNED = "skip_not_pack_owned"
ACTION_SKIP_DRIFT = "skip_drift"
ACTION_SKIP_UNVERIFIABLE = "skip_unverifiable"


def component_digest(path: Path) -> str:
    """A stable sha256 over a component's on-disk content — file or directory, one algorithm.

    A file digests its bytes. A directory digests the sorted ``<relpath>\\0<sha256>`` lines of
    its files, so a rename, an addition and an edit all move the value. Returns ``""`` when the
    path does not exist, which a caller reads as "gone" rather than as a hash collision.
    """
    path = Path(path)
    if path.is_file():
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""
    if not path.is_dir():
        return ""
    h = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if not child.is_file() or child.name in _DIGEST_EXCLUDE:
            continue
        try:
            blob = child.read_bytes()
        except OSError:
            continue
        h.update(child.relative_to(path).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(blob).hexdigest().encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def is_pack_owned(pack_path: str, patterns: list[str]) -> bool:
    """Does ``pack_path`` (a component's PACK-relative path) fall under any ``pack_owned``
    pattern? Matched against the full pack-relative path and against the containing directory,
    so ``skills/cfo-*`` covers ``skills/cfo-report/SKILL.md`` the way its author meant it."""
    parent = str(Path(pack_path).parent.as_posix())
    for pattern in patterns:
        if fnmatch.fnmatch(pack_path, pattern) or fnmatch.fnmatch(parent, pattern):
            return True
        # A directory-shaped pattern ("skills/cfo-*") should also cover everything beneath a
        # matching directory, which fnmatch alone does not express.
        if fnmatch.fnmatch(pack_path, pattern.rstrip("/") + "/*"):
            return True
    return False


def stamp_locks(
    plan: "ImportPlan", home: Path, committed: dict[str, Path]
) -> dict[str, dict[str, str]]:
    """Build the per-component ``{source, computedHash, path}`` locks for a fresh install.

    Called from the importer's post-commit ledger write, so the digest is taken from the bytes
    that actually landed on disk rather than from the archive — if the two ever disagreed, the
    on-disk value is the one a later update has to compare against.
    """
    source = f"pack:{plan.name}@{plan.version}"
    locks: dict[str, dict[str, str]] = {}
    for ref, path in committed.items():
        try:
            rel = Path(path).relative_to(home).as_posix()
        except ValueError:  # pragma: no cover - every commit target is under home
            rel = str(path)
        locks[ref] = {
            "source": source,
            "computedHash": component_digest(Path(path)),
            "path": rel,
        }
    return locks


@dataclass
class ComponentUpdate:
    """One component's update decision, with the reason a UI shows verbatim."""

    ref: str
    action: str
    reason: str
    pack_path: str = ""
    home_path: str = ""

    @property
    def writes(self) -> bool:
        return self.action == ACTION_OVERWRITE

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "action": self.action,
            "reason": self.reason,
            "pack_path": self.pack_path,
            "home_path": self.home_path,
        }


@dataclass
class UpdatePlan:
    """What an update WOULD do (``applied=False``) or DID do (``applied=True``).

    ``drift_notes`` is the visible half of the §1 contract: one human-readable line per
    user-edited component that was deliberately left alone. An update that silently skipped
    them would be indistinguishable from one that quietly clobbered them.
    """

    pack: str
    from_version: str
    to_version: str
    components: list[ComponentUpdate] = field(default_factory=list)
    applied: bool = False

    @property
    def drift_notes(self) -> list[str]:
        return [
            f"{c.ref}: {c.reason}"
            for c in self.components
            if c.action in (ACTION_SKIP_DRIFT, ACTION_SKIP_UNVERIFIABLE)
        ]

    @property
    def overwritten(self) -> list[str]:
        return [c.ref for c in self.components if c.writes]

    @property
    def skipped(self) -> list[str]:
        return [c.ref for c in self.components if not c.writes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "applied": self.applied,
            "components": [c.to_dict() for c in self.components],
            "drift_notes": list(self.drift_notes),
            "overwritten": list(self.overwritten),
            "skipped": list(self.skipped),
        }


class PackUpdateError(Exception):
    """An update that cannot proceed — the pack is not installed, or the archive is refused."""


def _decide(plan: "ImportPlan", installed: Any, home: Path) -> list[ComponentUpdate]:
    """The whole §1 decision table, one row per component in the incoming pack."""
    owned = list(installed.pack_owned)
    locks = installed.component_locks
    out: list[ComponentUpdate] = []
    for comp in plan.components:
        ref = comp.ref
        if not is_pack_owned(comp.path, owned):
            out.append(
                ComponentUpdate(
                    ref=ref,
                    action=ACTION_SKIP_NOT_OWNED,
                    reason=(
                        f"{comp.path!r} matches no pack_owned pattern "
                        f"({', '.join(owned) or 'none declared'}) — the pack does not own it"
                    ),
                    pack_path=comp.path,
                )
            )
            continue
        lock = locks.get(ref)
        if not lock or not lock.get("computedHash"):
            out.append(
                ComponentUpdate(
                    ref=ref,
                    action=ACTION_SKIP_UNVERIFIABLE,
                    reason=(
                        "no install lock recorded, so a local edit cannot be ruled out — "
                        "left untouched; re-import the pack to re-establish the lock"
                    ),
                    pack_path=comp.path,
                )
            )
            continue
        target = home / lock.get("path", "")
        current = component_digest(target)
        if current == "":
            # The pack's copy is gone. Restoring it is not clobbering anyone's edit.
            out.append(
                ComponentUpdate(
                    ref=ref,
                    action=ACTION_OVERWRITE,
                    reason="the installed copy is missing — reinstating it",
                    pack_path=comp.path,
                    home_path=lock.get("path", ""),
                )
            )
        elif current != lock["computedHash"]:
            out.append(
                ComponentUpdate(
                    ref=ref,
                    action=ACTION_SKIP_DRIFT,
                    reason=(
                        "edited since install (content hash differs from the install lock) — "
                        "your version was kept, the pack's update was not applied"
                    ),
                    pack_path=comp.path,
                    home_path=lock.get("path", ""),
                )
            )
        else:
            out.append(
                ComponentUpdate(
                    ref=ref,
                    action=ACTION_OVERWRITE,
                    reason="pack-owned and unmodified since install",
                    pack_path=comp.path,
                    home_path=lock.get("path", ""),
                )
            )
    return out


def plan_update(pack_name: str, archive: Path | str, *, tier: Any = None) -> UpdatePlan:
    """Dry-run an update: what a commit WOULD overwrite and what it would skip. No writes.

    Runs the archive through :func:`packs.import_.inspect_pack` (so integrity, lint and scan
    verdicts are computed exactly as on a fresh install) and then applies the §1 decision
    table. Raises :class:`PackUpdateError` when the pack is not installed, or when the incoming
    archive is refused outright — an update never proceeds on a plan a fresh install would
    reject.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.packs.import_ import PackImportRefused, inspect_pack
    from personalclaw.packs.installed import load_installed

    home = config_dir()
    installed = next((p for p in load_installed(home) if p.name == pack_name), None)
    if installed is None:
        raise PackUpdateError(f"pack not installed: {pack_name}")
    try:
        plan = inspect_pack(archive, tier=tier)
    except PackImportRefused as exc:
        raise PackUpdateError(f"update archive refused: {exc}") from exc
    if plan.name != pack_name:
        raise PackUpdateError(
            f"archive is pack {plan.name!r}, not {pack_name!r} — an update must be the same pack"
        )
    if plan.blocked:
        raise PackUpdateError(
            "update archive is blocked "
            f"(integrity_ok={plan.integrity_ok}, lint_ok={plan.lint.ok}, "
            f"dangerous={plan.has_dangerous})"
        )
    return UpdatePlan(
        pack=pack_name,
        from_version=installed.version,
        to_version=plan.version,
        components=_decide(plan, installed, home),
    )


def apply_update(
    pack_name: str, archive: Path | str, *, consent: bool = False, tier: Any = None
) -> UpdatePlan:
    """Apply an update: overwrite only the pack-owned, undrifted components. Journaled.

    The same refusal gates as a fresh install run first (a blocked archive never writes, a
    WARNING archive needs ``consent``). Then only the components the §1 decision table marked
    ``overwrite`` are committed — in place, keeping their original ids, because the colliding
    entity IS this pack's own previous copy. Any mid-commit fault unwinds every journaled write.

    On success the ledger is refreshed: the new version, the new manifest's ``pack_owned``
    patterns (they are now what is installed), fresh locks for the components that were
    replaced, and the ORIGINAL locks preserved for the ones that were skipped — so a skipped
    drifted component still reads as drifted on the next update rather than being retroactively
    blessed as clean.
    """
    import shutil
    import tempfile
    import uuid
    import zipfile

    from personalclaw.config.loader import config_dir
    from personalclaw.packs import import_ as pack_import
    from personalclaw.packs.installed import load_installed, record_install
    from personalclaw.skills.marketplace import get_default_skills_registry

    home = config_dir()
    installed = next((p for p in load_installed(home) if p.name == pack_name), None)
    if installed is None:
        raise PackUpdateError(f"pack not installed: {pack_name}")

    from_version = installed.version
    tier = plan_tier(tier)
    quarantine = Path(tempfile.mkdtemp(prefix="pclaw-pack-update-"))
    try:
        try:
            zf = zipfile.ZipFile(str(archive))
        except (zipfile.BadZipFile, OSError) as exc:
            raise PackUpdateError(f"not a readable .pclaw archive: {exc}") from exc
        with zf:
            members = pack_import._extract_quarantine(zf, quarantine)
            manifest = pack_import._read_manifest(members)
            plan, parsed = pack_import._build_plan(
                manifest, members, quarantine, home, tier, in_place=True
            )
        if plan.name != pack_name:
            raise PackUpdateError(
                f"archive is pack {plan.name!r}, not {pack_name!r} — "
                "an update must be the same pack"
            )
        if plan.blocked:
            raise PackUpdateError(
                "update archive is blocked "
                f"(integrity_ok={plan.integrity_ok}, lint_ok={plan.lint.ok}, "
                f"dangerous={plan.has_dangerous})"
            )
        if plan.needs_consent and not consent:
            raise PackUpdateError("update archive needs explicit consent — WARNING component(s)")

        decisions = _decide(plan, installed, home)
        write_refs = {d.ref for d in decisions if d.writes}

        update_id = uuid.uuid4().hex[:16]
        journal = pack_import._Journal(home, update_id)
        registry = get_default_skills_registry()
        mp_name = f"pack-update:{pack_name}:{update_id}"
        tier_str = getattr(tier, "value", str(tier))
        skill_files = {c.target_id: (c.skill_files or []) for c in parsed if c.kind == "skill"}
        registry.register(mp_name, pack_import.PackMarketplace(skill_files, tier_str))
        committed: dict[str, Path] = {}
        try:
            for comp in parsed:  # already leaves-first
                ref = f"{comp.kind}:{comp.id}"
                if ref not in write_refs:
                    continue
                if comp.kind == "skill":
                    skill_dir = pack_import._commit_skill(comp, home, mp_name, journal)
                    journal.record_skill(skill_dir)
                    committed[ref] = skill_dir
                else:
                    committed[ref] = pack_import._commit_file_component(
                        comp, home, journal, plan.name or pack_name
                    )
        except Exception as exc:
            journal.rollback()
            logger.error("pack update %s faulted and was rolled back: %s", pack_name, exc)
            raise PackUpdateError(f"update faulted and was rolled back: {exc}") from exc
        finally:
            registry.unregister(mp_name)
        journal.discard()

        # Refresh the ledger. Locks for replaced components are re-derived from the bytes that
        # just landed; every other lock is carried forward untouched.
        locks = dict(installed.component_locks)
        locks.update(stamp_locks(plan, home, committed))
        installed.version = plan.version
        installed.pack_owned = list(plan.pack_owned)
        installed.component_locks = locks
        record_install(installed, home)

        result = UpdatePlan(
            pack=pack_name,
            from_version=from_version,
            to_version=plan.version,
            components=decisions,
            applied=True,
        )
        pack_import._audit(
            "pack_update",
            "applied",
            resources=(
                f"{pack_name}@{plan.version} "
                f"({len(result.overwritten)} overwritten, {len(result.skipped)} skipped)"
            ),
        )
        return result
    finally:
        shutil.rmtree(quarantine, ignore_errors=True)


def plan_tier(tier: Any) -> Any:
    """The trust tier an update scans at — COMMUNITY by default, like any imported pack."""
    if tier is not None:
        return tier
    from personalclaw.supply_chain import TrustTier

    return TrustTier.COMMUNITY
