"""The skills PersonalClaw ships, kept in step with the home's library without overwriting hers.

PersonalClaw ships skills in its package (``skills/bundled``) and, from a checkout, in the
project's ``skills`` folder (``$PERSONALCLAW_PROJECT_DIR/skills``), which wins for a name both
hold. Agents read neither: each start copies them into the home's library (``<home>/skills``),
and agents read that copy, which is the owner's to change. :func:`sync` is that copy. It decides
by what the files hold, never by when they were written:

* A skill is installed with an install record (``marketplace.write_install_record``) holding a
  digest of each file installed, so what was installed is known exactly.
* A copy whose files are still what its record holds is PersonalClaw's own, and a release that
  ships other files replaces it whole (``marketplace.install_skill_files``).
* A copy that differs from its record is the owner's: kept, file for file. The version this
  release ships is offered to her on the Skills page (:class:`Offers`); taking it
  (:func:`use_shipped`) or keeping hers (:func:`keep_own`) is hers to do, and nothing does either
  for her.
* A copy with no record comes from a version that kept none. It is PersonalClaw's own when it is,
  file for file, a version that shipped (:data:`EARLIER_VERSIONS`, or this one), and it is the
  owner's otherwise.
* A skill this release no longer ships is removed while it is still the copy PersonalClaw
  installed (by its record, or :data:`EARLIER_VERSIONS`) and carries no refinement she accepted;
  once she changed it, it is kept as hers. Nothing is removed for its name alone.

A copy whose record is damaged, a folder that is a link, and a copy another source installed are
left as they are: there is no telling what of them is hers.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.skills.loader import hold_library, iter_skill_files
from personalclaw.skills.marketplace import (
    DamagedInstallRecord,
    InstallRecord,
    file_digests,
    files_digest,
    install_record,
    install_skill_files,
    payload_digests,
    read_skill_file_entry,
    write_install_record,
)

logger = logging.getLogger(__name__)

#: The install record's ``source`` for a skill that comes with PersonalClaw: what :func:`sync`
#: writes, and what an install from the ``native`` catalogue (the same skills) writes.
SOURCE = "native"

#: The owner's choices about offered versions, in ``entity_settings/skill_updates.json``:
#: ``{"declined": {name: digest}}``, the version of each skill she chose to keep hers over.
_CHOICES = "skill_updates"

#: The scan tier of what PersonalClaw ships (``supply_chain.TrustTier.BUILTIN``).
_TIER = "builtin"


class NotShipped(LookupError):
    """This version of PersonalClaw ships no skill of that name."""


class VersionChanged(ValueError):
    """The version offered is not the one this version of PersonalClaw ships now."""


class NotInstalled(RuntimeError):
    """The version that ships could not be installed; the copy that was there is unchanged."""


@dataclass(frozen=True)
class ShippedSkill:
    """One skill this version ships: where it comes from, and the digest of each file."""

    name: str
    folder: Path
    sha256: dict[str, str]

    @property
    def digest(self) -> str:
        return files_digest(self.sha256)


@dataclass
class SyncReport:
    """What one :func:`sync` did, by skill name."""

    installed: list[str] = field(default_factory=list)  # new to the library
    replaced: list[str] = field(default_factory=list)  # PersonalClaw's own copy, now this version
    recorded: list[str] = field(default_factory=list)  # an unrecorded copy that IS this version
    kept: list[str] = field(default_factory=list)  # the owner's copy, kept
    removed: list[str] = field(default_factory=list)  # PersonalClaw's own copy of a retired skill
    refused: list[str] = field(default_factory=list)  # refused by the safety scan, not installed


def project_root() -> Path | None:
    """The project's ``skills`` folder (``$PERSONALCLAW_PROJECT_DIR/skills``), when there is one."""
    value = os.environ.get("PERSONALCLAW_PROJECT_DIR")
    if value:
        folder = Path(value) / "skills"
        if folder.is_dir():
            return folder
    return None


def _roots() -> list[Path]:
    from personalclaw.skills.native import _bundled_root

    return [root for root in (project_root(), _bundled_root()) if root is not None]


#: Each shipped folder's file digests, with the signature they were taken at: each file's path,
#: inode, size and change time. A file whose signature moved is hashed again; what is decided is
#: decided on the digests.
_DIGESTS: dict[Path, tuple[tuple[tuple[str, int, int, int], ...], dict[str, str]]] = {}


def _shipped_files(folder: Path) -> dict[str, str]:
    """The files of a shipped folder and their digests, as an install writes them: tooling no skill
    runs (``supply_chain.never_installed``) and a stray install record are not among them."""
    from personalclaw.supply_chain import never_installed

    found: list[tuple[str, os.stat_result]] = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if rel.as_posix() == ".pclaw-lock.json" or any(never_installed(p) for p in rel.parts):
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            found.append((rel.as_posix(), info))
    signature = tuple((rel, i.st_ino, i.st_size, i.st_ctime_ns) for rel, i in found)
    cached = _DIGESTS.get(folder)
    if cached is not None and cached[0] == signature:
        return cached[1]
    wanted = {rel for rel, _ in found}
    sha256 = {rel: digest for rel, digest in file_digests(folder).items() if rel in wanted}
    _DIGESTS[folder] = (signature, sha256)
    return sha256


def shipped() -> dict[str, ShippedSkill]:
    """The skills this version of PersonalClaw ships, by name: the project's, then the package's."""
    out: dict[str, ShippedSkill] = {}
    for root in _roots():
        if not root.is_dir():
            continue
        for name, skill_md in iter_skill_files(root):
            if name not in out:
                folder = skill_md.parent
                out[name] = ShippedSkill(name, folder, _shipped_files(folder))
    return out


def _record(folder: Path) -> InstallRecord | None | DamagedInstallRecord:
    try:
        return install_record(folder)
    except DamagedInstallRecord as damaged:
        return damaged


def _is_folder(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _current(folder: Path) -> str:
    return files_digest(file_digests(folder))


def _scan(skill: ShippedSkill) -> Any:
    from personalclaw.supply_chain import TrustTier, scan_dir

    return scan_dir(skill.folder, TrustTier.BUILTIN)


def _install(base: Path, skill: ShippedSkill, report: SyncReport, outcome: list[str]) -> None:
    """Write *skill* into the library, exactly, with its record: scanned first at the tier of what
    PersonalClaw ships, and not installed when the scan finds it dangerous."""
    from personalclaw.supply_chain import Verdict

    scan = _scan(skill)
    if scan.verdict is Verdict.DANGEROUS:
        logger.error("Shipped skill %s refused by the safety scan; not installed", skill.name)
        report.refused.append(skill.name)
        return
    files = [read_skill_file_entry(skill.folder / rel, rel) for rel in sorted(skill.sha256)]
    parent, _, leaf = skill.name.rpartition("/")
    target_base = base / parent if parent else base
    try:
        install_skill_files(files, leaf, target_base)
    except (ValueError, OSError):
        logger.warning("Shipped skill %s could not be installed", skill.name, exc_info=True)
        return
    write_install_record(
        target_base / leaf,
        skill_id=skill.name,
        source=SOURCE,
        trust_tier=_TIER,
        verdict=scan.verdict.value,
        sha256=payload_digests(files),
    )
    outcome.append(skill.name)


def _needs_a_look(base: Path, skill: ShippedSkill) -> bool:
    """Whether :func:`sync` has anything to decide for *skill*, read without the library's lock: a
    copy that is missing, or that records another version of PersonalClaw's, or that has no
    record at all."""
    copy = base / skill.name
    if not copy.exists() and not copy.is_symlink():
        return True
    if not _is_folder(copy):
        return False
    record = _record(copy)
    if isinstance(record, DamagedInstallRecord):
        return False
    if record is None:
        return True
    return record.source == SOURCE and record.digest != skill.digest


def _bring_in_step(base: Path, skill: ShippedSkill, report: SyncReport) -> None:
    """The decision for one shipped skill, read again under the library's lock (the module's
    rules)."""
    copy = base / skill.name
    if not copy.exists() and not copy.is_symlink():
        _install(base, skill, report, report.installed)
        return
    if not _is_folder(copy):
        return
    record = _record(copy)
    if isinstance(record, DamagedInstallRecord):
        return
    current = _current(copy)
    if record is not None:
        if record.source != SOURCE or record.digest == skill.digest:
            return
        if current == record.digest:
            _install(base, skill, report, report.replaced)
        else:
            report.kept.append(skill.name)
        return
    if current == skill.digest:
        write_install_record(
            copy,
            skill_id=skill.name,
            source=SOURCE,
            trust_tier=_TIER,
            verdict=_scan(skill).verdict.value,
            sha256=file_digests(copy),
        )
        report.recorded.append(skill.name)
    elif current in EARLIER_VERSIONS.get(skill.name, ()):
        _install(base, skill, report, report.replaced)
    else:
        report.kept.append(skill.name)


def _retired(base: Path, ships: dict[str, ShippedSkill]) -> list[str]:
    """The library's skills that look like ones PersonalClaw shipped and no longer ships: a record
    of PersonalClaw's, or a name a version that kept no record shipped. Read without the lock."""
    if not base.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(base.iterdir()):
        name = entry.name
        if name.startswith(".") or name in ships or not _is_folder(entry):
            continue
        if not (entry / "SKILL.md").is_file():
            continue
        record = _record(entry)
        if isinstance(record, DamagedInstallRecord):
            continue
        if (record is not None and record.source == SOURCE) or (
            record is None and name in EARLIER_VERSIONS
        ):
            out.append(name)
    return out


def _retire(base: Path, name: str, report: SyncReport) -> None:
    """Remove the retired skill *name* while it is PersonalClaw's own copy and carries no
    refinement she accepted; keep it, as hers, otherwise."""
    from personalclaw.skills import overlays

    copy = base / name
    if not _is_folder(copy):
        return
    record = _record(copy)
    if isinstance(record, DamagedInstallRecord):
        return
    current = _current(copy)
    if record is not None:
        own = record.source == SOURCE and current == record.digest
    else:
        own = current in EARLIER_VERSIONS.get(name, ())
    if not own or overlays.refinement_count(name):
        report.kept.append(name)
        return
    shutil.rmtree(copy)
    report.removed.append(name)


def sync(base: Path) -> SyncReport:
    """Bring the library at *base* in step with the skills this version ships (the module's rules).

    Cheap when nothing changed: a copy whose record already holds this version is not read again.
    The decisions are made under the library's lock (``loader.hold_library``), so a save in the
    skill editor cannot land between a copy's check and its replacement.
    """
    report = SyncReport()
    ships = shipped()
    pending = [skill for skill in ships.values() if _needs_a_look(base, skill)]
    retired = _retired(base, ships)
    if not pending and not retired:
        return report
    with hold_library():
        for skill in pending:
            _bring_in_step(base, skill, report)
        for name in retired:
            _retire(base, name, report)
    for name in report.installed:
        logger.info("Installed shipped skill: %s", name)
    for name in report.replaced:
        logger.info("Updated shipped skill: %s", name)
    for name in report.removed:
        logger.info("Removed a shipped skill PersonalClaw no longer ships: %s", name)
    _audit(report)
    return report


def _audit(report: SyncReport) -> None:
    """One security-log event per kind of change made to the skills agents read."""
    rows = (
        ("installed", report.installed + report.replaced),
        ("removed", report.removed),
        ("refused", report.refused),
    )
    try:
        from personalclaw.sel import sel

        for outcome, names in rows:
            if names:
                sel().log_api_access(
                    caller="skills.shipped_sync",
                    operation="skill_install",
                    outcome=outcome,
                    source="skills",
                    resources=", ".join(f"{SOURCE}/{n}" for n in names),
                )
    except Exception:
        logger.debug("shipped skill sync SEL audit failed", exc_info=True)


# ── the offer, and her choice ───────────────────────────────────────────────────────────────


def _declined() -> dict[str, str]:
    """The version of each skill the owner chose to keep hers over. Fails open: an unreadable
    store declines nothing, so an offer she dismissed shows again rather than one hiding."""
    from personalclaw.providers.entity_routes import _load_entity_settings

    stored = (_load_entity_settings(_CHOICES) or {}).get("declined")
    if not isinstance(stored, dict):
        return {}
    return {str(k): v for k, v in stored.items() if isinstance(v, str)}


def _store_declined(declined: dict[str, str]) -> None:
    from personalclaw.providers.entity_routes import (
        _load_entity_settings,
        _save_entity_settings,
    )

    stored = _load_entity_settings(_CHOICES) or {}
    stored["declined"] = declined
    _save_entity_settings(_CHOICES, stored)


class Offers:
    """The versions offered over the owner's copies in the library at *base*: what ships and what
    she declined, read once for every copy asked about."""

    def __init__(self, base: Path) -> None:
        self._base = base
        self._ships = shipped()
        self._declined = _declined()

    def ships(self, name: str) -> bool:
        """Whether this version of PersonalClaw ships a skill named *name*."""
        return name in self._ships

    def __call__(self, name: str, *, current: str = "") -> str | None:
        """The digest of the version that ships for *name*, when it is offered over the copy in the
        library: the owner's copy, kept rather than replaced, that differs from it, and whose offer
        of that version she did not decline. ``None`` otherwise. *current* is the digest of the
        copy's files, when the caller read them already."""
        skill = self._ships.get(name)
        copy = self._base / name
        if skill is None or not _is_folder(copy):
            return None
        record = _record(copy)
        if isinstance(record, DamagedInstallRecord):
            return None
        if record is not None and (record.source != SOURCE or record.digest == skill.digest):
            return None
        current = current or _current(copy)
        if current == skill.digest:
            return None
        if record is not None and current == record.digest:
            return None  # PersonalClaw's own copy: the next start replaces it
        if record is None and current in EARLIER_VERSIONS.get(name, ()):
            return None  # likewise
        if self._declined.get(name) == skill.digest:
            return None
        return skill.digest


def _offered(name: str, digest: str) -> ShippedSkill:
    skill = shipped().get(name)
    if skill is None:
        raise NotShipped(name)
    if digest != skill.digest:
        raise VersionChanged(name)
    return skill


def use_shipped(base: Path, name: str, digest: str) -> None:
    """Replace the owner's copy of *name* with the version that ships, the one whose digest she
    was offered: what she asked for, on her word. Raises :class:`NotShipped` or
    :class:`VersionChanged`, changing nothing, and :class:`NotInstalled` when the version could
    not be written, leaving her copy as it was."""
    skill = _offered(name, digest)
    report = SyncReport()
    with hold_library():
        _install(base, skill, report, report.replaced)
    _audit(report)
    if not report.replaced:
        raise NotInstalled(name)
    declined = _declined()
    if declined.pop(name, None) is not None:
        _store_declined(declined)


def keep_own(name: str, digest: str) -> None:
    """Keep the owner's copy of *name* over the version whose digest she was offered: that version
    is not offered again, and a later one is. Raises :class:`NotShipped` or
    :class:`VersionChanged`, recording nothing then."""
    _offered(name, digest)
    declined = _declined()
    declined[name] = digest
    _store_declined(declined)


#: Every version of each skill PersonalClaw shipped before its copies carried an install record:
#: the digest (``marketplace.files_digest``) of each version of each folder of
#: ``src/personalclaw/skills/bundled`` from the first public release on. A copy with no record that
#: is one of these is PersonalClaw's own, unedited; any other is the owner's. Complete as it
#: stands: every copy installed since carries its record.
EARLIER_VERSIONS: dict[str, frozenset[str]] = {
    "artifacts": frozenset(
        {
            "4e6e9f2ba0d7c75a1cd174965c2c820c3996273b2cd0da44f5bb8f782324b83f",
            "6682b92cec321f76b69757e829562426992326004a1306e505b32f0114421b28",
            "c9846fcae733d0c94b596330a6e14b5c4216dbc69dc7f131c55d2b5c3a2de4c6",
        }
    ),
    "best-of-n": frozenset(
        {
            "fd824911dc11caff012c1743ab0f14e5a3f4aa623e17df1d09b0eca64cedf95b",
        }
    ),
    "check-work": frozenset(
        {
            "b8b504c9224a99994288ab64d90d57a0c98ca57a8f8655fe2210c26e0adf189f",
        }
    ),
    "delegation": frozenset(
        {
            "6fac2d9235647d18f0c4848f5042c218163c65dc152c806221282ba7c069c39a",
        }
    ),
    "document-authoring": frozenset(
        {
            "57a2daf79fecbce9eced5b107f1e0617c3e66d7c7197f2105ab956d8d5a13963",
        }
    ),
    "editorial-document": frozenset(
        {
            "bad0f99fa05311d1a15e559eaac1ad9d9c0bc4cdec179ae91c874374931d8758",
            "c10fe33b755c827cdeb71ab16426719f7064853652d1291a4bc7cd7f7575ce41",
        }
    ),
    "grill": frozenset(
        {
            "d49030300b75a1b0deb02b0bcba0629c1cd8015b3d07a6863e5fefcea33047a5",
            "d4ba110beb24ed06f43c13cf1aea0fd1063497ca04111d636374d932c9eba96e",
        }
    ),
    "infographic-syntax": frozenset(
        {
            "a29fc61b7342287647c53f8869137ae3c77504df19040d787278503c87f84bc4",
        }
    ),
    "knowledge-grounding": frozenset(
        {
            "baf05c474a3c801c61e19c623d70df9b9522189983653633338d266cb97c9892",
        }
    ),
    "loop-worker": frozenset(
        {
            "67c208020a0132d1e1a84288b453595b7968c7be03366b9947bb5af61517ffa3",
        }
    ),
    "memory-discipline": frozenset(
        {
            "462f9a03df1ad21d9f768a175b40fbdc99a04f62f8c99c4c62862d4dc2888c3c",
            "a74724fb67ebf4d52cf2c4265f4ccf2b6c1b58b2079ac3a540d2ddbf01209954",
        }
    ),
    "pclaw-api": frozenset(
        {
            "2a3553e663be423ad609aab78b494db9fcce46aa6e12e5f78efdf8c19c5c2174",
            "628f05d47bb7f2a596aa153d732a665b0440da7480b7c7e285333e0414fb18e4",
            "85e3c6d6cedabfc34cb278d8c5591c05ea872f0582953f763d85b9b5e25f7f4d",
            "b839dd176d99677a864f597d9c84fd3e22970ebfd944f5db8332dba70c5e05a1",
        }
    ),
    "pclaw-features": frozenset(
        {
            "0350b804173138eb94a95000fe6477373a55063d33d16eddf498c119a807cf57",
            "1c9e51e2c432b966deb6320ddf34dc83444d17e8d6c1967619f94f07156df0c7",
        }
    ),
    "research-campaign": frozenset(
        {
            "f158b565f69e7db221c00d7d39625edae1f356b88be008daad6a67177dcd039f",
        }
    ),
    "task-and-project": frozenset(
        {
            "166bc1cb18487869b64d7fa2431a3cb3d4a796411c0fe132b1e1364174e3c71b",
        }
    ),
    "visual-output": frozenset(
        {
            "02c3a9627a789dba7372ce75d47c24eb807a9012a8a92f8c8e80dbde6a71937a",
            "8dd4babf6f08a137a23dd147a5714c6be3b88756743fe718f088b3cb32470507",
            "97a22bdc103df8e2fd789cfd72c03c7b60a37f12957c4b0e1ecdc1eff54715b6",
            "c038ad7cb464e9bc7bbbb7e119652fb1caeb40caebd356570cc90dac70b12255",
            "f5c550334118101572e33be460b27103c3254c029bf03a42e3740913a9bb8a7f",
        }
    ),
    "web-verify": frozenset(
        {
            "78215bb9570dd2b4839bb8cfae60756dc165f3c52003e155cca17836b0f99eb2",
        }
    ),
}
