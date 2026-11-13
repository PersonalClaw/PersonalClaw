"""The one form every path and name check compares paths and names in.

A letter can be written as one code point or as a base letter and a combining mark, and a
filesystem may keep or hand back either, so two spellings of one path can differ code point for
code point. The credential guard (``command_paths``, ``security``), the owner-only fence
(``owner_only``) and the delete check (``protected_folders``) each compare in this one form, and
each looks its protected folders up as the filesystem keeps them (:func:`kept_spellings`). It
imports nothing of PersonalClaw's, so any check can read it without taking on what the others
import.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path


def folded(text: str) -> str:
    """*text* in the one form every path and name check compares: decomposed (Unicode NFD), case
    folded, then composed (NFC). A letter can be written as one code point or as a base letter and
    a combining mark, and a filesystem may keep or hand back either, so a check that folded case
    alone could miss a protected place by its other spelling. Decomposed first, since folding the
    case of a composed letter can split off a mark the other spelling keeps in a different place,
    and the two spellings would fold apart."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", text).casefold())


def kept_spellings(path: str) -> list[str]:
    """*path* as the filesystem keeps it, for a check that looks a protected folder up (the
    credential guard's, the owner-only fence's and the delete check's). A filesystem that
    matches names byte for byte finds a folder only by the spelling it was made with: either
    Unicode form, or both in one name. So when *path* is not there as written and the miss is in a
    part that is not ASCII, each folder along it is listed and every entry whose one form
    (:func:`folded`) is the part's is kept: a match too many only protects more. As written when it
    is there, when the parts it misses are ASCII (one spelling each, so an ASCII path costs what it
    did), or when nothing on disk matches."""
    if path.isascii() or not os.path.isabs(path) or os.path.lexists(path):
        return [path]
    parts = Path(path).parts
    last = max(at for at, part in enumerate(parts) if not part.isascii())
    if os.path.lexists(os.path.join(*parts[: last + 1])):
        return [path]
    found = [parts[0]]
    for part in parts[1:]:
        wanted = folded(part)
        kept: list[str] = []
        for here in found:
            there = os.path.join(here, part)
            if os.path.lexists(there):
                kept.append(there)
            elif not part.isascii():
                try:
                    names = os.listdir(here)
                except OSError:
                    continue
                kept += [os.path.join(here, n) for n in names if folded(n) == wanted]
        if not (found := kept):
            return [path]
    return found
