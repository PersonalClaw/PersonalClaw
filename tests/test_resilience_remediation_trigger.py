"""PR2-8 — the remediation engine as ONE adaptive-clock trigger, and its runs in the digest.

The atom's `done_when` has two clauses and this file covers both, each as a CALL-SITE assertion
rather than a config one:

1. **"runs as ONE adaptive-clock trigger (`created_by: system`) on the Automations page instead of
   the heartbeat job."** A trigger row that exists in storage but never renders is this repo's
   recurring defect, so the listing is asserted through `api_triggers`' real projection — the same
   function the page's `api.schedules()` fetch calls — and the heartbeat half is asserted by DRIVING
   `_beat` with the engine's own entry point instrumented, which fails if anyone re-adds a heartbeat
   driver. `tests/../web/src/pages/triggers/remediationTriggerListed.test.tsx` is the paired
   front-end half: it fails if the row stops rendering.

2. **"its runs are picked up by the runs-inbox learned-overnight digest like any other run."**
   Driven through `GatewayOrchestrator._deliver_fire_outcome` — the single point every store-backed
   fire reports from — into a real `DashboardState.notify`, so the rule resolution
   (`resolve_rule_for_legacy` → mode) is the shipped one. A hand-built note in the queue would prove
   nothing: it would skip the very selection this clause is about. Each pickup assertion is paired
   with a run that must NOT be picked up, so a queue that swallowed everything would fail.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from personalclaw.action_providers import remediation_provider as P
from personalclaw.action_providers.base import ActionContext

# ── fixtures ────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """One isolated home for EVERY reader this path touches.

    `PERSONALCLAW_HOME` is the lever (read per call, cached nowhere), but the import-bound
    `config_dir` re-exports are patched too: `notification_rules`, `dashboard.state` and the
    provider's own store write each hold their own binding, and patching three of four is how a
    "nothing was written" assertion passes while the fourth wrote to the real home.
    """
    from personalclaw.providers import entity_routes as er

    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.notification_rules.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(er, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(er, "_entity_settings_path", lambda entity: tmp_path / f"{entity}.json")
    # The redirect itself is asserted, not assumed: a fixture that silently failed to point the
    # config loader at `tmp_path` would run every destructive test below against the real home.
    from personalclaw.config.loader import config_dir as _loader_config_dir

    assert _loader_config_dir() == tmp_path
    return tmp_path


@pytest.fixture()
def store(home):
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=home)


def _cfg(**kw):
    base = dict(
        enabled=True,
        target_score=90,
        max_cost_usd=1.0,
        idle_minutes_healthy=60,
        tick_minutes_degraded=5,
    )
    base.update(kw)
    return SimpleNamespace(remediation=SimpleNamespace(**base))


def _patch_config(monkeypatch, **kw):
    """Pin `resilience.remediation` without writing a whole config document."""
    from personalclaw.config.loader import AppConfig

    fake = SimpleNamespace(resilience=_cfg(**kw))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: fake))
    return fake


# ── clause 1a: the adaptive clock kind ──────────────────────────────────────────────────


class TestAdaptiveClockKind:
    """§4.3's "adaptive clock kind" — the cadence primitive, independent of the engine."""

    def test_the_kind_is_accepted_and_both_cadences_are_required(self):
        from personalclaw.triggers.models import CLOCK_KINDS, validate_spec

        assert "adaptive" in CLOCK_KINDS
        ok = validate_spec(
            "clock",
            {"kind": "adaptive", "interval_secs_healthy": 3600, "interval_secs_degraded": 300},
        )
        assert [i.message for i in ok] == []
        # VACUITY: the same spec missing a cadence must be an ERROR, or the check above is
        # asserting nothing about the keys it names.
        missing = validate_spec("clock", {"kind": "adaptive", "interval_secs_healthy": 3600})
        assert [i.path for i in missing] == ["spec.interval_secs_degraded"]
        assert all(i.severity == "error" for i in missing)

    def test_the_state_picks_the_cadence(self):
        from personalclaw.triggers.arm import cadence_next_fire
        from personalclaw.triggers.models import Trigger

        spec = {"kind": "adaptive", "interval_secs_healthy": 3600, "interval_secs_degraded": 300}
        healthy = Trigger(id="t", name="t", kind="clock", spec=dict(spec, health_state="healthy"))
        degraded = Trigger(id="t", name="t", kind="clock", spec=dict(spec, health_state="degraded"))
        assert cadence_next_fire(healthy, now=1000.0) == 1000.0 + 3600
        assert cadence_next_fire(degraded, now=1000.0) == 1000.0 + 300
        # An absent state reads as healthy — a row written before its first run has no verdict, and
        # taking the SHORT tick on no evidence would make every fresh install poll every 5 minutes.
        blank = Trigger(id="t", name="t", kind="clock", spec=dict(spec))
        assert cadence_next_fire(blank, now=1000.0) == 1000.0 + 3600

    def test_the_cadence_reads_the_spec_only(self, monkeypatch):
        """PURE. A cadence that asked the remediation engine "am I healthy?" would put store I/O
        inside every wake computation — so `measure_deficits` must not be reachable from here."""
        from personalclaw.resilience import remediation as rem
        from personalclaw.triggers.arm import cadence_next_fire
        from personalclaw.triggers.models import Trigger

        def _boom():
            raise AssertionError("arming must not measure the store")

        monkeypatch.setattr(rem, "measure_deficits", _boom)
        monkeypatch.setattr(rem, "health_score", _boom)
        t = Trigger(
            id="t",
            name="t",
            kind="clock",
            spec={"kind": "adaptive", "interval_secs_healthy": 60, "interval_secs_degraded": 30},
        )
        assert cadence_next_fire(t, now=5.0) == 65.0

    def test_the_row_describes_its_own_cadence(self):
        """`describe_cadence` is the string the Triggers list renders in the `schedule` column.
        Falling through to the bare kind name would print "adaptive" and answer nothing."""
        from personalclaw.triggers.models import Trigger
        from personalclaw.triggers.schedule_view import describe_cadence

        t = Trigger(
            id="t",
            name="t",
            kind="clock",
            spec={
                "kind": "adaptive",
                "interval_secs_healthy": 3600,
                "interval_secs_degraded": 300,
                "health_state": "degraded",
            },
        )
        described = describe_cadence(t)
        assert "60m" in described and "5m" in described and "degraded" in described


# ── clause 1b: ONE trigger, created_by system, listed on the page ────────────────────────


class TestTheTriggerIsRegisteredAndListED:
    def test_reconcile_creates_ONE_system_adaptive_clock_trigger(self, store, monkeypatch):
        _patch_config(monkeypatch)
        P.reconcile_remediation_trigger(store)

        row = store.get(P.REMEDIATION_TRIGGER_ID)
        assert row is not None, "the engine's trigger was not registered"
        t = row.trigger
        assert t.created_by == "system"
        assert t.kind == "clock" and t.spec.get("kind") == "adaptive"
        assert t.enabled is True
        assert t.workflow["inline"]["provider"] == P.PROVIDER_NAME
        assert row.ok, [i.message for i in row.errors]
        # ARMED. A registered-but-unarmed trigger never fires — the S108 defect.
        assert t.next_fire_at, "the trigger was registered without a next fire"

    def test_reconcile_is_idempotent_and_does_not_duplicate(self, store, monkeypatch):
        _patch_config(monkeypatch)
        for _ in range(3):
            P.reconcile_remediation_trigger(store)
        clocks = [r for r in store.load() if r.trigger.kind == "clock"]
        assert len(clocks) == 1, [r.trigger.id for r in clocks]

    def test_reconcile_CONVERGES_the_configured_cadence(self, store, monkeypatch):
        """Both cadences live in config, so an edit in Settings must reach the spec without the
        user knowing a trigger exists (`reconcile_digest_cron`'s contract)."""
        _patch_config(monkeypatch, idle_minutes_healthy=60, tick_minutes_degraded=5)
        P.reconcile_remediation_trigger(store)
        assert store.get(P.REMEDIATION_TRIGGER_ID).trigger.spec["interval_secs_healthy"] == 3600

        _patch_config(monkeypatch, idle_minutes_healthy=30, tick_minutes_degraded=2)
        P.reconcile_remediation_trigger(store)
        spec = store.get(P.REMEDIATION_TRIGGER_ID).trigger.spec
        assert spec["interval_secs_healthy"] == 1800
        assert spec["interval_secs_degraded"] == 120

    def test_reconcile_preserves_the_run_produced_state(self, store, monkeypatch):
        """`health_state` is NOT converged: resetting it every boot would make a degraded install
        sleep for the healthy interval after each restart."""
        _patch_config(monkeypatch)
        P.reconcile_remediation_trigger(store)
        t = store.get(P.REMEDIATION_TRIGGER_ID).trigger
        t.spec = dict(t.spec, health_state="degraded")
        store.upsert(t)

        P.reconcile_remediation_trigger(store)
        assert store.get(P.REMEDIATION_TRIGGER_ID).trigger.spec["health_state"] == "degraded"

    def test_the_engine_switch_disables_the_row_instead_of_hiding_it(self, store, monkeypatch):
        _patch_config(monkeypatch, enabled=False)
        P.reconcile_remediation_trigger(store)
        row = store.get(P.REMEDIATION_TRIGGER_ID)
        assert row is not None, "a disabled engine must still leave a visible switch"
        assert row.trigger.enabled is False

    def test_the_provider_is_in_the_registry_AND_every_gate_set(self):
        """Four sets must agree. A provider in one but not the others saves and then refuses to
        dispatch, which is the failure this repo has hit repeatedly."""
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
            list_action_providers,
        )
        from personalclaw.triggers.screen import WRITE_CAPABLE_PROVIDERS
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        # The registry populates LAZILY, on the first action execution, so this must drive the same
        # entry point the fire path does. Reading it cold returns [] for every built-in — which is
        # how this assertion first failed as a red for the wrong reason.
        _ensure_default_providers_registered()
        assert "notification-digest" in list_action_providers(), "the registry did not populate"
        assert P.PROVIDER_NAME in list_action_providers()
        assert get_action_provider(P.PROVIDER_NAME) is not None
        assert P.PROVIDER_NAME in ALLOWED_HOOK_PROVIDERS
        assert P.PROVIDER_NAME in WRITE_CAPABLE_PROVIDERS

    def test_the_trigger_carries_the_write_capable_grant(self, store, monkeypatch):
        """Decision 7: the engine prunes and re-indexes unattended, so the fence needs a frozen
        grant. An empty capability block is denied at fire time."""
        _patch_config(monkeypatch)
        P.reconcile_remediation_trigger(store)
        caps = store.get(P.REMEDIATION_TRIGGER_ID).trigger.capabilities
        assert caps, "the system trigger was registered with an empty capability block"

    def test_the_API_LISTS_it_on_the_triggers_page(self, store, monkeypatch, home):
        """🔴 THE CALL SITE, not the storage. `api_triggers` is what `api.schedules()` fetches, so
        this is the projection the Automations page actually renders from. A row present in
        `triggers.json` and absent here is the present-and-invisible defect."""
        from personalclaw.dashboard.handlers import triggers as H

        _patch_config(monkeypatch)
        P.reconcile_remediation_trigger(store)
        monkeypatch.setattr(H, "_trigger_store", lambda: store)

        rows = H._schedule_rows(SimpleNamespace(conversation_log=None))
        listed = {r["raw_id"]: r for r in rows}
        assert P.REMEDIATION_TRIGGER_ID in listed, sorted(listed)
        row = listed[P.REMEDIATION_TRIGGER_ID]
        # The columns the list draws per row must be populated, not merely present.
        assert row["name"]
        assert "adaptive" in row["schedule"]
        assert row["action"]["provider"] == P.PROVIDER_NAME

    def test_it_plots_on_the_WEEK_view_too(self, store, monkeypatch):
        """The other half of the same page. An adaptive clock carries no `interval_secs`, so the
        grid's arithmetic path reads 0 and drops it — a live maintenance automation firing hourly
        and plotting nothing is the same invisible-but-firing defect the interval path already
        records."""
        import datetime as _dt

        from personalclaw.dashboard.handlers import triggers as H

        _patch_config(monkeypatch)
        P.reconcile_remediation_trigger(store)
        monkeypatch.setattr(H, "_trigger_store", lambda: store)

        trigger = store.get(P.REMEDIATION_TRIGGER_ID).trigger
        cells, _truncated = H._project_one(
            trigger, start=_dt.datetime.now(_dt.timezone.utc), days=7
        )
        assert cells, "the engine plots no fires on the week grid"

    def test_the_listing_assertion_can_fail(self, store, monkeypatch):
        """VACUITY for the test above: with no reconcile, the id must be ABSENT. A projection that
        invented a row would make the positive assertion unfalsifiable."""
        from personalclaw.dashboard.handlers import triggers as H

        monkeypatch.setattr(H, "_trigger_store", lambda: store)
        rows = H._schedule_rows(SimpleNamespace(conversation_log=None))
        assert P.REMEDIATION_TRIGGER_ID not in {r["raw_id"] for r in rows}


# ── clause 1c: instead of the heartbeat job ─────────────────────────────────────────────


class TestTheHeartbeatNoLongerRemediates:
    @pytest.mark.asyncio
    async def test_a_beat_never_runs_the_engine(self, monkeypatch, tmp_path):
        """🔴 DRIVES the real `_beat` with the engine's entry point instrumented, rather than
        asserting a method is absent. Re-adding a heartbeat driver — under any name — fails here."""
        import personalclaw.heartbeat as hb_mod
        from personalclaw.resilience import remediation as rem

        calls: list = []
        monkeypatch.setattr(rem, "run_remediation", lambda **kw: calls.append(kw))

        svc = hb_mod.HeartbeatService.__new__(hb_mod.HeartbeatService)
        svc._tick = 1440  # the tick the retired daily pass used to fire on
        svc._processing = True  # skip the HEARTBEAT.md work
        svc._consolidator = None
        svc._on_due_commitments = None
        svc._on_auto_archive = None
        svc._interval = 60

        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: tmp_path / "HEARTBEAT.md"
        try:
            await svc._beat()
        finally:
            hb_mod.heartbeat_path = original
        assert calls == [], "the heartbeat still drives the remediation engine"

    @pytest.mark.asyncio
    async def test_the_trigger_path_DOES_run_the_engine(self, home, monkeypatch):
        """VACUITY for the test above. "nobody calls it" is only meaningful beside a leg that
        proves the instrumented seam is the one a real run goes through."""
        from personalclaw.resilience import remediation as rem

        calls: list = []
        monkeypatch.setattr(
            rem, "run_remediation", lambda **kw: calls.append(kw) or rem.RunResult(100.0, 100.0)
        )
        _patch_config(monkeypatch)
        await P.SelfRemediationActionProvider().execute({}, ActionContext(event="cron"))
        assert len(calls) == 1, "the trigger's provider did not run the engine"

    def test_the_deleted_heartbeat_symbols_are_gone(self):
        """The clean break, stated once. Named so a re-introduction is a conversation, not a
        silent second mechanism."""
        import personalclaw.heartbeat as hb_mod

        assert not hasattr(hb_mod.HeartbeatService, "_maybe_remediate")
        assert not hasattr(hb_mod.HeartbeatService, "_legacy_maintenance")
        assert not hasattr(hb_mod, "_FTS_REBUILD_TICKS")
        assert not hasattr(hb_mod, "_PRUNE_TICKS")


# ── the adaptive half: the run re-arms its own clock ────────────────────────────────────


class TestTheRunRearmsItsOwnClock:
    def _fire(self):
        return asyncio.run(
            P.SelfRemediationActionProvider().execute({}, ActionContext(event="cron"))
        )

    def _seed(self, store, monkeypatch, **cfg):
        _patch_config(monkeypatch, **cfg)
        P.reconcile_remediation_trigger(store)

    def test_a_healthy_run_takes_the_LONG_cadence(self, store, monkeypatch):
        from personalclaw.resilience import remediation as rem
        from personalclaw.triggers.service import to_epoch

        self._seed(store, monkeypatch)
        monkeypatch.setattr(rem, "run_remediation", lambda **kw: rem.RunResult(100.0, 100.0))
        result = self._fire()
        assert result.success

        t = store.get(P.REMEDIATION_TRIGGER_ID).trigger
        assert t.spec["health_state"] == "healthy"
        import time as _time

        assert to_epoch(t.next_fire_at) - _time.time() > 1800

    def test_a_DEGRADED_run_shortens_the_cadence(self, store, monkeypatch):
        """The whole point of an adaptive clock. Asserted against the healthy leg above, because a
        cadence test that only checks one branch cannot tell adaptive from constant."""
        import time as _time

        from personalclaw.resilience import remediation as rem
        from personalclaw.triggers.service import to_epoch

        self._seed(store, monkeypatch)
        monkeypatch.setattr(rem, "run_remediation", lambda **kw: rem.RunResult(20.0, 40.0))
        assert self._fire().success

        t = store.get(P.REMEDIATION_TRIGGER_ID).trigger
        assert t.spec["health_state"] == "degraded"
        assert to_epoch(t.next_fire_at) - _time.time() < 600

    def test_the_healthy_threshold_is_above_the_target_score(self, monkeypatch, store):
        """A store brought back to exactly `target_score` is NOT healthy: the engine stopped
        spending, not finished. Sleeping an hour on it would be the wrong reading."""
        from personalclaw.resilience import remediation as rem

        assert rem.HEALTHY_SCORE > rem._DEFAULT_TARGET_SCORE
        self._seed(store, monkeypatch, target_score=90)
        monkeypatch.setattr(rem, "run_remediation", lambda **kw: rem.RunResult(80.0, 90.0))
        assert self._fire().success
        assert store.get(P.REMEDIATION_TRIGGER_ID).trigger.spec["health_state"] == "degraded"

    def test_a_failed_job_fails_the_FIRE(self, store, monkeypatch):
        """An absent prune is invisible by nature, so a failed job must not read as a quiet
        success — it has to reach the trigger's failure route."""
        from personalclaw.resilience import remediation as rem

        self._seed(store, monkeypatch)
        monkeypatch.setattr(
            rem,
            "run_remediation",
            lambda **kw: rem.RunResult(
                50.0,
                50.0,
                jobs=[{"id": "sel.prune", "status": "error"}],
                stopped_reason="exhausted",
            ),
        )
        result = self._fire()
        assert result.success is False
        assert "sel.prune" in result.error

    def test_a_disabled_engine_does_not_run_it(self, store, monkeypatch):
        from personalclaw.resilience import remediation as rem

        _patch_config(monkeypatch, enabled=False)
        calls: list = []
        monkeypatch.setattr(rem, "run_remediation", lambda **kw: calls.append(kw))
        result = self._fire()
        assert result.success and calls == []


# ── clause 2: the runs-inbox digest picks the runs up like any other run ────────────────


def _queued(home: Path) -> list[dict]:
    from personalclaw import notification_rules as nr

    path = nr.digest_queue_path()
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _wire_state(monkeypatch):
    """A REAL `DashboardState`, so `notify` applies the real gate and the real per-(source, kind)
    rule. A MagicMock would record a call and prove nothing about the selection."""
    from personalclaw.dashboard.state import DashboardState

    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


def _set_rule(home: Path, key: str, mode: str) -> None:
    """Write ONE rule into the user's rules store — the same document Settings edits.

    Through `notification_rules.save_rules` rather than to a hand-composed path: the store lives at
    `entity_settings/notification_rules.json`, and my first version wrote `notification_rules.json`
    at the home root — which the loader never reads, so every "landed in the digest" assertion was
    measuring a default rule instead of the one it set.
    """
    from personalclaw import notification_rules as nr

    doc = nr.load_rules()
    rules = dict(doc.get("rules") or {})
    rules[key] = {"mode": mode}
    nr.save_rules({**doc, "rules": rules})
    assert nr.resolve_rule_for_legacy("info").mode == mode, "the rule write did not take"


def _deliver(monkeypatch, trigger, *, ok: bool, error: str = ""):
    """Report a fire outcome through the SHIPPED path: `GatewayOrchestrator._deliver_fire_outcome`
    is the single point every store-backed run reports from, so this is "like any other run" by
    construction rather than by resemblance."""
    from personalclaw.gateway import GatewayOrchestrator

    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = _wire_state(monkeypatch)
    orch._deliver_fire_outcome(trigger, ok=ok, error=error)
    return orch.dashboard_state


class TestTheRunsReachTheDigest:
    def _trigger(self, store, monkeypatch):
        _patch_config(monkeypatch)
        P.reconcile_remediation_trigger(store)
        return store.get(P.REMEDIATION_TRIGGER_ID).trigger

    def test_a_successful_run_lands_in_the_digest_queue(self, store, monkeypatch, home):
        from personalclaw import notification_rules as nr

        trigger = self._trigger(store, monkeypatch)
        # The rule the digest selects on. Resolved from the registry, never guessed:
        # `build_delivery` picks the notification KIND per outcome, and hardcoding a key here
        # would make this test pass while the real note carried a different one.
        rule = nr.resolve_rule_for_legacy("info")
        _set_rule(home, rule.key, "digest")

        state = _deliver(monkeypatch, trigger, ok=True)

        queued = _queued(home)
        assert len(queued) == 1, queued
        assert queued[0]["mode"] == "digest"
        assert trigger.name.lower() in queued[0]["title"].lower()
        # A digest-mode note is QUEUED, never pushed — so nothing reached the live log.
        assert state._notification_log == []

    def test_the_queued_run_deep_links_to_its_own_run(self, store, monkeypatch, home):
        """ "Like any other run" includes R18's statusUrl: a digest line the user cannot follow back
        to the run is the notification→journal dead end R18 exists to close."""
        from personalclaw import notification_rules as nr

        trigger = self._trigger(store, monkeypatch)
        _set_rule(home, nr.resolve_rule_for_legacy("info").key, "digest")
        _deliver(monkeypatch, trigger, ok=True)
        queued = _queued(home)
        assert queued[0]["statusUrl"], queued[0]
        assert queued[0]["trigger_id"] == P.REMEDIATION_TRIGGER_ID

    def test_the_digest_DRAIN_renders_the_run(self, store, monkeypatch, home):
        """The last hop: `run_digest` is what turns the queue into ONE inbox item. Asserting the
        queue alone would stop one function short of the surface the clause names."""
        from personalclaw import notification_rules as nr

        trigger = self._trigger(store, monkeypatch)
        _set_rule(home, nr.resolve_rule_for_legacy("info").key, "digest")
        _deliver(monkeypatch, trigger, ok=True)

        body = nr.build_digest_body(nr.drain_digest_queue())
        assert trigger.name.lower() in body.lower(), body

    def test_a_never_rule_is_NOT_picked_up(self, store, monkeypatch, home):
        """🔴 VACUITY for every assertion above. If the queue swallowed everything the positive
        tests would pass on a path that ignores the user's settings entirely."""
        from personalclaw import notification_rules as nr

        trigger = self._trigger(store, monkeypatch)
        _set_rule(home, nr.resolve_rule_for_legacy("info").key, "never")
        _deliver(monkeypatch, trigger, ok=True)
        assert _queued(home) == []

    def test_an_immediate_rule_pushes_instead_of_queueing(self, store, monkeypatch, home):
        """The second falsification: a run must be able to MISS the digest by riding the rules
        engine, which a direct queue write could never honour."""
        from personalclaw import notification_rules as nr

        trigger = self._trigger(store, monkeypatch)
        _set_rule(home, nr.resolve_rule_for_legacy("info").key, "immediate")
        state = _deliver(monkeypatch, trigger, ok=True)
        assert _queued(home) == []
        assert len(state._notification_log) == 1

    def test_a_FAILED_run_escalates_past_a_digest_rule(self, store, monkeypatch, home):
        """`build_delivery` picks the kind per OUTCOME so a failure can escalate past a `digest`
        rule while a success cannot. Proven here rather than assumed: a broken maintenance engine
        must not be discoverable only in tomorrow's grouped summary."""
        from personalclaw import notification_rules as nr

        trigger = self._trigger(store, monkeypatch)
        _set_rule(home, nr.resolve_rule_for_legacy("info").key, "digest")
        state = _deliver(monkeypatch, trigger, ok=False, error="sel.prune exploded")
        assert _queued(home) == [], "a failure was grouped into the digest"
        assert len(state._notification_log) == 1

    def test_the_delivery_route_is_not_muted(self, store, monkeypatch):
        """`delivery.deliver` drops a `none` destination BEFORE any rule is consulted, so a
        `delivery: none` engine could never reach the digest whatever the rules said."""
        from personalclaw.triggers.delivery import route_for

        trigger = self._trigger(store, monkeypatch)
        assert route_for(trigger, ok=True) != "none"
        assert route_for(trigger, ok=False) != "none"
