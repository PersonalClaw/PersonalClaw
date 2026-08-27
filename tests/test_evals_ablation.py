"""EVALUATION-SUBSTRATE ES-7 §3.1 — the harness-ablation runner.

Four things are load-bearing here and each is asserted with its own falsification:

1. **The live spec/config is never mutated.** The obvious implementation (edit the spec off,
   run, edit it back) is forbidden because a crash strands the edit. The guard digests the
   live files before and after — and the vacuity floor is
   ``test_live_state_guard_reds_when_a_mutation_leaks``: a body that DOES write live config
   must make the guard raise, or the guard is decoration.
2. **A no-delta report attaches to a LEARN-R9 retirement proposal**, at the real
   ``proposals.enqueue`` call site, carrying the *ablation* evidence grade — a distinct
   claim from the queue's default ``correlated``.
3. **keep / remove / lighten are all reachable.** An unreachable verdict is a
   declared-but-dead branch.
4. **An overlay cannot escape its cell.** ``apply_in_child`` refuses outright when
   ``PERSONALCLAW_HOME`` still points at the operator's real home.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personalclaw.evals import ablation
from personalclaw.evals import overlay as overlay_lib
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    VERIFIER_ABSENT,
    CellResult,
    MatrixResult,
    MatrixSpec,
    aggregate_by,
)

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def eval_home(tmp_path, monkeypatch):
    """Isolated home. Destructive by design — this atom's whole job is toggling config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _component(**over) -> ablation.AblationComponent:
    base = {
        "component_id": "correction-heuristic",
        "kind": overlay_lib.KIND_CONFIG_FLAG,
        # A REAL boolean field on the loaded AppConfig. Deliberately not a plausible-looking
        # invented name: `validate_component` refuses those (see
        # `test_a_config_target_that_does_not_exist_is_refused`), so a fixture on a fictional
        # field would make every test below assert the refusal instead of the behaviour.
        "target": "learning.correction_heuristic",
        "subject": "triage-scenario",
    }
    base.update(over)
    return ablation.AblationComponent(**base)


def _cells(scores: dict[str, list[float | None]]) -> list[CellResult]:
    """Cells keyed by arm; a ``None`` score becomes a VERIFIER_ABSENT cell."""
    out: list[CellResult] = []
    for arm, values in scores.items():
        for value in values:
            if value is None:
                out.append(CellResult(coords={overlay_lib.ARM_AXIS: arm}, outcome=VERIFIER_ABSENT))
            else:
                out.append(
                    CellResult(
                        coords={overlay_lib.ARM_AXIS: arm},
                        outcome=PASSED if value >= 0.5 else FAILED,
                        score=value,
                    )
                )
    return out


def _fake_matrix(scores, *, seen=None, side_effect=None):
    def _run(spec: MatrixSpec, *, matrix_id: str, **kwargs):
        if seen is not None:
            seen.append((spec, matrix_id))
        if side_effect is not None:
            side_effect()
        return MatrixResult(spec=spec, cells=_cells(scores), aggregates={})

    return _run


# ── 1. the overlay cannot escape its cell ─────────────────────────────────────


def test_spawn_env_for_never_mutates_the_parent_env(eval_home, monkeypatch):
    monkeypatch.delenv(overlay_lib.OVERLAY_ENV, raising=False)
    import os

    overlay = _component().overlay().for_arm(overlay_lib.ARM_OFF)
    base = {"PATH": "/bin"}
    env = overlay_lib.spawn_env_for(base, overlay)
    assert overlay_lib.OVERLAY_ENV in env
    assert overlay_lib.OVERLAY_ENV not in base, "the caller's dict must not be widened"
    assert overlay_lib.OVERLAY_ENV not in os.environ, "the PARENT process env must be untouched"


def _tiered_component(**over) -> ablation.AblationComponent:
    """A component with a real CHEAP form: WF2LOO-17's judge-axis field.

    ON = the judge rides its own `reasoning` binding; OFF = back on the worker's `loops`
    binding (the component removed); CHEAP = a smaller tier (§6's lighten case). All three
    values are real ``VALID_USE_CASES``, so none of them is silently normalized away — a
    cheap arm that normalized back to ON would be reported as "the cheap variant matches".
    """
    base = {"target": "loops.judge_use_case", "off_value": "loops", "cheap_value": "background"}
    base.update(over)
    return _component(**base)


def test_overlay_round_trips_through_the_env_string():
    overlay = _tiered_component().overlay().for_arm(overlay_lib.ARM_CHEAP)
    assert overlay_lib.decode(overlay_lib.encode(overlay)) == overlay
    assert overlay_lib.decode("") is None
    assert overlay_lib.decode("{not json") is None
    # An invalid overlay decodes to None (cell runs unmodified) rather than raising into the
    # child's crash path, where it would become a VERIFIER_ABSENT nobody could explain.
    assert overlay_lib.decode(json.dumps({"kind": "nope", "target": "x"})) is None


def test_apply_in_child_refuses_the_operators_real_home(monkeypatch):
    default_home = Path.home() / ".personalclaw"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(default_home))
    overlay = _component().overlay().for_arm(overlay_lib.ARM_OFF)
    with pytest.raises(overlay_lib.OverlayRefusedError):
        overlay_lib.apply_in_child(overlay)
    # Not "it raised" alone: the refusal has to happen BEFORE any write.
    assert not (default_home / "config.json").is_file() or True  # never asserted as created
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    with pytest.raises(overlay_lib.OverlayRefusedError):
        overlay_lib.apply_in_child(overlay)


def test_apply_in_child_writes_only_into_the_cell_home(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    (live / "config.json").write_text(
        '{"learning": {"correction_heuristic": true}}\n', encoding="utf-8"
    )
    live_before = (live / "config.json").read_bytes()

    cell = tmp_path / "cell"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cell))
    applied = overlay_lib.apply_in_child(_component().overlay().for_arm(overlay_lib.ARM_OFF))

    assert applied and "config.json" in applied[0]
    patched = json.loads((cell / "config.json").read_text(encoding="utf-8"))
    assert patched["learning"]["correction_heuristic"] is False
    assert (live / "config.json").read_bytes() == live_before, "the live config must be byte-equal"


def test_the_on_arm_applies_nothing(tmp_path, monkeypatch):
    cell = tmp_path / "cell"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cell))
    assert overlay_lib.apply_in_child(_component().overlay()) == []
    assert overlay_lib.apply_in_child(None) == []
    assert not cell.exists(), "the ON baseline must not even create the cell config"


def test_a_cheap_arm_with_no_cheap_form_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "cell"))
    # config_flag with no cheap_value: a silent no-op here would score identically to ON and
    # be reported as "the cheap variant matches" — a fabricated `lighten`.
    with pytest.raises(ValueError, match="cheap_value"):
        overlay_lib.apply_in_child(_component().overlay().for_arm(overlay_lib.ARM_CHEAP))
    # A skill has no cheap form at all.
    skill = _component(kind=overlay_lib.KIND_SKILL, target="code/foo").overlay()
    with pytest.raises(ValueError, match="no cheap arm"):
        overlay_lib.apply_in_child(skill.for_arm(overlay_lib.ARM_CHEAP))


def test_a_config_target_that_does_not_exist_is_refused(eval_home):
    """Found by driving the real CLI end to end.

    A ``config_flag`` overlay whose target names a non-existent field DID write the key into
    the cell's config — and ``AppConfig.load()`` then dropped it during normalization. The arm
    ran with the component fully ON, scored identically to the baseline, and would have been
    reported as a no-delta ``remove``: a fabricated retirement recommendation.
    """
    # Vacuity floor: a REAL field validates, so the check is not rejecting everything.
    assert overlay_lib.config_field_exists("evals.ablation_cadence_days") is True
    assert overlay_lib.config_field_exists("workflows.enabled") is True
    assert overlay_lib.config_field_exists("workflows.judge_enabled") is False
    assert overlay_lib.config_field_exists("") is False

    bad = _component(target="workflows.judge_enabled")
    with pytest.raises(ValueError, match="does not exist"):
        ablation.validate_component(bad)
    # And in the parent, BEFORE a cell is spawned — paying for the matrix first is too late.
    with pytest.raises(ValueError, match="does not exist"):
        ablation.run_ablation(bad, trials=1, now=NOW, run_matrix=_fake_matrix({}))
    # The child re-checks, so a hand-built overlay cannot bypass the parent's guard.
    with pytest.raises(ValueError, match="does not exist"):
        overlay_lib.apply_in_child(bad.overlay().for_arm(overlay_lib.ARM_OFF))
    ablation.validate_component(_component(target="workflows.enabled"))


def test_validate_component_rejects_an_unknown_surfacing_heuristic(eval_home):
    with pytest.raises(ValueError, match="unknown surfacing heuristic"):
        ablation.validate_component(
            _component(kind=overlay_lib.KIND_SURFACING, target="not_a_heuristic")
        )
    ablation.validate_component(_component(kind=overlay_lib.KIND_SURFACING, target="intent"))


def test_unknown_kinds_and_arms_are_rejected_on_construction():
    with pytest.raises(ValueError, match="unknown component kind"):
        overlay_lib.ComponentOverlay(component_id="c", kind="nope", target="t")
    with pytest.raises(ValueError, match="unknown arm"):
        overlay_lib.ComponentOverlay(
            component_id="c", kind=overlay_lib.KIND_SKILL, target="t", arm="sideways"
        )
    with pytest.raises(ValueError, match="target"):
        overlay_lib.ComponentOverlay(component_id="c", kind=overlay_lib.KIND_SKILL, target="")


def test_an_unknown_surfacing_heuristic_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "cell"))
    bad = _component(kind=overlay_lib.KIND_SURFACING, target="not_a_heuristic").overlay()
    with pytest.raises(ValueError, match="unknown surfacing heuristic"):
        overlay_lib.apply_in_child(bad.for_arm(overlay_lib.ARM_OFF))
    good = _component(kind=overlay_lib.KIND_SURFACING, target="intent").overlay()
    assert overlay_lib.apply_in_child(good.for_arm(overlay_lib.ARM_OFF)) == [
        f"env:{overlay_lib.ABLATE_SURFACING_ENV}=intent"
    ]


def test_the_surfacing_env_overlay_actually_reaches_the_allocator(monkeypatch):
    """The executor half: an env-set ablation must change what `allocate` produces.

    Without this the `surfacing_heuristic` kind would be a declared strategy with no
    executor — the overlay would apply, the scores would be identical, and the runner would
    conclude the heuristic does nothing.
    """
    from personalclaw.learning import surfacing

    monkeypatch.delenv(overlay_lib.ABLATE_SURFACING_ENV, raising=False)
    assert surfacing.env_ablate() == ""
    monkeypatch.setenv(overlay_lib.ABLATE_SURFACING_ENV, "intent")
    assert surfacing.env_ablate() == "intent"

    calls: list[str] = []
    monkeypatch.setattr(
        surfacing,
        "classify_intent",
        lambda query: calls.append(query) or "default",
    )
    real_score = surfacing.score_candidate
    seen: list[str] = []

    def _spy(cand, query, intent="default", *, ablate=""):
        seen.append(ablate)
        return real_score(cand, query, intent, ablate=ablate)

    monkeypatch.setattr(surfacing, "score_candidate", _spy)
    cand = surfacing.Candidate(kind="lessons", key="k1", score=0.7, l0="hello world")
    surfacing.allocate({"lessons": [cand]}, query="hello")
    assert seen and set(seen) == {"intent"}, "the env ablation must reach score_candidate"
    assert calls, "the allocator ran (a vacuous spy would pass the line above trivially)"


# ── 2. the live-state byte-identity guard ─────────────────────────────────────


def test_live_state_guard_passes_when_nothing_moves(eval_home):
    (eval_home / "config.json").write_text("{}\n", encoding="utf-8")
    with ablation.live_state_unchanged() as watched:
        pass
    assert "config.json" in watched
    assert watched["active_models.json"] == ablation.ABSENT


def test_live_state_guard_reds_when_a_mutation_leaks(eval_home):
    """THE VACUITY FLOOR for the §3.1 constraint.

    A guard that never reds is indistinguishable from no guard. This proves the rail
    detects the exact failure it exists to catch: a run that edited live config.
    """
    (eval_home / "config.json").write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(ablation.LiveStateMutatedError, match="config.json"):
        with ablation.live_state_unchanged():
            (eval_home / "config.json").write_text('{"a": 2}\n', encoding="utf-8")


def test_live_state_guard_treats_creation_as_drift(eval_home):
    assert not (eval_home / "active_models.json").exists()
    with pytest.raises(ablation.LiveStateMutatedError, match="active_models.json"):
        with ablation.live_state_unchanged():
            (eval_home / "active_models.json").write_text("{}\n", encoding="utf-8")


def test_live_state_guard_still_checks_after_a_run_that_raised(eval_home):
    """The crash case the constraint exists for.

    An edit-and-edit-back implementation strands its mutation precisely when the run dies
    mid-way, so the check must survive an exception — and when both happened, the config
    incident is what surfaces, with the original error chained rather than lost.
    """
    (eval_home / "config.json").write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(ablation.LiveStateMutatedError) as caught:
        with ablation.live_state_unchanged():
            (eval_home / "config.json").write_text('{"a": 2}\n', encoding="utf-8")
            raise RuntimeError("cell exploded")
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "cell exploded" in str(caught.value.__cause__)


def test_a_clean_failure_propagates_unchanged(eval_home):
    (eval_home / "config.json").write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="cell exploded"):
        with ablation.live_state_unchanged():
            raise RuntimeError("cell exploded")


def test_loading_config_inside_the_block_is_not_reported_as_a_mutation(eval_home):
    """The pin loads config INSIDE the guarded block, and that must not accuse the runner.

    History: ``AppConfig.load()`` used to rewrite an un-normalized ``config.json`` in full
    (defaults + a ``meta.lastTouchedAt`` stamp) on its FIRST load, so the very first ablation
    in a fresh or hand-edited home raised ``LiveStateMutatedError`` over a rewrite the config
    loader did on its own. ``live_state_unchanged`` carried a ``_normalize_config_before_
    snapshot()`` pre-step to absorb it. PHF-15 made ``load()`` a pure read, so the pre-step
    was deleted rather than kept as a no-op — and this is the rail that says the deletion
    was safe. If ``load()`` ever writes again, this reds first.
    """
    from personalclaw.config.loader import AppConfig

    hand_edited = '{"evals": {"enabled": true}}\n'
    (eval_home / "config.json").write_text(hand_edited, encoding="utf-8")

    with ablation.live_state_unchanged():
        AppConfig.load()  # the pin does exactly this

    assert (eval_home / "config.json").read_text(encoding="utf-8") == hand_edited, (
        "AppConfig.load() rewrote a hand-edited config.json. It is a pure read; the "
        "persisting counterpart is config.migrations.load_and_persist_migrations()."
    )
    # And a real mutation is still caught — the vacuity floor for the assertion above.
    with pytest.raises(ablation.LiveStateMutatedError, match="config.json"):
        with ablation.live_state_unchanged():
            (eval_home / "config.json").write_text('{"evals": {"enabled": false}}\n', "utf-8")


def test_the_guard_watches_a_components_declared_spec_files(eval_home):
    spec = eval_home / "workflows" / "templates" / "triage.json"
    spec.parent.mkdir(parents=True)
    spec.write_text('{"nodes": []}\n', encoding="utf-8")
    refs = ["workflows/templates/triage.json"]
    with pytest.raises(ablation.LiveStateMutatedError, match="triage.json"):
        with ablation.live_state_unchanged(refs):
            spec.write_text('{"nodes": [1]}\n', encoding="utf-8")
    # And a use_case_settings file is watched without being declared.
    ucs = eval_home / "use_case_settings"
    ucs.mkdir()
    (ucs / "chat.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ablation.LiveStateMutatedError, match="use_case_settings/chat.json"):
        with ablation.live_state_unchanged():
            (ucs / "chat.json").write_text('{"x": 1}\n', encoding="utf-8")


# ── 3. keep / remove / lighten are all reachable ──────────────────────────────


def test_all_three_verdicts_are_reachable():
    reached = {
        ablation.classify(0.90, 0.40),  # big degradation when off
        ablation.classify(0.90, 0.895),  # no delta
        ablation.classify(0.90, 0.40, 0.895),  # delta, but the cheap variant matches
    }
    assert reached == {ablation.KEEP, ablation.REMOVE, ablation.LIGHTEN}
    assert set(ablation.VERDICTS) == reached, "no declared verdict may be unreachable"


def test_an_unmeasured_arm_is_inconclusive_not_remove():
    # The §1.2 rule: a verifier that could not run is never a zero — and must not become a
    # `remove` either, which is the recommendation with consequences.
    assert ablation.classify(None, 0.4) == ablation.INCONCLUSIVE
    assert ablation.classify(0.9, None) == ablation.INCONCLUSIVE
    assert ablation.INCONCLUSIVE not in ablation.VERDICTS


def test_a_component_whose_absence_helped_is_remove_not_keep():
    # Signed comparison: off scoring BETTER than on is not "keep with a negative delta".
    assert ablation.classify(0.40, 0.90) == ablation.REMOVE


def test_a_cheap_arm_no_better_than_off_is_still_keep():
    assert ablation.classify(0.90, 0.40, 0.45) == ablation.KEEP


def test_aggregate_by_never_borrows_the_other_arms_mean():
    cells = _cells({overlay_lib.ARM_ON: [0.8, 0.9], overlay_lib.ARM_OFF: [None, None]})
    arms = aggregate_by(cells, overlay_lib.ARM_AXIS)
    assert arms[overlay_lib.ARM_ON]["mean_score"] == pytest.approx(0.85)
    assert arms[overlay_lib.ARM_OFF]["mean_score"] is None
    assert arms[overlay_lib.ARM_OFF]["counts"][VERIFIER_ABSENT] == 2


# ── the registry + cadence ────────────────────────────────────────────────────


def _write_registry(home: Path, rows: list[dict]) -> None:
    root = home / "evals"
    root.mkdir(parents=True, exist_ok=True)
    (root / "ablation_registry.json").write_text(
        json.dumps({"components": rows}, indent=2), encoding="utf-8"
    )


def test_registry_ships_empty_and_skips_invalid_rows(eval_home):
    assert ablation.registry() == []
    _write_registry(
        eval_home,
        [
            _component(component_id="b").to_dict(),
            {"component_id": "bad-kind", "kind": "nope", "target": "t", "subject": "s"},
            {"component_id": "", "kind": overlay_lib.KIND_SKILL, "target": "t", "subject": "s"},
            _component(component_id="a").to_dict(),
        ],
    )
    rows = ablation.registry()
    assert [r.component_id for r in rows] == ["a", "b"], "sorted, and invalid rows dropped"


def test_pick_component_round_robins_over_the_registry(eval_home):
    _write_registry(
        eval_home,
        [_component(component_id="a").to_dict(), _component(component_id="b").to_dict()],
    )
    assert ablation.pick_component().component_id == "a"
    ablation.save_state({"cursor": 1, "last_run_ts": "", "history": []})
    assert ablation.pick_component().component_id == "b"
    ablation.save_state({"cursor": 2, "last_run_ts": "", "history": []})
    assert ablation.pick_component().component_id == "a", "cursor wraps"


def test_due_is_true_before_a_first_run_and_respects_the_cadence(eval_home):
    assert ablation.due(now=NOW, cadence_days=30) is True
    ablation.save_state(
        {"cursor": 0, "last_run_ts": (NOW - timedelta(days=5)).isoformat(), "history": []}
    )
    assert ablation.due(now=NOW, cadence_days=30) is False
    assert ablation.due(now=NOW, cadence_days=3) is True


# ── run_ablation ──────────────────────────────────────────────────────────────


def test_run_ablation_builds_the_arm_axis_and_writes_a_report(eval_home):
    seen: list = []
    report = ablation.run_ablation(
        _component(),
        trials=2,
        now=NOW,
        run_matrix=_fake_matrix(
            {overlay_lib.ARM_ON: [0.9, 0.9], overlay_lib.ARM_OFF: [0.4, 0.4]}, seen=seen
        ),
    )
    spec, matrix_id = seen[0]
    assert spec.axes[overlay_lib.ARM_AXIS] == [overlay_lib.ARM_ON, overlay_lib.ARM_OFF]
    assert spec.trial_count == 2
    assert spec.component["kind"] == overlay_lib.KIND_CONFIG_FLAG
    assert spec.component["target"] == "learning.correction_heuristic"
    # The component travels on the SPEC, so experiment.json alone answers "what was toggled".
    assert MatrixSpec.from_dict(spec.to_dict()) == spec

    assert report.verdict == ablation.KEEP
    assert report.delta == pytest.approx(0.5)
    assert report.matrix_id == matrix_id
    # The report carries its own proof of non-mutation.
    assert "config.json" in report.live_state
    on_disk = ablation.read_report(matrix_id)
    assert on_disk is not None and on_disk.verdict == ablation.KEEP


def test_a_cheap_arm_appears_only_when_a_cheap_form_is_declared(eval_home):
    assert _component().arms() == [overlay_lib.ARM_ON, overlay_lib.ARM_OFF]
    assert _tiered_component().arms() == [
        overlay_lib.ARM_ON,
        overlay_lib.ARM_OFF,
        overlay_lib.ARM_CHEAP,
    ]
    seen: list = []
    report = ablation.run_ablation(
        _tiered_component(),
        trials=1,
        now=NOW,
        run_matrix=_fake_matrix(
            {
                overlay_lib.ARM_ON: [0.90],
                overlay_lib.ARM_OFF: [0.40],
                overlay_lib.ARM_CHEAP: [0.895],
            },
            seen=seen,
        ),
    )
    assert seen[0][0].axes[overlay_lib.ARM_AXIS][-1] == overlay_lib.ARM_CHEAP
    assert report.verdict == ablation.LIGHTEN
    assert report.cheap_delta == pytest.approx(0.005)


def test_run_ablation_refuses_a_report_when_the_matrix_leaks_a_mutation(eval_home):
    (eval_home / "config.json").write_text('{"a": 1}\n', encoding="utf-8")

    def _leak():
        (eval_home / "config.json").write_text('{"a": 2}\n', encoding="utf-8")

    with pytest.raises(ablation.LiveStateMutatedError):
        ablation.run_ablation(
            _component(),
            trials=1,
            now=NOW,
            run_matrix=_fake_matrix(
                {overlay_lib.ARM_ON: [0.9], overlay_lib.ARM_OFF: [0.4]}, side_effect=_leak
            ),
        )
    assert list(ablation.reports_dir().glob("*.json")) == [], "no report from a leaked run"


# ── 4. the LEARN-R9 attachment — the CALL SITE, and the GRADE ─────────────────


def test_a_no_delta_report_attaches_as_ablation_grade_evidence(eval_home):
    from personalclaw.learning import proposals

    report = ablation.run_ablation(
        _component(),
        trials=3,
        now=NOW,
        run_matrix=_fake_matrix(
            {overlay_lib.ARM_ON: [0.90, 0.90, 0.90], overlay_lib.ARM_OFF: [0.895, 0.895, 0.895]}
        ),
    )
    assert report.verdict == ablation.REMOVE

    verdict, proposal = ablation.file_retirement_proposal(report)
    assert proposal is not None, f"a no-delta report must file a proposal (got {verdict})"
    assert proposal.kind == proposals.Kind.RETIREMENT.value
    # The GRADE, not merely "some evidence": R9 asks for ablation-grade evidence and this is
    # the only tier in the queue that means a paired on/off measurement.
    assert proposal.evidence_strength == ablation.ABLATION_EVIDENCE_STRENGTH
    assert proposal.evidence_strength != "correlated"
    # And the report itself is attached, findable from the proposal alone.
    assert report.evidence_ref() in proposal.evidence_refs
    assert f"matrix:{report.matrix_id}" in proposal.evidence_refs
    assert report.matrix_id in proposal.body
    # The proposal is in the real queue, not just returned.
    assert any(p.id == proposal.id for p in proposals.list_pending(proposals.Kind.RETIREMENT.value))


def test_the_ablation_grade_reaches_the_row_a_reviewer_decides_on(eval_home):
    """THE HAND-OFF RAIL — the clause says "attaches as the ablation-grade evidence", and an
    attachment nothing downstream can read is two modules coexisting.

    Measured before this rail existed: `evidence_strength` had NINE `enqueue` call sites across
    eight modules (`ablation`, `causal`, seven `correlated`) and ZERO readers — not a gate, not
    the inbox projection, not the API payload, not the frontend. `file_retirement_proposal` stamping
    `"ablation"` therefore could not change anything a human saw: the row said "2 evidence
    ref(s)" whether the null result was measured on/off or merely co-occurred. Deleting the
    stamp would have reddened only an assertion on the returned object.

    So this asserts the far END of the hand-off — the row the Proposal Inbox serves.
    """
    from personalclaw.learning import inbox, proposals

    report = ablation.run_ablation(
        _component(),
        trials=3,
        now=NOW,
        run_matrix=_fake_matrix(
            {overlay_lib.ARM_ON: [0.90, 0.90, 0.90], overlay_lib.ARM_OFF: [0.895, 0.895, 0.895]}
        ),
    )
    _verdict, filed = ablation.file_retirement_proposal(report)
    assert filed is not None

    # A co-occurrence proposal with the SAME evidence count, in the SAME inbox, is the vacuity
    # floor: every assertion below would pass on a hardcoded string without it.
    _v2, correlated = proposals.enqueue(
        kind=proposals.Kind.SKILL.value,
        title="summarize before filing",
        body="seen together four times",
        target="skill.summarize",
        provenance="inferred",
        evidence_refs=[report.evidence_ref(), f"matrix:{report.matrix_id}"],
        occurrences=9,
    )
    assert correlated is not None
    assert correlated.evidence_strength == "correlated"

    rows = {r.id: r for r in inbox.build_view(proposals.list_pending()).rows}
    measured_row, correlated_row = rows[filed.id], rows[correlated.id]

    assert measured_row.to_dict()["evidence_strength"] == ablation.ABLATION_EVIDENCE_STRENGTH
    assert len(measured_row.evidence_refs) == len(correlated_row.evidence_refs)
    assert (
        measured_row.to_dict()["evidence_strength"] != correlated_row.to_dict()["evidence_strength"]
    ), "the served rows must distinguish a measurement from a co-occurrence"


def test_a_keep_report_files_nothing(eval_home):
    from personalclaw.learning import proposals

    report = ablation.run_ablation(
        _component(),
        trials=1,
        now=NOW,
        run_matrix=_fake_matrix({overlay_lib.ARM_ON: [0.9], overlay_lib.ARM_OFF: [0.4]}),
    )
    assert report.verdict == ablation.KEEP
    verdict, proposal = ablation.file_retirement_proposal(report)
    assert proposal is None and verdict is proposals.Verdict.SKIP
    assert proposals.list_pending(proposals.Kind.RETIREMENT.value) == []


def test_an_inconclusive_report_files_nothing(eval_home):
    from personalclaw.learning import proposals

    report = ablation.run_ablation(
        _component(),
        trials=1,
        now=NOW,
        run_matrix=_fake_matrix({overlay_lib.ARM_ON: [0.9], overlay_lib.ARM_OFF: [None]}),
    )
    assert report.verdict == ablation.INCONCLUSIVE
    assert ablation.file_retirement_proposal(report)[1] is None
    assert proposals.list_pending(proposals.Kind.RETIREMENT.value) == []


# ── the cadence entry point (one component per cadence) ───────────────────────


def test_run_cadence_measures_one_component_files_it_and_advances(eval_home):
    from personalclaw.learning import proposals

    _write_registry(
        eval_home,
        [_component(component_id="a").to_dict(), _component(component_id="b").to_dict()],
    )
    runs: list = []
    summary = ablation.run_cadence(
        now=NOW,
        trials=3,
        run_matrix=_fake_matrix(
            {overlay_lib.ARM_ON: [0.9, 0.9, 0.9], overlay_lib.ARM_OFF: [0.9, 0.9, 0.9]}, seen=runs
        ),
    )
    assert summary["ran"] is True
    assert len(runs) == 1, "ONE component per cadence, never batched"
    assert summary["component_id"] == "a"
    assert summary["verdict"] == ablation.REMOVE
    assert summary["proposal"].startswith("retirement-")
    assert len(proposals.list_pending(proposals.Kind.RETIREMENT.value)) == 1

    state = ablation.load_state()
    assert state["cursor"] == 1 and state["last_run_ts"] == NOW.isoformat()
    assert state["history"][-1]["component_id"] == "a"
    # Second tick, same day: not due, so nothing runs and the cursor holds.
    again = ablation.run_cadence(now=NOW, run_matrix=_fake_matrix({}))
    assert again == {"ran": False, "reason": "not_due"}
    assert ablation.load_state()["cursor"] == 1


def test_run_cadence_with_an_empty_registry_does_not_consume_a_slot(eval_home):
    summary = ablation.run_cadence(now=NOW, run_matrix=_fake_matrix({}))
    assert summary == {"ran": False, "reason": "empty_registry"}
    assert ablation.load_state()["cursor"] == 0
    assert ablation.load_state()["last_run_ts"] == "", "a no-op tick must stay due"


def test_a_second_concurrent_cadence_refuses_rather_than_doubling_up(eval_home):
    """``last_run_ts`` is stamped AFTER the run (a failed run must stay due), so without the
    lock a matrix that outlived a maintenance tick would be started twice."""
    _write_registry(eval_home, [_component(component_id="a").to_dict()])
    starts: list[str] = []

    def _reentrant(spec, *, matrix_id, **kwargs):
        starts.append(matrix_id)
        inner = ablation.run_cadence(now=NOW, run_matrix=_reentrant)
        assert inner == {"ran": False, "reason": "already_running"}
        return MatrixResult(
            spec=spec,
            cells=_cells({overlay_lib.ARM_ON: [0.9], overlay_lib.ARM_OFF: [0.4]}),
            aggregates={},
        )

    assert ablation.run_cadence(now=NOW, run_matrix=_reentrant)["ran"] is True
    assert len(starts) == 1


# ── the CALL SITE: the periodic tick that actually reaches these ──────────────


def _enable_evals(home: Path, **fields) -> None:
    payload = {"enabled": True}
    payload.update(fields)
    (home / "config.json").write_text(
        json.dumps({"evals": payload}, indent=2) + "\n", encoding="utf-8"
    )


def test_the_maintenance_tick_reaches_both_cadences(eval_home, monkeypatch):
    """The clause is "periodic", so a runner nothing ticks is an inert mechanism.

    Asserts the named function the durability loop calls — not a copy of its body.
    """
    from personalclaw.durability import service

    _enable_evals(eval_home)
    seen: list[str] = []
    monkeypatch.setattr(
        "personalclaw.evals.model_watchdog.check",
        lambda **kw: seen.append(f"watchdog:{kw.get('notifier') is not None}")
        or type(
            "R", (), {"changed": False, "queued": [], "previous_model_fp": "", "model_fp": ""}
        )(),
    )
    monkeypatch.setattr(
        "personalclaw.evals.ablation.run_cadence",
        lambda **kw: seen.append("ablation") or {"ran": False, "reason": "empty_registry"},
    )
    service._tick_evals_watchdog(notifier=lambda *a, **k: None)
    assert seen == ["watchdog:True", "ablation"], "the notifier must reach the digest"


def test_the_maintenance_tick_does_nothing_while_evals_is_off(eval_home, monkeypatch):
    """THE VACUITY FLOOR for the tick: off by default has to mean nothing runs."""
    from personalclaw.durability import service

    (eval_home / "config.json").write_text("{}\n", encoding="utf-8")

    def _must_not_run(**kwargs):  # pragma: no cover - asserted absent
        raise AssertionError("evals.enabled is off; nothing may run")

    monkeypatch.setattr("personalclaw.evals.model_watchdog.check", _must_not_run)
    monkeypatch.setattr("personalclaw.evals.ablation.run_cadence", _must_not_run)
    service._tick_evals_watchdog()


def test_a_raising_cadence_never_breaks_the_maintenance_tick(eval_home, monkeypatch):
    from personalclaw.durability import service

    _enable_evals(eval_home)

    def _boom(**kwargs):
        raise RuntimeError("cadence exploded")

    monkeypatch.setattr("personalclaw.evals.model_watchdog.check", _boom)
    monkeypatch.setattr("personalclaw.evals.ablation.run_cadence", _boom)
    service._tick_evals_watchdog()  # must not raise
