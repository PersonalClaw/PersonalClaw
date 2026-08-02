"""Tests for surfacing channels 2/3, scope resolution, preflight and the doctor (S59).

Three things were MEASURED before this module asserted anything about them.

**The `create-task` hook silently drops unknown keys.** Probed live: an action config carrying
`linked_def` and `workflow_binding` returned `success=True` and created a task whose
`workflow_binding` was `None` and whose persisted JSON held neither value. The plan's "materialized
tasks carry an explicit bidirectional link block" would therefore have been code that runs, reports
success, and enforces nothing — so the link lives in the cadence ledger and
`escalation_action` emits only keys the provider actually reads.

**`store.list_runs` is the only run history.** Verified signature
`list_runs(*, workflow_name, status, root_run_id, limit, offset) -> (list, total)` and that
`WorkflowRun` carries `completed_at`. A `last_completed` field cached on the def would disagree with
this table the first time a run was deleted.

**`WorkflowDef` has no `scope`, `cadence_days` or `fingerprint` field yet** (measured against
`dataclasses.fields`). So this session's records are standalone and the def-side wiring is the
later session's scope — asserting against a field that does not exist is how a test passes while
the feature is absent.
"""

import pytest

from personalclaw.workflows.surfacing_channels import (
    BUNDLED_PACKS,
    ESCALATION_INTERVAL_SECS,
    FIXTURE_KINDS,
    MAX_SCAN_FILES,
    PACK_THRESHOLD,
    RESUME_BOOST,
    SCOPE_ORDER,
    UNREACHABLE_NO_CHANNEL,
    UNREACHABLE_REQUIREMENTS,
    UNREACHABLE_SHADOWED,
    Availability,
    CadenceState,
    Escalation,
    Freshness,
    Overlay,
    Pack,
    Predicate,
    PreFill,
    Requirement,
    ScopedDef,
    ScopeState,
    TriggerFixture,
    adopt_target,
    apply_overlay,
    build_prefill,
    check_fixtures,
    confidence,
    dismiss_pack,
    doctor,
    effective,
    escalation_action,
    escalation_decision,
    fixture_gaps,
    freshness,
    last_completed,
    link_block,
    load_dismissals,
    overdue,
    preflight,
    preflight_message,
    probe_state,
    propose_packs,
    resolve_scopes,
    resume_boost,
    scan_paths,
    sort_key,
    suggestion_inputs,
)

DAY = 86400.0
NOW = 1_700_000_000.0


def _state(**kw) -> CadenceState:
    base = dict(name="backup", cadence_days=7, last_completed_at=NOW - 3 * DAY)
    base.update(kw)
    return CadenceState(**base)


# ── channel 2: freshness bands ──


def test_an_untracked_def_is_always_FRESH():
    """`cadence_days: 0` means the author did not ask to be nagged — the same reading S57 gave
    `ttl: 0`, and for the same reason."""
    assert freshness(_state(cadence_days=0), NOW) is Freshness.FRESH
    assert overdue(_state(cadence_days=0), NOW) is False


def test_a_never_run_def_is_its_OWN_band_not_infinitely_stale():
    """A checklist the user authored yesterday has not failed to run. Reporting it as maximally
    stale on day one is how a freshness column trains a user to ignore it."""
    assert freshness(_state(last_completed_at=0.0), NOW) is Freshness.NEVER_RUN


def test_within_cadence_is_fresh():
    assert freshness(_state(last_completed_at=NOW - 2 * DAY), NOW) is Freshness.FRESH


def test_the_last_fifth_of_the_cadence_is_DUE_SOON():
    """A gradient exists so a user can act before the deadline, not after."""
    assert freshness(_state(last_completed_at=NOW - 6 * DAY), NOW) is Freshness.DUE_SOON


def test_past_cadence_is_OVERDUE():
    assert freshness(_state(last_completed_at=NOW - 8 * DAY), NOW) is Freshness.OVERDUE


def test_far_past_cadence_is_STALE_not_just_overdue():
    """A def three weeks past a weekly cadence is a different conversation from one a day late."""
    assert freshness(_state(last_completed_at=NOW - 21 * DAY), NOW) is Freshness.STALE


def test_overdue_covers_never_run_overdue_and_stale():
    for last in (0.0, NOW - 8 * DAY, NOW - 30 * DAY):
        assert overdue(_state(last_completed_at=last), NOW) is True


# ── channel 2: ordering ──


def test_the_list_sorts_STALE_first_then_overdue_then_never_run():
    states = [
        _state(name="fresh", last_completed_at=NOW - 1 * DAY),
        _state(name="stale", last_completed_at=NOW - 30 * DAY),
        _state(name="never", last_completed_at=0.0),
        _state(name="over", last_completed_at=NOW - 8 * DAY),
    ]
    ordered = [s.name for s in sorted(states, key=lambda s: sort_key(s, NOW))]
    assert ordered == ["stale", "over", "never", "fresh"]


def test_lateness_is_PROPORTIONAL_not_absolute():
    """Absolute lateness would park every long-cadence def permanently at the top: a 90-day def is
    always "more days late" than a 7-day one, which is not the same as more overdue."""
    weekly = CadenceState(name="weekly", cadence_days=7, last_completed_at=NOW - 21 * DAY)
    quarterly = CadenceState(name="quarterly", cadence_days=90, last_completed_at=NOW - 100 * DAY)
    assert sort_key(weekly, NOW) < sort_key(quarterly, NOW)


def test_the_sort_key_is_STABLE_by_name():
    a = _state(name="a", last_completed_at=NOW - 8 * DAY)
    b = _state(name="b", last_completed_at=NOW - 8 * DAY)
    assert sort_key(a, NOW) < sort_key(b, NOW)


# ── channel 2: last-completed comes from real run history ──


def test_last_completed_reads_the_RUN_TABLE():
    """Measured: `store.list_runs` is the only run history, and it filters by name and status. A
    timestamp cached on the def would disagree with this table the first time a run was deleted."""
    seen = {}

    class _Run:
        completed_at = "2026-07-01T00:00:00+00:00"

    def lister(**kw):
        seen.update(kw)
        return [_Run()], 1

    stamp = last_completed("backup", lister=lister)
    assert stamp > 0
    assert seen["workflow_name"] == "backup"
    assert seen["status"] == "complete"


def test_an_epoch_stamp_is_accepted_too():
    class _Run:
        completed_at = NOW

    assert last_completed("x", lister=lambda **kw: ([_Run()], 1)) == NOW


def test_a_run_with_no_completed_at_falls_back_to_created_at():
    class _Run:
        completed_at = ""
        created_at = "2026-07-01T00:00:00+00:00"

    assert last_completed("x", lister=lambda **kw: ([_Run()], 1)) > 0


def test_a_BROKEN_store_degrades_to_never_run_rather_than_raising():
    """A surfacing channel runs inside a turn and must never break it. "No history" degrades to
    "surface it", which is the recoverable direction."""

    def boom(**kw):
        raise RuntimeError("db locked")

    assert last_completed("x", lister=boom) == 0.0


def test_no_runs_means_zero():
    assert last_completed("x", lister=lambda **kw: ([], 0)) == 0.0


# ── channel 2: escalation ──


def test_MANUAL_escalation_materializes_NOTHING():
    """ "Remind me" and "put it on my board" are different asks; a user who wanted the first would
    resent the second."""
    ok, why = escalation_decision(_state(last_completed_at=NOW - 30 * DAY), NOW)
    assert ok is False
    assert "manual" in why


def test_AUTO_escalation_fires_for_an_overdue_def_with_a_baseline():
    state = _state(escalation=Escalation.AUTO, last_completed_at=NOW - 30 * DAY)
    ok, why = escalation_decision(state, NOW)
    assert ok is True
    assert why == ""


def test_a_NEVER_RUN_def_does_not_auto_materialize():
    """The non-obvious gate. An authored-and-never-run def is a draft — a "you are overdue" task
    for something the user never started reads as the system malfunctioning."""
    state = _state(escalation=Escalation.AUTO, last_completed_at=0.0)
    ok, why = escalation_decision(state, NOW)
    assert ok is False
    assert "baseline" in why


def test_escalation_is_throttled_to_ONCE_PER_DAY_not_per_tick():
    """The plan is explicit: once daily while the condition persists, never per evaluation tick. A
    tick-rate throttle would put one task on the board per scheduler pass."""
    state = _state(
        escalation=Escalation.AUTO,
        last_completed_at=NOW - 30 * DAY,
        last_escalated_at=NOW - 600,
    )
    ok, why = escalation_decision(state, NOW)
    assert ok is False
    assert "throttled" in why


def test_the_throttle_RELEASES_after_a_day():
    state = _state(
        escalation=Escalation.AUTO,
        last_completed_at=NOW - 30 * DAY,
        last_escalated_at=NOW - ESCALATION_INTERVAL_SECS - 1,
    )
    assert escalation_decision(state, NOW)[0] is True


def test_a_def_inside_its_cadence_never_escalates():
    state = _state(escalation=Escalation.AUTO, last_completed_at=NOW - 1 * DAY)
    ok, why = escalation_decision(state, NOW)
    assert ok is False
    assert why == "not overdue"


# ── channel 2: the action config, measured against the real provider ──


def test_the_action_emits_ONLY_keys_the_provider_actually_reads():
    """The measured defect. `CreateTaskActionProvider.execute` renders `title_template`/
    `body_template` and passes through only priority/project/assignee/due/labels. A `linked_def`
    key returns success=True and is silently dropped, so emitting one would be a control that
    enforces nothing."""
    config = escalation_action(_state(last_completed_at=NOW - 30 * DAY), NOW)
    honored = {
        "title_template",
        "body_template",
        "priority",
        "project",
        "assignee",
        "due",
        "labels",
    }
    assert set(config) <= honored, f"emits keys the provider drops: {set(config) - honored}"


def test_the_action_names_the_def_and_the_age():
    config = escalation_action(_state(last_completed_at=NOW - 30 * DAY), NOW)
    assert "backup" in config["title_template"]
    assert "30d" in config["title_template"]
    assert "/workflow backup" in config["body_template"]


def test_a_STALE_def_escalates_at_higher_priority():
    stale = escalation_action(_state(last_completed_at=NOW - 30 * DAY), NOW)
    over = escalation_action(_state(last_completed_at=NOW - 8 * DAY), NOW)
    assert stale["priority"] == "high"
    assert over["priority"] == "medium"


def test_the_link_block_carries_BOTH_directions():
    """def→task answers "did my reminder land"; task→def answers "why is this on my board"."""
    block = link_block(def_name="backup", task_id="t-1", run_id="r-1")
    assert block["linked_def"] == "backup"
    assert block["task_id"] == "t-1"
    assert block["completed"] is False


def test_the_link_block_records_downstream_completion():
    block = link_block(def_name="backup", task_id="t-1", completed=True, completed_at=NOW)
    assert block["completed"] is True
    assert block["completed_at"] == NOW


# ── channel 2: resume boost ──


def test_an_in_flight_run_BOOSTS_its_def():
    """An unfinished checklist is the most likely thing the user is about to ask about, and the
    per-turn matcher cannot know a run is half-done."""
    assert resume_boost(_state(in_flight=True), base=0.60) == pytest.approx(0.60 + RESUME_BOOST)


def test_the_boost_is_small_enough_not_to_jump_bands():
    """A boost large enough to override a clearly better match would let an abandoned run outrank
    an exact-match def."""
    assert RESUME_BOOST <= 0.05


def test_the_boost_is_CLAMPED_at_one():
    assert resume_boost(_state(in_flight=True), base=0.99) <= 1.0


def test_no_in_flight_run_means_no_boost():
    assert resume_boost(_state(in_flight=False), base=0.60) == 0.60


# ── channel 3: predicates ──


def test_a_directory_predicate_matches_a_path_PREFIX():
    """`tests/` asks about a directory, which may be empty in a fresh checkout."""
    assert Predicate("tests/").matches(["tests"]) is True
    assert Predicate("tests/").matches(["tests/test_a.py"]) is True
    assert Predicate("tests/").matches(["src/app.py"]) is False


def test_a_glob_matches_on_the_BASENAME_too():
    """`*.py` means "any python file", not "a python file in the root" — which is what an author
    writing that pattern expects."""
    assert Predicate("*.py").matches(["src/deep/mod.py"]) is True


def test_an_exact_filename_matches():
    assert Predicate("pyproject.toml").matches(["pyproject.toml"]) is True


def test_an_EMPTY_pattern_matches_nothing():
    """An empty predicate that matched everything would propose its pack in every directory."""
    assert Predicate("").matches(["anything"]) is False


# ── channel 3: confidence ──


def test_confidence_is_WEIGHT_normalized():
    """Signals are not equal: `pyproject.toml` says "python project" far more strongly than one
    stray `.py` file."""
    pack = Pack(name="p", predicates=[Predicate("a", 3.0), Predicate("b", 1.0)])
    assert confidence(pack, ["a"]) == 0.75
    assert confidence(pack, ["b"]) == 0.25


def test_a_pack_with_NO_predicates_scores_zero_not_one():
    """A pack that matched everything would propose itself in every directory — the over-firing
    failure this channel exists to avoid."""
    assert confidence(Pack(name="empty"), ["anything"]) == 0.0


def test_a_full_match_scores_one():
    pack = Pack(name="p", predicates=[Predicate("a"), Predicate("b")])
    assert confidence(pack, ["a", "b"]) == 1.0


def test_the_bundled_python_pack_recognizes_THIS_repo():
    """Measured against the real tree rather than a fixture: a pack that cannot recognize the
    repository it ships in is not a pack anyone will trust."""
    pack = next(p for p in BUNDLED_PACKS if p.name == "python-project")
    assert confidence(pack, ["pyproject.toml", "tests", "src/personalclaw/__init__.py"]) >= (
        PACK_THRESHOLD
    )


def test_the_python_pack_does_NOT_fire_on_a_bare_node_repo():
    pack = next(p for p in BUNDLED_PACKS if p.name == "python-project")
    assert confidence(pack, ["package.json", "src/index.ts"]) < PACK_THRESHOLD


# ── channel 3: scanning ──


def test_the_scan_SKIPS_vendor_directories(tmp_path):
    """A fingerprint found inside `node_modules` describes a dependency, not this project."""
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "pyproject.toml").write_text("x")
    (tmp_path / "app.py").write_text("x")
    found = scan_paths(tmp_path)
    assert "app.py" in found
    assert not any("node_modules" in p for p in found)


def test_the_scan_is_BOUNDED(tmp_path):
    """The scan runs on directory attach; a 400k-file repo must not stall it. A truncated scan
    under-scores, which yields a missing proposal rather than a hung UI."""
    for i in range(30):
        (tmp_path / f"f{i}.py").write_text("x")
    assert len(scan_paths(tmp_path, limit=10)) == 10


def test_the_scan_includes_DIRECTORIES(tmp_path):
    (tmp_path / "tests").mkdir()
    assert "tests" in scan_paths(tmp_path)


def test_scanning_a_MISSING_directory_is_empty_not_an_error(tmp_path):
    assert scan_paths(tmp_path / "nope") == []


def test_the_scan_cap_is_declared():
    assert MAX_SCAN_FILES >= 1000


# ── channel 3: proposals ──


def test_a_proposal_is_ONE_grouped_suggestion():
    """Five separate "enable this SOP?" prompts on attach is a wall the user clicks away — and
    clicking away a wall teaches them to click away the next one."""
    proposal = propose_packs(["pyproject.toml", "tests", ".github/workflows", "Makefile"])
    assert proposal is not None
    assert len(proposal.packs) >= 2
    assert isinstance(proposal.defs, list)


def test_a_proposal_ENABLES_NOTHING():
    """Propose-don't-enable, as a checkable field rather than a docstring claim."""
    proposal = propose_packs(["pyproject.toml", "tests"])
    assert proposal.enabled_anything is False


def test_nothing_below_threshold_proposes_NOTHING():
    assert propose_packs(["README.md"]) is None


def test_a_DISMISSED_pack_is_not_re_proposed():
    """ "Not in this repo" is the common case; re-asking every attach is what makes a user turn the
    channel off entirely."""
    paths = ["pyproject.toml", "tests"]
    assert propose_packs(paths) is not None
    assert propose_packs(paths, dismissed=["python-project"]) is None


def test_the_proposal_dedupes_defs_across_packs():
    packs = [
        Pack(name="a", predicates=[Predicate("x")], defs=["code-review"]),
        Pack(name="b", predicates=[Predicate("x")], defs=["code-review", "bug-fix"]),
    ]
    proposal = propose_packs(["x"], packs=packs)
    assert proposal.defs.count("code-review") == 1


def test_the_proposal_orders_by_CONFIDENCE():
    packs = [
        Pack(name="weak", predicates=[Predicate("x"), Predicate("absent")]),
        Pack(name="strong", predicates=[Predicate("x")]),
    ]
    proposal = propose_packs(["x"], packs=packs, threshold=0.4)
    assert proposal.packs[0] == "strong"


def test_the_proposal_carries_its_SCORES():
    proposal = propose_packs(["pyproject.toml", "tests"])
    assert all(0.0 <= v <= 1.0 for v in proposal.scores.values())


# ── channel 3: dismissal persistence, on the isolated home rail ──


def test_a_dismissal_PERSISTS_per_project(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    dismiss_pack("proj-a", "python-project")
    assert load_dismissals("proj-a") == {"python-project"}
    assert load_dismissals("proj-b") == set()


def test_dismissal_is_IDEMPOTENT(tmp_path, monkeypatch):
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    dismiss_pack("p", "ci")
    assert dismiss_pack("p", "ci") == {"ci"}


def test_UNREADABLE_dismissal_state_reads_as_empty(tmp_path, monkeypatch):
    """Degrading to empty re-proposes (mildly annoying) rather than suppressing forever
    — a channel that silently stopped working is one nobody would ever diagnose."""
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    path = tmp_path / "surfacing" / "dismissed-p.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert load_dismissals("p") == set()


def test_a_project_id_with_separators_cannot_ESCAPE_the_directory(tmp_path, monkeypatch):
    """A project id is not a trust boundary; it reaches this from a session attach."""
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    dismiss_pack("../../etc/evil", "ci")
    assert not (tmp_path.parent.parent / "etc").exists()
    assert list((tmp_path / "surfacing").iterdir())


# ── R18: scope resolution ──


def test_NARROWER_shadows_wider():
    resolved = resolve_scopes([ScopedDef("deploy", "global"), ScopedDef("deploy", "workspace")])
    states = {r.entry.scope: r.state for r in resolved}
    assert states["workspace"] is ScopeState.EFFECTIVE
    assert states["global"] is ScopeState.SHADOWED


def test_a_shadowed_def_stays_VISIBLE_with_a_reason():
    """Silently hiding it is how a user concludes their global procedure vanished and writes a
    third copy."""
    resolved = resolve_scopes([ScopedDef("deploy", "global"), ScopedDef("deploy", "session")])
    shadowed = next(r for r in resolved if r.state is ScopeState.SHADOWED)
    assert shadowed.shadowed_by == "session:deploy"


def test_the_ladder_is_the_documented_order():
    assert SCOPE_ORDER == ("session", "agent", "workspace", "global", "bundled")


def test_an_UNKNOWN_scope_sorts_WIDEST_and_cannot_shadow():
    """Unknown-is-widest is the safe direction: a scope this build does not recognize must not
    shadow a def the user explicitly wrote at a known scope."""
    resolved = resolve_scopes([ScopedDef("d", "global"), ScopedDef("d", "vibes")])
    winner = next(r for r in resolved if r.state is ScopeState.EFFECTIVE)
    assert winner.entry.scope == "global"


def test_a_DISABLED_def_neither_shadows_nor_wins():
    """Calling it shadowed would tell the user something else is winning when nothing is."""
    resolved = resolve_scopes([ScopedDef("d", "session", disabled=True), ScopedDef("d", "global")])
    states = {r.entry.scope: r.state for r in resolved}
    assert states["session"] is ScopeState.DISABLED
    assert states["global"] is ScopeState.EFFECTIVE


def test_every_def_appears_exactly_once_per_entry():
    entries = [ScopedDef("a", "global"), ScopedDef("a", "session"), ScopedDef("b", "bundled")]
    assert len(resolve_scopes(entries)) == len(entries)


def test_effective_returns_the_winners_by_name():
    entries = [ScopedDef("a", "global"), ScopedDef("a", "session"), ScopedDef("b", "bundled")]
    won = effective(entries)
    assert won["a"].scope == "session"
    assert won["b"].scope == "bundled"


def test_a_fully_disabled_name_has_NO_effective_def():
    assert effective([ScopedDef("a", "global", disabled=True)]) == {}


# ── R18: adopt ──


def test_adopting_a_BUNDLED_def_targets_an_editable_scope():
    target, error = adopt_target(ScopedDef("seed", "bundled"))
    assert target == "global"
    assert error == ""


def test_adopting_an_already_editable_def_is_REFUSED():
    """Two copies at one scope is the state that makes shadowing unexplainable."""
    target, error = adopt_target(ScopedDef("mine", "workspace"))
    assert target == ""
    assert "already at editable scope" in error


# ── R18: per-stage overlays ──


def test_an_overlay_PATCHES_one_stage_and_inherits_the_rest():
    """This is what keeps a SOP library DRY: a project swaps one stage of the global deploy
    procedure and keeps inheriting upstream improvements to the rest."""
    base = [{"id": "build", "cmd": "make"}, {"id": "deploy", "cmd": "global-deploy"}]
    overlay = Overlay(base_def="deploy-proc", patches={"deploy": {"cmd": "project-deploy"}})
    stages, diff = apply_overlay(base, overlay)
    assert stages[0] == {"id": "build", "cmd": "make"}
    assert stages[1]["cmd"] == "project-deploy"
    assert any("deploy" in line for line in diff)


def test_an_overlay_can_DISABLE_a_stage():
    base = [{"id": "a"}, {"id": "b"}]
    stages, diff = apply_overlay(base, Overlay(base_def="x", patches={"b": False}))
    assert [s["id"] for s in stages] == ["a"]
    assert "- b (disabled by overlay)" in diff


def test_an_overlay_renders_a_DIFF():
    """An overlay whose effect is invisible is one a user cannot audit, and this is the mechanism
    that changes what a procedure does."""
    base = [{"id": "a", "cmd": "x"}]
    _stages, diff = apply_overlay(base, Overlay(base_def="d", patches={"a": {"cmd": "y"}}))
    assert diff == ["~ a (cmd)"]


def test_an_overlay_naming_a_MISSING_stage_is_caught_at_SAVE_time():
    """A patch for a missing id simply never applies, so the stage the author meant to replace runs
    unchanged — silently. Save time is the only moment this is a typo rather than an incident."""
    from personalclaw.workflows.surfacing_channels import validate_overlay

    overlay = Overlay(base_def="d", patches={"typo": {"cmd": "x"}})
    assert any("does not define" in f for f in validate_overlay(overlay, ["real"]))


def test_an_overlay_with_no_base_def_is_a_finding():
    from personalclaw.workflows.surfacing_channels import validate_overlay

    assert any("no `base_def`" in f for f in validate_overlay(Overlay(base_def=""), []))


def test_a_valid_overlay_has_no_findings():
    from personalclaw.workflows.surfacing_channels import validate_overlay

    assert validate_overlay(Overlay(base_def="d", patches={"a": {}}), ["a", "b"]) == []


def test_disabled_stages_are_enumerable():
    overlay = Overlay(base_def="d", patches={"a": False, "b": None, "c": {"x": 1}})
    assert overlay.disabled_stages() == ["a", "b"]


# ── R11: the three-state availability model ──


def test_a_missing_provider_is_NOT_INSTALLED():
    """Three states because they need three remedies: an install, a toggle, and a settings page.
    Collapsing them sends the user to the wrong place."""
    state, why = probe_state(Requirement("deploy"), installed=[])
    assert state is Availability.NOT_INSTALLED
    assert "not installed" in why


def test_an_installed_but_off_provider_is_DISABLED():
    state, _ = probe_state(Requirement("deploy"), installed=["deploy"], disabled=["deploy"])
    assert state is Availability.DISABLED


def test_an_enabled_but_unconfigured_provider_is_UNAVAILABLE():
    state, why = probe_state(
        Requirement("deploy"), installed=["deploy"], probe=lambda n: (False, "no API key")
    )
    assert state is Availability.UNAVAILABLE
    assert why == "no API key"


def test_a_configured_provider_is_AVAILABLE():
    state, _ = probe_state(Requirement("deploy"), installed=["deploy"], probe=lambda n: (True, ""))
    assert state is Availability.AVAILABLE


def test_a_provider_with_no_probe_is_available():
    """`load_availability` returns None for the common case: a bundle with no hook is fine."""
    assert probe_state(Requirement("d"), installed=["d"])[0] is Availability.AVAILABLE


def test_a_probe_that_RAISES_reads_as_unavailable_not_available():
    """An availability hook is code from a removable bundle. Treating its crash as a pass would
    surface a suggestion that dies at dispatch — the exact failure preflight prevents."""

    def boom(_name):
        raise RuntimeError("bundle exploded")

    state, why = probe_state(Requirement("d"), installed=["d"], probe=boom)
    assert state is Availability.UNAVAILABLE
    assert "probe failed" in why


def test_availability_is_NOT_probed_for_an_uninstalled_provider():
    """Probing a module that is not there would crash, and reporting "unavailable" would send the
    user to a settings page for something they have not got."""
    calls = []

    def probe(name):
        calls.append(name)
        return True, ""

    probe_state(Requirement("d"), installed=[], probe=probe)
    assert calls == []


# ── R11: preflight ──


def test_preflight_PASSES_when_everything_is_available():
    ok, findings = preflight([Requirement("d")], installed=["d"])
    assert ok is True
    assert findings == []


def test_preflight_reports_EVERY_unmet_requirement():
    """A def needing two missing binaries has a user who should install two."""
    ok, findings = preflight([Requirement("a"), Requirement("b")], installed=[])
    assert ok is False
    assert len(findings) == 2


def test_a_finding_carries_the_capability_blocked_kind():
    """Shares §1's vocabulary so a preflight finding and a mid-run capability failure read
    identically — the user should not learn two names for "the deploy binary is missing"."""
    _ok, findings = preflight([Requirement("d")], installed=[])
    assert findings[0].to_dict()["blocked_kind"] == "capability"


def test_a_finding_DEEP_LINKS_to_the_fix():
    req = Requirement("d", settings_path="/settings/providers/d")
    _ok, findings = preflight([req], installed=[])
    assert findings[0].settings_path == "/settings/providers/d"


def test_the_refusal_NAMES_the_missing_item():
    """ "This workflow cannot run yet" with no noun is a message the user cannot act on."""
    _ok, findings = preflight([Requirement("deploy-cli")], installed=[])
    message = preflight_message(findings)
    assert "deploy-cli" in message
    assert "not_installed" in message


def test_no_findings_means_no_message():
    assert preflight_message([]) == ""


def test_a_def_with_NO_requirements_passes():
    assert preflight([])[0] is True


# ── R11: pre-fill ──

SCHEMA = {
    "properties": {"env": {"type": "string"}, "version": {"type": "string"}},
    "required": ["env"],
}


def test_only_USER_messages_count_as_truth():
    """A value the agent proposed is not something the user asked for; pre-filling from it puts
    words in their mouth and then runs on them."""
    result = build_prefill(
        SCHEMA,
        [
            {"role": "assistant", "values": {"env": "prod"}},
            {"role": "user", "values": {"env": "staging"}},
        ],
    )
    assert result.extracted["env"] == "staging"


def test_FENCED_content_is_excluded():
    """Pasted content firing a workflow is the failure that made manual-first the default."""
    result = build_prefill(SCHEMA, [{"role": "user", "values": {"env": "prod"}, "fenced": True}])
    assert result.extracted == {}
    assert result.all_filled is False


def test_PASTED_content_is_excluded_too():
    result = build_prefill(SCHEMA, [{"role": "user", "values": {"env": "prod"}, "pasted": True}])
    assert result.extracted == {}


def test_the_LATEST_value_wins():
    """A user who corrects themselves mid-turn means the correction."""
    result = build_prefill(
        SCHEMA,
        [
            {"role": "user", "values": {"env": "staging"}},
            {"role": "user", "values": {"env": "prod"}},
        ],
    )
    assert result.extracted["env"] == "prod"


def test_a_value_NOT_in_the_schema_is_dropped():
    result = build_prefill(SCHEMA, [{"role": "user", "values": {"nonsense": 1}}])
    assert result.extracted == {}


def test_all_filled_is_RE_DERIVED_from_the_schema():
    """A model reporting `all_filled: true` while omitting a required input produces a
    `workflow_start` that fails engine validation — after the user was told it was ready."""
    result = build_prefill(SCHEMA, [{"role": "user", "values": {"version": "1.2"}}])
    assert result.all_filled is False
    assert result.missing == ["env"]


def test_the_follow_up_asks_REQUIRED_first():
    result = build_prefill(SCHEMA, [])
    assert "env" in result.follow_up
    assert "version" not in result.follow_up


def test_a_DECLINED_optional_is_never_re_asked():
    """Re-asking is how a follow-up becomes an interrogation — the user already answered "no"."""
    result = build_prefill(
        SCHEMA, [{"role": "user", "values": {"env": "prod"}}], declined=["version"]
    )
    assert result.all_filled is True
    assert result.follow_up == ""


def test_an_optional_gap_is_offered_but_does_not_block():
    result = build_prefill(SCHEMA, [{"role": "user", "values": {"env": "prod"}}])
    assert result.all_filled is True
    assert "version" in result.follow_up


def test_suggestion_inputs_never_carry_a_PLACEHOLDER():
    """A placeholder would pass the engine's presence check and execute a step against a made-up
    value — worse than the run refusing to start."""
    result = build_prefill(SCHEMA, [{"role": "user", "values": {"version": "1.2"}}])
    assert "env" not in suggestion_inputs(result)


def test_a_prefill_round_trips_to_dict():
    payload = PreFill(extracted={"a": 1}, missing=["b"], follow_up="?", all_filled=False).to_dict()
    assert payload == {
        "extracted": {"a": 1},
        "missing": ["b"],
        "follow_up": "?",
        "all_filled": False,
    }


# ── the reachability doctor ──


def test_a_def_no_channel_can_reach_is_a_FINDING():
    """The mirror of over-firing, and the harder failure to notice: an over-firing def annoys the
    user into fixing it, while an unreachable def is simply never seen again."""
    findings = doctor([{"name": "ghost", "surface_mode": "passive"}])
    assert [f.code for f in findings] == [UNREACHABLE_NO_CHANNEL]


def test_a_CADENCE_only_def_is_reachable():
    """Checking only `match_text` would report every cadence-only def as broken, which trains a
    user to ignore the doctor."""
    assert doctor([{"name": "backup", "surface_mode": "passive", "cadence_days": 7}]) == []


def test_a_PACK_gated_def_is_reachable():
    assert doctor([{"name": "d", "surface_mode": "passive", "packs": ["python-project"]}]) == []


def test_an_INDEXED_def_is_reachable():
    assert doctor([{"name": "d", "surface_mode": "passive", "indexed": True}]) == []


def test_an_OFF_def_is_not_a_finding():
    """`off` is a deliberate choice and explicit invocation always works. Flagging it would make
    the doctor fire on every newly authored def."""
    assert doctor([{"name": "d", "surface_mode": "off"}]) == []


def test_a_DISABLED_def_is_skipped():
    assert doctor([{"name": "d", "surface_mode": "passive", "disabled": True}]) == []


def test_a_SHADOWED_def_is_reported():
    findings = doctor(
        [
            {
                "name": "d",
                "surface_mode": "passive",
                "match_text": "deploy",
                "shadowed_by": "session:d",
            }
        ]
    )
    assert [f.code for f in findings] == [UNREACHABLE_SHADOWED]


def test_UNMET_requirements_are_reported():
    findings = doctor(
        [
            {
                "name": "d",
                "surface_mode": "suggest",
                "match_text": "deploy",
                "unmet_requirements": ["deploy-cli"],
            }
        ]
    )
    assert findings[0].code == UNREACHABLE_REQUIREMENTS
    assert "deploy-cli" in findings[0].detail


def test_the_codes_are_TYPED_not_prose():
    """S54 paid for prose-matched reasons: a message containing the word "secret" was matched as if
    it were a secret. A code is what a surface should switch on."""
    findings = doctor([{"name": "d", "surface_mode": "passive"}])
    assert findings[0].to_dict()["code"] == UNREACHABLE_NO_CHANNEL


def test_a_healthy_registry_produces_NO_findings():
    assert doctor([{"name": "d", "surface_mode": "passive", "match_text": "deploy the app"}]) == []


# ── trigger-accuracy CI harness ──


def test_a_positives_only_fixture_set_is_a_GAP():
    """A def with positives only passes its own CI while over-firing on everything adjacent —
    precisely the failure that made manual-first the default."""
    gaps = fixture_gaps("d", [TriggerFixture("deploy the app", "positive", True)])
    assert len(gaps) == len(FIXTURE_KINDS) - 1
    assert any("pasted_history" in g for g in gaps)


def test_a_complete_fixture_set_has_no_gaps():
    fixtures = [TriggerFixture(f"p{k}", k, k == "positive") for k in FIXTURE_KINDS]
    assert fixture_gaps("d", fixtures) == []


def test_the_harness_reports_a_MISSED_positive():
    failures = check_fixtures([TriggerFixture("deploy", "positive", True)], lambda p: False)
    assert "did NOT fire" in failures[0]


def test_the_harness_reports_a_FIRED_negative():
    """A missed positive and a fired negative are different bugs with different fixes; one
    pass/fail count hides which happened."""
    failures = check_fixtures([TriggerFixture("paste", "pasted_history", False)], lambda p: True)
    assert "fired on" in failures[0]


def test_a_matcher_that_RAISES_is_a_failure_not_a_crash():
    def boom(_prompt):
        raise RuntimeError("matcher broken")

    failures = check_fixtures([TriggerFixture("x", "positive", True)], boom)
    assert "matcher raised" in failures[0]


def test_an_agreeing_matcher_produces_no_failures():
    fixtures = [
        TriggerFixture("deploy the app", "positive", True),
        TriggerFixture("unrelated", "neighbor_domain", False),
    ]
    assert check_fixtures(fixtures, lambda p: "deploy" in p) == []
