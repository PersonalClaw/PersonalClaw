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

from personalclaw.llm.base import (  # noqa: F401
    CancelOutcome,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
    ModelProvider,
)
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
from personalclaw.llm.registry import (  # noqa: F401
    CredentialMissing,
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
)
from personalclaw.llm.stream_tags import KIND_OUTSIDE, make_think_splitter  # noqa: F401
from personalclaw.model_windows import model_context_window  # noqa: F401

# The two supported inference-PROTOCOL clients — the standards PersonalClaw speaks,
# not provider-specific. A model-provider app declares which protocol it speaks +
# how it authenticates/configures: an OpenAI-compatible endpoint app (openai, vllm,
# lmstudio, together, …) builds on OpenAIProvider; an Anthropic-compatible one builds
# on AnthropicProvider. (A provider with a distinct wire, e.g. Bedrock's Converse API,
# owns its own client in its app.)
from personalclaw.llm.openai import OpenAIProvider  # noqa: F401
from personalclaw.llm.anthropic import AnthropicProvider  # noqa: F401

# Media-model catalog contribution: the OpenAI-compatible audio/image PROTOCOL
# clients are core, but WHICH concrete models a vendor serves (OpenAI's whisper-1/
# gpt-image-1/dall-e-*) is vendor data the provider's app contributes here, keyed by
# provider type. See personalclaw.media_catalogs.
from personalclaw.media_catalogs import (  # noqa: F401
    MediaCatalog,
    MediaModel,
    register_media_catalog,
)

__all__ = [
    "ModelProvider", "LLMEvent", "CancelOutcome",
    "EVENT_COMPLETE", "EVENT_TEXT_CHUNK", "EVENT_THINKING_CHUNK", "EVENT_TOOL_CALL",
    "Capability", "ProviderCapability",
    "Credential",
    "get_default_registry", "ProviderEntry", "ProviderResolutionError", "CredentialMissing",
    "KIND_OUTSIDE", "make_think_splitter",
    "model_context_window",
    "OpenAIProvider", "AnthropicProvider",
    # Catalog / management / connectivity axis (Settings → Models discovery).
    "ModelCatalog", "ModelManager", "ModelInfo", "ConnectionResult", "PullProgress",
    "infer_capabilities", "openai_compatible_list_models",
    # Branded/generic protocol-provider app helpers (see sdk.provider_helpers).
    "BrandedProviderSpec", "register_branded_app",
    # Media-model catalog contribution (stt/tts/image vendor catalogs).
    "MediaCatalog", "MediaModel", "register_media_catalog",
]

# Imported LAST (after the names above are defined) — provider_helpers imports from
# this module, so a top-of-file import would be circular. Re-exported so an app uses
# the single stable `personalclaw.sdk.model` path.
from personalclaw.sdk.provider_helpers import (  # noqa: E402,F401
    BrandedProviderSpec,
    register_branded_app,
)
