"""Capability descriptors for provider types.

This module is loaded as a side effect of importing ``personalclaw.llm``
and MUST NOT import any provider SDKs (``anthropic``, ``openai``,
``httpx``). Property 11 (Provider SDK Lazy Import) depends on this guarantee.
"""

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    """Static capability flags advertised by a provider type."""

    CHAT = "chat"
    CODE_TOOLS = "code_tools"
    SUMMARIZATION = "summarization"
    PLANNING = "planning"
    EMBEDDING = "embedding"
    VISION = "vision"
    STREAMING = "streaming"
    TOOL_APPROVAL = "tool_approval"


class StructuredOutput(str, Enum):
    """Graded native structured-output support (AUTONOMY-GUARDRAILS §2.4).

    A GRADED capability, not a boolean flag — so it rides on
    :class:`ProviderCapability` as its own field rather than joining the
    :class:`Capability` flag set. (``regex`` / ``cfg`` are reserved for a future
    local-logits path.)

    * ``NONE`` — no native enforcement; the guard parses with a targeted retry.
    * ``JSON_MODE`` — the provider can be asked to emit syntactically valid JSON
      (OpenAI-wire ``response_format={"type": "json_object"}``), but not to a
      specific schema.
    * ``JSON_SCHEMA`` — the provider enforces a supplied JSON Schema natively
      (ollama ``format=<schema>``; OpenAI-wire ``response_format`` json_schema).
    """

    NONE = "none"
    JSON_MODE = "json_mode"
    JSON_SCHEMA = "json_schema"


@dataclass(frozen=True)
class ProviderCapability:
    """Static descriptor of what a provider type can do."""

    type: str  # "openai", "anthropic", ...
    capabilities: frozenset[Capability]
    supports_streaming: bool
    supports_tools: bool
    supports_embeddings: bool
    supports_vision: bool
    max_context_tokens: int  # 0 == unknown / model-dependent
    notes: str = ""
    # Graded native structured-output support. Defaults to NONE so a provider that
    # doesn't declare it gets the universal parse-with-targeted-retry path — the
    # correct, safe behavior for every provider until it opts into native
    # enforcement. Branded/ollama apps that support it declare it via
    # ``BrandedProviderSpec.structured_output`` (a coordinated apps-repo change).
    structured_output: StructuredOutput = StructuredOutput.NONE
