"""AAP-4 — ``personalclaw-core`` is reachable from an ACP session.

ACP-AGENT-PARITY §2.1, "the biggest single unlock". Phase 1 measured the same
three NOs on all three providers — ``knowledge_search`` NO, ``task_create`` NO,
``notify`` NO, no ``personalclaw-core`` server (`O4`, `C4`, `K4`) — because every
live ``session/new`` sent ``"mcpServers": []``.

Two prongs are covered here:

* **prong A** — the protocol path: all three ``session/new``/``session/load``
  sites and the pooled (concurrent) path carry the ``personalclaw-core`` spec,
  with the session inject-back (``PERSONALCLAW_SESSION_KEY`` + ``PERSONALCLAW_HOME``)
  declared in its env;
* **prong B** — config seeding for a CLI that ignores the protocol field, under
  the full seeding contract (plan `:139`).

Plus the `G31` home-isolation break the seeding would otherwise activate: the
generated agent config's bash-audit hook named the operator's REAL
``~/.personalclaw/audit.log`` with a literal tilde.
"""

from __future__ import annotations

import asyncio
import json
import os
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

    Prong B makes a CLI honour this file, so a literal tilde turns every
    isolated-home ACP session into an append to the operator's real audit log.
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


# ── prong B: the seeding contract (plan :139) ───────────────────────────────


@pytest.fixture
def seed_env(home):
    """A generated agent config plus a fake CLI agent-discovery root."""
    src = home / "agents" / "personalclaw.json"
    src.write_text(json.dumps({"name": "personalclaw"}), encoding="utf-8")
    cli_root = home.parent / "cli-root" / "agents"
    cli_root.mkdir(parents=True)
    return src, cli_root


def test_seed_makes_the_config_discoverable(seed_env):
    from personalclaw.acp.config_seed import seed_agent_config

    src, cli_root = seed_env
    res = seed_agent_config("kiro-cli", cli_root)
    assert res["status"] == "seeded"
    dest = cli_root / "personalclaw.json"
    assert dest.is_symlink() and os.readlink(dest) == str(src)
    assert json.loads(dest.read_text())["name"] == "personalclaw"


def test_seed_is_idempotent(seed_env):
    """Contract item 2: seeding twice changes nothing and reports it."""
    from personalclaw.acp.config_seed import seed_agent_config

    _src, cli_root = seed_env
    assert seed_agent_config("kiro-cli", cli_root)["status"] == "seeded"
    before = sorted(p.name for p in cli_root.iterdir())
    assert seed_agent_config("kiro-cli", cli_root)["status"] == "already_seeded"
    assert sorted(p.name for p in cli_root.iterdir()) == before


def test_unseed_removes_exactly_what_we_wrote(seed_env):
    """Contract item 4: disable leaves nothing of ours, and only ours."""
    from personalclaw.acp.config_seed import seed_agent_config, unseed_agent_config

    _src, cli_root = seed_env
    (cli_root / "my-own-agent.json").write_text('{"name": "mine"}', encoding="utf-8")
    seed_agent_config("kiro-cli", cli_root)

    assert unseed_agent_config("kiro-cli")["status"] == "unseeded"
    assert not (cli_root / "personalclaw.json").exists()
    assert (cli_root / "my-own-agent.json").read_text() == '{"name": "mine"}'


def test_user_owned_config_survives_untouched(seed_env):
    """Contract item 3: a file we did not write is never clobbered.

    The user hand-wrote their own ``personalclaw.json`` agent. Seeding must
    refuse, and unseeding must not delete it either.
    """
    from personalclaw.acp.config_seed import seed_agent_config, unseed_agent_config

    _src, cli_root = seed_env
    mine = cli_root / "personalclaw.json"
    mine.write_text('{"name": "personalclaw", "hand": "edited"}', encoding="utf-8")

    assert seed_agent_config("kiro-cli", cli_root)["status"] == "skipped_user_owned"
    assert json.loads(mine.read_text())["hand"] == "edited"
    assert not mine.is_symlink()

    assert unseed_agent_config("kiro-cli")["status"] == "not_seeded"
    assert json.loads(mine.read_text())["hand"] == "edited"


def test_a_replaced_seed_is_disowned_not_deleted(seed_env):
    """Contract item 4's edge: the user overwrote our link after we seeded."""
    from personalclaw.acp.config_seed import seed_agent_config, seed_status, unseed_agent_config

    _src, cli_root = seed_env
    seed_agent_config("kiro-cli", cli_root)
    dest = cli_root / "personalclaw.json"
    dest.unlink()
    dest.write_text('{"name": "personalclaw", "mine": true}', encoding="utf-8")

    assert seed_status("kiro-cli") == "diverged"
    assert unseed_agent_config("kiro-cli")["status"] == "skipped_diverged"
    assert json.loads(dest.read_text())["mine"] is True


def test_seed_and_unseed_are_sel_audited(seed_env, monkeypatch):
    """Contract item 5: every seed/unseed decision reaches the audit log."""
    from personalclaw.acp import config_seed as cs

    rows: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cs,
        "_audit",
        lambda op, outcome, resources, error="": rows.append((op, outcome)),
    )
    _src, cli_root = seed_env
    cs.seed_agent_config("kiro-cli", cli_root)
    cs.unseed_agent_config("kiro-cli")
    assert ("seed", "completed") in rows
    assert ("unseed", "completed") in rows


def test_sel_event_carries_the_seed_type(seed_env):
    """The real SEL row, not a stub: type/operation/outcome are readable."""
    from personalclaw.acp.config_seed import _audit

    logged: list = []

    import personalclaw.sel as sel_mod

    class _Sel:
        def log(self, event):
            logged.append(event)

    orig = sel_mod.sel
    sel_mod.sel = lambda: _Sel()  # type: ignore[assignment]
    try:
        _audit("seed", "completed", "provider=kiro-cli dest=/x")
    finally:
        sel_mod.sel = orig  # type: ignore[assignment]

    assert len(logged) == 1
    assert logged[0].event_type == "acp_config_seed"
    assert logged[0].operation == "seed" and logged[0].outcome == "completed"


def test_seed_refuses_without_a_generated_config(home):
    """Nothing to point at — never create a dangling link into our home."""
    from personalclaw.acp.config_seed import seed_agent_config

    cli_root = home.parent / "empty-root"
    cli_root.mkdir()
    assert seed_agent_config("kiro-cli", cli_root)["status"] == "skipped_no_source"
    assert not (cli_root / "personalclaw.json").exists()


# ── prong B: the bundle seam stays provider-agnostic ───────────────────────


def test_registration_seeds_only_when_the_bundle_declares_a_dir(seed_env, monkeypatch):
    """Which directory a CLI reads is vendor knowledge — it comes from the bundle.

    A CLI that honours protocol ``mcpServers`` declares nothing and nothing is
    seeded; core never names a CLI's config root.
    """
    from personalclaw.acp_bundles import _register

    calls: list[tuple] = []
    monkeypatch.setattr(
        _register,
        "get_default_registry",
        lambda: type(
            "R",
            (),
            {"unregister_entry": lambda self, n: None, "register_entry": lambda self, e: None},
        )(),
    )
    import personalclaw.acp.config_seed as cs

    monkeypatch.setattr(
        cs, "seed_agent_config", lambda cli, d: calls.append((cli, str(d))) or {"status": "seeded"}
    )

    _src, cli_root = seed_env
    _register.register_acp_cli_entry(cli="honours-protocol", dialect="default", command=["x"])
    assert calls == []

    _register.register_acp_cli_entry(
        cli="needs-seed", dialect="default", command=["x"], agent_config_dir=str(cli_root)
    )
    assert calls == [("needs-seed", str(cli_root))]


def test_unregister_reverses_the_seed(seed_env, monkeypatch):
    """Disable must reverse it — an enable/disable cycle leaves no residue."""
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
    _src, cli_root = seed_env
    _register.register_acp_cli_entry(
        cli="needs-seed", dialect="default", command=["x"], agent_config_dir=str(cli_root)
    )
    assert (cli_root / "personalclaw.json").is_symlink()

    _register.unregister_acp_cli_entry("needs-seed")
    assert not (cli_root / "personalclaw.json").exists()
