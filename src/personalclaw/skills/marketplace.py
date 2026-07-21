"""Skills marketplace — abstract base, SkillsRegistry, and local skill discovery.

The agentskills.io format (https://agentskills.io) is the standard:
  - A skill is a directory containing a SKILL.md file with YAML frontmatter.
  - Frontmatter fields: name, description, license, compatibility, metadata, allowed-tools.
  - The body is Markdown loaded on demand by the LLM.

Discovery paths (loaded by ``_all_skill_paths()`` in ``agent.py``):
  - ``~/.agents/skills/``        — agentskills.io cross-client standard
  - ``PERSONALCLAW_PROJECT_DIR/skills/``  — project-level
  - ``~/.personalclaw/skills/``      — user-created

``SkillsRegistry`` holds named ``SkillsMarketplace`` implementations.
Additional marketplaces (skills.sh, custom registries) register via
``get_default_skills_registry().register(name, marketplace)``.
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd

        return _cd()
    except Exception:
        from pathlib import Path as _P

        return _P.home() / ".personalclaw"


logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SKILL_FILENAME = "SKILL.md"

# Standard discovery paths in priority order
SKILL_DISCOVERY_PATHS: list[Path] = [
    Path.home() / ".agents" / "skills",  # agentskills.io cross-client standard
    _path_home_pclaw() / "skills",  # user-created skills
]

# Default target for `skills install` when the caller doesn't override.
# Matches the first discovery path so the installed skill is immediately
# visible to running sessions without further config.
DEFAULT_SKILLS_INSTALL_PATH: Path = Path.home() / ".agents" / "skills"


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "url": self.url,
            "installs": self.installs,
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


def _write_lock(
    target_dir: Path, detail: "SkillDetail", source: str, tier: "Any", report: "Any"
) -> None:
    """Record install provenance + an integrity baseline in ``<skill>/.pclaw-lock.json``:
    id, source, tier, verdict, per-file sha256, timestamp. A later integrity lint (S6)
    compares on-disk sha256 vs this to detect a skill mutated after install."""
    import hashlib
    import json
    import time

    skill_dir = Path(target_dir) / (detail.name or detail.id)
    if not skill_dir.is_dir():
        return
    hashes: dict[str, str] = {}
    for entry in detail.files:
        rel = entry.get("path", "")
        if rel and ".." not in rel and not rel.startswith("/"):
            hashes[rel] = hashlib.sha256(_entry_bytes(entry)).hexdigest()
    lock = {
        "id": detail.id,
        "source": source,
        "trust_tier": getattr(tier, "value", str(tier)),
        "verdict": getattr(report.verdict, "value", str(report.verdict)),
        "sha256": hashes,
        "installed_at": time.time(),
    }
    try:
        (skill_dir / ".pclaw-lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("could not write skill lock file for %s", skill_dir, exc_info=True)


@dataclass
class IntegrityReport:
    """S6 post-install integrity check for one skill vs its ``.pclaw-lock.json``.

    ``ok`` is True when every locked file's on-disk sha256 matches the baseline recorded
    at install. ``mutated`` / ``missing`` / ``added`` name the drift so a tamper (a skill
    edited on disk after a clean install) is visible. ``unlocked`` = no lock file (a
    pre-gate or hand-placed skill — not a failure, just unverifiable)."""

    skill: str
    ok: bool = True
    unlocked: bool = False
    mutated: list[str] = field(default_factory=list)  # locked file whose hash changed
    missing: list[str] = field(default_factory=list)  # locked file now gone
    added: list[str] = field(default_factory=list)  # new file not in the lock

    def summary(self) -> str:
        if self.unlocked:
            return f"{self.skill}: no lock (unverifiable)"
        if self.ok:
            return f"{self.skill}: intact"
        parts = []
        if self.mutated:
            parts.append(f"{len(self.mutated)} mutated")
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.added:
            parts.append(f"{len(self.added)} added")
        return f"{self.skill}: TAMPERED ({', '.join(parts)})"


def verify_skill_integrity(skill_dir: Path) -> IntegrityReport:
    """S6: compare a skill's on-disk file hashes against its ``.pclaw-lock.json`` baseline
    to detect post-install mutation (a skill edited/replaced after a clean install — the
    tamper case a static install-time scan can't catch on its own). Content-only:
    ``.pclaw-lock.json`` itself is excluded. Emits a SEL audit on detected tamper."""
    import hashlib
    import json

    skill_dir = Path(skill_dir)
    name = skill_dir.name
    lock_path = skill_dir / ".pclaw-lock.json"
    if not lock_path.is_file():
        return IntegrityReport(skill=name, unlocked=True)
    try:
        locked = (json.loads(lock_path.read_text(encoding="utf-8")) or {}).get("sha256", {})
    except (OSError, json.JSONDecodeError):
        return IntegrityReport(skill=name, unlocked=True)

    rep = IntegrityReport(skill=name)
    on_disk: dict[str, str] = {}
    for f in sorted(skill_dir.rglob("*")):
        if not f.is_file() or f.name == ".pclaw-lock.json":
            continue
        rel = f.relative_to(skill_dir).as_posix()
        try:
            on_disk[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            continue
    for rel, want in locked.items():
        got = on_disk.get(rel)
        if got is None:
            rep.missing.append(rel)
        elif got != want:
            rep.mutated.append(rel)
    for rel in on_disk:
        if rel not in locked:
            rep.added.append(rel)
    rep.ok = not (rep.mutated or rep.missing or rep.added)
    if not rep.ok:
        try:
            from personalclaw.sel import sel

            sel().log_api_access(
                caller="skills.verify_integrity",
                operation="skill_integrity",
                outcome="tampered",
                source="skills",
                resources=name,
                error=rep.summary(),
            )
        except Exception:
            logger.debug("integrity SEL audit failed", exc_info=True)
    return rep


def _audit_install(source: str, skill_id: str, tier: "Any", report: "Any", *, outcome: str) -> None:
    """Emit a SEL audit event for a scan/install/refuse (best-effort)."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"skills.install_guarded:{source}",
            operation="skill_install",
            outcome=outcome,
            source="skills",
            resources=f"{source}/{skill_id}",
            error=f"tier={getattr(tier, 'value', tier)} verdict={getattr(report.verdict, 'value', report.verdict)}",  # noqa: E501
        )
    except Exception:
        logger.debug("skill install SEL audit failed", exc_info=True)


# ── Registry ──────────────────────────────────────────────────────────────────


class SkillsRegistry:
    """Holds named ``SkillsMarketplace`` implementations."""

    def __init__(self) -> None:
        self._marketplaces: dict[str, SkillsMarketplace] = {}

    def register(self, name: str, marketplace: SkillsMarketplace) -> None:
        self._marketplaces[name] = marketplace

    def get(self, name: str) -> SkillsMarketplace:
        mp = self._marketplaces.get(name)
        if mp is None:
            raise KeyError(f"No skills marketplace registered as {name!r}")
        return mp

    def list(self) -> "list[str]":
        return sorted(self._marketplaces)

    def info(self) -> "list[dict[str, str]]":  # type: ignore[valid-type]  # CI-1
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

        fetch → stage to quarantine → whole-dir scan at the marketplace's trust tier →
        decide → commit or refuse, recording ``lock.json`` provenance + a SEL audit.

        - ``clean`` / ``low`` → commit.
        - ``warning`` → refuse unless ``force`` (a calculated, explicit override).
        - ``dangerous`` → REFUSE; ``force`` does NOT override (the load-bearing floor).

        Quarantine-first means dangerous content never lands in the live skills tree.
        Raises :class:`SkillInstallRefused` on a blocked verdict; returns an
        :class:`InstallResult` on success."""
        import shutil
        import tempfile

        from personalclaw.supply_chain import TrustTier, Verdict, scan_dir

        mp = self.get(marketplace_name)
        try:
            tier = TrustTier(mp.trust_tier)
        except ValueError:
            tier = TrustTier.COMMUNITY

        detail = mp.fetch(skill_id)
        staged_root = Path(tempfile.mkdtemp(prefix="pclaw-skill-quarantine-"))
        try:
            # Stage the fetched payload to quarantine (path-safe) BEFORE any scan/commit.
            staged_skill = staged_root / (detail.name or skill_id)
            _stage_files(detail.files, staged_skill)

            report = scan_dir(staged_skill, tier)
            _audit_install(marketplace_name, skill_id, tier, report, outcome="scanned")

            if report.verdict is Verdict.DANGEROUS:
                _audit_install(marketplace_name, skill_id, tier, report, outcome="refused")
                raise SkillInstallRefused(report, dangerous=True)
            if report.verdict is Verdict.WARNING and not force:
                _audit_install(marketplace_name, skill_id, tier, report, outcome="needs_confirm")
                raise SkillInstallRefused(report, dangerous=False)

            # Commit the EXACT bytes we just scanned — write ``detail.files`` (the same
            # in-memory payload that was staged + scanned) straight to the live tree.
            # We never re-fetch: a re-fetch would open a TOCTOU window (a server could
            # serve clean content to the scan and malicious content to the commit) and,
            # for the skills.sh CLI fallback, would skip the scan entirely. Committing the
            # scanned bytes closes both. install_skill_files re-runs its per-file scan as
            # defense-in-depth and validates SKILL.md.
            written = install_skill_files(detail.files, detail.name or skill_id, target_dir)
            _write_lock(target_dir, detail, marketplace_name, tier, report)
            _audit_install(marketplace_name, skill_id, tier, report, outcome="installed")
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
    """
    search_paths = list(SKILL_DISCOVERY_PATHS)
    if extra_paths:
        search_paths.extend(extra_paths)

    skills: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for base in search_paths:
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / _SKILL_FILENAME
            if not skill_md.is_file():
                continue
            name = entry.name
            if name in seen_names:
                continue  # project-level wins; skip duplicates
            seen_names.add(name)
            description = _parse_description(skill_md)
            skills.append(
                {
                    "name": name,
                    "description": description,
                    "path": str(skill_md),
                    "source": str(base),
                }
            )

    return skills


def _parse_description(skill_md: Path) -> str:
    """Extract the description field from SKILL.md YAML frontmatter."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    frontmatter = text[3:end]
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^description:\s*(.*)$", line)
        if not m:
            continue
        value = m.group(1).strip().strip("\"'")
        if value in ("|", ">", "|+", "|-", ">+", ">-"):
            # YAML block scalar — collect indented continuation lines
            parts: list[str] = []
            for cont in lines[i + 1 :]:
                if cont and cont[0] in (" ", "\t"):
                    parts.append(cont.strip())
                else:
                    break
            return " ".join(parts)
        return value
    return ""


def _validate_skill_md(contents: str) -> list[str]:
    """Return validation errors for a SKILL.md string; empty = valid."""
    errors: list[str] = []
    if not contents.strip().startswith("---"):
        errors.append("SKILL.md must start with YAML frontmatter (---)")
        return errors
    end = contents.find("\n---", 3)
    if end == -1:
        errors.append("SKILL.md frontmatter is not closed with ---")
        return errors
    frontmatter = contents[3:end]
    # Check required name field
    name_m = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
    if not name_m:
        errors.append("SKILL.md frontmatter missing required 'name' field")
    else:
        name = name_m.group(1).strip().strip("\"'")
        if not _NAME_RE.match(name):
            errors.append(f"SKILL.md name must match ^[a-z0-9][a-z0-9-]{{0,62}}$ (got {name!r})")
    # Check required description field
    if not re.search(r"^description:\s*.+$", frontmatter, re.MULTILINE):
        errors.append("SKILL.md frontmatter missing required 'description' field")
    return errors


def install_skill_files(
    files: list[dict[str, str]],
    skill_name: str,
    target_base: Path,
) -> Path:
    """Write skill files to ``target_base/<skill_name>/``.

    Validates SKILL.md content and rejects any path containing ``..``.
    Returns the path to the written SKILL.md.
    """
    skill_dir = target_base / skill_name

    # Supply-chain gate (S3): scan ALL incoming content with the shared scanner
    # BEFORE writing anything to disk. A skill carries executable instructions +
    # optional scripts — the same install-time gate apps run through. A
    # ``dangerous`` verdict is terminal (never written); the scan runs on the
    # in-memory payload so nothing dangerous ever touches the filesystem.
    from personalclaw.supply_chain import Verdict, default_scanner

    for file_entry in files:
        rel_path = file_entry.get("path", "")
        if ".." in rel_path or rel_path.startswith("/"):
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

    skill_dir.mkdir(parents=True, exist_ok=True)
    written_skill_md: Path | None = None
    for file_entry in files:
        rel_path = file_entry.get("path", "")
        out_path = skill_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if rel_path.endswith("SKILL.md") or rel_path == "SKILL.md":
            errors = _validate_skill_md(str(file_entry.get("contents", "")))
            if errors:
                raise ValueError(f"SKILL.md validation failed: {'; '.join(errors)}")
            written_skill_md = out_path
        out_path.write_bytes(_entry_bytes(file_entry))

    if written_skill_md is None:
        raise ValueError(f"No SKILL.md found in files for skill {skill_name!r}")
    return written_skill_md
