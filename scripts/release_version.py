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
    python scripts/release_version.py notes --version 0.3.0-rc.1 --changelog CHANGELOG.md \
        --changelog-url https://github.com/PersonalClaw/PersonalClaw/blob/v0.3.0-rc.1/CHANGELOG.md

``--installed`` (before the command) refuses that stand-in: a gate asking a built ARTIFACT which
version it is must get the artifact's answer, and a broken artifact must fail rather than be
covered by the tree beside it.

``same`` and ``reports`` exit 0 when the versions match and 1, with the reason on stderr, when
they do not. ``notes`` prints the CHANGELOG section of that version, or ``Release <version>.``
when the CHANGELOG has none; a section longer than a GitHub Release can hold is cut on an entry
and ends with a line linking the whole section at ``--changelog-url`` (``release_notes``), and one
whose introduction and leading sections alone do not fit exits 1. Exit 2 means the comparison
could not run at all — no version comparison to load (no package, or one older than it), or bad
arguments — which a caller must never read as a mismatch or a match.
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
_HEADING = re.compile(r"^(?P<line>## \[(?P<version>[^\]\n]+)\][^\n]*)\n", re.MULTILINE)

#: The most a release's notes may hold, in characters. GitHub refuses a release body over 125,000
#: ("body is too long"), and the `notes` job creates the GitHub Release only after PyPI and the
#: images have published, so notes that do not fit leave a published version with no Release for
#: the updater to find. The difference is headroom for however GitHub counts a character.
NOTES_BUDGET = 120_000

#: The sections that stay whole in notes cut to fit: what a release leads with, and what to read
#: before upgrading. Matched in a section's title, whatever else it says.
_WHOLE_SECTIONS = ("highlights", "breaking changes")


def changelog_section(
    text: str, version: str, same_version: Callable[[str, str], bool]
) -> tuple[str, str] | None:
    """The CHANGELOG heading that names *version* and the body under it, or ``None``.

    The heading is the one whose version is the same version as *version* (``[Unreleased]`` is
    no version, so it never matches), and the body runs to the next ``## [`` heading or the end.
    """
    headings = list(_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        if same_version(heading.group("version").strip(), version):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            return heading.group("line"), text[heading.end() : end].strip()
    return None


def heading_anchor(heading: str) -> str:
    """The fragment GitHub gives a Markdown heading: ``## [0.1.3] — 2026-07-30`` is
    ``013--2026-07-30``. Lowercased, every character but a letter, a digit, a space, ``-`` and
    ``_`` dropped, and each space a ``-``; the two dashes are the spaces around the em dash."""
    text = heading.lstrip("#").strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _sections(body: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """A release body's introduction, and each section's heading line and entries in order.

    An entry is its ``- `` line and every line under it up to a blank line or the next entry, so
    no entry can be split however it is written. Entries before the first ``### `` heading form a
    section with no heading (``""``)."""
    intro: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    open_entry = False
    for line in body.split("\n"):
        if line.startswith("### "):
            sections.append((line, []))
            open_entry = False
        elif line.startswith("- "):
            if not sections:
                sections.append(("", []))
            sections[-1][1].append(line)
            open_entry = True
        elif not sections:
            intro.append(line)
        elif line.strip() and open_entry:
            sections[-1][1][-1] += "\n" + line
        else:
            open_entry = False
    return "\n".join(intro).strip(), sections


def _render(intro: str, sections: list[tuple[str, list[str]]], pointer: str) -> str:
    blocks = [intro] if intro else []
    for heading, entries in sections:
        blocks.append("\n\n".join(part for part in (heading, "\n".join(entries)) if part))
    blocks.append(pointer)
    return "\n\n".join(blocks)


def release_notes(body: str, *, full_list: str, budget: int = NOTES_BUDGET) -> str:
    """A release's notes from its CHANGELOG section, *body*, in at most *budget* characters.

    A section that fits is its notes, unchanged. One that does not keeps its introduction and the
    sections it leads with (:data:`_WHOLE_SECTIONS`) whole. The other sections take their entries
    in the CHANGELOG's order, each entry whole, one section after another in turn, so every kind
    of change is shown (a release's security fixes come last in the CHANGELOG, and cutting at one
    point would leave the notes saying nothing of them), until the next entry would not fit. One
    line ends the notes: how many of the release's entries they show, and a link to *full_list*,
    where all of them are. Raises :class:`ValueError` when even the parts kept whole do not fit.
    """
    if len(body) <= budget:
        return body
    intro, sections = _sections(body)
    total = sum(len(entries) for _, entries in sections)

    def whole(heading: str) -> bool:
        return any(name in heading.lower() for name in _WHOLE_SECTIONS)

    def pointer(shown: int) -> str:
        return (
            f"These notes show {shown:,} of the release's {total:,} entries. "
            f"All of them are in [CHANGELOG.md]({full_list})."
        )

    kept = [(heading, list(entries) if whole(heading) else []) for heading, entries in sections]
    # Measured with the pointer at its longest: the count it finally prints can only be shorter.
    size = len(_render(intro, [s for s in kept if s[1]], pointer(total)))
    if size > budget:
        raise ValueError(
            f"the introduction and the sections kept whole are longer than {budget:,} characters"
        )
    turns = [(entries, kept[i][1], heading) for i, (heading, entries) in enumerate(sections)]
    turns = [turn for turn in turns if not whole(turn[2])]
    while any(len(taken) < len(entries) for entries, taken, _ in turns):
        for entries, taken, heading in turns:
            if len(taken) == len(entries):
                continue
            entry = entries[len(taken)]
            # A section's first entry brings its heading and the blank lines around it.
            grow = len(entry) + (1 if taken else len(heading) + 4)
            if size + grow > budget:
                break
            taken.append(entry)
            size += grow
        else:
            continue
        break
    shown = sum(len(entries) for _, entries in kept)
    notes = _render(intro, [s for s in kept if s[1]], pointer(shown))
    if len(notes) > budget:
        raise ValueError(f"the notes measured {len(notes):,} characters, over {budget:,}")
    return notes


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
    notes.add_argument(
        "--changelog-url",
        required=True,
        help="the CHANGELOG's address at the release's tag, which notes cut to fit link",
    )

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
    if section is None:
        print(f"Release {args.version}.")
        return 0
    heading, body = section
    try:
        published = release_notes(body, full_list=f"{args.changelog_url}#{heading_anchor(heading)}")
    except ValueError as exc:
        print(f"the {args.version} notes cannot fit a GitHub Release: {exc}", file=sys.stderr)
        return 1
    print(published)
    return 0


if __name__ == "__main__":
    sys.exit(main())
