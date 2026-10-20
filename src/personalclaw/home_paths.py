"""A path in the user's home, written from ``~``: how the agent is shown one, and how it names one.

The home is the one PersonalClaw runs with: ``HOME`` as the shell reads ``~``
(:func:`os.path.expanduser`), never the account's entry in the user database. A gateway started
with a home of its own calls that folder ``~``, as its shell and its file tools already do.

Written out in full, a path in the home is long, and a model asked to repeat one rewrote it from
memory: told the home was ``/Users/ada/pc/home`` and shown ``~/Notes/w40.md``, it answered
``/Users/ada/Notes/w40.md``, a folder that is not hers, and the link it made opened nothing. From
``~`` the path is short and exact, and everything that reads it (the file tools, the shell, the
file viewer) takes ``~`` as this same folder. A path outside the home is written as it is.
"""

from __future__ import annotations

import functools
import os


def home() -> str:
    """The folder ``~`` names, or ``""`` when no path can be written from it: ``HOME`` unset to
    anything absolute, or the filesystem root, which would write every path from ``~``."""
    text = os.path.expanduser("~")
    if not os.path.isabs(text):
        return ""
    text = os.path.normpath(text)
    return "" if os.path.dirname(text) == text else text


@functools.lru_cache(maxsize=8)
def _spellings(root: str) -> tuple[str, ...]:
    """*root* as written and with its links resolved: a path the file tools resolved
    (:func:`os.path.realpath`) starts with the second."""
    return tuple(dict.fromkeys((root, os.path.realpath(root))))


def from_home(path: str | os.PathLike[str]) -> str:
    """*path* written from ``~`` when it is inside the home, else as it is."""
    text = os.fspath(path)
    root = home()
    if not root or not os.path.isabs(text):
        return text
    for base in _spellings(root):
        if text == base:
            return "~"
        if text.startswith(base + os.sep):
            return "~" + text[len(base) :]
    return text
