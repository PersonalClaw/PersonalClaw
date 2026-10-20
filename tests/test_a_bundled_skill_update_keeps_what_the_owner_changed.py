"""A bundled skill's update replaces only a copy the owner left as it was installed.

PersonalClaw copies the skills it ships into the home's library, and agents read that copy. The
copy is the owner's to change: she edits a skill's instructions, adds a file beside them. When a
later version of PersonalClaw shipped a changed skill, its copy was replaced whenever the shipped
file was newer by its modification time, and the whole folder was deleted first, so every edit and
every added file went with it. The cleanup of skills no longer shipped deleted any folder that had
one of four names, which no released version ever shipped, so the only folder it could find was a
skill of the owner's own.

The rule these pin is content, never a clock:

* the sync records what it installed, a digest per file, in the copy's install record;
* a copy whose files are still exactly what was installed is replaced by the new version, whole;
* a copy the owner changed is kept, file for file, and the Skills list offers her the new version,
  which she takes or declines;
* a copy installed before records were kept is replaced only when it is, byte for byte, a version
  PersonalClaw shipped; any other copy is hers;
* a skill PersonalClaw no longer ships is removed only while it is the copy PersonalClaw installed,
  and a skill of the owner's own is never removed for its name.

The skills a test ships come from the project's ``skills`` folder (``PERSONALCLAW_PROJECT_DIR``),
which the sync reads before the package's own; every check goes through a loader as the gateway
builds one and through the Skills page's routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.skills.loader import SkillsLoader, skills_dir

NAME = "tidy-notes"
V1 = {
    "SKILL.md": "---\nname: tidy-notes\ndescription: Tidy meeting notes.\n---\n# Tidy notes\n"
    "Group the notes by topic.\n",
    "references/style.md": "Use short headings.\n",
    "references/old-layout.md": "The layout this skill used first.\n",
}
V2 = {
    "SKILL.md": "---\nname: tidy-notes\ndescription: Tidy meeting notes.\n---\n# Tidy notes\n"
    "Group the notes by topic, then by date.\n",
    "references/style.md": "Use short headings, in sentence case.\n",
}
HER_TEXT = V1["SKILL.md"].replace("by topic.", "by topic, and keep my action items on top.")


@pytest.fixture
def shipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where this test's PersonalClaw ships skills from: its project's ``skills`` folder."""
    project = tmp_path / "project"
    (project / "skills").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(project))
    return project / "skills"


def _ship(shipped: Path, name: str, files: dict[str, str], *, mtime: float | None = None) -> None:
    """What a release ships for *name*: exactly *files*, each stamped *mtime* when given."""
    folder = shipped / name
    if folder.exists():
        shutil.rmtree(folder)
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))


def _start() -> None:
    """What a start does: the gateway, an agent's tools and each command build a loader this way."""
    SkillsLoader()


def _copy(name: str) -> Path:
    return skills_dir() / name


def _files(folder: Path) -> dict[str, str]:
    """The skill's own files and their text, the install record left out."""
    return {
        p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != ".pclaw-lock.json"
    }


async def _listed(name: str) -> dict:
    from personalclaw.dashboard.handlers import skills as skills_h

    resp = await skills_h.api_skills_list(make_mocked_request("GET", "/api/skills"))
    assert resp.status == 200, resp.body
    rows = [r for r in json.loads(resp.body.decode()) if r["key"] == name]
    assert len(rows) == 1, f"{name} is listed {len(rows)} times"
    return rows[0]


def _row(name: str) -> dict:
    return asyncio.run(_listed(name))


def _digest(files: dict[str, str]) -> str:
    """A version's digest: sha256 over its sorted ``<path>\\0<sha256 of the file>`` lines."""
    lines = "".join(
        f"{rel}\0{hashlib.sha256(text.encode('utf-8')).hexdigest()}\n"
        for rel, text in sorted(files.items())
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


# ── an update ─────────────────────────────────────────────────────────────────────────────────


def test_an_edited_bundled_skill_survives_an_update_and_is_offered_the_new_version(shipped):
    _ship(shipped, NAME, V1)
    _start()
    copy = _copy(NAME)
    # Her edit, made in the files: the instructions changed and a note of her own beside them.
    (copy / "SKILL.md").write_text(HER_TEXT, encoding="utf-8")
    (copy / "my-notes.md").write_text("Ask before merging two meetings.\n", encoding="utf-8")
    hers = _files(copy)

    # The next release ships a changed skill, stamped later than her edit.
    _ship(shipped, NAME, V2, mtime=time.time() + 3600)
    _start()

    assert _files(copy) == hers, "the update replaced the owner's copy"
    row = _row(NAME)
    assert row["bundled_update"] == {"digest": _digest(V2)}
    assert row["integrity"] == "edited"
    assert row["path"] == str(copy / "SKILL.md")


def test_an_unedited_bundled_skill_is_replaced_whole_by_the_new_version(shipped):
    _ship(shipped, NAME, V1)
    _start()
    copy = _copy(NAME)
    installed = (copy / "SKILL.md").stat().st_mtime

    # The next release's files are stamped EARLIER than the copy: content decides, not the clock.
    _ship(shipped, NAME, V2, mtime=installed - 3600)
    _start()

    assert _files(copy) == V2, "the copy is not exactly the new version"
    assert not (copy / "references" / "old-layout.md").exists(), "a file the release dropped stayed"
    row = _row(NAME)
    assert row["bundled_update"] is None
    assert row["integrity"] == "intact"
    assert row["source"] == "bundled"


def test_the_install_record_holds_a_digest_of_each_file_installed(shipped):
    _ship(shipped, NAME, V1)
    _start()
    lock = _copy(NAME) / ".pclaw-lock.json"
    assert lock.is_file(), "the sync recorded nothing about what it installed"
    record = json.loads(lock.read_text(encoding="utf-8"))
    assert record["sha256"] == {
        rel: hashlib.sha256(text.encode("utf-8")).hexdigest() for rel, text in V1.items()
    }
    # The control: a start with nothing new shipped leaves the copy and its record as they are.
    before = (_copy(NAME) / ".pclaw-lock.json").read_bytes()
    _start()
    assert (_copy(NAME) / ".pclaw-lock.json").read_bytes() == before
    assert _files(_copy(NAME)) == V1


# ── a copy from before install records ───────────────────────────────────────────────────────


def test_a_copy_from_before_records_that_matches_no_shipped_version_is_kept_and_offered(shipped):
    copy = _copy(NAME)
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text(HER_TEXT, encoding="utf-8")
    _ship(shipped, NAME, V2, mtime=time.time() + 3600)

    _start()

    assert _files(copy) == {"SKILL.md": HER_TEXT}, "an unrecorded copy of hers was replaced"
    assert _row(NAME)["bundled_update"] == {"digest": _digest(V2)}


def test_a_copy_from_before_records_that_is_a_version_that_shipped_is_replaced(
    shipped, monkeypatch
):
    from personalclaw.skills import shipped as shipped_skills

    monkeypatch.setitem(shipped_skills.EARLIER_VERSIONS, NAME, frozenset({_digest(V1)}))
    copy = _copy(NAME)
    for rel, text in V1.items():
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / rel).write_text(text, encoding="utf-8")
    _ship(shipped, NAME, V2)

    _start()

    assert _files(copy) == V2
    assert _row(NAME)["integrity"] == "intact"


# ── skills no longer shipped ─────────────────────────────────────────────────────────────────


def test_a_skill_of_the_owners_own_is_never_removed_for_its_name(shipped):
    """No release shipped ``cron``, ``learn``, ``subagent`` or ``personalclaw-core``: a folder by
    one of those names in the library is the owner's."""
    for name in ("cron", "learn", "subagent", "personalclaw-core"):
        own = _copy(name)
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Mine.\n---\n# {name}\n", encoding="utf-8"
        )

    _start()

    for name in ("cron", "learn", "subagent", "personalclaw-core"):
        assert (_copy(name) / "SKILL.md").is_file(), f"the owner's skill {name} was removed"


def test_a_skill_no_longer_shipped_is_removed_only_while_it_is_the_installed_copy(shipped):
    _ship(shipped, "sweep-inbox", {"SKILL.md": "---\nname: sweep-inbox\ndescription: S.\n---\n"})
    _ship(shipped, "polish-draft", {"SKILL.md": "---\nname: polish-draft\ndescription: P.\n---\n"})
    _start()
    edited = _copy("polish-draft") / "SKILL.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "Keep my tone.\n", encoding="utf-8")

    # The next release ships neither.
    shutil.rmtree(shipped / "sweep-inbox")
    shutil.rmtree(shipped / "polish-draft")
    _start()

    assert not _copy("sweep-inbox").exists(), "a retired skill PersonalClaw installed stayed"
    assert edited.read_text(encoding="utf-8").endswith("Keep my tone.\n"), "her edit was removed"
    row = _row("polish-draft")
    assert row["bundled_update"] is None
    assert row["source"] == "local", "a skill PersonalClaw no longer ships is not called bundled"


# ── her choice ───────────────────────────────────────────────────────────────────────────────


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import skills as skills_h

    app = web.Application()
    app.router.add_get("/api/skills", skills_h.api_skills_list)
    app.router.add_post("/api/skills/bundled/update", skills_h.api_skill_bundled_update)
    app.router.add_post("/api/skills/bundled/keep", skills_h.api_skill_bundled_keep)
    return app


def _edited_with_an_offer(shipped: Path) -> dict[str, str]:
    _ship(shipped, NAME, V1)
    _start()
    (_copy(NAME) / "SKILL.md").write_text(HER_TEXT, encoding="utf-8")
    _ship(shipped, NAME, V2)
    _start()
    return _files(_copy(NAME))


@pytest.mark.asyncio
async def test_using_the_new_version_replaces_her_copy_with_exactly_what_ships(shipped):
    _edited_with_an_offer(shipped)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/skills/bundled/update", json={"name": NAME, "digest": _digest(V2)}
        )
        assert resp.status == 200, await resp.text()
    assert _files(_copy(NAME)) == V2
    row = await _listed(NAME)
    assert row["bundled_update"] is None and row["integrity"] == "intact"


@pytest.mark.asyncio
async def test_a_version_she_was_not_offered_is_not_installed(shipped):
    hers = _edited_with_an_offer(shipped)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/skills/bundled/update", json={"name": NAME, "digest": _digest(V1)}
        )
        assert resp.status == 409, await resp.text()
        assert (await resp.json())["error"]["code"] == "skill_bundled_version_changed"
    assert _files(_copy(NAME)) == hers


@pytest.mark.asyncio
async def test_keeping_hers_ends_the_offer_until_a_later_version_ships(shipped):
    hers = _edited_with_an_offer(shipped)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/skills/bundled/keep", json={"name": NAME, "digest": _digest(V2)}
        )
        assert resp.status == 200, await resp.text()
    assert _files(_copy(NAME)) == hers
    assert (await _listed(NAME))["bundled_update"] is None

    v3 = dict(V2, **{"SKILL.md": V2["SKILL.md"] + "Number the topics.\n"})
    _ship(shipped, NAME, v3)
    _start()
    assert _files(_copy(NAME)) == hers
    assert (await _listed(NAME))["bundled_update"] == {"digest": _digest(v3)}
