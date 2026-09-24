"""Earned-autonomy rung ladder core (AUTONOMY-GUARDRAILS §5, atom AG-6).

Four behaviours the atom is defined by, plus the fail-closed rails:

1. ten clean approvals spread over seven days with zero rejections is ELIGIBLE;
2. one rejection demotes IMMEDIATELY and starts a cooldown;
3. an active incident clamps every resolution above ``one_tap``;
4. eligibility is RECOMPUTED — nothing about the track record ever lands on disk.

Evidence is driven through the PRODUCTION writers, not hand-built JSON:
``sel().log_tool_invocation`` for approval verdicts and ``feedback.record_feedback``
for 👎. A test that fabricates the file the missing writer should have produced is
exactly how a seam gap hides, so the approval events here are written by the same call
the subagent approval callback makes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from personalclaw.guardrails import autonomy as au


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for the store, the SEL and the feedback log.

    ``PERSONALCLAW_HOME`` is set as well as ``config_dir`` patched: the SEL singleton
    resolves its own directory from the environment at instantiation, so patching
    ``config_dir`` alone would leave approval events landing in the real home.
    """
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


KEY = "inbox.reply_draft"


def _register(**kw) -> au.ActionTypeSpec:
    spec = au.ActionTypeSpec(key=kw.pop("key", KEY), **kw)
    au.register_action_type(spec)
    return spec


def _approve(key: str, *, when: datetime, outcome: str = "approved") -> None:
    """Write ONE approval verdict through the production SEL writer.

    ``log_tool_invocation`` stamps its own timestamp, so the event is rewritten with the
    intended one afterwards — the shape (event_type, metadata, outcome) still comes from
    the real writer, only the clock is moved.
    """
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


def _earn_eligibility(key: str = KEY, *, approvals: int = 10, days: int = 7) -> None:
    """``approvals`` human approvals spread evenly across ``days``."""
    now = datetime.now(timezone.utc)
    for i in range(approvals):
        _approve(key, when=now - timedelta(days=days * i / max(1, approvals - 1)))


# ── the ladder itself ─────────────────────────────────────────────────────────


class TestLadder:
    def test_the_rungs_are_ordered_lowest_first(self):
        assert au.RUNGS == ("draft_only", "one_tap", "auto_with_undo", "autonomous")
        assert [au.rung_rank(r) for r in au.RUNGS] == [0, 1, 2, 3]

    def test_an_unknown_rung_name_has_no_rank(self):
        assert au.rung_rank("god_mode") == -1
        assert au.rung_rank("") == -1

    def test_an_unregistered_type_resolves_to_draft_only(self):
        """No declaration, no autonomy — the fail-closed default for an unknown key."""
        assert au.resolve_rung("nobody.declared.this") == au.RUNG_DRAFT_ONLY

    def test_a_registered_type_resolves_to_its_floor_with_no_grant(self):
        _register(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_AUTONOMOUS)
        assert au.resolve_rung(KEY) == au.RUNG_ONE_TAP

    def test_registration_refuses_an_unknown_rung(self):
        with pytest.raises(ValueError, match="unknown ceiling"):
            au.register_action_type(au.ActionTypeSpec(key=KEY, ceiling="god_mode"))
        with pytest.raises(ValueError, match="unknown floor"):
            au.register_action_type(au.ActionTypeSpec(key=KEY, floor="god_mode"))
        assert au.action_type(KEY) is None

    def test_the_default_ceiling_is_below_autonomous(self):
        """Anything leaving the machine ceilings below autonomous BY DEFAULT — which is
        the field default, so a type has to opt in deliberately."""
        spec = au.ActionTypeSpec(key="app:mailer.send", leaves_machine=True)
        assert au.rung_rank(spec.ceiling) < au.rung_rank(au.RUNG_AUTONOMOUS)


# ── done_when 1: the evidence bar ─────────────────────────────────────────────


class TestEligibilityIsEarned:
    def test_ten_clean_approvals_over_seven_days_is_eligible(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        elig = au.promotion_eligibility(KEY)
        assert elig.eligible, elig.reason
        assert elig.clean_approvals == 10
        assert elig.rejections == 0
        assert elig.observed_days >= 7
        assert elig.next_rung == au.RUNG_ONE_TAP

    def test_nine_approvals_is_not_enough(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility(approvals=9)
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert "9 of 10" in elig.reason

    def test_ten_approvals_in_one_afternoon_is_not_a_track_record(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility(days=0)
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert "days required" in elig.reason

    def test_one_sel_rejection_blocks_eligibility(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        _approve(KEY, when=datetime.now(timezone.utc), outcome="rejected")
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert elig.rejections == 1
        assert "rejection" in elig.reason

    def test_a_thumbs_down_counts_as_a_rejection(self):
        """FEEDBACK-SIGNAL (plan 58) is the second evidence source: a 👎 on this type's
        output is attributed by ``producer_id`` and blocks the promotion."""
        from personalclaw.feedback import record_feedback

        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        rec = record_feedback(
            target_kind="inbox_draft",
            target_id="msg-1",
            verdict="down",
            reason="wrong tone",
            producer_kind="prompt",
            producer_id=KEY,
        )
        assert rec is not None
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert elig.rejections == 1

    def test_a_thumbs_up_is_not_counted_as_an_approval(self):
        """👍 is silent-positive in plan 58 — it gives accuracy a denominator, it does
        not buy autonomy. Only a human APPROVAL verdict is evidence."""
        from personalclaw.feedback import record_feedback

        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        for i in range(20):
            record_feedback(
                target_kind="inbox_draft",
                target_id=f"msg-{i}",
                verdict="up",
                producer_kind="prompt",
                producer_id=KEY,
            )
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert elig.clean_approvals == 0

    def test_auto_approvals_are_not_evidence(self):
        """🔴 The bootstrap hole. If an ``auto_approved`` outcome counted, a type that
        already runs unattended would manufacture its own promotion case and climb the
        rest of the ladder on its own output."""
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        now = datetime.now(timezone.utc)
        for i in range(20):
            _approve(KEY, when=now - timedelta(days=i * 0.5), outcome="auto_approved")
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert elig.clean_approvals == 0

    def test_evidence_older_than_the_window_does_not_count(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        now = datetime.now(timezone.utc)
        for i in range(10):
            _approve(KEY, when=now - timedelta(days=400 + i))
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert elig.clean_approvals == 0

    def test_evidence_is_attributed_per_type_not_shared(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _register(key="sessions.auto_tag", ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility(KEY)
        assert au.promotion_eligibility(KEY).eligible
        other = au.promotion_eligibility("sessions.auto_tag")
        assert not other.eligible
        assert other.clean_approvals == 0

    def test_an_unregistered_type_is_never_eligible(self):
        assert not au.promotion_eligibility("nobody.declared.this").eligible

    def test_a_type_at_its_ceiling_is_not_eligible(self):
        _register(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_ONE_TAP)
        _earn_eligibility()
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert "ceiling" in elig.reason

    def test_a_leaves_machine_type_is_never_proposed_autonomous(self):
        """It may be granted deliberately; it is never PROPOSED off a track record."""
        _register(
            key="app:mailer.send",
            floor=au.RUNG_AUTO_WITH_UNDO,
            ceiling=au.RUNG_AUTONOMOUS,
            leaves_machine=True,
        )
        _earn_eligibility("app:mailer.send", approvals=40, days=60)
        elig = au.promotion_eligibility("app:mailer.send")
        assert not elig.eligible
        assert "leaves the machine" in elig.reason
        # The same evidence DOES propose the next rung for a local type.
        _register(key="local.rename", floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTONOMOUS)
        _earn_eligibility("local.rename", approvals=40, days=60)
        assert au.promotion_eligibility("local.rename").next_rung == au.RUNG_AUTONOMOUS


# ── done_when 2: demotion is immediate, with a cooldown ───────────────────────


class TestDemotionIsImmediate:
    def test_one_rejection_demotes_and_starts_a_cooldown(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        assert au.grant_rung(KEY, au.RUNG_ONE_TAP) == au.RUNG_ONE_TAP
        assert au.resolve_rung(KEY) == au.RUNG_ONE_TAP

        record = au.demote(KEY, "user rejected the drafted reply")

        assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY
        assert record.cooldown_until
        state = au.rung_state(KEY)
        assert state is not None
        assert state.rung == au.RUNG_DRAFT_ONLY
        assert [d.cause for d in state.demotions] == ["user rejected the drafted reply"]

    def test_the_cooldown_blocks_re_eligibility(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        au.demote(KEY, "undone")
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert "cooldown" in elig.reason
        assert elig.cooldown_until

    def test_a_lapsed_cooldown_reopens_eligibility(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        au.demote(KEY, "undone")
        # Age the recorded cooldown past now (a demotion 30 days ago with a 14d cooldown).
        path = au._store_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        data[KEY]["demotions"][0]["cooldown_until"] = stale
        path.write_text(json.dumps(data), encoding="utf-8")
        assert au.promotion_eligibility(KEY).eligible

    def test_the_cooldown_also_refuses_a_grant(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        au.demote(KEY, "rejected")
        assert au.grant_rung(KEY, au.RUNG_ONE_TAP) is None
        assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY

    def test_demoting_a_type_with_no_grant_still_starts_the_cooldown(self):
        """The cooldown is the point, and it must apply whether or not the type had
        climbed yet — otherwise a fresh type absorbs a rejection for free."""
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        au.demote(KEY, "rejected on its first outing")
        state = au.rung_state(KEY)
        assert state is not None and len(state.demotions) == 1

    def test_demotion_history_is_bounded(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        for i in range(au._MAX_DEMOTIONS + 5):
            au.demote(KEY, f"cause {i}")
        state = au.rung_state(KEY)
        assert state is not None and len(state.demotions) == au._MAX_DEMOTIONS


# ── promotion is always a click ───────────────────────────────────────────────


class TestPromotionIsAlwaysAClick:
    def test_eligibility_alone_never_promotes(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        for _ in range(5):
            assert au.promotion_eligibility(KEY).eligible
        assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY
        assert not au._store_path().exists()

    def test_a_grant_cannot_exceed_the_ceiling(self):
        _register(ceiling=au.RUNG_ONE_TAP)
        assert au.grant_rung(KEY, au.RUNG_AUTONOMOUS) is None
        assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY

    def test_a_grant_of_an_unknown_rung_is_refused(self):
        _register(ceiling=au.RUNG_AUTONOMOUS)
        assert au.grant_rung(KEY, "god_mode") is None

    def test_a_grant_for_an_unregistered_type_is_refused(self):
        assert au.grant_rung("nobody.declared.this", au.RUNG_ONE_TAP) is None

    def test_a_grant_is_sel_audited(self):
        from personalclaw.sel import sel

        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        au.grant_rung(KEY, au.RUNG_ONE_TAP, evidence_window="12 approvals over 9 days")
        ops = [e.get("operation") for e in sel().recent(50)]
        assert "guardrails.autonomy_granted" in ops
        au.demote(KEY, "undone")
        ops = [e.get("operation") for e in sel().recent(50)]
        assert "guardrails.autonomy_demoted" in ops


# ── done_when 3: the incident clamp ───────────────────────────────────────────


class TestIncidentClamp:
    def test_an_active_incident_clamps_above_one_tap(self, monkeypatch):
        from personalclaw.guardrails import incident

        _register(ceiling=au.RUNG_AUTONOMOUS)
        au.grant_rung(KEY, au.RUNG_AUTONOMOUS)
        assert au.resolve_rung(KEY) == au.RUNG_AUTONOMOUS

        incident.activate("drive test")
        assert au.resolve_rung(KEY) == au.RUNG_ONE_TAP
        # The GRANT is untouched — an incident suspends, it does not demote.
        assert au.granted_rung(KEY) == au.RUNG_AUTONOMOUS
        incident.resume()
        assert au.resolve_rung(KEY) == au.RUNG_AUTONOMOUS

    def test_the_clamp_outranks_a_higher_floor(self):
        """A declared floor above one_tap does not survive the kill switch — that is
        what makes it a kill switch and not a suggestion."""
        from personalclaw.guardrails import incident

        _register(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTONOMOUS)
        assert au.resolve_rung(KEY) == au.RUNG_AUTO_WITH_UNDO
        incident.activate("drive test")
        assert au.resolve_rung(KEY) == au.RUNG_ONE_TAP

    def test_one_tap_and_below_are_untouched(self):
        from personalclaw.guardrails import incident

        _register(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_ONE_TAP)
        incident.activate("drive test")
        assert au.resolve_rung(KEY) == au.RUNG_ONE_TAP

    def test_no_promotion_is_proposed_during_an_incident(self):
        from personalclaw.guardrails import incident

        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        incident.activate("drive test")
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert "incident" in elig.reason


# ── done_when 4: the record is derived, never cached ──────────────────────────


class TestTheRecordIsNeverCached:
    def test_the_store_holds_grants_and_demotions_only(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        au.grant_rung(KEY, au.RUNG_ONE_TAP, evidence_window="10 approvals over 7 days")
        au.demote(KEY, "undone")
        raw = json.loads(au._store_path().read_text(encoding="utf-8"))
        assert set(raw[KEY]) == {"rung", "granted_at", "evidence_window", "demotions"}
        assert set(raw[KEY]["demotions"][0]) == {"at", "cause", "cooldown_until"}
        # No derived counter anywhere in the serialized document.
        blob = json.dumps(raw)
        for banned in ("clean_approvals", "rejections", "observed_days", "eligible"):
            assert banned not in blob

    def test_recomputing_reflects_new_evidence_with_no_store_write(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility(approvals=9)
        assert not au.promotion_eligibility(KEY).eligible
        assert not au._store_path().exists(), "eligibility must not create the store"
        _approve(KEY, when=datetime.now(timezone.utc) - timedelta(days=8))
        assert au.promotion_eligibility(KEY).eligible
        assert not au._store_path().exists()

    def test_a_verdict_reverses_when_the_evidence_does(self):
        """The proof it is derived: adding a rejection flips an eligible type to
        ineligible with no store mutation in between."""
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        assert au.promotion_eligibility(KEY).eligible
        _approve(KEY, when=datetime.now(timezone.utc), outcome="rejected_spawn")
        assert not au.promotion_eligibility(KEY).eligible


# ── fail-closed rails ─────────────────────────────────────────────────────────


class TestFailsClosed:
    def test_a_corrupt_store_grants_nothing(self):
        _register(ceiling=au.RUNG_AUTONOMOUS)
        au.grant_rung(KEY, au.RUNG_AUTONOMOUS)
        assert au.resolve_rung(KEY) == au.RUNG_AUTONOMOUS
        au._store_path().write_text("{not json at all", encoding="utf-8")
        assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY

    def test_a_non_object_store_grants_nothing(self):
        _register(ceiling=au.RUNG_AUTONOMOUS)
        au._store_path().write_text('["autonomous"]', encoding="utf-8")
        assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY

    def test_an_unknown_stored_rung_does_not_resolve_above_the_floor(self):
        _register(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_AUTONOMOUS)
        au._store_path().write_text(json.dumps({KEY: {"rung": "god_mode"}}), encoding="utf-8")
        assert au.resolve_rung(KEY) == au.RUNG_ONE_TAP

    def test_one_corrupt_entry_does_not_erase_the_others(self):
        _register(key="good.type", ceiling=au.RUNG_AUTONOMOUS)
        au._store_path().write_text(
            json.dumps({"bad.type": "not-an-object", "good.type": {"rung": "auto_with_undo"}}),
            encoding="utf-8",
        )
        assert au.resolve_rung("good.type") == au.RUNG_AUTO_WITH_UNDO
        assert au.resolve_rung("bad.type") == au.RUNG_DRAFT_ONLY

    def test_an_unparseable_cooldown_is_treated_as_still_running(self):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()
        au._store_path().write_text(
            json.dumps(
                {KEY: {"rung": "draft_only", "demotions": [{"at": "", "cooldown_until": "soon"}]}}
            ),
            encoding="utf-8",
        )
        assert not au.promotion_eligibility(KEY).eligible
        assert au.grant_rung(KEY, au.RUNG_ONE_TAP) is None

    def test_unreadable_evidence_is_not_eligible(self, monkeypatch):
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility()

        def _boom(*_a, **_kw):
            raise OSError("evidence unavailable")

        monkeypatch.setattr(au, "_sel_evidence", _boom)
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert "could not be read" in elig.reason

    def test_a_ceiling_below_the_floor_resolves_to_the_ceiling(self):
        """A malformed declaration: the lower bound wins, because a ceiling is a
        refusal and a refusal outranks a convenience."""
        _register(floor=au.RUNG_AUTONOMOUS, ceiling=au.RUNG_ONE_TAP)
        assert au.resolve_rung(KEY) == au.RUNG_ONE_TAP


# ── configuration: the operator's bar actually moves the decision ─────────────


class TestConfigWiring:
    def test_the_config_threshold_moves_the_bar(self):
        """The production read path: ``AppConfig.load()`` → ``_rule_for`` → verdict."""
        from personalclaw.config.loader import AppConfig, config_path

        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        _earn_eligibility(approvals=4, days=8)
        assert not au.promotion_eligibility(KEY).eligible

        config_path().write_text(
            json.dumps({"guardrails": {"autonomy": {"clean_approvals": 4}}}), encoding="utf-8"
        )
        assert AppConfig.load().guardrails.autonomy.clean_approvals == 4
        assert au.promotion_eligibility(KEY).eligible

    def test_the_cooldown_length_comes_from_config(self):
        from personalclaw.config.loader import config_path

        _register(ceiling=au.RUNG_AUTO_WITH_UNDO)
        config_path().write_text(
            json.dumps({"guardrails": {"autonomy": {"cooldown_days": 0}}}), encoding="utf-8"
        )
        record = au.demote(KEY, "rejected")
        # A zero-day cooldown expires immediately, so a grant is possible again.
        assert record.cooldown_until
        assert au.grant_rung(KEY, au.RUNG_ONE_TAP) == au.RUNG_ONE_TAP

    def test_a_type_that_declares_its_own_rule_keeps_it(self):
        from personalclaw.config.loader import config_path

        config_path().write_text(
            json.dumps({"guardrails": {"autonomy": {"clean_approvals": 1}}}), encoding="utf-8"
        )
        _register(ceiling=au.RUNG_AUTO_WITH_UNDO, promotion=au.PromotionRule(clean_approvals=25))
        _earn_eligibility(approvals=10, days=9)
        elig = au.promotion_eligibility(KEY)
        assert not elig.eligible
        assert "of 25" in elig.reason

    def test_the_thresholds_are_runtime_editable(self):
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        for leaf in (
            "clean_approvals",
            "min_days",
            "max_rejections",
            "cooldown_days",
            "evidence_window_days",
        ):
            assert f"guardrails.autonomy.{leaf}" in _EDITABLE_CONFIG

    def test_a_config_read_failure_falls_back_to_the_stated_bar(self, monkeypatch):
        def _boom():
            raise RuntimeError("config unavailable")

        monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(_boom))
        assert au._config_rule() == au.PromotionRule()
        assert au._config_window_days() == 30


# ── durability ────────────────────────────────────────────────────────────────


class TestTheStoreTravels:
    def test_the_store_is_in_the_snapshot_core_files(self):
        import personalclaw.snapshot as snap

        staged = {f for files in snap.CORE_FILES.values() for f in files}
        assert "autonomy_rungs.json" in staged

    def test_the_store_is_a_declared_inventory_entry(self):
        from personalclaw.durability import inventory as inv

        entry = inv.claim_for("autonomy_rungs.json")
        assert entry is not None and entry.id == "autonomy_rungs"
        assert not entry.derived and not entry.secret
