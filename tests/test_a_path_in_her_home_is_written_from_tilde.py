"""A path in the user's home reaches the agent written from ``~``, and the agent names it so.

Her gateway ran with its HOME at a long folder (``/Users/<account>/sandboxes/ada/home``), and her
answers cited a folder that is not hers: "I read /Users/<account>/Notes/Garden/Weekly/2026-W40.md",
though every tool call had read her own file and the request named her home correctly. The model
dropped the middle of the long path. The always-on rule told it to write every path absolute, so it
spelled out each ``~/Notes/…`` it had used itself, and the search tools showed it that same long
prefix on every hit; the link it made opened nothing.

Now the request says once what ``~`` is and asks for paths in the home from ``~``; every path in the
home that the tools and the request show is written from ``~``, a path elsewhere as it is, and the
tools take either form. The chat's file links open a ``~`` path (``web/src/ui/Markdown.tsx``).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.agents.native.builtin_tools import PLATFORM_CATEGORIES, NativeBuiltinToolProvider

_WEEK = "Notes/Garden/Weekly/2026-W40.md"


@pytest.fixture()
def her(tmp_path, monkeypatch):
    """Her home, with PersonalClaw's home inside it, her notes folder allowed, and a shared folder
    outside her home allowed too."""
    root = Path(os.path.realpath(tmp_path))
    user = root / "home" / "user"
    pc_home = user / ".personalclaw"
    workspace = pc_home / "workspace"
    workspace.mkdir(parents=True)
    week = user / _WEEK
    week.parent.mkdir(parents=True)
    week.write_text("# Week 40\n- Book the ferry\n", encoding="utf-8")
    shared = root / "srv" / "shared"
    shared.mkdir(parents=True)
    (shared / "plan.md").write_text("- Book the ferry\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    allowed = {"agent": {"subagent_cwd_allowed_roots": [str(user / "Notes"), str(shared)]}}
    (pc_home / "config.json").write_text(json.dumps(allowed), encoding="utf-8")
    return SimpleNamespace(user=user, pc=pc_home, ws=workspace, week=week, shared=shared)


# ── the one rule: in the home from ~, anywhere else as it is ─────────────────────────────────


def test_a_path_in_the_home_is_written_from_tilde_and_any_other_as_it_is(monkeypatch):
    from personalclaw.home_paths import from_home, home

    monkeypatch.setenv("HOME", "/home/user/live")
    assert home() == "/home/user/live"
    assert from_home(f"/home/user/live/{_WEEK}") == f"~/{_WEEK}"
    assert from_home(Path("/home/user/live/src/app")) == "~/src/app"
    assert from_home("/home/user/live") == "~"
    # A folder whose name only starts like the home's is not in it.
    assert from_home("/home/user/live-old/Notes/a.md") == "/home/user/live-old/Notes/a.md"
    assert from_home("/srv/app/main.py") == "/srv/app/main.py"
    # A path that is not absolute is already written the way its reader takes it.
    assert from_home(f"~/{_WEEK}") == f"~/{_WEEK}"
    assert from_home("Notes/a.md") == "Notes/a.md"


def test_the_home_is_the_one_the_gateway_runs_with_and_a_link_to_it_counts(monkeypatch, tmp_path):
    """``HOME`` as the shell reads ``~``; a path the tools resolved through the link is in it."""
    from personalclaw.home_paths import from_home, home

    real = Path(os.path.realpath(tmp_path)) / "disk" / "user"
    real.mkdir(parents=True)
    link = Path(os.path.realpath(tmp_path)) / "home-link"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    assert home() == str(link)
    assert from_home(link / "Notes" / "a.md") == "~/Notes/a.md"
    assert from_home(real / "Notes" / "a.md") == "~/Notes/a.md"


def test_a_home_at_the_filesystem_root_writes_nothing_from_tilde(monkeypatch):
    from personalclaw.context import _home_directory_line
    from personalclaw.home_paths import from_home, home

    monkeypatch.setenv("HOME", "/")
    assert home() == ""
    assert from_home("/etc/hosts") == "/etc/hosts"
    assert _home_directory_line() == ""


def _call(her, name: str, **arguments):
    tools = NativeBuiltinToolProvider(cwd=her.ws, categories=PLATFORM_CATEGORIES)
    return asyncio.run(tools.invoke(name, arguments))


# ── what the file tools show ─────────────────────────────────────────────────────────────────


def test_a_search_names_her_file_from_tilde_and_both_forms_open_it(her):
    """🔴 Before: every hit carried her home's whole prefix, the prefix the model then cut."""
    found = _call(her, "grep", query="ferry", path="~/Notes")
    assert found.success, found.error
    assert found.output.splitlines() == [f"~/{_WEEK}:2: - Book the ferry"]

    matched = _call(her, "glob", pattern="**/*.md", path=str(her.user / "Notes"))
    assert matched.success, matched.error
    assert matched.output == f"~/{_WEEK}"

    for path in (f"~/{_WEEK}", str(her.week)):
        read = _call(her, "read_file", path=path)
        assert read.success, (path, read.error)
        assert "Book the ferry" in read.output


def test_a_file_outside_her_home_keeps_its_absolute_path(her):
    found = _call(her, "grep", query="ferry", path=str(her.shared))
    assert found.success, found.error
    assert found.output.splitlines() == [f"{her.shared / 'plan.md'}:1: - Book the ferry"]


def test_a_file_in_the_workspace_is_still_named_from_the_workspace(her):
    (her.ws / "RESEARCH.md").write_text("ferry times\n", encoding="utf-8")
    assert _call(her, "glob", pattern="*.md").output == "RESEARCH.md"


def test_the_places_note_names_a_folder_she_typed_in_full_from_tilde(her):
    from personalclaw.file_scope import FileScope, places_note

    note = places_note(FileScope([her.ws]))
    assert "- ~/Notes: an allowed working directory." in note
    # A folder outside her home keeps its absolute path (the note fits a name to one line).
    assert f"\n- {str(her.shared)[:60]}" in note
    assert str(her.user) not in note


def test_a_note_in_a_watched_folder_is_named_from_tilde(her):
    from personalclaw.knowledge_providers.dir_source import note_path

    source = {
        "provider": "watched-dir",
        "enabled": True,
        "spec": {"path": str(her.week.parent)},
    }
    store = SimpleNamespace(get_source=lambda _id: source)
    item = {"source_id": "src-1", "guid": "2026-W40.md"}
    assert note_path(store, item) == f"~/{_WEEK}"


# ── what the request says ────────────────────────────────────────────────────────────────────


def test_the_request_names_her_home_once_and_asks_for_tilde_paths(her):
    """The home's absolute path is written once, in the line that says what ``~`` is; the always-on
    rule asks for a path in the home from ``~``, not spelled out."""
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    skills = her.pc / "skills"
    (skills / "weekly-review").mkdir(parents=True)
    (skills / "weekly-review" / "SKILL.md").write_text(
        "---\nname: weekly-review\ndescription: Plan the week from the notes\n---\n\n# Review\n",
        encoding="utf-8",
    )
    builder = ContextBuilder(
        memory=MemoryStore(workspace=her.pc / "memory"),
        skills=SkillsLoader(skills_path=skills, install_builtins=False),
    )
    ctx = builder.build_session_context(session_key="dashboard:chat-1", cwd=str(her.ws))

    assert ctx.count(str(her.user)) == 1, ctx
    assert f"[HOME DIRECTORY] ~ is the user's home folder, {her.user}." in ctx
    assert "You are operating in workspace (working directory): ~/.personalclaw/workspace\n" in ctx
    assert "(dir: `~/.personalclaw/skills/weekly-review`)" in ctx
    assert "ALWAYS use the absolute path" not in ctx
    assert "from `~` for a file in the user's home folder" in ctx


def test_a_memory_section_names_its_source_from_tilde(her):
    from personalclaw.memory import MemoryStore

    store = MemoryStore(workspace=her.ws)
    store.init()
    store.write_preferences("# User Preferences\n\n- Plain and short.\n")
    (block,) = store.render_markdown_context(prefs_cap=4_000, projects_cap=10, history_cap=10)
    assert "_[source: ~/.personalclaw/workspace/memory/preferences.md]_" in block
