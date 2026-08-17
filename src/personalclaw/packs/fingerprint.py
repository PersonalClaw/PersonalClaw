"""Project-fingerprint auto-surfacing (AGENT-PACKS §7, AP-7).

Packs get *discovered*, not only installed: a project that binds a codebase on disk
(:attr:`personalclaw.tasks.models.Project.workspace_dir`) is matched against the file-shape
rules packs declare, and a match **proposes** a pack. Four properties are the whole point,
and each one is a mechanism here rather than a promise in a docstring:

**Zero-LLM.** The scanner is deterministic file-shape matching: sorted walk, glob match,
literal substring signals inside the files a glob already selected. No model call, no
provider, no sampling seam — this module imports none of them, and
``tests/test_packs_fingerprint.py`` asserts that statically (an AST sweep over this file's
imports) *and* dynamically (a scan with the model-call audit sink wired to explode records
zero attempts). "Zero-LLM" is otherwise unfalsifiable prose.

**On project-create and on-demand ONLY.** :func:`scan_project` takes a mandatory ``reason``
from :data:`SCAN_REASONS` and raises on anything else, so a background loop cannot quietly
acquire a scan: it would have to invent a reason name and fail. Nothing in this module
schedules itself, and no read path (``GET /api/projects/{id}``) calls it.

**Propose-only.** A scan writes NOTHING. :func:`scan_project` returns
:class:`PackProposal` values; installing is a separate, human-driven call into
:mod:`packs.import_`. The one thing this module ever persists is a *rejection* — the user's
"no" — which is written only by :func:`reject_proposal`.

**A rejection is remembered per (project, pack) and never re-nags.**
``<home>/packs/fingerprint_rejections.json`` (§9) holds ``{project_id: {pack: decided_at}}``
and :func:`scan_project` filters against it, so a second scan after a rejection is silent.

``packs.fingerprint_enabled = false`` stops scanning entirely — checked first, before the
workspace is even resolved, so "off" costs zero directory reads.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

#: The rejection store (§9). ``{project_id: {pack_name: decided_at}}`` — nested rather than a
#: composed ``"<project>:<pack>"`` key so no id containing the delimiter can collide with
#: another pair.
REJECTIONS_FILE = "fingerprint_rejections.json"

#: The ONLY reasons a scan may run (§7: "on project creation and on-demand, never on a
#: background loop in v1"). :func:`scan_project` refuses any other value, which is what makes
#: the negative clause enforceable instead of aspirational — a timer cannot pass this gate
#: without a code change that a reviewer sees.
SCAN_REASON_CREATE = "project-create"
SCAN_REASON_ON_DEMAND = "on-demand"
SCAN_REASONS = frozenset({SCAN_REASON_CREATE, SCAN_REASON_ON_DEMAND})

#: Directory names never walked. Build output and dependency trees would make a scan both slow
#: and wrong (a vendored ``*.tf`` in ``node_modules`` is not evidence about THIS project).
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".terraform",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".gradle",
        ".idea",
        ".vscode",
        ".next",
        ".nuxt",
        "__pycache__",
        "node_modules",
        "venv",
        ".venv",
        "site-packages",
        "dist",
        "build",
        "target",
        "vendor",
        "coverage",
    }
)

#: Walk bound. A pathological tree must not turn "create a project" into a stall; past the cap
#: the walk stops and the match is computed from what was seen (recorded in ``files_scanned``,
#: so a caller can tell a bounded scan from an exhaustive one).
_MAX_FILES = 20_000

#: Per-rule bound on how many glob-matched files are opened for signal scanning, and how many
#: bytes of each. Signals are corroboration for a shape a glob already established, so reading
#: the first chunk of a handful of matches is enough — and it keeps the scan's cost bounded by
#: the RULE, not by the size of the user's repo.
_MAX_SIGNAL_FILES = 25
_MAX_SIGNAL_BYTES = 64 * 1024


# ── declared rules ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fingerprint:
    """One declared file-shape rule, as a pack's ``pack.json`` carries it (§7).

    ``globs`` scope the rule to a file shape and are REQUIRED: a signals-only rule would
    have nothing to bound its reads with, so :func:`parse_fingerprints` drops it. ``signals``
    are literal substrings looked for inside the glob-matched files — corroboration, not the
    trigger. ``confidence`` is the rule author's ceiling; the score a user sees is that
    ceiling scaled by how much of the rule actually matched (:func:`_score`).
    """

    label: str
    globs: tuple[str, ...]
    signals: tuple[str, ...] = ()
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "globs": list(self.globs),
            "signals": list(self.signals),
            "confidence": self.confidence,
        }


def parse_fingerprints(raw: Any) -> list[Fingerprint]:
    """Parse a ``fingerprints`` manifest value into rules, dropping unusable rows.

    Lenient by design (the .ovsvoice best-effort forward-import rule, §1): a pack from a
    future schema may carry rule shapes this build does not understand, and one such row must
    not make the whole pack unfingerprintable. A row with no usable ``globs`` is dropped —
    it could otherwise match every project on signals alone.
    """
    if not isinstance(raw, list):
        return []
    out: list[Fingerprint] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        globs = tuple(str(g) for g in (row.get("globs") or []) if str(g).strip())
        if not globs:
            continue
        try:
            confidence = float(row.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = min(1.0, max(0.0, confidence))
        out.append(
            Fingerprint(
                label=str(row.get("label") or "").strip() or "matching project",
                globs=globs,
                signals=tuple(str(s) for s in (row.get("signals") or []) if str(s)),
                confidence=confidence,
            )
        )
    return out


def declared_fingerprints() -> dict[str, list[Fingerprint]]:
    """Every fingerprint rule declared by a pack this build can offer, keyed by pack name.

    v1 reads the BUNDLED packs' authored manifests: a proposal has to be actionable, and the
    only packs a user can install with one click today are the ones in the wheel. A remote
    catalog entry carrying its own ``fingerprints`` folds in here later without touching any
    caller — the return shape is already "pack name → rules".
    """
    from personalclaw.packs.bundled import bundled_packs

    out: dict[str, list[Fingerprint]] = {}
    for pack in bundled_packs():
        try:
            manifest = json.loads((pack.source / "pack.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rules = parse_fingerprints(manifest.get("fingerprints"))
        if rules:
            out[pack.name] = rules
    return out


# ── match results ─────────────────────────────────────────────────────────────


@dataclass
class FingerprintMatch:
    """One rule's outcome against one workspace — the arithmetic behind the score.

    Every input to :attr:`confidence` is carried alongside it, so a UI can explain the number
    instead of asserting it. An unexplained score is worse than no score.
    """

    label: str
    confidence: float
    declared_confidence: float
    matched_globs: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    declared_globs: list[str] = field(default_factory=list)
    declared_signals: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # example matched paths, workspace-rel

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "declared_confidence": self.declared_confidence,
            "matched_globs": list(self.matched_globs),
            "matched_signals": list(self.matched_signals),
            "declared_globs": list(self.declared_globs),
            "declared_signals": list(self.declared_signals),
            "evidence": list(self.evidence),
        }


@dataclass
class PackProposal:
    """A propose-only pack card (§7): what matched, how strongly, and what it WOULD install.

    ``inspect`` is the §3.1 dry-run report (:func:`packs.import_.inspect_pack`'s
    ``ImportPlan.to_dict``) — the "here's what it would install" half of the card, computed
    without a single write to home state. It is None only when the plan could not be built
    (a broken bundled pack); the proposal still surfaces so a user is not left wondering why a
    match vanished, and the reason rides in ``inspect_error``.
    """

    project_id: str
    pack: str
    display_name: str
    description: str
    version: str
    confidence: float
    matches: list[FingerprintMatch] = field(default_factory=list)
    files_scanned: int = 0
    inspect: dict[str, Any] | None = None
    inspect_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "pack": self.pack,
            "displayName": self.display_name,
            "description": self.description,
            "version": self.version,
            "confidence": self.confidence,
            "matches": [m.to_dict() for m in self.matches],
            "files_scanned": self.files_scanned,
            "inspect": self.inspect,
            "inspect_error": self.inspect_error,
        }


# ── the scanner ───────────────────────────────────────────────────────────────


def _walk(workspace: Path) -> tuple[list[str], bool]:
    """Every file under ``workspace`` as a sorted POSIX-relative path list, plus a truncated
    flag. Sorted so two scans of one tree produce byte-identical results — determinism is a
    property of this function, not a convention callers keep."""
    import os

    rels: list[str] = []
    truncated = False
    for root, dirs, files in os.walk(workspace, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".ruff"))
        base = Path(root)
        for name in sorted(files):
            path = base / name
            if path.is_symlink():
                # A symlink's bytes come from outside the tree the user pointed us at.
                continue
            try:
                rels.append(path.relative_to(workspace).as_posix())
            except ValueError:  # pragma: no cover - relative_to on a walked child
                continue
            if len(rels) >= _MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    return sorted(rels), truncated


def _glob_hits(rels: list[str], pattern: str) -> list[str]:
    """Paths matching ``pattern`` against the workspace-relative path OR the basename.

    Both, because a rule author writes ``*.tf`` meaning "a Terraform file anywhere" and
    ``.github/workflows/*.yml`` meaning a specific location — one match mode would break one
    of the two spellings, and the rule author has no way to know which we chose.
    """
    hits = [
        r for r in rels if fnmatch.fnmatch(r, pattern) or fnmatch.fnmatch(Path(r).name, pattern)
    ]
    return hits


def _signal_hits(workspace: Path, candidates: list[str], signals: tuple[str, ...]) -> list[str]:
    """Which ``signals`` appear inside the glob-matched ``candidates``.

    Literal substring matching — no regex, so a rule author cannot write a pattern that
    backtracks for a minute on an adversarial file. Reads are bounded on both axes
    (:data:`_MAX_SIGNAL_FILES`, :data:`_MAX_SIGNAL_BYTES`) and scoped to files a glob already
    selected, so the scanner never opens a file the rule did not point at.
    """
    if not signals:
        return []
    found: set[str] = set()
    for rel in candidates[:_MAX_SIGNAL_FILES]:
        try:
            with (workspace / rel).open("rb") as fh:
                blob = fh.read(_MAX_SIGNAL_BYTES)
        except OSError:
            continue
        text = blob.decode("utf-8", errors="replace")
        for sig in signals:
            if sig in text:
                found.add(sig)
        if len(found) == len(signals):
            break
    return [s for s in signals if s in found]


def _score(rule: Fingerprint, glob_hits: int, signal_hits: int) -> float:
    """The confidence a user sees: the rule's declared ceiling scaled by its COVERAGE.

    ``coverage`` is the mean of the two fractions the rule declared — how many of its globs
    matched and how many of its signals were found — or just the glob fraction when the rule
    declares no signals. So a 0.9 rule with 2 globs and 2 signals that hit 1 glob and both
    signals scores ``0.9 * ((1/2 + 2/2) / 2) = 0.675 → 0.68``.

    Scaling rather than reporting the declared number is the honest form: the declared value
    is the author's claim about a FULL match, and a partial match is weaker evidence than the
    author's ceiling. Rounded to two decimals because a third digit implies a precision
    file-shape matching does not have.
    """
    glob_fraction = glob_hits / len(rule.globs) if rule.globs else 0.0
    if rule.signals:
        coverage = (glob_fraction + signal_hits / len(rule.signals)) / 2
    else:
        coverage = glob_fraction
    return round(rule.confidence * coverage, 2)


def match_workspace(
    workspace: Path | str, rules: list[Fingerprint]
) -> tuple[list[FingerprintMatch], int]:
    """Match ``rules`` against the files under ``workspace``; return (matches, files_scanned).

    A rule matches only when at least one GLOB hit — signals alone never propose anything,
    because a signal is corroboration for a file shape and a project with none of the shape is
    not the project the rule describes. Deterministic and read-only.
    """
    root = Path(workspace)
    if not root.is_dir():
        return [], 0
    rels, _truncated = _walk(root)
    out: list[FingerprintMatch] = []
    for rule in rules:
        hit_globs: list[str] = []
        candidates: list[str] = []
        for pattern in rule.globs:
            hits = _glob_hits(rels, pattern)
            if hits:
                hit_globs.append(pattern)
                candidates.extend(hits)
        if not hit_globs:
            continue
        signals = _signal_hits(root, sorted(set(candidates)), rule.signals)
        out.append(
            FingerprintMatch(
                label=rule.label,
                confidence=_score(rule, len(hit_globs), len(signals)),
                declared_confidence=rule.confidence,
                matched_globs=hit_globs,
                matched_signals=signals,
                declared_globs=list(rule.globs),
                declared_signals=list(rule.signals),
                evidence=sorted(set(candidates))[:5],
            )
        )
    out.sort(key=lambda m: (-m.confidence, m.label))
    return out, len(rels)


# ── rejection memory (§9) ─────────────────────────────────────────────────────


def _rejections_path(home: Path | None = None) -> Path:
    return (home or config_dir()) / "packs" / REJECTIONS_FILE


def load_rejections(home: Path | None = None) -> dict[str, dict[str, str]]:
    """``{project_id: {pack: decided_at}}`` — every remembered "no" (empty when none)."""
    path = _rejections_path(home)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("fingerprint rejection store unreadable at %s", path)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for project_id, packs in raw.items():
        if isinstance(packs, dict):
            out[str(project_id)] = {str(k): str(v) for k, v in packs.items()}
    return out


def is_rejected(project_id: str, pack: str, home: Path | None = None) -> bool:
    """Has this exact (project, pack) pair already been turned down?"""
    return pack in load_rejections(home).get(project_id, {})


def reject_proposal(project_id: str, pack: str, home: Path | None = None) -> None:
    """Remember that this project's user does not want this pack — forever (§7).

    The ONLY write in this module. Idempotent: re-rejecting keeps the FIRST decision's
    timestamp, because the durable fact is *when the user said no*, and overwriting it every
    time the UI re-posts would erase that.
    """
    from personalclaw.atomic_write import atomic_write

    project_id = str(project_id).strip()
    pack = str(pack).strip()
    if not project_id or not pack:
        raise ValueError("a rejection needs both a project id and a pack name")
    store = load_rejections(home)
    packs = dict(store.get(project_id, {}))
    if pack in packs:
        return
    packs[pack] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store[project_id] = packs
    path = _rejections_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(store, indent=2, sort_keys=True) + "\n")


# ── the propose-only entry point ──────────────────────────────────────────────


def fingerprinting_enabled(config: Any = None) -> bool:
    """``packs.fingerprint_enabled`` — the one kill switch (§8).

    Read through the real config so the toggle a user flips in Settings is the value that
    governs. Fails OPEN to the dataclass default only when the config itself is unreadable,
    matching the guard-flag tenet the field is parsed with.
    """
    if config is None:
        from personalclaw.config.loader import AppConfig

        try:
            config = AppConfig.load()
        except Exception:  # noqa: BLE001 - an unreadable config must not crash project create
            logger.warning("could not load config for fingerprint gate", exc_info=True)
            return True
    return bool(getattr(getattr(config, "packs", None), "fingerprint_enabled", True))


def scan_project(
    project: Any,
    *,
    reason: str,
    config: Any = None,
    home: Path | None = None,
    with_inspect: bool = True,
) -> list[PackProposal]:
    """Propose packs matching ``project``'s bound workspace. Writes NOTHING.

    ``reason`` must be one of :data:`SCAN_REASONS` — ``"project-create"`` or ``"on-demand"``.
    Any other value raises :class:`ValueError`, which is how "never on a background loop"
    is enforced rather than merely documented.

    Returns [] — without touching the filesystem — when fingerprinting is disabled, when the
    project binds no ``workspace_dir``, or when the bound path is sensitive/system (the same
    two guards the project-create route applies before storing the binding). Already-installed
    packs and already-rejected (project, pack) pairs are filtered out, so a second scan after
    a rejection is silent.
    """
    if reason not in SCAN_REASONS:
        raise ValueError(
            f"fingerprint scans run on {sorted(SCAN_REASONS)} only, not {reason!r} "
            "(§7: never on a background loop)"
        )
    if not fingerprinting_enabled(config):
        return []
    workspace = str(getattr(project, "workspace_dir", "") or "").strip()
    if not workspace:
        return []

    from personalclaw.security import is_sensitive_path, is_system_path

    if is_sensitive_path(workspace) or is_system_path(workspace):
        logger.info("fingerprint scan refused for a sensitive/system workspace")
        return []
    root = Path(workspace).expanduser()
    if not root.is_dir():
        return []

    project_id = str(getattr(project, "id", "") or "")
    rejected = set(load_rejections(home).get(project_id, {}))
    installed = _installed_names(home)

    from personalclaw.packs.bundled import get_bundled

    out: list[PackProposal] = []
    for pack_name, rules in sorted(declared_fingerprints().items()):
        if pack_name in rejected or pack_name in installed:
            continue
        matches, files_scanned = match_workspace(root, rules)
        if not matches:
            continue
        bundled = get_bundled(pack_name)
        if bundled is None:  # pragma: no cover - declared_fingerprints reads bundled_packs
            continue
        proposal = PackProposal(
            project_id=project_id,
            pack=pack_name,
            display_name=bundled.display_name,
            description=bundled.description,
            version=bundled.version,
            # The card's headline number is the STRONGEST matching rule, not a blend: two
            # rules for one pack are alternative descriptions of the same project, and
            # averaging them would let a weak second rule drag a confident match down.
            confidence=max(m.confidence for m in matches),
            matches=matches,
            files_scanned=files_scanned,
        )
        if with_inspect:
            proposal.inspect, proposal.inspect_error = _inspect_report(pack_name)
        out.append(proposal)
    out.sort(key=lambda p: (-p.confidence, p.pack))
    logger.info(
        "fingerprint scan (%s) proposed %d pack(s) for project %s", reason, len(out), project_id
    )
    return out


def _installed_names(home: Path | None) -> set[str]:
    """Pack names already on this machine — proposing one of those is nagging, not helping."""
    from personalclaw.packs.installed import load_installed

    try:
        return {p.name for p in load_installed(home)}
    except Exception:  # noqa: BLE001 - an unreadable ledger must not break a scan
        logger.warning("installed-pack ledger unreadable during a scan", exc_info=True)
        return set()


def _inspect_report(pack_name: str) -> tuple[dict[str, Any] | None, str]:
    """The §3.1 dry-run report for a bundled pack — the "what it would install" half.

    Built by assembling the pack into a SYSTEM tempdir and running :func:`inspect_pack`, which
    extracts to its own tempdir and writes nothing to home state. The archive is deleted
    afterwards: a bundled pack is reproducible from the wheel, so keeping it would be state
    nothing reads.
    """
    import shutil
    import tempfile

    from personalclaw.packs.bundled import BundledPackError, build_bundled
    from personalclaw.packs.import_ import PackImportRefused, inspect_pack
    from personalclaw.supply_chain import TrustTier

    staging = Path(tempfile.mkdtemp(prefix="pclaw-fingerprint-"))
    try:
        archive = build_bundled(pack_name, staging / f"{pack_name}.pclaw")
        return inspect_pack(archive, tier=TrustTier.BUILTIN).to_dict(), ""
    except (BundledPackError, PackImportRefused) as exc:
        logger.warning("inspect report unavailable for pack %s: %s", pack_name, exc)
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(staging, ignore_errors=True)
