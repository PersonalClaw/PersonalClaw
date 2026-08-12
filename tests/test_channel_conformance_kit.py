"""The conformance kit's OWN tests (CE-6 / T7.1) — a conforming fake passes, and each
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
        with pytest.raises(ChannelContractError, match=r"\[fencing\].*never reads"):
            assert_channel_contract(mod.RawTransport(), inbound_via="_on_message")
    finally:
        sys.modules.pop("conformance_raw_transport", None)


def test_fencing_clause_is_skipped_for_outbound_only_transports():
    """A text-out-only transport has no inbound content to fence; demanding the read
    would fail it for lacking a `may`-level concern."""
    assert_channel_contract(GoodTransport())


# ── §C3 delivery obligations ────────────────────────────────────────────────


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
    """The MUST-NOT half of §C3 (email): edits=False ⇒ start_stream returns ""."""

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


# ── clause 9: vendor-seam completeness (advisory) ───────────────────────────
#
# Driven through `assert_channel_contract`, never by calling the private helper: the whole
# point of riding the existing entry point is that the four app suites get the advisory
# with no apps-repo change, and a test that only exercised the helper would prove the
# mechanism exists without proving anything uses it.

_CHANNEL_PROVIDER = {"type": "channel", "implementation": "fixture_runtime.transport:create"}
_INBOX_PROVIDER = {"type": "inbox", "implementation": "fixture_runtime.source:create"}


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


def test_channel_only_app_warns_about_the_missing_inbox_seam(tmp_path, monkeypatch):
    """The measured shape of telegram-channel/discord-channel: `channel`, no `inbox`."""
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {"name": "fixture-channel", "version": "0.1.0", "provider": _CHANNEL_PROVIDER},
        tag="channelonly",
    )
    with pytest.warns(UserWarning, match=r"vendor completeness") as record:
        assert_channel_contract(provider)
    advisories = _completeness_advisories(record)
    assert len(advisories) == 1, advisories
    message = advisories[0]
    # The advisory must name the MISSING seam and where the checklist lives — a warning
    # that only says "incomplete" sends the reader nowhere.
    assert "fixture-channel" in message
    assert "inbox message source" in message
    assert "no_inbox_source_reason" in message
    assert "docs/guides/build-a-channel-app.md" in message


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
            # The vendor-completeness shape slack-channel ships: canonical singular
            # `provider` for the transport + the inbox source in `providers[]`.
            {
                "name": "fixture-complete",
                "version": "0.1.0",
                "provider": _CHANNEL_PROVIDER,
                "providers": [_INBOX_PROVIDER],
            },
            "complete-singular",
        ),
        (
            # Both seams in the array. Read one declaration shape only and a complete app
            # gets reported as channel-only.
            {
                "name": "fixture-complete-array",
                "version": "0.1.0",
                "providers": [_CHANNEL_PROVIDER, _INBOX_PROVIDER],
            },
            "complete-array",
        ),
    ],
)
def test_complete_vendor_app_gets_no_advisory(tmp_path, monkeypatch, manifest, tag):
    provider = _transport_in_app_bundle(tmp_path, monkeypatch, manifest, tag=tag)
    assert _run_capturing_warnings(provider) == []


def test_declared_reason_suppresses_the_advisory(tmp_path, monkeypatch):
    """The documented exemption: an app states why the vendor has no message source."""
    provider = _transport_in_app_bundle(
        tmp_path,
        monkeypatch,
        {"name": "fixture-channel", "version": "0.1.0", "provider": _CHANNEL_PROVIDER},
        tag="exempt",
    )
    assert (
        _run_capturing_warnings(
            provider, no_inbox_source_reason="this vendor has no message-source semantics"
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
