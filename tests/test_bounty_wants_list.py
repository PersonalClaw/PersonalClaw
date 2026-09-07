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

# the gap that let a row rot

Every check above validates a row's SHAPE. Not one of them ever asked whether the row's
claimed GAP still exists — so the file carried a suite that made it look executable
while a justification went false underneath. It happened: the ``trigger_source`` row
correctly measured 0/4 adoption across the first-party channel apps, ``CE-10`` drove
that to 4/4 hours after this list merged, and every test here stayed green while the
list went on publicly soliciting work that was already built and tested.

Core CI has no checkout of ``PersonalClawApps``, so it cannot re-count the apps repo,
and a comment asking a future reader to re-measure is the failure, not the fix. What IS
enforceable from here is the *vintage* of each claim and the *core half* of it:

5. **Every measured-gap row carries a well-formed ``measured <ISO date> @ <apps ref>``
   stamp**, and a row that is NOT a measured gap carries none. An undated gap claim can
   no longer reach the public list at all.
6. **Every row stamp agrees with the one population stamp** in the doc's admission
   criteria, so a half-done re-measure — header bumped, rows left behind, or the reverse
   — reds instead of passing quietly. Re-measuring the table is all-or-nothing.
7. **The core-side premise of each measured gap is RE-MEASURED every run.** Rows 4-6
   rest on facts about core's own registries ("exactly one builtin gate, ``manual``";
   "``native`` is the only registered memory provider"; nothing surfaced as
   ``kind:external``). Those live in this repo, so they are read off the real registries
   rather than trusted — in a clean subprocess, because the registries are
   process-global and a sibling test that registers without restoring must not be able
   to turn this rail red or green by accident.

Checks 5-7 do not make the apps-side count self-verifying; nothing in core can. They
make it DATED, INTERNALLY CONSISTENT, and false-for-a-reason instead of silently false.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
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

#: The ``Ground`` cell value that makes a row a claim about another repository's contents
#: at a moment in time — the only kind of row that can rot without anybody touching it.
_MEASURED_GROUND = "measured gap"

#: The stamp a measured-gap row must end with. The ref is matched as a git object name
#: rather than as free text on purpose: "measured recently @ main" would satisfy a lax
#: pattern while naming nothing a reader could diff against, which is the un-checkable
#: claim this rail exists to refuse.
_STAMP_RE = re.compile(r"\bmeasured (\d{4}-\d{2}-\d{2}) @ ([0-9a-f]{7,40})\b")

#: The one paragraph that stamps the population every measured gap was counted over.
_POPULATION_MARKER = "**Measurement population:**"

#: Core-side premises the measured-gap rows rest on, keyed by the row's provider type.
#: Each value is (what a violation MEANS in that row's own words, the ONLY name core may
#: have registered for that type). These are the half of each measurement that lives in
#: THIS repo, so they are re-read from the real registries on every run rather than taken
#: on trust from the stamp. A type absent from this map is an apps-side-only claim, which
#: core cannot check at all — ``inbox`` and ``trigger_source`` are both that.
_CORE_PREMISES: dict[str, tuple[str, str]] = {
    "duty_gate": (
        "row 'Calendar-backed on-duty gate' says core registers exactly one builtin "
        "gate, `manual` — core now registers more, so that row's premise is dead",
        "manual",
    ),
    "memory": (
        "row 'External memory backend' says `native` is the only registered memory "
        "provider — core now registers another, so that row's premise is dead",
        "native",
    ),
    "knowledge": (
        "row 'External knowledge backend' says nothing has taken up `kind:external` — "
        "core's own registry now surfaces a non-native provider",
        "native",
    ),
}

#: Read off the REAL registries in a clean interpreter. Home is repointed at a tmp dir
#: because this subprocess is outside ``conftest``'s real-home guard, and importing core
#: must not be able to write into the developer's actual ``~/.personalclaw``.
_CORE_PREMISE_PROBE = """\
import json
from personalclaw.triggers.calendar import duty_gate_names
from personalclaw.knowledge_providers import registry as knowledge_registry
from personalclaw.memory_providers import registry as memory_registry

print(
    json.dumps(
        {
            "duty_gate": sorted(duty_gate_names()),
            "memory": sorted(memory_registry.list_providers()),
            "knowledge": sorted(
                {str(info.get("kind")) for info in knowledge_registry.list_provider_info()}
            ),
        }
    )
)
"""


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
def wants_text() -> str:
    assert _WANTS_LIST.is_file(), f"missing {_WANTS_LIST}"
    return _WANTS_LIST.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rows(wants_text: str) -> list[Row]:
    return _rows(wants_text)


@pytest.fixture(scope="module")
def population_stamp(wants_text: str) -> tuple[str, str]:
    """The doc's single ``(date, apps ref)`` measurement stamp.

    One stamp, in one paragraph, so there is exactly one thing for the rows to agree with.
    Two population paragraphs would be two vintages, which is the drift this rail forbids.
    """
    blocks = [b for b in wants_text.split("\n\n") if _POPULATION_MARKER in b]
    assert len(blocks) == 1, (
        f"{_WANTS_LIST.name}: expected exactly one '{_POPULATION_MARKER}' paragraph naming "
        f"the population every measured gap was counted over, found {len(blocks)}"
    )
    found = _STAMP_RE.findall(blocks[0])
    assert len(found) == 1, (
        f"{_WANTS_LIST.name}: the '{_POPULATION_MARKER}' paragraph must carry exactly one "
        f"'measured <YYYY-MM-DD> @ <apps ref>' stamp, found {found}. Without it no row's "
        "vintage can be checked against anything."
    )
    return found[0]


def test_population_stamp_is_a_real_measurement(population_stamp: tuple[str, str]) -> None:
    """The population stamp names a day that has happened and a ref somebody can diff.

    A future date is not a measurement anybody took, and it is the shape a placeholder
    stamp takes when someone fills the field in to make this suite green.
    """
    stamped, ref = population_stamp
    measured_on = date.fromisoformat(stamped)  # reds on 2026-02-30 and friends
    assert measured_on <= date.today(), (
        f"{_WANTS_LIST.name}: population stamped {stamped}, which is in the future — "
        "a measurement nobody has taken yet"
    )
    assert len(ref) >= 7, f"apps ref {ref!r} is too short to identify a commit"


def test_every_measured_gap_row_is_dated(rows: list[Row]) -> None:
    """A measured gap carries its vintage; a plan-named row does not borrow the look of one.

    Both directions matter. An undated gap claim is the defect — a row that reads as
    measured but names no day or ref cannot be re-checked by anybody. A *stamped* row whose
    ground is "named by a plan" is the mirror defect: it dresses an editorial choice up as
    evidence.
    """
    undated = [
        r.number for r in rows if r.ground == _MEASURED_GROUND and not _STAMP_RE.search(r.why)
    ]
    assert not undated, (
        f"{_WANTS_LIST.name}: measured-gap row(s) {undated} carry no "
        "'measured <YYYY-MM-DD> @ <apps ref>' stamp. Every measured gap is a claim about "
        "PersonalClawApps at a moment in time; an undated one cannot be re-verified and "
        "must not reach a public bounty issue."
    )
    borrowed = [r.number for r in rows if r.ground != _MEASURED_GROUND and _STAMP_RE.search(r.why)]
    assert not borrowed, (
        f"{_WANTS_LIST.name}: row(s) {borrowed} carry a measurement stamp but their ground "
        f"is not {_MEASURED_GROUND!r} — a plan-named row must not present itself as measured"
    )
    for row in rows:
        if row.ground == _MEASURED_GROUND:
            stamps = _STAMP_RE.findall(row.why)
            assert len(stamps) == 1, f"{row!r} carries {len(stamps)} stamps; expected exactly 1"


def test_row_stamps_all_agree_with_the_population_stamp(
    rows: list[Row], population_stamp: tuple[str, str]
) -> None:
    """Re-measuring this table is all-or-nothing.

    The population count and the per-row gap claims are the same measurement seen from two
    angles. If a re-measure can bump the header and leave a row behind (or stamp one row
    fresh while the header still names the old ref), the file is back to carrying two
    vintages and reading like one — the precise state that let a closed gap keep publishing.
    """
    disagreeing = {
        r.number: _STAMP_RE.search(r.why).groups()  # type: ignore[union-attr]
        for r in rows
        if r.ground == _MEASURED_GROUND
        and _STAMP_RE.search(r.why)
        and _STAMP_RE.search(r.why).groups() != population_stamp  # type: ignore[union-attr]
    }
    assert not disagreeing, (
        f"{_WANTS_LIST.name}: row stamp(s) disagree with the population stamp "
        f"{population_stamp}: {disagreeing}. Re-measure the whole table against one apps "
        "ref and restamp every measured-gap row, or the file carries two vintages at once."
    )


def test_core_side_premise_of_each_measured_gap_still_holds(
    rows: list[Row], tmp_path: Path
) -> None:
    """Re-measure, for real, the half of each measured gap that lives in THIS repo.

    Rows 4-6 justify themselves partly on core's own registries, and those are readable
    here — so they are read, not trusted. If core itself ships a calendar duty gate, a
    second memory provider, or a non-native knowledge provider, the corresponding row's
    stated premise is dead and this reds naming which row and why.

    The apps-side half (does any *app* implement the type) is NOT checked and cannot be:
    core CI has no ``PersonalClawApps`` checkout. That half is what the stamp dates.
    """
    measured_types = {_unwrap_code(r.type) for r in rows if r.ground == _MEASURED_GROUND}
    probed = sorted(measured_types & _CORE_PREMISES.keys())
    assert probed, (
        "no measured-gap row has a core-side premise to re-measure — if the table's provider "
        f"rows were renamed, update _CORE_PREMISES (knows: {sorted(_CORE_PREMISES)})"
    )

    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO_ROOT / "src"),
        # Outside conftest's real-home guard: importing core must not touch the real home.
        "HOME": str(tmp_path),
        "PERSONALCLAW_HOME": str(tmp_path / ".personalclaw"),
    }
    done = subprocess.run(
        [sys.executable, "-c", _CORE_PREMISE_PROBE],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert done.returncode == 0, (
        "could not read core's provider registries — the wants-list premises are "
        f"unverifiable:\n{done.stderr[-2000:]}"
    )
    registries = json.loads(done.stdout.strip().splitlines()[-1])

    dead = []
    for provider_type in probed:
        meaning, only_allowed = _CORE_PREMISES[provider_type]
        registered = set(registries[provider_type])
        if not registered <= {only_allowed}:
            dead.append(f"{provider_type}: {meaning} (registered: {sorted(registered)})")
    assert not dead, (
        "wants-list measured-gap row(s) rest on a core-side premise that no longer holds:\n  "
        + "\n  ".join(dead)
        + "\nFix or remove the row — do not restamp it. A stamp dates a claim; it does not "
        "make a false one true."
    )


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
