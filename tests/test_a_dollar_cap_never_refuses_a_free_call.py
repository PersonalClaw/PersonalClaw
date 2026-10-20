"""A dollar cap limits the calls that cost money; a call that costs nothing is not refused by it.

Once the day's dollars were over the cap, every unattended call was refused whatever its price, and
every seam that starts unattended work refused it up front: a trigger's fire was paused, a
subagent's spawn refused, an app's worker stopped and a proposal left unexecuted. So every
automation, loop and housekeeping call on the free local models stopped until midnight.

A call to a model priced at a known $0 is now admitted past a spent dollar cap, a fallback chain
that holds one lands on it, and the seams that start work stop it only on the token cap, which
counts every call. A spent dollar cap still refuses every call that costs, or may cost, money.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any
from unittest.mock import patch

import pytest

from personalclaw.guardrails.budgets import Budget, SpendMeter, get_meter
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.routing import rates as rates_mod

#: A day already past its cap: $10.50 spent against a $4.00 cap.
_SPENT, _CAP = 10.50, 4.00


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


def _dollar_cap(home, dollars: float = _CAP, tokens: int = 0) -> None:
    """The ceilings as the owner sets them in Settings → Guardrails."""
    budgets: dict[str, Any] = {"max_dollars_per_day": dollars}
    if tokens:
        budgets["max_tokens_per_day"] = tokens
    (home / "config.json").write_text(json.dumps({"guardrails": {"budgets": budgets}}), "utf-8")


class _Model:
    """A model's API as the guard reaches it: records each request and reports a small usage."""

    supports_tools = False

    def __init__(self, name: str, sent: list[str]) -> None:
        self.name = name
        self.sent = sent
        self.served_ref = name

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.sent.append(self.name)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1_200, output_tokens=300)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


def _capability(type_: str, **declared: Any) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
        **declared,
    )


@pytest.fixture
def machine(monkeypatch) -> list[str]:
    """Two models behind the real resolution and guard: a cloud one priced by the shipped table
    (gpt-4o-mini) and one that runs inside this process, a known $0."""
    sent: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Model(entry.name, sent)

    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="gpt-4o-mini"))
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    return sent


def _guard(meter: SpendMeter, sent: list[str], entry: str, model: str):
    return wrap_model_call_guard(
        _Model(entry, sent),
        use_case="background",
        provider_name=entry,
        model=model,
        budget=Budget(max_dollars=_CAP),
        meter=meter,
    )


async def _text(provider) -> str:
    return "".join(
        [e.text async for e in provider.stream("Tidy the inbox.") if e.kind == EVENT_TEXT_CHUNK]
    )


def test_past_a_spent_dollar_cap_a_free_model_still_answers(tmp_path, machine):
    """🔴 Measured before the fix: the free call was refused, "day dollars budget exceeded"."""
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(172_000_000, _SPENT)

    assert asyncio.run(_text(_guard(meter, machine, "here", "tiny"))) == "done"
    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(_text(_guard(meter, machine, "relay", "gpt-4o-mini")))

    assert machine == ["here"], "the free call was sent; the priced one was refused before it"
    assert (refused.value.scope, refused.value.dimension) == ("day", "dollars")
    assert meter.day_totals().dollars == pytest.approx(_SPENT), "the free call cost nothing"


def test_a_token_cap_still_counts_the_free_calls(tmp_path, machine):
    """The control: a token cap counts every call, a free one too."""
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(2_000, 0.0)
    guard = wrap_model_call_guard(
        _Model("here", machine),
        use_case="background",
        provider_name="here",
        model="tiny",
        budget=Budget(max_tokens=1_000),
        meter=meter,
    )

    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(_text(guard))

    assert (refused.value.dimension, machine) == ("tokens", [])


def test_a_fallback_chain_lands_on_its_free_model_past_a_spent_dollar_cap(tmp_path, machine):
    """🔴 Measured before the fix: "every model in the 'background' fallback chain failed … day
    dollars budget exceeded", its free entries refused with the paid one. The model that served
    in the paid one's place says why, in the words of the refusal."""
    from personalclaw.llm_helpers import run_over_use_case_chain

    _dollar_cap(tmp_path)
    get_meter().charge(172_000_000, _SPENT)
    served: list[Any] = []

    async def _run(provider) -> str:
        served.append(provider)
        return await _text(provider)

    answer = asyncio.run(
        run_over_use_case_chain("background", ["relay:gpt-4o-mini", "here:tiny"], _run)
    )

    assert answer == "done"
    assert machine == ["here"]
    assert served[-1].substituted_for.sentence() == (
        "ran on here:tiny instead of relay:gpt-4o-mini: the daily dollar budget is spent ($10.50 "
        "of $4.00). Raise Max dollars / day in Settings → Guardrails (0 removes the cap), or wait "
        "for it to reset at midnight"
    )


# ── the seams that start unattended work ─────────────────────────────────────────────────


def _over_the_dollar_cap(monkeypatch, tmp_path) -> SpendMeter:
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(2_000, _SPENT)
    monkeypatch.setattr("personalclaw.guardrails.budgets.get_meter", lambda: meter)
    monkeypatch.setattr(
        "personalclaw.guardrails.budgets.budget_from_config", lambda: Budget(max_dollars=_CAP)
    )
    return meter


def test_a_trigger_fires_past_a_spent_dollar_cap_and_the_owner_is_told_once(tmp_path, monkeypatch):
    """🔴 Measured before the fix: every fire was paused, the free local ones too."""
    _over_the_dollar_cap(monkeypatch, tmp_path)
    notes: list[tuple] = []
    from personalclaw.gateway import GatewayOrchestrator

    gw = object.__new__(GatewayOrchestrator)
    gw.dashboard_state = types.SimpleNamespace(
        notify=lambda kind, title, body, **kw: notes.append((title, body))
    )
    gw._budget_notified = False

    assert gw._day_budget_exceeded(context="trigger clock:tidy") is False
    assert gw._day_budget_exceeded(context="trigger clock:tidy") is False
    assert len(notes) == 1, "one note for the day, not one per fire"
    title, body = notes[0]
    assert title == "Daily dollar budget reached"
    assert body == (
        "Calls to models that cost money are refused until it resets tomorrow ($10.50 of "
        "$4.00 spent); models that cost nothing keep running. Raise it in Settings → Guardrails."
    )


def test_a_spent_token_cap_still_pauses_a_fire(tmp_path, monkeypatch):
    """The control: the token cap counts every call, so work that starts past it is paused."""
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(2_000, 0.0)
    monkeypatch.setattr("personalclaw.guardrails.budgets.get_meter", lambda: meter)
    monkeypatch.setattr(
        "personalclaw.guardrails.budgets.budget_from_config", lambda: Budget(max_tokens=1_000)
    )
    from personalclaw.gateway import GatewayOrchestrator

    gw = object.__new__(GatewayOrchestrator)
    gw.dashboard_state = None
    gw._budget_notified = False

    assert gw._day_budget_exceeded(context="trigger clock:tidy") is True


def test_a_subagent_is_spawned_past_a_spent_dollar_cap(tmp_path, monkeypatch):
    """🔴 Measured before the fix: "spawn refused: day dollar budget exceeded", before its model was
    known. Its paid calls are refused where they are made; a free one runs."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    _over_the_dollar_cap(monkeypatch, tmp_path)
    sessions = _mock_sessions()
    manager = SubagentManager(
        sessions=sessions, ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )

    async def _spawn():
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn("Tidy the inbox.", parent_session_key="dashboard:p")
            task = manager._tasks.get(info.id)
            assert task is not None, f"the spawn was refused up front: {info.error}"
            await task
        return info

    info = asyncio.run(_spawn())

    assert "budget" not in (info.error or ""), info.error
    assert sessions.get_or_create.await_count == 1


def test_an_apps_worker_keeps_running_past_a_spent_dollar_cap(tmp_path, monkeypatch):
    """🔴 Measured before the fix: the worker was paused, "day dollar budget exceeded"."""
    from personalclaw.apps.worker_runtime import _budget_pause_reason

    _over_the_dollar_cap(monkeypatch, tmp_path)

    assert _budget_pause_reason() == ""


def test_a_proposal_still_executes_past_a_spent_dollar_cap(tmp_path, monkeypatch):
    """🔴 Measured before the fix: the proposal stayed pending on the dollar cap."""
    from personalclaw.proactive.autoexec import default_budget_check

    _over_the_dollar_cap(monkeypatch, tmp_path)

    assert default_budget_check()() == (False, "")


def test_a_browse_step_is_not_stopped_up_front_by_a_spent_dollar_cap(tmp_path, monkeypatch):
    """🔴 Measured before the fix: "exceeded". Each of its model calls is still weighed by the
    guard, which refuses the ones that cost money."""
    from personalclaw.action_providers.browse_provider import _budget_check

    _over_the_dollar_cap(monkeypatch, tmp_path)

    assert _budget_check() == ("ok", "")
