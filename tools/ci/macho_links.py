"""Every Mach-O in a bundle links only the system's libraries and the bundle's own.

The Mac app's backend is a PyInstaller folder, and an Intel Mac may have no Homebrew, no Xcode and
nothing else a build runner had. So each file in it that is a Mach-O (by its magic number) may
link only:

- ``/usr/lib/...`` and ``/System/...``, which every Mac has;
- ``@rpath/...``, ``@loader_path/...`` and ``@executable_path/...`` that resolve, through the file's
  own ``LC_RPATH`` entries where it names any, to a file inside the bundle.

Anything else (``/usr/local/opt/openssl@3/lib/libcrypto.3.dylib``, ``/opt/homebrew/...``, a
build runner's path) is refused, by file and by library. A file's own install name (``otool -D``)
is not a library it links, and is skipped.

    python3 -I tools/ci/macho_links.py <bundle folder> --executable <its main binary>

Exit 0 when every link is the system's or the bundle's, 1 naming each that is not, and 2 when it
cannot read the bundle (no Mach-O at all included). Standard library only, with no relative
imports, and ``otool`` from the Xcode command line tools.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

#: The first four bytes of a Mach-O file, thin (either byte order, 32- or 64-bit) or fat.
_THIN = {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"}
_FAT = {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}
#: Where every Mac has its libraries.
SYSTEM = ("/usr/lib/", "/System/")
_DEPENDENCY = re.compile(r"^\s+(\S.*?) \(compatibility version ")
_RPATH = re.compile(r"^\s+path (.+?) \(offset \d+\)$")


class Unreadable(RuntimeError):
    """A bundle, or a file in it, that this cannot read."""


def is_macho(head: bytes) -> bool:
    """Whether a file that starts with *head* is a Mach-O. A fat file's magic is a Java class
    file's too; a fat file then names how many architectures it holds, a class file its version."""
    if head[:4] in _THIN:
        return True
    return head[:4] in _FAT and len(head) >= 8 and int.from_bytes(head[4:8], "big") < 20


def dependencies(otool_l: str, own_ids: set[str]) -> list[str]:
    """The libraries ``otool -L`` lists, every architecture's, less the file's own install name."""
    found = [m.group(1) for m in map(_DEPENDENCY.match, otool_l.splitlines()) if m]
    return sorted({d for d in found if d not in own_ids})


def install_names(otool_d: str) -> set[str]:
    """A dylib's own install name(s), from ``otool -D``: each line after a ``<file>:`` header."""
    return {
        line.strip() for line in otool_d.splitlines()[1:] if line.strip() and not line.endswith(":")
    }


def rpaths(otool_load_commands: str) -> list[str]:
    """The ``LC_RPATH`` entries ``otool -l`` lists, in order."""
    lines = otool_load_commands.splitlines()
    found: list[str] = []
    for n, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            for after in lines[n + 1 : n + 4]:
                m = _RPATH.match(after)
                if m:
                    found.append(m.group(1))
                    break
    return found


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.is_file() and path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def resolve(dependency: str, file: Path, executable: Path, file_rpaths: list[str]) -> list[Path]:
    """Where the loader would look for *dependency* of *file*: every candidate, in order."""

    def expand(prefix_path: str) -> Path:
        for token, folder in (
            ("@loader_path", file.parent),
            ("@executable_path", executable.parent),
        ):
            if prefix_path == token or prefix_path.startswith(token + "/"):
                return Path(os.path.normpath(str(folder) + prefix_path[len(token) :]))
        return Path(prefix_path)

    if dependency.startswith("@rpath/"):
        rest = dependency[len("@rpath/") :]
        return [expand(r) / rest for r in file_rpaths]
    if dependency.startswith(("@loader_path/", "@executable_path/")):
        return [expand(dependency)]
    return []


def machos(root: Path) -> Iterator[Path]:
    for folder, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                with path.open("rb") as handle:
                    head = handle.read(8)
            except OSError as exc:
                raise Unreadable(f"cannot read {path}: {exc}") from None
            if is_macho(head):
                yield path


def problems(
    root: Path, executable: Path, otool: Callable[[str, Path], str]
) -> tuple[int, list[str]]:
    """How many Mach-O files *root* holds, and each link of theirs that is neither the system's
    nor resolves inside *root*."""
    out: list[str] = []
    files = list(machos(root))
    if not files:
        raise Unreadable(f"{root} holds no Mach-O file")
    for file in files:
        own = install_names(otool("-D", file))
        file_rpaths = rpaths(otool("-l", file))
        for dependency in dependencies(otool("-L", file), own):
            if dependency.startswith(SYSTEM):
                continue
            candidates = resolve(dependency, file, executable, file_rpaths)
            if any(_inside(c, root) for c in candidates):
                continue
            where = "resolves to no file inside the bundle" if candidates else "is outside it"
            out.append(f"{file.relative_to(root)} links {dependency}, which {where}")
    return len(files), out


def _otool(flag: str, path: Path) -> str:
    done = subprocess.run(["otool", flag, str(path)], capture_output=True, text=True)
    if done.returncode != 0:
        raise Unreadable(f"otool {flag} {path} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="macho_links.py")
    parser.add_argument("root", type=Path)
    parser.add_argument("--executable", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        count, found = problems(args.root, args.executable, _otool)
    except Unreadable as exc:
        print(f"macho_links.py: refused: {exc}", file=sys.stderr)
        return 2
    for problem in found:
        print(f"::error::{problem}")
    if found:
        print(f"{len(found)} link(s) in {count} Mach-O file(s) leave {args.root}")
        return 1
    print(f"{count} Mach-O file(s) in {args.root}, each linking only /usr/lib, /System and itself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
