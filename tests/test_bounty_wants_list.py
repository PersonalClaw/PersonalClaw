"""The app bounty wants-list is executable, not prose (ECOSYSTEM-TOOLING ET-7 input).

``docs/maintainers/app-bounty-wants-list.md`` is the single list two plans consume:
``ET-7`` files one ``bounty`` issue per row, and ``CE-9`` files the three channel rows.
A wants-list is exactly the kind of doc that rots silently — it names provider types,
scaffold commands and file paths, and every one of those can go stale while the table
still *reads* fine. A contributor who picks up a rotted bounty burns an evening
discovering core cannot install what the issue asked for.

So the four things a bounty issue would carry over into the public are asserted here:

1. **Every row's type is a real ``PROVIDER_TYPES`` member.** A row naming a type core
   does not have sends a contributor to build an uninstallable app.
2. **Every row's type actually scaffolds.** The list's central promise is "there is no
   registration step to wait on" — asserted by resolving each type through the real
   ``cli_app_new`` type table, the same table ``personalclaw app new --list-types``
   prints.
3. **At least six rows, spanning all three families.** ``ET-7``'s ``done_when`` bar is
   "≥6 ``bounty`` issues (channels + providers + sources)"; a list that silently fell
   to five, or lost a family, would fail that atom at posting time rather than here.
4. **The three channel rows agree with the drafted issue prose** in
   ``community-bounty-drafts.md``. Those two files are the list and the issue text for
   the same three bounties; if they disagree, one of them is lying.

Cited in-tree paths are checked too — the docs-lint ratchet catches a dead *link*, but
these are the paths a contributor is told to read.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.apps.manifest import PROVIDER_TYPES
from personalclaw.cli_app_new import provider_type_rows, provider_types

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WANTS_LIST = _REPO_ROOT / "docs" / "maintainers" / "app-bounty-wants-list.md"
_DRAFTS = _REPO_ROOT / "docs" / "maintainers" / "community-bounty-drafts.md"

#: ``ET-7``'s literal bar: "≥6 `bounty` GitHub issues live (channels + providers +
#: sources from the wants-list)".
_MIN_ROWS = 6
_FAMILIES = frozenset({"channel", "provider", "source"})

#: The three channels CHANNEL-EXPANSION T7.3 names. Not derived from the table under
#: test — the point is to pin the plan's list against both files independently.
_T73_CHANNELS = ("WhatsApp", "Signal", "Matrix")


class Row:
    """One parsed wants-list row."""

    def __init__(self, cells: list[str]) -> None:
        self.number, self.app, self.type, self.family, self.ground, self.why = cells

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return f"Row({self.number}: {self.app!r} type={self.type!r})"


def _unwrap_code(cell: str) -> str:
    """```channel``` -> ``channel``; a bare cell passes through unchanged."""
    match = re.fullmatch(r"`([^`]+)`", cell.strip())
    return match.group(1) if match else cell.strip()


def _rows(text: str) -> list[Row]:
    """The six-column ``| # | Wanted app | Type | Family | Ground | Why |`` table body.

    Parsed positionally off the header rather than by a fixed line range so reordering
    or documenting around the table does not break the test.
    """
    lines = text.splitlines()
    header = next(
        (i for i, line in enumerate(lines) if line.startswith("| # | Wanted app |")),
        None,
    )
    assert header is not None, (
        f"{_WANTS_LIST.name}: no '| # | Wanted app |' table header found — the wants-list "
        "table is the machine-readable half of this doc and must keep that shape"
    )
    out: list[Row] = []
    for line in lines[header + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 6, f"{_WANTS_LIST.name}: expected 6 columns, got {cells}"
        out.append(Row(cells))
    return out


@pytest.fixture(scope="module")
def rows() -> list[Row]:
    assert _WANTS_LIST.is_file(), f"missing {_WANTS_LIST}"
    return _rows(_WANTS_LIST.read_text(encoding="utf-8"))


def test_every_wanted_type_is_a_real_provider_type(rows: list[Row]) -> None:
    """A row naming a type outside ``PROVIDER_TYPES`` asks for an uninstallable app."""
    bad = {
        r.number: _unwrap_code(r.type) for r in rows if _unwrap_code(r.type) not in PROVIDER_TYPES
    }
    assert not bad, (
        f"wants-list row(s) name provider types core does not have: {bad}. "
        f"Known types: {sorted(PROVIDER_TYPES)}"
    )


def test_every_wanted_type_scaffolds_today(rows: list[Row]) -> None:
    """The list promises "no registration step to wait on" — hold it to that.

    Resolved through ``cli_app_new``'s runtime type table (the one
    ``app new --list-types`` prints), not through ``PROVIDER_TYPES`` again: a type can
    be a valid manifest value while the generator cannot emit it, and that gap is
    precisely what would strand a contributor.
    """
    scaffoldable = set(provider_types())
    missing = sorted({_unwrap_code(r.type) for r in rows} - scaffoldable)
    assert not missing, (
        f"wants-list names type(s) `personalclaw app new` cannot scaffold: {missing}. "
        "Either the type table regressed or the row is aspirational."
    )
    # Every row's type resolves to a row in the printed table, so the issue's
    # `--type <t>` line is copy-pasteable.
    table = {row.type for row in provider_type_rows()}
    unlisted = sorted({_unwrap_code(r.type) for r in rows} - table)
    assert not unlisted, f"type(s) absent from the printed --list-types table: {unlisted}"


def test_clears_et7_count_and_family_bar(rows: list[Row]) -> None:
    """≥6 rows spanning channels + providers + sources — ``ET-7``'s literal bar."""
    assert len(rows) >= _MIN_ROWS, (
        f"wants-list has {len(rows)} rows; ET-7's done_when needs ≥{_MIN_ROWS} bounties "
        "(channels + providers + sources)"
    )
    families = {r.family for r in rows}
    assert families <= _FAMILIES, f"unknown family value(s): {sorted(families - _FAMILIES)}"
    assert families == _FAMILIES, (
        f"wants-list covers only {sorted(families)}; ET-7 names all three families "
        f"({sorted(_FAMILIES)})"
    )


def test_channel_rows_match_the_drafted_issue_prose(rows: list[Row]) -> None:
    """The list and the drafted issue text must name the same three channels.

    ``community-bounty-drafts.md`` is where those three bounties' public prose (and the
    owner-gated risk paragraph) lives; the wants-list is where they are enumerated. Two
    files, one set of three bounties.
    """
    assert _DRAFTS.is_file(), f"missing {_DRAFTS}"
    drafts = _DRAFTS.read_text(encoding="utf-8")

    channel_rows = [r for r in rows if r.family == "channel"]
    assert len(channel_rows) == len(_T73_CHANNELS), (
        f"expected {len(_T73_CHANNELS)} channel rows (T7.3 names "
        f"{', '.join(_T73_CHANNELS)}), got {[r.app for r in channel_rows]}"
    )
    for row in channel_rows:
        assert _unwrap_code(row.type) == "channel", f"{row!r} is family=channel but type≠channel"

    for name in _T73_CHANNELS:
        assert any(name in r.app for r in channel_rows), f"wants-list has no {name} channel row"
        assert f"Community channel app: {name}" in drafts, (
            f"{_DRAFTS.name} carries no drafted issue for {name}, but the wants-list "
            "lists it as a channel bounty"
        )

    # The gate is stated in BOTH files or in neither — a list that dropped the
    # owner-approval caveat would invite posting unapproved consent copy.
    for path, text in ((_WANTS_LIST, _WANTS_LIST.read_text(encoding="utf-8")), (_DRAFTS, drafts)):
        assert (
            "PENDING OWNER APPROVAL" in text
        ), f"{path.name} no longer marks the risk-policy paragraph PENDING OWNER APPROVAL"


def test_cited_in_tree_paths_exist(rows: list[Row]) -> None:
    """The paths the list tells a contributor to read must resolve.

    The docs-lint ratchet checks markdown *links*; this checks the specific three that
    every filed issue repeats, so a rename cannot ship six public issues pointing at
    nothing.
    """
    for rel in (
        "src/personalclaw/cli_app_new.py",
        "src/personalclaw/testing/channel_conformance.py",
        "docs/guides/build-a-channel-app.md",
    ):
        assert (_REPO_ROOT / rel).is_file(), f"wants-list cites missing path {rel}"
    text = _WANTS_LIST.read_text(encoding="utf-8")
    for rel in (
        "src/personalclaw/cli_app_new.py",
        "src/personalclaw/testing/channel_conformance.py",
        "guides/build-a-channel-app.md",
    ):
        assert rel in text, f"wants-list stopped citing {rel}"
