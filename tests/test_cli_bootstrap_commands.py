"""Every CLI command that resolves a model in its own process bootstraps app providers first.

`personalclaw chat` could never use an app-provided model. `chat` was not in
`cli._PROVIDER_BOOTSTRAP_COMMANDS`, so its process never imported the installed provider apps
(the bundled default model and `ollama-models` are both apps) and never replayed `config.json`'s
`providers[]` — measured on a dev home bound to `host-ollama:gemma4:12b`, it exited 1 with
"provider 'host-ollama' IS in config.json but is not registered in the running gateway …
restart the gateway", a fix that cannot help a command that does not talk to the gateway.

The audit found the same omission on every other command that resolves a model in-process:

* `consolidate` builds a SessionManager on the provider factory (extraction calls a model) and
  resolves the embedding model twice;
* `learn` and `memory` size their vector store with `get_active_embedding_dim()`, which PROBES
  the bound embedding model — unbootstrapped, that resolve failed on every invocation;
* `doctor`'s Provider Health section lists the registry's entries, so an unbootstrapped doctor
  reported "no provider entries configured" on a home that had one.

`run` and `spawn` are gateway clients (they resolve nothing themselves) and stay out.
"""

from __future__ import annotations

import sys

import pytest

from personalclaw import cli
from personalclaw.providers import loader

#: command -> (argv after the program name, the module-level handler `main()` dispatches to).
_MODEL_RESOLVING = {
    "chat": (["chat", "-m", "hello"], "_chat"),
    "consolidate": (["consolidate", "--all"], "_consolidate_cmd"),
    "learn": (["learn", "list"], "_learn"),
    "memory": (["memory", "list"], "_memory_cmd"),
    "doctor": (["doctor"], "_doctor"),
}

_ASYNC_HANDLERS = frozenset({"_chat", "_consolidate_cmd"})


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


def test_a_gateway_client_command_does_not_bootstrap(monkeypatch):
    """The control: `agent list` resolves no model, so it must not pay for loading every app."""
    assert _drive(monkeypatch, ["agent", "list"], "_handle_agent") == ["handler"]
