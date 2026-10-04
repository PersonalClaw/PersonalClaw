"""The owner's edit of an installed skill reads as her edit; "tampered" means a damaged record.

An installed skill carries an install record (``.pclaw-lock.json``): a digest of each file as it was
installed. The integrity check compared the files with it and called any difference "tampered" —
on the Skills page, in ``personalclaw skills verify`` (which then exited 1) and in the health score
(which then told her to reinstall or remove the skill). So the owner's own edit, saved in the
skill editor or made in the files, read as tampering, and the advice was to undo it.

PersonalClaw cannot tell who changed a file in her home, and the home is hers: a change to a
skill's files is an edit, shown as one ("edited", with what changed), and never as tampering. What
an edit never touches is the record itself, so "tampered" now names that and only that: an install
record that is there and cannot be read as one, so nothing can say what was installed. It used to
read "unverified", like a skill that never had a record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.skills.loader import SkillsLoader, skills_dir
from personalclaw.skills.marketplace import (
    SkillDetail,
    SkillNotFoundError,
    SkillsMarketplace,
    install_scanned,
)

NAME = "meeting-brief"
SKILL = (
    "---\nname: meeting-brief\ndescription: Brief me before a meeting.\n---\n# Brief\n"
    "List who is coming.\n"
)


class _Catalog(SkillsMarketplace):
    """A catalogue that serves one skill, as a marketplace install reads it."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def search(self, query: str, limit: int = 20) -> list:
        return []

    def fetch(self, skill_id: str) -> SkillDetail:
        if skill_id != NAME:
            raise SkillNotFoundError(skill_id)
        files = [{"path": p, "contents": c} for p, c in self._files.items()]
        return SkillDetail(id=NAME, name=NAME, files=files)


def _install() -> Path:
    install_scanned(_Catalog({"SKILL.md": SKILL}), "catalog.example", NAME, skills_dir())
    return skills_dir() / NAME


async def _listed() -> dict:
    from personalclaw.dashboard.handlers import skills as skills_h

    resp = await skills_h.api_skills_list(make_mocked_request("GET", "/api/skills"))
    return next(r for r in json.loads(resp.body.decode()) if r["key"] == NAME)


def _row() -> dict:
    return asyncio.run(_listed())


def _verify() -> dict:
    from personalclaw.dashboard.handlers import skills as skills_h

    req = make_mocked_request("POST", f"/api/skills/{NAME}/verify", match_info={"name": NAME})
    resp = asyncio.run(skills_h.api_skill_verify(req))
    assert resp.status == 200, resp.body
    return json.loads(resp.body.decode())


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import api_skill_detail
    from personalclaw.dashboard.handlers import skills as skills_h

    app = web.Application()
    app["state"] = SimpleNamespace(
        context_builder=SimpleNamespace(skills=SkillsLoader(install_builtins=False))
    )
    app.router.add_post("/api/skills/{name}/verify", skills_h.api_skill_verify)
    app.router.add_get("/api/skills/{name:.+}", api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", api_skill_detail)
    return app


def _skills_verify() -> int:
    """``personalclaw skills verify``: its exit status."""
    from personalclaw.cli import _handle_skills

    try:
        _handle_skills(argparse.Namespace(skills_command="verify"))
    except SystemExit as exited:
        return int(exited.code or 0)
    return 0


def _counted_in_the_health_score() -> int:
    from personalclaw.resilience import remediation as rem

    return next(d.count for d in rem.measure_deficits() if d.key == "skills_tampered")


# ── an edit ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_her_edit_in_the_skill_editor_reads_edited():
    _install()
    assert (await _listed())["integrity"] == "intact"
    async with TestClient(TestServer(_app())) as client:
        read = await (await client.get(f"/api/skills/{NAME}")).json()
        resp = await client.put(
            f"/api/skills/{NAME}",
            json={"content": read["content"] + "Add the agenda.\n"},
            headers={"If-Match": f'"{read["revision"]}"'},
        )
        assert resp.status == 200, await resp.text()
        verified = await client.post(f"/api/skills/{NAME}/verify")
        report = await verified.json()

    assert (await _listed())["integrity"] == "edited"
    assert report["integrity"] == "edited"
    assert report["mutated"] == ["SKILL.md"]


def test_her_edit_in_the_files_reads_edited_and_names_what_changed():
    copy = _install()
    (copy / "SKILL.md").write_text(SKILL + "Add the agenda.\n", encoding="utf-8")
    (copy / "agenda-template.md").write_text("1. Goals\n2. Decisions\n", encoding="utf-8")

    assert _row()["integrity"] == "edited"
    report = _verify()
    assert report["integrity"] == "edited"
    assert report["mutated"] == ["SKILL.md"] and report["added"] == ["agenda-template.md"]


def test_an_edit_is_not_counted_against_the_health_of_the_home():
    copy = _install()
    assert _counted_in_the_health_score() == 0
    (copy / "SKILL.md").write_text(SKILL + "Add the agenda.\n", encoding="utf-8")
    assert _counted_in_the_health_score() == 0


def test_verify_reports_an_edit_and_exits_0(capsys):
    copy = _install()
    (copy / "SKILL.md").write_text(SKILL + "Add the agenda.\n", encoding="utf-8")

    assert _skills_verify() == 0
    out = capsys.readouterr().out
    assert f"{NAME}: edited" in out and "changed: SKILL.md" in out


# ── a damaged record ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "record",
    ["{not json", "[]", json.dumps({"id": NAME}), json.dumps({"sha256": {"SKILL.md": 7}})],
    ids=["not-json", "not-an-object", "no-digests", "a-digest-that-is-not-text"],
)
def test_an_install_record_that_cannot_be_read_reads_tampered(record, capsys):
    copy = _install()
    (copy / ".pclaw-lock.json").write_text(record, encoding="utf-8")

    assert _row()["integrity"] == "tampered"
    assert _verify()["integrity"] == "tampered"
    assert _counted_in_the_health_score() == 1
    assert _skills_verify() == 1
    assert "install record" in capsys.readouterr().out


def test_a_skill_with_no_record_reads_unverified():
    """The control: a skill nothing installed has no record, which is not a damaged one."""
    folder = skills_dir() / NAME
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")

    assert _row()["integrity"] == "unverified"
    assert _verify()["integrity"] == "unverified"
    assert _counted_in_the_health_score() == 0
