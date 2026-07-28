"""Owner identity — the attribution username (TEAM-SHARED-ENTITIES §1).

PersonalClaw is single-user, and every record it writes is implicitly the owner's.
That implicitness is fine until a second identity exists — a shared task board, a
synced second machine, a teammate's contribution — at which point records written
*before* anyone thought about attribution are indistinguishable.

So this ships the cheap half now: one username, stamped onto new writes.

Three semantics matter, and each is a deliberate choice rather than an accident:

* **It is an attribution string, not a credential.** Nothing authenticates against
  it and nothing authorizes on it. It answers "who wrote this row", not "who may".
* **A rename affects future writes only.** Existing records keep the string they
  were written with — rewriting history to match a new name would silently
  falsify the record it exists to preserve.
* **Empty degrades to today's behavior.** An install that never re-onboards has no
  username, writes carry no attribution, and everything reads as the owner's. That
  is the pre-existing behavior, unchanged, so this is additive rather than a break.

The slug rule is strict on purpose: this string lands in JSON records, and later in
shard filenames and sync payloads, effectively forever. A permissive rule would bake
whatever someone typed once — trailing spaces, emoji, a full email address — into
records nobody can rename afterwards.
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# Long enough for a real name, short enough to stay readable in a record, a
# filename, and a diff.
USERNAME_MAX_LEN = 32

_ALLOWED = re.compile(r"[^a-z0-9_-]+")
_REPEATS = re.compile(r"-{2,}")


def slugify_username(raw: str) -> str:
    """Normalize a display name or typed username into the canonical slug form.

    Lowercase, ``[a-z0-9_-]`` only, everything else collapsed to a single ``-``,
    trimmed of leading/trailing separators, capped at :data:`USERNAME_MAX_LEN`.
    Accents fold to their base letters (via NFKD) rather than vanishing, so
    ``José`` becomes ``jose`` and not ``jos``.

    Returns ``""`` for input that has no usable characters — an empty username is
    a valid state (it means "no attribution", today's behavior), so this never
    invents a fallback like ``user-1`` that the owner didn't choose.
    """
    if not raw:
        return ""
    # NFKD splits accented characters into base + combining mark; dropping the
    # marks folds "é" to "e" instead of deleting the whole character.
    decomposed = unicodedata.normalize("NFKD", str(raw))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.lower()
    slug = _ALLOWED.sub("-", lowered)
    slug = _REPEATS.sub("-", slug).strip("-_")
    if len(slug) > USERNAME_MAX_LEN:
        # Cut at the cap, then re-trim so we never end on a separator.
        slug = slug[:USERNAME_MAX_LEN].rstrip("-_")
    return slug


def is_valid_username(value: str) -> bool:
    """Whether ``value`` is already in canonical form (so storing it is a no-op)."""
    return value == slugify_username(value)


def suggest_username(display_name: str) -> str:
    """The username to pre-fill from the operator's display name."""
    return slugify_username(display_name)


def current_username() -> str:
    """The owner's username, or ``""`` when unset.

    Never raises: attribution is a nice-to-have on every write path that calls it,
    so a config problem must degrade to "no attribution" rather than failing the
    write it was decorating.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return slugify_username(AppConfig.load().dashboard.username or "")
    except Exception:
        logger.debug("identity: username unreadable — writing without attribution")
        return ""
