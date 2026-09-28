"""Effective-rate resolution — ``routing/rates.py``.

The precedence (overlay > local > app default > builtin > absent) is the whole contract, so each
tier is asserted at its boundary AND the absent case is asserted to be ``None`` — a fabricated
0.0 would report an unpriced cloud model as free, the one wrong answer a spend meter must never
give. Every test passes an explicit ``home`` (or monkeypatches ``config_dir``); nothing here may
touch the real ``~/.personalclaw``.

The local tier is decided by where a provider entry's endpoint is. It was decided by the entry's
NAME (anything spelled like a local engine, ``ollama`` among them), so an Ollama on another
machine priced as a free local model: its turns read "ran locally at $0" and the router ranked it
cheapest. The entries here are registered with the endpoint they are configured with, the way
``config.json`` gives them to the gateway.
"""

from __future__ import annotations

import json

import pytest

import personalclaw.sdk.model  # noqa: F401 — ensure package import order (sdk.model first)
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import (
    ModelRate,
    cost_for,
    load_overlay,
    rate_for,
    save_overlay,
    served_on_this_machine,
)
from personalclaw.sdk.provider_helpers import BrandedProviderSpec

#: A model no tier prices, so what answers for it is the local tier or nothing.
_UNPRICED = "qwen3:8b"

#: Where a model server on another machine is (the documentation name, address and prefix).
_ELSEWHERE = [
    "http://gpu.example.test:11434",
    "http://192.0.2.10:11434",
    "http://[2001:db8::1]:11434",
    # A LAN address is another machine too, even one the owner runs.
    "http://10.0.0.5:11434",
]

#: This machine, as an endpoint can name it.
_HERE = [
    "http://localhost:11434",
    "http://127.0.0.1:11434",
    "http://[::1]:11434",
    "http://0.0.0.0:8000",
]


def _configured(name: str, provider_type: str = "ollama", **options: object) -> str:
    """Register the provider entry *name* with *options*, as the config sync does; conftest drops
    it after the test."""
    get_default_registry().register_entry(
        ProviderEntry(name=name, type=provider_type, model="", options=dict(options))
    )
    return name


@pytest.fixture(autouse=True)
def _clear_rate_caches():
    """The overlay memo is process-global (stat-keyed); reset it so one test's tmp home can't
    answer another's lookup."""
    rates_mod._overlay_cache = None
    yield
    rates_mod._overlay_cache = None


@pytest.fixture
def registered_pricing(monkeypatch):
    """Register an app-declared pricing map for a provider type, isolated per test."""

    def _register(provider_type: str, pricing: dict[str, dict[str, float]]) -> None:
        from personalclaw.llm import branded_specs

        specs = dict(branded_specs._REGISTERED_SPECS)
        specs[provider_type] = BrandedProviderSpec(type=provider_type, pricing=pricing)
        monkeypatch.setattr(branded_specs, "_REGISTERED_SPECS", specs)

    return _register


# ── The precedence, tier by tier ─────────────────────────────────────────────────────────


def test_overlay_wins_over_app_default(tmp_path, registered_pricing):
    """Tier 1 beats tier 3: the user's correction is mightier than the app's shipped price."""
    registered_pricing("acme", {"acme-large": {"in_per_mtok": 3.0, "out_per_mtok": 15.0}})
    save_overlay({"acme:acme-large": {"in_per_mtok": 1.0, "out_per_mtok": 2.0}}, home=tmp_path)

    rate = rate_for("acme", "acme-large", home=tmp_path)

    assert rate == ModelRate(1.0, 2.0)
    assert rate is not None and rate.source == "overlay"


def test_app_default_answers_when_no_overlay(tmp_path, registered_pricing):
    registered_pricing("acme", {"acme-large": {"in_per_mtok": 3.0, "out_per_mtok": 15.0}})

    rate = rate_for("acme", "acme-large", home=tmp_path)

    assert rate == ModelRate(3.0, 15.0)
    assert rate is not None and rate.source == "app_default"


def test_absent_is_none_not_a_free_model(tmp_path):
    """Tier 5. Nothing prices this cloud model → ``None`` ("unknown"), never 0.0 ("free")."""
    rate = rate_for("acme", "totally-unpriced-model", home=tmp_path)

    assert rate is None
    assert cost_for("acme", "totally-unpriced-model", input_tokens=10_000, home=tmp_path) is None


@pytest.mark.parametrize("endpoint", _HERE)
def test_a_model_server_on_this_machine_prices_zero_and_is_not_absent(tmp_path, endpoint):
    """A local provider's price is a KNOWN 0.0, distinguishable from an absent rate."""
    _configured("ollama", endpoint=endpoint)

    rate = rate_for("ollama", _UNPRICED, home=tmp_path)

    assert rate == ModelRate(0.0, 0.0)
    assert rate is not None and rate.source == "local"
    assert cost_for("ollama", _UNPRICED, input_tokens=1_000_000, home=tmp_path) == 0.0


@pytest.mark.parametrize("endpoint", _ELSEWHERE)
def test_a_model_server_on_another_machine_is_not_free(tmp_path, endpoint):
    """The defect: an Ollama entry named ``ollama``, on another machine, priced 0.0 as local.
    With nothing that prices its model it is unpriced, never free."""
    _configured("ollama", endpoint=endpoint)

    assert served_on_this_machine("ollama") is False
    assert rate_for("ollama", _UNPRICED, home=tmp_path) is None
    assert cost_for("ollama", _UNPRICED, input_tokens=1_000_000, home=tmp_path) is None


def test_a_model_server_on_another_machine_prices_at_its_configured_rate(tmp_path):
    _configured("gpu-box", endpoint=_ELSEWHERE[0])
    save_overlay({"gpu-box:qwen3:8b": {"in_per_mtok": 0.2, "out_per_mtok": 0.4}}, home=tmp_path)

    rate = rate_for("gpu-box", _UNPRICED, home=tmp_path)

    assert rate == ModelRate(0.2, 0.4)
    assert rate is not None and rate.source == "overlay"


def test_a_model_server_on_another_machine_prices_at_its_declared_rate(
    tmp_path, registered_pricing
):
    registered_pricing("acme", {"acme-large": {"in_per_mtok": 3.0, "out_per_mtok": 15.0}})
    _configured("acme-lab", "acme", base_url="https://models.example.com/v1")

    rate = rate_for("acme-lab", "acme-large", home=tmp_path)

    assert rate == ModelRate(3.0, 15.0)
    assert rate is not None and rate.source == "app_default"


def test_a_name_spelled_like_a_local_engine_is_not_local_by_itself(tmp_path):
    """What a provider is called never makes it free: a name no configured entry has names no
    endpoint, and so no machine."""
    for name in ("ollama", "ollama-models", "lm-studio", "vllm", "llama.cpp"):
        assert served_on_this_machine(name) is False, name
        assert rate_for(name, _UNPRICED, home=tmp_path) is None, name


def test_an_entry_that_names_no_endpoint_is_not_local(tmp_path):
    _configured("in-process", "some-runtime")

    assert served_on_this_machine("in-process") is False
    assert rate_for("in-process", _UNPRICED, home=tmp_path) is None


@pytest.mark.parametrize("remote_key", ["endpoint", "base_url"])
def test_an_entry_is_local_only_when_every_endpoint_it_names_is(tmp_path, remote_key):
    """Clients differ on which spelling wins when an entry carries both, so one endpoint on
    another machine is enough for its requests to leave this one."""
    local_key = "base_url" if remote_key == "endpoint" else "endpoint"
    _configured("mixed", **{local_key: _HERE[0], remote_key: _ELSEWHERE[0]})

    assert served_on_this_machine("mixed") is False
    assert rate_for("mixed", _UNPRICED, home=tmp_path) is None


def test_overlay_wins_over_local_zero(tmp_path):
    """The overlay is tier 1 — above the local rule, so a user can price local compute if they
    want to. Precedence is total, with no tier exempt from the one above it."""
    _configured("ollama", endpoint=_HERE[0])
    assert rate_for("ollama", _UNPRICED, home=tmp_path) == ModelRate(0.0, 0.0), "the local tier"
    save_overlay({"ollama:qwen3:8b": {"in_per_mtok": 0.5, "out_per_mtok": 0.5}}, home=tmp_path)

    rate = rate_for("ollama", _UNPRICED, home=tmp_path)

    assert rate == ModelRate(0.5, 0.5)
    assert rate is not None and rate.source == "overlay"


def test_builtin_table_is_the_last_tier_before_absent(tmp_path):
    """Tier 4: core's shipped ``model_pricing.json``, read through the public pricing API."""
    from personalclaw import pricing

    priced = next((m for m in pricing._PRICES if not m.startswith("_")), None)
    assert priced, "model_pricing.json has no rows — the builtin tier can't be exercised"

    rate = rate_for("some-cloud", priced, home=tmp_path)

    assert rate is not None and rate.source == "builtin"
    expected_in = pricing.estimate_cost(priced, input_tokens=1_000_000)
    assert rate.in_per_mtok == expected_in


def test_app_default_beats_builtin(tmp_path, registered_pricing):
    """An installed app's own declaration outranks core's shipped table for the same model."""
    from personalclaw import pricing

    priced = next(m for m in pricing._PRICES if not m.startswith("_"))
    registered_pricing("acme", {priced: {"in_per_mtok": 999.0, "out_per_mtok": 999.0}})

    rate = rate_for("acme", priced, home=tmp_path)

    assert rate == ModelRate(999.0, 999.0)
    assert rate is not None and rate.source == "app_default"


# ── Key matching ─────────────────────────────────────────────────────────────────────────


def test_overlay_glob_and_longest_pattern_wins(tmp_path):
    save_overlay(
        {
            "acme:*": {"in_per_mtok": 1.0, "out_per_mtok": 1.0},
            "acme:acme-large-*": {"in_per_mtok": 7.0, "out_per_mtok": 8.0},
        },
        home=tmp_path,
    )

    specific = rate_for("acme", "acme-large-2026", home=tmp_path)
    catch_all = rate_for("acme", "acme-tiny", home=tmp_path)

    assert specific == ModelRate(7.0, 8.0)
    assert catch_all == ModelRate(1.0, 1.0)


def test_overlay_bare_model_key_is_provider_agnostic(tmp_path):
    """A bare model spelling matches whatever provider serves it — the exact ref is tried first."""
    save_overlay({"acme-large": {"in_per_mtok": 4.0, "out_per_mtok": 5.0}}, home=tmp_path)

    assert rate_for("acme", "acme-large", home=tmp_path) == ModelRate(4.0, 5.0)
    assert rate_for("other", "acme-large", home=tmp_path) == ModelRate(4.0, 5.0)


def test_colon_bearing_model_ref_round_trips(tmp_path):
    """``provider:gpt-oss:20b`` — the ref joins on the FIRST colon (routing.stats.ref_of)."""
    save_overlay({"vendor:gpt-oss:20b": {"in_per_mtok": 2.0, "out_per_mtok": 3.0}}, home=tmp_path)

    assert rate_for("vendor", "gpt-oss:20b", home=tmp_path) == ModelRate(2.0, 3.0)


def test_empty_model_is_absent(tmp_path):
    assert rate_for("acme", "", home=tmp_path) is None


# ── The overlay store: editable live, fail-open when broken ──────────────────────────────


def test_editing_the_overlay_changes_the_answer_with_no_restart(tmp_path):
    """No restart-order dependency: the overlay is stat-keyed per call, never snapshotted at
    import, so a live edit lands on the very next lookup."""
    save_overlay({"acme:acme-large": {"in_per_mtok": 1.0, "out_per_mtok": 1.0}}, home=tmp_path)
    assert rate_for("acme", "acme-large", home=tmp_path) == ModelRate(1.0, 1.0)

    save_overlay({"acme:acme-large": {"in_per_mtok": 9.0, "out_per_mtok": 9.0}}, home=tmp_path)

    assert rate_for("acme", "acme-large", home=tmp_path) == ModelRate(9.0, 9.0)


def test_overlay_written_atomically_and_round_trips(tmp_path):
    path = save_overlay(
        {"acme:acme-large": {"in_per_mtok": 1.5, "out_per_mtok": 2.5}}, home=tmp_path
    )

    assert path == tmp_path / "model_rates.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == rates_mod.RATES_VERSION
    assert load_overlay(tmp_path)["rates"] == on_disk["rates"]
    assert not list(tmp_path.glob("*.tmp*")), "atomic_write left a temp file behind"


def test_corrupt_overlay_fails_open_to_the_app_default(tmp_path, registered_pricing, caplog):
    """A broken overlay must degrade to the next tier (log + continue), never crash a routing
    decision. This is the fail-open half of the precedence contract."""
    registered_pricing("acme", {"acme-large": {"in_per_mtok": 3.0, "out_per_mtok": 15.0}})
    (tmp_path / "model_rates.json").write_text("{not json at all", encoding="utf-8")

    rate = rate_for("acme", "acme-large", home=tmp_path)

    assert rate == ModelRate(3.0, 15.0)
    assert rate is not None and rate.source == "app_default"
    assert any("model_rates.json" in r.message for r in caplog.records)


def test_overlay_missing_rates_object_fails_open(tmp_path, registered_pricing):
    registered_pricing("acme", {"acme-large": {"in_per_mtok": 3.0, "out_per_mtok": 15.0}})
    (tmp_path / "model_rates.json").write_text('{"version": 1}', encoding="utf-8")

    assert rate_for("acme", "acme-large", home=tmp_path) == ModelRate(3.0, 15.0)


def test_malformed_rate_row_is_not_a_free_model(tmp_path):
    """A typo'd row (no in/out keys) is NOT a rate — it must not resolve to 0.0."""
    save_overlay({"acme:acme-large": {"input": 3.0}}, home=tmp_path)

    assert rate_for("acme", "acme-large", home=tmp_path) is None


def test_missing_overlay_reads_as_empty(tmp_path):
    assert load_overlay(tmp_path) == {"version": rates_mod.RATES_VERSION, "rates": {}}


def test_default_home_comes_from_config_dir(tmp_path, monkeypatch):
    """With no explicit home the overlay is read from ``config_dir()`` — asserted against a
    monkeypatched dir so the real home is never touched."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    save_overlay({"acme:acme-large": {"in_per_mtok": 6.0, "out_per_mtok": 7.0}}, home=tmp_path)

    assert rate_for("acme", "acme-large") == ModelRate(6.0, 7.0)


# ── cost_for ─────────────────────────────────────────────────────────────────────────────


def test_cost_for_applies_the_effective_rate(tmp_path):
    save_overlay({"acme:acme-large": {"in_per_mtok": 3.0, "out_per_mtok": 15.0}}, home=tmp_path)

    cost = cost_for("acme", "acme-large", input_tokens=1_000, output_tokens=2_000, home=tmp_path)

    assert cost == pytest.approx(0.003 + 0.030)


def test_model_rate_source_is_not_part_of_equality():
    assert ModelRate(1.0, 2.0, source="overlay") == ModelRate(1.0, 2.0, source="builtin")
