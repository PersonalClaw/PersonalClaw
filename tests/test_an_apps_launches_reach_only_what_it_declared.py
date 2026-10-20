"""A program an app's code starts is audited where it reaches, and held to what the app declared.

An app's provider ran ``npx -y skills find`` and ``git clone https://…`` inside the gateway, and
the audit log had no record of either host: the egress rows came only from core's own fetches.
Now every launch an app's code makes is read for the hosts its command line names, each gets an
egress row naming the app, the program and the host, and a host neither the app's manifest
declares for that program nor the owner's allowed hosts list is refused before it starts.

The programs here do not exist (``/nonexistent/…``) and every host is a reserved name, so a launch
that is let through fails to start, and nothing is ever reached.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from personalclaw import app_code
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import load_bundle_module

APP = "launch-fixture"
HOST = "code.invalid"
GIT = "/nonexistent/pc-fixture/git"

_PROVIDER = """
import asyncio
import subprocess


def launch(argv):
    return subprocess.run(argv, capture_output=True, check=False)


def launch_shell(command):
    return subprocess.run(command, shell=True, capture_output=True, check=False)


async def launch_async(argv):
    proc = await asyncio.create_subprocess_exec(*argv)
    return await proc.wait()
"""


def _refused():
    """``pytest.raises`` for a launch the app's declarations and the allowed hosts refuse."""
    from personalclaw.apps.launch_egress import LaunchRefused

    return pytest.raises(LaunchRefused)


def _egress(*, allow=(), deny=()) -> None:
    from personalclaw.config.loader import config_dir

    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["egress"] = {
        "allow_hosts": list(allow),
        "deny_hosts": list(deny),
        "allow_private": False,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The fixture app's own code, loaded the way the gateway loads an app's provider."""
    folder = tmp_path / APP
    folder.mkdir()
    (folder / "provider.py").write_text(_PROVIDER, encoding="utf-8")
    module = load_bundle_module(folder, APP, "provider")
    yield module
    app_code.release(APP)


def _declare(monkeypatch, **launch) -> None:
    from personalclaw.providers.registry import get_provider_registry

    manifest = AppManifest.from_dict(
        {
            "name": APP,
            "version": "1.0.0",
            "displayName": "Launch fixture",
            "description": "Starts a program.",
            "launches": [launch],
            **({"dependencies": {"npmPackages": ["fixture-cli"]}} if launch.get("npm") else {}),
        }
    )
    monkeypatch.setitem(
        get_provider_registry()._extensions, APP, SimpleNamespace(manifest=manifest)
    )


def _rows(host: str) -> list[dict]:
    from personalclaw.sel import sel

    return [
        r
        for r in sel().recent(300)
        if r.get("operation") == "egress_launch" and host in r.get("resources", "")
    ]


def test_an_app_launch_that_reaches_a_listed_host_leaves_an_egress_row(app):
    _egress(allow=[HOST])

    with pytest.raises(FileNotFoundError):  # let through: the program itself is not there
        app.launch([GIT, "clone", "--depth", "1", f"https://{HOST}/owner/repo.git", "dest"])

    rows = _rows(HOST)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "allowed"
    assert rows[0]["caller_identity"] == f"app:{APP}"
    assert "git" in rows[0]["resources"]


def test_a_host_neither_declared_nor_allowed_is_refused_before_the_program_starts(app):
    _egress(allow=["127.0.0.1"])

    with _refused() as refused:
        app.launch([GIT, "clone", f"https://{HOST}/owner/repo.git"])

    said = str(refused.value)
    assert HOST in said and APP in said
    assert "Allowed hosts in Settings → Security → Network egress" in said
    assert [r["outcome"] for r in _rows(HOST)] == ["denied"]


def test_a_command_line_an_app_runs_through_a_shell_is_read_the_same_way(app):
    _egress()

    with _refused():
        app.launch_shell(f"/nonexistent/pc-fixture/curl -s https://{HOST}/x")

    assert [r["outcome"] for r in _rows(HOST)] == ["denied"]


@pytest.mark.asyncio
async def test_an_asyncio_launch_is_held_the_same_way(app):
    _egress()

    with _refused():
        await app.launch_async(["/nonexistent/pc-fixture/curl", "-s", f"https://{HOST}/x"])


def test_a_host_the_manifest_declares_for_the_program_is_reached(app, monkeypatch):
    _egress()
    _declare(monkeypatch, program="git", why="Fetches a skill.", hosts=[HOST])

    with pytest.raises(FileNotFoundError):
        app.launch([GIT, "clone", f"https://{HOST}/owner/repo.git"])

    assert [r["outcome"] for r in _rows(HOST)] == ["allowed"]
    # Declared for git, not for another program.
    with _refused():
        app.launch(["/nonexistent/pc-fixture/curl", f"https://{HOST}/"])


def test_the_refusal_is_said_in_its_own_words_where_the_failure_is_relayed(app):
    """An app that wraps the refusal in its own error still has the owner read why."""
    from personalclaw.providers.failure_copy import relayed_failure_copy

    _egress()
    with pytest.raises(RuntimeError) as wrapped:
        try:
            app.launch([GIT, "clone", f"https://{HOST}/owner/repo.git"])
        except Exception as exc:
            raise RuntimeError(f"clone failed: {exc}") from exc

    said = relayed_failure_copy(wrapped.value)
    assert said.startswith(
        f"PersonalClaw's network settings stopped the {APP} app from starting git"
    )
    assert HOST in said


def test_a_denied_host_is_refused_even_when_declared(app, monkeypatch):
    _egress(deny=[HOST])
    _declare(monkeypatch, program="git", why="Fetches a skill.", hosts=[HOST])

    with _refused():
        app.launch([GIT, "clone", f"https://{HOST}/owner/repo.git"])


def test_a_declared_npm_package_reaches_its_registry(app, monkeypatch):
    from personalclaw.apps.declared import declared_hosts

    _egress()
    _declare(monkeypatch, program="npx", why="Searches skills.", npm=True)

    assert "registry.npmjs.org" in declared_hosts(APP, "npx")
    assert declared_hosts(APP, "curl") == ()


def test_an_npx_entry_that_names_its_package_reaches_the_npm_registry(app, monkeypatch):
    """Install consent says npx fetches the package an npx entry names from the npm registry, so
    that registry is among what the entry declares, as a declared npm package's is."""
    from personalclaw.apps.declared import declared_hosts

    _egress()
    _declare(monkeypatch, program="npx", why="Searches skills.", npmPackage="fixture-cli")
    assert "registry.npmjs.org" in declared_hosts(APP, "npx")

    _declare(monkeypatch, program="npx", why="Searches skills.")
    assert "registry.npmjs.org" not in declared_hosts(APP, "npx")


def test_a_launch_that_names_no_host_is_audited_and_starts(app):
    _egress()

    with pytest.raises(FileNotFoundError):
        app.launch([GIT, "-C", "repo", "fetch", "origin", "main"])

    rows = _rows("a host its command does not name")
    assert rows and rows[0]["outcome"] == "allowed" and rows[0]["caller_identity"] == f"app:{APP}"


def _launch_rows() -> int:
    from personalclaw.sel import sel

    return len([r for r in sel().recent(300) if r.get("operation") == "egress_launch"])


def test_a_local_launch_is_not_an_egress(app):
    _egress()
    before = _launch_rows()

    with pytest.raises(FileNotFoundError):
        app.launch([GIT, "-C", "repo", "status"])

    assert _launch_rows() == before


def test_a_launch_core_makes_is_not_read_as_an_apps(app):
    _egress()

    with pytest.raises(FileNotFoundError):
        subprocess.run(["/nonexistent/pc-fixture/curl", f"https://{HOST}/"], check=False)

    assert _rows(HOST) == []
