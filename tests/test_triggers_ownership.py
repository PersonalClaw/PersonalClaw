"""The owner filter and the trigger-store provider seam (TEAM-SHARED-ENTITIES §2.2/§3 — TSE-4).

§2.2's requirement is stated structurally: the harness "arms and fires ONLY the owner's triggers …
(a foreign row cannot tick, not 'is skipped')". So the tests below are written to fail if the filter
degrades from *absent from the candidate set* to *declined afterwards*: they assert that a foreign
row does not appear in what the arm path is HANDED, and separately that a full tick over a store
whose only due row is foreign produces zero fires.

Every test drives a REAL `TriggerStore` on `tmp_path`, for the reason `test_triggers_service` gives:
a mocked store hides the entity/serialization seams, and `author` is a persisted field.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import chain, file_poll, idle_poll
from personalclaw.triggers import ownership as OWN
from personalclaw.triggers import provider, pull_on_view
from personalclaw.triggers import registry as TREG
from personalclaw.triggers import service as SVC
from personalclaw.triggers import web_poll
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.store import LoadedTrigger, TriggerStore

NOW = 1_800_000_000.0
OWNER = "keyur"


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    """A configured owner for every test here. Without one the filter is a no-op by design (see
    `ownership`), so a test that forgot this would pass while measuring nothing."""
    monkeypatch.setattr(OWN, "owner_username", lambda: OWNER)


@pytest.fixture(autouse=True)
def _empty_trigger_registry():
    """The provider-store registry is process-global; leaving a store registered would leak rows
    into every later test in the session."""
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    yield
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)


def _trigger(tid="t1", *, next_at=0.0, kind="clock", author="", enabled=True, **over):
    base = dict(
        id=tid,
        name=f"T-{tid}",
        kind=kind,
        enabled=enabled,
        author=author,
        spec={"kind": "interval", "interval_secs": 3600},
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
        capabilities={"providers": ["run-prompt"]},
        next_fire_at=SVC.to_iso(next_at) if next_at else "",
    )
    base.update(over)
    return Trigger(**base)


# ── the predicate ──


@pytest.mark.parametrize(
    "author,expected",
    [
        (OWNER, True),
        ("", True),  # unattributed reads as the owner's — every pre-TSE-4 row
        ("  KEYUR  ", True),  # normalized at the boundary, so casing cannot mint a second author
        ("alice", False),
        ("Alice", False),
    ],
)
def test_only_the_owners_rows_are_armable(author, expected):
    assert OWN.is_owner_authored(_trigger(author=author)) is expected


def test_with_no_configured_username_every_row_is_the_owners(monkeypatch):
    """A single-user install must behave exactly as it did before this field existed. With no
    identity there is also nobody else a row could belong to."""
    monkeypatch.setattr(OWN, "owner_username", lambda: "")
    assert OWN.is_owner_authored(_trigger(author="alice")) is True


def test_a_row_with_no_author_attribute_at_all_reads_as_the_owners():
    """Duck-typed: the service accepts test doubles, and one without `author` must not crash a
    tick."""

    class Bare:
        id = "x"

    assert OWN.is_owner_authored(Bare()) is True


# ── the state shape ──


def test_author_survives_the_store_round_trip(store):
    store.upsert(_trigger("mine", author=OWNER))
    assert store.get("mine").trigger.author == OWNER


def test_an_old_shape_row_with_no_author_key_parses_and_arms(store):
    """The ONE place an old-shape row is handled: `parse_trigger` defaults the absent key to `""`,
    which the filter reads as the owner's. No migration, no dual read — the field is optional."""
    raw = _trigger("legacy", next_at=NOW - 5).to_dict()
    del raw["author"]
    row, issues = parse_trigger(raw)
    assert row.author == ""
    assert not [i for i in issues if i.severity == "error"]
    assert OWN.is_owner_authored(row) is True


def test_an_unknown_field_warning_does_not_fire_for_author(store):
    """`_known_fields` is derived from the dataclass, so `author` must be recognized rather than
    reported as a typo — a warning chip on a field the store itself writes would be absurd."""
    _, issues = parse_trigger(_trigger(author=OWNER).to_dict())
    assert not [i for i in issues if i.path == "author"]


# ── structural: the arm path never HOLDS a foreign row ──


def test_armable_excludes_foreign_rows(store):
    store.save_all([_trigger("mine", author=OWNER), _trigger("theirs", author="alice")])
    assert [t.id for t in provider.armable(store)] == ["mine"]


def test_armable_excludes_broken_rows(store):
    """The `row.ok` check the seven arm sites used to each re-derive."""
    store.save_all([_trigger("mine")])
    broken = _trigger("bad").to_dict()
    broken["kind"] = "not-a-kind"
    store._write([_trigger("mine").to_dict(), broken])
    assert [t.id for t in provider.armable(store)] == ["mine"]


def test_a_foreign_row_is_absent_from_the_tick_candidate_set(store):
    """The structural claim, measured where §2.2 puts it: a due foreign row produces NO fire.

    Not "is refused with a reason" — refused implies it was considered. `due_ids` is fed from
    `armable`, so the id never enters the walk at all.
    """
    store.save_all([_trigger("theirs", next_at=NOW - 60, author="alice")])
    result = asyncio.run(SVC.tick(store, now=NOW, persist=False))
    assert result.fires == []
    assert result.suppressed == 0


def test_the_owners_due_row_still_fires_beside_a_foreign_one(store):
    """The vacuity floor. A filter that dropped EVERYTHING would pass the test above."""
    store.save_all(
        [
            _trigger("mine", next_at=NOW - 60, author=OWNER),
            _trigger("theirs", next_at=NOW - 60, author="alice"),
        ]
    )
    result = asyncio.run(SVC.tick(store, now=NOW, persist=False))
    assert [f.trigger.id for f in result.fires] == ["mine"]


def test_boot_does_not_rearm_a_foreign_row(store):
    """Boot WRITES `next_fire_at`. Arming somebody else's automation on the owner's clock is the
    exact thing the filter exists to prevent, so it has to bite here too and not only in `tick`."""
    store.save_all([_trigger("theirs", next_at=NOW - 60, author="alice")])
    report = SVC.boot(store, now=NOW, persist=True)
    assert report["rearmed"] == []
    assert report["total"] == 0
    assert store.get("theirs").trigger.next_fire_at == SVC.to_iso(NOW - 60)


def test_boot_still_rearms_the_owners_row(store):
    store.save_all([_trigger("mine", next_at=NOW - 60, author=OWNER)])
    report = SVC.boot(store, now=NOW, persist=True)
    assert [r["id"] for r in report["rearmed"]] == ["mine"]


# ── the poll loops and the chain: every other way a row reaches a fire ──


def test_the_poll_loops_and_chain_lookups_all_drop_foreign_rows(store):
    """One test over all six selection sites: each dispatches straight to the gateway's fire path,
    so filtering only in `service.tick` would leave these kinds able to tick for somebody else."""
    store.save_all(
        [
            _trigger("f-mine", kind="file", author=OWNER, spec={"paths": ["/tmp/a"]}),
            _trigger("f-theirs", kind="file", author="alice", spec={"paths": ["/tmp/a"]}),
            _trigger("i-mine", kind="idle", author=OWNER, spec={"idle_secs": 60}),
            _trigger("i-theirs", kind="idle", author="alice", spec={"idle_secs": 60}),
            _trigger("w-mine", kind="web_watch", author=OWNER, spec={"url": "https://x.test"}),
            _trigger("w-theirs", kind="web_watch", author="alice", spec={"url": "https://x.test"}),
            _trigger("v-mine", kind="view", author=OWNER, spec={"surface_binding": "home"}),
            _trigger("v-theirs", kind="view", author="alice", spec={"surface_binding": "home"}),
            _trigger("c-mine", kind="run_completed", author=OWNER, spec={"source_trigger": "up"}),
            _trigger(
                "c-theirs", kind="run_completed", author="alice", spec={"source_trigger": "up"}
            ),
            _trigger("d-mine", kind="run_completed", author=OWNER, spec={"source_def": "wf"}),
            _trigger("d-theirs", kind="run_completed", author="alice", spec={"source_def": "wf"}),
        ]
    )
    assert [t.id for t in file_poll.file_triggers(store)] == ["f-mine"]
    assert [t.id for t in idle_poll.idle_triggers(store)] == ["i-mine"]
    assert [t.id for t in web_poll.web_watch_triggers(store)] == ["w-mine"]
    assert [t.id for t in pull_on_view.bound_triggers(store, surface="home")] == ["v-mine"]
    assert [t.id for t in chain.chain_triggers(store, source_id="up")] == ["c-mine"]
    assert [t.id for t in chain.chain_triggers_for_def(store, source_def="wf")] == ["d-mine"]


# ── the provider seam ──


def test_the_native_store_satisfies_the_seam():
    from personalclaw.triggers.provider import TriggerStoreProvider

    assert issubclass(TriggerStore, TriggerStoreProvider)


def test_the_sdk_reexports_the_contract():
    """A trigger-store app imports from `personalclaw.sdk.triggers`, never from core directly.

    Written in the exact `from personalclaw.sdk.triggers import …` form an app uses rather than
    importing the module and reading attributes off it: the two are equivalent to Python and NOT
    equivalent to the inert-surface census, which counts an `__all__` symbol with no in-repo
    `ImportFrom` consumer as a declared-but-inert export. A module-level import would leave all five
    of these looking dead from inside the repo.
    """
    from personalclaw.sdk import triggers as sdk
    from personalclaw.sdk.triggers import (
        Issue,
        LoadedTrigger,
        Trigger,
        TriggerStoreProvider,
        parse_trigger,
    )

    assert TriggerStoreProvider is provider.TriggerStoreProvider
    assert (LoadedTrigger, Trigger, Issue, parse_trigger) == (
        LoadedTrigger,
        Trigger,
        Issue,
        parse_trigger,
    )
    assert set(sdk.__all__) == {
        "TriggerStoreProvider",
        "LoadedTrigger",
        "Trigger",
        "Issue",
        "parse_trigger",
    }


class _FakeProviderStore:
    """The smallest thing a `trigger` provider must be: something with `load()`."""

    def __init__(self, rows, *, raises=False):
        self._rows = rows
        self._raises = raises

    def load(self):
        if self._raises:
            raise RuntimeError("team backend unreachable")
        return [LoadedTrigger(trigger=t) for t in self._rows]


def test_a_registered_providers_rows_join_the_listing_but_not_the_arm_path(store):
    """`all_rows` is the LISTING read and includes them; `armable` reads only the store it is given.

    A provider's row is rendered before it is armed on purpose — the arm path persists
    `next_fire_at` back with `store.upsert`, and there is no write-back routing yet, so arming one
    would either duplicate it into `triggers.json` or leave it permanently due.
    """
    store.save_all([_trigger("local", author=OWNER)])
    TREG.register_trigger_store("team", _FakeProviderStore([_trigger("remote", author=OWNER)]))
    assert sorted(r.trigger.id for r in provider.all_rows(store)) == ["local", "remote"]
    assert [t.id for t in provider.armable(store)] == ["local"]


def test_a_faulty_provider_store_costs_its_own_rows_and_nothing_else(store):
    """Fail-open: a team backend's outage must not silence the owner's local automations."""
    store.save_all([_trigger("local", author=OWNER)])
    TREG.register_trigger_store("broken", _FakeProviderStore([], raises=True))
    assert [r.trigger.id for r in provider.all_rows(store)] == ["local"]
    assert [t.id for t in provider.armable(store)] == ["local"]


def test_unregistering_removes_the_providers_rows(store):
    store.save_all([])
    TREG.register_trigger_store("team", _FakeProviderStore([_trigger("remote")]))
    assert provider.all_rows(store)
    assert TREG.unregister_trigger_store("team") is True
    assert provider.all_rows(store) == []
    assert TREG.unregister_trigger_store("team") is False


# ── the type handler (the #47 rule) ──


def test_the_trigger_type_has_a_live_handler():
    """The other direction of the #47 rule is guarded suite-wide by
    `test_app_manifest.py::TestProviderTypesMatchHandlers`; this pins the specific pairing so a
    deletion of either half names THIS atom in the failure."""
    from personalclaw.apps.manifest import PROVIDER_TYPES
    from personalclaw.providers.registry import TriggerTypeHandler, get_provider_registry

    assert "trigger" in PROVIDER_TYPES
    assert isinstance(get_provider_registry()._type_handlers.get("trigger"), TriggerTypeHandler)


def test_the_handler_registers_and_deregisters_a_store():
    from personalclaw.providers.registry import TriggerTypeHandler

    handler = TriggerTypeHandler()
    inst = _FakeProviderStore([_trigger("remote")])
    inst.name = "team"  # type: ignore[attr-defined]
    handler.register(None, inst)  # type: ignore[arg-type]
    assert TREG.registered_stores() == {"team": inst}
    handler.deregister(None, inst)  # type: ignore[arg-type]
    assert TREG.registered_stores() == {}


def test_the_handler_refuses_a_store_without_load():
    """Validated at REGISTER time, naming what is missing — the `DutyGateTypeHandler` precedent. A
    store without `load` would sit in the registry looking live and contribute nothing."""
    from personalclaw.providers.registry import TriggerTypeHandler

    class NoLoad:
        name = "team"

    with pytest.raises(ValueError, match="must expose load"):
        TriggerTypeHandler().register(None, NoLoad())  # type: ignore[arg-type]
    assert TREG.registered_stores() == {}
