"""Every CLI command that resolves a model in its own process bootstraps app providers first.

A command left out of `cli._PROVIDER_BOOTSTRAP_COMMANDS` never imports the installed provider apps
(the bundled default model and `ollama-models` are both apps) and never replays `config.json`'s
`providers[]`, so it cannot use an app-provided model. The audit found that omission on every
command that resolves a model in-process:

* `consolidate` builds a SessionManager on the provider factory (extraction calls a model) and
  resolves the embedding model twice;
* `learn` and `memory` open a vector store that embeds with the bound embedding model, resolved
  at each call — unbootstrapped, it resolves to no provider on every invocation and embeds
  nothing (it used to probe that model for the store's width, and fail the same way);
* `doctor`'s Provider Health section lists the registry's entries, so an unbootstrapped doctor
  reported "no provider entries configured" on a home that had one.

`chat`, `run` and `spawn` are gateway clients (their turns resolve in the gateway) and stay out.
"""

from __future__ import annotations

import sys

import pytest

from personalclaw import cli
from personalclaw.providers import loader

#: command -> (argv after the program name, the module-level handler `main()` dispatches to).
_MODEL_RESOLVING = {
    "consolidate": (["consolidate", "--all"], "_consolidate_cmd"),
    "learn": (["learn", "list"], "_learn"),
    "memory": (["memory", "list"], "_memory_cmd"),
    "doctor": (["doctor"], "_doctor"),
}

_ASYNC_HANDLERS = frozenset({"_consolidate_cmd"})


def _drive(monkeypatch, argv: list[str], handler: str) -> list[str]:
    """Run `cli.main()` for *argv* with the bootstrap and the handler recorded, in call order."""
    order: list[str] = []
    monkeypatch.setattr(loader, "bootstrap_cli_providers", lambda: order.append("bootstrap"))

    async def _async(*_a, **_k) -> None:
        order.append("handler")

    def _sync(*_a, **_k) -> None:
        order.append("handler")

    monkeypatch.setattr(cli, handler, _async if handler in _ASYNC_HANDLERS else _sync)
    monkeypatch.setattr(sys, "argv", ["personalclaw", *argv])
    cli.main()
    return order


@pytest.mark.parametrize("command", sorted(_MODEL_RESOLVING))
def test_a_model_resolving_command_bootstraps_app_providers_before_it_runs(command, monkeypatch):
    argv, handler = _MODEL_RESOLVING[command]
    assert _drive(monkeypatch, argv, handler) == ["bootstrap", "handler"]


@pytest.mark.parametrize(
    "argv, handler", [(["agent", "list"], "_handle_agent"), (["chat", "-m", "hello"], "_chat")]
)
def test_a_gateway_client_command_does_not_bootstrap(monkeypatch, argv, handler):
    """The control: `agent list` resolves no model, and `chat`'s turns resolve theirs in the
    gateway, so neither pays for loading every app."""
    assert _drive(monkeypatch, argv, handler) == ["handler"]
