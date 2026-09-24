"""Second-opinion verification gate for attention notifications (INU-6).

Three risks drive these tests.

**The gate must actually fire — and only when opted in.** A verification pass that is
defined but never reached from ``emit_attention_item`` would be a dead control: every item
delivered, the feature inert. So the emit-hook tests assert the OUTCOME (a refuted item is
FILTERED and its notification withheld), not merely that ``verify_attention_item`` exists.
Equally, an emit of a non-verifiable kind, or a verifiable kind whose rule did not opt in,
must make NO model call and be byte-for-byte the old behavior.

**Only a clear refutation may withhold (REFUTED-only, fail-OPEN).** Dropping a legitimate
attention item can lose a loop that needed an answer, so every ambiguous, empty, or failed
verdict must resolve to ``skipped`` and deliver. The dangerous direction — a false positive
that filters a real item — is the one made hard to reach.

**Restore fires the withheld notification exactly once.** A false positive must be fully
recoverable: Restore flips FILTERED→PENDING and replays the one notification verification
suppressed — not zero (a silent drop) and not twice (a double-interrupt on a repeat call).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import notification_rules
from personalclaw.inbox import (
    InboxItem,
    InboxStore,
    ItemStatus,
    _verification_opted_in,
    emit_attention_item,
)
from personalclaw.notification_verify import (
    CONFIRMED,
    REFUTED,
    SKIPPED,
    _parse_verdict,
    verify_attention_item,
)


@pytest.fixture()
def store(tmp_path):
    return InboxStore(path=tmp_path / "inbox_items.json")


@pytest.fixture()
def state():
    st = MagicMock()
    st.notify = MagicMock()
    return st


# ── _parse_verdict: the closed verdict map, conservative ─────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("REFUTED", REFUTED),
        ("refuted", REFUTED),
        ("REFUTED — the claim is contradicted", REFUTED),  # first token wins
        ('{"verdict": "refuted"}', REFUTED),
        ("CONFIRMED", CONFIRMED),
        ('{"verdict": "confirmed"}', CONFIRMED),
        ("UNCERTAIN", SKIPPED),  # ambiguity delivers
        ("", SKIPPED),  # empty delivers
        ("   ", SKIPPED),
        ("I think it might be refuted", SKIPPED),  # buried, not first token → deliver
        ("garbage {not json", SKIPPED),
        ('{"verdict": "maybe"}', SKIPPED),  # unknown verdict word → deliver
    ],
)
def test_parse_verdict_maps_onto_the_closed_set(raw, expected):
    assert _parse_verdict(raw) == expected


def test_parse_verdict_never_returns_an_off_menu_value():
    for raw in ("weird", "REFUTED?", "confirmed!!!", "\n\nrefuted\n"):
        assert _parse_verdict(raw) in (CONFIRMED, REFUTED, SKIPPED)


# ── verify_attention_item: the one model call, fail-OPEN ─────────────────


@pytest.mark.asyncio
async def test_empty_claim_skips_without_calling_the_model(monkeypatch):
    """No claim to check → skip, and never spend a model call doing it."""
    called = MagicMock()
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", called)
    assert await verify_attention_item("", "") == SKIPPED
    called.assert_not_called()


@pytest.mark.asyncio
async def test_a_clear_refutation_refutes(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.llm_helpers.one_shot_completion", AsyncMock(return_value="REFUTED")
    )
    assert await verify_attention_item("A false claim") == REFUTED


@pytest.mark.asyncio
async def test_a_confirmation_confirms(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.llm_helpers.one_shot_completion", AsyncMock(return_value="CONFIRMED")
    )
    assert await verify_attention_item("A true claim") == CONFIRMED


@pytest.mark.asyncio
async def test_uncertainty_skips(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.llm_helpers.one_shot_completion", AsyncMock(return_value="UNCERTAIN")
    )
    assert await verify_attention_item("An ambiguous claim") == SKIPPED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boom",
    [
        RuntimeError("no model configured"),
        TimeoutError("hard timeout"),
        Exception("circuit open / budget exhausted"),
    ],
)
async def test_every_model_failure_path_fails_open_to_skipped(monkeypatch, boom):
    """No model, timeout, breaker/budget — a claim we could not check is still delivered."""
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", AsyncMock(side_effect=boom))
    assert await verify_attention_item("Unverifiable due to failure") == SKIPPED


# ── _verification_opted_in: registry AND rule, fail-CLOSED ───────────────


def test_opted_in_true_only_for_a_verifiable_kind_whose_rule_asked(monkeypatch):
    monkeypatch.setattr(
        notification_rules, "resolve_rule", lambda s, k: SimpleNamespace(verify=True)
    )
    # skills/proposal is registered verifiable=True.
    assert _verification_opted_in("skills", "proposal") is True


def test_opted_in_false_for_a_non_verifiable_kind(monkeypatch):
    """A rule can't opt a non-verifiable kind in — the registry gate wins first."""
    monkeypatch.setattr(
        notification_rules, "resolve_rule", lambda s, k: SimpleNamespace(verify=True)
    )
    # loop/needs_input is attention-bearing but NOT verifiable.
    assert _verification_opted_in("loop", "needs_input") is False


def test_opted_in_false_when_the_rule_did_not_ask(monkeypatch):
    monkeypatch.setattr(
        notification_rules, "resolve_rule", lambda s, k: SimpleNamespace(verify=False)
    )
    assert _verification_opted_in("skills", "proposal") is False


def test_opted_in_fails_closed_when_the_rule_read_throws(monkeypatch):
    """A broken policy read must never START filtering — it degrades to deliver."""

    def boom(s, k):
        raise RuntimeError("rules store unreadable")

    monkeypatch.setattr(notification_rules, "resolve_rule", boom)
    assert _verification_opted_in("skills", "proposal") is False


# ── emit_attention_item hook: the OUTCOME (not-inert proof) ──────────────


def _opt_in(monkeypatch, verify=True):
    monkeypatch.setattr(
        notification_rules, "resolve_rule", lambda s, k: SimpleNamespace(verify=verify)
    )


def _verdict(monkeypatch, verdict):
    monkeypatch.setattr(
        "personalclaw.notification_verify.run_verification_sync", lambda *a, **k: verdict
    )


def test_a_refuted_verifiable_item_is_filtered_and_not_notified(store, state, monkeypatch):
    _opt_in(monkeypatch)
    _verdict(monkeypatch, REFUTED)
    item_id = emit_attention_item(
        state, source="skills", kind="proposal", title="Add a bogus skill", store=store
    )
    item = store.items[item_id]
    assert item.status == ItemStatus.FILTERED.value, "a refuted claim is withheld"
    assert item.refs["verify"] == REFUTED
    assert item.refs["verify_withheld"]["title"] == "Add a bogus skill"
    assert state.notify.call_count == 0, "the withheld notification must NOT fire"


def test_a_confirmed_verifiable_item_is_delivered(store, state, monkeypatch):
    _opt_in(monkeypatch)
    _verdict(monkeypatch, CONFIRMED)
    item_id = emit_attention_item(
        state, source="skills", kind="proposal", title="A real proposal", store=store
    )
    assert store.items[item_id].status == ItemStatus.PENDING.value
    assert store.items[item_id].refs["verify"] == CONFIRMED
    assert state.notify.call_count == 1, "a confirmed claim is delivered normally"


def test_a_skipped_verdict_delivers_carrying_the_marker(store, state, monkeypatch):
    _opt_in(monkeypatch)
    _verdict(monkeypatch, SKIPPED)
    item_id = emit_attention_item(
        state, source="skills", kind="proposal", title="Unchecked", store=store
    )
    assert store.items[item_id].status == ItemStatus.PENDING.value
    assert store.items[item_id].refs["verify"] == SKIPPED
    assert state.notify.call_count == 1


def test_a_non_verifiable_kind_is_never_verified(store, state, monkeypatch):
    """The common path makes NO model call and carries no verify marker."""
    ran = MagicMock()
    monkeypatch.setattr("personalclaw.notification_verify.run_verification_sync", ran)
    emit_attention_item(state, source="loop", kind="needs_input", title="T", store=store)
    ran.assert_not_called()
    assert state.notify.call_count == 1


def test_a_verifiable_kind_not_opted_in_is_never_verified(store, state, monkeypatch):
    _opt_in(monkeypatch, verify=False)
    ran = MagicMock()
    monkeypatch.setattr("personalclaw.notification_verify.run_verification_sync", ran)
    emit_attention_item(state, source="skills", kind="proposal", title="T", store=store)
    ran.assert_not_called()
    assert state.notify.call_count == 1


# ── Restore: fire the withheld notification exactly once ─────────────────


def _filtered_item(store):
    """A FILTERED item carrying the replay payload the emit hook would have written."""
    item = InboxItem(
        id="proposal_deadbeef_100.0",
        channel="",
        channel_name="",
        thread_ts=None,
        message="Add a bogus skill",
        sender_id="",
        sender_name="",
        item_kind="proposal",
        created_at=100.0,
    )
    item.status = ItemStatus.FILTERED.value
    item.refs = {
        "verify": REFUTED,
        "verify_withheld": {
            "kind": "skills:proposal",
            "title": "Add a bogus skill",
            "body": "because X",
            "item_kind": "proposal",
        },
    }
    store.add(item)
    store.save()
    return item


def _restore_request(store, item_id):
    st = MagicMock()
    st._inbox_svc = None
    st._inbox_state = MagicMock()
    st._inbox_store = store
    st.notify = MagicMock()
    st.broadcast_ws = MagicMock()
    req = MagicMock()
    req.app = {"state": st}
    req.match_info = {"id": item_id}
    return req, st


@pytest.mark.asyncio
async def test_restore_flips_to_pending_and_fires_the_withheld_notification_once(
    store, monkeypatch
):
    from personalclaw.dashboard import handlers_inbox as h

    monkeypatch.setattr(h, "sel", MagicMock())  # no SEL write to the real home
    item = _filtered_item(store)
    req, st = _restore_request(store, item.id)

    resp = await h.api_inbox_restore(req)

    assert resp.status == 200
    assert store.items[item.id].status == ItemStatus.PENDING.value
    assert st.notify.call_count == 1, "the withheld notification fires exactly once"
    kind, title, body = st.notify.call_args[0][:3]
    assert (kind, title) == ("skills:proposal", "Add a bogus skill")
    # The replay payload is consumed so no later path can re-fire it.
    assert "verify_withheld" not in store.items[item.id].refs
    assert store.items[item.id].refs["verify"] == "restored"


@pytest.mark.asyncio
async def test_restore_on_an_already_restored_item_is_a_conflict_no_op(store, monkeypatch):
    """A second call cannot double-notify: only the FILTERED→PENDING edge fires."""
    from personalclaw.dashboard import handlers_inbox as h

    monkeypatch.setattr(h, "sel", MagicMock())
    item = _filtered_item(store)
    req, st = _restore_request(store, item.id)

    await h.api_inbox_restore(req)  # first: fires
    resp2 = await h.api_inbox_restore(req)  # second: item is PENDING now

    assert resp2.status == 409
    assert st.notify.call_count == 1, "still exactly one notification, never two"


@pytest.mark.asyncio
async def test_restore_missing_item_is_404(store, monkeypatch):
    from personalclaw.dashboard import handlers_inbox as h

    monkeypatch.setattr(h, "sel", MagicMock())
    req, _ = _restore_request(store, "does_not_exist_0.0")
    assert (await h.api_inbox_restore(req)).status == 404


# ── V6: true + planted-false + unbound model in one flow ─────────────────


def test_v6_true_delivers_false_filters_unbound_delivers(store, state, monkeypatch):
    """The plan's V6 acceptance: a true proposal is kept, a planted-false one is FILTERED,
    and with no model bound everything degrades to delivered (skipped)."""
    _opt_in(monkeypatch)

    _verdict(monkeypatch, CONFIRMED)
    true_id = emit_attention_item(
        state, source="skills", kind="proposal", title="true", store=store
    )
    _verdict(monkeypatch, REFUTED)
    false_id = emit_attention_item(
        state, source="skills", kind="proposal", title="planted false", store=store
    )
    _verdict(monkeypatch, SKIPPED)  # unbound model → skipped (fail-open)
    unbound_id = emit_attention_item(
        state, source="skills", kind="proposal", title="unbound", store=store
    )

    assert store.items[true_id].status == ItemStatus.PENDING.value
    assert store.items[false_id].status == ItemStatus.FILTERED.value
    assert store.items[unbound_id].status == ItemStatus.PENDING.value
    # Two delivered (true + unbound), one withheld (false).
    assert state.notify.call_count == 2
