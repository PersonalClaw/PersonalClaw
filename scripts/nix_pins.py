#!/usr/bin/env python3
"""Write, or check, the uv.lock entries the Nix channel builds from wheels: ``nix/pins.toml``.

The flake packages a published wheel and takes its dependencies from nixpkgs, which supplies
most of them at a version that wheel accepts and a few at none: the release asks for
``croniter<3`` where nixpkgs carries 6.x. Those few are built from the wheels the release's own
``uv.lock`` pins (``nix/python.nix``), and ``nix/pins.toml`` holds their ``[[package]]``
entries, copied verbatim from ``uv.lock`` at the tag of the release ``nix/personalclaw.nix``
packages. So for each of them the Nix channel installs the version and the very file that
``uv sync --locked`` installs, and a release moves them by moving the lock:

    python3 scripts/nix_pins.py           # rewrite nix/pins.toml from the packaged release's lock
    python3 scripts/nix_pins.py --check   # exit 1, showing the difference, if it is anything else

The lock is read with ``git show v<version>:uv.lock``, so the tag has to be in this clone
(``git fetch origin tag v<version>``). Exit 2 means the run could not read what it compares.
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RECIPE = REPO / "nix" / "personalclaw.nix"
PINS_FILE = REPO / "nix" / "pins.toml"

#: What nixpkgs, at the revision flake.lock pins, cannot supply at a version the release
#: accepts, and why. A new one shows up as a red `next release` step in the `nix` workflow,
#: which builds the checkout's own wheel against the checkout's own lock.
PINS = {
    "certifi": (
        "the release's floor is certifi>=2026.10.19, the bundle that drops the root certificates "
        "whose intermediate keys were compromised, and nixpkgs carries 2026.1.4."
    ),
    "croniter": (
        "the release asks for croniter>=2.0,<3, the cron grammar its expression corpus was "
        "measured against, and nixpkgs carries 6.x."
    ),
    "sqlite-vec": (
        "nixpkgs builds the binding from the extension's C sources and its metadata says "
        "0.0.0, which the release's sqlite-vec>=0.1,<1 refuses."
    ),
    "tree-sitter-language-pack": (
        "the release needs 1.15.7 or later, the first that reads the setting that keeps its "
        "grammars in PersonalClaw's home, and nixpkgs carries 1.4.1, a Rust build that "
        "bundles its grammars in the store."
    ),
    "pillow": (
        "the release asks for Pillow>=13,<14, the first release that fixes the JPEG decoder's "
        "heap overflow and eight other image advisories, and nixpkgs carries 12.3.0."
    ),
    "urllib3": "the release's floor is urllib3>=2.8.0, and nixpkgs carries 2.7.0.",
}

HEADER = """\
# uv.lock's entries at v{version}, the release nix/personalclaw.nix packages, for the packages
# the Nix channel builds from the lock's own wheels instead of taking from nixpkgs (see
# nix/python.nix). Written by scripts/nix_pins.py and checked against that tag by CI: to pin
# another package, add it to PINS there and run `python3 scripts/nix_pins.py`.
"""


class Unreadable(Exception):
    """What the comparison needs could not be read; exit 2, never a pass."""


def recipe_version(text: str) -> str:
    """The one ``version = "X.Y.Z";`` in ``nix/personalclaw.nix``."""
    found = re.findall(r'^\s*version = "([^"]+)";\s*$', text, flags=re.MULTILINE)
    if len(found) != 1:
        raise Unreadable(f"expected one version in {RECIPE.name}, found {found}")
    return found[0]


def entries(lock_text: str) -> dict[str, list[str]]:
    """Every ``[[package]]`` entry of *lock_text*, verbatim, by package name."""
    found: dict[str, list[str]] = {}
    for chunk in re.split(r"(?m)^\[\[package\]\]\n", lock_text)[1:]:
        body = chunk.rstrip("\n")
        name = re.match(r'name = "([^"]+)"\n', body)
        if name is None:
            raise Unreadable(f"a [[package]] entry that does not open with its name: {body[:80]!r}")
        found.setdefault(name.group(1), []).append(f"[[package]]\n{body}\n")
    return found


def render(lock_text: str, version: str) -> str:
    """``nix/pins.toml`` for the release *version*, whose lock is *lock_text*."""
    found = entries(lock_text)
    parts = [HEADER.format(version=version)]
    for name, reason in sorted(PINS.items()):
        held = found.get(name, [])
        if len(held) != 1:
            raise Unreadable(
                f"uv.lock at v{version} has {len(held)} entries for {name}, and a pin is one"
            )
        comment = textwrap.fill(
            f"{name}: {reason}", width=100, initial_indent="# ", subsequent_indent="# "
        )
        parts.append(f"{comment}\n{held[0]}")
    return "\n".join(parts)


def release_lock(version: str) -> str:
    shown = subprocess.run(
        ["git", "show", f"v{version}:uv.lock"], cwd=REPO, capture_output=True, text=True
    )
    if shown.returncode != 0:
        raise Unreadable(
            f"cannot read uv.lock at v{version} ({shown.stderr.strip()}); "
            f"fetch the tag first: git fetch origin tag v{version}"
        )
    return shown.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="compare nix/pins.toml with the lock, write nothing"
    )
    parser.add_argument(
        "--lock", type=Path, help="read the lock from this file instead of the release's tag"
    )
    args = parser.parse_args(argv)
    try:
        version = recipe_version(RECIPE.read_text(encoding="utf-8"))
        lock = args.lock.read_text(encoding="utf-8") if args.lock else release_lock(version)
        expected = render(lock, version)
    except (Unreadable, OSError) as exc:
        print(f"nix_pins: {exc}", file=sys.stderr)
        return 2
    source = str(args.lock) if args.lock else f"uv.lock at v{version}"
    if not args.check:
        PINS_FILE.write_text(expected, encoding="utf-8")
        print(f"wrote nix/pins.toml from {source}: {', '.join(sorted(PINS))}")
        return 0
    current = PINS_FILE.read_text(encoding="utf-8") if PINS_FILE.exists() else ""
    if current == expected:
        print(f"nix/pins.toml is {source}, entry for entry: {', '.join(sorted(PINS))}")
        return 0
    sys.stderr.writelines(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            "nix/pins.toml",
            source,
        )
    )
    print(
        f"nix_pins: nix/pins.toml is not {source}; run `python3 scripts/nix_pins.py`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
