"""What a mid-chat switch to another model costs the next reply: the numbers its one ask needs.

A chat's next turn re-reads the whole conversation, so a switch of model is paid for at once: the
next reply reads the chat's context on the new model, at the new model's input price. On a long
chat that is real money, and the composer asks once before such a switch to a dearer model, with
both prices (#3808). ``GET /api/chat/switch-price`` answers that ask:

* **Tokens**: the chat's context as its context ring measured it (``session.context_usage``, its
  share of the window times the window's tokens), what the next reply reads. ``None`` while
  either is unknown, and then nothing is asked: there is no number to show.
* **The current model**: the one the chat's next reply runs on if nothing is switched: the model
  the chat names (``session.model``), or, on Auto, the one its latest turn was answered by
  (``session.answered_by``, set where the turn was priced).
* **Prices**: both models' input for those tokens, through the one pricing function
  (``routing.rates.price_call``), so the prices the owner set apply. ``None`` for a model nothing
  prices: unknown, never free.

It asks when the context is at least :data:`ASK_FROM_TOKENS` and the new model is not known to
cost no more than the current one: dearer, or either price unknown (a new model known to be free
costs no more than any). An agent CLI's chat is never
asked: its models are the CLI's, priced by its own runtime. Nothing here changes anything; the
switch itself goes through ``POST /api/chat/sessions/{session}/model`` as it always did.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error

#: The context, in tokens, from which a switch to a dearer model is asked about first. Below it the
#: next reply's re-read costs cents on any model a chat is likely to run on, and asking would only
#: get in the way; from it, a switch between a small and a large model can cost dollars per reply.
ASK_FROM_TOKENS = 50_000


def context_tokens(session: Any) -> int | None:
    """The tokens of *session*'s context as its ring last measured them, or ``None`` unknown."""
    usage = getattr(session, "context_usage", None) or {}
    pct, window = usage.get("pct"), usage.get("window")
    if not isinstance(pct, (int, float)) or not isinstance(window, int) or window <= 0:
        return None
    return round(window * max(0.0, float(pct)) / 100)


def _input_price(provider: str, model: str, tokens: int) -> float | None:
    """What *tokens* of input cost on *model*: dollars, or ``None`` when nothing prices it."""
    from personalclaw.routing.rates import price_call

    price = price_call(provider, model, input_tokens=tokens)
    return round(price.dollars, 6) if price.priced else None


def named_model(ref: str) -> tuple[str, str] | None:
    """``(provider entry, model id)`` of the model a chat names, as the composer's model list names
    it: ``"<entry>:<id>"`` from a use-case chain, ``"<entry>/<id>"`` from the list's discovered
    models, or a bare id; ``None`` for Auto."""
    ref = str(ref or "").strip()
    if not ref or ref == "Auto":
        return None
    # The entry ends at whichever separator comes first: an id may hold either one after it
    # ("bedrock/us.anthropic.claude-v1:0", "openrouter:meta-llama/llama-3"), an entry name neither.
    cut = min((i for i in (ref.find(":"), ref.find("/")) if i > 0), default=-1)
    if 0 < cut < len(ref) - 1:
        return ref[:cut], ref[cut + 1 :]
    return "", ref


def current_model(session: Any) -> tuple[str, str] | None:
    """The model *session*'s next reply runs on if nothing is switched: the one it names, else the
    one its latest turn was answered by, else ``None`` (on Auto, before a turn)."""
    return named_model(getattr(session, "model", "")) or getattr(session, "answered_by", None)


def switch_price(session: Any, provider: str, model: str) -> dict[str, Any]:
    """The answer for a switch of *session* to *model* on the provider entry *provider*."""
    tokens = context_tokens(session)
    current = current_model(session)
    cur_dollars: float | None = None
    next_dollars: float | None = None
    if tokens is not None and current is not None:
        cur_dollars = _input_price(current[0], current[1], tokens)
        next_dollars = _input_price(provider, model, tokens)
    cur: dict[str, str | float | None] = {
        "provider": current[0] if current else "",
        "model": current[1] if current else "",
        "dollars": cur_dollars,
    }
    nxt: dict[str, str | float | None] = {
        "provider": provider,
        "model": model,
        "dollars": next_dollars,
    }
    same = current is not None and (current[0], current[1]) == (provider, model)
    no_dearer = next_dollars is not None and (
        next_dollars == 0 or (cur_dollars is not None and next_dollars <= cur_dollars)
    )
    ask = (
        not getattr(session, "acp_provider", "")
        and tokens is not None
        and tokens >= ASK_FROM_TOKENS
        and current is not None
        and not same
        and not no_dearer
    )
    return {
        "ask": bool(ask),
        "tokens": tokens,
        "threshold": ASK_FROM_TOKENS,
        "current": cur,
        "next": nxt,
    }


async def api_chat_switch_price(request: web.Request) -> web.Response:
    """GET /api/chat/switch-price — what switching an open chat's model costs its next reply.

    ``session`` names the chat; ``provider`` and ``model`` the model it would switch to, as the
    composer's model list names them. Answers ``{ask, tokens, threshold, current: {provider, model,
    dollars}, next: {provider, model, dollars}}``. An unknown ``session``, or no ``model``, is
    refused."""
    state: DashboardState = request.app["state"]
    key = request.query.get("session", "")
    model = request.query.get("model", "")
    if not model:
        return json_error(
            "invalid_request", message="model names the model to switch to.", status=400
        )
    session = state._sessions.get(key) if key else None
    if session is None:
        return json_error("session_not_found", status=404)
    return web.json_response(switch_price(session, request.query.get("provider", ""), model))
