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

import contextlib
import contextvars
import hashlib
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
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
    size: int | None = None  # bytes — for downloadable managers (ollama)
    downloaded: bool | None = None  # None = not a downloadable/managed model
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
    """Outcome of a provider connectivity probe (Settings → "Test connection").

    ``rejected_credential`` is True when the endpoint answered but refused the stored key
    (HTTP 401/403) — the one failure whose fix is the key, not the endpoint. Core stops
    offering such an instance's models and stops re-sending the key until it changes.
    """

    ok: bool
    detail: str = ""
    model_count: int | None = None
    rejected_credential: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok}
        if self.detail:
            d["detail"] = self.detail
        if self.model_count is not None:
            d["model_count"] = self.model_count
        if self.rejected_credential:
            d["rejected_credential"] = True
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
    "embed",
    "embedding",
    "bge-",
    "e5-",
    "gte-",
    "minilm",
    "nomic",
    "mxbai",
    "arctic-embed",
    "paraphrase-",
    "sentence-",
)
# Image *understanding* (vision/VLM) — reads images, stacks with chat.
_IMAGE_MODALITY_MARKERS = (
    "vision",
    "vl-",
    "-vl",
    "vlm",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-5",
    "gpt-4.1",
    "claude-3",
    "claude-4",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "gemini",
    "qwen-vl",
    "llava",
    "pixtral",
    "internvl",
    "minicpm-v",
)
# Image *generation* — produces images, mutually exclusive with chat.
_IMAGE_GEN_MARKERS = (
    "dall-e",
    "dalle",
    "stable-diffusion",
    "sdxl",
    "sd3",
    "flux",
    "qwen-image",
    "wan-image",
    "imagen",
    "midjourney",
    "image-gen",
    "-image",
    "ideogram",
    "playground-v",
)
# Audio *generation* — produces audio/music/sfx (speech is stt/tts).
_AUDIO_GEN_MARKERS = (
    "musicgen",
    "audiogen",
    "audio-gen",
    "bark",
    "suno",
    "audiocraft",
    "stable-audio",
)
# Audio *understanding* — reads/analyzes audio (not transcription).
_AUDIO_MODALITY_MARKERS = ("audio", "voice")
# Video *generation* — produces video.
_VIDEO_GEN_MARKERS = (
    "video-gen",
    "sora",
    "runway",
    "veo",
    "wan2.",
    "kling",
    "pika",
    "ltx-video",
    "mochi",
)
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
#
# This table is the FLOOR, not the whole answer: it can only name the provider types core
# ships, so a branded/subscription provider APP that serves the family (a Claude-subscription
# app registering its own type, say) would be judged unable to serve a ``claude-*`` model and
# a persisted session model would be silently swapped out from under the user. The installed
# apps are asked too — see :func:`model_family_provider_types`.
_MODEL_FAMILY_PROVIDER_TYPES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (
        ("claude", "opus", "sonnet", "haiku"),
        frozenset({"anthropic", "anthropic_compatible", "bedrock"}),
    ),
    (
        ("gpt-", "o1", "o3", "o4", "dall-e", "text-embedding-", "whisper", "tts-"),
        frozenset({"openai", "openai_compatible", "azure_openai"}),
    ),
    (("gemini",), frozenset({"google", "gemini"})),
)


def model_family_provider_types(model_id: str) -> frozenset[str]:
    """The provider TYPES that can serve ``model_id``'s family, or an empty set when
    the family is unrecognized (→ callers should not restrict on it).

    Data-driven from :data:`_MODEL_FAMILY_PROVIDER_TYPES` — no vendor name is
    hard-coded at call sites. Used by session-restore to decide whether a persisted
    model is compatible with the active provider without brand-sniffing inline.

    The core table is UNIONED with the installed provider apps that declare a model of this
    family themselves (``spec.default_model`` / ``spec.fallback_models``), so a branded or
    subscription app is recognized without being added here by hand — the hand-maintained row
    is exactly what missed the first subscription app. The union only ever ADDS types, so no
    existing type's verdict changes: every caller treats membership as permission, and an
    unrecognized family already means "no restriction"."""
    mid = (model_id or "").lower()
    for markers, types in _MODEL_FAMILY_PROVIDER_TYPES:
        if any(m in mid for m in markers):
            return types | _app_declared_types(markers)
    return frozenset()


def _app_declared_types(markers: tuple[str, ...]) -> frozenset[str]:
    """Installed provider-app TYPES that declare a model matching ``markers``.

    Imported lazily, in the established order (same two lines as ``routing/rates.py``'s app
    pricing lookup): ``sdk.model`` and ``sdk.provider_helpers`` are a circular pair that
    resolves only when ``sdk.model`` goes first, and the spec registry is populated at app
    IMPORT time, so the answer is only meaningful at call time anyway. An install with no
    branded app registered returns an empty set and the core table stands alone — which is
    also what a failed lookup must do, since a family check runs on session restore and may
    never be the thing that breaks it."""
    try:
        import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
        from personalclaw.llm.branded_specs import spec_types_declaring_models

        return spec_types_declaring_models(markers)
    except Exception:  # noqa: BLE001 — a family lookup must never break a restore
        logger.debug("app model-family lookup failed", exc_info=True)
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


class ModelDiscoveryError(RuntimeError):
    """A ``GET {base}/models`` attempt that produced no model list, and why.

    Exists because ``[]`` was the answer to two different questions: "this endpoint
    serves no models" and "I never got a list out of it" (blocked, unreachable, 401,
    404, non-JSON). Those need different actions from the user, so they must not be the
    same value. The message says what to do next — no traceback, no response body echoed
    (an error page can contain a reflected credential), and never the API key.

    ``url`` is the URL actually requested (the single most useful fact when a base URL is
    wrong) and ``status`` the HTTP status when one was received.
    """

    def __init__(self, message: str, *, url: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status = status

    @property
    def rejected_credential(self) -> bool:
        """The endpoint answered and refused the key (401/403), as opposed to not answering."""
        return self.status in (401, 403)


#: Failures a fail-soft discovery swallowed, collected for whoever asked (see
#: :func:`capture_discovery_failures`). ``None`` outside a capture.
_SWALLOWED: contextvars.ContextVar[list[ModelDiscoveryError] | None] = contextvars.ContextVar(
    "personalclaw_swallowed_discovery_failures", default=None
)


@contextlib.contextmanager
def capture_discovery_failures() -> Iterator[list[ModelDiscoveryError]]:
    """Collect every discovery failure a fail-soft path swallows inside this block.

    A catalog that lists through :func:`openai_compatible_list_models` gets ``[]`` for a
    401, and its ``test_connection`` then reported "No models returned (check key/endpoint)"
    for a key the vendor had plainly rejected. The caller that relays a result to the user
    wraps the call in this and reads what actually happened — the resolver's own sentence —
    without every app having to change how it lists.
    """
    sink: list[ModelDiscoveryError] = []
    token = _SWALLOWED.set(sink)
    try:
        yield sink
    finally:
        _SWALLOWED.reset(token)


def record_swallowed_discovery_failure(exc: ModelDiscoveryError) -> None:
    """Hand a failure a fail-soft path is about to swallow to the active capture, if any."""
    sink = _SWALLOWED.get()
    if sink is not None:
        sink.append(exc)


#: (models URL, key digest) pairs whose rejection has already been logged at WARNING. The
#: same rejected key used to log once per page load — 51 identical warnings in one log.
_REJECTION_WARNED: set[tuple[str, str]] = set()


def _key_digest(api_key: str | None) -> str:
    return hashlib.sha256((api_key or "").encode()).hexdigest()[:16]


#: The most of a failure's own sentence a connection result carries. Long enough for the longest
#: one core composes — an egress refusal names the URL, the guard's reason and BOTH ways to allow
#: the host (~330 characters), and cutting it at 300 dropped exactly the instruction.
FAILURE_DETAIL_CHARS = 600


#: A path segment that is an API VERSION (``v1``, ``v4``, ``v1beta``, ``v1alpha1``) — as
#: opposed to an ordinary segment that merely starts with a "v" (``vllm``, ``voice``).
_VERSION_SEGMENT = re.compile(r"^v\d+[a-z0-9]*$", re.IGNORECASE)


def openai_compatible_models_url(
    endpoint: str | None, *, default_base: str = "https://api.openai.com/v1"
) -> str:
    """The ``/models`` URL to GET for an OpenAI-compatible ``endpoint``.

    ``/v1`` is appended only when the base carries NO version segment at all, which is
    the property the old ``if not base.endswith("/v1")`` was protecting: a user who
    types the bare host (``https://api.openai.com``) still reaches ``/v1/models``.

    What ``endswith`` got wrong (#955) is every OpenAI-compatible base whose version is
    spelled anything other than a trailing ``/v1``. A base of the form
    ``https://api.vendor.example/api/coding/paas/v4`` became ``…/paas/v4/v1/models`` → 404 →
    zero models discovered, silently, for an endpoint serving a textbook OpenAI model list.
    Gemini's OpenAI shim (``…/v1beta/openai``) has the same shape with the version not even
    last, so the test is "does any segment name a version", not "does the last one".
    (Reserved ``.example`` host by rule — ``tests/test_network_egress_hosts.py`` reads a
    routable hostname in shipped source as a DESTINATION, even in a docstring.)
    """
    base = (endpoint or default_base).rstrip("/")
    segments = base.split("://", 1)[-1].split("/")[1:]
    if not any(_VERSION_SEGMENT.match(s) for s in segments):
        base += "/v1"
    return f"{base}/models"


async def openai_compatible_discover_models(
    endpoint: str | None, api_key: str | None, *, default_base: str = "https://api.openai.com/v1"
) -> list[ModelInfo]:
    """List models from an OpenAI-compatible ``GET {base}/models`` endpoint, STRICTLY.

    Returns the models the endpoint advertised — including ``[]`` when it advertised an
    empty list, which is a real and honest answer. Raises :class:`ModelDiscoveryError`
    when no list was obtained at all, so a caller can tell the two apart; use
    :func:`openai_compatible_list_models` for the fail-soft view.

    The ``GET {base}/models`` discovery call routes through the ``net.fetch`` egress
    chokepoint (host classification, redirect-hop re-check, byte cap, timeout, SEL
    audit) rather than raw aiohttp — an operator-configured ``endpoint`` is an
    egress surface, so discovery is guarded the same as every other outbound call
    (#41 class). (The inference path is the ``openai`` SDK's own client — a separate,
    deliberate boundary; this GET is the cleanly-migratable part.)
    """
    import json as _json

    from personalclaw.sdk.net import CONNECTOR, EgressBlocked, egress_policy_for, fetch

    if not api_key and not endpoint:
        raise ModelDiscoveryError(
            "No endpoint or API key configured for this provider — set its endpoint "
            "(and key, if the endpoint needs one) in Settings → Providers."
        )
    url = openai_compatible_models_url(endpoint, default_base=default_base)
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
        r = await fetch(url, policy=policy, method="GET", headers=headers)
    except EgressBlocked as exc:
        raise ModelDiscoveryError(
            f"Egress policy blocked {url} ({exc}) — allow-list the host under "
            f"security.egress.allow_hosts, or set security.egress.allow_private for a "
            f"LAN/localhost endpoint.",
            url=url,
        ) from exc
    except Exception as exc:  # noqa: BLE001 — every transport failure, named not swallowed
        from personalclaw.providers.failure_copy import connectivity_guidance

        # The classified sentence when there is one: it names the cause (refused, timed out,
        # unresolvable) and, for a refused localhost inside the container image, the host
        # address to use instead — the one refusal whose fix is a hostname, not a server.
        raise ModelDiscoveryError(
            connectivity_guidance(exc, endpoint=url)
            or f"Could not reach {url} ({type(exc).__name__}) — check the endpoint host is "
            f"correct and reachable from this machine.",
            url=url,
        ) from exc
    if r.status in (401, 403):
        raise ModelDiscoveryError(
            f"{url} rejected the credential (HTTP {r.status}) — re-enter this provider's "
            f"API key.",
            url=url,
            status=r.status,
        )
    if r.status == 404:
        raise ModelDiscoveryError(
            f"No model list at {url} (HTTP 404) — set this provider's endpoint to the same "
            f"base your chat completions use (the base, not the /chat/completions path).",
            url=url,
            status=r.status,
        )
    if r.status != 200:
        raise ModelDiscoveryError(
            f"{url} answered HTTP {r.status} — check the endpoint and try again.",
            url=url,
            status=r.status,
        )
    try:
        data = _json.loads(r.text)
    except Exception as exc:  # noqa: BLE001 — a non-JSON 200 is a wrong-URL symptom
        raise ModelDiscoveryError(
            f"{url} answered HTTP 200 but the body is not JSON — check this provider's "
            f"endpoint points at the API base, not at a web page.",
            url=url,
            status=r.status,
        ) from exc
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ModelDiscoveryError(
            f"{url} answered HTTP 200 but not with an OpenAI-shaped model list "
            f'(expected {{"object":"list","data":[…]}}) — check this provider\'s endpoint.',
            url=url,
            status=r.status,
        )

    out: list[ModelInfo] = []
    for m in rows:
        model_id = m.get("id", "") if isinstance(m, dict) else ""
        if not model_id:
            continue
        out.append(
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id),
                extra={"owned_by": m.get("owned_by", "")} if m.get("owned_by") else {},
            )
        )
    if rows and not out:
        raise ModelDiscoveryError(
            f"{url} listed {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}, none of them "
            f'carrying an "id" — check this provider\'s endpoint speaks the OpenAI models '
            f"protocol.",
            url=url,
            status=r.status,
        )
    return out


async def openai_compatible_list_models(
    endpoint: str | None, api_key: str | None, *, default_base: str = "https://api.openai.com/v1"
) -> list[ModelInfo]:
    """The fail-soft view of :func:`openai_compatible_discover_models`: ``[]`` on any
    failure (unreachable / non-200 / unparseable / missing config), never raises.

    ``default_base`` lets a branded app point at its own default host while reusing this
    client. The failure is LOGGED at WARNING rather than dropped: a provider that
    contributes nothing to the model pool was previously indistinguishable from one that
    was never asked, at every log level (#955). A caller that needs to tell "no models"
    from "no answer" apart must call the strict function.
    """
    try:
        return await openai_compatible_discover_models(endpoint, api_key, default_base=default_base)
    except ModelDiscoveryError as exc:
        record_swallowed_discovery_failure(exc)
        seen = (exc.url, _key_digest(api_key))
        if exc.rejected_credential and seen in _REJECTION_WARNED:
            logger.debug("Model discovery found nothing (already reported): %s", exc)
        else:
            if exc.rejected_credential:
                _REJECTION_WARNED.add(seen)
            logger.warning("Model discovery found nothing: %s", exc)
        return []


class ModelCatalog(ABC):
    """Discovery + connectivity for a model provider.

    A pure function of the provider entry's stored config — it MUST NOT open a
    chat session, call the provider's ``start()``, or require a ``session_key``.
    Discovery runs on hot Settings GETs and must be cheap + side-effect-free
    beyond the network probe it makes.
    """

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return the models this provider can serve.

        ``[]`` means "asked, and it serves none" — a real answer. An implementation that
        could not obtain a list at all, and has nothing to degrade to, raises instead
        (:class:`ModelDiscoveryError` for the OpenAI-compatible wire): every caller of
        this method already relays a raised failure to the user, and "0 models" is not
        the same answer as "I could not reach the endpoint" (#955). An implementation
        with a curated fallback still degrades to it — with the failure logged."""
        raise NotImplementedError

    async def test_connection(self) -> ConnectionResult:
        """Probe connectivity. Default: derive from ``list_models`` (reachable +
        non-empty ⇒ ok). Providers with a cheaper health check override this."""
        try:
            models = await self.list_models()
        except ModelDiscoveryError as exc:
            return ConnectionResult(
                ok=False,
                detail=str(exc)[:FAILURE_DETAIL_CHARS],
                rejected_credential=exc.rejected_credential,
            )
        except Exception as exc:  # noqa: BLE001 — a probe never propagates
            from personalclaw.providers.failure_copy import relayed_failure_copy

            # The user-facing sentence, never the exception's own text: that belongs in the log.
            logger.debug("connection test raised", exc_info=True)
            return ConnectionResult(ok=False, detail=relayed_failure_copy(exc))
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
    "FAILURE_DETAIL_CHARS",
    "ModelInfo",
    "ConnectionResult",
    "PullProgress",
    "ModelCatalog",
    "ModelDiscoveryError",
    "ModelManager",
    "capture_discovery_failures",
    "infer_capabilities",
    "openai_compatible_discover_models",
    "openai_compatible_list_models",
    "openai_compatible_models_url",
]
