"""Share one chat as a redacted, read-only artifact (SESSION-MANAGEMENT SM-9 / T3.3).

T3.3 asked for two halves — "Markdown/JSON export (redacted) **+ optional read-only
shared artifact (never auto-published)**". Only export shipped (see the plan's execution
log, headed "T3.3 — export."), while `docs/roadmap/atomic/SM.md`'s SM-8 evidence line
already claimed the share half existed. This module is that half.

**"Share" here means inside the owner's own instance.** It creates an artifact in the
owner's artifact library on an explicit authenticated request. It does NOT publish
anything: no public URL, no share token, no unauthenticated route. Exposing a conversation
outside the machine belongs to EXTERNAL-ACCESS and is the owner's decision, not a
side effect of a "Share" menu item.

Three properties, and what makes each true rather than claimed:

**Redacted** — the body is :func:`session_export.render_markdown`'s output verbatim, not a
second rendering. That function re-runs both redaction passes over EVERY role because the
dashboard write path deliberately skips ``user``/``system`` (see its module docstring), so
the shared artifact inherits the only redaction those roles ever get. A test asserts the
artifact body is byte-identical to the export, which is what keeps the two from drifting
into "the export is redacted, the share is nearly redacted".

**Read-only** — the artifact is created with ``readonly=True``, and
``NativeArtifactProvider``'s three content-mutating methods refuse a readonly artifact at
the STORE (not the route), so the MCP tools and workflow actions cannot edit it either.
That is what stops the transcript round-tripping back into a session: an edited copy of a
redacted transcript is neither the conversation that happened nor a redacted export of it,
and there is no path that turns an artifact back into chat history.

**Never auto-published** — :func:`share_session` is called from exactly one place, the
explicit ``POST /api/chat/sessions/{session}/share`` handler. No heartbeat tick, no
post-turn hook, no "share on archive" convenience. ``tests/test_session_share.py`` proves
it with an AST census of every call site in ``src/personalclaw``, in the style of SM-5's
"never auto-applies" sweep; a future caller that "just shares it while we're here" reds
that test instead of quietly publishing conversations.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.artifacts.models import Artifact
from personalclaw.dashboard import session_export

logger = logging.getLogger(__name__)

#: A TEXT kind, chosen over ``widget``/``html``: a transcript is prose, and the text kinds
#: are the ones no renderer executes. A conversation about HTML shared as an ``html``
#: artifact would be rendered as a document instead of read as a record.
SHARE_KIND = "markdown"

#: ``manual`` = the owner did this by hand. The other sources (``chat``, ``cron``,
#: ``subagent``) all name an automated producer, and labelling an owner-only action with
#: one of those would make the library read as though something shared this on its own.
SHARE_SOURCE = "manual"

#: Library tag, so shared transcripts are one filter away in the artifacts library.
SHARE_TAG = "shared-chat"


def share_name(title: str, key: str) -> str:
    """Artifact display name. Redacted, because an auto-titled chat can carry a secret in
    its title — the same reason ``render_markdown`` redacts the heading."""
    base = session_export.redact_field(title or key or "Conversation").strip()
    return f"{base or 'Conversation'} (shared chat)"


def share_session(
    provider: Any,
    *,
    key: str,
    title: str,
    meta: dict,
    messages: list[dict],
    session_id: str = "",
) -> Artifact:
    """Create the read-only artifact for one conversation. THE only share path.

    *provider* is an ``ArtifactProvider`` (the registry's, so a second backend needs no
    change here). *key* is the canonical history key, *meta*/*messages* what the export
    route already read from the conversation log.

    Deliberately not a "share or update the existing one" upsert: re-sharing writes a NEW
    artifact. An upsert would need to mutate a readonly artifact — the one thing the
    read-only guarantee exists to prevent — and two shares of a conversation at different
    points are two different records, not two versions of one.
    """
    body = session_export.render_markdown(title=title, key=key, meta=meta, messages=messages)
    art = provider.create(
        name=share_name(title, key),
        content=body,
        kind=SHARE_KIND,
        source=SHARE_SOURCE,
        description=(
            "Read-only shared copy of a chat transcript. Credentials and suspicious URLs "
            "are redacted, so this is not a verbatim record of the conversation."
        ),
        tags=[SHARE_TAG],
        actor="user",
        session_id=session_id or "",
        # Provenance on the creation event: which chat, and that the body is redacted —
        # the same declaration `render_json` makes with `redacted: true`, carried onto the
        # artifact so a consumer reading it months later cannot mistake it for a
        # verbatim transcript.
        event_metadata={"shared_session": key, "redacted": True, "readonly": True},
        readonly=True,
    )
    logger.info("shared session %r as read-only artifact %r", key, art.slug)
    return art
