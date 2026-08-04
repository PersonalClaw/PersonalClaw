"""Decision 7's frozen-capability fence, actually enforced (§1.4 / R3 — S116).

🔴 THE DEFECT. `FireContext.requested` defaulted to `{}` and **nothing in production
ever populated it**. The only real construction (`service.tick`) omitted the field, so
`evaluate`'s `if ctx.requested:` was always false and the frozen-capability fence —
decision 7's whole enforcement point — had never run on a single real fire. It passed
its own unit tests the whole time, because those supply `requested` by hand.

Exactly the shape of S97's `existing_claim` finding, in the gate directly below it: a
control that is present, reviewed, and enforcing nothing because its input has no writer.

🔴 AND WIRING IT ALONE WOULD HAVE REFUSED EVERY AUTOMATION IN EXISTENCE. Measured before
choosing: no writer sets `capabilities` — not `tools.create`, not the app-cron
reconciler, not the digest reconciler, not the CLI, not the API — and every one of them
creates a WRITE-CAPABLE action (`invoke-agent`, `run-prompt`, `notification-digest`). The
fence denies on an empty block, so enforcement without decision 7's read-only default
plus a save-time freeze would have been a 100% outage of user automations dressed as a
security fix.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import service as svc
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.screen import (
    READ_ONLY_PROVIDERS,
    WRITE_CAPABLE_PROVIDERS,
    capabilities_for_action,
    provider_is_read_only,
    requested_capabilities,
)
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _due(store, tid, provider, *, caps=None):
    """A clock trigger already due at NOW, so one `tick` decides it."""
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            next_fire_at="2027-01-15T07:00:00+00:00",
            capabilities=caps or {},
            workflow={"inline": {"provider": provider, "config": {}}},
        )
    )


def _tick(store, tmp_path):
    return asyncio.run(svc.tick(store, now=NOW, base_dir=tmp_path, persist=False))


# ── the classification ──


def test_the_two_provider_sets_do_not_overlap():
    """A provider in both sets would resolve by dict order — the defect S71 found in `fuse`."""
    assert not (READ_ONLY_PROVIDERS & WRITE_CAPABLE_PROVIDERS)


def test_every_shipped_provider_is_classified():
    """🔴 An unclassified provider is treated as write-capable, which is the safe direction but a
    silent one: the author of a new read-only action would see it refuse and not know why. So every
    provider the registry actually ships must appear in one of the two sets."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        list_action_providers,
    )

    _ensure_default_providers_registered()
    shipped = set(list_action_providers())
    classified = READ_ONLY_PROVIDERS | WRITE_CAPABLE_PROVIDERS
    assert not (
        shipped - classified
    ), f"unclassified action providers: {sorted(shipped - classified)}"


def test_an_unknown_provider_fails_CLOSED():
    """The same direction `EMPTY_MEANS` takes: an action nobody classified must not be a hole."""
    assert provider_is_read_only("brand-new-provider") is False
    assert provider_is_read_only("") is False


def test_a_read_only_provider_is_recognized():
    assert provider_is_read_only("notify") is True
    assert provider_is_read_only("knowledge-retrieve") is True


def test_write_capable_providers_are_recognized():
    for name in ("bash", "run-script", "run-prompt", "invoke-agent"):
        assert provider_is_read_only(name) is False, name


# ── what a trigger requests ──


def test_requested_reads_the_inline_action_shape():
    """A migrated cron nests its action under `workflow.inline`."""
    trigger = Trigger(
        id="t", name="t", kind="clock", workflow={"inline": {"provider": "bash", "config": {}}}
    )
    assert requested_capabilities(trigger) == {"providers": ["bash"]}


def test_requested_reads_the_flat_action_shape():
    """S92's chat tools write a flat `{provider, config}`. Reading only one shape would leave half a
    real store unfenced — the same both-shapes lesson S103 recorded for the week grid."""
    trigger = Trigger(id="t", name="t", kind="clock", workflow={"provider": "notify", "config": {}})
    assert requested_capabilities(trigger) == {"providers": ["notify"]}


def test_a_workflow_REF_requests_nothing():
    """The def's own nodes are fenced by the workflow engine's capability layer. Naming the ref as a
    provider would refuse every workflow-backed trigger against a set that never lists def names."""
    assert (
        requested_capabilities(Trigger(id="t", name="t", kind="clock", workflow={"ref": "d"})) == {}
    )


def test_no_action_requests_nothing():
    assert requested_capabilities(Trigger(id="t", name="t", kind="clock")) == {}


# ── the fence, driven through a real tick ──


def test_a_read_only_action_fires_with_NO_capability_block(store, tmp_path):
    """🔴 Decision 7's default, and what makes the fence landable: "auto-fired triggers default to
    read-only action providers". A read-only action needs no opt-in."""
    _due(store, "clock:ro", "notify")
    result = _tick(store, tmp_path)
    assert [f.trigger.id for f in result.fires] == ["clock:ro"]


def test_a_WRITE_action_with_no_grant_is_REFUSED(store, tmp_path):
    """The other half: "write-capable actions require explicit opt-in"."""
    _due(store, "clock:w", "bash")
    result = _tick(store, tmp_path)
    assert result.fires == []
    row = next(r for r in result.ledger_rows if r["trigger_id"] == "clock:w")
    assert row["outcome"] == "refused"
    assert "capability" in row["reason"]
    assert "bash" in row["reason"], "the refusal must name the action it refused"


def test_a_WRITE_action_WITH_its_grant_fires(store, tmp_path):
    _due(store, "clock:w", "bash", caps={"providers": ["bash"]})
    result = _tick(store, tmp_path)
    assert [f.trigger.id for f in result.fires] == ["clock:w"]


def test_a_grant_for_a_DIFFERENT_provider_does_not_authorize_this_one(store, tmp_path):
    """The grant is per-action, not a blanket "this trigger may write"."""
    _due(store, "clock:w", "bash", caps={"providers": ["run-prompt"]})
    result = _tick(store, tmp_path)
    assert result.fires == []


def test_an_UNCLASSIFIED_provider_is_refused_without_a_grant(store, tmp_path):
    """Fails closed end-to-end, not just in the classifier."""
    _due(store, "clock:new", "brand-new-provider")
    result = _tick(store, tmp_path)
    assert result.fires == []
    row = next(r for r in result.ledger_rows if r["trigger_id"] == "clock:new")
    assert row["outcome"] == "refused"


def test_the_refusal_is_a_LEDGER_ROW_not_a_silent_drop(store, tmp_path):
    """§7's zero-silent-drops rule. A refused fire the user cannot see is indistinguishable from a
    scheduler that stopped working."""
    _due(store, "clock:w", "bash")
    result = _tick(store, tmp_path)
    assert len(result.ledger_rows) == 1
    assert result.ledger_rows[0]["trigger_id"] == "clock:w"


def test_mixed_triggers_are_decided_INDEPENDENTLY(store, tmp_path):
    """One refused trigger must not suppress another's fire."""
    _due(store, "clock:ro", "notify")
    _due(store, "clock:w", "bash")
    _due(store, "clock:ok", "bash", caps={"providers": ["bash"]})
    result = _tick(store, tmp_path)
    assert sorted(f.trigger.id for f in result.fires) == ["clock:ok", "clock:ro"]


def test_the_production_tick_POPULATES_requested(store, tmp_path):
    """🔴 The defect itself, pinned. `service.tick` omitted `requested`, so the fence never ran. A
    source check, because the property is that the field is SUPPLIED — a behavioural test would pass
    against a fence that happened to allow everything."""
    import inspect

    src = inspect.getsource(svc.tick)
    assert "requested=" in src, "tick must tell the fence what the trigger asks for"


# ── the save-time freeze ──


def test_capabilities_for_action_grants_a_write_provider():
    trigger = Trigger(
        id="t", name="t", kind="clock", workflow={"inline": {"provider": "bash", "config": {}}}
    )
    assert capabilities_for_action(trigger) == {"providers": ["bash"]}


def test_capabilities_for_action_leaves_a_read_only_action_EMPTY():
    """🔴 Deliberate. The fence permits read-only actions without a block, and writing
    `{"providers": ["notify"]}` would imply an opt-in the user never had to make — which matters the
    day someone edits that trigger's action to something write-capable and the stale block grants
    it."""
    trigger = Trigger(
        id="t", name="t", kind="clock", workflow={"inline": {"provider": "notify", "config": {}}}
    )
    assert capabilities_for_action(trigger) == {}


def test_tools_create_FREEZES_the_capability_set(tmp_path):
    """The chat tools and the API both create through here. Without the freeze, every trigger they
    make would refuse on its next fire."""
    from personalclaw.triggers import tools as T

    store = TriggerStore(base_dir=tmp_path)
    T.create(
        store,
        name="writer",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 3600},
        workflow={"provider": "bash", "config": {"command": "x"}},
        created_by="user",
    )
    assert store.get("clock:writer").trigger.capabilities == {"providers": ["bash"]}


def test_a_created_write_trigger_actually_FIRES(tmp_path):
    """The end-to-end proof that the freeze and the fence agree: create through the real tool, then
    drive a real tick. If these two disagreed, every new automation would be born refusing."""
    from personalclaw.triggers import tools as T

    store = TriggerStore(base_dir=tmp_path)
    T.create(
        store,
        name="writer",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 60},
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
        created_by="user",
    )
    row = store.get("clock:writer").trigger
    row.next_fire_at = "2027-01-15T07:00:00+00:00"
    store.upsert(row)

    result = _tick(store, tmp_path)
    assert [f.trigger.id for f in result.fires] == ["clock:writer"]


def test_the_app_cron_reconciler_freezes_too():
    """An app cron runs `invoke-agent` — write-capable — so without the freeze every app-declared
    cron would refuse."""
    import inspect

    from personalclaw.apps import app_crons

    src = inspect.getsource(app_crons.reconcile_app_crons)
    assert "capabilities_for_action" in src


def test_the_digest_reconciler_freezes_too():
    import inspect

    from personalclaw.action_providers import digest_provider

    src = inspect.getsource(digest_provider.reconcile_digest_cron)
    assert "capabilities_for_action" in src


# ── the boot backfill for pre-S116 rows ──


def test_the_backfill_freezes_a_pre_S116_write_row(store):
    """🔴 THE POPULATION THAT WOULD HAVE BROKEN. No writer set `capabilities` before this session,
    so every automation already on a user's disk carries an empty block — and the fence denies on
    one. Wiring enforcement without this backfill is a 100% outage of existing automations."""
    from personalclaw.triggers.boot_migrate import backfill_capabilities

    _due(store, "clock:old", "run-prompt")
    assert backfill_capabilities(store) == ["clock:old"]
    assert store.get("clock:old").trigger.capabilities == {"providers": ["run-prompt"]}


def test_the_backfill_grants_only_what_the_CURRENT_action_does(store):
    """A faithful grandfather, not a widening. The row is granted the provider it is already
    configured to run — so re-pointing that action at something else still needs a fresh opt-in."""
    from personalclaw.triggers.boot_migrate import backfill_capabilities

    _due(store, "clock:old", "run-prompt")
    backfill_capabilities(store)
    granted = store.get("clock:old").trigger.capabilities["providers"]
    assert granted == ["run-prompt"], "not a blanket write grant"
    assert "bash" not in granted


def test_the_backfill_leaves_a_read_only_row_EMPTY(store):
    """Decision 7's default already permits it, and writing a block would imply an opt-in the user
    never made — which matters the day that action is edited to something write-capable."""
    from personalclaw.triggers.boot_migrate import backfill_capabilities

    _due(store, "clock:ro", "notify")
    assert backfill_capabilities(store) == []
    assert store.get("clock:ro").trigger.capabilities == {}


def test_the_backfill_never_WIDENS_an_existing_grant(store):
    """An author who deliberately fenced a row tighter than its action must keep that decision."""
    from personalclaw.triggers.boot_migrate import backfill_capabilities

    _due(store, "clock:tight", "bash", caps={"providers": ["notify"]})
    assert backfill_capabilities(store) == []
    assert store.get("clock:tight").trigger.capabilities == {"providers": ["notify"]}


def test_the_backfill_is_IDEMPOTENT(store):
    """It runs on every boot, so a second pass must be a no-op rather than a re-grant."""
    from personalclaw.triggers.boot_migrate import backfill_capabilities

    _due(store, "clock:old", "run-prompt")
    assert backfill_capabilities(store) == ["clock:old"]
    assert backfill_capabilities(store) == []


def test_a_backfilled_row_actually_FIRES(store, tmp_path):
    """The end-to-end proof, driven through a real tick: the grandfathered row passes the fence."""
    from personalclaw.triggers.boot_migrate import backfill_capabilities

    _due(store, "clock:old", "run-prompt")
    backfill_capabilities(store)
    assert [f.trigger.id for f in _tick(store, tmp_path).fires] == ["clock:old"]


def test_boot_RUNS_the_backfill(tmp_path):
    """🔴 The wiring, not the helper. A backfill nothing calls is the inert-control defect this
    whole session exists to close — so assert `migrate_and_arm` reports it."""
    from personalclaw.triggers import boot_migrate

    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="clock:old",
            name="old",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"inline": {"provider": "run-prompt", "config": {}}},
        )
    )
    report = boot_migrate.migrate_and_arm(base_dir=tmp_path, now=NOW)
    assert report["frozen"] == ["clock:old"]
    assert store.get("clock:old").trigger.capabilities == {"providers": ["run-prompt"]}


def test_the_backfill_SKIPS_a_broken_row(tmp_path):
    """Granting capabilities to a row that does not parse is how a fence becomes decorative.

    The `ok is False` assertion is load-bearing: without it an `if not row.ok` guard in the test
    would make this pass vacuously against a store that parsed the row just fine.
    """
    from personalclaw.triggers.boot_migrate import backfill_capabilities

    (tmp_path / "triggers.json").write_text(
        '[{"id": "clock:broken", "name": "b", "kind": "clock", "spec": {"kind": "??"},'
        ' "workflow": {"inline": {"provider": "bash", "config": {}}}}]'
    )
    store = TriggerStore(base_dir=tmp_path)
    rows = store.load()
    assert [r.ok for r in rows] == [False], "the fixture must actually be a broken row"
    assert backfill_capabilities(store) == []
    assert store.load()[0].trigger.capabilities == {}


# ── the doctor finding for pre-S116 rows ──


def _diagnose(store):
    from personalclaw.triggers.calendar import diagnose

    rows = [
        {
            "id": f"schedule:{r.trigger.id}",
            "gates": r.trigger.gates or {},
            "workflow": r.trigger.workflow or {},
            "spec": dict(r.trigger.spec or {}),
            "capabilities": dict(r.trigger.capabilities or {}),
        }
        for r in store.load()
    ]
    return diagnose(rows, known_workflows=None)


def test_the_doctor_reports_an_unfenced_write_action(store):
    """🔴 The population this session cannot fix automatically: a trigger authored BEFORE the fence
    was wired carries an empty block and refuses on its next fire. The refusal is in the ledger, but
    the user's question is "why did my automation stop" — and the doctor is where that is answered.
    """
    _due(store, "clock:old", "bash")
    finding = next(f for f in _diagnose(store).findings if f.code == "unfenced_write_action")
    assert "bash" in finding.detail
    assert "re-save" in finding.fix, "and it must say how to fix it"


def test_the_doctor_is_SILENT_for_a_granted_trigger(store):
    _due(store, "clock:ok", "bash", caps={"providers": ["bash"]})
    assert not [f for f in _diagnose(store).findings if f.code == "unfenced_write_action"]


def test_the_doctor_is_SILENT_for_a_read_only_trigger(store):
    _due(store, "clock:ro", "notify")
    assert not [f for f in _diagnose(store).findings if f.code == "unfenced_write_action"]


def test_the_facade_passes_capabilities_to_the_doctor():
    """🔴 Found by driving the endpoint: the facade's doctor payload omitted `capabilities`, so the
    check read every trigger as ungranted. The payload has to carry what the check reads."""
    import inspect

    from personalclaw.dashboard.handlers import triggers as T

    src = inspect.getsource(T.api_triggers_doctor)
    assert '"capabilities"' in src
