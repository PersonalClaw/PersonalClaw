"""A model nothing prices is unpriced to routing, never free.

The model-call log wrote each attempt's ``dollars_est``, and nothing said whether that figure was a
price. A call to a cloud model with no rate recorded 0.0, the routing fold averaged it in as what
the model's calls cost, and the Routing tab rendered that "free". On cost it then beat every priced
model and knocked them off the frontier, so the one tab that compares models by price named the
model with no price the cheapest.

Now each attempt row says whether its figure is a price (``AttemptRecord.priced``), the fold
averages what the calls a ref served cost when something priced them (``priced_n`` counts them), a
ref with none reads ``priced: false`` and has no cost on the frontier, and a proposal names a price
difference only between two priced models.
"""

from __future__ import annotations

import pytest

from personalclaw.guardrails.audit import read_recent
from personalclaw.guardrails.budgets import Budget, get_meter
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.routing import rates as rates_mod
from personalclaw.routing import stats
from personalclaw.routing.rates import save_overlay
from personalclaw.routing.telemetry import telemetry_rows

_MODEL = "acme-large"
_TOKENS = (1_000_000, 100_000)


@pytest.fixture(autouse=True)
def _clear_rate_caches():
    rates_mod._overlay_cache = None
    yield
    rates_mod._overlay_cache = None


def _entry(name: str, **options: object) -> None:
    """Register the provider entry *name*, as the config sync does; conftest drops it after."""
    get_default_registry().register_entry(
        ProviderEntry(name=name, type="openai_compatible", model="", options=dict(options))
    )


class _Adapter:
    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=_TOKENS[0], output_tokens=_TOKENS[1])


async def _call(provider: str, **guard: object) -> dict:
    """One call through the spend guard; the attempt row it wrote."""
    wrapped = wrap_model_call_guard(
        _Adapter(), use_case="background", provider_name=provider, model=_MODEL, **guard
    )
    try:
        async for event in wrapped.stream("summarize the release notes"):
            if event.kind == EVENT_COMPLETE:
                break
    except Exception:  # noqa: BLE001 - a refusal is the attempt under test
        pass
    return read_recent(1)[-1]


# ── the attempt row ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_call_nothing_prices_is_recorded_as_unpriced():
    _entry("acme-cloud", base_url="https://models.example.com/v1")

    row = await _call("acme-cloud")

    assert (row["passed"], row["dollars_est"]) == (True, 0.0)
    assert row.get("priced") is False, "a completed call nothing prices was recorded as a price"


@pytest.mark.asyncio
async def test_a_call_a_rate_prices_is_recorded_as_priced():
    _entry("acme-cloud", base_url="https://models.example.com/v1")
    save_overlay({_MODEL: {"in_per_mtok": 3.0, "out_per_mtok": 15.0}})

    row = await _call("acme-cloud")

    assert row["dollars_est"] == pytest.approx(4.5)
    assert row.get("priced") is True


@pytest.mark.asyncio
async def test_a_call_on_this_machine_is_a_priced_zero(ollama_app):
    get_default_registry().register_entry(
        ProviderEntry(
            name="here", type="ollama", model="", options={"endpoint": "http://127.0.0.1:11434"}
        )
    )

    row = await _call("here")

    assert (row["dollars_est"], row.get("priced")) == (0.0, True)


@pytest.mark.asyncio
async def test_an_openai_compatible_endpoint_on_this_machine_is_not_a_free_model():
    """A proxy on this machine can answer for a paid cloud API: a model nothing prices behind it
    is unpriced, not a known zero."""
    _entry("here", base_url="http://127.0.0.1:8080/v1")

    row = await _call("here")

    assert (row["dollars_est"], row.get("priced")) == (0.0, False)


@pytest.mark.asyncio
async def test_a_refusal_sent_nothing_and_is_a_known_zero():
    _entry("acme-cloud", base_url="https://models.example.com/v1")
    get_meter().charge(0, 5.0)

    row = await _call("acme-cloud", budget=Budget(max_dollars=1.0))

    assert (row["failure_mode"], row["passed"]) == ("budget_exceeded", False)
    assert (row["dollars_est"], row.get("priced")) == (0.0, True)


@pytest.mark.asyncio
async def test_an_agent_cli_turn_nothing_prices_is_recorded_as_unpriced():
    from personalclaw.acp.spend import AcpTurnMeter

    class _Cli(AcpTurnMeter):
        provider_id = "acp:acme-cli"
        agent_model = _MODEL

    cli = _Cli()
    cli.set_spend_axis("background")

    async def _events():
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=_TOKENS[0], output_tokens=_TOKENS[1])

    async for _ in cli._metered(_events()):
        pass

    assert read_recent(1)[-1].get("priced") is False


# ── the fold and the table it feeds ─────────────────────────────────────────────────────


def _attempt(ref: str, *, passed: bool = True, dollars: float = 0.0, priced: bool) -> dict:
    """An attempt row in the shape the guard writes it (``AttemptRecord.to_json_line``)."""
    provider, model = ref.split(":", 1)
    return {
        "use_case": "background",
        "query_class": "summarize",
        "provider": provider,
        "model": model,
        "passed": passed,
        "latency_ms": 800.0,
        "dollars_est": dollars,
        "priced": priced,
    }


def _table(*attempts: dict) -> dict[str, dict]:
    """The Routing tab's rows for the cell the attempts fold into, by ref."""
    fold: dict = {"use_cases": {}}
    for attempt in attempts:
        stats.fold_record(fold, attempt, now="t")
    rows = telemetry_rows(fold, list(attempts), "background", "summarize")
    return {row["ref"]: row for row in rows}


UNPRICED = "acme-cloud:acme-large"
PRICED = "some-cloud:gpt-4o"
LOCAL = "here:llama3.1"


def test_a_model_nothing_prices_reads_unpriced_not_free():
    rows = _table(_attempt(UNPRICED, priced=False))

    assert rows[UNPRICED].get("priced") is False


def test_a_model_nothing_prices_does_not_knock_a_priced_one_off_the_frontier():
    """Equal on quality and speed: at its $0 the unpriced model dominated the priced one on cost,
    so the table's one model with a price read as the one never to pick."""
    rows = _table(
        _attempt(UNPRICED, priced=False),
        _attempt(PRICED, dollars=0.02, priced=True),
    )

    assert rows[PRICED]["on_frontier"] is True


def test_a_model_on_this_machine_reads_as_a_priced_zero():
    rows = _table(_attempt(LOCAL, priced=True))

    assert (rows[LOCAL].get("priced"), rows[LOCAL]["avg_cost_usd"]) == (True, 0.0)


def test_a_refusal_or_a_failure_is_no_price_of_the_model():
    """A breaker's refusal cost nothing, and so did a failed call: neither is what the model's
    calls cost. Folded in at $0, a refusal made a model nothing prices read free, and failures
    made a priced one look cheaper than its calls are."""
    rows = _table(
        _attempt(UNPRICED, priced=False),
        _attempt(UNPRICED, passed=False, priced=True),
        _attempt(PRICED, dollars=0.02, priced=True),
        _attempt(PRICED, passed=False, priced=True),
    )

    assert rows[UNPRICED].get("priced") is False
    assert rows[PRICED]["avg_cost_usd"] == pytest.approx(0.02)


@pytest.mark.parametrize(("avg", "priced"), [(0.0, False), (0.004, True)])
def test_a_ref_folded_before_reads_by_what_it_carries(avg, priced):
    """A fold row written before the fold counted priced calls: a positive average was folded
    from priced calls, and a zero one cannot say it is a price."""
    fold = {
        "use_cases": {
            "background": {
                "summarize": {
                    UNPRICED: {"n": 7, "success_rate": 1.0, "avg_ms": 800.0, "avg_cost_usd": avg}
                }
            }
        }
    }

    [row] = telemetry_rows(fold, [], "background", "summarize")

    assert row.get("priced") is priced


def test_the_first_price_seeds_a_ref_folded_with_none():
    """A ref whose average held no price yet takes its first priced call's cost whole, not blended
    with the $0 that was never a price."""
    fold = {
        "use_cases": {
            "background": {
                "summarize": {
                    UNPRICED: {"n": 7, "success_rate": 1.0, "avg_ms": 800.0, "avg_cost_usd": 0.0}
                }
            }
        }
    }

    stats.fold_record(fold, _attempt(UNPRICED, dollars=0.03, priced=True), now="t")

    [row] = telemetry_rows(fold, [], "background", "summarize")
    assert (row.get("priced"), row["avg_cost_usd"]) == (True, pytest.approx(0.03))


# ── a proposal's evidence ────────────────────────────────────────────────────────────────


def test_a_proposal_names_no_price_difference_against_a_model_with_no_price(tmp_path, monkeypatch):
    from personalclaw.routing import gap

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    fold: dict = {"use_cases": {}}
    for _ in range(3):
        stats.fold_record(fold, _attempt(UNPRICED, priced=False), now="t")
        stats.fold_record(fold, _attempt(PRICED, dollars=0.02, priced=True), now="t")
    knobs = {"min_samples": 1, "hysteresis": 0.05, "cloud_quality_margin": 0.1}

    evidence = gap._evidence(
        fold,
        "background",
        "summarize",
        opinions={UNPRICED: 0.9, PRICED: 0.5},
        knobs=knobs,
        current=[PRICED, UNPRICED],
        proposed=[UNPRICED, PRICED],
    )

    assert "cost_delta_usd" not in evidence, "a model with no price read as $0.02 cheaper"
