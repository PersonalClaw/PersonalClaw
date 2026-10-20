"""A skill's edit lands in the copy agents read.

An agent can keep its own copy of a skill in its own folder (``agents/<agent>/skills``), and that
copy is the one its turns load. The loader's update wrote the library's copy whatever copy it had
read, so an edit of the agent's own skill was refused, or landed in the library's copy of that
name, where the agent never sees it. The Skills page did the same from the other side: the row of
an agent's own skill opened, saved and deleted the library's skill of that name.

Pinned here, through the loader and through the Skills page's routes: the copy a loader resolves
is the copy it reads, edits and deletes; the agent's own copy is addressed with ``agent``; and a
skill in the folder other AI tools share, which PersonalClaw only reads, is never written.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.skills.loader import SkillsLoader, agent_skills_dir, skills_dir

AGENT = "researcher"
NAME = "source-check"
LIBRARY = "---\nname: source-check\ndescription: Check sources.\n---\n# Check\nCite one source.\n"
OWN = "---\nname: source-check\ndescription: Check sources.\n---\n# Check\nCite two sources.\n"


def _skill(root: Path, text: str) -> Path:
    folder = root / NAME
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    return folder / "SKILL.md"


@pytest.fixture
def copies() -> tuple[Path, Path]:
    """The library's copy and the agent's own copy of one skill."""
    return _skill(skills_dir(), LIBRARY), _skill(agent_skills_dir(AGENT), OWN)


def test_an_edit_through_an_agents_loader_lands_in_the_agents_own_copy(copies):
    library, own = copies
    loader = SkillsLoader(install_builtins=False, agent=AGENT)
    current = loader.skill_text(NAME)
    assert current == OWN

    assert loader.update_skill(NAME, current.replace("two", "three"), over=current)

    assert own.read_text(encoding="utf-8") == OWN.replace("two", "three")
    assert library.read_text(encoding="utf-8") == LIBRARY, "the library's copy was written"
    assert "three sources" in (loader.load_skill(NAME) or "")


def test_a_skill_in_the_shared_folder_is_never_written(tmp_path, monkeypatch):
    """The control: PersonalClaw reads the shared folder and never writes into it."""
    import personalclaw.skills.marketplace as mp

    shared = tmp_path / "shared-skills"
    target = _skill(shared, LIBRARY)
    monkeypatch.setattr(mp, "skill_discovery_paths", lambda: [skills_dir(), shared])
    loader = SkillsLoader(install_builtins=False)
    current = loader.skill_text(NAME)
    assert current == LIBRARY

    assert loader.update_skill(NAME, current + "More.\n", over=current) is False

    assert target.read_text(encoding="utf-8") == LIBRARY
    assert not (skills_dir() / NAME).exists(), "an edit of a shared skill made a library copy"


# ── the Skills page ──────────────────────────────────────────────────────────────────────────


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import api_skill_detail
    from personalclaw.dashboard.handlers import skills as skills_h

    app = web.Application()
    app["state"] = SimpleNamespace(
        context_builder=SimpleNamespace(skills=SkillsLoader(install_builtins=False))
    )
    app.router.add_get("/api/skills/{name}/files", skills_h.api_skill_files)
    app.router.add_post("/api/skills/{name}/verify", skills_h.api_skill_verify)
    app.router.add_delete("/api/skills/{name}", skills_h.api_skills_delete)
    app.router.add_get("/api/skills/{name:.+}", api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", api_skill_detail)
    return app


@pytest.mark.asyncio
async def test_the_editor_of_an_agents_own_skill_reads_and_saves_that_copy(copies):
    library, own = copies
    async with TestClient(TestServer(_app())) as client:
        read = await (await client.get(f"/api/skills/{NAME}", params={"agent": AGENT})).json()
        assert read["content"] == OWN
        resp = await client.put(
            f"/api/skills/{NAME}",
            params={"agent": AGENT},
            json={"content": OWN.replace("two", "three")},
            headers={"If-Match": f'"{read["revision"]}"'},
        )
        assert resp.status == 200, await resp.text()

    assert own.read_text(encoding="utf-8") == OWN.replace("two", "three")
    assert library.read_text(encoding="utf-8") == LIBRARY


@pytest.mark.asyncio
async def test_the_files_of_an_agents_own_skill_are_that_copys(copies):
    library, own = copies
    (own.parent / "checklist.md").write_text("1. Date\n", encoding="utf-8")
    async with TestClient(TestServer(_app())) as client:
        tree = await (await client.get(f"/api/skills/{NAME}/files", params={"agent": AGENT})).json()
        library_tree = await (await client.get(f"/api/skills/{NAME}/files")).json()

    assert {f["path"] for f in tree["files"]} == {"SKILL.md", "checklist.md"}
    assert {f["path"] for f in library_tree["files"]} == {"SKILL.md"}


@pytest.mark.asyncio
async def test_deleting_an_agents_own_skill_leaves_the_librarys_skill_of_that_name(copies):
    library, own = copies
    async with TestClient(TestServer(_app())) as client:
        resp = await client.delete(f"/api/skills/{NAME}", params={"agent": AGENT})
        assert resp.status == 200, await resp.text()
        body = await resp.json()

    assert not own.parent.exists(), "the agent's own copy is still there"
    assert library.read_text(encoding="utf-8") == LIBRARY, "the library's skill was deleted"
    assert body["removed"] == str(own.parent)


@pytest.mark.asyncio
async def test_without_an_agent_the_routes_address_the_librarys_copy(copies):
    """The control: the library's row reads and deletes the library's copy."""
    library, own = copies
    async with TestClient(TestServer(_app())) as client:
        read = await (await client.get(f"/api/skills/{NAME}")).json()
        assert read["content"] == LIBRARY
        resp = await client.delete(f"/api/skills/{NAME}")
        assert resp.status == 200, await resp.text()

    assert not library.parent.exists()
    assert own.read_text(encoding="utf-8") == OWN


# ── what a delete may remove ─────────────────────────────────────────────────────────────────


def _auto_skills() -> list[Path]:
    """Two skills in the ``auto/`` folder, which holds skills and is not one itself."""
    out = []
    for name in ("first-draft", "second-draft"):
        folder = skills_dir() / "auto" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Drafted.\n---\n# {name}\n", encoding="utf-8"
        )
        out.append(folder / "SKILL.md")
    return out


@pytest.mark.asyncio
async def test_a_folder_that_holds_skills_is_not_a_skill_and_is_not_deleted():
    kept = _auto_skills()
    async with TestClient(TestServer(_app())) as client:
        resp = await client.delete("/api/skills/auto")
        assert resp.status == 404, await resp.text()
    assert all(path.is_file() for path in kept), "deleting a folder of skills removed them"


def test_the_command_line_removes_no_folder_that_holds_skills(capsys):
    import argparse

    from personalclaw.cli import _handle_skills

    kept = _auto_skills()
    with pytest.raises(SystemExit) as exited:
        _handle_skills(argparse.Namespace(skills_command="remove", name="auto"))
    assert exited.value.code == 1
    assert "not found" in capsys.readouterr().err
    assert all(path.is_file() for path in kept), "removing a folder of skills removed them"
