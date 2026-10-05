"""Per-site browser profiles and the ``request_login`` handoff.

The handoff is the *positive* half of the invariant :mod:`personalclaw.browse.credentials` enforces
negatively. That module makes it impossible for the agent to read or write a credential; this one
gives the user somewhere to put one. Without both, the invariant is just a feature that does not
work: an agent that cannot type a password and has no other way to authenticate simply fails on
every logged-in site.

**The shape, and why each piece is where it is.**

*A persistent profile per site* at ``$PERSONALCLAW_HOME/browse/profiles/<site_slug>/``. Per
SITE rather than one shared profile because a single profile is a single identity: a run scoped to
one account would carry every other site's live session with it, and one site's tracking cookie
would follow the agent everywhere. The slug is derived from the host by :func:`site_slug`, which is
the only place a URL becomes a path — see its docstring for why that matters.

*The profile is machine-local state, NOT config and NOT entity state.* This was the one real design
question in this module and it is worth recording the answer rather than the conclusion. A Chrome
``user-data-dir`` is an opaque, unbounded, live-session blob: it holds the cookies that ARE the
authentication. It is not config (nothing in it is a decision the user expressed, and no
round-trippable field describes it), and it is not entity state (it is not a fact about a person
or a project — it is a fact about *this machine's browser*). So it lives as a profile directory, and
``durability/inventory.py`` claims ``browse`` in :data:`~personalclaw.durability.inventory.IGNORED`
rather than as a ``secret=True`` entry. That distinction is load-bearing: a ``secret`` entry is
excluded from EXPORTS but *deliberately captured by snapshots* so a backup can restore the
credential store, whereas an IGNORED path never travels at all. A snapshot is restored onto another
machine — carrying a live authenticated browser session there plants a credential on a host the user
never logged in from. It is the same reasoning ``IGNORED`` already records for ``session_key`` and
``sessions.json``, and the "never backed up by snapshot/portability, never exported" is precisely
that posture.

*The park goes through the SHIPPED needs-input gate.* :func:`request_login` builds a
:class:`~personalclaw.workflows.needs_input.NeedsInputItem` and a sentence; it does NOT invent a
second park/resume. The browse loop parks with :data:`PARK_LOGIN_REQUIRED`, the provider maps any
park to ``outcome="needs_input"``, the engine's action-node dispatch maps that to a WAITING
instance, and ``workflows/attention.py`` fires the inbox item — the path the browse loop already
relies on for step/budget exhaustion. A credential handoff is not a new kind of waiting.

*The session check is a heuristic and says so*.:func:`session_state` answers from the
profile's own ``.meta.json`` — cheap, offline, and honest about being a guess. A definitive answer
requires loading an authenticated-only URL and watching for a redirect to a login page, which the
loop can only do once it has a browser; the meta answer is what lets the provider decide *before*
spending a model call. When it says ``EXPIRED`` the run parks before it starts, which is the whole
point: a mid-task interruption wastes every step already paid for.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: The loop's park reason for "a human must authenticate". A distinct value rather than reusing
#: ``PARK_STUCK``: the remedies are opposite — a stuck run wants a different approach, a login wall
#: wants a person — and a card that offers the wrong one trains users to dismiss cards.
PARK_LOGIN_REQUIRED = "login_required"

#: How long a recorded login is ASSUMED to still be good, absent evidence. Two weeks: long enough
#: that a weekly automation does not re-prompt (which is how a handoff becomes the thing the user
#: turns off), short enough that a stale guess is corrected within one cycle of most sites' own
#: session lifetimes. It is a floor on re-prompting, never a claim — a site that logs the profile
#: out on day two is caught by the run itself hitting the login wall, so the pre-run check is a
#: heuristic layered UNDER the mid-run escalation rather than a replacement for it.
DEFAULT_SESSION_TTL_SECS = 14 * 24 * 3600

#: Directory + file modes. ``0o700``/``0o600`` because the profile holds the session cookies that
#: ARE the authentication: another local account being able to read them is the same exposure as
#: leaking the credential itself.
PROFILE_DIR_MODE = 0o700
META_FILE_MODE = 0o600

META_FILENAME = ".meta.json"

#: ``auth_state`` values. ``expired`` is the one that raises a persistent banner; it is written
#: here so the state exists the moment a stale session is observed rather than being invented later.
AUTH_STATE_ACTIVE = "active"
AUTH_STATE_EXPIRED = "expired"
AUTH_STATE_NEW = "new"

#: :func:`session_state` verdicts.
SESSION_ABSENT = "absent"  # no profile has ever been created for this site
SESSION_FRESH = "fresh"  # a login was recorded and the heuristic TTL has not elapsed
SESSION_EXPIRED = "expired"  # a profile exists but its session is assumed stale

#: URL path fragments that mean "this is a login page". Used by :func:`looks_like_login_url` as the
#: detector that does not need a DOM — a start_url pointing straight at a sign-in page is a
#: login wall before a single byte is extracted.
_LOGIN_PATH_TOKENS: tuple[str, ...] = (
    "/login",
    "/signin",
    "/sign-in",
    "/sign_in",
    "/auth",
    "/oauth",
    "/sso",
    "/session/new",
    "/account/login",
    "/accounts/login",
    "/users/sign_in",
)

#: Everything a slug may contain. Anything else collapses to ``-``; see :func:`site_slug`.
_SLUG_SAFE = re.compile(r"[^a-z0-9.-]+")


def profiles_root() -> Path:
    """``$PERSONALCLAW_HOME/browse/profiles``.

    ``config_dir()`` is imported and called INSIDE the function, never bound at import time: a
    module-level constant freezes whichever home was set when the module first loaded, which is how
    a test that patches ``config_dir`` still writes into the operator's real home.
    """
    from personalclaw.config import config_dir

    return Path(config_dir()) / "browse" / "profiles"


def site_slug(url: str) -> str:
    """The directory name for one site — the ONLY place a URL becomes a path.

    Derived from the host and nothing else. Not the path, not the query: ``example.com/a`` and
    ``example.com/b`` are one login, and slugging the path would make the agent re-authenticate per
    page. The port is folded in when present, because ``localhost:8080`` and ``localhost:9090`` are
    genuinely different sites during development.

    **Traversal-proof by construction, not by a check.** Every character outside ``[a-z0-9.-]``
    collapses to ``-``, then leading dots and dashes are stripped. So ``..``, ``../..``, an absolute
    path, a URL-encoded separator and a NUL all reduce to a plain name — there is no input for which
    this returns something that escapes :func:`profiles_root`, which is a stronger statement than
    "we reject the bad ones". A hostless or unparseable URL yields ``unknown-site`` rather than an
    empty string, because an empty slug would resolve the profile directory to the root itself and
    quietly share one profile across every site that failed to parse.
    """
    raw = (url or "").strip()
    host = ""
    try:
        parts = urlsplit(raw if "//" in raw else f"//{raw}")
        host = (parts.netloc or "").lower()
    except ValueError:
        host = ""
    # Drop userinfo before slugging: `https://alice:hunter2@site/` must not put a password in a
    # directory name, and `address_logins.redact_url_userinfo` records that userinfo is the one
    # credential shape a name-based rule never sees.
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    slug = _SLUG_SAFE.sub("-", host).strip(".-")
    # Collapse runs left by the substitution so `a...b` does not become `a---b`.
    slug = re.sub(r"-{2,}", "-", slug)
    slug = re.sub(r"\.{2,}", ".", slug).strip(".-")
    if not slug:
        return "unknown-site"
    return slug[:96]


def profile_dir(url: str) -> Path:
    """The persistent Chrome ``user-data-dir`` for ``url``'s site. Does not create it."""
    return profiles_root() / site_slug(url)


@dataclass
class ProfileMeta:
    """``.meta.json`` — ``{site, last_login_at, session_valid_until, created_at}``.

    ``auth_state`` is the fifth field, added here rather than by the mirror: the mirror *surfaces*
    an expired session (banner + inbox item) and cannot surface a state nobody writes. Writing it
    at the moment staleness is observed is what makes the banner a rendering job instead of a
    re-derivation.

    Deliberately holds NO credential — not a cookie, not a token, not a username. The authentication
    lives in the Chrome profile beside this file, where only Chrome reads it; this file is the
    bookkeeping that lets PersonalClaw answer "do I need to ask the human again" without opening a
    browser. Anything secret in here would be a second copy of the credential in a format the rest
    of the tree is happy to serialize.
    """

    site: str = ""
    created_at: float = 0.0
    last_login_at: float = 0.0
    session_valid_until: float = 0.0
    auth_state: str = AUTH_STATE_NEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "session_valid_until": self.session_valid_until,
            "auth_state": self.auth_state,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ProfileMeta:
        """Tolerant read. A corrupt or partial file degrades to "never logged in", which routes
        the user through the handoff — the safe direction. Degrading to "logged in" would make a
        run sail into a login wall it was told to expect."""
        d = d or {}

        def _f(key: str) -> float:
            try:
                return float(d.get(key, 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        state = str(d.get("auth_state", "") or AUTH_STATE_NEW)
        if state not in (AUTH_STATE_ACTIVE, AUTH_STATE_EXPIRED, AUTH_STATE_NEW):
            state = AUTH_STATE_NEW
        return cls(
            site=str(d.get("site", "") or ""),
            created_at=_f("created_at"),
            last_login_at=_f("last_login_at"),
            session_valid_until=_f("session_valid_until"),
            auth_state=state,
        )


def load_meta(url: str) -> ProfileMeta | None:
    """Read one site's ``.meta.json``, or ``None`` when no profile exists.

    ``None`` and a default :class:`ProfileMeta` are deliberately different answers: the first means
    "this site has never been visited", the second means "a profile exists and its session is not
    usable". The provider phrases those differently to the user.
    """
    import json

    path = profile_dir(url) / META_FILENAME
    try:
        if not path.is_file():
            return None
        return ProfileMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.debug("browse: unreadable profile meta for %s", site_slug(url), exc_info=True)
        # A profile directory that exists with an unreadable meta is a profile, so return the
        # pessimistic record rather than None: the cookies may well still be good, but we cannot
        # claim so, and reporting "no profile" would recreate a directory that already exists.
        return ProfileMeta(site=site_slug(url), auth_state=AUTH_STATE_EXPIRED)


def save_meta(url: str, meta: ProfileMeta) -> Path:
    """Create the profile directory if needed and write ``.meta.json`` atomically at 0600."""
    import json

    pdir = profile_dir(url)
    pdir.mkdir(parents=True, exist_ok=True)
    try:
        pdir.chmod(PROFILE_DIR_MODE)
    except OSError:
        logger.debug("browse: could not tighten profile dir mode", exc_info=True)
    path = pdir / META_FILENAME
    atomic_write(
        path, json.dumps(meta.to_dict(), indent=2, sort_keys=True) + "\n", mode=META_FILE_MODE
    )
    return path


def ensure_profile(url: str, *, now: float | None = None) -> ProfileMeta:
    """The profile for ``url``, created (with a ``new`` meta) if it does not exist yet."""
    existing = load_meta(url)
    if existing is not None:
        return existing
    ts = time.time() if now is None else now
    meta = ProfileMeta(site=site_slug(url), created_at=ts, auth_state=AUTH_STATE_NEW)
    save_meta(url, meta)
    return meta


def record_login(
    url: str, *, now: float | None = None, ttl_secs: float = DEFAULT_SESSION_TTL_SECS
) -> ProfileMeta:
    """Mark a completed human authentication for ``url``'s site.

    Called after the handoff is satisfied — the point at which the Chrome profile on disk holds a
    session it did not hold before. Records WHEN, not WHAT: see :class:`ProfileMeta`.
    """
    ts = time.time() if now is None else now
    meta = load_meta(url) or ProfileMeta(site=site_slug(url), created_at=ts)
    meta.site = meta.site or site_slug(url)
    meta.created_at = meta.created_at or ts
    meta.last_login_at = ts
    meta.session_valid_until = ts + max(0.0, float(ttl_secs))
    meta.auth_state = AUTH_STATE_ACTIVE
    save_meta(url, meta)
    return meta


def mark_expired(url: str, *, now: float | None = None) -> ProfileMeta:
    """Record that ``url``'s session is stale — the state the mirror's banner renders.

    Written the moment staleness is OBSERVED (a login wall mid-run), not when it is predicted, so
    the file distinguishes "the TTL guess elapsed" from "we actually hit a login page".
    """
    ts = time.time() if now is None else now
    meta = load_meta(url) or ProfileMeta(site=site_slug(url), created_at=ts)
    meta.site = meta.site or site_slug(url)
    meta.created_at = meta.created_at or ts
    meta.session_valid_until = 0.0
    meta.auth_state = AUTH_STATE_EXPIRED
    save_meta(url, meta)
    return meta


#: The credential-store prefix an earlier release stored a per-site "profile-encryption key" under,
#: at every browse sign-in (``BROWSE_PROFILE_KEY_<site>``). Nothing ever encrypted a profile with
#: one, and nothing read one but a presence check, so none is minted now;
#: :func:`forget_unused_profile_keys` deletes the ones left behind.
_UNUSED_PROFILE_KEY_PREFIX = "BROWSE_PROFILE_KEY_"


def forget_unused_profile_keys() -> int:
    """Delete every ``BROWSE_PROFILE_KEY_<site>`` an earlier release left in the credential store,
    and return how many. Idempotent: run at every start, it finds nothing after the first.

    Deleted rather than left: with nothing hiding the prefix any more, each would show on the
    Secrets page as a secret the owner never typed — and one the page cannot delete, because a
    site's name carries a dot or a dash, which a secret's name may not.
    """
    from personalclaw.config.credentials import credential_names, delete_credential

    removed = 0
    for name in credential_names():
        if name.startswith(_UNUSED_PROFILE_KEY_PREFIX) and delete_credential(name):
            removed += 1
    if removed:
        logger.info("browse: deleted %d unused profile key(s) an earlier release stored", removed)
    return removed


def expired_sites() -> list[dict[str, Any]]:
    """Every site whose saved session is EXPIRED — the set the mirror's persistent banner renders.

    Scans the profiles root (cheap, offline) and returns the sites whose ``.meta.json`` records
    ``auth_state=expired``. An unreadable meta is surfaced as expired — a profile we cannot read is
    precisely a session a human should re-establish.
    """
    import json

    root = profiles_root()
    out: list[dict[str, Any]] = []
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for pdir in entries:
        meta_path = pdir / META_FILENAME
        try:
            if not meta_path.is_file():
                continue
            meta = ProfileMeta.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception:
            meta = ProfileMeta(site=pdir.name, auth_state=AUTH_STATE_EXPIRED)
        if meta.auth_state != AUTH_STATE_EXPIRED:
            continue
        out.append({"site": meta.site or pdir.name})
    return out


def session_state(url: str, *, now: float | None = None) -> str:
    """The pre-run verdict: :data:`SESSION_ABSENT`, :data:`SESSION_FRESH` or
    :data:`SESSION_EXPIRED`.

    Offline and cheap on purpose — it runs BEFORE the browser is opened and before a model call is
    spent, which is the only position from which it can prevent a mid-task interruption.

    Fails toward EXPIRED on every ambiguity (no recorded login, a zero TTL, an ``expired``
    ``auth_state``, an unreadable file). The asymmetry is the point: a wrong EXPIRED costs one
    handoff prompt the user could have skipped, and a wrong FRESH costs a run that walks into a
    login wall with its steps already spent.
    """
    meta = load_meta(url)
    if meta is None:
        return SESSION_ABSENT
    if meta.auth_state == AUTH_STATE_EXPIRED:
        return SESSION_EXPIRED
    ts = time.time() if now is None else now
    if meta.last_login_at <= 0 or meta.session_valid_until <= 0:
        return SESSION_EXPIRED
    return SESSION_FRESH if meta.session_valid_until > ts else SESSION_EXPIRED


def looks_like_login_url(url: str) -> bool:
    """The URL-pattern detector: does this path look like a sign-in page?

    Path-only. A *query* containing ``login`` is routine on search and analytics pages, and treating
    ``?q=login`` as a login wall would park a research run on its own results.
    """
    try:
        path = (urlsplit(url or "").path or "").lower().rstrip("/")
    except ValueError:
        return False
    if not path:
        return False
    return any(token in f"{path}/" for token in _LOGIN_PATH_TOKENS)


@dataclass
class LoginHandoff:
    """One ``request_login`` park: what the user reads, and the card that asks them.

    ``sentence`` is a **product surface** — the text on the parked run and in the notification, not
    a debug string — so it is composed once here rather than assembled at three call sites that
    would drift.
    """

    site: str
    url: str
    reason: str
    sentence: str
    item: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "url": self.url,
            "reason": self.reason,
            "sentence": self.sentence,
            "needs_input": dict(self.item),
        }


#: Why a handoff was requested. Kept as a closed set so the card's wording is derived, not typed.
REASON_NO_SESSION = "no_session"
REASON_SESSION_EXPIRED = "session_expired"
REASON_CREDENTIAL_FIELD = "credential_field"

_REASON_BLOCKER = {
    REASON_NO_SESSION: "has never been signed in on this machine",
    REASON_SESSION_EXPIRED: "needs you to sign in again — the saved session has gone stale",
    REASON_CREDENTIAL_FIELD: "asked for a password, which the agent is not allowed to type",
}


def request_login(
    url: str,
    *,
    reason: str,
    run_id: str = "",
    node_id: str = "browse",
    resume_token: str = "",
    now: float | None = None,
) -> LoginHandoff:
    """Build the login handoff — the sentence and the needs-input card. Writes no state.

    Goes through :func:`personalclaw.workflows.needs_input.build_item` so the card is the SAME shape
    every other gate produces (one decision, ``attempted`` before ``recommendation``, capped
    evidence). A hand-rolled dict here would be a second dialect on the one surface whose value is
    that every row reads alike.

    **Nothing in the returned payload can hold a credential.** ``evidence`` carries the screened URL
    and the reason code; there is no field for a value, and the two facts it states — which site,
    and that a human must act — are exactly the two the agent may know. The import is
    function-local so ``browse`` stays importable without the workflows package (the CDP transport
    makes the same choice for ``websockets``).
    """
    from personalclaw.browse.credentials import screen_url
    from personalclaw.workflows.needs_input import build_item

    ts = time.time() if now is None else now
    slug = site_slug(url)
    safe_url = screen_url(url)
    blocker = _REASON_BLOCKER.get(reason, "needs you to sign in")
    # Where to sign in is the browser the step DRIVES — the one it resumes in. Nothing opens a
    # window for the person (core launches no browser, `browse/transport.py`), so a sentence saying
    # a window "is open" sent them looking for one.
    sentence = (
        f"Browse needs you to sign in to {slug}, in the browser this step drives: authenticate "
        "there (password, 2FA, whatever it asks), then answer this item and the run continues "
        "with the session you created. PersonalClaw never sees what you type."
    )
    item = build_item(
        run_id=run_id,
        node_id=node_id,
        ask={
            "kind": "approval",
            "prompt": (
                f"Sign in to {slug}, then confirm — the browse run resumes with that session."
            ),
            "node_id": node_id,
            "choices": ["I have signed in", "Cancel this run"],
        },
        # The attempt ledger's own key names — `outcome` + `note`, the pair
        # `publish.ledger_row` mints and `needs_input.summarize_attempts` reads. Any other key
        # is read by nothing: the card falls back to its placeholder and the `_REASON_BLOCKER`
        # wording never reaches the one surface a stuck user actually reads. The wording lives
        # in `note` because that is the clause that explains the result.
        attempts=[{"outcome": f"opened {slug}", "note": f"it {blocker}"}],
        evidence={
            "site": slug,
            "url": safe_url,
            "reason": reason,
            "profile": str(profile_dir(url)),
            "credentials_seen_by_agent": "none",
        },
        resume_token=resume_token,
        now=ts,
    )
    return LoginHandoff(
        site=slug, url=safe_url, reason=reason, sentence=sentence, item=item.to_dict()
    )
