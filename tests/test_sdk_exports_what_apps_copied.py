"""What PersonalClawApps #124 had to copy out of core is published on the SDK, and core uses it too.

#124 mirrored four things because core kept them private or had none: the chat-title parser (#3590,
private to ``dashboard.chat_title``, pinned by a parity test to a core-private name); the per-call
sampling temperature and the output cap every model factory reads from its build kwargs (five
apps, plus three of core's own factories, each with its own copy of the same clauses); and the
environment a provider app needs to run one of its declared packages in a child process (piper-tts
re-derived core's from where ``importlib`` found the package). One definition each now, published,
with core's own callers on it.
"""

from __future__ import annotations

import pytest

from personalclaw.sdk import channel, util
from personalclaw.sdk.model import output_cap, per_call_temperature
from personalclaw.sdk.util import app_packages_env


def test_the_title_parser_is_published_and_is_the_dashboards_own():
    from personalclaw.dashboard import chat_title

    assert "parse_title" in channel.__all__
    assert chat_title.parse_title is channel.parse_title
    assert channel.parse_title("Title: Offsite Planning\nTAGS: Planned") == "Offsite Planning"
    assert channel.parse_title("```python") == ""


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"temperature": 0.7}, 0.7),
        ({"temperature": 1}, 1.0),
        ({"temperature": True}, None),  # a bool is an int, and not a temperature
        ({"temperature": "0.7"}, None),  # a build kwarg is core's, never a typed string
        ({}, None),
    ],
)
def test_per_call_temperature(kwargs, expected):
    assert per_call_temperature(kwargs) == expected


@pytest.mark.parametrize(
    ("configured", "per_call", "default", "expected"),
    [
        (2048, 512, 4096, 2048),  # the operator's cap wins
        (None, 512, 4096, 512),  # else the budget core derived for this call
        (0, 512, None, 512),  # 0 means unset
        (True, 512, None, 512),  # True is not a cap
        ("1024", None, None, 1024),  # the settings form stores what was typed
        ("lots", None, 4096, 4096),
        (None, None, None, None),
    ],
)
def test_output_cap(configured, per_call, default, expected):
    assert output_cap(configured, per_call, default) == expected


def test_the_branded_factory_sends_the_per_call_budget():
    """Core's own copy dropped the `max_tokens` build kwarg (#3595's per-call output budget), so a
    branded app answered one-shot calls with its spec's cap whatever core had sized them to."""
    from personalclaw.sdk.model import BrandedProviderSpec, Capability, ProviderEntry
    from personalclaw.sdk.provider_helpers import register_branded_app

    spec = BrandedProviderSpec(
        type="test_budgeted",
        protocol="openai",
        default_base_url="https://example.invalid",
        default_model="m",
        max_tokens=4096,
        capabilities=frozenset({Capability.CHAT}),
    )
    factory, _, _ = register_branded_app(spec)
    entry = ProviderEntry(name="B", type=spec.type, model="m", options={"api_key": "k"})
    assert factory(entry=entry)._max_tokens == 4096  # noqa: SLF001 — nothing asked: the default
    assert factory(entry=entry, max_tokens=321)._max_tokens == 321  # noqa: SLF001
    pinned = ProviderEntry(name="P", type=spec.type, model="m", options={"max_tokens": 900})
    assert factory(entry=pinned, max_tokens=321)._max_tokens == 900  # noqa: SLF001


def test_the_app_packages_environment_is_published_and_is_the_hooks_own():
    from personalclaw.apps import app_python

    assert "app_packages_env" in util.__all__
    assert app_packages_env is app_python.app_packages_env
