"""Per-surface voice bindings + the precedence chain (MULTIMODAL-IO §3).

One JSON store, ``<home>/voice_bindings.json``, sibling of ``active_models.json``::

    { "default": "vp-a1b2c3d4",
      "channel:slack": "vp-9f8e7d6c",
      "agent:research-agent": "vp-a1b2c3d4",
      "client:some-client": "vp-..." }

Resolution is the plan's four-level chain, and the order is the whole point:

1. **explicit** — a ``profile_id`` the caller passed ("speak as X") always wins;
2. **binding** — the surface key (``channel:<transport>`` / ``agent:<slug>`` /
   ``client:<id>``, the last reserved now so a future inbound audio API plugs in
   without a store migration);
3. **default** — the ``default`` key here;
4. **built-in** — no profile at all: :func:`personalclaw.tts.registry.active_voice_params`
   keeps its pre-profile flat resolution, which is why an empty store reproduces
   today's output exactly.

Levels 1-3 live here; level 4 is the resolver's fallback, not a binding.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.voice.profiles import VoiceProfile, VoiceProfileError, get_profile, validate_id

logger = logging.getLogger(__name__)

DEFAULT_KEY = "default"

#: Surface namespaces. Anything else is refused rather than silently stored, so the
#: key space stays a contract instead of a free-text dictionary.
SURFACE_NAMESPACES = ("channel", "agent", "client")

_SURFACE_RE = re.compile(r"^(channel|agent|client):[A-Za-z0-9_.\-]{1,64}$")

#: Precedence levels, returned by :func:`resolve_profile_id` so a caller can say
#: *why* a voice was chosen.
LEVEL_EXPLICIT = "explicit"
LEVEL_BINDING = "binding"
LEVEL_DEFAULT = "default"
LEVEL_BUILTIN = "built-in"


def bindings_path() -> Path:
    return config_dir() / "voice_bindings.json"


def validate_surface(surface: str) -> str:
    """Return the surface key, or raise. ``default`` is a legal key here too."""
    key = str(surface or "").strip()
    if key == DEFAULT_KEY:
        return key
    if not _SURFACE_RE.match(key):
        raise VoiceProfileError(
            f"surface must be 'default' or <{'|'.join(SURFACE_NAMESPACES)}>:<name>",
            400,
            "invalid_surface",
        )
    return key


def load_bindings() -> dict[str, str]:
    """The binding map. Unreadable/garbage entries are dropped, never fatal."""
    path = bindings_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("voice_bindings.json unreadable; treating as empty")
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value:
            continue
        try:
            validate_surface(str(key))
            validate_id(value)
        except VoiceProfileError:
            logger.warning("dropping malformed voice binding %r", key)
            continue
        out[str(key)] = value
    return out


def save_bindings(bindings: dict[str, str]) -> None:
    atomic_write(bindings_path(), json.dumps(bindings, indent=2), mode=0o600)


def set_binding(surface: str, profile_id: str) -> dict[str, str]:
    """Bind a surface (or ``default``) to an existing profile."""
    key = validate_surface(surface)
    pid = validate_id(profile_id)
    if get_profile(pid) is None:
        raise VoiceProfileError(f"no such voice profile: {pid}", 404, "not_found")
    bindings = load_bindings()
    bindings[key] = pid
    save_bindings(bindings)
    return bindings


def clear_binding(surface: str) -> dict[str, str]:
    key = validate_surface(surface)
    bindings = load_bindings()
    bindings.pop(key, None)
    save_bindings(bindings)
    return bindings


def forget_profile(profile_id: str) -> dict[str, str]:
    """Drop every binding pointing at a deleted profile (no dangling references)."""
    bindings = load_bindings()
    remaining = {k: v for k, v in bindings.items() if v != profile_id}
    if remaining != bindings:
        save_bindings(remaining)
    return remaining


def resolve_profile_id(*, surface: str = "", explicit: str = "") -> tuple[str, str]:
    """Walk levels 1-3 and return ``(profile_id, level)``.

    Returns ``("", LEVEL_BUILTIN)`` when nothing resolves — the signal for the
    resolver to keep today's flat behavior. A binding pointing at a profile that no
    longer exists is treated as absent so a stale key degrades instead of erroring.
    """
    if explicit:
        pid = validate_id(explicit)
        if get_profile(pid) is None:
            raise VoiceProfileError(f"no such voice profile: {pid}", 404, "not_found")
        return pid, LEVEL_EXPLICIT

    bindings = load_bindings()
    key = str(surface or "").strip()
    if key and key != DEFAULT_KEY:
        bound = bindings.get(key, "")
        if bound and get_profile(bound) is not None:
            return bound, LEVEL_BINDING
    fallback = bindings.get(DEFAULT_KEY, "")
    if fallback and get_profile(fallback) is not None:
        return fallback, LEVEL_DEFAULT
    return "", LEVEL_BUILTIN


def binding_warning(profile: VoiceProfile, surface: str) -> str:
    """Non-blocking consent warning for an agentic/off-machine binding (§1.3).

    Binding a *cloned* voice to a channel or subagent is the agentic case the plan
    says should warn when consent is unverified — plain local synthesis is never
    gated, so this returns a reason string for the UI rather than refusing.
    """
    if profile.kind != "clone" or profile.verified_own_voice:
        return ""
    if str(surface or "").split(":", 1)[0] in ("channel", "agent", "client"):
        return "unverified_clone_consent"
    return ""
