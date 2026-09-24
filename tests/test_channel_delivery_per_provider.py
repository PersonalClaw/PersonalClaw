"""Every channel's outbound reply goes to that channel (issue 959).

`channel_delivery` was a single handle held in TWO places — `GatewayOrchestrator._channel_delivery`
and `DashboardState.channel_delivery` — and every shipped transport wrote both at `start_inbound`.
So with Discord, Slack and Telegram connected, the last registration won. Measured on
`origin/main` by executing the real registration path:

    for name in ("discord", "slack", "telegram"):   # apps load alphabetically
        orch.register_channel_delivery(FakeDelivery(name))
    orch._channel_delivery  ->  <telegramDelivery>

A Discord message is then received correctly, the agent runs, and the answer is handed to
`TelegramDelivery` along with a Discord channel id — so the reply is lost, deterministically, for
every provider except the alphabetically-last one.

The root cause is a missing routing key, and it was missing on both sides: the 18-method
`ChannelDelivery` protocol has no provider member, and the session→channel link is
`(thread_ts, channel_id)` with no provider — yet a channel id means nothing without one.
`deliver_text("C123", …)` is answerable only by the provider that issued `C123`. Meanwhile the
INBOUND door already takes the provider explicitly (`deliver_channel_inbound(provider, msg)`),
from the same caller at the same lifecycle point. That asymmetry is the bug.

Two resolution policies, because the call sites ask two different questions — a partition measured
from the 25 sites, not invented:

* `delivery_for(provider)` — a REPLY carries the origin channel's id, so exactly one provider can
  deliver it, and None means DO NOT SEND (`chat_runner`'s three mirror sites);
* `owner_reachable()` — a cron result, heartbeat summary, approval prompt or subagent reply is
  addressed to the OWNER via `open_dm(owner_id)` and has no origin channel (every `gateway.py`
  site).

ARCC was queried for this work (message routing / tenant isolation). It returned SAX-05 AWS
multi-tenant material with no requirement applicable to a local single-user app — noted. Its
principle does name the shape: a shared slot with no keying is an absent isolation boundary, and a
reply reaching the wrong channel is a confidentiality question, not only a delivery one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from personalclaw import channel_delivery as cd


def delivery(provider: str) -> Any:
    """A stand-in shaped like a real one: its class lives in `<provider>_runtime`, which is how
    every shipped channel app is packaged (`discord_runtime.delivery.DiscordDelivery`)."""
    cls = type(f"{provider.title()}Delivery", (), {})
    cls.__module__ = f"{provider}_runtime.delivery"
    return cls()


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-level (like `inbox_providers.native_source`'s state hook), so a
    test that left a handle behind would leak into the next one's `owner_reachable` pick."""
    cd.register(None)
    yield
    cd.register(None)


# ── the defect ───────────────────────────────────────────────────────────────────────────


class TestThreeChannelsCoexist:
    def test_each_provider_keeps_its_own_handle(self) -> None:
        """🔑 The reported bug. Three registrations used to leave exactly one handle."""
        for name in ("discord", "slack", "telegram"):
            cd.register(delivery(name))

        assert cd.registered_providers() == ["discord", "slack", "telegram"]
        for name in ("discord", "slack", "telegram"):
            assert type(cd.delivery_for(name)).__name__ == f"{name.title()}Delivery"

    def test_a_later_registration_does_not_displace_another_provider(self) -> None:
        """The mechanism, stated directly: registration order must not decide who can reply."""
        first = delivery("discord")
        cd.register(first)
        cd.register(delivery("telegram"))
        assert cd.delivery_for("discord") is first

    def test_re_registering_one_provider_replaces_only_that_one(self) -> None:
        """A reconnect is normal — a transport restarting hands over a fresh handle."""
        cd.register(delivery("slack"))
        fresh = delivery("slack")
        keep = delivery("discord")
        cd.register(keep)
        cd.register(fresh)
        assert cd.delivery_for("slack") is fresh
        assert cd.delivery_for("discord") is keep


class TestReplyResolution:
    @pytest.mark.parametrize("unknown", ["whatsapp", ""])
    def test_an_unconnected_provider_resolves_to_None_not_to_someone_else(
        self, unknown: str
    ) -> None:
        """🪤 The assertion that IS the fix. Returning any other provider is not a degraded
        delivery — it is a message posted to the wrong place, which is what #959 measured. The
        empty-provider case matters too: a dashboard-only session has no origin channel, and it
        must not mean "pick one"."""
        cd.register(delivery("telegram"))
        assert cd.delivery_for(unknown) is None

    def test_delivery_for_does_not_fall_back_even_when_exactly_one_is_connected(self) -> None:
        """The tempting shortcut: with a single channel connected, "just use it" is right often
        enough to look correct — and it is exactly how the single slot behaved."""
        cd.register(delivery("telegram"))
        assert cd.registered_providers() == ["telegram"]
        assert cd.delivery_for("discord") is None


class TestOwnerReachable:
    def test_it_returns_a_connected_channel(self) -> None:
        cd.register(delivery("slack"))
        assert type(cd.owner_reachable()).__name__ == "SlackDelivery"

    def test_it_is_None_when_nothing_is_connected(self) -> None:
        assert cd.owner_reachable() is None

    def test_the_pick_is_deterministic_across_registration_orders(self) -> None:
        """🪤 Sorted, not insertion-ordered. An owner notification that lands on a different
        channel depending on which app happened to boot first is the property that made this
        family of bugs hard to see at all."""
        cd.register(delivery("telegram"))
        cd.register(delivery("discord"))
        first = type(cd.owner_reachable()).__name__
        cd.register(None)
        cd.register(delivery("discord"))
        cd.register(delivery("telegram"))
        assert type(cd.owner_reachable()).__name__ == first == "DiscordDelivery"


class TestClearing:
    def test_clearing_one_provider_leaves_the_others(self) -> None:
        cd.register(delivery("discord"))
        cd.register(delivery("slack"))
        cd.register(None, provider="discord")
        assert cd.registered_providers() == ["slack"]

    def test_clearing_with_no_provider_clears_everything(self) -> None:
        """What `register_channel_delivery(None)` always meant: nothing is reachable."""
        cd.register(delivery("discord"))
        cd.register(delivery("slack"))
        cd.register(None)
        assert cd.registered_providers() == []
        assert cd.owner_reachable() is None


class TestProviderDerivation:
    @pytest.mark.parametrize(
        "module,expected",
        [
            ("discord_runtime.delivery", "discord"),
            ("telegram_runtime.transport", "telegram"),
            ("slack_runtime", "slack"),
            ("email_runtime.delivery", "email"),
            ("whatsapp_channel.out", "whatsapp"),
            ("matrix.delivery", "matrix"),
        ],
    )
    def test_a_handle_that_declares_no_provider_still_gets_a_distinct_key(
        self, module: str, expected: str
    ) -> None:
        """The fallback's only job is DISTINCTNESS, so a core upgrade cannot break an app that
        has not been updated yet: three un-updated apps land in three keys instead of one slot.
        It is a namespacing derivation, never a semantic guess."""
        cls = type("D", (), {})
        cls.__module__ = module
        assert cd.provider_of(cls()) == expected

    def test_an_explicit_provider_wins_over_the_derivation(self) -> None:
        """What a transport SHOULD pass — the same string it already gives the inbound door."""
        handle = delivery("discord")
        assert cd.register(handle, "discord-beta") == "discord-beta"
        assert cd.delivery_for("discord-beta") is handle
        assert cd.delivery_for("discord") is None

    def test_a_handle_with_no_usable_module_still_registers(self) -> None:
        """🪤 Never return "" — an empty key would collide with `delivery_for("")`, which is the
        dashboard-session case that must resolve to None."""
        cls = type("OddDelivery", (), {})
        cls.__module__ = ""
        assert cd.provider_of(cls()) == "odddelivery"


# ── the two holders become one ───────────────────────────────────────────────────────────


class TestDashboardStateIsAViewNotASlot:
    def test_assigning_the_attribute_registers_the_handle(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Every shipped transport does `services.dashboard_state.channel_delivery = delivery`
        (alongside its `register_channel_delivery` call — both, not either). That assignment is
        now a registration keyed by the handle's provider, so the apps need no change for this
        fix to hold."""
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        state.channel_delivery = delivery("discord")
        assert cd.registered_providers() == ["discord"]

    def test_the_getter_is_the_owner_reachable_pick(self) -> None:
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        cd.register(delivery("slack"))
        assert type(state.channel_delivery).__name__ == "SlackDelivery"

    def test_assigning_None_clears(self) -> None:
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        cd.register(delivery("slack"))
        state.channel_delivery = None
        assert state.channel_delivery is None
        assert cd.registered_providers() == []

    def test_delivery_for_on_the_state_reaches_the_same_registry(self) -> None:
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        handle = delivery("telegram")
        cd.register(handle)
        assert state.delivery_for("telegram") is handle
        assert state.delivery_for("discord") is None

    def test_the_orchestrator_and_the_state_resolve_ONE_registry(self) -> None:
        """🪤 The invariant the fix rests on. Two holders for one fact is why a single overwrite
        took out delivery on two unrelated paths at once."""
        from personalclaw.dashboard.state import DashboardState
        from personalclaw.gateway import GatewayOrchestrator

        orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
        state = DashboardState.__new__(DashboardState)
        handle = delivery("slack")
        orch.register_channel_delivery(handle, "slack")
        assert state.channel_delivery is handle
        assert orch._channel_delivery is handle


class TestSessionProvider:
    def test_a_channel_session_reports_the_provider_it_came_from(self) -> None:
        """The routing key for a reply, read where it was already stamped: the one inbound door
        creates the session with `app=provider` (`channel_inbound._route_to_session`)."""
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        session = type("S", (), {"_app": "discord"})()
        state._sessions = {"chan-1": session}
        assert state.channel_provider_for("chan-1") == "discord"

    def test_a_dashboard_session_reports_no_provider(self) -> None:
        """Which makes `delivery_for("")` None, so a dashboard turn mirrors nowhere — correct,
        and the reason the empty case is asserted in `TestReplyResolution` too."""
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        state._sessions = {"web-1": type("S", (), {"_app": ""})()}
        assert state.channel_provider_for("web-1") == ""
        assert state.delivery_for(state.channel_provider_for("web-1")) is None

    def test_an_unknown_session_reports_no_provider_rather_than_raising(self) -> None:
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        state._sessions = {}
        assert state.channel_provider_for("gone") == ""


# ── the rails ────────────────────────────────────────────────────────────────────────────

_CORE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _code(path: Path) -> str:
    """Source with comment-only lines stripped — the fix's own comments quote the shape they
    replaced, and a raw-text rail would read that explanation as the defect."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").split("\n")
        if not line.strip().startswith("#")
    )


class TestNoSecondSlotComesBack:
    def test_the_mirror_path_resolves_the_ORIGIN_handle(self) -> None:
        """`chat_runner`'s three mirror sites (user echo, tool stream, response) must all use the
        origin-scoped handle. Reading the owner-reachable attribute there is the bug."""
        code = _code(_CORE / "dashboard" / "chat_runner.py")
        assert (
            "_mirror_delivery = state.delivery_for(state.channel_provider_for(session_key))" in code
        )
        assert "state.channel_delivery" not in code, (
            "a mirror site went back to the owner-reachable handle — a reply carries the origin "
            "channel's id and is answerable by exactly one provider"
        )

    def test_no_module_assigns_the_attribute_as_a_slot(self) -> None:
        """The setter makes an assignment a registration, so an assignment is not itself wrong —
        but core assigning it would mean core is choosing a provider, which only a transport can
        do. Zero core writers was already true before this change; the rail keeps it true."""
        offenders = [
            p.relative_to(_CORE).as_posix()
            for p in _CORE.rglob("*.py")
            if "channel_delivery =" in _code(p) and p.name != "state.py"
        ]
        assert offenders == [], f"core assigned the channel-delivery attribute: {offenders}"

    def test_the_registry_is_the_only_holder(self) -> None:
        """A private slot anywhere else is the shape that drifted. `channel_delivery.py` owns the
        dict; nobody else may keep one."""
        offenders = [
            p.relative_to(_CORE).as_posix()
            for p in _CORE.rglob("*.py")
            if "_channel_delivery: " in _code(p) and p.name != "channel_delivery.py"
        ]
        assert offenders == [], f"a second delivery slot reappeared in {offenders}"

    def test_the_census_is_not_vacuous(self) -> None:
        """🪤 Floor for the two rails above: if the scan read nothing, both pass on empty lists."""
        files = list(_CORE.rglob("*.py"))
        assert len(files) > 100
        assert "_REGISTRY" in _code(_CORE / "channel_delivery.py")
        assert "def delivery_for" in _code(_CORE / "channel_delivery.py")
