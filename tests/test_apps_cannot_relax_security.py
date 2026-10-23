"""An installed app can never change the owner's security settings, and the owner's own
loosening writes need consent on the wire.

**The hole.** An app that declares ``/api/config`` in ``permissions.api`` passes the app
permission middleware for ``PATCH /api/config/personalclaw`` — a path-prefix allowlist says
nothing about WHICH field is written — and the handler never looked at who was asking. So an
app could turn ``agent.yolo`` on (every tool call auto-approved, every session), turn password
sign-in on, drop the 2FA requirement, point egress at the LAN, or raise every guardrail budget.
#3596's ``confirm: true`` did not stop it: the flag records that the CLIENT asked, and anything
holding a session can send it — an app included.

Beside the PATCH, the same posture had side doors an app could reach with an ordinary
declaration: the chat approval mode (``/api/chat/mode`` turns auto-approve-everything on),
resolving a pending approval with a STANDING verb (``yolo``, ``trust_agent``), an agent
profile's ``approval_mode``, and the auth branch for internal routes, which validated an app
token and then forgot it was an app's.

The app is installed once for the module (``shared_app_rows.SharedApp``), through preview →
consent → install, and every control, posture route, approval verb and internal route is a row on
that install, behind the REAL token middleware and the REAL ``app_permission_middleware``. The
token you mint for the app is presented both ways an app presents one: as a Bearer header beside
your cookie (its SDK) and as the only credential (its backend). Each refusal is read back as the
Security Event Log row that names the app. Before every row the home is put back to what the
install left and checked by digest, every session is signed out, the fenced registries are read
around it, and the row gets a gateway and a ``DashboardState`` of its own. A refused row reads
your home before and after the app's requests, so a refusal that wrote anything fails its own
row. The config handler and the chat's approve route are the real ones; a posture route and an
internal route answer with a stand-in at its real template, recording who reached it. Each
family's end-to-end refusal stays its own test, on an app installed for it alone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from shared_app_rows import (
    RecordedRows,
    Row,
    SharedApp,
    home_files,
    planted_rows,
    recording_sel,
    written_since,
)
from table_rows import row
from test_apps_cannot_change_your_models import PORT
from test_apps_cannot_post_into_your_chats import OWNER_ONLY, WAYS, _AppGateway, _pending

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.apps import register_app_routes

pytestmark = pytest.mark.security


APP = "evil-app"
#: The ordinary setting the app declares in `permissions.config`, and one it does not.
ORDINARY = "voice.echo_filter_enabled"
UNDECLARED = "voice.duplex_mute_enabled"
#: Your settings, your chats (whose mode and approvals are your posture) and the tool routes, so no
#: refusal here is "path not declared"; and one ordinary setting, the list install consent shows.
DECLARED = {"api": ["/api/config", "/api/chat", "/api/tools"], "config": [ORDINARY]}

CONFIG = "/api/config/personalclaw"
APPROVE = "/api/chat/sessions/{session}/approve"
#: An internal route (PersonalClaw's own processes call it with the internal secret, the browser
#: with its sign-in) and a mixed one, declared to the token middleware as the gateway declares them.
TOOLS_INVOKE = "/api/tools/invoke"
TRIGGERS = "/api/triggers"
INTERNAL_SECRET = "s3cret"

#: Why the config handler refuses an app a security setting, and an undeclared ordinary one.
SECURITY_SETTING = "security_setting_owner_only"
NOT_DECLARED = "config_field_not_declared"


# ── the shared app, and the gateway its rows reach ─────────────────────────────────────────────


def _bundle(root: Path) -> Path:
    """The probe app, declaring :data:`DECLARED`, and shipping nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Posture",
        "description": "A fixture app that declares the routes your security posture is set by.",
        "permissions": DECLARED,
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return d


@asynccontextmanager
async def _gateway(routes: list[tuple[str, str, Any]]) -> AsyncIterator[_AppGateway]:
    """The real token middleware, with :data:`TOOLS_INVOKE` internal and :data:`TRIGGERS` mixed,
    the real permission middleware and the request boundary, in front of the app routes and
    *routes*, over a ``DashboardState`` of its own on the home."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.request_boundary import request_boundary_middleware
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    state = _make_state(loader.config_dir())
    app = web.Application(
        middlewares=[
            token_auth_middleware(
                port=PORT,
                internal_routes=frozenset({f"POST {TOOLS_INVOKE}"}),
                mixed_internal_routes=frozenset({f"POST {TRIGGERS}"}),
                internal_secret=INTERNAL_SECRET,
            ),
            app_permission_middleware,
            request_boundary_middleware(),
        ]
    )
    app["state"] = state
    register_app_routes(app)
    for method, template, handler in routes:
        app.router.add_route(method, template, handler)
    owner = generate_token("owner", ttl_seconds=600)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        yield _AppGateway(client, owner, state)


#: The fixture app, installed once for the module.
SHARED = SharedApp(APP, _bundle, _gateway)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """A home of this test's own, with every session signed out before and after."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    revoke_all_sessions()
    yield tmp_path
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every Security Event Log row the test's requests write, the token middleware's included."""
    fake = MagicMock()
    with recording_sel(fake):
        yield fake.log_api_access


def _denials(rows: MagicMock, resources: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("source") == "app_permissions"
        and c.kwargs.get("resources") == resources
    ]


def _routes(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """The real config handler and the chat's real approve route, and a stand-in at every posture
    route and internal route, recording who reached it and at which path."""
    from personalclaw.dashboard.chat_handlers import api_chat_session_approve
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.path))
        return web.json_response({"app": request.get("app", "")})

    return [
        ("PATCH", CONFIG, api_personalclaw_config_patch),
        ("POST", APPROVE, api_chat_session_approve),
        ("POST", TOOLS_INVOKE, stand_in),
        ("POST", TRIGGERS, stand_in),
        *((method, template, stand_in) for method, template, _path in POSTURE_ROUTES),
    ]


def _seed(home: Path, data: dict) -> None:
    """*data* written into your config file, over what the install left there."""

    def merge(into: dict, more: dict) -> None:
        for key, value in more.items():
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                merge(into[key], value)
            else:
                into[key] = value

    path = home / "config.json"
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merge(stored, data)
    path.write_text(json.dumps(stored), encoding="utf-8")


def _stored(home: Path, field: str) -> Any:
    stored: Any = json.loads((home / "config.json").read_text(encoding="utf-8"))
    for part in field.split("."):
        stored = stored[part]
    return stored


async def _write(
    gw: _AppGateway,
    field: str,
    value: Any,
    *,
    confirm: bool = False,
    token: str = "",
    as_backend: bool = False,
) -> tuple[int, str]:
    """A write of *field*, as the SPA sends it (yours), or as the app sends it with *token*. A
    field holding a whole list or object names the revision of the value it replaces
    (`personalclaw/stale_write.py`), read here from what is stored, as a page would have read it,
    so what these rows measure is the refusal and the consent rule."""
    from personalclaw.dashboard.handlers.core import _DOCUMENT_PATHS, _value_in_effect
    from personalclaw.stale_write import revision_of

    body: dict[str, Any] = {"path": field, "value": value}
    if confirm:
        body["confirm"] = True
    headers = {"If-Match": revision_of(_value_in_effect(field))} if field in _DOCUMENT_PATHS else {}
    return await gw.call(
        "PATCH", CONFIG, body, app_token=token, as_backend=as_backend, headers=headers
    )


#: The config transaction's lock, beside your config file (`config/transactions.py`).
_LOCK = "config.json.lock"


def _code(text: str) -> str:
    return str(json.loads(text)["error"]["code"])


# ── 1. Every control: the app is refused it, and you consent to loosening it ────────────────────

#: (field, what is stored now, the looser value). Each is a write that weakens a control the
#: owner set; each needs `confirm: true` from the owner, not a dialog one UI surface remembered.
#: It covers EVERY field on the list — `test_security_posture_rail.py` fails if a security
#: control is added without a case here, so both refusals are driven for each one.
LOOSENING_WRITES = [
    ("agent.yolo", {}, True),
    ("agent.approval_mode", {"agent": {"approval_mode": "interactive"}}, "auto"),
    ("agent.approval_mode", {"agent": {"approval_mode": "interactive"}}, "trust_reads"),
    ("agent.subagent_cwd_allowed_roots", {"agent": {"subagent_cwd_allowed_roots": []}}, ["/"]),
    (
        "agent.unattended_requires_verified_adapter",
        {"agent": {"unattended_requires_verified_adapter": True}},
        False,
    ),
    ("agent.self_qa.fix_branch_enabled", {}, True),
    ("auth.require_totp", {"auth": {"require_totp": True}}, False),
    ("auth.login_enabled", {"auth": {"login_enabled": False}}, True),
    ("auth.session_ttl", {"auth": {"session_ttl": "30d"}}, "90d"),
    ("auth.lockout_threshold", {"auth": {"lockout_threshold": 5}}, 50),
    ("auth.lockout_window", {"auth": {"lockout_window": "15m"}}, "1m"),
    (
        "security.egress",
        {},
        {"allow_hosts": [], "deny_hosts": [], "allow_private": True},
    ),
    (
        "security.egress",
        {},
        {"allow_hosts": ["nas.local"], "deny_hosts": [], "allow_private": False},
    ),
    (
        "security.egress",
        {"security": {"egress": {"deny_hosts": ["tracker.example"]}}},
        {"allow_hosts": [], "deny_hosts": [], "allow_private": False},
    ),
    ("security.credential_keychain", {"security": {"credential_keychain": True}}, False),
    ("security.allow_git_push", {}, True),
    ("security.denied_commands", {"security": {"denied_commands": ["^rm "]}}, []),
    ("security.mcp_elicitation_servers", {}, ["some-server"]),
    ("security.outside_home", {}, ["agent-skills"]),
    ("sandbox.nofile", {}, 0),  # 0 removes the limit
    ("sandbox.max_pids", {"sandbox": {"max_pids": 500}}, 1000),
    ("sandbox.max_rss_mb", {"sandbox": {"max_rss_mb": 4096}}, 0),
    ("sandbox.cgroup_scopes", {"sandbox": {"cgroup_scopes": True}}, False),
    ("sandbox.env_passthrough", {}, ["MY_TOKEN_NAME"]),
    (
        "guardrails.budgets.max_tokens_per_run",
        {"guardrails": {"budgets": {"max_tokens_per_run": 1000}}},
        5000,
    ),
    (
        "guardrails.budgets.max_tokens_per_day",
        {"guardrails": {"budgets": {"max_tokens_per_day": 1000}}},
        0,  # 0 is unlimited
    ),
    (
        "guardrails.budgets.max_dollars_per_day",
        {"guardrails": {"budgets": {"max_dollars_per_day": 5.0}}},
        50.0,
    ),
    ("guardrails.loop_breaker.circuit_threshold", {}, 100),
    ("guardrails.scan_mode", {"guardrails": {"scan_mode": "block"}}, "warn"),
    ("external_access.enabled", {}, True),
    ("external_access.openai.enabled", {}, True),
    ("external_access.mcp.enabled", {}, True),
    ("external_access.a2a.enabled", {}, True),
    ("external_access.capture.enabled", {}, True),
    ("external_access.bridge.enabled", {}, True),
    ("external_access.rate_rps", {"external_access": {"rate_rps": 2.0}}, 500.0),
    ("external_access.rate_burst", {"external_access": {"rate_burst": 5}}, 500),
    ("external_access.rate_concurrent", {"external_access": {"rate_concurrent": 4}}, 100),
    (
        "external_access.auto_disable_after_breaches",
        {"external_access": {"auto_disable_after_breaches": 10}},
        0,  # 0 never auto-disables
    ),
    ("external_access.capture.upstream_allowlist", {}, ["api.example.com"]),
    ("proactive.auto_execute_enabled", {}, True),
    ("proactive.max_auto_actions_per_run", {}, 20),
    ("browse.user_browser_enabled", {}, True),
    ("durability.sync_enabled", {}, True),
    ("durability.sync_transport", {}, "some-transport"),
    ("durability.sync_encrypt", {"durability": {"sync_encrypt": "on"}}, "off"),
]


def _case_id(case: tuple) -> str:
    field, _seed, value = case
    return f"{field}={json.dumps(value, sort_keys=True)}"


def _the_app_is_refused(field: str, value: Any, *, confirms: tuple[bool, ...]):
    """The app's writes of *field*, sent both ways for each of *confirms*: each refused as a
    security setting, with an SEL row naming the app and the field, and your home as it was."""

    async def check(r: Row) -> None:
        before = home_files(r.home)
        theirs = [
            await _write(r.gw, field, value, confirm=confirm, token=r.token, as_backend=alone)
            for confirm in confirms
            for alone in WAYS.values()
        ]
        written = written_since(r.home, before)
        for status, text in theirs:
            assert status == 403, text
            assert _code(text) == SECURITY_SETTING, text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(field)) == len(theirs), "each refusal leaves an SEL row naming the app"

    return check


def _a_loosening(case: tuple):
    """The app is refused the write, with ``confirm`` and without; you are asked to consent, the
    question naming the field and what it changes, and with your consent it is written."""
    field, seed, value = case
    refused = _the_app_is_refused(field, value, confirms=(False, True))

    async def run(r: Row) -> None:
        _seed(r.home, seed)
        await refused(r)
        before = home_files(r.home)
        status, text = await _write(r.gw, field, value)
        # Your write is refused inside the config transaction, whose lock file holds nothing.
        written = [f for f in written_since(r.home, before) if f != _LOCK]
        assert status == 400, text
        error = json.loads(text)["error"]
        assert error["code"] == "confirmation_required"
        # The refusal carries the sentence a consent dialog shows, so a surface that did not
        # know this field was sensitive can still ask the right question…
        assert error["detail"]["field"] == field
        assert error["detail"]["consent"].strip()
        # …and what THIS write changes, from and to: the sentence alone is the same question
        # whatever value was typed (`test_a_loosening_consent_names_what_it_changes.py`).
        assert error["detail"]["change"].strip()
        assert not written, f"an unconsented write changed your home: {written}"
        status, text = await _write(r.gw, field, value, confirm=True)
        assert status == 200, text
        assert _stored(r.home, field) == value

    return run


def _controls() -> set[str]:
    from personalclaw.config.edit_spec import security_control
    from personalclaw.config.editable import _EDITABLE_CONFIG

    return {k for k, spec in _EDITABLE_CONFIG.items() if security_control(spec)}


@pytest.mark.asyncio
async def test_every_control_is_refused_to_an_app_and_loosened_only_with_your_consent(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [(_case_id(case), _a_loosening(case)) for case in LOOSENING_WRITES]
    assert len(rows) == len(LOOSENING_WRITES), "a case was left out of the table"
    assert {f for f, _s, _v in LOOSENING_WRITES} == _controls(), "a control was left out"
    await SHARED.run(subtests, rows, _routes([]), label="control")


async def _a_row_that_leaves_state_behind_fails_the_next_row(tmp_path_factory, monkeypatch) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    probe = (_case_id(LOOSENING_WRITES[0]), _a_loosening(LOOSENING_WRITES[0]))
    failed = await planted_rows(SHARED, _routes([]), probe)
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


#: The case the planted refusal below answers: dropping the 2FA requirement.
PLANTED = ("auth.require_totp", {"auth": {"require_totp": True}}, False)


async def _a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path_factory, monkeypatch
) -> None:
    """A config handler that, for the app's write of one field, stores it in your config and then
    answers as the refusal does: a 403 saying it is a security setting, and an SEL row naming the
    app and the field. Every other write goes to the real handler. Only your home tells it from a
    refusal, so its row fails on that, and the rows either side of it, the home put back before
    each, pass."""
    import personalclaw.config.loader as loader
    from personalclaw import sel
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch
    from personalclaw.http_errors import json_error

    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [(_case_id(case), _a_loosening(case)) for case in LOOSENING_WRITES]
    at = LOOSENING_WRITES.index(PLANTED)
    field, _seed_data, value = PLANTED

    async def writes_then_refuses(request: web.Request) -> web.StreamResponse:
        app = request.get("app", "")
        body = await request.json()
        if not app or body.get("path") != field:
            return await api_personalclaw_config_patch(request)
        _seed(loader.config_dir(), {"auth": {"require_totp": value}})
        sel.sel().log_api_access(
            caller=f"app:{app}",
            operation="config.patch",
            outcome="denied",
            source="app_permissions",
            resources=field,
        )
        return json_error(SECURITY_SETTING, message=f"{field} is a security setting", status=403)

    routes = [
        (m, t, writes_then_refuses if (m, t) == ("PATCH", CONFIG) else handler)
        for m, t, handler in _routes([])
    ]
    recorded = RecordedRows()
    await SHARED.run(recorded, rows[at - 1 : at + 2], routes, label="control")
    name = _case_id(PLANTED)
    assert list(recorded.failed) == [name], recorded.failed
    assert "a refusal changed your home: ['config.json']" in recorded.failed[name]


@pytest.mark.asyncio
async def test_the_table_keeps_each_row_to_itself(subtests, tmp_path_factory, monkeypatch) -> None:
    """Both halves of the table's isolation, each a row: a row that leaves state behind fails the
    row after it, and a refusal that writes state fails its own row and no other."""
    with row(subtests, check="a row that leaves state behind fails the next row"):
        await _a_row_that_leaves_state_behind_fails_the_next_row(tmp_path_factory, monkeypatch)
    with row(subtests, check="a refusal that writes state fails its own row and no other"):
        await _a_refusal_that_writes_state_fails_its_own_row_and_no_other(
            tmp_path_factory, monkeypatch
        )


# ── 2. Tightening is refused to an app too, and never needs your consent ───────────────────────

#: (field, what is stored now, the tighter value). The app is refused each — these include the
#: lockout and denial-of-service directions: 2FA before it is enrolled, a one-guess lockout, a
#: deny-everything command pattern, a one-token budget — and you write each without being asked:
#: revoking a grant is the direction a broken or confused client must always be able to take,
#: and a consent requirement there would be a lock on the emergency exit.
TIGHTENING_WRITES = [
    ("agent.yolo", {"agent": {"yolo": True}}, False),
    ("agent.approval_mode", {"agent": {"approval_mode": "auto"}}, "interactive"),
    ("auth.require_totp", {"auth": {"require_totp": False}}, True),
    # Turning password sign-in OFF removes a way in; it never needs this consent. The Account
    # panel's own dialog for it is a lockout warning, not a security one.
    ("auth.login_enabled", {"auth": {"login_enabled": True}}, False),
    ("auth.lockout_threshold", {"auth": {"lockout_threshold": 5}}, 3),
    ("auth.lockout_threshold", {"auth": {"lockout_threshold": 5}}, 1),
    ("security.denied_commands", {}, ["^curl "]),
    ("security.denied_commands", {}, [".*"]),
    (
        "guardrails.budgets.max_dollars_per_day",
        {"guardrails": {"budgets": {"max_dollars_per_day": 50.0}}},
        5.0,
    ),
    (
        "guardrails.budgets.max_tokens_per_day",
        {"guardrails": {"budgets": {"max_tokens_per_day": 0}}},
        100_000,
    ),
    ("guardrails.budgets.max_tokens_per_day", {}, 1),
    ("external_access.enabled", {"external_access": {"enabled": True}}, False),
    ("guardrails.scan_mode", {"guardrails": {"scan_mode": "warn"}}, "block"),
    ("security.credential_keychain", {}, True),
]


def _a_tightening(case: tuple):
    field, seed, value = case
    refused = _the_app_is_refused(field, value, confirms=(False,))

    async def run(r: Row) -> None:
        _seed(r.home, seed)
        await refused(r)
        status, text = await _write(r.gw, field, value)
        assert status == 200, text
        assert _stored(r.home, field) == value

    return run


@pytest.mark.asyncio
async def test_tightening_is_refused_to_an_app_and_never_needs_your_consent(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [(_case_id(case), _a_tightening(case)) for case in TIGHTENING_WRITES]
    assert len(rows) == len(TIGHTENING_WRITES), "a case was left out of the table"
    await SHARED.run(subtests, rows, _routes([]), label="control")


# ── 3. An app still writes an ordinary setting it declared ─────────────────────────────────────


async def _the_app_writes_the_ordinary_setting_it_declared(r: Row) -> None:
    # The `/api/config` grant is not revoked wholesale: a field that governs nothing about who may
    # do what stays writable by an app whose manifest names it in `permissions.config`.
    for alone, value in zip(WAYS.values(), (False, True), strict=True):
        status, text = await _write(r.gw, ORDINARY, value, token=r.token, as_backend=alone)
        assert status == 200, text
        assert _stored(r.home, ORDINARY) is value


async def _the_app_is_refused_an_ordinary_setting_it_did_not_declare(r: Row) -> None:
    before = home_files(r.home)
    theirs = [
        await _write(r.gw, UNDECLARED, False, token=r.token, as_backend=alone)
        for alone in WAYS.values()
    ]
    written = written_since(r.home, before)
    for status, text in theirs:
        assert status == 403, text
        assert _code(text) == NOT_DECLARED, text
    assert not written, f"a refusal changed your home: {written}"
    assert len(r.denials(UNDECLARED)) == 2, "each refusal leaves an SEL row naming the app"
    status, text = await _write(r.gw, UNDECLARED, False)
    assert status == 200, text
    assert _stored(r.home, UNDECLARED) is False


@pytest.mark.asyncio
async def test_an_app_still_writes_an_ordinary_setting_it_declared(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [
        ("the ordinary setting it declared", _the_app_writes_the_ordinary_setting_it_declared),
        (
            "an ordinary setting it did not declare",
            _the_app_is_refused_an_ordinary_setting_it_did_not_declare,
        ),
    ]
    await SHARED.run(subtests, rows, _routes([]), label="case")


# ── 4. An agent profile's approval mode is the same control, stored per agent ──────────────────


def _agent_request(body: Any, *, method: str = "POST", match: dict | None = None):
    """An OWNER request to an agent handler."""
    from unittest.mock import AsyncMock

    identity = {"user": "owner"}
    req = MagicMock()
    req.method = method
    req.json = AsyncMock(return_value=body)
    req.match_info = match or {}
    req.get = lambda key, default=None: identity.get(key, default)
    req.headers = {}
    req.app = {"state": MagicMock()}
    return req


@pytest.fixture
def agents_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.dashboard.handlers.agents.config_dir", lambda: home)
    per_file = tmp_path / "agents"
    per_file.mkdir()
    monkeypatch.setattr("personalclaw.agent.agents_dir", lambda: per_file)
    (per_file / "helper.json").write_text(json.dumps({"name": "helper"}), encoding="utf-8")
    return home


def _stored_approval_mode(home, name: str) -> str:
    cfg = json.loads((home / "config.json").read_text(encoding="utf-8"))
    return cfg["agents"][name].get("approval_mode", "")


class TestAnAgentsApprovalModeIsTheOwners:
    """The owner's half. An app reaches no agent write at all — every route is owner-only in
    ``apps/permissions.ROUTE_AUTHZ``, driven through the real middleware in
    ``test_apps_cannot_rewrite_agents_or_skills.py`` — because an agent's prompt and tools are
    as much the owner's as its approval mode."""

    @pytest.mark.asyncio
    async def test_the_owner_confirms_an_auto_approving_agent(self, agents_home):
        from personalclaw.dashboard.handlers.agents import (
            api_personalclaw_agent_update,
            api_personalclaw_agents_create,
        )

        unconfirmed = await api_personalclaw_agents_create(
            _agent_request({"name": "a1", "approval_mode": "auto"})
        )
        assert unconfirmed.status == 400
        body = json.loads(unconfirmed.body)
        assert body["error"]["code"] == "confirmation_required"
        assert body["error"]["detail"]["field"] == "agents.a1.approval_mode"
        # In the agent editor's own names for the two values.
        assert body["error"]["detail"]["change"] == "Default (hook-based) → Auto-approve all"
        assert not (agents_home / "config.json").exists()

        assert (await api_personalclaw_agents_create(_agent_request({"name": "a1"}))).status == 200
        refused = await api_personalclaw_agent_update(
            _agent_request({"approval_mode": "auto"}, method="PUT", match={"name": "a1"})
        )
        assert refused.status == 400
        assert _stored_approval_mode(agents_home, "a1") == ""

        granted = await api_personalclaw_agent_update(
            _agent_request(
                {"approval_mode": "auto", "confirm": True}, method="PUT", match={"name": "a1"}
            )
        )
        assert granted.status == 200
        assert _stored_approval_mode(agents_home, "a1") == "auto"

        # Handing the grant back never needs consent.
        revoked = await api_personalclaw_agent_update(
            _agent_request({"approval_mode": "interactive"}, method="PUT", match={"name": "a1"})
        )
        assert revoked.status == 200
        assert _stored_approval_mode(agents_home, "a1") == "interactive"


# ── 5. The side doors to the same posture ──────────────────────────────────────────────────────

#: Routes whose whole purpose is changing your posture, and that an app could reach with an
#: ordinary declaration, each at its real template with a path it is sent to.
#: `owner_only_api_reason` is what both the middleware and the install-time manifest check
#: consult.
POSTURE_ROUTES = [
    ("POST", "/api/chat/mode", "/api/chat/mode"),
    ("POST", "/api/incident/resume", "/api/incident/resume"),
    ("POST", "/api/guardrails/project-trust", "/api/guardrails/project-trust"),
    ("POST", "/api/autonomy/grant", "/api/autonomy/grant"),
    ("POST", "/api/memory/approval-rules", "/api/memory/approval-rules"),
    ("DELETE", "/api/memory/approval-rules/{key}", "/api/memory/approval-rules/some-key"),
    ("POST", "/api/devices/pair/start", "/api/devices/pair/start"),
    ("POST", "/api/external-access/clients", "/api/external-access/clients"),
    (
        "POST",
        "/api/external-access/clients/{client_id}/disabled",
        "/api/external-access/clients/abc/disabled",
    ),
    ("PUT", "/api/agent/config", "/api/agent/config"),
    # Handing a grant back needs no confirmation from the owner, and is a denial of service from
    # an app: it can undo every grant, restarting each cooldown.
    ("POST", "/api/autonomy/demote", "/api/autonomy/demote"),
    ("POST", "/api/autonomy/undo", "/api/autonomy/undo"),
]


def _a_posture_route(reached: list[tuple[str, str]], method: str, path: str):
    """No declaration reaches the route, not even ``*``: the app is refused both ways, each
    refusal an SEL row naming it, your home as it was, and only your request reaches it."""
    from personalclaw.apps.manifest import Permissions
    from personalclaw.apps.permissions import PermissionChecker, owner_only_api_reason

    async def run(r: Row) -> None:
        reached.clear()
        assert owner_only_api_reason(path), f"{path} must be owner-only"
        everything = PermissionChecker(app_name=APP, permissions=Permissions(api=["*"]))
        assert not everything.can_use_api(path), "not even a '*' declaration reaches it"
        before = home_files(r.home)
        theirs = await r.gw.both_ways(r.token, method, path, {})
        written = written_since(r.home, before)
        yours = await r.gw.call(method, path, {})
        for status, text in theirs:
            assert status == 403, text
            assert OWNER_ONLY in text, text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app"
        assert yours[0] == 200, yours
        assert reached == [("", path)], "only your request reached it"

    return run


@pytest.mark.asyncio
async def test_no_app_declaration_reaches_a_posture_route(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = [(f"{m} {t}", _a_posture_route(reached, m, p)) for m, t, p in POSTURE_ROUTES]
    assert len(rows) == len(POSTURE_ROUTES), "a route was left out of the table"
    await SHARED.run(subtests, rows, _routes(reached), label="route")


#: The stop and the read halves, which stay where an app can declare them.
NEIGHBOURING_PATHS = [
    "/api/incident",
    "/api/chat/sessions/s1/messages",
    "/api/devices/pair/complete",
    "/api/external-access",
    "/api/autonomy",
    "/api/agents",
]


def test_the_neighbouring_paths_stay_grantable(subtests) -> None:
    from personalclaw.apps.permissions import owner_only_api_reason

    for path in NEIGHBOURING_PATHS:
        with row(subtests, path=path):
            assert owner_only_api_reason(path) == ""


# ── 6. No approval in a chat is answered by an app ─────────────────────────────────────────────

#: The chat card's whole vocabulary. A standing verb raises the approval posture, and even a
#: one-off answer decides whether your agent's tool call runs, so no verb on the chat's approve
#: route is an app's. Nor is ``/api/approvals``: the menu-bar companion answers with your own
#: sign-in (``test_apps_cannot_post_into_your_chats.py``).
VERBS = ["yolo", "trust", "trust_agent", "trust_reads", "approved", "rejected"]


def _no_verb_is_decided_for_the_app(verb: str):
    path = APPROVE.replace("{session}", "mine")

    async def run(r: Row) -> None:
        _session, pending = _pending(r.gw.state, "mine")
        before = home_files(r.home)
        body = {"action": verb, "request_id": "req-1"}
        theirs = await r.gw.both_ways(r.token, "POST", path, body)
        written = written_since(r.home, before)
        for status, text in theirs:
            assert status == 403, text
            assert OWNER_ONLY in text, text
        assert not pending.done(), "nothing may be decided, least of all a standing grant"
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app"

    return run


def _your_answer_is_decided(verb: str, body: dict[str, str]):
    path = APPROVE.replace("{session}", "mine")

    async def run(r: Row) -> None:
        _session, pending = _pending(r.gw.state, "mine")
        status, text = await r.gw.call("POST", path, {"action": verb, **body})
        assert status == 200, text
        assert pending.done() and pending.result() == verb

    return run


#: Your answers, each naming the approval it answers, or naming none when one is pending.
YOUR_ANSWERS = {"approved": {}, "rejected": {"request_id": "req-1"}}


@pytest.mark.asyncio
async def test_an_app_answers_no_approval_in_a_chat_and_you_do(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    from personalclaw.dashboard.approval_state import SESSION_APPROVAL_ACTIONS

    await SHARED.home(tmp_path_factory, monkeypatch)
    assert set(VERBS) == SESSION_APPROVAL_ACTIONS, "the verbs are the card's vocabulary"
    rows = [(f"the app's {verb}", _no_verb_is_decided_for_the_app(verb)) for verb in VERBS]
    rows += [(f"your {verb}", _your_answer_is_decided(verb, b)) for verb, b in YOUR_ANSWERS.items()]
    await SHARED.run(subtests, rows, _routes([]), label="case")


# ── 7. Internal routes keep the app identity the token carries ─────────────────────────────────


def _the_apps_token_stays_the_apps(reached: list[tuple[str, str]], alone: bool):
    """An internal route admits the browser by its cookie or a ``?token=``, from loopback. That
    branch validated an app token like any other and never recorded the ``app`` claim, so the
    request reached the handler as the owner — and ``/api/tools/invoke`` runs any tool. The app
    permission middleware and every handler-level app check key on ``request["app"]``, so this
    is the one place the claim has to survive."""

    async def run(r: Row) -> None:
        reached.clear()
        status, text = await r.gw.call(
            "POST", TOOLS_INVOKE, {}, app_token=r.token, as_backend=alone
        )
        assert status == 200, text
        assert json.loads(text)["app"] == APP
        assert reached == [(APP, TOOLS_INVOKE)]

    return run


def _the_apps_token_on_a_mixed_route_is_refused_as_the_apps(reached: list[tuple[str, str]]):
    """A mixed internal route that is yours: the claim survives the cookie beside it, so the
    permission middleware refuses the app, by name."""

    async def run(r: Row) -> None:
        reached.clear()
        before = home_files(r.home)
        theirs = await r.gw.both_ways(r.token, "POST", TRIGGERS, {})
        written = written_since(r.home, before)
        for status, text in theirs:
            assert status == 403, text
            assert OWNER_ONLY in text, text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(TRIGGERS)) == 2, "each refusal leaves an SEL row naming the app"
        assert reached == [], "the app's request reached nothing"

    return run


def _your_cookie_alone_is_you(reached: list[tuple[str, str]]):
    async def run(r: Row) -> None:
        reached.clear()
        for path in (TOOLS_INVOKE, TRIGGERS):
            status, text = await r.gw.call("POST", path, {})
            assert status == 200, text
            assert json.loads(text)["app"] == ""
        assert reached == [("", TOOLS_INVOKE), ("", TRIGGERS)]

    return run


def _the_internal_secret_is_personalclaws_own(reached: list[tuple[str, str]]):
    async def run(r: Row) -> None:
        reached.clear()
        for path in (TOOLS_INVOKE, TRIGGERS):
            resp = await r.gw.client.post(
                path, json={}, headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            assert resp.status == 200, await resp.text()
            assert (await resp.json())["app"] == ""
        assert reached == [("", TOOLS_INVOKE), ("", TRIGGERS)]

    return run


@pytest.mark.asyncio
async def test_internal_routes_keep_the_app_identity(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = [
        (
            f"the app's token {way}, on an internal route, stays the app's",
            _the_apps_token_stays_the_apps(reached, alone),
        )
        for way, alone in WAYS.items()
    ]
    rows += [
        (
            "the app's token on a mixed route of yours is refused as the app's",
            _the_apps_token_on_a_mixed_route_is_refused_as_the_apps(reached),
        ),
        ("your cookie alone is you", _your_cookie_alone_is_you(reached)),
        (
            "the internal secret is PersonalClaw's own",
            _the_internal_secret_is_personalclaws_own(reached),
        ),
    ]
    await SHARED.run(subtests, rows, _routes(reached), label="case")


# ── 8. Each family, end to end, on an app installed for this test alone ────────────────────────


@pytest.mark.asyncio
async def test_each_family_refuses_an_app_installed_for_this_test_alone(home, sel_rows) -> None:
    """Your settings, a posture route, a chat's approval and an internal route, each sent both
    ways by an app installed for this test alone."""
    reached: list[tuple[str, str]] = []
    approve = APPROVE.replace("{session}", "mine")
    async with _gateway(_routes(reached)) as gw:
        await gw.install(_bundle(home))
        token = str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])
        _session, pending = _pending(gw.state, "mine")
        before = home_files(home)
        yolo = [
            await _write(gw, "agent.yolo", True, confirm=True, token=token, as_backend=alone)
            for alone in WAYS.values()
        ]
        mode = await gw.both_ways(token, "POST", "/api/chat/mode", {"mode": "yolo"})
        answer = await gw.both_ways(
            token, "POST", approve, {"action": "yolo", "request_id": "req-1"}
        )
        triggers = await gw.both_ways(token, "POST", TRIGGERS, {})
        invoke = await gw.both_ways(token, "POST", TOOLS_INVOKE, {})
        written = written_since(home, before)
    for status, text in yolo:
        assert status == 403 and _code(text) == SECURITY_SETTING, text
    assert not written, f"a refusal changed your home: {written}"
    for path, sent in (("/api/chat/mode", mode), (approve, answer), (TRIGGERS, triggers)):
        for status, text in sent:
            assert status == 403 and OWNER_ONLY in text, f"{path}: {text}"
        assert len(_denials(sel_rows, path)) == 2, path
    assert len(_denials(sel_rows, "agent.yolo")) == 2
    assert not pending.done(), "nothing was decided"
    for status, text in invoke:
        assert status == 200 and json.loads(text)["app"] == APP, text
    assert (
        reached == [(APP, TOOLS_INVOKE)] * 2
    ), "only the internal route the app may call is reached"
