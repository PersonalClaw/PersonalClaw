"""The store-trigger dispatch seam wraps a RAISING provider in the AgentError envelope.

PLATFORM-LEGIBILITY §2 says an uncaught provider exception is wrapped in the shared
WHAT/WHY/FIX envelope at every action-dispatch seam, "so an app-contributed provider
inherits it without knowing it exists". Two of the three seams do it — `hooks.py` and
`event_triggers.py`. The third, `gateway._fire_store_trigger` (the successor
`_run_action_job` became when `ScheduleService` retired — S112, the busiest UNATTENDED
clock/file/webhook/chained path), regressed: its handler emitted a bare
``f"{type(exc).__name__}: {exc}"`` into both the delivered notification and the persisted
run record.

This pins the fix at the SEAM: a provider that raises must surface the coded envelope in
BOTH sinks the handler feeds — the delivered outcome (`_deliver_fire_outcome`) and the
recorded outcome (`_record_fire_outcome` → the run-ledger row + `last_error_summary`) —
never a bare ``TypeName: msg``. It fails if the wrap is removed: the delivered/recorded
error would collapse back to ``"RuntimeError: boom"`` with no WHY/FIX, which is exactly the
regression it guards.
"""

from __future__ import annotations

import asyncio

from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

# The bare string the regressed seam produced, and must never produce again.
_BARE = "RuntimeError: boom"


class _Raiser:
    """A provider that raises rather than returning a failed result — the misbehaving
    (usually app-contributed) shape the envelope exists to make legible."""

    async def execute(self, config, ctx, timeout=30):
        raise RuntimeError("boom")


def _drive_raise(tmp_path, monkeypatch, tid: str = "clock:env") -> tuple[dict, Trigger]:
    """Fire ONE store trigger whose provider raises, through the REAL dispatch seam.

    Returns the ``error`` kwarg the handler passed to `_deliver_fire_outcome` (captured
    before its own truncation) and the trigger's persisted final state.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda name: _Raiser())

    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )

    orch = object.__new__(GatewayOrchestrator)
    delivered: dict = {}

    def _capture(trigger, *, ok: bool, error: str = "") -> None:
        delivered["ok"] = ok
        delivered["error"] = error

    # Shadow the bound method so we capture exactly what the seam hands the delivery sink,
    # without the notification transport (or its dedup) running in the test.
    orch._deliver_fire_outcome = _capture  # type: ignore[method-assign]
    asyncio.run(orch._fire_store_trigger(store.get(tid).trigger, {"trigger_id": tid}))

    row = store.get(tid)
    assert row is not None
    return delivered, row.trigger


def test_the_DELIVERED_error_carries_the_envelope_not_a_bare_TypeName(tmp_path, monkeypatch):
    """🔴 THE REGRESSION. The delivered outcome must be the WHAT/WHY/FIX envelope, whose
    WHAT line names the provider AND still carries the concrete ``TypeName: msg`` — never
    the bare string the regressed seam emitted."""
    delivered, _trigger = _drive_raise(tmp_path, monkeypatch)

    assert delivered["ok"] is False
    err = delivered["error"]
    # It is the rendered envelope: three labeled lines, provider named, cause preserved.
    assert err.startswith("WHAT: action provider 'notify' failed:")
    assert "WHY: the provider raised an exception instead of returning a result" in err
    assert "FIX: " in err
    assert "RuntimeError" in err and "boom" in err
    # …and it is NOT the bare string the seam regressed to. This is the falsifier: revert the
    # wrap and `err` collapses to exactly `_BARE`, which has no WHY/FIX and fails above.
    assert err != _BARE


def test_the_RECORDED_error_carries_the_envelope_in_both_persisted_sinks(tmp_path, monkeypatch):
    """The recorded outcome is the other sink the handler feeds. Both the trigger's
    `last_error_summary` (what the attention card and detail panel read) and the run-ledger
    row's `error` must carry the envelope, not the bare ``TypeName: msg``."""
    _delivered, trigger = _drive_raise(tmp_path, monkeypatch)

    # The FULL envelope the seam renders for this exact failure. Comparing each persisted
    # sink to it byte-for-byte proves the WHOLE envelope survives — in particular the FIX
    # line, which renders LAST (~char 250) and was silently cut mid-word by the old 200-char
    # slice (its `FIX: ` label sat under 200, so a substring check would have missed the cut).
    # Equality also pins the S162 "error, not the lifecycle reason" contract: no "consecutive
    # failures" restatement leaks in. Lower `_ERROR_SUMMARY_MAX` back below the envelope and
    # both equalities fail.
    from personalclaw.action_providers import provider_failure

    expected = provider_failure("notify", RuntimeError("boom")).render()
    assert (
        "FIX: " in expected and "the provider raised an exception" in expected
    )  # guard the fixture

    # `last_error_summary` (the attention card / detail panel line) is exactly the envelope.
    assert trigger.last_error_summary == expected
    assert "consecutive failures" not in trigger.last_error_summary

    # The run-ledger row records the same full envelope, not the bare `TypeName: msg`.
    runs, total = asyncio.run(ScheduleRunStore(tmp_path).list_for_job("clock:env", 0, 5))
    assert total == 1
    assert runs[0]["status"] == "failure"
    assert runs[0]["error"] == expected
    assert runs[0]["error"] != _BARE
