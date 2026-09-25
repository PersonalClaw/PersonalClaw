"""A notification addressed to somebody else is VISIBLE here and FIRED nowhere here.

MULTI-TENANCY-ENTITY `TSE2-5`: *"Notification delivery is pluggable and addressable so a
shared-store notification can route to the intended owner rather than 'the' dashboard;
foreign-addressed notifications are visible-but-not-fired locally (same visible-but-inert
posture as foreign triggers)."*

**What this suite takes the acceptance test to be**, because the clause is short and abstract:

1. A foreign-addressed note is **listed** — it is in ``state._notification_log``, which is
   verbatim what ``GET /api/notifications`` returns (``handlers/messaging.py:271``), and it is
   **persisted**, so it survives a restart like every other row.
2. It is **provably not fired locally**, on all four local deliveries independently: no WS
   broadcast, no ``push``, no ``native`` key on the note, and no digest queue entry. Each is a
   separate assertion because each is a separate code path in ``notify`` and a fix that closed
   three of four would look green under one combined assertion.
3. A **locally addressed** note still fires, and so does an **unaddressed** one — that second
   half is the back-compat bargain (`identity`: "empty degrades to today's behavior") and
   without it this atom is a regression on every existing install.
4. The foreign note **routes to the intended owner** when a ``type=notification`` delivery
   provider says it can reach them, and records WHO took it; with no provider installed it
   records ``""`` and stays inert — "nobody could reach them" must never read as "delivered".

**The mirrored mechanism.** This is not a second inert-ness mechanism. It is
:mod:`personalclaw.triggers.ownership` — TEAM-SHARED-ENTITIES §2.2's shipped foreign-trigger
posture — applied to the attention path:

  * ``triggers/ownership.py:62`` ``is_owner_authored``  → ``notification_addressing.py``
    ``is_locally_addressed`` (same clauses: empty author/owner answers True; only two
    non-empty disagreeing strings answer False).
  * ``triggers/ownership.py:76`` ``owner_authored``     → ``locally_addressed``.
  * ``triggers/ownership.py:42`` ``FOREIGN_AUTHOR``     → ``FOREIGN_ADDRESSEE``.
  * ``triggers/provider.py:113`` ``armable`` (the FIRE read, foreign dropped) vs
    ``provider.py:150`` ``all_rows`` (the LISTING read, foreign kept) → ``notify``'s fire half
    vs ``_append_notification``.

The tests below assert that correspondence directly rather than trusting the docstrings.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw import notification_addressing as addressing
from personalclaw.dashboard.state import DashboardState
from personalclaw.notification_providers import registry as delivery_registry
from personalclaw.sdk.notification import NotificationDeliveryProvider

OWNER = "keyur"
TEAMMATE = "dana"


class _FixtureBackend(NotificationDeliveryProvider):
    """A delivery backend that can reach exactly one named teammate."""

    def __init__(self, name: str, reaches: str, *, accept: bool = True) -> None:
        self._name = name
        self._reaches = reaches
        self._accept = accept
        self.delivered: list[dict[str, Any]] = []

    @property
    def delivery_name(self) -> str:
        return self._name

    def addresses(self, username: str) -> bool:
        return username == self._reaches

    def deliver(self, note: Any) -> bool:
        self.delivered.append(dict(note))
        return self._accept


@pytest.fixture
def state(monkeypatch, tmp_path):
    """A state whose four local deliveries are each captured separately.

    Shaped after ``test_note_owns_its_own_fields.py``'s fixture: no real home is touched, and
    ``_broadcast`` / ``_push_target`` / ``_persist_notification`` are recorded rather than
    performed so "was it fired" is an observation, not an inference.
    """
    st = DashboardState.__new__(DashboardState)
    st._notification_log = []
    st._sessions = {}
    broadcast: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []
    pushed: list[tuple[str, dict[str, Any]]] = []
    digested: list[dict[str, Any]] = []
    monkeypatch.setattr(st, "_broadcast", lambda note: broadcast.append(note), raising=False)
    monkeypatch.setattr(
        st, "_push_target", lambda kind, note: pushed.append((kind, note)), raising=False
    )
    monkeypatch.setattr(
        "personalclaw.dashboard.state._persist_notification", lambda note: persisted.append(note)
    )
    monkeypatch.setattr(
        "personalclaw.notification_rules.queue_for_digest", lambda note: digested.append(note)
    )
    monkeypatch.setattr("personalclaw.identity.operator_name", lambda: "")

    class _Desktop:
        """A connected shell that reports the native-notification capability.

        Present so the `native` branch is REACHABLE: on a bare state
        `self.desktop.capability(...)` raises, `notify` catches it and `native` is never set —
        which would make the "no native banner" assertion vacuous.
        """

        def capability(self, name: str) -> dict[str, Any]:
            return {"granted": True}

    monkeypatch.setattr(st, "desktop", _Desktop(), raising=False)
    # The owner identity every addressing decision is made against. Patched at the identity
    # seam rather than at `notification_addressing.owner_username`, so the module's own read
    # path is exercised.
    monkeypatch.setattr("personalclaw.identity.current_username", lambda: OWNER)
    st.captured = {
        "broadcast": broadcast,
        "persisted": persisted,
        "pushed": pushed,
        "digested": digested,
    }
    return st


@pytest.fixture(autouse=True)
def _clean_delivery_registry():
    """No test may leak a backend into the module-global registry."""
    for name in delivery_registry.list_provider_names():
        delivery_registry.unregister_provider(name)
    yield
    for name in delivery_registry.list_provider_names():
        delivery_registry.unregister_provider(name)


def _rule_with(monkeypatch, *, mode: str = "immediate", targets: tuple[str, ...]) -> None:
    """Pin the resolved rule so a delivery branch under test is actually REACHABLE.

    Every branch below the addressee is conditional on a rule field, and the shipped default
    for this kind is ``mode=immediate, targets=("dashboard",)`` — so a test that does not pin
    the rule asserts about a path the code never took.
    """
    from personalclaw import notification_rules

    monkeypatch.setattr(
        notification_rules,
        "resolve_rule_for_legacy",
        lambda kind: notification_rules.Rule(
            source="system", kind="info", mode=mode, targets=targets
        ),
    )


def _notify(state: DashboardState, addressee: str | None) -> dict[str, Any]:
    meta: dict[str, Any] = {"inbox_item": "i-1"}
    if addressee is not None:
        meta[addressing.ADDRESSEE_KEY] = addressee
    state.notify("info", "Ready for review", "PR #42 needs a look", meta=meta)
    assert state._notification_log, "the note reached neither the log nor a delivery"
    return state._notification_log[-1]


# ── (a) VISIBLE ───────────────────────────────────────────────────────────────


def test_a_foreign_addressed_note_is_listed_and_persisted(state):
    """Clause half 1: visible. The row is in the log the bell and `GET /api/notifications`
    read, and it is written to disk — a note withheld from delivery is not a note deleted."""
    note = _notify(state, TEAMMATE)
    assert note in state._notification_log
    assert state.captured["persisted"] == [note]
    # Legible about WHY it is here but quiet, and about who it was for — the frontend renders
    # exactly these two fields (mirroring the trigger row's server-computed `read_only`).
    assert note[addressing.WITHHELD_REASON_KEY] == addressing.FOREIGN_ADDRESSEE
    assert note[addressing.ADDRESSEE_KEY] == TEAMMATE


# ── (b) NOT FIRED — four independent local deliveries ─────────────────────────


def test_b_foreign_addressed_note_is_not_broadcast(state):
    _notify(state, TEAMMATE)
    assert state.captured["broadcast"] == []


def test_b_foreign_addressed_note_is_not_pushed(state, monkeypatch):
    """Driven with `push` among the rule's TARGETS. Without that the branch is unreachable and
    the assertion is vacuous — measured: this test passed under a mutation that withheld
    nothing, because the default targets for this kind are `("dashboard",)` alone."""
    _rule_with(monkeypatch, targets=("dashboard", "push"))
    _notify(state, TEAMMATE)
    assert state.captured["pushed"] == []
    # The control: the SAME rule fires a push for a locally-addressed note, so the assertion
    # above is the addressee's doing and not a target that was never selected.
    state.captured["pushed"].clear()
    _notify(state, OWNER)
    assert len(state.captured["pushed"]) == 1


def test_b_foreign_addressed_note_carries_no_native_banner(state, monkeypatch):
    """`native` is what tells the Electron shell to raise an OS notification. Absent, the
    shell raises nothing — so the assertion is on the key, not on a shell call.

    `native_delivery` is forced to a decision here for the same vacuity reason as the push
    test: on a bare state the desktop-capability read raises and `native` is never set at all,
    so an un-gated build would have passed this too."""
    _rule_with(monkeypatch, targets=("dashboard", "native"))
    monkeypatch.setattr(
        "personalclaw.notification_rules.native_delivery",
        lambda rule, capability: {"title": "t", "body": "b"},
    )
    note = _notify(state, TEAMMATE)
    assert "native" not in note
    assert "native" in _notify(state, OWNER)  # the control


def test_b_foreign_addressed_note_never_enters_the_digest(state, monkeypatch):
    """A foreign note batched into the morning digest is a foreign note fired, one day late.
    Driven with the rule in `digest` mode so the branch that would queue it is live."""
    _rule_with(monkeypatch, mode="digest", targets=("dashboard",))
    _notify(state, TEAMMATE)
    assert state.captured["digested"] == []
    # The control: the same rule DOES queue a locally-addressed note, so the assertion above
    # is the addressee's doing and not a `digest` branch that was never reached.
    state.notify("info", "mine", "b", meta={addressing.ADDRESSEE_KEY: OWNER})
    assert len(state.captured["digested"]) == 1


# ── (c) LOCAL AND UNADDRESSED NOTES STILL FIRE ────────────────────────────────


def test_c_a_locally_addressed_note_fires(state):
    """The other side of the rail: addressing must gate the foreign note and nothing else."""
    note = _notify(state, OWNER)
    assert state.captured["broadcast"] == [note]
    assert addressing.WITHHELD_REASON_KEY not in note


def test_c_an_unaddressed_note_fires(state):
    """The back-compat bargain: every note written before this field existed carries none, and
    treating those as foreign would silence every notification on every existing install."""
    note = _notify(state, None)
    assert state.captured["broadcast"] == [note]
    assert addressing.WITHHELD_REASON_KEY not in note


def test_c_an_empty_owner_fires_everything(state, monkeypatch):
    """An install with no username cannot call anything foreign — `is_owner_authored`'s rule."""
    monkeypatch.setattr("personalclaw.identity.current_username", lambda: "")
    note = _notify(state, TEAMMATE)
    assert state.captured["broadcast"] == [note]


def test_c_addressing_is_case_and_whitespace_insensitive(state):
    """`triggers.ownership` normalizes both sides before comparing; an addressee that differs
    only in case is the owner's, not a silently withheld note."""
    note = _notify(state, f"  {OWNER.upper()}  ")
    assert state.captured["broadcast"] == [note]


# ── (d) PLUGGABLE: it ROUTES to the intended owner ────────────────────────────


def test_d_foreign_note_routes_to_the_backend_that_addresses_the_owner(state):
    """The "rather than 'the' dashboard" half: a registered backend that says it can reach the
    addressee gets the note, and the row records who took it."""
    backend = _FixtureBackend("team-bridge", TEAMMATE)
    delivery_registry.register_provider(backend)

    note = _notify(state, TEAMMATE)
    assert [n["title"] for n in backend.delivered] == ["Ready for review"]
    assert note[addressing.ROUTED_TO_KEY] == "team-bridge"
    # Routed is not fired-here: the local screen still stays quiet.
    assert state.captured["broadcast"] == []


def test_d_a_backend_that_cannot_reach_the_addressee_is_not_offered_the_note(state):
    backend = _FixtureBackend("dm-bridge", "someone-else")
    delivery_registry.register_provider(backend)

    note = _notify(state, TEAMMATE)
    assert backend.delivered == []
    assert note[addressing.ROUTED_TO_KEY] == ""


def test_d_with_no_backend_installed_the_note_is_visible_not_delivered(state):
    """The ordinary single-user case. `routed_to` must be falsy — a truthy value here would
    make the UI claim a delivery that never happened."""
    note = _notify(state, TEAMMATE)
    assert note[addressing.ROUTED_TO_KEY] == ""
    assert note[addressing.WITHHELD_REASON_KEY] == addressing.FOREIGN_ADDRESSEE


def test_d_a_declining_backend_does_not_claim_the_delivery(state):
    """`deliver` returning False means "I did not take it"."""
    delivery_registry.register_provider(_FixtureBackend("half-bridge", TEAMMATE, accept=False))
    note = _notify(state, TEAMMATE)
    assert note[addressing.ROUTED_TO_KEY] == ""


def test_d_a_raising_backend_cannot_suppress_a_working_one(state):
    """One broken app must not be able to block another app's working route — the fail-open
    posture the whole notify path already takes."""

    class _Boom(_FixtureBackend):
        def deliver(self, note: Any) -> bool:
            raise RuntimeError("bridge down")

    delivery_registry.register_provider(_Boom("broken-bridge", TEAMMATE))
    good = _FixtureBackend("good-bridge", TEAMMATE)
    delivery_registry.register_provider(good)

    note = _notify(state, TEAMMATE)
    assert note[addressing.ROUTED_TO_KEY] == "good-bridge"
    assert len(good.delivered) == 1


def test_d_a_locally_addressed_note_is_never_offered_to_a_backend(state):
    """The seam is the route for what this harness cannot serve. Handing it the owner's own
    notes would send every local toast to an installed app for no reason."""
    backend = _FixtureBackend("team-bridge", OWNER)
    delivery_registry.register_provider(backend)
    _notify(state, OWNER)
    assert backend.delivered == []


# ── The platform's verdict is not an emitter's input ──────────────────────────


@pytest.mark.parametrize("key", [addressing.WITHHELD_REASON_KEY, addressing.ROUTED_TO_KEY])
def test_an_emitter_cannot_supply_the_addressing_verdict(state, key):
    """Both fields are `notify`'s conclusion. An emitter that could set them could label its
    own note "already routed to Dana" while it was fired at the local owner — the exact
    inversion the frontend reads them to render (issue 423's trust boundary)."""
    state.notify("info", "T", "B", meta={key: "smuggled"})
    note = state._notification_log[-1]
    assert note.get(key, "") != "smuggled"


# ── The mirror is the trigger posture, asserted rather than claimed ───────────


def test_the_fire_predicate_agrees_with_the_trigger_owner_predicate():
    """`is_locally_addressed` and `triggers.ownership.is_owner_authored` are one rule with two
    subjects. Asserted over the full 3x3 of {empty, owner, other} so a future edit to either
    cannot quietly change one of the nine answers."""
    from personalclaw.triggers.ownership import is_owner_authored

    class _Row:
        def __init__(self, author: str) -> None:
            self.author = author

    for owner in ("", OWNER, TEAMMATE):
        for who in ("", OWNER, TEAMMATE):
            assert addressing.is_locally_addressed(
                {addressing.ADDRESSEE_KEY: who}, owner=owner
            ) is is_owner_authored(_Row(who), owner=owner), (owner, who)


def test_the_batch_form_filters_a_list_and_preserves_order():
    notes = [
        {addressing.ADDRESSEE_KEY: OWNER, "n": 1},
        {addressing.ADDRESSEE_KEY: TEAMMATE, "n": 2},
        {"n": 3},
    ]
    assert [n["n"] for n in addressing.locally_addressed(notes, owner=OWNER)] == [1, 3]


def test_the_digest_drops_a_queued_foreign_note(monkeypatch):
    """`locally_addressed`'s production caller. The queue is durable state under the home, so
    it can hold rows a synced/older writer appended — filtering at drain is what makes the
    posture a property of the digest rather than of one writer's good behaviour."""
    from personalclaw import notification_rules

    monkeypatch.setattr("personalclaw.identity.current_username", lambda: OWNER)
    monkeypatch.setattr(
        notification_rules,
        "drain_digest_queue",
        lambda: [{"kind": "info", "title": "theirs", addressing.ADDRESSEE_KEY: TEAMMATE}],
    )
    emitted: list[str] = []
    monkeypatch.setattr(
        "personalclaw.inbox.emit_attention_item",
        lambda *a, **k: emitted.append(k.get("title", "")) or "i-1",
    )
    assert notification_rules.run_digest(None) == ""
    assert emitted == []


# ── The production path: a shared inbox item's notification is addressed ──────


def test_emit_attention_item_addresses_the_notification_to_the_items_own_owner(monkeypatch):
    """The wiring that makes this non-inert. `TSE2-3`'s shared inbox renders a teammate's
    item; when that item wants attention the notification must be addressed to THEM, because
    the notification is a *view* of the item."""
    from personalclaw.inbox import InboxItem, InboxStore, emit_attention_item

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def notify(self, kind, title, body, *, meta=None):  # noqa: ANN001
            self.calls.append(dict(meta or {}))

    store = InboxStore()
    store.items = {}
    monkeypatch.setattr(store, "save", lambda: None, raising=False)
    # A shared source hands over a teammate's item; `InboxStore.add` preserves a value
    # already set rather than re-attributing it to the local owner.
    monkeypatch.setattr("personalclaw.inbox._local_username", lambda: OWNER)
    original_add = store.add

    def _add(item: InboxItem) -> None:
        item.owner_username = TEAMMATE
        original_add(item)

    monkeypatch.setattr(store, "add", _add, raising=False)

    state = _Recorder()
    emit_attention_item(
        state, source="system", kind="needs_input", title="Approve?", body="", store=store
    )
    assert state.calls, "emit_attention_item delivered no notification"
    assert state.calls[-1][addressing.ADDRESSEE_KEY] == TEAMMATE


def test_emit_attention_item_addresses_a_local_item_to_the_local_owner(monkeypatch):
    """The same call on an ordinary local item addresses it HERE — so the wiring above is not
    a behaviour change for anybody without a shared source installed."""
    from personalclaw.inbox import InboxStore, emit_attention_item

    calls: list[dict[str, Any]] = []

    class _Recorder:
        def notify(self, kind, title, body, *, meta=None):  # noqa: ANN001
            calls.append(dict(meta or {}))

    store = InboxStore()
    store.items = {}
    monkeypatch.setattr(store, "save", lambda: None, raising=False)
    monkeypatch.setattr("personalclaw.inbox._local_username", lambda: OWNER)

    emit_attention_item(
        _Recorder(), source="system", kind="needs_input", title="Approve?", body="", store=store
    )
    assert calls[-1][addressing.ADDRESSEE_KEY] == OWNER


# ── The provider type is a real handler now, not a declared-but-dead seam ─────


def test_notification_type_handler_round_trips_a_backend_through_the_registry():
    """The #47 bug class: the type was in `PROVIDER_TYPES` with an `EntitySeamHandler` that
    ran the factory and discarded the result, so an app's backend could never be reached. A
    phantom is worse here than for a source — a note recorded `routed_to: <uninstalled app>`
    reads as delivered when nothing was."""
    from personalclaw.providers.registry import NotificationTypeHandler

    handler = NotificationTypeHandler()
    backend = _FixtureBackend("handler-bridge", TEAMMATE)
    handler.register(None, backend)  # ext unused by register()
    assert "handler-bridge" in delivery_registry.list_provider_names()
    handler.deregister(None, backend)
    assert "handler-bridge" not in delivery_registry.list_provider_names()


def test_a_backend_without_a_name_is_refused():
    """Keyed by `delivery_name`; an unnamed backend could not be deregistered, which is the
    phantom above."""

    class _Nameless:
        delivery_name = ""

    with pytest.raises(ValueError):
        delivery_registry.register_provider(_Nameless())  # type: ignore[arg-type]


def test_the_delivery_contract_is_reexported_through_the_sdk():
    """An app implements this from `personalclaw.sdk.notification`, never the core path."""
    from personalclaw.notification_providers.base import (
        NotificationDeliveryProvider as CoreProvider,
    )

    assert NotificationDeliveryProvider is CoreProvider
    for member in ("delivery_name", "addresses", "deliver"):
        assert hasattr(NotificationDeliveryProvider, member)
