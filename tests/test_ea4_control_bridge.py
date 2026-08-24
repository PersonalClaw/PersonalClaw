"""EXTERNAL-ACCESS §4 (atom ``EA-4``) — the self-describing loopback control bridge.

What is worth testing here is not "does the happy path work" but the four properties the
surface exists to guarantee, each of which fails silently if it regresses:

* the bridge is **loopback forever** and does not consult ``allow_remote``;
* the discovery file carries a token **ref**, never the token, and is 0600;
* ``requiresConfirmation`` is enforced **server-side** — a flagged action does not mutate
  on first call, no matter what the client sends;
* a confirm token is **single-use** and expires.

Nothing here starts a real listener except the one test that must, and that one binds
127.0.0.1 port 0 and tears the runner down in a finally.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from personalclaw.inbound import bridge


class _State:
    """The two DashboardState methods the bridge actually calls."""

    def __init__(self) -> None:
        self.broadcasts: list[tuple] = []
        self.notices: list[tuple] = []

    def broadcast_ws(self, kind, payload):
        self.broadcasts.append((kind, payload))

    def notify(self, kind, title, body, *, meta=None):
        self.notices.append((kind, title, body, meta or {}))


def _request(state, *, headers=None, body=None, peer="127.0.0.1"):
    async def _json_body():
        if body is None:
            raise ValueError("no body")
        return body

    return SimpleNamespace(
        app={"state": state},
        headers=headers or {},
        json=_json_body,
        transport=SimpleNamespace(get_extra_info=lambda _k: (peer, 0)),
        remote=peer,
    )


def _payload(resp):
    return json.loads(resp.body.decode())


# ── the self-describing catalogue ────────────────────────────────────────────


class TestTheCatalogueDescribesItself:
    def test_every_declared_action_is_present(self):
        """The v1 registry §4 names, exactly. A missing one is a client that cannot do
        what the plan says it can; an extra one is an unreviewed capability."""
        assert [a.name for a in bridge.actions()] == [
            "open_cockpit",
            "read_transcript",
            "list_automations",
            "run_trigger_dry",
            "notify",
            "create_task",
            "toggle_automation",
        ]

    def test_each_descriptor_carries_the_four_declared_fields(self):
        for d in bridge.descriptor():
            assert set(d) == {
                "name",
                "params_schema",
                "sideEffect",
                "requiresConfirmation",
                "description",
            }, d
            assert d["description"].strip(), f"{d['name']} has no description"

    def test_the_handler_is_not_part_of_the_wire_contract(self):
        """A client that could see the handler would start depending on its shape."""
        assert all("handler" not in d for d in bridge.descriptor())

    def test_no_action_is_destructive_and_the_vocabulary_is_closed(self):
        """`sideEffect: destructive` has no members in v1 BY CONSTRUCTION — delete and
        uninstall are absent rather than confirm-gated. This is the rail that makes
        adding one a decision instead of a typo."""
        assert "destructive" not in bridge.SIDE_EFFECTS
        assert all(a.side_effect in bridge.SIDE_EFFECTS for a in bridge.actions())
        assert all(a.side_effect != "destructive" for a in bridge.actions())

    def test_exactly_the_two_write_mutations_are_confirm_gated(self):
        """`notify` writes too, but gating it would be circular: the confirmation
        arrives AS a notification."""
        gated = {a.name for a in bridge.actions() if a.requires_confirmation}
        assert gated == {"create_task", "toggle_automation"}
        writes = {a.name for a in bridge.actions() if a.side_effect == "write"}
        assert writes == {"create_task", "toggle_automation", "notify"}

    def test_the_digest_tracks_the_set_not_the_call(self):
        """Stable across calls (a client caches it) and sensitive to the registry."""
        assert bridge.actions_digest() == bridge.actions_digest()
        assert len(bridge.actions_digest()) == 16

    def test_the_digest_changes_when_an_action_is_added(self, monkeypatch):
        """Vacuity floor: a digest that ignored the registry would satisfy the test
        above while telling every client the catalogue never changes."""
        before = bridge.actions_digest()
        extra = bridge.Action(
            name="zz_probe",
            params_schema={"type": "object"},
            side_effect="read",
            requires_confirmation=False,
            description="probe",
            handler=bridge._list_automations,
        )
        monkeypatch.setattr(bridge, "_REGISTRY", bridge.actions() + (extra,))
        assert bridge.actions_digest() != before


# ── admission: three gates, and the bridge's own exception ───────────────────


class TestAdmission:
    @pytest.mark.asyncio
    async def test_a_disabled_surface_404s_without_reading_the_body(self, monkeypatch):
        """404 not 403: an off surface must not confirm its own existence to a prober.
        The plan's wording, enforced by `admission_problem`."""
        monkeypatch.setattr(bridge, "admission_problem", lambda _s: ("disabled: off", 404))
        resp = await bridge.handle_actions(_request(_State()))
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_a_remote_peer_is_refused_even_when_the_token_is_right(self, monkeypatch):
        """The bridge is loopback FOREVER. `peer_allowed` special-cases the surface, so
        this asserts the bridge consults it rather than re-deciding locally."""
        monkeypatch.setattr(bridge, "admission_problem", lambda _s: (None, 200))
        monkeypatch.setattr(bridge, "verify_bearer", lambda _s, _t: True)
        monkeypatch.setattr(bridge, "peer_allowed", lambda _r, _s: (False, "loopback only"))
        resp = await bridge.handle_actions(
            _request(_State(), headers={"Authorization": "Bearer x"})
        )
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_a_loopback_peer_without_a_token_is_still_refused(self, monkeypatch):
        """Loopback is not proof of anything — port forwarders make remote traffic
        arrive as 127.0.0.1, which is why `auth` says so in its own docstring."""
        monkeypatch.setattr(bridge, "admission_problem", lambda _s: (None, 200))
        monkeypatch.setattr(bridge, "peer_allowed", lambda _r, _s: (True, ""))
        monkeypatch.setattr(bridge, "verify_bearer", lambda _s, _t: False)
        resp = await bridge.handle_actions(_request(_State()))
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_a_fully_admitted_caller_gets_the_catalogue(self, monkeypatch):
        monkeypatch.setattr(bridge, "admission_problem", lambda _s: (None, 200))
        monkeypatch.setattr(bridge, "peer_allowed", lambda _r, _s: (True, ""))
        monkeypatch.setattr(bridge, "verify_bearer", lambda _s, _t: True)
        resp = await bridge.handle_actions(
            _request(_State(), headers={"Authorization": "Bearer x"})
        )
        body = _payload(resp)
        assert resp.status == 200
        assert body["schema_version"] == bridge.SCHEMA_VERSION
        assert body["actions_digest"] == bridge.actions_digest()
        assert len(body["actions"]) == len(bridge.actions())


# ── requiresConfirmation, enforced here ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _never_touch_the_real_inbox(monkeypatch, tmp_path):
    """`emit_attention_item` builds an ``InboxStore()`` from ``config_dir()``. One
    forgotten patch would file control-bridge rows in the operator's real home, so the
    home is redirected for EVERY test here rather than per test."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    yield


@pytest.fixture
def admitted(monkeypatch):
    monkeypatch.setattr(bridge, "admission_problem", lambda _s: (None, 200))
    monkeypatch.setattr(bridge, "peer_allowed", lambda _r, _s: (True, ""))
    monkeypatch.setattr(bridge, "verify_bearer", lambda _s, _t: True)
    bridge._pending.clear()
    yield
    bridge._pending.clear()


class TestConfirmationIsServerSide:
    @pytest.mark.asyncio
    async def test_a_flagged_action_does_not_run_on_first_call(self, admitted, monkeypatch):
        """The whole point. A client that ignores the flag gets a token, not a write —
        so the handler must not have been reached."""
        ran: list[str] = []

        async def _never(state, params):
            ran.append("mutated")
            return {}

        monkeypatch.setattr(
            bridge,
            "_REGISTRY",
            tuple(
                bridge.Action(**{**a.__dict__, "handler": _never}) if a.name == "create_task" else a
                for a in bridge.actions()
            ),
        )
        state = _State()
        resp = await bridge.handle_action(
            _request(
                state,
                headers={"Authorization": "Bearer x"},
                body={"action": "create_task", "params": {"title": "t"}},
            )
        )
        body = _payload(resp)
        assert resp.status == 202
        assert body["status"] == "needs_confirmation"
        assert body["confirm_token"]
        assert ran == [], "a confirm-gated action mutated before the user confirmed"

    @pytest.mark.asyncio
    async def test_the_user_is_told_and_the_notice_carries_the_token(self, admitted, monkeypatch):
        """Raised through `emit_attention_item` — `inbox.py` calls that "the only correct
        way to raise a durable agent request", because a caller doing `store.add` plus
        `state.notify` separately drifts into two notifications for one event or a row
        nobody was told about. `notify("needs_input", …)` is NOT the mechanism: that kind
        has no `_LEGACY_FLAT` history and the notification-kind ratchet refuses it.

        The emitter is intercepted rather than exercised: the real one constructs an
        ``InboxStore()`` against ``config_dir()``, so letting it run would file a row in
        the OPERATOR's real home.
        """
        raised: list[dict] = []

        def _fake_emit(state, **kw):
            raised.append(kw)
            return "item-1"

        monkeypatch.setattr("personalclaw.inbox.emit_attention_item", _fake_emit)
        state = _State()
        resp = await bridge.handle_action(
            _request(
                state,
                headers={"Authorization": "Bearer x"},
                body={"action": "toggle_automation", "params": {"id": "t1"}},
            )
        )
        token = _payload(resp)["confirm_token"]
        assert raised, "no needs-input attention item was raised"
        kw = raised[-1]
        assert kw["kind"] == "needs_input"
        assert kw["refs"]["confirm_token"] == token
        assert kw["refs"]["action"] == "toggle_automation"
        # Idempotent per token: a client that retries must not stack inbox rows.
        assert kw["dedup_key"] == f"control_bridge:{token}"

    @pytest.mark.asyncio
    async def test_confirming_runs_it_exactly_once(self, admitted, monkeypatch):
        calls: list[dict] = []

        async def _record(state, params):
            calls.append(params)
            return {"ok": True}

        monkeypatch.setattr(
            bridge,
            "_REGISTRY",
            tuple(
                (
                    bridge.Action(**{**a.__dict__, "handler": _record})
                    if a.name == "create_task"
                    else a
                )
                for a in bridge.actions()
            ),
        )
        state = _State()
        first = await bridge.handle_action(
            _request(
                state,
                headers={"Authorization": "Bearer x"},
                body={"action": "create_task", "params": {"title": "write me"}},
            )
        )
        token = _payload(first)["confirm_token"]
        ok = await bridge.handle_confirm(
            _request(state, headers={"Authorization": "Bearer x"}, body={"confirm_token": token})
        )
        assert ok.status == 200 and _payload(ok)["status"] == "ok"
        assert calls == [{"title": "write me"}]

        # Single-use: replaying the token must not mutate again.
        replay = await bridge.handle_confirm(
            _request(state, headers={"Authorization": "Bearer x"}, body={"confirm_token": token})
        )
        assert replay.status == 404
        assert len(calls) == 1, "a confirm token was redeemable twice"

    @pytest.mark.asyncio
    async def test_an_unknown_token_is_refused(self, admitted):
        resp = await bridge.handle_confirm(
            _request(
                _State(),
                headers={"Authorization": "Bearer x"},
                body={"confirm_token": "not-a-real-token"},
            )
        )
        assert resp.status == 404

    def test_a_token_expires(self, monkeypatch):
        """An abandoned intent must not be redeemable hours later by whatever still
        holds the token."""
        bridge._pending.clear()
        action = next(a for a in bridge.actions() if a.requires_confirmation)
        token = bridge._mint_confirmation(action, {"title": "x"})
        assert bridge.pending_count() == 1
        # Compute the target BEFORE patching: a lambda that reads `_pending[token]`
        # lazily raises KeyError the second time `_reap` calls it, because the first
        # call is what popped the entry.
        expired_at = bridge._pending[token]["created"] + bridge.CONFIRM_TTL_SECS + 1
        monkeypatch.setattr(time, "monotonic", lambda: expired_at)
        assert bridge.take_confirmation(token) is None
        assert bridge.pending_count() == 0

    @pytest.mark.asyncio
    async def test_an_unflagged_action_runs_straight_through(self, admitted):
        """Vacuity floor for the gating tests: if EVERY action returned
        needs_confirmation they would all pass while the bridge did nothing."""
        state = _State()
        resp = await bridge.handle_action(
            _request(
                state,
                headers={"Authorization": "Bearer x"},
                body={"action": "open_cockpit", "params": {"kind": "loops", "id": "L1"}},
            )
        )
        assert resp.status == 200
        assert _payload(resp)["result"]["route"] == "#/loops/L1"
        assert state.broadcasts and state.broadcasts[-1][0] == "navigate"

    @pytest.mark.asyncio
    async def test_an_unknown_action_is_404_not_a_500(self, admitted):
        resp = await bridge.handle_action(
            _request(_State(), headers={"Authorization": "Bearer x"}, body={"action": "rm_rf"})
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_a_handler_validation_error_is_400_and_names_the_field(self, admitted):
        resp = await bridge.handle_action(
            _request(
                _State(),
                headers={"Authorization": "Bearer x"},
                body={"action": "open_cockpit", "params": {}},
            )
        )
        assert resp.status == 400
        assert "kind" in _payload(resp)["error"]


# ── the discovery file ───────────────────────────────────────────────────────


class TestDiscoveryFile:
    def test_it_names_the_token_and_never_carries_it(self, tmp_path, monkeypatch):
        """A file that carried the secret would make "readable file" and
        "authenticated" the same thing."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        # A REAL configured token, so "the secret is absent" is a claim about the writer
        # rather than about a local string the writer never saw. The first draft of this
        # test asserted `"a"*64 not in raw` with nothing wiring that value in — a dead
        # assertion that a deliberate leak still slipped past (the sibling key check
        # caught it instead).
        secret = "s3cr3t-" + "b" * 57
        monkeypatch.setattr("personalclaw.inbound.auth.load_surface_token", lambda _s: secret)
        bridge._write_discovery(51234)
        raw = (tmp_path / bridge.DISCOVERY_FILENAME).read_text()
        info = json.loads(raw)
        assert info["port"] == 51234
        assert info["url"] == "http://127.0.0.1:51234"
        assert info["token_ref"] == "PERSONALCLAW_INBOUND_BRIDGE_TOKEN"
        assert info["schema_version"] == bridge.SCHEMA_VERSION
        assert info["actions_digest"] == bridge.actions_digest()
        assert secret not in raw, "the discovery file leaked the bearer token"
        assert "token" not in info, "the discovery file must carry a REF, not a token"

    def test_it_is_0600(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        bridge._write_discovery(1234)
        mode = (tmp_path / bridge.DISCOVERY_FILENAME).stat().st_mode & 0o777
        assert mode == 0o600, oct(mode)

    def test_remove_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        bridge._write_discovery(1234)
        bridge.remove_discovery()
        bridge.remove_discovery()  # a crash-recovery boot calls this with no file present
        assert not (tmp_path / bridge.DISCOVERY_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_an_unmounted_bridge_leaves_no_file(self, tmp_path, monkeypatch):
        """An agent that finds no discovery file correctly concludes there is nothing to
        talk to — so a refused mount must not leave a stale one behind."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        bridge._write_discovery(999)  # a file from a PREVIOUS boot
        monkeypatch.setattr(bridge, "enablement_problem", lambda: "disabled: off")
        port = await bridge.start(_State())
        assert port is None
        assert not (tmp_path / bridge.DISCOVERY_FILENAME).exists()


class TestTheRealListener:
    @pytest.mark.asyncio
    async def test_it_binds_loopback_on_an_os_chosen_port_and_publishes_it(
        self, tmp_path, monkeypatch
    ):
        """The one test that starts a real runner. Asserts the port is ephemeral (not a
        fixed one someone could scan for) and that the file matches what bound."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(bridge, "enablement_problem", lambda: None)
        port = await bridge.start(_State())
        try:
            assert port and port > 1024, port
            info = json.loads((tmp_path / bridge.DISCOVERY_FILENAME).read_text())
            assert info["port"] == port
        finally:
            await bridge.stop()
        assert not (tmp_path / bridge.DISCOVERY_FILENAME).exists()


def test_write_actions_call_the_dashboards_own_services_not_a_second_path():
    """§4: "no parallel mutation paths". Pinned at the source level because a second
    implementation would pass every behavioural test in this file while drifting from
    whatever validation the real handler gained."""
    import pathlib

    import personalclaw

    src = (pathlib.Path(personalclaw.__file__).parent / "inbound" / "bridge.py").read_text()
    assert "from personalclaw.tasks import registry" in src
    assert "registry.create_task(" in src
    assert "store.set_enabled(" in src
    # And it must not have grown its own writer.
    assert "atomic_write(" in src  # the discovery file is the ONLY thing it writes itself
    assert src.count("atomic_write(") == 1
