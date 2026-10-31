"""An account's connection health, kept apart from its settings (#3842).

Each account of an app carries its connection's health: ``connected``, ``signing_in``,
``refused`` or ``unreachable``, with the sentence that says why and the time of the last good
contact, and ``unknown`` until a capability has said anything since the gateway started. It is
reported beside the settings and never written into them, so a refused token marks the account
as failing and leaves every capability's configuration as it is.

A capability reports what it saw (``personalclaw.sdk.health``):

- a failure (refused, unreachable) or a sign-in under way is kept as that capability's own, and
  stays until the same capability next succeeds, so another capability's success never hides it;
- a success clears that capability's failure, and is the account's last good contact;
- a connection's health is the worst failure outstanding on it (refused, then unreachable, then
  signing in), else ``connected`` once a capability has succeeded, else ``unknown``.

An account with more than one connection (an app's ``connections``: Email reads over one and
sends over another) has each its own, and the account's health is the worst of them. A success
may carry a short note ("Checked 4 min ago; newest 2 h ago"), shown with the capability's last
good report on its row.

The board is held in memory, behind one lock (reports come from worker threads too), and
nothing of it is stored: a start begins at ``unknown``, so no health is shown that nobody
measured since. A save that changes an account's settings sets its failures back to ``unknown``
("Not checked since the account was changed."), since they were about the settings it had, and
one that changes a capability's own settings does the same for that capability's failure. A
sentence or a note is shown as one line of plain text, with the account's secret values and any
other credential-shaped text masked. An account's connection runtime, where it has one, is
#3843's to report through the same calls.

A report reads no file and no credential, so a capability may make one on the gateway's loop:
the app's manifest is the one its registered capabilities were built from, and an account
removed while the gateway runs is known here (:func:`forget`). The masking reads the account's
secrets, so it is done where the rows are read (:func:`accounts`, :func:`capabilities`, which
``/api/apps`` calls off the loop), once per sentence or note: from then on only the masked text
is held. A save that changes an account's settings drops its capabilities' notes as well as
its failures, and one that changes a capability's own settings drops that capability's note, so
no text outlives a secret it might quote and is then masked against the new one.
"""

from __future__ import annotations

import logging
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.apps.manifest import AppManifest

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"
CONNECTED = "connected"
SIGNING_IN = "signing_in"
UNREACHABLE = "unreachable"
REFUSED = "refused"

#: Every health an account or connection reads, as it is on the wire, worst first.
HEALTHS = (REFUSED, UNREACHABLE, SIGNING_IN, UNKNOWN, CONNECTED)

#: How bad each health is, worst highest: an account reads ``connected`` only when every one of
#: its connections does.
_RANK = {health: len(HEALTHS) - i for i, health in enumerate(HEALTHS)}

#: The longest note a success may carry, and the longest sentence a failure may: one line of
#: plain text each.
NOTE_MAX = 120
SENTENCE_MAX = 200

#: The most of a sentence or a note a report keeps until it is masked. The cut to
#: :data:`SENTENCE_MAX` or :data:`NOTE_MAX` is made on the masked text, whose length masking
#: changes; this only bounds what is held until then.
_RAW_MAX = 4096

_SIGNING_IN_SENTENCE = "Signing in."
CHANGED_SENTENCE = "Not checked since the account was changed."
CAPABILITY_CHANGED_SENTENCE = "Not checked since its settings were changed."

_lock = threading.Lock()


@dataclass(eq=False)
class _Text:
    """A sentence or a note: as the capability gave it until it is first read, and from then on
    only as the owner reads it (:func:`_mask_pending`)."""

    raw: str
    limit: int
    shown: str | None = None


def _text(raw: str, limit: int) -> _Text:
    raw = str(raw)[:_RAW_MAX]
    return _Text(raw, limit) if _one_line(raw, limit) else _Text("", limit, "")


@dataclass(frozen=True)
class _Report:
    state: str
    sentence: _Text
    at: float


#: (app, account, connection) -> each capability's outstanding failure or sign-in there.
_open: dict[tuple[str, str, str], dict[str, _Report]] = {}
#: (app, account, connection) -> the last good contact over it.
_last_ok: dict[tuple[str, str, str], float] = {}
#: (app, account, capability) -> its last good report, and the note it carried.
_last_report: dict[tuple[str, str, str], tuple[float, _Text]] = {}
#: (app, account) -> when a save last changed its settings.
_changed: dict[tuple[str, str], float] = {}
#: (app, account) of each further account removed while the gateway runs: a report about one
#: keeps nothing, and is told so without reading its record.
_gone: set[tuple[str, str]] = set()


def _iso(at: float | None) -> str | None:
    return None if at is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def _one_line(text: str, limit: int) -> str:
    """*text* as one line of plain text, at most *limit* characters. Each run of whitespace is one
    space, and control and format characters go: a bidirectional override among them would show
    a sentence reversed."""
    kept = "".join(
        ch
        for ch in str(text)[: 4 * _RAW_MAX]
        if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    return " ".join(kept.split())[:limit]


def _secret_values(app: str, acct: str, ids: list[str]) -> list[str] | None:
    """The values of the account's own secrets and its capabilities', so a sentence that quotes
    one (an error carrying a key in a URL) never shows it. Read here, off the loop, and never
    kept. ``None`` when they cannot be read: nothing is masked against less than all of them."""
    from personalclaw.config import secret_refs
    from personalclaw.config.credentials import get_credential
    from personalclaw.providers.settings import (
        account_secret_owner,
        capability_secret_owner,
        load_stored_account,
        load_stored_capability,
    )

    account = acct or None
    try:
        parts = [(load_stored_account(app, account), account_secret_owner(app, account))]
        parts += [
            (load_stored_capability(app, cid, account), capability_secret_owner(app, cid, account))
            for cid in ids
        ]
        values = []
        for stored, owner in parts:
            for value in stored.values():
                key = secret_refs.ref_key(value)
                if key is not None and owner.holds(key):
                    held = get_credential(key)
                    if held:
                        values.append(held)
    except (OSError, ValueError):
        logger.debug("health of %s: its secrets could not be read to mask", app, exc_info=True)
        return None
    return values


def _shown(text: str, limit: int, secrets: list[str]) -> str:
    """What the owner reads of a sentence or a note: the account's secret values masked, then any
    other credential-shaped text, on one line of at most *limit* characters."""
    from personalclaw.security import redact_for_display, redact_known_values

    return _one_line(redact_for_display(redact_known_values(str(text), secrets)), limit)


def _manifest(app: str, capability: str | None = None) -> AppManifest:
    """*app*'s manifest: the one its registered capabilities were built from, held in memory, so
    a report reads no file. Read from the app's folder only for an app none of whose capabilities
    is registered, which runs none to report."""
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers.registry import get_provider_registry

    registry = get_provider_registry()
    ext = registry.get(app, capability) or registry.get(app)
    if ext is not None and isinstance(ext.manifest, AppManifest):
        return ext.manifest
    from personalclaw.apps.app_manager import _manifest_of

    manifest = _manifest_of(app)
    if manifest is None:
        raise ValueError(f"{app!r} is not an installed app")
    return manifest


def _checked(
    app: str, capability: str, account: str | None, connection: str | None
) -> tuple[str, str] | None:
    """The account and connection a report is kept under.

    Raises :class:`ValueError` for what the manifest settles: an app that is not installed, a
    capability it does not declare, and a connection it does not declare (or none named where it
    declares them). ``None``, logged, for an account removed while the gateway runs, or a name
    that is no account's: accounts change while the app runs, and a report may race a removal.
    Nothing is read to tell, so a report never waits on a file. No account named is the account
    the capability runs under, which is the first until each account runs its own (#3843):
    adding an account never makes a running capability's report fail."""
    from personalclaw.apps.account_settings import connection_ids
    from personalclaw.providers.accounts import FIRST, checked_account

    manifest = _manifest(app, capability)
    try:
        ids = manifest.capability_ids()
    except ValueError as exc:
        raise ValueError(f"{app!r} has no capabilities this core can tell apart") from exc
    if capability not in ids:
        raise ValueError(f"{app!r} declares no capability {capability!r}")
    declared = connection_ids(manifest)
    if declared and connection not in declared:
        raise ValueError(
            f"{app!r} keeps its account's health per connection: name one of "
            f"{', '.join(declared)} (connection=...)"
        )
    if not declared and connection is not None:
        raise ValueError(f"{app!r} declares no connection {connection!r}: leave connection out")
    if account is None:
        return "", connection or ""
    try:
        acct = "" if account == FIRST else checked_account(account)
    except ValueError:
        logger.debug("health of %s: %r is no account's id; the report is dropped", app, account)
        return None
    with _lock:
        gone = (app, acct) in _gone
    if gone:
        logger.debug("health of %s: no account %r any more; the report is dropped", app, account)
        return None
    return acct, connection or ""


def record(
    app: str,
    capability: str,
    state: str,
    sentence: str = "",
    *,
    account: str | None = None,
    connection: str | None = None,
    note: str = "",
) -> None:
    """Keep what *capability* of *app* just saw of its account's connection: ``connected`` (a
    success, with an optional *note*), ``signing_in``, or a failure (``refused``,
    ``unreachable``), which needs the *sentence* that says why. Raises :class:`ValueError`, and
    keeps nothing, for what the manifest settles (:func:`_checked`); keeps nothing, and says so
    in the debug log, for an account the app no longer has. It reads no file and no credential:
    the sentence and the note are masked when they are first read (:func:`_mask_pending`)."""
    if state not in (CONNECTED, SIGNING_IN, UNREACHABLE, REFUSED):
        raise ValueError(f"no such health: {state!r}")
    if state in (UNREACHABLE, REFUSED) and not _one_line(sentence, SENTENCE_MAX):
        raise ValueError("a failure is reported with the sentence that says why")
    checked = _checked(app, capability, account, connection)
    if checked is None:
        return
    acct, conn = checked
    text = _text(sentence, SENTENCE_MAX)
    if state == SIGNING_IN and text.shown == "":
        text = _Text("", SENTENCE_MAX, _SIGNING_IN_SENTENCE)
    now = time.time()
    where = (app, acct, conn)
    with _lock:
        if state == CONNECTED:
            _open.get(where, {}).pop(capability, None)
            _last_ok[where] = now
            _last_report[(app, acct, capability)] = (now, _text(note, NOTE_MAX))
        else:
            _open.setdefault(where, {})[capability] = _Report(state, text, now)


def account_changed(app: str, account: str | None = None) -> None:
    """A save changed *account*'s settings (the first, with ``None``): what its capabilities
    reported was about the settings it had, so its outstanding failures go, and it reads
    ``unknown`` ("Not checked since the account was changed.") until a capability reports
    again. Its last good contact stays, and each capability's last good report, without its
    note: a note not read yet is masked against the secrets the account has when it is read,
    so one that quoted a secret this save replaced goes with it."""
    from personalclaw.providers.accounts import FIRST

    acct = "" if account in (None, FIRST) else str(account)
    with _lock:
        reported = any(w[:2] == (app, acct) for w in (*_open, *_last_ok))
        for where in list(_open):
            if where[:2] == (app, acct):
                del _open[where]
        for key, (at, _note) in list(_last_report.items()):
            if key[:2] == (app, acct):
                _last_report[key] = (at, _text("", NOTE_MAX))
        # With nothing reported yet it reads unknown anyway, and says nothing about a change.
        if reported:
            _changed[(app, acct)] = time.time()


def capability_changed(app: str, capability: str, account: str | None = None) -> None:
    """A save changed *capability*'s own settings in *account* (the first, with ``None``): a
    failure it reported was about the settings it had, so it reads ``unknown`` ("Not checked
    since its settings were changed.") until that capability reports again. Its last good report
    stays, without its note (as :func:`account_changed` says why), and so does every other
    capability's."""
    from personalclaw.providers.accounts import FIRST

    acct = "" if account in (None, FIRST) else str(account)
    now = time.time()
    changed = _Text("", SENTENCE_MAX, CAPABILITY_CHANGED_SENTENCE)
    with _lock:
        for where, reports in _open.items():
            report = reports.get(capability)
            if where[:2] == (app, acct) and report is not None and report.state != UNKNOWN:
                reports[capability] = _Report(UNKNOWN, changed, now)
        last = _last_report.get((app, acct, capability))
        if last is not None:
            _last_report[(app, acct, capability)] = (last[0], _text("", NOTE_MAX))


def _mask_pending(app: str, acct: str, ids: list[str]) -> None:
    """Mask each sentence and note of *app*'s account *acct* that nobody has read yet: the
    account's secret values and its capabilities' (read here, never kept), then anything else
    shaped like a credential, on one line. Each is masked once, and from then on only its masked
    text is held. The board's lock is not held while the secrets are read, so a report made
    meanwhile never waits on the credential store. With the secrets unreadable, nothing is
    masked and each reads empty until a later read can."""
    with _lock:
        pending = [
            r.sentence for w, rs in _open.items() if w[:2] == (app, acct) for r in rs.values()
        ]
        pending += [t for k, (_at, t) in _last_report.items() if k[:2] == (app, acct)]
        pending = [t for t in pending if t.shown is None]
    if not pending:
        return
    secrets = _secret_values(app, acct, ids)
    if secrets is None:
        return
    masked = [(t, _shown(t.raw, t.limit, secrets)) for t in pending]
    with _lock:
        for t, shown in masked:
            if t.shown is None:
                t.shown, t.raw = shown, ""


def _connection(app: str, acct: str, conn: str, ids: set[str]) -> dict[str, Any]:
    """One connection's health, from the reports of the capabilities the app declares now: one
    renamed, retired or dropped by an update reports nothing any more."""
    reports = [r for cid, r in _open.get((app, acct, conn), {}).items() if cid in ids]
    last_ok = _last_ok.get((app, acct, conn))
    if reports:
        worst = max(reports, key=lambda r: (_RANK[r.state], r.at))
        return {
            "health": worst.state,
            "reason": worst.sentence.shown or "",
            "lastOk": _iso(last_ok),
        }
    changed = _changed.get((app, acct))
    if changed is not None and (last_ok is None or last_ok < changed):
        return {"health": UNKNOWN, "reason": CHANGED_SENTENCE, "lastOk": _iso(last_ok)}
    return {"health": CONNECTED if last_ok else UNKNOWN, "reason": "", "lastOk": _iso(last_ok)}


def accounts(app: str) -> list[dict[str, Any]]:
    """Each account of *app*, the first then each further one, with its health, the sentence,
    and its last good contact; and each connection's, when it has more than one. It reads the
    app's account records, and the secrets a sentence is masked against: call it off the loop."""
    from personalclaw.apps.account_settings import connection_ids
    from personalclaw.providers.accounts import FIRST
    from personalclaw.providers.settings import ProviderSettings

    manifest = _manifest(app)
    try:
        ids = set(manifest.capability_ids())
    except ValueError:
        ids = set()
    declared = [c for c in manifest.connections if isinstance(c, dict)]
    listed = ProviderSettings.list_accounts(app)
    for one in listed:
        _mask_pending(app, "" if one["id"] == FIRST else one["id"], sorted(ids))
    rows = []
    with _lock:
        for one in listed:
            acct = "" if one["id"] == FIRST else one["id"]
            row: dict[str, Any] = {"id": one["id"], "label": one["label"]}
            if not connection_ids(manifest):
                row.update(_connection(app, acct, "", ids))
                rows.append(row)
                continue
            conns = [
                {
                    "id": str(c["id"]),
                    "label": str(c.get("label", "")),
                    **_connection(app, acct, str(c["id"]), ids),
                }
                for c in declared
            ]
            worst = max(conns, key=lambda c: _RANK[c["health"]])
            oks = [c["lastOk"] for c in conns if c["lastOk"]]
            row.update(
                {
                    "health": worst["health"],
                    "reason": worst["reason"],
                    "lastOk": max(oks) if oks else None,
                    "connections": conns,
                }
            )
            rows.append(row)
    return rows


def capabilities(
    app: str, account: str | None = None, *, held: set[str] | None = None
) -> list[dict[str, Any]]:
    """Each capability of *app* in one account (the first, with ``None``): its last good report
    and the note it carried, and the account fields it requires that the account leaves empty.
    *held* is the credential store's key names, when the caller has read them already. It reads
    the account's settings, and the secrets a note is masked against: call it off the loop."""
    from personalclaw.apps.account_settings import missing_account_fields
    from personalclaw.providers.accounts import FIRST

    manifest = _manifest(app)
    try:
        ids = manifest.capability_ids()
    except ValueError:
        return []
    acct = "" if account in (None, FIRST) else str(account)
    _mask_pending(app, acct, ids)
    rows = []
    for cid, provider in zip(ids, manifest.all_providers()):
        with _lock:
            at, note = _last_report.get((app, acct, cid), (None, None))
        rows.append(
            {
                "id": cid,
                "label": provider.label,
                "lastOk": _iso(at),
                "note": (note.shown or "") if note is not None else "",
                "needsSetup": missing_account_fields(app, cid, acct or None, manifest, held=held),
            }
        )
    return rows


def forget(app: str, capability: str | None = None, *, account: str | None = None) -> None:
    """Drop what was reported for *app*: one capability's reports (an update renamed, retired or
    dropped it), one account's (removed: a later report about it keeps nothing), or all of it
    (the app uninstalled). What a capability the app no longer declares reported is never read
    either way."""
    from personalclaw.providers.accounts import FIRST

    acct = None if account is None else ("" if account == FIRST else account)
    with _lock:
        if acct and capability is None:
            _gone.add((app, acct))
        elif acct is None and capability is None:
            _gone.difference_update({g for g in _gone if g[0] == app})
        for where in list(_open):
            if where[0] != app or (acct is not None and where[1] != acct):
                continue
            if capability is None:
                del _open[where]
            else:
                _open[where].pop(capability, None)
        for key in list(_last_report):
            if key[0] == app and (acct is None or key[1] == acct):
                if capability is None or key[2] == capability:
                    del _last_report[key]
        if capability is None:
            for where in list(_last_ok):
                if where[0] == app and (acct is None or where[1] == acct):
                    del _last_ok[where]
            for mark in list(_changed):
                if mark[0] == app and (acct is None or mark[1] == acct):
                    del _changed[mark]


def added(app: str, account: str) -> None:
    """*app* has an account *account* again (added under an id one it removed had): reports about
    it are kept from now on."""
    with _lock:
        _gone.discard((app, account))


def reset() -> None:
    """Forget every report (a test's fresh start)."""
    with _lock:
        _open.clear()
        _last_ok.clear()
        _last_report.clear()
        _changed.clear()
        _gone.clear()
