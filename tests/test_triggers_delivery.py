"""The outbound delivery contract (AUTO §7 criterion 10 / R18 — S85).

Criterion 10: "A completed-run notification deep-links (statusUrl) to the exact run journal row; a
retried delivery does not double-ping."

**Measured before writing.** A grep for `statusUrl` or `status_url` across `src/personalclaw`
returned nothing — the deep link the criterion names did not exist anywhere in the package. A
completed-run notification carried a title and a body, so a user reading "Nightly digest
finished" had no route to the run that produced it. R18 calls that "the notification→journal
dead end".

The two load-bearing tests are the criterion's own two clauses:
`test_the_status_url_points_at_the_exact_run` and `test_a_retried_delivery_does_not_double_ping`.
"""

from __future__ import annotations

import pytest

from personalclaw import notification_kinds
from personalclaw.triggers import delivery as D


class _State:
    """A `DashboardState` stand-in that records what reached `notify`.

    Deliberately a recorder rather than a mock of `deliver`: R18 forbids a second notification
    path, so the property under test is "the arguments arrive at `notify`", and mocking the
    function that calls it would assert nothing.
    """

    def __init__(self):
        self.sent: list[dict] = []

    def notify(self, kind, title, body, *, meta=None):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})


def _ok(**over):
    kwargs = dict(
        trigger_id="schedule:j1",
        trigger_name="Nightly digest",
        ok=True,
        summary="wrote 3 items",
        run_id="r1",
    )
    kwargs.update(over)
    return D.build_delivery(**kwargs)


# ── clause 1: the statusUrl deep link ──


def test_the_status_url_points_at_the_exact_run():
    """The criterion, stated directly. Asserted against the live route
    (`WorkflowsSection` documents `#/workflows/runs/<run_id>`), not an invented path."""
    assert D.status_url(run_id="r-abc") == "#/workflows/runs/r-abc"
    assert _ok(run_id="r-abc").status_url == "#/workflows/runs/r-abc"


def test_a_run_id_wins_over_a_trigger_id():
    """R18 says "the exact runs-inbox row / run journal" — the run is the specific thing that just
    happened."""
    assert D.status_url(run_id="r1", trigger_id="schedule:j1") == "#/workflows/runs/r1"


def test_a_fire_with_no_run_links_to_the_trigger():
    """A `LEDGER`-weight fire (suppressed, noop) produces no run directory, and a notification
    about one
    still needs somewhere to go. `#/triggers?open=<id>` is the panel `TriggersListPage` opens."""
    assert D.status_url(trigger_id="schedule:j1") == "#/triggers?open=schedule:j1"


def test_no_ids_yields_an_empty_url_not_a_dashboard_root_link():
    """A link to `#/` tells the user nothing and costs them a click to discover that."""
    assert D.status_url() == ""


def test_the_status_url_reaches_notify_in_meta():
    """`meta` is the dict `notify` already merges into the note, so `statusUrl` reaches every
    surface
    without `InboxItem` or the note schema gaining a field — the seam S51's card also rides."""
    state = _State()
    assert D.deliver(state, _ok()) is True
    assert state.sent[0]["meta"]["statusUrl"] == "#/workflows/runs/r1"


def test_the_wire_key_is_camelCase_as_R18_names_it():
    """A channel consumer reads the wire key. R18 writes `statusUrl`; `status_url` would be a
    different
    field to every external reader."""
    kwargs = _ok().to_notify_kwargs()
    assert "statusUrl" in kwargs["meta"]
    assert "status_url" not in kwargs["meta"]


# ── clause 2: a retried delivery does not double-ping ──


def test_the_event_id_is_stable_across_retries():
    """🔴 DERIVED, never random. A `uuid4()` or a timestamp would produce a NEW id on the
    retry, and the
    consumer would show the notification twice — the exact failure the criterion names."""
    first = D.event_id(trigger_id="schedule:j1", run_id="r1")
    retry = D.event_id(trigger_id="schedule:j1", run_id="r1")
    assert first == retry


def test_a_genuine_rerun_gets_a_new_event_id():
    """A manual re-fire of the same trigger SHOULD ping again — `attempt_key` is what
    distinguishes a
    transport retry from a new event."""
    once = D.event_id(trigger_id="schedule:j1", run_id="r1")
    twice = D.event_id(trigger_id="schedule:j1", run_id="r1", attempt_key="2")
    assert once != twice


def test_different_triggers_never_collide():
    a = D.event_id(trigger_id="schedule:a", run_id="r1")
    b = D.event_id(trigger_id="schedule:b", run_id="r1")
    assert a != b


def test_a_retried_delivery_does_not_double_ping():
    """The criterion's second clause, driven through the real `deliver` path."""
    state = _State()
    seen: set[str] = set()
    delivery = _ok()
    assert D.deliver(state, delivery, delivered_ids=seen) is True
    assert D.deliver(state, delivery, delivered_ids=seen) is False
    assert len(state.sent) == 1


def test_the_seen_set_is_updated_by_deliver():
    """So a caller cannot forget to record it — the failure mode would be silent double-pinging."""
    state = _State()
    seen: set[str] = set()
    delivery = _ok()
    D.deliver(state, delivery, delivered_ids=seen)
    assert delivery.event_id in seen


def test_is_duplicate_accepts_a_list_too():
    """A persisted seen-set arrives as a JSON list, not a set."""
    delivery = _ok()
    assert D.is_duplicate(delivery, [delivery.event_id]) is True
    assert D.is_duplicate(delivery, []) is False
    assert D.is_duplicate(delivery, None) is False


def test_two_distinct_events_both_deliver():
    """Dedup must not swallow a second, genuinely different completion."""
    state = _State()
    seen: set[str] = set()
    assert D.deliver(state, _ok(run_id="r1"), delivered_ids=seen) is True
    assert D.deliver(state, _ok(run_id="r2"), delivered_ids=seen) is True
    assert len(state.sent) == 2


# ── the event types ──


def test_success_and_failure_are_distinct_event_types():
    """🔴 Two names, not one with a boolean: a channel consumer routes on the event name, and
    `automation.run` + `{"ok": false}` would make "only tell me about failures" a body inspection.
    """
    assert _ok().event == D.EVENT_SUCCEEDED
    assert _ok(ok=False).event == D.EVENT_FAILED


def test_the_event_type_rides_meta():
    state = _State()
    D.deliver(state, _ok(ok=False))
    assert state.sent[0]["meta"]["event"] == "automation.run.failed"


def test_ok_reflects_the_event_type():
    assert _ok().ok is True
    assert _ok(ok=False).ok is False


# ── the notification kind ──


def test_a_failure_uses_the_error_kind_so_it_can_escalate():
    """A failure has to be able to escalate past a "digest" rule while a success should not —
    that is a
    property of what happened, not of who is reporting it."""
    assert _ok().kind == notification_kinds.INFO
    assert _ok(ok=False).kind == notification_kinds.ERROR


def test_both_kinds_are_registered_names():
    """Inventing a kind here would produce a notification no rule matches, which resolves to
    `immediate` and ignores the user's settings entirely."""
    for kind in (_ok().kind, _ok(ok=False).kind):
        assert isinstance(kind, str) and kind
        assert hasattr(notification_kinds, kind.upper())


# ── destination-aware formatting ──


@pytest.mark.parametrize(
    "destination,flat",
    [
        ("", False),
        ("inbox", False),
        ("notify", False),
        ("channel:slack", True),
        ("channel:slack:T0123", True),
        ("CHANNEL:Slack", True),
    ],
)
def test_channel_destinations_want_flat_text(destination, flat):
    """🔴 Prefix-matched: the channel id carries a workspace suffix in practice, and an exact
    `== "channel:slack"` would send rich blocks to every real Slack destination — which renders as
    `[object Object]`."""
    assert D.wants_flat_text(destination) is flat


def test_the_flat_text_ends_with_the_status_url():
    """Appended as a LINE, not embedded in prose: a Slack consumer auto-links a bare URL, and a user
    scanning text finds a trailing link faster than one buried mid-sentence."""
    text = _ok().to_text()
    assert text.splitlines()[-1] == "#/workflows/runs/r1"
    assert text.splitlines()[0] == "Nightly digest finished"


def test_the_flat_text_survives_an_empty_body():
    text = _ok(summary="").to_text()
    assert "Nightly digest finished" in text
    assert text.splitlines()[-1] == "#/workflows/runs/r1"


# ── redaction before any surface ──


def test_a_credential_in_the_summary_is_redacted():
    """R18 requires `redact_exfiltration_urls` + `redact_credentials` before any surface, as
    heartbeat
    delivery does today. A run summary is whatever the run produced — it can contain a token a
    tool printed."""
    delivery = _ok(summary="key sk-ant-api03-LOOKSREALENOUGH1234567890 leaked")
    assert "sk-ant-api03" not in delivery.body
    assert "REDACTED" in delivery.body


def test_redaction_covers_the_title_too():
    """The trigger NAME is user-authored and reaches the title; redacting only the body would leak
    through the one line every surface shows."""
    delivery = _ok(trigger_name="job sk-ant-api03-LOOKSREALENOUGH1234567890")
    assert "sk-ant-api03" not in delivery.title


def test_the_body_is_capped():
    """An unbounded run summary pushes the statusUrl off the bottom of a Slack card, defeating
    the deep
    link this session exists to add."""
    delivery = _ok(summary="x" * 5000)
    assert len(delivery.body) <= D.BODY_CAP


# ── it routes through the EXISTING gate, and never breaks the run ──


def test_delivery_goes_through_state_notify_not_a_second_path():
    """R18: "the substrate does not build a second notification path." Asserted by the fact that the
    only outbound call is `state.notify`, which applies `notification_allowed` and the per-kind
    rule.
    """
    state = _State()
    D.deliver(state, _ok())
    assert len(state.sent) == 1
    assert set(state.sent[0]) == {"kind", "title", "body", "meta"}


def test_a_broken_notify_never_fails_the_completed_run():
    """The run already happened; a failed ping must not undo it."""

    class Exploding:
        def notify(self, *a, **kw):
            raise RuntimeError("notify exploded")

    assert D.deliver(Exploding(), _ok()) is False


def test_no_state_is_a_no_op():
    """The state is absent in CLI runs and most tests."""
    assert D.deliver(None, _ok()) is False


def test_a_failed_send_is_not_recorded_as_delivered():
    """Otherwise a transport blip would permanently suppress the retry that should have gone out."""

    class Exploding:
        def notify(self, *a, **kw):
            raise RuntimeError("boom")

    seen: set[str] = set()
    delivery = _ok()
    D.deliver(Exploding(), delivery, delivered_ids=seen)
    assert delivery.event_id not in seen


# ── the wire shape ──


def test_the_dict_form_carries_every_field_a_consumer_needs():
    payload = _ok(duration_secs=12.5).to_dict()
    assert payload["status_url"] == "#/workflows/runs/r1"
    assert payload["event_id"].startswith("evt_")
    assert payload["ok"] is True
    assert payload["meta"]["duration_secs"] == 12.5


def test_a_zero_duration_is_omitted_rather_than_reported_as_zero():
    """A run that reported no duration is not a run that took 0.0s."""
    assert "duration_secs" not in _ok().meta


def test_the_module_imports_without_a_syntax_warning():
    """🔴 Found by RUNNING the module, not by reading it: a backslash-pipe alternation inside a
    non-raw
    docstring is an invalid escape sequence, and Python warns on import. The first version of this
    module's docstring quoted its own grep pattern literally and warned."""
    import importlib
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        importlib.reload(D)


# ── 🔴 the failure route was declared and never read (S158) ──


def test_a_MUTED_automation_still_reports_a_FAILURE():
    """🔴 THE DEFECT. `Trigger.failure_delivery` states its own contract: *"A SEPARATE route for
    failures (R12). Failures reach the inbox even when `delivery` is none: an automation the user
    asked to stay quiet still has to be able to say it broke."*

    It was declared, persisted, round-tripped by `to_dict`/`from_dict`, defaulted by the migration
    and editable through `automation_update` — and read by NOTHING. `_deliver_fire_outcome` passed
    `destination=trigger.delivery` unconditionally, so a quiet automation that BROKE reported its
    failure through the silent channel.
    """
    import types

    from personalclaw.triggers.delivery import route_for

    trigger = types.SimpleNamespace(
        id="t", name="nightly", delivery="none", failure_delivery="inbox"
    )
    assert route_for(trigger, ok=False) == "inbox", "a failure must escape the mute"
    assert route_for(trigger, ok=True) == "none", "…and a success must still respect it"


def test_a_SUCCESS_never_inherits_the_failure_route():
    """The asymmetry is the point: falling back the other way would make a quiet automation start
    announcing its ordinary runs, which is the setting the user explicitly turned off."""
    import types

    from personalclaw.triggers.delivery import route_for

    trigger = types.SimpleNamespace(id="t", name="n", delivery="none", failure_delivery="notify")
    assert route_for(trigger, ok=True) == "none"


def test_an_EMPTY_failure_route_falls_back_to_delivery():
    """A trigger predating the field (or one that cleared it) keeps its old single-route behaviour
    rather than acquiring a channel nobody asked for."""
    import types

    from personalclaw.triggers.delivery import route_for

    trigger = types.SimpleNamespace(id="t", name="n", delivery="none", failure_delivery="")
    assert route_for(trigger, ok=False) == "none"


def test_DESTINATION_none_actually_SILENCES():
    """🔴 THE SECOND HALF. `Delivery` carried `destination` and `to_notify_kwargs` **dropped it**, so
    `delivery: "none"` silenced nothing — measured, a `none` trigger notified exactly like an
    `inbox` one. The field round-tripped and was inert at the one point that could honour it."""
    from personalclaw.triggers.delivery import build_delivery, deliver

    class _State:
        def __init__(self):
            self.sent = []

        def notify(self, **kw):
            self.sent.append(kw)

    state = _State()
    note = build_delivery(trigger_id="t", trigger_name="n", ok=True, destination="none")
    assert deliver(state, note, delivered_ids=set()) is False
    assert state.sent == [], "a muted destination must not reach state.notify at all"

    state2 = _State()
    note2 = build_delivery(trigger_id="t", trigger_name="n", ok=True, destination="inbox")
    assert deliver(state2, note2, delivered_ids=set()) is True
    assert len(state2.sent) == 1


def test_an_EMPTY_destination_is_not_treated_as_MUTED():
    """`from_dict` defaults `delivery` to `"none"` explicitly, so a BLANK value means a caller built
    a Delivery without one. Defaulting that to silence would let a bug become missing alerts — the
    fail-quiet direction, which is exactly what this session fixed."""
    from personalclaw.triggers.delivery import is_muted

    assert is_muted("none") and is_muted("NONE") and is_muted(" none ")
    assert not is_muted("") and not is_muted("inbox") and not is_muted("channel:slack")


def test_the_mute_is_enforced_in_DELIVER_not_at_each_caller():
    """Enforced at the boundary so a future emitter inherits it — the same reason redaction lives
    here. A per-caller check is a control that works until someone adds the next caller."""
    import inspect

    from personalclaw.triggers import delivery

    assert "is_muted(delivery.destination)" in inspect.getsource(delivery.deliver)


def test_the_fire_path_routes_by_OUTCOME():
    """The wiring: the defect was `destination=trigger.delivery` regardless of `ok`."""
    import inspect

    from personalclaw import gateway

    source = inspect.getsource(gateway)
    assert "_delivery.route_for(trigger, ok=ok)" in source
    assert 'destination=str(getattr(trigger, "delivery", "") or "")' not in source
