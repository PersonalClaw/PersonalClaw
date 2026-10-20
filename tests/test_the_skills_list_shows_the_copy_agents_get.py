"""The Skills list describes the copy of each skill that agents get.

A skill's name can resolve in more than one folder: an agent's own, the home's library, and the
folder other AI tools share once the owner allows it. Agents get the first in that order
(``SkillsLoader.skill_file``). The Skills list walked its folders in alphabetical order of their
paths instead, and listed the package's own copy of each bundled skill, which no agent reads, so a
row could describe one copy while agents loaded another, and the inspector's file list and
re-verify read whichever folder sorted first.

Pinned here: the list, its file list and its re-verify all describe the copy the loader resolves,
and one agent's loader lists each skill once, its own copy where it has one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config.loader import config_dir
from personalclaw.skills.loader import SkillsLoader, agent_skills_dir, skills_dir

NAME = "trip-planner"


def _skill(root: Path, description: str, *extra: str) -> Path:
    folder = root / NAME
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {NAME}\ndescription: {description}\n---\n# Trips\n", encoding="utf-8"
    )
    for rel in extra:
        (folder / rel).write_text(f"{rel}\n", encoding="utf-8")
    return folder


@pytest.fixture
def shared(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The folder AI tools share, allowed, at a path that sorts before the home's library."""
    home = tmp_path_factory.mktemp("aaa-user")
    monkeypatch.setenv("HOME", str(home))
    path = config_dir() / "config.json"
    path.write_text(json.dumps({"security": {"outside_home": ["agent-skills"]}}), encoding="utf-8")
    folder = home / ".agents" / "skills"
    folder.mkdir(parents=True)
    assert str(folder) < str(skills_dir()), "the shared folder must sort first for this to measure"
    return folder


def _rows() -> list[dict]:
    from personalclaw.dashboard.handlers import skills as skills_h

    resp = asyncio.run(skills_h.api_skills_list(make_mocked_request("GET", "/api/skills")))
    assert resp.status == 200, resp.body
    return json.loads(resp.body.decode())


def _row(key: str) -> dict:
    rows = [r for r in _rows() if r["key"] == key]
    assert len(rows) == 1, f"{key} is listed {len(rows)} times"
    return rows[0]


def _files(name: str) -> set[str]:
    from personalclaw.dashboard.handlers import skills as skills_h

    req = make_mocked_request("GET", f"/api/skills/{name}/files", match_info={"name": name})
    resp = asyncio.run(skills_h.api_skill_files(req))
    assert resp.status == 200, resp.body
    return {f["path"] for f in json.loads(resp.body.decode())["files"]}


def test_a_skill_in_the_library_and_the_shared_folder_is_listed_as_the_librarys(shared):
    mine = _skill(skills_dir(), "My trips.", "packing-list.md")
    _skill(shared, "Someone else's trips.", "their-notes.md")
    loaded = SkillsLoader(install_builtins=False).skill_file(NAME)
    assert loaded == mine / "SKILL.md"

    row = _row(NAME)
    assert row["path"] == str(loaded)
    assert row["description"] == "My trips."
    assert row["source"] == "local"
    assert _files(NAME) == {"SKILL.md", "packing-list.md"}


def test_a_skill_only_in_the_shared_folder_is_listed_as_shared(shared):
    """The control: with no library copy, agents get the shared one, and the row says so."""
    theirs = _skill(shared, "Someone else's trips.")
    row = _row(NAME)
    assert row["path"] == str(theirs / "SKILL.md")
    assert row["source"] == "shared"


def test_a_bundled_skill_is_listed_as_the_copy_in_the_library():
    SkillsLoader()  # the start's sync puts the bundled skills in the library
    copy = skills_dir() / "grill" / "SKILL.md"
    text = copy.read_text(encoding="utf-8")
    copy.write_text(
        text.replace("description:", "description: My questions first.", 1), encoding="utf-8"
    )

    row = _row("grill")
    assert row["path"] == str(copy), "the row is a copy no agent reads"
    assert row["description"].startswith("My questions first.")
    assert row["source"] == "bundled"


def test_an_agents_loader_lists_each_skill_once_its_own_copy_where_it_has_one():
    _skill(skills_dir(), "My trips.")
    own = _skill(agent_skills_dir("planner"), "The planner's trips.")

    rows = [r for r in SkillsLoader(install_builtins=False, agent="planner").list_skills()]
    named = [r for r in rows if r["key"] == NAME]
    assert len(named) == 1, f"{NAME} is listed {len(named)} times"
    assert named[0]["path"] == str(own / "SKILL.md")
    assert named[0]["agent_local"] is True
