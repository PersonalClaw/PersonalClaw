"""Staging an app bundle into the home — the one copy of a third-party tree.

An install, an update and an install preview all begin by copying the bundle they were
handed (a git clone, a local folder) into quarantine, and every gate after that — the
signature, the content scan, the digest the owner's consent is bound to — reads the copy.
So the copy decides what "the bundle" is. It used to be ``shutil.copytree``, which FOLLOWS
symbolic links: a bundle shipping ``data/key -> ~/.ssh/id_ed25519`` arrived in the installed
app holding the key's bytes, where the app's own code could read them, while anyone looking
at the source saw only a link. This module never reads through a link.

The policy is an allowlist, checked over the WHOLE tree before a single byte is written
(:func:`survey`), and the copy (:meth:`Survey.copy_to`) writes exactly what was checked:

* **Tooling is not the app.** An entry named in ``supply_chain.NEVER_INSTALLED_NAMES`` —
  ``.git``, ``__pycache__``, a virtualenv — is left out at any depth, and nothing inside it
  is read. So it is not scanned, not in the consent digest and not installed, and a link
  that resolves into one leads to something the app does not contain. Everything else,
  ``node_modules`` included, is the app, and the scan reads all of it.
* **Files, folders and links — nothing else.** A named pipe, a socket or a device file is
  refused: copying one blocks, fails, or reads bytes that are not the bundle's.
* **No hard link out.** A regular file whose inode also has a name outside the bundle IS
  that outside file, so it is refused.
* **A link must lead to one of the bundle's own files.** Its text must be relative, and it
  is resolved the way the kernel will resolve it once installed — through the bundle's own
  links, one component at a time — without climbing above the bundle's top folder for even
  one step, so it means the same thing wherever the bundle is copied to. It must end on a
  regular file the bundle ships: not outside, not nothing, not a loop, and not a folder,
  because the content scan does not descend into a link to a folder and would never read
  what it reaches under that name.
* **``data/`` is the app's own.** It is the one folder a confined app may write, and the
  gateway re-reads the files an app ships — ``app.json``'s grants on every permission check.
  So no link may lead into ``data/`` from outside it, or out of it from inside, and the
  platform's own names — ``data`` and ``installed.json`` — may not be links.

A link that passes is copied AS A LINK, with its text unchanged. Anything else is refused
with a sentence naming the offending path; nothing is written.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from personalclaw.apps.manager import INSTALLED_META_FILENAME
from personalclaw.supply_chain import never_installed

logger = logging.getLogger(__name__)

#: The folder a confined app writes (``apps/manager.app_data_dir``).
_DATA = "data"
#: Top-level names the PLATFORM owns inside an installed app, so a bundle may never ship one
#: as a link. Compared case-insensitively, like every ``data`` test here: macOS's default
#: disk resolves ``Data`` to ``data``.
_PLATFORM_OWNED = frozenset({_DATA, INSTALLED_META_FILENAME.casefold()})
#: Links followed while resolving one before it is called a loop (Linux's MAXSYMLINKS).
_MAX_HOPS = 40
#: How much of an attacker-written link text a refusal quotes.
_QUOTE_LIMIT = 120

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)


class UnsafeBundleError(Exception):
    """The bundle holds an entry staging refuses to copy. ``str()`` is the sentence."""


@dataclass(frozen=True)
class _Entry:
    rel: tuple[str, ...]
    kind: str  # "dir" | "file" | "link"
    mode: int
    atime_ns: int
    mtime_ns: int
    ident: tuple[int, int] = (0, 0)  # (st_dev, st_ino) of a file, as it was checked
    target: str = ""  # a link's text, verbatim


@dataclass(frozen=True)
class Survey:
    """A bundle that passed the policy: every entry in copy order, parents first.

    Only :func:`survey` makes one, so a copy cannot happen without the check."""

    root: Path
    entries: tuple[_Entry, ...]

    def copy_to(self, dest: Path) -> None:
        """Write the surveyed bundle to ``dest``, which must not exist yet.

        Regular files are opened with ``O_NOFOLLOW`` and must still be the inode the survey
        checked, so a source that changed underneath the survey is refused rather than
        copied; links are recreated from their surveyed text and never opened. Modes (minus
        setuid/setgid/sticky) and times are kept, as ``copytree``'s ``copy2`` kept them."""
        os.mkdir(dest, 0o700)
        folders: list[tuple[Path, _Entry]] = []
        for entry in self.entries:
            src = self.root.joinpath(*entry.rel)
            out = dest.joinpath(*entry.rel)
            if entry.kind == "dir":
                if entry.rel:
                    os.mkdir(out, 0o700)
                folders.append((out, entry))
            elif entry.kind == "link":
                try:
                    now = os.readlink(src)
                except OSError:  # no longer a link
                    now = None
                if now != entry.target:
                    raise _changed(entry)
                os.symlink(entry.target, out)
            else:
                _copy_file(src, out, entry)
        # Deepest first, so a read-only folder is closed only once everything in it is written.
        for out, entry in sorted(folders, key=lambda f: len(f[1].rel), reverse=True):
            os.chmod(out, stat.S_IMODE(entry.mode) & 0o777)
            os.utime(out, ns=(entry.atime_ns, entry.mtime_ns))


def survey(src: Path) -> Survey:
    """Check every entry of the bundle at ``src`` against the policy, writing nothing.

    ``src`` itself may be reached through a link (a local folder the owner typed through
    one): the bundle is the folder it names. Raises :class:`UnsafeBundleError` naming the
    first offending entry in path order."""
    root = Path(os.path.realpath(src))
    top = os.lstat(root)
    if not stat.S_ISDIR(top.st_mode):
        raise UnsafeBundleError(f"{str(src)!r} is not a folder.")
    found, left_out = _walk(root)
    if left_out:
        logger.info(
            "staging %s: not installing %s — tooling that is never part of an app",
            root.name,
            ", ".join(_path(rel) for rel in left_out),
        )
    names_per_inode: dict[tuple[int, int], int] = {}
    for _rel, st in found:
        if stat.S_ISREG(st.st_mode):
            key = (st.st_dev, st.st_ino)
            names_per_inode[key] = names_per_inode.get(key, 0) + 1

    entries = [_Entry((), "dir", top.st_mode, top.st_atime_ns, top.st_mtime_ns)]
    for rel, st in found:
        mode = st.st_mode
        if stat.S_ISDIR(mode):
            entries.append(_Entry(rel, "dir", mode, st.st_atime_ns, st.st_mtime_ns))
        elif stat.S_ISREG(mode):
            ident = (st.st_dev, st.st_ino)
            if st.st_nlink > names_per_inode[ident]:
                raise UnsafeBundleError(f"{_path(rel)} is a hard link to a file outside the app.")
            entries.append(_Entry(rel, "file", mode, st.st_atime_ns, st.st_mtime_ns, ident))
        elif stat.S_ISLNK(mode):
            text = os.readlink(root.joinpath(*rel))
            _check_link(root, rel, text)
            entries.append(_Entry(rel, "link", mode, st.st_atime_ns, st.st_mtime_ns, target=text))
        else:
            raise UnsafeBundleError(
                f"{_path(rel)} is {_special(mode)}. An app may hold only files, folders and "
                "links to its own files."
            )
    return Survey(root=root, entries=tuple(entries))


def _walk(
    root: Path,
) -> tuple[list[tuple[tuple[str, ...], os.stat_result]], list[tuple[str, ...]]]:
    """Every entry under ``root``, ``lstat``-ed (a link is the link), in path order — and,
    apart, the entries left out as tooling (:func:`~personalclaw.supply_chain.never_installed`),
    which are neither recorded nor descended into."""
    found: list[tuple[tuple[str, ...], os.stat_result]] = []
    left_out: list[tuple[str, ...]] = []
    pending: list[tuple[str, ...]] = [()]
    while pending:
        folder = pending.pop()
        with os.scandir(root.joinpath(*folder)) as it:
            children = [(e.name, e.stat(follow_symlinks=False)) for e in it]
        for name, st in children:
            rel = (*folder, name)
            if never_installed(name):
                left_out.append(rel)
                continue
            found.append((rel, st))
            if stat.S_ISDIR(st.st_mode):
                pending.append(rel)
    found.sort(key=lambda item: item[0])
    return found, sorted(left_out)


def _check_link(root: Path, rel: tuple[str, ...], text: str) -> None:
    """Refuse the link at ``rel`` unless it leads to one of the bundle's own files.

    Resolution walks the text one component at a time from the folder the link sits in,
    reading the SOURCE tree: a component that is itself a link splices its own text in (as
    the kernel does), and ``..`` climbs to the physical parent. ``via`` is the link whose
    text is being walked and ``shown`` that text, so a refusal names the hop that failed.

    When a link fails more than one rule the refusal names the gravest: leaving the app,
    then crossing the data folder (a link into ``data/`` that leads nowhere yet is exactly
    one the app can fill in later), then leading to nothing, to a folder, or round a loop."""
    if len(rel) == 1 and rel[0].casefold() in _PLATFORM_OWNED:
        whose = (
            "The data folder holds what the app saves and must be a real folder."
            if rel[0].casefold() == _DATA
            else "PersonalClaw writes that file itself."
        )
        raise UnsafeBundleError(f"{_path(rel)} is a link. {whose}")
    in_data = _in_data(rel[:-1])
    here = list(rel[:-1])
    via, shown = rel, text
    crossed: UnsafeBundleError | None = None
    if os.path.isabs(shown):
        raise _refusal("outside", rel, via, shown)
    pending = shown.split("/")
    hops = 1
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not here:
                raise _refusal("outside", rel, via, shown)
            here.pop()
        else:
            if never_installed(part):
                # Present in the source but never installed, so once installed the link
                # names something the app does not contain — which a hook could fill later.
                raise crossed or _refusal("missing", rel, via, shown)
            full = root.joinpath(*here, part)
            try:
                st = os.lstat(full)
            except (FileNotFoundError, NotADirectoryError):
                raise crossed or _refusal("missing", rel, via, shown) from None
            if stat.S_ISLNK(st.st_mode):
                hops += 1
                if hops > _MAX_HOPS:
                    raise crossed or UnsafeBundleError(
                        f"{_path(rel)} is a link that never resolves (a loop)."
                    )
                via, shown = (*here, part), os.readlink(full)
                if os.path.isabs(shown):
                    raise _refusal("outside", rel, via, shown)
                pending = shown.split("/") + pending
                continue
            if pending and not stat.S_ISDIR(st.st_mode):
                raise crossed or _refusal("missing", rel, via, shown)
            here.append(part)
        if crossed is None and _in_data(tuple(here)) != in_data:
            crossed = _refusal("out-of-data" if in_data else "into-data", rel, via, shown)

    if crossed is not None:
        raise crossed
    final = os.lstat(root.joinpath(*here))
    if stat.S_ISDIR(final.st_mode):
        raise _refusal("folder", rel, via, shown)
    if not stat.S_ISREG(final.st_mode):
        raise UnsafeBundleError(f"{_path(rel)} links to {_special(final.st_mode)}.")


#: The refusal sentence per reason. ``{link}`` is the offending entry, ``{through}`` names
#: the chained link whose text failed (empty when it is the entry's own), ``{to}`` that text.
_REFUSALS = {
    "outside": "{link} links outside the app{through} (to {to}). An app may link only to its "
    "own files.",
    "missing": "{link} links{through} to {to}, which the app does not contain.",
    "folder": "{link} links to a folder{through} ({to}). An app may link only to its own files, "
    "not to folders.",
    "into-data": "{link} links into the app's data folder{through} (to {to}). The app writes "
    "that folder, so nothing it ships may come from there.",
    "out-of-data": "{link} links out of the app's data folder{through} (to {to}). A link in the "
    "data folder must stay inside it.",
}


def _refusal(
    reason: str, rel: tuple[str, ...], via: tuple[str, ...], shown: str
) -> UnsafeBundleError:
    through = f" through {_path(via)}" if via != rel else ""
    return UnsafeBundleError(
        _REFUSALS[reason].format(link=_path(rel), through=through, to=_quote(shown))
    )


def _copy_file(src: Path, out: Path, entry: _Entry) -> None:
    try:
        fd = os.open(src, os.O_RDONLY | _NOFOLLOW | _BINARY)
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # it became a link after the survey
            raise _changed(entry) from None
        raise
    with os.fdopen(fd, "rb") as fin:
        st = os.fstat(fin.fileno())
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != entry.ident:
            raise _changed(entry)
        with open(out, "xb") as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
    os.chmod(out, stat.S_IMODE(entry.mode) & 0o777)
    os.utime(out, ns=(entry.atime_ns, entry.mtime_ns))


def _changed(entry: _Entry) -> UnsafeBundleError:
    return UnsafeBundleError(f"{_path(entry.rel)} changed while it was being staged.")


def _in_data(parts: tuple[str, ...]) -> bool:
    return bool(parts) and parts[0].casefold() == _DATA


def _special(mode: int) -> str:
    if stat.S_ISFIFO(mode):
        return "a named pipe"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        return "a device file"
    return "a special file"


def _path(rel: tuple[str, ...]) -> str:
    return repr("/".join(rel))


def _quote(text: str) -> str:
    return repr(text if len(text) <= _QUOTE_LIMIT else text[: _QUOTE_LIMIT - 1] + "…")
