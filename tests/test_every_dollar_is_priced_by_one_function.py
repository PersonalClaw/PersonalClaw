"""Every dollar PersonalClaw shows or holds to a cap is priced by one function.

The spend guard's charge against the daily and per-run caps, an agent CLI's metered turn, the
usage row the Usage page sums, a chat turn's cost and a subagent's: each priced from the shipped
table alone (``pricing.estimate_cost``), while the rate table (``routing.rates``) knew three
things that table cannot: a rate the owner set (Settings → Usage → Model prices), a local
model's known $0, and a rate the serving app declared. So a rate the owner set reached the router
and nothing else. And a model the table has no row for counted as $0 against a dollar cap, with
nothing saying so.

Now each prices through ``routing.rates.price_call``, and a call nothing prices is charged as one
the dollar caps could not count: the meter keeps a count of them, and the refusal, the verdict and
the Usage page each say how many calls their dollar figure leaves out. The chat and subagent halves
are driven through the real runtime in
``tests/test_usage_and_rooms_follow_the_model_that_answered.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import personalclaw
from personalclaw.guardrails.budgets import Budget, BudgetVerdict, CallCost, get_meter
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import save_overlay

SRC = Path(personalclaw.__file__).parent

#: A model the shipped table has no row for, priced here only by the rate the owner sets.
_MODEL = "acme-large"
_OWNER_RATE = {"in_per_mtok": 3.0, "out_per_mtok": 15.0}
#: One million tokens in and a hundred thousand out, at the owner's rate: $3.00 + $1.50.
_TOKENS = (1_000_000, 100_000)
_OWNER_COST = 4.5
#: The next call a spent ceiling weighs: one to a priced model (gpt-4o, $2.50/$10 per 1M).
_NEXT_CALL = CallCost(ref="some-cloud:gpt-4o", prompt_tokens=1, rate=(2.5, 10.0))


@pytest.fixture(autouse=True)
def _clear_rate_caches():
    """The overlay memo is process-global (stat-keyed); reset it around each test."""
    rates_mod._overlay_cache = None
    yield
    rates_mod._overlay_cache = None


def _owner_sets_a_rate() -> None:
    """The owner prices the model (``config.json`` → ``model_prices``) under the (test-isolated)
    home."""
    save_overlay({_MODEL: dict(_OWNER_RATE)})


class _Adapter:
    """A provider whose one call reports tokens and no cost, as most do."""

    def __init__(self, usage: tuple[int, int] = _TOKENS) -> None:
        self.usage = usage

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=self.usage[0], output_tokens=self.usage[1])


async def _guarded_call(*, provider: str = "acme", model: str = _MODEL) -> None:
    guard = wrap_model_call_guard(
        _Adapter(), use_case="background", provider_name=provider, model=model
    )
    async for event in guard.stream("hi"):
        if event.kind == EVENT_COMPLETE:
            break


# ── the one function ─────────────────────────────────────────────────────────────────────


def _imported_modules(path: Path, *, root: Path = SRC.parent) -> set[str]:
    """Every module *path* imports, relative imports resolved against its own package (*path*
    sits under *root* as the package's own dotted path)."""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    package = parts if parts[-1] == "__init__" else parts[:-1]
    if package and package[-1] == "__init__":
        package = package[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def _readers_of_the_shipped_table() -> set[str]:
    return {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "personalclaw.pricing" in _imported_modules(path) and path.name != "pricing.py"
    }


def test_only_the_rate_table_reads_the_shipped_price_table():
    """A module that priced from ``personalclaw.pricing`` priced by the table alone, which never
    sees a rate the owner set, a local model's zero or an app's declared rate."""
    assert _readers_of_the_shipped_table() == {"routing/rates.py"}


@pytest.mark.parametrize(
    "spelling",
    [
        "import personalclaw.pricing",
        "from personalclaw.pricing import estimate_cost",
        "from personalclaw import pricing",
        "from .. import pricing",
        "from ..pricing import has_pricing",
    ],
)
def test_the_census_sees_every_spelling_of_the_import(tmp_path, spelling):
    """Positive control: each way a module in the package can import the table is found, by the
    same resolution the census runs over the real tree (a probe module in a copy of its layout)."""
    probe = tmp_path / "personalclaw" / "routing" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(spelling + "\n", encoding="utf-8")

    assert "personalclaw.pricing" in _imported_modules(probe, root=tmp_path)
    probe.write_text("from personalclaw.routing import rates\n", encoding="utf-8")
    assert "personalclaw.pricing" not in _imported_modules(probe, root=tmp_path)


# ── the spend guard: the caps a call is held to ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_guard_charges_the_caps_at_the_rate_the_owner_set():
    _owner_sets_a_rate()

    await _guarded_call()

    day = get_meter().day_totals()
    assert day.dollars == pytest.approx(_OWNER_COST)
    assert day.unpriced == 0


@pytest.mark.asyncio
async def test_a_call_nothing_prices_is_charged_as_one_the_cap_could_not_count():
    """No rate the owner set, no local endpoint, no app declaration and no row: the call's
    dollars are unknown. Its tokens count as any call's do; it is counted as unpriced, not free."""
    await _guarded_call()

    day = get_meter().day_totals()
    assert (day.tokens, day.dollars, day.unpriced) == (sum(_TOKENS), 0.0, 1)


@pytest.mark.asyncio
async def test_a_model_on_this_machine_is_charged_its_known_zero(ollama_app):
    get_default_registry().register_entry(
        ProviderEntry(
            name="here", type="ollama", model="", options={"endpoint": "http://localhost:11434"}
        )
    )

    await _guarded_call(provider="here")

    day = get_meter().day_totals()
    assert (day.dollars, day.unpriced) == (0.0, 0)


@pytest.mark.asyncio
async def test_a_cap_that_holds_an_unpriced_call_says_its_total_leaves_it_out():
    """The defect, through the guard alone: a call the shipped table has no row for charged $0,
    and a cap spent by the calls it could price stated that total as all there was."""
    # gpt-4o, priced by the shipped table: $2.50 in + $1.00 out.
    await _guarded_call(provider="some-cloud", model="gpt-4o")
    await _guarded_call(provider="some-cloud", model=_MODEL)

    refusal = get_meter().admit(_NEXT_CALL, day=Budget(max_dollars=1.0))

    assert isinstance(refusal, BudgetExceededError)
    assert refusal.sentence() == (
        "The daily dollar budget is spent ($3.50 of $1.00, not counting 1 call that had no "
        "price): it resets tomorrow, or raise it in Settings → Guardrails."
    )


def test_a_spent_cap_says_how_many_calls_its_total_leaves_out():
    meter = get_meter()
    meter.charge(1_000, 5.0)
    meter.charge(500, 0.0, unpriced=1)

    refusal = meter.admit(_NEXT_CALL, day=Budget(max_dollars=5.0))

    assert isinstance(refusal, BudgetExceededError)
    assert refusal.sentence() == (
        "The daily dollar budget is spent ($5.00 of $5.00, not counting 1 call that had no "
        "price): it resets tomorrow, or raise it in Settings → Guardrails."
    )
    verdict, reason = meter.check_day(Budget(max_dollars=5.0))
    assert verdict is BudgetVerdict.EXCEEDED
    assert reason.endswith("not counting 1 call that had no price)")


def test_a_token_cap_counts_every_call_and_says_nothing_of_prices():
    meter = get_meter()
    meter.charge(1_000, 0.0, unpriced=1)

    refusal = meter.admit(_NEXT_CALL, day=Budget(max_tokens=1_000))

    assert isinstance(refusal, BudgetExceededError)
    assert "price" not in refusal.sentence()


def test_a_run_s_cap_counts_its_unpriced_calls_too():
    """The run scope keeps the count as the day's does, and its warning near the ceiling says what
    the figure leaves out."""
    meter = get_meter()
    meter.charge(10, 0.9, run_key="run-1")
    meter.charge(10, 0.0, run_key="run-1", unpriced=1)
    meter.charge(10, 0.0, run_key="run-1", unpriced=1)

    assert meter.run_totals("run-1").unpriced == 2
    verdict, reason = meter.check_run("run-1", Budget(max_dollars=1.0))
    assert verdict is BudgetVerdict.WARN
    assert reason == "run dollar budget at $0.9/$1, not counting 2 calls that had no price"


# ── an agent CLI's metered turn ──────────────────────────────────────────────────────────


class _Cli:
    """The two names an ACP provider identifies its turns by, and the meter mixed in."""

    def __init__(self) -> None:
        from personalclaw.acp.spend import AcpTurnMeter

        class _Provider(AcpTurnMeter):
            provider_id = "acp:acme-cli"
            agent_model = _MODEL

        self.provider = _Provider()
        self.provider.set_spend_axis("background")

    async def turn(self) -> None:
        async def _events():
            yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=_TOKENS[0], output_tokens=_TOKENS[1])

        async for _ in self.provider._metered(_events()):
            pass


@pytest.mark.asyncio
async def test_an_agent_cli_turn_is_charged_at_the_rate_the_owner_set():
    _owner_sets_a_rate()

    await _Cli().turn()

    day = get_meter().day_totals()
    assert day.dollars == pytest.approx(_OWNER_COST)
    assert day.unpriced == 0


@pytest.mark.asyncio
async def test_an_agent_cli_turn_nothing_prices_is_counted_unpriced():
    await _Cli().turn()

    day = get_meter().day_totals()
    assert (day.dollars, day.unpriced) == (0.0, 1)


# ── the usage row the Usage page sums ────────────────────────────────────────────────────


def _row_for(provider: str) -> dict:
    from personalclaw import usage_ledger

    usage_ledger.record_from_event(
        LLMEvent(kind=EVENT_COMPLETE, input_tokens=_TOKENS[0], output_tokens=_TOKENS[1]),
        source="cron",
        session_key="cron:priced",
        provider=provider,
        model=_MODEL,
    )
    return usage_ledger._iter_rows()[-1]


def test_the_usage_row_is_priced_at_the_rate_the_owner_set():
    _owner_sets_a_rate()

    row = _row_for("acme")

    assert (row["cost_usd"], row["priced"]) == (pytest.approx(_OWNER_COST), True)


def test_a_usage_row_nothing_prices_reads_unpriced():
    row = _row_for("acme")

    assert (row["cost_usd"], row["priced"]) == (0.0, False)


def test_a_usage_row_for_a_model_on_this_machine_is_a_priced_zero(ollama_app):
    get_default_registry().register_entry(
        ProviderEntry(
            name="here", type="ollama", model="", options={"endpoint": "http://127.0.0.1:11434"}
        )
    )

    row = _row_for("here")

    assert (row["cost_usd"], row["priced"]) == (0.0, True)
