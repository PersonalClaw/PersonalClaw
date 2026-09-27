"""SDK: what an app's test suite needs to keep its tests off the machine it runs on.

An app's tests run on a developer's machine, and some of what core reaches there is not
inside any PersonalClaw home. ``keychain_off()`` is the one such switch today. The OS keychain
serves every home on the machine, and core reads it, writes it and deletes from it whenever
``keyring`` is importable, so a scratch ``PERSONALCLAW_HOME`` alone leaves a test able to read
or delete the owner's real secrets. After the call, core finds no keychain and credentials live
in the scratch home's ``.env``. It returns the call that lets the keychain back in. A
``conftest.py``'s ``pytest_configure`` turns it off before anything is collected, and
``pytest_unconfigure`` calls what it returned.

An ACP app's tests prove what its CLI is handed without launching the CLI.
``launch_acp_entry(options, work_dir)`` launches the command an ACP agent entry registers,
through the transport every spawn from an entry uses, with the environment such a spawn gets,
waits for it to exit and returns its exit code. The command is a stub standing in for the CLI:
it records what it received and exits. It is never spoken to over ACP, and no host sandbox wraps
it, because the environment is what it measures.

An app's harness imports core through ``personalclaw.sdk`` like the app does
(``tests/test_apps_import_boundary.py``), which is why these are published here rather than
reached for in core's internals by each test suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from personalclaw.config.credentials import keychain_off  # noqa: F401

__all__ = ["keychain_off", "launch_acp_entry"]


async def launch_acp_entry(
    options: Mapping[str, Any], work_dir: Path, *, timeout: float = 60.0
) -> int:
    """Launch an ACP agent entry's ``command`` as a spawn from the entry would, and wait for it.

    *options* is the entry's options, as ``register_acp_cli_entry`` registered them. The child
    gets the child allowlist, the entry's ``env`` and the variables it declares in
    ``env_passthrough`` that are set here, and no other variable of this process's environment.
    Returns the exit code; a command still running after *timeout* seconds is killed and raises
    ``TimeoutError``.
    """
    from personalclaw.acp.transport import AcpProcess
    from personalclaw.llm.acp_agent import options_env

    entry = dict(options)
    proc = AcpProcess(
        command=[str(part) for part in entry.get("command") or []],
        work_dir=Path(work_dir),
        extra_env=options_env(entry) or None,
        sandbox_mode="off",
    )
    await proc.spawn()
    try:
        running = proc.process
        if running is None:  # pragma: no cover - spawn raises rather than leave no process
            raise RuntimeError("the entry's command did not start")
        return await asyncio.wait_for(running.wait(), timeout=timeout)
    finally:
        await proc.kill()
        proc.teardown()
