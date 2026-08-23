"""AAP-4 — ``personalclaw-core`` is reachable from an ACP session.

ACP-AGENT-PARITY §2.1, "the biggest single unlock". Phase 1 measured the same
three NOs on all three providers — ``knowledge_search`` NO, ``task_create`` NO,
``notify`` NO, no ``personalclaw-core`` server (`O4`, `C4`, `K4`) — because every
live ``session/new`` sent ``"mcpServers": []``.

**Prong A is the whole mechanism.** All three ``session/new``/``session/load``
sites and the pooled (concurrent) path carry the ``personalclaw-core`` spec, with
the session inject-back (``PERSONALCLAW_SESSION_KEY`` + ``PERSONALCLAW_HOME``)
declared in its env.

**Prong B — config seeding for a CLI that ignores the protocol field — is
deleted** (`AAP-4` DEVIATION 2): no such CLI exists, nothing ever supplied the
argument that reached it, and it was kiro-shaped by construction. The rails at the
bottom of this file are that deletion's regression floor.

The one record that reads like a contradiction, resolved: `K6` found that kiro
never *discovers* the ``$PERSONALCLAW_HOME/agents/personalclaw.json`` the host
writes, because its only roots are ``<cwd>/.kiro/agents`` and ``~/.kiro/agents``.
That is a fact about a **config file path** and it was prong B's motivation — not
evidence that kiro ignores the protocol array, which is a separate channel and
measured live (`K54`, `K100`). A config kiro cannot see and a protocol array kiro
honours are both true, so the deletion removes no kiro mechanism.

Plus the `G31` home-isolation break: the generated agent config's bash-audit hook
named the operator's REAL ``~/.personalclaw/audit.log`` with a literal tilde.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.acp.mcp_servers import CORE_SERVER_NAME, core_mcp_servers


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated PERSONALCLAW_HOME. Never the real one."""
    h = tmp_path / "home"
    (h / "agents").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    import personalclaw.config as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: h, raising=False)
    return h


def _env_of(server: dict) -> dict[str, str]:
    return {e["name"]: e["value"] for e in server.get("env", [])}


# ── prong A: the spec itself ────────────────────────────────────────────────


def test_core_spec_is_the_acp_array_shape(home):
    """ACP wants an ARRAY of named servers, not the agent-config mapping."""
    servers = core_mcp_servers(session_key="sk-1")
    assert isinstance(servers, list) and len(servers) == 1
    s = servers[0]
    assert s["name"] == CORE_SERVER_NAME
    assert s["args"] == ["mcp-core"]
    assert Path(s["command"]).name == "personalclaw"


def test_core_spec_declares_the_session_inject_back(home):
    """``mcp_core._resolve_session_key`` prefers the env var; declare it.

    Without ``PERSONALCLAW_SESSION_KEY`` the server falls back to walking its
    ancestors for a ``session_pid_<pid>.txt``, so ``subagent_run`` completion and
    ``notify`` would land on whatever session that walk happens to find.
    """
    env = _env_of(core_mcp_servers(session_key="sk-abc")[0])
    assert env["PERSONALCLAW_SESSION_KEY"] == "sk-abc"


def test_core_spec_declares_the_isolated_home(home):
    """``PERSONALCLAW_HOME`` is declared, not inherited.

    ``mcp_core`` resolves ``config_dir()`` for the IPC secret, the gateway port
    and the session-pid files. A CLI that spawns MCP servers with a filtered
    environment would otherwise send an isolated-home session's tool calls at
    the operator's real home.
    """
    env = _env_of(core_mcp_servers(session_key="sk")[0])
    assert env["PERSONALCLAW_HOME"] == str(home)


def test_core_spec_omits_an_empty_session_key(home):
    """A warm pool process has no key until ``rekey()``; declare nothing then."""
    assert "PERSONALCLAW_SESSION_KEY" not in _env_of(core_mcp_servers()[0])


def test_core_spec_declares_the_gateway_port(home, monkeypatch):
    """``PERSONALCLAW_PORT`` is declared, not left to the 10000 default.

    ``mcp_core`` builds its API base from ``dashboard.url`` and falls back to 10000.
    A gateway on any other port — ``--port 10051``, or the ``--port auto`` that
    ``--test-mode`` uses — therefore spawned an MCP server that POSTed where nothing
    listened, and every HTTP-bridged core tool returned its ``urlopen`` error as result
    text. Driven on a kiro ACP session: ``subagent_run`` answered
    ``<urlopen error [Errno 61] Connection refused>`` while ``skill_search``,
    ``artifact_save`` and ``knowledge`` (in-process) all worked in the same turn.
    """
    monkeypatch.setenv("PERSONALCLAW_PORT", "10051")
    env = _env_of(core_mcp_servers(session_key="sk")[0])
    assert env["PERSONALCLAW_PORT"] == "10051"


def test_core_spec_port_follows_the_configured_url(home, monkeypatch):
    """With no env override the declared port is the configured one, not the default."""
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
    (home / "config.json").write_text(
        '{"dashboard": {"url": "http://localhost:6777"}}', encoding="utf-8"
    )
    env = _env_of(core_mcp_servers(session_key="sk")[0])
    assert env["PERSONALCLAW_PORT"] == "6777"


# ── prong A: the three client sites + the pooled path ───────────────────────


class _FakeSession:
    def __init__(self, sid="S1"):
        self.session_id = sid


class _UnansweredPending:
    """A pending reply this fake never answers — ``add_done_callback`` is registered
    and simply never fires. Mirrors the real contract: ``send_request`` hands back
    ``(req_id, future)``, and the client attaches a rejection watcher to it."""

    def add_done_callback(self, cb):
        return None


class _FakeConn:
    """Records the params of every session/new + session/load."""

    def __init__(self):
        self.new_params: list[dict] = []
        self.load_params: list[dict] = []
        self.last_session_new_snapshot: dict = {}

    async def new_session(self, params, *, timeout=None, session_files_dir=None):
        self.new_params.append(params)
        return _FakeSession("NEW")

    async def load_session(self, params, *, session_id=None, timeout=None, session_files_dir=None):
        self.load_params.append(params)
        return _FakeSession(session_id or "LOADED")

    async def send_request(self, method, params):
        return 0, _UnansweredPending()

    async def drain_init_notifications(self, duration=0.0):
        return None

    async def close_session(self, sid):
        return None

    def is_process_alive(self):
        return True


def _client(home, session_key="sk-live"):
    from personalclaw.acp.client import AcpClient

    return AcpClient(work_dir=home / "workspace", session_key=session_key)


def test_client_session_new_carries_core(home):
    """``session/new`` on the one-session path (``client.py`` site 2)."""
    c = _client(home)
    servers = c._core_mcp_servers()
    assert [s["name"] for s in servers] == [CORE_SERVER_NAME]
    assert _env_of(servers[0])["PERSONALCLAW_SESSION_KEY"] == "sk-live"


def test_client_rebuilds_the_spec_after_rekey(home):
    """A rekeyed warm process must not carry the first session's key.

    ``AcpPool`` hands out a live process and ``rekey()``s it per session. A spec
    captured in ``__init__`` would pin session 1's key onto every later session,
    which is the inject-back landing on the wrong chat.
    """
    c = _client(home, session_key="sk-first")
    c.rekey("sk-second")
    assert _env_of(c._core_mcp_servers()[0])["PERSONALCLAW_SESSION_KEY"] == "sk-second"


def test_fresh_turn_session_carries_core(home):
    """``start_fresh_turn_session`` — the site the plan names explicitly.

    claude-code finishes a session after its first turn, so a long-lived driver
    reopens one per cycle through here. If this site alone sent ``[]``, core
    would be reachable on turn 1 and gone from turn 2 on.
    """
    c = _client(home)
    conn = _FakeConn()
    c._connection = conn
    c._session = _FakeSession("OLD")
    c._session_id = "OLD"
    c._transport._process = object()  # is_alive() reads the process handle
    c._transport.is_alive = lambda: True  # type: ignore[method-assign]

    asyncio.run(c.start_fresh_turn_session())

    assert len(conn.new_params) == 1
    assert [s["name"] for s in conn.new_params[0]["mcpServers"]] == [CORE_SERVER_NAME]


class _RecordingConn(_FakeConn):
    """``_FakeConn`` plus the two things the fresh-turn contract is about: which verbs went
    out, and whether the MCP init notifications were drained."""

    def __init__(self):
        super().__init__()
        self.sent: list[str] = []
        self.drains = 0

    async def send_request(self, method, params):
        self.sent.append((method, dict(params or {})))
        return len(self.sent), _UnansweredPending()

    async def drain_init_notifications(self, duration=0.0):
        self.drains += 1
        return None


def _fresh_turn(home, *, effort="xhigh", model="openai.gpt-5.4", mode="default"):
    """Drive the real ``start_fresh_turn_session`` on a live client object.

    Built through ``_client`` rather than by hand because ``_work_dir`` is a property that
    writes through to the transport — a hand-assembled client raises before the method runs,
    which would look like a passing test that never executed the path.
    """
    from personalclaw.acp.dialect import CodexDialect

    c = _client(home)
    c._dialect = CodexDialect()
    conn = _RecordingConn()
    c._connection = conn
    c._session = _FakeSession("OLD")
    c._session_id = "OLD"
    c._agent = "codex"
    c._model = model
    c._mode = mode
    c._reasoning_effort = effort
    c._transport._process = object()
    c._transport.is_alive = lambda: True  # type: ignore[method-assign]
    asyncio.run(c.start_fresh_turn_session())
    return conn


def _config_ids(sent: list) -> list[str]:
    """The ``configId`` sequence, in order. codex sends model, mode AND effort as
    ``session/set_config_option`` — asserting on the METHOD name alone cannot tell them
    apart, so every assertion here reads the params."""
    return [p.get("configId", "") for _m, p in sent if p.get("configId")]


def test_fresh_turn_session_reapplies_the_effort_pin(home):
    """`G20` — the pin that silently stopped applying after the first turn.

    ``start_fresh_turn_session``'s own docstring promises it re-runs
    "activate/model/mode/effort + drain"; it ran only the first three. A driver that reopens a
    session per cycle (``gateway.py``, one prompt per cycle) therefore kept the agent, model
    and mode looking correctly specialized while the EFFORT silently reverted to the adapter
    default from turn 2 on.
    """
    conn = _fresh_turn(home, effort="xhigh")
    ids = _config_ids(conn.sent)
    assert "effort" in ids, f"the effort pin was not re-applied: {ids}"
    effort_params = [p for _m, p in conn.sent if p.get("configId") == "effort"]
    assert effort_params[0]["value"] == "xhigh", effort_params
    # Effort must follow the model, the ordering the full handshake documents (granularity
    # can be model-dependent).
    assert ids.index("effort") > ids.index("model"), f"effort preceded model: {ids}"


def test_fresh_turn_session_drains_mcp_init_notifications(home):
    """The other half of the same docstring. Skipping the drain leaves MCP server init
    notifications queued to interleave into the turn — on the very path `AAP-4` exists to keep
    core reachable."""
    conn = _fresh_turn(home)
    assert conn.drains == 1, f"init notifications were not drained (drains={conn.drains})"


def test_fresh_turn_session_sends_no_effort_verb_when_none_is_pinned(home):
    """The vacuity floor: with no effort pinned the dialect returns None and nothing extra
    goes out, so the fix cannot be "always send something"."""
    pinned = _config_ids(_fresh_turn(home, effort="xhigh").sent)
    unpinned = _config_ids(_fresh_turn(home, effort="").sent)
    assert "effort" in pinned and "effort" not in unpinned, f"pinned={pinned} unpinned={unpinned}"


@pytest.mark.asyncio
async def test_pooled_path_defaults_to_core(home):
    """The pooled ``mcp_servers`` parameter had no supplier — it does now.

    ``open_acp_session_provider`` declared ``mcp_servers`` and passed
    ``mcp_servers or []``, and no caller ever supplied it: a live reader of an
    unwritten key. The concurrent path therefore opened every session exactly as
    empty as the one-session path.
    """
    from personalclaw.llm.acp_session_provider import open_acp_session_provider

    conn = _FakeConn()
    await open_acp_session_provider(
        conn, runtime_id="acp:demo", cwd=home / "workspace", session_key="sk-pool"
    )
    servers = conn.new_params[0]["mcpServers"]
    assert [s["name"] for s in servers] == [CORE_SERVER_NAME]
    assert _env_of(servers[0])["PERSONALCLAW_SESSION_KEY"] == "sk-pool"


@pytest.mark.asyncio
async def test_pooled_path_honours_an_explicit_empty_list(home):
    """``[]`` still means "no servers" — the default is not a clamp."""
    from personalclaw.llm.acp_session_provider import open_acp_session_provider

    conn = _FakeConn()
    await open_acp_session_provider(
        conn, runtime_id="acp:demo", cwd=home / "workspace", mcp_servers=[]
    )
    assert conn.new_params[0]["mcpServers"] == []


def test_pool_forwards_the_session_key_to_the_opener():
    """The pool holds the session key; the opener needs it to build the spec.

    Asserts the CALL, not just that the opener can accept a key: the pool's
    ``open_session`` had the key in hand and dropped it.
    """
    import inspect

    from personalclaw.acp.connection_pool import AcpConnectionPool

    src = inspect.getsource(AcpConnectionPool.open_session)
    assert "session_key=session_key" in src


# ── G31: the generated hook must not name the REAL home ─────────────────────


def test_generated_hook_writes_into_the_active_home(home, monkeypatch):
    """`G31` — the bash-audit hook used a literal ``~/.personalclaw/audit.log``.

    A literal tilde turns every isolated-home session into an append to the
    operator's REAL audit log. This rail outlived prong B on purpose: the deleted
    seeder was one consumer of this generated file, but the native path and the
    dashboard MCP manager write it too, so the leak was never prong B's to own.
    """
    import personalclaw.agent as agent_mod

    monkeypatch.setattr(agent_mod, "_USER_PROMPT", home / "nope.md", raising=False)
    cfg = agent_mod.build_agent_config()
    commands = [e["command"] for e in cfg["hooks"]["postToolUse"]]
    assert commands, "the bundled bash-audit hook disappeared"
    for cmd in commands:
        assert str(home / "audit.log") in cmd
        assert "~/.personalclaw" not in cmd
        assert "{{" not in cmd


def test_shipped_defaults_name_no_real_home_path():
    """The shipped file itself must not carry a tilde path to a home."""
    import personalclaw.agent as agent_mod

    raw = (Path(agent_mod.__file__).parent / "config" / "defaults.json").read_text()
    assert "~/.personalclaw" not in raw


def test_unresolved_placeholder_fails_closed(home, monkeypatch):
    """A placeholder that survives would be run verbatim by the CLI's shell."""
    import personalclaw.agent as agent_mod

    with pytest.raises(RuntimeError, match="unresolved placeholder"):
        agent_mod._bundled_hooks(
            {"hooks": {"postToolUse": [{"matcher": "bash", "command": "x >> {{NOPE}}"}]}}
        )


# ── prong B is DELETED — AAP-4 DEVIATION 2 ──────────────────────────────────
#
# ``acp/config_seed.py`` seeded a ``personalclaw.json`` symlink into a CLI's own
# agent-discovery directory, for a CLI that ignored the protocol's ``mcpServers``
# field. No such CLI exists: §2.1's four fenced drives measured all three shipped
# CLIs honouring the protocol array (`O76`, `K100`, `C90`), and codex decisively —
# ``personalclaw mcp-core`` ran four levels under ``codex-acp`` with zero
# ``personalclaw`` entries in ``~/.codex/config.toml``, so the protocol frame was
# the only channel. The seeder was also unreachable (nothing in either repo passed
# ``agent_config_dir``) and kiro-shaped by construction (a hardcoded
# ``personalclaw.json`` filename holding a kiro agent document, where codex reads
# TOML ``[mcp_servers.*]`` and claude-code reads ``personalclaw.mcp.json``).
#
# The rails below are the deletion's regression floor. They exist because `G116`
# proved the three cheap checks all pass on a half-deleted tree: ``mypy`` reports
# success (``ignore_missing_imports`` hides a missing first-party MODULE), and both
# ``config_seed`` imports were function-local, so importing ``_register`` succeeded
# too. Only *calling* the disable path failed.


def test_the_config_seeder_module_is_gone():
    """The module is deleted, not merely unreferenced (clean-break tenet)."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("personalclaw.acp.config_seed")

    # Vacuity floor: the package this module lived in must still import, else the
    # raise above would be an unrelated broken import root rather than the
    # deletion this rail measures.
    assert importlib.import_module("personalclaw.acp.mcp_servers") is not None


def test_the_sdk_signature_no_longer_carries_agent_config_dir():
    """``register_acp_cli_entry`` is a public SDK export, so its shape is a contract.

    A CLI's agent-config directory is no longer declarable: an app bundle that
    still passed it would now ``TypeError`` at import time rather than silently
    seeding nothing, which is the point of removing it instead of ignoring it.
    """
    import inspect

    from personalclaw.sdk.acp import register_acp_cli_entry

    params = inspect.signature(register_acp_cli_entry).parameters
    assert "agent_config_dir" not in params
    # Vacuity floor: the other bundle-declared vendor-knowledge parameters must
    # still be there, or this rail would pass against a wrong/renamed symbol.
    assert {"cli", "dialect", "command", "requires_executable", "login_command"} <= set(params)


def test_disable_is_registry_only_and_survives_the_deletion(monkeypatch):
    """`G116` closed by construction: the disable path no longer imports the seeder.

    This is the ONE call path that was ungated, so it is the one that raised
    ``ModuleNotFoundError`` on a tree with ``config_seed.py`` removed. Asserting
    it runs clean is what makes the deletion complete rather than half-done.
    """
    from personalclaw.acp_bundles import _register

    unregistered: list[str] = []

    class _Registry:
        def unregister_entry(self, name: str) -> None:
            unregistered.append(name)

    monkeypatch.setattr(_register, "get_default_registry", lambda: _Registry())

    _register.unregister_acp_cli_entry("never-enabled")

    # Vacuity floor: the call must actually have reached the registry. Without
    # this, a disable path rewritten into a no-op would also "survive", and the
    # rail would prove nothing about the deletion.
    assert unregistered == ["acp:never-enabled"]


@pytest.fixture
def _stub_registry(monkeypatch):
    """Registration without mutating the process-wide provider registry — the same
    shape the seeding tests above use, so these tests can call the real
    ``register_acp_cli_entry`` for its DIRECTORY side effect without leaking entries."""
    from personalclaw.acp_bundles import _register

    monkeypatch.setattr(
        _register,
        "get_default_registry",
        lambda: type(
            "R",
            (),
            {"unregister_entry": lambda self, n: None, "register_entry": lambda self, e: None},
        )(),
    )


# ── AAP-7 §2.4: a DECLARED session_files_dir is provisioned, not just recorded ──


def test_register_provisions_a_declared_session_files_dir(tmp_path, _stub_registry):
    """The plan's registration deliverable: "the core registration helper creates it".

    Both readers of the option probe for files INSIDE the directory (``AcpClient``'s
    ``_meta`` session-file hint, ``AcpSession``'s JSONL tool-result tail), and a path
    that does not exist makes every probe a silent miss indistinguishable from an empty
    directory. So a bundle that declares one gets a live one.
    """
    from personalclaw.acp_bundles._register import register_acp_cli_entry

    target = tmp_path / "acp_sessions" / "demo"
    assert not target.exists()
    entry = register_acp_cli_entry(
        cli="demo",
        dialect="default",
        command=["/bin/true"],
        session_files_dir=str(target),
    )
    assert entry is not None
    assert target.is_dir(), "declared session_files_dir was recorded but never created"
    assert entry.options["session_files_dir"] == str(target)


def test_an_uncreatable_session_files_dir_drops_the_option(tmp_path, _stub_registry):
    """VACUITY FLOOR / fail-honest: advertising a directory nothing can read is worse
    than declaring none, so a creation failure removes the option rather than leaving a
    dangling path for the readers to miss on."""
    from personalclaw.acp_bundles._register import register_acp_cli_entry

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    entry = register_acp_cli_entry(
        cli="demo2",
        dialect="default",
        command=["/bin/true"],
        session_files_dir=str(blocker / "under-a-file"),
    )
    assert entry is not None
    assert "session_files_dir" not in entry.options


def test_no_declaration_creates_nothing(tmp_path, _stub_registry):
    """The option stays OPT-IN: a bundle that declares nothing gets nothing."""
    from personalclaw.acp_bundles._register import register_acp_cli_entry

    entry = register_acp_cli_entry(cli="demo3", dialect="default", command=["/bin/true"])
    assert entry is not None
    assert "session_files_dir" not in entry.options


def test_no_seed_receipt_is_written_anywhere(home):
    """The ``acp_seeds.json`` receipt is gone with its only writer and reader."""
    import inspect

    from personalclaw.acp_bundles import _register

    assert "config_seed" not in inspect.getsource(_register)
    assert not (home / "acp_seeds.json").exists()
    # Vacuity floor: the home fixture is real and writable, so "no receipt" is a
    # fact about the code and not about an unusable directory.
    (home / "probe.json").write_text("{}", encoding="utf-8")
    assert (home / "probe.json").exists()
