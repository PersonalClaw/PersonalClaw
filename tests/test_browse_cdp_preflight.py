"""BA-2: every CDP navigation is pre-flighted through the egress guard.

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


class FakeTransport:
    """Records every CDP message instead of sending it to a browser."""

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.listener = None
        self._fail_on = fail_on or set()

    async def send(self, method: str, params: dict | None = None) -> dict:
        if method in self._fail_on:
            # Nothing is recorded: a send that raised never reached the wire.
            raise RuntimeError(f"transport is down for {method}")
        self.sent.append((method, dict(params or {})))
        return {}

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


@pytest.mark.asyncio
async def test_an_unenforceable_redirect_block_quarantines_the_session():
    transport = FakeTransport(fail_on={cdp.STOP_LOADING})
    session = await _started(transport)

    await transport.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))

    assert session.quarantine_reason
    transport._fail_on = set()
    outcome = await session.navigate("https://allowed.example/page")
    assert outcome.allowed is False
    assert transport.count(cdp.NAVIGATE) == 0


@pytest.mark.asyncio
async def test_start_is_idempotent():
    transport = FakeTransport()
    session = await _started(transport)
    await session.start()

    assert transport.count(cdp.ADD_SCRIPT) == 1
