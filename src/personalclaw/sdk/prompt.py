"""SDK: the prompt-provider ABC + data types, and the assembled prompt's one declared boundary.

Stable re-export of ``personalclaw.prompt_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.

``USER_REQUEST_MARKER`` rides alongside them because a MODEL app can legitimately need it: an
assembled prompt is one ~11 KB string (identity, memory, session context, skills, history, then
the request), and a provider whose model cannot take that much has to be able to find where the
context ends and the request begins. Exporting core's own constant is the whole point — an app
that hard-coded the wording would silently stop matching the day core reworded it.
"""

from personalclaw.context import USER_REQUEST_MARKER  # noqa: F401
from personalclaw.prompt_providers.base import (  # noqa: F401
    PromptProvider,
    PromptRenderError,
    PromptSnippet,
    PromptTemplate,
    PromptVariable,
)

__all__ = [
    "PromptProvider",
    "PromptTemplate",
    "PromptSnippet",
    "PromptVariable",
    "PromptRenderError",
    "USER_REQUEST_MARKER",
]
