"""Loop-3 field metrics beside lab results + ``lab_field_divergence`` (E3, atom ES-9).

The behaviours the atom is defined by:

1. the field metrics are QUERIES — 👍/👎 from the FEEDBACK-SIGNAL store,
   edit-before-approve from run journals, approval/rejection/undo from the SEL tail and
   the reversal store — computed per subject and stored nowhere new;
2. one row per subject carries lab score (Loop 1, pinned), gate status (Loop 2) and the
   field trend (Loop 3), and every unmeasured cell is ``None``, never ``0.0``;
3. the divergence flag needs ALL THREE clauses — lab rose, field falling, field evidence
   postdating the lab row — and each clause has a vacuous partner that keeps it off;
4. a flagged subject files the §4.2 trust-record demotion signal MECHANICALLY through
   the shipped rung machinery, gated on a standing grant so a standing divergence files
   once and then nothing.

Evidence is driven through the PRODUCTION writers wherever one exists (the
``test_guardrails_autonomy`` discipline): ``sel().log_tool_invocation`` for approval
verdicts, ``feedback.record_feedback`` for thumbs, ``store.append_result`` behind a
complete ``RunPin`` for lab rows, and ``proposals.enqueue``/``attach_gate`` for the
Loop-2 column.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from personalclaw.evals import field_metrics as fm
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import ladder, trust_record


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for every store this seam reads (SEL, feedback, evals, rungs)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    from personalclaw import sel as sel_mod

    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    from personalclaw import feedback as fb

    fb._invalidate()
    yield home
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    fb._invalidate()


@pytest.fixture(autouse=True)
def _no_runs(monkeypatch):
    """No workflow runs unless a test provides them — the store would otherwise read
    whatever database the suite's other tests left behind."""
    monkeypatch.setattr("personalclaw.workflows.store.list_runs", lambda **kw: ([], 0))


KEY = "action.divergent"


def _register(key: str = KEY, **kw) -> au.ActionTypeSpec:
    spec = au.ActionTypeSpec(
        key=key, floor=kw.pop("floor", "draft_only"), ceiling=kw.pop("ceiling", "auto_with_undo")
    )
    au.register_action_type(spec)
    return spec


def _only_registered(monkeypatch):
    """Keep the subject table to the types THIS test registered — the core inventory's
    twenty-one rows are noise here, and `ensure_core_action_types` would re-add them."""
    monkeypatch.setattr("personalclaw.guardrails.rungs.ensure_core_action_types", lambda: None)


def _approve(key: str, *, when: datetime, outcome: str = "approved") -> None:
    """One approval verdict through the production SEL writer, clock moved afterwards."""
    from personalclaw.sel import sel

    log = sel()
    log.log_tool_invocation(
        session_key="_bg",
        source="subagent",
        tool_name="draft reply",
        tool_kind="fs_write",
        outcome=outcome,
        metadata={au.SEL_ACTION_TYPE_KEY: key},
    )
    path = log._path
    lines = path.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    last["timestamp"] = when.isoformat()
    lines[-1] = json.dumps(last)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _thumb(subject: str, verdict: str, *, at: float) -> None:
    """One 👍/👎 through the production feedback writer, clock moved afterwards."""
    from personalclaw import feedback as fb

    rec = fb.record_feedback(
        target_kind="loop_finding",
        target_id=f"t-{subject}-{at}",
        verdict=verdict,
        producer_kind="loop_judge",
        producer_id=subject,
    )
    assert rec is not None
    # record_feedback stamps now(); rewrite the line so the SERIES order is the test's.
    path = fb._path()
    lines = path.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    last["created_at"] = at
    lines[-1] = json.dumps(last)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fb._invalidate()


def _lab_row(subject: str, *, old, new, verdict: str = "win", ts: float | None = None) -> None:
    """One Loop-1 ledger row through the production writer, behind a complete pin."""
    from personalclaw.evals import store
    from personalclaw.evals.pinning import RunPin

    pin = RunPin(
        scenario_id=subject,
        scenario_sha256="a" * 64,
        model_fingerprint={"chat": "Prov:model"},
        prompt_pack_sha256="b" * 64,
        config_snapshot_ref="c" * 16,
    )
    store.append_result(
        {
            "study_id": f"st-{subject}",
            "kind": "template_ab",
            "verdict": verdict,
            "score_old": old,
            "score_new": new,
            "k": 5,
            "ts": f"{(time.time() if ts is None else ts):.0f}",
        },
        pin=pin,
    )


def _falling_thumbs(subject: str, *, start: float) -> None:
    """Six verdicts whose good-rate falls across the halves — the minimum honest sample."""
    for i, verdict in enumerate(["up", "up", "up", "down", "down", "down"]):
        _thumb(subject, verdict, at=start + i * 60.0)


def _run(name: str, run_id: str, started_at: float):
    class _Run:
        pass

    r = _Run()
    r.workflow_name = name
    r.id = run_id
    r.started_at = started_at
    r.created_at = started_at
    return r


# ── 1. the field trend reads both directions and refuses a thin sample ───────


def test_field_trend_needs_a_sample_and_reads_both_directions():
    assert fm.field_trend([True, False]) == ""  # below the minimum — unmeasured, not flat
    assert fm.field_trend([True, True, True, False, False, False]) == "falling"
    assert fm.field_trend([False, False, False, True, True, True]) == "rising"
    assert fm.field_trend([True, True, False, True, True, False]) == "flat"


# ── 2. the per-source signals ─────────────────────────────────────────────────


def test_edit_before_approve_comes_from_the_run_journal(monkeypatch):
    """A human-approved run without an edit is a clean approval; a mid-flight edit is an
    edit-before-approve; a run whose gates were ALL auto-approved yields NOTHING —
    nobody looked, so it is evidence of nothing (the §4.4 rule this seam inherits)."""
    _only_registered(monkeypatch)
    runs = [_run("weekly-report", f"r{i}", 100.0 + i) for i in range(3)]
    ledgers = {
        "r0": [{"kind": "gate_resolved", "answer": {"choice": "ship"}}],
        "r1": [
            {"kind": "user_edited_mid_flight", "ops": []},
            {"kind": "gate_resolved", "answer": {"choice": "ship"}},
        ],
        "r2": [{"kind": "gate_resolved", "answer": {"auto": True}}],
    }
    monkeypatch.setattr("personalclaw.workflows.store.list_runs", lambda **kw: (runs, 3))
    monkeypatch.setattr("personalclaw.workflows.journal.ledger", lambda rid: ledgers[rid])

    rows = {r.subject: r for r in fm.subject_rows()}
    row = rows["weekly-report"]
    assert row.subject_kind == fm.SUBJECT_TEMPLATE
    assert row.field.clean_approved_runs == 1
    assert row.field.edited_runs == 1
    assert row.field.edit_before_approve_rate == 0.5
    assert row.field.signals == 2, "the auto-approved run must contribute no signal"


def test_an_action_types_edit_rate_is_unmeasured_never_zero(monkeypatch):
    """No record anywhere captures an edit on an action type's output (plan 58 defers
    edit-before-approve records), so the cell is None — 0.0 would claim a measurement."""
    _only_registered(monkeypatch)
    _register()
    row = {r.subject: r for r in fm.subject_rows()}[KEY]
    assert row.subject_kind == fm.SUBJECT_ACTION_TYPE
    assert row.field.edit_before_approve_rate is None


def test_thumbs_attach_by_producer_id_and_only_to_their_own_subject(monkeypatch):
    """The attribution is ``producer_id == subject`` — the same dialect
    `autonomy._feedback_rejections` counts, and a neighbour's thumbs never bleed in."""
    _only_registered(monkeypatch)
    _register()
    _register("action.bystander")
    now = time.time()
    _thumb(KEY, "up", at=now - 300)
    _thumb(KEY, "down", at=now - 200)

    rows = {r.subject: r for r in fm.subject_rows()}
    assert rows[KEY].field.ups == 1 and rows[KEY].field.downs == 1
    assert rows[KEY].field.thumb_rate == 0.5
    assert rows["action.bystander"].field.thumb_rate is None, "no thumbs, no rate"


def test_approvals_rejections_and_undos_come_from_the_earned_autonomy_ledger(monkeypatch):
    _only_registered(monkeypatch)
    _register()
    now = datetime.now(timezone.utc)
    _approve(KEY, when=now - timedelta(days=2))
    _approve(KEY, when=now - timedelta(days=1), outcome="rejected")
    record_id = ladder.record_reversal_handle(
        action_type=KEY, rung="auto_with_undo", handle="task:native:abc", label="undo me"
    )
    assert record_id
    ladder._mark_reversed(record_id)  # what a successful reverse_action stamps

    row = {r.subject: r for r in fm.subject_rows()}[KEY]
    assert row.field.approvals == 1
    assert row.field.rejections == 1
    assert row.field.undos == 1
    assert row.field.approval_rate == pytest.approx(1 / 3, abs=1e-3)


def test_an_unreversed_undo_handle_is_not_an_undo(monkeypatch):
    """A pending reversal record means the action RAN, not that it was taken back —
    counting it against the type would punish it for being reversible."""
    _only_registered(monkeypatch)
    _register()
    assert ladder.record_reversal_handle(
        action_type=KEY, rung="auto_with_undo", handle="task:native:xyz", label="pending"
    )
    row = {r.subject: r for r in fm.subject_rows()}[KEY]
    assert row.field.undos == 0
    assert row.field.approval_rate is None, "nothing decided means no rate"


# ── 3. the lab and gate columns ───────────────────────────────────────────────


def test_the_lab_cell_is_the_newest_pinned_row_and_absence_is_none(monkeypatch):
    _only_registered(monkeypatch)
    runs = [_run("weekly-report", "r0", 100.0)]
    monkeypatch.setattr("personalclaw.workflows.store.list_runs", lambda **kw: (runs, 1))
    monkeypatch.setattr(
        "personalclaw.workflows.journal.ledger",
        lambda rid: [{"kind": "gate_resolved", "answer": {"choice": "ship"}}],
    )
    _lab_row("weekly-report", old=0.6, new=0.4, verdict="loss", ts=1_000.0)
    _lab_row("weekly-report", old=0.4, new=0.7, verdict="win", ts=2_000.0)

    rows = {r.subject: r for r in fm.subject_rows()}
    lab = rows["weekly-report"].lab
    assert lab is not None
    assert lab["score"] == 0.7 and lab["previous"] == 0.4, "the newest row wins"
    assert lab["rose"] is True
    assert lab["model_fp"], "the lab cell travels with its pin"
    assert rows.get("weekly-report").gate is None, "no gated proposal, no gate cell"


def test_an_unmeasured_lab_score_is_none_and_can_never_rise(monkeypatch):
    """A study whose arms went unmeasured writes empty score cells; reading them back as
    0.0 would let 'not measured' both render as a score and count as a fall."""
    _only_registered(monkeypatch)
    _register()
    _lab_row(KEY, old=None, new=None, verdict="judge_unreliable")
    lab = {r.subject: r for r in fm.subject_rows()}[KEY].lab
    assert lab is not None
    assert lab["score"] is None and lab["previous"] is None
    assert lab["rose"] is None, "unmeasured is not a rise and not a fall"


def test_the_gate_cell_is_the_newest_proposals_report_via_the_shipped_projection():
    from personalclaw.learning import proposals

    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.SKILL.value,
        title="candidate edit",
        body="body",
        target="weekly-report",
        occurrences=1,
        min_evidence=1,
    )
    assert prop is not None
    report = {
        "state": "gated",
        "before": {"mean_score": 0.9},
        "after": {"mean_score": 0.7},
        "delta": -0.2,
        "regressed": True,
        "pin": {"model_fp": "abc123", "scenario_sha256": "d" * 64},
    }
    assert proposals.attach_gate(prop.id, report)

    got = proposals.newest_gate_for_target("weekly-report")
    assert got is not None and got["regressed"] is True
    assert proposals.newest_gate_for_target("someone-else") is None

    from personalclaw.evals import gate

    cell = fm._gate_view("weekly-report")
    assert cell == gate.summary(report), "one projection — the inbox row's own"


# ── 4. the divergence flag needs all three clauses ────────────────────────────


def _divergent_template(monkeypatch, *, name: str = "weekly-report") -> None:
    """Lab rose at t=1000; six thumbs falling AFTER it. The flag's positive case."""
    _only_registered(monkeypatch)
    _lab_row(name, old=0.4, new=0.7, verdict="win", ts=1_000.0)
    runs = [_run(name, "r0", 900.0)]
    monkeypatch.setattr("personalclaw.workflows.store.list_runs", lambda **kw: (runs, 1))
    monkeypatch.setattr(
        "personalclaw.workflows.journal.ledger",
        lambda rid: [{"kind": "gate_resolved", "answer": {"choice": "ship"}}],
    )
    _falling_thumbs(name, start=time.time() - 3_600)


def test_a_lab_rise_over_a_falling_field_trend_is_flagged(monkeypatch):
    _divergent_template(monkeypatch)
    row = {r.subject: r for r in fm.subject_rows()}["weekly-report"]
    assert row.lab_field_divergence is True
    assert "lab score rose" in row.divergence_reason
    assert row.field.trend == "falling"


def test_no_clause_alone_flags_a_subject(monkeypatch):
    """Each conjunct's vacuous partner: a lab FALL, an UNMEASURED rise, a non-falling
    field, and no lab row at all must each keep the flag off — the flag files a
    demotion, and a demotion must rest on a measured contradiction."""
    _only_registered(monkeypatch)
    now = time.time()

    _register("action.lab_fell")
    _lab_row("action.lab_fell", old=0.7, new=0.4, verdict="loss", ts=1_000.0)
    _falling_thumbs("action.lab_fell", start=now - 3_600)

    _register("action.lab_unmeasured")
    _lab_row("action.lab_unmeasured", old=None, new=None, verdict="judge_unreliable", ts=1_000.0)
    _falling_thumbs("action.lab_unmeasured", start=now - 3_600)

    _register("action.field_rising")
    _lab_row("action.field_rising", old=0.4, new=0.7, verdict="win", ts=1_000.0)
    for i, verdict in enumerate(["down", "down", "down", "up", "up", "up"]):
        _thumb("action.field_rising", verdict, at=now - 3_600 + i * 60)

    _register("action.field_thin")
    _lab_row("action.field_thin", old=0.4, new=0.7, verdict="win", ts=1_000.0)
    _thumb("action.field_thin", "down", at=now - 60)  # one 👎 is not a trend

    _register("action.no_lab")
    _falling_thumbs("action.no_lab", start=now - 3_600)

    rows = {r.subject: r for r in fm.subject_rows()}
    for key in (
        "action.lab_fell",
        "action.lab_unmeasured",
        "action.field_rising",
        "action.field_thin",
        "action.no_lab",
    ):
        assert rows[key].lab_field_divergence is False, key


def test_field_signals_that_predate_the_lab_row_do_not_diverge(monkeypatch):
    """E3 says a POST-SHIP field decline. A falling trend measured entirely before the
    lab row says nothing about the change that row measured."""
    _only_registered(monkeypatch)
    _register()
    _falling_thumbs(KEY, start=time.time() - 7_200)  # thumbs end ~2h ago, in the window
    _lab_row(KEY, old=0.4, new=0.7, verdict="win")  # lab row is NOW — newer than all of them

    row = {r.subject: r for r in fm.subject_rows()}[KEY]
    assert row.field.trend == "falling"
    assert row.lab.get("rose") is True
    assert row.lab_field_divergence is False


# ── 5. the mechanical §4.2 demotion signal ────────────────────────────────────


@pytest.fixture()
def evals_on(monkeypatch):
    monkeypatch.setattr(fm, "_evals_enabled", lambda: True)


@pytest.fixture()
def notices(monkeypatch):
    calls: list[dict] = []

    def spy(keys, **kwargs):
        calls.append({"keys": list(keys), **kwargs})

    monkeypatch.setattr(ladder, "_file_revocation_notice", spy)
    return calls


def _divergent_action_type(monkeypatch, key: str = KEY) -> None:
    _only_registered(monkeypatch)
    _register(key)
    _lab_row(key, old=0.4, new=0.7, verdict="win", ts=1_000.0)
    _falling_thumbs(key, start=time.time() - 3_600)


def test_a_divergent_action_type_loses_its_own_grant_mechanically(monkeypatch, evals_on, notices):
    _divergent_action_type(monkeypatch)
    _register("action.bystander")
    assert au.grant_rung(KEY, "one_tap", evidence_window="10 clean") == "one_tap"
    assert au.grant_rung("action.bystander", "one_tap", evidence_window="10 clean") == "one_tap"

    filed = fm.sweep_lab_field_divergence()

    assert filed == [KEY]
    assert au.resolve_rung(KEY) == "draft_only", "the next decision is the floor"
    record = trust_record.load_record(KEY)
    assert record is not None and record.revoked, "the §4.2 record carries the flag"
    assert (
        au.resolve_rung("action.bystander") == "one_tap"
    ), "scope-attributed evidence demotes its own scope, not the neighbours"
    assert len(notices) == 1 and notices[0]["keys"] == [KEY]
    assert notices[0]["evidence_id"] == f"lab_field_divergence:{KEY}"


def test_a_standing_divergence_files_once_then_nothing(monkeypatch, evals_on, notices):
    """The divergence is a standing condition; the demotion floor entry it leaves is not
    a standing grant, so the second sweep is naturally a no-op."""
    _divergent_action_type(monkeypatch)
    au.grant_rung(KEY, "one_tap", evidence_window="10 clean")

    first = fm.sweep_lab_field_divergence()
    second = fm.sweep_lab_field_divergence()

    assert first == [KEY] and second == []
    assert len(notices) == 1
    record = trust_record.load_record(KEY)
    assert record is not None and record.demotion_count == 1, "no demotion-record spam"


def test_nothing_granted_means_the_sweep_files_nothing(monkeypatch, evals_on, notices):
    _divergent_action_type(monkeypatch)
    assert fm.sweep_lab_field_divergence() == []
    assert notices == []
    assert trust_record.load_record(KEY) is None, "no grant, no record, no demotion"


def test_a_divergent_template_revokes_standing_grants_wholesale(monkeypatch, evals_on, notices):
    """Template-scoped divergence carries the failed-study consequence: the evidence
    behind EVERY standing grant ('the system behaves well') is what it contradicts."""
    _divergent_template(monkeypatch)
    _register("action.alpha")
    _register("action.beta")
    au.grant_rung("action.alpha", "one_tap", evidence_window="10 clean")
    au.grant_rung("action.beta", "one_tap", evidence_window="10 clean")

    filed = fm.sweep_lab_field_divergence()

    assert filed == ["weekly-report"]
    for key in ("action.alpha", "action.beta"):
        assert au.resolve_rung(key) == "draft_only"
        record = trust_record.load_record(key)
        assert record is not None and record.revoked
    assert len(notices) == 1
    assert notices[0]["evidence_id"] == "lab_field_divergence:weekly-report"


def test_the_kill_switch_stops_the_sweep_not_the_flag(monkeypatch, notices):
    """`evals.enabled` off (the default here) suspends the DEMOTION; the row still
    reports the divergence, because hiding a measured contradiction is not a safety
    posture."""
    _divergent_action_type(monkeypatch)
    au.grant_rung(KEY, "one_tap", evidence_window="10 clean")

    assert fm.sweep_lab_field_divergence() == []
    assert notices == []
    row = {r.subject: r for r in fm.subject_rows()}[KEY]
    assert row.lab_field_divergence is True


def test_one_subjects_failure_never_stops_the_rest(monkeypatch, evals_on, notices):
    _divergent_action_type(monkeypatch, "action.first")
    _divergent_action_type(monkeypatch, "action.second")
    au.grant_rung("action.first", "one_tap", evidence_window="e")
    au.grant_rung("action.second", "one_tap", evidence_window="e")
    real = ladder.revoke_scope

    def flaky(key, **kwargs):
        if key == "action.first":
            raise OSError("disk said no")
        return real(key, **kwargs)

    monkeypatch.setattr(ladder, "revoke_scope", flaky)
    assert fm.sweep_lab_field_divergence() == ["action.second"]


# ── 6. the E3 discipline: computed by query, stored nowhere new ───────────────


def test_the_table_is_a_query_and_writes_no_new_file(monkeypatch, _isolated_home):
    _divergent_action_type(monkeypatch)
    before = {p for p in _isolated_home.rglob("*") if p.is_file()}
    fm.subject_rows()
    after = {p for p in _isolated_home.rglob("*") if p.is_file()}
    assert after == before, "Loop 3 is derived — a new file here is a design regression"


# ── 7. the HTTP surface (GET /api/evals/field-metrics) ────────────────────────


def _req(path="/api/evals/field-metrics"):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request("GET", path, app=web.Application())
    request["user"] = "owner"
    return request


def _http(coro):
    import asyncio

    return asyncio.run(coro)


def _body(resp):
    return json.loads(resp.body.decode())


def test_the_route_is_a_404_with_its_own_code_when_evals_is_off(monkeypatch):
    from personalclaw.dashboard.handlers import evals as E

    monkeypatch.setattr(E, "_enabled", lambda: False)
    resp = _http(E.api_evals_field_metrics(_req()))
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "evals_disabled"


def test_a_read_failure_is_a_500_not_an_empty_table(monkeypatch):
    """An unreadable SEL/journal tree rendered as an empty table would say "no subject
    has any field record", which is the opposite of what happened."""
    from personalclaw.dashboard.handlers import evals as E

    monkeypatch.setattr(E, "_enabled", lambda: True)

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(fm, "subject_rows", boom)
    resp = _http(E.api_evals_field_metrics(_req()))
    assert resp.status == 500
    assert _body(resp)["error"]["code"] == "field_metrics_unreadable"


def test_the_rows_are_served_as_computed_with_divergence_intact(monkeypatch):
    """The divergence verdict travels DECIDED; a frontend re-deriving it from the
    visible numbers would eventually disagree with what the sweep demoted on."""
    from personalclaw.dashboard.handlers import evals as E

    monkeypatch.setattr(E, "_enabled", lambda: True)
    _divergent_action_type(monkeypatch)

    resp = _http(E.api_evals_field_metrics(_req()))
    assert resp.status == 200
    subjects = {row["subject"]: row for row in _body(resp)["subjects"]}
    row = subjects[KEY]
    assert row["lab_field_divergence"] is True
    assert row["lab"]["score"] == 0.7 and row["lab"]["model_fp"]
    assert row["field"]["trend"] == "falling"
    assert row["field"]["edit_before_approve_rate"] is None, "unmeasured stays None on the wire"
    assert row["gate"] is None, "no gate run is an absence, not a zero"
