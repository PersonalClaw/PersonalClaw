"""Every ACP session the host opens from an app's entry carries the ``_meta`` that app declared.

An agent CLI's adapter can take per-session options in the ``_meta`` of ``session/new`` and
``session/load``: which of the CLI's own configuration sources a session loads, for one. Which
keys a CLI reads is vendor knowledge, so the app declares the object when it registers its
runtime (``register_acp_cli_entry(session_meta=...)``) and core sends it, as declared, on every
path that opens a session from the entry: the runtime factory (a new session and a resumed one),
the readiness probe and a pooled connection. A path that left it off would open that session
with the CLI's own defaults, so each is driven here against a stub agent that records every
frame it is sent. No real agent CLI is launched.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

import personalclaw.config.loader as loader

#: What the stub app declares: an invented adapter key, as an app would name its own.
META = {"stubAgent": {"options": {"configSources": ["own"]}}}

_STUB = textwrap.dedent("""
    import json, sys
    record = open(sys.argv[1], "a")
    modes = {"currentModeId": "default", "availableModes": []}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        record.write(json.dumps(msg) + "\\n")
        record.flush()
        if "id" not in msg or "method" not in msg:
            continue
        method = msg["method"]
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
        elif method == "session/new":
            result = {"sessionId": "stub-session", "modes": modes}
        elif method == "session/load":
            result = {"modes": modes}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """)


@pytest.fixture
def home(tmp_path, monkeypatch):
    pc = tmp_path / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    return pc


@pytest.fixture
def stub(tmp_path):
    script = tmp_path / "stub_agent.py"
    script.write_text(_STUB)
    record = tmp_path / "frames.jsonl"
    return [sys.executable, str(script), str(record)], record


def _register(command, **extra):
    from personalclaw.sdk.acp import register_acp_cli_entry

    registered = register_acp_cli_entry(
        cli="stub-agent",
        dialect="claude-code",
        command=command,
        # The host sandbox is not what this is about, and a stub needs no second fence.
        self_sandboxing=True,
        **extra,
    )
    assert registered is not None
    return registered


@pytest.fixture
def unregister():
    yield
    from personalclaw.llm.registry import get_default_registry

    get_default_registry().unregister_entry("acp:stub-agent")


def _opened(record) -> list[dict]:
    """The params of every ``session/new`` and ``session/load`` the stub was sent."""
    frames = [json.loads(line) for line in record.read_text().splitlines() if line.strip()]
    return [f["params"] for f in frames if f.get("method") in ("session/new", "session/load")]


async def _factory(name: str, *, resume: str = "") -> None:
    from personalclaw.llm.registry import get_default_registry

    provider = get_default_registry().build(name)
    if resume:
        provider.set_resume(resume)
    try:
        await asyncio.wait_for(provider.start(), timeout=60)
    finally:
        await provider.shutdown()


async def _probe(options: dict) -> None:
    from personalclaw.llm.acp_agent import AcpAgentProvider

    status = await asyncio.wait_for(AcpAgentProvider.probe_readiness(options), timeout=60)
    assert status.state == "ready", status.detail


async def _pooled(options: dict, cwd) -> None:
    from personalclaw.acp.connection_pool import AcpConnectionPool
    from personalclaw.llm.acp_agent import options_env, options_session_meta

    pool = AcpConnectionPool(start_sem=asyncio.Semaphore(1))
    try:
        provider = await pool.open_session(
            "acp:stub-agent",
            cwd=cwd,
            command=list(options["command"]),
            dialect=options["dialect"],
            sandbox_mode="off",
            extra_env=options_env(options) or None,
            session_meta=options_session_meta(options) or None,
        )
        assert provider is not None
    finally:
        await pool.shutdown()


def test_the_entry_keeps_its_own_copy_of_the_declaration(home, stub, unregister):
    command, _record = stub
    declared = json.loads(json.dumps(META))
    registered = _register(command, session_meta=declared)
    declared["stubAgent"]["options"]["configSources"].append("added-later")
    assert registered.options["session_meta"] == META


def test_an_entry_that_declares_nothing_sends_no_meta(home, stub, unregister):
    command, record = stub
    registered = _register(command)
    assert "session_meta" not in registered.options
    asyncio.run(_factory(registered.name))
    opened = _opened(record)
    assert opened, "the stub was never asked for a session"
    assert [p for p in opened if "_meta" in p] == []


@pytest.mark.parametrize("path", ["factory", "resumed", "probe", "pooled"])
def test_every_session_the_host_opens_carries_the_declaration(
    home, stub, unregister, path, tmp_path
):
    command, record = stub
    sessions = tmp_path / "sessions"
    registered = _register(command, session_meta=META, session_files_dir=str(sessions))
    if path == "factory":
        asyncio.run(_factory(registered.name))
    elif path == "resumed":
        # A session-file hint core adds on a resume of its own rides along with the declaration.
        (sessions / "earlier-session.json").write_text("{}")
        asyncio.run(_factory(registered.name, resume="earlier-session"))
    elif path == "probe":
        asyncio.run(_probe(dict(registered.options)))
    else:
        asyncio.run(_pooled(dict(registered.options), home / "workspace"))

    opened = _opened(record)
    assert opened, "the stub was never asked for a session"
    for params in opened:
        meta = params.get("_meta") or {}
        assert {k: meta.get(k) for k in META} == META, params
    if path == "resumed":
        [loaded] = [p for p in opened if p.get("sessionId") == "earlier-session"]
        assert loaded["_meta"]["_vendor.dev/session_file"].endswith("earlier-session.json")


@pytest.mark.parametrize("declared", [["not", "an", "object"], {"key": {1, 2}}, "text"])
def test_a_declaration_that_is_not_a_json_object_is_refused(home, stub, unregister, declared):
    command, _record = stub
    from personalclaw.llm.registry import get_default_registry

    with pytest.raises(ValueError, match="session_meta"):
        _register(command, session_meta=declared)
    assert get_default_registry()._entries.get("acp:stub-agent") is None  # noqa: SLF001
