"""The source-tree checks of ``tests/function_size.py``: no function in core over 300 lines or of
complexity over 30 that its baseline does not list, and none listed that grew.

They run once per CI run, in the rails job. The module named above says what they hold to, and
``tests/test_function_size.py`` proves the measure and each check on functions it plants.
"""

from __future__ import annotations

import function_size
import site_baseline
import source_index


def _measures() -> list[function_size.Measure]:
    return function_size.measure_all(
        (source_index.rel(path), tree) for path, tree in source_index.package_trees()
    )


def test_no_function_in_core_grows_past_a_readable_size() -> None:
    measures = _measures()
    assert (
        len(measures) > 20_000
    ), f"the scan measured {len(measures)} functions: it is reading little"
    over = function_size.over_the_limits(measures)
    assert over, "no function in core is over a limit: the measure is reading nothing"
    site_baseline.check([m.site for m in over], function_size.BASELINE)
    listed = site_baseline.load(site_baseline.REPO / function_size.BASELINE)
    grown = function_size.grown(over, listed)
    assert not grown, "\n".join(grown)


def test_the_native_loop_is_not_on_the_list() -> None:
    """``NativeAgentRuntime.stream`` and its five phases fit both limits: it was split so that the
    record, the recovery and the limits each change one phase (#4015)."""
    listed = {line.site for line in site_baseline.load(site_baseline.REPO / function_size.BASELINE)}
    loop = [
        site
        for site in listed
        if site.startswith("src/personalclaw/agents/native/")
        and ("stream" in site or "_phase" in site)
    ]
    assert loop == [], loop
    phases = [
        m
        for m in _measures()
        if m.site.startswith("src/personalclaw/agents/native/loop_phases.py::LoopPhases._")
        and m.site.endswith("_phase")
    ]
    assert sorted(m.site.rpartition(".")[2] for m in phases) == [
        "_compact_phase",
        "_dispatch_phase",
        "_end_phase",
        "_infer_phase",
        "_steer_phase",
    ], phases
    assert not function_size.over_the_limits(phases)
