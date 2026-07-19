"""Model catalog / management / connectivity — the provider-agnostic seam.

A model provider's *inference* path (build + resolve, ``registry.build``) is one
axis; its *catalog* — "what models can I use, can I reach it, and (for local
managers) pull/delete them" — is a separate axis the dashboard's Settings → Models
surface drives. This module defines that second axis as an interface so core stops
hardcoding per-type knowledge (ollama ``/api/tags`` vs an OpenAI ``/v1/models`` vs
Bedrock's boto3 control plane, an in-core hardcoded Anthropic list, …) in the HTTP
handlers.

Two ABCs:

* :class:`ModelCatalog` — every model provider can expose one: ``list_models`` +
  ``test_connection``. Pure function of the entry's stored config — it must NOT open
  a chat session / call ``start()`` / need a ``session_key`` (a Settings dropdown
  hitting discovery must never spin up the live provider).
* :class:`ModelManager` — the OPTIONAL management axis (search a remote catalog,
  pull/delete/show a local model). Ollama is the reference implementer (it owns
  local model download/management); a future LMStudio/other local runner can
  implement it too. Core gates the management endpoints on
  ``isinstance(cat, ModelManager)``.

Registration mirrors :func:`ProviderRegistry.register_type`: a provider registers a
catalog FACTORY for its type via :func:`ProviderRegistry.register_catalog`, invoked
as the same import-time side effect (the app loader imports the app's ``provider.py``
at enable-time). Core resolves an entry → its catalog
with :func:`ProviderRegistry.catalog_of`, fail-soft (no catalog registered → the
provider simply has no discovery, handled as an empty result, never a crash).

The catalog factory contract:

    def create_catalog(options: dict, *, model: str = "") -> ModelCatalog: ...

``options`` is the entry's stored options bag (endpoint / api_key / region /
profile / …); ``model`` is the entry's pinned model (rarely needed for listing).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """One model a provider can serve.

    ``capabilities`` uses the same string tags the Settings → Models discovery
    already speaks (``chat``, ``image_modality``, ``embedding``, ``stt``, ``tts``,
    ``image_gen``, …) so the FE and ``_infer_capabilities`` consumers are unchanged.
    ``extra`` carries provider-specific display fields the ollama UI shows
    (``owned_by``, ``parameter_size``, ``quantization``, ``family``, ``modified_at``).
    """

    id: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    size: int | None = None          # bytes — for downloadable managers (ollama)
    downloaded: bool | None = None   # None = not a downloadable/managed model
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the wire shape the model endpoints return.

        Only present (non-None / non-empty) optional fields are emitted, and
        ``extra`` is flattened onto the top level (that is where the ollama UI
        reads ``parameter_size`` / ``owned_by`` / … today), so this is a drop-in
        for the dicts the handlers built by hand.
        """
        d: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.capabilities:
            d["capabilities"] = list(self.capabilities)
        if self.description:
            d["description"] = self.description
        if self.size is not None:
            d["size"] = self.size
        if self.downloaded is not None:
            d["downloaded"] = self.downloaded
        for k, v in (self.extra or {}).items():
            d.setdefault(k, v)
        return d


@dataclass
class ConnectionResult:
    """Outcome of a provider connectivity probe (Settings → "Test connection")."""

    ok: bool
    detail: str = ""
    model_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok}
        if self.detail:
            d["detail"] = self.detail
        if self.model_count is not None:
            d["model_count"] = self.model_count
        return d


@dataclass
class PullProgress:
    """One progress frame while a :class:`ModelManager` pulls a model.

    Mirrors the NDJSON frames the ollama pull endpoint already streams
    (``{status, completed?, total?, digest?}``) so the FE progress bar is
    unchanged. ``error`` carries a terminal failure in-band.
    """

    status: str
    completed: int | None = None
    total: int | None = None
    digest: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.error:
            d["error"] = self.error
            return d
        d["status"] = self.status
        if self.completed is not None:
            d["completed"] = self.completed
        if self.total is not None:
            d["total"] = self.total
        if self.digest:
            d["digest"] = self.digest
        return d


# ── Capability inference (provider-agnostic model knowledge) ──────────────────
#
# Deriving a model's capabilities from its id is NOT provider-specific — an
# "embed" model is an embedding model whether it is served by ollama, an
# OpenAI-compatible endpoint, or a branded app. This lived in the discovery
# handler; it belongs on the shared catalog seam so every ModelCatalog
# implementation (every model app) tags models identically.

# Substring markers used to auto-tag a model's capabilities from its id. Includes the
# common ollama-library embedding families whose names don't contain "embed" (minilm,
# nomic, mxbai, snowflake-arctic-embed, paraphrase-*), so a pulled embedding model is
# classified under the Embedding use-case — not miscategorized as Chat.
_EMBEDDING_MARKERS = (
    "embed", "embedding", "bge-", "e5-", "gte-",
    "minilm", "nomic", "mxbai", "arctic-embed", "paraphrase-", "sentence-",
)
# Image *understanding* (vision/VLM) — reads images, stacks with chat.
_IMAGE_MODALITY_MARKERS = (
    "vision", "vl-", "-vl", "vlm",
    "gpt-4o", "gpt-4-turbo", "gpt-5", "gpt-4.1",
    "claude-3", "claude-4", "claude-opus", "claude-sonnet", "claude-haiku",
    "gemini", "qwen-vl", "llava", "pixtral", "internvl", "minicpm-v",
)
# Image *generation* — produces images, mutually exclusive with chat.
_IMAGE_GEN_MARKERS = (
    "dall-e", "dalle", "stable-diffusion", "sdxl", "sd3", "flux",
    "qwen-image", "wan-image", "imagen", "midjourney", "image-gen",
    "-image", "ideogram", "playground-v",
)
# Audio *generation* — produces audio/music/sfx (speech is stt/tts).
_AUDIO_GEN_MARKERS = ("musicgen", "audiogen", "audio-gen", "bark", "suno", "audiocraft", "stable-audio")
# Audio *understanding* — reads/analyzes audio (not transcription).
_AUDIO_MODALITY_MARKERS = ("audio", "voice")
# Video *generation* — produces video.
_VIDEO_GEN_MARKERS = ("video-gen", "sora", "runway", "veo", "wan2.", "kling", "pika", "ltx-video", "mochi")
# Video *understanding* — reads/analyzes video.
_VIDEO_MODALITY_MARKERS = ("video-understanding", "video-vl", "videollava", "video-llava")
_STT_MARKERS = ("whisper", "stt-", "transcribe")
_TTS_MARKERS = ("tts-", "-tts", "piper", "elevenlabs", "polly", "kokoro")

# Model-family → the provider TYPES that can serve that family. Reference data used
# ONLY as a fallback signal (e.g. "is a persisted session model compatible with the
# active provider?") when a provider hasn't told us which models it owns. Kept here,
# next to the capability markers, so all model-id classification knowledge lives in
# ONE place rather than being sniffed inline across the codebase. A branded remote
# (Groq/Together/…) speaks the openai_compatible protocol, so families served over
# that protocol include ``openai_compatible``. Absent family → no restriction.
_MODEL_FAMILY_PROVIDER_TYPES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("claude", "opus", "sonnet", "haiku"), frozenset({"anthropic", "anthropic_compatible", "bedrock"})),
    (("gpt-", "o1", "o3", "o4", "dall-e", "text-embedding-", "whisper", "tts-"),
     frozenset({"openai", "openai_compatible", "azure_openai"})),
    (("gemini",), frozenset({"google", "gemini"})),
)


def model_family_provider_types(model_id: str) -> frozenset[str]:
    """The provider TYPES that can serve ``model_id``'s family, or an empty set when
    the family is unrecognized (→ callers should not restrict on it).

    Data-driven from :data:`_MODEL_FAMILY_PROVIDER_TYPES` — no vendor name is
    hard-coded at call sites. Used by session-restore to decide whether a persisted
    model is compatible with the active provider without brand-sniffing inline."""
    mid = (model_id or "").lower()
    for markers, types in _MODEL_FAMILY_PROVIDER_TYPES:
        if any(m in mid for m in markers):
            return types
    return frozenset()


def infer_capabilities(model_id: str, families: list[str] | None = None) -> list[str]:
    """Heuristically derive capabilities from a model id + optional family hints.

    Returns at least one of: chat, embedding, stt, tts, image_modality,
    image_gen, audio_modality, audio_gen, video_modality, video_gen.
    Embedding / stt / tts / generation tags are mutually exclusive with chat
    (a model produces media OR converses). Modality (understanding) tags stack
    with chat, since a chat model can also read images / audio / video.
    """
    mid = (model_id or "").lower()
    fam = " ".join(families or []).lower()
    blob = f"{mid} {fam}"

    if any(m in blob for m in _EMBEDDING_MARKERS):
        return ["embedding"]
    if any(m in blob for m in _STT_MARKERS):
        return ["stt"]
    if any(m in blob for m in _TTS_MARKERS):
        return ["tts"]
    # Generation models are dedicated — they don't double as chat models.
    if any(m in blob for m in _VIDEO_GEN_MARKERS):
        return ["video_gen"]
    if any(m in blob for m in _IMAGE_GEN_MARKERS):
        return ["image_gen"]
    if any(m in blob for m in _AUDIO_GEN_MARKERS):
        return ["audio_gen"]

    caps: list[str] = ["chat"]
    if any(m in blob for m in _IMAGE_MODALITY_MARKERS) or "clip" in fam:
        caps.append("image_modality")
    if any(m in blob for m in _VIDEO_MODALITY_MARKERS):
        caps.append("video_modality")
    if any(m in blob for m in _AUDIO_MODALITY_MARKERS):
        caps.append("audio_modality")
    return caps


# ── Shared OpenAI-compatible protocol helper ──────────────────────────────────
#
# The GET /v1/models client is the same for every OpenAI-compatible endpoint —
# the openai app, vllm, and every branded app (together/groq/deepseek/…). It is
# generic PROTOCOL infra (not one app importing another), so it lives on the SDK
# seam and each app reuses it.

async def openai_compatible_list_models(
    endpoint: str | None, api_key: str | None, *, default_base: str = "https://api.openai.com/v1"
) -> list[ModelInfo]:
    """List models from an OpenAI-compatible ``GET {base}/v1/models`` endpoint.

    ``default_base`` lets a branded app point at its own default host while
    reusing this client. Returns ``[]`` on any failure (unreachable / non-200 /
    missing config) — discovery degrades gracefully, never raises.

    The ``GET {base}/models`` discovery call routes through the ``net.fetch`` egress
    chokepoint (host classification, redirect-hop re-check, byte cap, timeout, SEL
    audit) rather than raw aiohttp — an operator-configured ``endpoint`` is an
    egress surface, so discovery is guarded the same as every other outbound call
    (#41 class). (The inference path is the ``openai`` SDK's own client — a separate,
    deliberate boundary; this fail-soft GET is the cleanly-migratable part.)
    """
    import json as _json

    from personalclaw.sdk.net import CONNECTOR, EgressBlocked, egress_policy_for, fetch

    if not api_key and not endpoint:
        return []
    base = (endpoint or default_base).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # Layer the operator's security.egress config onto CONNECTOR so a self-hosted
    # OpenAI-compatible server on a private/LAN/loopback host (vLLM, LM Studio,
    # Ollama, …) is reachable for discovery when the operator allow-lists it.
    # Without this, a localhost/LAN endpoint is blocked as non-public even when
    # allow-listed, so model discovery silently returns [] (the picker stays empty).
    policy = egress_policy_for(CONNECTOR)
    try:
        r = await fetch(f"{base}/models", policy=policy, method="GET", headers=headers)
        if r.status != 200:
            return []
        data = _json.loads(r.text)
    except EgressBlocked:
        return []  # blocked host/redirect — discovery degrades gracefully
    except Exception:  # noqa: BLE001 — discovery is fail-soft
        return []

    out: list[ModelInfo] = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        if not model_id:
            continue
        out.append(ModelInfo(
            id=model_id,
            name=model_id,
            capabilities=infer_capabilities(model_id),
            extra={"owned_by": m.get("owned_by", "")} if m.get("owned_by") else {},
        ))
    return out


class ModelCatalog(ABC):
    """Discovery + connectivity for a model provider.

    A pure function of the provider entry's stored config — it MUST NOT open a
    chat session, call the provider's ``start()``, or require a ``session_key``.
    Discovery runs on hot Settings GETs and must be cheap + side-effect-free
    beyond the network probe it makes.
    """

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return the models this provider can serve. Empty list on any failure
        (never raise for a routine "can't reach it / not configured" — return
        ``[]`` so the dropdown degrades gracefully)."""
        raise NotImplementedError

    async def test_connection(self) -> ConnectionResult:
        """Probe connectivity. Default: derive from ``list_models`` (reachable +
        non-empty ⇒ ok). Providers with a cheaper health check override this."""
        try:
            models = await self.list_models()
        except Exception as exc:  # noqa: BLE001 — a probe never propagates
            return ConnectionResult(ok=False, detail=str(exc)[:200])
        return ConnectionResult(ok=True, model_count=len(models))


class ModelManager(ModelCatalog):
    """Optional management axis for providers that own local model lifecycle.

    Ollama is the reference implementer (pull/delete/show + remote-catalog
    search). Core gates the management HTTP endpoints on
    ``isinstance(catalog, ModelManager)`` — a provider exposing only
    :class:`ModelCatalog` returns 400 "management not supported" for these, which
    is exactly today's behavior for every non-ollama type.
    """

    @abstractmethod
    async def search_catalog(self, query: str) -> list[ModelInfo]:
        """Search the provider's *remote* installable catalog (not local models)."""
        raise NotImplementedError

    @abstractmethod
    def pull_model(self, model_id: str) -> AsyncIterator[PullProgress]:
        """Download a model, yielding progress frames. An async generator (NOT a
        coroutine): callers iterate ``async for frame in mgr.pull_model(id)``."""
        raise NotImplementedError

    @abstractmethod
    async def delete_model(self, model_id: str) -> None:
        """Delete a locally-installed model. Raises on failure."""
        raise NotImplementedError

    @abstractmethod
    async def show_model(self, model_id: str) -> ModelInfo:
        """Return rich metadata for one model (family, params, context window, …)."""
        raise NotImplementedError


__all__ = [
    "ModelInfo",
    "ConnectionResult",
    "PullProgress",
    "ModelCatalog",
    "ModelManager",
    "infer_capabilities",
    "openai_compatible_list_models",
]
