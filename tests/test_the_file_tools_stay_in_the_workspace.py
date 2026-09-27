"""Every file tool stays inside the places the Files view reaches.

🔴 THE DEFECT (measured on ``origin/main``). The native file tools confined a path they were
handed to the session's folder, and nothing else:

* ``glob`` and ``grep`` never resolved what they matched. A pattern with ``..`` listed and searched
  files outside the workspace, and so did a link inside it that leads out.
* No file tool refused a credential file. A code worker whose folder is the owner's home read
  ``~/.ssh`` and wrote ``~/.aws``, and in any workspace ``read_file`` returned a ``.env`` and
  ``grep`` printed its lines, all of which the Files view refuses.
* ``list_dir`` and ``repo_map`` named and mapped files through links that lead out.
* ``code_map`` indexed whatever folder its ``workspace`` argument named, and its default was the
  global workspace rather than the session's.

The contract now: every file tool resolves through the check the Files view and
``/api/file-read`` make (`file_roots.admit`): symlinks and ``..`` resolved, the PersonalClaw home
reached only through a root inside it, no credential location, PersonalClaw's own keys, ``.env``,
``*.key``, ``*.pem`` or ``*.secret``. A listing or a search leaves out what it could not open.

Each tool is driven through the provider the gateway builds for a session.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw.agents.native.builtin_tools import PLATFORM_CATEGORIES, NativeBuiltinToolProvider

pytestmark = pytest.mark.asyncio

_TOKEN = "planted-outside-token-7f3a"


@pytest.fixture
def place(tmp_path, monkeypatch):
    """A workspace, a folder beside it holding things the tools must not reach, and an owner's
    home with a key in it. Real paths throughout, so every comparison is of what the OS sees."""
    root = Path(os.path.realpath(tmp_path))
    home = root / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text(f"an ssh key {_TOKEN}\n")
    monkeypatch.setenv("HOME", str(home))
    ws = root / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "app.py").write_text("def serve():\n    return 'ok'\n")
    (ws / ".env").write_text(f"API_TOKEN={_TOKEN}\n")
    (ws / "server.pem").write_text(f"a certificate {_TOKEN}\n")
    outside = root / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text(f"token {_TOKEN}\n")
    (outside / "stolen.py").write_text("def exfiltrate():\n    return 'token'\n")
    (ws / "linked-notes.txt").symlink_to(outside / "notes.txt")
    (ws / "linked.py").symlink_to(outside / "stolen.py")
    (ws / "linked-dir").symlink_to(outside, target_is_directory=True)
    return {"ws": ws, "outside": outside, "home": home}


def _tools(cwd: Path) -> NativeBuiltinToolProvider:
    """The platform provider as `provider_bridge._build_native_runtime` builds it."""
    return NativeBuiltinToolProvider(cwd=cwd, categories=PLATFORM_CATEGORIES)


async def _call(tools, name: str, **arguments):
    return await tools.invoke(name, arguments)


# ── glob ──


async def test_glob_does_not_climb_out_of_the_workspace(place):
    result = await _call(_tools(place["ws"]), "glob", pattern="../outside/*")
    assert not result.success
    assert "notes.txt" not in (result.output or "")
    assert "leaves the workspace" in (result.error or "")


async def test_glob_lists_nothing_a_link_leads_out_to_or_a_secret_file(place):
    result = await _call(_tools(place["ws"]), "glob", pattern="**/*")
    listed = set((result.output or "").splitlines())
    assert "src/app.py" in listed, "the ordinary file is still listed"
    assert not listed & {"linked-notes.txt", "linked.py", ".env", "server.pem"}, listed
    through = await _call(_tools(place["ws"]), "glob", pattern="linked-dir/*")
    assert "notes.txt" not in (through.output or ""), through.output


# ── grep ──


async def test_grep_does_not_search_out_of_the_workspace(place):
    result = await _call(_tools(place["ws"]), "grep", query=_TOKEN, glob="../outside/*")
    assert not result.success
    assert _TOKEN not in (result.output or "")


async def test_grep_reads_no_secret_file_and_follows_no_link_out(place):
    result = await _call(_tools(place["ws"]), "grep", query=_TOKEN)
    assert _TOKEN not in (result.output or ""), result.output
    control = await _call(_tools(place["ws"]), "grep", query="def serve")
    assert "src/app.py:1" in (control.output or ""), "the ordinary file is still searched"


# ── list_dir ──


async def test_list_dir_names_no_secret_file_and_no_link_out(place):
    result = await _call(_tools(place["ws"]), "list_dir", path=".")
    listed = set((result.output or "").splitlines())
    assert "src/" in listed, "the ordinary folder is still listed"
    assert not listed & {".env", "server.pem", "linked-notes.txt", "linked.py", "linked-dir/"}


async def test_list_dir_does_not_open_the_owners_key_folder(place):
    """A code worker whose folder is the owner's home."""
    result = await _call(_tools(place["home"]), "list_dir", path=".ssh")
    assert not result.success
    assert "id_ed25519" not in (result.output or "")


# ── read_file / write_file / edit_file ──


async def test_read_file_refuses_a_key_and_a_dotenv(place):
    key = await _call(_tools(place["home"]), "read_file", path=".ssh/id_ed25519")
    dotenv = await _call(_tools(place["ws"]), "read_file", path=".env")
    for result in (key, dotenv):
        assert not result.success
        assert _TOKEN not in (result.output or "")
        assert "credential or secret file" in (result.error or "")
    control = await _call(_tools(place["ws"]), "read_file", path="src/app.py")
    assert control.success and "def serve" in control.output


async def test_write_file_refuses_a_credential_location(place):
    target = place["home"] / ".aws" / "credentials"
    result = await _call(
        _tools(place["home"]), "write_file", path=".aws/credentials", content="[default]\n"
    )
    assert not result.success
    assert not target.exists()


async def test_edit_file_refuses_a_secret_file(place):
    tools = _tools(place["ws"])
    await _call(tools, "read_file", path="server.pem")
    result = await _call(
        tools, "edit_file", path="server.pem", old_str="certificate", new_str="REPLACED"
    )
    assert not result.success
    assert "certificate" in (place["ws"] / "server.pem").read_text()


# ── repo_map ──


async def test_repo_map_maps_no_file_a_link_leads_out_to(place):
    result = await _call(_tools(place["ws"]), "repo_map")
    assert "src/app.py" in (result.output or "")
    assert "exfiltrate" not in (result.output or ""), result.output
    assert "linked.py" not in (result.output or "")


# ── code_map ──


async def _code_map(ws: Path, **arguments):
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context
    from personalclaw.tool_providers.code_map import CodeMapToolProvider

    tokens = bind_tool_context(cwd=ws)
    try:
        return await CodeMapToolProvider().invoke("code_map", arguments)
    finally:
        reset_tool_context(tokens)


async def test_code_map_indexes_no_folder_outside_the_session(place):
    result = await _code_map(place["ws"], symbol="exfiltrate", workspace=str(place["outside"]))
    assert not result.success
    assert "stolen.py" not in (result.output or "")
    assert "not a folder this session's file tools reach" in (result.error or "")


async def test_code_map_answers_for_the_sessions_folder(place):
    """Its default is the session's workspace, not the global one."""
    found = await _code_map(place["ws"], symbol="serve")
    assert found.success and "src/app.py" in found.output, found


async def test_code_map_indexes_no_file_a_link_leads_out_to(place):
    leaked = await _code_map(place["ws"], symbol="exfiltrate", workspace=str(place["ws"]))
    assert "linked.py" not in (leaked.output or ""), leaked.output
    control = await _code_map(place["ws"], symbol="serve", workspace=str(place["ws"]))
    assert control.success and "src/app.py" in control.output, control
