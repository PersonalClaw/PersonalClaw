"""Evidence bundle capture — the genuinely new mechanics of SV-10 (SELF-VERIFICATION §3.3).

A self-QA scenario leaves a **bundle**: the screenshots and screen recording the execute stage
captured, the contact-sheet and GIF derived from that recording, and any run logs — all indexed
by one SHA256'd ``manifest.json`` and registered as a **single Artifact**. This module is the
deterministic half of that: everything a model must not be trusted to do by hand.

Three shapes are load-bearing here, each a direct response to a way the LLM-authored version of
this node could lie:

**The digests are computed, never claimed.** The plan's evidence prompt says "compute the
digests; do not estimate them" — which is an instruction a model can ignore and no one would
notice. :func:`build_manifest` hashes the actual bytes on disk, so the manifest is a measurement
of the bundle rather than a description of it.

**ffmpeg degrades typed, never crashes.** The contact-sheet and GIF come from ffmpeg run as a
local subprocess. When ffmpeg is absent, :func:`derive_contact_sheet` / :func:`derive_gif` return
a :class:`Derivation` carrying a ``degraded_reason`` that :func:`build_manifest` records in the
manifest — a screenshots-only bundle that says *why* it is thinner, not a stack trace. The
availability probe is modelled on the docker/lima sandbox probes
(:func:`personalclaw.sandbox_providers.docker.docker_available`): a cached result with a short
TTL, and — deliberately — a ``None`` "never yet checked" sentinel rather than comparing
``time.monotonic()`` against ``0.0`` (a real reading of the monotonic clock can be small, so
``0.0`` is a valid cache time, not an "unset" marker).

**The completion gate checks KINDS present in the bundle.** :func:`check_required_kinds` is the
kind-level completion gate the ``selfqa-evidence`` provider runs — the deterministic,
engine-independent counterpart to the file-glob ``required_artifacts`` gate
(:func:`personalclaw.workflows.verify.check_required_artifacts`, WF2-R3): where that one refuses a
node that did not write its declared *files*, this one refuses a run whose bundle is missing a
required *kind*, and names the missing kinds so the failure says what to fix. The default required
kinds are the ffmpeg-independent proof (screenshot, recording, manifest), so a degraded
(ffmpeg-less) bundle is still *complete* and only a genuinely missing proof blocks.

Nothing here writes to ``memory.db`` or ``knowledge.db``: the bundle is an Artifact, and the
manifest is repo/run state. This follows §5's Memory-vs-Knowledge boundary for the companion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── artifact kinds a bundle can hold ──────────────────────────────────────────
#
# A KIND is what the completion gate reasons about; a file's NAME is incidental. Two screenshots
# and one recording are three files but two kinds, and the gate asks about kinds, so a bundle with
# ten screenshots and no recording is caught.
KIND_SCREENSHOT = "screenshot"
KIND_RECORDING = "recording"
KIND_CONTACT_SHEET = "contact_sheet"
KIND_GIF = "gif"
KIND_LOG = "log"
KIND_MANIFEST = "manifest"
KIND_OTHER = "other"

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

#: The bundle files, by convention, that the template's execute/evidence nodes write.
RECORDING_NAME = "recording.mp4"
CONTACT_SHEET_NAME = "contact-sheet.png"
GIF_NAME = "failure.gif"
SCREENSHOTS_DIR = "screenshots"

#: The default proof the completion gate requires. These are the ffmpeg-INDEPENDENT kinds, chosen
#: to match the evidence node's engine-enforced ``required_artifacts`` globs exactly. Contact-sheet
#: and GIF are enrichments derived from the recording, so requiring them would fail a bundle whose
#: only defect is a missing host tool — the degradation the Risk table calls "screenshots-only".
DEFAULT_REQUIRED_KINDS: tuple[str, ...] = (KIND_SCREENSHOT, KIND_RECORDING, KIND_MANIFEST)

_SHA256_CHUNK = 1 << 20  # 1 MiB — bound peak memory when hashing a large recording.


def classify_kind(relpath: str) -> str:
    """Classify a bundle-relative path into an artifact KIND.

    By path and suffix, deterministically. The order matters: the recording and the derived
    contact-sheet/GIF have fixed names, so they win over the generic image/screenshot rule that
    would otherwise swallow ``contact-sheet.png`` as a screenshot.
    """
    name = Path(relpath).name.lower()
    parts = [p.lower() for p in Path(relpath).parts]

    if name == MANIFEST_NAME:
        return KIND_MANIFEST
    if name == CONTACT_SHEET_NAME:
        return KIND_CONTACT_SHEET
    if name == GIF_NAME or name.endswith(".gif"):
        return KIND_GIF
    if name == RECORDING_NAME or name.endswith((".mp4", ".mov", ".webm", ".mkv")):
        return KIND_RECORDING
    if SCREENSHOTS_DIR in parts or name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return KIND_SCREENSHOT
    if name.endswith((".log", ".txt", ".ndjson", ".jsonl")):
        return KIND_LOG
    return KIND_OTHER


# ── ffmpeg availability probe (modelled on the docker/lima sandbox probes) ─────
#
# `_probe_cache` is `None` until the first probe, NOT a `(0.0, False)` pair. A monotonic reading
# can legitimately be a small float, so `0.0` is a valid "checked at t=0" time, not an "unset"
# marker — the docker probe makes exactly this choice, and the user's rule restates it.
FFMPEG_BIN = "ffmpeg"
_PROBE_TTL_SECS = 30.0
_probe_cache: tuple[float, bool] | None = None
_FFMPEG_PROBE_TIMEOUT = 5


def _ffmpeg_ping() -> bool:
    """True when the ffmpeg binary is on PATH AND answers ``-version``. Never raises.

    ``shutil.which`` alone proves the name resolves; running ``-version`` proves the binary is
    executable on this host (a stale symlink or a wrong-arch build resolves but cannot run). The
    argv is a fixed literal, so this is a host-fact read, not an agent-influenced spawn.
    """
    if not shutil.which(FFMPEG_BIN):
        return False
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, host-fact version probe
            [FFMPEG_BIN, "-version"],
            capture_output=True,
            text=True,
            timeout=_FFMPEG_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def ffmpeg_available(*, refresh: bool = False) -> bool:
    """Cached ffmpeg availability. ``refresh=True`` forces a re-probe (a doctor's live dot).

    Cached with a short TTL so a per-scenario derivation does not re-pay the ``-version`` spawn,
    exactly as :func:`personalclaw.sandbox_providers.docker.docker_available` caches its daemon
    ping.
    """
    global _probe_cache
    now = time.monotonic()
    if not refresh and _probe_cache is not None and (now - _probe_cache[0]) < _PROBE_TTL_SECS:
        return _probe_cache[1]
    ok = _ffmpeg_ping()
    _probe_cache = (now, ok)
    return ok


def reset_probe_cache() -> None:
    """Clear the cached probe. For tests, and for a host whose ffmpeg was just installed."""
    global _probe_cache
    _probe_cache = None


# ── typed graceful degradation ────────────────────────────────────────────────


@dataclass(frozen=True)
class Derivation:
    """The outcome of one ffmpeg derivation. ``produced`` and ``degraded_reason`` are exclusive.

    A produced derivation carries the ``name``/``path`` of the file it wrote; a degraded one
    carries a non-empty ``degraded_reason`` and no file. The reason is what
    :func:`build_manifest` records in the manifest, so a thin bundle explains itself instead of
    reading like a bug.
    """

    kind: str
    name: str = ""
    path: str = ""
    produced: bool = False
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "produced": self.produced,
            "degraded_reason": self.degraded_reason,
        }


_REASON_NO_FFMPEG = "ffmpeg is not available on this host, so it was skipped"
_REASON_NO_RECORDING = f"no {RECORDING_NAME} to derive from, so it was skipped"
_FFMPEG_RUN_TIMEOUT = 120

#: One frame every N seconds tiled into the contact sheet (§3.3: "1 frame/5s").
CONTACT_SHEET_FRAME_INTERVAL_SECS = 5
CONTACT_SHEET_COLUMNS = 4
#: The GIF's frame rate and width — small enough to embed, per crabbox's watchable-proof set.
GIF_FPS = 5
GIF_WIDTH = 640


def _run_ffmpeg(argv: list[str]) -> tuple[bool, str]:
    """Run one ffmpeg command as a local subprocess. Returns ``(ok, stderr_tail)``.

    The caller composes a FIXED filter argv; the only caller-derived values are input/output
    paths inside the bundle dir. A failure returns ``(False, <stderr>)`` rather than raising, so
    the derivation degrades to a recorded reason instead of taking the node down.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - fixed filter argv, no shell, host media tool
            [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", *argv],
            capture_output=True,
            text=True,
            timeout=_FFMPEG_RUN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)[:300]
    if proc.returncode != 0:
        return False, (proc.stderr or "").strip()[:300]
    return True, ""


def derive_contact_sheet(
    bundle_dir: Path | str,
    *,
    recording_name: str = RECORDING_NAME,
    out_name: str = CONTACT_SHEET_NAME,
) -> Derivation:
    """Derive a contact-sheet PNG from the recording (ffmpeg tile filter, 1 frame / 5s).

    Degrades typed: a missing recording or an absent ffmpeg returns a :class:`Derivation` with a
    ``degraded_reason`` and produces no file. An ffmpeg failure is degraded the same way, carrying
    ffmpeg's own stderr as the reason.
    """
    root = Path(bundle_dir)
    recording = root / recording_name
    if not recording.is_file():
        return Derivation(kind=KIND_CONTACT_SHEET, degraded_reason=_REASON_NO_RECORDING)
    if not ffmpeg_available():
        return Derivation(kind=KIND_CONTACT_SHEET, degraded_reason=_REASON_NO_FFMPEG)

    out = root / out_name
    # `fps=1/N` picks one frame every N seconds; `tile` lays them into a grid. The grid is one
    # row-per-COLUMNS wide, which keeps a long recording from producing a single unreadable strip.
    vf = f"fps=1/{CONTACT_SHEET_FRAME_INTERVAL_SECS},tile={CONTACT_SHEET_COLUMNS}x0"
    ok, err = _run_ffmpeg(["-i", str(recording), "-vf", vf, "-frames:v", "1", str(out)])
    if not ok:
        return Derivation(
            kind=KIND_CONTACT_SHEET,
            degraded_reason=(
                f"ffmpeg could not build the contact sheet: {err}"
                if err
                else "ffmpeg could not build the contact sheet"
            ),
        )
    return Derivation(kind=KIND_CONTACT_SHEET, name=out_name, path=str(out), produced=True)


def derive_gif(
    bundle_dir: Path | str,
    *,
    recording_name: str = RECORDING_NAME,
    out_name: str = GIF_NAME,
    start_secs: float | None = None,
    window_secs: float | None = None,
) -> Derivation:
    """Derive a trimmed GIF from the recording (ffmpeg palettegen for faithful colour).

    ``start_secs``/``window_secs`` trim to the interesting window (§3.3: the failure window ±10s);
    ``None`` means the whole recording. Degrades typed exactly like :func:`derive_contact_sheet`.
    """
    root = Path(bundle_dir)
    recording = root / recording_name
    if not recording.is_file():
        return Derivation(kind=KIND_GIF, degraded_reason=_REASON_NO_RECORDING)
    if not ffmpeg_available():
        return Derivation(kind=KIND_GIF, degraded_reason=_REASON_NO_FFMPEG)

    out = root / out_name
    trim: list[str] = []
    if start_secs is not None:
        trim += ["-ss", f"{max(0.0, float(start_secs)):.3f}"]
    if window_secs is not None:
        trim += ["-t", f"{max(0.0, float(window_secs)):.3f}"]
    # `palettegen`/`paletteuse` in one graph gives a GIF whose colours are not crushed to the
    # default 216-colour web palette — the difference between a watchable proof and a smear.
    vf = (
        f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,"
        "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
    )
    ok, err = _run_ffmpeg([*trim, "-i", str(recording), "-vf", vf, str(out)])
    if not ok:
        return Derivation(
            kind=KIND_GIF,
            degraded_reason=(
                f"ffmpeg could not build the GIF: {err}"
                if err
                else "ffmpeg could not build the GIF"
            ),
        )
    return Derivation(kind=KIND_GIF, name=out_name, path=str(out), produced=True)


# ── manifest (SHA256) ──────────────────────────────────────────────────────────


@dataclass
class ManifestEntry:
    """One file in the bundle, with its content digest. The manifest's per-file record."""

    kind: str
    name: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "name": self.name, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ManifestEntry":
        return cls(
            kind=str(d.get("kind", "") or ""),
            name=str(d.get("name", "") or ""),
            size=int(d.get("size", 0) or 0),
            sha256=str(d.get("sha256", "") or ""),
        )


@dataclass
class Manifest:
    """The bundle index: schema-versioned, one :class:`ManifestEntry` per file, plus the reasons
    any ffmpeg-derived kind was skipped.

    ``degraded`` is the honesty half — an entry ``{"kind": "gif", "reason": "…"}`` records that a
    kind is *absent on purpose*, so a reader can tell a degraded bundle from a broken one.
    """

    schema_version: int = MANIFEST_SCHEMA_VERSION
    scenario_id: str = ""
    sha: str = ""
    passed: bool = False
    files: list[ManifestEntry] = field(default_factory=list)
    degraded: list[dict[str, str]] = field(default_factory=list)

    def kinds(self) -> set[str]:
        """The set of kinds actually present as files (a degraded kind is NOT present)."""
        return {e.kind for e in self.files}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "sha": self.sha,
            "passed": self.passed,
            "files": [e.to_dict() for e in self.files],
            "degraded": [dict(d) for d in self.degraded],
        }

    def to_json(self) -> str:
        # `sort_keys` so a bundle hashes identically across machines; indented so a human can read
        # it in the artifact viewer.
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Manifest":
        raw_files = d.get("files")
        raw_degraded = d.get("degraded")
        return cls(
            schema_version=int(d.get("schema_version", MANIFEST_SCHEMA_VERSION) or 0),
            scenario_id=str(d.get("scenario_id", "") or ""),
            sha=str(d.get("sha", "") or ""),
            passed=bool(d.get("passed", False)),
            files=(
                [ManifestEntry.from_dict(e) for e in raw_files if isinstance(e, dict)]
                if isinstance(raw_files, list)
                else []
            ),
            degraded=(
                [
                    {"kind": str(x.get("kind", "")), "reason": str(x.get("reason", ""))}
                    for x in raw_degraded
                    if isinstance(x, dict)
                ]
                if isinstance(raw_degraded, list)
                else []
            ),
        )


def sha256_file(path: Path | str) -> str:
    """The hex SHA256 of a file's bytes, read in bounded chunks so a large MP4 is not slurped."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_SHA256_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    bundle_dir: Path | str,
    *,
    scenario_id: str = "",
    sha: str = "",
    passed: bool = False,
    degradations: tuple[Derivation, ...] = (),
    exclude: tuple[str, ...] = (MANIFEST_NAME,),
) -> Manifest:
    """Walk ``bundle_dir`` and record every file with its size and SHA256.

    The manifest.json itself is excluded (it cannot list its own not-yet-written digest), as is
    anything named in ``exclude``. ``degradations`` carries the :class:`Derivation` results whose
    file was NOT produced, so the reasons land in the manifest's ``degraded`` section.
    """
    root = Path(bundle_dir)
    manifest = Manifest(scenario_id=scenario_id, sha=sha, passed=passed)

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if path.name in exclude:
            continue
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except OSError:
            logger.warning("selfqa evidence: could not hash %s", rel, exc_info=True)
            continue
        manifest.files.append(
            ManifestEntry(kind=classify_kind(rel), name=rel, size=size, sha256=digest)
        )

    for deriv in degradations:
        if not deriv.produced and deriv.degraded_reason:
            manifest.degraded.append({"kind": deriv.kind, "reason": deriv.degraded_reason})

    return manifest


def write_manifest(bundle_dir: Path | str, manifest: Manifest) -> Path:
    """Write ``manifest.json`` into the bundle dir and return its path."""
    out = Path(bundle_dir) / MANIFEST_NAME
    out.write_text(manifest.to_json() + "\n", encoding="utf-8")
    return out


def load_manifest(bundle_dir: Path | str) -> Manifest | None:
    """Read a bundle's ``manifest.json`` back into a :class:`Manifest`, or None if unreadable."""
    path = Path(bundle_dir) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Manifest.from_dict(data) if isinstance(data, dict) else None


# ── required-kinds completion gate (criterion 7) ───────────────────────────────


@dataclass(frozen=True)
class GateResult:
    """The completion gate's verdict: complete iff every required kind is present."""

    complete: bool
    present: list[str]
    missing: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "present": sorted(self.present),
            "missing": list(self.missing),
        }


def check_required_kinds(
    manifest: Manifest, required_kinds: tuple[str, ...] = DEFAULT_REQUIRED_KINDS
) -> GateResult:
    """Refuse completion until every ``required_kinds`` entry is present as a file in the bundle.

    A self-QA run counts complete only when the configured proof kinds are all present; a missing
    kind marks the run incomplete and names what is missing, so the failure is actionable rather
    than "the gate said no". Order in ``missing`` follows ``required_kinds`` so the message reads
    the way the author declared it.
    """
    present = manifest.kinds()
    # A `Manifest` object exists only because a manifest.json was (or is about to be) written, so
    # the manifest kind is present by construction — `build_manifest` cannot list the manifest
    # among its own files (it hashes bytes on disk, and the manifest is written afterward), so
    # deriving it purely from the file list would make the default gate always report it missing.
    present = present | {KIND_MANIFEST}
    missing = [k for k in required_kinds if k not in present]
    return GateResult(complete=not missing, present=sorted(present), missing=missing)


# ── register the bundle as ONE Artifact ────────────────────────────────────────


@dataclass(frozen=True)
class RegisteredBundle:
    """What :func:`register_bundle` produced: one Artifact's slug, its ``artifact:<slug>`` ref,
    and how many of the manifest's files were copied under the artifact dir."""

    slug: str
    ref: str
    kinds: list[str]
    file_count: int
    stored_files: int


def _content_addressed_name(entry: ManifestEntry) -> str:
    """A ``<stem>@<sha>.<ext>`` companion-file name the artifact store accepts.

    The store's companion-file namespace is content-addressed (``native._MEDIA_NAME_RE``): the
    ``@<hex>`` makes it disjoint from version snapshots, so the same unchanged file re-stored is
    written once. The manifest's own sha256 IS that address, so a bundle file and its stored copy
    are provably the same bytes. The stem is the file's base name with the directory dropped
    (companion names are flat), sanitised to the ``[\\w.-]`` the store allows.
    """
    stem = Path(entry.name).stem
    ext = Path(entry.name).suffix
    safe_stem = "".join(c if (c.isalnum() or c in "._-") else "-" for c in stem) or "file"
    digest = entry.sha256[:32] if entry.sha256 else "0" * 12
    return f"{safe_stem}@{digest}{ext}"


def register_bundle(
    bundle_dir: Path | str,
    *,
    manifest: Manifest | None = None,
    scenario_id: str = "",
    sha: str = "",
    passed: bool = False,
    project_id: str = "",
    provider: str = "native",
    name: str = "",
) -> RegisteredBundle:
    """Register the bundle as a **single** Artifact and return its ref.

    The manifest is the artifact's content (so the artifact IS the index, per WORK-R4's "evidence
    bundle = Artifact composition"); every manifest file is copied under the artifact's dir
    content-addressed, so the artifact is self-contained rather than a set of pointers into a run
    workspace that a later teardown could remove. One Artifact, not one-per-file: the bundle is a
    composition, and a reviewer opens one thing.

    ``manifest`` may be supplied (already built) or read from the bundle's ``manifest.json``.
    Raises ``FileNotFoundError`` when neither exists — a bundle with no manifest is not a bundle.
    """
    root = Path(bundle_dir)
    if manifest is None:
        manifest = load_manifest(root)
    if manifest is None:
        raise FileNotFoundError(f"no {MANIFEST_NAME} in {root} and none supplied")

    from personalclaw.artifacts.registry import get_provider

    art_provider = get_provider(provider)
    if art_provider is None:
        raise RuntimeError(f"no artifact provider {provider!r} to register the evidence bundle")

    label = name or f"Self-QA evidence — {scenario_id or manifest.scenario_id or 'scenario'}"
    art = art_provider.create(
        name=label,
        content=manifest.to_json(),
        kind="json",
        source="subagent",
        description=(
            f"Self-QA evidence bundle for commit {(sha or manifest.sha)[:8]} — "
            f"{len(manifest.files)} file(s) under one SHA256 manifest."
        ),
        tags=["self-qa", "evidence"],
        actor="self-qa",
        project_id=project_id,
    )

    stored = 0
    for entry in manifest.files:
        src = root / entry.name
        try:
            data = src.read_bytes()
        except OSError:
            logger.warning("selfqa evidence: could not read %s for registration", entry.name)
            continue
        if art_provider.store_version_file(art.slug, _content_addressed_name(entry), data):
            stored += 1

    return RegisteredBundle(
        slug=art.slug,
        ref=f"artifact:{art.slug}",
        kinds=sorted(manifest.kinds()),
        file_count=len(manifest.files),
        stored_files=stored,
    )
