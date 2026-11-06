"""The update to pair-bound sign-ins: what an older build signed in signs in again.

A sign-in from before browsers were bound to their pair (:mod:`sign_in_pair`) is a cookie that
works alone, which is what the update exists to end. Every row this build mints says so
(``session_store.PAIRING_ROW_FIELD``), and an older build drops that field when it rewrites the
store. So at every start, while the gateway holds the home's claim and before it binds
(``gateway_base.claim_home``), this looks at the live rows without the field, and ends every one
it doesn't spare:

* **spared** (:func:`spared`): an app's scoped token, and a link or token never used, or whose
  first client was a script, which is what the CLI, a script and a harness hold. Each of them is
  presented as a Bearer, which this update doesn't change. An older build named a browser only
  when its agent read as one, so a used token ends unless its first client read as a script;
* **ended**, as ``END_UPDATED``: everything else. That's every password, device-code and pairing
  sign-in, every link a browser opened, and every row from before the door was recorded. Phones and
  paired devices are ended too, with their pushes and relay registrations.

The rows decide, not the version stamp: after a rollback to a build from before this, and the
upgrade back, everything that build signed in, or rewrote, is ended; a rollback between two builds
that both write the field ends nothing. It runs before the listener binds, so no request sees a
half-updated store. It fails closed: when the store can't be read, when a fresh read still finds a
row it should have ended, or when anything here fails, the start stops with a sentence that names
the store, and the next start tries again. A store that reads but isn't one loads as empty, as
everywhere else: nothing in it can be used, and the next save writes a store over it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from personalclaw.dashboard.session_store import ISSUER_APP, POOL_TOKEN, SessionRecord, pool_of

logger = logging.getLogger(__name__)


def spared(record: SessionRecord) -> bool:
    """Whether the update leaves the older row *record* signed in: an app's token, and a link or
    token never used, or whose first client was a script. An older build named a browser only
    when its agent read as one, so a used token ends unless its first client read as a script.
    One predicate, for the run, its check and its count."""
    if record.issuer == ISSUER_APP:
        return True
    unused = not record.device.last_seen
    return pool_of(record) == POOL_TOKEN and (record.device.kind == "cli" or unused)


def older(record: SessionRecord) -> bool:
    """Whether *record* was minted, or last rewritten, by a build whose sign-ins needed no pair."""
    return not record.pairing


def pair_sign_ins(home: Path) -> None:
    """End every live row :func:`older` finds and :func:`spared` doesn't spare, then check the
    store says so. A store whose rows all carry the field ends nothing.

    Raises ``gateway_base.HomeNotClaimed`` when it can't, naming the session store.
    """
    from personalclaw import gateway_base
    from personalclaw.dashboard import session_store, token_auth

    store = session_store.sessions_path()
    try:
        # Strict: a store that can't be read may still hold an older build's sign-ins, which
        # this start would leave live. One that reads but isn't a store loads as empty.
        records = session_store.load_session_records(strict=True)
        doomed = [n for n, r in records.items() if older(r) and not spared(r)]
        ended = session_store.end_sessions(doomed, session_store.END_UPDATED) if doomed else []
        again = session_store.load_session_records(strict=True)
        left = [n for n, r in again.items() if older(r) and not spared(r)]
    except OSError as exc:
        raise gateway_base.HomeNotClaimed(_not_updated(store, "it couldn't be read")) from exc
    except Exception as exc:  # noqa: BLE001 — any failure stops the start, which says why
        raise gateway_base.HomeNotClaimed(_not_updated(store, str(exc))) from exc
    if left:
        raise gateway_base.HomeNotClaimed(
            _not_updated(store, f"{len(left)} sign-in(s) were still there after it")
        )
    if ended:
        # A home nothing was signed in to is left exactly as it was: nothing to record.
        token_auth._signed_out(ended, session_store.END_UPDATED, actor="update")
        _record(len(ended), sum(1 for r in records.values() if older(r) and spared(r)))


def _not_updated(store: Path, why: str) -> str:
    return (
        f"PersonalClaw did not start: it couldn't end the sign-ins an older PersonalClaw may have "
        f"made, in its session store {store} ({why}). Nothing was changed that a start won't "
        "finish: start PersonalClaw again, and if this repeats, move that file aside, which signs "
        "every device out."
    )


def _record(ended: int, spared_count: int) -> None:
    """One security-log row for the update: how many sign-ins it ended and how many it spared."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="update",
            operation="sign_ins_paired",
            outcome="ok",
            source="sign_in_update",
            resources=f"ended={ended} spared={spared_count}",
        )
    except Exception:  # noqa: BLE001 — the update stands; say it couldn't be recorded
        logger.warning("could not record the sign-in update in the SEL", exc_info=True)
