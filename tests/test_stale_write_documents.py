"""A save of a whole document from a stale page is refused instead of undoing a change made since.

Seven editors save a WHOLE document built from the copy they read: a skill's SKILL.md, a prompt,
a snippet, the three markdown memory docs, a project's overview, a project's agent-instructions
text (the "Judge guidance" dialog) and a draft run's policy overlay. When the stored document
changed after the page read it — another tab saved, or the gateway wrote it itself — the save
replaced the newer document and the change vanished without a word.

The contract (`personalclaw/stale_write.py`), pinned here for each of them: the read the page
paints from carries the document's ``revision``; the save names it in ``If-Match``; a save from a
stale copy is refused with ``409 stale_write`` and nothing is written; a save that names no
revision gets ``428 revision_required``. Where the gateway has a writer of its own for the
document, one test runs that REAL writer between the read and the save and checks that its change
is the one that survives.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def revision_of(document):
    """Imported per call, so this file collects on a tree that predates the module and each test
    reports its own verdict there."""
    from personalclaw.stale_write import revision_of as _revision_of

    return _revision_of(document)


def _based_on(revision: str) -> dict[str, str]:
    """The header the web client sends (`lib/staleWrite.ts` `basedOn`)."""
    return {"If-Match": f'"{revision}"'}


async def _code(resp) -> str:
    return (await resp.json())["error"]["code"]


@pytest.fixture(autouse=True)
def _quiet_sel(monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())


# ── PUT /api/skills/{name} ────────────────────────────────────────────────────────────────


SKILL = "---\nname: notes\ndescription: Take notes.\n---\n# Notes\nKeep them short.\n"
SKILL_TAB_A = SKILL + "Tab A's line.\n"
SKILL_TAB_B = SKILL + "Tab B's line.\n"


@pytest.fixture
def skills(tmp_path):
    from personalclaw.skills import SkillsLoader

    loader = SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False)
    assert loader.create_skill("notes", SKILL)
    return loader


def _skills_app(loader) -> web.Application:
    from personalclaw.dashboard.handlers import api_skill_detail

    app = web.Application()
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(skills=loader))
    app.router.add_get("/api/skills/{name:.+}", api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", api_skill_detail)
    return app


class TestSkill:
    @pytest.mark.asyncio
    async def test_two_saves_from_one_read_the_second_is_refused(self, skills) -> None:
        async with TestClient(TestServer(_skills_app(skills))) as c:
            read = await (await c.get("/api/skills/notes")).json()
            base = read["revision"]  # both tabs paint this
            assert base == revision_of(read["content"])
            first = await c.put(
                "/api/skills/notes", json={"content": SKILL_TAB_A}, headers=_based_on(base)
            )
            assert first.status == 200
            second = await c.put(
                "/api/skills/notes", json={"content": SKILL_TAB_B}, headers=_based_on(base)
            )
            assert second.status == 409
            assert await _code(second) == "stale_write"
        assert skills.load_skill("notes") == SKILL_TAB_A

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_revision_is_refused(self, skills) -> None:
        async with TestClient(TestServer(_skills_app(skills))) as c:
            resp = await c.put("/api/skills/notes", json={"content": SKILL_TAB_A})
            assert resp.status == 428
            assert await _code(resp) == "revision_required"
        assert skills.load_skill("notes") == SKILL

    @pytest.mark.asyncio
    async def test_an_accepted_refinement_between_read_and_save_is_not_undone(self, skills) -> None:
        # The read is what a session loads — SKILL.md plus the accepted refinements overlaid on
        # it — so a refinement the learning flywheel accepts while the editor is open changes
        # the document even though SKILL.md itself is untouched.
        from personalclaw.skills import overlays

        async with TestClient(TestServer(_skills_app(skills))) as c:
            base = (await (await c.get("/api/skills/notes")).json())["revision"]
            overlays.apply_overlay("notes", description="Cite the source.", procedure_md="- cite")
            resp = await c.put(
                "/api/skills/notes", json={"content": SKILL_TAB_A}, headers=_based_on(base)
            )
            assert resp.status == 409
            assert await _code(resp) == "stale_write"
        assert skills.skill_file("notes").read_text(encoding="utf-8") == SKILL
        assert "Cite the source." in skills.load_skill("notes")

    @pytest.mark.asyncio
    async def test_the_curator_reactivating_a_skill_between_read_and_save_is_not_undone(
        self, skills
    ) -> None:
        from personalclaw.skills import curator

        archived = "---\nname: auto/flow\ndescription: A flow.\nstatus: archived\n---\n# Flow\n"
        assert skills.create_skill("auto/flow", archived)
        async with TestClient(TestServer(_skills_app(skills))) as c:
            base = (await (await c.get("/api/skills/auto/flow")).json())["revision"]
            assert curator.restore(skills, "auto/flow")  # the gateway's own write
            resp = await c.put(
                "/api/skills/auto/flow",
                json={"content": archived + "My edit.\n"},
                headers=_based_on(base),
            )
            assert resp.status == 409
        stored = skills.load_skill("auto/flow")
        # Reactivating drops the `status:` line (active is the default), and it stays dropped.
        assert "status: archived" not in stored and "My edit." not in stored

    @pytest.mark.asyncio
    async def test_the_save_answers_the_revision_the_next_read_reports(self, skills) -> None:
        async with TestClient(TestServer(_skills_app(skills))) as c:
            base = (await (await c.get("/api/skills/notes")).json())["revision"]
            saved = await (
                await c.put(
                    "/api/skills/notes", json={"content": SKILL_TAB_A}, headers=_based_on(base)
                )
            ).json()
            again = (await (await c.get("/api/skills/notes")).json())["revision"]
            assert saved["revision"] == again != base
            # An editor that stays open saves again from the answer, not from its first read.
            resp = await c.put(
                "/api/skills/notes", json={"content": SKILL_TAB_B}, headers=_based_on(again)
            )
            assert resp.status == 200


# ── PUT /api/prompts/{name} and PUT /api/prompt-snippets/{name} ───────────────────────────


@pytest.fixture
def prompts(monkeypatch):
    """The native prompt provider over the test's own home, with no bundled seed."""
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")
    from personalclaw.dashboard.handlers.prompts import _get_default_prompt_provider

    return _get_default_prompt_provider()


def _prompts_app() -> web.Application:
    from personalclaw.dashboard.handlers import (
        api_prompt_detail,
        api_prompt_save,
        api_snippet_detail,
        api_snippet_save,
    )

    app = web.Application()
    app.router.add_get("/api/prompts/{name:.+}", api_prompt_detail)
    app.router.add_put("/api/prompts/{name:.+}", api_prompt_save)
    app.router.add_get("/api/prompt-snippets/{name:.+}", api_snippet_detail)
    app.router.add_put("/api/prompt-snippets/{name:.+}", api_snippet_save)
    return app


def _seed_prompt(provider, content: str = "Sort {{items}} by urgency.") -> None:
    from personalclaw.prompt_providers.base import PromptTemplate, PromptVariable

    provider.create_prompt(
        PromptTemplate(
            name="triage",
            kind="user",
            title="Triage",
            content=content,
            variables=[PromptVariable(name="items")],
            tags=["inbox"],
        )
    )


def _page_prompt(read: dict, **edits) -> dict:
    """What the prompt editor PUTs: every field, rebuilt from the copy it read."""
    body = {k: read.get(k) for k in ("name", "kind", "title", "description", "content", "tags")}
    body["variables"] = read.get("variables") or []
    body["launch_spec"] = read.get("launch_spec") or {}
    return {**body, **edits}


class TestPrompt:
    @pytest.mark.asyncio
    async def test_two_saves_from_one_read_the_second_is_refused(self, prompts) -> None:
        _seed_prompt(prompts)
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompts/triage")).json()
            base = read["revision"]
            first = await c.put(
                "/api/prompts/triage",
                json=_page_prompt(read, title="Tab A"),
                headers=_based_on(base),
            )
            assert first.status == 200
            # Tab B changed the tags only — and would have put the title back.
            second = await c.put(
                "/api/prompts/triage",
                json=_page_prompt(read, tags=["inbox", "tab-b"]),
                headers=_based_on(base),
            )
            assert second.status == 409
            assert await _code(second) == "stale_write"
        stored = prompts.get_prompt("triage")
        assert stored.title == "Tab A" and stored.tags == ["inbox"]

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_revision_is_refused(self, prompts) -> None:
        _seed_prompt(prompts)
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompts/triage")).json()
            resp = await c.put("/api/prompts/triage", json=_page_prompt(read, title="Nope"))
            assert resp.status == 428
            assert await _code(resp) == "revision_required"
        assert prompts.get_prompt("triage").title == "Triage"

    @pytest.mark.asyncio
    async def test_a_history_rollback_between_read_and_save_is_not_undone(
        self, prompts, tmp_path
    ) -> None:
        from personalclaw.config.loader import config_dir
        from personalclaw.durability import state_history
        from personalclaw.prompt_providers.base import PromptTemplate

        assert state_history.git_available(), "time travel needs git; this test must not skip"
        home = config_dir()
        root = next(
            r for r in state_history.roots(home=home, workspace=tmp_path) if r.id == "prompts"
        )
        _seed_prompt(prompts, content="version one")
        first = state_history.commit(root, reason="seed", home=home)
        assert first
        prompts.update_prompt("triage", PromptTemplate(name="triage", content="version two"))
        state_history.commit(root, reason="edit", home=home)
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompts/triage")).json()  # the page paints v2
            # The owner rolls the prompt back from the time-travel panel while the editor is open.
            state_history.rollback(root, first, paths=["prompts/triage.yaml"], home=home)
            resp = await c.put(
                "/api/prompts/triage",
                json=_page_prompt(read, content="version two, edited"),
                headers=_based_on(read["revision"]),
            )
            assert resp.status == 409
        assert prompts.get_prompt("triage").content == "version one"

    @pytest.mark.asyncio
    async def test_a_record_the_read_migrates_in_place_still_saves(self, prompts) -> None:
        # Migrate-on-read rewrites a legacy file's SHAPE; the content the editor shows is the
        # same before and after, so it must not turn the next save into a refusal.
        from personalclaw.prompt_providers.native_provider import _prompt_path

        legacy = _prompt_path("legacy")
        legacy.write_text(
            "name: legacy\ncontent: Hi {{who}}\nvariables:\n- name: who\n  type: string\n",
            encoding="utf-8",
        )
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompts/legacy")).json()
            assert "kind: " in legacy.read_text(encoding="utf-8"), "the read did not migrate it"
            resp = await c.put(
                "/api/prompts/legacy",
                json=_page_prompt(read, content="Hello {{who}}"),
                headers=_based_on(read["revision"]),
            )
            assert resp.status == 200, await resp.text()
        assert prompts.get_prompt("legacy").content == "Hello {{who}}"

    @pytest.mark.asyncio
    async def test_the_save_answers_the_revision_the_next_read_reports(self, prompts) -> None:
        _seed_prompt(prompts)
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompts/triage")).json()
            saved = await (
                await c.put(
                    "/api/prompts/triage",
                    json=_page_prompt(read, title="Renamed title"),
                    headers=_based_on(read["revision"]),
                )
            ).json()
            again = (await (await c.get("/api/prompts/triage")).json())["revision"]
            assert saved["revision"] == again != read["revision"]


def _seed_snippet(provider, content: str = "Be brief.") -> None:
    from personalclaw.prompt_providers.base import PromptSnippet

    provider.create_snippet(PromptSnippet(name="tone", title="Tone", content=content))


def _page_snippet(read: dict, **edits) -> dict:
    """What the snippet editor PUTs: every field, rebuilt from the copy it read."""
    body = {k: read.get(k) for k in ("name", "title", "description", "content", "tags")}
    body["variables"] = read.get("variables") or []
    return {**body, **edits}


class TestSnippet:
    @pytest.mark.asyncio
    async def test_two_saves_from_one_read_the_second_is_refused(self, prompts) -> None:
        _seed_snippet(prompts)
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompt-snippets/tone")).json()
            base = read["revision"]
            first = await c.put(
                "/api/prompt-snippets/tone",
                json=_page_snippet(read, content="Be brief. Tab A."),
                headers=_based_on(base),
            )
            assert first.status == 200
            second = await c.put(
                "/api/prompt-snippets/tone",
                json=_page_snippet(read, content="Be brief. Tab B."),
                headers=_based_on(base),
            )
            assert second.status == 409
            assert await _code(second) == "stale_write"
        assert prompts.get_snippet("tone").content == "Be brief. Tab A."

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_revision_is_refused(self, prompts) -> None:
        _seed_snippet(prompts)
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompt-snippets/tone")).json()
            resp = await c.put("/api/prompt-snippets/tone", json=_page_snippet(read, content="x"))
            assert resp.status == 428
            assert await _code(resp) == "revision_required"
        assert prompts.get_snippet("tone").content == "Be brief."

    @pytest.mark.asyncio
    async def test_a_new_includer_elsewhere_does_not_make_the_save_stale(self, prompts) -> None:
        # `used_by` is on the read and moves when ANOTHER record changes; it is not this
        # snippet's text, so a prompt that starts including it is no reason to refuse the save.
        from personalclaw.prompt_providers.base import PromptTemplate

        _seed_snippet(prompts)
        async with TestClient(TestServer(_prompts_app())) as c:
            read = await (await c.get("/api/prompt-snippets/tone")).json()
            prompts.create_prompt(PromptTemplate(name="letter", kind="user", content="{{> tone}}"))
            later = await (await c.get("/api/prompt-snippets/tone")).json()
            assert later["used_by"] != read["used_by"], "precondition: the read did move"
            resp = await c.put(
                "/api/prompt-snippets/tone",
                json=_page_snippet(read, content="Be brief. Mine."),
                headers=_based_on(read["revision"]),
            )
            assert resp.status == 200, await resp.text()

    @pytest.mark.asyncio
    async def test_the_seeder_and_the_migrator_do_not_move_an_edited_bundled_snippet(
        self, prompts, monkeypatch
    ) -> None:
        # Measured shape: every boot the bundled-snippet seeder stamps a user-edited bundled
        # snippet `user_owned`, and the next read's migrate-on-read strips that stamp again — the
        # FILE is rewritten on both sides, forever. The revision describes the content, so it
        # must hold still through that, or every save of such a snippet would be refused.
        from personalclaw.prompt_providers.catalog import BUNDLED_SNIPPETS
        from personalclaw.prompt_providers.native_provider import (
            _snippet_path,
            seed_bundled_snippets,
        )

        monkeypatch.delenv("PERSONALCLAW_SKIP_PROMPT_SEED", raising=False)
        seed_bundled_snippets()
        name = BUNDLED_SNIPPETS[0].name
        edited = prompts.get_snippet(name)
        edited.content += "\nMy own rule."
        prompts.update_snippet(name, edited)  # the user's edit, stored as the page saves it
        async with TestClient(TestServer(_prompts_app())) as c:
            revisions = []
            for _ in range(3):
                seed_bundled_snippets()  # a boot
                assert "user_owned" in _snippet_path(name).read_text(encoding="utf-8")
                read = await (await c.get(f"/api/prompt-snippets/{name}")).json()
                assert "user_owned" not in _snippet_path(name).read_text(encoding="utf-8")
                revisions.append(read["revision"])
            assert len(set(revisions)) == 1, revisions
            resp = await c.put(
                f"/api/prompt-snippets/{name}",
                json=_page_snippet(read, content=read["content"] + "\nAnother rule."),
                headers=_based_on(revisions[0]),
            )
            assert resp.status == 200, await resp.text()


# ── PUT /api/memory/{preferences|projects|history} ────────────────────────────────────────


@pytest.fixture
def memory(tmp_path):
    from personalclaw.memory import MemoryStore

    mem = MemoryStore(workspace=tmp_path / "workspace")
    mem.init()
    return mem


def _memory_app(mem) -> web.Application:
    from personalclaw.dashboard.handlers.memory import (
        api_memory_history,
        api_memory_preferences,
        api_memory_projects,
    )

    app = web.Application()
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(memory=mem))
    for which, handler in (
        ("preferences", api_memory_preferences),
        ("projects", api_memory_projects),
        ("history", api_memory_history),
    ):
        app.router.add_get(f"/api/memory/{which}", handler)
        app.router.add_put(f"/api/memory/{which}", handler)
    return app


DOCS = ("preferences", "projects", "history")


class TestMemoryDoc:
    @pytest.mark.parametrize("which", DOCS)
    @pytest.mark.asyncio
    async def test_two_saves_from_one_read_the_second_is_refused(self, memory, which) -> None:
        async with TestClient(TestServer(_memory_app(memory))) as c:
            read = await (await c.get(f"/api/memory/{which}")).json()
            base = read["revision"]
            assert base == revision_of(read["content"])
            first = await c.put(
                f"/api/memory/{which}",
                json={"content": read["content"] + "\n- tab A's line"},
                headers=_based_on(base),
            )
            assert first.status == 200
            second = await c.put(
                f"/api/memory/{which}",
                json={"content": read["content"] + "\n- tab B's line"},
                headers=_based_on(base),
            )
            assert second.status == 409
            assert await _code(second) == "stale_write"
            stored = (await (await c.get(f"/api/memory/{which}")).json())["content"]
        assert "tab A's line" in stored and "tab B's line" not in stored

    @pytest.mark.parametrize("which", DOCS)
    @pytest.mark.asyncio
    async def test_a_save_that_names_no_revision_is_refused(self, memory, which) -> None:
        async with TestClient(TestServer(_memory_app(memory))) as c:
            before = (await (await c.get(f"/api/memory/{which}")).json())["content"]
            resp = await c.put(f"/api/memory/{which}", json={"content": "- overwritten"})
            assert resp.status == 428
            assert await _code(resp) == "revision_required"
            after = (await (await c.get(f"/api/memory/{which}")).json())["content"]
        assert after == before

    @pytest.mark.asyncio
    async def test_a_consolidated_history_entry_between_read_and_save_is_not_undone(
        self, memory
    ) -> None:
        async with TestClient(TestServer(_memory_app(memory))) as c:
            read = await (await c.get("/api/memory/history")).json()
            memory.append_history("Shipped the stale-write fix.")  # the consolidator's write
            resp = await c.put(
                "/api/memory/history",
                json={"content": read["content"] + "\nmy note"},
                headers=_based_on(read["revision"]),
            )
            assert resp.status == 409
            stored = (await (await c.get("/api/memory/history")).json())["content"]
        assert "Shipped the stale-write fix." in stored and "my note" not in stored

    @pytest.mark.asyncio
    async def test_a_remembered_preference_between_read_and_save_is_not_undone(
        self, memory
    ) -> None:
        async with TestClient(TestServer(_memory_app(memory))) as c:
            read = await (await c.get("/api/memory/preferences")).json()
            memory.add_preference("Prefers tea.")  # the agent's `memory_remember` tool
            resp = await c.put(
                "/api/memory/preferences",
                json={"content": read["content"] + "- my line\n"},
                headers=_based_on(read["revision"]),
            )
            assert resp.status == 409
        assert "Prefers tea." in memory.read_preferences()
        assert "my line" not in memory.read_preferences()

    @pytest.mark.asyncio
    async def test_the_save_answers_what_is_stored_and_its_revision(self, memory) -> None:
        # `write_projects` adds its header to a body without one, so what is stored is NOT what
        # was sent — the answer has to carry the stored text, or the next save names the wrong
        # revision.
        async with TestClient(TestServer(_memory_app(memory))) as c:
            base = (await (await c.get("/api/memory/projects")).json())["revision"]
            saved = await (
                await c.put(
                    "/api/memory/projects",
                    json={"content": "- the roofing job"},
                    headers=_based_on(base),
                )
            ).json()
            read = await (await c.get("/api/memory/projects")).json()
        assert saved["content"].startswith("# Active Projects")
        assert saved["content"] == read["content"]
        assert saved["revision"] == read["revision"] == revision_of(read["content"])


# ── PUT /api/legibility/always-on/doc ─────────────────────────────────────────────────────


OVERVIEW = "The roof deck is stripped; the membrane is on order."
OVERVIEW_ID = "project_instruction:overview.md"


def _always_on_app() -> web.Application:
    from personalclaw.dashboard.handlers.legibility import (
        api_always_on_doc,
        api_always_on_doc_write,
    )

    app = web.Application()
    app.router.add_get("/api/legibility/always-on/doc", api_always_on_doc)
    app.router.add_put("/api/legibility/always-on/doc", api_always_on_doc_write)
    return app


@pytest.fixture
def project():
    from personalclaw.tasks.hierarchy import HierarchyStore

    return HierarchyStore().create_project("Roofing Rebuild")


async def _read_overview(c: TestClient, project_id: str) -> dict:
    resp = await c.get(
        "/api/legibility/always-on/doc", params={"id": OVERVIEW_ID, "project_id": project_id}
    )
    assert resp.status == 200, await resp.text()
    return await resp.json()


def _save_overview(c: TestClient, project_id: str, body: str, base: str | None):
    return c.put(
        "/api/legibility/always-on/doc",
        json={"id": OVERVIEW_ID, "project_id": project_id, "body": body},
        headers=_based_on(base) if base is not None else {},
    )


class TestProjectOverview:
    @pytest.mark.asyncio
    async def test_two_saves_from_one_read_the_second_is_refused(self, project) -> None:
        from personalclaw import project_context

        assert project_context.write_overview(project.id, OVERVIEW)
        async with TestClient(TestServer(_always_on_app())) as c:
            read = await _read_overview(c, project.id)
            assert read["revision"] == revision_of(read["body"])
            first = await _save_overview(c, project.id, OVERVIEW + " Tab A.", read["revision"])
            assert first.status == 200
            second = await _save_overview(c, project.id, OVERVIEW + " Tab B.", read["revision"])
            assert second.status == 409
            assert await _code(second) == "stale_write"
        assert project_context.read_overview(project.id) == OVERVIEW + " Tab A."

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_revision_is_refused(self, project) -> None:
        from personalclaw import project_context

        assert project_context.write_overview(project.id, OVERVIEW)
        async with TestClient(TestServer(_always_on_app())) as c:
            resp = await _save_overview(c, project.id, "overwritten", None)
            assert resp.status == 428
            assert await _code(resp) == "revision_required"
        assert project_context.read_overview(project.id) == OVERVIEW

    @pytest.mark.asyncio
    async def test_a_completed_runs_line_between_read_and_save_is_not_undone(self, project) -> None:
        # Every workflow run that completes in the project appends a line to its overview.
        from personalclaw import project_context
        from personalclaw.workflows import run_finish
        from personalclaw.workflows.models import RunStatus

        assert project_context.write_overview(project.id, OVERVIEW)
        async with TestClient(TestServer(_always_on_app())) as c:
            read = await _read_overview(c, project.id)
            run = SimpleNamespace(
                id="run-1",
                project_id=project.id,
                workflow_name="deploy",
                status=RunStatus.COMPLETE,
                extra={"summary": "shipped"},
            )
            # What the controller's terminal write runs for a run in a project.
            run_finish.revise_project_overview(SimpleNamespace(run=run))
            assert "deploy" in project_context.read_overview(project.id), "the run wrote nothing"
            resp = await _save_overview(c, project.id, OVERVIEW + " Mine.", read["revision"])
            assert resp.status == 409
        stored = project_context.read_overview(project.id)
        assert "- deploy → complete: shipped" in stored and "Mine." not in stored

    @pytest.mark.asyncio
    async def test_creating_the_overview_needs_no_revision(self, project) -> None:
        # A project with no overview yet has nothing a read could hand a revision for.
        from personalclaw import project_context

        async with TestClient(TestServer(_always_on_app())) as c:
            resp = await _save_overview(c, project.id, "A brand-new overview.", None)
            assert resp.status == 200, await resp.text()
            assert (await resp.json())["item"]["revision"] == revision_of("A brand-new overview.")
        assert project_context.read_overview(project.id) == "A brand-new overview."

    @pytest.mark.asyncio
    async def test_a_save_over_an_overview_emptied_since_is_refused(self, project) -> None:
        from personalclaw import project_context

        assert project_context.write_overview(project.id, OVERVIEW)
        async with TestClient(TestServer(_always_on_app())) as c:
            read = await _read_overview(c, project.id)
            assert project_context.write_overview(project.id, "")  # emptied elsewhere
            resp = await _save_overview(c, project.id, OVERVIEW + " Mine.", read["revision"])
            assert resp.status == 409
            assert await _code(resp) == "stale_write"
        assert project_context.read_overview(project.id) == ""


# ── PUT /api/projects/{id} with agent_instructions_template ───────────────────────────────


def _projects_app() -> web.Application:
    from personalclaw.tasks.hierarchy_handlers import api_projects_get, api_projects_update

    app = web.Application()
    app.router.add_get("/api/projects/{project_id}", api_projects_get)
    app.router.add_put("/api/projects/{project_id}", api_projects_update)
    return app


async def _read_template(c: TestClient, project_id: str) -> tuple[str, str]:
    body = await (await c.get(f"/api/projects/{project_id}")).json()
    return body["agent_instructions_template"], body["revisions"]["agent_instructions_template"]


class TestProjectInstructions:
    @pytest.mark.asyncio
    async def test_two_saves_from_one_read_the_second_is_refused(self, project) -> None:
        from personalclaw.tasks.hierarchy import HierarchyStore

        async with TestClient(TestServer(_projects_app())) as c:
            text, base = await _read_template(c, project.id)
            assert base == revision_of(text)
            first = await c.put(
                f"/api/projects/{project.id}",
                json={"agent_instructions_template": "Prefer primary sources."},
                headers=_based_on(base),
            )
            assert first.status == 200
            second = await c.put(
                f"/api/projects/{project.id}",
                json={"agent_instructions_template": "Reject a summary that cites none."},
                headers=_based_on(base),
            )
            assert second.status == 409
            assert await _code(second) == "stale_write"
        stored = HierarchyStore().get_project(project.id).agent_instructions_template
        assert stored == "Prefer primary sources."

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_revision_is_refused_and_a_scalar_needs_none(
        self, project
    ) -> None:
        from personalclaw.tasks.hierarchy import HierarchyStore

        async with TestClient(TestServer(_projects_app())) as c:
            resp = await c.put(
                f"/api/projects/{project.id}", json={"agent_instructions_template": "blank it"}
            )
            assert resp.status == 428
            assert await _code(resp) == "revision_required"
            # Control: a rename is one scalar written over another, the edit the user made.
            renamed = await c.put(f"/api/projects/{project.id}", json={"name": "Roof Rebuild v2"})
            assert renamed.status == 200
        stored = HierarchyStore().get_project(project.id)
        assert stored.agent_instructions_template == "" and stored.name == "Roof Rebuild v2"

    @pytest.mark.asyncio
    async def test_an_accepted_instruction_between_read_and_save_is_not_undone(
        self, project
    ) -> None:
        from personalclaw.learning.project_context_review import _install_instruction
        from personalclaw.tasks.hierarchy import HierarchyStore

        async with TestClient(TestServer(_projects_app())) as c:
            _text, base = await _read_template(c, project.id)
            # Accepting a learning proposal appends an instruction while the dialog is open.
            assert _install_instruction(project.id, "Run make lint before pushing.")
            resp = await c.put(
                f"/api/projects/{project.id}",
                json={"agent_instructions_template": "Cite primary sources."},
                headers=_based_on(base),
            )
            assert resp.status == 409
        stored = HierarchyStore().get_project(project.id).agent_instructions_template
        assert stored == "Run make lint before pushing."

    @pytest.mark.asyncio
    async def test_the_save_answers_the_revision_the_next_read_reports(self, project) -> None:
        async with TestClient(TestServer(_projects_app())) as c:
            _text, base = await _read_template(c, project.id)
            saved = await (
                await c.put(
                    f"/api/projects/{project.id}",
                    json={"agent_instructions_template": "Prefer primary sources."},
                    headers=_based_on(base),
                )
            ).json()
            _text, again = await _read_template(c, project.id)
        assert saved["revisions"]["agent_instructions_template"] == again != base


# ── PUT /api/workflows/runs/{run_id}/policy-overrides ─────────────────────────────────────


@pytest.fixture
def runs(tmp_path, monkeypatch):
    home = tmp_path / "runs-home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    from personalclaw.workflows import store

    return store


def _runs_app() -> web.Application:
    from personalclaw.workflows import handlers as H

    app = web.Application()
    app["state"] = None
    app.router.add_get("/api/workflows/runs/{run_id}", H.api_run_status)
    app.router.add_put("/api/workflows/runs/{run_id}/policy-overrides", H.api_run_policy_overrides)
    return app


async def _read_overlay(c: TestClient, run_id: str) -> tuple[dict, str]:
    body = await (await c.get(f"/api/workflows/runs/{run_id}")).json()
    return body["policy_overrides"], body["revisions"]["policy_overrides"]


class TestRunPolicyOverrides:
    @pytest.mark.asyncio
    async def test_two_saves_from_one_read_the_second_is_refused(self, runs) -> None:
        from personalclaw.workflows.models import WorkflowRun

        run = runs.create(WorkflowRun(id="", workflow_name="w"))
        async with TestClient(TestServer(_runs_app())) as c:
            overlay, base = await _read_overlay(c, run.id)
            assert base == revision_of(overlay)
            url = f"/api/workflows/runs/{run.id}/policy-overrides"
            first = await c.put(url, json={**overlay, "max_cycles": 3}, headers=_based_on(base))
            assert first.status == 200
            # The second tab overrides a different knob — from the copy without max_cycles.
            second = await c.put(url, json={**overlay, "idle_secs": 30}, headers=_based_on(base))
            assert second.status == 409
            assert await _code(second) == "stale_write"
        assert runs.get(run.id).policy_overrides == {"max_cycles": 3}

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_revision_is_refused(self, runs) -> None:
        from personalclaw.workflows.models import WorkflowRun

        run = runs.create(WorkflowRun(id="", workflow_name="w"))
        async with TestClient(TestServer(_runs_app())) as c:
            resp = await c.put(
                f"/api/workflows/runs/{run.id}/policy-overrides", json={"max_cycles": 3}
            )
            assert resp.status == 428
            assert await _code(resp) == "revision_required"
        assert runs.get(run.id).policy_overrides == {}

    @pytest.mark.asyncio
    async def test_consent_does_not_carry_a_stale_overlay(self, runs) -> None:
        # The stale check runs before the consent check: consenting to loosen the copy on screen
        # is consent about the wrong overlay.
        from personalclaw.workflows.models import WorkflowRun

        run = runs.create(WorkflowRun(id="", workflow_name="w"))
        async with TestClient(TestServer(_runs_app())) as c:
            overlay, base = await _read_overlay(c, run.id)
            url = f"/api/workflows/runs/{run.id}/policy-overrides"
            assert (await c.put(url, json={"max_cycles": 3}, headers=_based_on(base))).status == 200
            resp = await c.put(
                url, json={"autopilot": True, "confirm": True}, headers=_based_on(base)
            )
            assert resp.status == 409
            assert await _code(resp) == "stale_write"
        assert runs.get(run.id).policy_overrides == {"max_cycles": 3}

    @pytest.mark.asyncio
    async def test_a_launched_run_answers_that_it_launched_whatever_the_base(self, runs) -> None:
        # A control, green before and after: a launched run's overlay is frozen, and saying so
        # beats asking for a revision the write could never use.
        from personalclaw.workflows.models import RunStatus, WorkflowRun

        run = runs.create(WorkflowRun(id="", workflow_name="w", status=RunStatus.RUNNING))
        async with TestClient(TestServer(_runs_app())) as c:
            resp = await c.put(
                f"/api/workflows/runs/{run.id}/policy-overrides", json={"max_cycles": 2}
            )
            assert resp.status == 409
            assert await _code(resp) == "run_not_prelaunch"

    @pytest.mark.asyncio
    async def test_the_save_answers_the_revision_the_next_read_reports(self, runs) -> None:
        from personalclaw.workflows.models import WorkflowRun

        run = runs.create(WorkflowRun(id="", workflow_name="w"))
        async with TestClient(TestServer(_runs_app())) as c:
            _overlay, base = await _read_overlay(c, run.id)
            saved = await (
                await c.put(
                    f"/api/workflows/runs/{run.id}/policy-overrides",
                    json={"attended": True},
                    headers=_based_on(base),
                )
            ).json()
            _overlay, again = await _read_overlay(c, run.id)
        assert saved["revisions"]["policy_overrides"] == again != base
