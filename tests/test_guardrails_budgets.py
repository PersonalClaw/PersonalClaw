"""Tests for spend metering + budgets + the outbound scan (AUTONOMY-GUARDRAILS §1.1, §2.2).

Session 2: SpendMeter (run/day counters in spend.json), budget verdicts, the
secret/PII scan mode ladder, and their integration into ModelCallGuard.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.guardrails.budgets import (
    Budget,
    BudgetVerdict,
    SpendMeter,
)
from personalclaw.guardrails.failure import BudgetExceededError, FailureMode, SecretLeakBlocked
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.guardrails.scan import scan_outbound
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelProvider


class FakeProvider(ModelProvider):
    def __init__(self, *, text="ok", tokens=(10, 20)):
        self._text = text
        self._tokens = tokens
        self._base_url = "https://api.example.com/v1"  # remote by default

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def stream(self, message):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self._text)
        yield LLMEvent(
            kind=EVENT_COMPLETE, input_tokens=self._tokens[0], output_tokens=self._tokens[1]
        )

    async def approve_tool(self, r):
        pass

    async def reject_tool(self, r):
        pass

    def context_usage_pct(self):
        return 0.0


async def _drain(provider, msg="hi"):
    return "".join([e.text async for e in provider.stream(msg) if e.kind == EVENT_TEXT_CHUNK])


# ── Budget dataclass + verdicts ──────────────────────────────────────────────


def test_budget_unlimited_by_default():
    assert Budget().is_unlimited
    assert not Budget(max_tokens=100).is_unlimited
    assert not Budget(max_dollars=1.0).is_unlimited


def test_meter_charge_and_day_total(tmp_path):
    m = SpendMeter(config_dir=tmp_path)
    m.charge(100, 0.05)
    m.charge(50, 0.02)
    total = m.day_totals()
    assert total.tokens == 150
    assert total.dollars == pytest.approx(0.07)
    # Persisted to spend.json.
    data = json.loads((tmp_path / "spend.json").read_text())
    assert sum(v["tokens"] for v in data.values()) == 150


def test_meter_run_scope(tmp_path):
    m = SpendMeter(config_dir=tmp_path)
    m.charge(100, 0.0, run_key="run-A")
    m.charge(30, 0.0, run_key="run-B")
    assert m.run_totals("run-A").tokens == 100
    assert m.run_totals("run-B").tokens == 30
    m.end_run("run-A")
    assert m.run_totals("run-A").tokens == 0  # dropped
    assert m.run_totals("run-B").tokens == 30  # untouched


def test_verdict_ok_warn_exceeded(tmp_path):
    m = SpendMeter(config_dir=tmp_path)
    budget = Budget(max_tokens=100)
    assert m.check_day(budget)[0] is BudgetVerdict.OK
    m.charge(85, 0.0)  # 85% → WARN
    assert m.check_day(budget)[0] is BudgetVerdict.WARN
    m.charge(20, 0.0)  # 105% → EXCEEDED
    assert m.check_day(budget)[0] is BudgetVerdict.EXCEEDED


def test_verdict_dollar_ceiling(tmp_path):
    m = SpendMeter(config_dir=tmp_path)
    budget = Budget(max_dollars=1.0)
    m.charge(0, 1.5)
    assert m.check_day(budget)[0] is BudgetVerdict.EXCEEDED


# ── Outbound scan ladder ─────────────────────────────────────────────────────


def test_scan_clean_text_no_findings():
    r = scan_outbound("just a normal prompt about the weather", mode="block")
    assert r.findings == 0 and not r.blocked


def test_scan_warn_proceeds_with_original():
    r = scan_outbound("my key AKIAIOSFODNN7EXAMPLE here", mode="warn")
    assert r.findings >= 1 and not r.blocked
    assert "AKIA" in r.text  # warn does NOT redact


def test_scan_redact_substitutes():
    r = scan_outbound("email foo@bar.com and AKIAIOSFODNN7EXAMPLE", mode="redact")
    assert r.findings >= 2 and not r.blocked
    assert "foo@bar.com" not in r.text
    assert "AKIA" not in r.text


def test_scan_block_refuses():
    r = scan_outbound("secret AKIAIOSFODNN7EXAMPLE", mode="block")
    assert r.blocked and r.findings >= 1


def test_scan_unknown_mode_treated_as_warn():
    r = scan_outbound("AKIAIOSFODNN7EXAMPLE", mode="bogus")
    assert not r.blocked  # never a silent hard block on an unknown mode


# ── Guard integration: budget ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_charges_meter_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    meter = SpendMeter(config_dir=tmp_path)
    guard = ModelCallGuard(
        FakeProvider(tokens=(10, 20)),
        use_case="reasoning",
        provider_name="P",
        model="m",
        meter=meter,
    )
    await guard.start()
    await _drain(guard)
    assert meter.day_totals().tokens == 30


@pytest.mark.asyncio
async def test_guard_refuses_when_day_budget_exceeded(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(1000, 0.0)  # already over
    guard = ModelCallGuard(
        FakeProvider(),
        use_case="reasoning",
        provider_name="P",
        model="m",
        budget=Budget(max_tokens=500),
        meter=meter,
    )
    await guard.start()
    with pytest.raises(BudgetExceededError) as exc:
        await _drain(guard)
    assert exc.value.scope == "day" and exc.value.dimension == "tokens"


@pytest.mark.asyncio
async def test_guard_unlimited_budget_never_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(1_000_000, 0.0)
    guard = ModelCallGuard(
        FakeProvider(), use_case="reasoning", provider_name="P", model="m", meter=meter
    )  # no budget → unlimited
    await guard.start()
    assert await _drain(guard) == "ok"


# ── Guard integration: scan ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_block_mode_refuses_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    guard = ModelCallGuard(
        FakeProvider(), use_case="reasoning", provider_name="P", model="m", scan_mode="block"
    )
    await guard.start()
    with pytest.raises(SecretLeakBlocked):
        await _drain(guard, "leak this AKIAIOSFODNN7EXAMPLE now")
    # audited as secret_leak
    from personalclaw.guardrails.audit import read_recent

    assert read_recent()[-1]["failure_mode"] == FailureMode.SECRET_LEAK.value


@pytest.mark.asyncio
async def test_guard_redact_mode_rewrites_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    seen: list[str] = []

    class CaptureProvider(FakeProvider):
        async def stream(self, message):
            seen.append(message)
            async for ev in super().stream(message):
                yield ev

    guard = ModelCallGuard(
        CaptureProvider(), use_case="reasoning", provider_name="P", model="m", scan_mode="redact"
    )
    await guard.start()
    await _drain(guard, "my key AKIAIOSFODNN7EXAMPLE")
    assert seen and "AKIA" not in seen[0]  # provider saw the redacted prompt


@pytest.mark.asyncio
async def test_local_provider_forced_to_warn(tmp_path, monkeypatch):
    """A localhost/ollama provider is forced to warn even if config says block —
    the content never leaves the machine (§2.2)."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    from personalclaw.guardrails.model_call import wrap_model_call_guard

    local = FakeProvider()
    local._base_url = "http://localhost:11434"
    guard = wrap_model_call_guard(
        local, use_case="reasoning", provider_name="ollama", model="llama3", scan_mode="block"
    )
    await guard.start()
    # block would raise; warn proceeds → returns text
    assert await _drain(guard, "AKIAIOSFODNN7EXAMPLE") == "ok"


# ── Gateway day-budget dispatch gate (§1.1) ──────────────────────────────────


def test_gateway_day_budget_gate(tmp_path, monkeypatch):
    """Gateway._day_budget_exceeded skips unattended fires + notifies once when the
    day ceiling is hit, and re-arms once back under (auto-resume next day)."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    from personalclaw.guardrails.budgets import Budget, SpendMeter

    meter = SpendMeter(config_dir=tmp_path)
    monkeypatch.setattr("personalclaw.guardrails.budgets.get_meter", lambda: meter)
    budget_holder = {"b": Budget(max_tokens=1000)}
    monkeypatch.setattr(
        "personalclaw.guardrails.budgets.budget_from_config", lambda: budget_holder["b"]
    )

    notes: list[tuple] = []

    class _FakeState:
        def notify(self, kind, title, body, **kw):
            notes.append((kind, title, body))

    from personalclaw.gateway import GatewayOrchestrator

    gw = GatewayOrchestrator.__new__(
        GatewayOrchestrator
    )  # bypass heavy __init__; method uses only 2 attrs
    gw.dashboard_state = _FakeState()
    gw._budget_notified = False

    # Under budget → not exceeded, no note.
    meter.charge(100, 0.0)
    assert gw._day_budget_exceeded(context="cron 'x'") is False
    assert notes == []

    # Over budget → exceeded + exactly one note (de-duped on the second call).
    meter.charge(2000, 0.0)
    assert gw._day_budget_exceeded(context="cron 'x'") is True
    assert gw._day_budget_exceeded(context="cron 'x'") is True
    assert len(notes) == 1

    # Raise the budget (simulating a new day / config bump) → re-armed.
    budget_holder["b"] = Budget(max_tokens=100_000)
    assert gw._day_budget_exceeded(context="cron 'x'") is False
    budget_holder["b"] = Budget(max_tokens=1000)
    assert gw._day_budget_exceeded(context="cron 'x'") is True
    assert len(notes) == 2  # notified again after re-arm


def test_gateway_unlimited_budget_never_gates(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    from personalclaw.guardrails.budgets import Budget, SpendMeter

    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(10_000_000, 0.0)
    monkeypatch.setattr("personalclaw.guardrails.budgets.get_meter", lambda: meter)
    monkeypatch.setattr("personalclaw.guardrails.budgets.budget_from_config", lambda: Budget())

    from personalclaw.gateway import GatewayOrchestrator

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw.dashboard_state = None
    gw._budget_notified = False
    assert gw._day_budget_exceeded(context="cron 'x'") is False


# ── the ambient run scope: attribution that never happened (S153) ──


def test_an_unbound_call_charges_only_the_day_scope():
    """The pre-S153 behaviour, preserved: a model call outside any tracked run must not invent a
    run scope to charge."""
    from personalclaw.guardrails.budgets import SpendMeter, current_run_key

    assert current_run_key() == ""
    meter = SpendMeter()
    meter.charge(100, 0.50, run_key=current_run_key() or None)
    assert meter.run_totals("anything").dollars == 0.0


def test_a_bound_run_scope_accrues_spend():
    """🔴 THE DEFECT. `SpendMeter.charge` has accepted `run_key=` since guardrails landed and its ONE
    production caller (`ModelCallGuard`) never passed one — so `run_totals` was permanently empty
    and every run-scoped cap read zero. That is why `cost_cap`/`max_cost_usd_per_run` sat in
    `UNMETERED_CAPS` for twenty sessions."""
    from personalclaw.guardrails.budgets import (
        SpendMeter,
        current_run_key,
        reset_current_run_key,
        set_current_run_key,
    )

    meter = SpendMeter()
    # The DELTA, not the absolute: the day scope is PERSISTED to the home, so it carries whatever
    # earlier tests in this process already charged. Asserting 0.50 absolute passed only by accident
    # of test order — measured, it read 8.0 here.
    before = meter.day_totals().dollars
    token = set_current_run_key("trigger:t1:999")
    try:
        meter.charge(100, 0.50, run_key=current_run_key() or None)
    finally:
        reset_current_run_key(token)
    assert meter.run_totals("trigger:t1:999").dollars == 0.50
    assert meter.day_totals().dollars - before == pytest.approx(
        0.50
    ), "the day scope is charged too, always"


def test_the_run_scope_VERDICT_now_binds():
    """The point of the attribution: `check_run` could always compute a verdict, against a total
    that was structurally always zero."""
    from personalclaw.guardrails.budgets import (
        Budget,
        BudgetVerdict,
        SpendMeter,
        current_run_key,
        reset_current_run_key,
        set_current_run_key,
    )

    meter = SpendMeter()
    token = set_current_run_key("doctor")
    try:
        meter.charge(1000, 2.75, run_key=current_run_key() or None)
    finally:
        reset_current_run_key(token)
    verdict, why = meter.check_run("doctor", Budget(max_dollars=1.0))
    assert verdict == BudgetVerdict.EXCEEDED
    assert "$2.75/$1" in why
    assert meter.check_run("doctor", Budget(max_dollars=10.0))[0] == BudgetVerdict.OK


def test_a_nested_scope_restores_its_parent():
    """A trigger fire that spawns a subagent must not lose the outer scope — the same token contract
    `mcp_core.set_current_session_key` uses."""
    from personalclaw.guardrails.budgets import (
        current_run_key,
        reset_current_run_key,
        set_current_run_key,
    )

    outer = set_current_run_key("outer")
    inner = set_current_run_key("inner")
    assert current_run_key() == "inner"
    reset_current_run_key(inner)
    assert current_run_key() == "outer", "the parent scope must come back"
    reset_current_run_key(outer)
    assert current_run_key() == ""


def test_reset_never_raises_on_a_stale_token():
    """A failed reset must not break a run's teardown — it clears rather than propagating."""
    from personalclaw.guardrails.budgets import (
        current_run_key,
        reset_current_run_key,
        set_current_run_key,
    )

    token = set_current_run_key("x")
    reset_current_run_key(token)
    reset_current_run_key(token)  # stale — must be a no-op, not a raise
    assert current_run_key() == ""


def test_the_guard_charges_the_ambient_scope():
    """The wiring itself: `ModelCallGuard` must read the ContextVar. Asserted on the source rather
    than by driving a provider, because the alternative is a full streaming fake — and the defect
    being guarded is precisely a missing ARGUMENT, which source inspection sees exactly."""
    import inspect

    from personalclaw.guardrails import model_call

    source = inspect.getsource(model_call)
    assert "run_key=current_run_key() or None" in source, (
        "the guard must pass the ambient run scope to charge(); without it run_totals is "
        "permanently empty and every run-scoped cap reads zero"
    )


def test_remediation_binds_the_doctor_scope_its_own_cap_reads():
    """🔴 A live reader of an unwritten key. `run_remediation`'s docstring always said it "charges
    the guardrails SpendMeter under run_key `doctor`", and its judgment-lane cap reads
    `run_totals("doctor").dollars >= max_cost_usd` — while nothing ever charged that key, so the cap
    never bound."""
    import inspect

    from personalclaw.resilience import remediation

    source = inspect.getsource(remediation)
    assert 'set_current_run_key("doctor")' in source
    assert "reset_current_run_key(token)" in source, "and it must not leak the scope"
