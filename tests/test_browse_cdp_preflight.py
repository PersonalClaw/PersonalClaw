"""Every CDP navigation is pre-flighted through the egress guard.

The clause under test is an ORDERING clause — "a denied host is blocked before
``Page.navigate`` fires" — so almost every assertion here is about the CDP messages
recorded on the fake transport, not about a return value. A gate that decides after the
message is on the wire returns exactly the same object as one that decides before it; only
the wire tells them apart.

The fake transport and the fake resolver together mean no browser is launched and no DNS
query is made. ``SecurityEventLog`` is replaced for every test, so nothing writes to a real
audit log, and ``AppConfig.load`` is replaced, so nothing reads the real home.

The profile under test is the SHIPPED ``net.policy.BROWSE``, deliberately. This file used to
install a stand-in profile via an autouse fixture, because BROWSE lived on a sibling branch
and did not exist here. Once that sibling merged, the stand-in stopped being a scaffold and
became a *shadow*: every test in the file kept asserting against a two-field fake while the
real 50 MB / 10-redirect / ``pin_resolved_ip=False`` profile went unexercised, so the clause
"pre-flighted against a new BROWSE profile in ``net/policy.py``" was self-fulfilling. This is
the same defect the sibling ``safety_script`` stub had (see
:func:`test_a_missing_safety_script_module_fails_closed`) — a scaffold that only reads as a
scaffold on the branch that lacked the real thing. Both are gone now.

Two tests carry what the stand-in used to:
:func:`test_the_guard_is_called_with_the_real_browse_profile` asserts the policy handed to the
guard at the call site carries the shipped profile's own field values (a fallback to STRICT
reds it), and
:func:`test_a_missing_browse_profile_fails_closed` proves the module really reads that symbol.
The deny used throughout comes from the OPERATOR's ``deny_hosts``, layered on by
``egress_policy_for`` — the real path a self-hoster's denial travels.
"""

import sys
import types
from dataclasses import dataclass, field

import pytest

from personalclaw.browse import cdp
from personalclaw.config.loader import AppConfig
from personalclaw.net import policy as net_policy
from personalclaw.net.policy import egress_policy_for

# Public addresses, so the guard's public-only stance is never what denies a host here —
# only the policy is.
_DNS = {
    "allowed.example": ["93.184.216.34"],
    "denied.example": ["93.184.216.35"],
    "operator-denied.example": ["93.184.216.36"],
    "redirected.example": ["93.184.216.37"],
}


def _resolver(host: str) -> list[str]:
    import socket

    try:
        return _DNS[host]
    except KeyError:  # pragma: no cover - a test asking for an unknown host is a bug
        raise socket.gaierror(f"no fake DNS entry for {host!r}")


def _tree(*urls: str) -> dict:
    """A ``Page.getFrameTree`` reply: the first URL is the top frame, the rest its children."""
    top, *children = urls
    return {
        "frameTree": {
            "frame": {"id": "TOP", "url": top},
            "childFrames": [
                {"frame": {"id": f"CHILD{i}", "url": u}} for i, u in enumerate(children)
            ],
        }
    }


class FakeTransport:
    """Records every CDP message instead of sending it to a browser.

    ``frames`` is what the tab holds when a session starts (``Page.getFrameTree``); by default
    a blank tab, as a page target the browser has just opened holds."""

    def __init__(
        self, *, fail_on: set[str] | None = None, frames: tuple[str, ...] = (cdp.BLANK_URL,)
    ) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.listener = None
        self._fail_on = fail_on or set()
        self.frames = frames
        self.replies: dict[str, dict] = {}

    async def send(self, method: str, params: dict | None = None) -> dict:
        if method in self._fail_on:
            # Nothing is recorded: a send that raised never reached the wire.
            raise RuntimeError(f"transport is down for {method}")
        self.sent.append((method, dict(params or {})))
        if method == cdp.GET_FRAME_TREE:
            # ``frames=None``: the browser answers no frame tree at all.
            return _tree(*self.frames) if self.frames is not None else {}
        return dict(self.replies.get(method, {}))

    def set_event_listener(self, listener) -> None:
        self.listener = listener

    @property
    def methods(self) -> list[str]:
        return [m for m, _ in self.sent]

    def count(self, method: str) -> int:
        return self.methods.count(method)


@dataclass
class _FakeEgressConfig:
    allow_hosts: list = field(default_factory=list)
    deny_hosts: list = field(default_factory=list)
    allow_private: bool = False


@pytest.fixture
def operator_egress(monkeypatch):
    """The operator's ``security.egress`` block, faked so no real config is read.

    ``denied.example`` is denied by default because the shipped BROWSE profile carries no
    ``deny_hosts`` of its own — its stance is public-only, and every host in ``_DNS``
    resolves to a public address on purpose. So the denial has to come from the operator
    layer, which is also the only place a self-hoster's denial ever comes from. That makes
    ``egress_policy_for`` part of the path under test rather than a detail around it.
    """
    cfg = _FakeEgressConfig(deny_hosts=["denied.example"])
    fake_app = types.SimpleNamespace(security=types.SimpleNamespace(egress=cfg))
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: fake_app))
    return cfg


@pytest.fixture(autouse=True)
def _no_real_config(operator_egress):
    """Autouse so no test in this module reaches ``AppConfig.load()``'s real home."""
    return operator_egress


@pytest.fixture(autouse=True)
def _forget_remembered_denies(monkeypatch):
    """``egress_policy_for`` remembers the last deny list at MODULE scope (policy.py).

    Left alone, one test's operator denials leak into the next test in the same worker via
    ``_LAST_DENY_HOSTS`` — and worse, into whatever else that worker runs. Reset per test.
    """
    monkeypatch.setattr(net_policy, "_LAST_DENY_HOSTS", ())


@pytest.fixture(autouse=True)
def sel_rows(monkeypatch):
    """Capture SEL rows; never write to a real ``SecurityEventLog``."""
    rows: list = []

    class _Recorder:
        def log(self, event) -> None:
            rows.append(event)

    monkeypatch.setattr(cdp, "SecurityEventLog", lambda: _Recorder())
    return rows


@pytest.fixture(autouse=True)
def safety_script_calls(monkeypatch):
    """Stub the sibling's ``browse/safety_script`` module and record how it was called."""
    calls: list[dict] = []
    module = types.ModuleType("personalclaw.browse.safety_script")

    def safety_script(*, allow_hosts: tuple[str, ...] = ()) -> str:
        calls.append({"allow_hosts": allow_hosts})
        return "/* in-page guard */ window.__personalclaw_guard = true;"

    module.SAFETY_SCRIPT = "/* in-page guard */"
    module.safety_script = safety_script
    monkeypatch.setitem(sys.modules, "personalclaw.browse.safety_script", module)
    return calls


def _session(transport: FakeTransport) -> cdp.GatedCdpSession:
    return cdp.GatedCdpSession(transport, resolver=_resolver)


async def _started(transport: FakeTransport) -> cdp.GatedCdpSession:
    session = _session(transport)
    await session.start()
    return session


def _frame(url: str) -> dict:
    return {"frame": {"id": "FRAME1", "url": url}}


# ── the ordering clause ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_denied_host_sends_zero_page_navigate():
    """THE clause: the block happens BEFORE ``Page.navigate`` reaches the wire."""
    transport = FakeTransport()
    session = await _started(transport)

    outcome = await session.navigate("https://denied.example/secret")

    assert transport.count(cdp.NAVIGATE) == 0, (
        "a denied host must not produce a Page.navigate message; " f"wire was {transport.methods}"
    )
    assert outcome.allowed is False and outcome.ok is False
    assert outcome.host == "denied.example"
    assert "deny list" in outcome.reason


@pytest.mark.asyncio
async def test_allowed_host_sends_exactly_one_page_navigate():
    """Vacuity floor for the test above: the 'zero' assertion can discriminate."""
    transport = FakeTransport()
    session = await _started(transport)

    outcome = await session.navigate("https://allowed.example/page")

    assert transport.count(cdp.NAVIGATE) == 1
    assert (cdp.NAVIGATE, {"url": "https://allowed.example/page"}) in transport.sent
    assert outcome.ok is True and outcome.allowed is True
    assert outcome.pinned_ips == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_guard_script_is_registered_before_the_first_navigate():
    """Injection must precede navigation, or the first document loads unguarded."""
    transport = FakeTransport()
    session = await _started(transport)
    await session.navigate("https://allowed.example/page")

    methods = transport.methods
    assert cdp.ADD_SCRIPT in methods and cdp.NAVIGATE in methods
    assert methods.index(cdp.ADD_SCRIPT) < methods.index(cdp.NAVIGATE), methods


@pytest.mark.asyncio
async def test_safety_script_is_called_with_the_layered_allow_hosts(
    safety_script_calls, operator_egress
):
    """The in-page guard is handed the operator's allow-list, not an empty tuple."""
    operator_egress.allow_hosts = ["lan.example"]
    transport = FakeTransport()
    await _started(transport)

    assert safety_script_calls == [{"allow_hosts": ("lan.example",)}]


@pytest.mark.asyncio
async def test_the_guard_is_called_with_the_real_browse_profile(monkeypatch):
    """Clause 1, asserted at the CALL SITE: the policy handed to ``evaluate`` IS BROWSE.

    Every other test in this file would still pass if the session quietly reached for STRICT,
    because the operator's deny list is layered onto any profile. This one cannot: it captures
    the second positional argument at the ``evaluate`` call site and compares it, field for
    field, with ``egress_policy_for(net_policy.BROWSE)``.

    The three explicit field assertions are the discriminator, and each names a value STRICT
    does NOT have — 10 redirects (STRICT 5), 50 MB (STRICT 5 MB), ``pin_resolved_ip=False``
    (STRICT True). A fallback to STRICT, CONNECTOR or SOURCE reds all three.
    """
    captured: list = []
    real_evaluate = cdp.evaluate

    def _spy(url, policy, **kwargs):
        captured.append(policy)
        return real_evaluate(url, policy, **kwargs)

    monkeypatch.setattr(cdp, "evaluate", _spy)
    transport = FakeTransport()
    session = await _started(transport)
    outcome = await session.navigate("https://allowed.example/page")

    assert outcome.ok is True
    assert len(captured) == 1, "exactly one pre-flight per navigation"
    policy = captured[0]
    assert policy == egress_policy_for(net_policy.BROWSE), (
        "the pre-flight must use the operator-layered BROWSE profile, not another profile "
        f"and not the bare base; got {policy!r}"
    )
    assert policy.name == "browse"
    assert policy.max_redirects == 10, "STRICT's 5 would mean the session fell back"
    assert policy.max_bytes == 50_000_000, "STRICT's 5 MB would mean the session fell back"
    assert policy.pin_resolved_ip is False, "BROWSE cannot pin; True would be another profile"


# ── the SEL row ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deny_writes_exactly_one_sel_row(sel_rows):
    transport = FakeTransport()
    session = await _started(transport)

    await session.navigate("https://denied.example/secret")

    assert len(sel_rows) == 1
    row = sel_rows[0]
    assert row.outcome == "denied"
    assert row.event_type == cdp.SEL_EVENT_TYPE
    assert row.operation == cdp.NAVIGATE
    assert row.metadata["host"] == "denied.example"
    assert "deny list" in row.metadata["reason"]
    assert row.metadata["phase"] == "preflight"
    assert row.timestamp.endswith("+00:00")


@pytest.mark.asyncio
async def test_allowed_navigation_writes_no_sel_row(sel_rows):
    """Vacuity floor for the row count: an allow is not audited as a denial."""
    transport = FakeTransport()
    session = await _started(transport)

    await session.navigate("https://allowed.example/page")

    assert sel_rows == []


@pytest.mark.asyncio
async def test_audit_failure_does_not_turn_a_deny_into_a_navigation(monkeypatch):
    """Fail OPEN on the audit, never on the decision."""

    class _Exploding:
        def log(self, event):
            raise OSError("audit log is unwritable")

    transport = FakeTransport()
    session = await _started(transport)
    monkeypatch.setattr(cdp, "SecurityEventLog", lambda: _Exploding())

    outcome = await session.navigate("https://denied.example/secret")

    assert transport.count(cdp.NAVIGATE) == 0
    assert outcome.allowed is False


# ── client-side redirects ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_frame_navigated_to_a_denied_host_tears_the_page_down(sel_rows):
    transport = FakeTransport()
    session = await _started(transport)
    await session.navigate("https://allowed.example/page")
    sel_rows.clear()

    # The page redirects itself somewhere the pre-flight never saw.
    await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert transport.methods[-2:] == [cdp.STOP_LOADING, cdp.NAVIGATE]
    assert transport.sent[-1] == (cdp.NAVIGATE, {"url": cdp.BLANK_URL})
    assert len(sel_rows) == 1
    assert sel_rows[0].operation == cdp.FRAME_NAVIGATED
    assert sel_rows[0].metadata["phase"] == "frame_navigated"
    assert session.blocks[-1].host == "denied.example"


@pytest.mark.asyncio
async def test_frame_navigated_to_an_allowed_host_is_left_alone(sel_rows):
    transport = FakeTransport()
    session = await _started(transport)
    await session.navigate("https://allowed.example/page")
    before = list(transport.methods)
    sel_rows.clear()

    await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://redirected.example/ok"))

    assert transport.methods == before, "an allowed redirect must not be torn down"
    assert sel_rows == []
    assert session.blocks == []


@pytest.mark.asyncio
async def test_the_teardown_navigation_is_not_re_judged():
    """The blank page the teardown navigates to reports itself; judging it would recurse."""
    transport = FakeTransport()
    session = await _started(transport)
    await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))
    after_teardown = list(transport.methods)

    await transport.listener(cdp.FRAME_NAVIGATED, _frame(cdp.BLANK_URL))
    await transport.listener(cdp.FRAME_NAVIGATED, _frame("chrome-error://chromewebdata/"))

    assert transport.methods == after_teardown
    assert len(session.blocks) == 1


@pytest.mark.asyncio
async def test_a_file_scheme_redirect_is_denied_not_ignored(sel_rows):
    """Only the teardown URLs are exempt — ``file://`` is judged like anything else."""
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()

    await transport.listener(cdp.FRAME_NAVIGATED, _frame("file:///etc/passwd"))

    assert transport.sent[-1] == (cdp.NAVIGATE, {"url": cdp.BLANK_URL})
    assert len(sel_rows) == 1
    assert "scheme" in sel_rows[0].metadata["reason"]
    assert session.blocks[-1].url == "file:///etc/passwd"


# ── the operator stays in control ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operator_deny_list_is_on_the_path(operator_egress, sel_rows):
    """``egress_policy_for`` is used, not the bare profile: an operator deny still bites."""
    transport = FakeTransport()
    session = await _started(transport)

    allowed_first = await session.navigate("https://operator-denied.example/a")
    assert allowed_first.ok is True, "base profile allows this host, so the test can discriminate"

    operator_egress.deny_hosts = ["operator-denied.example"]
    outcome = await session.navigate("https://operator-denied.example/b")

    assert outcome.allowed is False
    assert transport.count(cdp.NAVIGATE) == 1, "only the pre-config navigation reached the wire"
    assert sel_rows[-1].metadata["host"] == "operator-denied.example"


# ── fail closed, every way in ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_denied_host_stays_denied_when_the_config_read_fails(monkeypatch):
    """An unparseable operator config must not UN-deny a host, at this seam.

    ``egress_policy_for``'s best-effort catch is tested at the policy layer
    (``test_egress_deny_survives_a_config_error.py``); this asserts the consequence where it
    matters — the session still writes no ``Page.navigate``. A bare ``return base`` on the
    error path would silently make the denied host reachable through the browser.
    """
    transport = FakeTransport()
    session = await _started(transport)
    assert (await session.navigate("https://denied.example/secret")).allowed is False

    def _explode(_cls):
        raise OSError("config.json is unparseable")

    monkeypatch.setattr(AppConfig, "load", classmethod(_explode))

    outcome = await session.navigate("https://denied.example/secret")

    assert outcome.allowed is False, "a config-read error must not un-deny a denied host"
    assert "deny list" in outcome.reason
    assert transport.count(cdp.NAVIGATE) == 0


@pytest.mark.asyncio
async def test_the_residual_a_cold_start_has_no_denial_to_preserve(monkeypatch):
    """The documented RESIDUAL, asserted at the seam so it is visible rather than assumed.

    ``egress_policy_for`` preserves the last *observed* deny list. If the very first config
    read fails there is nothing observed, so the browse path runs on the bare BROWSE profile —
    public-only and scheme-gated, but without the operator's denials. That is a deliberate
    choice in ``net/policy.py`` (refusing all egress on a transient read would take the machine
    offline over a control that only ever ADDS denials), not an accident here.

    If that choice is ever revisited — a hard refusal, or a persisted deny list — this test is
    what should go red, and its expectation is what should be inverted. It exists to make that
    a decision rather than a discovery.
    """

    def _explode(_cls):
        raise OSError("config.json is unparseable")

    monkeypatch.setattr(AppConfig, "load", classmethod(_explode))
    monkeypatch.setattr(net_policy, "_LAST_DENY_HOSTS", ())
    transport = FakeTransport()
    session = await _started(transport)

    outcome = await session.navigate("https://denied.example/secret")

    assert outcome.allowed is True, (
        "the residual has changed: a cold-start config failure now preserves a denial, which "
        "is an improvement — invert this test rather than deleting it"
    )
    assert transport.count(cdp.NAVIGATE) == 1


@pytest.mark.asyncio
async def test_an_unparseable_url_is_denied_without_navigating(sel_rows):
    transport = FakeTransport()
    session = await _started(transport)

    outcome = await session.navigate("not a url at all")

    assert transport.count(cdp.NAVIGATE) == 0
    assert outcome.allowed is False
    assert len(sel_rows) == 1


@pytest.mark.asyncio
async def test_an_empty_url_is_denied_without_navigating():
    transport = FakeTransport()
    session = await _started(transport)

    outcome = await session.navigate("")

    assert transport.count(cdp.NAVIGATE) == 0
    assert outcome.allowed is False


@pytest.mark.asyncio
async def test_a_guard_exception_fails_closed(monkeypatch, sel_rows):
    transport = FakeTransport()
    session = await _started(transport)

    def _explode(*a, **kw):
        raise ValueError("guard blew up")

    monkeypatch.setattr(cdp, "evaluate", _explode)
    outcome = await session.navigate("https://allowed.example/page")

    assert transport.count(cdp.NAVIGATE) == 0
    assert outcome.allowed is False
    assert "guard raised" in outcome.reason
    assert len(sel_rows) == 1


@pytest.mark.asyncio
async def test_a_missing_browse_profile_fails_closed(monkeypatch):
    """A build without BROWSE must REFUSE, not fall back to STRICT.

    Also the vacuity partner for
    :func:`test_the_guard_is_called_with_the_real_browse_profile`: together they say the module
    reads that exact symbol and uses what it finds there.
    """
    transport = FakeTransport()
    session = await _started(transport)
    monkeypatch.delattr(net_policy, "BROWSE", raising=False)

    outcome = await session.navigate("https://allowed.example/page")

    assert transport.count(cdp.NAVIGATE) == 0
    assert outcome.allowed is False
    assert "BROWSE egress policy" in outcome.reason


@pytest.mark.asyncio
async def test_navigate_before_start_is_refused():
    transport = FakeTransport()
    session = _session(transport)

    outcome = await session.navigate("https://allowed.example/page")

    assert transport.sent == []
    assert outcome.allowed is False
    assert "never started" in outcome.reason


@pytest.mark.asyncio
async def test_a_failed_script_injection_leaves_a_session_that_cannot_navigate():
    transport = FakeTransport(fail_on={cdp.ADD_SCRIPT})
    session = _session(transport)

    with pytest.raises(cdp.CdpSessionError):
        await session.start()

    assert session.started is False
    outcome = await session.navigate("https://allowed.example/page")
    assert transport.count(cdp.NAVIGATE) == 0
    assert outcome.allowed is False


@pytest.mark.asyncio
async def test_a_missing_safety_script_module_fails_closed(monkeypatch):
    """The sibling module unimportable is setup failure, not a session that browses unguarded.

    Simulated by putting ``None`` in ``sys.modules``, which makes ``import`` raise
    ``ImportError``. It used to `delitem` the entry instead, which only simulated absence while
    ``safety_script.py`` genuinely did not exist on this branch — once the sibling landed, evicting
    the cache just re-imported it from disk and the test stopped exercising anything. The merge is
    what exposed that; on either branch alone it looked green.
    """
    monkeypatch.setitem(sys.modules, "personalclaw.browse.safety_script", None)
    transport = FakeTransport()
    session = _session(transport)

    with pytest.raises(cdp.CdpSessionError):
        await session.start()

    assert session.started is False
    assert (await session.navigate("https://allowed.example/page")).allowed is False
    assert transport.count(cdp.NAVIGATE) == 0


@pytest.mark.asyncio
async def test_a_transport_failure_is_not_reported_as_a_navigation():
    transport = FakeTransport(fail_on={cdp.NAVIGATE})
    session = await _started(transport)

    outcome = await session.navigate("https://allowed.example/page")

    assert outcome.ok is False, "a send that raised is not a navigation"
    assert outcome.allowed is True, "the gate did allow it; only the transport failed"
    assert transport.count(cdp.NAVIGATE) == 0
    assert session.quarantine_reason, "unknown browser state must quarantine the session"

    # And the quarantine holds: the session refuses even an allowed URL afterwards.
    transport._fail_on = set()
    again = await session.navigate("https://allowed.example/page")
    assert again.allowed is False and transport.count(cdp.NAVIGATE) == 0


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_unenforceable_redirect_block_quarantines_the_session():
    transport = FakeTransport(fail_on={cdp.STOP_LOADING})
    session = await _started(transport)

    await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert session.quarantine_reason
    transport._fail_on = set()
    outcome = await session.navigate("https://allowed.example/page")
    assert outcome.allowed is False
    # The one navigation on the wire is the quarantine's own attempt to blank the page; the
    # quarantined session sent nothing for the allowed URL.
    navigated = [params for method, params in transport.sent if method == cdp.NAVIGATE]
    assert navigated == [{"url": cdp.BLANK_URL}], navigated


@pytest.mark.asyncio
async def test_start_is_idempotent():
    transport = FakeTransport()
    session = await _started(transport)
    await session.start()

    assert transport.count(cdp.ADD_SCRIPT) == 1


# ── a page detached for a moment mid-teardown ─────────────────────────────────

#: The refusal Chrome sends while a page's session is detached, as the browser words it.
_DETACHED = (
    "the browser rejected the command: "
    "{'code': -32000, 'message': 'Not attached to an active page'}"
)


class _DetachedFor:
    """A transport whose page answers "Not attached" to the first *times* sends of *method*
    (``times=None``: every one), and records every send that was accepted."""

    def __init__(self, method: str, times: int | None, *, error: str = _DETACHED) -> None:
        self.inner = FakeTransport()
        self.method = method
        self.left = times
        self.error = error
        self.refused: list[str] = []

    async def send(self, method: str, params: dict | None = None) -> dict:
        if method == self.method and (self.left is None or self.left > 0):
            if self.left is not None:
                self.left -= 1
            self.refused.append(method)
            raise RuntimeError(self.error)
        return await self.inner.send(method, params)

    def set_event_listener(self, listener) -> None:
        self.inner.set_event_listener(listener)


class _Clock:
    """A fake clock that the fake sleep moves forward, so a bound is crossed without waiting."""

    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def _paced(transport) -> tuple[cdp.GatedCdpSession, _Clock]:
    clock = _Clock()
    session = cdp.GatedCdpSession(transport, resolver=_resolver, sleep=clock.sleep, clock=clock)
    await session.start()
    return session, clock


def _teardown_wire(transport: _DetachedFor) -> list[tuple[str, dict]]:
    return [(m, p) for m, p in transport.inner.sent if m in (cdp.STOP_LOADING, cdp.NAVIGATE)]


@pytest.mark.asyncio
async def test_a_stop_refused_once_while_the_page_is_detached_is_sent_again():
    """🔴 Red before: one "Not attached" on ``Page.stopLoading`` quarantined the session and
    sent nothing more, so the denied document stayed loaded (CI's browse-live red)."""
    transport = _DetachedFor(cdp.STOP_LOADING, 1)
    session = await _started(transport)  # the production pacing: one real 10 ms wait

    await transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert session.quarantine_reason == ""
    assert transport.refused == [cdp.STOP_LOADING]
    assert _teardown_wire(transport) == [
        (cdp.STOP_LOADING, {}),
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL}),
    ]


@pytest.mark.asyncio
async def test_a_blank_refused_once_while_the_page_is_detached_is_sent_again():
    """🔴 Red before: the same refusal on the ``about:blank`` navigation quarantined too."""
    transport = _DetachedFor(cdp.NAVIGATE, 1)
    session = await _started(transport)

    await transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert session.quarantine_reason == ""
    assert transport.refused == [cdp.NAVIGATE]
    assert _teardown_wire(transport) == [
        (cdp.STOP_LOADING, {}),
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL}),
    ]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_detached_past_the_bound_quarantines_and_is_blanked_once():
    """Fail closed: a page that stays detached past the bound quarantines the session. The
    resends were only ever ``Page.stopLoading``, and the page is sent to ``about:blank`` once."""
    transport = _DetachedFor(cdp.STOP_LOADING, None)
    session, clock = await _paced(transport)

    await transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert "stayed detached" in session.quarantine_reason
    assert set(transport.refused) == {cdp.STOP_LOADING}
    # Literals, not the constants: the documented 2 s bound at a 10 ms interval.
    assert len(transport.refused) == 201
    assert sum(clock.slept) == pytest.approx(2.0)
    assert set(clock.slept) == {0.01}
    assert _teardown_wire(transport) == [(cdp.NAVIGATE, {"url": cdp.BLANK_URL})]
    refused = await session.navigate("https://allowed.example/page")
    assert refused.allowed is False
    assert _teardown_wire(transport) == [(cdp.NAVIGATE, {"url": cdp.BLANK_URL})]


@pytest.mark.security
@pytest.mark.asyncio
async def test_any_other_teardown_refusal_quarantines_without_a_resend():
    """Fail closed: only "Not attached" is sent again. Any other refusal quarantines at once."""
    transport = _DetachedFor(cdp.STOP_LOADING, None, error="the CDP socket closed")
    session = await _started(transport)

    await transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert "the CDP socket closed" in session.quarantine_reason
    assert transport.refused == [cdp.STOP_LOADING], "a refusal other than detached is not resent"
    assert _teardown_wire(transport) == [
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL})
    ], "the quarantine still sends the page to about:blank once"


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_quarantined_session_refuses_every_read_and_action_on_its_page(tmp_path):
    """🔴 Red before: the page driver held the raw transport, so a session quarantined with the
    denied document still loaded went on handing its text, DOM, URL and screenshot to the agent
    and taking its clicks. Every page command now goes through the session and is refused."""
    from personalclaw.browse.extraction import ElementRef
    from personalclaw.browse.page import CdpPageDriver

    transport = _DetachedFor(cdp.STOP_LOADING, None, error="the CDP socket closed")
    session = await _started(transport)
    await transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))
    assert session.quarantine_reason
    page = CdpPageDriver(session.page_transport, screenshot_dir=tmp_path)
    before = list(transport.inner.sent)
    ref = ElementRef(ref="r1", role="link", label="next", target="/next")

    for read in (page.html, page.current_url, page.viewport, page.submit, page.go_back):
        with pytest.raises(cdp.CdpSessionError):
            await read()
    with pytest.raises(cdp.CdpSessionError):
        await page.click(ref)
    with pytest.raises(cdp.CdpSessionError):
        await page.fill(ref, "x")
    with pytest.raises(cdp.CdpSessionError):
        await page.scroll("down")
    with pytest.raises(cdp.CdpSessionError):
        await page.click_at(10.0, 10.0)
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Network.getCookies", {})
    assert await page.screenshot() == "", "a refused capture writes no file"
    assert list(tmp_path.iterdir()) == []
    assert transport.inner.sent == before, "nothing reached the page after the quarantine"


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_read_that_straddles_a_teardown_is_discarded():
    """A reply that comes back after a teardown began may have been read off the denied document,
    so it is refused rather than handed over, even when the teardown itself succeeds."""
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    page_reply = asyncio.get_running_loop().create_future()
    real_send = transport.send

    async def send(method, params=None):
        if method == "Runtime.evaluate":
            return await page_reply
        return await real_send(method, params)

    transport.send = send
    read = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {}))
    await asyncio.sleep(0)
    await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))
    page_reply.set_result({"result": {"value": "<html>denied</html>"}})

    with pytest.raises(cdp.CdpSessionError, match="discarded"):
        await read
    assert session.quarantine_reason == ""


@pytest.mark.asyncio
async def test_a_page_read_on_a_healthy_session_goes_through():
    """Vacuity partner: the session's page transport passes a read through when nothing is
    quarantined or tearing down."""
    transport = FakeTransport()
    session = await _started(transport)

    assert await session.page_transport.send("Runtime.evaluate", {"expression": "1"}) == {}
    assert transport.sent[-1] == ("Runtime.evaluate", {"expression": "1"})


@pytest.mark.security
def test_both_openers_hand_the_page_driver_the_sessions_page_transport():
    """The refusal holds only if the driver is built on it: neither opener may hand the driver
    the raw transport again."""
    import ast
    import inspect
    import textwrap

    from personalclaw.action_providers.browse_provider import BrowseActionProvider
    from personalclaw.browse import plan_runner

    for source in (
        inspect.getsource(BrowseActionProvider._open),
        inspect.getsource(plan_runner.make_gateway_opener),
    ):
        calls = [
            node
            for node in ast.walk(ast.parse(textwrap.dedent(source)))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "CdpPageDriver"
        ]
        assert len(calls) == 1, source
        first = calls[0].args[0]
        assert isinstance(first, ast.Attribute) and first.attr == "page_transport", ast.dump(first)


# ── the document a session inherits ──────────────────────────────────────────


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frames",
    [
        ("https://denied.example/secret",),
        ("https://allowed.example/page", "https://denied.example/frame"),
    ],
    ids=["denied-top-frame", "denied-child-frame"],
)
async def test_a_session_tears_down_a_denied_document_the_tab_already_holds(frames, sel_rows):
    """🔴 Red before: ``start`` never looked at the tab, and ``Page.enable`` does not replay the
    document it holds, so a fresh session read a denied page another session (or the page itself,
    after its session closed) left loaded."""
    transport = FakeTransport(frames=frames)
    session = await _started(transport)

    assert session.started and session.quarantine_reason == ""
    assert transport.methods == [
        cdp.PAGE_ENABLE,
        cdp.ADD_SCRIPT,
        cdp.GET_FRAME_TREE,
        cdp.STOP_LOADING,
        cdp.NAVIGATE,
    ]
    assert transport.sent[-1] == (cdp.NAVIGATE, {"url": cdp.BLANK_URL})
    assert [b.host for b in session.blocks] == ["denied.example"]
    assert [(r.operation, r.metadata["phase"]) for r in sel_rows] == [(cdp.GET_FRAME_TREE, "start")]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_session_that_cannot_tear_an_inherited_document_down_refuses_to_start():
    """Fail closed: the inherited denied document could not be torn down, so the session neither
    starts nor reads or navigates anything."""
    transport = FakeTransport(fail_on={cdp.STOP_LOADING}, frames=("https://denied.example/s",))
    session = _session(transport)

    with pytest.raises(cdp.CdpSessionError):
        await session.start()

    assert session.started is False and session.quarantine_reason
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})
    assert (await session.navigate("https://allowed.example/page")).allowed is False
    assert [p for m, p in transport.sent if m == cdp.NAVIGATE] == [{"url": cdp.BLANK_URL}]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_session_whose_frames_cannot_be_read_refuses_to_start():
    """Fail closed: a tab whose frames the browser does not report cannot be judged."""
    transport = FakeTransport(frames=None)
    session = _session(transport)

    with pytest.raises(cdp.CdpSessionError, match="frames could not be read"):
        await session.start()

    assert session.started is False
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})
    assert transport.count(cdp.NAVIGATE) == 0


@pytest.mark.asyncio
async def test_an_inherited_blank_or_error_page_is_left_alone():
    """Vacuity partner: the teardown URLs and Chrome's error page are not judged, as a redirect
    to them is not."""
    for frames in ((cdp.BLANK_URL,), ("chrome-error://chromewebdata/",), ("about:srcdoc",)):
        transport = FakeTransport(frames=frames)
        session = await _started(transport)
        assert transport.methods == [cdp.PAGE_ENABLE, cdp.ADD_SCRIPT, cdp.GET_FRAME_TREE]
        assert session.blocks == []


# ── a navigation that loads no document ─────────────────────────────────────


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [{"errorText": "net::ERR_ABORTED"}, {"isDownload": True}],
    ids=["error-text", "download"],
)
async def test_a_navigation_that_loads_no_document_is_not_ok(reply):
    """🔴 Red before: ``navigate`` threw the reply away and reported ok, so a 204, an abort or a
    download left the tab's previous document in place for the caller to read."""
    transport = FakeTransport()
    transport.replies[cdp.NAVIGATE] = reply
    session = await _started(transport)

    outcome = await session.navigate("https://allowed.example/page")

    assert outcome.ok is False and outcome.allowed is True
    assert outcome.error and "loaded no document" in outcome.reason
    assert session.quarantine_reason == "", "the navigation failed; the session is still sound"


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_browse_run_whose_first_navigation_loads_no_document_reads_nothing():
    """End to end: the browse loop parks on the not-ok outcome before any read or model call."""
    from personalclaw.browse.loop import PARK_NAVIGATION_BLOCKED, run_browse_loop
    from personalclaw.browse.page import CdpPageDriver

    transport = FakeTransport()
    transport.replies[cdp.NAVIGATE] = {"errorText": "net::ERR_ABORTED"}
    session = _session(transport)
    decided: list[str] = []

    async def decide(prompt: str) -> str:
        decided.append(prompt)
        return "DONE"

    result = await run_browse_loop(
        goal="read the page",
        start_url="https://allowed.example/page",
        session=session,
        page=CdpPageDriver(session.page_transport),
        decide=decide,
    )

    assert result.parked and result.park_reason == PARK_NAVIGATION_BLOCKED
    assert decided == []
    assert "Runtime.evaluate" not in transport.methods


# ── page commands and navigations held while a teardown runs ─────────────────


class _YieldingClock(_Clock):
    """A fake clock whose sleep also yields, so other tasks run while a teardown resends."""

    async def sleep(self, seconds: float) -> None:
        import asyncio

        await super().sleep(seconds)
        await asyncio.sleep(0)


async def _mid_teardown(detached: int | None):
    """A session tearing a denied redirect down, its Page.stopLoading refused as detached
    *detached* times (``None``: every time), with the teardown still running on return."""
    import asyncio

    transport = _DetachedFor(cdp.STOP_LOADING, detached)
    clock = _YieldingClock()
    session = cdp.GatedCdpSession(transport, resolver=_resolver, sleep=clock.sleep, clock=clock)
    await session.start()
    teardown = asyncio.ensure_future(
        transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))
    )
    for _ in range(3):
        await asyncio.sleep(0)
    assert not teardown.done() and transport.refused and session.tearing_down
    return transport, session, teardown


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_command_waits_for_a_running_teardown():
    """A command sent mid-teardown reaches the page only after it was blanked."""
    import asyncio

    transport, session, teardown = await _mid_teardown(50)
    act = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {"e": 1}))
    await asyncio.sleep(0)
    assert "Runtime.evaluate" not in transport.inner.methods

    await teardown
    assert await act == {}
    sent = transport.inner.sent
    assert sent.index(("Runtime.evaluate", {"e": 1})) > sent.index(
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL})
    )


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_command_and_a_navigation_held_through_a_failed_teardown_are_refused():
    """Fail closed: what was held while a teardown ran is refused when it quarantines."""
    import asyncio

    transport, session, teardown = await _mid_teardown(None)
    act = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {}))
    nav = asyncio.ensure_future(session.navigate("https://allowed.example/page"))

    await teardown
    with pytest.raises(cdp.CdpSessionError):
        await act
    outcome = await nav
    assert outcome.allowed is False and outcome.ok is False
    assert "Runtime.evaluate" not in transport.inner.methods
    assert [p for m, p in transport.inner.sent if m == cdp.NAVIGATE] == [{"url": cdp.BLANK_URL}]


class _BackToBack(FakeTransport):
    """The first Page.stopLoading waits on *release*; the second yields once, then fails."""

    def __init__(self) -> None:
        import asyncio

        super().__init__()
        self.release = asyncio.get_running_loop().create_future()
        self.stops = 0

    async def send(self, method: str, params: dict | None = None) -> dict:
        import asyncio

        if method == cdp.STOP_LOADING:
            self.stops += 1
            if self.stops == 1:
                await self.release
            else:
                await asyncio.sleep(0)
                raise RuntimeError("the CDP socket closed")
        return await super().send(method, params)


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_command_held_by_one_teardown_stays_held_through_a_back_to_back_second():
    """🔴 Red before: a held command waited once, so when a second denied event started a
    teardown the instant the first ended (the dispatcher hands them over without yielding),
    the command went to the page mid-teardown, and a held navigation went out and reported ok
    on a session that then quarantined."""
    import asyncio

    transport = _BackToBack()
    session = await _started(transport)

    async def dispatcher() -> None:
        await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/one"))
        await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/two"))

    events = asyncio.ensure_future(dispatcher())
    await asyncio.sleep(0)
    assert session.tearing_down
    act = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {}))
    nav = asyncio.ensure_future(session.navigate("https://allowed.example/page"))
    await asyncio.sleep(0)
    transport.release.set_result(None)
    await events

    assert "stayed detached" not in session.quarantine_reason and session.quarantine_reason
    with pytest.raises(cdp.CdpSessionError):
        await act
    assert (await nav).allowed is False
    assert "Runtime.evaluate" not in transport.methods
    assert {"url": "https://allowed.example/page"} not in [
        p for m, p in transport.sent if m == cdp.NAVIGATE
    ]


class _DispatcherDuringStart(FakeTransport):
    """A tab holding a denied document that also reports a denied commit while start() runs.

    Page.enable hands a denied Page.frameNavigated to a separate task, standing in for the
    transport's dispatcher. That task's Page.stopLoading waits on *release*, then fails."""

    def __init__(self) -> None:
        import asyncio

        super().__init__(frames=("https://denied.example/inherited",))
        self.release = asyncio.get_running_loop().create_future()
        self.dispatcher: asyncio.Task | None = None

    async def send(self, method: str, params: dict | None = None) -> dict:
        import asyncio

        if method == cdp.PAGE_ENABLE:
            self.dispatcher = asyncio.ensure_future(
                self.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/next"))
            )
            await asyncio.sleep(0)
        elif method == cdp.STOP_LOADING and asyncio.current_task() is self.dispatcher:
            await self.release
            raise RuntimeError("the CDP socket closed")
        return await super().send(method, params)


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_teardown_start_ends_does_not_lift_the_hold_while_the_dispatchers_still_runs():
    """🔴 Red before: one flag and one event tracked the teardowns, so start()'s teardown of the
    inherited document, ending first, lifted the hold while the dispatcher's teardown of the
    next denied commit still ran: a page read and a navigation went out mid-teardown, and the
    navigation reported ok on a session that then quarantined."""
    import asyncio

    transport = _DispatcherDuringStart()
    session = _session(transport)
    await session.start()

    assert session.tearing_down, "the dispatcher's teardown is still running"
    read = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {}))
    nav = asyncio.ensure_future(session.navigate("https://allowed.example/page"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert "Runtime.evaluate" not in transport.methods
    assert {"url": "https://allowed.example/page"} not in [
        p for m, p in transport.sent if m == cdp.NAVIGATE
    ]

    transport.release.set_result(None)
    await transport.dispatcher
    assert session.quarantine_reason
    with pytest.raises(cdp.CdpSessionError):
        await read
    outcome = await nav
    assert outcome.ok is False and outcome.allowed is False
    assert "Runtime.evaluate" not in transport.methods
    assert {"url": "https://allowed.example/page"} not in [
        p for m, p in transport.sent if m == cdp.NAVIGATE
    ]


class _CommitDuringStartsTeardown(FakeTransport):
    """A tab holding a denied document; while start()'s Page.stopLoading is in flight, the page
    reports a second denied commit, handed over on a separate task as the dispatcher would.

    Each command stays in flight for a few loop turns, as on a socket, so the two teardowns
    interleave on the wire: start()'s stopLoading, the dispatcher's stopLoading, start()'s
    blank, the dispatcher's blank. As the browser does, a blank that a later stopLoading or
    navigation supersedes is answered ``net::ERR_ABORTED``."""

    def __init__(self) -> None:
        super().__init__(frames=("https://denied.example/inherited",))
        self.dispatcher = None

    async def send(self, method: str, params: dict | None = None) -> dict:
        import asyncio

        if method == cdp.STOP_LOADING and self.dispatcher is None:
            self.dispatcher = asyncio.ensure_future(
                self.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/next"))
            )
        reply = await super().send(method, params)
        mark = len(self.sent)
        for _ in range(3):
            await asyncio.sleep(0)
        superseded = any(m in (cdp.STOP_LOADING, cdp.NAVIGATE) for m, _ in self.sent[mark:])
        if method == cdp.NAVIGATE and (params or {}).get("url") == cdp.BLANK_URL and superseded:
            return {"errorText": "net::ERR_ABORTED"}
        return reply


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_denied_commit_reported_during_starts_teardown_is_judged_and_torn_down(sel_rows):
    """🔴 Red before: a Page.frameNavigated that arrived while start()'s teardown ran was dropped
    unjudged, with no block, no SEL row and no teardown of its own. And once it was judged, the
    dispatcher's teardown superseded start()'s pending blank, the browser answered that blank
    net::ERR_ABORTED, and start() refused although the dispatcher's own blank landed."""
    transport = _CommitDuringStartsTeardown()
    session = _session(transport)
    await session.start()
    await transport.dispatcher

    assert transport.sent[3:] == [
        (cdp.STOP_LOADING, {}),
        (cdp.STOP_LOADING, {}),
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL}),
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL}),
    ], "the two teardowns interleave on the wire"
    assert [b.url for b in session.blocks] == [
        "https://denied.example/inherited",
        "https://denied.example/next",
    ]
    assert [r.metadata["phase"] for r in sel_rows] == ["start", "frame_navigated"]
    assert session.started and not session.tearing_down and session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_inherited_documents_blank_answered_with_error_text_refuses_start():
    """Fail closed, for start()'s teardown running alone: its blank answered with errorText
    loaded no document, so the inherited denied one may still be there, and start() refuses."""
    transport = FakeTransport(frames=("https://denied.example/inherited",))
    transport.replies[cdp.NAVIGATE] = {"errorText": "net::ERR_ABORTED"}
    session = _session(transport)

    with pytest.raises(cdp.CdpSessionError, match="loaded no document"):
        await session.start()

    assert session.started is False
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})
    assert "Runtime.evaluate" not in transport.methods


# ── the about:blank step, and the one shared 2 s deadline ────────────────────


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_blank_detached_past_the_bound_quarantines_and_refuses_the_page():
    """Fail closed: Page.stopLoading went through but about:blank stayed detached past the bound.
    stopLoading alone leaves the denied document loaded, so the session quarantines."""
    transport = _DetachedFor(cdp.NAVIGATE, None)
    session, clock = await _paced(transport)

    await transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert "stayed detached" in session.quarantine_reason
    assert cdp.NAVIGATE in session.quarantine_reason
    assert sum(clock.slept) == pytest.approx(2.0)
    assert set(transport.refused) == {cdp.NAVIGATE}
    assert _teardown_wire(transport) == [(cdp.STOP_LOADING, {})]
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})
    assert (await session.navigate("https://allowed.example/page")).allowed is False


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_blank_answered_with_error_text_quarantines_and_refuses_the_page():
    """🔴 Red before: the teardown threw the about:blank reply away, so a blank the browser
    answered with errorText (it loaded no document, and the denied one may still be there)
    counted as a blanked page, and the page was read."""
    transport = FakeTransport()
    session = await _started(transport)
    transport.replies[cdp.NAVIGATE] = {"errorText": "net::ERR_ABORTED"}

    await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert "loaded no document" in session.quarantine_reason
    assert [p for m, p in transport.sent if m == cdp.NAVIGATE] == [{"url": cdp.BLANK_URL}]
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})
    assert "Runtime.evaluate" not in transport.methods


class _HeldBlank(FakeTransport):
    """The teardown's about:blank navigation waits on *release* before it is answered."""

    def __init__(self) -> None:
        import asyncio

        super().__init__()
        self.release = asyncio.get_running_loop().create_future()

    async def send(self, method: str, params: dict | None = None) -> dict:
        if method == cdp.NAVIGATE and (params or {}).get("url") == cdp.BLANK_URL:
            self.sent.append((method, dict(params or {})))
            await self.release
            return {}
        return await super().send(method, params)


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_command_is_held_until_the_blank_is_answered():
    """The hold lasts through the about:blank step, not only through Page.stopLoading:
    stopLoading leaves the denied document loaded until the blank replaces it."""
    import asyncio

    transport = _HeldBlank()
    session = await _started(transport)
    teardown = asyncio.ensure_future(
        transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))
    )
    for _ in range(3):
        await asyncio.sleep(0)
    assert transport.sent[-1] == (cdp.NAVIGATE, {"url": cdp.BLANK_URL})
    act = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {}))
    for _ in range(3):
        await asyncio.sleep(0)
    assert session.tearing_down
    assert "Runtime.evaluate" not in transport.methods

    transport.release.set_result(None)
    await teardown
    assert await act == {}
    assert transport.methods[-1] == "Runtime.evaluate"


class _DetachedUntil:
    """A page that answers "Not attached" to each teardown step until a time on *clock*."""

    def __init__(self, clock: _Clock, until: dict[str, float]) -> None:
        self.inner = FakeTransport()
        self.clock = clock
        self.until = until
        self.refused: list[str] = []

    async def send(self, method: str, params: dict | None = None) -> dict:
        if self.clock() < self.until.get(method, 0.0):
            self.refused.append(method)
            raise RuntimeError(_DETACHED)
        return await self.inner.send(method, params)

    def set_event_listener(self, listener) -> None:
        self.inner.set_event_listener(listener)


@pytest.mark.security
@pytest.mark.asyncio
async def test_both_teardown_steps_share_one_2s_deadline():
    """Fail closed within the documented 2 s for the whole teardown: the time spent resending
    Page.stopLoading is not given back to the blank."""
    clock = _Clock()
    transport = _DetachedUntil(clock, {cdp.STOP_LOADING: 101.5, cdp.NAVIGATE: 103.0})
    session = cdp.GatedCdpSession(transport, resolver=_resolver, sleep=clock.sleep, clock=clock)
    await session.start()

    await transport.inner.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert "stayed detached" in session.quarantine_reason
    assert cdp.NAVIGATE in transport.refused
    assert sum(clock.slept) == pytest.approx(2.0)  # literal: the documented bound, shared
    assert set(clock.slept) == {0.01}  # literal: the documented 10 ms interval


# ── each opener's driver answers to the session it returns ───────────────────


class _ClosableFake(FakeTransport):
    async def close(self) -> None:
        return None


async def _plan_runner_open():
    from personalclaw.browse import plan_runner

    return await plan_runner.make_gateway_opener()("ws://fake/1")


async def _provider_open():
    from personalclaw.action_providers.browse_provider import BrowseActionProvider

    return await BrowseActionProvider()._open(
        {}, types.SimpleNamespace(event="t"), cdp_url="ws://fake/1"
    )


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "open_", [_plan_runner_open, _provider_open], ids=["plan_runner", "browse_provider"]
)
async def test_each_openers_driver_is_closed_by_the_session_it_returns(monkeypatch, open_):
    """The driver each opener returns refuses once the session it returns is quarantined:
    built on another session's transport, or the raw one, it would still read the page."""
    from personalclaw.browse import transport as transport_mod

    fake = _ClosableFake(fail_on={cdp.STOP_LOADING})

    async def _connect(*_a, **_k):
        return fake

    monkeypatch.setattr(transport_mod.WebSocketCdpTransport, "connect", staticmethod(_connect))
    session, driver, _close = await open_()
    await session.start()
    await fake.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))
    assert session.quarantine_reason
    before = list(fake.sent)
    with pytest.raises(cdp.CdpSessionError):
        await driver.html()
    with pytest.raises(cdp.CdpSessionError):
        await driver.current_url()
    assert fake.sent == before
