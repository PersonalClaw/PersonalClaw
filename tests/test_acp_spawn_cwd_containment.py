"""G39 rails — the cwd that reaches a spawned ACP CLI must stay inside the CONFIGURED workspace.

These assert the SPAWN KWARGS: the ``cwd=`` value handed to the sandbox handle's ``exec``,
which is literally what ``asyncio.create_subprocess_exec`` receives. That seam is the point,
not a resolver's return value — the defect these rails exist for was a per-session working
directory that the resolvers got right and the spawn path then ignored, so a unit test over a
helper passed while a profile-bound session ran the CLI in the operator's REAL home
(``~/.personalclaw/workspace``), defeating PERSONALCLAW_HOME, dev homes and test fixtures.

Every test pins BOTH ``PERSONALCLAW_HOME`` and ``PERSONALCLAW_WORKSPACE`` under ``tmp_path``;
the containment rail then reds on any spawn cwd outside that workspace root.
"""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

import personalclaw
from personalclaw.config.loader import (
    AgentProfile,
    AppConfig,
    resolve_session_workspace,
    workspace_root,
)
from personalclaw.llm.acp_agent import _factory
from personalclaw.llm.registry import ProviderEntry
from personalclaw.sandbox_providers.none import _NoneHandle


class _SpawnReached(Exception):
    """Raised from the stubbed ``exec`` so no real process is ever created."""


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """An isolated config home AND workspace — the two env rails a real spawn must honor."""
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    return ws


def _provider(*, cwd: str | None = None):
    """Build an ACP provider through the PRODUCTION factory (what ``registry.build`` calls)."""
    entry = ProviderEntry(
        name="acp:test-cli",
        type="acp_agent",
        model="",
        options={"command": ["/bin/echo", "acp"], "sandbox_mode": "none"},
    )
    kwargs: dict[str, object] = {"agent": "a-profile"}
    if cwd is not None:
        kwargs["cwd"] = cwd
    return _factory(entry=entry, session_key="g39", **kwargs)


async def _spawn_cwd(provider) -> str:
    """The ``cwd=`` the spawn hands to the sandbox handle's ``exec`` — the process's real cwd."""
    seen: dict[str, object] = {}

    async def _exec(self, **kwargs):  # noqa: ANN001, ANN003
        seen.update(kwargs)
        raise _SpawnReached()

    with patch.object(_NoneHandle, "exec", _exec):
        with pytest.raises(_SpawnReached):
            await provider._client._transport.spawn()
    # Vacuity guard: a rail that captured nothing would pass every assertion below.
    assert seen, "spawn never reached exec — the capture seam moved, the rail is vacuous"
    assert "cwd" in seen, f"spawn kwargs carry no cwd: {sorted(seen)}"
    return str(seen["cwd"])


@pytest.mark.asyncio
async def test_spawn_cwd_is_the_per_session_workspace(isolated_home):
    """An explicitly bound per-session workspace reaches the process as ``cwd=``."""
    bound = isolated_home / "bound"
    bound.mkdir()
    assert await _spawn_cwd(_provider(cwd=str(bound))) == str(bound)


@pytest.mark.asyncio
async def test_unbound_spawn_cwd_stays_inside_the_configured_workspace(isolated_home):
    """With NO per-session workspace, the fallback is the configured root — never a real home."""
    cwd = Path(await _spawn_cwd(_provider()))
    assert cwd.is_relative_to(workspace_root()), f"{cwd} escaped {workspace_root()}"
    assert not cwd.is_relative_to(Path.home() / ".personalclaw"), f"{cwd} is in the real home"


@pytest.mark.asyncio
async def test_empty_default_dir_never_relocates_a_bound_session(isolated_home):
    """A profile that declared NO ``default_dir`` inherits — the bound session cwd survives
    all the way to the spawn kwargs, and stays inside the configured workspace."""
    bound = isolated_home / "bound"
    bound.mkdir()
    cfg = AppConfig.load()
    cfg.agents = {"inheriting": AgentProfile()}

    resolved = resolve_session_workspace(cfg, "inheriting", str(bound))
    cwd = Path(await _spawn_cwd(_provider(cwd=resolved)))

    assert cwd == bound
    assert cwd.is_relative_to(workspace_root()), f"{cwd} escaped {workspace_root()}"
    assert not cwd.is_relative_to(Path.home() / ".personalclaw"), f"{cwd} is in the real home"


def test_non_empty_default_dir_still_wins(isolated_home):
    """The other half of the contract: a profile's DECLARED directory is still its opinion."""
    opinion = isolated_home / "opinion"
    cfg = AppConfig.load()
    cfg.agents = {"opinionated": AgentProfile(default_dir=str(opinion))}
    assert resolve_session_workspace(cfg, "opinionated", str(isolated_home / "bound")) == str(
        opinion
    )


def test_empty_default_dir_with_no_session_binding_falls_back_to_the_workspace(isolated_home):
    """ "Empty inherits the workspace root" — with nothing to inherit, that root is the answer."""
    cfg = AppConfig.load()
    cfg.agents = {"inheriting": AgentProfile()}
    assert resolve_session_workspace(cfg, "inheriting", "") == str(workspace_root())


# Every module that can spawn an ACP backend. ``discover_agents`` (llm/acp_agent.py) is
# the reason this is a STATIC rail rather than a driven one: it spawns a real CLI, so the
# only cheap way to keep its cwd honest is to forbid the construct that broke it.
_ACP_SPAWN_MODULES = (
    "personalclaw/acp/client.py",
    "personalclaw/acp/session.py",
    "personalclaw/acp/transport.py",
    "personalclaw/acp/connection_pool.py",
    "personalclaw/llm/acp_agent.py",
    "personalclaw/llm/acp_session_provider.py",
)


def _home_call_lines(path: Path) -> list[int]:
    """Lines calling ``Path.home()``. AST, not grep: a prose mention of the banned literal
    in a comment or docstring must not red the rail, and a real call must not hide in one."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "home"
    ]


def test_the_agent_bind_path_resolves_the_workspace_through_the_contract():
    """`G39`'s CALL SITE, not just its resolver.

    The three contract tests above drive ``resolve_session_workspace`` directly, so they stay green
    even if a caller stops using it — measured: replacing the agent-bind assignment with
    ``resolve_agent_bindings(cfg, matched).workspace_dir`` — which IS the G39 bug: it collapses
    "inherit" to a concrete path and relocates a session the user bound elsewhere — leaves this
    file at 6 passed. A fix whose USE is unrailed can be reverted silently, which is how G39 got
    written in the first place.

    Asserted at source level because the alternative is standing up a live chat session and a
    profile just to read one assignment back; the string this checks IS the seam.
    """
    src = Path(personalclaw.__file__).resolve().parent
    handlers = src / "dashboard" / "chat_handlers.py"
    assert handlers.is_file(), "chat_handlers.py moved — this rail no longer covers the bind path"
    text = handlers.read_text(encoding="utf8")

    # Vacuity floor: the module must still contain the seam this rail is about.
    assert "resolve_session_workspace" in text, (
        "chat_handlers no longer references resolve_session_workspace at all — either the bind "
        "path moved (re-point this rail) or G39's fix was removed"
    )

    # Every assignment to a session's workspace_dir on the bind path must go through the contract.
    offenders = [
        (i + 1, line.strip())
        for i, line in enumerate(text.splitlines())
        if "workspace_dir" in line
        and "=" in line
        and "resolve_session_workspace" not in line
        and "resolve_agent_bindings" in line
    ]
    assert not offenders, (
        f"chat_handlers assigns a session workspace from resolve_agent_bindings at {offenders} — "
        "that collapses default_dir's INHERIT case to a concrete path and relocates a session the "
        "user bound elsewhere (`G39`). Use resolve_session_workspace(cfg, agent, current)."
    )


def test_no_acp_spawn_site_anchors_its_cwd_to_the_real_home():
    """An ACP spawn site must resolve its cwd through ``workspace_root()``, never
    ``Path.home()``. ``AcpConnection.spawn``/``AcpProcess.spawn`` MKDIR the cwd, so a
    real-home anchor here does not merely read the operator's home — it writes to it,
    from a gateway or a test that set PERSONALCLAW_HOME precisely to prevent that."""
    src = Path(personalclaw.__file__).resolve().parent.parent

    # Positive control: the detector must actually find a real ``Path.home()`` call.
    # ``config/loader.py`` legitimately has one (the platform workspace default), so an
    # empty result here means the detector is broken and every assertion below is vacuous.
    control = _home_call_lines(src / "personalclaw/config/loader.py")
    assert control, "detector found no Path.home() call in config/loader.py — rail is vacuous"

    scanned = 0
    for rel in _ACP_SPAWN_MODULES:
        path = src / rel
        assert path.is_file(), f"{rel} moved — this rail no longer covers it"
        scanned += 1
        assert not _home_call_lines(path), (
            f"{rel} calls Path.home() at line(s) {_home_call_lines(path)} — an ACP spawn cwd "
            f"must come from workspace_root() so PERSONALCLAW_HOME/dev homes are honored"
        )
    assert scanned == len(_ACP_SPAWN_MODULES)
