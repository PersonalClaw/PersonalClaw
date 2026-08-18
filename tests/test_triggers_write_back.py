"""Write-back routing: an app-served trigger row autonomously fires (TEAM-SHARED-ENTITIES — TSE-5).

TSE-4 shipped the read half of the ``trigger`` provider seam and named the gap it stopped at: a
provider's rows rendered but could not ARM, because the arm path PERSISTS and the store it persisted
into was the native one. Arming anyway had two possible outcomes, and this file is written to fail
if either one comes back:

* **duplicate identity** — the reschedule lands in ``triggers.json``, so one id exists in two stores
  and the local copy (which wins every later read) silently forks the shared row; and
* **fire storm** — the reschedule lands nowhere, so the row is due again on the very next tick, and
  every tick after that. "It fired" is not the property; "it fired, its schedule moved, and the next
  tick left it alone" is.

So every firing test below asserts three things, not one: the fire happened, the SERVING store's
``next_fire_at`` advanced, and ``triggers.json`` never gained the id. Plus a second tick at the same
clock that must produce nothing.

The provider store here is a real file-backed one rather than a mock, for the reason
``test_triggers_ownership`` gives: ``author`` and ``next_fire_at`` are persisted fields, and a mock
hides the serialization seam that the whole verification depends on. Its fault switches
(``noop_upsert``, ``raise_on_upsert``, ``noop_delete``, ``blind_get``) exist because a store that
pretends to write is the one shape that costs the owner a fire.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from personalclaw.triggers import chain
from personalclaw.triggers import ownership as OWN
from personalclaw.triggers import provider
from personalclaw.triggers import registry as TREG
from personalclaw.triggers import routing as ROUTE
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.store import LoadedTrigger, TriggerStore

NOW = 1_800_000_000.0
OWNER = "keyur"

#: The four things a shared automation fires, mapped onto the action providers that ship. Kept as
#: data so the parametrized fire test and the "these names are real" test cannot disagree about
#: which four are being claimed.
FOUR_TARGETS = {
    "workflow": ("run-workflow", {"workflow": "morning-brief"}),
    "automation": ("invoke-agent", {"prompt": "triage the shared inbox"}),
    "prompt": ("run-prompt", {"message": "draft my standup"}),
    "action": ("create-task", {"title": "weekly ops sweep"}),
}


class FileProviderStore:
    """A minimal but REAL ``trigger`` provider store: rows in a JSON file, writes that persist.

    Deliberately not a mock. Core's write-back verification re-reads the row it just wrote, so a
    double whose ``get`` returned the in-memory object would pass verification that a real store
    could fail — the test would then prove nothing about the seam it exists to guard.
    """

    def __init__(self, path: Path, *, name: str = "team") -> None:
        self.name = name
        self._path = Path(path)
        # Fault switches, each one a real shape a backend can have.
        self.noop_upsert = False  # accepts the write, persists nothing (the storm shape)
        self.raise_on_upsert = False  # a backend outage mid-write
        self.noop_delete = False  # says it deleted, left the row
        self.blind_get = False  # cannot read a row back (so a write is unverifiable)
        self.upserts: list[str] = []  # ids written here, for "the write went where it should"

    # ── the contract ──

    @property
    def base_dir(self) -> Path:
        return self._path.parent

    def _rows(self) -> list[dict]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return list(data.get("triggers") or [])

    def _write(self, rows: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"triggers": rows}), encoding="utf-8")

    def load(self) -> list[LoadedTrigger]:
        out = []
        for raw in self._rows():
            trigger, issues = parse_trigger(raw)
            out.append(LoadedTrigger(trigger=trigger, issues=list(issues)))
        return out

    def list_triggers(self, *, kind: str = "", include_broken: bool = True) -> list[Trigger]:
        return [
            row.trigger
            for row in self.load()
            if (not kind or row.trigger.kind == kind) and (include_broken or row.ok)
        ]

    def get(self, trigger_id: str):
        if self.blind_get:
            return None
        return next((r for r in self.load() if r.trigger.id == trigger_id), None)

    def upsert(self, trigger: Trigger) -> Trigger:
        self.upserts.append(trigger.id)
        if self.raise_on_upsert:
            raise RuntimeError("team backend unreachable")
        if self.noop_upsert:
            return trigger
        rows = [r for r in self._rows() if str(r.get("id") or "") != trigger.id]
        rows.append(trigger.to_dict())
        self._write(rows)
        return trigger

    def delete(self, trigger_id: str) -> bool:
        if self.noop_delete:
            return True
        rows = self._rows()
        kept = [r for r in rows if str(r.get("id") or "") != trigger_id]
        if len(kept) == len(rows):
            return False
        self._write(kept)
        return True

    def changed_on_disk(self) -> bool:
        return False

    # ── test helpers ──

    def seed(self, *triggers: Trigger) -> None:
        self._write([t.to_dict() for t in triggers])

    def next_fire_of(self, trigger_id: str) -> str:
        row = next((r for r in self.load() if r.trigger.id == trigger_id), None)
        return row.trigger.next_fire_at if row else ""


# ── fixtures ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def native(tmp_path):
    return TriggerStore(base_dir=tmp_path / "home")


@pytest.fixture
def team(tmp_path):
    """A registered provider store. Registration IS the app-install seam being exercised."""
    store = FileProviderStore(tmp_path / "shared" / "automations.json")
    TREG.register_trigger_store(store.name, store)
    return store


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    """A configured owner. Without one the ownership filter is a no-op by design, so a test that
    forgot this would pass while measuring nothing."""
    monkeypatch.setattr(OWN, "owner_username", lambda: OWNER)


@pytest.fixture(autouse=True)
def _clean_process_globals():
    """The provider registry AND the storm quarantine are both process-global. A leaked registration
    would inject rows into every later test in the session; a leaked quarantine would silently make
    a later test's provider serve nothing, which reads as a passing filter."""
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    ROUTE.clear_quarantine()
    yield
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    ROUTE.clear_quarantine()


def _trigger(
    tid="t1", *, next_at=0.0, kind="clock", author="", action="run-prompt", config=None, **over
):
    base = dict(
        id=tid,
        name=f"T-{tid}",
        kind=kind,
        enabled=True,
        author=author,
        spec={"kind": "interval", "interval_secs": 3600},
        workflow={"provider": action, "config": dict(config or {"message": "go"})},
        capabilities={"providers": [action]},
        next_fire_at=SVC.to_iso(next_at) if next_at else "",
    )
    base.update(over)
    return Trigger(**base)


def _local_ids(native: TriggerStore) -> list[str]:
    """The ids actually in `triggers.json` — the duplicate-identity assertion's only honest form."""
    return [row.trigger.id for row in native.load()]


# ── the wrapper ───────────────────────────────────────────────────────────────────────────


def test_with_no_provider_installed_the_store_is_not_wrapped_at_all(native):
    """Every single-user install. The seam must cost nothing when nothing is registered."""
    assert ROUTE.routed(native) is native


def test_a_registered_provider_wraps_the_store_once(native, team):
    wrapped = ROUTE.routed(native)
    assert isinstance(wrapped, ROUTE.RoutingTriggerStore)
    assert ROUTE.routed(wrapped) is wrapped
    assert wrapped.native is native


def test_the_arm_read_sees_both_stores_rows(native, team):
    native.upsert(_trigger("local", author=OWNER))
    team.seed(_trigger("remote", author=OWNER))
    assert sorted(t.id for t in provider.armable(ROUTE.routed(native))) == ["local", "remote"]
    # And the bare native store is untouched by all of this.
    assert [t.id for t in provider.armable(native)] == ["local"]


def test_the_claim_root_stays_local(native, team):
    """A claim describes a LOCAL run. Collecting every machine's claims in a shared folder would
    make `overlap` refuse one member's fire because another member happened to hold the trigger."""
    assert ROUTE.routed(native).base_dir == native.base_dir


def test_a_providers_change_notification_reaches_the_tick(native, team, monkeypatch):
    monkeypatch.setattr(team, "changed_on_disk", lambda: True)
    assert ROUTE.routed(native).changed_on_disk() is True


def test_an_id_in_both_stores_arms_only_once_and_locally(native, team):
    """Two rows under one identity cannot both hold the schedule, so the arm path takes the local
    one — and `all_rows` still shows both, because the page that could report the conflict is the
    last place to hide it."""
    native.upsert(_trigger("clash", author=OWNER, config={"message": "local"}))
    team.seed(_trigger("clash", author=OWNER, config={"message": "remote"}))
    armed = provider.armable(ROUTE.routed(native))
    assert [t.id for t in armed] == ["clash"]
    assert armed[0].workflow["config"]["message"] == "local"
    assert [r.trigger.id for r in provider.all_rows(native)] == ["clash", "clash"]


# ── the write routes to the SERVING store ─────────────────────────────────────────────────


def test_a_providers_row_is_written_back_to_the_provider(native, team):
    """THE atom's precondition. `triggers.json` must never gain the id."""
    team.seed(_trigger("remote", author=OWNER, next_at=NOW))
    row = team.get("remote").trigger
    row.next_fire_at = SVC.to_iso(NOW + 3600)
    native.upsert(row)

    assert team.next_fire_of("remote") == SVC.to_iso(NOW + 3600)
    assert _local_ids(native) == [], "the provider's row must not appear in triggers.json"
    assert team.upserts == ["remote"]


def test_a_local_row_still_writes_locally_with_a_provider_installed(native, team):
    """The vacuity floor for the routing: a rule that sent EVERYTHING to the provider would pass the
    test above."""
    team.seed(_trigger("remote", author=OWNER))
    native.upsert(_trigger("local", author=OWNER, next_at=NOW))
    assert _local_ids(native) == ["local"]
    assert team.upserts == []


def test_a_brand_new_row_is_local(native, team):
    team.seed(_trigger("remote", author=OWNER))
    native.upsert(_trigger("fresh", author=OWNER))
    assert sorted(_local_ids(native)) == ["fresh"]
    assert "fresh" not in team.upserts


def test_a_colliding_id_writes_locally_because_the_local_row_is_the_armed_one(native, team):
    """Read and write must agree about who owns a clashing id, or the arm path reschedules a row
    nobody reads."""
    native.upsert(_trigger("clash", author=OWNER))
    team.seed(_trigger("clash", author=OWNER))
    row = _trigger("clash", author=OWNER, next_at=NOW + 60)
    native.upsert(row)
    assert native.get("clash").trigger.next_fire_at == SVC.to_iso(NOW + 60)
    assert team.next_fire_of("clash") == ""


def test_a_delete_routes_to_the_provider_too(native, team):
    team.seed(_trigger("remote", author=OWNER))
    native.upsert(_trigger("local", author=OWNER))
    assert native.delete("remote") is True
    assert team.load() == []
    assert _local_ids(native) == ["local"]


# ── the four fires ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", sorted(FOUR_TARGETS))
def test_an_app_served_owner_row_autonomously_fires_each_target(native, team, label):
    """Criterion 5, measured per target: a real `tick` over a real gate walk produces a real fire.

    Four assertions, because "it fired" is the weakest of them:
      1. the fire exists and names the right action provider;
      2. the SERVING store's `next_fire_at` advanced (the reschedule went home);
      3. `triggers.json` is still empty (no duplicate identity); and
      4. a SECOND tick at the same clock fires nothing (not a storm).
    """
    action, config = FOUR_TARGETS[label]
    team.seed(_trigger(label, author=OWNER, next_at=NOW - 60, action=action, config=config))
    before = team.next_fire_of(label)

    result = asyncio.run(SVC.tick(native, now=NOW, persist=True))

    assert [f.trigger.id for f in result.fires] == [label]
    assert result.fires[0].trigger.workflow["provider"] == action
    assert result.fires[0].trigger.workflow["config"] == config
    assert result.rescheduled == [label]

    after = team.next_fire_of(label)
    assert after and after != before, "the schedule did not advance — this is the fire storm"
    assert SVC.to_epoch(after) > NOW
    assert _local_ids(native) == [], "the fired row leaked into triggers.json"

    again = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert again.fires == [], "the row was due again on the next tick — fire storm"


def test_the_four_action_providers_the_fires_name_are_real(native):
    """A fire that named a provider nothing implements would satisfy every assertion above and still
    be a dead automation. So the names are checked against the real registry, not a list."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    for label, (action, _config) in sorted(FOUR_TARGETS.items()):
        assert get_action_provider(action) is not None, f"{label} → {action} is not registered"


def test_the_run_count_and_last_fired_meters_land_in_the_serving_store(native, team):
    """The budget, the spacing gate and autopause all read these. Written into the native store they
    would be read from the provider's row forever after — a permanent zero, and a cap needs a meter.
    """
    team.seed(_trigger("metered", author=OWNER, next_at=NOW - 60))
    asyncio.run(SVC.tick(native, now=NOW, persist=True))
    row = team.get("metered").trigger
    assert row.run_count == 1
    assert row.last_fired_at == SVC.to_iso(NOW)
    assert _local_ids(native) == []


def test_the_owners_app_served_row_fires_beside_a_local_one(native, team):
    """The vacuity floor for the whole seam: both stores contribute a fire in the same tick."""
    native.upsert(_trigger("local", author=OWNER, next_at=NOW - 60))
    team.seed(_trigger("remote", author=OWNER, next_at=NOW - 60))
    result = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert sorted(f.trigger.id for f in result.fires) == ["local", "remote"]
    assert _local_ids(native) == ["local"]
    assert SVC.to_epoch(team.next_fire_of("remote")) > NOW


def test_boot_arms_an_app_served_row_into_the_providers_store(native, team):
    """Boot WRITES `next_fire_at`. Without this a shared automation would come up unarmed and only
    start firing after its first clock tick — a first fire that silently depends on uptime."""
    team.seed(_trigger("remote", author=OWNER, next_at=NOW - 3600))
    report = SVC.boot(native, now=NOW, persist=True)
    assert [r["id"] for r in report["rearmed"]] == ["remote"]
    assert SVC.to_epoch(team.next_fire_of("remote")) > NOW
    assert _local_ids(native) == []


def test_a_retiring_one_shot_is_deleted_from_the_providers_store(native, team):
    """`delete_after_run` retirement must remove the row where it lives. Deleting a local row that
    never existed would leave the provider's copy live on an elapsed slot — the storm again."""
    team.seed(
        _trigger(
            "one-shot",
            author=OWNER,
            next_at=NOW - 60,
            spec={"kind": "at", "at": SVC.to_iso(NOW - 60), "delete_after_run": True},
        )
    )
    result = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert result.retired == ["one-shot"]
    assert team.load() == []
    assert _local_ids(native) == []


def test_an_app_served_automation_can_be_chained_off_another(native, team):
    """An automation fired BY an automation — "when the team brief finishes, notify me", served by
    the app. Safe to route because a `run_completed` row holds no schedule to advance."""
    team.seed(
        _trigger("brief", author=OWNER, action="run-workflow", config={"workflow": "brief"}),
        _trigger(
            "followup",
            author=OWNER,
            kind="run_completed",
            action="notify",
            config={"message": "done"},
            spec={"source_trigger": "brief"},
        ),
        _trigger(
            "alice-followup",
            author="alice",
            kind="run_completed",
            action="notify",
            config={"message": "hers"},
            spec={"source_trigger": "brief"},
        ),
    )
    fires, refused = chain.next_fires(native, source_id="brief", source_payload={})
    assert [t.id for t, _p in fires] == ["followup"]
    assert refused == []


# ── the storm guards ─────────────────────────────────────────────────────────────────────


def test_a_provider_that_silently_does_not_persist_fires_once_and_is_quarantined(native, team):
    """The named failure mode, as a runtime invariant rather than only a test assertion.

    A store that accepts the write and keeps the old `next_fire_at` is due again every tick forever.
    Core re-reads what it wrote; a mismatch withholds that provider's rows from the arm path. One
    extra fire, then silence with a reason — not one fire per tick.
    """
    team.seed(_trigger("frozen", author=OWNER, next_at=NOW - 60))
    team.noop_upsert = True

    first = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert [f.trigger.id for f in first.fires] == ["frozen"]
    assert "team" in ROUTE.quarantine_report()
    assert "next_fire_at" in ROUTE.quarantine_report()["team"]

    second = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert second.fires == [], "a frozen schedule fired twice — the storm is open"
    third = asyncio.run(SVC.tick(native, now=NOW + 7200, persist=True))
    assert third.fires == []
    assert _local_ids(native) == [], "the frozen row must not be rescued into triggers.json"


def test_a_provider_whose_write_raises_is_quarantined_and_does_not_break_the_tick(native, team):
    """A team backend's outage must not abort a tick that is also rescheduling local automations."""
    native.upsert(_trigger("local", author=OWNER, next_at=NOW - 60))
    team.seed(_trigger("remote", author=OWNER, next_at=NOW - 60))
    team.raise_on_upsert = True

    first = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert sorted(f.trigger.id for f in first.fires) == ["local", "remote"]
    assert ROUTE.quarantine_report()["team"] == "upsert() raised"

    second = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert [f.trigger.id for f in second.fires] == [], "local was already rescheduled"
    assert SVC.to_epoch(native.get("local").trigger.next_fire_at) > NOW


def test_a_provider_whose_write_cannot_be_read_back_is_quarantined(native, team):
    """Unverifiable is treated as unpersisted. A store core cannot re-read is a store core cannot
    promise anything about, and the safe reading of "I don't know" is "do not arm it again"."""
    team.seed(_trigger("blind", author=OWNER, next_at=NOW - 60))
    team.blind_get = True
    asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert "read back" in ROUTE.quarantine_report()["team"]
    assert asyncio.run(SVC.tick(native, now=NOW, persist=True)).fires == []


def test_a_provider_whose_delete_leaves_the_row_is_quarantined(native, team):
    """A retirement that did not take leaves a live row on an elapsed slot — the same storm."""
    team.seed(
        _trigger(
            "sticky",
            author=OWNER,
            next_at=NOW - 60,
            spec={"kind": "at", "at": SVC.to_iso(NOW - 60), "delete_after_run": True},
        )
    )
    team.noop_delete = True
    asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert "delete()" in ROUTE.quarantine_report()["team"]
    assert asyncio.run(SVC.tick(native, now=NOW, persist=True)).fires == []


def test_a_quarantined_providers_rows_still_render(native, team):
    """Quarantine withholds rows from the ARM path only. A row that vanished from the page would
    make a store that cannot persist indistinguishable from one that was uninstalled."""
    team.seed(_trigger("frozen", author=OWNER, next_at=NOW - 60))
    team.noop_upsert = True
    asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert ROUTE.quarantine_report()
    assert [r.trigger.id for r in provider.all_rows(native)] == ["frozen"]


# ── "alice": the two separate properties ─────────────────────────────────────────────────


def test_an_app_served_alice_row_renders_visible_and_read_only(native, team):
    """PROPERTY ONE — visible-but-inert RENDERING. The row is listed, and the read-only fact the UI
    renders is computed from the same predicate the arm path uses, never re-derived from `author`.
    """
    team.seed(_trigger("alice-nightly", author="alice"), _trigger("mine", author=OWNER))
    listed = {r.trigger.id: r.trigger for r in provider.all_rows(native)}
    assert sorted(listed) == ["alice-nightly", "mine"]
    assert OWN.is_owner_authored(listed["alice-nightly"]) is False
    assert OWN.is_owner_authored(listed["mine"]) is True
    # Enabled, so it is inert by OWNERSHIP and not by a toggle somebody could flip back on.
    assert listed["alice-nightly"].enabled is True
    assert [t.id for t in ROUTE.routed(native).list_triggers()] == ["alice-nightly", "mine"]


def test_an_app_served_alice_row_is_never_handed_to_the_arm_path(native, team):
    """PROPERTY TWO — the STRUCTURAL cannot-arm filter, through a PROVIDER-served row.

    A different claim from the one above, and the reason they are two tests: rendering is about what
    the user sees, this is about what the code can reach. The row is absent from the candidate set,
    so there is no downstream gate that could be asked to decline it.
    """
    team.seed(_trigger("alice-nightly", author="alice", next_at=NOW - 60))
    wrapped = ROUTE.routed(native)
    assert [r.trigger.id for r in wrapped.load()] == ["alice-nightly"]  # the read sees it
    assert provider.armable(wrapped) == []  # the arm path never does
    assert SVC.due_ids(provider.armable(wrapped), now=NOW) == []


def test_a_tick_over_an_app_served_alice_row_fires_nothing_and_writes_nothing(native, team):
    """The end-to-end form. The last assertion is the sharp one: her `next_fire_at` is UNCHANGED, so
    the arm path did not merely decline to fire it — it never touched it."""
    team.seed(_trigger("alice-nightly", author="alice", next_at=NOW - 60))
    before = team.next_fire_of("alice-nightly")
    result = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert result.fires == []
    assert result.rescheduled == []
    assert team.next_fire_of("alice-nightly") == before
    assert team.upserts == []
    assert _local_ids(native) == []


def test_the_owners_app_served_row_still_fires_beside_alices(native, team):
    """The vacuity floor. A filter dropping every provider row would pass all three tests above."""
    team.seed(
        _trigger("mine", author=OWNER, next_at=NOW - 60),
        _trigger("alice-nightly", author="alice", next_at=NOW - 60),
    )
    result = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert [f.trigger.id for f in result.fires] == ["mine"]
    assert SVC.to_epoch(team.next_fire_of("mine")) > NOW
    assert team.next_fire_of("alice-nightly") == SVC.to_iso(NOW - 60)


def test_boot_does_not_arm_an_app_served_alice_row(native, team):
    """Boot writes `next_fire_at`, so the filter has to bite here too and not only in `tick`."""
    team.seed(_trigger("alice-nightly", author="alice", next_at=NOW - 3600))
    report = SVC.boot(native, now=NOW, persist=True)
    assert report["rearmed"] == []
    assert report["total"] == 0
    assert team.next_fire_of("alice-nightly") == SVC.to_iso(NOW - 3600)


def test_an_unattributed_app_served_row_reads_as_the_owners(native, team):
    """The bargain the SDK states: a store that wants its rows treated as somebody else's must SAY
    whose. Every pre-attribution row is unattributed, and treating those as foreign would stop every
    existing automation the moment a provider is installed."""
    team.seed(_trigger("unattributed", author="", next_at=NOW - 60))
    result = asyncio.run(SVC.tick(native, now=NOW, persist=True))
    assert [f.trigger.id for f in result.fires] == ["unattributed"]
