"""The source-tree checks split from
``tests/test_automation_spend_is_metered.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable

import source_index
from automation_spend_is_metered_shared import (
    DECIDED,
    INTERACTIVE,
    REACQUIRES,
    UNMETERED_RESOLUTIONS,
    Site,
    _acquires,
    _called,
    _resolves,
    census,
    guarded_axes,
    resolution_verdict,
    verdict,
)
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


#: The resolvers' own modules: what they call there is the resolution itself, and a runtime's
#: inner model (``_build_native_runtime``) is metered by the axis its acquisition names.
RESOLVER_HOMES = frozenset({"providers/provider_bridge.py", "providers/image_input.py"})


def src_sites(counted: Callable[[ast.Call], bool] = _acquires) -> list[Site]:
    sites: list[Site] = []
    for path, tree in source_index.package_trees():
        rel = path.relative_to(SRC).as_posix()
        if counted is _resolves and rel in RESOLVER_HOMES:
            continue
        sites.extend(census(tree, rel, counted))
    return sites


def test_every_runtime_acquisition_is_metered_or_is_a_person_s_surface():
    guarded = guarded_axes()
    sites = src_sites()
    unaccounted = [f"{s.path}:{s.line} in {s.qualname}" for s in sites if not verdict(s, guarded)]
    assert not unaccounted, (
        "these calls acquire a runtime on no metered axis, so the daily cap never counts what "
        'they spend. Name the axis the work is (`model_axis="orchestration"` for agent work '
        "nobody typed), or — only for a surface a person is working in — add it to INTERACTIVE "
        "with the reason:\n  " + "\n  ".join(unaccounted)
    )
    # A listing is ONE decision about ONE call. A second unmetered call in the same function is
    # a new decision, not the old one: the scheduled job's reading of its subagent's result sat
    # beside the channel announcement in one function, on no axis, and a per-function allowance
    # would have waved it through.
    unmetered = Counter(
        (s.path, s.qualname) for s in sites if verdict(s, guarded) in ("interactive", "reacquires")
    )
    doubled = {where: n for where, n in unmetered.items() if n > 1}
    assert not doubled, doubled


def test_the_census_is_not_vacuous():
    """A census that parsed nothing would pass the rail above for free."""
    guarded = guarded_axes()
    verdicts = [verdict(s, guarded) for s in src_sites()]
    assert len(verdicts) >= 10, len(verdicts)
    for kind in ("guarded", "decided", "reacquires", "interactive"):
        assert kind in verdicts, f"no site reads {kind!r}: the classifier or the census drifted"


def test_every_listed_exemption_still_names_a_real_call():
    """A listed site that no longer exists is an allowance waiting for the next call to use it."""
    guarded = guarded_axes()
    sites = src_sites()
    for listing, kind in (
        (INTERACTIVE, "interactive"),
        (REACQUIRES, "reacquires"),
        (DECIDED, "decided"),
    ):
        for where in listing:
            used = [s for s in sites if (s.path, s.qualname) == where]
            assert used, f"{where} is listed but acquires nothing now: drop it"
            assert any(verdict(s, guarded) == kind for s in used), (where, kind)


def test_every_direct_model_resolution_is_metered_or_is_a_person_s_surface():
    guarded = guarded_axes()
    sites = src_sites(_resolves)
    unaccounted = [
        f"{s.path}:{s.line} in {s.qualname}" for s in sites if not resolution_verdict(s, guarded)
    ]
    assert not unaccounted, (
        "these calls resolve a model on no metered axis and call it, so the daily cap never counts "
        "what they spend. Resolve it with `resolve_metered_model` (the image reader with "
        "`metered=True`), or, only for a surface a person is working in, add it to "
        "UNMETERED_RESOLUTIONS with the reason:\n  " + "\n  ".join(unaccounted)
    )
    listed = Counter(
        (s.path, s.qualname) for s in sites if resolution_verdict(s, guarded) == "listed"
    )
    doubled = {where: n for where, n in listed.items() if n > 1}
    assert not doubled, doubled


def test_the_resolution_census_is_not_vacuous():
    """The tree holds both kinds a direct resolution may be. None resolves a guarded axis through
    the general resolver any more: every call automation makes is resolved as the model itself
    (``resolve_metered_model``), which never builds an agent CLI. That verdict's classifier is
    still read, by the snippets below."""
    guarded = guarded_axes()
    verdicts = [resolution_verdict(s, guarded) for s in src_sites(_resolves)]
    assert len(verdicts) >= 8, len(verdicts)
    for kind in ("metered", "listed"):
        assert kind in verdicts, f"no resolution reads {kind!r}: the classifier or census drifted"


def test_every_listed_unmetered_resolution_still_names_a_real_call():
    guarded = guarded_axes()
    sites = src_sites(_resolves)
    for where in UNMETERED_RESOLUTIONS:
        used = [s for s in sites if (s.path, s.qualname) == where]
        assert used, f"{where} is listed but resolves nothing now: drop it"
        assert any(resolution_verdict(s, guarded) == "listed" for s in used), where


#: The one call that may acquire a runtime the cap does not count on a metered axis, and the one
#: function that decides it: an Attended loop's planner and workers are answered by a person, so
#: their spend is their owner's, as a chat's is (``loop.posture``). The guard still wraps them.
UNMETERED_DECIDED: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat"): "spend_metered",
}


def test_only_a_loops_mode_takes_a_session_off_the_cap():
    """Every acquisition that says ``unmetered=`` is the one listed, and it says it through the
    posture function, negated: nothing hands a runtime an unmetered flag of its own."""
    saying = [s for s in src_sites() if any(k.arg == "unmetered" for k in s.call.keywords)]
    assert [(s.path, s.qualname) for s in saying] == list(UNMETERED_DECIDED), saying
    for site in saying:
        value = next(k.value for k in site.call.keywords if k.arg == "unmetered")
        assert isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not), ast.dump(value)
        assert _called(value.operand) == UNMETERED_DECIDED[(site.path, site.qualname)]
