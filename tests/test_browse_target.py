"""BA-7 — the `user_browser` execution-target selector on the browse action config.

Three clauses, and the failure mode of each is what shapes the test:

1. **`target` defaults to `gateway`, byte-identically.** Proven by comparing what the gateway
   path OBSERVABLY produces — the CDP endpoint that reaches the connect, and every field of the
   `ActionResult` — across `target` absent, `target` empty and `target: "gateway"`, against a
   table of raw `cdp_url` values including the whitespace and non-string shapes the old
   `str(... or "").strip()` read had to tolerate.
2. **An unconnected `user_browser` task SKIPS and never falls back.** Asserted in both
   directions: the refusal happens (`outcome="skip"` + a typed code), AND the gateway endpoint
   on the very same config is never reached — with a CONNECTED task as the vacuity leg, which
   runs the same code path and does not skip.
3. **Never unattended.** Refused at REGISTRATION (`triggers.tools.create`/`update`, with the
   store left empty as the proof nothing saved) and again at the provider's call site, where it
   outranks a connected connector. Each has an attended/gateway vacuity leg, and a rail on the
   rung ladder stops a later session promoting `browse` to an unattended rung underneath it.

No browser is launched here: every leg stubs the CDP transport, so what is proven is the
provider's DECISION about which endpoint to drive, never a real page. The behavioural CDP legs
live in `test_browse_cdp_live.py` and need a browser the pytest gate does not install.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.browse import target as bt
from personalclaw.config.loader import AppConfig, BrowseConfig

GATEWAY_URL = "ws://127.0.0.1:9222/devtools/page/GATEWAYPROFILE"
USER_URL = "ws://127.0.0.1:9333/devtools/page/MYOWNBROWSER"
START_URL = "https://example.test/start"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_connector():
    """Every test starts detached, and leaves the process detached.

    A process-global attachment that leaked would make the next test's "unconnected" leg pass
    for the wrong reason — the exact false green this file exists to prevent.
    """
    bt.clear_connector()
    yield
    bt.clear_connector()


@pytest.fixture
def switch(monkeypatch):
    """Set `browse.user_browser_enabled` without touching any home.

    Patches `AppConfig.load` rather than writing a config file: the real body of
    `user_browser_enabled()` still runs (attribute read + bool + the fail-closed except), and
    nothing under `~/.personalclaw` is read or written by any leg in this file. The
    `load()`/`to_dict()` round trip for the same field is covered generically by
    `test_config_roundtrip.py::test_every_leaf_field_survives_save_load`.
    """

    def _set(enabled: bool) -> None:
        cfg = AppConfig(browse=BrowseConfig(user_browser_enabled=enabled))
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))

    return _set


class _Probe:
    """Records the endpoint the provider actually connected to, and stubs the run."""

    def __init__(self) -> None:
        self.connected: list[str] = []

    def install(self, monkeypatch, *, ok: bool = True):
        import personalclaw.action_providers.browse_provider as bp
        from personalclaw.browse.loop import BrowseLoopResult

        async def _open(_self, _cfg, _ctx, *, cdp_url: str):
            self.connected.append(cdp_url)
            if not cdp_url:
                raise bp.BrowseUnavailable(
                    "no `cdp_url` is configured, so there is no browser to drive"
                )
            return object(), object(), None

        monkeypatch.setattr(bp.BrowseActionProvider, "_open", _open)
        monkeypatch.setattr(
            bp,
            "run_browse_loop",
            lambda **kw: _done(BrowseLoopResult(goal=kw["goal"], ok=ok, final_url=kw["start_url"])),
        )
        return bp


def _done(value):
    async def _coro():
        return value

    return _coro()


def _execute(cfg: dict):
    import personalclaw.action_providers.browse_provider as bp

    return _run(bp.BrowseActionProvider().execute(cfg, ActionContext(event="manual")))


def _observable(result) -> dict:
    """Every field of an `ActionResult` a caller can branch on, minus the wall clock."""
    return {
        "success": result.success,
        "outcome": result.outcome,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error": result.error,
        "reversal": result.reversal,
        "agent_error": result.agent_error.to_dict() if result.agent_error else None,
    }


# ── clause 1: the default is `gateway`, and it behaves identically ────────────


class TestTheDefaultTargetIsGateway:
    def test_an_absent_target_resolves_to_gateway(self):
        assert bt.resolve_target({}) == bt.TARGET_GATEWAY
        assert bt.resolve_target(None) == bt.TARGET_GATEWAY
        assert bt.resolve_target({"target": ""}) == bt.TARGET_GATEWAY
        assert bt.resolve_target({"target": "  "}) == bt.TARGET_GATEWAY
        assert bt.DEFAULT_TARGET == bt.TARGET_GATEWAY

    @pytest.mark.parametrize(
        "raw",
        [
            GATEWAY_URL,
            f"  {GATEWAY_URL}  ",
            "",
            "   ",
            None,
            0,
            False,
        ],
    )
    def test_the_gateway_endpoint_is_the_same_string_the_provider_read_before(self, raw):
        """The pre-BA-7 provider computed `str(action_config.get("cdp_url") or "").strip()`
        inline in `_open`. `resolve_cdp_url` is now the only reader, so the two must agree on
        every shape that expression tolerated — including the falsy non-strings a config file
        can legally hold."""
        cfg = {"cdp_url": raw}
        assert bt.resolve_cdp_url(bt.TARGET_GATEWAY, cfg) == str(cfg.get("cdp_url") or "").strip()

    def test_absent_empty_and_explicit_gateway_produce_an_IDENTICAL_result(self, monkeypatch):
        """Three spellings of the default, one observable outcome.

        Compares the connected endpoint AND every branchable `ActionResult` field. A `target`
        key that changed any of them would be a behaviour change wearing a default's clothes.
        """
        probe = _Probe()
        probe.install(monkeypatch)
        base = {"goal": "read the page", "start_url": START_URL, "cdp_url": GATEWAY_URL}

        seen = []
        for extra in ({}, {"target": ""}, {"target": "gateway"}):
            probe.connected.clear()
            result = _execute({**base, **extra})
            seen.append((list(probe.connected), _observable(result)))

        assert seen[0] == seen[1] == seen[2]
        assert seen[0][0] == [GATEWAY_URL]
        assert seen[0][1]["success"] is True
        assert seen[0][1]["outcome"] == ""

    def test_a_gateway_task_with_no_cdp_url_still_returns_the_same_typed_refusal(self, monkeypatch):
        """The shipped `ERR_BROWSE_NO_TARGET` sentence is unchanged: the resolution moved, the
        refusal did not."""
        probe = _Probe()
        probe.install(monkeypatch)
        result = _execute({"goal": "read the page", "start_url": START_URL})
        assert result.success is False
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_NO_TARGET"
        assert probe.connected == [""]

    def test_an_unknown_target_is_refused_and_NOT_read_as_the_default(self, monkeypatch):
        probe = _Probe()
        probe.install(monkeypatch)
        result = _execute(
            {
                "goal": "read the page",
                "start_url": START_URL,
                "cdp_url": GATEWAY_URL,
                "target": "user_browsr",
            }
        )
        assert result.success is False
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_TARGET_UNKNOWN"
        # The whole point: a typo did NOT silently run on the gateway profile.
        assert probe.connected == []
        with pytest.raises(bt.UnknownBrowseTarget):
            bt.resolve_target({"target": "USER_BROWSER"})


# ── clause 2: no silent fallback ──────────────────────────────────────────────


class TestAnUnconnectedUserBrowserTaskSkipsAndNeverFallsBack:
    def test_the_switch_being_off_skips_with_a_typed_actionable_reason(self, monkeypatch, switch):
        switch(False)
        probe = _Probe()
        probe.install(monkeypatch)
        result = _execute(
            {
                "goal": "reply to the message",
                "start_url": START_URL,
                "cdp_url": GATEWAY_URL,
                "target": "user_browser",
            }
        )
        assert result.outcome == "skip"
        assert result.success is True  # a skip is not a failure; the engine maps it to NO_CHANGE
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_USER_BROWSER_DISCONNECTED"
        assert result.agent_error.fix  # actionable, not just typed
        assert "switched off" in result.agent_error.what

    def test_the_switch_on_but_nothing_attached_skips_with_the_OTHER_reason(
        self, monkeypatch, switch
    ):
        """Two distinct "no"s, because they have two distinct remedies."""
        switch(True)
        probe = _Probe()
        probe.install(monkeypatch)
        result = _execute({"goal": "reply", "start_url": START_URL, "target": "user_browser"})
        assert result.outcome == "skip"
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_USER_BROWSER_DISCONNECTED"
        assert "no browser is connected" in result.agent_error.what
        assert probe.connected == []

    def test_it_NEVER_reaches_the_gateway_endpoint_on_the_same_config(self, monkeypatch, switch):
        """The security-relevant half. The config carries a perfectly usable gateway endpoint;
        the task asked for the operator's browser; the gateway profile is a different cookie and
        credential context, so it must not be touched."""
        switch(True)
        probe = _Probe()
        probe.install(monkeypatch)
        result = _execute(
            {
                "goal": "reply",
                "start_url": START_URL,
                "cdp_url": GATEWAY_URL,
                "target": "user_browser",
            }
        )
        assert probe.connected == []
        assert GATEWAY_URL not in (result.stdout + result.stderr + (result.error or ""))
        # Structural, not merely observed: the resolver cannot read the config key on this branch.
        assert bt.resolve_cdp_url(bt.TARGET_USER_BROWSER, {"cdp_url": GATEWAY_URL}) == ""

    def test_VACUITY_a_CONNECTED_user_browser_task_runs_the_same_path_and_does_not_skip(
        self, monkeypatch, switch
    ):
        """The leg that proves the two assertions above are not vacuous: the same provider, the
        same config shape, the same `_open` seam — and it neither skips nor uses GATEWAY_URL."""
        switch(True)
        bt.register_connector(device_id="my-mac", cdp_url=USER_URL)
        probe = _Probe()
        probe.install(monkeypatch)
        result = _execute(
            {
                "goal": "reply",
                "start_url": START_URL,
                "cdp_url": GATEWAY_URL,
                "target": "user_browser",
            }
        )
        assert result.outcome != "skip"
        assert result.success is True
        assert probe.connected == [USER_URL]
        assert GATEWAY_URL not in probe.connected

    def test_the_connector_status_names_the_attached_device(self, switch):
        switch(True)
        assert bt.connector_status().connected is False
        bt.register_connector(device_id="my-mac", cdp_url=USER_URL)
        status = bt.connector_status()
        assert (status.connected, status.device_id, status.cdp_url) == (True, "my-mac", USER_URL)
        bt.clear_connector()
        assert bt.connector_status().connected is False

    def test_the_switch_fails_CLOSED_when_config_is_unreadable(self, monkeypatch):
        def _boom(cls):
            raise RuntimeError("config.json is not JSON")

        monkeypatch.setattr(AppConfig, "load", classmethod(_boom))
        assert bt.user_browser_enabled() is False
        assert bt.connector_status().connected is False


# ── clause 3: never unattended ────────────────────────────────────────────────


class TestTheUserBrowserTargetCanNeverRunUnattended:
    def test_permits_unattended_is_a_closed_allowlist(self):
        assert bt.permits_unattended(bt.TARGET_GATEWAY) is True
        assert bt.permits_unattended(bt.TARGET_USER_BROWSER) is False
        # A future third target must opt in, not inherit permission from a negated comparison.
        assert bt.permits_unattended("some_future_target") is False

    def test_the_rung_ladder_can_never_promote_browse_to_an_unattended_rung(self):
        """Consumes AUTONOMY-GUARDRAILS' ladder rather than inventing a second floor: if a later
        session raises `action.browse`'s ceiling to `auto_with_undo` or `autonomous`, this reds —
        which is the only way this floor could be undermined from the outside."""
        from personalclaw.guardrails.autonomy import (
            RUNG_ONE_TAP,
            action_type_for_provider,
            rung_rank,
        )
        from personalclaw.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        spec = action_type_for_provider("browse")
        assert spec is not None, "the browse provider must be governed by the ladder"
        assert rung_rank(spec.ceiling) <= rung_rank(RUNG_ONE_TAP)
        assert rung_rank(spec.floor) <= rung_rank(RUNG_ONE_TAP)

    def test_the_provider_refuses_under_the_background_writing_surface(self, monkeypatch, switch):
        """The call-site floor. `gateway._background_write_surface` wraps EVERY store-trigger
        dispatch in this surface, so this is the posture a cron fire actually presents."""
        from personalclaw.durability.state_history import SURFACE_BACKGROUND, writing_surface

        switch(True)
        bt.register_connector(device_id="my-mac", cdp_url=USER_URL)
        probe = _Probe()
        probe.install(monkeypatch)
        with writing_surface(SURFACE_BACKGROUND):
            result = _execute({"goal": "reply", "start_url": START_URL, "target": "user_browser"})
        assert result.success is False
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_TARGET_UNATTENDED"
        # It outranks a CONNECTED connector: attachment is not a substitute for a person.
        assert probe.connected == []

    def test_VACUITY_the_same_call_is_permitted_attended_and_for_the_gateway_target(
        self, monkeypatch, switch
    ):
        from personalclaw.durability.state_history import SURFACE_BACKGROUND, writing_surface

        switch(True)
        bt.register_connector(device_id="my-mac", cdp_url=USER_URL)
        probe = _Probe()
        probe.install(monkeypatch)
        # (a) attended + user_browser: runs.
        attended = _execute({"goal": "reply", "start_url": START_URL, "target": "user_browser"})
        assert attended.success is True
        assert probe.connected == [USER_URL]
        # (b) unattended + gateway: still runs — the floor is per-TARGET, not a browse-wide ban.
        probe.connected.clear()
        with writing_surface(SURFACE_BACKGROUND):
            unattended = _execute({"goal": "read", "start_url": START_URL, "cdp_url": GATEWAY_URL})
        assert unattended.success is True
        assert probe.connected == [GATEWAY_URL]

    def test_an_unreadable_surface_is_not_taken_as_evidence_of_a_human(self, monkeypatch, switch):
        import personalclaw.durability.state_history as sh

        def _boom() -> str:
            raise RuntimeError("contextvar gone")

        monkeypatch.setattr(sh, "current_surface", _boom)
        assert bt.unattended_origin() != ""


# ── clause 3, registration half ───────────────────────────────────────────────


def _store(tmp_path):
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    # The redirect, asserted rather than assumed: nothing in this file may reach the real home.
    assert str(store.path).startswith(str(tmp_path))
    return store


def _browse_workflow(target: str | None) -> dict:
    config: dict = {"goal": "check the order", "start_url": START_URL}
    if target is not None:
        config["target"] = target
    return {"inline": {"provider": "browse", "config": config}}


class TestASchedulePlanNamingUserBrowserIsRefusedAtRegistration:
    def test_create_refuses_and_saves_NOTHING(self, tmp_path):
        from personalclaw.triggers import tools

        store = _store(tmp_path)
        result = tools.create(
            store,
            name="Nightly order check",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow=_browse_workflow("user_browser"),
            created_by="user",
        )
        assert result.ok is False
        assert result.data["error"]["code"] == "ERR_BROWSE_TARGET_UNATTENDED"
        assert result.data["error"]["fix"]
        # Refused at REGISTRATION means the row does not exist — not that it exists and fails.
        assert store.list_triggers() == []

    def test_VACUITY_the_same_cron_saves_with_the_default_target(self, tmp_path):
        from personalclaw.triggers import tools

        store = _store(tmp_path)
        for target in (None, "gateway"):
            result = tools.create(
                store,
                name=f"Nightly check {target}",
                kind="clock",
                spec={"kind": "cron", "expr": "0 9 * * *"},
                workflow=_browse_workflow(target),
                created_by="user",
            )
            assert result.ok is True, result.text
        assert len(store.list_triggers()) == 2

    def test_update_cannot_walk_around_the_create_check(self, tmp_path):
        from personalclaw.triggers import tools

        store = _store(tmp_path)
        created = tools.create(
            store,
            name="Nightly order check",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow=_browse_workflow("gateway"),
            created_by="user",
        )
        assert created.ok is True
        trigger_id = created.data["trigger"]["id"]

        refused = tools.update(
            store, trigger_id=trigger_id, patch={"workflow": _browse_workflow("user_browser")}
        )
        assert refused.ok is False
        assert refused.data["error"]["code"] == "ERR_BROWSE_TARGET_UNATTENDED"
        # And the stored row is untouched, not half-patched.
        row = store.get(trigger_id)
        assert row is not None
        assert row.trigger.workflow["inline"]["config"]["target"] == "gateway"

    def test_an_unknown_target_is_refused_at_registration_too(self, tmp_path):
        from personalclaw.triggers import tools

        store = _store(tmp_path)
        result = tools.create(
            store,
            name="Typo",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow=_browse_workflow("userbrowser"),
            created_by="user",
        )
        assert result.ok is False
        assert result.data["error"]["code"] == "ERR_BROWSE_TARGET_UNKNOWN"
        assert store.list_triggers() == []

    def test_a_NON_browse_automation_is_untouched_by_the_check(self, tmp_path):
        """The refusal is one provider-name comparison on the normal path, and it must not
        acquire an opinion about anything else."""
        from personalclaw.triggers import tools

        store = _store(tmp_path)
        assert tools.unattended_action_refusal({"provider": "notify", "config": {}}) is None
        assert tools.unattended_action_refusal({}) is None
        assert tools.unattended_action_refusal(None) is None
        # The literal that keeps a ~1s import chain (`browse.extraction` →
        # `knowledge.connectors.web_url`) off every trigger create/update cannot drift from the
        # provider it names.
        from personalclaw.action_providers.browse_provider import PROVIDER_NAME

        assert tools._BROWSE_PROVIDER == PROVIDER_NAME
        result = tools.create(
            store,
            name="Morning ping",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            message="say good morning",
            created_by="user",
        )
        assert result.ok is True


# ── the config round trip's uncovered point ───────────────────────────────────


class TestTheConnectorToggleHasAWritePath:
    def test_it_is_in_the_PATCH_allowlist(self):
        """`test_config_roundtrip.py` covers dataclass/_meta, `load()` and `to_dict()`, but
        provably NOT the `_EDITABLE_CONFIG` allowlist — a field missing from it leaves that file
        fully green while the Settings control silently 400s."""
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        assert _EDITABLE_CONFIG["browse.user_browser_enabled"] == {"type": "bool"}

    def test_the_allowlisted_value_coerces_the_way_the_toggle_sends_it(self):
        from personalclaw.config.edit_spec import coerce_edit_value
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        spec = _EDITABLE_CONFIG["browse.user_browser_enabled"]
        assert coerce_edit_value("browse.user_browser_enabled", True, spec) is True
        assert coerce_edit_value("browse.user_browser_enabled", False, spec) is False

    def test_the_field_defaults_off(self):
        assert AppConfig().browse.user_browser_enabled is False
        assert AppConfig().to_dict()["browse"] == {"user_browser_enabled": False}

    def test_every_new_code_is_in_the_append_only_registry(self):
        from personalclaw.errors import ERROR_CODES

        for code in (
            "ERR_BROWSE_TARGET_UNKNOWN",
            "ERR_BROWSE_TARGET_UNATTENDED",
            "ERR_BROWSE_USER_BROWSER_DISCONNECTED",
        ):
            assert code in ERROR_CODES, code
