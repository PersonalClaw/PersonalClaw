"""INU-7 T7.2 — the app emission path for proposals.

Deny-by-default, proven on the three refusals rather than asserted in a docstring:

* no app-scoped identity → 403 (identity comes from the transport, never the body);
* a kind the app did not declare in ``permissions.proposals`` → 403;
* an ``apply.app_callback`` naming ANOTHER app → 403 (otherwise app A could launder a
  call into app B through the user's approval click).

Plus the mechanics that make the grant real: the declared kind is REGISTERED at enable
time (``verifiable=True``, so INU-6's gate may apply), DEREGISTERED on disable so no
phantom kind outlives its app, one SEL row per emission (granted and denied), and the
emitted row carries a C6 payload the apply dispatcher can read.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import notification_kinds
from personalclaw import proposals_contract as pc
from personalclaw.apps import app_manager, manager
from personalclaw.apps.manifest import AppManifest, Permissions, ProposalKind
from personalclaw.dashboard import handlers_inbox

# ── the manifest field ──


def test_permissions_roundtrip_carries_proposals():
    p = Permissions(proposals=[ProposalKind(kind_suffix="draft", label="Draft reply")])
    assert p.to_dict()["proposals"] == [{"kind_suffix": "draft", "label": "Draft reply"}]
    back = Permissions.from_dict(p.to_dict())
    assert back.proposals == p.proposals
    # Empty is omitted from the consent surface (same discipline as appMessaging).
    assert "proposals" not in Permissions().to_dict()


def test_from_dict_defaults_the_label_to_the_suffix():
    p = Permissions.from_dict({"proposals": [{"kind_suffix": "draft"}]})
    assert p.proposals[0].label == "draft"


def _manifest(**perms) -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": "demo",
            "version": "1.0.0",
            "displayName": "Demo",
            "description": "x",
            "permissions": perms,
        }
    )


@pytest.mark.parametrize(
    "suffix,expected",
    [("draft", ""), ("Draft", "must be a slug"), ("a/b", "must be a slug"), ("", "kind_suffix")],
)
def test_validate_rejects_a_suffix_that_would_break_the_rules_key(suffix, expected):
    errs = _manifest(proposals=[{"kind_suffix": suffix}]).validate()
    hits = [e for e in errs if "proposal" in e]
    if not expected:
        assert hits == []
    else:
        assert hits and expected in hits[0]


def test_validate_rejects_a_duplicate_suffix():
    errs = _manifest(proposals=[{"kind_suffix": "draft"}, {"kind_suffix": "draft"}]).validate()
    assert any("duplicate proposal kind_suffix" in e for e in errs)


def test_declared_proposals_reach_the_pre_install_consent_payload():
    """The Store's install-consent panel is the ONLY place a user sees what an app may
    raise, so the field has to survive ``catalog._manifest_consent`` — not merely
    ``to_dict``."""
    from personalclaw.apps.catalog import _manifest_consent

    perms, _crons = _manifest_consent(_manifest(proposals=[{"kind_suffix": "draft"}]))
    assert perms["proposals"] == [{"kind_suffix": "draft", "label": "draft"}]


# ── enable-time registration ──


@pytest.fixture()
def clean_registry():
    """Snapshot/restore the process-global kind registry (test isolation)."""
    saved = dict(notification_kinds._REGISTRY)
    yield
    notification_kinds._REGISTRY.clear()
    notification_kinds._REGISTRY.update(saved)


def test_register_app_kinds_mints_a_verifiable_attention_pair(clean_registry):
    kinds = pc.register_app_proposal_kinds("demo", _manifest(proposals=[{"kind_suffix": "draft"}]))
    assert kinds == ["proposal:draft"]
    k = notification_kinds.resolve_kind("app:demo", "proposal:draft")
    assert (k.source, k.kind) == ("app:demo", "proposal:draft")
    # verifiable → INU-6's skeptic MAY apply once a rule opts in; attention → durable row.
    assert k.verifiable is True and k.attention is True


def test_re_enable_does_not_raise_on_the_duplicate(clean_registry):
    m = _manifest(proposals=[{"kind_suffix": "draft"}])
    pc.register_app_proposal_kinds("demo", m)
    assert pc.register_app_proposal_kinds("demo", m) == ["proposal:draft"]


def test_deregister_leaves_no_phantom_kind(clean_registry):
    m = _manifest(proposals=[{"kind_suffix": "draft"}])
    pc.register_app_proposal_kinds("demo", m)
    assert pc.deregister_app_proposal_kinds("demo", m) == ["proposal:draft"]
    # resolve_kind fails OPEN to a generic, so the tell is the registry itself.
    assert ("app:demo", "proposal:draft") not in notification_kinds._REGISTRY


def test_two_apps_declaring_the_same_suffix_do_not_collide(clean_registry):
    m = _manifest(proposals=[{"kind_suffix": "draft"}])
    pc.register_app_proposal_kinds("a", m)
    pc.register_app_proposal_kinds("b", m)
    assert ("app:a", "proposal:draft") in notification_kinds._REGISTRY
    assert ("app:b", "proposal:draft") in notification_kinds._REGISTRY


# ── HTTP: POST /api/inbox/proposals ──


class _State:
    """The minimum DashboardState surface the two handlers touch."""

    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, dict]] = []
        self.notifications: list[dict] = []

    def broadcast_ws(self, event: str, payload: dict) -> None:
        self.broadcasts.append((event, payload))

    def notify(self, *args, **kwargs) -> None:
        self.notifications.append({"args": args, **kwargs})


@asynccontextmanager
async def _client(tmp_path, monkeypatch):
    """``X-Test-App`` stands in for the verified app-scoped token: the middleware stamps
    ``request["app"]`` exactly as token auth would, so identity is un-spoofable by body."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
        patch("personalclaw.inbox.config_dir", return_value=tmp_path),
    ):

        @web.middleware
        async def stamp_app(request, handler):
            ident = request.headers.get("X-Test-App", "")
            if ident:
                request["app"] = ident
            return await handler(request)

        app = web.Application(middlewares=[stamp_app])
        app["state"] = _State()
        app.router.add_post("/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create)
        app.router.add_post("/api/inbox/{id}/apply", handlers_inbox.api_inbox_proposal_apply)
        async with TestClient(TestServer(app)) as client:
            yield client


def _install(tmp_path: Path, name: str, *, proposals: list[dict] | None = None):
    d = tmp_path / "src" / name
    d.mkdir(parents=True)
    mani: dict = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
    if proposals is not None:
        mani["permissions"] = {"proposals": proposals, "api": ["/api/inbox/proposals"]}
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    res = app_manager.install(d)
    assert res.ok, res.error


def _sel_rows(tmp_path: Path) -> list[dict]:
    rows: list[dict] = []
    path = tmp_path / "security_events.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


@pytest.mark.asyncio
async def test_no_app_identity_is_refused(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        r = await client.post("/api/inbox/proposals", json={"kind_suffix": "draft"})
        assert r.status == 403
        assert "app-scoped token" in (await r.json())["error"]


@pytest.mark.asyncio
async def test_undeclared_kind_is_refused_and_audited(tmp_path, monkeypatch):
    """Deny by default: the app is real and authenticated, it just never declared this
    kind. The 403 reads the MANIFEST, so an app cannot widen its own reach by posting."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "demo", proposals=[{"kind_suffix": "draft"}])
        r = await client.post(
            "/api/inbox/proposals",
            json={"kind_suffix": "wire-money", "title": "t", "apply": {"workflow": {"ref": "w"}}},
            headers={"X-Test-App": "demo"},
        )
        assert r.status == 403
        assert "not declared" in (await r.json())["error"]
    denied = [
        r
        for r in _sel_rows(tmp_path)
        if r.get("operation") == "inbox.proposal_emit" and r.get("outcome") == "denied"
    ]
    assert denied and denied[0]["caller_identity"] == "app:demo"


@pytest.mark.asyncio
async def test_a_foreign_app_callback_is_refused(tmp_path, monkeypatch):
    """The cross-app laundering case: `demo` proposes a callback into `other`, so a single
    user click would invoke an app that never consented. Refused at the door."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "demo", proposals=[{"kind_suffix": "draft"}])
        _install(tmp_path, "other", proposals=[{"kind_suffix": "draft"}])
        r = await client.post(
            "/api/inbox/proposals",
            json={
                "kind_suffix": "draft",
                "title": "Innocent looking",
                "apply": {"app_callback": {"app": "other", "route": "transfer"}},
            },
            headers={"X-Test-App": "demo"},
        )
        assert r.status == 403
        body = await r.json()
        assert "may not propose a callback into 'other'" in body["error"]
    assert any(
        r.get("error") == "foreign app_callback"
        for r in _sel_rows(tmp_path)
        if r.get("operation") == "inbox.proposal_emit"
    )


@pytest.mark.asyncio
async def test_own_app_callback_is_accepted_and_the_identity_is_stamped(tmp_path, monkeypatch):
    """A callback with NO `app` defaults to the caller — and is stamped, not trusted: the
    stored payload names the token's app, so apply cannot be redirected later."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "demo", proposals=[{"kind_suffix": "draft", "label": "Draft"}])
        r = await client.post(
            "/api/inbox/proposals",
            json={
                "kind_suffix": "draft",
                "title": "Send the reply?",
                "preview": "Hi there",
                "apply": {"app_callback": {"route": "send"}},
            },
            headers={"X-Test-App": "demo"},
        )
        assert r.status == 201, await r.text()
        item_id = (await r.json())["id"]

        from personalclaw.inbox import InboxStore

        store = InboxStore()
        store.load()
        item = store.items[item_id]
        payload = pc.Proposal.from_dict(item.refs[pc.REFS_KEY])
        assert payload.apply_case() is pc.ApplyCase.APP_CALLBACK
        assert payload.payload()["app"] == "demo"
        assert payload.provenance == "app:demo"
        assert item.item_kind == "proposal"

    granted = [
        r
        for r in _sel_rows(tmp_path)
        if r.get("operation") == "inbox.proposal_emit" and r.get("outcome") == "granted"
    ]
    assert len(granted) == 1
    assert granted[0]["resources"] == "kind=proposal:draft"


@pytest.mark.asyncio
async def test_a_payload_with_no_nameable_apply_case_is_refused(tmp_path, monkeypatch):
    """A row nobody can approve is worse than a rejection: refuse at emission."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "demo", proposals=[{"kind_suffix": "draft"}])
        r = await client.post(
            "/api/inbox/proposals",
            json={"kind_suffix": "draft", "title": "t", "apply": {}},
            headers={"X-Test-App": "demo"},
        )
        assert r.status == 400
        assert "exactly one apply case" in (await r.json())["error"]


@pytest.mark.asyncio
async def test_apply_endpoint_reports_a_failure_as_ok_false_and_keeps_the_row(
    tmp_path, monkeypatch
):
    """The HTTP shape of the stays-PENDING rule: 200 with ``ok:false`` and the item still
    PENDING, because a status code alone cannot say "nothing happened, it's still here"."""
    from personalclaw.inbox import InboxStore, ItemStatus

    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "demo", proposals=[{"kind_suffix": "draft"}])
        r = await client.post(
            "/api/inbox/proposals",
            json={
                "kind_suffix": "draft",
                "title": "Run it",
                "apply": {"workflow": {"ref": "no-such-workflow"}},
            },
            headers={"X-Test-App": "demo"},
        )
        item_id = (await r.json())["id"]
        r2 = await client.post(f"/api/inbox/{item_id}/apply", json={})
        assert r2.status == 200
        body = await r2.json()
        assert body["ok"] is False and body["error"]
        assert body["item"]["status"] == ItemStatus.PENDING.value

        store = InboxStore()
        store.load()
        assert store.items[item_id].status == ItemStatus.PENDING.value
        assert store.items[item_id].refs[pc.ERROR_KEY]["ok"] is False


@pytest.mark.asyncio
async def test_apply_endpoint_404s_an_unknown_item(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        r = await client.post("/api/inbox/nope/apply", json={})
        assert r.status == 404
