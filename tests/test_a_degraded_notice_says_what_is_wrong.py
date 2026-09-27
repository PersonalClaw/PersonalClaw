"""A notice about a surface going down says what is wrong, in the words the chip uses.

The chip learned (#3735) to tell a surface waiting on a model nobody chose ("Choose a model", "No
model chosen for Chat") from one whose chosen model cannot serve ("Chat degraded"). The notice for
the same transition did not: every one read "<surface> degraded" and "No Chat model — …", so a
user who removed their only instance was told Chat had degraded, and one whose bound model lost
its credential was told there was no Chat model at all.

Each row of the report now carries ``problem``, one sentence for what the surface waits on, and
the notice leads with it: that no model is chosen, or why the chosen one cannot serve, as
resolution would refuse it. Driven through the real built-in contracts and a real registry.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

ENTRY = "local"


class _Bell:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body))

    def about(self, label: str) -> list[tuple[str, str, str]]:
        return [
            n
            for n in self.notes
            if n[1]
            in {
                f"{label} degraded",
                f"{label} recovered",
                f"{label} is ready",
                f"Choose a model for {label}",
            }
        ]


@pytest.fixture
def instance():
    """Register one chat-capable instance, as the Add-instance form saves it (``model: ""``);
    ``serves=False`` makes its type decline, the way a model not downloaded yet does."""
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
            readiness=(
                None
                if serves
                else (
                    lambda _entry, *, implicit: (
                        "the model alpha:1b is not downloaded yet",
                        "download it in Settings → Models",
                    )
                )
            ),
        )
        registry.register_entry(
            ProviderEntry(name=ENTRY, type="rec", model="", options=dict(options or {}))
        )
        set_default_registry(registry)  # conftest restores the singleton afterwards
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"providers": [{"name": ENTRY, "type": "rec", "model": ""}]}))

    yield _register
    degraded.reset_transition_state()


def _evaluate(bell: _Bell) -> dict[str, dict]:
    from personalclaw.resilience import degraded

    return {row["surface"]: row for row in degraded.evaluate(notify=True, state=bell)}


def test_a_surface_left_with_no_model_chosen_is_told_to_choose_one(instance):
    """🔴 Red on main: "Chat degraded" / "No Chat model — …", a warning, for a choice to make."""
    bell = _Bell()
    instance({"default_model": "alpha:1b"})  # Chat runs on the instance's Default Model
    _evaluate(bell)
    instance()  # its Default Model is cleared: nothing is chosen now
    rows = _evaluate(bell)

    [(kind, title, body)] = bell.about("Chat")
    assert (kind, title) == ("info", "Choose a model for Chat")
    assert body == f"No model chosen for Chat. {rows['chat']['floor']}"
    assert rows["chat"]["problem"] == "No model chosen for Chat.", "the chip's row says it too"

    instance({"default_model": "alpha:1b"})
    _evaluate(bell)
    assert bell.about("Chat")[1] == ("info", "Chat is ready", "A Chat model is chosen.")


def test_a_chosen_model_that_cannot_serve_says_why(instance):
    """🔴 Red on main: "No Chat model — …", about a Chat model that is chosen."""
    from personalclaw.providers.use_cases import save_active_models

    bell = _Bell()
    instance()
    save_active_models({"chat": [f"{ENTRY}:alpha:1b"]})
    _evaluate(bell)
    instance(serves=False)  # the model it is bound to stops serving
    rows = _evaluate(bell)

    [(kind, title, body)] = bell.about("Chat")
    assert (kind, title) == ("warning", "Chat degraded")
    assert body.startswith(
        "The model alpha:1b is not downloaded yet. Download it in Settings → Models. "
    ), body
    assert body.startswith(rows["chat"]["problem"] + " "), "the chip's row says it too"

    instance()
    _evaluate(bell)
    assert bell.about("Chat")[1][:2] == ("info", "Chat recovered")


def test_an_available_surface_has_no_problem(instance):
    instance({"default_model": "alpha:1b"})
    rows = _evaluate(_Bell())
    assert rows["chat"]["available"] is True and rows["chat"]["problem"] is None
