"""The three folders a delete always asks a person about: the owner's home folder, the filesystem
root and the folder the work runs in (a chat's working folder, the folder a channel's conversation
or a run works in).

A shell command that would delete one of them, a folder that holds one, or everything inside one is
put to a person whatever would otherwise have answered for it: Trust, YOLO, Trust reads, a chat's
grant, an operator's auto-approve pattern, an agent's Always allow, the Approval mode, a loop's or
a run's own grant (:mod:`personalclaw.run_bounds` holds every approval gate to it). With nobody
there to ask, the command is refused, and the run is told which folder and why.

It is decided on the command, never on its text. :mod:`personalclaw.command_effects` reads the
line as its shell would split it and names what each delete removes: ``rm``, ``rmdir`` and
``unlink`` with their options in any order, ``find -delete`` and a ``find`` that runs one of them,
through ``sudo``, ``env``, ``sh -c``, ``eval``, ``xargs`` and a leading ``cd``. Here each removed
path is resolved as the shell and the kernel would resolve it: the home shorthand (``~``,
``~name``) and the home variable (``$HOME``, ``${HOME}``) are expanded, and so is the temporary
folder's; a relative path is read from the folder the command runs in; the current and parent
folders (``.``, ``..``), repeated separators and a trailing slash are normalised; links are
followed; and every comparison ignores letter case, as the default macOS file system does. A glob
removes every path its expansion can be, so ``/*`` deletes a folder holding the home folder, and
``~/*`` everything inside it.

What the reading cannot name it does not guess: a delete of a path built from a variable it does
not know, the paths ``xargs`` hands one, and a line the reader cannot split, or a command a shell
is handed with a variable's value already in it (``sh -c "…$X"``), that names a delete program may
remove anything, and each is put to a person too.

An ordinary delete of any other path follows the normal rules: a recursive delete of a build
folder is no more a question than any other change.

Defence in depth, never the fence: a program can delete what its command line does not name (a
script, an interpreter's own call), which the OS sandbox and the owner-only fences hold.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from personalclaw.command_effects import Removal, command_effects

#: The protected folders, in the order a sentence names them.
ROOT = "root"
HOME = "home"
WORKING = "working"
_ORDER = (ROOT, HOME, WORKING)

#: How a command deletes a protected folder: the folder itself, a folder holding it, or everything
#: inside it.
ITSELF = "itself"
HOLDER = "holder"
CONTENTS = "contents"


@dataclass(frozen=True)
class Hit:
    """One protected folder a command would delete: which one (*kind*), where it is (*folder*),
    and how (*how*). For :data:`HOLDER`, *deleted* is the folder holding it that goes."""

    kind: str
    folder: str
    how: str
    deleted: str = ""


@dataclass(frozen=True)
class ProtectedDelete:
    """What one command would delete of the protected folders; false when nothing.

    ``unread``: a delete in it removes a path this reading cannot name, so it may be any of them."""

    hits: tuple[Hit, ...] = ()
    unread: bool = False

    def __bool__(self) -> bool:
        return bool(self.hits) or self.unread

    @property
    def kinds(self) -> tuple[str, ...]:
        """The protected folders it deletes, in :data:`_ORDER`."""
        return tuple(k for k in _ORDER if any(h.kind == k for h in self.hits))


NOTHING = ProtectedDelete()


def protected_delete(command: str, *, cwd: str, tmpdir: str | None = None) -> ProtectedDelete:
    """What *command* would delete of the protected folders, run in the folder *cwd* (``""``: this
    process's own, the one a command given no folder runs in). *tmpdir* is what its shell has as
    ``TMPDIR`` (``None``: this process's, which the shells PersonalClaw starts inherit)."""
    effects = command_effects(command)
    if not effects.removes and not effects.removes_unread:
        return NOTHING
    folder = cwd or os.getcwd()
    temporary = os.environ.get("TMPDIR", "") if tmpdir is None else tmpdir
    folders = _folders(folder)
    hits: list[Hit] = []
    unread = effects.removes_unread
    for removal in effects.removes:
        found = _resolve(removal, folders, cwd=folder, tmpdir=temporary)
        if found is None:
            unread = True
        else:
            hits.extend(found)
    return ProtectedDelete(_strongest(hits), unread)


# ── The folders ──────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Folder:
    kind: str
    shown: str  # the folder as a sentence names it
    forms: tuple[str, ...]  # each way to spell it, compared case-folded


def _norm(path: str) -> str:
    """*path*, absolute, with ``.``, ``..``, repeated separators and a trailing slash taken out (two
    leading slashes are the root too)."""
    return re.sub(r"^/+", "/", posixpath.normpath(path))


def _forms(path: str) -> tuple[str, ...]:
    """*path* as written and as the kernel resolves it, links followed."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        real = path
    return tuple(dict.fromkeys(_norm(p) for p in (path, real)))


def home_folders() -> tuple[str, ...]:
    """The owner's home folder, every way the shell or the system names it: ``$HOME``, and the
    folder this account's entry in the user database gives, when it is another."""
    found = [os.path.expanduser("~"), str(Path.home())]
    try:
        import pwd

        found.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    return tuple(dict.fromkeys(_norm(p) for p in found if p and p.startswith("/")))


def _folders(cwd: str) -> tuple[_Folder, ...]:
    homes = home_folders()
    return (
        _Folder(ROOT, "/", ("/",)),
        _Folder(HOME, homes[0] if homes else "", tuple(f for h in homes for f in _forms(h))),
        _Folder(WORKING, _norm(cwd), _forms(cwd)),
    )


# ── One removal ──────────────────────────────────────────────────────────────────────────────────


def _expanded(path: str, *, tmpdir: str) -> str | None:
    """*path* with the home shorthand and the home and temporary-folder variables it begins with
    written out; ``None`` for a variable this reading has no value for."""
    if path.startswith("~"):
        head, sep, rest = path.partition("/")
        owner = os.path.expanduser("~") if head == "~" else os.path.expanduser(head)
        if owner == head:
            return path  # no such account: the shell leaves the word as written
        return owner + sep + rest
    for name, value in (("HOME", os.path.expanduser("~")), ("TMPDIR", tmpdir)):
        for spelled in (f"${{{name}}}", f"${name}"):
            if path == spelled or path.startswith(spelled + "/"):
                return value + path[len(spelled) :]
    return None if path.startswith("$") else path


def _resolve(
    removal: Removal, folders: tuple[_Folder, ...], *, cwd: str, tmpdir: str
) -> list[Hit] | None:
    """The protected folders *removal* deletes; ``None`` when where it reaches cannot be told."""
    expanded = _expanded(removal.path, tmpdir=tmpdir)
    if expanded is None:
        return None
    if not expanded:
        return []  # an empty name, which nothing can delete
    absolute = expanded if expanded.startswith("/") else posixpath.join(cwd, expanded)
    if removal.glob_at < 0:
        return _hits_of_path(absolute, folders)
    glob_at = removal.glob_at + len(absolute) - len(removal.path)
    return _hits_of_glob(absolute, glob_at, folders)


def _physical(absolute: str) -> str:
    """Where the entry *absolute* names lies once links are followed: the folder holding it
    resolved, and the entry itself as named, since a delete removes a link rather than what it
    points at, unless a trailing slash or a last ``.`` or ``..`` makes the path the folder."""
    last = absolute.rstrip("/").rsplit("/", 1)[-1]
    if absolute.endswith("/") or last in (".", ".."):
        return _forms(absolute)[-1]
    parent = posixpath.dirname(absolute.rstrip("/")) or "/"
    return _norm(posixpath.join(_forms(parent)[-1], last))


def _hits_of_path(absolute: str, folders: tuple[_Folder, ...]) -> list[Hit]:
    hits: list[Hit] = []
    for deleted in dict.fromkeys((_norm(absolute), _physical(absolute))):
        gone = deleted.casefold()
        for folder in folders:
            for form in folder.forms:
                held = form.casefold()
                if gone == held:
                    hits.append(Hit(folder.kind, folder.shown, ITSELF))
                elif gone == "/" or held.startswith(gone.rstrip("/") + "/"):
                    hits.append(Hit(folder.kind, folder.shown, HOLDER, deleted))
    return hits


def _hits_of_glob(absolute: str, glob_at: int, folders: tuple[_Folder, ...]) -> list[Hit] | None:
    """What a glob deletes: each protected folder, and each folder holding one, that its expansion
    can be, and everything in a protected folder when its last part matches every name there.
    ``None`` when its expansion can climb out of where it is written (a ``..`` after a glob, or a
    glob before its last part that can match ``..``)."""
    parts = _pattern_parts(absolute, glob_at)
    if parts is None:
        return None
    hits: list[Hit] = []
    for folder in folders:
        for form in folder.forms:
            held = _segments(form)
            for depth in range(len(held) + 1):
                if _matches(parts, held[:depth]):
                    deleted = "/" + "/".join(held[:depth])
                    if depth == len(held):
                        hits.append(Hit(folder.kind, folder.shown, ITSELF))
                    else:
                        hits.append(Hit(folder.kind, folder.shown, HOLDER, deleted))
            if parts and _every_name(parts[-1][0]) and _matches(parts[:-1], held):
                hits.append(Hit(folder.kind, folder.shown, CONTENTS))
    return hits


#: One part of a glob, between separators: its text, and whether it is matched as a pattern.
_Part = tuple[str, bool]


def _pattern_parts(absolute: str, glob_at: int) -> list[_Part] | None:
    """*absolute*, whose first glob character is at *glob_at*, as the parts a glob matches: every
    part before that one literal, ``.`` taken out and a literal ``..`` applied. ``None`` when the
    glob can climb out of where it is written: a ``..`` after a pattern, or a pattern before the
    last part that can match ``..`` (one that begins with ``.``)."""
    parts: list[_Part] = []
    start = 0
    for text in absolute.split("/"):
        cut = glob_at - start  # where the glob begins in this part (<= 0: before it)
        start += len(text) + 1
        if not text or text == ".":
            continue
        if text == "..":
            if any(pattern for _, pattern in parts):
                return None
            if parts:
                parts.pop()
            continue
        if cut >= len(text):
            parts.append((text, False))
        else:
            parts.append((_escaped(text[: max(cut, 0)]) + text[max(cut, 0) :], True))
    climbs = [
        text
        for text, pattern in parts[:-1]
        if pattern and text.startswith(".") and fnmatch.fnmatchcase("..", _for_fnmatch(text))
    ]
    return None if climbs else parts


def _escaped(text: str) -> str:
    """*text* as a pattern that matches it literally."""
    return re.sub(r"([*?\[])", r"[\1]", text)


def _for_fnmatch(pattern: str) -> str:
    """A shell pattern in :mod:`fnmatch`'s spelling: ``[^…]`` is ``[!…]``, compared case-folded."""
    return pattern.replace("[^", "[!").casefold()


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _matches(parts: list[_Part], segments: list[str]) -> bool:
    """Whether a glob of *parts* can expand to the path of *segments*: one part per segment, a
    literal one equal to it and a pattern one matching it, where a name that begins with ``.`` is
    matched only by a pattern that begins with one (the shell's rule)."""
    if len(parts) != len(segments):
        return False
    for (text, pattern), segment in zip(parts, segments):
        name = segment.casefold()
        if not pattern:
            if text.casefold() != name:
                return False
            continue
        if name.startswith(".") and not text.startswith("."):
            return False
        if not fnmatch.fnmatchcase(name, _for_fnmatch(text)):
            return False
    return True


def _every_name(pattern: str) -> bool:
    """Whether the last part of a glob matches every name in its folder (``*``, ``?*``), or every
    hidden one (``.*``)."""
    rest = pattern[1:] if pattern.startswith(".") else pattern
    return bool(rest) and set(rest) <= {"*", "?"} and "*" in rest


def _strongest(hits: list[Hit]) -> tuple[Hit, ...]:
    """Each protected folder once, by the most it loses: itself, then a folder holding it, then what
    is in it. The filesystem root gone, it is the one named."""
    rank = {ITSELF: 0, HOLDER: 1, CONTENTS: 2}
    best: dict[str, Hit] = {}
    for hit in hits:
        seen = best.get(hit.kind)
        if seen is None or rank[hit.how] < rank[seen.how]:
            best[hit.kind] = hit
    if (root := best.get(ROOT)) is not None and root.how in (ITSELF, CONTENTS):
        return (root,)  # everything on this computer goes with it
    # A holder that is itself a protected folder is that folder going: it is named as such.
    gone = {h.folder.casefold() for h in best.values() if h.how == ITSELF}
    kept = [h for h in best.values() if not (h.how == HOLDER and h.deleted.casefold() in gone)]
    return tuple(sorted(kept, key=lambda h: _ORDER.index(h.kind)))


# ── What it says ─────────────────────────────────────────────────────────────────────────────────


def _named(hit: Hit) -> str:
    if hit.kind == ROOT:
        return "the filesystem root (/)"
    if hit.kind == HOME:
        return "your home folder"
    return f"the working folder ({hit.folder})"


def deletes_what(found: ProtectedDelete) -> str:
    """What *found* would delete, as the object of "it would delete …": ``your home folder``,
    ``/Users, which holds your home folder``, ``everything in the working folder (/w)``; ``""``
    when it names none of them."""
    clauses: list[str] = []
    holders: dict[str, list[str]] = {}
    for hit in found.hits:
        if hit.how == ITSELF:
            clauses.append(_named(hit))
        elif hit.how == CONTENTS:
            clauses.append(f"everything in {_named(hit)}")
        else:
            holders.setdefault(hit.deleted, []).append(_named(hit))
    for deleted, names in holders.items():
        clauses.append(f"{deleted}, which holds {' and '.join(names)}")
    return ", and ".join(clauses)


#: The folders a delete always asks about, as every sentence names them together.
ALWAYS_ASKS = "your home folder, the filesystem root or the working folder"


def clause(found: ProtectedDelete) -> str:
    """Why *found*'s command is put to a person, as a clause: ``it would delete your home folder``,
    or that it deletes a path that cannot be checked; ``""`` for nothing."""
    what = deletes_what(found)
    if what:
        return f"it would delete {what}"
    if found.unread:
        return (
            "it deletes a path its command does not name, which cannot be checked against your "
            "home folder, the filesystem root and the working folder"
        )
    return ""


def sentence(found: ProtectedDelete) -> str:
    """:func:`clause` as a sentence of its own: ``This would delete your home folder.``"""
    said = clause(found)
    if not said:
        return ""
    return "This" + said[2:] + "." if said.startswith("it ") else said[0].upper() + said[1:] + "."


def refusal(found: ProtectedDelete, *, where: str) -> str:
    """The refusal of a command a path with nobody to ask was about to run (*where*: ``a bash
    action``, ``an app's onInstall hook``), in the words its run records: what it would delete,
    that nobody is here to allow it, and that it did not run."""
    return (
        f"Blocked: {clause(found)}. A delete of {ALWAYS_ASKS} always asks a person, and "
        f"{where} has nobody to ask. It was not run."
    )


__all__ = [
    "ALWAYS_ASKS",
    "CONTENTS",
    "HOLDER",
    "HOME",
    "Hit",
    "ITSELF",
    "NOTHING",
    "ProtectedDelete",
    "ROOT",
    "WORKING",
    "clause",
    "deletes_what",
    "home_folders",
    "protected_delete",
    "refusal",
    "sentence",
]
