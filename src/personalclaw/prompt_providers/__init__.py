"""Prompt providers — pluggable storage + retrieval backends for prompts.

Each provider implements `PromptProvider` and is registered with the local
registry. The bundled `native-prompts` extension registers the native
filesystem-backed provider at startup.
"""

from personalclaw.prompt_providers.base import (
    ALLOWED_PROMPT_KINDS,
    ALLOWED_VARIABLE_TYPES,
    PromptProvider,
    PromptRenderError,
    PromptSnippet,
    PromptTemplate,
    PromptVariable,
    normalize_variable_type,
)
from personalclaw.prompt_providers.engine import (
    BUILT_IN_FUNCTIONS,
    extract_inline_variables,
    included_snippet_names,
    merged_variables,
    parse_type_decl,
    render,
    render_snippet,
    render_template,
)
from personalclaw.prompt_providers.registry import (
    get_default_provider,
    get_prompt_provider,
    list_prompt_providers,
    register_prompt_provider,
)

__all__ = [
    "PromptProvider",
    "PromptTemplate",
    "PromptSnippet",
    "PromptVariable",
    "PromptRenderError",
    "normalize_variable_type",
    "ALLOWED_VARIABLE_TYPES",
    "ALLOWED_PROMPT_KINDS",
    "render",
    "render_template",
    "render_snippet",
    "merged_variables",
    "included_snippet_names",
    "extract_inline_variables",
    "parse_type_decl",
    "BUILT_IN_FUNCTIONS",
    "register_prompt_provider",
    "get_prompt_provider",
    "list_prompt_providers",
    "get_default_provider",
]
