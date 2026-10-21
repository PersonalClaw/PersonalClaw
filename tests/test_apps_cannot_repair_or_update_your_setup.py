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

The app is installed once for the module (``shared_app_rows.SharedApp``), and each route and each
case on the real Doctor is a row on that install: the home is put back to what the install left
before every row and checked by digest, every session is signed out, and the fenced registries are
read around it. A refused row also reads the home before and after the app's two requests, so a
refusal that wrote anything fails its own row. Both families' end-to-end refusal stays its own
test, on an app installed for it alone.
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
from shared_app_rows import (
    RecordedRows,
    Row,
    RowBody,
    SharedApp,
    home_files,
    planted_rows,
    written_since,
)
from table_rows import row
from test_apps_cannot_change_your_models import _gateway, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers import doctor as doctor_handlers
from personalclaw.resilience import remediation

pytestmark = pytest.mark.security


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


def _this_machine(root: Path, mp: pytest.MonkeyPatch, pc: Path) -> SimpleNamespace:
    """A scratch ``HOME`` under *root*, and a stand-in for this install's command, beside the
    PersonalClaw home *pc*.

    The stand-in is a script nothing runs: the Fix only names it in the server entry. The places
    an agent CLI keeps its own configuration point at empty scratch folders.
    """
    user = root / "user"
    user.mkdir()
    mp.setenv("HOME", str(user))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        empty = root / f"empty-{var.lower()}"
        empty.mkdir()
        mp.setenv(var, str(empty))
    stand_in = root / "bin" / "personalclaw"
    stand_in.parent.mkdir()
    stand_in.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stand_in.chmod(0o755)
    mp.setattr("personalclaw.agent._PERSONALCLAW_BIN", str(stand_in))
    return SimpleNamespace(pc=pc, bin=stand_in, config=pc / "agents" / "personalclaw.json")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch PersonalClaw home of this test's own, on :func:`_this_machine`."""
    import personalclaw.config.loader as loader

    pc = tmp_path / "pc-home"
    pc.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(manager, "config_dir", lambda: pc)
    return _this_machine(tmp_path, monkeypatch, pc)


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


#: The fixture app, installed once for the module.
SHARED = SharedApp(APP, _bundle, _gateway)


async def _shared_machine(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, mp: pytest.MonkeyPatch
) -> SimpleNamespace:
    """The module's home, with the app installed, on a :func:`_this_machine` of this test's."""
    return _this_machine(tmp_path, mp, await SHARED.home(tmp_path_factory, mp))


# ── 1. Every change to your setup is yours; what you granted stays the app's ────────────────────


def _change_rows(reached: list[tuple[str, str]]) -> list[tuple[str, RowBody]]:
    """A row per route of :data:`YOUR_CHANGES`: the app refused both ways it sends its token, in
    the registry's words, each refusal an SEL row naming it, the home as it was after both, and
    only your request reaching the stand-in."""

    def refused(method: str, template: str, body: Any) -> RowBody:
        path = _path(template)

        async def case(r: Row) -> None:
            reached.clear()
            before = home_files(r.home)
            as_sdk = await r.gw.call(method, path, body, app_token=r.token)
            as_backend = await r.gw.call(method, path, body, app_token=r.token, as_backend=True)
            written = written_since(r.home, before)
            yours = await r.gw.call(method, path, body)
            capability = _capability(method, template)
            for status, text in (as_sdk, as_backend):
                assert status == 403, text
                assert capability and f"{OWNER_ONLY}{capability}" in text, text
            assert not written, f"a refusal changed your home: {written}"
            assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app"
            assert yours[0] == 200, yours
            assert reached == [("", method)], "only your request reached the handler"

        return case

    return [(f"{m} {t}", refused(m, t, b)) for m, t, b in YOUR_CHANGES]


def _granted_rows(reached: list[tuple[str, str]]) -> list[tuple[str, RowBody]]:
    """A row per route of :data:`STILL_THE_APPS`: the app reaches it both ways, as you do, and
    no request leaves a refusal."""

    def granted(method: str, template: str, body: Any) -> RowBody:
        path = _path(template)

        async def case(r: Row) -> None:
            reached.clear()
            as_sdk = await r.gw.call(method, path, body, app_token=r.token)
            as_backend = await r.gw.call(method, path, body, app_token=r.token, as_backend=True)
            yours = await r.gw.call(method, path, body)
            assert [as_sdk[0], as_backend[0], yours[0]] == [200, 200, 200], (as_sdk, as_backend)
            assert reached == [(APP, method), (APP, method), ("", method)]
            assert not r.denials(path)

        return case

    return [(f"{m} {t}", granted(m, t, b)) for m, t, b in STILL_THE_APPS]


@pytest.mark.asyncio
async def test_an_app_is_refused_every_change_and_you_are_not(
    subtests, tmp_path, tmp_path_factory, monkeypatch
) -> None:
    await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _change_rows(reached)
    assert len(rows) == len(YOUR_CHANGES), "a change was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_an_app_still_reaches_what_you_granted(
    subtests, tmp_path, tmp_path_factory, monkeypatch
) -> None:
    await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _granted_rows(reached)
    assert len(rows) == len(STILL_THE_APPS), "a read was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_a_row_that_leaves_state_behind_fails_the_next_row(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    probe = _change_rows(reached)[0]
    failed = await planted_rows(SHARED, _stand_ins(reached), probe)
    name = probe[0]
    # Reset as every row is, a grant left in the home and an SEL row reach nothing after them.
    assert failed["home"]["reset"] == {} and failed["sel"]["reset"] == {}, failed
    # Without the reset, the row after them fails, by name, on the check that guards it.
    assert "a home other than the installed app's" in failed["home"]["no reset"][name]
    assert "planted-grant.json" in failed["home"]["no reset"][name]
    assert "Security Event Log rows" in failed["sel"]["no reset"][name]
    # A fenced registry is caught in the row that changed it, and again in the row after.
    assert set(failed["fenced"]["reset"]) == {"planted fenced", name}, failed["fenced"]
    assert all("left registries" in why for why in failed["fenced"]["reset"].values())


#: The route the planted refusal below answers: the maintenance run, between two neighbours.
PLANTED = "POST /api/doctor/remediation/run"
#: Where the maintenance records each run it makes, in your home.
LEDGER = "doctor/remediation.jsonl"


@pytest.mark.asyncio
async def test_a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """A handler the app reaches, which runs the maintenance (it records the run in the Doctor's
    ledger) and then answers as a refusal does: a 403 in the registry's words, and an SEL row
    naming the app. Only your home tells it from a refusal, so its row fails on that, and the rows
    either side of it, the home put back before each, pass."""
    from personalclaw.apps import permissions

    machine = await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _change_rows(reached)
    at = [name for name, _ in rows].index(PLANTED)
    method, template = PLANTED.split(" ", 1)

    async def runs_then_refuses(request: web.Request) -> web.Response:
        from personalclaw import sel

        app = request.get("app", "")
        if not app:
            reached.append(("", request.method))
            return web.json_response({"reached": True})
        ledger = machine.pc / LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"job": "history-retention", "deleted": 3}) + "\n")
        sel.sel().log_api_access(
            caller=f"app:{app}", outcome="denied", source="app_permissions", resources=request.path
        )
        refusal = permissions.owner_only_refusal(_capability(method, template))
        raise web.HTTPForbidden(
            text=f"app {app!r} not permitted to access {request.path}: {refusal}"
        )

    decide = permissions.app_request_denial

    def lets_the_planted_route_through(app: str, path: str, *, method: str = "", route: str = ""):
        return (
            "" if f"{method} {route}" == PLANTED else decide(app, path, method=method, route=route)
        )

    monkeypatch.setattr(permissions, "app_request_denial", lets_the_planted_route_through)
    routes = [
        (m, t, runs_then_refuses if (m, t) == (method, template) else handler)
        for m, t, handler in _stand_ins(reached)
    ]
    recorded = RecordedRows()
    await SHARED.run(recorded, rows[at - 1 : at + 2], routes, label="route")
    assert list(recorded.failed) == [PLANTED], recorded.failed
    assert f"a refusal changed your home: ['{LEDGER}']" in recorded.failed[PLANTED]


# ── 2. What a refusal keeps from an app, measured on the real Doctor ────────────────────────────


def _doctor_rows(machine: SimpleNamespace) -> list[tuple[str, RowBody]]:
    """The fix and the maintenance refused the app and run for you, and the Doctor's report and
    the fix it offers still the app's to read."""

    async def it_cannot_apply_a_fix_and_you_still_can(r: Row) -> None:
        _agent_config_without_its_server(machine)
        path = f"/api/doctor/fix/{FIX_ID}"
        before = home_files(r.home)
        as_sdk = await r.gw.call("POST", path, {"confirm": True}, app_token=r.token)
        as_backend = await r.gw.call(
            "POST", path, {"confirm": True}, app_token=r.token, as_backend=True
        )
        written = written_since(r.home, before)
        yours = await r.gw.ok("POST", path, {"confirm": True})
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "applying a Doctor fix" in text, text
        assert not written, f"the app's request changed your home: {written}"
        assert len(r.denials(path)) == 2
        assert yours["ok"] is True, yours
        now = json.loads(machine.config.read_text(encoding="utf-8"))
        assert now["mcpServers"][CORE] == {"command": str(machine.bin), "args": ["mcp-core"]}
        assert now["tools"] == ["@notes"] and now["allowedTools"] == ["@notes"], now
        assert REF not in now["allowedTools"], "your fix adds nothing to what runs without asking"

    async def it_still_reads_the_doctor_and_the_fix_it_offers(r: Row) -> None:
        _agent_config_without_its_server(machine)
        catalog = await r.gw.call("GET", "/api/doctor/fixes", app_token=r.token)
        checks = await r.gw.call("GET", "/api/doctor/tools", app_token=r.token, as_backend=True)
        assert catalog[0] == 200, catalog
        offered = {fix["id"]: fix for fix in json.loads(catalog[1])["fixes"]}
        assert f"{machine.bin} mcp-core" in offered[FIX_ID]["preview"], offered[FIX_ID]
        assert checks[0] == 200, checks
        probes = {probe["id"]: probe for probe in json.loads(checks[1])["probes"]}
        assert probes["tools.core_server"]["ok"] is False
        assert probes["tools.core_server"]["fix_id"] == FIX_ID
        assert not [c for c in r.sel.call_args_list if c.kwargs.get("outcome") == "denied"]

    async def it_cannot_run_the_maintenance_and_you_still_can(r: Row) -> None:
        runs: list[dict[str, Any]] = []

        def engine(**kwargs: Any) -> SimpleNamespace:
            runs.append(kwargs)
            return SimpleNamespace(score_before=80.0, score_after=95.0, jobs=[], stopped_reason="")

        path = "/api/doctor/remediation/run"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(remediation, "run_remediation", engine)
            before = home_files(r.home)
            as_sdk = await r.gw.call("POST", path, {"confirm": True}, app_token=r.token)
            as_backend = await r.gw.call(
                "POST", path, {"confirm": True}, app_token=r.token, as_backend=True
            )
            written = written_since(r.home, before)
            ran_for_the_app = list(runs)
            yours = await r.gw.ok("POST", path, {"confirm": True})
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "running PersonalClaw's maintenance" in text, text
        assert ran_for_the_app == [], "the maintenance ran for the app"
        assert not written, f"the app's request changed your home: {written}"
        assert len(r.denials(path)) == 2
        assert len(runs) == 1 and yours["score_after"] == 95.0, (runs, yours)

    return [
        ("it cannot apply a fix and you still can", it_cannot_apply_a_fix_and_you_still_can),
        (
            "it still reads the Doctor and the fix it offers",
            it_still_reads_the_doctor_and_the_fix_it_offers,
        ),
        (
            "it cannot run the maintenance and you still can",
            it_cannot_run_the_maintenance_and_you_still_can,
        ),
    ]


@pytest.mark.asyncio
async def test_what_an_app_no_longer_does(
    subtests, tmp_path, tmp_path_factory, monkeypatch
) -> None:
    machine = await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    await SHARED.run(subtests, _doctor_rows(machine), _doctor_routes())


@pytest.mark.asyncio
async def test_each_family_refuses_an_app_installed_for_this_test_alone(home, sel_rows) -> None:
    """Both families' end-to-end refusal, on an app installed for this test alone: the Doctor's
    fix on its real handler, and an update at a stand-in that records who reached it."""
    before = _agent_config_without_its_server(home)
    reached: list[str] = []

    async def update(request: web.Request) -> web.Response:
        reached.append(request.get("app", ""))
        return web.json_response({"reached": True})

    fix = f"/api/doctor/fix/{FIX_ID}"
    asks = [(fix, {"confirm": True}, "applying a Doctor fix"), (UPDATE, None, "updating")]
    answers: list[tuple[str, tuple[int, str]]] = []
    async with _gateway([*_doctor_routes(), ("POST", UPDATE, update)]) as gw:
        token = await _installed(gw, home)
        for path, body, why in asks:
            for as_backend in (False, True):
                answer = await gw.call("POST", path, body, app_token=token, as_backend=as_backend)
                answers.append((why, answer))
    for why, (status, text) in answers:
        assert status == 403, text
        assert f"{OWNER_ONLY}{why}" in text, text
    assert home.config.read_bytes() == before, "the app's request changed your agent config"
    assert reached == [], "the update ran for the app"
    assert len(_denials(sel_rows, fix)) == 2 and len(_denials(sel_rows, UPDATE)) == 2


# ── 3. The route table declares both families ───────────────────────────────────────────────────


#: What the census below reads: both families, and the subtree of the bundled skills' two writes,
#: which sit in another family.
CENSUSED = (*FAMILIES, "/api/skills/bundled")


def _census() -> set[str]:
    from route_census import routes

    return {
        f"{r['method']} {r['path']}"
        for r in routes()
        if any(r["path"] == root or r["path"].startswith(root + "/") for root in CENSUSED)
    }


def test_the_route_table_declares_both_families(subtests) -> None:
    from personalclaw.apps.permissions import (
        READ_DECLARED_FAMILIES,
        SECURITY_ROUTE_FAMILIES,
        AppMay,
        OwnerOnly,
        route_authz,
    )

    with row(subtests, check="both are families whose reads stay the allowlists"):
        for family in FAMILIES:
            assert family in SECURITY_ROUTE_FAMILIES, family
            assert family not in READ_DECLARED_FAMILIES, f"{family}: an app keeps its reads"
    with row(subtests, check="every route in both families, and each bundled skill's, is driven"):
        census = _census()
        assert len(census) >= 17, f"only {len(census)} routes in the two families — vacuous"
        driven = {
            f"{m} {t}" for m, t, _body in YOUR_CHANGES + STILL_THE_APPS if t.startswith(CENSUSED)
        }
        assert census == driven, census ^ driven
    with row(subtests, check="each change is yours and each simulator says why an app may"):
        for method, template, _body in YOUR_CHANGES:
            assert isinstance(route_authz(method, template), OwnerOnly), f"{method} {template}"
        for method, template, _body in STILL_THE_APPS:
            authz = route_authz(method, template)
            if method == "GET":
                assert authz is None, f"{method} {template} is the allowlist's, as it was"
            else:
                assert isinstance(authz, AppMay) and authz.reason.strip(), f"{method} {template}"
