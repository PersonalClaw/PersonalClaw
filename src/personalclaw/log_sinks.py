"""Where the gateway's log is shown, and the one rule for which records it shows.

Four sinks show it: the console (the stream a service manager keeps as the gateway's log:
launchd's file, the systemd journal), ``gateway.log`` in the home (what ``personalclaw logs``
prints for a gateway that is not a service), and Settings › Diagnostics' buffer and live stream.
Each is attached here, to the root logger, with this module's one filter, so all four show the
same lines:

* PersonalClaw's own records at the level the owner chose (:func:`set_level`): every
  ``personalclaw`` logger's, and every record logged from the code of an app loaded in this
  process, whatever its logger is called;
* any other library's records at WARNING or above (or the chosen level, when that is higher).

An app's records are its by where they were logged, not by a name it has to declare: a call site
under a directory its code was loaded from is the app's (:func:`personalclaw.app_code.loaded_app`).
So an app is shown at the owner's level from the moment its code loads (at startup, an install,
an enable, an update) and is held to WARNING, like any library, once it is unloaded, with nothing
attached per app and nothing left to detach.

The root logger carries the chosen level, so an app's logger, which names no level of its own, is
enabled down to it; the filter is what keeps every other library at WARNING.
"""

from __future__ import annotations

import logging

from personalclaw import app_code

#: The logger every PersonalClaw module logs under.
PRODUCT_LOGGER = "personalclaw"

_level = logging.WARNING


def level() -> int:
    """The level PersonalClaw's own records are shown at."""
    return _level


def set_level(new_level: int) -> None:
    """Show PersonalClaw's own records, and its loaded apps', from *new_level* up, live."""
    global _level  # noqa: PLW0603 — the one level every sink reads
    _level = new_level
    logging.getLogger().setLevel(new_level)


def is_product(record: logging.LogRecord) -> bool:
    """Whether *record* is PersonalClaw's: a ``personalclaw`` logger's, or a loaded app's code's."""
    name = record.name
    if name == PRODUCT_LOGGER or name.startswith(PRODUCT_LOGGER + "."):
        return True
    return app_code.loaded_app(record.pathname) is not None


def shown(record: logging.LogRecord) -> bool:
    """Whether the sinks show *record*: the rule this module's docstring states."""
    if record.levelno < _level:
        return False
    return record.levelno >= logging.WARNING or is_product(record)


class _Shown(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return shown(record)


_FILTER = _Shown()


def attach(handler: logging.Handler) -> None:
    """Make *handler* one of the sinks: it is given every record :func:`shown` admits, from any
    logger. Attaching one already attached changes nothing."""
    handler.addFilter(_FILTER)
    logging.getLogger().addHandler(handler)


def detach(handler: logging.Handler) -> None:
    """Stop giving *handler* records."""
    logging.getLogger().removeHandler(handler)
