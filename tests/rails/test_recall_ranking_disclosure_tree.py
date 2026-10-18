"""The source-tree checks split from
``tests/test_recall_ranking_disclosure.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO
from recall_ranking_disclosure_shared import _SRC

from personalclaw.memory_ranking import FALLS_BACK_CLAUSE

_WEB = REPO / "web" / "src"


def test_the_degradation_clause_lives_in_exactly_one_file():
    owners = sorted(
        p.relative_to(_SRC).as_posix()
        for p in source_index.package_files()
        if FALLS_BACK_CLAUSE in source_index.text(p)
    )
    assert owners == ["memory_ranking.py"], (
        f"the recall-degradation clause is authored in {owners}. It must exist only in "
        "personalclaw/memory_ranking.py — every surface reads the composed sentence from "
        "there, which is why the Recall tab and the entity-graph section can no longer "
        "disagree about what a degraded recall means."
    )


def test_no_frontend_file_authors_the_degradation_clause():
    """The sentence is server-composed; the frontend renders it verbatim."""
    scanned = [
        p
        for p in source_index.web_files()
        if p.suffix.startswith(".ts")
        and ".test." not in p.name
        and ".behaviour." not in p.name
        and ".doc." not in p.name
    ]
    # Vacuity floor: prove the scan actually read the panel this issue is about.
    panel = _WEB / "pages" / "settings" / "MemoryPanel.tsx"
    assert panel in scanned and "RankingNote" in source_index.text(panel)
    offenders = [
        p.relative_to(_WEB).as_posix() for p in scanned if FALLS_BACK_CLAUSE in source_index.text(p)
    ]
    assert not offenders, (
        f"{offenders} author the recall-degradation clause client-side. Render "
        "`ranking.summary` from the API instead — a second copy is how the two panels "
        "drifted apart in the first place."
    )


def test_recall_ranking_reaches_the_frontend_through_one_component():
    """A third surface must adopt `RankingNote`, not write its own note.

    Scoped to files that name the wire type: adding one is the moment a new surface
    decides how to render the disclosure, and that decision belongs in the shared
    component (move it into `web/src/ui/` when a page outside MemoryPanel needs it).
    """
    consumers = sorted(
        p.relative_to(_WEB).as_posix()
        for p in source_index.web_files()
        if p.suffix.startswith(".ts")
        and ".test." not in p.name
        and "RecallRanking" in source_index.text(p)
    )
    assert consumers == ["lib/api.ts", "pages/settings/MemoryPanel.tsx"], (
        f"RecallRanking is now referenced by {consumers}. A new consumer must render it "
        "through the one RankingNote component (promote it to web/src/ui/ first) rather "
        "than composing its own wording."
    )
