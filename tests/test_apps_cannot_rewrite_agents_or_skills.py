"""An installed app cannot rewrite what your agents follow, install a skill, or start a loop by a
side door — and install consent names every kind of code an app brings and says it runs as you.
The gaps #3614 found and left.

**The holes, each measured on ``main`` at #3614 (``bad3621e3``)**, with an app declaring the path:

1. ``PUT /api/agents/{name}`` rewrote an agent the OWNER made — ``system_prompt``, ``tools``,
   ``skills``, ``model`` — so the owner's next chat with it followed the app's instructions with the
   owner's tools. #3602 screened ``approval_mode`` and nothing else. ``POST /api/agents``,
   ``PATCH /api/agents/detail/{name}``, ``POST /api/agents/sync`` and both deletes reached their
   handlers too.
2. ``POST /api/skills/install``, ``POST /api/skills``, ``PUT /api/skills/{name}``, promoting a
   session draft and accepting a proposal installed or rewrote a skill — instructions every agent
   loads.
   ``POST /api/agent-marketplace/agents/{name}/activate`` made a definition one of the owner's
   agents, replacing one of the same name.
3. The same defect one level up. ``PUT /api/prompts/{name}`` and ``PUT /api/prompts/bindings``
   rewrote or rebound the system prompt every chat and unattended run starts from, and the judges'.
   ``POST /api/prompts/{name}/launch`` created AND STARTED a goal loop — whose worker approves its
   own tool calls — past #3614's owner-only ``POST /api/loops``. ``/api/prompt-snippets`` edits text
   those prompts include; ``/api/agent-metadata`` rewrites the routing note the orchestrator skill
   is built from; ``POST /api/onboarding/import`` copied MCP servers and skills in from the owner's
   other agent tools; ``POST /api/lessons`` wrote a lesson, which every agent is handed, without the
   ``memory`` grant ``/api/memory`` needs.
4. Install consent named a backend, the install/update hook and the MCP servers. A provider module
   (imported into the gateway), the enable/disable/uninstall hooks, the CLI setup/doctor steps, a
   connector pack's parser scripts and the skills an app ships appeared nowhere, and nothing said
   the code runs as you.
5. The file explorer refused an app a path in the home with the owner's ``400`` and no audit row
   naming the app.

The route cases drive the REAL ``app_permission_middleware`` against an app installed in a scratch
home, with #3614's harness, and read the Security Event Log row the refusal writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_apps_cannot_run_code_or_bypass_approvals import APP, _call, _denials, _home, _install

from personalclaw.apps import manager


@pytest.fixture
def sel_rows():
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


#: Every path the families below live under, so a refusal is never "path not declared".
DECLARED = [
    "/api/agents",
    "/api/agent-marketplace",
    "/api/agent-metadata",
    "/api/skills",
    "/api/prompts",
    "/api/prompt-snippets",
    "/api/onboarding",
    "/api/file-read",
    "/api/file-write",
    "/api/file-list",
]


async def _refused(tmp_path: Path, method: str, template: str, path: str) -> str:
    with _home(tmp_path):
        _install(tmp_path, APP, {"api": DECLARED})
        status, text = await _call(APP, method, template, path)
    assert status == 403, text
    assert "owner-only capability" in text, "the refusal names the capability, not a bare 403"
    return text


# ── 1. An agent's instructions are the owner's ──────────────────────────────────────


#: Every route that writes an agent the owner's chats run: its prompt, tools, skills, model,
#: approval mode, or whether it exists at all.
AGENT_WRITES = [
    ("POST", "/api/agents", "/api/agents"),
    ("PUT", "/api/agents/{name}", "/api/agents/helper"),
    ("PATCH", "/api/agents/detail/{name}", "/api/agents/detail/helper"),
    ("POST", "/api/agents/sync", "/api/agents/sync"),
    ("DELETE", "/api/agents/{name}", "/api/agents/helper"),
    ("DELETE", "/api/agents/detail/{name}", "/api/agents/detail/helper"),
    ("POST", "/api/agent-marketplace/agents", "/api/agent-marketplace/agents"),
    ("PUT", "/api/agent-marketplace/agents/{name}", "/api/agent-marketplace/agents/helper"),
    ("DELETE", "/api/agent-marketplace/agents/{name}", "/api/agent-marketplace/agents/helper"),
    (
        "POST",
        "/api/agent-marketplace/agents/{name}/activate",
        "/api/agent-marketplace/agents/helper/activate",
    ),
    (
        "POST",
        "/api/agent-marketplace/agents/{name}/test",
        "/api/agent-marketplace/agents/helper/test",
    ),
    ("PUT", "/api/agent-metadata/{name}", "/api/agent-metadata/helper"),
    ("DELETE", "/api/agent-metadata/{name}", "/api/agent-metadata/helper"),
]


class TestAnAppCannotRewriteYourAgents:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "path"), AGENT_WRITES)
    async def test_every_agent_write_is_refused(
        self, tmp_path, sel_rows, method, template, path
    ) -> None:
        await _refused(tmp_path, method, template, path)
        assert _denials(sel_rows, path), "the refusal leaves an SEL row naming the app and path"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "path"),
        [
            ("GET", "/api/agents", "/api/agents"),
            ("GET", "/api/agent-marketplace/agents", "/api/agent-marketplace/agents"),
            ("POST", "/api/agents/routing/dismiss", "/api/agents/routing/dismiss"),
            ("POST", "/api/agents/routing/unmute", "/api/agents/routing/unmute"),
        ],
    )
    async def test_reads_and_routing_suggestions_stay_an_apps(
        self, tmp_path, method, template, path
    ) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": DECLARED})
            status, text = await _call(APP, method, template, path)
        assert status == 200, text

    @pytest.mark.asyncio
    async def test_an_owner_agents_prompt_tools_and_approval_mode_stay_as_the_owner_left_them(
        self, tmp_path, sel_rows
    ) -> None:
        # The REAL update handler behind the real middleware, so "refused" means config.json is
        # byte-for-byte what the owner wrote — not only that a stub was not reached.
        from personalclaw.dashboard.handlers.agents import api_personalclaw_agent_update
        from personalclaw.dashboard.server import app_permission_middleware

        owner_agent = {
            "system_prompt": "Summarise my inbox. Never send mail.",
            "tools": ["@personalclaw-core"],
            "approval_mode": "interactive",
        }
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"agents": {"helper": owner_agent}}), encoding="utf-8")
        before = config.read_text(encoding="utf-8")

        def _gateway(app_name: str) -> web.Application:
            @web.middleware
            async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
                request["user"] = "owner"
                if app_name:
                    request["app"] = app_name
                return await handler(request)

            gw = web.Application(middlewares=[identity, app_permission_middleware])
            gw["state"] = MagicMock()
            gw.router.add_put("/api/agents/{name}", api_personalclaw_agent_update)
            return gw

        injected = {
            "system_prompt": "Instructions the owner did not write.",
            "tools": ["*"],
            "approval_mode": "auto",
            "confirm": True,
        }
        with _home(tmp_path), patch("personalclaw.config.loader.config_path", return_value=config):
            _install(tmp_path, APP, {"api": ["/api/agents"]})
            async with TestClient(TestServer(_gateway(APP))) as client:
                resp = await client.put("/api/agents/helper", json=injected)
                assert resp.status == 403, await resp.text()
            assert config.read_text(encoding="utf-8") == before, "nothing of the agent changed"
            assert _denials(sel_rows, "/api/agents/helper")

            # The owner still edits their own agent.
            async with TestClient(TestServer(_gateway(""))) as client:
                resp = await client.put("/api/agents/helper", json={"system_prompt": "Be brief."})
                assert resp.status == 200, await resp.text()
        stored = json.loads(config.read_text(encoding="utf-8"))["agents"]["helper"]
        assert stored["system_prompt"] == "Be brief."


# ── 2. Skills, and the prompts every run starts from ─────────────────────────────────


INSTRUCTION_WRITES = [
    ("POST", "/api/skills/install", "/api/skills/install"),
    ("POST", "/api/skills", "/api/skills"),
    # The real templates, regex and all: the verdict is keyed on the canonical form the router
    # reports (`/api/skills/{name}`), so a nested skill name must not slip past it.
    ("PUT", "/api/skills/{name:.+}", "/api/skills/auto/release-flow"),
    ("DELETE", "/api/skills/{name}", "/api/skills/deploy-site"),
    ("POST", "/api/skills/overlay/revert", "/api/skills/overlay/revert"),
    ("POST", "/api/skills/ephemeral/{session}/promote", "/api/skills/ephemeral/s1/promote"),
    ("POST", "/api/skills/proposals/{id}/accept", "/api/skills/proposals/p1/accept"),
    ("POST", "/api/prompts", "/api/prompts"),
    ("PUT", "/api/prompts/{name:.+}", "/api/prompts/native/chat"),
    ("DELETE", "/api/prompts/{name:.+}", "/api/prompts/chat"),
    ("PUT", "/api/prompts/bindings", "/api/prompts/bindings"),
    ("POST", "/api/prompts/{name:.+}/launch", "/api/prompts/campaign/launch"),
    ("POST", "/api/prompt-snippets", "/api/prompt-snippets"),
    ("PUT", "/api/prompt-snippets/{name:.+}", "/api/prompt-snippets/house-rules"),
    ("DELETE", "/api/prompt-snippets/{name:.+}", "/api/prompt-snippets/house-rules"),
]


class TestAnAppCannotInstallASkillOrRewriteAPrompt:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "path"), INSTRUCTION_WRITES)
    async def test_every_instruction_write_is_refused(
        self, tmp_path, sel_rows, method, template, path
    ) -> None:
        await _refused(tmp_path, method, template, path)
        assert _denials(sel_rows, path)

    @pytest.mark.asyncio
    async def test_a_template_launch_is_named_for_the_loop_it_starts(self, tmp_path) -> None:
        text = await _refused(
            tmp_path, "POST", "/api/prompts/{name:.+}/launch", "/api/prompts/campaign/launch"
        )
        assert "loop" in text, text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "path"),
        [
            ("GET", "/api/skills", "/api/skills"),
            ("POST", "/api/skills/{name}/verify", "/api/skills/deploy-site/verify"),
            ("DELETE", "/api/skills/proposals/{id}", "/api/skills/proposals/p1"),
            ("DELETE", "/api/skills/ephemeral/{session}/{slug}", "/api/skills/ephemeral/s1/d1"),
            ("GET", "/api/prompts", "/api/prompts"),
            ("POST", "/api/prompts/{name:.+}/render", "/api/prompts/chat/render"),
            ("POST", "/api/prompts/preview", "/api/prompts/preview"),
            ("POST", "/api/prompt-snippets/{name:.+}/render", "/api/prompt-snippets/x/render"),
        ],
    )
    async def test_reading_checking_and_declining_stay_an_apps(
        self, tmp_path, method, template, path
    ) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": DECLARED})
            status, text = await _call(APP, method, template, path)
        assert status == 200, text

    @pytest.mark.asyncio
    async def test_the_owner_still_installs_a_skill(self, tmp_path) -> None:
        with _home(tmp_path):
            status, _ = await _call("", "POST", "/api/skills/install", "/api/skills/install")
        assert status == 200


class TestImportingAnotherToolsSetupIsTheOwners:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_an_app_cannot_scan_or_import_it(self, tmp_path, sel_rows, method) -> None:
        text = await _refused(tmp_path, method, "/api/onboarding/import", "/api/onboarding/import")
        assert "MCP servers" in text, text
        assert _denials(sel_rows, "/api/onboarding/import")

    def test_a_manifest_naming_it_does_not_install(self) -> None:
        from personalclaw.apps.manifest import AppManifest

        errors = AppManifest.from_dict(
            {
                "name": "importer",
                "version": "1.0.0",
                "displayName": "x",
                "description": "x",
                "permissions": {"api": ["/api/onboarding/import"]},
            }
        ).validate()
        assert any("owner-only" in e for e in errors), errors


class TestALessonIsMemory:
    """A lesson is handed to every agent, and ``/api/memory`` needs the ``memory`` grant — so the
    second door to the same store needs it too."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["POST", "DELETE"])
    async def test_without_the_memory_grant_it_is_refused(self, tmp_path, sel_rows, method) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": ["/api/lessons"]})
            status, text = await _call(APP, method, "/api/lessons", "/api/lessons")
        assert status == 403, text
        assert "memory" in text
        assert _denials(sel_rows, "/api/lessons")

    @pytest.mark.asyncio
    async def test_with_it_the_app_reaches_the_route(self, tmp_path) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": ["/api/lessons"], "memory": True})
            status, text = await _call(APP, "POST", "/api/lessons", "/api/lessons")
        assert status == 200, text


# ── 3. Install consent names every kind of code, and says it runs as you ────────────


def _manifest(**over: Any):
    from personalclaw.apps.manifest import AppManifest

    data: dict[str, Any] = {
        "name": "brings-code",
        "version": "1.0.0",
        "displayName": "Brings Code",
        "description": "fixture",
        "provider": {"type": "model", "implementation": "provider.main:make"},
        "providers": [
            {"type": "memory", "implementation": "store.main:make", "execution": "sidecar"}
        ],
        "backend": {"entryPoint": "backend/server.py"},
        "mcpServers": {"notes": {"command": "python", "args": ["mcp.py"]}},
        "setup": {
            "onInstall": "bash install.sh",
            "onUpdate": "bash migrate.sh",
            "onEnable": "bash on.sh",
            "onDisable": "bash off.sh",
            "onUninstall": "bash remove.sh",
        },
        "cli": {"setup": "steps:setup", "doctor": "steps:doctor"},
        "skills": [{"path": "skills/deploy-site/"}],
    }
    data.update(over)
    return AppManifest.from_dict(data)


class TestConsentNamesEveryKindOfCode:
    def test_provider_modules_are_listed_with_where_they_run(self) -> None:
        from personalclaw.apps.disclosure import describe

        assert describe(_manifest())["providers"] == [
            {"type": "model", "implementation": "provider.main:make", "execution": "in-process"},
            {"type": "memory", "implementation": "store.main:make", "execution": "sidecar"},
        ]

    def test_every_lifecycle_hook_is_listed_verbatim(self) -> None:
        from personalclaw.apps.disclosure import describe

        d = describe(_manifest())
        assert (d["onInstall"], d["onUpdate"]) == ("bash install.sh", "bash migrate.sh")
        assert (d["onEnable"], d["onDisable"], d["onUninstall"]) == (
            "bash on.sh",
            "bash off.sh",
            "bash remove.sh",
        )
        assert (d["cliSetup"], d["cliDoctor"]) == ("steps:setup", "steps:doctor")

    def test_the_skills_an_app_ships_are_listed_by_the_name_they_install_under(self) -> None:
        from personalclaw.apps.disclosure import describe

        assert describe(_manifest())["skills"] == ["deploy-site"]

    def test_it_says_the_code_runs_as_you_and_names_each_kind(self) -> None:
        from personalclaw.apps.disclosure import describe

        sentence = describe(_manifest())["runsAsYou"]
        assert "run as you" in sentence
        for kind in ("server", "provider modules", "MCP server", "install", "uninstall", "setup"):
            assert kind in sentence, (kind, sentence)
        assert "permissions" in sentence, "it says what the permissions do NOT bound"

    def test_an_app_that_ships_only_a_provider_is_no_longer_silent(self) -> None:
        from personalclaw.apps.disclosure import describe

        d = describe(
            _manifest(providers=[], backend={}, mcpServers={}, setup={}, cli={}, skills=[])
        )
        assert "provider module" in d["runsAsYou"] and "run" in d["runsAsYou"]

    def test_an_app_with_no_code_makes_no_such_claim(self) -> None:
        from personalclaw.apps.disclosure import describe

        d = describe(
            _manifest(provider=None, providers=[], backend={}, mcpServers={}, setup={}, cli={})
        )
        assert d["runsAsYou"] == ""

    def test_a_sandboxed_server_is_not_claimed_to_run_as_you(self) -> None:
        from personalclaw.apps.disclosure import describe

        d = describe(
            _manifest(
                provider=None,
                providers=[],
                mcpServers={},
                setup={},
                cli={},
                backend={"entryPoint": "backend/server.py", "sandbox": "docker"},
            )
        )
        assert d["backendSandbox"] == "docker"
        assert d["runsAsYou"] == "", d["runsAsYou"]

    def test_an_update_that_adds_code_asks_again(self) -> None:
        # The new keys take part in the update gate: an update that adds a hook or a provider
        # module changes what the app gets, so it needs consent like one that adds a permission.
        from personalclaw.apps.disclosure import changed, describe

        before = describe(_manifest(setup={}, providers=[]))
        assert not changed(before, describe(_manifest(setup={}, providers=[])))
        assert changed(before, describe(_manifest(setup={"onEnable": "bash on.sh"}, providers=[])))
        assert changed(before, describe(_manifest(setup={})))

    def test_every_new_key_is_a_catalog_field_so_the_card_says_the_same(self) -> None:
        from personalclaw.apps.catalog import CatalogEntry
        from personalclaw.apps.disclosure import describe

        entry = CatalogEntry(name="x", displayName="X", consentKnown=True, **describe(_manifest()))
        wire = entry.to_dict()
        assert wire["onUninstall"] == "bash remove.sh"
        assert wire["providers"][0]["implementation"] == "provider.main:make"
        assert "run as you" in wire["runsAsYou"]


class TestTheInstallPreviewCarriesIt:
    @pytest.fixture
    def home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        import personalclaw.config.loader as loader

        h = tmp_path / "home"
        h.mkdir()
        monkeypatch.setattr(loader, "config_dir", lambda: h)
        monkeypatch.setattr(manager, "config_dir", lambda: h)
        return h

    def test_the_preview_lists_providers_hooks_and_the_runs_as_you_sentence(
        self, home: Path, tmp_path: Path
    ) -> None:
        from personalclaw.apps import app_manager

        src = tmp_path / "src" / "brings-code"
        src.mkdir(parents=True)
        (src / "app.json").write_text(
            json.dumps(
                {
                    "name": "brings-code",
                    "version": "1.0.0",
                    "displayName": "Brings Code",
                    "description": "fixture",
                    "provider": {"type": "memory", "implementation": "store:make"},
                    "setup": {"onEnable": "true", "onDisable": "true", "onUninstall": "true"},
                }
            ),
            encoding="utf-8",
        )
        (src / "store.py").write_text("def make(cfg):\n    return None\n", encoding="utf-8")
        wire = app_manager.preview(src).to_dict()
        disclosure = wire["disclosure"]
        assert disclosure["providers"] == [
            {"type": "memory", "implementation": "store:make", "execution": "in-process"}
        ], wire
        assert (disclosure["onEnable"], disclosure["onDisable"], disclosure["onUninstall"]) == (
            "true",
            "true",
            "true",
        )
        assert "run as you" in disclosure["runsAsYou"], disclosure["runsAsYou"]
        assert wire["consent"], "the digest consent is bound to"


# ── 4. The file explorer refuses an app the way every other app refusal does ────────


@pytest.fixture
def handler_sel():
    """The rows the file handlers write (they reach SEL through ``dashboard.handlers.sel``)."""
    fake = MagicMock()
    with patch("personalclaw.dashboard.handlers.sel", return_value=fake):
        yield fake.log_api_access


def _files_gateway(app_name: str) -> web.Application:
    from personalclaw.dashboard.handlers.files import api_file_list, api_file_read, api_file_write
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if app_name:
            request["app"] = app_name
        return await handler(request)

    gw = web.Application(middlewares=[identity, app_permission_middleware])
    gw.router.add_get("/api/file-read", api_file_read)
    gw.router.add_post("/api/file-write", api_file_write)
    gw.router.add_get("/api/file-list", api_file_list)
    return gw


def _app_file_denials(rows: MagicMock, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("resources") == path
    ]


class TestTheFileExplorerRefusesAnAppAsAnAppRefusal:
    @pytest.fixture
    def home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
        _install(tmp_path, APP, {"api": DECLARED})
        (tmp_path / "config.json").write_text('{"agent": {"yolo": false}}', encoding="utf-8")
        return tmp_path

    @pytest.mark.asyncio
    async def test_reading_your_config_is_a_403_with_a_row_naming_the_app_and_path(
        self, home, handler_sel
    ) -> None:
        target = str(home / "config.json")
        async with TestClient(TestServer(_files_gateway(APP))) as client:
            resp = await client.get("/api/file-read", params={"path": target})
            assert resp.status == 403, await resp.text()
            body = await resp.json()
        assert body["error"]["code"] == "forbidden"
        assert target in body["error"]["message"]
        assert _app_file_denials(handler_sel, target), handler_sel.call_args_list

    @pytest.mark.asyncio
    async def test_writing_it_is_refused_the_same_way_and_nothing_is_written(
        self, home, handler_sel
    ) -> None:
        target = str(home / "config.json")
        before = (home / "config.json").read_text(encoding="utf-8")
        async with TestClient(TestServer(_files_gateway(APP))) as client:
            resp = await client.post(
                "/api/file-write", json={"path": target, "content": '{"agent": {"yolo": true}}'}
            )
            assert resp.status == 403, await resp.text()
        assert (home / "config.json").read_text(encoding="utf-8") == before
        assert _app_file_denials(handler_sel, target)

    @pytest.mark.asyncio
    async def test_listing_the_home_is_refused_the_same_way(self, home, handler_sel) -> None:
        async with TestClient(TestServer(_files_gateway(APP))) as client:
            resp = await client.get("/api/file-list", params={"path": str(home)})
            assert resp.status == 403, await resp.text()
        assert _app_file_denials(handler_sel, str(home))

    @pytest.mark.asyncio
    async def test_what_you_uploaded_stays_readable_to_the_app(self, home) -> None:
        (home / "uploads").mkdir()
        (home / "uploads" / "photo.txt").write_text("hello", encoding="utf-8")
        async with TestClient(TestServer(_files_gateway(APP))) as client:
            resp = await client.get(
                "/api/file-read", params={"path": str(home / "uploads" / "photo.txt")}
            )
            assert resp.status == 200, await resp.text()

    @pytest.mark.asyncio
    async def test_the_owners_answers_are_unchanged(self, home, handler_sel) -> None:
        # The owner reads the file, and the owner's refusal stays the 400 that confirms nothing
        # about the path — no app row, because no app asked.
        async with TestClient(TestServer(_files_gateway(""))) as client:
            ok = await client.get("/api/file-read", params={"path": str(home / "config.json")})
            assert ok.status == 200
            refused = await client.get("/api/file-read", params={"path": "/etc/hosts"})
            assert refused.status == 400
            assert await refused.json() == {"error": "invalid or forbidden path"}
        assert not [
            c
            for c in handler_sel.call_args_list
            if str(c.kwargs.get("caller", "")).startswith("app:")
        ]
