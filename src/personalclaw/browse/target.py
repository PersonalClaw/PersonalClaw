"""WHICH browser a browse task drives — the execution-target selector (BA-7, plan §(a)/§(d)).

Two targets, one closed vocabulary:

* ``gateway`` (the DEFAULT) — the shipped §5 path: a CDP page target named on the action
  config, running under the gateway's own per-site profile. Absent a ``target`` key this is
  what a config resolves to, so every browse action authored before this module existed
  keeps the behaviour it had.
* ``user_browser`` — the operator's OWN browser, reached through the connector registered
  below. It inherits the sessions the operator is already logged into, which is exactly why
  it carries the two refusals this module exists to make unavoidable.

**The two refusals, and why they are not one.**

1. **No silent fallback.** A ``user_browser`` task with no connector returns
   ``outcome="skip"`` and a typed reason. It must NEVER quietly run on the gateway profile
   instead: the gateway's profile is a *different identity* — different cookies, different
   logins, different credentials — so "fell back" would run work the operator scoped to
   their own session against an account they did not name. The mechanism that makes that
   structural rather than a promise is :func:`resolve_cdp_url`: the ``user_browser`` branch
   reads its endpoint from the CONNECTOR and never from ``action_config["cdp_url"]``, so
   there is no code path along which the gateway's target can be reached by a task that
   asked for the user's browser.
2. **Never unattended.** Per AUTONOMY-GUARDRAILS' earned-autonomy ladder the ``user_browser``
   target sits at a floor that no evidence promotes: driving a browser that is already
   logged into the operator's bank while nobody is watching is not a rung, it is a category
   the ladder does not contain. So this is expressed as a construction-level refusal rather
   than a fifth rung name — see :func:`permits_unattended` and
   :func:`unattended_refusal`. The provider ``browse`` remains registered at
   ``one_tap``/``one_tap`` in ``guardrails.rungs`` (that spec is read, not restructured,
   here); ``tests/test_browse_target.py`` rails its ceiling so a later session cannot
   promote the provider to an unattended rung underneath this floor.

**Where each refusal is consulted.** The unattended refusal fires at REGISTRATION
(``triggers.tools.create``/``update`` — every trigger fire is unattended by construction,
see ``gateway._background_write_surface``) *and* at the call site inside
``BrowseActionProvider.execute``, because a gate placed one level away from the work is
bypassed by the next caller that brings its own plumbing. The connector refusal can only be
answered at run time — whether a browser is attached is not knowable when a row is saved.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from personalclaw.errors import AgentError

logger = logging.getLogger(__name__)

#: The action-config key. One literal, read by the provider and by the registration refusal.
TARGET_KEY = "target"

TARGET_GATEWAY = "gateway"
TARGET_USER_BROWSER = "user_browser"

#: The closed vocabulary. A value outside it is REFUSED, never coerced: silently reading a
#: typo'd ``user_browsr`` as the default would run on the gateway profile a task that asked
#: for the operator's own browser — the very substitution clause 1 above forbids, arriving
#: through a spelling mistake instead of through a fallback branch.
BROWSE_TARGETS: tuple[str, ...] = (TARGET_GATEWAY, TARGET_USER_BROWSER)

DEFAULT_TARGET = TARGET_GATEWAY


class UnknownBrowseTarget(ValueError):
    """``target`` named something outside :data:`BROWSE_TARGETS`."""

    def __init__(self, raw: str) -> None:
        super().__init__(raw)
        self.raw = raw


def resolve_target(action_config: Mapping[str, Any] | None) -> str:
    """The target one browse action config names. Missing/empty ⇒ :data:`DEFAULT_TARGET`.

    Raises :class:`UnknownBrowseTarget` for anything else, for the reason
    :data:`BROWSE_TARGETS` records.
    """
    raw = str((action_config or {}).get(TARGET_KEY) or "").strip()
    if not raw:
        return DEFAULT_TARGET
    if raw not in BROWSE_TARGETS:
        raise UnknownBrowseTarget(raw)
    return raw


def permits_unattended(target: str) -> bool:
    """Whether ``target`` may be driven with no human present.

    ``gateway`` may (its profile is the machine's own, and the rung ladder plus the egress
    policy bound it). ``user_browser`` never may — and an UNKNOWN name never may either, so
    a future third target has to opt in deliberately rather than inherit permission from a
    boolean that happened to read False.
    """
    return target == TARGET_GATEWAY


# ── the connector ─────────────────────────────────────────────────────────────
#
# A live attachment, so a process-global rather than a file: "is the operator's browser
# attached right now" is a socket property, and a persisted flag would answer "it was
# attached once" — which for a target chosen to inherit live logins is the wrong question.
# BA-8 (the loopback extension connector, paired through COMPANION-APPS' device-session
# machinery) is the WRITER by design; today nothing calls `register_connector` outside
# tests, so `connector_status()` answers "not connected" — which is the truth, and the
# `skip` path below is the reachable consequence of it.


@dataclass(frozen=True)
class ConnectorSession:
    """One attached browser: who it is, and the page target it exposes."""

    device_id: str
    cdp_url: str
    connected_at: float


@dataclass(frozen=True)
class ConnectorStatus:
    """Whether a ``user_browser`` task can run, and the sentence to show when it cannot."""

    connected: bool
    #: WHY not, phrased for a person. Empty when connected.
    reason: str = ""
    #: What to do about it. Empty when connected.
    fix: str = ""
    device_id: str = ""
    cdp_url: str = ""


_lock = threading.Lock()
_session: ConnectorSession | None = None


def register_connector(*, device_id: str, cdp_url: str) -> ConnectorSession:
    """Attach the operator's browser. Returns the recorded session.

    Replaces any prior attachment: one operator, one browser at a time, and two live
    registrations would make "which browser did that run in" unanswerable.
    """
    global _session
    if not (device_id or "").strip():
        raise ValueError("a connector must name its device")
    if not (cdp_url or "").strip():
        raise ValueError("a connector must expose a CDP page target")
    session = ConnectorSession(
        device_id=device_id.strip(), cdp_url=cdp_url.strip(), connected_at=time.time()
    )
    with _lock:
        _session = session
    logger.info("browse: user browser connected (%s)", session.device_id)
    return session


def clear_connector() -> None:
    """Detach. Idempotent — a double disconnect is not an error."""
    global _session
    with _lock:
        _session = None


def connector_status() -> ConnectorStatus:
    """Whether a ``user_browser`` task can run right now.

    Two distinct "no"s, kept distinct because they have different remedies: the operator has
    not switched the target on (a settings decision), or they have and no browser is
    attached (a connector decision). Collapsing them into one "unavailable" would send a
    user to look for an extension problem when the switch is simply off.
    """
    if not user_browser_enabled():
        return ConnectorStatus(
            connected=False,
            reason="the user-browser target is switched off",
            fix="turn on Settings → Companion apps → Browser control, then re-run",
        )
    with _lock:
        session = _session
    if session is None:
        return ConnectorStatus(
            connected=False,
            reason="no browser is connected",
            fix=(
                "open your browser and connect the PersonalClaw extension, " "then re-run this task"
            ),
        )
    return ConnectorStatus(connected=True, device_id=session.device_id, cdp_url=session.cdp_url)


def user_browser_enabled() -> bool:
    """The operator's ``browse.user_browser_enabled`` switch.

    Fails CLOSED on an unreadable config: a target that inherits live logins must not become
    available because a JSON file could not be parsed.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().browse.user_browser_enabled)
    except Exception:
        logger.debug("browse: user_browser_enabled unreadable", exc_info=True)
        return False


def resolve_cdp_url(target: str, action_config: Mapping[str, Any] | None) -> str:
    """The CDP page target ``target`` drives — the structural half of "no silent fallback".

    ``gateway`` reads ``action_config["cdp_url"]`` (byte-identical to what the provider read
    before this module existed). ``user_browser`` reads the CONNECTOR and nothing else: this
    function is the only place a browse endpoint is chosen, and the ``user_browser`` branch
    cannot reach the config key at all, so there is no fallback to suppress — the substitution
    is unrepresentable rather than merely unperformed.
    """
    if target == TARGET_USER_BROWSER:
        return connector_status().cdp_url
    return str((action_config or {}).get("cdp_url") or "").strip()


def unattended_origin() -> str:
    """The ambient unattended origin, or ``""`` when a human is at the keyboard.

    Reads the ONE signal this tree already produces for exactly this question: the
    ``state_history`` writing surface. ``gateway._background_write_surface`` wraps EVERY
    store-trigger dispatch in ``SURFACE_BACKGROUND`` — deliberately for the whole fire,
    including "the provider's own writes" — and the hand-driven "run now" path
    (``dashboard.handlers.triggers._dispatch_store_action``) keeps the default
    ``interactive``. So a clock/cron/file/webhook fire reads unattended here without this
    module inventing a second notion of the word, and a person clicking Run reads attended.

    Returns the surface NAME rather than a bool so the refusal can say what asked.
    """
    try:
        from personalclaw.durability.state_history import current_surface, is_unattended_surface

        surface = current_surface()
        return surface if is_unattended_surface(surface) else ""
    except Exception:
        # An unreadable surface is NOT taken as attended: this floor exists to stop work in
        # somebody's logged-in browser, and "the import failed" is not evidence of a human.
        logger.debug("browse: writing surface unreadable", exc_info=True)
        return "an unattended run"


# ── typed refusals ────────────────────────────────────────────────────────────


def unknown_target_error(raw: str) -> AgentError:
    """``target`` named something the vocabulary does not contain."""
    return AgentError(
        code="ERR_BROWSE_TARGET_UNKNOWN",
        what=f"browse does not know the execution target {raw!r}",
        why=(
            "`target` is a closed vocabulary, and an unrecognised value is refused rather "
            "than read as the default — running on the gateway's own browser profile a task "
            "that asked for yours would use different logins than you named"
        ),
        fix=f"set `target` to one of {', '.join(BROWSE_TARGETS)} (omit it for the default)",
    )


def unattended_refusal(target: str, *, origin: str) -> AgentError:
    """``target`` may not run with nobody watching. ``origin`` names what asked."""
    return AgentError(
        code="ERR_BROWSE_TARGET_UNATTENDED",
        what=(
            f"the {target!r} browse target cannot run unattended "
            f"({origin or 'an unattended run'} has no human present)"
        ),
        why=(
            "the user-browser target drives the browser you are already logged into, so it "
            "requires a person watching by construction — it sits at a floor on the "
            "earned-autonomy ladder that no track record promotes"
        ),
        fix=(
            "run this from the dashboard yourself, or set `target` to "
            f"{TARGET_GATEWAY!r} so it runs on this machine's own browser profile"
        ),
    )


def disconnected_skip(status: ConnectorStatus) -> AgentError:
    """No browser is attached, so the task is SKIPPED — never re-pointed at the gateway."""
    return AgentError(
        code="ERR_BROWSE_USER_BROWSER_DISCONNECTED",
        what=f"the browse task asked for your own browser, but {status.reason}",
        why=(
            "the gateway's browser profile is a different identity — different cookies, "
            "different logins — so running there instead would do the work as somebody else; "
            "the task is skipped rather than silently re-pointed"
        ),
        fix=status.fix,
    )
