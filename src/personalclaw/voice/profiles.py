"""The ``voice_profiles`` entity store (MULTIMODAL-IO §1).

Voice today is a flat string (a piper ``.onnx`` name) resolved by
:func:`personalclaw.tts.registry.active_voice_params`. This module promotes it to a
first-class entity so a voice can carry reference audio, a pinned seed, a bounded
generation history, and consent provenance — the things a cloning engine needs and a
bare string cannot hold.

Layout (per-entity JSON + a self-contained sibling dir, the tasks/projects pattern —
no sqlite for an entity family, no absolute paths inside a record so a profile dir
stays bundle-able for a future exporter)::

    <home>/voice_profiles/
      vp-<8hex>.json                 # the record
      vp-<8hex>/ref_audio.<ext>      # reference clip (clone kind)
      vp-<8hex>/locked.wav           # lock-from-history artifact (§1.2)
      vp-<8hex>/consent.<ext>        # consent recording (§1.3)
      vp-<8hex>/history/<n>.wav      # bounded generation history (last 10)

Two rules in here are load-bearing and deliberately paranoid:

**1. ``verified_own_voice`` is RECOMPUTED, never believed.** The stored flag is
written for readability only; every read recomputes it from the artifacts on disk
(a consent recording of at least :data:`MIN_CONSENT_SECS` plus non-empty
``consent_text``). Hand-editing ``"verified_own_voice": true`` into the JSON does
not flip it — a forgeable provenance flag is worse than no flag, because it is the
one field a future off-machine export would trust.

**2. Ids are symlink-contained.** An id is matched against
:data:`_ID_RE` (no separators, no ``..``, no absolute paths) *and* every derived
path is resolved and asserted to live under the resolved profiles root, so a planted
symlink (``voice_profiles/vp-evil -> /etc``) cannot be read or written through.
Same posture as the uploads store's destination validation.

Naming discipline (§ plan Overview): this is NOT ``AgentConfig.voice``, which is the
agent's persona *text*. Nothing here adds a bare ``voice`` config key.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import time
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

# A profile id is server-generated as ``vp-<8hex>``, but the accept-side pattern is
# the broader "no separators, no traversal" shape so an older/renamed id still reads.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

KINDS = ("clone", "design")

#: How many generations a profile remembers. The bound is enforced on every append
#: (records AND files), never left to a comment: unbounded per-generation wavs would
#: turn a chatty voice surface into a disk leak.
HISTORY_MAX = 10

#: A consent recording shorter than this cannot verify a voice. Below one second
#: there is nothing to hear, so an empty ping would otherwise "prove" consent.
MIN_CONSENT_SECS = 1.0

#: Fallback floor for containers we cannot read a duration from (mp3/m4a/webm):
#: ~1s at 128 kbit/s. A byte floor is weaker than a real duration, so wav — which we
#: can measure exactly — is what the UI records.
MIN_CONSENT_BYTES = 16_000

_ARTIFACT_REF = "ref_audio"
_ARTIFACT_CONSENT = "consent"
_ARTIFACT_LOCKED = "locked"

#: Artifacts a client may read back. ``consent`` is deliberately absent: a consent
#: recording is provenance evidence, not media to re-serve.
READABLE_ARTIFACTS = (_ARTIFACT_REF, _ARTIFACT_LOCKED)


class VoiceProfileError(Exception):
    """A validation/lookup failure carrying the HTTP status the route should use."""

    def __init__(self, message: str, status: int = 400, reason: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        #: A stable machine-readable tag (``consent_required``, ``not_found``, …).
        self.reason = reason or "invalid_request"


@dataclass
class VoiceProfile:
    """One voice: what renders it, how it is conditioned, and who consented."""

    id: str
    name: str = ""
    kind: str = "design"
    provider: str = ""
    model: str = ""
    # clone kind
    ref_audio: str = ""  # relative to the profile dir; never absolute
    ref_text: str = ""
    # design kind
    design_params: dict[str, Any] = field(default_factory=dict)
    instruct: str = ""
    # shared
    seed: int = 0  # 0 = unseeded
    language: str = ""
    speed: float = 1.0
    locked: bool = False
    locked_at: str = ""
    # consent-as-provenance
    verified_own_voice: bool = False
    consent_text: str = ""
    consent_audio: str = ""
    consent_recorded_at: str = ""
    # bounded generation history: {path, seed, text_hash, created_at}
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "ref_audio": self.ref_audio,
            "ref_text": self.ref_text,
            "design_params": dict(self.design_params),
            "instruct": self.instruct,
            "seed": self.seed,
            "language": self.language,
            "speed": self.speed,
            "locked": self.locked,
            "locked_at": self.locked_at,
            "verified_own_voice": self.verified_own_voice,
            "consent_text": self.consent_text,
            "consent_audio": self.consent_audio,
            "consent_recorded_at": self.consent_recorded_at,
            "history": [dict(h) for h in self.history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "VoiceProfile":
        def _f(key: str, default: float) -> float:
            try:
                return float(raw.get(key, default))
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int) -> int:
            try:
                return int(raw.get(key, default))
            except (TypeError, ValueError):
                return default

        params = raw.get("design_params")
        history = raw.get("history")
        return cls(
            id=str(raw.get("id") or ""),
            name=str(raw.get("name") or ""),
            kind=str(raw.get("kind") or "design"),
            provider=str(raw.get("provider") or ""),
            model=str(raw.get("model") or ""),
            ref_audio=str(raw.get("ref_audio") or ""),
            ref_text=str(raw.get("ref_text") or ""),
            design_params=dict(params) if isinstance(params, dict) else {},
            instruct=str(raw.get("instruct") or ""),
            seed=_i("seed", 0),
            language=str(raw.get("language") or ""),
            speed=_f("speed", 1.0),
            locked=bool(raw.get("locked")),
            locked_at=str(raw.get("locked_at") or ""),
            # NOTE: whatever the file claims here is overwritten by the recompute in
            # :func:`get_profile`. Kept only so a round-trip preserves the shape.
            verified_own_voice=bool(raw.get("verified_own_voice")),
            consent_text=str(raw.get("consent_text") or ""),
            consent_audio=str(raw.get("consent_audio") or ""),
            consent_recorded_at=str(raw.get("consent_recorded_at") or ""),
            history=(
                [dict(h) for h in history if isinstance(h, dict)]
                if isinstance(history, list)
                else []
            ),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )


# ── paths + containment ─────────────────────────────────────────────────────


def profiles_root() -> Path:
    """``<home>/voice_profiles`` (not created — readers tolerate absence)."""
    return config_dir() / "voice_profiles"


def validate_id(profile_id: str) -> str:
    """Return the id, or raise if it could escape the profiles dir.

    Rejects separators, ``..``, absolute paths, NUL, and anything outside
    ``[A-Za-z0-9_-]{1,64}`` — the traversal half of the containment rail. The
    symlink half lives in :func:`_within`.
    """
    pid = str(profile_id or "")
    if not _ID_RE.match(pid):
        raise VoiceProfileError(f"invalid profile id: {pid!r}", 400, "invalid_profile_id")
    return pid


def _within(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and assert it stays under the resolved ``root``.

    ``Path.resolve()`` follows symlinks, so a planted ``vp-evil -> /etc`` resolves
    outside the root and is refused here rather than read through.
    """
    root_real = root.resolve()
    real = candidate.resolve()
    if real != root_real and root_real not in real.parents:
        raise VoiceProfileError("path escapes the voice_profiles dir", 400, "path_escape")
    return candidate


def profile_path(profile_id: str) -> Path:
    """The record file for ``profile_id``, containment-checked."""
    pid = validate_id(profile_id)
    return _within(profiles_root(), profiles_root() / f"{pid}.json")


def profile_dir(profile_id: str) -> Path:
    """The artifact dir for ``profile_id``, containment-checked."""
    pid = validate_id(profile_id)
    return _within(profiles_root(), profiles_root() / pid)


def artifact_path(profile_id: str, relative: str) -> Path:
    """A path inside the profile dir, contained against the *resolved* profile dir."""
    pdir = profile_dir(profile_id)
    rel = str(relative or "").strip()
    if not rel or rel.startswith("/") or "\x00" in rel or ".." in Path(rel).parts:
        raise VoiceProfileError(f"invalid artifact path: {rel!r}", 400, "invalid_artifact")
    return _within(pdir, pdir / rel)


# ── consent provenance (recomputed, never believed) ─────────────────────────


def _audio_at_least(path: Path, secs: float) -> bool:
    """True when ``path`` holds at least ``secs`` of audio."""
    try:
        if not path.is_file():
            return False
        with contextlib.closing(wave.open(str(path), "rb")) as handle:
            rate = handle.getframerate()
            if rate > 0:
                return (handle.getnframes() / float(rate)) >= secs
    except Exception:
        logger.debug("not a readable wav, falling back to a byte floor: %s", path.name)
    try:
        return path.stat().st_size >= MIN_CONSENT_BYTES
    except OSError:
        return False


def consent_recording(profile_id: str) -> Path | None:
    """The consent recording found ON DISK, or None.

    Discovered by globbing the profile dir rather than by reading the record's
    ``consent_audio`` field: the whole point of the recompute is that no stored
    string decides whether consent exists.
    """
    try:
        pdir = profile_dir(profile_id)
    except VoiceProfileError:
        return None
    if not pdir.is_dir():
        return None
    for candidate in sorted(pdir.glob(f"{_ARTIFACT_CONSENT}.*")):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def recompute_verified(profile: VoiceProfile) -> bool:
    """Derive ``verified_own_voice`` from artifacts on disk.

    The single non-forgeable rule: consent is verified only when a real recording of
    at least :data:`MIN_CONSENT_SECS` exists on disk *and* the consent text is
    non-empty. Called on every read, so the stored flag is a cache, never an
    authority — and the recording is found by looking, not by believing a path field.
    """
    if not profile.consent_text.strip():
        return False
    path = consent_recording(profile.id)
    if path is None:
        return False
    return _audio_at_least(path, MIN_CONSENT_SECS)


def assert_artifact_release_allowed(profile: VoiceProfile, artifact: str) -> None:
    """Gate reading a clone profile's audio back out of the store.

    Consent gates *off-machine* use (§1.3): plain local synthesis is never gated, but
    handing the reference/locked clip of a cloned voice back over HTTP is the machine
    boundary, so a clone-kind profile must be verified. Revoking consent deletes the
    recording, the recompute goes false, and this starts refusing — the revocation is
    an actual block, not a flag nobody reads.
    """
    if artifact not in READABLE_ARTIFACTS:
        raise VoiceProfileError(f"artifact not readable: {artifact}", 403, "artifact_not_readable")
    if profile.kind == "clone" and not profile.verified_own_voice:
        raise VoiceProfileError(
            "consent for this cloned voice is not verified", 403, "consent_required"
        )


# ── CRUD ────────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def new_profile_id() -> str:
    return f"vp-{uuid.uuid4().hex[:8]}"


def _read(path: Path) -> VoiceProfile | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("unreadable voice profile: %s", path.name, exc_info=True)
        return None
    if not isinstance(raw, dict):
        return None
    profile = VoiceProfile.from_dict(raw)
    if not profile.id:
        return None
    # The recompute: whatever the file said about consent is discarded here.
    profile.verified_own_voice = recompute_verified(profile)
    return profile


def _write(profile: VoiceProfile) -> None:
    profile.updated_at = _now()
    profile.verified_own_voice = recompute_verified(profile)
    root = profiles_root()
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(
        profile_path(profile.id),
        json.dumps(profile.to_dict(), indent=2),
        mode=0o600,
    )


def get_profile(profile_id: str) -> VoiceProfile | None:
    """One profile with consent recomputed, or None."""
    path = profile_path(profile_id)
    if not path.is_file():
        return None
    return _read(path)


def require_profile(profile_id: str) -> VoiceProfile:
    profile = get_profile(profile_id)
    if profile is None:
        raise VoiceProfileError(f"no such voice profile: {profile_id}", 404, "not_found")
    return profile


def list_profiles() -> list[VoiceProfile]:
    """Every profile, newest first. An unreadable record is skipped, not fatal."""
    root = profiles_root()
    if not root.is_dir():
        return []
    out: list[VoiceProfile] = []
    for path in sorted(root.glob("*.json")):
        try:
            _within(root, path)
        except VoiceProfileError:
            logger.warning("skipping voice profile outside the store: %s", path.name)
            continue
        profile = _read(path)
        if profile is not None:
            out.append(profile)
    out.sort(key=lambda p: p.created_at, reverse=True)
    return out


_MUTABLE_FIELDS = (
    "name",
    "provider",
    "model",
    "ref_text",
    "design_params",
    "instruct",
    "seed",
    "language",
    "speed",
)


def _apply(profile: VoiceProfile, fields: dict[str, Any]) -> None:
    for key in _MUTABLE_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "design_params":
            profile.design_params = dict(value) if isinstance(value, dict) else {}
        elif key == "seed":
            try:
                profile.seed = int(value)
            except (TypeError, ValueError) as exc:
                raise VoiceProfileError("seed must be an integer", 400, "invalid_seed") from exc
        elif key == "speed":
            try:
                profile.speed = float(value)
            except (TypeError, ValueError) as exc:
                raise VoiceProfileError("speed must be a number", 400, "invalid_speed") from exc
        else:
            setattr(profile, key, str(value or ""))


def create_profile(**fields: Any) -> VoiceProfile:
    """Create a profile. ``kind`` must be clone|design; the id is server-generated."""
    kind = str(fields.get("kind") or "design")
    if kind not in KINDS:
        raise VoiceProfileError(f"kind must be one of {KINDS}", 400, "invalid_kind")
    name = str(fields.get("name") or "").strip()
    if not name:
        raise VoiceProfileError("name required", 400, "name_required")
    profile = VoiceProfile(id=new_profile_id(), name=name, kind=kind, created_at=_now())
    _apply(profile, fields)
    profile_dir(profile.id).mkdir(parents=True, exist_ok=True)
    _write(profile)
    return profile


def update_profile(profile_id: str, **fields: Any) -> VoiceProfile:
    """Patch the mutable fields. ``kind``/``id``/consent/lock are not settable here —
    consent has its own audited endpoints and lock has its own transition."""
    profile = require_profile(profile_id)
    if "kind" in fields and str(fields["kind"]) != profile.kind:
        raise VoiceProfileError("kind is immutable", 400, "kind_immutable")
    _apply(profile, fields)
    _write(profile)
    return profile


def delete_profile(profile_id: str) -> bool:
    """Delete the record and its artifact dir (audio included).

    A planted symlink is unlinked, never recursed through: deleting a profile must
    not become a way to delete whatever the link points at.
    """
    pid = validate_id(profile_id)
    root = profiles_root()
    path = root / f"{pid}.json"
    existed = path.is_file() and not path.is_symlink()
    with contextlib.suppress(OSError):
        path.unlink()
    pdir = root / pid
    if pdir.is_symlink():
        with contextlib.suppress(OSError):
            pdir.unlink()
    elif pdir.is_dir():
        shutil.rmtree(pdir, ignore_errors=True)
    return existed


# ── ref audio + consent artifacts ───────────────────────────────────────────


def _audio_suffix(source: Path, suffix: str = "") -> str:
    """A safe lowercase extension for an incoming clip (no path, no traversal)."""
    ext = (suffix or Path(source).suffix or ".wav").lower()
    if not re.match(r"^\.[A-Za-z0-9]{1,8}$", ext):
        raise VoiceProfileError(f"invalid audio extension: {ext!r}", 400, "invalid_extension")
    return ext


def attach_ref_audio(profile_id: str, source: Path, *, suffix: str = "") -> VoiceProfile:
    """Move a completed upload in as the profile's reference clip."""
    profile = require_profile(profile_id)
    ext = _audio_suffix(source, suffix)
    dest = artifact_path(profile_id, f"{_ARTIFACT_REF}{ext}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Replace any previous clip so a profile never keeps two references.
    for stale in profile_dir(profile_id).glob(f"{_ARTIFACT_REF}.*"):
        if stale != dest:
            with contextlib.suppress(OSError):
                stale.unlink()
    shutil.move(str(source), str(dest))
    dest.chmod(0o600)
    profile.ref_audio = dest.name
    _write(profile)
    return profile


def attach_consent_audio(profile_id: str, source: Path, *, suffix: str = "") -> VoiceProfile:
    """Move a completed upload in as the consent recording.

    Text and audio arrive independently (a JSON POST for the statement, a resumable
    upload for the clip), so neither ordering is privileged — verification is the
    recompute over whatever ends up on disk.
    """
    profile = require_profile(profile_id)
    ext = _audio_suffix(source, suffix)
    dest = artifact_path(profile_id, f"{_ARTIFACT_CONSENT}{ext}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in profile_dir(profile_id).glob(f"{_ARTIFACT_CONSENT}.*"):
        if stale != dest:
            with contextlib.suppress(OSError):
                stale.unlink()
    shutil.move(str(source), str(dest))
    dest.chmod(0o600)
    profile.consent_audio = dest.name
    if not profile.consent_recorded_at:
        profile.consent_recorded_at = _now()
    _write(profile)
    return profile


def record_consent(
    profile_id: str, *, consent_text: str, audio_source: Path | None = None, suffix: str = ""
) -> VoiceProfile:
    """Record consent provenance. Verification still comes from the artifacts."""
    profile = require_profile(profile_id)
    text = str(consent_text or "").strip()
    if not text:
        raise VoiceProfileError("consent_text required", 400, "consent_text_required")
    profile.consent_text = text
    existing = consent_recording(profile_id)
    if existing is not None:
        # A recording that arrived first (upload before statement) is adopted, so the
        # two halves can land in either order.
        profile.consent_audio = existing.name
    if audio_source is not None:
        ext = _audio_suffix(audio_source, suffix)
        dest = artifact_path(profile_id, f"{_ARTIFACT_CONSENT}{ext}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        for stale in profile_dir(profile_id).glob(f"{_ARTIFACT_CONSENT}.*"):
            if stale != dest:
                with contextlib.suppress(OSError):
                    stale.unlink()
        shutil.move(str(audio_source), str(dest))
        dest.chmod(0o600)
        profile.consent_audio = dest.name
    profile.consent_recorded_at = _now()
    _write(profile)
    return profile


def revoke_consent(profile_id: str) -> VoiceProfile:
    """Delete the consent recording and clear its provenance fields."""
    profile = require_profile(profile_id)
    for stale in profile_dir(profile_id).glob(f"{_ARTIFACT_CONSENT}.*"):
        with contextlib.suppress(OSError):
            stale.unlink()
    profile.consent_text = ""
    profile.consent_audio = ""
    profile.consent_recorded_at = ""
    _write(profile)
    return profile


# ── bounded generation history + lock ───────────────────────────────────────


def append_history(
    profile_id: str, audio: Path, *, seed: int = 0, text_hash: str = ""
) -> VoiceProfile:
    """Record one generation, keeping at most :data:`HISTORY_MAX` (records + files).

    ``audio`` is copied into ``<profile>/history/<n>.wav``; the oldest entries beyond
    the bound are dropped and their files deleted, so history cannot grow without
    limit no matter how chatty the surface is.
    """
    profile = require_profile(profile_id)
    hdir = artifact_path(profile_id, "history")
    hdir.mkdir(parents=True, exist_ok=True)
    slot = f"history/{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.wav"
    dest = artifact_path(profile_id, slot)
    shutil.copyfile(audio, dest)
    dest.chmod(0o600)
    profile.history.append(
        {
            "path": slot,
            "seed": int(seed),
            "text_hash": str(text_hash or ""),
            "created_at": _now(),
        }
    )
    while len(profile.history) > HISTORY_MAX:
        dropped = profile.history.pop(0)
        with contextlib.suppress(OSError, VoiceProfileError):
            artifact_path(profile_id, str(dropped.get("path") or "")).unlink()
    _write(profile)
    return profile


def lock_profile(profile_id: str, history_index: int) -> VoiceProfile:
    """Freeze one generation: copy its audio to ``locked.wav`` and pin its seed."""
    profile = require_profile(profile_id)
    try:
        index = int(history_index)
    except (TypeError, ValueError) as exc:
        raise VoiceProfileError(
            "history_index must be an integer", 400, "invalid_history_index"
        ) from exc
    if not profile.history:
        raise VoiceProfileError("profile has no generation history", 409, "empty_history")
    if index < 0 or index >= len(profile.history):
        raise VoiceProfileError(
            f"history_index out of range (0..{len(profile.history) - 1})",
            404,
            "history_index_out_of_range",
        )
    entry = profile.history[index]
    source = artifact_path(profile_id, str(entry.get("path") or ""))
    if not source.is_file():
        raise VoiceProfileError("that generation's audio is gone", 409, "history_audio_missing")
    dest = artifact_path(profile_id, "locked.wav")
    shutil.copyfile(source, dest)
    dest.chmod(0o600)
    profile.locked = True
    profile.locked_at = _now()
    try:
        profile.seed = int(entry.get("seed") or 0)
    except (TypeError, ValueError):
        profile.seed = 0
    _write(profile)
    return profile


def unlock_profile(profile_id: str) -> VoiceProfile:
    """Clear the lock: drop ``locked.wav`` and unpin the seed (variation returns)."""
    profile = require_profile(profile_id)
    with contextlib.suppress(OSError, VoiceProfileError):
        artifact_path(profile_id, "locked.wav").unlink()
    profile.locked = False
    profile.locked_at = ""
    profile.seed = 0
    _write(profile)
    return profile


def profile_payload(profile: VoiceProfile) -> dict[str, Any]:
    """The API/WS shape: the record plus which artifacts actually exist on disk.

    Clients ask "is there a reference clip?" constantly; answering from the files
    (not the record) keeps an abandoned upload from ever looking complete.
    """
    data = profile.to_dict()
    exists: dict[str, bool] = {}
    for name, rel in (
        (_ARTIFACT_REF, profile.ref_audio),
        (_ARTIFACT_CONSENT, profile.consent_audio),
        (_ARTIFACT_LOCKED, "locked.wav"),
    ):
        try:
            exists[name] = bool(rel) and artifact_path(profile.id, rel).is_file()
        except VoiceProfileError:
            exists[name] = False
    data["artifacts"] = exists
    data["history_count"] = len(profile.history)
    return data
