"""EXTERNAL-ACCESS §4 (atom ``EA-4``) — the self-describing loopback control bridge.

What is worth testing here is not "does the happy path work" but the four properties the
surface exists to guarantee, each of which fails silently if it regresses:

* the bridge is **loopback forever** and does not consult ``allow_remote``;
* the discovery file carries a token **ref**, never the token, and is 0600;
* ``requiresConfirmation`` is enforced **server-side** — a flagged action does not mutate
  on first call, no matter what the client sends;
* a confirm token is **single-use** and expires;
* the catalogue is **filtered to the caller's pin** and the digest covers what was
  actually served — a self-describing surface that describes more than the caller may
  invoke is an enumeration of the authority the caller lacks, and the same predicate has
  to gate the catalogue, the invoke and the redemption or the three drift apart.

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
        """The structured envelope: a branchable ``code`` AND the field in the message.

        Both halves matter. The code is what a client branches on; the message is what
        keeps "which field?" answerable without reading our source. Asserting only the
        code would let the message degrade to "bad request" unnoticed.
        """
        resp = await bridge.handle_action(
            _request(
                _State(),
                headers={"Authorization": "Bearer x"},
                body={"action": "open_cockpit", "params": {}},
            )
        )
        assert resp.status == 400
        err = _payload(resp)["error"]
        assert err["code"] == "bad_request"
        assert "kind" in err["message"]


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


# ── the catalogue is filtered to the caller's pin ────────────────────────────
#
# `GET /actions` used to return the FULL seven-action catalogue to every admitted
# caller, with `actions_digest` computed over that same full set — so a client whose
# record permits one action was still shown `toggle_automation`: a catalogue of the lock
# handed to whoever lacks the key. These tests hold the filter, the digest-over-served,
# and the invariant that the description and the enforcement cannot disagree.


@pytest.fixture
def register_client(monkeypatch, tmp_path):
    """Mint a REAL client record in a REAL registry file, under an isolated home.

    Deliberately not a monkeypatched `lookup_by_token`: the claim under test is that
    ``_admit`` resolves a bearer to a client record AT ALL, and a patched resolver would
    satisfy every assertion below whether or not that call site exists.

    ``PERSONALCLAW_HOME`` is set as well as ``config_dir`` patched, because
    `clients.clients_path` prefers the env var — patching only the loader would let a
    surrounding ``PERSONALCLAW_HOME`` write client records into the operator's real home.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.inbound import clients as clients_mod

    def _make(tools, *, surfaces=None):
        return clients_mod.create_client(
            "pinned-agent",
            surfaces=list(surfaces if surfaces is not None else [bridge.BRIDGE_SURFACE]),
            tools=list(tools),
        )

    return _make


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


class TestTheCatalogueIsFilteredToThePin:
    @pytest.mark.asyncio
    async def test_a_pinned_client_is_not_shown_the_actions_it_cannot_call(
        self, admitted, register_client
    ):
        """The negative, which is the whole point: the unpermitted descriptors are ABSENT.

        `toggle_automation` is named explicitly rather than counted — a count assertion
        passes if the filter drops the wrong five.
        """
        _client, token = register_client(["list_automations", "open_cockpit"])
        resp = await bridge.handle_actions(_request(_State(), headers=_bearer(token)))
        assert resp.status == 200
        served = [a["name"] for a in _payload(resp)["actions"]]
        assert sorted(served) == ["list_automations", "open_cockpit"], served
        assert "toggle_automation" not in served
        assert "create_task" not in served
        assert "read_transcript" not in served

    @pytest.mark.asyncio
    async def test_an_unpinned_surface_token_caller_still_sees_all_seven(self, admitted):
        """VACUITY FLOOR for the assertion above.

        A filter that returned nothing — or one applied to every caller — would satisfy
        "the pinned client cannot see toggle_automation" while breaking the surface. The
        surface principal has no client record and therefore no pin.
        """
        resp = await bridge.handle_actions(_request(_State(), headers=_bearer("surface-token")))
        served = [a["name"] for a in _payload(resp)["actions"]]
        assert len(served) == len(bridge.actions()) == 7, served
        assert "toggle_automation" in served

    @pytest.mark.asyncio
    async def test_an_empty_tools_list_means_no_pin_not_no_actions(self, admitted, register_client):
        """The second half of the floor, and the one worth verifying rather than assuming.

        ``tools: []`` is "unpinned", matching `clients.allowed_tools`. It is NOT read the
        way `may_use` reads an empty ``surfaces`` list ("none") — ``surfaces`` GRANTS and
        ``tools`` NARROWS, so an absent narrowing narrows nothing. Reading it the other
        way would silently brick every client registered without a tools binding.
        """
        _client, token = register_client([])
        resp = await bridge.handle_actions(_request(_State(), headers=_bearer(token)))
        served = [a["name"] for a in _payload(resp)["actions"]]
        assert len(served) == 7, served

    @pytest.mark.asyncio
    async def test_the_digest_covers_what_was_served_not_the_registry(
        self, admitted, register_client
    ):
        """A digest over the full set beside a filtered list never matches the payload it
        accompanies, so a pinned client re-caches on EVERY poll — the caching mechanism
        inverted into a per-request cost."""
        _client, token = register_client(["list_automations"])
        resp = await bridge.handle_actions(_request(_State(), headers=_bearer(token)))
        body = _payload(resp)
        assert body["actions_digest"] == bridge.digest_of(body["actions"])
        assert body["actions_digest"] != bridge.actions_digest(), (
            "the served digest equals the FULL-registry digest — either the catalogue was "
            "not filtered or the digest still ignores what was served"
        )

    @pytest.mark.asyncio
    async def test_an_unpinned_callers_digest_is_still_the_registry_digest(self, admitted):
        """Floor for the test above: `digest_of` must not be a function that simply
        disagrees with `actions_digest` for everyone."""
        resp = await bridge.handle_actions(_request(_State(), headers=_bearer("surface-token")))
        assert _payload(resp)["actions_digest"] == bridge.actions_digest()

    @pytest.mark.asyncio
    async def test_a_pinned_client_cannot_invoke_an_action_it_was_not_shown(
        self, admitted, register_client, monkeypatch
    ):
        """The pin is a CONTROL, not a display preference: it denies, and the handler is
        never reached."""
        ran: list[str] = []

        async def _never(state, params):
            ran.append("ran")
            return {}

        monkeypatch.setattr(
            bridge,
            "_REGISTRY",
            tuple(
                bridge.Action(**{**a.__dict__, "handler": _never}) if a.name == "notify" else a
                for a in bridge.actions()
            ),
        )
        _client, token = register_client(["list_automations"])
        resp = await bridge.handle_action(
            _request(
                _State(),
                headers=_bearer(token),
                body={"action": "notify", "params": {"text": "hello"}},
            )
        )
        assert resp.status == 403
        assert _payload(resp)["error"]["code"] == "action_not_bound"
        assert ran == [], "an un-bound action reached its handler"

    @pytest.mark.asyncio
    async def test_the_refusal_does_not_read_the_pin_back_to_the_caller(
        self, admitted, register_client
    ):
        """A 403 that recited the caller's bindings would leak exactly what the catalogue
        filter withholds — the shape of the authority the caller does not have."""
        _client, token = register_client(["list_automations"])
        resp = await bridge.handle_action(
            _request(_State(), headers=_bearer(token), body={"action": "toggle_automation"})
        )
        raw = resp.body.decode()
        assert resp.status == 403
        assert "list_automations" not in raw, raw

    @pytest.mark.asyncio
    async def test_the_pin_is_checked_before_a_confirmation_is_minted(
        self, admitted, register_client, monkeypatch
    ):
        """Order matters. Refusing AFTER the mint would still stop the mutation, but the
        owner would already have been asked to approve it — so an un-bound client would
        own a write channel into the attention surface, which is what the pin denies."""
        raised: list[dict] = []
        monkeypatch.setattr(
            "personalclaw.inbox.emit_attention_item",
            lambda state, **kw: raised.append(kw) or "item-1",
        )
        _client, token = register_client(["list_automations"])
        before = bridge.pending_count()
        resp = await bridge.handle_action(
            _request(
                _State(),
                headers=_bearer(token),
                body={"action": "create_task", "params": {"title": "t"}},
            )
        )
        assert resp.status == 403
        assert bridge.pending_count() == before, "a confirm token was minted for an un-bound action"
        assert raised == [], "the owner was asked to approve an action the client cannot run"

    @pytest.mark.asyncio
    async def test_redemption_re_checks_the_pin(self, admitted, register_client, monkeypatch):
        """Catalogue and redemption are the two places the pin has to hold, and this is
        the one that could drift: a token minted by a WIDER principal must not become a
        way for a narrower one to run an action its own record forbids."""
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
        monkeypatch.setattr("personalclaw.inbox.emit_attention_item", lambda state, **kw: "item-1")
        state = _State()
        # Minted by the un-pinned SURFACE principal.
        minted = await bridge.handle_action(
            _request(
                state,
                headers=_bearer("surface-token"),
                body={"action": "create_task", "params": {"title": "wide"}},
            )
        )
        token_value = _payload(minted)["confirm_token"]
        # Redeemed by a narrower client whose record does not include the action.
        _client, narrow = register_client(["list_automations"])
        resp = await bridge.handle_confirm(
            _request(state, headers=_bearer(narrow), body={"confirm_token": token_value})
        )
        assert resp.status == 403
        assert _payload(resp)["error"]["code"] == "action_not_bound"
        assert calls == [], "a narrower client redeemed a wider principal's confirmation"

    @pytest.mark.asyncio
    async def test_the_catalogue_and_the_invoke_path_cannot_disagree(
        self, admitted, register_client
    ):
        """The drift assertion, stated as an invariant over the WHOLE registry.

        Everything the catalogue advertised is invocable (whatever else it may fail on),
        and everything it withheld is refused. Two separately-written predicates would
        pass the individual tests above and still disagree about the sixth action; one
        `_bound` shared by all three call sites is what makes this hold by construction.
        """
        pinned = ["list_automations", "open_cockpit"]
        _client, token = register_client(pinned)
        served = {
            a["name"]
            for a in _payload(
                await bridge.handle_actions(_request(_State(), headers=_bearer(token)))
            )["actions"]
        }
        assert served, "nothing was served — the invariant below would be vacuous"
        withheld = {a.name for a in bridge.actions()} - served
        assert withheld, "nothing was withheld — the invariant below would be vacuous"
        for name in sorted(served):
            resp = await bridge.handle_action(
                _request(_State(), headers=_bearer(token), body={"action": name})
            )
            assert resp.status != 403, f"{name} was advertised but refused as un-bound"
        for name in sorted(withheld):
            resp = await bridge.handle_action(
                _request(_State(), headers=_bearer(token), body={"action": name})
            )
            assert resp.status == 403, f"{name} was withheld from the catalogue but invocable"
            assert _payload(resp)["error"]["code"] == "action_not_bound"

    @pytest.mark.asyncio
    async def test_a_client_bound_to_another_surface_is_not_resolved_here(
        self, monkeypatch, register_client
    ):
        """A client record's ``surfaces`` list is what admits it. A record bound to `mcp`
        presenting its token to the bridge must not resolve — otherwise "bound to mcp"
        would mean "bound to every dialect", and the un-pinned surface principal is what
        it would be mistaken for."""
        monkeypatch.setattr(bridge, "admission_problem", lambda _s: (None, 200))
        monkeypatch.setattr(bridge, "peer_allowed", lambda _r, _s: (True, ""))
        monkeypatch.setattr(bridge, "verify_bearer", lambda _s, _t: False)
        _client, token = register_client(["list_automations"], surfaces=["mcp"])
        resp = await bridge.handle_actions(_request(_State(), headers=_bearer(token)))
        assert resp.status == 401
        assert _payload(resp)["error"]["code"] == "unauthorized"


def test_every_refusal_goes_through_the_shared_wire_emitter():
    """No flat ``{"error": "<prose>"}`` may return here, however it is routed.

    Pinned at the source level because the wire-envelope census scored this module at
    ZERO on both of its rails while it shipped eleven of them: the payloads reached
    `json_response` through the local ``_json`` wrapper as a variable, and the companion
    rail matched helper NAMES (``_err``/``_error``/``_bad_request``) rather than shape.
    The census now follows wrapper indirection; this is the module-local restatement so
    a regression here names this file rather than a tree-wide count.
    """
    import pathlib

    import personalclaw

    src = (pathlib.Path(personalclaw.__file__).parent / "inbound" / "bridge.py").read_text()
    assert "from personalclaw.http_errors import json_error" in src
    assert '_json({"error"' not in src, "a flat wire envelope came back through the wrapper"
    assert 'json_response({"error"' not in src, "a flat wire envelope came back directly"


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
