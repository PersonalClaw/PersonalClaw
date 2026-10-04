"""Skills marketplace — abstract base, SkillsRegistry, and local skill discovery.

The agentskills.io format (https://agentskills.io) is the standard:
  - A skill is a directory containing a SKILL.md file with YAML frontmatter.
  - Frontmatter fields: name, description, license, compatibility, metadata, allowed-tools.
  - The body is Markdown loaded on demand by the LLM.

Discovery paths (:func:`skill_discovery_paths`, after an agent's own folder in
``SkillsLoader``'s order, which every surface follows):
  - ``<home>/skills/``                    — installed and user-created, the only install target,
                                            and where the skills PersonalClaw ships are kept
                                            (``skills.shipped``)
  - ``~/.agents/skills/``                 — agentskills.io's shared folder, read only once the
                                            owner allows it (``personalclaw.outside_home``)

An installed skill's folder carries its install record (:data:`LOCK_FILENAME`): a digest of each
file installed, which :func:`verify_skill_integrity` compares the files with.

``SkillsRegistry`` holds named ``SkillsMarketplace`` implementations.
Additional marketplaces (an app's, a configured catalog) register via
``get_default_skills_registry().register(name, marketplace)``; one an app's code
registers leaves with the app.
"""

import builtins
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw import app_code
from personalclaw.record_ids import record_path
from personalclaw.skills.loader import validate_skill_md as _validate_skill_md

logger = logging.getLogger(__name__)

_SKILL_FILENAME = "SKILL.md"

#: A skill's install record, in its folder: what was installed there, a digest per file. Written
#: by :func:`write_install_record` and read by :func:`install_record`, and by nothing else.
LOCK_FILENAME = ".pclaw-lock.json"


def skill_discovery_paths() -> list[Path]:
    """The standard skill discovery paths, in priority order, resolved per call.

    The home's skills first, where every install lands. Then the folder AI tools share
    (``~/.agents/skills``), only when the owner allowed PersonalClaw to read it, and only read:
    a skill there is never installed into, changed or deleted by PersonalClaw.
    """
    from personalclaw import outside_home
    from personalclaw.skills.loader import skills_dir

    paths = [skills_dir()]
    shared = outside_home.place_path(outside_home.AGENT_SKILLS)
    if shared is not None:
        paths.append(shared)
    return paths


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class SkillEntry:
    """Metadata for a skill returned from a marketplace search."""

    id: str  # marketplace-scoped id, e.g. "vercel-labs/agent-skills/next-js"
    name: str  # from SKILL.md frontmatter
    description: str  # from SKILL.md frontmatter
    source: str  # marketplace name or "local"
    url: str = ""  # human-readable URL on the marketplace
    installs: int = 0  # install count if known
    # Already present in the user's skills dir. An ANNOTATION, never a reason to withhold
    # the row: the fan-out used to DROP every already-installed hit, which emptied the
    # store on a stock install (every bundled skill is auto-installed at startup, and the
    # `native` marketplace mirrors exactly that bundle — so its whole catalogue matched the
    # drop filter and "all marketplaces" answered 0 for every query, issue #301). A search
    # that omits what you have cannot tell you that you already have it.
    installed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "url": self.url,
            "installs": self.installs,
            "installed": self.installed,
        }


@dataclass
class SkillDetail:
    """Full skill contents returned from marketplace fetch.

    Each ``files`` entry is ``{path, contents}`` for a text file or ``{path, data}``
    (raw ``bytes``) for a binary — the whole tree is carried so nothing is dropped
    before the scan/commit/lock. Use :func:`read_skill_file_entry` to build entries."""

    id: str
    name: str
    files: list[dict[str, Any]] = field(default_factory=list)  # [{path, contents|data}]
    audit_status: str = "unknown"  # "pass", "warn", "fail", or "unknown"

    def skill_md(self) -> str | None:
        """Return the SKILL.md content, or None if not present."""
        for f in self.files:
            if f.get("path", "").endswith("SKILL.md"):
                return f.get("contents", "")
        return None


# ── Abstract base ─────────────────────────────────────────────────────────────


class SkillsMarketplace(ABC):
    """Abstract skills marketplace."""

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[SkillEntry]:
        """Search the marketplace for skills matching *query*."""

    @abstractmethod
    def fetch(self, skill_id: str) -> SkillDetail:
        """Fetch full skill detail (including SKILL.md contents) for *skill_id*.

        A marketplace is a read-only SOURCE: it only searches and fetches. Installing
        is not its job — :meth:`SkillsRegistry.install_guarded` stages the fetched
        payload to quarantine, scans it at this marketplace's trust tier, and commits
        the exact scanned bytes via the shared ``install_skill_files`` writer. That one
        chokepoint is the only path that writes to the live skills tree, so a fetch
        never has to be trusted to write."""

    @property
    def marketplace_type(self) -> str:
        return "unknown"

    @property
    def trust_tier(self) -> str:
        """Provenance tier that modulates the scan verdict (S2). Bundled/native content
        is trusted; an arbitrary community registry (skills.sh) gets the full gate.
        Returns a :class:`~personalclaw.supply_chain.TrustTier` value string."""
        return "community"


# ── Guarded-install result + refusal ────────────────────────────────────────


@dataclass
class InstallResult:
    """A successful guarded install: where it landed + the scan evidence surfaced."""

    path: Path
    report: "Any"  # supply_chain.ScanReport
    tier: "Any"  # supply_chain.TrustTier


class SkillNotFoundError(KeyError):
    """A marketplace was asked for a skill id it does not have.

    Typed so HTTP handlers can map "no such skill" to 404 instead of the
    500 a bare ``RuntimeError`` produced. Also raised for a syntactically
    invalid id (path separators, ``..``, absolute paths) — to a caller those
    are indistinguishable from "not found", and saying more would leak
    which paths exist outside the marketplace root.
    """

    def __init__(self, skill_id: str) -> None:
        super().__init__(skill_id)
        self.skill_id = skill_id

    def __str__(self) -> str:
        return f"Skill not found: {self.skill_id!r}"


class SkillInstallRefused(Exception):
    """A guarded install was blocked by the supply-chain gate.

    ``dangerous`` distinguishes the non-overridable floor (high-confidence malice — no
    ``force`` installs it) from an overridable ``warning`` (a calculated risk the caller
    may re-attempt with ``force=True``). ``report`` carries the findings for the UX.
    """

    def __init__(self, report: "Any", *, dangerous: bool) -> None:
        self.report = report
        self.dangerous = dangerous
        cats = ", ".join(sorted({f.rule for f in report.findings})) or "no specific rule"
        verb = (
            "refused (dangerous, non-overridable)" if dangerous else "needs confirmation (warning)"
        )
        super().__init__(f"skill install {verb}: {cats}")


def read_skill_file_entry(path: Path, rel: str) -> "dict[str, Any]":
    """Read one skill file into a payload entry, preserving binary content.

    Text (UTF-8-decodable) files carry ``contents: str``; anything else carries
    ``data: bytes``. Binaries must NOT be dropped — an icon/asset that goes missing
    means an incomplete install AND a spurious S6 "added" finding on the untracked
    file. Both variants flow through staging, the scan, the commit, and the lock."""
    raw = path.read_bytes()
    try:
        return {"path": rel, "contents": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": rel, "data": raw}


def _entry_bytes(entry: "dict[str, Any]") -> bytes:
    """The raw bytes a file entry writes to disk — text ``contents`` UTF-8-encoded, or
    binary ``data`` verbatim. One definition shared by stage, commit, and lock so all
    three hash/write identical bytes (a fresh install verifies intact under S6)."""
    if "data" in entry:
        data = entry["data"]
        return data if isinstance(data, bytes) else str(data).encode("utf-8")
    return str(entry.get("contents", "")).encode("utf-8")


def _stage_files(files: "list[dict[str, Any]]", staged_skill: Path) -> None:
    """Write the fetched payload into a quarantine dir, path-safe, for scanning BEFORE
    it can touch the live skills tree. Rejects traversal (mirrors install_skill_files)."""
    staged_skill.mkdir(parents=True, exist_ok=True)
    for entry in files:
        rel = entry.get("path", "")
        if ".." in rel or rel.startswith("/"):
            raise ValueError(f"Rejected unsafe file path: {rel!r}")
        out = staged_skill / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_entry_bytes(entry))


def file_digests(skill_dir: Path) -> dict[str, str]:
    """Each file in *skill_dir* and the sha256 of its bytes, by its path in the folder.

    The install record itself is left out: it describes the files, it is not one of them. A file
    that cannot be read is left out too, so a comparison with a record reads it as missing.
    """
    skill_dir = Path(skill_dir)
    out: dict[str, str] = {}
    for f in sorted(skill_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(skill_dir).as_posix()
        if rel == LOCK_FILENAME:
            continue
        try:
            out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def files_digest(sha256: Mapping[str, str]) -> str:
    """One digest for a set of files: sha256 over their sorted ``<path>\\0<sha256>`` lines.

    Two folders hold the same files exactly when their digests are equal, whatever their files'
    times say. The algorithm is a stable contract: :data:`shipped.EARLIER_VERSIONS` holds digests
    of versions that shipped before install records were kept.
    """
    lines = "".join(f"{rel}\0{sha256[rel]}\n" for rel in sorted(sha256))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InstallRecord:
    """What a skill folder's install record says was installed: its source, and each file."""

    source: str
    sha256: dict[str, str]

    @property
    def digest(self) -> str:
        return files_digest(self.sha256)


class DamagedInstallRecord(ValueError):
    """A skill's install record is there and cannot be read as one, so nothing can say what was
    installed in its folder."""


def install_record(skill_dir: Path) -> InstallRecord | None:
    """The install record in *skill_dir*, or ``None`` when it has none (nothing installed it).

    Raises :class:`DamagedInstallRecord` when ``.pclaw-lock.json`` is there but is not a record:
    unreadable, not JSON, not an object, or without a ``sha256`` map of file paths to digests.
    """
    path = Path(skill_dir) / LOCK_FILENAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DamagedInstallRecord(f"{path.name} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise DamagedInstallRecord(f"{path.name} is not an object")
    digests = data.get("sha256")
    if not isinstance(digests, dict) or not all(
        isinstance(rel, str) and isinstance(digest, str) for rel, digest in digests.items()
    ):
        raise DamagedInstallRecord(f"{path.name} holds no digest of the files installed")
    source = data.get("source")
    return InstallRecord(source=source if isinstance(source, str) else "", sha256=dict(digests))


def write_install_record(
    skill_dir: Path,
    *,
    skill_id: str,
    source: str,
    trust_tier: str,
    verdict: str,
    sha256: Mapping[str, str],
) -> None:
    """Record in *skill_dir* that *sha256* (each file and its digest) was installed there from
    *source*, scanned at *trust_tier* with *verdict*. Atomic; a failure to write it is logged and
    leaves the skill without a record, which reads as unverified."""
    import time

    from personalclaw.atomic_write import atomic_json_write

    record = {
        "id": skill_id,
        "source": source,
        "trust_tier": trust_tier,
        "verdict": verdict,
        "sha256": dict(sha256),
        "installed_at": time.time(),
    }
    try:
        atomic_json_write(Path(skill_dir) / LOCK_FILENAME, record)
    except OSError:
        logger.warning("could not write the install record of %s", skill_dir, exc_info=True)


def payload_digests(files: "list[dict[str, Any]]") -> dict[str, str]:
    """Each file of an install payload and the sha256 of the bytes it writes
    (:func:`_entry_bytes`), the install record left out."""
    return {
        str(entry.get("path", "")): hashlib.sha256(_entry_bytes(entry)).hexdigest()
        for entry in files
        if entry.get("path") and entry.get("path") != LOCK_FILENAME
    }


def _write_lock(
    target_dir: Path, detail: "SkillDetail", source: str, tier: "Any", report: "Any"
) -> None:
    """Record what an install wrote (:func:`write_install_record`): the payload's files, which
    are exactly the folder's files (:func:`install_skill_files`), from *source* at *tier*."""
    skill_dir = Path(target_dir) / (detail.name or detail.id)
    if not skill_dir.is_dir():
        return
    write_install_record(
        skill_dir,
        skill_id=detail.id,
        source=source,
        trust_tier=getattr(tier, "value", str(tier)),
        verdict=getattr(report.verdict, "value", str(report.verdict)),
        sha256=payload_digests(detail.files),
    )


#: The four answers to "are this skill's files what was installed?". ``intact``: exactly what its
#: record holds. ``edited``: changed since (a file changed, added or removed), whoever changed
#: it: the owner's save in the skill editor, a change she made to the files, PersonalClaw's own
#: writers. ``tampered``: its record is there and cannot be read (:class:`DamagedInstallRecord`),
#: so nothing can say what was installed; an edit never touches the record. ``unverified``: no
#: record, so nothing installed it, or it was installed before records were kept.
INTACT = "intact"
EDITED = "edited"
TAMPERED = "tampered"
UNVERIFIED = "unverified"


@dataclass
class IntegrityReport:
    """One skill's files compared with its install record. ``state`` is one of the four answers
    above; ``mutated`` / ``missing`` / ``added`` name what changed, for an ``edited`` skill."""

    skill: str
    state: str = UNVERIFIED
    mutated: list[str] = field(default_factory=list)  # a recorded file whose bytes changed
    missing: list[str] = field(default_factory=list)  # a recorded file now gone
    added: list[str] = field(default_factory=list)  # a file the record does not hold
    #: The digest of the folder's files as they are (:func:`files_digest`), when it was read.
    digest: str = ""

    @property
    def ok(self) -> bool:
        return self.state == INTACT

    @property
    def unlocked(self) -> bool:
        return self.state == UNVERIFIED

    def summary(self) -> str:
        if self.state == UNVERIFIED:
            return f"{self.skill}: no install record (unverifiable)"
        if self.state == TAMPERED:
            return f"{self.skill}: its install record is damaged, so what was installed is unknown"
        if self.state == INTACT:
            return f"{self.skill}: intact"
        parts = []
        if self.mutated:
            parts.append(f"{len(self.mutated)} changed")
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.added:
            parts.append(f"{len(self.added)} added")
        return f"{self.skill}: edited ({', '.join(parts)})"


def verify_skill_integrity(skill_dir: Path) -> IntegrityReport:
    """Compare a skill's files with its install record (:func:`install_record`).

    Its owner's edit reads ``edited``, with what changed, and never as tampering: PersonalClaw
    cannot tell who changed a file in the home, and a change to a skill there is the owner's to
    make. A damaged record is the one finding that is not an edit; it reads ``tampered`` and is
    written to the security log.
    """
    skill_dir = Path(skill_dir)
    name = skill_dir.name
    try:
        record = install_record(skill_dir)
    except DamagedInstallRecord as exc:
        rep = IntegrityReport(skill=name, state=TAMPERED)
        try:
            from personalclaw.sel import sel

            sel().log_api_access(
                caller="skills.verify_integrity",
                operation="skill_integrity",
                outcome="tampered",
                source="skills",
                resources=name,
                error=str(exc),
            )
        except Exception:
            logger.debug("integrity SEL audit failed", exc_info=True)
        return rep
    on_disk = file_digests(skill_dir)
    if record is None:
        return IntegrityReport(skill=name, state=UNVERIFIED, digest=files_digest(on_disk))
    rep = IntegrityReport(skill=name, digest=files_digest(on_disk))
    for rel, want in record.sha256.items():
        got = on_disk.get(rel)
        if got is None:
            rep.missing.append(rel)
        elif got != want:
            rep.mutated.append(rel)
    rep.added = [rel for rel in on_disk if rel not in record.sha256]
    rep.state = EDITED if (rep.mutated or rep.missing or rep.added) else INTACT
    return rep


def _audit_install(
    source: str, skill_id: str, tier: "Any", report: "Any", *, outcome: str, rules: str = ""
) -> None:
    """Emit a SEL audit event for a scan/install/refuse (best-effort). ``rules`` names the
    findings the event is about, when it is about some (the warnings a person accepted)."""
    try:
        from personalclaw.sel import sel

        verdict = getattr(report.verdict, "value", report.verdict)
        detail = f"tier={getattr(tier, 'value', tier)} verdict={verdict}"
        sel().log_api_access(
            caller=f"skills.install_guarded:{source}",
            operation="skill_install",
            outcome=outcome,
            source="skills",
            resources=f"{source}/{skill_id}",
            error=f"{detail} rules={rules}" if rules else detail,
        )
    except Exception:
        logger.debug("skill install SEL audit failed", exc_info=True)


# ── Registry ──────────────────────────────────────────────────────────────────


class SkillsRegistry:
    """Holds named ``SkillsMarketplace`` implementations."""

    def __init__(self) -> None:
        self._marketplaces: dict[str, SkillsMarketplace] = {}

    def register(self, name: str, marketplace: SkillsMarketplace) -> None:
        """Offer *marketplace* under *name*, in place of whatever held the name.

        One an app's code registers (the module the provider loader imports, or a call the app
        makes later) leaves with the app: unloading it (a disable, each uninstall rung, the start
        of an update) takes it back (:mod:`personalclaw.app_code`). So a removed app's marketplace
        is not listed or searched, and a search, preview or install that names it is refused
        instead of running the app's code. A core catalogue stays for the life of the process.
        """
        self._marketplaces[name] = marketplace
        app_code.keep(lambda: self._forget(name, marketplace))

    def _forget(self, name: str, marketplace: SkillsMarketplace) -> None:
        """Drop *name* if *marketplace* is still what it offers."""
        if self._marketplaces.get(name) is marketplace:
            del self._marketplaces[name]

    def unregister(self, name: str) -> None:
        """Remove a registered marketplace. Idempotent — a name that was never registered
        is a no-op. Used by transient, single-operation sources (a pack import registers a
        ``PackMarketplace`` for one commit, then unregisters it — it is not a public
        marketplace and must not outlive the import)."""
        self._marketplaces.pop(name, None)

    def get(self, name: str) -> SkillsMarketplace:
        mp = self._marketplaces.get(name)
        if mp is None:
            raise KeyError(f"No skills marketplace registered as {name!r}")
        return mp

    def list(self) -> "list[str]":
        return sorted(self._marketplaces)

    # `builtins.list`, not `list`: this class defines a method named `list` (just above), which
    # shadows the builtin inside its own annotation scope — so `"list[dict[str, str]]"` resolved
    # to the METHOD and mypy read the return type as uniterable. That was silenced with a
    # `type: ignore[valid-type]` and went unnoticed for as long as nobody iterated the result;
    # the first caller that did got `"list?[dict[str, str]]" has no attribute "__iter__"`.
    # Qualifying the name states the type correctly instead of suppressing the complaint.
    def info(self) -> "builtins.list[dict[str, str]]":
        return [
            {"name": n, "type": mp.marketplace_type, "trust_tier": mp.trust_tier}
            for n, mp in sorted(self._marketplaces.items())
        ]

    def install_guarded(
        self,
        marketplace_name: str,
        skill_id: str,
        target_dir: Path,
        *,
        force: bool = False,
    ) -> "InstallResult":
        """The install CHOKEPOINT (S3): every install routes through here so one gate
        covers all marketplaces and each ``install()`` stays a dumb file-writer.

        Resolves the registered marketplace by name and delegates to the shared gate
        :func:`install_scanned`. Raises :class:`SkillInstallRefused` on a blocked
        verdict; returns an :class:`InstallResult` on success."""
        return install_scanned(
            self.get(marketplace_name), marketplace_name, skill_id, target_dir, force=force
        )


def _tier_of(marketplace: "SkillsMarketplace") -> "Any":
    from personalclaw.supply_chain import TrustTier

    try:
        return TrustTier(marketplace.trust_tier)
    except ValueError:
        return TrustTier.COMMUNITY


def _installable(marketplace: "SkillsMarketplace", skill_id: str) -> "SkillDetail":
    """What the skill IS: its files minus the tooling no skill runs (a `.git`, a `__pycache__`
    whose bytecode the interpreter would run in place of the scanned source, a virtualenv).
    Decided once, here, so staging, the scan, the commit and the lock all see the same list —
    the scan reads every file it is handed, so it reads exactly what installs."""
    import dataclasses
    from pathlib import PurePosixPath

    from personalclaw.supply_chain import never_installed

    detail = marketplace.fetch(skill_id)
    return dataclasses.replace(
        detail,
        files=[
            entry
            for entry in detail.files
            if not any(never_installed(part) for part in PurePosixPath(entry.get("path", "")).parts)
        ],
    )


def _scan_staged(detail: "SkillDetail", skill_id: str, tier: "Any", staged_root: Path) -> "Any":
    """Stage ``detail``'s files under ``staged_root`` (path-safe) and scan them there.

    🔴 The QUARANTINE directory's name is marketplace-supplied too, and it had the same hole
    `install_skill_files` had (#739): `detail.name` comes from `fetch`, so a name of
    `"../../evil"` escaped the temp dir that exists to contain it — and it escaped HERE, which
    is before `scan_dir` runs, so the supply-chain gate could not refuse a write it had not yet
    been asked about. `_stage_files` validates every file path and then `mkdir`s whatever this
    expression produced.
    """
    from personalclaw.supply_chain import scan_dir

    staged_skill = record_path(staged_root, detail.name or skill_id, suffix="", kind="skill name")
    _stage_files(detail.files, staged_skill)
    return scan_dir(staged_skill, tier)


def warnings_consent(detail: "SkillDetail", report: "Any") -> str:
    """What a person accepts when they install a skill over a WARNING verdict: its warnings, and
    the exact files that were scanned for them. ``""`` for any other verdict.

    A digest of both, so an acceptance is bound to what was read: the same bytes scanned twice
    give the same value, and a file that changes after the scan (a second command appended to a
    script the scan had already flagged once) gives another, and is not installed on it.
    """

    from personalclaw.supply_chain import Verdict

    if report.verdict is not Verdict.WARNING:
        return ""
    digest = hashlib.sha256()
    for entry in sorted(detail.files, key=lambda e: str(e.get("path", ""))):
        body = entry.get("data")
        raw = body if isinstance(body, bytes) else str(entry.get("contents", "")).encode("utf-8")
        digest.update(f"{entry.get('path', '')}\0{hashlib.sha256(raw).hexdigest()}\n".encode())
    warned = [f for f in report.findings if f.severity is Verdict.WARNING]
    for line in sorted(f"{f.rule}\0{f.path}\0{f.evidence}" for f in warned):
        digest.update(f"{line}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


def scan_before_install(marketplace: "SkillsMarketplace", skill_id: str) -> "tuple[Any, str]":
    """The scan :func:`install_scanned` would make of this skill, without installing it, and the
    :func:`warnings_consent` an install over its warnings would need.

    The same files, staged the same way and scanned at the same tier, so a verdict shown before
    an install is the verdict the install reaches on unchanged bytes. Writes nothing but its
    own quarantine directory, which it removes, and audits nothing: nothing was installed or
    refused."""

    tier = _tier_of(marketplace)
    detail = _installable(marketplace, skill_id)
    staged_root = Path(tempfile.mkdtemp(prefix="pclaw-skill-quarantine-"))
    try:
        report = _scan_staged(detail, skill_id, tier, staged_root)
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)
    return report, warnings_consent(detail, report)


def install_scanned(
    marketplace: "SkillsMarketplace",
    source: str,
    skill_id: str,
    target_dir: Path,
    *,
    force: bool = False,
    accepted_warnings: str | None = None,
) -> "InstallResult":
    """The single supply-chain install gate — used by both registered-marketplace
    installs (:meth:`SkillsRegistry.install_guarded`) and app-owned skill seeding
    (:mod:`personalclaw.apps.skill_seed`), which passes a transient marketplace
    rooted at the app dir. One gate implementation, so nothing writes to the live
    skills tree without passing through it.

    fetch → stage to quarantine → whole-dir scan at the marketplace's trust tier →
    decide → commit the scanned bytes + ``.pclaw-lock.json`` provenance + SEL audit.

    - ``clean`` / ``low`` → commit.
    - ``warning`` → refuse, unless ``force`` (a calculated, explicit override) or
      ``accepted_warnings`` is the :func:`warnings_consent` of THIS scan of THESE files — the
      warnings a person read, on the bytes they were read on. Either way the acceptance is its
      own SEL event, naming the rules.
    - ``dangerous`` → REFUSE; neither overrides it (the load-bearing floor).

    Quarantine-first means dangerous content never lands in the live skills tree.
    Raises :class:`SkillInstallRefused` on a blocked verdict; returns an
    :class:`InstallResult` on success."""

    from personalclaw.supply_chain import Verdict

    tier = _tier_of(marketplace)
    detail = _installable(marketplace, skill_id)
    staged_root = Path(tempfile.mkdtemp(prefix="pclaw-skill-quarantine-"))
    try:
        # Stage the fetched payload to quarantine (path-safe) BEFORE any scan/commit.
        report = _scan_staged(detail, skill_id, tier, staged_root)
        _audit_install(source, skill_id, tier, report, outcome="scanned")

        if report.verdict is Verdict.DANGEROUS:
            _audit_install(source, skill_id, tier, report, outcome="refused")
            raise SkillInstallRefused(report, dangerous=True)
        if report.verdict is Verdict.WARNING:
            if not force and accepted_warnings != warnings_consent(detail, report):
                _audit_install(source, skill_id, tier, report, outcome="needs_confirm")
                raise SkillInstallRefused(report, dangerous=False)
            warned = {f.rule for f in report.findings if f.severity is Verdict.WARNING}
            rules = ",".join(sorted(warned))
            _audit_install(source, skill_id, tier, report, outcome="accepted", rules=rules)

        # Commit the EXACT bytes we just scanned — write ``detail.files`` (the same
        # in-memory payload that was staged + scanned) straight to the live tree.
        # We never re-fetch: a re-fetch would open a TOCTOU window (a server could
        # serve clean content to the scan and malicious content to the commit) and,
        # for the skills.sh CLI fallback, would skip the scan entirely. Committing the
        # scanned bytes closes both. install_skill_files re-runs its per-file scan as
        # defense-in-depth and validates SKILL.md.
        written = install_skill_files(detail.files, detail.name or skill_id, target_dir)
        _write_lock(target_dir, detail, source, tier, report)
        _audit_install(source, skill_id, tier, report, outcome="installed")
        return InstallResult(path=written, report=report, tier=tier)
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)


_DEFAULT_REGISTRY: SkillsRegistry | None = None


def get_default_skills_registry() -> SkillsRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SkillsRegistry()
    return _DEFAULT_REGISTRY


# ── Local skill discovery ─────────────────────────────────────────────────────


def list_local_skills(extra_paths: list[Path] | None = None) -> list[dict[str, str]]:
    """Scan all skill discovery paths and return a list of skill metadata dicts.

    Each dict contains: ``{name, description, path, source}``.
    The ``source`` field is the discovery directory name.

    🔴 RECURSIVE, through ``loader.iter_skill_files`` — the same enumeration the runtime
    loader uses. This walked ONE level with ``iterdir()``, so ``auto/`` (the namespace holding
    every accepted skill proposal, with no ``SKILL.md`` of its own) was skipped whole and both
    consumers went blind to it: ``personalclaw skills list`` printed none of them, and the
    loop classifier's capability catalog (``handlers/loop_routes._installed_capability_
    catalogs``) could never rank a skill the user had just approved. Measured before the fix:
    3 ``auto/*`` skills on disk, 0 in this list (#302, #409).

    ``name`` is the path RELATIVE to the discovery root, so ``auto/loop-worker`` keeps its
    namespace — which is what the loader calls it and what stops it colliding in ``seen_names``
    with a top-level skill of the same basename.
    """
    from personalclaw.skills.loader import iter_skill_files

    search_paths = skill_discovery_paths()
    if extra_paths:
        search_paths.extend(extra_paths)

    skills: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for base in search_paths:
        if not base.is_dir():
            continue
        for name, skill_md in iter_skill_files(base):
            if name in seen_names:
                continue  # project-level wins; skip duplicates
            seen_names.add(name)
            skills.append(
                {
                    "name": name,
                    "description": _parse_description(skill_md),
                    "path": str(skill_md),
                    "source": str(base),
                }
            )

    return skills


def _parse_description(skill_md: Path) -> str:
    """Extract the description field from SKILL.md YAML frontmatter.

    Delegated to the one parser. This copy's block-scalar folding was the ONLY
    capability any duplicate had over the loader, so it was promoted into
    `_parse_frontmatter_text` rather than dropped — the behavior is preserved,
    the duplication is not.
    """
    from personalclaw.skills.loader import SkillsLoader

    try:
        text = skill_md.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    return SkillsLoader._parse_frontmatter_text(text).get("description", "")


def install_skill_files(
    files: list[dict[str, str]],
    skill_name: str,
    target_base: Path,
) -> Path:
    """Make ``target_base/<skill_name>/`` hold exactly *files*, and nothing else.

    THE writer of an installed skill's folder: a marketplace install and reinstall, a pack's
    install and update, an app's skills, an import from another tool and the skills PersonalClaw
    ships all come here. Returns the path to the written SKILL.md.

    Every file is checked before anything is written: no path may climb out of the folder, the
    scanner must not find one dangerous, and a SKILL.md must validate. Then the files are written
    into a new folder beside the skill's, which takes its place whole
    (:func:`replace_skill_folder`): a file the previous version had and this one dropped goes with
    the old folder, and a refused or failed install leaves the installed copy exactly as it was.

    🔴 THE DIRECTORY NAME IS VALIDATED TOO. Every *file* path here was checked for ``..`` and for a
    leading ``/`` — and ``skill_name``, which names the directory all of them are written into, was
    not. So a marketplace-supplied name of ``"../../evil"`` escaped the skills tree entirely, and
    `mkdir(parents=True)` created wherever it landed (#739). Checking the leaves while the branch is
    unchecked is the whole bug: a safe relative path under an unsafe root is an unsafe path.

    Latent rather than exploited — only trusted marketplaces are registered by default — which is
    also why it is worth closing now rather than after that changes.
    """
    skill_dir = record_path(target_base, skill_name, suffix="", kind="skill name")

    # Supply-chain gate (S3): scan ALL incoming content with the shared scanner
    # BEFORE writing anything to disk. A skill carries executable instructions +
    # optional scripts — the same install-time gate apps run through. A
    # ``dangerous`` verdict is terminal (never written); the scan runs on the
    # in-memory payload so nothing dangerous ever touches the filesystem.
    from personalclaw.supply_chain import Verdict, default_scanner

    for file_entry in files:
        rel_path = file_entry.get("path", "")
        if not rel_path or ".." in rel_path or rel_path.startswith("/"):
            raise ValueError(f"Rejected unsafe file path: {rel_path!r}")
        # A binary entry (``data``) carries no scannable text — the text ruleset can't
        # analyze it; its provenance is the sha256 recorded in the lock. Only text
        # ``contents`` runs through the injection/destructive-script scan.
        if "data" in file_entry:
            continue
        contents = file_entry.get("contents", "")
        is_script = (
            rel_path.endswith((".sh", ".bash", ".py", ".js", ".rb", ".pl"))
            or "/scripts/" in f"/{rel_path}"
        )
        report = default_scanner.scan_text(
            contents,
            surface="script" if is_script else "manifest",
        )
        if report.verdict is Verdict.DANGEROUS:
            cats = ", ".join(sorted({f.rule for f in report.findings})) or "dangerous content"
            raise ValueError(
                f"skill install refused: scanner flagged {rel_path!r} as dangerous ({cats})"
            )

    skill_md: str | None = None
    for file_entry in files:
        rel_path = file_entry.get("path", "")
        if rel_path.endswith("SKILL.md"):
            errors = _validate_skill_md(str(file_entry.get("contents", "")))
            if errors:
                raise ValueError(f"SKILL.md validation failed: {'; '.join(errors)}")
            skill_md = rel_path
    if skill_md is None:
        raise ValueError(f"No SKILL.md found in files for skill {skill_name!r}")

    replace_skill_folder(skill_dir, files)
    return skill_dir / skill_md


def replace_skill_folder(folder: Path, files: "list[dict[str, Any]]") -> None:
    """Make *folder* hold exactly *files* (``{path, contents | data}`` entries) and nothing else.

    The files are written into a new folder beside it, under a hidden name no skill listing reads
    (``loader.iter_skill_files`` skips one), which then takes *folder*'s place: two renames in
    one directory. Whatever *folder* held goes with it; a failure before the swap leaves *folder*
    as it was. A *folder* that is a link is replaced, as a link: what it led to is not touched.
    """
    folder = Path(folder)
    parent = folder.parent
    parent.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:12]
    staging = parent / f".{folder.name}.{tag}.installing"
    old = parent / f".{folder.name}.{tag}.replaced"
    had = False
    try:
        staging.mkdir()
        for entry in files:
            out = staging / str(entry.get("path", ""))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(_entry_bytes(entry))
        had = folder.exists() or folder.is_symlink()
        if had:
            os.rename(folder, old)
        try:
            os.rename(staging, folder)
        except OSError:
            if had:
                os.rename(old, folder)
            raise
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if not had:
        return
    try:
        if old.is_symlink() or not old.is_dir():
            old.unlink()
        else:
            shutil.rmtree(old)
    except OSError:
        logger.warning("could not remove the replaced copy %s", old, exc_info=True)
