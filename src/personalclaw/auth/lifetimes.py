"""How long a sign-in, a sign-in link or a token may last, and what asking for longer is told.

**The limit is 90 days** (ledger 285): a long-lived credential is replaced at least every 90
days, and a sign-in that renews nothing and simply lasts is exactly such a credential. The reason
is the one the refusal gives: the longer a link or token keeps working, the longer anyone who
copies it (from shell history, a screenshot, a synced browser, a stolen phone) can use the
dashboard.

**Longer is REFUSED, never clamped.** A clamp mints a credential its caller did not ask for and
tells them nothing — the caller believes it lasts a year and it lasts 90 days, or the reverse. So
every place a lifetime is ASKED for — ``personalclaw token --ttl``, ``GET /api/token/local?ttl=``,
``auth.session_ttl`` through the config API or ``personalclaw config set``, and an app calling
``generate_token`` — refuses with a sentence naming the limit and why (:func:`too_long`, and
:func:`config_too_long` for a config write). The one exception is a config file that ALREADY says
longer: it is applied as 90 days (:func:`configured_lifetime`), because a hand-edited file must
never brick the box, and ``personalclaw doctor`` and the Doctor page say so until it is fixed
(:func:`session_lifetime_report`).

Deliberately free of imports from the rest of PersonalClaw: the config validator, the gateway's
token code and both doctors need this, and none of them may drag another in — the doctors in
particular must not import the HTTP surface to read a setting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The longest any sign-in, link or token may last: 90 days.
MAX_LIFETIME_SECS = 90 * 86400

#: How long a BROWSER sign-in lasts when ``auth.session_ttl`` says nothing: 30 days
#: (REMOTE-USER-AUTH S1). Sessions survive a restart, so a long default would mean a browser
#: cookie that outlives the reason it was issued, and a stolen one stays good that long. 30 days
#: is long enough that a daily-driven instance never prompts you, and short enough that an
#: abandoned session ages out. ``auth.session_ttl`` overrides it, up to the limit, for every door
#: a browser signs in through: a password, a device code, a pairing, and the link the gateway
#: prints and opens at startup (and its harness twin).
DEFAULT_BROWSER_SESSION_TTL_SECS = 30 * 86400

#: The one grammar for a lifetime, wherever one is written: a whole number and a unit — minutes,
#: hours or days (``30m``, ``20h``, ``7d``). Config has always read days; the CLI and the token
#: endpoint now read them too, so ``--ttl 90d`` means what it says.
_LIFETIME = re.compile(r"(\d+)([mhd])")
_UNIT_SECS = {"m": 60, "h": 3600, "d": 86400}

#: Why the limit exists, in the words every refusal uses.
WHY = (
    "90 days is the limit for a long-lived credential, because the longer a link or token keeps "
    "working, the longer anyone who copies it can use your dashboard."
)


def lifetime_seconds(text: str) -> int | None:
    """*text* (``30m`` / ``20h`` / ``7d``) in seconds, or *None* when it is not a lifetime.

    Returned as written — NOT clamped to the limit — so the caller can refuse a longer request
    rather than quietly shorten it. Zero is not a lifetime, and neither is one with spaces around
    it: a caller reading a hand-edited file strips it first (``parse_config_duration``).
    """
    match = _LIFETIME.fullmatch(str(text or ""))
    if not match:
        return None
    secs = int(match.group(1)) * _UNIT_SECS[match.group(2)]
    return secs if secs > 0 else None


def configured_lifetime(text: str, *, default_secs: int) -> int:
    """A lifetime read from a hand-edited config file, in seconds.

    The opposite posture to a request: never let a typo brick the box. A value that is not a
    lifetime takes *default_secs*, and one longer than the limit is applied AS the limit — the
    one place a longer lifetime is not refused, because nobody is there to be told.
    :func:`session_lifetime_report` is what says so instead.
    """
    secs = lifetime_seconds(str(text or "").strip())
    if secs is None:
        return default_secs
    return min(secs, MAX_LIFETIME_SECS)


@dataclass(frozen=True)
class SessionLifetimeReport:
    """What ``auth.session_ttl`` says against how long a browser sign-in therefore lasts."""

    #: The setting as written (stripped); ``""`` when it is not set.
    configured: str
    #: How long a browser sign-in lasts because of it.
    applied_secs: int
    #: The limit it is held to.
    limit_secs: int
    #: Whether the setting asks for longer than the limit (and so is applied AS the limit).
    over_limit: bool
    #: The sentence that says so; ``""`` unless ``over_limit``.
    warning: str
    #: What to do about it; ``""`` unless ``over_limit``.
    remedy: str


def session_lifetime_report(configured: str) -> SessionLifetimeReport:
    """What ``auth.session_ttl`` = *configured* says, how long a browser sign-in therefore
    lasts, and — when the two differ because the setting is over the limit — the sentence
    that says so, and what to do.

    The ONE derivation behind ``personalclaw doctor``'s line and the Doctor page's
    ``security.session_lifetime`` row, so the two cannot disagree.
    """
    shown = str(configured or "").strip()
    asked = lifetime_seconds(shown) if shown else None
    over = asked is not None and asked > MAX_LIFETIME_SECS
    return SessionLifetimeReport(
        configured=shown,
        applied_secs=(
            configured_lifetime(shown, default_secs=DEFAULT_BROWSER_SESSION_TTL_SECS)
            if shown
            else DEFAULT_BROWSER_SESSION_TTL_SECS
        ),
        limit_secs=MAX_LIFETIME_SECS,
        over_limit=over,
        warning=(
            f"auth.session_ttl is {shown}, longer than the 90-day limit for a sign-in, so every "
            "sign-in lasts 90 days."
            if over
            else ""
        ),
        remedy=(
            "No automatic fix: set auth.session_ttl to 90d or less (`personalclaw config set "
            "auth.session_ttl 90d`), or remove it to use the 30-day default."
            if over
            else ""
        ),
    )


def exact_words(secs: int) -> str:
    """``365 days`` / ``2161 hours`` / ``45 minutes`` — *secs* in the largest unit that divides it
    exactly, so a refusal names what was asked for rather than a rounding of it (2161 hours is
    not "90 days")."""
    secs = max(0, int(secs))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size and secs % size == 0:
            count = secs // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{secs} second{'' if secs == 1 else 's'}"


def too_long(requested_secs: int) -> str:
    """The refusal a request for a lifetime longer than the limit gets: the limit, what was
    asked for, why, and what to ask for instead."""
    return (
        f"A sign-in can last at most 90 days, and {exact_words(requested_secs)} is longer. {WHY} "
        "Ask for 90 days or less, such as 90d or 2160h."
    )


def config_too_long(field: str, configured: str) -> str:
    """The refusal a config WRITE of *field* = *configured* (longer than the limit) gets."""
    return (
        f"{field} can be at most 90 days, and {configured} is longer. {WHY} "
        f"Set {field} to 90d or less."
    )


def unreadable(text: str) -> str:
    """What asking for a lifetime that is not one is told."""
    shown = str(text or "").strip()[:40] or "an empty value"
    return (
        f"“{shown}” is not a length of time. Use a whole number and a unit — minutes, hours or "
        "days — such as 30m, 20h or 7d."
    )
