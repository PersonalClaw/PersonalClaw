"""The conformance kit's OWN tests — a conforming fake passes, and each
deliberately-broken fake fails for the RIGHT named clause.

A kit whose failure path is untested is the "test exercises the mechanism, not its use"
trap: every assertion could be inverted, or silently vacuous, and a green suite would
still say nothing. So each clause here gets a mutant — a fake that violates exactly one
obligation — and the test pins which clause name the failure carries.

Imported the way an APP imports it (``personalclaw.sdk.channel``), not by the
``personalclaw.testing`` path, so the export path the four apps depend on is the one core
exercises. See the kit's module docstring for the export-path decision.
"""

from __future__ import annotations

import json
import sys
import types
import warnings

import pytest

from personalclaw.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)
from personalclaw.sdk.channel import (
    CapturingState,
    ChannelContractError,
    approval_brief_for,
    assert_channel_contract,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Isolate the trust store + SEL to tmp_path — the kit drives the REAL trust seam."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield tmp_path


# ── the conforming reference transport ──────────────────────────────────────


class GoodTransport(ChannelTransportProvider):
    """A minimal transport that honours every MUST: text-out only, no inbound.

    Deliberately the CONSERVATIVE shape — it declares no capability it cannot back — so
    it also proves the kit does not demand `may`-level affordances of every channel.
    """

    def __init__(self, *, token: str = "tok") -> None:
        self._token = token
        # `fenced_text` appears nowhere in this module: an inbound=False transport is
        # never asked to consume it, which the kit must respect.

    @property
    def name(self) -> str:
        return "conformance-good"

    @property
    def display_name(self) -> str:
        return "Conformance Good"

    @property
    def connected(self) -> bool:
        return bool(self._token)

    async def connect(self) -> bool:
        return bool(self._token)

    async def disconnect(self) -> None:
        return None

    async def send(self, message: OutboundMessage) -> bool:
        return bool(self._token)


class InboundTransport(GoodTransport):
    """Declares inbound and backs it with its own loop + a fenced_text read.

    Mirrors the shipped channels' shape: `start_inbound` owns the receive loop and a
    private async handler runs each message through the trust seam. The `fenced_text`
    read below is what the kit's source-level consumption clause looks for.
    """

    @property
    def name(self) -> str:
        return "conformance-inbound"

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True, threads=True, max_text_len=4096)

    async def start_inbound(self, services):  # noqa: ANN001 - matches the ABC
        self._services = services
        return None

    async def _on_message(self, cm: ChannelMessage) -> None:
        from personalclaw.sdk.channel import guard_inbound

        verdict = guard_inbound(
            None, self.name, cm.sender, channel_id=cm.channel_id, is_dm=False, text=cm.text
        )
        if not verdict.allowed:
            return
        self.last_text = verdict.fenced_text or cm.text


def test_conforming_transport_passes():
    assert_channel_contract(GoodTransport()) is None


def test_conforming_inbound_transport_passes():
    assert_channel_contract(InboundTransport(), inbound_via="_on_message")


def test_unconfigured_transport_still_conforms():
    """An offline transport is not a non-conforming one — health/test just agree it is
    offline. Pinned because the easiest way to write clause 5 wrong is to require
    'ready'."""
    assert_channel_contract(GoodTransport(token=""))


# ── clause 1: identity ──────────────────────────────────────────────────────


def test_blank_name_fails_identity():
    class Blank(GoodTransport):
        @property
        def name(self) -> str:
            return "   "

    with pytest.raises(ChannelContractError, match=r"\[identity\].*non-empty string 'name'"):
        assert_channel_contract(Blank())


def test_info_that_relabels_itself_fails_identity():
    class Liar(GoodTransport):
        def info(self):
            d = super().info()
            d["display_name"] = "Something Else"
            return d

    with pytest.raises(ChannelContractError, match=r"\[identity\].*MUST project"):
        assert_channel_contract(Liar())


# ── clause 3: capabilities completeness ─────────────────────────────────────


def test_incomplete_capability_dict_fails():
    class Partial(GoodTransport):
        def capabilities(self):
            caps = ChannelCapabilities(inbound=False)

            class _Trimmed(ChannelCapabilities):
                def to_dict(self):
                    d = ChannelCapabilities.to_dict(self)
                    d.pop("reactions")
                    return d

            return _Trimmed(**{f: getattr(caps, f) for f in caps.to_dict()})

    with pytest.raises(ChannelContractError, match=r"\[capabilities\].*missing \['reactions'\]"):
        assert_channel_contract(Partial())


def test_capability_dict_with_undeclared_key_fails():
    class Extended(GoodTransport):
        def capabilities(self):
            class _Extra(ChannelCapabilities):
                def to_dict(self):
                    d = ChannelCapabilities.to_dict(self)
                    d["telepathy"] = True
                    return d

            return _Extra()

    with pytest.raises(ChannelContractError, match=r"\[capabilities\].*undeclared keys"):
        assert_channel_contract(Extended())


def test_non_boolean_capability_value_fails():
    class Stringly(GoodTransport):
        def capabilities(self):
            class _Str(ChannelCapabilities):
                def to_dict(self):
                    d = ChannelCapabilities.to_dict(self)
                    d["threads"] = "yes"
                    return d

            return _Str()

    with pytest.raises(ChannelContractError, match=r"\[capabilities\].*'threads'.*MUST be a bool"):
        assert_channel_contract(Stringly())


# ── clause 2: connect/send ──────────────────────────────────────────────────


def test_send_returning_non_bool_fails():
    class Chatty(GoodTransport):
        async def send(self, message: OutboundMessage):
            return "sent"

    with pytest.raises(
        ChannelContractError, match=r"\[connect/send\].*send\(\) MUST return a bool"
    ):
        assert_channel_contract(Chatty())


def test_send_that_raises_fails_rather_than_erroring_out():
    """An unconfigured transport must return False, not explode — and the kit must
    report that as a contract violation, not leak the transport's own exception."""

    class Exploding(GoodTransport):
        async def send(self, message: OutboundMessage):
            raise RuntimeError("no credentials")

    with pytest.raises(RuntimeError, match="no credentials"):
        # The kit deliberately does NOT swallow this: a transport raising here is a bug
        # in the transport, and the traceback is more useful than a reworded assertion.
        assert_channel_contract(Exploding())


# ── clause 4: inbound honesty ───────────────────────────────────────────────


def test_inbound_declared_without_any_receiver_fails():
    class Pretender(GoodTransport):
        def capabilities(self):
            return ChannelCapabilities(inbound=True)

    with pytest.raises(ChannelContractError, match=r"\[inbound\].*neither overrides receive"):
        assert_channel_contract(Pretender())


def test_inbound_declared_but_start_inbound_inherited_fails():
    class Inert(GoodTransport):
        def capabilities(self):
            return ChannelCapabilities(inbound=True)

        async def _on_message(self, cm):  # noqa: ANN001
            return None

    with pytest.raises(ChannelContractError, match=r"\[inbound\].*DEFAULT start_inbound"):
        assert_channel_contract(Inert(), inbound_via="_on_message")


def test_receiver_without_the_inbound_flag_fails():
    """The other direction: a real receiver that forgot the flag is invisible to
    routing, which is a silent outage rather than a missing feature."""

    class Hidden(InboundTransport):
        def capabilities(self):
            return ChannelCapabilities(inbound=False)

    with pytest.raises(ChannelContractError, match=r"\[inbound\].*declare inbound=True"):
        assert_channel_contract(Hidden(), inbound_via="_on_message")


# ── clause 5: health/test ───────────────────────────────────────────────────


def test_unmapped_health_state_fails():
    class Novel(GoodTransport):
        async def health(self):
            return {"state": "degraded", "detail": "half up"}

    with pytest.raises(ChannelContractError, match=r"\[health/test\].*MUST be one of"):
        assert_channel_contract(Novel())


def test_test_disagreeing_with_health_fails():
    class Optimist(GoodTransport):
        async def health(self):
            return {"state": "offline", "detail": "no token"}

        async def test(self):
            return {"ok": True, "detail": "looks fine to me"}

    with pytest.raises(ChannelContractError, match=r"\[health/test\].*MUST agree"):
        assert_channel_contract(Optimist())


# ── clause 6: unknown-sender flow ───────────────────────────────────────────


def test_unknown_sender_flow_runs_against_the_real_seam():
    """The clause is not self-driven: it exercises core's guard_inbound + notification
    path, so it would fail if the seam regressed even with every fake intact."""
    assert_channel_contract(GoodTransport())


def test_kit_is_re_entrant_against_one_provider_key():
    """Two runs of the same transport both pass.

    The unknown-sender dedup is PERSISTED on purpose (a stranger who messaged before you
    slept must not re-alert at boot), so a kit reusing one fixture sender id would see
    ``fired_notification=False`` on its second run and fail a conforming transport. An
    app calls the kit once per configured instance, so 'works only the first time' would
    be a trap rather than a contract."""
    assert_channel_contract(GoodTransport())
    assert_channel_contract(GoodTransport())


def test_kit_notices_a_non_default_dm_policy(tmp_path):
    """An 'open' DM policy makes every stranger allowed — the kit must refuse to
    'pass' under a policy that cannot exercise pairing."""
    from personalclaw import channel_trust as ct

    store = ct._read_store()
    rec = ct._provider_record(store, "conformance-good")
    rec["policies"]["dm"] = "open"
    ct._save_provider("conformance-good", rec)
    with pytest.raises(ChannelContractError, match=r"\[unknown-sender\].*'pairing'"):
        assert_channel_contract(GoodTransport())


class OwnerVoiceTransport(GoodTransport):
    """A channel whose messages go out as the owner (their own mailbox)."""

    @property
    def name(self) -> str:
        return "conformance-owner-voice"

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(speaks_as_owner=True)


def test_a_channel_that_speaks_as_the_owner_is_handed_no_reply_for_a_stranger():
    assert_channel_contract(OwnerVoiceTransport())


def test_the_kit_catches_a_gate_that_answers_a_stranger_in_the_owners_name(monkeypatch):
    """🔴 The mailbox case: a reply to a stranger from the owner's own address."""
    from personalclaw import channel_trust as ct

    real = ct.guard_inbound

    def answers_anyway(*args, **kwargs):
        verdict = real(*args, **kwargs)
        verdict.canned_reply = ct.CANNED_PAIRING_REPLY
        return verdict

    monkeypatch.setattr(ct, "guard_inbound", answers_anyway)
    with pytest.raises(ChannelContractError, match=r"\[unknown-sender\].*speaks as the owner"):
        assert_channel_contract(OwnerVoiceTransport())


def test_the_kit_catches_a_strangers_message_that_nobody_holds(monkeypatch):
    """The second message, inside the notice's window, is held too: nothing else answers it."""
    from personalclaw import channel_trust as ct

    real = ct.guard_inbound
    holds: list[int] = []

    def dropped() -> bool:
        return False

    def holds_the_first_only(*args, hold_for_owner=None, **kwargs):
        if hold_for_owner is not None:
            holds.append(1)
            if len(holds) > 1:
                hold_for_owner = dropped  # the mutant: this message is held for nobody
        return real(*args, hold_for_owner=hold_for_owner, **kwargs)

    monkeypatch.setattr(ct, "guard_inbound", holds_the_first_only)
    with pytest.raises(ChannelContractError, match=r"\[unknown-sender\].*held 1 of 2"):
        assert_channel_contract(OwnerVoiceTransport())


def test_capturing_state_records_actionable_notifications():
    state = CapturingState()
    state.notify("agent_request", "t", "b", meta={"actions": ["allow", "deny"]})
    state.notify("info", "plain", "b")
    assert len(state.notifications) == 2
    assert len(state.with_actions()) == 1


# ── clause 7: fencing ───────────────────────────────────────────────────────


def test_inbound_transport_ignoring_fenced_text_fails(tmp_path, monkeypatch):
    """The consumption half. A transport that produces the fence and then passes the RAW
    text on is the failure mode a seam-only assertion cannot see.

    Written to a real file and imported, because that is how an app's transport module
    reaches the kit (the app loader imports from disk) — an ``exec``'d module has no
    retrievable source and would exercise the kit's unverifiable-source path instead of
    the clause under test.
    """
    import importlib
    import sys
    import textwrap

    mod_path = tmp_path / "conformance_raw_transport.py"
    mod_path.write_text(
        textwrap.dedent('''
            """A transport that fences nothing — the regression the clause catches."""

            from personalclaw.channel_transports.base import (
                ChannelCapabilities,
                ChannelTransportProvider,
            )


            class RawTransport(ChannelTransportProvider):
                @property
                def name(self):
                    return "conformance-raw"

                @property
                def display_name(self):
                    return "Conformance Raw"

                @property
                def connected(self):
                    return True

                async def connect(self):
                    return True

                async def disconnect(self):
                    return None

                async def send(self, message):
                    return True

                def capabilities(self):
                    return ChannelCapabilities(inbound=True)

                async def start_inbound(self, services):
                    return None

                async def _on_message(self, cm):
                    self.last_text = cm.text  # RAW — the verdict's fence is dropped
            '''),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = importlib.import_module("conformance_raw_transport")
    try:
        with pytest.raises(ChannelContractError, match=r"\[fencing\].*neither routes"):
            assert_channel_contract(mod.RawTransport(), inbound_via="_on_message")
    finally:
        sys.modules.pop("conformance_raw_transport", None)


def test_inbound_transport_routing_through_the_door_passes_fencing(tmp_path, monkeypatch):
    """A transport that hands inbound to ``services.deliver_channel_inbound``
    satisfies the fencing clause BY CONSTRUCTION — core applies the fence inside the
    door, so there is no ``fenced_text`` left for the transport to read. The kit must
    not fail the migration it exists to encourage."""
    import importlib
    import sys
    import textwrap

    mod_path = tmp_path / "conformance_door_transport.py"
    mod_path.write_text(
        textwrap.dedent('''
            """A transport on the guarded door — the target shape."""

            from personalclaw.channel_transports.base import (
                ChannelCapabilities,
                ChannelTransportProvider,
            )


            class DoorTransport(ChannelTransportProvider):
                _services = None

                @property
                def name(self):
                    return "conformance-door"

                @property
                def display_name(self):
                    return "Conformance Door"

                @property
                def connected(self):
                    return True

                async def connect(self):
                    return True

                async def disconnect(self):
                    return None

                async def send(self, message):
                    return True

                def capabilities(self):
                    return ChannelCapabilities(inbound=True)

                async def start_inbound(self, services):
                    self._services = services

                async def _on_message(self, cm):
                    await self._services.deliver_channel_inbound(self.name, cm, is_dm=True)
            '''),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = importlib.import_module("conformance_door_transport")
    try:
        assert_channel_contract(mod.DoorTransport(), inbound_via="_on_message")
    finally:
        sys.modules.pop("conformance_door_transport", None)


def test_fencing_clause_is_skipped_for_outbound_only_transports():
    """A text-out-only transport has no inbound content to fence; demanding the read
    would fail it for lacking a `may`-level concern."""
    assert_channel_contract(GoodTransport())


# ── delivery obligations ────────────────────────────────────────────────────


class _FakeBackend:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self._mid = 0

    def next_id(self) -> str:
        self._mid += 1
        return str(self._mid)


class GoodDelivery:
    """A conforming delivery over ``_FakeBackend`` with an injected clock + 1.1s floor."""

    MIN_EDIT_INTERVAL = 1.1

    def __init__(self, backend: _FakeBackend) -> None:
        self._api = backend
        self._now_value = 0.0
        self._last_edit = 0.0
        self._pending = ""
        self._streams: dict[str, str] = {}

    def _now(self) -> float:
        return self._now_value

    def set_clock(self, t: float) -> None:
        self._now_value = t

    async def deliver_text(self, channel, text, thread_ts="", **kw):
        self._api.sent.append({"channel": channel, "text": text})
        return self._api.next_id()

    async def deliver_rich(self, channel, payload, fallback_text, *, thread_ts="", **kw):
        return await self.deliver_text(channel, fallback_text, thread_ts)

    async def upload_attachment(self, channel, file_path, *, filename="", thread_ts="", **kw):
        return ""

    async def request_approval(self, event, *, source, **kw):
        return None

    def build_thread_link(self, channel, ts):
        return f"conformance://{channel}/{ts}"

    async def start_stream(self, channel, thread_ts="", initial_text=""):
        ts = self._api.next_id()
        self._api.sent.append({"channel": channel, "text": initial_text})
        self._streams[ts] = initial_text
        self._last_edit = self._now()
        return ts

    async def append_stream_task(self, channel, stream_ts, task_id, title, status):
        self._pending = f"{title} [{status}]"
        await self._maybe_edit(channel, stream_ts)

    async def stop_stream(self, channel, stream_ts):
        await self._maybe_edit(channel, stream_ts, force=True)

    async def _maybe_edit(self, channel, stream_ts, force=False):
        now = self._now()
        if not force and (now - self._last_edit) < self.MIN_EDIT_INTERVAL:
            return
        self._last_edit = now
        self._api.edits.append({"ts": stream_ts, "text": self._pending})


def _wire(delivery: GoodDelivery):
    return {
        "delivery": delivery,
        "fake_backend": delivery._api,
        "min_edit_interval": GoodDelivery.MIN_EDIT_INTERVAL,
        "clock": delivery.set_clock,
    }


class StreamingTransport(GoodTransport):
    @property
    def name(self) -> str:
        return "conformance-streaming"

    def capabilities(self):
        return ChannelCapabilities(inbound=False, edits=True, rich_text=True, max_text_len=2000)


def test_conforming_delivery_and_throttle_pass():
    d = GoodDelivery(_FakeBackend())
    assert_channel_contract(StreamingTransport(), **_wire(d))


def test_missing_should_level_delivery_method_fails():
    class NoThreadLink(GoodDelivery):
        build_thread_link = None  # type: ignore[assignment]

    d = NoThreadLink(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[delivery\].*build_thread_link is SHOULD"):
        assert_channel_contract(StreamingTransport(), **_wire(d))


def test_unthrottled_stream_fails_the_throttle_clause():
    class Unthrottled(GoodDelivery):
        async def _maybe_edit(self, channel, stream_ts, force=False):
            self._last_edit = self._now()
            self._api.edits.append({"ts": stream_ts, "text": self._pending})

    d = Unthrottled(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[streaming\].*MUST NOT edit"):
        assert_channel_contract(StreamingTransport(), **_wire(d))


def test_stop_stream_that_never_flushes_fails():
    class NoFlush(GoodDelivery):
        async def stop_stream(self, channel, stream_ts):
            return None

    d = NoFlush(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[streaming\].*force-flush"):
        assert_channel_contract(StreamingTransport(), **_wire(d))


def test_throttle_clause_refuses_to_pass_vacuously_without_a_counter():
    """No `edits` list anywhere ⇒ the kit fails loudly rather than counting zero edits
    forever and calling every throttle correct."""

    class Opaque(GoodDelivery):
        def __init__(self):
            super().__init__(_FakeBackend())
            self._api = object()  # no `edits` list to count

    d = Opaque()
    wired = _wire(d)
    wired["fake_backend"] = None
    with pytest.raises(ChannelContractError, match=r"\[streaming\].*cannot count edits"):
        assert_channel_contract(StreamingTransport(), **wired)


def test_non_streaming_channel_must_return_empty_stream_ts():
    """The MUST-NOT half of clause 8 (email): edits=False ⇒ start_stream returns ""."""

    class NoEdits(GoodTransport):
        @property
        def name(self) -> str:
            return "conformance-noedits"

        def capabilities(self):
            return ChannelCapabilities(inbound=False, edits=False, rich_text=True)

    good = GoodDelivery(_FakeBackend())

    class Silent(GoodDelivery):
        async def start_stream(self, channel, thread_ts="", initial_text=""):
            return ""

    assert_channel_contract(NoEdits(), delivery=Silent(_FakeBackend()))
    with pytest.raises(ChannelContractError, match=r"\[streaming\].*MUST return"):
        assert_channel_contract(NoEdits(), delivery=good)


def test_throttle_clause_is_presence_only_without_a_clock():
    """A caller that cannot inject a clock still gets the trio-presence check, and the
    kit does not sleep or invent a floor."""
    d = GoodDelivery(_FakeBackend())
    assert_channel_contract(StreamingTransport(), delivery=d)


def test_partial_streaming_trio_fails():
    class NoStop(GoodDelivery):
        stop_stream = None  # type: ignore[assignment]

    with pytest.raises(ChannelContractError, match=r"\[streaming\].*stop_stream"):
        assert_channel_contract(StreamingTransport(), delivery=NoStop(_FakeBackend()))


@pytest.mark.parametrize("wired", [True, False], ids=["throttled", "presence-only"])
def test_a_stream_that_takes_only_the_first_two_statuses_fails(wired):
    """🔴 The kit drove `in_progress` and `complete` alone, so this delivery passed."""

    class TwoStatuses(GoodDelivery):
        async def append_stream_task(self, channel, stream_ts, task_id, title, status):
            if status not in ("in_progress", "complete"):
                raise ValueError(f"unknown status {status}")
            await super().append_stream_task(channel, stream_ts, task_id, title, status)

    d = TwoStatuses(_FakeBackend())
    kwargs = _wire(d) if wired else {"delivery": d}
    with pytest.raises(ChannelContractError, match=r"\[streaming\].*status 'failed'"):
        assert_channel_contract(StreamingTransport(), **kwargs)


def test_every_status_reaches_the_stream():
    d = GoodDelivery(_FakeBackend())
    seen: list[str] = []
    real = d.append_stream_task

    async def recording(channel, stream_ts, task_id, title, status):
        seen.append(status)
        await real(channel, stream_ts, task_id, title, status)

    d.append_stream_task = recording  # type: ignore[method-assign]
    assert_channel_contract(StreamingTransport(), **_wire(d))

    from personalclaw.channel_delivery import TASK_STATUSES

    assert set(TASK_STATUSES) <= set(seen)


# ── clause 10: approval endings ─────────────────────────────────────────────


class _Pending:
    def __init__(self, request_id: str) -> None:
        import asyncio

        self.request_id = request_id
        self.future = asyncio.get_running_loop().create_future()


_LATE = {
    "approved": "Already approved. This press changes nothing.",
    "rejected": "Already rejected. This press changes nothing.",
    "expired": "Nobody answered in time, so it did not run.",
    "cancelled": "Cancelled: the work that asked for it stopped first.",
}


class PromptingDelivery(GoodDelivery):
    """Asks, offering the brief's answers, waits for the approval to end however it ends, and
    answers a late press."""

    def __init__(self, backend: _FakeBackend) -> None:
        super().__init__(backend)
        self.waiting: dict[str, _Pending] = {}
        self.offered: dict[str, dict[str, str]] = {}
        self.ended: dict[str, str] = {}

    def offers(self, event) -> list[dict[str, str]]:
        return list(approval_brief_for(event)["answers"])

    async def request_approval(self, event, *, source, on_prompted=None, **kw):
        pending = _Pending(str(event.request_id))
        self.waiting[pending.request_id] = pending
        self.offered = {a["key"]: a for a in self.offers(event)}
        buttons = " / ".join(a["label"] for a in self.offered.values())
        await self.deliver_text("owner-dm", f"Approve {event.title}? {buttons}")
        if on_prompted:
            on_prompted(pending)
        try:
            outcome = await pending.future
        finally:
            self.waiting.pop(pending.request_id, None)
        ending = self.offered[outcome]["ends"] if outcome in self.offered else outcome
        self.ended[pending.request_id] = ending
        return ending == "approved"

    async def on_press(self, request_id: str, answer: str) -> str:
        """The app's own press handler."""
        pending = self.waiting.get(request_id)
        if pending is None or pending.future.done():
            return _LATE.get(self.ended.get(request_id, ""), "No longer waiting.")
        if answer in self.offered:
            pending.future.set_result(answer)
        return ""


def _press(delivery: PromptingDelivery):
    async def press(pending, answer: str) -> str:
        return await delivery.on_press(pending.request_id, answer)

    return press


def _approvals(delivery: PromptingDelivery, **overrides):
    return {"delivery": delivery, "press": _press(delivery), **overrides}


def test_a_delivery_whose_approvals_end_as_core_ends_them_passes():
    d = PromptingDelivery(_FakeBackend())
    assert_channel_contract(StreamingTransport(), **_approvals(d))
    # Four endings core gives, then a press on each answer offered: once, this chat, deny.
    assert sorted(d.ended.values()) == sorted(
        ["approved", "rejected", "expired", "cancelled", "approved", "approved", "rejected"]
    )


def test_a_prompt_that_offers_only_approve_and_deny_fails():
    """A channel renders what the brief offers. One that keeps its own Approve/Deny drops "Allow
    for this chat", the dashboard card's answer, and a press on it answers nothing."""

    class TwoButtons(PromptingDelivery):
        def offers(self, event):
            return [a for a in super().offers(event) if a["key"] in ("approved", "rejected")]

    d = TwoButtons(_FakeBackend())
    with pytest.raises(
        ChannelContractError, match=r"\[approvals\].*'Allow for this chat'.*MUST resolve"
    ):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_a_standing_answer_read_as_not_approved_fails():
    """ "Allow for this chat" approves this call too: a prompt that returns False for it would
    leave the call it was pressed on unrun."""

    class OnceOnly(PromptingDelivery):
        async def request_approval(self, event, **kw):
            approved = await super().request_approval(event, **kw)
            return approved and self.pressed != "trust"

        async def on_press(self, request_id, answer):
            self.pressed = answer
            return await super().on_press(request_id, answer)

    d = OnceOnly(_FakeBackend())
    d.pressed = ""
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*'Allow for this chat'.*True"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_without_a_press_the_endings_are_not_asserted():
    """The kit cannot press a vendor's button itself: GoodDelivery asks nobody and passes."""
    assert_channel_contract(StreamingTransport(), delivery=GoodDelivery(_FakeBackend()))


def test_a_prompt_that_never_hands_core_its_record_fails():
    class Mute(PromptingDelivery):
        async def request_approval(self, event, *, source, on_prompted=None, **kw):
            return None

    d = Mute(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*never handed core"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_a_record_without_a_future_fails():
    class NoFuture(PromptingDelivery):
        async def request_approval(self, event, *, source, on_prompted=None, **kw):
            on_prompted(types.SimpleNamespace(request_id=event.request_id))
            return None

    d = NoFuture(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*asyncio.Future"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_an_approval_that_expired_read_as_approved_fails():
    class Yes(PromptingDelivery):
        async def request_approval(self, event, **kw):
            await super().request_approval(event, **kw)
            return True

    d = Yes(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*ended 'rejected' MUST make"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_a_wait_with_a_clock_of_its_own_fails(monkeypatch):
    """A prompt that ends on core's word only after a timer of its own: the wait lags the end."""
    import asyncio

    from personalclaw.testing import channel_conformance as kit

    monkeypatch.setattr(kit, "_APPROVAL_WAIT_SECS", 0.2)

    class OwnClock(PromptingDelivery):
        async def request_approval(self, event, **kw):
            answer = await super().request_approval(event, **kw)
            await asyncio.sleep(1)
            return answer

    d = OwnClock(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*did not return once"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_a_late_press_taken_for_an_answer_fails():
    class Racy(PromptingDelivery):
        async def on_press(self, request_id, answer):
            # Answers the record whatever state it is in: a press after the end raises.
            pending = self.waiting.get(request_id) or self._last
            pending.future.set_result(answer)
            return "Recorded"

        async def request_approval(self, event, *, source, on_prompted=None, **kw):
            def remember(pending):
                self._last = pending
                on_prompted(pending)

            return await super().request_approval(event, source=source, on_prompted=remember)

    d = Racy(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*raised InvalidStateError"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_a_late_press_left_unanswered_fails():
    class Silent(PromptingDelivery):
        async def on_press(self, request_id, answer):
            await super().on_press(request_id, answer)
            return ""

    d = Silent(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*MUST be told how it ended"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_late_presses_told_alike_whatever_ended_fail():
    class OneAnswer(PromptingDelivery):
        async def on_press(self, request_id, answer):
            told = await super().on_press(request_id, answer)
            return "This approval is closed." if told else ""

    d = OneAnswer(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*answered alike"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_a_press_that_answers_nothing_fails(monkeypatch):
    from personalclaw.testing import channel_conformance as kit

    monkeypatch.setattr(kit, "_APPROVAL_WAIT_SECS", 0.2)

    class Deaf(PromptingDelivery):
        async def on_press(self, request_id, answer):
            pending = self.waiting.get(request_id)
            if pending is not None and not pending.future.done():
                return ""  # the owner's press, dropped
            return await super().on_press(request_id, answer)

    d = Deaf(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*MUST resolve the approval"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


def test_a_wait_that_swallows_its_cancellation_fails():
    import asyncio

    class Swallows(PromptingDelivery):
        async def request_approval(self, event, **kw):
            try:
                return await super().request_approval(event, **kw)
            except asyncio.CancelledError:
                return False

    d = Swallows(_FakeBackend())
    with pytest.raises(ChannelContractError, match=r"\[approvals\].*swallowed it"):
        assert_channel_contract(StreamingTransport(), **_approvals(d))


# ── clause 9: vendor-seam completeness (advisory) ───────────────────────────
#
# Driven through `assert_channel_contract`, never by calling the private helper: the whole
# point of riding the existing entry point is that the four app suites get the advisory
# with no apps-repo change, and a test that only exercised the helper would prove the
# mechanism exists without proving anything uses it.

_CHANNEL_PROVIDER = {"type": "channel", "implementation": "fixture_runtime.transport:create"}
_INBOX_PROVIDER = {"type": "inbox", "implementation": "fixture_runtime.source:create"}
#: the arm. Generically named, like every other fixture here — core contains no vendor names,
#: and a test fixture is a tracked file like any other.
_TRIGGER_SOURCE_PROVIDER = {
    "type": "trigger_source",
    "implementation": "fixture_runtime.trigger_source:create",
}
_COMPLETE_PROVIDERS = [_INBOX_PROVIDER, _TRIGGER_SOURCE_PROVIDER]


def _transport_in_app_bundle(tmp_path, monkeypatch, manifest, *, tag: str):
    """A `GoodTransport` in a real app bundle directory, reusing the conforming fixture.

    `inspect.getfile(cls)` resolves a class through `sys.modules[cls.__module__].__file__`
    — the exact mechanism the completeness clause starts its walk from — so pointing a
    subclass's module at a real file inside a real bundle drives the real discovery walk
    without writing a second fake transport. `manifest=None` writes no `app.json` at all.
    """
    bundle = tmp_path / "apps" / f"{tag}-channel"
    runtime = bundle / f"{tag}_runtime"
    runtime.mkdir(parents=True)
    if manifest is not None:
        (bundle / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    module_file = runtime / "transport.py"
    module_file.write_text("# fixture bundle module for the completeness clause\n")
    module_name = f"_conformance_bundle_{tag}"
    module = types.ModuleType(module_name)
    module.__file__ = str(module_file)
    monkeypatch.setitem(sys.modules, module_name, module)
    cls = type("BundledTransport", (GoodTransport,), {"__module__": module_name})
    module.BundledTransport = cls
    return cls()


def _completeness_advisories(record) -> list[str]:
    return [str(w.message) for w in record if "vendor completeness" in str(w.message)]


def _run_capturing_warnings(provider, **kwargs) -> list[str]:
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        assert_channel_contract(provider, **kwargs)
    return _completeness_advisories(record)


def test_channel_only_app_warns_about_BOTH_missing_seams_SEPARATELY(tmp_path, monkeypatch):
    """A `channel`-only manifest is missing two seams, and gets two advisories.

    Two, not one merged sentence: "you have no inbox" and "you have no trigger source" are
    different facts about different work, and a reader who has decided about one must not
    have to re-read a paragraph to find the other. Each must name ITS seam, ITS consequence
    and ITS own suppressor.
    """
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {"name": "fixture-channel", "version": "0.1.0", "provider": _CHANNEL_PROVIDER},
        tag="channelonly",
    )
    with pytest.warns(UserWarning, match=r"vendor completeness") as record:
        assert_channel_contract(provider)
    advisories = _completeness_advisories(record)
    assert len(advisories) == 2, advisories
    inbox = [m for m in advisories if "no inbox provider" in m]
    trigger = [m for m in advisories if "no trigger_source provider" in m]
    assert len(inbox) == 1, advisories
    assert len(trigger) == 1, advisories
    # Each advisory must name the app, the missing seam, the fix, its own exemption and
    # where the checklist lives — a warning that only says "incomplete" sends the reader
    # nowhere.
    for message in advisories:
        assert "fixture-channel" in message
        assert "docs/guides/build-a-channel-app.md" in message
    assert "no_inbox_source_reason" in inbox[0]
    assert "no_trigger_source_reason" not in inbox[0], "the arms must not cross-reference"
    assert "no_trigger_source_reason" in trigger[0]
    assert "no_inbox_source_reason" not in trigger[0], "the arms must not cross-reference"
    # The trigger arm must say the seam is available, not that it is coming: the whole 0/4
    # window happened because the prose said "once that seam exists" after it existed.
    assert "seam is LIVE" in trigger[0]
    assert "once that seam exists" not in trigger[0]


def test_a_channel_plus_inbox_app_still_warns_about_the_TRIGGER_SOURCE_arm(tmp_path, monkeypatch):
    """🔴 The regression this arm exists for: the original slack-channel shape.

    `channel` + `inbox` was "full vendor completeness" when this clause shipped, and the
    clause went silent on it — so the trigger-source obligation sat at 0/4 with the kit reporting
    nothing wrong. Exactly ONE advisory now, and it is the trigger one.
    """
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {
            "name": "fixture-inboxonly",
            "version": "0.1.0",
            "provider": _CHANNEL_PROVIDER,
            "providers": [_INBOX_PROVIDER],
        },
        tag="inboxonly",
    )
    advisories = _run_capturing_warnings(provider)
    assert len(advisories) == 1, advisories
    assert "no trigger_source provider" in advisories[0]
    assert "no inbox provider" not in advisories[0]


def test_a_channel_plus_trigger_source_app_still_warns_about_the_INBOX_arm(tmp_path, monkeypatch):
    """The mirror. Adopting one arm must not silence the other — the failure mode of a
    single merged "seams are incomplete" advisory."""
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {
            "name": "fixture-triggeronly",
            "version": "0.1.0",
            "provider": _CHANNEL_PROVIDER,
            "providers": [_TRIGGER_SOURCE_PROVIDER],
        },
        tag="triggeronly",
    )
    advisories = _run_capturing_warnings(provider)
    assert len(advisories) == 1, advisories
    assert "no inbox provider" in advisories[0]
    assert "no trigger_source provider" not in advisories[0]


def test_channel_only_app_still_passes_every_hard_clause(tmp_path, monkeypatch):
    """The advisory is a WARNING, never a failure: two already-green app suites must not
    go red for a doctrine that postdates them."""
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {"name": "fixture-channel", "version": "0.1.0", "provider": _CHANNEL_PROVIDER},
        tag="stillpasses",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert assert_channel_contract(provider) is None


@pytest.mark.parametrize(
    "manifest,tag",
    [
        (
            # The vendor-completeness shape slack-channel ships: canonical
            # singular `provider` for the transport + both companion seams in `providers[]`.
            {
                "name": "fixture-complete",
                "version": "0.1.0",
                "provider": _CHANNEL_PROVIDER,
                "providers": _COMPLETE_PROVIDERS,
            },
            "complete-singular",
        ),
        (
            # All three seams in the array. Read one declaration shape only and a complete
            # app gets reported as channel-only.
            {
                "name": "fixture-complete-array",
                "version": "0.1.0",
                "providers": [_CHANNEL_PROVIDER, *_COMPLETE_PROVIDERS],
            },
            "complete-array",
        ),
    ],
)
def test_complete_vendor_app_gets_no_advisory(tmp_path, monkeypatch, manifest, tag):
    provider = _transport_in_app_bundle(tmp_path, monkeypatch, manifest, tag=tag)
    assert _run_capturing_warnings(provider) == []


def test_each_declared_reason_suppresses_ONLY_ITS_OWN_arm(tmp_path, monkeypatch):
    """The documented exemptions, and their INDEPENDENCE.

    Suppressing the inbox arm must leave the trigger arm audible and vice versa: a shared
    "seams I skip" string would let an app silence a seam nobody had thought about. Only
    supplying both goes quiet.
    """
    manifest = {"name": "fixture-channel", "version": "0.1.0", "provider": _CHANNEL_PROVIDER}
    inbox_only = _transport_in_app_bundle(tmp_path, monkeypatch, manifest, tag="exempt-inbox")
    advisories = _run_capturing_warnings(
        inbox_only, no_inbox_source_reason="this vendor has no message-source semantics"
    )
    assert len(advisories) == 1, advisories
    assert "no trigger_source provider" in advisories[0]

    trigger_only = _transport_in_app_bundle(tmp_path, monkeypatch, manifest, tag="exempt-trigger")
    advisories = _run_capturing_warnings(
        trigger_only, no_trigger_source_reason="this vendor emits nothing an automation can use"
    )
    assert len(advisories) == 1, advisories
    assert "no inbox provider" in advisories[0]

    both = _transport_in_app_bundle(tmp_path, monkeypatch, manifest, tag="exempt-both")
    assert (
        _run_capturing_warnings(
            both,
            no_inbox_source_reason="no message-source semantics",
            no_trigger_source_reason="nothing an automation can use",
        )
        == []
    )


def test_provider_with_no_discoverable_manifest_is_silent(tmp_path, monkeypatch):
    """No `app.json` in the bundle: an undiscoverable manifest is not evidence of an
    incomplete app, and an advisory firing on a bare fixture teaches readers to ignore
    it."""
    provider = _transport_in_app_bundle(tmp_path, monkeypatch, None, tag="nomanifest")
    assert _run_capturing_warnings(provider) == []


def test_core_fixture_transport_is_silent():
    """The same claim for core's OWN fixtures: `GoodTransport` lives under `tests/`, whose
    walk hits the repo root marker before any `app.json`, so core's suite never nags
    itself."""
    assert _run_capturing_warnings(GoodTransport()) == []


def test_manifest_declaring_no_channel_provider_is_silent(tmp_path, monkeypatch):
    """This clause only speaks about channel apps. A manifest that registers no `channel`
    provider is a different shape (and a different defect), not an incomplete vendor."""
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {"name": "fixture-notachannel", "version": "0.1.0", "providers": [_INBOX_PROVIDER]},
        tag="nochannel",
    )
    assert _run_capturing_warnings(provider) == []


def test_unreadable_manifest_is_silent(tmp_path, monkeypatch):
    """Malformed JSON is the install pipeline's red to report, not this clause's."""
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {"name": "fixture-broken", "version": "0.1.0", "provider": _CHANNEL_PROVIDER},
        tag="broken",
    )
    (tmp_path / "apps" / "broken-channel" / "app.json").write_text("{not json", encoding="utf-8")
    assert _run_capturing_warnings(provider) == []
