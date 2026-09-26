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

These tests drive the real handlers. Each app case sets ``request["app"]`` the way
``token_auth`` does for an app-scoped token; ``TestInternalRoutesKeepTheAppIdentity`` mints a
real app token and runs it through the real middleware.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

APP = "evil-app"


def _identity(app_name: str) -> Any:
    """What ``token_auth`` hands the handler: the owner's user, plus the app claim if any."""

    @web.middleware
    async def mw(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if app_name:
            request["app"] = app_name
        return await handler(request)

    return mw


def _config_app(app_name: str = "") -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application(middlewares=[_identity(app_name)])
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """An isolated home. `config_dir` too, because a PATCH's live-apply hooks (the Self-QA
    reconcile) write beside the config file."""
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    with patch("personalclaw.config.loader.config_path", return_value=path):
        yield path


@pytest.fixture
def sel_rows():
    """Every SEL row the config handlers write, captured."""
    fake = MagicMock()
    with patch("personalclaw.dashboard.handlers.sel", return_value=fake):
        yield fake.log_api_access


def _seed(path, data: dict) -> str:
    text = json.dumps(data)
    path.write_text(text, encoding="utf-8")
    return text


async def _patch(client: TestClient, field: str, value: Any, *, confirm: bool = False):
    body: dict[str, Any] = {"path": field, "value": value}
    if confirm:
        body["confirm"] = True
    return await client.patch("/api/config/personalclaw", json=body)


def _denials_naming(rows: MagicMock, *, caller: str, field: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == caller
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("resources") == field
    ]


# ── An app can never write a security setting ──────────────────────────────────────


class TestAnAppCannotWriteASecuritySetting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("confirm", [False, True], ids=["no-confirm", "with-confirm"])
    async def test_an_app_cannot_turn_yolo_on(self, config_file, sel_rows, confirm) -> None:
        before = config_file.read_text(encoding="utf-8")
        async with TestClient(TestServer(_config_app(APP))) as c:
            resp = await _patch(c, "agent.yolo", True, confirm=confirm)
            assert resp.status == 403
            body = await resp.json()
        assert body["error"]["code"] == "security_setting_owner_only"
        assert config_file.read_text(encoding="utf-8") == before, "nothing may be written"
        assert _denials_naming(
            sel_rows, caller=f"app:{APP}", field="agent.yolo"
        ), "the refusal must leave an SEL row naming the app and the field"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "seed", "value"),
        [
            ("auth.require_totp", {"auth": {"require_totp": True}}, False),
            ("auth.login_enabled", {"auth": {"login_enabled": False}}, True),
            ("auth.lockout_threshold", {"auth": {"lockout_threshold": 5}}, 100),
            ("auth.session_ttl", {"auth": {"session_ttl": "30d"}}, "365d"),
        ],
    )
    @pytest.mark.parametrize("confirm", [False, True], ids=["no-confirm", "with-confirm"])
    async def test_an_app_cannot_loosen_sign_in(
        self, config_file, sel_rows, field, seed, value, confirm
    ) -> None:
        before = _seed(config_file, seed)
        async with TestClient(TestServer(_config_app(APP))) as c:
            resp = await _patch(c, field, value, confirm=confirm)
            assert resp.status == 403
        assert config_file.read_text(encoding="utf-8") == before
        assert _denials_naming(sel_rows, caller=f"app:{APP}", field=field)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "seed", "value"),
        [
            # Tightening is refused too. These are the lockout and denial-of-service
            # directions: 2FA before it is enrolled, a one-guess lockout, a deny-everything
            # command pattern, a zero-token budget.
            ("auth.require_totp", {"auth": {"require_totp": False}}, True),
            ("auth.lockout_threshold", {"auth": {"lockout_threshold": 5}}, 1),
            ("security.denied_commands", {}, [".*"]),
            ("guardrails.budgets.max_tokens_per_day", {}, 1),
            ("agent.yolo", {"agent": {"yolo": True}}, False),
        ],
    )
    async def test_an_app_cannot_tighten_one_either(
        self, config_file, sel_rows, field, seed, value
    ) -> None:
        before = _seed(config_file, seed)
        async with TestClient(TestServer(_config_app(APP))) as c:
            resp = await _patch(c, field, value)
            assert resp.status == 403
        assert config_file.read_text(encoding="utf-8") == before
        assert _denials_naming(sel_rows, caller=f"app:{APP}", field=field)

    @pytest.mark.asyncio
    async def test_an_app_still_writes_an_ordinary_setting_it_declared(
        self, config_file, sel_rows
    ) -> None:
        # The `/api/config` grant is not revoked wholesale: a field that governs nothing about
        # who may do what stays writable by an app whose manifest names it in
        # `permissions.config` — the list install consent shows. An undeclared one is refused
        # (`test_apps_cannot_run_code_or_bypass_approvals.py`).
        with patch(
            "personalclaw.dashboard.handlers.core._app_config_fields",
            return_value=["voice.echo_filter_enabled"],
        ):
            async with TestClient(TestServer(_config_app(APP))) as c:
                resp = await _patch(c, "voice.echo_filter_enabled", False)
                assert resp.status == 200
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["voice"]["echo_filter_enabled"] is False


# ── The owner's loosening writes need consent; tightening never does ─────────────────

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
    ("security.denied_commands", {"security": {"denied_commands": ["^rm "]}}, []),
    ("security.mcp_elicitation_servers", {}, ["some-server"]),
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
    field, seed, value = case
    return f"{field}={json.dumps(value, sort_keys=True)}"


class TestTheOwnerConsentsToEveryLoosening:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", LOOSENING_WRITES, ids=_case_id)
    async def test_without_confirm_it_is_refused_and_nothing_changes(
        self, config_file, sel_rows, case
    ) -> None:
        field, seed, value = case
        before = _seed(config_file, seed)
        async with TestClient(TestServer(_config_app())) as c:
            resp = await _patch(c, field, value)
            assert resp.status == 400, await resp.text()
            body = await resp.json()
        assert body["error"]["code"] == "confirmation_required"
        # The refusal carries the sentence a consent dialog shows, so a surface that did not
        # know this field was sensitive can still ask the right question.
        assert body["error"]["detail"]["field"] == field
        assert body["error"]["detail"]["consent"].strip()
        assert config_file.read_text(encoding="utf-8") == before, "nothing may be written"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", LOOSENING_WRITES, ids=_case_id)
    async def test_with_confirm_it_is_written(self, config_file, sel_rows, case) -> None:
        field, seed, value = case
        _seed(config_file, seed)
        async with TestClient(TestServer(_config_app())) as c:
            resp = await _patch(c, field, value, confirm=True)
            assert resp.status == 200, await resp.text()
        stored: Any = json.loads(config_file.read_text(encoding="utf-8"))
        for part in field.split("."):
            stored = stored[part]
        assert stored == value

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "seed", "value"),
        [
            ("agent.yolo", {"agent": {"yolo": True}}, False),
            ("agent.approval_mode", {"agent": {"approval_mode": "auto"}}, "interactive"),
            ("auth.require_totp", {"auth": {"require_totp": False}}, True),
            # Turning password sign-in OFF removes a way in; it never needs this consent. The
            # Account panel's own dialog for it is a lockout warning, not a security one.
            ("auth.login_enabled", {"auth": {"login_enabled": True}}, False),
            ("auth.lockout_threshold", {"auth": {"lockout_threshold": 5}}, 3),
            ("security.denied_commands", {}, ["^curl "]),
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
            ("external_access.enabled", {"external_access": {"enabled": True}}, False),
            ("guardrails.scan_mode", {"guardrails": {"scan_mode": "warn"}}, "block"),
            ("security.credential_keychain", {}, True),
        ],
    )
    async def test_tightening_never_needs_consent(
        self, config_file, sel_rows, field, seed, value
    ) -> None:
        # Revoking a grant is the direction a broken or confused client must always be able
        # to take; a consent requirement there would be a lock on the emergency exit.
        _seed(config_file, seed)
        async with TestClient(TestServer(_config_app())) as c:
            resp = await _patch(c, field, value)
            assert resp.status == 200, await resp.text()


class TestEveryControlIsRefusedToAnApp:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", LOOSENING_WRITES, ids=_case_id)
    async def test_an_app_is_refused_even_with_confirm(self, config_file, sel_rows, case) -> None:
        field, seed, value = case
        before = _seed(config_file, seed)
        async with TestClient(TestServer(_config_app(APP))) as c:
            resp = await _patch(c, field, value, confirm=True)
            assert resp.status == 403, await resp.text()
            assert (await resp.json())["error"]["code"] == "security_setting_owner_only"
        assert config_file.read_text(encoding="utf-8") == before
        assert _denials_naming(sel_rows, caller=f"app:{APP}", field=field)


# ── An agent profile's approval mode is the same control, stored per agent ──────────


def _agent_request(body: Any, *, app: str = "", method: str = "POST", match: dict | None = None):
    from unittest.mock import AsyncMock

    identity = {"user": "owner", **({"app": app} if app else {})}
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
    @pytest.mark.asyncio
    async def test_an_app_cannot_create_an_auto_approving_agent(self, agents_home, sel_rows):
        from personalclaw.dashboard.handlers.agents import api_personalclaw_agents_create

        resp = await api_personalclaw_agents_create(
            _agent_request({"name": "sneaky", "approval_mode": "auto", "confirm": True}, app=APP)
        )
        assert resp.status == 403
        assert not (agents_home / "config.json").exists(), "nothing may be written"
        assert _denials_naming(sel_rows, caller=f"app:{APP}", field="agents.sneaky.approval_mode")

    @pytest.mark.asyncio
    async def test_an_app_cannot_make_an_agent_auto_approve(self, agents_home, sel_rows):
        from personalclaw.dashboard.handlers.agents import (
            api_personalclaw_agent_update,
            api_personalclaw_agents_create,
        )

        assert (await api_personalclaw_agents_create(_agent_request({"name": "a1"}))).status == 200
        resp = await api_personalclaw_agent_update(
            _agent_request(
                {"approval_mode": "auto", "confirm": True},
                app=APP,
                method="PUT",
                match={"name": "a1"},
            )
        )
        assert resp.status == 403
        assert _stored_approval_mode(agents_home, "a1") == ""

    @pytest.mark.asyncio
    async def test_an_app_cannot_patch_a_per_file_agents_approval_mode(self, agents_home):
        from personalclaw.agent import agents_dir
        from personalclaw.dashboard.handlers.agents import api_agent_detail

        before = (agents_dir() / "helper.json").read_text(encoding="utf-8")
        resp = await api_agent_detail(
            _agent_request(
                {"approval_mode": "auto", "confirm": True},
                app=APP,
                method="PATCH",
                match={"name": "helper"},
            )
        )
        assert resp.status == 403
        assert (agents_dir() / "helper.json").read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_an_app_still_edits_an_agents_description(self, agents_home):
        from personalclaw.dashboard.handlers.agents import (
            api_personalclaw_agent_update,
            api_personalclaw_agents_create,
        )

        assert (await api_personalclaw_agents_create(_agent_request({"name": "a1"}))).status == 200
        resp = await api_personalclaw_agent_update(
            _agent_request({"description": "hi"}, app=APP, method="PUT", match={"name": "a1"})
        )
        assert resp.status == 200

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


# ── The side doors to the same posture ─────────────────────────────────────────────


class TestTheDedicatedPostureRoutesAreOwnerOnly:
    """Routes whose whole purpose is changing the owner's posture, and that an app could reach
    with an ordinary declaration. `owner_only_api_reason` is what both the middleware and the
    install-time manifest check consult."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/chat/mode",
            "/api/incident/resume",
            "/api/guardrails/project-trust",
            "/api/autonomy/grant",
            "/api/memory/approval-rules",
            "/api/memory/approval-rules/some-key",
            "/api/devices/pair/start",
            "/api/external-access/clients",
            "/api/external-access/clients/abc/disabled",
            "/api/agent/config",
            # Handing a grant back needs no confirmation from the owner, and is a denial of
            # service from an app: it can undo every grant, restarting each cooldown.
            "/api/autonomy/demote",
            "/api/autonomy/undo",
        ],
    )
    def test_no_app_declaration_reaches_it(self, path) -> None:
        from personalclaw.apps.manifest import Permissions
        from personalclaw.apps.permissions import PermissionChecker, owner_only_api_reason

        assert owner_only_api_reason(path), f"{path} must be owner-only"
        everything = PermissionChecker(app_name=APP, permissions=Permissions(api=["*"]))
        assert not everything.can_use_api(path), "not even a '*' declaration reaches it"

    @pytest.mark.parametrize(
        "path",
        [
            # The stop and the read halves stay where an app can declare them.
            "/api/incident",
            "/api/chat/sessions/s1/messages",
            "/api/devices/pair/complete",
            "/api/external-access",
            "/api/autonomy",
            "/api/agents",
        ],
    )
    def test_the_neighbouring_paths_stay_grantable(self, path) -> None:
        from personalclaw.apps.permissions import owner_only_api_reason

        assert owner_only_api_reason(path) == ""


def _approve_app(state: Any, app_name: str) -> web.Application:
    from personalclaw.dashboard.chat_handlers import api_chat_session_approve

    app = web.Application(middlewares=[_identity(app_name)])
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)
    return app


class _PendingApprovalState:
    """One chat with one pending approval, recording every decision it is asked to make."""

    def __init__(self) -> None:
        import asyncio

        self.decisions: list[tuple[str, str]] = []
        session = MagicMock()
        future = asyncio.get_running_loop().create_future()
        session._approval_futures = {"req-1": future}
        self._sessions = {"s1": session}

    def decide_session_approval(self, session: Any, request_id: str, action: str) -> None:
        self.decisions.append((request_id, action))


class TestAnAppAnswersAnApprovalOnlyOnce:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("verb", ["yolo", "trust", "trust_agent", "trust_reads"])
    async def test_a_standing_grant_verb_is_refused(self, sel_rows, verb) -> None:
        state = _PendingApprovalState()
        async with TestClient(TestServer(_approve_app(state, APP))) as c:
            resp = await c.post("/api/chat/sessions/s1/approve", json={"action": verb})
            assert resp.status == 403
        assert state.decisions == [], "nothing may be decided, least of all a standing grant"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verb", ["approved", "rejected"])
    async def test_a_one_off_answer_still_works(self, verb) -> None:
        # The menu-bar companion's whole job is answering approvals away from the dashboard;
        # a one-off answer is the owner's decision relayed, not a change of posture.
        state = _PendingApprovalState()
        async with TestClient(TestServer(_approve_app(state, APP))) as c:
            resp = await c.post("/api/chat/sessions/s1/approve", json={"action": verb})
            assert resp.status == 200
        assert state.decisions == [("req-1", verb)]


# ── Internal routes keep the app identity the token carries ───────────────────────


#: The port the middleware names its session cookie after (``pc_token_{port}``).
_PORT = 10000


def _echo_identity_app(**mw_kwargs: Any) -> web.Application:
    from personalclaw.dashboard.token_auth import token_auth_middleware

    async def echo(request: web.Request) -> web.Response:
        return web.json_response({"app": request.get("app", "")})

    app = web.Application(middlewares=[token_auth_middleware(port=_PORT, **mw_kwargs)])
    app.router.add_post("/api/tools/invoke", echo)
    app.router.add_post("/api/triggers", echo)
    return app


class TestInternalRoutesKeepTheAppIdentity:
    """An internal route admits the browser by its cookie or a `?token=`, from loopback. That
    branch validated an app token like any other and never recorded the `app` claim, so the
    request reached the handler as the owner — and `/api/tools/invoke` runs any tool. The app
    permission middleware and every handler-level app check key on `request["app"]`, so this is
    the one place the claim has to survive."""

    @pytest.fixture(autouse=True)
    def _fresh_sessions(self):
        from personalclaw.dashboard.token_auth import revoke_all_sessions

        revoke_all_sessions()
        yield
        revoke_all_sessions()

    @pytest.mark.asyncio
    async def test_an_app_token_as_the_credential_stays_an_app(self) -> None:
        from personalclaw.dashboard.token_auth import generate_token

        token = generate_token("owner", ttl_seconds=600, app=APP)
        app = _echo_identity_app(
            internal_paths=frozenset({"/api/tools/invoke"}), internal_secret="s3cret"
        )
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(f"/api/tools/invoke?token={token}", json={})
            assert resp.status == 200
            assert (await resp.json())["app"] == APP

    @pytest.mark.asyncio
    async def test_an_app_token_layered_on_the_owner_cookie_stays_an_app(self) -> None:
        from personalclaw.dashboard.token_auth import generate_token

        owner = generate_token("owner", ttl_seconds=600)
        app_token = generate_token("owner", ttl_seconds=600, app=APP)
        app = _echo_identity_app(
            mixed_internal_paths=frozenset({"/api/triggers"}), internal_secret="s3cret"
        )
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(
                "/api/triggers",
                json={},
                headers={
                    "Cookie": f"pc_token_{_PORT}={owner}",
                    "Authorization": f"Bearer {app_token}",
                },
            )
            assert resp.status == 200
            assert (await resp.json())["app"] == APP

    @pytest.mark.asyncio
    async def test_the_owner_cookie_alone_is_still_the_owner(self) -> None:
        from personalclaw.dashboard.token_auth import generate_token

        owner = generate_token("owner", ttl_seconds=600)
        app = _echo_identity_app(
            mixed_internal_paths=frozenset({"/api/triggers"}), internal_secret="s3cret"
        )
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(
                "/api/triggers", json={}, headers={"Cookie": f"pc_token_{_PORT}={owner}"}
            )
            assert resp.status == 200
            assert (await resp.json())["app"] == ""

    @pytest.mark.asyncio
    async def test_the_internal_secret_is_still_core(self) -> None:
        app = _echo_identity_app(
            internal_paths=frozenset({"/api/tools/invoke"}), internal_secret="s3cret"
        )
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(
                "/api/tools/invoke", json={}, headers={"X-Internal-Secret": "s3cret"}
            )
            assert resp.status == 200
            assert (await resp.json())["app"] == ""
