"""CLI chat subcommand."""

import gc
import json
import sys

from personalclaw.acp.errors import AcpError, AcpTimeoutError
from personalclaw.config import AppConfig
from personalclaw.config.loader import config_path
from personalclaw.constants import DATA_WARNING
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, ModelProvider

BANNER = r"""
   __  __         _    ___ _
  |  \/  |___ ___| |_ / __| |__ ___ __ __
  | |\/| / -_|_-<| ' \ (__| / _` \ V  V /
  |_|  |_\___/__/|_||_\___|_\__,_|\_/\_/

  Your personal AI agent
"""


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
        await _send_and_print(provider, message)
    else:
        await _interactive(provider, cfg)

    await provider.shutdown()
    # Force GC so subprocess transports are collected while the loop is
    # still open, avoiding "Event loop is closed" noise on exit.
    gc.collect()


async def _send_and_print(provider: ModelProvider, message: str) -> None:
    """Stream a single message to stdout, handling errors and timeouts."""
    try:
        async for event in provider.stream(message):
            if event.kind == EVENT_TEXT_CHUNK:
                print(event.text, end="", flush=True)
            elif event.kind == EVENT_COMPLETE:
                break
        print()  # final newline
    except AcpTimeoutError as e:
        if e.partial_output:
            print(e.partial_output)
        print("\n⏱️  Response timed out.", file=sys.stderr)
        sys.exit(1)
    except AcpError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        sys.exit(1)


async def _interactive(provider: ModelProvider, cfg: AppConfig) -> None:
    """REPL loop — read user input, stream responses, auto-compact at configured threshold."""
    print(BANNER)
    print(DATA_WARNING)
    print()
    print("Type your message (Ctrl+D or 'exit' to quit)\n")

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

        await _send_and_print(provider, message)

        # Check context usage — compact and restart if needed
        pct = provider.context_usage_pct()
        needs_compact = pct >= cfg.session.autocompact_pct

        if needs_compact:
            reason = f"context at {pct:.0f}%"
            print(f"\n🔄 Compacting — {reason}", file=sys.stderr)
            try:
                await provider.compact()
            except Exception:
                pass
            await provider.shutdown()
            await provider.start()
        elif pct >= 75.0:
            print(f"\n⚠️  Context at {pct:.0f}%", file=sys.stderr)

        print()


def _ensure_default_agent_in_config() -> None:
    """Ensure config.json includes a default PersonalClaw agent for fresh installs."""
    p = config_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    if not data.get("agents"):
        data["agents"] = {
            "default": {
                "provider_agent": "personalclaw",
                "workspace": "default",
                "memory_store": "default",
            }
        }
        data["default_agent"] = "default"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
