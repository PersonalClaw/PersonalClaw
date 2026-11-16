"""What an automation client's session may reach: the scope vocabulary, and its one refusal.

An automation client (a script, a CI job, another agent's runner) is paired like a phone, from a
code the owner makes in Settings → Devices, but the code names a *scope*: the parts of the
dashboard its session may use. The scope is one fact kept in two places, the signed ``scope``
claim on the token and the ``scope`` field of the session's row, and every request checks the
two agree (``token_auth``). The fixed vocabulary is :data:`SCOPES`; a session holds one or more.

Anything a scoped session may not do is answered with :func:`refused`, one body that names the
scopes it holds and never the route it asked for.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# The vocabulary is core (`auth.automation_scopes`), so the pairing-code store can check a code's
# scope; it's named here too, where everything else about a scoped session lives.
from personalclaw.auth.automation_scopes import SCOPES, normalize_scope  # noqa: F401

#: The scopes a pairing code may be made for in this release. The others answer or cause work,
#: which needs the client's own principal in the approval and loop doors; until those exist a
#: code naming one is refused, so no session holds a scope nothing enforces.
GRANTABLE: tuple[str, ...] = ("status", "loops")

#: The token claim, and the session row field, that carry the scope.
SCOPE_CLAIM = "scope"

#: The device kind of a scoped pairing. Set by the gateway alone, from the code's scope: a
#: client that declares it is ignored (``session_store.sanitize_device_kind``).
AUTOMATION_KIND = "automation"

#: An automation client unused this long is signed out (``session_store.END_IDLE``).
IDLE_END_SECS = 14 * 86400

#: A scoped pairing's lifetime, in days: chosen at pair/start, 30 unless the owner says.
DEFAULT_LIFETIME_DAYS = 30
MIN_LIFETIME_DAYS = 1
MAX_LIFETIME_DAYS = 90

#: Where the redeem page sends an automation client that has just paired.
LANDING = "/#/loops"

#: The wire code of the uniform refusal, and of a token whose scope its row doesn't hold.
ERR_SCOPE_REFUSED = "device_scope_refused"
ERR_SCOPE_MISMATCH = "device_scope_mismatch"


def scope_words(scope: Iterable[str]) -> str:
    """*scope* as the refusal names it: ``loops, status``."""
    return ", ".join(scope)


def refusal_message(scope: Iterable[str]) -> str:
    """The sentence of the uniform refusal: the scopes the session holds, never the route."""
    return f"This automation client can use only what its scope allows: {scope_words(scope)}."


def refused(scope: Iterable[str]) -> Any:
    """The uniform 403 for a scoped session: the same body whatever it asked for."""
    from personalclaw.http_errors import json_error

    return json_error(ERR_SCOPE_REFUSED, message=refusal_message(scope), status=403)
