"""What a browser is told when its sign-in, or its pair, can't admit a request.

The sentences of the refusals :mod:`sign_in_pair` adds, beside the ones ``token_auth`` already
composes: a live bound sign-in whose request didn't carry its pair, a startup link a browser
already opened, and a sign-in the update to pair-bound sign-ins ended. Each is worded through the
session's own door back in (``token_auth._how_to_sign_back_in``), and none says "pair", which
Settings → Devices already uses for pairing a device.
"""

from __future__ import annotations

import re
import time
from typing import Any

from aiohttp import web
from packaging.version import Version

from personalclaw import versions
from personalclaw.dashboard import token_auth as ta
from personalclaw.dashboard.session_store import ISSUER_ENROLL, ISSUER_PAIR
from personalclaw.dashboard.sign_in_pair import (
    REFUSED_NAVIGATION_UNPAIRED,
    REFUSED_PAIR_MISSING,
    REFUSED_PAIR_WRONG,
    REFUSED_SIGNATURE,
    REFUSED_UNBOUND_COOKIE,
    SIGNS_THE_PAGE_OUT,
    is_navigation,
)


def link_opened_notice() -> ta.SignedOutNotice:
    """A startup link a browser has already opened, opened again by one that holds no sign-in.
    It signed that browser in, and it signs nothing else in."""
    return ta.SignedOutNotice(
        code=ta.ERR_SESSION_REQUIRED,
        reason=ta.REFUSED_LINK_OPENED,
        at=time.time(),
        message=(
            "This sign-in link has already signed a browser in. "
            f"{ta._how_to_sign_in_with_a_new_link()}"
        ),
        heading="This link can’t sign you in",
    )


def updated_sentence(via: str, kind: str, when: str, *, user_agent: str = "") -> str:
    """Why a sign-in the update to pair-bound sign-ins ended is over, and the way back in
    (:func:`way_back`). A paired device is told the order: the computer first, then this device,
    then its notifications."""
    if older_desktop_app(user_agent):
        return (
            f"This window was signed out {when}, when PersonalClaw was updated. "
            f"{way_back(via, kind, user_agent)}"
        )
    if via in (ISSUER_PAIR, ISSUER_ENROLL):
        return (
            f"This device was signed out {when}, when PersonalClaw was updated. To sign it back "
            f"in, {_pair_it_again(kind)}"
        )
    device = "This browser" if kind == "browser" else "This device"
    return (
        f"{device} was signed out {when}, when PersonalClaw was updated. "
        f"{ta._how_to_sign_back_in(via, kind)}"
    )


def _pair_it_again(kind: str) -> str:
    """A paired *kind* of device's way back in, in its order. The desktop app redeems a pairing
    link in its own Gateway → Gateways… window, not in a browser."""
    if kind == "desktop":
        pair = (
            "pair this device again: choose Pair a device in Settings → Devices & Access → "
            "Devices there, then paste the pairing link into this app’s Gateway → Gateways… window"
        )
    else:
        pair = "pair this device again from Settings → Devices & Access → Devices"
    return (
        f"sign in on the computer running PersonalClaw first, then {pair}, then turn its "
        "notifications back on."
    )


def way_back(via: str, kind: str, user_agent: str) -> str:
    """The way back in for a sign-in that came through *via* on a *kind* of device, read from the
    requester's agent first. A window of a desktop app older than the window door
    (:func:`older_desktop_app`) is told to update the app, since reopening it would sign nothing
    in, and a paired one then how to pair again. Anyone else is told what
    ``token_auth._how_to_sign_back_in`` says."""
    if not older_desktop_app(user_agent):
        return ta._how_to_sign_back_in(via, kind)
    if via in (ISSUER_PAIR, ISSUER_ENROLL):
        return f"{update_the_app()} Then {_pair_it_again(kind)}"
    return update_the_app()


#: The first desktop app that signs its windows in with a pair of their own (the window door).
MIN_DESKTOP_VERSION = "0.3.7"
DESKTOP_RELEASES = "https://github.com/PersonalClaw/PersonalClaw/releases"


#: The app and its version in a window's agent, as Electron writes them: ``PersonalClaw/0.3.7``.
_APP_IN_THE_AGENT = re.compile(r"\bPersonalClaw/(\S+)", re.IGNORECASE)


def from_the_desktop_app(user_agent: str) -> bool:
    """Whether *user_agent* is a PersonalClaw desktop window's: Electron's agent names the app."""
    return "Electron/" in user_agent and "personalclaw" in user_agent.lower()


def older_desktop_app(user_agent: str) -> bool:
    """Whether *user_agent* is a window of a desktop app from before :data:`MIN_DESKTOP_VERSION`,
    which can't sign its windows in with a pair of their own. An app whose agent names no version
    this can read counts as older. A current app is told what any refusal says."""
    if not from_the_desktop_app(user_agent):
        return False
    named = _APP_IN_THE_AGENT.search(user_agent)
    version = versions.parse_version(named.group(1)) if named else None
    # By release: the floor's own candidates (`PersonalClaw/<floor>-rc.1`) have the window door.
    return version is None or version.release < Version(MIN_DESKTOP_VERSION).release


def update_the_app() -> str:
    """The way back in for a desktop app older than the window door: update it, from where."""
    return (
        f"Update the PersonalClaw app to {MIN_DESKTOP_VERSION} or newer, from {DESKTOP_RELEASES}."
    )


def pair_notice(reason: str, token: str, *, user_agent: str = "") -> ta.SignedOutNotice:
    """What a page on a live bound sign-in is told when its request can't be admitted as it is.

    A desktop app older than the window door holds a cookie no pair goes with, and no sign-in in
    its window can fix that: it is told to update the app, where to get it, and from which version.
    """
    from personalclaw.dashboard.session_store import load_session_records

    if reason == REFUSED_UNBOUND_COOKIE and older_desktop_app(user_agent):
        return ta.SignedOutNotice(
            code=ta.ERR_SESSION_REQUIRED,
            reason=reason,
            at=time.time(),
            message=(
                f"This copy of the PersonalClaw app is older than this gateway. {update_the_app()}"
            ),
            heading="Update the PersonalClaw app",
        )
    record = load_session_records().get(ta.token_nonce(token))
    how = ta._how_to_sign_back_in(record.issuer, record.device.kind) if record else ""
    again = "Open it again from PersonalClaw."
    message = {
        REFUSED_PAIR_MISSING: f"This browser’s sign-in isn’t complete. {how}",
        REFUSED_NAVIGATION_UNPAIRED: f"This address opens only from inside PersonalClaw. {again}",
        REFUSED_SIGNATURE: f"This address has expired, or isn’t the one PersonalClaw made. {again}",
        REFUSED_PAIR_WRONG: f"This page’s sign-in doesn’t match this browser’s. {how}",
        REFUSED_UNBOUND_COOKIE: f"This browser holds a sign-in that can’t be used here. {how}",
    }[reason]
    return ta.SignedOutNotice(
        code=ta.ERR_SESSION_REQUIRED,
        reason=reason,
        at=time.time(),
        message=message.strip(),
        heading="Open this from PersonalClaw",
    )


def pair_refused(request: web.Request, credentials: Any) -> web.Response:
    """The refusal of a live bound sign-in's request that didn't carry what it needs.

    ``session_required`` with ``detail.reason`` (:mod:`sign_in_pair`). Only a wrong pair and an
    unbound cookie carry ``X-Auth-Required``, the signal every client reads as signed out; the
    others aren't an ending, so the page keeps what it holds and asks again. A wrong pair never
    ends the session: the security event log records the refusal and its reason
    (``token_auth._log_denied``), never a value.
    """
    reason = credentials.pair_reason
    notice = pair_notice(
        reason, credentials.token, user_agent=request.headers.get("User-Agent", "")
    )
    headers = {"Cache-Control": "no-store"}
    if reason in SIGNS_THE_PAGE_OUT:
        headers["X-Auth-Required"] = "true"
    if request.path.startswith("/api/") or not is_navigation(request):
        return ta._notice_response(notice, headers)
    return web.Response(
        text=ta._403_HTML.format(
            heading=notice.heading,
            explanation=ta.notice_html(notice.message),
            owner_token_script=ta.owner_token_script(scrub=True),
        ),
        status=403,
        content_type="text/html",
        headers=headers,
    )
