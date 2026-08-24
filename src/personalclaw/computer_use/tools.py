"""The computer-use tool surface, and the thin stdio shim that forwards it (`DCU-4`).

**Two things live here and nothing else: the DECLARATION of the seven tools, and the
transport that carries a call to the gateway.** No decision is made in this file. That is the
whole content of the plan's "thin shim" claim (§2): *"the OS driver needs the gateway's
policy, enable-state, and SEL — replicating those in a subprocess would fork the security
surface. The shim resolves session identity and forwards; it holds no OS handles."*

**Why "thin" has to be enforced, not asserted.** This module runs inside
``personalclaw mcp-core``, a separate process an ACP CLI spawns. Any screen implemented here
would be a screen the operator's *other* entry points do not get, and any screen implemented
here *as well as* in the dispatch would be two homes for one policy — the failure
``enable_state.require_enabled``'s docstring records from measurement (two readers of one
flag, and the mutation that should have reded every refusal test left them all green). So the
shim's obligations are stated as a shape a test can check:
``tests/test_computer_use_dispatch.py::test_the_shim_imports_no_driver_and_no_dispatch``
asserts by AST that this file imports neither a driver nor
:mod:`personalclaw.computer_use.service`, and
``test_the_shim_makes_no_policy_decision`` asserts it calls none of the three screens.

**Why the seven tools are always listed, even when the keystone is OFF.** Hiding them while
disabled is tempting — a disarmed machine would spend no tokens on tools that refuse — and it
is wrong twice. First, ``mcp_core._aggregated_call_tool`` routes a call by asking each
category module whether it *lists* the name: an empty list would make ``computer_click`` fall
through to core's own dispatch and answer "unknown tool", replacing `DCU-1`'s WHAT/WHY/FIX
refusal (which names the enable file, the exact bytes and the restart) with a dead end. The
keystone's whole done_when is *"every computer-use tool refuses with a WHAT/WHY/FIX message
pointing to the out-of-band enable step"*; a tool that cannot be reached cannot refuse
legibly. Second, a conditional tool population is a second code path whose disabled branch no
production caller ever exercises. So the surface is constant and the *answer* is what changes,
which is also the honest thing to show a model: the description says the capability is
operator-armed and off until then.

**The tool names are the plan's** (§2, "The tool surface"): ``computer_list_apps``,
``computer_snapshot``, ``computer_click``, ``computer_type``, ``computer_set_value``,
``computer_scroll``, ``computer_perform_action``. They are NOT defined here as Python
functions, deliberately: ``tests/test_computer_use_enable_state.py``'s keystone ratchet binds
every module-level ``computer_*`` *function* in this package to ``require_enabled()`` as its
first statement, and seven such functions here would be seven keystone readers in a process
that must not hold the decision at all. The dispatchable entry point is exactly one —
``service.computer_dispatch`` — and these are the names it accepts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The gateway route the shim forwards to. One path for every tool: the branch on *which*
#: tool belongs to the dispatch, and a per-tool route would be seven places to forget a check.
DISPATCH_PATH = "/api/computer-use/dispatch"


@dataclass(frozen=True)
class ToolSpec:
    """One computer-use tool, and which chain steps the dispatch owes it.

    The three ``screen_*`` flags are **declarations the dispatch reads**, not documentation:
    :func:`personalclaw.computer_use.service.computer_dispatch` branches on them, and
    ``test_every_acting_tool_declares_the_app_screen`` /
    ``test_only_text_writing_tools_declare_the_input_target_screen`` pin the sets so a tool
    added later cannot quietly opt out of a screen. A flag is therefore the *only* way to be
    exempt, and setting one to ``False`` reds a test that names the tool.
    """

    name: str
    description: str
    #: JSON-schema ``properties`` for the MCP tool definition.
    parameters: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    #: Step 2 — ``policy.check_app`` must pass for this tool's target application.
    screen_app: bool = True
    #: Step 3 — this tool acts on an element from a prior snapshot, so the index must be
    #: proved fresh (TTL) and re-walked (fingerprint) before the element is touched.
    screen_index: bool = True
    #: Step 4 — this tool writes text into a destination, so ``policy.check_input_target``
    #: must pass. Scoped to text writers because that is exactly what the plan scopes it to:
    #: *"Secure/password fields and sensitive text screened (check_input_target) before any
    #: type/set-value."*
    screen_input_target: bool = False
    #: True when the tool changes the desktop. Reads (list/snapshot) are still keystoned,
    #: screened and audited; this flag exists so the ordering rail can assert that every
    #: screen precedes the ACTING driver call.
    acts: bool = True


_ELEMENT_PARAMS: dict[str, Any] = {
    "snapshot_id": {
        "type": "string",
        "description": "The id returned by the computer_snapshot call that found the element.",
    },
    "element_index": {
        "type": "integer",
        "description": "Zero-based index of the element within that snapshot.",
    },
}

#: The seven tools, in the order §2 lists them.
TOOL_SURFACE: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="computer_list_apps",
        description=(
            "List the desktop applications this machine will let you drive. Requires the "
            "operator to have armed desktop computer use out-of-band; refuses with the exact "
            "enable step otherwise. Reports how many running applications were withheld "
            "because the operator did not name them."
        ),
        # No app argument exists to screen, and there is no element. Both exemptions are
        # declared rather than implied, and the result is still narrowed to the allowlist by
        # the dispatch's step 7 — listing every window an operator has open would disclose
        # more than the allowlist grants.
        screen_app=False,
        screen_index=False,
        acts=False,
    ),
    ToolSpec(
        name="computer_snapshot",
        description=(
            "Walk one application's front window into an indexed accessibility tree. Returns "
            "a snapshot id plus numbered elements; act on an element by its index, never by "
            "screen coordinates. The index expires, so re-snapshot rather than reusing an old "
            "id after the user has touched the app."
        ),
        parameters={
            "app": {
                "type": "string",
                "description": "Exact application name, as computer_list_apps spells it.",
            }
        },
        required=("app",),
        screen_index=False,
        acts=False,
    ),
    ToolSpec(
        name="computer_click",
        description=(
            "Activate an element by index. The default performs an accessibility press, which "
            "moves no pointer at all. The coordinate methods must be named explicitly and are "
            "audited separately; 'auto' never resolves onto them."
        ),
        parameters={
            **_ELEMENT_PARAMS,
            "click_method": {
                "type": "string",
                "enum": ["auto", "located", "global"],
                "description": (
                    "auto (default): accessibility press on the element, no pointer motion. "
                    "located: post a click to the target process at x,y without moving the "
                    "real cursor. global: warp the operator's real cursor and click — the only "
                    "method that touches their physical pointer, so it must be asked for by "
                    "name."
                ),
            },
            "x": {"type": "number", "description": "Coordinate methods only."},
            "y": {"type": "number", "description": "Coordinate methods only."},
            "app": {"type": "string", "description": "Coordinate methods only: target app."},
        },
    ),
    ToolSpec(
        name="computer_type",
        description=(
            "Type text into the element at this index. Refuses secure/password destinations, "
            "fields whose label names a secret, and fields already holding credential-shaped "
            "text — a refusal you cannot talk it out of."
        ),
        parameters={
            **_ELEMENT_PARAMS,
            "text": {"type": "string", "description": "The text to type."},
        },
        required=("snapshot_id", "element_index", "text"),
        screen_input_target=True,
    ),
    ToolSpec(
        name="computer_set_value",
        description=(
            "Set the element's value directly (faster and more reliable than typing for long "
            "text). Screened for secure/password destinations exactly like computer_type."
        ),
        parameters={
            **_ELEMENT_PARAMS,
            "value": {"type": "string", "description": "The value to set."},
        },
        required=("snapshot_id", "element_index", "value"),
        screen_input_target=True,
    ),
    ToolSpec(
        name="computer_scroll",
        description="Scroll the element at this index.",
        parameters={
            **_ELEMENT_PARAMS,
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": "Lines to scroll (default 3)."},
        },
        required=("snapshot_id", "element_index", "direction"),
    ),
    ToolSpec(
        name="computer_perform_action",
        description=(
            "Perform a named accessibility action the element advertises (for controls a press "
            "does not cover). The action must be one the snapshot listed for that element."
        ),
        parameters={
            **_ELEMENT_PARAMS,
            "action": {
                "type": "string",
                "description": "An action name from the element's own 'actions' list.",
            },
        },
        required=("snapshot_id", "element_index", "action"),
    ),
)

#: Name → spec, the lookup both this module and the dispatch use.
TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SURFACE}

#: The tool names, for callers that only need membership.
TOOL_NAMES: frozenset[str] = frozenset(TOOLS_BY_NAME)


def _list_tools() -> list[dict[str, Any]]:
    """The MCP tool definitions — the same shape every ``mcp_*`` category module returns.

    Constant, and constant on purpose: see the module docstring on why an OFF keystone must
    not empty this list. ``required`` defaults to the element pair for the acting tools, which
    is what makes "act by index" the only reachable shape for them.
    """
    out: list[dict[str, Any]] = []
    for spec in TOOL_SURFACE:
        required = list(spec.required) or (
            ["snapshot_id", "element_index"] if spec.screen_index else []
        )
        out.append(
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": {
                    "type": "object",
                    "properties": dict(spec.parameters),
                    "required": required,
                },
            }
        )
    return out


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    """Forward one call to the gateway and render the answer. NO decision happens here.

    ``mcp_core._post`` is the transport, and it is what "resolves session identity" means in
    §2: it attaches the internal secret and the resolved ``X-Session-Key`` so the gateway can
    attribute the attempt. Reusing it rather than minting an identity path here is the point —
    a second identity resolver in the one process that must hold no authority is exactly the
    kind of thing that later grows an exemption.

    A transport failure is reported as text, never raised: an MCP tool result is the only
    channel the model has, and an exception here would surface as a framework error the model
    cannot act on. The wording says the call did **not** reach the desktop, because the one
    thing a computer-use failure must never read as is "the click landed".
    """
    from personalclaw.mcp_core import _post

    response = _post(DISPATCH_PATH, {"tool": name, "params": args})
    if not isinstance(response, dict):
        return f"Error: computer use returned {type(response).__name__}, not an object."
    if response.get("error"):
        # Both envelopes land here: the gateway's ``{"error": {...}}`` refusal body and
        # ``_post``'s own ``{"error": "<transport message>"}``. Rendering the refusal's
        # WHAT/WHY/FIX verbatim is deliberate — the dispatch composed those three lines for a
        # model to act on, and re-wording them here would give one refusal two voices.
        return _render_error(response["error"])
    result = response.get("result")
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, default=str)


def _render_error(error: Any) -> str:
    """One refusal, rendered the way the dispatch composed it.

    The code surfaced is ``agent_code`` (the ``ERR_UPPER_SNAKE`` :class:`AgentError` code), NOT
    the wire ``code``. The two vocabularies are deliberately disjoint — see
    :mod:`personalclaw.http_errors` — and this string goes into a model's context, which is the
    surface ``AgentError`` codes exist for. Surfacing the wire code here would tell a model to
    branch on ``computer_use_refused``, a value that cannot distinguish "your app is not
    allowlisted" from "you named no valid tool"; ``agent_code`` can, and every sibling refusal
    in this package already carries it. Falls back to the wire code only when the gateway
    answered with no agent code at all (a transport-level failure), so the line is never blank.
    """
    if isinstance(error, dict):
        lines = [str(error.get(key, "")).strip() for key in ("what", "why", "fix")]
        body = "\n".join(line for line in lines if line)
        if body:
            code = str(error.get("agent_code") or error.get("code") or "")
            return f"{body}\n\n(code: {code})" if code else body
        return str(error.get("message") or error)
    return f"Error: {error}"


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    """Category entry point, wrapped in the shared logging/validation seam."""
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        lambda _name, args: args,
        _call_tool_inner,
        session_key="mcp_computer_use",
        downstream_service="personalclaw-computer-use",
    )
