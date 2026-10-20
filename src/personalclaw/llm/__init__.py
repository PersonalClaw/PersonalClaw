"""LLM provider abstraction — pluggable provider layer.

Core holds the generic infra (the ModelProvider ABC + the two supported inference
PROTOCOL clients, ``openai`` + ``anthropic``, exposed via ``personalclaw.sdk.model``).
The model *providers* (openai/anthropic/vllm/bedrock/ollama/…) are ALL apps that register
via the app loader when enabled — none are eager-imported here. Ollama is no exception:
its full implementation (a bespoke wire client for Ollama's ``/api/*`` dialect plus a
local-model pull/delete catalog) lives in the ``ollama-models`` app's own provider module
and imports core contracts only through ``personalclaw.sdk.model``. It loads like every
other model app via its manifest ``implementation`` entry-point when the app is enabled.

``ollama-models`` is BUNDLED (it ships inside the wheel under the native-app tree and
auto-installs at first boot) while the others are Store installs — but that is a shipping
decision, not an architectural one. A bundled app reaches core through exactly the same
SDK boundary and the same typed handler; nothing here knows it is bundled. Only
``acp_agent`` (the ACP agent-runtime type, not a model provider) registers on import here.
"""

# Importing acp_agent registers the ACP agent-runtime type with the default registry. It
# guarantees no heavy SDK is pulled into ``sys.modules`` as a side effect (Property 11).
# Model provider types (openai/anthropic/vllm/bedrock/ollama) self-register when the app
# loader imports each app's implementation module — NOT here.
from personalclaw.llm import acp_agent as _acp_agent  # noqa: F401
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.llm.base import LLMEvent, ModelProvider
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.credentials import Credential, CredentialStore
from personalclaw.llm.registry import (
    CredentialMissing,
    ProviderEntry,
    ProviderFactory,
    ProviderRegistry,
    ProviderResolutionError,
    get_default_registry,
    reset_default_registry,
    set_default_registry,
)

__all__ = [
    "AcpAgentProvider",
    "Capability",
    "Credential",
    "CredentialMissing",
    "CredentialStore",
    "LLMEvent",
    "ModelProvider",
    "ProviderCapability",
    "ProviderEntry",
    "ProviderFactory",
    "ProviderRegistry",
    "ProviderResolutionError",
    "get_default_registry",
    "reset_default_registry",
    "set_default_registry",
]
