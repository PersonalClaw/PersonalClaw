"""An installed app cannot repair, maintain or update your setup, and still reads the Doctor.

**The holes, measured with the fixture app below before this change.** ``/api/doctor`` and
``/api/update`` sat in no ``SECURITY_ROUTE_FAMILIES`` root, so an app that declared them reached
every route in both.

1. ``POST /api/doctor/fix/tools.restore-core-server`` with ``{"confirm": true}`` answered the app
   200 and rewrote PersonalClaw's server entry in your agent runtime config. Every Doctor fix was
   the app's to apply the same way, removing models from your bindings among them, and the
   confirmation the route asks for, which is yours once you have read what the fix does, was the
   app's to send.
2. ``POST /api/doctor/remediation/run`` ran the maintenance pass on the app's say: history files,
   inbox items and security-log entries past their retention deleted, skills aged, knowledge
   embedded with your embedding model.
3. ``POST /api/update`` updated PersonalClaw for the app, new code and a restart, and the app could
   check for an update with automatic checks off, cancel an update you started, and dismiss how
   one ended.
4. ``POST /api/skills/bundled/update`` and ``…/keep`` were refused only as writes nobody had
   declared, a refusal that named no capability.

Each is the owner's now, refused in the registry's words, and the owner's own request still runs.
What stays the app's is what it was granted to read: the Doctor's report, one capability's checks,
the fix catalog, the maintenance plan, a crash file, the two simulators, which write nothing, and
the update status.

Same harness as ``test_apps_cannot_change_your_models.py``: the real token middleware and the real
``app_permission_middleware``, a fixture app you installed through preview → consent → install, and
the token you minted for it, sent both ways an app sends one. The route matrix answers with a
stand-in at each real template, so no case here updates anything; the fix and maintenance cases run
the real Doctor handlers on a scratch home.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from test_apps_cannot_change_your_models import _gateway, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers import doctor as doctor_handlers
from personalclaw.resilience import remediation

APP = "probe-setup"
DOCTOR = "/api/doctor"
UPDATE = "/api/update"
DECLARES = [DOCTOR, UPDATE, "/api/skills"]
FAMILIES = (DOCTOR, UPDATE)
#: The fix the Doctor's tools check offers, by the id a client sends.
FIX_ID = "tools.restore-core-server"
CORE = "personalclaw-core"
REF = "@personalclaw-core"
OWNER_ONLY = "owner-only capability, not grantable to an app: "

#: Every write that repairs, maintains or updates your setup, with what a call would send.
YOUR_CHANGES: list[tuple[str, str, Any]] = [
    ("POST", "/api/doctor/fix/{fix_id}", {"confirm": True}),
    ("POST", "/api/doctor/remediation/run", {"confirm": True}),
    ("POST", "/api/update", None),
    ("POST", "/api/update/check", None),
    ("POST", "/api/update/cancel", None),
    ("POST", "/api/update/dismiss", None),
    ("POST", "/api/update/simulate", None),
    ("POST", "/api/skills/bundled/update", {"name": "check-work", "digest": "0" * 64}),
    ("POST", "/api/skills/bundled/keep", {"name": "check-work", "digest": "0" * 64}),
]

#: What an app you granted these families still reaches, in the order the gateway registers them
#: (the literal reads before ``{capability}``).
STILL_THE_APPS: list[tuple[str, str, Any]] = [
    ("GET", "/api/doctor", None),
    ("GET", "/api/doctor/fixes", None),
    ("GET", "/api/doctor/crash/{filename}", None),
    ("GET", "/api/doctor/remediation", None),
    ("GET", "/api/doctor/{capability}", None),
    ("POST", "/api/doctor/simulate/surfacing", {"text": "summarise my reading list"}),
    ("POST", "/api/doctor/simulate/automation", {"trigger_id": "morning-digest"}),
    ("GET", "/api/update/check", None),
]

_PATH_PARAMS = {"fix_id": FIX_ID, "filename": "1700000000-gateway.json", "capability": "tools"}


def _path(template: str) -> str:
    for key, value in _PATH_PARAMS.items():
        template = template.replace("{" + key + "}", value)
    return template


def _bundle(root: Path) -> Path:
    """A fixture app that declares the Doctor, the updater and the skills, and ships nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Setup",
        "description": "A fixture app that declares the routes your setup is repaired through.",
        "permissions": {"api": DECLARES},
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch PersonalClaw home and ``HOME``, and a stand-in for this install's command.

    The stand-in is a script nothing runs: the Fix only names it in the server entry. The places
    an agent CLI keeps its own configuration point at empty scratch folders.
    """
    import personalclaw.config.loader as loader

    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        empty = tmp_path / f"empty-{var.lower()}"
        empty.mkdir()
        monkeypatch.setenv(var, str(empty))
    pc = tmp_path / "pc-home"
    pc.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(manager, "config_dir", lambda: pc)
    stand_in = tmp_path / "bin" / "personalclaw"
    stand_in.parent.mkdir()
    stand_in.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stand_in.chmod(0o755)
    monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", str(stand_in))
    return SimpleNamespace(pc=pc, bin=stand_in, config=pc / "agents" / "personalclaw.json")


@pytest.fixture(autouse=True)
def _fresh_sessions() -> Iterator[None]:
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    yield
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every row the permission middleware writes (it imports ``sel`` at the refusal), and the
    Fix's and the maintenance run's own audit rows."""
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


async def _installed(gw: _Gateway, home: SimpleNamespace) -> str:
    """Your install of the fixture app, and the token you mint for it."""
    await gw.install(_bundle(home.pc))
    return str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])


def _stand_ins(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """A stand-in at every template in both lists, recording who reached it."""

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.method))
        return web.json_response({"reached": True})

    return [(m, t, stand_in) for m, t, _body in STILL_THE_APPS + YOUR_CHANGES]


def _doctor_routes() -> list[tuple[str, str, Any]]:
    """The real Doctor handlers, at the templates the gateway registers them under."""
    h = doctor_handlers
    return [
        ("GET", "/api/doctor/fixes", h.api_doctor_fixes),
        ("GET", "/api/doctor/{capability}", h.api_doctor_capability),
        ("POST", "/api/doctor/fix/{fix_id}", h.api_doctor_fix_apply),
        ("POST", "/api/doctor/remediation/run", h.api_doctor_remediation_run),
    ]


def _agent_config_without_its_server(home: SimpleNamespace) -> bytes:
    """Your agent runtime config with PersonalClaw's server entry gone: the finding the Fix
    repairs. A second server rides along, and both lists hold only it, so a write that touched
    anything but the server entry would show."""
    document = {
        "name": "personalclaw",
        "description": "Fixture agent",
        "prompt": "file:///nonexistent/pc-fixture/prompt.md",
        "tools": ["@notes"],
        "allowedTools": ["@notes"],
        "mcpServers": {
            "notes": {"command": "/nonexistent/pc-fixture-notes-mcp", "args": ["--stdio"]},
        },
    }
    data = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    home.config.parent.mkdir(parents=True, exist_ok=True)
    home.config.write_bytes(data)
    return data


# ── 1. Every change to your setup is yours; what you granted stays the app's ────────────────────


class TestAnAppCannotChangeYourSetup:
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
        assert len(_denials(sel_rows, path)) == 2, "each refusal leaves an SEL row naming the app"
        assert yours[0] == 200, yours
        assert reached == [("", method)], "only your request reached the handler"

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


# ── 2. What a refusal keeps from an app, measured on the real Doctor ────────────────────────────


class TestWhatAnAppNoLongerDoes:
    @pytest.mark.asyncio
    async def test_it_cannot_apply_a_fix_and_you_still_can(self, home, sel_rows) -> None:
        before = _agent_config_without_its_server(home)
        path = f"/api/doctor/fix/{FIX_ID}"
        async with _gateway(_doctor_routes()) as gw:
            token = await _installed(gw, home)
            as_sdk = await gw.call("POST", path, {"confirm": True}, app_token=token)
            as_backend = await gw.call(
                "POST", path, {"confirm": True}, app_token=token, as_backend=True
            )
            after_the_app = home.config.read_bytes()
            yours = await gw.ok("POST", path, {"confirm": True})
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "applying a Doctor fix" in text, text
        assert after_the_app == before, "the app's request changed your agent config"
        assert len(_denials(sel_rows, path)) == 2
        assert yours["ok"] is True, yours
        now = json.loads(home.config.read_text(encoding="utf-8"))
        assert now["mcpServers"][CORE] == {"command": str(home.bin), "args": ["mcp-core"]}
        assert now["tools"] == ["@notes"] and now["allowedTools"] == ["@notes"], now
        assert REF not in now["allowedTools"], "your fix adds nothing to what runs without asking"

    @pytest.mark.asyncio
    async def test_it_still_reads_the_doctor_and_the_fix_it_offers(self, home, sel_rows) -> None:
        _agent_config_without_its_server(home)
        async with _gateway(_doctor_routes()) as gw:
            token = await _installed(gw, home)
            catalog = await gw.call("GET", "/api/doctor/fixes", app_token=token)
            checks = await gw.call("GET", "/api/doctor/tools", app_token=token, as_backend=True)
        assert catalog[0] == 200, catalog
        offered = {fix["id"]: fix for fix in json.loads(catalog[1])["fixes"]}
        assert f"{home.bin} mcp-core" in offered[FIX_ID]["preview"], offered[FIX_ID]
        assert checks[0] == 200, checks
        rows = {row["id"]: row for row in json.loads(checks[1])["probes"]}
        assert rows["tools.core_server"]["ok"] is False
        assert rows["tools.core_server"]["fix_id"] == FIX_ID
        assert not [c for c in sel_rows.call_args_list if c.kwargs.get("outcome") == "denied"]

    @pytest.mark.asyncio
    async def test_it_cannot_run_the_maintenance_and_you_still_can(
        self, home, sel_rows, monkeypatch
    ) -> None:
        runs: list[dict[str, Any]] = []

        def engine(**kwargs: Any) -> SimpleNamespace:
            runs.append(kwargs)
            return SimpleNamespace(score_before=80.0, score_after=95.0, jobs=[], stopped_reason="")

        monkeypatch.setattr(remediation, "run_remediation", engine)
        path = "/api/doctor/remediation/run"
        async with _gateway(_doctor_routes()) as gw:
            token = await _installed(gw, home)
            refused = await gw.call("POST", path, {"confirm": True}, app_token=token)
            ran_for_the_app = list(runs)
            yours = await gw.ok("POST", path, {"confirm": True})
        assert refused[0] == 403, refused
        assert "running PersonalClaw's maintenance" in refused[1], refused
        assert ran_for_the_app == [], "the maintenance ran for the app"
        assert _denials(sel_rows, path)
        assert len(runs) == 1 and yours["score_after"] == 95.0, (runs, yours)


# ── 3. The route table declares both families ───────────────────────────────────────────────────


def _census() -> set[str]:
    from personalclaw.manifest_reference import _routes_from_ast

    return {
        f"{r['method']} {r['path']}"
        for r in _routes_from_ast()
        if any(r["path"] == root or r["path"].startswith(root + "/") for root in FAMILIES)
    }


class TestTheRouteTableDeclaresBothFamilies:
    def test_both_are_families_whose_reads_stay_the_allowlists(self) -> None:
        from personalclaw.apps.permissions import READ_DECLARED_FAMILIES, SECURITY_ROUTE_FAMILIES

        for family in FAMILIES:
            assert family in SECURITY_ROUTE_FAMILIES, family
            assert family not in READ_DECLARED_FAMILIES, f"{family}: an app keeps its reads"

    def test_every_route_in_both_families_is_driven_here(self) -> None:
        census = _census()
        assert len(census) >= 15, f"only {len(census)} routes in the two families — vacuous"
        driven = {
            f"{m} {t}" for m, t, _body in YOUR_CHANGES + STILL_THE_APPS if t.startswith(FAMILIES)
        }
        assert census == driven, census ^ driven

    def test_each_change_is_yours_and_each_simulator_says_why_an_app_may(self) -> None:
        from personalclaw.apps.permissions import AppMay, OwnerOnly, route_authz

        for method, template, _body in YOUR_CHANGES:
            assert isinstance(route_authz(method, template), OwnerOnly), f"{method} {template}"
        for method, template, _body in STILL_THE_APPS:
            authz = route_authz(method, template)
            if method == "GET":
                assert authz is None, f"{method} {template} is the allowlist's, as it was"
            else:
                assert isinstance(authz, AppMay) and authz.reason.strip(), f"{method} {template}"
