"""A project's secret files are refused by name, by every door the agent reads a folder through.

A project keeps its keys in files whose names say so: ``.env`` and its variants (``.env.local``,
``.env.production``, ``prod.env``), direnv's ``.envrc``, ``secrets.*``, ``credentials*.json``,
``service-account*.json``, a ``secrets`` data file (``secrets.json``, ``secrets.yaml``…, never a
source module such as ``secrets.py``), and the package tools' ``.npmrc``, ``.pypirc`` and
``.netrc``. One rule names them (``project_secrets.is_project_secret_name``), and every reader asks
it through the credential check (``security.SensitivePaths``): the file tools (``read_file``,
``grep``, ``glob``, ``list_dir``), the Files view (``file_roots.admit``) and the shell's screen
(``is_sensitive_bash_command``). A template of one (``.env.example``, ``.env.sample``,
``secrets.template.json``) holds no secret and still reads, and so does an ordinary file beside
them.

The file tools are driven through the provider the gateway builds for a session, over a real
workspace on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw import file_roots
from personalclaw.agents.native.builtin_tools import PLATFORM_CATEGORIES, NativeBuiltinToolProvider
from personalclaw.security import is_sensitive_bash_command, is_sensitive_path

pytestmark = pytest.mark.security

_TOKEN = "planted-project-secret-5d1c"

#: The secret files a project keeps, each named as projects name them.
SECRET_FILES = (
    ".env",
    ".env.local",
    ".env.production",
    ".envrc",
    "prod.env",
    "secrets.env",
    "secrets.json",
    "secrets.yaml",
    "credentials.json",
    "service-account.json",
    ".npmrc",
    ".pypirc",
    ".netrc",
)
#: Templates of them, and an ordinary file: each still reads.
READABLE = ("notes.txt", ".env.example", ".env.sample", "secrets.template.json")
#: A project's own source module that shares the name: code, which still reads and changes.
MODULE = "src/app/secrets.py"


@pytest.fixture
def ws(tmp_path, monkeypatch) -> Path:
    root = Path(os.path.realpath(tmp_path))
    monkeypatch.setenv("HOME", str(root / "home"))
    ws = root / "project"
    (ws / "config").mkdir(parents=True)
    for name in SECRET_FILES:
        (ws / name).write_text(f"TOKEN={_TOKEN}\n")
        (ws / "config" / name).write_text(f"TOKEN={_TOKEN}\n")
    for name in READABLE:
        (ws / name).write_text(f"an ordinary line in {name}\n")
    (ws / MODULE).parent.mkdir(parents=True)
    (ws / MODULE).write_text("def load_the_secrets():\n    return {}\n")
    return ws


def _tools(cwd: Path) -> NativeBuiltinToolProvider:
    """The platform provider as `provider_bridge._build_native_runtime` builds it."""
    return NativeBuiltinToolProvider(cwd=cwd, categories=PLATFORM_CATEGORIES)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SECRET_FILES)
async def test_read_file_refuses_each_one(ws, name) -> None:
    for path in (name, f"config/{name}"):
        result = await _tools(ws).invoke("read_file", {"path": path})
        assert not result.success, path
        assert _TOKEN not in (result.output or "")
        assert "credential or secret file" in (result.error or ""), result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("name", READABLE)
async def test_a_template_and_an_ordinary_file_still_read(ws, name) -> None:
    result = await _tools(ws).invoke("read_file", {"path": name})
    assert result.success, result.error
    assert "an ordinary line" in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SECRET_FILES)
async def test_write_file_refuses_each_one_too(ws, name) -> None:
    result = await _tools(ws).invoke("write_file", {"path": f"config/{name}", "content": "x"})
    assert not result.success
    assert "credential or secret file" in (result.error or ""), result.error
    assert (ws / "config" / name).read_text() == f"TOKEN={_TOKEN}\n"


@pytest.mark.asyncio
async def test_a_source_module_named_secrets_still_reads_greps_and_changes(ws) -> None:
    tools = _tools(ws)
    read = await tools.invoke("read_file", {"path": MODULE})
    assert read.success and "load_the_secrets" in read.output, read.error
    found = await tools.invoke("grep", {"query": "load_the_secrets"})
    assert "secrets.py" in (found.output or ""), found.output
    wrote = await tools.invoke("write_file", {"path": MODULE, "content": "VALUE = 1\n"})
    assert wrote.success, wrote.error


@pytest.mark.asyncio
async def test_grep_searches_none_of_them(ws) -> None:
    found = await _tools(ws).invoke("grep", {"query": _TOKEN})
    assert _TOKEN not in (found.output or ""), found.output
    control = await _tools(ws).invoke("grep", {"query": "an ordinary line"})
    assert "notes.txt" in (control.output or ""), "the ordinary file is still searched"


@pytest.mark.asyncio
async def test_glob_and_list_dir_name_none_of_them(ws) -> None:
    tools = _tools(ws)
    listed = set()
    for pattern in ("**/*", "*", ".*", "config/*", "config/.*"):
        result = await tools.invoke("glob", {"pattern": pattern})
        listed |= {Path(line).name for line in (result.output or "").splitlines()}
    for path in (".", "config"):
        result = await tools.invoke("list_dir", {"path": path})
        listed |= {line.rstrip("/") for line in (result.output or "").splitlines()}
    assert not listed & set(SECRET_FILES), sorted(listed & set(SECRET_FILES))
    assert "notes.txt" in listed and ".env.example" in listed, sorted(listed)


@pytest.mark.parametrize("name", SECRET_FILES)
def test_the_files_view_and_the_credential_check_refuse_each_one(ws, name) -> None:
    for path in (ws / name, ws / "config" / name):
        assert file_roots.admit(str(path), [str(ws)]) is None, path
        assert is_sensitive_path(str(path)), path
    assert file_roots.admit(str(ws / "notes.txt"), [str(ws)]) is not None


@pytest.mark.parametrize("name", SECRET_FILES)
def test_the_shell_screen_refuses_a_command_that_names_one(ws, name) -> None:
    for command in (f"cat {name}", f"grep TOKEN config/{name}", f"cp {name} /tmp/copied"):
        assert is_sensitive_bash_command(command, cwd=ws), command


def test_the_shell_screen_still_runs_a_command_on_an_ordinary_file(ws) -> None:
    for command in ("cat notes.txt", "cp .env.example .env.example.bak", "ls -la", "grep x *.txt"):
        assert is_sensitive_bash_command(command, cwd=ws) is None, command
