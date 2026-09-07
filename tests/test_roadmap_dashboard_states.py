"""``roadmap-dashboard.html`` must say WHY an atom is not moving, and never guess.

The dashboard used to render every atom that was not done, not in progress and not on the
ready frontier as one grey ``blocked`` bucket, mixing three unrelated situations: a sign-off
or repo or release only the OWNER can produce, a machine / OS grant / live account / price
row this ENVIRONMENT does not have, and plain dependency ordering behind another unfinished
atom. One colour for all three is the same as no colour: the owner cannot see which items are
his, which is the page's whole stated purpose.

Nothing here asserts how many atoms are in any state. That population turns over daily (the
old bucket held 37 at main ``281d693b1`` and 34 three commits later), so a test that pinned a
size would red on someone else's flip; these pin the SHAPE instead.

So the tests here pin the two properties that make the split trustworthy rather than merely
colourful:

* **It partitions.** Every atom lands in exactly one state and the states account for the
  whole catalog, enforced in the generator (not only here) by ``assert_state_partition`` — and
  proven to have teeth by adding a state and watching it red.
* **It refuses to guess.** An atom whose ``blocked_reason`` matches no rule is
  ``unclassified`` and named on the page. The specific trap is provenance prose: "split from
  WF2UNI-12 per owner ruling 2026-08-27" records who decided a split, and reading that as
  "the owner can act" would put an atom that is purely waiting into the owner's queue. A
  wrong classification here is worse than the grey bucket it replaced, because it would be
  acted on.
"""

from __future__ import annotations

import re

import pytest

from tools.gen_roadmap_dashboard import (
    _CSS,
    _OWNER_TAG_RE,
    _STATE_ROLL_CALL,
    ALLOW_DETACHED_ENV,
    DAG_STATES,
    AtomClassifier,
    DagState,
    QueueStats,
    _state_css,
    assert_state_partition,
    classifier_for,
    detached_workspace,
    main,
    parse_atoms,
    render,
)
from tools.regen_dag_derived import OWNER_GATE_RE

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# ── the table itself ────────────────────────────────────────────────────────────────────


def test_state_table_is_well_formed() -> None:
    """Keys, classes, labels and BOTH colours of every pair, present and unique."""
    assert len(DAG_STATES) == len(_STATE_ROLL_CALL) == 7
    assert [s.key for s in DAG_STATES] == list(_STATE_ROLL_CALL)
    assert len({s.key for s in DAG_STATES}) == len(DAG_STATES), "duplicate state key"
    assert len({s.cls for s in DAG_STATES}) == len(DAG_STATES), "duplicate CSS class"
    for s in DAG_STATES:
        assert s.cls.startswith("st-"), f"{s.key}: CSS class must be st-*"
        assert HEX.match(s.bg), f"{s.key}: fill {s.bg!r} is not a hex colour"
        assert HEX.match(s.ink), f"{s.key}: ink {s.ink!r} is not a hex colour"
        # Chip-short: the legend lays these out on one line beside six siblings.
        assert 0 < len(s.label) <= 14, f"{s.key}: label {s.label!r} is too long for a chip"
        assert len(s.hint) > 20, f"{s.key}: hint must explain what the state asks of a reader"


def test_the_three_stuck_states_and_unclassified_all_exist() -> None:
    """The point of the change: 'not moving' is no longer one bucket, and has an escape."""
    keys = {s.key for s in DAG_STATES}
    assert {"owner", "environment", "waiting", "unclassified"} <= keys
    assert "blocked" not in keys, "the single grey bucket is what this change removed"


# ── the partition invariant ─────────────────────────────────────────────────────────────


def test_partition_holds_over_the_live_catalog() -> None:
    dag = parse_atoms()
    atoms = dag.get("atoms") or {}
    # Vacuity guard: an empty or truncated catalog would make the assertion below pass.
    assert len(atoms) > 500, f"only {len(atoms)} atoms parsed — did dag.json's shape change?"
    clf = classifier_for(dag)
    counts = {s.key: 0 for s in DAG_STATES}
    for atom in atoms.values():
        counts[clf.state(atom)] += 1
    assert_state_partition(counts, len(atoms), "live catalog")
    # Every state is a real bucket, not a decorative one: the stuck half is non-empty and is
    # NOT all in one state — which is the defect this page had.
    stuck = {k: counts[k] for k in ("owner", "environment", "waiting")}
    assert sum(stuck.values()) > 0
    assert sum(1 for n in stuck.values() if n) >= 2, f"stuck atoms collapsed into one: {stuck}"


def test_partition_assertion_reds_on_a_wrong_total() -> None:
    counts = {s.key: 0 for s in DAG_STATES}
    counts["done"] = 5
    with pytest.raises(AssertionError, match="total is 6"):
        assert_state_partition(counts, 6, "deliberate mismatch")


def test_partition_assertion_reds_when_a_state_is_added_and_not_rolled_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The teeth: a future 8th state must fail loudly, not skew the percentages silently.

    This is why the roll-call is an explicit tuple of names and not ``sum(counts.values())``
    — a plain sum cannot distinguish a state that is counted from one that was added to the
    table and then dropped from the bar.
    """
    extra = DagState("nudged", "st-nudged", "#123456", "#0d1117", "nudged", "a new state" * 3)
    monkeypatch.setattr("tools.gen_roadmap_dashboard.DAG_STATES", DAG_STATES + (extra,))
    counts = {s.key: 0 for s in DAG_STATES}
    counts["nudged"] = 3
    counts["done"] = 4
    with pytest.raises(AssertionError, match="nudged"):
        assert_state_partition(counts, 7, "table grew")


# ── the classification rules ────────────────────────────────────────────────────────────


def _clf(*atoms: dict, ready: tuple[str, ...] = (), gates: dict | None = None) -> AtomClassifier:
    return AtomClassifier(
        ready_ids=frozenset(ready),
        gates={k: tuple(v) for k, v in (gates or {}).items()},
        atoms={a["id"]: a for a in atoms},
    )


def test_gate_array_decides_owner_versus_environment() -> None:
    """Rules 1-2. ``owner`` in the gate wins even when the atom is also EXT-gated."""
    both = {"id": "AR-1", "status": "todo"}
    ext = {"id": "DCU-3", "status": "todo"}
    clf = _clf(both, ext, gates={"AR-1": ("ext", "owner"), "DCU-3": ("ext",)})
    assert clf.state(both) == "owner"
    assert clf.state(ext) == "environment"


def test_ready_frontier_still_wins_for_everything_ungated() -> None:
    atom = {"id": "CA-8", "status": "todo"}
    assert _clf(atom, ready=("CA-8",)).state(atom) == "ready"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("OWNER-GATED — one question, nothing else", "owner"),
        ("OWNER-ONLY (inventory correction): owner-executed, no agent code change", "owner"),
        ("OWNER-RESIDUAL: the only open clause is the owner approving the preface", "owner"),
        ("PARTIAL, ENVIRONMENT-GATED: every code clause is met on main", "environment"),
        # The explicit ENVIRONMENT marker outranks an owner ruling CITED INSIDE it: PCS-9
        # reads "ENVIRONMENT-GATED (owner ruling 2026-08-28 split this from …)", and the
        # marker is the atom's own answer to the question this page asks.
        (
            "ENVIRONMENT-GATED (owner ruling 2026-08-28 split this): no OPENAI_API_KEY",
            "environment",
        ),
        ("BLOCKED on a core scanner decision, not on app work — see issue #2526", "waiting"),
        ("Status `blocked` is CORRECT and the blocker is an OWNER LIVE RUN", "owner"),
    ],
)
def test_blocked_reason_markers_route_to_the_right_state(reason: str, expected: str) -> None:
    atom = {"id": "X-1", "status": "blocked", "blocked_reason": reason}
    assert _clf(atom).state(atom) == expected


def test_naming_an_unfinished_atom_is_waiting_but_a_done_one_is_not() -> None:
    """Rule 3c. The blocker has to still be open for 'waiting' to mean anything."""
    open_dep = {"id": "OPEN-2", "status": "todo"}
    shipped = {"id": "SHIP-2", "status": "done"}
    waits = {"id": "X-1", "status": "blocked", "blocked_reason": "fires after OPEN-2 lands"}
    stale = {"id": "X-2", "status": "blocked", "blocked_reason": "fires after SHIP-2 lands"}
    clf = _clf(open_dep, shipped, waits, stale)
    assert clf.state(waits) == "waiting"
    # Nothing open is named, no marker, no decision issue → the page must not invent a cause.
    assert clf.state(stale) == "unclassified"


def test_owner_provenance_prose_is_not_read_as_an_owner_gate() -> None:
    """The WF2UNI-14 trap, and why ``unclassified`` exists at all.

    Its reason names the loop drain as the blocker — which the DAG does not model as one of
    its deps (its only dep is ``done``) — and mentions an owner ruling purely as provenance
    for a historical split. Classifying that as ``owner`` would send the owner off to do
    something that is not his to do; ``waiting`` would hide a real gap in dag.json. So it is
    left uncoloured and named on the page.
    """
    atom = {
        "id": "WF2UNI-14",
        "status": "blocked",
        "blocked_reason": (
            "Fires after loop drain (LOOPS-EVOLUTION Phase-4 endgame): the modules are "
            "load-bearing until stored loop references resolve exclusively through "
            "templates. Split from WF2UNI-12 per owner ruling 2026-08-27 so the already-met "
            "12a half could flip without waiting on the drain."
        ),
    }
    done_dep = {"id": "WF2UNI-12", "status": "done"}
    state, why = _clf(atom, done_dep).explain(atom)
    assert state == "unclassified", why
    assert "blocked_reason" in why


def test_todo_off_both_frontiers_is_waiting() -> None:
    """Rule 4. A marker on a ``todo`` atom describes a LATER gate, not today's blocker.

    DL-10 is OWNER-ONLY, but what stops it now is DL-11 — which is exactly why the deriver
    left it off both frontiers. Calling it ``owner`` would advertise work the owner cannot
    start.
    """
    atom = {
        "id": "DL-10",
        "status": "todo",
        "blocked_reason": "OWNER-ONLY: the lists prohibit bot submissions",
        "deps": ["DL-8", "DL-11"],
    }
    assert _clf(atom).state(atom) == "waiting"


def test_an_unknown_status_is_unclassified_not_folded_into_a_bucket() -> None:
    atom = {"id": "X-9", "status": "parked"}
    state, why = _clf(atom).explain(atom)
    assert state == "unclassified"
    assert "parked" in why


def test_owner_marker_vocabulary_agrees_with_the_dag_deriver() -> None:
    """One vocabulary, two readers.

    ``tools/regen_dag_derived.OWNER_GATE_RE`` is what puts ``"owner"`` in a
    ``gated_frontier`` gate array; this page's ``_OWNER_TAG_RE`` reads the same markers off
    ``blocked_reason`` for atoms the deriver never gates (a ``blocked`` atom is not
    startable, so it never reaches ``gated_frontier``). If the two drifted, the same marker
    would mean different things in two panels of the same page.
    """
    for sample in ("OWNER-GATED", "owner-only", "Owner Only", "OWNER GATED remainder"):
        assert OWNER_GATE_RE.search(sample), f"deriver stopped recognising {sample!r}"
        assert _OWNER_TAG_RE.search(sample), f"dashboard stopped recognising {sample!r}"
    # The dashboard's one deliberate addition, for reasons the deriver never sees.
    assert _OWNER_TAG_RE.search("OWNER-RESIDUAL (audit 2026-09-05)")
    assert not _OWNER_TAG_RE.search("the owner was consulted"), "bare 'owner' must not match"


# ── colours and the page ────────────────────────────────────────────────────────────────


def test_state_colours_live_only_in_the_table() -> None:
    """No state may be styled by hand: the static sheet carries no ``.st-*`` rule at all."""
    assert ".st-" not in _CSS, "a per-state rule crept back into the hand-written CSS"


def test_generated_css_never_sets_ink_without_its_fill() -> None:
    """Theme safety, mechanically: a fill and the ink on it are one pair, emitted together.

    Half a pair is how a new state inherits near-black text on a dark fill (or vice versa) —
    the exact latent bug in the block this replaced, where the grey state's ink lived in two
    unrelated overrides that any new state would have missed.
    """
    css = _state_css()
    for line in css.splitlines():
        if "color:" in line and "border-left-color:" not in line:
            assert "background:" in line, f"ink without a fill: {line.strip()}"
    for s in DAG_STATES:
        assert (
            f".statbar i.{s.cls}, .atomchip.{s.cls} {{ background:{s.bg}; color:{s.ink}; }}" in css
        )
        assert f".tile.{s.cls} {{ border-left-color:{s.bg}; }}" in css
        # Scoped, never a bare `.st-*`: `.tile` and `.atom` wear the state class too, so a
        # bare rule would repaint a whole plan tile and blacken its text.
        assert f"\n  .{s.cls} {{" not in css


def _synthetic_dag() -> dict:
    atoms = {
        "P-1": {"id": "P-1", "title": "shipped", "status": "done", "plan": "P", "plan_code": "P"},
        "P-2": {"id": "P-2", "title": "startable", "status": "todo", "plan": "P", "plan_code": "P"},
        "P-3": {
            "id": "P-3",
            "title": "owner sign-off",
            "status": "todo",
            "plan": "P",
            "plan_code": "P",
        },
        "P-4": {
            "id": "P-4",
            "title": "needs a mac",
            "status": "todo",
            "plan": "P",
            "plan_code": "P",
        },
        "P-5": {
            "id": "P-5",
            "title": "behind P-2",
            "status": "todo",
            "plan": "P",
            "plan_code": "P",
            "deps": ["P-2"],
        },
        "P-6": {
            "id": "P-6",
            "title": "unplaceable",
            "status": "blocked",
            "plan": "P",
            "plan_code": "P",
            "blocked_reason": "fires after the drain",
        },
    }
    return {
        "atoms": atoms,
        "ready": [{"id": "P-2", "title": "startable", "plan": "P"}],
        "gated": [
            {"id": "P-3", "title": "owner sign-off", "plan": "P", "gate": ["owner"]},
            {"id": "P-4", "title": "needs a mac", "plan": "P", "gate": ["ext"]},
        ],
        "topo": list(atoms),
        "cycles": [],
        "dangling": [],
        "unresolved": [],
        "edges": 1,
    }


def test_page_renders_standalone_and_shows_every_state() -> None:
    """It is opened as a ``file://`` URL, so nothing may be fetched to read it."""
    html = render([], QueueStats(), {"pr_by_branch": {}}, [], _synthetic_dag(), {}, {})
    assert html.startswith("<!doctype html>")
    assert "<script src=" not in html and '<link rel="stylesheet"' not in html
    assert "{" not in html.split("<style>")[0], "an unsubstituted format placeholder escaped"
    for s in DAG_STATES:
        assert f".statbar i.{s.cls}" in html, f"{s.key}: no colour reached the page"
        assert f">{s.label}<" in html, f"{s.key}: missing from the legend"
    # The unplaceable atom is NAMED, not quietly bucketed.
    assert "Unclassified — why these are stuck" in html
    assert "P-6" in html


def test_a_clean_catalog_renders_no_unclassified_strip() -> None:
    dag = _synthetic_dag()
    dag["atoms"].pop("P-6")
    html = render([], QueueStats(), {"pr_by_branch": {}}, [], dag, {}, {})
    assert "Unclassified — why these are stuck" not in html


# ── the workspace guard ─────────────────────────────────────────────────────────────────


def test_detached_workspace_detects_a_worktree_parked_outside_the_workspace(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``WORKSPACE = CORE.parent``, so a worktree elsewhere silently re-points the inputs."""
    missing = tmp_path / "ROADMAP.md"
    monkeypatch.setattr("tools.gen_roadmap_dashboard.WORKSPACE_ROADMAP", missing)
    why = detached_workspace()
    assert str(missing) in why, "the diagnosis must name the file it looked for"
    missing.write_text("# ROADMAP\n", encoding="utf-8")
    assert detached_workspace() == "", "a real workspace must not be flagged"


def test_main_refuses_to_write_a_degraded_page_from_a_detached_worktree(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page written where nobody reads it, with no exec state, must not look like success.

    Nothing raised before this: the generator happily rendered a plausible page missing the
    ROADMAP §5 prose and the whole "Working now" panel, wrote it beside the worktree, and
    exited 0. Two builders hit that on 2026-09-07 and one nearly published it.
    """
    out = tmp_path / "roadmap-dashboard.html"
    monkeypatch.setattr("tools.gen_roadmap_dashboard.WORKSPACE_ROADMAP", tmp_path / "nope.md")
    monkeypatch.setattr("tools.gen_roadmap_dashboard.OUT", out)
    monkeypatch.delenv(ALLOW_DETACHED_ENV, raising=False)
    assert main() == 2, "a detached run must exit non-zero"
    assert not out.exists(), "and must write nothing at all"
