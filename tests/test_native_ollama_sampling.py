"""The bundled Ollama app puts a per-call sampling temperature where ollama reads it.

Measured in the day-5/6 validation: a proxy in front of a live ollama saw every best-of-n
candidate request carry only `default_model`, `messages`, `model` and `stream`. The factory read
`model`, `embedding_model` and the structured-output keys from its build kwargs and dropped the
rest, so the `temperature` best-of-N threads per candidate never reached the request — and
`default_model`, a routing label the "Add instance" form writes, rode onto the wire instead.

The request body itself is asserted end to end in `tests/test_best_of_n_provider_outage.py`; this
file pins the factory contract that body is built from.
"""

from __future__ import annotations

import sys

import pytest

from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
)
from personalclaw.llm.registry import ProviderEntry

APP_NAME = "ollama-models"


@pytest.fixture()
def module():
    name = namespaced_module_name(APP_NAME, "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / APP_NAME, APP_NAME, "provider")
    finally:
        sys.modules.pop(name, None)


def _entry(**options) -> ProviderEntry:
    return ProviderEntry(
        name="flaky-ollama",
        type="ollama",
        model="gemma3:4b",
        options={"endpoint": "http://127.0.0.1:9", "default_model": "gemma3:4b", **options},
    )


def test_a_per_call_temperature_lands_in_the_request_options_object(module):
    provider = module._factory(entry=_entry(), temperature=0.7)
    assert provider._extra_options == {"options": {"temperature": 0.7}}
    assert provider.sampling_temperature == 0.7


def test_it_wins_over_the_entry_default_and_keeps_the_other_sampling_options(module):
    provider = module._factory(
        entry=_entry(options={"num_ctx": 8192, "temperature": 0.1}), temperature=0.9
    )
    assert provider._extra_options["options"] == {"num_ctx": 8192, "temperature": 0.9}


def test_no_per_call_temperature_sends_none_and_adds_nothing(module):
    provider = module._factory(entry=_entry())
    assert provider.sampling_temperature is None
    # Nothing is injected, and the routing label is gone: every key left here goes on the wire.
    assert provider._extra_options == {}


def test_a_bool_is_not_a_temperature(module):
    assert module._factory(entry=_entry(), temperature=True).sampling_temperature is None


def test_the_output_budget_lands_in_num_predict(module):
    """The ``max_tokens`` build kwarg (#3595's per-call budget) was dropped here, so a one-shot
    call generated until the model stopped. ollama reads a cap from ``options.num_predict``."""
    provider = module._factory(entry=_entry(), max_tokens=256, temperature=0.7)
    assert provider._extra_options == {"options": {"temperature": 0.7, "num_predict": 256}}


def test_an_entry_that_sets_num_predict_keeps_it(module):
    provider = module._factory(entry=_entry(options={"num_predict": -1}), max_tokens=256)
    assert provider._extra_options == {"options": {"num_predict": -1}}


def test_a_top_level_max_tokens_option_is_the_entrys_cap(module):
    provider = module._factory(entry=_entry(max_tokens=512), max_tokens=256)
    assert provider._extra_options == {"options": {"num_predict": 512}}
