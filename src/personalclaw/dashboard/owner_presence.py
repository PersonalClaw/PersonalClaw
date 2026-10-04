"""Proof that the owner is here now, asked by every route that mints a lasting credential or
changes how the owner signs in.

A live session is not that proof. A browser sign-in lasts 30 days by default and up to 90, so a
phone left unlocked, or a session cookie copied once, could otherwise mint a pairing code, a
device sign-in code or an integration token that outlives the session that minted it, set the
password the owner signs in with, or store a secret the gateway reads to decide who can sign in
(``secrets_vault.is_sign_in_key``). So each such route calls :func:`require_owner_presence`
first, and it accepts one of:

* **a sign-in from the last** :data:`PRESENCE_WINDOW_SECS` **(ten minutes)** — the time the
  session's device signed in, as the session store records it (Settings → Devices shows the same
  time). Signing in again (``POST /api/auth/confirm``, or opening a new ``personalclaw token``
  link) starts a new sign-in, so the window counts from the last identity check;
* **the local machine secret** — ``X-Local-Secret`` from this computer, the claim "a process
  running as this user on this machine" that ``/api/token/local`` rests on;
* **the session the gateway handed the process that started it**, used from this computer: the
  ready line's session (``--json-ready``), which only that process reads. That is the desktop
  app's own windows, which the app signs in itself each time it starts its gateway, and
  ``personalclaw run`` and a test harness;
* **no sign-in at all, when authentication is off** (``PERSONALCLAW_AUTH_MODE=none``, which the
  gateway serves on loopback only): there is no sign-in to be recent, and every request is the
  owner's by the owner's own choice.

An app is never the owner here, whatever session it narrows. Anything else is refused with
``401 fresh_sign_in_required``: a sentence saying what to do, the facts the dashboard needs to
ask for it (``detail``), and a security log row. The answer carries no ``X-Auth-Required``, so no
client reads it as the device being signed out: it is still signed in, just not recently enough.
The dashboard turns it into a sign-in prompt and sends the write again (``web/src/lib/
freshSignIn.ts``); it is a question the page asks, so a page that says it asks gets it as a
``200`` marked ``X-PersonalClaw-Consent-Asked`` (``dashboard.consent_ask``), as it gets a consent
question.

**Changing an existing password asks for more than presence**: the current password, and the
authenticator code when one is set up (``identity=``). A wrong one is refused, logged, and counted
toward the same per-address lockout as the sign-in page, so a session cannot be used to guess it.

**Containment never asks.** Signing a device out, signing out every other device, replacing the
signing key, revoking an integration token, removing a secret, turning incident mode on and
tightening a sign-in setting must work when something is wrong, and a prompt would only delay
them. They do not call this; ``tests/test_owner_presence_census.py`` holds both halves.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from personalclaw.http_errors import CONSENT_QUESTION, json_error

logger = logging.getLogger(__name__)

#: How recent a sign-in must be to stand for the owner being here: ten minutes. Inside the
#: fifteen-minute idle limit a whole session is commonly held to, and long enough to sign in,
#: open Settings → Devices and pair the phone in your hand without being asked twice; short
#: enough that a copied cookie, or a device left signed in, cannot mint a credential later.
PRESENCE_WINDOW_SECS = 10 * 60

#: How a request showed the owner is here, as its security log row records it.
PROOF_RECENT_SIGN_IN = "recent_sign_in"
PROOF_LOCAL_SECRET = "local_secret"
PROOF_STARTED_BY = "started_by"
PROOF_AUTH_OFF = "auth_off"
PROOF_IDENTITY = "identity"

#: What each guarded action is called at the start of the refusal's sentence.
ACTION_PAIR_DEVICE = "Pairing a device"
ACTION_ENROLL_DEVICE = "Making a device sign-in code"
ACTION_INTEGRATION_TOKEN = "Creating an integration token"
ACTION_CHANNEL_OWNER = "Pairing a channel’s owner"
ACTION_SET_PASSWORD = "Setting a sign-in password"
ACTION_CHANGE_PASSWORD = "Changing the sign-in password"
ACTION_SIGN_IN_SETTING = "Making sign-in less strict"
ACTION_SIGN_IN_SECRET = "Storing a secret that changes who can sign in"
ACTION_CONFIRM = "Signing in again"


@dataclass(frozen=True)
class Identity:
    """The owner's credential, sent with a request that asks for it: the password, and the
    authenticator code when one is set up."""

    password: str
    code: str = ""


def require_owner_presence(
    request: web.Request, action: str, *, identity: Identity | None = None
) -> web.Response | None:
    """``None`` when *request* shows the owner is here now; otherwise the answer to send instead.

    *action* names what is being done (one of the ``ACTION_*`` phrases). With *identity*, the
    owner's password (and code) is the one proof accepted — the route asks for the credential
    itself, so no session, however recent, stands in for it. Every outcome is written to the
    security log, the refusals included.
    """
    if identity is not None:
        return _check_identity(request, action, identity)
    proof = presence_proof(request)
    if proof:
        _audit(request, action, "granted", proof=proof)
        return None
    return _sign_in_again(request, action)


def presence_proof(request: web.Request) -> str:
    """How *request* shows the owner is here now (a ``PROOF_*``), or ``""`` when it does not."""
    if _auth_is_off(request):
        return PROOF_AUTH_OFF
    if request.get("app"):
        return ""
    if _local_secret_presented(request):
        return PROOF_LOCAL_SECRET
    record = _session_record(request)
    if record is None:
        return ""
    from personalclaw.dashboard.origin import is_loopback
    from personalclaw.dashboard.session_store import ISSUER_READY

    if record.issuer == ISSUER_READY and is_loopback(request.remote or ""):
        return PROOF_STARTED_BY
    signed_in = float(record.device.minted_at or 0.0)
    if signed_in and time.time() - signed_in <= PRESENCE_WINDOW_SECS:
        return PROOF_RECENT_SIGN_IN
    return ""


#: The config section that says how the owner signs in (``config.loader.AuthConfig``).
SIGN_IN_SECTION = "auth"


def loosens_sign_in(path_key: str, spec: Mapping[str, Any], *, current: Any, new: Any) -> bool:
    """Whether writing *new* over *current* at *path_key* makes signing in less strict.

    A field of the sign-in section whose security control the write loosens: password sign-in
    turned on, the authenticator code no longer required, a longer sign-in, a looser lockout.
    The config write asks :func:`require_owner_presence` for those. Tightening one never asks:
    that is the direction a device that may be compromised must always be able to take.
    """
    from personalclaw.config.edit_spec import security_control

    if path_key.split(".", 1)[0] != SIGN_IN_SECTION:
        return False
    control = security_control(spec)
    return control is not None and bool(control.loosens(current, new))


def second_factor_enrolled() -> bool:
    """Whether the owner has an authenticator code set up: the record says so, or a secret is
    stored. Either is enough to ask for the code — one without the other cannot verify one, and
    the check then refuses rather than skip the factor the owner set up."""
    from personalclaw.auth import credentials as creds

    return bool(creds.status().get("totp_enabled")) or bool(creds.totp_secret())


def sign_in_again_sentence(action: str, *, signed_in: float, password: bool) -> str:
    """What a device whose sign-in is too old for *action* is told: why, and how to sign in again.

    *signed_in* is when its device signed in (0 when that was never recorded); *password* is
    whether this gateway offers password sign-in, which is the door the dashboard then opens.
    """
    from personalclaw.auth.lifetimes import duration_words, when_words

    when = f" This device signed in {when_words(signed_in)}." if signed_in else ""
    if password:
        how = "Confirm it’s you by signing in again with your password."
    else:
        how = (
            "Confirm it’s you: run `personalclaw token` on the computer running PersonalClaw, "
            "then open the link it prints on this device."
        )
    window = duration_words(PRESENCE_WINDOW_SECS)
    return f"{action} needs a sign-in from the last {window}.{when} {how}"


# ── the refusals ────────────────────────────────────────────────────────


def _sign_in_again(request: web.Request, action: str) -> web.Response:
    from personalclaw.dashboard.token_auth import _login_offered

    record = _session_record(request)
    signed_in = float(record.device.minted_at or 0.0) if record is not None else 0.0
    password = _login_offered()
    _audit(
        request,
        action,
        "denied",
        error=f"no sign-in from the last {PRESENCE_WINDOW_SECS // 60} minutes",
    )
    response = json_error(
        "fresh_sign_in_required",
        message=sign_in_again_sentence(action, signed_in=signed_in, password=password),
        status=401,
        error_extra={
            "detail": {
                "action": action,
                "window_secs": PRESENCE_WINDOW_SECS,
                "password": password,
                "second_factor": password and second_factor_enrolled(),
            }
        },
    )
    # A question the page asks the owner, so a page that says it asks gets it as an answer, not a
    # failed request in its console (`dashboard.consent_ask`). Every other client keeps the 401.
    response[CONSENT_QUESTION] = True
    return response


def _check_identity(request: web.Request, action: str, identity: Identity) -> web.Response | None:
    """The owner's password, and their code when one is set up, checked now.

    The lockout is the sign-in page's own (per address, in memory), so guessing through a session
    costs exactly what guessing at the page costs. A request that carries no password, or no code
    when one is set up, is answered before anything is checked, so the answer says nothing about
    the password and is not counted as a guess; both are then checked together, and a wrong one
    is refused with one sentence that does not say which.
    """
    from personalclaw.auth import credentials as creds
    from personalclaw.dashboard.handlers import auth as auth_h

    ip = auth_h._client_ip(request)
    remaining = auth_h._lockout_remaining(ip, auth_h._auth_cfg())
    if remaining:
        _audit(request, action, "denied", error=f"locked out, retry_after={remaining}s")
        return json_error("auth_locked_out", status=429, headers={"Retry-After": str(remaining)})
    if not identity.password:
        _audit(request, action, "denied", error="no password")
        return json_error(
            "auth_password_required", message=f"{action} needs your current password.", status=400
        )
    second = second_factor_enrolled()
    if second and not identity.code.strip():
        _audit(request, action, "denied", error="no authenticator code")
        return json_error(
            "auth_totp_required",
            message="Enter the code from your authenticator app too.",
            status=401,
        )
    password_ok = creds.verify_password(str(creds.status()["username"]), identity.password)
    code_ok = _code_verifies(identity.code) if second else True
    if not (password_ok and code_ok):
        auth_h._record_failure(ip)
        _audit(request, action, "denied", error="the password or the code did not verify")
        return json_error(
            "auth_invalid_credentials",
            message=(
                "That password or code isn’t right." if second else "That password isn’t right."
            ),
            status=401,
        )
    auth_h._clear_failures(ip)
    _audit(request, action, "granted", proof=PROOF_IDENTITY)
    return None


def _code_verifies(code: str) -> bool:
    """Whether *code* is the authenticator's code now. No stored secret verifies nothing."""
    from personalclaw.auth import credentials as creds
    from personalclaw.auth import totp

    secret = creds.totp_secret()
    return bool(secret) and bool(totp.verify_code(secret, code.strip()))


# ── what a request carries ──────────────────────────────────────────────


def _auth_is_off(request: web.Request) -> bool:
    """Whether this gateway serves without authentication. A gateway that does not say is not."""
    from personalclaw.dashboard.origin import auth_is_off

    cfg = request.app.get("auth_cfg")
    return cfg is not None and auth_is_off(cfg)


def _local_secret_presented(request: web.Request) -> bool:
    """Whether *request* comes from this computer carrying the local machine secret."""
    from personalclaw.dashboard.origin import is_loopback

    provided = str(request.headers.get("X-Local-Secret", "") or "")
    expected = str(request.app.get("local_secret", "") or "")
    if not provided or not expected or not is_loopback(request.remote or ""):
        return False
    # Bytes, not str: `compare_digest` raises on a non-ASCII str, and the header is anyone's.
    return hmac.compare_digest(expected.encode(), provided.encode())


def _session_record(request: web.Request) -> Any:
    """The stored record of the session that authorized *request*, or ``None``.

    Read from the store, never from the request: what a client presents names a session, and the
    store, written by the gateway alone, says when it signed in and through which door. A session
    the store does not hold (it could not be written) proves nothing.
    """
    nonce = str(request.get("session_nonce") or "")
    if not nonce:
        return None
    from personalclaw.dashboard.session_store import load_session_records

    try:
        return load_session_records().get(nonce)
    except Exception:  # noqa: BLE001 — an unreadable store proves nothing: refuse, and say so
        logger.warning("could not read the session store to check a sign-in", exc_info=True)
        return None


def _audit(
    request: web.Request, action: str, outcome: str, *, proof: str = "", error: str = ""
) -> None:
    """One security log row per check. Never the credential, and never raises."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=str(request.get("user") or request.remote or "unknown"),
            operation="owner_presence",
            outcome=outcome,
            source="auth",
            resources=action,
            error=error,
            **({"metadata": {"proof": proof}} if proof else {}),
        )
    except Exception:  # noqa: BLE001 — the check stands; say the row could not be written
        logger.warning("could not write the owner-presence row to the security log", exc_info=True)
