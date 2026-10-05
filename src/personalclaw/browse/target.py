"""WHICH browser a browse task drives — the execution-target selector.

Two targets, one closed vocabulary:

* ``gateway`` (the DEFAULT) — the shipped path: a CDP page target named on the action
  config, running under the gateway's own per-site profile. Absent a ``target`` key this is
  what a config resolves to, so every browse action authored before this module existed
  keeps the behaviour it had.
* ``user_browser`` — the operator's OWN browser, reached through the connector registered
  below. It inherits the sessions the operator is already logged into, which is exactly why
  it carries the two refusals this module exists to make unavoidable, and why a run there works
  only in a tab it opened for itself (the run tabs below), never in one the operator has open.

**The two refusals, and why they are not one.**

1. **No silent fallback.** A ``user_browser`` task with no connector returns
   ``outcome="skip"`` and a typed reason. It must NEVER quietly run on the gateway profile
   instead: the gateway's profile is a *different identity* — different cookies, different
   logins, different credentials — so "fell back" would run work the operator scoped to
   their own session against an account they did not name. The mechanism that makes that
   structural rather than a promise is :func:`resolve_cdp_url`: the ``user_browser`` branch
   reads its endpoint from the run's GRANT (the tab the browser opened for that run) and never
   from ``action_config["cdp_url"]``, so there is no code path along which the gateway's target
   can be reached by a task that asked for the user's browser.
2. **Never unattended.** On the earned-autonomy ladder the ``user_browser``
   target sits at a floor that no evidence promotes: driving a browser that is already
   logged into the operator's bank while nobody is watching is not a rung, it is a category
   the ladder does not contain. So this is expressed as a construction-level refusal rather
   than a fifth rung name — see :func:`permits_unattended` and
   :func:`unattended_refusal`. The provider ``browse`` remains registered at
   ``one_tap``/``one_tap`` in ``guardrails.rungs`` (that spec is read, not restructured,
   here); ``tests/test_browse_target.py`` rails its ceiling so a later change cannot
   promote the provider to an unattended rung underneath this floor.

**Where each refusal is consulted.** The unattended refusal fires at REGISTRATION
(``triggers.tools.create``/``update`` — every trigger fire is unattended by construction,
see ``gateway._background_write_surface``) *and* at the call site inside
``BrowseActionProvider.execute``, because a gate placed one level away from the work is
bypassed by the next caller that brings its own plumbing. The connector refusal can only be
answered at run time — whether a browser is attached is not knowable when a row is saved.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, replace
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
# The writer is the paired browser extension, through the loopback routes in
# `dashboard.handlers.browse_connector`. Attaching names a DEVICE and no page: a page target
# exists only for a tab the browser opened for one granted run (the run tabs below).


@dataclass(frozen=True)
class ConnectorSession:
    """One attached browser: which paired device it is, and since when."""

    device_id: str
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


_lock = threading.Lock()
_session: ConnectorSession | None = None
#: request id → the tab the attached browser opened, or was asked to open, for one granted run.
_run_tabs: dict[str, RunTab] = {}


def register_connector(*, device_id: str) -> ConnectorSession:
    """Attach the operator's browser. Returns the recorded session.

    Replaces any prior attachment: one operator, one browser at a time, and two live
    registrations would make "which browser did that run in" unanswerable. A DIFFERENT device
    attaching drops every run tab the previous one held, so a run in flight sees its tab gone and
    stops; the same device attaching again (its worker restarted) keeps them.
    """
    global _session
    if not (device_id or "").strip():
        raise ValueError("a connector must name its device")
    session = ConnectorSession(device_id=device_id.strip(), connected_at=time.time())
    with _lock:
        if _session is None or _session.device_id != session.device_id:
            _run_tabs.clear()
        _session = session
    logger.info("browse: user browser connected (%s)", session.device_id)
    return session


def clear_connector() -> None:
    """Detach. Idempotent — a double disconnect is not an error. Every run tab goes with it."""
    global _session
    with _lock:
        _session = None
        _run_tabs.clear()


def attached_device() -> str:
    """The device attached as the connector, or ``""``: the attachment alone, whether or not the
    ``user_browser`` switch is on. The switch decides whether a task may run
    (:func:`connector_status`); it does not detach the browser, which must not be told to attach
    again because of it."""
    with _lock:
        return _session.device_id if _session is not None else ""


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
    return ConnectorStatus(connected=True, device_id=session.device_id)


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


def resolve_cdp_url(
    target: str, action_config: Mapping[str, Any] | None, *, grant: Any = None
) -> str:
    """The CDP page target ``target`` drives — the structural half of "no silent fallback".

    ``gateway`` reads ``action_config["cdp_url"]`` (byte-identical to what the provider read
    before this module existed). ``user_browser`` reads the run's ``grant`` and nothing else:
    the page target of the tab the browser opened for THAT run (``BrowserGrant.bound_cdp_url``),
    and ``""`` before there is one. This function is the only place a browse endpoint is chosen,
    and the ``user_browser`` branch cannot reach the config key at all, so there is no fallback
    to suppress — the substitution is unrepresentable rather than merely unperformed.
    """
    if target == TARGET_USER_BROWSER:
        return str(getattr(grant, "bound_cdp_url", "") or "")
    return str((action_config or {}).get("cdp_url") or "").strip()


# ── the run's own tab ─────────────────────────────────────────────────────────
#
# A ``user_browser`` run works only in a tab the browser opened for it. Once the operator grants
# a run, the provider asks the connected browser for a tab (:func:`request_run_tab`); the
# extension sees the request on its next poll, opens a background tab in a group named after the
# task (or a window of its own), and announces THAT tab's page target (:func:`announce_run_tab`),
# which the grant is then bound to. It reports the tab's end the same way
# (:func:`report_run_tab`): closed stops the run, taken over pauses it. Process-global like the
# connector itself, and for the same reason: a run tab is a live fact about a live browser.

#: How long a granted run waits for the browser to open its tab before the run is refused. The
#: extension asks about once a second, so a live one answers well inside this.
RUN_TAB_TIMEOUT = 15.0

#: How often :func:`wait_for_run_tab` looks again.
_RUN_TAB_POLL_SECS = 0.05

#: Asked for; the browser has not answered yet.
TAB_REQUESTED = "requested"
#: The browser opened the run's tab and named its page target.
TAB_OPEN = "open"
#: The browser could not open a tab of the run's own.
TAB_UNAVAILABLE = "unavailable"
#: The person closed the run's tab, its group or its window.
TAB_CLOSED = "closed"
#: The person brought the run's tab to the front to take over.
TAB_TAKEN_OVER = "taken_over"

#: What the browser may REPORT about a run's tab (an announce is the other answer). Each maps to
#: the states a report may move a tab out of: a tab that never opened cannot be taken over, and
#: an ended one cannot reopen.
_REPORTS_FROM: dict[str, tuple[str, ...]] = {
    TAB_UNAVAILABLE: (TAB_REQUESTED,),
    TAB_CLOSED: (TAB_REQUESTED, TAB_OPEN, TAB_TAKEN_OVER),
    TAB_TAKEN_OVER: (TAB_OPEN, TAB_TAKEN_OVER),
}
TAB_REPORTS: tuple[str, ...] = tuple(_REPORTS_FROM)


@dataclass(frozen=True)
class RunTab:
    """The tab the operator's browser opened, or was asked to open, for ONE granted run."""

    #: Core's id for this run's tab, minted here: the extension's only handle on the run.
    request_id: str
    #: The paired device asked: no other device may answer for this tab.
    device_id: str
    #: The tab group's name — the task, as :func:`personalclaw.browse.grant.task_group_name` has it.
    group: str
    state: str = TAB_REQUESTED
    #: The tab's own page target, set once by the announce and never changed after.
    cdp_url: str = ""


def request_run_tab(*, group: str, device_id: str) -> RunTab | None:
    """Ask the attached browser for a tab of a granted run's own. ``None`` when the device the
    grant was given for is no longer the attached connector — there is nobody to ask."""
    with _lock:
        if _session is None or _session.device_id != (device_id or "").strip():
            return None
        tab = RunTab(request_id=uuid.uuid4().hex, device_id=_session.device_id, group=group)
        _run_tabs[tab.request_id] = tab
    return tab


def run_tabs_for(device_id: str) -> list[RunTab]:
    """Every run tab held for ``device_id``: the requests it should answer and the tabs it holds."""
    with _lock:
        return [t for t in _run_tabs.values() if t.device_id == device_id]


def run_tab(request_id: str) -> RunTab | None:
    with _lock:
        return _run_tabs.get(request_id)


def announce_run_tab(*, device_id: str, request_id: str, cdp_url: str) -> RunTab | None:
    """The browser opened the run's tab: record its page target. ``None`` — and nothing changes —
    unless that device was asked for that tab and has not answered yet: a tab's page target is
    set once, so a later announce can never re-point a run at another page."""
    with _lock:
        tab = _run_tabs.get(request_id)
        if tab is None or tab.device_id != device_id or tab.state != TAB_REQUESTED:
            return None
        tab = replace(tab, state=TAB_OPEN, cdp_url=cdp_url)
        _run_tabs[request_id] = tab
    return tab


def report_run_tab(*, device_id: str, request_id: str, state: str) -> RunTab | None:
    """The browser reports the run's tab: unavailable, closed or taken over. ``None`` when that
    device holds no such tab or the report does not apply to the tab's state."""
    allowed = _REPORTS_FROM.get(state)
    with _lock:
        tab = _run_tabs.get(request_id)
        if allowed is None or tab is None or tab.device_id != device_id:
            return None
        if tab.state not in allowed:
            return None
        tab = replace(tab, state=state)
        _run_tabs[request_id] = tab
    return tab


def release_run_tab(request_id: str) -> None:
    """The run ended: forget its tab. The tab itself stays open in the browser, as the
    operator's, and nothing reaches it any more. Idempotent."""
    with _lock:
        _run_tabs.pop(request_id, None)


async def wait_for_run_tab(request_id: str, *, timeout: float = RUN_TAB_TIMEOUT) -> RunTab | None:
    """The run's tab once the browser has answered (announced it, said it could not open one, or
    the person closed it first), or as it stands when ``timeout`` runs out — still
    :data:`TAB_REQUESTED`. ``None`` once the request is gone: the browser disconnected, or another
    one attached."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    while True:
        tab = run_tab(request_id)
        if tab is None or tab.state != TAB_REQUESTED or loop.time() >= deadline:
            return tab
        await asyncio.sleep(_RUN_TAB_POLL_SECS)


def unattended_origin() -> str:
    """The ambient unattended origin, or ``""`` when a human is at the keyboard.

    Reads the ONE signal this tree already produces for exactly this question: the
    ``state_history`` writing surface. ``gateway._background_write_surface`` wraps EVERY
    store-trigger dispatch in ``SURFACE_BACKGROUND`` — deliberately for the whole fire,
    including "the provider's own writes" — and the hand-driven "run now" path
    (``dashboard.handlers.trigger_runs._dispatch_store_action``) keeps the default
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
