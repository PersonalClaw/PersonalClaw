"""HTTP API for onboarding's "can I chat yet" VERIFICATION — ``/api/onboarding/model-check``.

``GET /api/onboarding/model-check``
    Attempt the resolution chat itself performs, and report the verdict:
    ``{"ok": true, "source": "binding"|"fallback", "bound": [ref, …]}`` when a chat
    provider really built, else ``{"ok": false, "code", "what", "why", "fix"}`` — the
    bridge's own ``AgentError`` envelope, relayed field for field.

Why this exists at all, and why it is not ``needs_model``
---------------------------------------------------------
``GET /api/onboarding``'s ``needs_model`` is derived from
:func:`~personalclaw.providers.provider_bridge.can_resolve_use_case`, which is
documented as — and must remain — a **no-instantiate** probe: it runs on a hot GET and
on every workflow preflight, so it may not build anything. Its first branch returns
``True`` as soon as ``active_models.json`` holds a ref for the use case, *without
checking that the ref still resolves*.

That is exactly the state onboarding's model step leaves behind, because writing the
binding is the last thing the step does. So the step used to read its own write back as
proof, and the failure mode is measurable: a home whose ``config.json`` carries
``{"name": "my-openai", "type": "openai"}`` with no app registering that type, plus a
``chat`` binding to it, reports ``can_resolve_use_case("chat") == True`` — hence
``needs_model: false``, hence a green "you're ready" — while
``resolve_provider_for_use_case("chat")`` raises ``ERR_MODEL_UNRESOLVED``. The user is
told chat is ready and chat is dead.

A *declaration* and a *build* are different claims, and every cause the bridge
distinguishes (a ref naming a provider config.json no longer has; an entry present but
not registered; a type with no factory because no app claims it, or its app is installed
but disabled, or it failed to load; a capability that does not cover the use case; a
credential with no secret) fires at build time, where ``needs_model`` cannot see it. So
this route asks the real question by doing the real thing, and relays the real cause.

It deliberately adds NO cause analysis of its own. The bridge is the single source of
"why can't I chat", and a second opinion here would be a second thing to keep true; the
route's whole contract is that it does not paraphrase. The consequence worth stating: the
number of distinct causes a user sees is whatever the bridge distinguishes, so improving
the bridge's envelope improves this surface with no change here.

Method, cost and side effects
-----------------------------
A ``GET`` because it asks a question and stores nothing. Resolution *constructs* a
provider (an HTTP client object) but performs no outbound request — the same shape as
``GET /api/onboarding/local-model``, which does a real loopback round trip on a GET. The
built provider is shut down in a ``finally``, exactly as
``llm_helpers.one_shot_completion`` does, so a verification never leaks a client.

What ``ok: true`` does NOT claim
--------------------------------
**It is not a reachability claim, and nothing here may be read as one.** Because a build
constructs a client rather than calling out, a provider pointed at a dead endpoint builds
fine: measured live on a ``{"type": "ollama", "options": {"endpoint":
"http://127.0.0.1:19999"}}`` entry, this route answers ``ok: true`` while ``POST
/api/model-providers/{name}/test`` answers ``Cannot connect to host 127.0.0.1:19999``.

That split is deliberate, not a gap left open: the two probe different layers, and the
onboarding step uses both. This route answers *"is the binding coherent"* — the
configuration/registration/capability/credential family, which is where the nine causes
live and the only family a green tick could otherwise fake. The provider test answers *"is
it answering"*, and the step fires it exactly where the difference is visible: on an empty
model list, which ``GET /api/models/chat`` produces identically for "offers no chat model"
and "could not be reached" (it gathers catalogs with ``return_exceptions=True`` and drops a
raising provider silently). Collapsing the two into one endpoint would mean either putting
a network round trip behind this GET or reporting an unreachable provider as unbindable —
both worse than naming the layer each answer belongs to.
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)

#: The use case onboarding's model lane is about. Chat is what the flow promises and the
#: only use case a first run binds, so the verification is pinned to it rather than
#: parameterised — a route that could be asked about ``embedding`` would need copy for an
#: answer onboarding never shows.
_USE_CASE = "chat"


async def api_onboarding_model_check(request: web.Request) -> web.Response:
    """GET /api/onboarding/model-check — did a chat provider actually build?

    Always ``200``: this is a verdict about the home, not a failure of the request, and
    the failing body carries the envelope the UI needs. A 4xx would make the API client
    throw and the three lines (``what``/``why``/``fix``) would be flattened into one
    string on the way out — which is the presentation-layer version of the very defect
    this route exists to fix.
    """
    from personalclaw.providers.provider_bridge import (
        ProviderResolutionError,
        resolve_provider_for_use_case,
    )

    # Read the binding BEFORE resolving: resolution honours an active ref first (see
    # ``resolve_provider_for_use_case``'s documented order), so the presence of a ref is
    # what distinguishes "the model you picked answered" from "a configured provider
    # answered because you picked nothing". Both are `ok`, and they are different
    # sentences — a user who never chose a model should not be told their choice works.
    bound: list[str] = []
    try:
        from personalclaw.providers.use_cases import active_model_refs

        bound = list(active_model_refs(_USE_CASE))
    except Exception:  # noqa: BLE001 — an unreadable binding store is not a verdict
        logger.debug("model-check: active-model probe failed", exc_info=True)

    provider = None
    try:
        provider = resolve_provider_for_use_case(_USE_CASE)
    except ProviderResolutionError as e:
        return web.json_response({"ok": False, **_envelope(e)})
    except Exception as e:  # noqa: BLE001 — a crash is a NO, never a silent yes
        logger.warning("model-check: resolution raised %s", type(e).__name__, exc_info=True)
        return web.json_response({"ok": False, **_unexpected(e)})
    finally:
        if provider is not None:
            try:
                await provider.shutdown()
            except Exception:  # noqa: BLE001 — a failed teardown does not change the verdict
                logger.debug("model-check: provider shutdown failed", exc_info=True)

    return web.json_response(
        {
            "ok": True,
            "source": "binding" if bound else "fallback",
            "bound": bound,
        }
    )


def _envelope(e: Exception) -> dict[str, str]:
    """The bridge's own ``AgentError`` as JSON, or an honest stand-in when it carried none.

    ``ProviderResolutionError.agent_error`` is optional, so a caller that assumed it
    present would turn a real refusal into a 500 and the step would show nothing. The
    fallback branch uses ``str(e)`` — which for an envelope-carrying error is already the
    rendered three lines, and for the bare form is the plain message.
    """
    err = getattr(e, "agent_error", None)
    if err is not None:
        return {k: v for k, v in err.to_dict().items() if isinstance(v, str)}
    from personalclaw.security import redact_for_display

    detail = redact_for_display(str(e) or type(e).__name__)
    return {
        "code": "ERR_MODEL_UNRESOLVED",
        "what": f"no model provider resolves for use case {_USE_CASE!r}",
        "why": detail,
        "fix": "add or fix a model provider in Settings → Providers, then bind chat to it",
    }


def _unexpected(e: Exception) -> dict[str, str]:
    """A non-``ProviderResolutionError`` crash, reported as a refusal rather than swallowed.

    The exception's text is masked before it is shown: a provider factory's failure can
    quote the configuration it was handed, and this body reaches a browser.
    """
    from personalclaw.security import redact_for_display

    return {
        "code": "ERR_MODEL_UNRESOLVED",
        "what": f"building a provider for use case {_USE_CASE!r} crashed",
        "why": f"{type(e).__name__}: {redact_for_display(str(e))}".strip().rstrip(":"),
        "fix": (
            "check the gateway log for the traceback, then correct or re-add the provider "
            "in Settings → Providers"
        ),
    }


def register_model_check_routes(app: web.Application) -> None:
    """Register the one /api/onboarding/model-check route."""
    app.router.add_get("/api/onboarding/model-check", api_onboarding_model_check)
