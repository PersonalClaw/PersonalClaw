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

BA-3 adds the driver that consumes all three:

  * ``loop`` — ``run_browse_loop``: navigate (through BA-2's egress gate) → extract → FENCE →
    decide → act, bounded by ``max_steps``, the model budget and stuck detection, verifying
    every SUBMIT and PARKING with its notes intact when it runs out of room.
  * ``page`` — ``CdpPageDriver``: the non-navigation half of the wire (click / fill / submit /
    scroll / back / screenshot-to-a-PATH), addressing elements by BA-1's stable identity.

``action_providers.browse_provider`` is the ActionProvider that supplies the loop with a real
model and a real browser; nothing in this package knows about a provider or a gateway.
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
from personalclaw.browse.loop import (
    MAX_STEPS_DEFAULT,
    PARK_BUDGET_EXHAUSTED,
    PARK_NAVIGATION_BLOCKED,
    PARK_STEP_EXHAUSTED,
    PARK_STUCK,
    STUCK_REPEAT_LIMIT,
    BrowseLoopResult,
    BrowseStep,
    PageDriver,
    run_browse_loop,
    verify_submission,
)
from personalclaw.browse.page import CdpPageDriver, PageActionError
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
from personalclaw.browse.target import (
    BROWSE_TARGETS,
    DEFAULT_TARGET,
    TARGET_GATEWAY,
    TARGET_KEY,
    TARGET_USER_BROWSER,
    ConnectorSession,
    ConnectorStatus,
    UnknownBrowseTarget,
    clear_connector,
    connector_status,
    disconnected_skip,
    permits_unattended,
    register_connector,
    resolve_cdp_url,
    resolve_target,
    unattended_origin,
    unattended_refusal,
    unknown_target_error,
    user_browser_enabled,
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
    "run_browse_loop",
    "verify_submission",
    "BrowseLoopResult",
    "BrowseStep",
    "PageDriver",
    "MAX_STEPS_DEFAULT",
    "STUCK_REPEAT_LIMIT",
    "PARK_STEP_EXHAUSTED",
    "PARK_BUDGET_EXHAUSTED",
    "PARK_STUCK",
    "PARK_NAVIGATION_BLOCKED",
    "CdpPageDriver",
    "PageActionError",
    # BA-7 — the execution-target selector.
    "TARGET_KEY",
    "TARGET_GATEWAY",
    "TARGET_USER_BROWSER",
    "BROWSE_TARGETS",
    "DEFAULT_TARGET",
    "UnknownBrowseTarget",
    "resolve_target",
    "permits_unattended",
    "resolve_cdp_url",
    "unattended_origin",
    "ConnectorSession",
    "ConnectorStatus",
    "register_connector",
    "clear_connector",
    "connector_status",
    "user_browser_enabled",
    "unknown_target_error",
    "unattended_refusal",
    "disconnected_skip",
]
