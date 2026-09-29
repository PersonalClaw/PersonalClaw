"""The agent's shell refuses PersonalClaw's credential store and its keys in the home in use.

The shell's screen (`security.is_sensitive_bash_command`) knew the credential store only as a
spelling of `~/.personalclaw/…`, so it knew it only where the default home keeps it. Every
documented container runs with `PERSONALCLAW_HOME=/data`, and there, measured through the Tools
page's Try it on the shipped image: `cut -d= -f1 /data/.env` listed the stored secrets' names and
`awk -F= 'NR==1{print substr($2,1,6)}' /data/.env` printed the first characters of a stored key,
while `~/.personalclaw/.env`, a file that did not exist there, was refused. The file tools refused
`/data/.env` all along: their path guard had learned the active home, and the shell's screen was a
second declaration of the same files that had not.

The default home was open too. The screen named two entries of it (`.env`, `governance`), so the
session signing key, the session table and the device and password files under `auth/` were read
by `cat` on every install; and the workspace sits inside the home, so from where the agent's tools
run `cat ../.env` read the store.

Each layer is driven here: the screen, the native `bash` tool and a bash action as they run a
command, the path guard the file tools share, and the OS sandbox's file masks.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from personalclaw import security
from personalclaw.security import is_sensitive_bash_command, is_sensitive_path

FAKE_KEY = "BSAfakeexamplekey0000"
OWN = "Blocked: command accesses PersonalClaw's own credential or audit key"

#: The home's secret entries the screen let through: every one but the three refused by name.
OPEN_ENTRIES = (
    ".env",
    "session_key",
    "sessions.json",
    "auth/credentials.json",
    "auth/pair_codes.json",
    "credentials/stored.json",
    "governance/ceiling.json",
)


def _populate(home: Path) -> None:
    (home / "workspace").mkdir(parents=True)
    (home / ".env").write_text(f"BRAVE_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    for entry in OPEN_ENTRIES[1:]:
        (home / entry).parent.mkdir(parents=True, exist_ok=True)
        (home / entry).write_text("secret", encoding="utf-8")
    for name in ("sel_hmac.key", ".local_secret", "telemetry_salt"):
        (home / name).write_text("secret", encoding="utf-8")
    (home / "config.json").write_text("{}", encoding="utf-8")
    (home / "workspace" / "notes.md").write_text("notes", encoding="utf-8")


@pytest.fixture
def container(tmp_path, monkeypatch) -> Path:
    """A home that is not under `$HOME`, the way every documented container runs (`/data`)."""
    user = tmp_path / "user"
    user.mkdir()
    data = tmp_path / "data"
    _populate(data)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(data))
    return data


@pytest.fixture
def default_home(tmp_path, monkeypatch) -> Path:
    """The default home, `~/.personalclaw`, under a scratch `$HOME`."""
    user = tmp_path / "user"
    home = user / ".personalclaw"
    _populate(home)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    return home


# ── the screen ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    ["cut -d= -f1 {home}/.env", "awk -F= 'NR==1{{print substr($2,1,6)}}' {home}/.env"],
)
def test_the_container_repro_is_refused(command, container):
    """The two commands that read the stored key on the shipped container image."""
    assert is_sensitive_bash_command(command.format(home=container)) == OWN


@pytest.mark.parametrize("entry", OPEN_ENTRIES)
def test_every_secret_of_a_relocated_home_is_refused(entry, container):
    assert is_sensitive_bash_command(f"cat {container}/{entry}") == OWN
    assert is_sensitive_path(str(container / entry)), "the file tools' guard refuses it too"


@pytest.mark.parametrize(
    "command",
    [
        "cat $PERSONALCLAW_HOME/.env",
        "cat ${{PERSONALCLAW_HOME}}/.env",
        'cat "$PERSONALCLAW_HOME"/.env',
        "cat ../.env",
        "cd .. && cut -d= -f1 .env",
        "(cd .. && cat sessions.json)",
        "sh -c 'cd .. && cat session_key'",
        "cat {home}/.e*",
        "cat ../*",
        "cat {home}/{{.env,notes}}",
        "grep -a KEY {home}//./.env",
        "cat {home}/workspace/../.env",
        "cat {home}/AUTH/pair_codes.json",
        "python3 -c \"print(open('{home}/.env').read())\"",
        "node -e \"console.log(require('fs')"
        ".readFileSync(process.env.PERSONALCLAW_HOME+'/.env'))\"",
        "rm {home}/sel_hmac.key",
    ],
)
def test_every_spelling_of_the_store_is_refused(command, container):
    """However the command reaches the file: through a variable, from the workspace, after a
    `cd`, through a glob or a brace list, from a one-liner. A relative path is read from where the
    agent's tools run, the workspace inside the home."""
    cmd = command.format(home=container)
    assert is_sensitive_bash_command(cmd, cwd=container / "workspace") == OWN, cmd


def test_a_link_to_the_store_is_refused(container, tmp_path):
    link = tmp_path / "elsewhere"
    link.symlink_to(container)
    assert is_sensitive_bash_command(f"cat {link}/.env") == OWN


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.personalclaw/session_key",
        "cat $HOME/.personalclaw/sessions.json",
        "cat ~/.personalclaw/auth/pair_codes.json",
        "cat ~/.personalclaw/credentials/stored.json",
        "cat ../session_key",
        "cd ~/.personalclaw && cat sessions.json",
        "node -e \"require('fs').readFileSync(process.env.HOME+'/.personalclaw/session_key')\"",
    ],
)
def test_the_default_home_is_covered_the_same_way(command, default_home):
    """The default home had two of its entries on the screen's list, spelled from `$HOME`."""
    assert is_sensitive_bash_command(command, cwd=default_home / "workspace") == OWN, command


def test_the_default_home_stays_refused_while_another_home_is_in_use(container, tmp_path):
    """A dev gateway on `.dev-home` runs beside the owner's own `~/.personalclaw`, which holds
    real keys: an agent of the first must not read the second's."""
    own = tmp_path / "user" / ".personalclaw"
    own.mkdir(parents=True)
    (own / "session_key").write_text("the owner's own", encoding="utf-8")
    assert is_sensitive_bash_command("cat ~/.personalclaw/session_key") == OWN
    assert is_sensitive_path(str(own / "session_key"))
    assert is_sensitive_path(str(own / "auth" / "credentials.json"))


@pytest.mark.parametrize(
    "command",
    [
        "ls {home}",
        "ls -la ..",
        "cat {home}/config.json",
        "cat ../config.json",
        "cat notes.md",
        "cat .env",
        "grep -r TODO .",
        "ls ~/.personalclaw",
        "cd /tmp && cat .env",
        "cat /tmp/some-project/.env",
        "cat /tmp/some-project/sessions.json",
        "echo session_key",
        "git log --grep auth",
    ],
)
def test_ordinary_work_in_and_around_the_home_passes(command, container):
    """The vacuity half. The home is browsable and so is everything in it but its secrets; a
    project's own `.env` and a word that happens to be a secret's name are nobody's secret."""
    cmd = command.format(home=container)
    assert is_sensitive_bash_command(cmd, cwd=container / "workspace") is None, cmd


def test_the_screen_resolves_the_home_without_making_it(tmp_path, monkeypatch):
    """A read-only screen must not create the home it asks about."""
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "not-yet"))
    (tmp_path / "user").mkdir()
    assert is_sensitive_bash_command("cat ../.env") == OWN
    assert not (tmp_path / "not-yet").exists()


# ── the tools, as they run a command ────────────────────────────────────────────────────────────


class _Spawns:
    """Counts what reached the spawn, and runs it, so a leak shows as what it printed."""

    def __init__(self, real):
        self.calls: list[tuple] = []
        self._real = real

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return await self._real(*args, **kwargs)


@pytest.mark.parametrize("command", ["cat ../.env", "awk -F= '{{print $2}}' {home}/.env"])
def test_the_native_shell_refuses_the_store_before_it_runs(command, container, monkeypatch):
    from personalclaw import sandbox
    from personalclaw.agents.native import builtin_tools as bt

    spawns = _Spawns(sandbox.create_subprocess_limited)
    monkeypatch.setattr(sandbox, "create_subprocess_limited", spawns)
    provider = bt.NativeBuiltinToolProvider(
        cwd=container / "workspace", categories=bt.PLATFORM_CATEGORIES
    )
    result = asyncio.run(provider._t_bash({"command": command.format(home=container)}))
    assert FAKE_KEY not in (result.output or "") + (result.error or ""), "the store was read"
    assert not result.success
    assert result.error == OWN
    assert spawns.calls == [], "a refused command must not reach the spawn"


def test_a_bash_action_refuses_the_store_from_the_folder_it_runs_in(container, monkeypatch):
    """A bash action runs in the gateway's folder; relative to it, `.env` is the store when the
    gateway was started in its home."""
    from personalclaw.action_providers import bash_provider
    from personalclaw.action_providers.base import ActionContext

    monkeypatch.chdir(container)
    result = asyncio.run(
        bash_provider.BashActionProvider().execute(
            {"command": "cat .env", "timeout": 5},
            ActionContext(event="test", context={}, payload={}),
            timeout=5,
        )
    )
    assert FAKE_KEY not in (result.stdout or "")
    assert not result.success
    assert result.error == OWN


def test_an_agent_cli_call_reads_a_relative_path_from_its_own_folder(container, tmp_path):
    """The permission hook an agent CLI's calls pass is told the session's folder when it is
    known: from a project, `../.env` is the project's parent's file, and from the home's workspace
    it is the store."""
    from personalclaw.hooks import HookManager

    project = tmp_path / "src" / "app"
    project.mkdir(parents=True)
    hooks = HookManager()
    assert hooks.on_tool_call("Running: cat ../.env", cwd=project).action != "deny"
    assert hooks.on_tool_call("Running: cat ../.env").reason == OWN
    assert hooks.on_tool_call(f"Running: cat {container}/.env", cwd=project).reason == OWN


@pytest.mark.asyncio
async def test_a_chat_tells_the_hook_the_folder_its_agent_cli_runs_in(tmp_path):
    """A chat's agent CLI session runs in the chat's workspace folder, and the hook is told so
    for the command it asks about and for the call's title."""
    from test_acp_permission_authority import (
        _context_builder,
        _drive,
        _make_state,
        _session,
        _set_stream,
    )

    from personalclaw.hooks import ToolHookResult
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

    project = tmp_path / "src" / "app"
    project.mkdir(parents=True)
    builder = _context_builder(on_tool_call=lambda title, **_kw: ToolHookResult.deny("screened"))
    state, client = _make_state(tmp_path, context_builder=builder)
    session = _session()
    session.workspace_dir = str(project)
    ask = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="cat ../.env",
        tool_kind="execute",
        request_id="req-1",
        tool_input='{"command": "cat ../.env"}',
    )
    _set_stream(client, [ask, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    await _drive(state, session)

    calls = builder.hooks.on_tool_call.call_args_list
    assert calls, "the hook was asked about the call"
    assert {call.kwargs.get("cwd") for call in calls} == {project.resolve()}


# ── the OS sandbox's masks ─────────────────────────────────────────────────────────────────────


def test_the_sandbox_hides_the_store_of_the_home_in_use(container):
    """At the levels that hide single files (`cc`, `strict`), the credential store's file is found
    from the home in use. It was `~/.personalclaw/.env`, which does not exist on a container."""
    from personalclaw.sandbox import _build_launcher_script, _build_seatbelt_profile

    store = os.path.realpath(container / ".env")
    for level in ("cc", "strict"):
        assert f'(deny file-read* (literal "{store}"))' in _build_seatbelt_profile(level), level
        assert f'"{store}"' in _build_launcher_script(level), level


def test_the_sandbox_hides_the_default_homes_store_beside_another(container, tmp_path):
    from personalclaw.sandbox import _hidden_files

    default_store = str(tmp_path / "user" / ".personalclaw" / ".env")
    assert default_store in _hidden_files(str(tmp_path / "user"), "strict", realpath_only=False)


def test_the_standard_sandbox_level_still_hides_no_single_file(container):
    """Unchanged, and documented: the native shell's own level hides no file, so the screen above
    is what refuses the store there."""
    from personalclaw.sandbox import _build_seatbelt_profile

    assert "(deny file-read* (literal" not in _build_seatbelt_profile("standard")


def test_every_guard_reads_one_list_of_homes():
    """The shell's screen, the path guard and the sandbox all read `_pclaw_homes`: one answer to
    "which homes' secrets are refused", so none of them can drift back to the default alone."""
    assert security.credential_store_paths() == [
        os.path.join(home, security.CREDENTIAL_STORE_FILE) for home in security._pclaw_homes()
    ]
