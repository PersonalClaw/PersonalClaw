"""The instructions an agent runs on, read in one place, whichever store defines the agent.

An agent is its instructions (its system prompt) and its voice as much as its runtime and tools,
and every path that runs a named agent hands its model those words: a chat routed to it, a spawn,
a workflow stage, an automation or an app run that names it, a webhook on it, a room it sits in,
a loop it works, the heartbeat on the default agent. They used to be read three ways. The chat read
the agent's profile through its runtime bindings, which answer for a name ``config.json`` does not
hold with the default agent's own fields; the assembler read only the agent files under
``<home>/agents``, so a webhook on an agent defined in ``config.json`` sent its model no prompt at
all; and a spawn, a room member and the heartbeat read nothing, so a spawned agent ran on the
Background prompt and a room member on its role line alone. :func:`agent_instructions` is now the
one reader, and ``tests/test_agent_safety_rules_census.py`` fails for a path that names an agent
and hands its model none of it.

What the platform's safety rules add is not here: they are layered on whatever prompt resolved,
after the agent's own words, once (``prompt_providers.runtime.with_safety_rules``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.config.loader import AppConfig

logger = logging.getLogger(__name__)

#: Files under ``<home>/agents`` that define no agent: the runtime config the agent CLI reads
#: (``agent.rebuild_agent_config``), whose ``prompt`` names the shipped Chat prompt. Read as an
#: agent's instructions, it would put that file in place of the prompt Settings → Prompts binds.
_NOT_A_DEFINITION = frozenset({"personalclaw.json"})


class AgentInstructions(NamedTuple):
    """An agent's own instructions and its voice, as its definition holds them ("" for none)."""

    prompt: str = ""
    voice: str = ""

    def composed(self) -> str:
        """The instructions with the voice ahead of them, as a turn's prompt composes the two
        (``config.loader._compose_voice``), for a path that writes its own first message."""
        from personalclaw.config.loader import _compose_voice

        return _compose_voice(self.voice, self.prompt).strip()


def agent_instructions(name: str | None, cfg: "AppConfig | None" = None) -> AgentInstructions:
    """Agent *name*'s own instructions and voice, from whichever store defines it.

    *name* is the agent as a turn names it: a profile's name (exactly as the runtime is looked up
    by it), the default native agent under any of its spellings, or empty for the default agent,
    the one every new chat starts with (``default_agent``).

    1. Its profile in ``config.json``: what the Agents page shows and edits, and where the
       built-in workers' protocols are seeded.
    2. When that profile holds no instructions, or there is no profile, the agent's file under
       ``<home>/agents`` (``<name>.json``, by its ``name`` or its file name, as every reader of
       that layout matches it): its ``system_prompt``, else its ``prompt``, inline or as a
       ``file://`` path read through the sensitive-path check.
    3. Neither: none. The default agent ships with none on purpose, so its turns run on the
       prompt Settings → Prompts binds for their context (``ContextBuilder.build_message``).
    """
    from personalclaw.agents.defaults import default_agent_name, normalize_agent_name
    from personalclaw.config.loader import AppConfig

    if cfg is None:
        cfg = AppConfig.load()
    wanted = (name or "").strip()
    key = normalize_agent_name(wanted) if wanted else default_agent_name(cfg)
    profile = (cfg.agents or {}).get(key)
    prompt = _text(getattr(profile, "system_prompt", ""))
    voice = _text(getattr(profile, "voice", ""))
    if prompt.strip():
        return AgentInstructions(prompt=prompt, voice=voice)
    file_prompt, file_voice = _from_agent_file(key)
    return AgentInstructions(prompt=file_prompt, voice=voice if profile is not None else file_voice)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _from_agent_file(name: str) -> tuple[str, str]:
    """``(instructions, voice)`` from *name*'s file under ``<home>/agents``; empty when it has
    none, its file cannot be read, or its ``file://`` prompt is refused or unreadable."""
    from personalclaw.agent import agents_dir

    try:
        files = sorted(agents_dir().glob("*.json"))
    except OSError:
        return "", ""
    for path in files:
        if path.name in _NOT_A_DEFINITION:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not (data.get("name") == name or path.stem == name):
            continue
        prompt = _text(data.get("system_prompt")) or _text(data.get("prompt"))
        if prompt.startswith("file://"):
            prompt = _read_prompt_file(prompt[len("file://") :])
        return prompt, _text(data.get("voice"))
    return "", ""


def _read_prompt_file(path: str) -> str:
    """The prompt file an agent file points at, through the sensitive-path check; "" when it is
    refused or cannot be read, so a broken pointer leaves the agent with no instructions rather
    than failing its turn."""
    from personalclaw.hooks import safe_read_file

    try:
        return safe_read_file(path)
    except (OSError, PermissionError, UnicodeDecodeError):
        logger.warning("An agent's prompt file could not be read: %s", path)
        return ""
