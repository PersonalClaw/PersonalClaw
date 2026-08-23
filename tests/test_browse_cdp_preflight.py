"""BA-2: every CDP navigation is pre-flighted through the egress guard.

The clause under test is an ORDERING clause — "a denied host is blocked before
``Page.navigate`` fires" — so almost every assertion here is about the CDP messages
recorded on the fake transport, not about a return value. A gate that decides after the
message is on the wire returns exactly the same object as one that decides before it; only
the wire tells them apart.

The fake transport and the fake resolver together mean no browser is launched and no DNS
query is made. ``SecurityEventLog`` is replaced for every test, so nothing writes to a real
audit log, and ``AppConfig.load`` is replaced, so nothing reads the real home.

``net.policy.BROWSE`` is a sibling branch's symbol (it does not exist here yet), so the
autouse ``browse_profile`` fixture installs a stand-in with ``raising=False`` — pytest
removes it again after each test, so nothing lingers once the real profile lands. That
stand-in doubles as the discriminator for "did the session actually use BROWSE?": its
``deny_hosts`` entry resolves to a perfectly public IP under the fake resolver, so a
session that reached for STRICT (or any other profile) instead would ALLOW it and
:func:`test_denied_host_sends_zero_page_navigate` would go red.
"""

import sys
import types
from dataclasses import dataclass, field

import pytest

from personalclaw.browse import cdp
from personalclaw.config.loader import AppConfig
from personalclaw.net import policy as net_policy
from personalclaw.net.policy import EgressPolicy

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
    """The operator's ``security.egress`` block, faked so no real config is read."""
    cfg = _FakeEgressConfig()
    fake_app = types.SimpleNamespace(security=types.SimpleNamespace(egress=cfg))
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: fake_app))
    return cfg


@pytest.fixture(autouse=True)
def _no_real_config(operator_egress):
    """Autouse so no test in this module reaches ``AppConfig.load()``'s real home."""
    return operator_egress


@pytest.fixture(autouse=True)
def browse_profile(monkeypatch):
    """The sibling's BROWSE profile, stood in for until it lands in ``net/policy.py``."""
    base = EgressPolicy(name="browse", deny_hosts=("denied.example",))
    monkeypatch.setattr(net_policy, "BROWSE", base, raising=False)
    return base


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
    """Before the sibling's BROWSE lands, browsing must refuse — not fall back to STRICT."""
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
