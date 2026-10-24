"""The inert-surface census on the real tree: each surface it finds is listed, with its reason.

``scripts/generate_inert_surface_baseline.py`` names every declared-but-inert surface as a site,
``<file>::<kind>:<name>``. :data:`~scripts.generate_inert_surface_baseline.SITES` lists them, one
line each with the reason the surface is left inert, in the per-site format of
``tests/site_baseline.py``. A surface the list does not hold fails by name, and the fix is to add
the missing writer or reader, not its line. A listed surface the census no longer finds fails on
its stale line until the line is deleted (``python tests/site_baseline.py --prune``), and against
the gate's base a new line needs ``added`` and a reason. The list replaced the per-file counters
of ``inert-surface-baseline.json``, which could only shrink.

``scripts/gate_report.py`` in the rails-checks job fails on a new surface as well; these hold the
rest: the stale and repeated lines, the base, and two vacuity floors (two census kinds still find
a population). ``tests/test_inert_surface_baseline.py`` proves each detector on trees it plants.
"""

from __future__ import annotations

import re
import textwrap
from functools import cache
from pathlib import Path

import pytest
import site_baseline
from rails_repo import REPO

from scripts import generate_inert_surface_baseline as inert
from scripts.generate_inert_surface_baseline import (
    KIND_CONFIG_READER,
    _attribute_names_in_src,
    _enum_members,
    _inert_enum_members,
    _iterated_enum_classes,
    _parse,
    _src_py_files,
    build_inventory,
)


@cache
def _inventory() -> dict:
    """The real tree's census, built once for the tests below that read it."""
    return build_inventory()


@pytest.mark.timeout(300)
def test_every_inert_surface_is_listed_with_its_reason():
    """Every surface the census finds is a line of :data:`inert.SITES`, and every line is a
    surface it finds. A new one fails by name: add the missing writer or reader. A wired one
    fails on its stale line: delete it in the same commit."""
    site_baseline.check(inert.sites(_inventory()), inert.SITES)


#: A tree of the control's own: an enum with a member nothing reads and an SDK export nothing
#: imports, beside the two anchors the census refuses to run without.
_PLANTED_TREE = {
    "src/personalclaw/__init__.py": "",
    "src/personalclaw/config/loader.py": """
        class AppConfig:
            @classmethod
            def load(cls, raw):
                return cls(name=raw["name"])
        """,
    "src/personalclaw/config/editable.py": '_EDITABLE_CONFIG = {"name": str}\n',
    "src/personalclaw/planted_inert.py": """
        import enum

        class Planted(enum.Enum):
            READ = "read"
            GHOST = "ghost"

        DEFAULT = Planted.READ
        """,
    "src/personalclaw/sdk/planted_facade.py": '__all__ = ["planted_lonely"]\n',
}
_PLANTED_SITES = (
    "src/personalclaw/planted_inert.py::enum:Planted.GHOST",
    "src/personalclaw/sdk/planted_facade.py::sdk_export:planted_lonely",
)


def _planted(root: Path) -> list[str]:
    for rel, source in _PLANTED_TREE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    planted = ("src/personalclaw/planted_inert.py::", "src/personalclaw/sdk/planted_facade.py::")
    return [site for site in inert.sites(build_inventory(root)) if site.startswith(planted)]


@pytest.mark.timeout(300)
def test_a_planted_surface_fails_by_name_and_a_wired_one_by_its_line(tmp_path):
    """The positive control, on the real list: the tree's surfaces with a planted module's fail,
    naming its unread enum member and its unimported export, and its read member is no site; the
    gate's comparison names the same two. The tree's surfaces less one, as if a reader for it
    had landed, fail on that surface's stale line. No run writes the list."""
    listed = (REPO / inert.SITES).read_text(encoding="utf-8")
    found = inert.sites(_inventory())
    planted = _planted(tmp_path)
    assert sorted(planted) == sorted(_PLANTED_SITES), planted
    with pytest.raises(AssertionError) as failed:
        site_baseline.check([*found, *planted], inert.SITES, None, prune=False)
    message = str(failed.value)
    for site in _PLANTED_SITES:
        assert f"Site {site} is not listed" in message, message
    assert message.count("is not listed") == len(_PLANTED_SITES), message
    assert "is listed in" not in message, message
    gate = inert.unlisted(inert.listed(), [*found, *planted])
    assert [line.split(" ", 2)[1] for line in gate] == sorted(_PLANTED_SITES), gate
    wired, *rest = found
    with pytest.raises(AssertionError, match=f"Site {re.escape(wired)} is listed in") as stale:
        site_baseline.check(rest, inert.SITES, None, prune=False)
    assert str(stale.value).count("is listed in") == 1, stale.value
    assert inert.unlisted(inert.listed(), rest) == []
    assert (REPO / inert.SITES).read_text(encoding="utf-8") == listed


@pytest.mark.timeout(300)
def test_the_real_config_reader_census_fires_and_clears_an_accessor():
    """Vacuity + real-tree calibration in both directions."""
    inv = _inventory()
    assert inv["totals"]["by_kind"][KIND_CONFIG_READER] > 0
    flagged = {surface for bucket in inv["per_file"].values() for surface in bucket["surfaces"]}
    assert f"{KIND_CONFIG_READER}:routing.energy_sampling" in flagged
    for wired in [
        "agent.self_qa.enabled",
        "external_access.a2a.enabled",
        "external_access.openai.enabled",
        "inbox.enabled",
        "knowledge.embed_batch_size",
        "knowledge.embed_retry_budget",
        "knowledge.max_mentions_per_claim",
        "knowledge.require_citations",
        "knowledge.similarity_degree_cap",
        "knowledge.synthesis_window",
        "learning.self_model_enabled",
        "resilience.remediation.enabled",
        "sources.enabled",
        "workflows.max_concurrent_llm_nodes",
        "workflows.retention_per_def",
    ]:
        assert f"{KIND_CONFIG_READER}:{wired}" not in flagged


@pytest.mark.timeout(300)
def test_the_enum_census_still_finds_a_nontrivial_population():
    """Vacuity guard. Clearing a whole class per iteration site is a broad clear, so a bug
    that over-cleared (or a detector that silently matched everything) would leave the enum
    census reporting nothing while every other kind still looked healthy. The census must
    still SEE many enum classes and still REPORT some inert members.

    🔴 The timeout is RAISED, not the work reduced, and the number is measured — the same
    ruling `test_render_is_deterministic` records above, for the same reason and at the same
    number. This is the file's second-heaviest test: it walks and parses every `src` file for
    the `>50` claim, then `build_inventory()` walks the tree AGAIN. Measured through pytest at
    load average 40: **19.2s** of call time alone, and **28.5s** running with the rest of this
    file — second only to the 42.8s of `test_render_is_deterministic`, which is already at 300
    for having crossed 120s in CI twice (#1205, #1222). CI starved this one past 120s the same
    way. Both assertions are untouched; only the wall-clock budget moves.

    Neither half of the work can be dropped without gutting the guard. The `>50` sweep is
    what proves the walk still SEES the population, and the `>= 5` half has to read the real
    census output — a stubbed or cached inventory would assert on the stub, which is exactly
    the suspiciously-clean census this test exists to catch. `_parse()` is deliberately
    uncached tree-wide (see `test_render_is_deterministic`: both builds have to be real), so
    there is no memoization to lean on here either.
    """
    files = _src_py_files()
    classes = {
        cls for f in files if (tree := _parse(f)) is not None for cls, _ in _enum_members(tree)
    }
    assert len(classes) > 50, f"only {len(classes)} enum classes seen — the walk is broken"
    assert build_inventory()["totals"]["by_kind"]["enum"] >= 5, (
        "the enum census reports (almost) nothing — whole-class clearing has over-reached; "
        "shrink the detected shapes rather than trusting a suspiciously clean census"
    )


# A census of the tree, moved here from tests/test_inert_surface_baseline.py so it runs once, in
# the rails job, not in every suite leg (#3929): it took 57 s of that file's 66 s on a Linux leg
# (2026-10-24), and 85-95 s under coverage (2026-10-14/15).
@pytest.mark.timeout(300)
def test_the_lineage_false_red_is_gone_and_its_iteration_site_is_seen():
    """The finding itself, on the real tree: ``Lineage`` is recognised as iterated (its
    validation set in ``workflows/publish.py``) and no ``Lineage`` member is reported.

    If the validation that iterates ``Lineage`` is ever deleted, this test SHOULD red — the
    members would then genuinely be unreachable."""
    files = _src_py_files()
    enum_names: dict[Path, set[str]] = {}
    for f in files:
        tree = _parse(f)
        if tree is None:
            continue
        names = {cls for cls, _ in _enum_members(tree)}
        if names:
            enum_names[f.resolve()] = names
    iterated = _iterated_enum_classes(files, enum_names)
    publish = next(f for f in files if f.as_posix().endswith("workflows/publish.py"))
    assert (publish.resolve(), "Lineage") in iterated

    inert = {surface for _, surface in _inert_enum_members(files, _attribute_names_in_src(files))}
    assert not [s for s in inert if s.startswith("Lineage.")], sorted(inert)
