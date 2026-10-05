"""A delete of the owner's home folder, the filesystem root or the folder the work runs in always
asks a person, whatever grant stands, by every spelling the shell reads.

The shell denylist held two text patterns for this. They refused an ordinary recursive delete of a
build folder, and other spellings of a delete aimed at the home folder, the root or the chat's
working folder slipped past them (the home shorthand and variable, the current folder, a trailing
slash, flags in another order). Under Trust, YOLO or a chat's grant an agent's mistake could then
delete one of those folders without asking anyone.

Now the delete is read on the command as its shell reads it (``protected_folders``), and every gate
puts such a call to a person past every grant (``run_bounds``): the chat's own gate, the built-in
agent's, a background agent's and a one-shot's. Where nobody can be asked the call is refused, and
the run is told which folder. Any other delete follows the normal rules.

Every command here is decided and never run: the folders are under this test's own folder, and
each path that would run a command is driven with a stand-in that runs nothing.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_an_off_list_host_is_asked_past_every_grant import (
    _answer,
    _asked,
    _background,
    _events,
    _quiet_sel,
    _shell_ask,
    _state,
)
from test_an_unattended_run_stays_inside_its_bounds import _result
from test_an_unattended_run_stays_inside_its_bounds import _run as _native_turn

from personalclaw import run_bounds, trust_mode

#: The protected folders, as ``protected_folders`` names them.
HOME, ROOT, WORKING = "home", "root", "working"


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
    """The owner's home folder and a working folder inside it, both in this test's own folder, and
    the temporary folder a shell is handed. The process stays out of both."""
    home = tmp_path / "home"
    work = home / "work" / "app"
    work.mkdir(parents=True)
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setattr("tempfile.tempdir", str(temporary))
    return SimpleNamespace(home=home, work=work, tmp=temporary)


def _deleting(target: str, shape: str = "rm -rf {}") -> str:
    """The command a case decides: *shape* (``rm -rf {}`` unless the case says otherwise) around
    *target*, the path the delete aims at."""
    return shape.format(target)


def _target(target: str, folders: SimpleNamespace) -> str:
    """*target* with this test's folders filled in where it names them: ``{user}`` is this
    account's name, whose shorthand (``~name``) is the account's own home folder."""
    import getpass

    user = getpass.getuser()
    if "{user}" in target and os.path.expanduser(f"~{user}") == f"~{user}":
        pytest.skip("this account has no entry the shell's ~name could expand")
    return target.format(
        home=folders.home,
        home_holder=folders.home.parent,
        home_in_capitals=folders.home.parent / folders.home.name.upper(),
        work=folders.work,
        user=user,
    )


# ── The reading: which folder a delete would take ────────────────────────────────────────────────

#: (what the case covers, the target, the command's shape, the folder it deletes)
DELETES_A_PROTECTED_FOLDER = [
    ("home via the shorthand", "~", "rm -rf {}", HOME),
    ("home via the shorthand with a trailing slash", "~/", "rm -rf {}", HOME),
    ("home via the home variable", "$HOME", "rm -rf {}", HOME),
    ("home via the braced home variable", "${{HOME}}", "rm -rf {}", HOME),
    ("home via the quoted home variable", '"$HOME"', "rm -rf {}", HOME),
    ("home with repeated separators", "~//", "rm -rf {}", HOME),
    ("home via its own current folder", "~/.", "rm -rf {}", HOME),
    ("home via the parent of a folder inside it", "~/work/..", "rm -rf {}", HOME),
    ("home by its absolute path", "{home}", "rm -rf {}", HOME),
    ("home by its name in other letter case", "{home_in_capitals}", "rm -rf {}", HOME),
    ("home via the account's own shorthand", "~{user}", "rm -rf {}", HOME),
    ("a folder that holds the home folder", "{home_holder}", "rm -rf {}", HOME),
    ("everything in the home folder via a glob", "~/*", "rm -rf {}", HOME),
    ("the root", "/", "rm -rf {}", ROOT),
    ("the root with repeated separators", "//", "rm -rf {}", ROOT),
    ("the root with flags in another order", "/", "rm -fr {}", ROOT),
    ("the root with its flags apart", "/", "rm -r -f {}", ROOT),
    ("the root with long flags", "/", "rm --recursive --force {}", ROOT),
    ("the root with its flags after it", "/", "rm {} -rf", ROOT),
    ("everything on the root via a glob", "/*", "rm -rf {}", ROOT),
    ("working folder via the current folder", ".", "rm -rf {}", WORKING),
    ("working folder via the current folder with a trailing slash", "./", "rm -rf {}", WORKING),
    ("working folder by its absolute path", "{work}", "rm -rf {}", WORKING),
    ("working folder by its absolute path with a trailing slash", "{work}/", "rm -rf {}", WORKING),
    ("working folder via the parent folder and its own name", "../app", "rm -rf {}", WORKING),
    ("a folder holding the working folder via the parent folder", "..", "rm -rf {}", WORKING),
    ("everything in the working folder via a glob", "*", "rm -rf {}", WORKING),
    ("everything in the working folder via its current folder", "./*", "rm -rf {}", WORKING),
    ("the working folder's hidden files via a glob", ".*", "rm -rf {}", WORKING),
    ("home through sudo", "~", "sudo rm -rf {}", HOME),
    ("home through a shell's command string", "~", "sh -c 'rm -rf {}'", HOME),
    (
        "home via the home variable its own shell puts in a shell's command string",
        "$HOME",
        'sh -c "rm -rf {}"',
        HOME,
    ),
    ("home through eval", "~", "eval 'rm -rf {}'", HOME),
    ("home through env", "~", "env rm -rf {}", HOME),
    ("home in the second command of a line", "~", "ls && rm -rf {}", HOME),
    ("home via the current folder after a leading cd", ".", "cd ~ && rm -rf {}", HOME),
    ("the root via the current folder of env's folder", ".", "env -C / rm -rf {}", ROOT),
    ("home via find's delete", "~", "find {} -delete", HOME),
    ("home via a find that runs rm", "~", "find {} -maxdepth 0 -exec rm -rf '{{}}' +", HOME),
    ("home via rmdir", "~", "rmdir {}", HOME),
    ("home via a program named by its path", "~", "/bin/rm -rf {}", HOME),
]

#: (what the case covers, the target, the command's shape): a delete whose path cannot be told.
DELETES_A_PATH_IT_DOES_NOT_NAME = [
    ("a delete whose path is in a variable", '"$BUILD_DIR"', "rm -rf {}"),
    ("a delete of a folder under a variable", '"$OUT"/', "rm -rf {}"),
    ("a delete whose paths xargs hands it", "paths.txt", "xargs rm -rf < {}"),
    ("a delete in a shell's command string built from a variable", "$TARGET", 'sh -c "rm -rf {}"'),
    ("a delete in a line eval builds from a variable", "$TARGET", 'eval "rm -rf {}"'),
    ("a delete after the folder it runs in is lost", ".", "cd build || true; rm -rf {}"),
    ("a line the reader cannot split, run in the background", "~", "rm -rf {} &"),
    ("a line the reader cannot split, in a subshell", "*", "(cd / && rm -rf {})"),
    ("a line the reader cannot split, in a substitution", "~", "echo $(rm -rf {})"),
]

#: (what the case covers, the target, the command's shape): an ordinary delete, or none.
FOLLOWS_THE_NORMAL_RULES = [
    ("a build folder", "build", "rm -rf {}"),
    ("a build folder by its absolute path", "{work}/build", "rm -rf {}"),
    ("a build folder in the temporary folder", "$TMPDIR/build", "rm -rf {}"),
    ("a build folder through a shell's command string", "build", "sh -c 'rm -rf {}'"),
    ("a folder outside every protected folder", "/var/tmp/pclaw-example-scratch", "rm -rf {}"),
    ("a cache folder in the home folder", "~/.cache/example", "rm -rf {}"),
    ("log files in the home folder via a glob", "~/*.log", "rm -f {}"),
    ("a folder beside the working folder", "../other", "rm -rf {}"),
    ("a file in the working folder", "notes.txt", "rm -f {}"),
    ("a listing of the home folder", "~", "ls -la {}"),
    ("a line the reader cannot split that deletes nothing", "", "echo $(date){}"),
]


def _decided(target: str, shape: str, folders: SimpleNamespace):
    from personalclaw.protected_folders import protected_delete

    return protected_delete(_deleting(_target(target, folders), shape), cwd=str(folders.work))


@pytest.mark.parametrize(
    ("target", "shape", "kind"),
    [case[1:] for case in DELETES_A_PROTECTED_FOLDER],
    ids=[case[0] for case in DELETES_A_PROTECTED_FOLDER],
)
def test_a_delete_of_a_protected_folder_is_named(folders, target, shape, kind):
    from personalclaw import protected_folders

    found = _decided(target, shape, folders)

    assert kind in found.kinds, (target, shape, found)
    named = {
        HOME: "your home folder",
        ROOT: "the filesystem root",
        WORKING: "the working folder",
    }[kind]
    said = protected_folders.sentence(found)
    assert said.startswith("This would delete") and named in said, said


@pytest.mark.parametrize(
    ("target", "shape"),
    [case[1:] for case in DELETES_A_PATH_IT_DOES_NOT_NAME],
    ids=[case[0] for case in DELETES_A_PATH_IT_DOES_NOT_NAME],
)
def test_a_delete_whose_path_cannot_be_told_asks_too(folders, target, shape):
    from personalclaw import protected_folders

    found = _decided(target, shape, folders)

    assert found and found.unread and not found.hits, (target, shape, found)
    assert "does not name" in protected_folders.sentence(found)


@pytest.mark.parametrize(
    ("target", "shape"),
    [case[1:] for case in FOLLOWS_THE_NORMAL_RULES],
    ids=[case[0] for case in FOLLOWS_THE_NORMAL_RULES],
)
def test_any_other_command_follows_the_normal_rules(folders, target, shape):
    assert not _decided(target, shape, folders), (target, shape)


def test_the_shell_denylist_no_longer_refuses_an_ordinary_recursive_delete():
    """The text patterns are gone: a recursive delete of a build folder is no more a refusal than
    any other change, and the delete of a protected folder is decided on the command instead."""
    from personalclaw import security

    assert security.denied_command(_deleting("/tmp/build")) is None
    assert security.denied_command(_deleting("~/.cache/build")) is None
    baseline = security.baseline_denied_command_patterns()
    assert not [p for p in baseline if p.startswith("rm ")], "a text pattern for rm is left"


# ── The chat's gate ──────────────────────────────────────────────────────────────────────────────


def _chat(folders: SimpleNamespace, **posture):
    from personalclaw.dashboard.state import _ChatSession

    session = _ChatSession("chat-1-folders")
    session.workspace_dir = str(folders.work)
    for name, value in posture.items():
        setattr(session, name, value)
    return session


def _turn(client, command: str, *, native: bool) -> None:
    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask(command, native=native),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False], ids=["the built-in agent", "an agent CLI"])
async def test_trust_does_not_answer_a_delete_of_the_working_folder(tmp_path, folders, native):
    from personalclaw.dashboard.chat import run_chat

    state, client = _state(tmp_path)
    session = _chat(folders, _trust=True)
    _turn(client, _deleting("."), native=native)
    answering = _answer(session, "rejected")

    with _quiet_sel():
        await run_chat(state, session, "tidy up")
    await answering

    asked = _asked(session)
    assert len(asked) == 1, "Trust answered a delete of the working folder"
    reach = json.loads(asked[0]["cls"])["reach"]
    assert f"This would delete the working folder ({folders.work})." in reach
    assert "is always asked about" in reach
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
async def test_yolo_does_not_answer_a_delete_of_the_home_folder(tmp_path, folders):
    from personalclaw.dashboard.chat import run_chat

    state, client = _state(tmp_path)
    session = _chat(folders)
    trust_mode.enable_yolo(ttl_secs=600)
    _turn(client, _deleting("$HOME/", "rm -fr {}"), native=False)
    answering = _answer(session, "approved")

    with _quiet_sel():
        await run_chat(state, session, "tidy up")
    await answering

    asked = _asked(session)
    assert len(asked) == 1, "YOLO answered a delete of the home folder"
    assert "This would delete your home folder." in json.loads(asked[0]["cls"])["reach"]
    client.approve_tool.assert_called_once()  # the person's own Allow


@pytest.mark.asyncio
async def test_an_auto_approve_pattern_does_not_answer_a_delete_of_the_root(tmp_path, folders):
    from personalclaw.dashboard.chat import run_chat
    from personalclaw.hooks import ToolHookResult

    state, client = _state(tmp_path, hook=ToolHookResult.auto_approve())
    session = _chat(folders)
    _turn(client, _deleting("/", "rm -r -f {}"), native=False)
    answering = _answer(session, "rejected")

    with _quiet_sel():
        await run_chat(state, session, "tidy up")
    await answering

    assert len(_asked(session)) == 1, "an auto-approve pattern answered a delete of the root"
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
async def test_trust_still_answers_an_ordinary_delete_of_a_build_folder(tmp_path, folders):
    """A recursive delete by an absolute path, which the shell denylist's text pattern refused
    whatever it named, is the change Trust answers when it names no protected folder."""
    from personalclaw.dashboard.chat import run_chat

    state, client = _state(tmp_path)
    state.context_builder.hooks = None  # the gateway's own hook chain, its shell denylist included
    session = _chat(folders, _trust=True)
    _turn(client, _deleting(str(folders.tmp / "build")), native=False)

    with _quiet_sel():
        await run_chat(state, session, "tidy up")

    assert _asked(session) == []
    client.approve_tool.assert_called_once()


@pytest.mark.asyncio
async def test_an_unattended_turn_is_refused_a_delete_of_its_folder_and_told_which(
    tmp_path, folders
):
    from personalclaw.dashboard.chat import run_chat

    state, client = _state(tmp_path)
    session = _chat(folders, _trust=True, _unattended=True)
    _turn(client, _deleting("./"), native=False)

    with _quiet_sel():
        await run_chat(state, session, "tidy up")

    assert _asked(session) == []
    client.approve_tool.assert_not_called()
    client.reject_tool.assert_called_once()
    said = " ".join(m["content"] for m in session.messages if m.get("role") == "tool")
    assert "it would delete the working folder" in said


# ── The built-in agent's own gate ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_built_in_agent_asks_about_it_though_its_grant_answers_every_other_call(
    tmp_path,
):
    from personalclaw.llm.events import EVENT_PERMISSION_REQUEST

    events, invoked, _ = await _native_turn(
        tmp_path, "bash", {"command": _deleting(".", "rm -fr {}")}, unattended=False
    )

    asks = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert len(asks) == 1 and invoked == [], "the run's grant answered a delete of its folder"


@pytest.mark.asyncio
async def test_the_built_in_agent_runs_an_ordinary_delete_on_its_grant(tmp_path, folders):
    from personalclaw.llm.events import EVENT_PERMISSION_REQUEST

    events, invoked, _ = await _native_turn(
        tmp_path, "bash", {"command": _deleting(str(folders.tmp / "build"))}, unattended=False
    )

    assert not [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert [name for name, _ in invoked] == ["bash"]  # the stand-in runs nothing


@pytest.mark.asyncio
async def test_an_unattended_built_in_agent_is_refused_it_and_told_which_folder(tmp_path):
    from personalclaw.llm.events import TOOL_META_REFUSED_BY

    events, invoked, _ = await _native_turn(
        tmp_path, "bash", {"command": _deleting("~")}, unattended=True
    )

    assert invoked == []
    result = _result(events)
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
    assert "it would delete your home folder" in result.tool_output


# ── A background agent's and a one-shot's gate ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_background_agents_grant_does_not_answer_a_delete_of_the_home_folder():
    client, relayed = await _background(_shell_ask(_deleting("${HOME}"), native=False))

    relayed.assert_awaited_once()
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_one_shot_with_nobody_to_ask_refuses_a_delete_of_the_root():
    from personalclaw.llm_helpers import ToolApprovalPolicy, _resolve_permission

    provider = AsyncMock()

    approved = await _resolve_permission(
        provider,
        _shell_ask(_deleting("//", "rm -r -f {}"), native=True),
        ToolApprovalPolicy.AUTO_APPROVE,
        None,
    )

    assert approved is False
    provider.reject_tool.assert_awaited_once()
    provider.approve_tool.assert_not_awaited()


# ── What the card and a channel's prompt say ─────────────────────────────────────────────────────


def test_the_cards_line_names_the_folder_in_plain_words(folders):
    call = SimpleNamespace(
        title="bash", tool_kind="", risk_level="", tool_input={"command": _deleting("~")}
    )

    note = run_bounds.event_note(call, "dashboard:chat-1-folders", cwd=str(folders.work))

    assert note == (
        "This would delete your home folder. A command that deletes your home folder, the "
        "filesystem root or the working folder is always asked about, whatever this chat or its "
        "agent allows."
    )
    ordinary = SimpleNamespace(
        title="bash", tool_kind="", risk_level="", tool_input={"command": _deleting("build")}
    )
    assert run_bounds.event_note(ordinary, "dashboard:chat-1-folders", cwd=str(folders.work)) == ""


def test_allow_for_this_chat_says_such_a_delete_still_asks():
    from personalclaw.channel_delivery import ALLOW_FOR_THIS_CHAT

    assert (
        "deletes your home folder, the filesystem root or the working folder, still asks"
        in ALLOW_FOR_THIS_CHAT.promise
    )


# ── Where nobody is asked ────────────────────────────────────────────────────────────────────────


def test_unattended_work_is_refused_a_delete_of_its_folder_and_attended_work_is_asked(folders):
    from personalclaw.guardrails.denylist import check_command
    from personalclaw.guardrails.policy import unattended_dispatch_key

    where = str(folders.work)
    held = check_command(
        _deleting(".", "rm -r -f {}"), session_key=unattended_dispatch_key("loop_gate"), cwd=where
    )

    assert held.blocked and held.matched == "protected_folder:working"
    assert "it would delete the working folder" in held.refusal()
    ordinary = check_command(
        _deleting("build"), session_key=unattended_dispatch_key("loop_gate"), cwd=where
    )
    assert not ordinary.blocked
    # A chat you are in is not refused it here: its approval gate asks you instead.
    assert not check_command(_deleting("."), session_key="chat-1-folders", cwd=where).blocked


@pytest.mark.asyncio
async def test_a_loops_check_is_refused_a_delete_of_its_folder_before_anything_runs(folders):
    from personalclaw.loop.gates import CheckReport, run_verify_command

    report = CheckReport()
    ran = AsyncMock(side_effect=AssertionError("the check ran"))
    with patch("personalclaw.sandbox.create_subprocess_limited", ran):
        ok = await run_verify_command(
            _deleting(str(folders.work)), str(folders.work), report=report
        )

    assert ok is None
    assert "it would delete the working folder" in report.not_run
    ran.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bash_action_is_refused_a_delete_of_the_home_folder_before_anything_runs():
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.bash_provider import BashActionProvider

    ran = AsyncMock(side_effect=AssertionError("the action ran"))
    with patch("personalclaw.sandbox.create_subprocess_limited", ran):
        result = await BashActionProvider().execute(
            {"command": _deleting("~", "rm -fr {}")},
            ActionContext(event="test", context={}, payload={}),
            timeout=5,
        )

    assert result.success is False
    assert "it would delete your home folder" in (result.error or "")
    ran.assert_not_awaited()


def test_a_bash_action_that_deletes_a_protected_folder_is_refused_when_saved():
    from personalclaw.action_providers.bash_provider import config_problem

    assert config_problem({"command": _deleting("$HOME")}) == (
        "Its command would never run: it would delete your home folder."
    )
    assert config_problem({"command": _deleting("build")}) == ""


def test_an_apps_hook_is_refused_a_delete_of_its_own_folder_before_anything_runs(folders):
    from personalclaw.apps.app_manager import AppLifecycleError, _run_hook

    ran = MagicMock(side_effect=AssertionError("the hook ran"))
    with patch("subprocess.run", ran), pytest.raises(AppLifecycleError) as refused:
        _run_hook(_deleting("."), cwd=folders.work, timeout=5, env_name="onUninstall")

    assert "it would delete the working folder" in str(refused.value)
    ran.assert_not_called()
