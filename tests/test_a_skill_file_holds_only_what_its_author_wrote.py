"""A skill's own file holds only what its author wrote; accepted refinements are applied once.

An accepted refinement is kept beside its skill, in the skill's overlay, and added after the skill's
own text each time the skill loads. Two writers of ``SKILL.md`` used to start from the skill as it
loads: the Skills page's editor read that body, refinements included, and saved it back whole, and
the curator rewrote a skill's ``status`` line on the same body. The refinement then stood in the
skill's own file AND in its overlay, so every load carried it twice, and reverting it removed only
the overlay's copy.

The rule these pin, for every writer of the file: the file is the author's text and nothing else.
The editor reads and saves that text and names the refinements applied on top of it; the curator
changes the ``status`` line of that text; a consolidation's rewrite of an auto-created skill is
stored without a copy of a refinement that is applied on top; and a file an earlier save doubled is
put back to its author's text the first time the skill loads, so a revert removes the refinement
completely.

They drive the product's own paths: the dashboard's skill routes over a home of the test's own, the
curator's aging pass, and the consolidation's refine writer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.skills import curator, overlays
from personalclaw.skills.loader import AutoSkillProvenance, SkillsLoader

SKILL = "---\nname: notes\ndescription: Take notes.\n---\n# Notes\nKeep them short.\n"
EDITED = SKILL.replace("Keep them short.", "Keep them short and dated.")
CITE = "Cite the source of every note."
LINK = "Link each note to the meeting it came from."
AT = "2026-10-02T09:00:00+00:00"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of the test's own for the library, its overlays and its lock."""
    import personalclaw.config.loader as cfg
    import personalclaw.skills.loader as sl
    import personalclaw.skills.marketplace as mp

    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(sl, "config_dir", lambda: root)
    monkeypatch.setattr(cfg, "config_dir", lambda: root)
    monkeypatch.setattr(mp, "skill_discovery_paths", lambda: [])
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
    assert root in overlays.overlays_dir().parents, "the overlays were not redirected"
    return root


def _loader() -> SkillsLoader:
    return SkillsLoader(install_builtins=False)


def _skill_md(home: Path, name: str) -> Path:
    return home / "skills" / name / "SKILL.md"


def _accept(name: str, procedure: str, *, trigger: str = "correction") -> None:
    """A refinement as accepting its proposal applies it (``proposals.accept``)."""
    overlays.apply_overlay(
        name,
        description="Refined after you corrected this turn",
        procedure_md=procedure,
        created_at=AT,
        trigger=trigger,
    )


def _app(loader: SkillsLoader) -> web.Application:
    from personalclaw.dashboard.handlers import api_skill_detail
    from personalclaw.dashboard.handlers.skills import api_skill_overlay_revert

    app = web.Application()
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(skills=loader))
    app.router.add_post("/api/skills/overlay/revert", api_skill_overlay_revert)
    app.router.add_get("/api/skills/{name:.+}", api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", api_skill_detail)
    return app


def _based_on(revision: str) -> dict[str, str]:
    """The header the web client sends (`lib/staleWrite.ts` `basedOn`)."""
    return {"If-Match": f'"{revision}"'}


async def _edit_on_the_skills_page(client: TestClient, name: str, old: str, new: str) -> dict:
    """Open the editor, change one passage, save: what the Skills page does."""
    read = await (await client.get(f"/api/skills/{name}")).json()
    resp = await client.put(
        f"/api/skills/{name}",
        json={"content": read["content"].replace(old, new)},
        headers=_based_on(read["revision"]),
    )
    assert resp.status == 200, await resp.text()
    return read


# ── the Skills page ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_edit_on_the_skills_page_leaves_an_accepted_refinement_applied_once(home):
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    async with TestClient(TestServer(_app(loader))) as client:
        await _edit_on_the_skills_page(
            client, "notes", "Keep them short.", "Keep them short and dated."
        )
    assert _skill_md(home, "notes").read_text(encoding="utf-8") == EDITED
    loaded = loader.load_skill("notes")
    assert loaded is not None and loaded.count(CITE) == 1
    assert "Keep them short and dated." in loaded


@pytest.mark.asyncio
async def test_the_editor_reads_the_skills_own_text_and_names_what_is_applied_on_top(home):
    """The editor's read is the author's text with its own revision, the refinements applied on
    top of it in the order they load, and the skill as a session loads it."""
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    _accept("notes", LINK, trigger="rejection")
    async with TestClient(TestServer(_app(loader))) as client:
        read = await (await client.get("/api/skills/notes")).json()
    from personalclaw.stale_write import revision_of

    assert read["content"] == SKILL
    assert read["revision"] == revision_of(SKILL)
    applied = read["refinements"]
    assert [r["version"] for r in applied] == [1, 2]
    assert [r["trigger"] for r in applied] == ["correction", "rejection"]
    assert CITE in applied[0]["text"] and LINK in applied[1]["text"]
    assert applied[0]["text"].startswith("## Refinement v1 (2026-10-02, from a correction)")
    assert len({r["id"] for r in applied}) == 2
    assert read["loaded"] == loader.load_skill("notes")


@pytest.mark.asyncio
async def test_an_edit_of_a_skill_with_no_refinement_is_saved_as_typed(home):
    """The control: with nothing applied on top, the editor's text is the file, byte for byte."""
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    typed = SKILL + "\nWrite the date first.\n"
    async with TestClient(TestServer(_app(loader))) as client:
        read = await (await client.get("/api/skills/notes")).json()
        assert read["content"] == SKILL
        resp = await client.put(
            "/api/skills/notes", json={"content": typed}, headers=_based_on(read["revision"])
        )
        assert resp.status == 200, await resp.text()
    assert _skill_md(home, "notes").read_text(encoding="utf-8") == typed
    assert loader.load_skill("notes") == typed


@pytest.mark.asyncio
async def test_a_save_that_carries_the_skill_as_it_loads_stores_only_its_own_text(home):
    """A client that sends back the body a session loads (an older page, a script) still leaves
    each refinement where it is kept: its copy is not written into the skill's file."""
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    as_loaded = overlays.render_with_overlay("notes", EDITED)
    assert CITE in as_loaded
    async with TestClient(TestServer(_app(loader))) as client:
        read = await (await client.get("/api/skills/notes")).json()
        resp = await client.put(
            "/api/skills/notes", json={"content": as_loaded}, headers=_based_on(read["revision"])
        )
        assert resp.status == 200, await resp.text()
    assert _skill_md(home, "notes").read_text(encoding="utf-8") == EDITED
    assert loader.load_skill("notes").count(CITE) == 1


# ── revert ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reverting_the_refinement_after_an_edit_removes_it_completely(home):
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    async with TestClient(TestServer(_app(loader))) as client:
        await _edit_on_the_skills_page(
            client, "notes", "Keep them short.", "Keep them short and dated."
        )
        resp = await client.post("/api/skills/overlay/revert", json={"name": "notes"})
        assert resp.status == 200, await resp.text()
        assert (await resp.json())["reverted"] == 1
    loaded = loader.load_skill("notes")
    assert CITE not in loaded
    assert loaded == EDITED
    assert _skill_md(home, "notes").read_text(encoding="utf-8") == EDITED


@pytest.mark.asyncio
async def test_reverting_one_refinement_leaves_the_others_applied_once(home):
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    _accept("notes", LINK)
    async with TestClient(TestServer(_app(loader))) as client:
        read = await (await client.get("/api/skills/notes")).json()
        first = read["refinements"][0]
        assert CITE in first["text"]
        resp = await client.post(
            "/api/skills/overlay/revert", json={"name": "notes", "refinement": first["id"]}
        )
        assert resp.status == 200, await resp.text()
        answer = await resp.json()
        assert answer["reverted"] == 1
        assert [LINK in r["text"] for r in answer["refinements"]] == [True]
        again = await (await client.get("/api/skills/notes")).json()
        # The same id names nothing now, and reverting it again changes nothing.
        gone = await client.post(
            "/api/skills/overlay/revert", json={"name": "notes", "refinement": first["id"]}
        )
        assert gone.status == 404
    assert [LINK in r["text"] for r in again["refinements"]] == [True]
    loaded = loader.load_skill("notes")
    assert CITE not in loaded and loaded.count(LINK) == 1
    assert _skill_md(home, "notes").read_text(encoding="utf-8") == SKILL


# ── the curator ─────────────────────────────────────────────────────────────────────────────


AUTO = (
    "---\nname: auto/flow\ndescription: A flow.\nsource: auto\n"
    "created_at: 2026-01-01T00:00:00+00:00\n---\n# Flow\nRun it.\n"
)
NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def test_the_curator_leaves_an_accepted_refinement_applied_once(home):
    loader = _loader()
    assert loader.create_skill("auto/flow", AUTO)
    _accept("auto/flow", CITE)
    report = curator.run_aging(loader, now=NOW)
    assert report.to_archived == ["auto/flow"]
    archived = AUTO.replace("\n---\n# Flow", "\nstatus: archived\n---\n# Flow")
    assert _skill_md(home, "auto/flow").read_text(encoding="utf-8") == archived
    assert loader.load_skill("auto/flow").count(CITE) == 1

    # Restoring it is the same kind of write, and keeps the same rule.
    assert curator.restore(loader, "auto/flow")
    assert _skill_md(home, "auto/flow").read_text(encoding="utf-8") == AUTO
    assert loader.load_skill("auto/flow").count(CITE) == 1


def test_a_dry_run_of_the_curator_writes_nothing(home):
    """`--dry-run` reports without writing, even a skill whose file an earlier save doubled."""
    loader = _loader()
    assert loader.create_skill("auto/flow", AUTO)
    _accept("auto/flow", CITE)
    doubled = overlays.render_with_overlay("auto/flow", AUTO)
    _skill_md(home, "auto/flow").write_text(doubled, encoding="utf-8")
    report = curator.run_aging(loader, now=NOW, dry_run=True)
    assert report.to_archived == ["auto/flow"]
    assert _skill_md(home, "auto/flow").read_text(encoding="utf-8") == doubled


# ── the consolidation's rewrite of an auto-created skill ───────────────────────────────────


def test_a_consolidation_rewrite_that_copies_an_applied_refinement_leaves_it_applied_once(home):
    """The consolidation rewrites an auto-created skill from what its session loaded, so its
    procedure can hold a refinement word for word: that copy is not written into the file."""
    loader = _loader()
    name = loader.create_auto_skill(
        "flow",
        description="A flow.",
        triggers="",
        procedure_md="# Flow\nRun it.",
        provenance=AutoSkillProvenance(session_key="s", created_at=AT),
    )
    assert name == "auto/flow"
    _accept(name, CITE)
    as_loaded = loader.load_skill(name)
    block = as_loaded[as_loaded.index("## Refinement v1") :].strip()
    assert loader.update_auto_skill(
        name,
        description="A flow.",
        triggers="",
        procedure_md=f"# Flow\nRun it twice.\n\n{block}",
        provenance=AutoSkillProvenance(session_key="s", created_at=AT, refined_at=AT),
    )
    stored = _skill_md(home, name).read_text(encoding="utf-8")
    assert "Run it twice." in stored and CITE not in stored
    assert loader.load_skill(name).count(CITE) == 1


# ── a file an earlier save doubled ──────────────────────────────────────────────────────────


def test_a_skill_an_earlier_save_doubled_is_repaired_the_first_time_it_loads(home):
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    md = _skill_md(home, "notes")
    # What the old editor wrote: the skill as it loaded, refinement included, with the edit.
    md.write_text(overlays.render_with_overlay("notes", EDITED), encoding="utf-8")

    loaded = loader.load_skill("notes")
    assert loaded is not None and loaded.count(CITE) == 1
    assert md.read_text(encoding="utf-8") == EDITED
    # Idempotent: nothing more to repair, so a second load leaves the file as it is.
    stamp = md.stat().st_mtime_ns
    assert loader.load_skill("notes") == loaded
    assert md.stat().st_mtime_ns == stamp
    # And reverting the refinement now removes it completely.
    assert loader.revert_refinements("notes") == 1
    assert loader.load_skill("notes") == EDITED


def test_a_file_saved_doubled_more_than_once_is_repaired_whole(home):
    """Two saves from the old editor, the second after a refinement was added, and a line the
    author typed after the refinement: every copy goes, and every word the author wrote stays."""
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    md = _skill_md(home, "notes")
    once = overlays.render_with_overlay("notes", SKILL)
    _accept("notes", LINK)
    # The second save wrote what loaded then: the first copy, then both refinements.
    twice = once.rstrip() + "\n\n" + overlays.render_with_overlay("notes", "").strip() + "\n"
    md.write_text(twice + "\nAsk before sharing a note.\n", encoding="utf-8")

    loaded = loader.load_skill("notes")
    assert loaded.count(CITE) == 1 and loaded.count(LINK) == 1
    assert md.read_text(encoding="utf-8") == SKILL.rstrip() + "\n\nAsk before sharing a note.\n"


def test_the_repair_keeps_what_is_not_an_exact_copy_of_an_applied_refinement(home):
    """A refinement the author reworded is the author's text now, and the text of a refinement
    that is no longer applied cannot be told from the author's: both stay in the file."""
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    _accept("notes", CITE)
    md = _skill_md(home, "notes")
    copied = overlays.render_with_overlay("notes", SKILL)
    reworded = copied.replace(CITE, "Cite the source of every note, with its date.")
    md.write_text(reworded, encoding="utf-8")
    loaded = loader.load_skill("notes")
    assert md.read_text(encoding="utf-8") == reworded
    assert loaded.count(CITE) == 1 and "with its date." in loaded

    assert overlays.revert_overlay("notes") == 1
    md.write_text(copied, encoding="utf-8")
    assert loader.load_skill("notes") == copied
    assert md.read_text(encoding="utf-8") == copied


BLOCK = (
    "## Refinement v1 (2026-10-02, from a correction)\n\n_Refined after you corrected this turn_"
    "\n\nCite the source of every note."
)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        (f"{SKILL}\n{BLOCK}\n", SKILL),
        (f"{SKILL}\n{BLOCK}", SKILL.rstrip()),
        (f"{SKILL}\n{BLOCK}\n\nMy note.\n", SKILL.rstrip() + "\n\nMy note.\n"),
        ("---\nname: notes\n---\n\n" + BLOCK + "\n", "---\nname: notes\n---\n"),
        # The author's: reworded, run into the next line, or nothing of it at all.
        (f"{SKILL}\n{BLOCK.replace('every', 'each')}\n", None),
        (f"{SKILL}\n{BLOCK}\nand more on the same part.\n", None),
        (SKILL, None),
    ],
    ids=["at-the-end", "no-final-newline", "before-more-text", "after-frontmatter"]
    + ["reworded", "run-on", "absent"],
)
def test_a_copy_is_a_block_that_stands_as_a_part_of_its_own(text, kept):
    """The same cases the Skills page's own test of the rule answers (`holdsRefinementCopy`)."""
    assert overlays.without_copies(text, [BLOCK]) == (text if kept is None else kept)


def test_a_skill_outside_the_library_is_never_written_by_the_repair(home, tmp_path, monkeypatch):
    """The folder other AI tools share is read, never written: a skill there with a copy in its
    file loads with the refinement once, and its file is left exactly as it is."""
    import personalclaw.skills.marketplace as mp

    shared = tmp_path / "shared"
    (shared / "notes").mkdir(parents=True)
    monkeypatch.setattr(mp, "skill_discovery_paths", lambda: [shared])
    _accept("notes", CITE)
    copied = overlays.render_with_overlay("notes", SKILL)
    (shared / "notes" / "SKILL.md").write_text(copied, encoding="utf-8")

    assert _loader().load_skill("notes").count(CITE) == 1
    assert (shared / "notes" / "SKILL.md").read_text(encoding="utf-8") == copied


def test_a_write_over_a_copy_that_changed_meanwhile_is_not_made(home):
    """A writer names the text it built its change from, and a file that changed since keeps the
    later words: the write is not made."""
    loader = _loader()
    assert loader.create_skill("notes", SKILL)
    read = loader.skill_text("notes")
    _skill_md(home, "notes").write_text(EDITED, encoding="utf-8")  # another writer, meanwhile
    assert loader.update_skill("notes", SKILL + "Mine.\n", over=read) is False
    assert _skill_md(home, "notes").read_text(encoding="utf-8") == EDITED
    assert loader.update_skill("notes", EDITED + "Mine.\n", over=EDITED) is True
    assert _skill_md(home, "notes").read_text(encoding="utf-8") == EDITED + "Mine.\n"
