"""The security-log rows for a credential starting or ending: ``session_signed_in`` and
``session_signed_out``.

One spelling for every credential that lets something reach the gateway: a dashboard session
(``dashboard/token_auth.py``) and an integration's token (``inbound/tokens.py``)
write the same two operations with the same metadata, so the security log answers "what could
reach this gateway, and when did that stop" in one query rather than one per kind.

A row names the credential by its public handle — the one Settings → Devices shows — and never
carries a nonce, a token or a token's hash. Writing never raises: the audit must not break a
sign-in or a sign-out.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SIGNED_IN = "session_signed_in"
SIGNED_OUT = "session_signed_out"


def record(
    operation: str,
    *,
    caller: str,
    session_id: str,
    issuer: str,
    kind: str,
    source: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """One SEL row for *session_id* starting (:data:`SIGNED_IN`) or ending (:data:`SIGNED_OUT`).

    *extra* rides in the metadata; a ``reason`` in it is also named in the row's resources.
    """
    metadata: dict[str, Any] = {"session": session_id, "issuer": issuer, "kind": kind}
    metadata.update(extra or {})
    detail = f" reason={metadata['reason']}" if "reason" in metadata else ""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=caller or "system",
            operation=operation,
            outcome="ok",
            source=source,
            resources=f"session={session_id} issuer={issuer}{detail}",
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001 — the audit must not break a sign-in or a sign-out
        logger.warning("could not record a sign-in event in the SEL", exc_info=True)
