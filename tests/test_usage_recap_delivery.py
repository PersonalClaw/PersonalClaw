"""MRT-3 — the monthly usage recap's DELIVERY (the clause the fold shipped without).

`usage_recap(month)` renders and is pinned verbatim by `test_routing_usage.py`. This file covers
the other half of the clause: "delivers ONE digest-mode notification honoring quiet hours/mute via
the rules engine + system cron". Three properties, each of which is a GATE and therefore worthless
untested:

1. **Exactly one per month.** The mark is checked before rendering, so a monthly cron that fires
   twice (an overdue trigger re-armed by the boot sweep, a hand fire, a reconcile) emits once.
2. **Quiet hours / mute suppress it.** These live in `notification_allowed`, upstream of the rules
   engine, and a recap is `SEV_INFO` — so both must swallow it whole.
3. **The system cron exists and is ARMED.** A registered-but-unarmed trigger never runs; that is
   the exact S108 defect `digest_provider`'s tests were rewritten to catch.

Every delivery assertion here is paired with a VACUITY control: a suppression test that would pass
on a recap that never rendered proves nothing, so each one is run twice — once suppressed, once not
— against the same fold, and the unsuppressed leg must produce a real figure.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.action_providers import usage_recap_provider as P
from personalclaw.action_providers.base import ActionContext
from personalclaw.routing import usage as U

MONTH = "2026-07"
DAY1 = "2026-07-02"
DAY2 = "2026-07-19"

#: A fold with real money in it. Small on purpose — the arithmetic is pinned in
#: `test_routing_usage.py`; what matters here is that a NON-EMPTY recap reaches the gate.
FIXTURE_GROUPS = [
    (6, DAY1, "chat", "anthropic", "claude-x", 400, 40, 0.05, True),
    (4, DAY1, "loop", "ollama-models", "qwen3:8b", 200, 20, 0.0, True),
    (2, DAY2, "cron", "anthropic", "claude-x", 50, 5, 0.02, True),
]

_STUB_RATES = {("anthropic", "claude-x"): None, ("ollama-models", "qwen3:8b"): None}


@pytest.fixture()
def stub_rates(monkeypatch):
    """Pin the rate table so the expected figures cannot drift with shipped price defaults.

    `priced=True` rows carry their own `cost_usd`, so a None rate is enough: the fold uses the
    recorded cost and never consults the table.
    """
    monkeypatch.setattr(U, "rate_for", lambda p, m, home=None: _STUB_RATES.get((p, m)))


def _turns() -> list[dict]:
    rows: list[dict] = []
    for n, date, source, provider, model, t_in, t_out, cost, priced in FIXTURE_GROUPS:
        for _ in range(n):
            rows.append(
                {
                    "ts": f"{date}T12:00:00+00:00",
                    "session_key": "s1",
                    "source": source,
                    "agent": "",
                    "provider": provider,
                    "model": model,
                    "input_tokens": t_in,
                    "output_tokens": t_out,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cost_usd": cost,
                    "priced": priced,
                    "duration_ms": 1200,
                }
            )
    return rows


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """One isolated home for EVERY reader the delivery path touches.

    The provider reads the fold through `config.loader.config_dir`, the rules engine reads its
    store and the digest queue through `notification_rules.config_dir`, the global gate reads
    entity settings through `entity_routes`, and `DashboardState` persists the notification log
    through its own `config_dir`. Patching three of the four is how a "no notification was
    delivered" assertion passes while the fourth wrote to the real home.
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
    return tmp_path


def _seed_fold(home: Path) -> dict:
    """Persist a real fold at `usage_stats.json`, the way `refresh` would."""
    ledger = home / "turns.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in _turns()), encoding="utf-8")
    return U.rebuild(home, ledger_path=ledger, audit_path=home / "none.jsonl")


def _wire_state(monkeypatch, home: Path):
    """A real `DashboardState` behind a real `ActionServices` — the provider's only route to
    `notify()`, and therefore to the rules engine. A MagicMock here would record a call and
    prove nothing about the gate, which is the whole subject of this file."""
    from personalclaw.action_providers import services as S
    from personalclaw.dashboard.state import DashboardState

    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    monkeypatch.setattr(
        S,
        "_services",
        S.ActionServices(state=state, spawn_background=lambda coro: None),
    )
    return state


def _fire(month: str | None = MONTH):
    cfg = {} if month is None else {"month": month}
    return asyncio.run(P.UsageRecapActionProvider().execute(cfg, ActionContext(event="cron")))


def _queued(home: Path) -> list[dict]:
    from personalclaw import notification_rules as nr

    path = nr.digest_queue_path()
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_notification_settings(home: Path, **kw) -> None:
    (home / "notifications.json").write_text(json.dumps(kw), encoding="utf-8")


# ── month arithmetic ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "want"),
    [
        (datetime(2026, 8, 1), "2026-07"),
        (datetime(2026, 8, 31), "2026-07"),
        # 🔴 The case an f-string built from `month - 1` renders as "2026-00".
        (datetime(2026, 1, 1), "2025-12"),
        (datetime(2026, 3, 1), "2026-02"),
    ],
)
def test_previous_month_wraps_the_year_boundary(now, want):
    assert P.previous_month(now) == want


def test_the_cron_defaults_to_the_month_that_just_closed(home, stub_rates, monkeypatch):
    """No `month` in the action config ⇒ the previous month, not the current one.

    A recap of the CURRENT month on the 1st would summarize a few hours of spend and read as a
    collapse in usage.
    """
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    res = _fire(month=None)
    assert res.success
    assert P.previous_month() in res.stdout


# ── the delivery itself ─────────────────────────────────────────────────────────────────


def test_the_recap_is_delivered_as_ONE_digest_mode_notification(home, stub_rates, monkeypatch):
    """The clause's core: one notification, mode `digest`, body == the pinned renderer's output."""
    fold = _seed_fold(home)
    _wire_state(monkeypatch, home)

    # VACUITY FLOOR — a recap over an empty month would satisfy every assertion below while
    # proving nothing about a delivery path. The fixture must have produced real rows.
    assert fold["days"], "the fixture produced no fold days"
    expected = U.usage_recap(MONTH, fold=fold)
    assert "12 turns" in expected, f"the fixture recapped nothing real: {expected!r}"

    res = _fire()
    assert res.success, res.error

    queued = _queued(home)
    assert len(queued) == 1, f"expected exactly one queued notification, got {len(queued)}"
    note = queued[0]
    assert note["kind"] == "usage_recap"
    assert note["mode"] == "digest", "the registered default mode for system/usage_recap"
    assert note["body"] == expected, "the delivered body must be the pinned renderer's output"
    assert note["month"] == MONTH

    # A digest-mode note is QUEUED, never pushed — so nothing landed in the live log.
    from personalclaw import notification_rules as nr

    assert nr.digest_queue_path().exists()


def test_the_delivered_body_says_the_money_is_an_estimate(home, stub_rates, monkeypatch):
    """Money rule: a rounded estimate must never read as an exact charge.

    Asserted on the DELIVERED body rather than on the renderer, because the notification is what
    a user actually reads.
    """
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    assert _fire().success

    body = _queued(home)[0]["body"]
    assert "~$" in body, "every dollar in a recap is an estimate and must carry the tilde"
    assert "Every dollar here is an estimate, not a provider-reported charge." in body


def test_firing_TWICE_in_a_month_delivers_exactly_once(home, stub_rates, monkeypatch):
    """🔴 A monthly cron firing twice is real, not hypothetical: the boot sweep re-arms an
    overdue trigger, so a machine asleep across the 1st fires on wake and again next month.
    Without the mark this is "one recap per boot in the first week of the month"."""
    _seed_fold(home)
    _wire_state(monkeypatch, home)

    first = _fire()
    assert first.success
    assert len(_queued(home)) == 1  # vacuity floor: the first fire really delivered

    second = _fire()
    assert second.success, "a duplicate suppression is a success, not a failed cron"
    # The COUNT first, deliberately: asserting the stdout string first short-circuits, and a
    # falsification run then reds on the wording rather than on the property that matters.
    assert len(_queued(home)) == 1, "the second fire delivered a SECOND recap"
    assert "already sent" in second.stdout


def test_a_different_month_is_not_suppressed_by_the_previous_months_mark(
    home, stub_rates, monkeypatch
):
    """The vacuity control for the test above: the mark must key on the MONTH, not act as a
    permanent latch that silences every future recap."""
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    assert _fire(month="2026-07").success
    assert _fire(month="2026-08").success
    assert len(_queued(home)) == 2


def test_the_mark_records_which_month_was_sent(home, stub_rates, monkeypatch):
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    _fire()
    mark = P.read_mark(home)
    assert mark["last_month"] == MONTH
    assert mark["delivered"] is True
    assert mark["last_at"]


# ── the gates (quiet hours + mute), each with its unsuppressed control ──────────────────


def test_quiet_hours_suppress_the_recap(home, stub_rates, monkeypatch):
    """A recap is SEV_INFO, and quiet hours drop everything below `error`.

    The window is 00:00→23:59 so the test does not depend on the wall clock — a fixed window
    around "now" would pass or fail by time of day, which is a flake, not a gate.
    """
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    _write_notification_settings(
        home,
        quiet_hours_enabled=True,
        quiet_hours_start="00:00",
        quiet_hours_end="23:59",
    )

    res = _fire()
    assert res.success, "suppression by the user's own setting is not a failed action"
    assert _queued(home) == [], "quiet hours did not suppress the recap"


def test_the_recap_IS_delivered_when_quiet_hours_are_off(home, stub_rates, monkeypatch):
    """The vacuity control for the test above. Identical fixture, `quiet_hours_enabled` false —
    if this also produced nothing, the suppression test would be measuring a broken fixture."""
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    _write_notification_settings(
        home,
        quiet_hours_enabled=False,
        quiet_hours_start="00:00",
        quiet_hours_end="23:59",
    )
    assert _fire().success
    assert len(_queued(home)) == 1


def test_mute_all_suppresses_the_recap(home, stub_rates, monkeypatch):
    """Mute means mute, whatever the rule says — the global gate is outermost."""
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    _write_notification_settings(home, mute_all=True)
    assert _fire().success
    assert _queued(home) == [], "mute_all did not suppress the recap"


def test_a_never_rule_suppresses_the_recap(home, stub_rates, monkeypatch):
    """The per-kind rule half of "via the rules engine": a user who sets system/usage_recap to
    `never` must stop receiving it, and that path is distinct from the global gate above."""
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps({"rules": {"system/usage_recap": {"mode": "never"}}}), encoding="utf-8"
    )
    assert _fire().success
    assert _queued(home) == []


def test_an_immediate_rule_pushes_instead_of_queueing(home, stub_rates, monkeypatch):
    """A user may opt OUT of digest mode. Proves the recap rides the rules engine rather than
    writing the digest queue directly — a direct queue write would ignore this setting."""
    _seed_fold(home)
    state = _wire_state(monkeypatch, home)
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps({"rules": {"system/usage_recap": {"mode": "immediate"}}}), encoding="utf-8"
    )
    assert _fire().success
    assert _queued(home) == [], "an immediate rule must not queue"
    assert [n["kind"] for n in state._notification_log] == ["usage_recap"]


# ── an empty month, and a missing fold ──────────────────────────────────────────────────


def test_an_empty_month_delivers_the_no_turns_sentence_not_a_zero_dollar_figure(
    home, stub_rates, monkeypatch
):
    """ "$0.00 this month" is a claim; "no model turns recorded" is the truth."""
    _seed_fold(home)
    _wire_state(monkeypatch, home)
    assert _fire(month="2026-01").success
    body = _queued(home)[0]["body"]
    assert body == "January 2026: no model turns recorded."
    assert "$0.00" not in body


def test_no_dashboard_state_is_an_error_and_writes_no_mark(home, stub_rates, monkeypatch):
    """Nothing was attempted, so the next fire must still try — a mark here would silently
    swallow the month."""
    from personalclaw.action_providers import services as S

    _seed_fold(home)
    monkeypatch.setattr(S, "_services", None)
    res = _fire()
    assert not res.success
    assert P.read_mark(home) == {}


# ── the system cron ─────────────────────────────────────────────────────────────────────


def _store(home: Path):
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=home)


def test_the_recap_cron_is_registered_and_ARMED(home):
    """🔴 ARMED is the difference between a registered recap and one that runs — the S108 defect
    a fake store could not see, which is why this drives a real `TriggerStore`."""
    store = _store(home)
    P.reconcile_usage_recap_cron(store)

    row = store.get(P.USAGE_RECAP_JOB_NAME)
    assert row is not None
    assert row.trigger.spec["expr"] == P.USAGE_RECAP_SCHEDULE
    assert row.trigger.kind == "clock"
    assert row.trigger.enabled
    inline = (row.trigger.workflow or {}).get("inline") or {}
    assert inline.get("provider") == "usage-recap"
    # The recap's OUTPUT is a notification; a cron-result toast about it would be a notification
    # about your notification.
    assert row.trigger.delivery == "none"
    assert row.trigger.next_fire_at, "registered but never armed ⇒ it never fires"
    assert row.ok, row.errors


def test_the_recap_cron_fires_on_the_first_of_the_month(home):
    """The schedule must land on the 1st: on the 31st the month it recaps is still open."""
    store = _store(home)
    P.reconcile_usage_recap_cron(store)
    assert P.USAGE_RECAP_SCHEDULE.split()[2] == "1"
    assert store.get(P.USAGE_RECAP_JOB_NAME).trigger.next_fire_at.split("T")[0].endswith("-01")


def test_the_recap_cron_is_not_duplicated(home):
    """A deterministic id, so a restart recognizes its own row instead of adding another."""
    store = _store(home)
    P.reconcile_usage_recap_cron(store)
    before = len(store.load())
    P.reconcile_usage_recap_cron(store)
    P.reconcile_usage_recap_cron(store)
    assert len(store.load()) == before


def test_the_recap_cron_carries_the_write_capable_grant(home):
    """Emitting a notification puts something in front of the user unattended, so the fence needs
    the frozen grant (decision 7). Without it the trigger validates and then refuses to fire."""
    from personalclaw.triggers import screen

    store = _store(home)
    P.reconcile_usage_recap_cron(store)
    trigger = store.get(P.USAGE_RECAP_JOB_NAME).trigger
    assert not screen.provider_is_read_only("usage-recap")
    assert trigger.capabilities, "a write-capable action with no grant cannot fire"


def test_the_provider_is_in_BOTH_the_registry_and_the_validation_allowlist():
    """🪤 A provider in one set but not the other validates, saves, and then fails at run time —
    the trap `registry.py`'s own comment records."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
        list_action_providers,
    )
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    # The registry is populated lazily, on the first action execution — so this must drive the
    # same entry point `hooks` does. Reading `_providers` cold returns None for EVERY built-in,
    # including `notification-digest`, which is how this assertion first passed as a red.
    _ensure_default_providers_registered()
    assert "notification-digest" in list_action_providers(), "the registry did not populate"
    assert get_action_provider("usage-recap") is not None
    assert "usage-recap" in ALLOWED_HOOK_PROVIDERS


def test_the_recap_kind_is_registered_with_digest_as_its_default():
    """An unregistered kind falls open to system/generic: it would carry generic's mode, never
    appear as a row in Settings → Notifications, and be grouped in the digest as "Uncategorized"."""
    from personalclaw import notification_kinds as nk

    kind = nk.kind_for_legacy(nk.USAGE_RECAP)
    assert kind.key == "system/usage_recap"
    assert kind.default_mode == "digest"
    assert kind.default_severity == nk.SEV_INFO
    # It persists no row of its own — the digest it rides into is the durable item.
    assert kind.attention is False
