"""CLI chat subcommand."""

import gc
import sys

from personalclaw.acp.errors import AcpError, AcpTimeoutError
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.constants import BANNER, DATA_WARNING
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, ModelProvider
from personalclaw.llm.events import EVENT_SPENT


def config_path():
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


async def _chat(message: str | None, model: str | None) -> None:
    """Run a single message or interactive chat session."""
    cfg = AppConfig.load()
    # --model threads through the factory as a per-session override — the same
    # lever the dashboard composer uses (writing cfg.agent.model was a no-op:
    # the bridge resolves models from active_models.json, never that field).
    provider: ModelProvider = cfg.create_provider_factory()(
        "cli_chat", agent=cfg.default_agent or None, model_override=model or None
    )
    await provider.start()

    if message:
        await _send_and_print(provider, message, opens_session=True)
    else:
        await _interactive(provider, cfg)

    await provider.shutdown()
    # Force GC so subprocess transports are collected while the loop is
    # still open, avoiding "Event loop is closed" noise on exit.
    gc.collect()


def _with_safety_rules(message: str) -> str:
    """*message* behind the default agent's own instructions and the platform's safety rules,
    framed as the turn engine frames an agent's system prompt. This chat hands its provider the
    typed words and no prompt of its own, and the agent it talks to is the default agent, so the
    message that opens its session is the one that carries them, as every agent's first message
    does (``agent_instructions``, ``with_safety_rules``)."""
    from personalclaw.agents.instructions import agent_instructions
    from personalclaw.prompt_providers.runtime import render_snippet_block, with_safety_rules

    prompt = with_safety_rules(agent_instructions(None).composed())
    block = render_snippet_block("agent-system-prompt-wrapper", {"agent_prompt": prompt})
    return f"{block or prompt}\n\n{message}"


async def _send_and_print(provider: ModelProvider, message: str, *, opens_session: bool) -> None:
    """Stream a single message to stdout, handling errors and timeouts. The message that opens
    the provider's session carries the platform's safety rules ahead of it."""
    if opens_session:
        message = _with_safety_rules(message)
    try:
        async for event in provider.stream(message):
            if event.kind == EVENT_TEXT_CHUNK:
                print(event.text, end="", flush=True)
            elif event.kind in (EVENT_COMPLETE, EVENT_SPENT):
                # Per-turn cost/token ledger, CLI write-site; a turn ending in an error says what
                # its calls spent first (EVENT_SPENT), and the error follows.
                from personalclaw.usage_ledger import record_from_event

                _m = getattr(getattr(provider, "client", None), "_model", "") or ""
                record_from_event(
                    event,
                    source="cli",
                    session_key="cli_chat",
                    provider="acp",
                    model=_m if isinstance(_m, str) and _m != "auto" else "",
                )
                if event.kind == EVENT_COMPLETE:
                    break
        print()  # final newline
    except AcpTimeoutError as e:
        if e.partial_output:
            print(e.partial_output)
        print("\nResponse timed out.", file=sys.stderr)
        sys.exit(1)
    except AcpError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


async def _interactive(provider: ModelProvider, cfg: AppConfig) -> None:
    """REPL loop — read user input, stream responses, auto-compact at configured threshold."""
    print(BANNER)
    print(DATA_WARNING)
    print()
    print("Type your message (Ctrl+D or 'exit' to quit)\n")

    opens_session = True
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not message:
            continue
        if message.lower() in ("exit", "quit", "/exit", "/quit", ":q"):
            print("Bye!")
            break

        await _send_and_print(provider, message, opens_session=opens_session)
        opens_session = False

        # Check context usage — compact and restart if needed
        # ``None`` = the provider measured nothing. Neither compact nor print a
        # percentage in that case; there is no percentage to print.
        pct = provider.context_usage_pct()

        if pct is not None and pct >= cfg.session.autocompact_pct:
            reason = f"context at {pct:.0f}%"
            print(f"\nCompacting the conversation: {reason}.", file=sys.stderr)
            try:
                await provider.compact()
            except Exception:
                pass
            await provider.shutdown()
            await provider.start()
            # A provider that keeps its turns in this process still holds the one that carried
            # the safety rules; one whose turns lived in its CLI starts over, without them.
            opens_session = not provider.compacts_in_process
        elif pct is not None and pct >= 75.0:
            print(f"\nContext at {pct:.0f}%.", file=sys.stderr)

        print()


def _ensure_default_agent_in_config() -> str | None:
    """Ensure config.json includes a default PersonalClaw agent for fresh installs.

    In the config transaction. An unreadable config.json is left alone, and why is returned for
    `setup` to report: this used to read it as `{}` and write the default agent over every
    setting it held. None when the agent is there.
    """
    from personalclaw.config.loader import ConfigWriteError
    from personalclaw.config.transactions import mutate_config

    def _seed(data: dict) -> None:
        if data.get("agents"):
            return
        data["agents"] = {
            "default": {
                "provider_agent": "personalclaw",
                "workspace": "default",
                "memory_store": "default",
            }
        }
        data["default_agent"] = "default"

    try:
        mutate_config(_seed, path=config_path())
    except ConfigWriteError as exc:
        return f"could not add the default agent: {exc}"
    return None
