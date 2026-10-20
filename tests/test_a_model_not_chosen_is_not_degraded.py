"""A surface on its floor because no model is chosen says so; one whose chosen model cannot serve
is degraded.

The shell chip read "12 degraded" on a home with a provider connected and no model chosen for it:
an instance saved from the Add-instance form without a Default Model, nothing bound in Settings →
Models (#3718 left this). "Degraded" claims a decline, and nothing declined — a model was simply
never chosen. The degraded report now says, per surface, whether a model is chosen for the use
cases it needs (``model_chosen``), so the chip can word that state as a choice to make.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

ENTRY = "local"


@pytest.fixture
def instance(monkeypatch):
    """Register one chat-capable instance the way the Add-instance form saves it: ``model: ""``.

    Returns a function that (re)registers it with the given options; ``readiness`` makes its type
    decline to serve, the way a bundled model that is not downloaded yet does.
    """
    from personalclaw.config.loader import config_path
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry
    from personalclaw.resilience import degraded

    degraded.reset_transition_state()

    def _register(options: dict[str, Any] | None = None, *, serves: bool = True) -> None:
        registry = ProviderRegistry()
        registry.register_type(
            ProviderCapability(
                type="rec",
                capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
                supports_streaming=True,
                supports_tools=False,
                supports_embeddings=False,
                supports_vision=False,
                max_context_tokens=8192,
            ),
            lambda **_kw: object(),
            readiness=None if serves else (lambda _entry, *, implicit: ("gone", "fix it")),
        )
        registry.register_entry(
            ProviderEntry(name=ENTRY, type="rec", model="", options=dict(options or {}))
        )
        set_default_registry(registry)  # conftest restores the singleton afterwards
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"providers": [{"name": ENTRY, "type": "rec", "model": ""}]}))

    return _register


def _rows() -> dict[str, dict]:
    from personalclaw.resilience import degraded

    return {row["surface"]: row for row in degraded.evaluate()}


def test_with_no_model_chosen_every_surface_down_says_it_is_not_chosen(instance):
    """🔴 Red on main: the row carried no such field, so the chip could only say "degraded"."""
    instance()

    rows = _rows()

    down = [row for row in rows.values() if not row["available"]]
    assert len(down) == len(rows), "nothing resolves while no model is chosen"
    assert {row["surface"]: row["model_chosen"] for row in down} == {s: False for s in rows}


def test_the_instances_default_model_is_a_choice(instance):
    """With a Default Model the chat surfaces run; the rest still wait on a model for their own
    use case, which no instance here serves."""
    instance({"default_model": "alpha:1b"})

    rows = _rows()

    assert rows["chat"]["available"] is True and rows["chat"]["model_chosen"] is True
    assert rows["search_ranking"]["available"] is False
    assert rows["search_ranking"]["model_chosen"] is False, "no Embedding model is chosen"


def test_a_chosen_model_that_cannot_serve_is_degraded(instance):
    """The negative case: chat is bound, and the instance it is bound to cannot serve."""
    from personalclaw.providers.use_cases import save_active_models

    instance(serves=False)
    save_active_models({"chat": [f"{ENTRY}:alpha:1b"]})

    rows = _rows()

    assert rows["chat"]["available"] is False
    assert rows["chat"]["model_chosen"] is True, "a model is chosen; it is what went away"
    assert rows["transcription"]["model_chosen"] is False
