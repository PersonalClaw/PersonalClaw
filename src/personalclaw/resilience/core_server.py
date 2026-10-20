"""PersonalClaw's own server in the agent runtime config: the Doctor's one reading of it.

``agents/personalclaw.json`` names the servers the agent launches (``mcpServers``), the tools it is
offered (``tools``) and the tools it runs without asking (``allowedTools``). On a config that
exists, every gateway start writes PersonalClaw's own server into ``mcpServers``
(``agent.rebuild_agent_config``) and keeps the server where the owner left it in the two lists.

The Doctor's check (``tools.core_server`` in :mod:`~personalclaw.resilience.doctor`),
``personalclaw doctor`` and the check's Fix (:mod:`~personalclaw.resilience.fixes`) all read the
file here, so they judge one file one way and say a fault in the same words. Nothing here writes:
the Fix is the one writer, and it changes only the server entry. Where the server stands in the
two lists is read and never changed, by the Doctor or by any of its Fixes: they are the owner's.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # the Doctor imports this module to register the check below
    from personalclaw.resilience.doctor import DoctorContext, ProbeResult

#: The confirm-gated Fix that writes PersonalClaw's own server into the agent runtime config again.
CORE_SERVER_FIX = "tools.restore-core-server"

#: The agent runtime config, as the Doctor's sentences name it: under the home.
AGENT_CONFIG_NAME = "agents/personalclaw.json"


def core_server_name() -> str:
    from personalclaw.acp.mcp_servers import CORE_SERVER_NAME

    return CORE_SERVER_NAME


def core_server_args() -> list[str]:
    """The server's own arguments, from the one definition a start writes it from."""
    from personalclaw.agent import _MANAGED_MCP_SERVERS

    return [str(a) for a in _MANAGED_MCP_SERVERS[core_server_name()]["args"]]


def is_program(command: str) -> bool:
    """An absolute path to an executable file: what a start accepts as a command as it stands."""
    return (
        bool(command)
        and os.path.isabs(command)
        and os.path.isfile(command)
        and os.access(command, os.X_OK)
    )


@dataclass(frozen=True)
class CoreServerReading:
    """Where PersonalClaw's own MCP server stands in the agent runtime config, as it was read."""

    found: bool = False
    #: Why the file cannot be read as an agent config, or ``""`` when it can.
    unreadable: str = ""
    #: The ``mcpServers`` entry for the server: ``None`` when there is none.
    entry: Optional[dict[str, Any]] = None
    #: An entry is there, but it is not a server definition (not a JSON object).
    malformed: bool = False
    in_tools: bool = False
    in_allowed: bool = False
    #: The file as it was read, for the Fix, which changes only the server entry in it.
    document: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def command(self) -> str:
        return str((self.entry or {}).get("command") or "")

    @property
    def args(self) -> list[str]:
        args = (self.entry or {}).get("args")
        return [str(a) for a in args] if isinstance(args, list) else []

    @property
    def set_up(self) -> bool:
        """The entry starts PersonalClaw's server as a gateway start writes it: a command that is a
        program on this machine, given the server's own arguments."""
        return (
            self.entry is not None and is_program(self.command) and self.args == core_server_args()
        )

    @property
    def needs_setting_up(self) -> bool:
        """A readable agent config whose entry for the server is missing or does not start it."""
        return self.found and not self.unreadable and not self.set_up


def core_server_command() -> str:
    """This install's ``personalclaw``, the command a start writes into the entry, or ``""`` when
    none is found here: then there is no command to give the entry."""
    from personalclaw.agent import _MANAGED_MCP_SERVERS

    spec = _MANAGED_MCP_SERVERS[core_server_name()]
    command = str(spec.get("command") or spec["command_fn"]())
    return command if is_program(command) else ""


def agent_config_path() -> Path:
    """The agent runtime config the Doctor's check reads and its Fix writes: one path, so the Fix
    always acts on the file the check found wrong."""
    from personalclaw.agent import AGENT_FILENAME, agents_dir

    return agents_dir() / AGENT_FILENAME


def read_core_server(path: Path) -> CoreServerReading:
    """Read *path* for PersonalClaw's own server. Opens the file for reading only."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CoreServerReading()
    except OSError as exc:
        return CoreServerReading(found=True, unreadable=f"it cannot be opened ({exc.strerror})")
    try:
        document = json.loads(text)
    except ValueError:
        return CoreServerReading(found=True, unreadable="it is not JSON")
    if not isinstance(document, dict):
        return CoreServerReading(found=True, unreadable="it is not a JSON object")
    servers = document.get("mcpServers", {})
    if not isinstance(servers, dict):
        return CoreServerReading(found=True, unreadable="its mcpServers is not a JSON object")
    raw = servers.get(core_server_name())
    ref = f"@{core_server_name()}"
    tools, allowed = document.get("tools"), document.get("allowedTools")
    return CoreServerReading(
        found=True,
        entry=raw if isinstance(raw, dict) else None,
        malformed=raw is not None and not isinstance(raw, dict),
        in_tools=isinstance(tools, list) and ref in tools,
        in_allowed=isinstance(allowed, list) and ref in allowed,
        document=document,
    )


def core_server_detail(reading: CoreServerReading) -> str:
    """What the agent runtime config says of PersonalClaw's own server, in one sentence: the Doctor
    page's row and ``personalclaw doctor``'s say it the same way."""
    where = f"PersonalClaw's server in {AGENT_CONFIG_NAME}"
    if not reading.found:
        return f"there is no {AGENT_CONFIG_NAME} in this home; the gateway writes it as it starts"
    if reading.unreadable:
        return (
            f"{AGENT_CONFIG_NAME} cannot be read as an agent config ({reading.unreadable}), so "
            "PersonalClaw's server entry in it cannot be checked"
        )
    if reading.malformed:
        return f"{where} is not a server definition"
    if reading.entry is None:
        return (
            f"{AGENT_CONFIG_NAME} has no entry for PersonalClaw's own server ({core_server_name()})"
        )
    command = reading.command
    if not command:
        return f"{where} names no command"
    if not is_program(command):
        return f"{where} starts {command}, which is not a program on this machine"
    own = " ".join(core_server_args())
    if reading.args != core_server_args():
        return f"{where} starts {command} with arguments other than {own}"
    return f"{where} starts {command} {own}"


def core_server_remedy(reading: CoreServerReading) -> str:
    """The next step when no Fix can act: the file cannot be read, or there is no command.

    A file that cannot be read is replaced at the next start by one built from the shipped
    defaults, so the sentence says what those defaults allow, read from them: an owner who took
    the server out of ``allowedTools`` gets it back that way, and should know before it happens.
    """
    if reading.unreadable:
        from personalclaw.agent import get_shipped_tools

        widens = f"@{core_server_name()}" in get_shipped_tools().get("allowedTools", [])
        return (
            "No automatic fix — what the file holds is yours to judge. The gateway's next start "
            "replaces it with one built from the defaults, which keeps nothing of it"
            + (" and lets PersonalClaw's own tools run without asking" if widens else "")
            + "; to keep what it holds, repair the file first."
        )
    return (
        "No automatic fix — this install's personalclaw command was not found, so there is no "
        "command to give the entry. Reinstall PersonalClaw; once its command is there, a gateway "
        "start sets the entry up."
    )


async def probe_core_server(_ctx: DoctorContext) -> ProbeResult:
    """tools — does the agent runtime config start PersonalClaw's own server as a start writes it?

    The Doctor's check ``tools.core_server``, and read-only like every probe. An entry that is
    missing, or whose command is not a program here, names its repair (:data:`CORE_SERVER_FIX`),
    which adds nothing to ``tools`` or ``allowedTools``. The file is the one the Fix writes, not
    one worked out from the context's home, so the Fix always acts on what this check read.
    """
    from personalclaw.resilience.doctor import ProbeResult

    def _read() -> tuple[CoreServerReading, str]:
        reading = read_core_server(agent_config_path())
        return reading, (core_server_command() if reading.needs_setting_up else "")

    reading, command = await asyncio.to_thread(_read)
    detail = core_server_detail(reading)
    evidence = {
        "path": str(agent_config_path()),
        "command": reading.command,
        "in_tools": reading.in_tools,
        "in_allowed_tools": reading.in_allowed,
    }
    if reading.unreadable or (reading.needs_setting_up and not command):
        remedy = core_server_remedy(reading)
        return ProbeResult(ok=False, detail=detail, evidence=evidence, remedy=remedy)
    if reading.needs_setting_up:
        return ProbeResult(ok=False, detail=detail, evidence=evidence, fix_id=CORE_SERVER_FIX)
    return ProbeResult(ok=True, detail=detail, evidence=evidence)
