"""A system-owned singleton trigger converges on the JOB, not on the id (issue 396).

Measured on an upgraded home: TWO identical `system:notification-digest` rows, both enabled, both
`0 8 * * *`, armed for the same instant — one under the deterministic id the reconciler creates, one
under the random id the legacy `crons.json` had given it:

    schedule:system:notification-digest  cron "0 8 * * *"  next 1785830400  enabled
    schedule:2dc39747                    cron "0 8 * * *"  next 1785830400  enabled

Byte-identical apart from the id, so the digest was scheduled twice and the UI showed two
indistinguishable rows with no way to tell which was safe to delete. The reconciler's guard is
id-keyed (deliberately — a generated slug would make every restart add a copy) and the boot import
upserts by id (also deliberately — a hand-authored row must survive a re-import). Neither side is
wrong alone; nothing reconciled across them.

Five providers register a singleton exactly this way, so the convergence is one shared primitive and
these tests cover the primitive plus the wiring at every call site.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from personalclaw.triggers.system_singleton import converge_system_singleton, inline_provider

DIGEST = "system:notification-digest"
PROVIDER = "notification-digest"


def _row(
    trigger_id: str,
    *,
    provider: str = PROVIDER,
    created_by: str = "system",
    enabled: bool = True,
) -> Trigger:
    return Trigger(
        id=trigger_id,
        name=trigger_id,
        kind="clock",
        enabled=enabled,
        created_by=created_by,
        spec={"kind": "cron", "expr": "0 8 * * *"},
        workflow={"inline": {"provider": provider, "config": {}}},
        delivery="none",
    )


@pytest.fixture()
def store(tmp_path) -> TriggerStore:
    return TriggerStore(base_dir=tmp_path)


# ── the primitive ──────────────────────────────────────────────────────────────────────────


def test_the_legacy_copy_is_retired_and_the_canonical_row_kept(store):
    # The measured state, exactly: canonical + a random-id twin.
    store.upsert(_row(DIGEST))
    store.upsert(_row("2dc39747"))
    converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER)
    assert [t.id for t in store.list_triggers()] == [DIGEST]


def test_it_reports_nothing_to_adopt_when_the_canonical_row_already_exists(store):
    # Its state is authoritative; the duplicate has nothing to teach it.
    store.upsert(_row(DIGEST, enabled=False))
    store.upsert(_row("2dc39747", enabled=True))
    assert converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER) is None
    assert store.get(DIGEST).trigger.enabled is False


def test_a_lone_legacy_copy_hands_its_enabled_flag_to_the_caller(store):
    # The migration ran, the reconciler has not. Without this the fresh canonical row would be
    # created at its default and silently switch the job back on.
    store.upsert(_row("2dc39747", enabled=False))
    assert converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER) is False
    assert store.list_triggers() == []


def test_a_lone_enabled_legacy_copy_reports_enabled(store):
    store.upsert(_row("2dc39747", enabled=True))
    assert converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER) is True


def test_any_disabled_duplicate_keeps_the_job_off(store):
    # Conservative on purpose: an off switch the user flipped outweighs a default.
    store.upsert(_row("aaa11111", enabled=True))
    store.upsert(_row("bbb22222", enabled=False))
    assert converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER) is False


def test_a_USER_authored_row_for_the_same_action_is_never_touched(store):
    # The load-bearing discriminator. A user may schedule the same action for themselves; deleting
    # their row would be a far worse bug than the duplicate.
    store.upsert(_row(DIGEST))
    store.upsert(_row("my-own-digest", created_by="user"))
    converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER)
    assert sorted(t.id for t in store.list_triggers()) == ["my-own-digest", DIGEST]


def test_an_agent_authored_row_is_also_left_alone(store):
    store.upsert(_row("agent-made", created_by="agent"))
    assert converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER) is None
    assert [t.id for t in store.list_triggers()] == ["agent-made"]


def test_a_system_row_running_a_DIFFERENT_job_survives(store):
    # Five singletons share this primitive; converging one must not retire another.
    store.upsert(_row(DIGEST))
    store.upsert(_row("system:usage-recap", provider="usage-recap"))
    converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER)
    assert sorted(t.id for t in store.list_triggers()) == [DIGEST, "system:usage-recap"]


def test_a_clean_store_is_untouched(store):
    # Vacuity guard: on a fresh install this must do nothing at all and say so.
    store.upsert(_row(DIGEST))
    assert converge_system_singleton(store, canonical_id=DIGEST, provider=PROVIDER) is None
    assert [t.id for t in store.list_triggers()] == [DIGEST]


def test_an_unreadable_workflow_is_simply_not_this_job():
    # `workflow` is persisted JSON, so a hand-edited or legacy row can hold any shape — and the
    # store refuses to serialize a non-dict, so the accessor is asserted directly rather than
    # through a round trip it could never make.
    weird = _row("legacy-weird")
    weird.workflow = "not-a-dict"  # type: ignore[assignment]
    assert inline_provider(weird) == ""
    empty = _row("legacy-empty")
    empty.workflow = {}
    assert inline_provider(empty) == ""
    unnamed = _row("legacy-unnamed")
    unnamed.workflow = {"inline": {"config": {}}}
    assert inline_provider(unnamed) == ""


def test_an_unreadable_store_does_not_raise(tmp_path):
    # It runs inside a boot reconcile whose contract is never to block the scheduler.
    class Exploding:
        def list_triggers(self, **_kw):
            raise RuntimeError("disk gone")

    verdict = converge_system_singleton(  # type: ignore[arg-type]
        Exploding(), canonical_id=DIGEST, provider=PROVIDER
    )
    assert verdict is None


# ── the wiring, at all five call sites ─────────────────────────────────────────────────────


def test_every_system_singleton_reconciler_converges_on_the_job():
    """A primitive with one caller fixes one bug. All five reconcilers register a singleton with a
    deterministic id and are exposed to the same legacy-copy duplication, so all five call it."""
    import inspect

    from personalclaw.action_providers import (
        digest_provider,
        identity_report_provider,
        remediation_provider,
        source_digest_provider,
        usage_recap_provider,
    )

    RECONCILERS = [
        digest_provider.reconcile_digest_cron,
        source_digest_provider.reconcile_source_digest_cron,
        usage_recap_provider.reconcile_usage_recap_cron,
        remediation_provider.reconcile_remediation_trigger,
        identity_report_provider.reconcile_identity_report_trigger,
    ]
    for fn in RECONCILERS:
        src = inspect.getsource(fn)
        assert (
            "converge_system_singleton(" in src
        ), f"{fn.__module__}.{fn.__name__} does not converge"


def test_the_three_default_enabled_reconcilers_seed_from_the_adopted_flag():
    # The other two derive `enabled` from config, which is authoritative for those rows — so only
    # these three have a default to be corrected.
    import inspect

    from personalclaw.action_providers import (
        digest_provider,
        source_digest_provider,
        usage_recap_provider,
    )

    for fn in (
        digest_provider.reconcile_digest_cron,
        source_digest_provider.reconcile_source_digest_cron,
        usage_recap_provider.reconcile_usage_recap_cron,
    ):
        src = inspect.getsource(fn)
        assert "enabled=True if adopted_enabled is None else adopted_enabled" in src, fn.__name__
        assert "enabled=True," not in src, f"{fn.__name__} still hardcodes enabled=True"
