"""Autonomous browse perception layer — token-frugal STRUCTURAL page reading (BA-1).

Two standalone, pure modules that turn HTML into a compact, ref-addressable representation an
agent can act on, plus the sentinel action language it emits back:

  * ``extraction`` — walk the DOM into text (≤4000 chars) + a Links DSL + a Forms DSL, with a
    STABLE ``ElementRef`` per interactive element (survives a re-snapshot after unrelated DOM
    mutation — the load-bearing property for "CLICK <ref>" meaning the same thing next step).
  * ``compress`` — fold the extraction into a ``PageOutline`` under a token budget (<1K tokens
    for a 100K-token DOM), with the screenshot referenced by PATH, never inlined as base64.
  * ``sentinels`` — ``parse_sentinel`` turns an LLM's text (``CLICK <ref>``, ``TYPE <ref>(v)``,
    …) into typed ``Action`` objects that round-trip back to their sentinel line.

No network, no gateway, no config — the browse LOOP that drives these (CDP, egress, the
ActionProvider) lands in later BA atoms.
"""

from personalclaw.browse.compress import (
    DEFAULT_MAX_TOKENS,
    PageOutline,
    assert_no_base64,
    compress_page,
)
from personalclaw.browse.extraction import (
    ElementRef,
    FormRepr,
    PageExtraction,
    extract_page,
    render_forms_dsl,
    render_links_dsl,
)
from personalclaw.browse.sentinels import (
    Action,
    ClickAction,
    DoneAction,
    GoBackAction,
    NavigateAction,
    NotesAction,
    ScrollAction,
    SubmitAction,
    TypeAction,
    WaitAction,
    parse_sentinel,
)

__all__ = [
    "ElementRef",
    "FormRepr",
    "PageExtraction",
    "extract_page",
    "render_links_dsl",
    "render_forms_dsl",
    "PageOutline",
    "compress_page",
    "assert_no_base64",
    "DEFAULT_MAX_TOKENS",
    "Action",
    "NavigateAction",
    "ClickAction",
    "TypeAction",
    "SubmitAction",
    "ScrollAction",
    "WaitAction",
    "GoBackAction",
    "DoneAction",
    "NotesAction",
    "parse_sentinel",
]
