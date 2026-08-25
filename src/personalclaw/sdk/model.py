"""SDK: the model (LLM) provider ABC + the generic LLM infrastructure a model app
builds on.

Stable re-exports of the provider-agnostic LLM machinery — the ``ModelProvider`` ABC
+ event/stream types, the capability descriptors, the provider registry (so an app
registers its type + capability factory), the credential type, the streaming-tag
splitter, the context-window lookup, and ``OpenAIProvider`` (the canonical OpenAI-wire
client that any OpenAI-COMPATIBLE endpoint app — vllm, together, groq, … — subclasses).

An app imports these, not core internals, so core can evolve underneath it. This is
generic infra: openai/anthropic/bedrock/vllm are all implementations built on it (they
ship pre-installed for a working out-of-box system, but are architecturally identical
to any installed model app). Ollama is the one model provider that stays core-native
(it owns model download/management), so it is not built on this surface.
"""

from personalclaw.llm.anthropic import AnthropicProvider  # noqa: F401
from personalclaw.llm.base import (  # noqa: F401
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    CancelOutcome,
    LLMEvent,
    ModelProvider,
)
from personalclaw.llm.branded_specs import BrandedProviderSpec  # noqa: F401
from personalclaw.llm.capabilities import Capability, ProviderCapability  # noqa: F401
from personalclaw.llm.catalog import (  # noqa: F401
    ConnectionResult,
    ModelCatalog,
    ModelInfo,
    ModelManager,
    PullProgress,
    infer_capabilities,
    openai_compatible_list_models,
)
from personalclaw.llm.credentials import Credential  # noqa: F401

# The two supported inference-PROTOCOL clients — the standards PersonalClaw speaks,
# not provider-specific. A model-provider app declares which protocol it speaks +
# how it authenticates/configures: an OpenAI-compatible endpoint app (openai, vllm,
# lmstudio, together, …) builds on OpenAIProvider; an Anthropic-compatible one builds
# on AnthropicProvider. (A provider with a distinct wire, e.g. Bedrock's Converse API,
# owns its own client in its app.)
from personalclaw.llm.openai import OpenAIProvider  # noqa: F401

# ``CACHE_HINT_KEY`` rides alongside ``PromptCache`` because an app whose provider owns
# its OWN wire (Bedrock's Converse API) must READ the neutral marker to translate it —
# core never learns that vendor's cache syntax. Core's own tests/test_apps_import_boundary.py
# names this the prescribed fix: "If a symbol isn't on the SDK yet, the fix is to PROMOTE it
# to a personalclaw.sdk submodule instead of reaching around the boundary."
from personalclaw.llm.prompt_cache import CACHE_HINT_KEY, PromptCache  # noqa: F401
from personalclaw.llm.registry import (  # noqa: F401
    CredentialMissing,
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
)
from personalclaw.llm.stream_tags import KIND_OUTSIDE, make_think_splitter  # noqa: F401

# Media-model catalog contribution: the OpenAI-compatible audio/image PROTOCOL
# clients are core, but WHICH concrete models a vendor serves (OpenAI's whisper-1/
# gpt-image-1/dall-e-*) is vendor data the provider's app contributes here, keyed by
# provider type. See personalclaw.media_catalogs.
from personalclaw.media_catalogs import (  # noqa: F401
    MediaCatalog,
    MediaModel,
    register_media_catalog,
)
from personalclaw.model_windows import model_context_window  # noqa: F401

# Media-capability config scanners — the app-owned extension point a model app
# calls at import to contribute per-capability adapters (image/video/stt/embedding)
# for its provider WITHOUT core knowing the vendor. See personalclaw.providers.media_scanners.
from personalclaw.providers.media_scanners import register_scanner  # noqa: F401

# ``register_branded_app`` is the one name here that a SIBLING SDK module owns rather than
# core. Re-exported so an app uses the single stable ``personalclaw.sdk.model`` path. This is
# a plain top-of-file import and MUST stay one: ``sdk.provider_helpers`` imports its core
# machinery straight from ``personalclaw.llm.*``, so the edge runs one way only. Reversing
# that (importing ``sdk.model`` from ``provider_helpers``) reinstates a module-scope cycle in
# which whichever module is imported first from a cold interpreter decides whether the SDK
# loads at all. tests/test_sdk_import_cycle.py drives both orders and fails if it comes back.
from personalclaw.sdk.provider_helpers import register_branded_app  # noqa: F401

__all__ = [
    "ModelProvider",
    "LLMEvent",
    "CancelOutcome",
    "EVENT_COMPLETE",
    "EVENT_TEXT_CHUNK",
    "EVENT_THINKING_CHUNK",
    "EVENT_TOOL_CALL",
    "Capability",
    "ProviderCapability",
    "PromptCache",
    "CACHE_HINT_KEY",
    "Credential",
    "get_default_registry",
    "ProviderEntry",
    "ProviderResolutionError",
    "CredentialMissing",
    "KIND_OUTSIDE",
    "make_think_splitter",
    "model_context_window",
    "OpenAIProvider",
    "AnthropicProvider",
    # Catalog / management / connectivity axis (Settings → Models discovery).
    "ModelCatalog",
    "ModelManager",
    "ModelInfo",
    "ConnectionResult",
    "PullProgress",
    "infer_capabilities",
    "openai_compatible_list_models",
    # Branded/generic protocol-provider app helpers (see sdk.provider_helpers).
    "BrandedProviderSpec",
    "register_branded_app",
    # Media-model catalog contribution (stt/tts/image vendor catalogs).
    "MediaCatalog",
    "MediaModel",
    "register_media_catalog",
    # Media-capability config scanner registration (app-owned extension point).
    "register_scanner",
]
