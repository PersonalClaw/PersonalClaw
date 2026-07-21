"""P4c/P4d — the schedule executor dispatches non-agent actions through the
action-provider registry, with the schedule $variable payload enriched.

Covers GatewayOrchestrator._run_action_job: provider lookup, success/skip/done
outcome mapping, unknown-provider failure, and that the ActionContext payload
carries the schedule vars ($last_result / $now / $timezone / $job_id / $job_name)
so a templated action can interpolate them.
"""

import asyncio
from unittest.mock import patch

from personalclaw.action_providers.base import ActionResult
from personalclaw.schedule import (
    ScheduleDefinition,
    ScheduleJob,
    make_command_action,
    make_script_action,
)


def _make_gateway():
    from personalclaw.gateway import GatewayOrchestrator

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw._running_script_ids = set()
    return gw


def _job(action, **overrides):
    job = ScheduleJob(
        id="j1",
        name="Backup",
        action=action,
        schedule=ScheduleDefinition(kind="every", every_secs=3600),
    )
    for k, v in overrides.items():
        setattr(job, k, v)
    return job


def _dispatch(job, result, *, supports_dry_run=False):
    """Run _run_action_job with the registry returning a stub provider->result;
    returns (return_value, captured_action_config, captured_ctx)."""
    gw = _make_gateway()
    captured = {}
    _supports = supports_dry_run

    class _StubProvider:
        display_name = "Stub"
        supports_dry_run = _supports

        async def execute(self, action_config, ctx, timeout=30):
            captured["config"] = action_config
            captured["ctx"] = ctx
            captured["timeout"] = timeout
            return result

    with (
        patch("personalclaw.action_providers.get_action_provider", return_value=_StubProvider()),
        patch("personalclaw.action_providers.registry._ensure_default_providers_registered"),
    ):
        rv = asyncio.run(gw._run_action_job(job))
    return rv, captured


def test_command_action_success_sets_result():
    job = _job(make_command_action("echo hi"))
    rv, cap = _dispatch(job, ActionResult(success=True, stdout="hi"))
    assert rv == "hi"
    assert job.last_status == "ok"
    assert job.last_result == "hi"
    assert cap["config"]["command"] == "echo hi"
    # bash default timeout is 300s when zt_timeout unset
    assert cap["timeout"] == 300


def test_script_action_skip_is_silent():
    job = _job(make_script_action("r.py:run"))
    rv, _ = _dispatch(job, ActionResult(success=True, stdout="quiet", outcome="skip"))
    assert rv is None  # skip → silent success
    assert job.last_status == "ok"


def test_script_action_done_marks_delete_after_run():
    job = _job(make_script_action("once.py:run"))
    rv, cap = _dispatch(job, ActionResult(success=True, stdout="final", outcome="done"))
    assert rv == "final"
    assert job.delete_after_run is True
    # script default timeout is 30s
    assert cap["timeout"] == 30


def test_failure_records_error():
    job = _job(make_command_action("false"))
    rv, _ = _dispatch(job, ActionResult(success=False, error="boom", exit_code=1))
    assert rv is None
    assert job.last_status == "error"
    assert job.last_error == "boom"


def test_zt_timeout_override_passed_through():
    job = _job(make_command_action("slow", 120))
    _, cap = _dispatch(job, ActionResult(success=True, stdout=""))
    assert cap["timeout"] == 120


def test_unknown_provider_fails_without_dispatch():
    gw = _make_gateway()
    job = _job({"provider": "nope", "config": {}})
    with (
        patch("personalclaw.action_providers.get_action_provider", return_value=None),
        patch("personalclaw.action_providers.registry._ensure_default_providers_registered"),
    ):
        rv = asyncio.run(gw._run_action_job(job))
    assert rv is None
    assert job.last_status == "error"
    assert "nope" in job.last_error


def test_dry_run_refused_for_direct_execution_provider():
    """T9 honesty: a provider with no observe mode (bash/webhook/…) must NOT be
    executed on a dry run — that would run REAL side effects while the UI
    promises none. The dispatcher previews the config instead."""
    job = _job(make_command_action("rm -rf /tmp/x"), dry_run=True)
    rv, cap = _dispatch(job, ActionResult(success=True, stdout="ran!"))
    assert "config" not in cap  # provider.execute was NEVER called
    assert rv is None  # silent (skip) — nothing was delivered
    assert job.last_status == "ok"
    assert job.last_outcome == "skip"
    assert "[dry run]" in job.last_result
    assert "rm -rf /tmp/x" in job.last_result  # preview shows what WOULD run


def test_dry_run_dispatches_to_observe_capable_provider():
    """Spawn-based providers (run-prompt/run-workflow) DO get executed on a dry
    run, with dry_run injected into the action config (observe-mode turn)."""
    job = _job({"provider": "run-prompt", "config": {"prompt": "hi"}}, dry_run=True)
    rv, cap = _dispatch(
        job,
        ActionResult(success=True, stdout="launched", outcome="launched"),
        supports_dry_run=True,
    )
    assert cap["config"]["dry_run"] is True
    assert cap["config"]["prompt"] == "hi"
    assert rv == "launched"


def test_context_carries_schedule_variables():
    job = _job(make_command_action("report"), last_result="prev output", timezone="UTC")
    _, cap = _dispatch(job, ActionResult(success=True, stdout="ok"))
    ctx = cap["ctx"]
    assert ctx.event == "schedule:j1"
    assert ctx.context == "prev output"
    assert ctx.payload["last_result"] == "prev output"
    assert ctx.payload["job_id"] == "j1"
    assert ctx.payload["job_name"] == "Backup"
    assert ctx.payload["timezone"] == "UTC"
    assert ctx.payload["now"]  # ISO timestamp present
