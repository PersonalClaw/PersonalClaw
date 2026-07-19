"""SDK: the prompt-provider ABC + data types.

Stable re-export of ``personalclaw.prompt_providers.base`` — an app imports these, not the core module
directly, so the core path can move without breaking installed apps.
"""

from personalclaw.prompt_providers.base import (  # noqa: F401
    PromptProvider,
    PromptTemplate,
    PromptSnippet,
    PromptVariable,
    PromptRenderError,
)

__all__ = ['PromptProvider', 'PromptTemplate', 'PromptSnippet', 'PromptVariable', 'PromptRenderError']
