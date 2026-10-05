"""An installed app cannot write what a project gives every session in it, and still makes and
reads projects.

**The hole, measured with the fixture app below before this change.** ``/api/projects`` sat in no
``SECURITY_ROUTE_FAMILIES`` root, so an app that declared it (the shipped ``minutes`` and ``growth``
apps do) reached every route under it:

1. ``PUT /api/projects/{project_id}`` answered the app 200 and replaced a project's brief and its
   instructions. Every chat and loop in the project is given the brief as the project's goal, and an
   agent that loads the project's context (``get_context``) is given both as its rules, unfenced,
   the way it is given your own words.
2. ``POST /api/projects`` made a project carrying a brief, instructions and a folder of the app's
   choosing; the folder is where the project's chats, loops and runs then work.
3. ``POST /api/projects/import`` made a project out of an archive the app sent, its brief,
   instructions and overview included.
4. Every other write answered the app the same way: changing or deleting a project, the default
   project, a claim on its Work board, and writing PersonalClaw's block into the instruction files
   in a project's folder.
5. The file explorer kept a project's own folder among an app's roots, so an app that declared
   ``/api/file-write`` replaced the project's overview past the owner-only overview write, and one
   that declared ``/api/file-create`` added a ledger the project's chats are then given.

Each is the owner's now, refused in the registry's words with a security-log row naming the app,
and the owner's own request still runs. What stays the app's: making a project under a name, as
``minutes`` does when it turns a meeting's action items into tasks, and every read (``growth`` reads
the list).

Same harness as ``test_apps_cannot_change_your_models.py``: the real token middleware and the real
``app_permission_middleware``, a fixture app installed through preview → consent → install, and the
token minted for it, sent both ways an app sends one. The route matrix answers with a stand-in at
each real template, so no case there changes anything; the brief, instructions, create, import,
context-file and explorer cases run the real handlers on a scratch home.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from test_apps_cannot_change_your_models import _gateway, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers import context as context_handlers
from personalclaw.dashboard.handlers import files as file_handlers
from personalclaw.legibility import context_router
from personalclaw.tasks import hierarchy_handlers as project_handlers
from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.tasks.models import Project
from personalclaw.workflows import project_archive
from personalclaw.workflows.project_export import plan_export

APP = "probe-projects"
PROJECTS = "/api/projects"
#: The task lists too, as `minutes` declares them: it files a meeting's action items in a list
#: under the project it makes.
DECLARES = [PROJECTS, "/api/task-lists"]
OWNER_ONLY = "owner-only capability, not grantable to an app: "

#: The project the route matrix names, where a stand-in answers every route.
PROJECT_ID = "p-garden01"

YOUR_BRIEF = "Grow enough vegetables for the summer: plan the beds, order seeds, log the harvest."
YOUR_STEPS = "Write each plan as a checklist, and note what was planted with its date."
YOUR_EDIT = "Grow enough vegetables for the summer and the autumn, and keep a harvest log."
YOUR_EDITED_STEPS = "Write each plan as a checklist, and note what was harvested with its weight."
THE_APPS_BRIEF = "The app's own goal for the garden project, sent in place of yours."
THE_APPS_STEPS = "The app's own way of working in the garden project, sent in place of yours."

#: What `minutes` sends when it turns a meeting's action items into tasks (``ActionsToTasks`` in
#: `minutes/ui/src/index.tsx`): a project under the name you typed, then a task list under it.
MINUTES_PROJECT = {"name": "Planning sync"}
MINUTES_LIST = "Planning sync — action items"

#: What every session in a project is given, or works in, by the field of a create that writes it,
#: with what an app would send. An empty brief is still a brief named: the refusal is decided on
#: the field, before its value is read.
THE_PROJECTS_OWN: list[tuple[str, str]] = [
    ("brief", THE_APPS_BRIEF),
    ("brief", ""),
    ("agent_instructions_template", THE_APPS_STEPS),
    ("workspace_dir", "/srv/garden-plan"),
]

#: Every write only you make, with what a call would send.
YOUR_CHANGES: list[tuple[str, str, Any]] = [
    ("PUT", "/api/projects/{project_id}", {"brief": THE_APPS_BRIEF}),
    ("DELETE", "/api/projects/{project_id}", None),
    ("POST", "/api/projects/import", None),
    ("PUT", "/api/projects/settings", {"default_project_id": PROJECT_ID}),
    ("POST", "/api/projects/{project_id}/work/claim", {"target_id": "t-1", "holder": APP}),
    ("POST", "/api/projects/{project_id}/work/release", {"target_id": "t-1", "holder": APP}),
    ("POST", "/api/projects/{project_id}/context-adapters/regenerate", {}),
]

#: What an app you granted your projects still reaches.
STILL_THE_APPS: list[tuple[str, str, Any]] = [
    ("GET", "/api/projects", None),
    ("POST", "/api/projects", MINUTES_PROJECT),
    ("GET", "/api/projects/settings", None),
    ("GET", "/api/projects/{project_id}", None),
    ("GET", "/api/projects/{project_id}/export", None),
    ("GET", "/api/projects/{project_id}/linked", None),
    ("GET", "/api/projects/{project_id}/work", None),
]


def _bundle(root: Path, api: list[str]) -> Path:
    """A fixture app that declares *api* (your projects and task lists, as `minutes` does, unless a
    test says otherwise) and ships nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Projects",
        "description": "A fixture app that declares the routes your projects live on.",
        "permissions": {"api": api},
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch PersonalClaw home and ``HOME``, and a folder a project may work in."""
    import personalclaw.config.loader as loader

    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    pc = tmp_path / "pc-home"
    pc.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(manager, "config_dir", lambda: pc)
    folder = tmp_path / "garden-plan"
    folder.mkdir()
    return SimpleNamespace(pc=pc, folder=folder)


@pytest.fixture(autouse=True)
def _fresh_sessions() -> Iterator[None]:
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    yield
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


def _denials(rows: MagicMock, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("source") == "app_permissions"
        and c.kwargs.get("resources") == path
    ]


def _capability(method: str, template: str) -> str:
    """What the route grants, in the registry's words, or ``""`` when no row says it is yours."""
    from personalclaw.apps.permissions import OwnerOnly, route_authz

    authz = route_authz(method, template)
    return authz.capability if isinstance(authz, OwnerOnly) else ""


def _field_capability(field: str) -> str:
    """What writing *field* in a create is, in the registry's words, or ``""`` when no row says it
    is yours."""
    from personalclaw.apps.permissions import AppMay, route_authz

    authz = route_authz("POST", PROJECTS)
    owned = authz.owner_only_fields if isinstance(authz, AppMay) else ()
    return next((o.capability for o in owned if o.field == field), "")


def _path(template: str, project_id: str = PROJECT_ID) -> str:
    return template.replace("{project_id}", project_id)


async def _installed(gw: _Gateway, home: SimpleNamespace, api: list[str] = DECLARES) -> str:
    """Your install of the fixture app, and the token you mint for it."""
    await gw.install(_bundle(home.pc, api))
    return str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])


def _registered_in_order(routes: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
    """*routes*, the fixed paths first, as the gateway registers them: `settings` and `import`
    would otherwise be read as a project id."""
    return sorted(routes, key=lambda route: "{" in route[1])


def _stand_ins(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """A stand-in at every template in both lists, recording who reached it."""

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.method))
        return web.json_response({"reached": True})

    return _registered_in_order([(m, t, stand_in) for m, t, _ in STILL_THE_APPS + YOUR_CHANGES])


def _real_routes() -> list[tuple[str, str, Any]]:
    """The real handlers, at the templates the gateway registers them under."""
    h = project_handlers
    return _registered_in_order(
        [
            ("POST", "/api/projects/import", h.api_projects_import),
            ("GET", "/api/projects", h.api_projects_list),
            ("POST", "/api/projects", h.api_projects_create),
            ("GET", "/api/projects/{project_id}", h.api_projects_get),
            ("PUT", "/api/projects/{project_id}", h.api_projects_update),
            (
                "POST",
                "/api/projects/{project_id}/context-adapters/regenerate",
                context_handlers.api_project_context_regenerate,
            ),
            ("GET", "/api/context", context_handlers.api_context_get),
            ("POST", "/api/task-lists", h.api_task_lists_create),
        ]
    )


async def _names(gw: _Gateway) -> list[str]:
    """Your projects' names, as you read them."""
    return sorted(p["name"] for p in (await gw.ok("GET", PROJECTS))["projects"])


@pytest.fixture
def routed_context_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routed context with no memory, knowledge or skills behind it: the project's own text
    alone, as ``GET /api/context`` renders it."""
    monkeypatch.setattr(context_handlers, "_memory_service", lambda state, project: None)
    monkeypatch.setattr(context_handlers, "_knowledge_retriever", lambda: None)
    monkeypatch.setattr(context_handlers, "_skills_index", lambda query: [])


async def _given(gw: _Gateway, project_id: str) -> dict[str, str]:
    """What the project's sessions are given of it, read where each is put together: a chat's
    first-turn preamble, a loop's brief, and the routed context an agent loads (``get_context``
    reads ``GET /api/context`` and hands the agent its ``text``)."""
    from personalclaw.dashboard.chat_utils import _project_context_preamble
    from personalclaw.loop.manager import _project_brief_block

    routed = await gw.ok("GET", f"/api/context?project_id={project_id}")
    return {
        "chat": _project_context_preamble(project_id),
        "loop": _project_brief_block(SimpleNamespace(id="l-garden", project_id=project_id)),
        "get_context": routed["text"],
    }


ARCHIVED_BRIEF = "Keep the shared allotment watered through August."


def _upload() -> aiohttp.FormData:
    """A project archive from somewhere else: its record carries a brief and instructions, and its
    folder an overview."""
    record = {"brief": ARCHIVED_BRIEF, "agent_instructions_template": THE_APPS_STEPS}
    files = {
        "project.json": json.dumps(record).encode(),
        "context/overview.md": b"The allotment rota is agreed for July.",
    }
    plan = plan_export("p-elsewhere", project_name="Shared allotment", files=files)
    form = aiohttp.FormData()
    form.add_field(
        "file",
        project_archive.write_archive(plan, files),
        filename="shared-allotment.zip",
        content_type="application/zip",
    )
    return form


# ── 1. Every change to a project, and what its sessions are given, is yours ─────────────────────


class TestAnAppCannotChangeYourProjects:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "body"),
        YOUR_CHANGES,
        ids=[f"{m} {t}" for m, t, _ in YOUR_CHANGES],
    )
    async def test_an_app_is_refused_in_the_registrys_words_and_you_are_not(
        self, home, sel_rows, method, template, body
    ) -> None:
        reached: list[tuple[str, str]] = []
        path = _path(template)
        async with _gateway(_stand_ins(reached)) as gw:
            token = await _installed(gw, home)
            as_sdk = await gw.call(method, path, body, app_token=token)
            as_backend = await gw.call(method, path, body, app_token=token, as_backend=True)
            yours = await gw.call(method, path, body)
        capability = _capability(method, template)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert capability and f"{OWNER_ONLY}{capability}" in text, text
        assert len(_denials(sel_rows, path)) == 2, "each refusal leaves an SEL row for the app"
        assert yours[0] == 200, yours
        assert reached == [("", method)], "only your request reached the handler"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "value"),
        THE_PROJECTS_OWN,
        ids=[f"{f}={v!r}"[:40] for f, v in THE_PROJECTS_OWN],
    )
    async def test_a_project_an_app_makes_carries_nothing_its_sessions_are_given(
        self, home, sel_rows, field, value
    ) -> None:
        reached: list[tuple[str, str]] = []
        body = {**MINUTES_PROJECT, field: value}
        async with _gateway(_stand_ins(reached)) as gw:
            token = await _installed(gw, home)
            as_sdk = await gw.call("POST", PROJECTS, body, app_token=token)
            as_backend = await gw.call("POST", PROJECTS, body, app_token=token, as_backend=True)
            yours = await gw.call("POST", PROJECTS, body)
        capability = _field_capability(field)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert capability and f"{OWNER_ONLY}{capability}" in text, text
        assert len(_denials(sel_rows, PROJECTS)) == 2, "each refusal leaves an SEL row for the app"
        assert yours[0] == 200, yours
        assert reached == [("", "POST")], "only your request reached the handler"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "body"),
        STILL_THE_APPS,
        ids=[f"{m} {t}" for m, t, _ in STILL_THE_APPS],
    )
    async def test_an_app_still_reaches_what_you_granted(
        self, home, sel_rows, method, template, body
    ) -> None:
        reached: list[tuple[str, str]] = []
        path = _path(template)
        async with _gateway(_stand_ins(reached)) as gw:
            token = await _installed(gw, home)
            as_sdk = await gw.call(method, path, body, app_token=token)
            as_backend = await gw.call(method, path, body, app_token=token, as_backend=True)
            yours = await gw.call(method, path, body)
        assert [as_sdk[0], as_backend[0], yours[0]] == [200, 200, 200], (as_sdk, as_backend)
        assert reached == [(APP, method), (APP, method), ("", method)]
        assert not _denials(sel_rows, path)


# ── 2. What a refusal keeps from an app, measured on the real handlers ──────────────────────────


class TestWhatAnAppNoLongerDoes:
    @pytest.mark.asyncio
    async def test_it_cannot_rewrite_a_projects_brief_or_instructions_and_you_still_can(
        self, home, sel_rows, routed_context_alone
    ) -> None:
        yours = {"name": "Garden", "brief": YOUR_BRIEF, "agent_instructions_template": YOUR_STEPS}
        async with _gateway(_real_routes()) as gw:
            token = await _installed(gw, home)
            pid = (await gw.ok("POST", PROJECTS, yours))["id"]
            path = f"{PROJECTS}/{pid}"
            record = home.pc / "projects" / pid / "project.json"
            as_you_wrote_it = record.read_bytes()
            given_before = await _given(gw, pid)
            read = await gw.call("GET", path, app_token=token, as_backend=True)
            assert read[0] == 200, read
            # The app names the revision it read, so its write is refused for being the app's and
            # for nothing else.
            names_it = {"If-Match": json.loads(read[1])["revisions"]["agent_instructions_template"]}
            brief = await gw.call("PUT", path, {"brief": THE_APPS_BRIEF}, app_token=token)
            steps = await gw.call(
                "PUT",
                path,
                {"agent_instructions_template": THE_APPS_STEPS},
                app_token=token,
                as_backend=True,
                headers=names_it,
            )
            after_the_app = record.read_bytes()
            given_after = await _given(gw, pid)
            your_brief = await gw.call("PUT", path, {"brief": YOUR_EDIT})
            your_steps = await gw.call(
                "PUT", path, {"agent_instructions_template": YOUR_EDITED_STEPS}, headers=names_it
            )
            given_after_yours = await _given(gw, pid)
        for status, text in (brief, steps):
            assert status == 403, text
            assert "changing one of your projects" in text, text
        assert after_the_app == as_you_wrote_it, "the app's request rewrote your project"
        assert given_after == given_before, "what the project's sessions are given changed"
        assert YOUR_BRIEF in given_after["chat"] and YOUR_BRIEF in given_after["loop"]
        assert YOUR_BRIEF in given_after["get_context"] and YOUR_STEPS in given_after["get_context"]
        for text in given_after.values():
            assert THE_APPS_BRIEF not in text and THE_APPS_STEPS not in text, text
        assert len(_denials(sel_rows, path)) == 2
        assert [your_brief[0], your_steps[0]] == [200, 200], (your_brief, your_steps)
        now = HierarchyStore().get_project(pid)
        assert (now.brief, now.agent_instructions_template) == (YOUR_EDIT, YOUR_EDITED_STEPS)
        # The same readers show your edit, so what they showed after the app's was no stale copy.
        assert YOUR_EDIT in given_after_yours["chat"] and YOUR_EDIT in given_after_yours["loop"]
        assert YOUR_EDITED_STEPS in given_after_yours["get_context"]

    @pytest.mark.asyncio
    async def test_it_cannot_make_a_project_its_sessions_are_told_about_and_you_still_can(
        self, home, sel_rows
    ) -> None:
        asked = {
            "name": "Garden",
            "brief": THE_APPS_BRIEF,
            "agent_instructions_template": THE_APPS_STEPS,
            "workspace_dir": str(home.folder),
        }
        async with _gateway(_real_routes()) as gw:
            token = await _installed(gw, home)
            before = await _names(gw)
            as_sdk = await gw.call("POST", PROJECTS, asked, app_token=token)
            as_backend = await gw.call("POST", PROJECTS, asked, app_token=token, as_backend=True)
            after_the_app = await _names(gw)
            yours = await gw.call(
                "POST",
                PROJECTS,
                {**asked, "brief": YOUR_BRIEF, "agent_instructions_template": YOUR_STEPS},
            )
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "writing a project's brief" in text, text
        assert after_the_app == before, "the app's request made a project"
        assert len(_denials(sel_rows, PROJECTS)) == 2
        assert yours[0] == 201, yours
        made = HierarchyStore().get_project(json.loads(yours[1])["id"])
        assert (made.brief, made.agent_instructions_template, made.workspace_dir) == (
            YOUR_BRIEF,
            YOUR_STEPS,
            str(home.folder),
        )

    @pytest.mark.asyncio
    async def test_minutes_still_files_a_meetings_action_items_under_a_project_it_makes(
        self, home, sel_rows
    ) -> None:
        async with _gateway(_real_routes()) as gw:
            token = await _installed(gw, home)
            made = await gw.call("POST", PROJECTS, MINUTES_PROJECT, app_token=token)
            assert made[0] == 201, made
            pid = json.loads(made[1])["id"]
            listed = await gw.call(
                "POST",
                "/api/task-lists",
                {"name": MINUTES_LIST, "project_id": pid},
                app_token=token,
            )
            read = await gw.call("GET", PROJECTS, app_token=token, as_backend=True)
        assert listed[0] == 201, listed
        assert json.loads(listed[1])["project_id"] == pid
        project = HierarchyStore().get_project(pid)
        assert project.name == MINUTES_PROJECT["name"]
        assert (project.brief, project.agent_instructions_template, project.workspace_dir) == (
            "",
            "",
            "",
        )
        assert MINUTES_PROJECT["name"] in [p["name"] for p in json.loads(read[1])["projects"]]
        assert not [c for c in sel_rows.call_args_list if c.kwargs.get("outcome") == "denied"]

    @pytest.mark.asyncio
    async def test_it_cannot_import_a_project_and_you_still_can(self, home, sel_rows) -> None:
        path = f"{PROJECTS}/import"
        async with _gateway(_real_routes()) as gw:
            token = await _installed(gw, home)
            before = await _names(gw)
            as_sdk = await gw.call("POST", path, app_token=token, form=_upload())
            as_backend = await gw.call(
                "POST", path, app_token=token, as_backend=True, form=_upload()
            )
            after_the_app = await _names(gw)
            yours = await gw.call("POST", path, form=_upload())
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "importing a project" in text, text
        assert after_the_app == before, "the app's archive became a project"
        assert len(_denials(sel_rows, path)) == 2
        assert yours[0] == 201, yours
        imported = HierarchyStore().get_project(json.loads(yours[1])["project_id"])
        assert imported.brief == ARCHIVED_BRIEF

    @pytest.mark.asyncio
    async def test_it_cannot_write_the_instruction_files_in_a_projects_folder(
        self, home, sel_rows, monkeypatch
    ) -> None:
        (home.pc / "config.json").write_text(json.dumps({"legibility": {"context_adapters": True}}))
        # The routed context with no store behind it: the brief and the instructions alone.
        monkeypatch.setattr(
            context_handlers,
            "_route_for_project",
            lambda state, project, query, withheld="": context_router.route_context(project),
        )
        yours = {"name": "Garden", "brief": YOUR_BRIEF, "workspace_dir": str(home.folder)}
        async with _gateway(_real_routes()) as gw:
            token = await _installed(gw, home)
            pid = (await gw.ok("POST", PROJECTS, yours))["id"]
            path = f"{PROJECTS}/{pid}/context-adapters/regenerate"
            as_sdk = await gw.call("POST", path, {}, app_token=token)
            as_backend = await gw.call("POST", path, {}, app_token=token, as_backend=True)
            after_the_app = sorted(p.name for p in home.folder.iterdir())
            regenerated = await gw.call("POST", path, {})
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "the instruction files in a project's folder" in text, text
        assert after_the_app == [], "the app's request wrote into the project's folder"
        assert len(_denials(sel_rows, path)) == 2
        assert regenerated[0] == 200, regenerated
        assert YOUR_BRIEF in (home.folder / "CLAUDE.md").read_text(encoding="utf-8")


# ── 3. The file explorer is no door to them ─────────────────────────────────────────────────────

#: The explorer's reads and writes, which an app may declare beside your projects.
THE_EXPLORER = ["/api/file-read", "/api/file-write", "/api/file-create"]
YOUR_OVERVIEW = "The beds are dug and the seed order went out on Monday."
THE_APPS_OVERVIEW = "The app's own account of the garden project, sent in place of yours."
THE_APPS_DECISION = "The app's own decision, filed as the project's."


def _explorer_routes() -> list[tuple[str, str, Any]]:
    """The real explorer handlers, at the paths the gateway registers them under."""
    h = file_handlers
    return [
        ("GET", "/api/file-read", h.api_file_read),
        ("POST", "/api/file-write", h.api_file_write),
        ("POST", "/api/file-create", h.api_file_create),
    ]


class TestTheExplorerKeepsAnAppOutOfAProjectsContext:
    """A chat in the project is given its overview and ledgers from the files in the project's
    context folder (``chat_utils._project_context_preamble``), so writing those files is writing
    what the chat is told. For an app the folder is no explorer root, as a loop's own folder is not:
    the explorer refuses it there, and the owner still browses and edits it."""

    def test_a_projects_context_folder_is_not_among_an_apps_roots(self, home) -> None:
        from personalclaw.apps.permissions import scoped_to_app
        from personalclaw.file_roots import dashboard_roots

        store = HierarchyStore()
        folder = os.path.realpath(store.context_dir(store.create_project("Garden").id))
        assert folder in [r for _label, r in dashboard_roots()], "precondition: you browse it"
        with scoped_to_app(APP):
            assert folder not in [r for _label, r in dashboard_roots()]

    @pytest.mark.asyncio
    async def test_through_the_gateway_and_you_still_edit_it(self, home, sel_rows) -> None:
        from personalclaw import project_context
        from personalclaw.dashboard.chat_utils import _project_context_preamble
        from personalclaw.stale_write import revision_of

        store = HierarchyStore()
        pid = store.create_project("Garden", brief=YOUR_BRIEF).id
        assert project_context.write_overview(pid, YOUR_OVERVIEW)
        context = store.context_dir(pid)
        overview = context / project_context.OVERVIEW_FILE
        as_you_wrote_it = overview.read_bytes()
        # The revision the app would have read, so its write is refused for being the app's.
        names_it = {"If-Match": revision_of(overview.read_text(encoding="utf-8"))}
        theirs = {"path": str(overview), "content": THE_APPS_OVERVIEW}
        ledger = {"path": str(context), "name": "decisions.md", "content": f"- {THE_APPS_DECISION}"}
        async with _gateway(_explorer_routes()) as gw:
            token = await _installed(gw, home, THE_EXPLORER)
            read = await gw.call(
                "GET", f"/api/file-read?path={overview}", app_token=token, as_backend=True
            )
            write = await gw.call(
                "POST", "/api/file-write", theirs, app_token=token, headers=names_it
            )
            create = await gw.call(
                "POST", "/api/file-create", ledger, app_token=token, as_backend=True
            )
            after_the_app = (overview.read_bytes(), sorted(p.name for p in context.iterdir()))
            preamble = _project_context_preamble(pid)
            yours = await gw.call(
                "POST", "/api/file-write", {**theirs, "content": YOUR_EDIT}, headers=names_it
            )
        assert [read[0], write[0], create[0]] == [403, 403, 403], (read, write, create)
        assert after_the_app == (as_you_wrote_it, [project_context.OVERVIEW_FILE]), after_the_app
        assert YOUR_OVERVIEW in preamble, preamble
        assert THE_APPS_OVERVIEW not in preamble and THE_APPS_DECISION not in preamble, preamble
        assert yours[0] == 200, yours
        assert project_context.read_overview(pid) == YOUR_EDIT


# ── 4. The route table declares the family ──────────────────────────────────────────────────────


def _census() -> set[str]:
    from personalclaw.manifest_reference import _routes_from_ast

    return {
        f"{r['method']} {r['path']}"
        for r in _routes_from_ast()
        if r["path"] == PROJECTS or r["path"].startswith(PROJECTS + "/")
    }


#: Every field of a project that is not one of the words its sessions are given: the store's own,
#: and what a project is called and whether it is in use — a label and two switches.
NOT_WHAT_A_SESSION_FOLLOWS = {
    "id",
    "is_builtin",
    "origin_harness",
    "created_at",
    "updated_at",
    "name",
    "name_locked",
    "status",
}


class TestTheRouteTableDeclaresYourProjects:
    def test_it_is_a_family_whose_reads_stay_the_allowlists(self) -> None:
        from personalclaw.apps.permissions import READ_DECLARED_FAMILIES, SECURITY_ROUTE_FAMILIES

        assert PROJECTS in SECURITY_ROUTE_FAMILIES
        assert PROJECTS not in READ_DECLARED_FAMILIES, "an app keeps its reads"

    def test_every_route_in_the_family_is_driven_here(self) -> None:
        census = _census()
        assert len(census) >= 14, f"only {len(census)} routes in the family — vacuous"
        driven = {f"{m} {t}" for m, t, _body in YOUR_CHANGES + STILL_THE_APPS}
        assert census == driven, census ^ driven

    def test_each_change_is_yours_and_the_create_an_app_keeps_says_why(self) -> None:
        from personalclaw.apps.permissions import AppMay, OwnerOnly, route_authz

        for method, template, _body in YOUR_CHANGES:
            assert isinstance(route_authz(method, template), OwnerOnly), f"{method} {template}"
        for method, template, _body in STILL_THE_APPS:
            authz = route_authz(method, template)
            if method == "GET":
                assert authz is None, f"{method} {template} is the allowlist's, as it was"
            else:
                assert isinstance(authz, AppMay) and authz.reason.strip(), f"{method} {template}"

    def test_the_create_refuses_an_app_every_field_a_projects_sessions_are_given(self) -> None:
        """Each field of a project is decided: a field added tomorrow fails here until it is named
        either the owner's (the create refuses it to an app) or one no session follows."""
        from personalclaw.apps.permissions import AppMay, route_authz

        authz = route_authz("POST", PROJECTS)
        assert isinstance(authz, AppMay)
        owners = {owned.field for owned in authz.owner_only_fields}
        assert owners == {field for field, _value in THE_PROJECTS_OWN}
        assert all(owned.capability.strip() for owned in authz.owner_only_fields)
        fields = {f.name for f in dataclasses.fields(Project)}
        assert fields == owners | NOT_WHAT_A_SESSION_FOLLOWS, fields ^ (
            owners | NOT_WHAT_A_SESSION_FOLLOWS
        )
