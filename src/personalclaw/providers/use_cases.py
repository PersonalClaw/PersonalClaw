"""Model use-case truth store — the single source of which model serves a capability.

There is ONE store: ``~/.personalclaw/active_models.json``. It maps a use case to
the active model reference(s), each ``"<provider_name>:<model_id>"`` where
``provider_name`` is a configured provider (config.json ``providers[]`` /
``default_registry``) or a bundled in-process provider. Every consumer — chat,
embedding, and (via the typed registries) stt/tts — reads selections from here, so
the Settings → Models picker and what the runtime resolves never disagree.

Use cases are two-grain:

* **Capabilities** (top grain) — ``chat``, ``embedding``, ``stt``, ``tts`` and the
  modality / generation kinds. A capability always has its own active model.
* **Chat sub-categories** — ``reasoning``, ``code_tools``: finer roles *within*
  chat. A user MAY pin a distinct model to one; when they don't, resolution falls
  back to the parent capability's model (:func:`parent_capability`). They are never
  their own capability — they borrow chat's pool.

Per-use-case *behavior* settings (provider-agnostic — e.g. auto-speak for tts,
language for stt) live separately in
``~/.personalclaw/extensions/use_case_settings/{use_case}.json``.
"""

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


# ── Canonical use-case vocabulary ────────────────────────────────────────────
# Capabilities are split into modality (understanding an input) vs generation
# (producing that media) for image / audio / video, so a model that *reads*
# images is selected separately from one that *creates* them.
#   image_modality  — understands images (a.k.a. vision)
#   image_gen       — generates images
#   audio_modality  — understands audio
#   audio_gen       — generates audio (music / sfx; speech is stt/tts)
#   video_modality  — understands video
#   video_gen       — generates video
CAPABILITIES: tuple[str, ...] = (
    "chat",
    "embedding",
    "stt",
    "tts",
    # Diarization ("who spoke when") is its OWN parent capability with NO fallback
    # (core L1): if no diarization model is bound, the feature is simply off — exactly
    # like STT with no model. Served by the separate diarization provider app.
    "diarization",
    "image_modality",
    "image_gen",
    "audio_modality",
    "audio_gen",
    "video_modality",
    "video_gen",
)
# NOTE: the knowledge-ingestion pipeline (OCR / vision / video-classify / consolidation)
# does NOT have its own use-cases. Each ingestion node resolves DIRECTLY to the relevant
# default capability binding (image-understanding → ``image_modality``, reasoning →
# ``chat``, transcription → ``stt``). There is intentionally no per-role ingestion
# *override* — it added a settings row with no real control and was never wired to a
# distinct model, so it was removed (the ingestion model is simply the default).

# Finer roles within the ``chat`` capability. Each falls back to ``chat`` when
# the user has not pinned a distinct model (see :func:`parent_capability`).
# Only roles with a real runtime consumer live here (the removal doctrine:
# ``summarization`` / ``planning`` were selectable routing targets with NO
# resolver — a pinned model was silently ignored — so they were removed. The
# Capability.SUMMARIZATION / .PLANNING enum flags remain as provider capability
# *advertisements* that installed apps declare; they are not use-cases.)
# Consumers, per axis (MODEL-USE-CASES-V2):
#   code_tools    — the native agent runtime (provider_bridge)
#   reasoning     — explicit one-shot judgment calls (web-extract, guarded one-shots)
#                   and, by default, every LOOP JUDGE (loops.judge_use_case) — the
#                   judge must not ride the binding of the work it grades (WF2LOO-17)
#   background    — the _bg/personalclaw-lite session factory + one_shot_completion's
#                   informal-label collapse (titles, tags, suggestions, digests,
#                   consolidation)
#   orchestration — orchestrated-chat supervising turns + model-less subagent spawns
#   loops         — loop WORKER sessions (long-horizon). NOT the judges/gates that
#                   grade them: those ride `loops.judge_use_case` (WF2LOO-17).
CHAT_SUBCATEGORIES: tuple[str, ...] = (
    "code_tools",
    "reasoning",
    "background",
    "orchestration",
    "loops",
)

# Every selectable use case = capabilities + chat sub-categories.
USE_CASES: tuple[str, ...] = CAPABILITIES + CHAT_SUBCATEGORIES
VALID_USE_CASES = frozenset(USE_CASES)

# Use cases whose bound models feed PICKER POOLS (the app-wide model dropdowns
# offer every entry). Historically this set also gated ">1 entry allowed" on the
# PUT — that meaning is retired: EVERY use case now stores an ordered fallback
# CHAIN (position 0 = default, 1..n = fallbacks; MODEL-USE-CASES-V2), so the
# constant's only remaining meaning is dropdown-pool membership.
MULTI_ACTIVE_USE_CASES = frozenset({"chat", "image_modality"})


def parent_capability(use_case: str) -> str:
    """Return the capability a use case resolves under.

    A chat sub-category (``reasoning`` / ``code_tools``) resolves under ``chat``; every
    other capability is its own parent. (Knowledge-ingestion roles are no longer their own
    use-cases — the pipeline nodes point straight at ``image_modality`` / ``chat`` /
    ``stt``, so there is nothing to fall back FROM here.)
    """
    if use_case in CHAT_SUBCATEGORIES:
        return "chat"
    return use_case


# ── Active-model store (active_models.json) ──────────────────────────────────


def active_models_path() -> Path:
    """The single active-model store. Public because the ES-7 model-upgrade watchdog
    watches this exact file for changes, and a second module deriving the path itself is
    how two "the same" paths drift apart."""
    from personalclaw.config.loader import config_dir

    return config_dir() / "active_models.json"


# Provider names that contribute models without a config.json entry (the
# in-process bundled providers). Refs prefixed with these must never be pruned
# as "from a removed provider".
_BUNDLED_PROVIDER_NAMES = frozenset(
    {"sentence-transformers", "sentence_transformers", "native", "faster-whisper", "piper-tts"}
)


def _dynamic_media_provider_names() -> set[str]:
    """Provider names from the typed media registries (image-gen today).

    Image-gen providers register lazily (the OpenAI-Images adapter keyed by config
    name — already covered by config providers — plus bespoke bundles like ``fal``
    that have NO config.json entry). Their refs (``fal:<model>``) must not be
    pruned as "from a removed provider". Import-guarded + best-effort so a registry
    hiccup never discards valid selections. (STT/TTS bundled names are static in
    ``_BUNDLED_PROVIDER_NAMES``; only image-gen has dynamic bespoke bundles.)
    """
    names: set[str] = set()
    try:
        from personalclaw.image_gen import registry as ig

        ig._ensure_registered()
        names.update(p.name for p in ig.list_providers())
    except Exception:  # noqa: BLE001 — never let registry trouble prune valid refs
        pass
    try:
        from personalclaw.video_gen import registry as vg

        names.update(p.name for p in vg.list_providers())
    except Exception:  # noqa: BLE001
        pass
    # Also include image_gen/video_gen providers declared by an installed model-type
    # bundle (FAL via its app manifest), even if its instance isn't currently registered
    # (e.g. disabled, or the typed registry was cleared) — its selection ref must
    # survive pruning so re-enabling it restores the binding. The instance name is
    # the provider's own (e.g. "fal"), resolved from the bundle factory.
    try:
        from personalclaw.providers.registry import get_provider_registry

        for ext in get_provider_registry().list_by_type("model"):
            caps = ext.provider_config.capabilities
            if "image_gen" in caps or "video_gen" in caps:
                inst = getattr(ext, "provider_instance", None)
                names.add(getattr(inst, "name", "") or ext.name)
    except Exception:  # noqa: BLE001
        pass
    # Every LOCAL downloadable provider (faster-whisper, piper, sentence-transformers,
    # the diarization backends, ollama, …) contributes bindable models under its
    # registry key — the single source, so a new local provider is known automatically
    # (no hardcoded name list). Its ``provider:model`` refs must survive pruning.
    try:
        from personalclaw.local_models.registry import _key_for as _local_key
        from personalclaw.local_models.registry import list_providers as _local_list

        for p in _local_list():
            names.add(_local_key(p))
    except Exception:  # noqa: BLE001
        pass
    return names


def _known_provider_names() -> set[str] | None:
    """Names of providers that currently contribute models.

    The union of config.json provider names, the static bundled in-process
    providers, and the dynamically-registered media providers (image-gen bundles
    like ``fal``). Returns ``None`` when ``config.json`` cannot be read (distinct
    from an empty-but-readable config), so the caller can skip pruning rather
    than discard valid selections on a transient I/O error.
    """
    from personalclaw.config.loader import config_path

    bundled = set(_BUNDLED_PROVIDER_NAMES) | _dynamic_media_provider_names()
    path = config_path()
    if not path.is_file():
        # No config yet — only the bundled + dynamic media providers exist.
        return bundled
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    # A MISSING `providers` key is not an unreadable config — a fresh install simply
    # has no configured providers yet, and the answer there is "only the bundled +
    # dynamic ones exist", exactly like the no-config-file case above. Returning None
    # here conflated "I can't tell" with "there are none", and every caller treats
    # None as skip-validation — so on a fresh home an unknown-provider ref was stored
    # unchallenged instead of being rejected at bind time.
    if "providers" not in data:
        return bundled
    providers = data.get("providers")
    if not isinstance(providers, list):
        return None
    names = {str(p.get("name", "")) for p in providers if isinstance(p, dict) and p.get("name")}
    return names | bundled


def _prune_removed_providers(active: dict[str, list[str]]) -> dict[str, list[str]]:
    """Drop active-model refs whose provider is no longer configured.

    Active selections are stored as ``"<provider_name>:<model_id>"``. When a
    provider is removed in Settings, its refs would otherwise linger here and
    surface as ghost models in the Settings count, the app-wide model
    dropdowns, and routing. A ref with no ``:`` prefix is left untouched
    (provider-agnostic); a ref whose provider is unknown is dropped. If the
    configured set can't be determined, nothing is pruned.
    """
    known = _known_provider_names()
    if known is None:
        return active
    pruned: dict[str, list[str]] = {}
    for use_case, refs in active.items():
        if not isinstance(refs, list):
            pruned[use_case] = refs
            continue
        kept = [r for r in refs if ":" not in str(r) or str(r).split(":", 1)[0] in known]
        pruned[use_case] = kept
    return pruned


def load_active_models() -> dict[str, list[str]]:
    """Active-model selections per use-case, with refs from removed providers
    pruned so no consumer surfaces a ghost model.

    Tolerant chain read (MODEL-USE-CASES-V2, clean break under the pre-1.0
    banner): every value is semantically an ordered fallback CHAIN. A bare
    string value from an older store normalizes to a one-entry chain; writes
    (:func:`save_active_models`) emit lists only.
    """
    path = active_models_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for uc, refs in data.items():
        if isinstance(refs, str):
            normalized[uc] = [refs]
        else:
            normalized[uc] = refs
    return _prune_removed_providers(normalized)


def save_active_models(active: dict[str, list[str]]) -> None:
    path = active_models_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(active, indent=2) + "\n")


def active_model_refs(use_case: str) -> list[str]:
    """Active model ref(s) for ``use_case``, applying the sub-category fallback.

    A chat sub-category with no model of its own borrows the parent ``chat``
    selection (:func:`parent_capability`). Returns ``[]`` when nothing is active.
    The list is the use case's ordered fallback CHAIN (position 0 = default).
    """
    active = load_active_models()
    refs = active.get(use_case)
    if not refs and use_case in CHAT_SUBCATEGORIES:
        refs = active.get("chat")
    return list(refs) if isinstance(refs, list) else []


def resolution_chain(use_case: str, *, session_override: str = "") -> list[str]:
    """Ordered candidate refs for one resolution: ``[override?] + chain``.

    The composer/session override sits ONE LEVEL ABOVE the chain (owner-decided
    semantics, MODEL-USE-CASES-V2): it is a single ref — never itself a chain —
    prepended to the use case's chain (or, for an unbound sub-category, the
    parent ``chat`` chain via :func:`active_model_refs`). Duplicates dedupe with
    the override keeping the front position. Resolution walks the result in
    order: override fails → chain default → fallback 1 → …
    """
    chain = active_model_refs(use_case)
    if not session_override:
        return chain
    out = [session_override]
    out.extend(r for r in chain if r != session_override)
    return out


def split_ref(ref: str) -> tuple[str, str] | None:
    """Parse a ``"provider_name:model_id"`` ref. Returns None if unqualified.

    Splits on the FIRST colon only, so model ids that contain colons (e.g.
    ``"gpt-oss:20b"``) survive intact.
    """
    if ":" not in ref:
        return None
    provider_name, model_id = ref.split(":", 1)
    return (provider_name, model_id)


# ── Per-use-case behavior settings (provider-agnostic) ───────────────────────


def _settings_dir() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "extensions" / "use_case_settings"


def load_use_case_settings(use_case: str) -> dict[str, Any]:
    """Load provider-agnostic settings for a use case (e.g. auto-speak for tts).

    The closed-set check mirrors :func:`save_use_case_settings`, which has always had
    it. Load having none was the create-vs-update asymmetry that turns an id into a
    filesystem address (#455's class): an unknown use case is a caller bug, and a use
    case containing a separator is a path. Returning ``{}`` rather than raising keeps
    every existing caller's contract — they all treat "no settings" as the default.
    """
    if use_case not in VALID_USE_CASES:
        return {}
    path = _settings_dir() / f"{use_case}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_use_case_settings(use_case: str, settings: dict[str, Any]) -> None:
    """Save provider-agnostic settings for a use case."""
    if use_case not in VALID_USE_CASES:
        raise ValueError(f"Invalid use case: {use_case!r}")
    _settings_dir().mkdir(parents=True, exist_ok=True)
    path = _settings_dir() / f"{use_case}.json"
    atomic_write(path, json.dumps(settings, indent=2) + "\n")


# ── One-shot migration off the legacy binding store ──────────────────────────


def _legacy_bindings_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "extensions" / "use_cases.json"


def migrate_legacy_bindings() -> bool:
    """Fold a legacy ``use_cases.json`` binding store into ``active_models.json``.

    Earlier builds stored *which model serves a use case* as an
    ``extension:instance`` binding in ``use_cases.json``, separate from the
    model-level ``active_models.json``. There is now one store. This one-shot,
    run at startup, fills any use case that has no active selection yet with the
    bound provider's configured model (``provider:model``), then deletes the
    legacy file so only one store remains. Returns True if a migration ran.

    Best-effort: a binding names an ``extension``/provider but no model id, so we
    use the provider's configured ``model`` from config.json. Bindings we can't
    map (no resolvable model) are skipped — the user re-selects in Settings.
    """
    path = _legacy_bindings_path()
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = None
    if isinstance(raw, dict) and raw:
        active = load_active_models()
        config_models = _config_provider_models()
        changed = False
        for use_case, binding in raw.items():
            if use_case not in VALID_USE_CASES or not isinstance(binding, dict):
                continue
            if active.get(use_case):  # never clobber an existing selection
                continue
            provider_name = str(binding.get("extension") or "").strip()
            model_id = config_models.get(provider_name)
            if not provider_name or not model_id:
                continue
            active[use_case] = [f"{provider_name}:{model_id}"]
            changed = True
        if changed:
            save_active_models(active)
    # Remove the legacy file unconditionally — the store is now active_models.json.
    try:
        path.unlink()
    except OSError:
        logger.debug("Could not remove legacy use_cases.json", exc_info=True)
    logger.info("Migrated legacy use_cases.json into active_models.json")
    return True


def _config_provider_models() -> dict[str, str]:
    """Map ``provider_name -> configured model`` from config.json ``providers[]``."""
    from personalclaw.config.loader import config_path

    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    providers = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(providers, list):
        return {}
    out: dict[str, str] = {}
    for p in providers:
        if isinstance(p, dict) and p.get("name") and p.get("model"):
            out[str(p["name"])] = str(p["model"])
    return out


# Config-provider types whose hosted audio API speaks the OpenAI dialect
# (transcriptions + speech). The remote STT/TTS adapters register one instance
# per configured provider of these types, keyed by the provider's config name.
OPENAI_FAMILY_TYPES: tuple[str, ...] = (
    "openai",
    "openai_compatible",
    "together",
    "groq",
    "deepseek",
    "mistral",
    "azure_openai",
    "google",
)


def openai_family_providers() -> list[dict[str, str]]:
    """Configured OpenAI-family providers as ``[{name, type, endpoint, api_key}]``.

    The remote STT/TTS/image adapters build from this so a single OpenAI-compatible
    provider configured in Settings serves every capability it offers — chat,
    embedding, transcription, speech, images — from the same endpoint + credential.
    ``type`` lets the adapter look up the vendor's contributed media catalog
    (personalclaw.media_catalogs) instead of host-sniffing.
    """
    from personalclaw.config.loader import config_path

    path = config_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    providers = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(providers, list):
        return []
    out: list[dict[str, str]] = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", ""))
        ptype = str(p.get("type", ""))
        if not name or ptype not in OPENAI_FAMILY_TYPES:
            continue
        opts = p.get("options") or {}
        out.append(
            {
                "name": name,
                "type": ptype,
                "endpoint": str(opts.get("endpoint", "") or ""),
                "api_key": str(opts.get("api_key", "") or ""),
            }
        )
    return out
