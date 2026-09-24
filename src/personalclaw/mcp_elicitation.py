"""Server→client elicitation — an MCP server asks the USER, through the confirmation
boundary PersonalClaw already owns (``MCP-BIDIRECTIONAL-REQUESTS`` ``MBR-1``).

The MCP spec's ``elicitation/create`` lets a *server* interrupt its own tool call to ask
the human a question. Every peer with an MCP client at all (dify, open-webui, LibreChat)
constructs its ``ClientSession`` with no callbacks, so all three advertise nothing and
answer "not supported". The reason this is cheap for us and expensive for them is that we
already ship a real confirmation boundary with a frontend — so this module is a ROUTE into
:meth:`personalclaw.dashboard.state.DashboardState.request_approval`, not a new approval UI.

**Default deny, per server.** The grant is ``security.mcp_elicitation_servers``, an
allowlist of server names that ships empty. It is consulted at ONE place, at session
construction (``mcp_client.McpServerConn._run``):

* not granted → :func:`elicitation_callback_for` returns ``None``, the SDK keeps its own
  ``_default_elicitation_callback``, and ``ClientSession.initialize`` therefore sends
  ``ClientCapabilities(elicitation=None)``. The capability is **absent from the wire**, so
  a conformant server never asks; one that asks anyway gets the SDK's typed
  ``ErrorData(-32600, "Elicitation not supported")`` back immediately. That refusal is
  the SDK's and is deliberately NOT reimplemented here — a hand-rolled refusing callback
  would be a non-default callback, which is exactly what makes the SDK ADVERTISE the
  capability we are trying to withhold.
* granted → the callback below is passed, the capability is advertised for that server
  only, and a request becomes an approval card.

**Bounded by the window the answer can be delivered in.** ``mcp_client.call_tool`` abandons a
tool call after ``_CALL_TIMEOUT_SECS``, while the approval boundary's interactive window is two
hours (``DashboardState._APPROVAL_TIMEOUT``; ``mcp:<server>`` matches none of its unattended
markers). Waiting the boundary's window behind the transport's would discard the user's answer
in silence — the call is already abandoned, the card is still up, and the click delivers
nothing. So a granted question is bounded by :func:`approval_window_secs`, derived from the
call ceiling rather than configured, and an unanswered one is withdrawn and answered ``cancel``
while the server is still listening.

**Why the answer is a confirmation and not a form.** The boundary we reuse yields one bit:
the user allowed it or did not. That bit can truthfully fill a confirmation-shaped form
(no properties, or boolean properties) and nothing else. A schema asking for a *string*
cannot be answered from a yes — filling it with ``""`` or a schema default would be
inventing an answer the user never gave, which is the failure this module exists to
prevent. Such a form is refused with a typed protocol error instead, so the server learns
it must ask a different way rather than receiving a fabricated reply.

**Non-goal.** The INBOUND direction — PersonalClaw's own MCP *server* surface issuing
elicitations — stays out of scope; :mod:`personalclaw.inbound.mcp_http` says so in writing
and that note stands.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Type-check-time only. The `mcp` SDK is an OPTIONAL extra, so importing its protocol
    # at runtime would make this module — and `mcp_client`, which imports it — fail to load
    # on an install without the extra. `from __future__ import annotations` keeps the
    # annotation a string, so nothing here is evaluated at import time.
    from mcp.client.session import ElicitationFnT

logger = logging.getLogger(__name__)

__all__ = [
    "APPROVAL_TOOL_PREFIX",
    "FORM_UNSUPPORTED_MESSAGE",
    "URL_MODE_UNSUPPORTED_MESSAGE",
    "approval_window_secs",
    "confirmation_content",
    "elicitation_callback_for",
    "elicitation_granted",
]

#: Head-room reserved out of the tool-call ceiling for delivering the answer: the
#: `ElicitResult` still has to be serialised back and the server still has to finish the
#: tool call it asked from. An elicitation allowed to consume the whole ceiling would time
#: the call out at the instant it was answered.
_ANSWER_DELIVERY_MARGIN_SECS = 15.0

#: The ``tool`` name the approval record carries, suffixed with the server name. This is
#: what ``web/src/pages/chat/ApprovalCard.tsx`` renders as the subject of the card and
#: what makes a granted question attributable to the server that asked it.
APPROVAL_TOOL_PREFIX = "mcp_elicitation:"

#: Refusal for a form this boundary cannot answer truthfully (see the module docstring).
FORM_UNSUPPORTED_MESSAGE = (
    "PersonalClaw answers elicitation/create through its user-confirmation boundary, "
    "which can supply a confirmation (an empty or all-boolean requestedSchema) only. "
    "Ask for a confirmation, or collect this value through a tool argument instead."
)

#: Refusal for URL-mode elicitation. The SDK advertises `form` and `url` together (it
#: exposes no way to advertise one), and url mode exists for "sensitive out-of-band
#: interactions like OAuth flows, credential collection, or payment processing" — i.e. it
#: asks us to send the user to a third party's URL. Sending a user somewhere to type a
#: credential is not a confirmation, so it is refused rather than improvised.
URL_MODE_UNSUPPORTED_MESSAGE = (
    "PersonalClaw supports form-mode elicitation only. URL-mode elicitation would send "
    "the user to a server-supplied URL for out-of-band credential entry, which this "
    "client does not do."
)


def elicitation_granted(server: str) -> bool:
    """Whether the user has granted ``server`` the right to ask them questions.

    Reads ``security.mcp_elicitation_servers``. Any failure to read config answers
    ``False`` — the fail-closed direction for a consent check, and the direction that
    keeps a corrupt ``config.json`` from silently granting a third party the boundary.
    """
    name = (server or "").strip()
    if not name:
        return False
    try:
        from personalclaw.config.loader import AppConfig

        return name in set(AppConfig.load().security.mcp_elicitation_servers)
    except Exception:
        logger.debug("MCP elicitation grant lookup failed for %r", server, exc_info=True)
        return False


def approval_window_secs() -> float:
    """How long a granted question may hold the user's attention.

    Derived from the tool-call ceiling and deliberately NOT a config field: the constraint
    is structural rather than a preference. The answer is only worth collecting while the
    call that asked for it is still alive, so a knob permitted to exceed that ceiling would
    restore the silent discard this bound exists to prevent. Read lazily because
    ``mcp_client`` imports this module.
    """
    from personalclaw.mcp_client import _CALL_TIMEOUT_SECS

    return max(5.0, float(_CALL_TIMEOUT_SECS) - _ANSWER_DELIVERY_MARGIN_SECS)


def confirmation_content(schema: Any) -> dict[str, Any] | None:
    """The form content a YES on the confirmation boundary can truthfully supply.

    Returns the content dict, or ``None`` when the form asks for something a yes/no
    cannot answer (see the module docstring). An absent/empty ``properties`` is a bare
    confirmation and yields ``{}``; every declared property must be typed ``boolean``,
    and each is filled with ``True`` because the user's answer WAS yes.
    """
    if not isinstance(schema, dict):
        return None
    props = schema.get("properties")
    if props is None or props == {}:
        return {}
    if not isinstance(props, dict):
        return None
    out: dict[str, Any] = {}
    for key, sub in props.items():
        if not isinstance(sub, dict) or sub.get("type") != "boolean":
            return None
        out[str(key)] = True
    return out


def elicitation_callback_for(server: str) -> "ElicitationFnT | None":
    """The SDK ``elicitation_callback`` for ``server``, or ``None`` when not granted.

    ``None`` is the load-bearing return: passing it to ``ClientSession`` leaves the SDK's
    own default in place, which is what keeps ``elicitation`` off the advertised
    capabilities for that server (and only that server).
    """
    if not elicitation_granted(server):
        return None

    async def _elicit(context: Any, params: Any) -> Any:
        return await _handle_elicitation(server, params)

    return _elicit


async def _handle_elicitation(server: str, params: Any) -> Any:
    """Route one granted ``elicitation/create`` to the user and map their answer back.

    Returns an SDK ``ElicitResult`` or ``ErrorData``; the caller (the SDK) serialises
    whichever it gets. Never raises — an exception out of a request handler would leave
    the server waiting on a response that is never sent, which is the hang this whole
    seam is built to avoid.
    """
    from mcp import types as mcp_types

    try:
        message = str(getattr(params, "message", "") or "")
        # `url` mode is refused before anything else, and before the user is troubled.
        if str(getattr(params, "mode", "form") or "form") != "form":
            logger.info("MCP server %r asked for url-mode elicitation; refused", server)
            return mcp_types.ErrorData(
                code=mcp_types.INVALID_REQUEST, message=URL_MODE_UNSUPPORTED_MESSAGE
            )

        schema = getattr(params, "requestedSchema", None)
        content = confirmation_content(schema)
        if content is None:
            # Refused BEFORE asking: a question whose answer we could not deliver is a
            # question not worth spending the user's attention on.
            logger.info("MCP server %r asked for a non-confirmation form; refused", server)
            return mcp_types.ErrorData(
                code=mcp_types.INVALID_REQUEST, message=FORM_UNSUPPORTED_MESSAGE
            )

        state = _live_state()
        if state is None:
            # No gateway → nobody to ask. `cancel` and not `decline`: the spec's
            # `decline` means the user explicitly refused, and claiming a refusal from a
            # user who was never asked would misreport their intent. `cancel` is
            # "dismissed without an explicit choice", which is exactly what happened.
            logger.info("MCP server %r asked a question with no UI to ask; cancelled", server)
            return mcp_types.ElicitResult(action="cancel")

        approval_id = f"mcp-elicit-{uuid.uuid4().hex[:16]}"
        # The server's message travels as `tool_purpose` so it inherits the existing
        # redaction on that field (`redact_exfiltration_urls` + `redact_credentials`).
        # This text is written by a third party, so passing it through the sanitiser the
        # approval store already applies is the point, not a side effect.
        try:
            approved = await asyncio.wait_for(
                state.request_approval(
                    approval_id,
                    f"mcp:{server}",
                    f"{APPROVAL_TOOL_PREFIX}{server}",
                    tool_input=_schema_summary(schema),
                    tool_purpose=message,
                ),
                timeout=approval_window_secs(),
            )
        except asyncio.TimeoutError:
            # The call that asked this question is about to be abandoned, so the answer has
            # nowhere left to go. Cancelling the wait runs `request_approval`'s own `finally`,
            # which drops the pending row; the broadcast below takes the card out of the UI's
            # actionable state so nobody clicks a button that can no longer deliver anything.
            #
            # `cancel` and not `decline`: the spec's `decline` is an explicit refusal, and the
            # user made no choice here. Reporting one they did not make would misstate their
            # intent to the server — the same distinction the no-UI branch above draws.
            _withdraw(state, approval_id)
            logger.info(
                "MCP server %r asked a question nobody answered within %.0fs; cancelled",
                server,
                approval_window_secs(),
            )
            return mcp_types.ElicitResult(action="cancel")
        if not approved:
            return mcp_types.ElicitResult(action="decline")
        return mcp_types.ElicitResult(action="accept", content=content)
    except Exception:  # noqa: BLE001
        logger.warning("MCP elicitation from %r failed", server, exc_info=True)
        return mcp_types.ErrorData(
            code=mcp_types.INTERNAL_ERROR, message="elicitation could not be delivered"
        )


def _withdraw(state: Any, approval_id: str) -> None:
    """Take a card the user can no longer usefully answer out of the UI.

    Reuses the existing ``approval_resolved`` event rather than minting a third resolution
    state: that is the vocabulary every approval surface already consumes, and a new event
    kind would be a second way to say "this card is finished". ``approved: False`` is the
    truthful effect — the boundary fails closed on an unanswered prompt, as
    ``request_approval`` does for every other origin.
    """
    try:
        state.broadcast_ws("approval_resolved", {"id": approval_id, "approved": False})
    except Exception:
        logger.debug("could not withdraw elicitation approval %s", approval_id, exc_info=True)


def _live_state() -> Any:
    """The process-wide dashboard state, or ``None`` outside a running gateway.

    Resolved through :mod:`personalclaw.inbox_providers.native_source` — the settled hook
    for core code that runs outside an HTTP request. A core module must not import
    ``dashboard/`` (the layer-order rail), which is why the state arrives as a duck-typed
    object rather than a ``DashboardState`` annotation.
    """
    try:
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        return get_dashboard_state()
    except Exception:
        logger.debug("dashboard state lookup failed for MCP elicitation", exc_info=True)
        return None


def _schema_summary(schema: Any) -> str:
    """The requested schema as the approval card's argument line.

    The user is being asked to answer a server's question; what the answer will contain
    is part of the decision, so the schema is shown rather than hidden. Serialisation
    failures degrade to an empty line — the card's `purpose` (the server's actual
    question) is the load-bearing text and must render either way.
    """
    if not isinstance(schema, dict) or not schema:
        return ""
    try:
        return json.dumps(schema, sort_keys=True, default=str)[:2000]
    except Exception:  # noqa: BLE001
        return ""
