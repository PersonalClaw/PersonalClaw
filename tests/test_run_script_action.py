"""P4a — the run-script action provider (zero-token script execution).

Wraps :func:`personalclaw.schedule_script.run_script_sandboxed`; asserts the
missing-field error path and that the sandbox status dict maps onto ActionResult
(ok/done/report/skip → success; error → failure). The sandbox call is stubbed so
no script actually runs.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.action_providers.base import ActionContext, ActionProvider
from personalclaw.action_providers.run_script_provider import RunScriptActionProvider


def _ctx() -> ActionContext:
    return ActionContext(event="Stop", context="go", payload={})


def test_is_action_provider():
    assert isinstance(RunScriptActionProvider(), ActionProvider)
    assert RunScriptActionProvider().name == "run-script"


def test_missing_script_is_error_result():
    res = asyncio.run(RunScriptActionProvider().execute({}, _ctx()))
    assert res.success is False
    assert "script" in res.error.lower()


@pytest.mark.parametrize("status", ["ok", "done", "report", "skip"])
def test_success_statuses_map_to_success(monkeypatch, status):
    import personalclaw.schedule_script as ss

    monkeypatch.setattr(
        ss, "run_script_sandboxed", lambda *a, **k: {"status": status, "message": "out"}
    )
    res = asyncio.run(RunScriptActionProvider().execute({"script": "f.py:run"}, _ctx()))
    assert res.success is True
    assert res.stdout == "out"


def test_error_status_maps_to_failure(monkeypatch):
    import personalclaw.schedule_script as ss

    monkeypatch.setattr(
        ss, "run_script_sandboxed", lambda *a, **k: {"status": "error", "error": "boom"}
    )
    res = asyncio.run(RunScriptActionProvider().execute({"script": "f.py:run"}, _ctx()))
    assert res.success is False
    assert res.error == "boom"


def test_sandbox_exception_is_caught(monkeypatch):
    import personalclaw.schedule_script as ss

    def _raise(*a, **k):
        raise RuntimeError("explode")

    monkeypatch.setattr(ss, "run_script_sandboxed", _raise)
    res = asyncio.run(RunScriptActionProvider().execute({"script": "f.py:run"}, _ctx()))
    assert res.success is False
    assert "explode" in res.error


def test_run_script_registered_by_default():
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
        list_action_providers,
    )

    _ensure_default_providers_registered()
    assert "run-script" in list_action_providers()
    assert get_action_provider("run-script") is not None
