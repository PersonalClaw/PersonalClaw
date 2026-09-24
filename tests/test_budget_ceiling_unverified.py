"""An unreadable spend ceiling must not resolve into an unlimited one (#3458).

``budget_from_config`` swallowed every config-read failure and answered ``Budget()`` —
unlimited — so a config the operator *had* set a ceiling in silently lost it on the one
path an unattended loop spends real money through. Same shape as #3456 and #3457: an
unknown resolved into a permission.

🔑 THE PREMISE IS DRIVEN, NOT MOCKED. Every test here writes a REAL ``config.json`` whose
``max_tokens_per_day`` is a string, under an isolated home. That is enough: the schema
validator logs the type mismatch and then ``int('lots')`` raises out of
``AppConfig.load()``. Nothing is monkeypatched into raising, so the tests witness the seam
rather than a stand-in for it.

🪤 AND THE FIX IS NOT IN THE LOADER. There is no restrictive number to substitute — ``0``
means unlimited, so a "safe default" would be a ceiling the user never chose, which is the
case ``CONFIG_ON_DISCARDED_READ`` deliberately excludes. What the builder can do is refuse
to answer, and then each consumer decides what "the ceiling is unknown" means at its own
seam. Those decisions are what this file pins — the refusals AND the one documented
fail-open, because an exemption nobody wrote down is indistinguishable from an oversight.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def unreadable_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home whose config.json cannot be parsed into a budget."""
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_tokens_per_day": "lots"}}})
    )
    return tmp_path


def test_the_premise_a_malformed_ceiling_makes_the_config_read_fail(unreadable_ceiling: Path):
    """The positive control. Without this, every assertion below could be vacuous —
    a config that loaded fine would exercise nothing."""
    from personalclaw.config.loader import AppConfig

    with pytest.raises(ValueError):
        AppConfig.load()


def test_an_unreadable_ceiling_is_not_an_unlimited_one(unreadable_ceiling: Path):
    from personalclaw.guardrails.budgets import BudgetConfigUnreadable, budget_from_config

    with pytest.raises(BudgetConfigUnreadable):
        budget_from_config()


def test_the_run_scope_ceiling_refuses_the_same_way(unreadable_ceiling: Path):
    from personalclaw.guardrails.budgets import BudgetConfigUnreadable, run_budget_from_config

    with pytest.raises(BudgetConfigUnreadable):
        run_budget_from_config()


def test_an_absent_config_is_still_legitimately_unlimited(tmp_path, monkeypatch):
    """The direction that must NOT change. An operator who set no ceiling has no ceiling;
    only a FAILED read is an unknown. Conflating the two would invent a limit nobody chose."""
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    from personalclaw.guardrails.budgets import budget_from_config, run_budget_from_config

    assert budget_from_config().is_unlimited is True
    assert run_budget_from_config().is_unlimited is True


# ── The per-consumer decisions ────────────────────────────────────────────────────────


def test_auto_execute_refuses_on_an_unverified_ceiling(unreadable_ceiling: Path):
    """`proactive/autoexec.py` documents this refusal already — *"an unverified ceiling
    authorises nothing"* — and it could not fire, because the builder swallowed the read
    failure and handed back a budget that reads as "under the ceiling". So the raise is not
    redundant here; it is the only thing that makes the written refusal reachable."""
    from personalclaw.proactive.autoexec import default_budget_check

    paused, reason = default_budget_check("run-1")()
    assert paused is True
    assert "could not be verified" in reason


@pytest.mark.asyncio
async def test_a_subagent_spawn_is_refused_on_an_unverified_ceiling(unreadable_ceiling: Path):
    """A subagent is unattended work, so it follows autoexec rather than its own
    fail-open: the ceiling it would spend under is unknown."""
    from personalclaw.subagent import SubagentManager
    from tests.test_subagent import _mock_ctx_builder, _mock_sessions

    manager = SubagentManager(
        sessions=_mock_sessions(),
        ctx_builder=_mock_ctx_builder(),
        is_yolo=lambda: True,  # would auto-approve; the budget guard precedes it
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("expensive unattended task")
    assert info is not None and info.done is True
    assert "could not be verified" in info.error


def test_a_subagent_fanout_stops_on_an_unverified_ceiling(unreadable_ceiling: Path):
    """The run scope is what actually bounds a fan-out (N children each spend under the
    day snapshot), so an unverifiable run ceiling stops it with a typed reason."""
    from personalclaw.subagent import SubagentInfo, SubagentManager
    from tests.test_subagent import _mock_ctx_builder, _mock_sessions

    manager = SubagentManager(sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder())
    info = SubagentInfo(id="c1", task="t", agent="", parent_session_key="s")
    info.input_tokens, info.output_tokens, info.cost_usd = 10, 20, 0.5
    with patch("personalclaw.subagent.sel"):
        manager._charge_child_and_check_budget(info)
    stops = [v for v in manager._fanout_stops.values()]
    assert stops and "could not be verified" in stops[0]


def test_the_worker_supervisor_pauses_on_an_unverified_ceiling(unreadable_ceiling: Path):
    """An app's worker is unattended too, and the supervisor's whole job is to say why a
    worker went quiet — so an unknown ceiling produces a pause reason a human can read,
    rather than an unbounded sweep."""
    from personalclaw.apps import worker_runtime

    reason = worker_runtime._budget_pause_reason()
    assert "could not be verified" in reason
    # and it reaches the one policy question the supervisor actually asks
    assert worker_runtime._policy_pause_reason() == reason


def test_the_gateway_skips_an_unattended_fire_on_an_unverified_ceiling(unreadable_ceiling: Path):
    """Every clock, file, webhook and chained fire goes through here. Its old docstring
    chose fail-open so "a broken budget read must never wedge unattended work" — but the
    ceiling is exactly what makes unattended work safe to leave running, so an unknown one
    pauses it and TELLS the user, which is not a wedge."""
    from personalclaw.gateway import GatewayOrchestrator

    notes: list[tuple] = []

    class _FakeState:
        def notify(self, kind, title, body, **kw):
            notes.append((kind, title, body))

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)  # method uses only 2 attrs
    gw.dashboard_state = _FakeState()
    gw._budget_notified = False

    assert gw._day_budget_exceeded(context="cron 'x'") is True
    # one notification, de-duped the same way the exceeded-window one is
    assert gw._day_budget_exceeded(context="cron 'x'") is True
    assert len(notes) == 1
    assert "could not be verified" in notes[0][2]


def test_a_meter_failure_keeps_its_fail_open(tmp_path, monkeypatch):
    """The narrowing, asserted. This change is about the operator's DECISION being lost, not
    about any bookkeeping error: a spend-counter read that fails leaves every one of these
    seams' written fail-open reasons intact, so widening the refusal to it would change
    behaviour for a cause nobody measured."""
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_tokens_per_day": 1000}}})
    )

    def _boom():
        raise RuntimeError("spend.json unreadable")

    monkeypatch.setattr("personalclaw.guardrails.budgets.get_meter", _boom)

    from personalclaw.apps import worker_runtime
    from personalclaw.gateway import GatewayOrchestrator

    assert worker_runtime._budget_pause_reason() == ""
    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw.dashboard_state = None
    gw._budget_notified = False
    assert gw._day_budget_exceeded(context="cron 'x'") is False


def test_browse_keeps_its_documented_fail_open(unreadable_ceiling: Path):
    """The one exemption, and it is a decision rather than an oversight.

    `browse_provider._budget_check` is consulted before every model call *inside* the loop,
    and the model call it guards passes through `ModelCallGuard`, which meters the same day
    scope. Failing browse closed on a bookkeeping error would take out the feature without
    protecting a cent — so it answers `ok` and lets the chokepoint be the control.
    """
    from personalclaw.action_providers.browse_provider import _budget_check

    assert _budget_check() == ("ok", "")
