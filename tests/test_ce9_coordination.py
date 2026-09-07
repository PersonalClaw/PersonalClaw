"""The trigger-source coordination line is executable, not prose.

CHANNEL-EXPANSION ``CE-9`` clause 3 / ``T7.6``.

``CE-9``'s third clause wants "a coordination line into WORKFLOWS-V2-AUTOMATION-SUBSTRATE
[recording] the app-registered trigger-source forward obligation with no bespoke early event
glue shipped". A forward obligation is the single most rot-prone sentence a plan can carry:
it is written while the thing it waits on does not exist, and it keeps reading fine long
after that thing ships. That is exactly what happened here. The 2026-07-26 amendment wrote
"once WORKFLOWS-V2-AUTOMATION-SUBSTRATE exposes app-registered source types", the guide's
vendor-completeness table wrote "*when that seam exists* ... until it lands", and then
``WF2AUT-8`` landed the seam and both sentences quietly became false. A contributor reading
the guide was told to wait for something they could already use.

So the four things the coordination line asserts are pinned here:

1. **The seam is REAL, not merely declared.** ``trigger_source`` being a ``PROVIDER_TYPES``
   member proves nothing on its own: a type can sit in the frozenset with no runtime handler,
   in which case a manifest declaring it resolves to nothing. The handler and the SDK contract
   identity are what make the guide's instruction executable, so all three are asserted
   together.
2. **The guide no longer tells a contributor to wait.** The stale conditional phrasing is
   forbidden in that table row by name. This is the "absent-vs-declared-false" guard pointed
   the other way: a shipped capability must not read as an unshipped one.
3. **The guide's replacement instruction is executable and still forbids hand-rolled glue.**
   Both halves have to survive a future edit. Dropping the prohibition would invite the
   bespoke event loop the whole coordination exists to prevent; dropping the declaration would
   put the row back to being useless.
4. **The plan records BOTH halves of the obligation, and the adoption half still has no owner
   atom.** The core seam is discharged; per-app adoption is ``0/4``. If someone mints the
   adoption atom, clause 4 reds and the coordination block has to stop saying nobody owns it.

None of this checks the apps repo, which is not in this tree. The ``0/4`` adoption count and
the swept-for-glue evidence live in the coordination block itself, sourced from a measured
sweep and dated there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUIDE = _REPO_ROOT / "docs" / "guides" / "build-a-channel-app.md"
_PLAN = _REPO_ROOT / "docs" / "roadmap" / "plans" / "CHANNEL-EXPANSION.md"
_DAG = _REPO_ROOT / "docs" / "roadmap" / "atomic" / "dag.json"

#: The atom that shipped the seam, the atom that wrote the checklist, and the atom that
#: records this note. Not derived from the file under test: the point is to pin the
#: coordination block's "the adoption half has no owner atom" claim against ``dag.json``
#: independently, so minting a fourth reds here rather than silently outdating the prose.
_ATOMS_NAMING_THE_SEAM = frozenset({"WF2AUT-8", "CE-7", "CE-9"})

#: Phrasings that put a shipped seam back into the future. Each one appeared verbatim in the
#: guide or the plan before this note landed.
_STALE_CONDITIONALS = (
    "when that seam exists",
    "until it lands",
    "when the seam ships",
    "once that seam exists",
    "does not exist yet",
)

_SEAM_HEADING = "### COORDINATION"


def _guide_text() -> str:
    return _GUIDE.read_text(encoding="utf-8")


def _trigger_source_row() -> str:
    """The one ``| **Trigger source** | ... |`` row of the vendor-completeness table.

    Scoped to the row on purpose. Asserting over the whole guide would pass for the wrong
    reason the moment any other paragraph happened to use one of the banned phrases.
    """
    rows = [
        line
        for line in _guide_text().splitlines()
        if line.lstrip().startswith("| **Trigger source**")
    ]
    assert len(rows) == 1, (
        "expected exactly one '**Trigger source**' row in the vendor-completeness table of "
        f"{_GUIDE.relative_to(_REPO_ROOT)}, found {len(rows)}"
    )
    return rows[0]


def _coordination_block() -> str:
    """The ``### COORDINATION`` section body, up to the next same-level heading."""
    text = _PLAN.read_text(encoding="utf-8")
    start = text.find(_SEAM_HEADING)
    assert start != -1, (
        f"{_PLAN.relative_to(_REPO_ROOT)} has no '{_SEAM_HEADING}' section. CE-9's third "
        "clause is the coordination line; without the section there is nothing to record it."
    )
    nxt = text.find("\n### ", start + len(_SEAM_HEADING))
    return text[start:] if nxt == -1 else text[start:nxt]


# ---------------------------------------------------------------------------
# Clause 1 — the seam is real, not merely declared
# ---------------------------------------------------------------------------


def test_the_trigger_source_seam_is_REAL_not_merely_declared(tmp_path, monkeypatch) -> None:
    """The premise the whole coordination line rests on.

    A type in ``PROVIDER_TYPES`` with no ``_TypeHandler`` is a declared-but-absent seam: a
    manifest can name it and nothing will resolve. The guide now tells contributors to declare
    ``trigger_source``, so both legs are pinned here rather than assumed. Isolated home
    because building the registry constructs real providers, matching
    ``test_manifest_types_match_handlers``.

    The seam's own behaviour (fencing, provenance, parking, end-to-end fire) is asserted in
    ``tests/test_trigger_sources.py`` and is not re-litigated here.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.providers.registry as reg
    from personalclaw.apps.manifest import PROVIDER_TYPES

    monkeypatch.setattr(reg, "_registry", None, raising=False)

    assert "trigger_source" in PROVIDER_TYPES
    handler = reg.get_provider_registry()._type_handlers.get("trigger_source")
    assert handler is not None, (
        "trigger_source is in PROVIDER_TYPES but no runtime handler is registered for it, so "
        "an app declaring it would resolve to nothing and the guide's instruction is a lie."
    )
    assert isinstance(handler, reg.TriggerSourceTypeHandler)


def test_the_scaffold_type_table_offers_trigger_source_alongside_channel() -> None:
    """Both rows the coordination block cites are in the derived table.

    ``personalclaw app new --list-types`` is what the guide and the wants-list point a
    contributor at. If ``trigger_source`` fell out of it, the row would be undeliverable even
    though the manifest type survived.
    """
    from personalclaw.cli_app_new import provider_types

    types = set(provider_types())
    assert {"channel", "trigger_source"} <= types, sorted(types)


# ---------------------------------------------------------------------------
# Clause 2 — the guide no longer tells a contributor to wait
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", _STALE_CONDITIONALS)
def test_the_guide_does_not_put_the_SHIPPED_seam_back_in_the_future(phrase: str) -> None:
    """The absent-vs-declared-false trap, pointed the other way.

    ``WF2AUT-8`` shipped the seam. A row that still says "when that seam exists" costs a
    contributor the feature and invites the hand-rolled loop instead.
    """
    row = _trigger_source_row().lower()
    assert phrase not in row, (
        f"the vendor-completeness Trigger source row still says {phrase!r}, but the seam "
        "shipped with WF2AUT-8. Update the row, do not relax this test."
    )


def test_the_guide_row_is_no_longer_a_bare_forward_obligation() -> None:
    """The old row's Manifest cell was literally ``(forward obligation)``."""
    assert "(forward obligation)" not in _trigger_source_row()


# ---------------------------------------------------------------------------
# Clause 3 — the replacement instruction is executable, and the prohibition survives
# ---------------------------------------------------------------------------


def test_the_guide_row_names_the_manifest_type_and_the_sdk_path() -> None:
    """What a contributor has to type, both halves of it."""
    row = _trigger_source_row()
    assert '"trigger_source"' in row, row
    assert "personalclaw.sdk.trigger_source" in row, row


def test_the_guide_row_STILL_forbids_hand_rolled_event_glue() -> None:
    """The prohibition is the half of the coordination that stops the damage.

    Now that a seam exists the temptation is to drop the sentence as obsolete. It is not: a
    contributor who has never heard of the seam reaches for a poller by default.
    """
    row = _trigger_source_row().lower()
    assert "hand-roll" in row, row
    assert "glue" in row, row


# ---------------------------------------------------------------------------
# Clause 4 — the plan records both halves, and the adoption half has no owner atom
# ---------------------------------------------------------------------------


def test_the_coordination_block_names_the_atom_that_DISCHARGED_the_core_half() -> None:
    block = _coordination_block()
    assert "WF2AUT-8" in block
    assert "DISCHARGED" in block, (
        "the coordination block must say which half is closed. 'the substrate owns this' "
        "reads as pending, which is the drift this note exists to correct."
    )


def test_the_coordination_block_keeps_the_adoption_half_OUTSTANDING() -> None:
    """The negative must not read as satisfied.

    "No bespoke event glue shipped" is true because no app shipped anything at all, not
    because the apps adopted the seam. A block that recorded only the true negative would let
    a reader conclude adoption happened.
    """
    block = _coordination_block()
    assert "OUTSTANDING" in block, block[:400]
    assert "0/4" in block, (
        "the adoption count is the fact that keeps the negative honest. Update the number "
        "when an app adopts the seam; do not drop it."
    )


def test_no_FOURTH_atom_has_quietly_taken_ownership_of_adoption() -> None:
    """Pins the block's "the adoption half has no owner atom" claim against ``dag.json``.

    Reds two ways, and both are useful. A new atom naming the seam means adoption may now be
    owned, so the block has to stop saying nobody owns it. One of the three disappearing means
    the block cites an atom that no longer exists.
    """
    dag = json.loads(_DAG.read_text(encoding="utf-8"))
    pattern = re.compile(r"trigger[_ -]sources?\b", re.IGNORECASE)

    naming = {
        atom["id"]
        for plan in dag["plans"]
        for atom in plan["atoms"]
        if pattern.search(
            " ".join((atom.get("title", ""), atom.get("scope", ""), atom.get("done_when", "")))
        )
    }

    assert naming == set(_ATOMS_NAMING_THE_SEAM), (
        "the set of atoms naming the trigger-source seam changed from "
        f"{sorted(_ATOMS_NAMING_THE_SEAM)} to {sorted(naming)}. CHANNEL-EXPANSION's "
        "COORDINATION block claims the per-app adoption half has no owner atom anywhere; "
        "reconcile the block with the roadmap before updating this set."
    )
