"""A question the owner is asked, answered as a question to a client that asks one.

A write that needs the owner's yes, sent without it, is answered ``400 confirmation_required``
with the question in ``error.detail`` (`http_errors.consent_required`). The SPA sends every such
write that way first ON PURPOSE — the gateway, not the page, decides which writes need a yes
(`web/src/lib/securityConsent.ts`) — then asks the owner in the gateway's words and sends it again
with ``"confirm": true``. So every Allow the owner was asked for left a failed request in the
browser's console: the handshake worked, and read as an error while it did. A write that needs a
recent sign-in, from a device that signed in too long ago, is the same kind of answer
(``401 fresh_sign_in_required``, `dashboard.owner_presence`): the page asks the owner to sign in
again (`web/src/lib/freshSignIn.ts`) and sends it once more.

A client that says it will ask (``X-PersonalClaw-Consent: ask``) gets the same body with ``200``
and ``X-PersonalClaw-Consent-Asked: 1``: for it the question is the answer. Nothing is written
either way, and a client that does not say so keeps the ``400``, so the write stays refused for
every caller that cannot ask. This sits outside the security log's audit middleware, which
records the refusal as it was made.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

from personalclaw.http_errors import CONSENT_QUESTION

#: What a client sends to receive the consent question as an answer rather than a failure.
CONSENT_ASK_HEADER = "X-PersonalClaw-Consent"
#: What marks a ``200`` as the consent question, for the client that asked for it that way.
CONSENT_ASKED_HEADER = "X-PersonalClaw-Consent-Asked"


@web.middleware
async def consent_ask_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    response = await handler(request)
    asks = request.headers.get(CONSENT_ASK_HEADER, "").strip().lower() == "ask"
    if asks and not response.prepared and response.get(CONSENT_QUESTION):
        response.set_status(200)
        response.headers[CONSENT_ASKED_HEADER] = "1"
    return response
