"""SDK: a capability reports how its account's connection is doing (#3842).

The owner sees each account's health beside its settings, never in them: connected, signing in,
refused or unreachable, with the sentence that says why and the time of the last good contact.
A capability reports what it saw when it talked to its service::

    from personalclaw.sdk import health

    health.report_ok("my-mail", "inbox", note="Checked 4 min ago; newest 2 h ago")
    health.report_refused("my-mail", "inbox", "The mail server refused the token.")
    health.report_unreachable("my-mail", "search", "The search server did not answer.")

* A failure stays until the SAME capability next reports a success, so report a success every
  time one happens, not only after a failure.
* The sentence is shown to the owner: say what failed and what to do about it. It is kept as
  one line of plain text, cut at 200 characters, with the account's secret values and anything
  else shaped like a credential masked, so ``str(exc)`` carrying a key in a URL shows no key.
* A note is kept the same way, cut at 120 characters.
* A report reads no file and no credential, so it may be made on the gateway's loop: the masking
  is done where the owner's view is read.
* ``account`` names the account the report is about: ``"default"`` for the first, or a further
  account's id. Left out, it is the account the capability runs under, which is the first for
  now: adding an account never makes a report fail.
* ``connection`` names one of the app's declared ``connections``: required when it declares
  them, refused when it does not.

Each call raises :class:`ValueError`, and records nothing, for what the manifest settles: a
capability or connection the installed app does not declare. A report about an account the app
no longer has records nothing, since accounts change while the app runs. Nothing is stored:
after a restart an account reads ``unknown`` until a capability reports again, and so does an
account whose settings a save changed, or a failing capability whose own settings it changed.

The health an account or connection reads on ``/api/apps`` is one of :data:`HEALTHS`.
"""

from __future__ import annotations

from personalclaw.apps import account_health
from personalclaw.apps.account_health import (  # noqa: F401
    CONNECTED,
    HEALTHS,
    REFUSED,
    SIGNING_IN,
    UNKNOWN,
    UNREACHABLE,
)


def report_ok(
    app: str,
    capability: str,
    *,
    account: str | None = None,
    connection: str | None = None,
    note: str = "",
) -> None:
    """*capability* reached its service and was let in: it clears that capability's failure, and
    is the account's last good contact. *note* is shown on the capability's row."""
    account_health.record(
        app, capability, account_health.CONNECTED, account=account, connection=connection, note=note
    )


def report_refused(
    app: str,
    capability: str,
    sentence: str,
    *,
    account: str | None = None,
    connection: str | None = None,
) -> None:
    """The service refused *capability*'s credentials: the account reads ``refused``, with
    *sentence*, until this capability next succeeds."""
    account_health.record(
        app,
        capability,
        account_health.REFUSED,
        sentence,
        account=account,
        connection=connection,
    )


def report_unreachable(
    app: str,
    capability: str,
    sentence: str,
    *,
    account: str | None = None,
    connection: str | None = None,
) -> None:
    """*capability* could not reach its service: the account reads ``unreachable``, with
    *sentence*, until this capability next succeeds."""
    account_health.record(
        app,
        capability,
        account_health.UNREACHABLE,
        sentence,
        account=account,
        connection=connection,
    )


def report_signing_in(
    app: str,
    capability: str,
    sentence: str = "",
    *,
    account: str | None = None,
    connection: str | None = None,
) -> None:
    """*capability* is signing in to its service: the account reads ``signing_in`` until this
    capability next reports."""
    account_health.record(
        app,
        capability,
        account_health.SIGNING_IN,
        sentence,
        account=account,
        connection=connection,
    )


__all__ = [
    "CONNECTED",
    "HEALTHS",
    "REFUSED",
    "SIGNING_IN",
    "UNKNOWN",
    "UNREACHABLE",
    "report_ok",
    "report_refused",
    "report_signing_in",
    "report_unreachable",
]
