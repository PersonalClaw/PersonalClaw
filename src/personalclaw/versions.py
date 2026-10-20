"""Versions: the one reading and ordering of a version, for every check that compares two.

The running core against the ``minPersonalClawVersion`` an app declares
(:mod:`personalclaw.apps.core_version`), a release against the running core (the updater,
:mod:`personalclaw.self_update`), an app's version against the copy a source offers (the
Store, :mod:`personalclaw.apps.catalog`), and the release pipeline's own checks
(``scripts/release_version.py``) all read versions here, by the packaging standard (PEP 440,
through ``packaging``). So one version spelled two ways is one version: a release TAG spells a
candidate ``v0.3.0-rc.1``, and the package built from it reports ``0.3.0rc1``. And a line orders
the way it ships: ``0.3.0.dev1 < 0.3.0a1 < 0.3.0b1 < 0.3.0rc1 < 0.3.0 < 0.3.0.post1``, so a
release candidate is older than its release.

A string the reading cannot read is not a version (:func:`parse_version` answers ``None``), and
every comparison here answers ``False`` for it: never newer, never the same. What that means is
the caller's to say, and each says it where it decides: the app gate refuses the app, the
updater offers no release, and the Store offers no update.
"""

from __future__ import annotations

from packaging.version import Version

#: Longer than any version a release, an app or a build writes. A longer string is not read: it
#: comes from somewhere else (a registry index is text anyone can publish), and a release number
#: past Python's integer-string limit raises out of ``packaging`` instead of failing to parse.
_MAX_LENGTH = 128

#: What every unreadable version orders as in :func:`order_key`, below every readable one.
_UNREADABLE = Version("0")


def parse_version(text: str) -> Version | None:
    """*text* as an orderable version, or ``None`` when it is not one.

    Leading and trailing space and a leading ``v`` are allowed, as the standard allows them, so
    ``v0.3.0-rc.1`` and ``0.3.0rc1`` read as one version.
    """
    if not isinstance(text, str) or len(text) > _MAX_LENGTH:
        return None
    try:
        return Version(text)
    except ValueError:  # InvalidVersion, and a release number too long to be an integer
        return None


def is_newer(candidate: str, current: str) -> bool:
    """True only when *candidate* is a later version than *current*.

    An empty or unreadable side is never newer: a version this cannot order against the other is
    not one to offer.
    """
    new, now = parse_version(candidate), parse_version(current)
    return new is not None and now is not None and new > now


def same_version(a: str, b: str) -> bool:
    """True when *a* and *b* are one version, however each is spelled.

    ``v0.3.0-rc.1`` (a tag) and ``0.3.0rc1`` (what that release reports once installed) are one.
    """
    version = parse_version(a)
    return version is not None and version == parse_version(b)


def order_key(text: str) -> tuple[bool, Version]:
    """A sort key that orders versions as :func:`is_newer` does, every unreadable one below every
    readable one, so the newest of several (``max(..., key=order_key)``) is never a string this
    cannot read while a version it can read is among them."""
    version = parse_version(text)
    return (True, version) if version is not None else (False, _UNREADABLE)
