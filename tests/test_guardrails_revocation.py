"""§4.4 mechanical revocation (ES-15) — the shared revoker and the nodding leg.

`revoke_granted_scopes` is the one seam all four revocation triggers share: it demotes
every STANDING grant through `autonomy.demote` (floor + cooldown + trust-record
`revoked` + SEL) and files ONE notification naming the cause and evidence. These tests
pin the semantics that make the triggers safe to wire mechanically:

* only standing grants are touched — a demotion floor entry is not re-demoted, which is
  what lets a STANDING trigger condition (a nodding gate nobody fixed) fire every sweep
  without spamming demotion records;
* nothing granted ⇒ nothing filed — the notification exists to explain a consequence,
  and there is no consequence;
* the trust record flips `revoked`, so `resolve_rung` clamps to the floor even when the
  flat store diverges (the ES-13 rail).

The per-trigger event wirings are pinned in the sites' own suites
(`test_evals_model_watchdog.py`, `test_learning_attribution.py`,
`test_evals_studies.py`); the nodding CAUSE builder is pinned here because it has no
suite of its own.
"""

from __future__ import annotations

import pytest

from personalclaw.guardrails import autonomy, ladder, trust_record
from personalclaw.guardrails.autonomy import ActionTypeSpec


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    yield home


@pytest.fixture(autouse=True)
def _types():
    autonomy.reset_action_types()
    for key in ("action.alpha", "action.beta", "action.gamma"):
        autonomy.register_action_type(
            ActionTypeSpec(key=key, floor="draft_only", ceiling="auto_with_undo")
        )
    yield
    autonomy.reset_action_types()


@pytest.fixture()
def notices(monkeypatch):
    """Capture the revocation notice instead of reaching the real inbox."""
    calls: list[dict] = []

    def spy(keys, **kwargs):
        calls.append({"keys": list(keys), **kwargs})

    monkeypatch.setattr(ladder, "_file_revocation_notice", spy)
    return calls


def test_revokes_every_standing_grant_and_files_one_notice(notices):
    autonomy.grant_rung("action.alpha", "one_tap", evidence_window="10 clean approvals")
    autonomy.grant_rung("action.beta", "auto_with_undo", evidence_window="20 clean")
    # gamma has no grant — it must not be demoted just for existing.

    revoked = ladder.revoke_granted_scopes(
        cause="the evidence was invalidated", evidence_id="ev-1", source="test"
    )

    assert sorted(revoked) == ["action.alpha", "action.beta"]
    for key in revoked:
        record = trust_record.load_record(key)
        assert record is not None and record.revoked, f"{key} must carry the revoked flag"
        assert autonomy.resolve_rung(key) == "draft_only", "the next decision is the floor"
    assert trust_record.load_record("action.gamma") is None, "ungranted scope untouched"
    assert len(notices) == 1, "one event, one notice — not one per scope"
    assert notices[0]["evidence_id"] == "ev-1"
    assert sorted(notices[0]["keys"]) == ["action.alpha", "action.beta"]


def test_nothing_granted_means_nothing_revoked_and_nothing_filed(notices):
    assert ladder.revoke_granted_scopes(cause="c", evidence_id="ev-2", source="test") == []
    assert notices == []


def test_a_second_pass_is_naturally_idempotent(notices):
    """A standing condition (a nodding gate) re-fires every sweep; the first revocation
    must be the only one, because a demotion floor entry is not a standing grant."""
    autonomy.grant_rung("action.alpha", "one_tap", evidence_window="10 clean")

    first = ladder.revoke_granted_scopes(cause="c", evidence_id="ev-3", source="test")
    second = ladder.revoke_granted_scopes(cause="c", evidence_id="ev-3", source="test")

    assert first == ["action.alpha"] and second == []
    assert len(notices) == 1
    record = trust_record.load_record("action.alpha")
    assert record is not None and record.demotion_count == 1, "no demotion-record spam"


def test_one_unwritable_record_does_not_stop_the_rest(notices, monkeypatch):
    autonomy.grant_rung("action.alpha", "one_tap", evidence_window="e")
    autonomy.grant_rung("action.beta", "one_tap", evidence_window="e")
    real = autonomy.demote

    def flaky(key, cause):
        if key == "action.alpha":
            raise OSError("disk said no")
        return real(key, cause)

    monkeypatch.setattr(ladder, "demote", flaky)

    revoked = ladder.revoke_granted_scopes(cause="c", evidence_id="ev-4", source="test")

    assert revoked == ["action.beta"], "the failure is logged, the rest still revoke"
    assert len(notices) == 1 and notices[0]["keys"] == ["action.beta"]


# ── the nodding cause builder (workflows side of the boundary) ─────────────────────


def test_nodding_cause_is_empty_when_there_are_no_runs():
    from personalclaw.workflows.handlers import nodding_revocation_cause

    assert nodding_revocation_cause() == ""


def test_nodding_cause_names_the_template_and_never_raises(monkeypatch):
    """A gate with a 100% pass rate over enough real verdicts is named; the judging is
    done by the REAL detector — only the run/journal reads are stubbed."""
    from personalclaw.workflows import handlers as H
    from personalclaw.workflows import journal as journal_mod

    class _Run:
        id = "run-1"
        workflow_name = "nightly-digest"

    monkeypatch.setattr(H.store, "list_runs", lambda **kw: ([_Run()], 1))
    # Journal entries are FLAT (kind + fields at top level), per `verdicts_from_journal`.
    entries = [
        {
            "kind": "judge_verdict",
            "run_id": "run-1",
            "node_id": "gate-1",
            "template": "nightly-digest",
            "verdict": "PASS",
            "overall": 0.9,
        }
        for _ in range(30)
    ]
    monkeypatch.setattr(journal_mod, "ledger", lambda run_id, kinds=None: entries)

    cause = H.nodding_revocation_cause()

    assert "nightly-digest" in cause
    assert "does not check" in cause

    # And a read failure revokes nothing: the cause is empty, never an exception.
    def boom(**kw):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(H.store, "list_runs", boom)
    assert H.nodding_revocation_cause() == ""
