"""The release pipeline's version comparisons, made with PersonalClaw's own.

One version reaches a release spelled more than one way. The tag spells a release candidate
``v0.3.0-rc.1``; the package built from it reports ``0.3.0rc1``, because the build normalizes
the spelling; and the CHANGELOG heading and the README banner carry whatever the release cut
wrote. A check that compared them as text failed on the first release candidate — the image
smoke expected ``personalclaw 0.3.0-rc.1`` from a binary that prints ``personalclaw 0.3.0rc1``
— and read a heading spelled differently from the tag as no heading at all.

So every check here that asks "is this the version being released?" compares VERSIONS, through
``personalclaw.versions``: the one comparison the product uses wherever it compares two versions
(the updater deciding what is newer among them), which keeps the pipeline and the product from
disagreeing about which release a string names.

Run it with an interpreter that can import that comparison: the image's own, a scratch venv the
wheel was installed into, or any Python with ``packaging`` beside a checkout, whose ``src/``
stands in when the package is not installed (the release notes are resolved that way, under
``uv run --with packaging``)::

    python scripts/release_version.py same 0.3.0rc1 0.3.0-rc.1
    python scripts/release_version.py reports --expect 0.3.0-rc.1 --output "personalclaw 0.3.0rc1"
    python scripts/release_version.py notes --version 0.3.0-rc.1 --changelog CHANGELOG.md

``--installed`` (before the command) refuses that stand-in: a gate asking a built ARTIFACT which
version it is must get the artifact's answer, and a broken artifact must fail rather than be
covered by the tree beside it.

``same`` and ``reports`` exit 0 when the versions match and 1, with the reason on stderr, when
they do not. ``notes`` prints the CHANGELOG section of that version, or ``Release <version>.``
when the CHANGELOG has none. Exit 2 means the comparison could not run at all — no version
comparison to load (no package, or one older than it), or bad arguments — which a caller must
never read as a mismatch or a match.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

#: Set by ``--installed``: only an installed package may answer.
_INSTALLED_ONLY = False


def _versions() -> ModuleType:
    """``personalclaw.versions`` — installed, or from the checkout this script sits in."""
    try:
        from personalclaw import versions
    except ImportError:
        # Piped in on stdin (`python - …`) there is no file to find a checkout from.
        here = globals().get("__file__")
        if _INSTALLED_ONLY or not here:
            raise
        sys.path.insert(0, str(Path(here).resolve().parents[1] / "src"))
        from personalclaw import versions
    return versions


def reported_version(output: str, program: str = "personalclaw") -> str:
    """The version a ``<program> --version`` banner reports, or ``""`` when it names none.

    The last line that starts with the program's name wins: the smoke captures stderr too, so a
    warning printed before the banner must not be read as the version.
    """
    for line in reversed(output.splitlines()):
        words = line.split()
        if len(words) >= 2 and words[0] == program:
            return words[1]
    return ""


#: A release heading: ``## [<version>]``, with whatever follows it on the line.
_HEADING = re.compile(r"^## \[(?P<version>[^\]\n]+)\][^\n]*\n", re.MULTILINE)


def changelog_section(
    text: str, version: str, same_version: Callable[[str, str], bool]
) -> str | None:
    """The body under the CHANGELOG heading that names *version*, or ``None``.

    The heading is the one whose version is the same version as *version* (``[Unreleased]`` is
    no version, so it never matches), and the body runs to the next ``## [`` heading or the end.
    """
    headings = list(_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        if same_version(heading.group("version").strip(), version):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            return text[heading.end() : end].strip()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--installed",
        action="store_true",
        help="answer with the installed package only, never a checkout's src/",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    same = commands.add_parser("same", help="exit 0 when A and B are one version")
    same.add_argument("a")
    same.add_argument("b")

    reports = commands.add_parser(
        "reports", help="exit 0 when a --version banner reports the expected version"
    )
    reports.add_argument("--expect", required=True, help="the version being released")
    reports.add_argument("--output", required=True, help="what `personalclaw --version` printed")

    notes = commands.add_parser("notes", help="print the CHANGELOG section of a version")
    notes.add_argument("--version", required=True)
    notes.add_argument("--changelog", default="CHANGELOG.md")

    args = parser.parse_args(argv)
    global _INSTALLED_ONLY
    _INSTALLED_ONLY = args.installed
    try:
        same_version = _versions().same_version
    except Exception as exc:
        # A package that predates the comparison, or fails as it loads, is a comparison that
        # did not run. Letting it traceback would exit 1, which reads as a mismatch.
        print(f"cannot load PersonalClaw's version comparison: {exc}", file=sys.stderr)
        return 2
    if args.command == "same":
        if same_version(args.a, args.b):
            return 0
        print(f"{args.a!r} and {args.b!r} are not the same version", file=sys.stderr)
        return 1
    if args.command == "reports":
        found = reported_version(args.output)
        if found and same_version(found, args.expect):
            return 0
        print(
            f"expected version {args.expect}, but the banner reports "
            f"{found or 'no version'}: {args.output.strip()!r}",
            file=sys.stderr,
        )
        return 1
    text = Path(args.changelog).read_text(encoding="utf-8")
    section = changelog_section(text, args.version, same_version)
    print(section if section is not None else f"Release {args.version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
