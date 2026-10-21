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
    "secret.allowed.example": ["93.184.216.38"],
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
        # Commands on a frame's or a worker's own session: (sid, method, params).
        self.child_sent: list[tuple[str, str, dict]] = []
        self.child_replies: dict[tuple[str, str], dict] = {}
        self.child_fail: dict[tuple[str, str], str] = {}
        self.child_hold: dict[tuple[str, str], object] = {}
        # A frame's own blank (``Page.navigate`` with a ``frameId``) is answered apart from the
        # whole page's: ``frame_fail`` refuses the next ones in order, ``frame_hold`` holds one.
        self.frame_reply: dict = {}
        self.frame_fail: list[str] = []
        self.frame_hold = None
        self.refused: list[tuple[str, dict]] = []
        # Events the browser reports before it answers a command: method -> [(m, params, sid)].
        self.events_on: dict[str, list[tuple[str, dict, str | None]]] = {}
        # Events handed over on tasks of their own, as the transport's dispatcher does.
        self._dispatching: list = []
        # A page command whose reply waits on a future: method -> future (used once).
        self.page_hold: dict[str, object] = {}

    async def send(
        self, method: str, params: dict | None = None, *, session_id: str | None = None
    ) -> dict:
        params = dict(params or {})
        if session_id is not None:
            key = (session_id, method)
            self.child_sent.append((session_id, method, params))
            if key in self.child_hold:
                await self.child_hold[key]
            if key in self.child_fail:
                raise RuntimeError(self.child_fail[key])
            return dict(self.child_replies.get(key, {}))
        if method in self._fail_on:
            # Nothing is recorded: a send that raised never reached the wire.
            raise RuntimeError(f"transport is down for {method}")
        if method == cdp.NAVIGATE and "frameId" in params:
            if self.frame_fail:
                self.refused.append((method, params))
                raise RuntimeError(self.frame_fail.pop(0))
            self.sent.append((method, params))
            if self.frame_hold is not None:
                hold, self.frame_hold = self.frame_hold, None
                await hold
            return dict(self.frame_reply)
        self.sent.append((method, params))
        if method in self.page_hold:
            await self.page_hold.pop(method)
        for event in self.events_on.pop(method, ()):
            await self.listener(*event)
        if method == cdp.GET_FRAME_TREE:
            # ``frames=None``: the browser answers no frame tree at all; a dict is the reply.
            if isinstance(self.frames, dict):
                return self.frames
            return _tree(*self.frames) if self.frames is not None else {}
        return dict(self.replies.get(method, {}))

    def set_event_listener(self, listener) -> None:
        self.listener = listener

    def dispatch(self, method: str, params: dict, sid: str | None = None):
        """Hand one event to the listener on a task of its own, as the dispatcher does."""
        import asyncio

        task = asyncio.ensure_future(self.listener(method, params, sid))
        self._dispatching.append(task)
        return task

    async def drain_events(self) -> None:
        """Every event handed over so far has been handled (only those :meth:`dispatch` gave)."""
        import asyncio

        pending = [t for t in self._dispatching if not t.done()]
        if pending:
            await asyncio.wait(pending)

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
    cfg = _FakeEgressConfig(deny_hosts=["denied.example", "secret.allowed.example"])
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


async def _inline(fn, *args):
    """Runs a judgement on the loop, so a test's fixed-turn orderings stay deterministic."""
    return fn(*args)


def _session(transport: FakeTransport, **kwargs) -> cdp.GatedCdpSession:
    return cdp.GatedCdpSession(transport, resolver=_resolver, blocking=_inline, **kwargs)


async def _started(transport: FakeTransport) -> cdp.GatedCdpSession:
    session = _session(transport)
    await session.start()
    return session


def _frame(url: str) -> dict:
    return {"frame": {"id": "FRAME1", "url": url}}


# ── the ordering clause ───────────────────────────────────────────────────────


@pytest.mark.security
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


@pytest.mark.security
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


@pytest.mark.security
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


@pytest.mark.security
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


@pytest.mark.security
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


@pytest.mark.security
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


@pytest.mark.security
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

    async def send(
        self, method: str, params: dict | None = None, *, session_id: str | None = None
    ) -> dict:
        if session_id is None and method == self.method and (self.left is None or self.left > 0):
            if self.left is not None:
                self.left -= 1
            self.refused.append(method)
            raise RuntimeError(self.error)
        return await self.inner.send(method, params, session_id=session_id)

    def set_event_listener(self, listener) -> None:
        self.inner.set_event_listener(listener)

    async def drain_events(self) -> None:
        await self.inner.drain_events()


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
    session = _session(transport, sleep=clock.sleep, clock=clock)
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

    with pytest.raises(cdp.PageReplyDiscarded, match="discarded"):
        await read
    assert session.quarantine_reason == ""
    assert not issubclass(
        cdp.PageReplyDiscarded, cdp.CdpSessionError
    ), "a discarded reply on a sound session is not the session being unusable"


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
    [("https://denied.example/secret",)],
    ids=["denied-top-frame"],
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
        cdp.SET_AUTO_ATTACH,
        cdp.GET_FRAME_TREE,
        cdp.STOP_LOADING,
        cdp.NAVIGATE,
    ]
    assert transport.sent[-1] == (cdp.NAVIGATE, {"url": cdp.BLANK_URL})
    assert [b.host for b in session.blocks] == ["denied.example"]
    assert [(r.operation, r.metadata["phase"]) for r in sel_rows] == [(cdp.GET_FRAME_TREE, "start")]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_denied_frame_the_tab_already_holds_is_blanked_alone(sel_rows):
    """🔴 Red before: a denied child frame tore the whole page down, so the allowed page that
    embedded it was lost with it. Now that one frame is blanked, by its id, and nothing else."""
    transport = FakeTransport(frames=("https://allowed.example/page", "https://denied.example/f"))
    session = await _started(transport)

    assert session.started and session.quarantine_reason == ""
    assert transport.sent[-1] == (cdp.NAVIGATE, {"url": cdp.BLANK_URL, "frameId": "CHILD0"})
    assert cdp.STOP_LOADING not in transport.methods
    assert (cdp.NAVIGATE, {"url": cdp.BLANK_URL}) not in transport.sent
    assert [b.host for b in session.blocks] == ["denied.example"]
    assert session.blocks[0].frame is True
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
        assert transport.methods == [
            cdp.PAGE_ENABLE,
            cdp.ADD_SCRIPT,
            cdp.SET_AUTO_ATTACH,
            cdp.GET_FRAME_TREE,
        ]
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
    """End to end: the browse loop stops on the not-ok outcome before any read or model call, as
    a run whose start URL loaded no page, not as an egress block: the gate allowed it."""
    from personalclaw.browse.loop import run_browse_loop
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

    assert result.ok is False and not result.parked
    assert result.error == "the start URL loaded no page: net::ERR_ABORTED"
    assert result.blocked_urls == ()
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
    session = _session(transport, sleep=clock.sleep, clock=clock)
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

    assert transport.sent[4:] == [
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

    async def send(
        self, method: str, params: dict | None = None, *, session_id: str | None = None
    ) -> dict:
        if session_id is None and self.clock() < self.until.get(method, 0.0):
            self.refused.append(method)
            raise RuntimeError(_DETACHED)
        return await self.inner.send(method, params, session_id=session_id)

    def set_event_listener(self, listener) -> None:
        self.inner.set_event_listener(listener)

    async def drain_events(self) -> None:
        await self.inner.drain_events()


@pytest.mark.security
@pytest.mark.asyncio
async def test_both_teardown_steps_share_one_2s_deadline():
    """Fail closed within the documented 2 s for the whole teardown: the time spent resending
    Page.stopLoading is not given back to the blank."""
    clock = _Clock()
    transport = _DetachedUntil(clock, {cdp.STOP_LOADING: 101.5, cdp.NAVIGATE: 103.0})
    session = _session(transport, sleep=clock.sleep, clock=clock)
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


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "open_", [_plan_runner_open, _provider_open], ids=["plan_runner", "browse_provider"]
)
async def test_each_closer_closes_the_socket_however_ending_the_sessions_work_ends(
    monkeypatch, open_
):
    """The closer ends the session's own work before the socket closes; if that fails, or the
    closer is cancelled meanwhile, the socket is still closed rather than left open."""
    from personalclaw.browse import transport as transport_mod

    closed: list[bool] = []

    class _Tracked(_ClosableFake):
        async def close(self) -> None:
            closed.append(True)

    async def _connect(*_a, **_k):
        return _Tracked()

    async def _fails(self) -> None:
        raise RuntimeError("the session's work could not be ended")

    monkeypatch.setattr(transport_mod.WebSocketCdpTransport, "connect", staticmethod(_connect))
    monkeypatch.setattr(cdp.GatedCdpSession, "close", _fails)
    _session, _driver, close = await open_()

    with pytest.raises(RuntimeError, match="could not be ended"):
        await close()
    assert closed == [True]


# ── a reply discarded on a sound session is read once more ───────────────────


class _RedirectDuringRead(FakeTransport):
    """The page redirects itself to a denied host while the first *straddled* reads are in flight,
    so each of those replies straddles a teardown. ``stop_fails`` makes that teardown quarantine."""

    def __init__(self, *, straddled: int = 1, stop_fails: bool = False) -> None:
        super().__init__()
        self.straddled = straddled
        self.stop_fails = stop_fails

    async def send(self, method: str, params: dict | None = None) -> dict:
        if method == cdp.STOP_LOADING and self.stop_fails:
            raise RuntimeError("the CDP socket closed")
        reply = await super().send(method, params)
        if method == "Runtime.evaluate" and self.straddled > 0:
            self.straddled -= 1
            await self.listener(cdp.FRAME_NAVIGATED, _frame("https://denied.example/evil"))
        return reply


async def _browse(transport: FakeTransport):
    from personalclaw.browse.loop import run_browse_loop
    from personalclaw.browse.page import CdpPageDriver

    session = _session(transport)

    async def decide(prompt: str) -> str:
        return "DONE"

    result = await run_browse_loop(
        goal="read the page",
        start_url="https://allowed.example/page",
        session=session,
        page=CdpPageDriver(session.page_transport),
        decide=decide,
    )
    return session, result


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_read_discarded_by_a_successful_teardown_is_read_again_and_the_run_goes_on():
    """🔴 Red before: the read's reply straddled the teardown of a denied redirect, which
    succeeded, and was discarded as if the session were unusable, so a healthy run ended with
    "the page could not be read". The read is sent once more, after the page was blanked."""
    transport = _RedirectDuringRead(straddled=1)
    session, result = await _browse(transport)

    assert session.quarantine_reason == ""
    assert result.ok is True and result.error == ""
    evaluates = [i for i, (m, _) in enumerate(transport.sent) if m == "Runtime.evaluate"]
    blank = transport.sent.index((cdp.NAVIGATE, {"url": cdp.BLANK_URL}))
    assert len(evaluates) == 3, "the discarded read, then the page's HTML and URL once more"
    assert evaluates[1] > blank, "the second read reached the page only after it was blanked"


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_read_discarded_twice_ends_the_run_and_is_not_read_a_third_time():
    """Fail closed: one more read only. A page that redirects itself to a denied host during
    every read ends the run rather than be read in a loop."""
    transport = _RedirectDuringRead(straddled=10)
    _session_, result = await _browse(transport)

    assert result.ok is False and "the page could not be read" in result.error
    assert transport.count("Runtime.evaluate") == 2


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_read_discarded_by_a_teardown_that_quarantined_is_not_read_again():
    """Fail closed: when the teardown quarantined the session, the denied page may still be
    loaded, so the discarded read is not sent again and the run ends."""
    transport = _RedirectDuringRead(straddled=1, stop_fails=True)
    session, result = await _browse(transport)

    assert session.quarantine_reason
    assert result.ok is False and "the page could not be read" in result.error
    assert "reply was discarded" in result.error, "the run reports the discard, not a retry"
    assert transport.count("Runtime.evaluate") == 1


# ── every frame of the page is judged ────────────────────────────────────────

DENIED = "https://denied.example/frame"
ALLOWED = "https://allowed.example/frame"
#: A frame the page's own site serves from a denied host, on a page at :data:`PAGE`: the frame
#: that is blanked as its navigation starts. One from another site waits for its attach or commit.
SAME_SITE_DENIED = "https://secret.allowed.example/frame"
PAGE = "https://allowed.example/page"


def _attached(sid, tid, url="", *, waiting=True, kind="iframe", subtype=""):
    """A ``Target.attachedToTarget`` the browser reports for a frame or worker it started."""
    info = {"targetId": tid, "type": kind, "url": url}
    if subtype:
        info["subtype"] = subtype
    return {"sessionId": sid, "targetInfo": info, "waitingForDebugger": waiting}


def _started_nav(fid: str, url: str) -> dict:
    return {"frameId": fid, "url": url, "loaderId": "L1", "navigationType": "differentDocument"}


def _requested_nav(fid: str, url: str, disposition: str = "currentTab") -> dict:
    return {"frameId": fid, "url": url, "reason": "scriptInitiated", "disposition": disposition}


def _info(tid: str, url: str, kind: str = "iframe") -> dict:
    return {"targetInfo": {"targetId": tid, "type": kind, "url": url}}


def _child_frame(url: str, fid: str = "CHILD1", parent: str = "TOP") -> dict:
    return {"frame": {"id": fid, "parentId": parent, "url": url}}


def _restore(url: str = "https://allowed.example/page") -> dict:
    return {"frame": {"id": "TOP", "url": url}, "type": "BackForwardCacheRestore"}


def _frame_blanks(transport) -> list[str]:
    """The frame ids a frame's own blank was attempted for, sent or refused, in order."""
    wire = [(i, m, p) for i, (m, p) in enumerate(transport.sent)]
    tried = [p["frameId"] for _, m, p in wire if m == cdp.NAVIGATE and "frameId" in p]
    return tried + [p["frameId"] for m, p in transport.refused if "frameId" in p]


def _top_blanks(transport) -> int:
    return transport.sent.count((cdp.NAVIGATE, {"url": cdp.BLANK_URL}))


def _sel(rows) -> list[tuple[str, str]]:
    return [(r.operation, r.metadata["phase"]) for r in rows]


def _resumed(transport) -> list[str]:
    return [sid for sid, m, _ in transport.child_sent if m == cdp.RUN_IF_WAITING]


async def _events(session, transport, *events) -> None:
    """Hand each ``(method, params, sid)`` over as the dispatcher would, then let every task the
    session started for them finish."""
    for event in events:
        await transport.dispatch(*event)
    await session.wait_idle()


# start and discovery


@pytest.mark.security
@pytest.mark.asyncio
async def test_start_watches_every_frame_and_worker_but_the_owners_service_workers():
    """🔴 Red before: start never asked the browser to report the frames it starts, so a frame
    the browser runs in a process of its own was never seen at all."""
    transport = FakeTransport()
    await _started(transport)

    assert transport.methods == [
        cdp.PAGE_ENABLE,
        cdp.ADD_SCRIPT,
        cdp.SET_AUTO_ATTACH,
        cdp.GET_FRAME_TREE,
    ]
    assert transport.sent[2] == (cdp.SET_AUTO_ATTACH, cdp.AUTO_ATTACH)
    assert cdp.AUTO_ATTACH == {
        "autoAttach": True,
        "waitForDebuggerOnStart": True,
        "flatten": True,
        "filter": [
            {"type": "service_worker", "exclude": True},
            {"type": "browser", "exclude": True},
            {"type": "tab", "exclude": True},
            {},
        ],
    }


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_session_that_cannot_watch_the_frames_refuses_to_start():
    transport = FakeTransport(fail_on={cdp.SET_AUTO_ATTACH})
    session = _session(transport)

    with pytest.raises(cdp.CdpSessionError, match="frames could not be watched"):
        await session.start()

    assert (await session.navigate("https://allowed.example/page")).allowed is False
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})


@pytest.mark.security
@pytest.mark.asyncio
async def test_two_denied_frames_the_tab_holds_are_each_blanked_by_id():
    transport = FakeTransport(
        frames=("https://allowed.example/page", DENIED, ALLOWED, "https://denied.example/two")
    )
    session = await _started(transport)

    assert _frame_blanks(transport) == ["CHILD0", "CHILD2"]
    assert cdp.STOP_LOADING not in transport.methods and _top_blanks(transport) == 0
    assert session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_denied_frame_in_a_process_of_its_own_is_blanked_before_start_ends(sel_rows):
    """🔴 Red before: a frame the browser runs in its own process is not in the page's frame tree
    as the page's process reports it, so start judged the allowed page and left the frame showing.
    """
    transport = FakeTransport()
    transport.events_on[cdp.SET_AUTO_ATTACH] = [
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "OOP1", DENIED, waiting=False), None)
    ]
    session = await _started(transport)

    blank = transport.sent.index((cdp.NAVIGATE, {"url": cdp.BLANK_URL, "frameId": "OOP1"}))
    assert blank < transport.methods.index(cdp.GET_FRAME_TREE)
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "frame_attached")]
    assert transport.child_sent == [] and session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_allowed_frame_in_its_own_process_is_watched_and_its_frames_judged():
    """The frame is armed in order (its page events, then its own frames reported), then its
    frames are read: a denied one is blanked by id, and one still on its first empty document
    (url ``""``) is not judged, because nothing of it has loaded."""
    transport = FakeTransport()
    transport.events_on[cdp.SET_AUTO_ATTACH] = [
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "OOP1", ALLOWED, waiting=False), None)
    ]
    transport.child_replies[("S1", cdp.GET_FRAME_TREE)] = {
        "frameTree": {
            "frame": {"id": "OOP1", "url": ALLOWED},
            "childFrames": [
                {"frame": {"id": "N1", "url": DENIED}},
                {"frame": {"id": "N2", "url": ""}},
            ],
        }
    }
    session = await _started(transport)
    await session.wait_idle()

    assert [(s, m) for s, m, _ in transport.child_sent] == [
        ("S1", cdp.PAGE_ENABLE),
        ("S1", cdp.SET_AUTO_ATTACH),
        ("S1", cdp.GET_FRAME_TREE),
    ]
    assert _frame_blanks(transport) == ["N1"]
    assert _resumed(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_that_cannot_be_judged_in_time_at_start_blanks_the_page_not_the_session(
    sel_rows,
):
    """Fail closed without ending the run: the frame's renderer does not answer, so the page it
    is in is blanked whole within the bound, and the session still starts."""
    import asyncio

    transport = FakeTransport()
    transport.events_on[cdp.SET_AUTO_ATTACH] = [
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "OOP1", ALLOWED, waiting=False), None)
    ]
    held = asyncio.get_running_loop().create_future()
    transport.child_hold[("S1", cdp.PAGE_ENABLE)] = held
    session = _session(transport, frame_start_bound=0.05)
    await session.start()

    assert session.started and session.quarantine_reason == ""
    assert transport.methods[3:5] == [cdp.STOP_LOADING, cdp.NAVIGATE]
    assert _top_blanks(transport) == 1
    assert (cdp.SET_AUTO_ATTACH, "start") in _sel(sel_rows)
    held.set_result(None)
    await session.wait_idle()


class _Readings:
    """A clock that answers *readings* in turn, then the last one from then on."""

    def __init__(self, *readings: float) -> None:
        self._readings = list(readings)
        self.last = self._readings[0]

    def __call__(self) -> float:
        self.last = self._readings.pop(0) if len(self._readings) > 1 else self._readings[0]
        return self.last


@pytest.mark.security
@pytest.mark.asyncio
async def test_start_blanks_a_page_left_unjudged_two_seconds_on_the_sessions_clock(sel_rows):
    """🔴 Red before: start's bound was read off the wall clock, not the session's. It is 2 s on
    the session's clock: the page is blanked once that clock passes 2 s from the start of the
    wait, and not before."""
    import asyncio

    clock = _Readings(100.0, 101.99, 101.99, 102.01)

    class _Blanks(FakeTransport):
        blanked_at = None

        async def send(self, method, params=None, *, session_id=None):
            if method == cdp.STOP_LOADING:
                self.blanked_at = clock.last
            return await super().send(method, params, session_id=session_id)

    transport = _Blanks()
    transport.events_on[cdp.SET_AUTO_ATTACH] = [
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "OOP1", ALLOWED, waiting=False), None)
    ]
    transport.child_replies[("S1", cdp.GET_FRAME_TREE)] = {
        "frameTree": {"frame": {"id": "OOP1", "url": ALLOWED}}
    }
    held = asyncio.get_running_loop().create_future()
    transport.child_hold[("S1", cdp.PAGE_ENABLE)] = held
    session = _session(transport, clock=clock)
    try:
        await asyncio.wait_for(session.start(), 1.0)
    finally:
        held.set_result(None)

    assert transport.blanked_at is not None and transport.blanked_at >= 102.0
    assert session.started and _top_blanks(transport) == 1
    assert (cdp.SET_AUTO_ATTACH, "start") in _sel(sel_rows)
    await session.wait_idle()


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_session_whose_events_cannot_be_drained_at_start_refuses_to_start():
    """Fail closed: start cannot tell whether the frames already running were judged, so the
    session neither starts nor reads or navigates anything."""

    class _Undrainable(FakeTransport):
        async def drain_events(self) -> None:
            if cdp.SET_AUTO_ATTACH in self.methods:
                raise RuntimeError("the CDP transport is closed")
            await super().drain_events()

    transport = _Undrainable()
    session = _session(transport)

    with pytest.raises(cdp.CdpSessionError, match="frames could not be watched"):
        await session.start()

    assert session.started is False
    assert (await session.navigate("https://allowed.example/page")).allowed is False
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})
    assert transport.count(cdp.NAVIGATE) == 0


# where a paused frame was going


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_paused_before_its_document_loads_is_judged_by_where_it_was_going(sel_rows):
    """🔴 Red before: the frame loaded unjudged. It is blanked when it attaches, never resumed,
    recorded once, and a read in flight is returned: nothing of the frame was ever shown."""
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    held = asyncio.get_running_loop().create_future()
    transport.page_hold["Runtime.evaluate"] = held
    read = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {}))
    await asyncio.sleep(0)

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", DENIED), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None),
    )
    held.set_result({"ok": 1})

    assert await read == {}
    assert _frame_blanks(transport) == ["F1"]
    assert transport.child_sent == []
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "frame_attached")]
    assert len(session.blocks) == 1 and session.blocks[0].frame is True


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_navigation_the_page_requested_in_the_frame_names_where_it_goes():
    """``frameRequestedNavigation`` names the URL when it is for the frame itself; one opened in a
    new tab is not this frame's, so its frame attaches with no URL known and is blanked."""
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.FRAME_REQUESTED_NAVIGATION, _requested_nav("F1", ALLOWED), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None),
        (cdp.FRAME_REQUESTED_NAVIGATION, _requested_nav("F2", ALLOWED, "newTab"), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S2", "F2"), None),
    )

    assert _resumed(transport) == ["S1"]
    assert _frame_blanks(transport) == ["F2"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_paused_frame_whose_destination_is_unknown_is_blanked_not_resumed():
    transport = FakeTransport()
    session = await _started(transport)
    await _events(session, transport, (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None))

    assert _frame_blanks(transport) == ["F1"]
    assert "did not say" in session.blocks[-1].reason
    assert _resumed(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_allowed_paused_frame_is_watched_first_then_let_load():
    """Its page events before its own frames are reported: the other order drops the start event
    of a nested frame that begins between the two, and that frame is then blanked for nothing."""
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", ALLOWED), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None),
    )

    assert transport.child_sent == [
        ("S1", cdp.PAGE_ENABLE, {}),
        ("S1", cdp.SET_AUTO_ATTACH, cdp.AUTO_ATTACH),
        ("S1", cdp.RUN_IF_WAITING, {}),
    ]
    assert _frame_blanks(transport) == [] and session.blocks == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_nested_in_a_frame_is_driven_on_its_own_session():
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F2", ALLOWED), "S1"),
        (cdp.ATTACHED_TO_TARGET, _attached("S2", "F2"), "S1"),
    )

    assert {sid for sid, _, _ in transport.child_sent} == {"S2"}
    assert _resumed(transport) == ["S2"]


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["worker", "worklet", "shared_worker"])
async def test_a_paused_worker_is_resumed_and_nothing_else(kind):
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session, transport, (cdp.ATTACHED_TO_TARGET, _attached("S3", "W1", DENIED, kind=kind), None)
    )

    assert transport.child_sent == [("S3", cdp.RUN_IF_WAITING, {})]
    assert session.blocks == [] and _frame_blanks(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_target_of_a_type_this_session_does_not_know_is_left_paused(sel_rows):
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session, transport, (cdp.ATTACHED_TO_TARGET, _attached("S4", "X1", kind="mystery"), None)
    )

    assert transport.child_sent == []
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "target_left_paused")]
    assert sel_rows[0].outcome == "denied"
    assert session.blocks == []


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [DENIED, ALLOWED, ""], ids=["denied", "allowed", "no-url"])
async def test_a_page_the_browser_attaches_to_the_page_is_left_paused_not_let_run(url, sel_rows):
    """🔴 Red before: a page-type target the browser attached to the page, such as a prerendered
    page, was let run unjudged, and nothing inside it was watched. It is left paused and audited,
    as a target of a type the session does not know is, whatever its URL."""
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session,
        transport,
        (
            cdp.ATTACHED_TO_TARGET,
            _attached("S5", "P1", url, kind="page", subtype="prerender"),
            None,
        ),
    )

    assert transport.child_sent == []
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "target_left_paused")]
    assert "left paused" in sel_rows[0].metadata["reason"]
    assert sel_rows[0].outcome == "denied"
    assert session.blocks == [] and _frame_blanks(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_target_the_session_does_not_know_that_attaches_running_is_audited_as_running(
    sel_rows,
):
    """An unknown target that attached already running is not paused, so its row does not say
    it was left paused.

    🔴 Red before: nor was it refused, and its row still said ``denied``, which the audit surface
    files under Denied: the log asserted a refusal that never happened. It says ``ungated``, as a
    tool call that ran without asking the host does, which no outcome family claims."""
    from personalclaw.sel import audit_outcome_tone

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session,
        transport,
        (cdp.ATTACHED_TO_TARGET, _attached("S6", "P2", DENIED, waiting=False, kind="page"), None),
    )

    assert transport.child_sent == []
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "target_left_running")]
    assert "left paused" not in sel_rows[0].metadata["reason"]
    assert sel_rows[0].outcome == "ungated"
    assert audit_outcome_tone(sel_rows[0].outcome) == "neutral"


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"sessionId": "S1", "waitingForDebugger": True},
        {"sessionId": "S1", "targetInfo": "x", "waitingForDebugger": True},
        {"targetInfo": {"targetId": "F1", "type": "iframe", "url": ""}, "waitingForDebugger": True},
    ],
    ids=["no-target-info", "target-info-not-a-dict", "no-session"],
)
async def test_an_attach_the_session_cannot_read_blanks_the_page(params, sel_rows):
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(session, transport, (cdp.ATTACHED_TO_TARGET, params, None))

    assert transport.methods[-2:] == [cdp.STOP_LOADING, cdp.NAVIGATE] and _top_blanks(transport)
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "frame_unjudged")]
    assert _resumed(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_fenced_frame_is_left_unloaded_when_paused_and_blanks_the_page_when_running():
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "FF1", subtype="fenced"), None),
    )
    assert transport.child_sent == [] and _frame_blanks(transport) == []
    assert cdp.STOP_LOADING not in transport.methods
    assert len(session.blocks) == 1

    await _events(
        session,
        transport,
        (
            cdp.ATTACHED_TO_TARGET,
            _attached("S2", "FF2", ALLOWED, waiting=False, subtype="fenced"),
            None,
        ),
    )
    assert transport.methods[-2:] == [cdp.STOP_LOADING, cdp.NAVIGATE]
    assert transport.child_sent == [] and _frame_blanks(transport) == []


# a frame already running when it attaches


def _running_tree(fid: str, own: str, *nested: tuple[str, str]) -> dict:
    """What a frame in a process of its own answers ``Page.getFrameTree`` with on its session."""
    return {
        "frameTree": {
            "frame": {"id": fid, "url": own},
            "childFrames": [{"frame": {"id": nid, "url": url}} for nid, url in nested],
        }
    }


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_running_when_it_attaches_holds_the_page_until_its_frames_are_judged():
    """🔴 Red before: a frame the browser reports already running after start (a page restored
    from the back-forward cache brings its frames back so) was judged alone, and the frames
    inside it in a task that held nothing, so a screenshot sent meanwhile was answered with a
    denied frame nested in it still showing. Page commands now wait until those are judged."""
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    tree = asyncio.get_running_loop().create_future()
    transport.child_hold[("S1", cdp.GET_FRAME_TREE)] = tree
    transport.child_replies[("S1", cdp.GET_FRAME_TREE)] = _running_tree(
        "R1", ALLOWED, ("N1", DENIED)
    )
    transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached("S1", "R1", ALLOWED, waiting=False), None)
    for _ in range(5):
        await asyncio.sleep(0)
    shot = asyncio.ensure_future(session.page_transport.send("Page.captureScreenshot", {}))
    for _ in range(5):
        await asyncio.sleep(0)
    assert (
        "Page.captureScreenshot" not in transport.methods
    ), "the screenshot went out while a frame inside the running frame was still unjudged"

    tree.set_result(None)
    assert await asyncio.wait_for(shot, 1.0) == {}
    assert transport.methods.index("Page.captureScreenshot") > transport.sent.index(
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL, "frameId": "N1"})
    )
    assert cdp.STOP_LOADING not in transport.methods and session.quarantine_reason == ""
    await session.wait_idle()


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_reply_read_while_a_running_frames_frames_are_judged_waits_and_is_discarded():
    """🔴 Red before: a read already in flight when a frame attached running came back while the
    frames inside it were still being judged, and was returned; a denied one among them was
    blanked after. A reply waits for that judgement, and is discarded when it blanks a frame that
    had loaded, since the reply may show it."""
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    reply = asyncio.get_running_loop().create_future()
    transport.page_hold["Page.captureScreenshot"] = reply
    tree = asyncio.get_running_loop().create_future()
    transport.child_hold[("S1", cdp.GET_FRAME_TREE)] = tree
    transport.child_replies[("S1", cdp.GET_FRAME_TREE)] = _running_tree(
        "R1", ALLOWED, ("N1", DENIED)
    )
    shot = asyncio.ensure_future(session.page_transport.send("Page.captureScreenshot", {}))
    await asyncio.sleep(0)
    transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached("S1", "R1", ALLOWED, waiting=False), None)
    for _ in range(5):
        await asyncio.sleep(0)
    reply.set_result(None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert not shot.done(), "the reply was returned while a frame inside it was still unjudged"

    tree.set_result(None)
    with pytest.raises(cdp.PageReplyDiscarded):
        await asyncio.wait_for(shot, 1.0)
    assert _frame_blanks(transport) == ["N1"] and session.quarantine_reason == ""
    await session.wait_idle()


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_running_frame_whose_frames_are_not_judged_in_time_blanks_the_page_not_the_session(
    sel_rows,
):
    """Fail closed within the bound start() keeps: the frame's renderer does not answer, so the
    page is blanked whole rather than held, or shown with a frame unjudged, and is read after."""
    import asyncio

    transport = FakeTransport()
    session = _session(transport, frame_start_bound=0.05)
    await session.start()
    sel_rows.clear()
    held = asyncio.get_running_loop().create_future()
    transport.child_hold[("S1", cdp.PAGE_ENABLE)] = held
    transport.child_replies[("S1", cdp.GET_FRAME_TREE)] = _running_tree("R1", ALLOWED)
    transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached("S1", "R1", ALLOWED, waiting=False), None)
    for _ in range(5):
        await asyncio.sleep(0)
    try:
        read = await asyncio.wait_for(session.page_transport.send("Runtime.evaluate", {}), 1.0)
    finally:
        held.set_result(None)

    assert read == {}
    assert transport.methods[-3:] == [cdp.STOP_LOADING, cdp.NAVIGATE, "Runtime.evaluate"]
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "frame_unjudged")]
    assert session.quarantine_reason == ""
    await session.wait_idle()


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("own", "blanked"), [(DENIED, ["R1"]), (ALLOWED, [])], ids=["moved-meanwhile", "unchanged"]
)
async def test_a_running_frames_own_document_is_judged_as_a_frame_when_it_changed(
    own, blanked, monkeypatch
):
    """🔴 Red before (moved-meanwhile): a frame that committed a new document between its attach
    and its page events being enabled reported that commit nowhere, and only the frames inside it
    were read. Its own record is judged too, as a frame: a denied one is blanked by its id and the
    page kept. Unchanged, it is not judged a second time."""
    judged: list[str] = []
    real_evaluate = cdp.evaluate

    def spy(url, policy, **kwargs):
        judged.append(url)
        return real_evaluate(url, policy, **kwargs)

    monkeypatch.setattr(cdp, "evaluate", spy)
    transport = FakeTransport()
    session = await _started(transport)
    transport.child_replies[("S1", cdp.GET_FRAME_TREE)] = _running_tree("R1", own)
    await _events(
        session,
        transport,
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "R1", ALLOWED, waiting=False), None),
    )

    assert _frame_blanks(transport) == blanked
    assert cdp.STOP_LOADING not in transport.methods
    assert judged.count(ALLOWED) == 1, judged


# commit time, and what a document is judged as


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_denied_frame_committed_in_the_pages_process_is_blanked_alone(sel_rows):
    """🔴 Red before: the whole page was torn down for one denied frame."""
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None))

    assert _frame_blanks(transport) == ["CHILD1"]
    assert cdp.STOP_LOADING not in transport.methods and _top_blanks(transport) == 0
    assert _sel(sel_rows) == [(cdp.FRAME_NAVIGATED, "frame_committed")]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frames_own_commit_on_its_session_is_blanked_by_its_id():
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session, transport, (cdp.FRAME_NAVIGATED, {"frame": {"id": "OOP1", "url": DENIED}}, "S1")
    )

    assert _frame_blanks(transport) == ["OOP1"]
    assert cdp.STOP_LOADING not in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_that_moves_itself_to_a_denied_host_is_blanked():
    transport = FakeTransport()
    session = await _started(transport)
    await _events(session, transport, (cdp.TARGET_INFO_CHANGED, _info("OOP1", DENIED), None))

    assert _frame_blanks(transport) == ["OOP1"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_one_commit_reported_twice_is_blanked_and_recorded_once(sel_rows):
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "OOP1", "url": DENIED}}, "S1"),
        (cdp.TARGET_INFO_CHANGED, _info("OOP1", DENIED), None),
    )
    assert _frame_blanks(transport) == ["OOP1"]
    assert len(session.blocks) == 1 and len(sel_rows) == 1

    # A new top document: the frame is a new one, and judged again.
    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": "https://allowed.example/p"}}, None),
        (cdp.TARGET_INFO_CHANGED, _info("OOP1", DENIED), None),
    )
    assert _frame_blanks(transport) == ["OOP1", "OOP1"]

    # The other order: the move reported first, then the frame's own commit. Still one blank.
    transport2 = FakeTransport()
    session2 = await _started(transport2)
    await _events(
        session2,
        transport2,
        (cdp.TARGET_INFO_CHANGED, _info("OOP2", DENIED), None),
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "OOP2", "url": DENIED}}, "S2"),
    )
    assert _frame_blanks(transport2) == ["OOP2"]
    assert len(session2.blocks) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        # The browser's own method name, literal: it is read when the module is collected.
        ("Target.targetInfoChanged", _info("OOP1", "")),
        ("Target.targetInfoChanged", _info("OOP1", "about:blank?x=1")),
        ("Target.targetInfoChanged", _info("OOP1", "about:srcdoc#y")),
        ("Target.targetInfoChanged", _info("OOP1", "chrome-error://chromewebdata/")),
        ("Target.targetInfoChanged", _info("OOP1", "chrome-extension://abc/index.html")),
        ("Target.targetInfoChanged", _info("P1", DENIED, kind="page")),
        ("Target.targetInfoChanged", _info("W1", DENIED, kind="worker")),
        ("Page.frameNavigated", _child_frame("chrome-extension://abc/index.html")),
    ],
    ids=[
        "empty",
        "blank-query",
        "srcdoc-fragment",
        "error",
        "extension",
        "page",
        "worker",
        "extension-commit",
    ],
)
async def test_a_frame_that_is_no_network_document_is_left_alone(event):
    """Vacuity partner: none of these is a document a web host served."""
    transport = FakeTransport()
    session = await _started(transport)
    before = list(transport.sent)
    await _events(session, transport, (*event, None))

    assert transport.sent == before and session.blocks == []


@pytest.mark.asyncio
async def test_an_extension_pages_frame_is_watched_so_the_frames_inside_it_are_judged():
    """The browser's PDF viewer is one: it is not judged, but it is armed and let load."""
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        (
            cdp.FRAME_STARTED_NAVIGATING,
            _started_nav("X1", "chrome-extension://abc/index.html"),
            None,
        ),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "X1"), None),
    )

    assert _resumed(transport) == ["S1"] and session.blocks == []


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "blanked"),
    [
        (ALLOWED, False),
        ("blob:https://allowed.example/9f1c", False),
        ("blob:https://denied.example/9f1c", True),
        # A blob an opaque origin wrote (a sandboxed frame's, or a data: document's) names no
        # host to judge, so it is denied.
        ("blob:null/9f1c", True),
        ("data:text/html,<b>x</b>", True),
    ],
    ids=["allowed", "allowed-blob", "denied-blob", "opaque-blob", "data"],
)
async def test_a_frame_is_judged_by_the_origin_that_wrote_it(url, blanked):
    transport = FakeTransport()
    session = await _started(transport)
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(url), None))

    assert _frame_blanks(transport) == (["CHILD1"] if blanked else [])
    assert cdp.STOP_LOADING not in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_top_document_that_is_no_network_document_stays_and_an_extension_page_goes():
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": "about:blank?x=1"}}, None),
    )
    assert cdp.STOP_LOADING not in transport.methods

    await _events(
        session,
        transport,
        (
            cdp.FRAME_NAVIGATED,
            {"frame": {"id": "TOP", "url": "chrome-extension://abc/p.html"}},
            None,
        ),
    )
    assert transport.methods[-2:] == [cdp.STOP_LOADING, cdp.NAVIGATE]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_restored_from_the_back_forward_cache_has_its_frames_judged_again(sel_rows):
    """🔴 Red before: a page brought back from the cache came back with its frames as they were,
    a denied one included, and nothing judged them."""
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    transport.frames = ("https://allowed.example/page", DENIED)
    reads = transport.count(cdp.GET_FRAME_TREE)

    await _events(session, transport, (cdp.FRAME_NAVIGATED, _restore(), None))
    assert transport.count(cdp.GET_FRAME_TREE) == reads + 1
    assert _frame_blanks(transport) == ["CHILD0"]
    assert _sel(sel_rows) == [(cdp.GET_FRAME_TREE, "restored")]

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {**_restore(), "type": "Navigation"}, None),
    )
    assert transport.count(cdp.GET_FRAME_TREE) == reads + 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_restored_in_a_process_of_its_own_is_judged_again(operator_egress, sel_rows):
    """The case the page's own frame tree does not list: a frame in a process of its own comes
    back from the cache attached again, already running, and is judged again. A denied one is
    blanked by its id and the restored page kept; an allowed one has the frames inside it read,
    and a denied one of those is blanked too."""
    operator_egress.deny_hosts = ["denied.example", "operator-denied.example"]
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    transport.child_replies[("S2", cdp.GET_FRAME_TREE)] = _running_tree(
        "R2", ALLOWED, ("N2", "https://operator-denied.example/nested")
    )

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, _restore(), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "R1", DENIED, waiting=False), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S2", "R2", ALLOWED, waiting=False), None),
    )

    assert sorted(_frame_blanks(transport)) == ["N2", "R1"]
    assert cdp.STOP_LOADING not in transport.methods and _top_blanks(transport) == 0
    assert sorted(b.host for b in session.blocks) == ["denied.example", "operator-denied.example"]
    assert sorted(_sel(sel_rows)) == sorted(
        [(cdp.ATTACHED_TO_TARGET, "frame_attached"), (cdp.GET_FRAME_TREE, "frame_attached")]
    )
    assert {s for s, _m, _p in transport.child_sent} == {"S2"}, "a denied frame is never watched"
    assert session.quarantine_reason == ""


# the fail-closed ladder


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_already_gone_from_the_page_counts_as_blanked():
    transport = FakeTransport()
    transport.frame_fail = [f"the browser rejected the command: {cdp.FRAME_GONE}"]
    session = await _started(transport)
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None))

    assert _frame_blanks(transport) == ["CHILD1"]
    assert cdp.STOP_LOADING not in transport.methods and _top_blanks(transport) == 0
    assert session.quarantine_reason == "" and len(session.blocks) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blank_that_loaded_nothing_blanks_the_whole_page():
    transport = FakeTransport()
    transport.frame_reply = {"errorText": "net::ERR_ABORTED"}
    session = await _started(transport)
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None))

    assert _frame_blanks(transport) == ["CHILD1"]
    assert transport.methods[-2:] == [cdp.STOP_LOADING, cdp.NAVIGATE]
    assert session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, expected",
    [
        ("blanked", "frame"),
        ("gone", "frame"),
        ("blank-loaded-nothing", "page"),
        ("blank-refused", "page"),
        ("fenced-paused", "unloaded"),
        ("fenced-running", "page"),
        ("no-frame-id", "page"),
    ],
)
async def test_a_frame_block_says_what_was_done_about_the_frame(case, expected):
    """🔴 Red before: a frame's block did not say what was done about the frame, so the run was
    told a frame was blanked when its blank failed and the whole page was blanked in its place,
    and when a fenced frame was left paused and nothing was blanked at all."""
    transport = FakeTransport()
    if case == "gone":
        transport.frame_fail = [f"the browser rejected the command: {cdp.FRAME_GONE}"]
    elif case == "blank-loaded-nothing":
        transport.frame_reply = {"errorText": "net::ERR_ABORTED"}
    elif case == "blank-refused":
        transport.frame_fail = ["the browser rejected the command: boom"]
    session = await _started(transport)
    if case.startswith("fenced"):
        attach = _attached("S1", "FF1", DENIED, waiting=case == "fenced-paused", subtype="fenced")
        event = (cdp.ATTACHED_TO_TARGET, attach, None)
    elif case == "no-frame-id":
        event = (cdp.FRAME_NAVIGATED, {"frame": {"parentId": "TOP", "url": DENIED}}, None)
    else:
        event = (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    await _events(session, transport, event)

    assert [(b.frame, getattr(b, "blanked", None)) for b in session.blocks] == [(True, expected)]
    assert (_top_blanks(transport) == 1) is (expected == "page")
    # What was done is said once, by the block's effect; the reason says only why.
    assert "left unloaded" not in session.blocks[0].reason
    assert session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blank_refused_while_the_page_is_detached_is_sent_again():
    transport = FakeTransport()
    transport.frame_fail = [_DETACHED]
    session = await _started(transport)
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None))

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert cdp.STOP_LOADING not in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [False, True], ids=["committed", "paused"])
async def test_a_frame_that_can_be_blanked_neither_alone_nor_with_its_page_quarantines(paused):
    transport = FakeTransport(fail_on={cdp.STOP_LOADING})
    transport.frame_fail = ["the browser rejected the command: boom"]
    session = await _started(transport)
    event = (
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1", DENIED), None)
        if paused
        else (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="F1"), None)
    )
    await _events(session, transport, event)

    assert _frame_blanks(transport) == ["F1"] and session.quarantine_reason
    assert (await session.navigate("https://allowed.example/page")).allowed is False
    with pytest.raises(cdp.CdpSessionError):
        await session.page_transport.send("Runtime.evaluate", {})
    assert _resumed(transport) == []


@pytest.mark.asyncio
async def test_a_frame_that_left_while_it_was_armed_is_left_alone():
    """Vacuity partner: its session went away, so there is nothing of it to blank or resume."""
    transport = FakeTransport()
    transport.child_fail[("S1", cdp.PAGE_ENABLE)] = cdp.SESSION_GONE
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", ALLOWED), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None),
    )

    assert _frame_blanks(transport) == [] and _resumed(transport) == []
    assert session.blocks == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_that_cannot_be_watched_is_blanked_not_let_load():
    transport = FakeTransport()
    transport.child_fail[("S1", cdp.SET_AUTO_ATTACH)] = "the browser rejected the command: no"
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", ALLOWED), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None),
    )

    assert _frame_blanks(transport) == ["F1"] and _resumed(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_denied_frame_with_no_id_blanks_the_whole_page():
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"parentId": "TOP", "url": DENIED}}, None),
    )

    assert {"url": cdp.BLANK_URL, "frameId": ""} not in [p for _, p in transport.sent]
    assert transport.methods[-2:] == [cdp.STOP_LOADING, cdp.NAVIGATE]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_event_the_session_fails_on_blanks_the_whole_page(monkeypatch, sel_rows):
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()

    async def broken(*_a, **_k):
        raise RuntimeError("judging this frame failed")

    monkeypatch.setattr(session, "_block_frame", broken)
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None))

    assert (cdp.FRAME_NAVIGATED, "frame_unjudged") in _sel(sel_rows)
    assert transport.methods[-2:] == [cdp.STOP_LOADING, cdp.NAVIGATE]


# holds


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_after_it_loaded_holds_the_page_and_discards_a_read_across_it():
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    page_reply = asyncio.get_running_loop().create_future()
    transport.page_hold["Runtime.evaluate"] = page_reply
    blank = asyncio.get_running_loop().create_future()
    transport.frame_hold = blank

    straddling = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {"n": 1}))
    await asyncio.sleep(0)
    commit = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert session.tearing_down
    later = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {"n": 2}))
    for _ in range(5):
        await asyncio.sleep(0)
    assert ("Runtime.evaluate", {"n": 2}) not in transport.sent

    page_reply.set_result(None)
    blank.set_result(None)
    await commit
    with pytest.raises(cdp.PageReplyDiscarded):
        await straddling
    assert await later == {}
    assert transport.sent.index(("Runtime.evaluate", {"n": 2})) > transport.sent.index(
        (cdp.NAVIGATE, {"url": cdp.BLANK_URL, "frameId": "CHILD1"})
    )


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_before_it_loaded_holds_the_page_but_discards_nothing():
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    page_reply = asyncio.get_running_loop().create_future()
    transport.page_hold["Runtime.evaluate"] = page_reply
    blank = asyncio.get_running_loop().create_future()
    transport.frame_hold = blank

    in_flight = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {"n": 1}))
    await asyncio.sleep(0)
    transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None)
    for _ in range(5):
        await asyncio.sleep(0)
    later = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {"n": 2}))
    for _ in range(5):
        await asyncio.sleep(0)
    assert ("Runtime.evaluate", {"n": 2}) not in transport.sent

    page_reply.set_result(None)
    blank.set_result(None)
    await session.wait_idle()
    assert await in_flight == {}
    assert await later == {}


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_navigation_during_a_frame_blank_follows_it():
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    blank = asyncio.get_running_loop().create_future()
    transport.frame_hold = blank
    commit = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    nav = asyncio.ensure_future(session.navigate("https://allowed.example/next"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert (cdp.NAVIGATE, {"url": "https://allowed.example/next"}) not in transport.sent

    blank.set_result(None)
    await commit
    assert (await nav).ok is True


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blank_beside_it_never_excuses_a_page_blank_that_loaded_nothing():
    import asyncio

    transport = FakeTransport()
    transport.replies[cdp.NAVIGATE] = {"errorText": "net::ERR_ABORTED"}
    session = await _started(transport)
    blank = asyncio.get_running_loop().create_future()
    transport.frame_hold = blank
    frame = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    await transport.listener(
        cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": "https://denied.example/top"}}
    )
    blank.set_result(None)
    await frame

    assert session.quarantine_reason


# judgements are made off the loop


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_is_judged_on_a_thread_for_the_run_that_opened_the_session():
    import threading

    from personalclaw.net.policy import _HELD_TO, egress_held_to

    seen: list = []

    def resolver(host: str) -> list[str]:
        seen.append((threading.get_ident(), _HELD_TO.get()))
        return _resolver(host)

    transport = FakeTransport()
    with egress_held_to("run-key"):
        session = cdp.GatedCdpSession(transport, resolver=resolver)
        await session.start()
        # An allowed frame is the one looked up (a denied host is refused before any lookup),
        # and a denied one beside it is still blanked.
        await _events(
            session,
            transport,
            (cdp.FRAME_NAVIGATED, _child_frame(ALLOWED, fid="CHILD1"), None),
            (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="CHILD2"), None),
        )

    assert _frame_blanks(transport) == ["CHILD2"]
    assert seen and all(ident != threading.get_ident() for ident, _ in seen)
    assert all(held and held[0] == "run-key" for _, held in seen)


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [True, False], ids=["paused", "running"])
async def test_no_more_than_four_frames_are_judged_at_once(paused):
    """Four at a time for frames that are paused, and four for the rest."""
    import asyncio

    gate = asyncio.Event()
    state = {"now": 0, "peak": 0}

    async def gated(fn, *args):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await gate.wait()
        state["now"] -= 1
        return fn(*args)

    transport = FakeTransport()
    session = cdp.GatedCdpSession(transport, resolver=_resolver, blocking=_inline)
    await session.start()
    session._blocking = gated
    for i in range(10):
        if paused:
            transport.dispatch(
                cdp.FRAME_REQUESTED_NAVIGATION, _requested_nav(f"F{i}", ALLOWED), None
            )
            transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached(f"S{i}", f"F{i}"), None)
        else:
            transport.child_replies[(f"S{i}", cdp.GET_FRAME_TREE)] = _running_tree(f"F{i}", ALLOWED)
            transport.dispatch(
                cdp.ATTACHED_TO_TARGET, _attached(f"S{i}", f"F{i}", ALLOWED, waiting=False), None
            )
    for _ in range(20):
        await asyncio.sleep(0)
    assert state["peak"] == 4
    gate.set()
    await session.wait_idle()
    assert len(_resumed(transport)) == (10 if paused else 0)
    assert session.blocks == []


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("held_by", ["commit", "running-attach"])
async def test_slow_lookups_for_paused_frames_hold_no_read_of_the_page(held_by):
    """🔴 Red before: a paused frame's judgement and the judgements a page read waits for shared
    the four slots, so four slow lookups for paused frames, which show nothing, held an unrelated
    read of the page behind a frame commit (a 2 s lookup held it 1.9 s). Each kind has its own.

    Both judgements a read waits on are pinned: a frame's commit, and a frame that attaches
    already running, whose judgement is made in the dispatcher's step. A frame running is not
    paused, so its judgement takes no slot of the paused frames'."""
    import asyncio

    gate = asyncio.Event()

    async def gated(fn, *args):
        if "paused" in args[0]:
            await gate.wait()
        return fn(*args)

    transport = FakeTransport()
    session = await _started(transport)
    session._blocking = gated
    for i in range(4):
        transport.dispatch(
            cdp.FRAME_REQUESTED_NAVIGATION,
            _requested_nav(f"P{i}", f"https://allowed.example/paused-{i}"),
            None,
        )
        transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached(f"S{i}", f"P{i}"), None)
    for _ in range(10):
        await asyncio.sleep(0)
    if held_by == "commit":
        frame = _child_frame("https://allowed.example/committed")
        transport.dispatch(cdp.FRAME_NAVIGATED, frame, None)
    else:
        transport.child_replies[("R", cdp.GET_FRAME_TREE)] = _running_tree("RF", ALLOWED)
        transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached("R", "RF", ALLOWED, waiting=False))
    try:
        read = await asyncio.wait_for(session.page_transport.send("Runtime.evaluate", {}), 0.5)
    finally:
        gate.set()

    assert read == {}
    await session.wait_idle()
    assert len(_resumed(transport)) == 4 and session.blocks == []


# a frame's navigation is blanked as it starts


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_navigating_to_a_denied_host_is_blanked_as_it_starts(sel_rows):
    """🔴 Red before: a frame in the page's own process loaded and ran until its commit was
    reported. Its navigation is now blanked by id as soon as it starts, before it commits, and a
    read in flight is not discarded, since nothing of it was shown."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()
    page_reply = asyncio.get_running_loop().create_future()
    transport.page_hold["Runtime.evaluate"] = page_reply
    read = asyncio.ensure_future(session.page_transport.send("Runtime.evaluate", {}))
    await asyncio.sleep(0)

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    page_reply.set_result(None)

    assert _frame_blanks(transport) == ["CHILD1"]
    assert _sel(sel_rows) == [(cdp.FRAME_STARTED_NAVIGATING, "frame_started")]
    assert await read == {}
    assert cdp.STOP_LOADING not in transport.methods


@pytest.mark.asyncio
async def test_an_allowed_frame_navigation_and_the_top_frames_own_are_not_blanked_as_they_start():
    """Vacuity partner: an allowed frame is left to load, and the top document is judged at
    commit as it always was (its blank is the whole page's). Both start from the page's own site,
    so the site rule is not what leaves them alone."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    before = list(transport.sent)
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", ALLOWED), None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("TOP", SAME_SITE_DENIED), None),
        (cdp.FRAME_REQUESTED_NAVIGATION, _requested_nav("CHILD2", SAME_SITE_DENIED), None),
    )

    assert transport.sent == before and session.blocks == []


# what a denial leaves behind


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_host_blocked_again_and_again_on_one_page_is_recorded_once(
    operator_egress, sel_rows
):
    """🔴 Red before: every blank of a frame was a block, an SEL row and a line in the run's next
    prompt, so a page that kept re-pointing a denied frame, or adding one, grew all three without
    end. The frame is still blanked every time; a block of a host already recorded for the page
    is counted on that one. Another host is a block of its own, and a new document starts over."""
    operator_egress.deny_hosts = ["denied.example", "operator-denied.example"]
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        *[(cdp.FRAME_NAVIGATED, _child_frame(f"{DENIED}?{n}"), None) for n in range(5)],
        (cdp.FRAME_NAVIGATED, _child_frame("https://denied.example/two", fid="CHILD2"), None),
    )
    assert _frame_blanks(transport) == ["CHILD1"] * 5 + ["CHILD2"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("denied.example", 5)]
    assert len(sel_rows) == 1

    await _events(
        session,
        transport,
        (
            cdp.FRAME_NAVIGATED,
            _child_frame("https://operator-denied.example/f", fid="CHILD3"),
            None,
        ),
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": "https://allowed.example/b"}}, None),
    )
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None))
    assert [b.host for b in session.blocks] == [
        "denied.example",
        "operator-denied.example",
        "denied.example",
    ]
    assert len(sel_rows) == 3


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["blank-refused", "blank-loaded-nothing", "no-frame-id", "fenced-running"]
)
async def test_a_repeat_hosts_frame_that_costs_the_whole_page_is_recorded_and_told_so(
    case, sel_rows
):
    """🔴 Red before: a frame block of a host the page had already blocked was counted on that
    block before its own blank was tried. When that blank then failed (refused, loaded nothing,
    no frame id) or the frame was a running fenced one, the whole page was blanked in its place
    with no block and no SEL row, and the run was told nothing: the first block still said only
    its own frame was blanked. A block is counted on an earlier one only while its own frame alone
    goes; once the whole page goes it is a block of its own, audited as it would have been, and
    the run is told the page went."""
    from personalclaw.browse.loop import _LoopState, _note_frame_blocks

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    told = _LoopState()
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="A"), None))
    _note_frame_blocks(told, session)
    told.warnings.clear()

    second = "https://denied.example/two"
    phase = (cdp.FRAME_NAVIGATED, "frame_committed")
    if case == "blank-refused":
        transport.frame_fail = ["the browser rejected the command: boom"]
    elif case == "blank-loaded-nothing":
        transport.frame_reply = {"errorText": "net::ERR_ABORTED"}
    if case == "fenced-running":
        attach = _attached("S2", "FF2", second, waiting=False, subtype="fenced")
        event = (cdp.ATTACHED_TO_TARGET, attach, None)
        phase = (cdp.ATTACHED_TO_TARGET, "frame_attached")
    elif case == "no-frame-id":
        event = (cdp.FRAME_NAVIGATED, {"frame": {"parentId": "TOP", "url": second}}, None)
    else:
        event = (cdp.FRAME_NAVIGATED, _child_frame(second, fid="B"), None)
    await _events(session, transport, event)
    _note_frame_blocks(told, session)

    assert _top_blanks(transport) == 1 and session.quarantine_reason == ""
    assert [(b.host, b.blanked, b.repeats) for b in session.blocks] == [
        ("denied.example", "frame", 0),
        ("denied.example", "page", 0),
    ]
    assert _sel(sel_rows) == [(cdp.FRAME_NAVIGATED, "frame_committed"), phase]
    assert told.warnings == [
        "a frame from denied.example could not be blanked on its own, so the whole page was "
        f"blanked: {session.blocks[1].reason}"
    ]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_repeat_hosts_frame_that_costs_the_whole_page_is_recorded_before_it_goes(sel_rows):
    """Recorded before the page is blanked, as every block is: a teardown the browser holds, or a
    process that ends during it, still leaves the block and its SEL row."""
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="A"), None))
    transport.frame_fail = ["the browser rejected the command: boom"]
    stop = asyncio.get_running_loop().create_future()
    transport.page_hold[cdp.STOP_LOADING] = stop
    second = _child_frame("https://denied.example/two", fid="B")
    step = transport.dispatch(cdp.FRAME_NAVIGATED, second, None)
    for _ in range(10):
        await asyncio.sleep(0)
    assert cdp.STOP_LOADING in transport.methods and not step.done(), "the page is not going"

    assert [b.blanked for b in session.blocks] == ["frame", "page"] and len(sel_rows) == 2
    stop.set_result(None)
    await step
    assert _top_blanks(transport) == 1 and session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
async def test_only_a_frames_block_is_counted_on_an_earlier_one_of_its_host(sel_rows):
    """Counting a block on an earlier one of its host is for frames: a refused navigation is a
    page the run asked for, so it is recorded and audited every time, after a frame block of the
    same host on the page too."""
    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None))
    one = await session.navigate("https://denied.example/a")
    two = await session.navigate("https://denied.example/b")

    assert one.allowed is False and two.allowed is False
    assert [(b.host, b.frame, b.repeats) for b in session.blocks] == [
        ("denied.example", True, 0),
        ("denied.example", False, 0),
        ("denied.example", False, 0),
    ]
    assert [r.operation for r in sel_rows] == [cdp.FRAME_NAVIGATED, cdp.NAVIGATE, cdp.NAVIGATE]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_session_remembers_256_frames_and_forgets_the_oldest_first():
    """Each memory the session keeps of frames (where each was going, what each was last judged
    as, the lock its blanks take) is bounded, the oldest forgotten first, so a page that starts
    frame after frame cannot grow them without end."""
    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        *[(cdp.FRAME_STARTED_NAVIGATING, _started_nav(f"F{i}", ALLOWED), None) for i in range(257)],
    )
    assert len(session._frame_starts) == 256
    # The oldest start is forgotten: that frame attaches with nowhere known and is blanked. The
    # next one is still known, and is let load.
    await _events(
        session,
        transport,
        (cdp.ATTACHED_TO_TARGET, _attached("S0", "F0"), None),
        (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None),
    )
    assert _frame_blanks(transport) == ["F0"] and _resumed(transport) == ["S1"]

    transport = FakeTransport()
    session = await _started(transport)
    await _events(
        session,
        transport,
        *[(cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid=f"C{i}"), None) for i in range(257)],
    )
    assert len(_frame_blanks(transport)) == 257
    assert len(session._judged) == 256 and len(session._frame_locks) == 256
    # The oldest commit is forgotten, so reported again it is judged and blanked again; the newest
    # is remembered, and is not.
    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="C0"), None),
        (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="C256"), None),
    )
    assert _frame_blanks(transport)[257:] == ["C0"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_failed_early_blank_is_not_escalated_and_the_frame_is_still_judged_at_load(
    sel_rows,
):
    """The early blank is a head start, not the check: its failure is logged and nothing more,
    and the denied commit that follows still gets the whole ladder, recorded once."""
    transport = FakeTransport(frames=(PAGE,))
    transport.frame_fail = ["the browser rejected the command: superseded"]
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    assert cdp.STOP_LOADING not in transport.methods and session.quarantine_reason == ""

    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None))
    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert len(sel_rows) == 1 and len(session.blocks) == 1
    assert cdp.STOP_LOADING not in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_as_it_started_is_blanked_again_when_it_commits(sel_rows):
    """Blanked twice, recorded once: the early blank never marks the frame as judged."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert len(sel_rows) == 1 and len(session.blocks) == 1


# ── the production wire ──────────────────────────────────────────────────────


class _ScriptedSocket:
    """A page target's debugger socket for the production transport, standing in for the browser.

    Every command is answered on a task of its own, in the order the browser would: events listed
    for a method in ``before`` first, then the reply, held while ``hold[(sid, method)]`` is
    pending. ``sent`` keeps every message as written."""

    def __init__(self) -> None:
        import asyncio

        self.sent: list[dict] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.before: dict[str, list[dict]] = {}
        self.hold: dict[tuple, object] = {}
        self.replies: dict[tuple, dict] = {}
        self.tree: dict = {"frame": {"id": "TOP", "url": cdp.BLANK_URL}}

    async def send(self, payload: str) -> None:
        import asyncio
        import json

        message = json.loads(payload)
        self.sent.append(message)
        asyncio.ensure_future(self._answer(message))

    async def _answer(self, message: dict) -> None:
        import json

        sid = message.get("sessionId")
        method = message["method"]
        for event in self.before.pop(method, ()):
            await self._inbox.put(json.dumps(event))
        if (sid, method) in self.hold:
            await self.hold[(sid, method)]
        result = self.replies.get((sid, method), {})
        if method == cdp.GET_FRAME_TREE and sid is None:
            result = {"frameTree": self.tree}
        await self._inbox.put(json.dumps({"id": message["id"], "result": result}))

    def event(self, method: str, params: dict, sid: str | None = None) -> None:
        import json

        message: dict = {"method": method, "params": params}
        if sid is not None:
            message["sessionId"] = sid
        self._inbox.put_nowait(json.dumps(message))

    def wrote(self, method: str, sid: str | None = None) -> list[dict]:
        return [m for m in self.sent if m["method"] == method and m.get("sessionId") == sid]

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        raw = await self._inbox.get()
        if raw is None:
            raise StopAsyncIteration
        return raw

    async def close(self) -> None:
        self._inbox.put_nowait(None)


def _wire(socket: _ScriptedSocket, *, request_timeout: float = 5.0):
    from personalclaw.browse.transport import WebSocketCdpTransport

    transport = WebSocketCdpTransport(socket, request_timeout=request_timeout)
    transport.start_reading()
    return transport


@pytest.mark.security
@pytest.mark.asyncio
async def test_the_wire_names_a_frames_session_only_on_that_frames_commands():
    socket = _ScriptedSocket()
    transport = _wire(socket)
    try:
        await transport.send("Page.enable", {})
        await transport.send("Page.enable", {}, session_id="S1")
    finally:
        await transport.close()

    assert "sessionId" not in socket.sent[0]
    assert socket.sent[1]["sessionId"] == "S1"


@pytest.mark.security
@pytest.mark.asyncio
async def test_the_wire_hands_the_listener_the_session_an_event_came_on():
    import asyncio

    socket = _ScriptedSocket()
    transport = _wire(socket)
    seen: list = []

    async def listener(method, params, sid=None):
        seen.append((method, sid))

    transport.set_event_listener(listener)
    try:
        socket.event(cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": cdp.BLANK_URL}})
        socket.event(cdp.FRAME_NAVIGATED, {"frame": {"id": "F1", "url": cdp.BLANK_URL}}, "S1")
        # A drain covers the events read so far: let the reader take both off the socket first.
        await asyncio.sleep(0.01)
        await transport.drain_events()
    finally:
        await transport.close()

    assert seen == [(cdp.FRAME_NAVIGATED, None), (cdp.FRAME_NAVIGATED, "S1")]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_command_to_a_frame_that_went_away_fails_at_once():
    """Its reply will never come; waiting the full command timeout would hold the frame."""
    import asyncio

    from personalclaw.browse.transport import SESSION_DETACHED, CdpTransportError

    socket = _ScriptedSocket()
    socket.hold[("S1", "Page.enable")] = asyncio.get_running_loop().create_future()
    socket.hold[("S2", "Page.enable")] = release = asyncio.get_running_loop().create_future()
    transport = _wire(socket, request_timeout=30.0)
    try:
        first = asyncio.ensure_future(transport.send("Page.enable", {}, session_id="S1"))
        other = asyncio.ensure_future(transport.send("Page.enable", {}, session_id="S2"))
        await asyncio.sleep(0.01)
        socket.event("Target.detachedFromTarget", {"sessionId": "S1"})
        with pytest.raises(CdpTransportError, match=SESSION_DETACHED):
            await asyncio.wait_for(first, 1.0)
        assert not other.done()
        release.set_result(None)
        assert await asyncio.wait_for(other, 1.0) == {}
    finally:
        await transport.close()


@pytest.mark.security
@pytest.mark.asyncio
async def test_draining_waits_for_the_events_read_so_far_and_no_later_ones():
    """The later event stays held while the drain runs: a drain that also waited for events read
    after it began would never end on a page that keeps reporting them."""
    import asyncio

    socket = _ScriptedSocket()
    transport = _wire(socket)
    handled: list[str] = []
    gates = {"a": asyncio.Event(), "b": asyncio.Event()}

    async def listener(method, params, sid=None):
        await gates[params["n"]].wait()
        handled.append(params["n"])

    transport.set_event_listener(listener)
    try:
        socket.event("Page.x", {"n": "a"})
        await asyncio.sleep(0.01)
        drain = asyncio.ensure_future(transport.drain_events())
        await asyncio.sleep(0.01)
        socket.event("Page.x", {"n": "b"})
        await asyncio.sleep(0.01)
        assert not drain.done(), "the drain returned before the slow listener ended"
        gates["a"].set()
        await asyncio.wait_for(drain, 1.0)
        assert handled == ["a"], "the drain waited for an event read after it began"
    finally:
        gates["b"].set()
        await transport.close()


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_drain_from_the_listener_is_refused_rather_than_waiting_on_itself():
    """The listener is the one task that can end a drain, so a drain from inside it would wait on
    itself until the wire's silence bound: it is refused at once, and the event still counts as
    handled."""
    import asyncio

    from personalclaw.browse.transport import CdpTransportError

    socket = _ScriptedSocket()
    transport = _wire(socket, request_timeout=0.2)
    raised: list[BaseException] = []

    async def listener(method, params, sid=None):
        try:
            await transport.drain_events()
        except Exception as exc:  # noqa: BLE001 - what the drain raised is the assertion
            raised.append(exc)

    transport.set_event_listener(listener)
    try:
        socket.event("Page.x", {})
        await asyncio.sleep(0.01)
        await asyncio.wait_for(transport.drain_events(), 2.0)
    finally:
        await transport.close()

    assert len(raised) == 1 and not isinstance(raised[0], CdpTransportError), raised
    assert "called from the event listener" in str(raised[0])


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_busy_dispatcher_drains_and_a_silent_one_fails_the_drain():
    """The bound is on progress, not on the total: a listener that keeps the wire busy (or waits
    on its own replies) is not a wedge; a listener that is silent for twice the command timeout
    is."""
    import asyncio
    import time

    from personalclaw.browse.transport import CdpTransportError

    sids = ("S1", "S2", "S3", "S4", "S5", "S6")
    socket = _ScriptedSocket()
    for sid in sids:
        socket.hold[(sid, "Page.enable")] = asyncio.get_running_loop().create_future()
    transport = _wire(socket, request_timeout=1.0)

    async def release_slowly():
        # 6 x 0.45 s: longer in all than twice the 1 s timeout, never silent for one second.
        for sid in sids:
            await asyncio.sleep(0.45)
            socket.hold[(sid, "Page.enable")].set_result(None)

    async def busy(method, params, sid=None):
        if method == "Page.busy":
            await transport.send("Page.enable", {}, session_id=params["sid"])
        elif method == "Page.silent":
            await asyncio.sleep(10)

    transport.set_event_listener(busy)
    try:
        for sid in sids:
            socket.event("Page.busy", {"sid": sid})
        # A drain covers the events read so far: let the reader take them off the socket first.
        await asyncio.sleep(0.01)
        releaser = asyncio.ensure_future(release_slowly())
        drain = asyncio.ensure_future(transport.drain_events())
        await asyncio.sleep(0.2)
        assert not drain.done(), "the drain did not wait for the busy listener"
        await asyncio.wait_for(drain, 5.0)
        await releaser

        socket.event("Page.silent", {})
        await asyncio.sleep(0.01)
        began = time.monotonic()
        with pytest.raises(CdpTransportError):
            await asyncio.wait_for(transport.drain_events(), 5.0)
        assert time.monotonic() - began < 3.5
    finally:
        await transport.close()


@pytest.mark.security
@pytest.mark.asyncio
async def test_start_waits_for_the_frames_it_inherits_and_a_denied_one_is_blanked_first():
    import asyncio

    socket = _ScriptedSocket()
    socket.before[cdp.SET_AUTO_ATTACH] = [
        {
            "method": cdp.ATTACHED_TO_TARGET,
            "params": _attached("S1", "OK1", ALLOWED, waiting=False),
        },
        {
            "method": cdp.ATTACHED_TO_TARGET,
            "params": _attached("S2", "BAD1", DENIED, waiting=False),
        },
    ]
    held = socket.hold[("S1", cdp.PAGE_ENABLE)] = asyncio.get_running_loop().create_future()
    socket.replies[("S1", cdp.GET_FRAME_TREE)] = {
        "frameTree": {"frame": {"id": "OK1", "url": ALLOWED}}
    }
    transport = _wire(socket)
    session = cdp.GatedCdpSession(transport, resolver=_resolver)
    try:
        start = asyncio.ensure_future(session.start())
        await asyncio.sleep(0.2)
        assert not start.done(), "start ended while a frame it inherited was still unjudged"
        held.set_result(None)
        await asyncio.wait_for(start, 2.0)
        await session.page_transport.send("Runtime.evaluate", {"expression": "1"})
    finally:
        await transport.close()

    methods = [(m["method"], (m.get("params") or {}).get("frameId")) for m in socket.sent]
    assert methods.index((cdp.NAVIGATE, "BAD1")) < methods.index(("Runtime.evaluate", None))
    assert session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
async def test_start_keeps_its_bound_while_a_frame_it_inherits_is_still_being_judged(sel_rows):
    """🔴 Red before: start's bound was checked only between drains, and a drain waits for the
    dispatcher, which judges (and blanks) a frame already running inline, so a slow lookup held
    start past the bound (a 2 s lookup under a 0.5 s bound took 2.01 s), and a blank the browser
    never answered would have held it for the command timeout. The drain is bounded too: past the
    bound the page is blanked, and the session starts."""
    import asyncio
    import contextlib

    gate = asyncio.Event()

    async def gated(fn, *args):
        if args and args[0] == ALLOWED:
            await gate.wait()
        return fn(*args)

    socket = _ScriptedSocket()
    socket.before[cdp.SET_AUTO_ATTACH] = [
        {
            "method": cdp.ATTACHED_TO_TARGET,
            "params": _attached("S1", "OK1", ALLOWED, waiting=False),
        }
    ]
    socket.replies[("S1", cdp.GET_FRAME_TREE)] = _running_tree("OK1", ALLOWED)
    transport = _wire(socket)
    session = cdp.GatedCdpSession(
        transport, resolver=_resolver, blocking=gated, frame_start_bound=0.05
    )
    try:
        await asyncio.wait_for(session.start(), 1.0)
        assert session.started and session.quarantine_reason == ""
        assert [m["params"] for m in socket.wrote(cdp.NAVIGATE)] == [{"url": cdp.BLANK_URL}]
        assert len(socket.wrote(cdp.STOP_LOADING)) == 1
        assert (cdp.SET_AUTO_ATTACH, "start") in _sel(sel_rows)
    finally:
        gate.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(session.wait_idle(), 2.0)
        await transport.close()


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_reply_read_behind_a_denied_frames_commit_is_discarded():
    """The commit was read before the reply, so the reply may hold the frame: it is judged first.
    The judgement is slow here, so the reply arrives while it still runs, before any block
    began; only waiting for the events read with the reply catches it."""
    import asyncio

    async def slow(fn, *args):
        await asyncio.sleep(0.2)
        return fn(*args)

    socket = _ScriptedSocket()
    transport = _wire(socket)
    session = cdp.GatedCdpSession(transport, resolver=_resolver)
    try:
        await session.start()
        session._blocking = slow
        socket.before["Page.captureScreenshot"] = [
            {"method": cdp.FRAME_NAVIGATED, "params": _child_frame(DENIED)}
        ]
        with pytest.raises(cdp.PageReplyDiscarded):
            await session.page_transport.send("Page.captureScreenshot", {"format": "png"})
    finally:
        await transport.close()

    assert [m["params"].get("frameId") for m in socket.wrote(cdp.NAVIGATE)] == ["CHILD1"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_slow_judgement_of_a_paused_frame_holds_no_read_of_the_page():
    """Nothing of a paused frame can be shown, so its judgement waits on its own: the page is read
    meanwhile, and the frame is let load once it is judged allowed."""
    import asyncio
    import threading

    gate = threading.Event()
    slow = {"on": False}

    def resolver(host: str) -> list[str]:
        if slow["on"]:
            gate.wait(5)
        return _resolver(host)

    socket = _ScriptedSocket()
    transport = _wire(socket)
    session = cdp.GatedCdpSession(transport, resolver=resolver)
    try:
        await session.start()
        slow["on"] = True
        socket.event(cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", ALLOWED))
        socket.event(cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"))
        await asyncio.sleep(0.05)
        await asyncio.wait_for(session.page_transport.send("Runtime.evaluate", {}), 1.0)
        assert socket.wrote(cdp.RUN_IF_WAITING, "S1") == []
        gate.set()
        await asyncio.wait_for(session.wait_idle(), 2.0)
    finally:
        gate.set()
        await transport.close()

    assert len(socket.wrote(cdp.RUN_IF_WAITING, "S1")) == 1


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "open_", [_plan_runner_open, _provider_open], ids=["plan_runner", "browse_provider"]
)
async def test_a_run_that_ends_while_a_frame_is_being_judged_writes_no_denial_after_it(
    monkeypatch, open_, sel_rows, caplog
):
    """🔴 Red before: each closer closed only the transport, so a frame still being judged when a
    run ended went on, and failed on the closed socket: a "denied" row for a frame on an allowed
    host, a block, a quarantine and three errors, all after the run. Each closer now ends the
    session's own work before the transport closes. The lookup here returns as the socket closes,
    so a closer that closed the transport first would still write that row."""
    import asyncio
    import logging
    import threading

    from personalclaw.browse import transport as transport_mod

    entered, release = threading.Event(), threading.Event()
    real_evaluate = cdp.evaluate

    def slow(host: str) -> list[str]:
        entered.set()
        release.wait(5)
        return _resolver(host)

    monkeypatch.setattr(
        cdp, "evaluate", lambda url, policy, **_kw: real_evaluate(url, policy, resolver=slow)
    )

    class _Socket(_ScriptedSocket):
        async def close(self) -> None:
            release.set()
            await asyncio.sleep(0.2)
            await super().close()

    socket = _Socket()

    async def _connect(*_a, **_k):
        return _wire(socket)

    monkeypatch.setattr(transport_mod.WebSocketCdpTransport, "connect", staticmethod(_connect))
    caplog.set_level(logging.INFO, logger=cdp.__name__)
    try:
        session, _driver, close = await open_()
        await session.start()
        socket.event(cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", ALLOWED))
        socket.event(cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"))
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), "the frame's judgement never began"
        await close()
        await asyncio.sleep(0.1)
    finally:
        release.set()

    assert sel_rows == [] and session.blocks == [] and session.quarantine_reason == ""
    browse = [r for r in caplog.records if r.name.startswith("personalclaw.browse")]
    assert not [r for r in browse if r.levelno >= logging.WARNING], [r.getMessage() for r in browse]


def _closing_error():
    """What the production transport fails a command still in flight with as it closes."""
    from personalclaw.browse.transport import CdpTransportError

    return CdpTransportError("the CDP transport was closed")


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_restored_pages_frames_read_as_the_session_closes_records_no_denial(
    sel_rows, caplog
):
    """🔴 Red before: the dispatcher's own step outlives the session's tasks, so a restored page
    whose frames were being read when the run ended had that read failed by the closing socket,
    and recorded a denial of an allowed page (a block, a row, a whole-page teardown, errors). A
    command the closing transport fails is the session ending, not a page it cannot judge."""
    import asyncio
    import logging

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    caplog.set_level(logging.INFO, logger=cdp.__name__)
    tree = asyncio.get_running_loop().create_future()
    transport.page_hold[cdp.GET_FRAME_TREE] = tree
    step = transport.dispatch(cdp.FRAME_NAVIGATED, _restore(), None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert cdp.GET_FRAME_TREE not in transport.page_hold, "the restored page's frames were not read"

    await session.close()
    tree.set_exception(_closing_error())
    await asyncio.wait_for(step, 1.0)

    assert sel_rows == [] and session.blocks == [] and session.quarantine_reason == ""
    assert cdp.STOP_LOADING not in transport.methods
    browse = [r for r in caplog.records if r.name.startswith("personalclaw.browse")]
    assert not [r for r in browse if r.levelno >= logging.WARNING], [r.getMessage() for r in browse]


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["frame", "page"])
async def test_a_blank_the_closing_session_cuts_off_quarantines_nothing(which, sel_rows, caplog):
    """🔴 Red before: a denied frame's blank, or a denied page's teardown, still in flight when the
    run ended was failed by the closing socket, then fell back to a whole-page teardown on that
    socket, quarantined the session and logged errors, all after the run. The denial itself was
    real and stays recorded, once; nothing after the closing is."""
    import asyncio
    import logging

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    caplog.set_level(logging.INFO, logger=cdp.__name__)
    hold = asyncio.get_running_loop().create_future()
    if which == "frame":
        transport.frame_hold = hold
        step = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    else:
        transport.page_hold[cdp.STOP_LOADING] = hold
        step = transport.dispatch(cdp.FRAME_NAVIGATED, _frame(DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert transport.frame_hold is None and cdp.STOP_LOADING not in transport.page_hold

    await session.close()
    hold.set_exception(_closing_error())
    await asyncio.wait_for(step, 1.0)

    assert [b.host for b in session.blocks] == ["denied.example"] and len(sel_rows) == 1
    assert session.quarantine_reason == ""
    assert transport.count(cdp.STOP_LOADING) == (0 if which == "frame" else 1)
    assert _top_blanks(transport) == 0
    browse = [r for r in caplog.records if r.name.startswith("personalclaw.browse")]
    assert not [r for r in browse if r.levelno >= logging.WARNING], [r.getMessage() for r in browse]


def _browse_warnings(caplog) -> list[str]:
    import logging

    return [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("personalclaw.browse") and r.levelno >= logging.WARNING
    ]


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["closing-error", "answered"])
@pytest.mark.parametrize("paused", [True, False], ids=["paused", "running"])
async def test_closing_cancels_the_watch_of_a_frame_and_it_goes_no_further(
    paused, answer, sel_rows, caplog
):
    """close() cancels the session's own tasks, pinned on its own: the closer test above lets the
    lookup return before the socket closes, so a close that only marked the session closed passed
    it. Here an allowed frame's watch is waiting on its own page events being enabled when the
    run ends, past its judgement; after the close, that command is failed by the closing socket
    or answered. Either way the frame records nothing (no block, no SEL row, no warning), sends
    nothing more on its session, and holds no page command. Without the cancel the failure wrote
    a denial of an allowed host after the run, and the answer let the watch go on."""
    import asyncio
    import logging

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    caplog.set_level(logging.INFO, logger=cdp.__name__)
    held = asyncio.get_running_loop().create_future()
    transport.child_hold[("S1", cdp.PAGE_ENABLE)] = held
    if paused:
        transport.dispatch(cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", ALLOWED), None)
        transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None)
    else:
        transport.child_replies[("S1", cdp.GET_FRAME_TREE)] = _running_tree("F1", ALLOWED)
        transport.dispatch(cdp.ATTACHED_TO_TARGET, _attached("S1", "F1", ALLOWED, waiting=False))
    for _ in range(10):
        await asyncio.sleep(0)
    assert transport.child_sent == [("S1", cdp.PAGE_ENABLE, {})], "the frame was not being watched"

    await session.close()
    # A cancelled watch took its command's wait with it; one left running is still waiting.
    if not held.done():
        if answer == "closing-error":
            held.set_exception(_closing_error())
        else:
            held.set_result(None)
    for _ in range(20):
        await asyncio.sleep(0)

    assert sel_rows == [] and session.blocks == [] and session.quarantine_reason == ""
    assert transport.child_sent == [("S1", cdp.PAGE_ENABLE, {})]
    assert _frame_blanks(transport) == [] and cdp.STOP_LOADING not in transport.methods
    assert not session.tearing_down
    assert _browse_warnings(caplog) == []


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [True, False], ids=["paused", "running"])
async def test_a_frame_reported_after_the_session_closed_is_never_watched(paused, sel_rows, caplog):
    """A task the session would start after close() is cancelled before it runs, pinned on its
    own: the closer runs the session's close before the transport's, and the socket can still
    report a frame between the two. An allowed frame reported then sends nothing on its session
    (the closing socket would fail it) and records nothing; without the cancel, its watch ran into
    the closing socket and wrote a denial of an allowed host after the run."""
    import logging

    transport = FakeTransport()
    session = await _started(transport)
    sel_rows.clear()
    caplog.set_level(logging.INFO, logger=cdp.__name__)
    await session.close()
    for method in (cdp.PAGE_ENABLE, cdp.SET_AUTO_ATTACH, cdp.GET_FRAME_TREE, cdp.RUN_IF_WAITING):
        transport.child_fail[("S1", method)] = str(_closing_error())
    if paused:
        events = [
            (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", ALLOWED), None),
            (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None),
        ]
    else:
        events = [(cdp.ATTACHED_TO_TARGET, _attached("S1", "F1", ALLOWED, waiting=False), None)]
    await _events(session, transport, *events)

    assert transport.child_sent == []
    assert sel_rows == [] and session.blocks == [] and session.quarantine_reason == ""
    assert _frame_blanks(transport) == [] and not session.tearing_down
    assert _browse_warnings(caplog) == []


# ── going back one page ──────────────────────────────────────────────────────


class _History(FakeTransport):
    """A page whose history is *entries* (``(id, url)``), the current one at *current*."""

    def __init__(self, entries: list[tuple[int, str]], current: int) -> None:
        super().__init__()
        self.replies["Page.getNavigationHistory"] = {
            "currentIndex": current,
            "entries": [{"id": i, "url": u, "title": ""} for i, u in entries],
        }


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_skips_a_frames_own_history_and_leaves_the_page():
    """🔴 Red before: going back after a frame was blanked went back inside that frame, so the
    page never changed and the run was stuck on it. Back now goes to the latest earlier entry
    whose address differs from the page the run is on: one page back, as the action says."""
    from personalclaw.browse.page import CdpPageDriver

    page_a, page_b = "https://allowed.example/a", "https://allowed.example/b"
    transport = _History([(1, page_a), (2, page_b), (3, page_b), (4, page_b)], current=3)
    session = await _started(transport)
    await CdpPageDriver(session.page_transport).go_back()

    assert transport.sent[-2:] == [
        ("Page.getNavigationHistory", {}),
        ("Page.navigateToHistoryEntry", {"entryId": 1}),
    ]
    assert "Runtime.evaluate" not in transport.methods


@pytest.mark.asyncio
async def test_going_back_with_no_earlier_page_does_nothing():
    from personalclaw.browse.page import CdpPageDriver

    transport = _History([(1, "https://allowed.example/a"), (2, "https://allowed.example/a")], 1)
    session = await _started(transport)
    await CdpPageDriver(session.page_transport).go_back()

    assert transport.methods[-1] == "Page.getNavigationHistory"


def _history_loads(transport) -> list[int]:
    """The history entries the session asked the browser to load, in order."""
    return [p["entryId"] for m, p in transport.sent if m == "Page.navigateToHistoryEntry"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_judges_each_entry_first_and_skips_a_denied_one(sel_rows):
    """🔴 Red before: GO_BACK loaded the earlier entry without the pre-flight a navigation gets,
    so the page a teardown left (the denied document, then the blank) was requested from its host
    again, torn down again once it loaded, and the run kept landing on it. Each entry is judged
    before it is loaded, as a navigation is; a denied one is recorded and skipped, never loaded."""
    from personalclaw.browse.page import CdpPageDriver

    page_a, denied = "https://allowed.example/a", "https://denied.example/secret"
    transport = _History([(1, page_a), (2, denied), (3, cdp.BLANK_URL)], current=2)
    session = await _started(transport)
    sel_rows.clear()
    await CdpPageDriver(session.page_transport).go_back()

    assert _history_loads(transport) == [1]
    assert [(b.host, b.frame) for b in session.blocks] == [("denied.example", False)]
    assert _sel(sel_rows) == [("Page.navigateToHistoryEntry", "preflight")]
    assert session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_when_every_earlier_page_is_denied_loads_nothing_and_says_why():
    """🔴 Red before: the denied entry was loaded. Nothing is loaded, and the run is told why."""
    from personalclaw.browse.page import CdpPageDriver, PageActionError

    transport = _History([(1, "https://denied.example/a"), (2, cdp.BLANK_URL)], current=1)
    session = await _started(transport)
    with pytest.raises(PageActionError, match="denied.example"):
        await CdpPageDriver(session.page_transport).go_back()

    assert _history_loads(transport) == []
    assert [b.host for b in session.blocks] == ["denied.example"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_judges_an_entry_by_the_document_rule(sel_rows):
    """An entry is judged as a committed document is (``_document_url``): a blank entry is no
    network document and is loaded unjudged, as before; a ``data:`` entry is judged and denied."""
    from personalclaw.browse.page import CdpPageDriver

    here = "https://allowed.example/b"
    transport = _History([(1, cdp.BLANK_URL), (2, "data:text/html,x"), (3, here)], current=2)
    session = await _started(transport)
    sel_rows.clear()
    await CdpPageDriver(session.page_transport).go_back()

    assert _history_loads(transport) == [1]
    assert [b.url for b in session.blocks] == ["data:text/html,x"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_on_a_session_never_started_is_refused_and_reads_nothing():
    """As a navigation is (:func:`test_navigate_before_start_is_refused`): the in-page guard is not
    installed, so the earlier page would load unguarded. The driver used to send it through the
    page's transport unchecked; the session now refuses it before it reads the history."""
    transport = _History([(1, "https://allowed.example/a"), (2, "https://allowed.example/b")], 1)
    session = _session(transport)

    outcome = await session.go_back()

    assert outcome.ok is False and outcome.allowed is False
    assert "never started" in outcome.reason
    assert transport.sent == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_waits_for_a_running_teardown_and_reads_nothing_once_it_quarantined():
    """GO_BACK waits for a block before it reads the page's history, as every page command does,
    and a teardown that quarantines the session refuses it: the history of a page whose denied
    document may still be loaded is not read, and nothing is loaded."""
    import asyncio

    transport = _History([(1, "https://allowed.example/a"), (2, "https://allowed.example/b")], 1)
    session = await _started(transport)
    stop = asyncio.get_running_loop().create_future()
    transport.page_hold[cdp.STOP_LOADING] = stop
    commit = transport.dispatch(cdp.FRAME_NAVIGATED, _frame("https://denied.example/top"), None)
    for _ in range(5):
        await asyncio.sleep(0)
    back = asyncio.ensure_future(session.go_back())
    for _ in range(10):
        await asyncio.sleep(0)
    assert "Page.getNavigationHistory" not in transport.methods, "read while a teardown ran"

    stop.set_exception(RuntimeError("the browser rejected the command: boom"))
    await commit
    outcome = await back

    assert session.quarantine_reason and outcome.ok is False and outcome.allowed is False
    assert "Page.getNavigationHistory" not in transport.methods and _history_loads(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_waits_for_a_block_that_began_while_the_entry_was_judged():
    """The wait is made again after the entry's judgement, which yields, as navigate() makes it
    (:func:`test_a_navigation_during_a_frame_blank_follows_it`): a frame's blank that begins while
    the entry is judged holds the history load until the blank ends."""
    import asyncio

    page_a = "https://allowed.example/a"
    transport = _History([(1, page_a), (2, "https://allowed.example/b")], 1)
    session = await _started(transport)
    gate = asyncio.Event()

    async def gated(fn, *args):
        if args[0] == page_a:
            await gate.wait()
        return fn(*args)

    session._blocking = gated
    back = asyncio.ensure_future(session.go_back())
    for _ in range(10):
        await asyncio.sleep(0)
    assert "Page.getNavigationHistory" in transport.methods and not back.done()
    blank = asyncio.get_running_loop().create_future()
    transport.frame_hold = blank
    commit = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    for _ in range(10):
        await asyncio.sleep(0)
    assert session.tearing_down, "the frame's blank did not begin"

    gate.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert _history_loads(transport) == [], "the earlier page was loaded while a frame's blank ran"

    blank.set_result(None)
    await commit
    assert (await back).ok is True
    assert _history_loads(transport) == [1]


# ── one blank of a frame at a time ───────────────────────────────────────────


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frames_blank_at_load_waits_for_its_early_blank_still_running():
    """Sent beside each other, the browser answers one of two blanks of the same frame "aborted",
    which reads as a blank that loaded nothing and costs the whole page. So the load-time blank
    waits for the early one, then goes out, and the page is never blanked whole."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    early = asyncio.get_running_loop().create_future()
    transport.frame_hold = early

    transport.dispatch(cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert _frame_blanks(transport) == ["CHILD1"], "the early blank was not sent"
    commit = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert _frame_blanks(transport) == [
        "CHILD1"
    ], "the load-time blank went out beside the early one"

    early.set_result(None)
    await commit
    await session.wait_idle()
    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert cdp.STOP_LOADING not in transport.methods and _top_blanks(transport) == 0


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_unanswered_early_blank_holds_the_page_no_longer_than_its_bound(monkeypatch):
    """Nothing of the frame has been shown, so a hold past the bound protects nothing: a read of
    the page goes out once it passes, and the blank is still answered later."""
    import asyncio

    monkeypatch.setattr(cdp, "EARLY_BLANK_HOLD", 0.05)
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    early = asyncio.get_running_loop().create_future()
    transport.frame_hold = early

    transport.dispatch(cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert session.tearing_down, "the early blank did not hold the page"
    assert await asyncio.wait_for(session.page_transport.send("Runtime.evaluate", {}), 1.0) == {}

    early.set_result(None)
    await session.wait_idle()
    assert session.quarantine_reason == "" and cdp.STOP_LOADING not in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_early_blank_judged_after_its_frame_was_blanked_at_load_is_not_sent(sel_rows):
    """The head start came too late: the frame was already blanked when it loaded, so a second
    blank would only abort that one. Blanked once, recorded once."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()
    calls = {"n": 0}

    async def first_is_slow(fn, *args):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(0.1)
        return fn(*args)

    session._blocking = first_is_slow
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1"]
    assert len(sel_rows) == 1 and len(session.blocks) == 1


# ── no head start while the session's own page navigation is in flight ─────────


@pytest.mark.security
@pytest.mark.asyncio
async def test_no_frame_is_blanked_as_it_starts_while_the_sessions_page_navigation_is_in_flight():
    """🔴 Red before: a frame of the page being left was blanked as its navigation started, the
    browser left that blank and a later blank of the same frame unanswered as the page was
    replaced, and the whole page was blanked. While a page navigation the session sent has not
    committed, a frame's start is only remembered; the frame is still blanked when it loads, and
    once the page has committed, frames are blanked as they start again."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    transport.replies[cdp.NAVIGATE] = {"frameId": "TOP", "loaderId": "L2"}
    session = await _started(transport)
    held = asyncio.get_running_loop().create_future()
    transport.page_hold[cdp.NAVIGATE] = held
    nav = asyncio.ensure_future(session.navigate("https://allowed.example/next"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert (cdp.NAVIGATE, {"url": "https://allowed.example/next"}) in transport.sent

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == [], "a frame was blanked as it started, mid navigation"
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None))
    assert _frame_blanks(transport) == ["CHILD1"], "the frame was not blanked when it loaded"

    held.set_result(None)
    assert (await nav).ok is True
    # Answered, not yet committed: still in flight.
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD2", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1"]

    await _events(
        session,
        transport,
        (
            cdp.FRAME_NAVIGATED,
            {"frame": {"id": "TOP", "url": "https://allowed.example/next"}},
            None,
        ),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD3", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1", "CHILD3"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {"frameId": "TOP"},
        {"frameId": "TOP", "loaderId": "L2", "errorText": "net::ERR_ABORTED"},
        {"frameId": "TOP", "loaderId": "L2", "isDownload": True},
    ],
    ids=["same-document", "loaded-nothing", "download"],
)
async def test_a_page_navigation_that_brings_no_new_document_ends_at_its_answer(reply):
    """Vacuity partner: a navigation within the document, one that loaded nothing, and a download
    commit no new document, so the head start comes back as soon as the browser answers."""
    transport = FakeTransport(frames=(PAGE,))
    transport.replies[cdp.NAVIGATE] = reply
    session = await _started(transport)
    await session.navigate("https://allowed.example/next")

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_second_blank_of_a_frame_waits_for_the_first_still_running():
    """Sent beside each other, the browser answers one of two blanks of the same frame "aborted",
    which reads as a blank that loaded nothing and costs the whole page. A frame that commits a
    second denied document while its first blank runs is blanked again after it, never beside
    it, and the page is never blanked whole."""
    import asyncio

    transport = FakeTransport()
    session = await _started(transport)
    first = asyncio.get_running_loop().create_future()
    transport.frame_hold = first

    one = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    two = transport.dispatch(
        cdp.TARGET_INFO_CHANGED, _info("CHILD1", "https://denied.example/other"), None
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert _frame_blanks(transport) == ["CHILD1"], "the second blank went out beside the first"

    first.set_result(None)
    await one
    await two
    await session.wait_idle()
    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert cdp.STOP_LOADING not in transport.methods and _top_blanks(transport) == 0


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_second_blank_waits_for_the_first_no_longer_than_its_bound(monkeypatch):
    """A first blank the browser never answers must not hold the second forever: past the bound
    it is sent anyway, so the frame is still blanked."""
    import asyncio

    monkeypatch.setattr(cdp, "FRAME_BLANK_WAIT", 0.05)
    transport = FakeTransport()
    session = await _started(transport)
    first = asyncio.get_running_loop().create_future()
    transport.frame_hold = first

    one = transport.dispatch(cdp.FRAME_NAVIGATED, _child_frame(DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    two = transport.dispatch(
        cdp.TARGET_INFO_CHANGED, _info("CHILD1", "https://denied.example/other"), None
    )
    await asyncio.wait_for(two, 1.0)
    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]

    first.set_result(None)
    await one
    await session.wait_idle()
    assert session.quarantine_reason == ""


def test_a_second_blank_of_a_frame_waits_for_the_first_two_seconds_at_most():
    """The documented bound, as a literal: the test above shortens it, so only this pins it."""
    assert cdp.FRAME_BLANK_WAIT == 2.0


# ── what the session and the guide say it leaves open ────────────────────────


def _flat(text: str) -> str:
    return " ".join(text.split())


def _guide_entry() -> str:
    """The limitations guide's entry on what a page in the browser loads on its own."""
    from pathlib import Path

    guide = Path(__file__).resolve().parents[1] / "docs" / "security" / "limitations.md"
    text = guide.read_text(encoding="utf-8")
    start = text.index("- **What a page in the browser loads on its own.**")
    return _flat(text[start : text.index("\n\n", start)])


@pytest.mark.security
def test_the_guide_names_every_frame_that_runs_before_it_is_blanked():
    """🔴 Red before: the guide named one window, a frame that moves itself, so a reader took it
    that a denied frame that does not move never runs. Every frame judged only once its document
    has loaded runs its first scripts before the blank, and a frame in a process of its own runs
    without the in-page guard; the guide says both."""
    entry = _guide_entry()
    for case in (
        "the page's own process",
        "every frame from another site",
        "redirected",
        "navigated again",
        "inside a sandboxed frame",
        "first scripts before they are blanked",
        "without the in-page guard",
    ):
        assert case in entry, f"the guide does not name {case!r}: {entry}"


@pytest.mark.security
def test_the_residual_list_says_a_frame_navigated_again_is_judged_only_when_it_commits():
    """🔴 Red before: the session's residual list named a frame that moves itself, and the BROWSE
    profile said a denied frame in a process of its own is blanked before its document loads.
    That holds for its first document there: navigated again, by itself or by its page, it is
    not attached again, so it is not paused, and is judged when it commits.

    The residual list is the session's own account of what it does not stop, read as it reads
    (``cdp.__doc__``). What it describes is driven above: a frame in a process of its own that
    navigates again is judged at its commit, by itself
    (``test_a_frame_that_moves_itself_to_a_denied_host_is_blanked``) or on its own session
    (``test_a_frames_own_commit_on_its_session_is_blanked_by_its_id``). The same sentence in a
    comment of ``net/policy.py`` is no subject of a test."""
    residual = _flat(cdp.__doc__ or "")
    assert "navigated again, by itself or by its page" in residual, residual


@pytest.mark.security
def test_the_code_and_the_guide_say_a_page_target_already_running_is_left_running():
    """A page-type target attached paused is held; one that attached already running was never
    paused, and nothing of it is judged or stopped. The residual list and the guide both say so,
    so neither reads as if every page the browser prepares were held."""
    residual = _flat(cdp.__doc__ or "")
    assert "attaches already running" in residual and "prerendered" in residual, residual
    entry = _guide_entry()
    assert "prerendered" in entry and "left running, unjudged" in entry, entry


@pytest.mark.security
def test_the_guide_says_what_is_held_loads_once_the_session_ends():
    """🔴 Red before: the guide said a page the browser prepares out of sight is held and never
    loaded, while the residual list says a target left paused, a page-type one among them, loads
    once the socket closes, as a frame still being judged does. The guide says what the code
    does: held while the run's session lasts, loaded once it has ended."""
    residual = _flat(cdp.__doc__ or "")
    assert "load once the socket closes" in residual, residual
    entry = _guide_entry()
    assert "never loaded" not in entry, entry
    for case in ("held while the run's session lasts", "still held when the session ends"):
        assert case in entry, f"the guide does not say {case!r}: {entry}"


@pytest.mark.security
def test_the_code_says_a_page_leaving_by_its_own_navigation_does_not_hold_the_early_blank_back():
    """🔴 Red before: the early blank is held back only while a page navigation the session sent is
    in flight, though a page that leaves by a navigation of its own is replaced as surely. The
    residual list says so, so no reader takes the rule for one that covers every page."""
    residual = _flat(cdp.__doc__ or "")
    assert "leaves by a navigation of its own can lose a frame's blank" in residual, residual


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_navigation_sent_while_a_frames_start_is_judged_stops_its_early_blank():
    """The judgement can wait on a host lookup, and the session can send its page navigation
    meanwhile: the frame's start was judged before the navigation, and its blank would land in it.
    So whether a page navigation is in flight is asked after the judgement, not before."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    transport.replies[cdp.NAVIGATE] = {"frameId": "TOP", "loaderId": "L2"}
    session = await _started(transport)
    gate = asyncio.Event()

    async def slow_for_frames(fn, *args):
        if args and "secret.allowed.example" in str(args[0]):
            await gate.wait()
        return fn(*args)

    session._blocking = slow_for_frames
    start = transport.dispatch(
        cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert (await session.navigate("https://allowed.example/next")).ok is True
    gate.set()
    await start
    await session.wait_idle()

    assert _frame_blanks(transport) == [], "the frame was blanked during the page navigation"


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_commit_reported_before_its_answer_ends_the_navigation():
    """The browser can report the new document's commit before it answers the navigation. That
    commit is the end of it: the answer that follows must not mark the page in flight again, or no
    frame of the new page would ever be blanked as it starts."""
    transport = FakeTransport(frames=(PAGE,))
    transport.replies[cdp.NAVIGATE] = {"frameId": "TOP", "loaderId": "L2"}
    transport.events_on[cdp.NAVIGATE] = [
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": "https://allowed.example/next"}}, None)
    ]
    session = await _started(transport)
    assert (await session.navigate("https://allowed.example/next")).ok is True

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_page_navigation_whose_send_failed_stays_in_flight_until_a_page_commits():
    """A send that raised may have reached the browser, so the page may be being replaced: no
    frame is blanked as it starts until a top-level document commits. The frame is still blanked
    when it loads, and once a page commits, frames are blanked as they start again."""
    transport = FakeTransport(fail_on={cdp.NAVIGATE}, frames=(PAGE,))
    session = await _started(transport)
    assert (await session.navigate("https://allowed.example/next")).ok is False
    transport._fail_on = set()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == [], "a frame was blanked as it started, mid navigation"
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None))
    assert _frame_blanks(transport) == ["CHILD1"], "the frame was not blanked when it loaded"

    await _events(
        session,
        transport,
        (
            cdp.FRAME_NAVIGATED,
            {"frame": {"id": "TOP", "url": "https://allowed.example/next"}},
            None,
        ),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD3", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1", "CHILD3"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_is_a_page_navigation_in_flight_until_a_page_commits():
    """Going back replaces the page as a navigation does: no frame is blanked as it starts until
    a top-level document commits. The frame is still blanked when it loads, and once a page
    commits, frames are blanked as they start again."""
    from personalclaw.browse.page import CdpPageDriver

    page_a, page_b = "https://allowed.example/a", "https://allowed.example/b"
    transport = _History([(1, page_a), (2, page_b)], current=1)
    transport.frames = (page_b,)
    session = await _started(transport)
    await CdpPageDriver(session.page_transport).go_back()
    assert _history_loads(transport) == [1]

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == [], "a frame was blanked as it started, mid navigation"
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None))
    assert _frame_blanks(transport) == ["CHILD1"], "the frame was not blanked when it loaded"

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": page_a}}, None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD3", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1", "CHILD3"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_as_it_started_and_again_at_load_is_one_denial_not_a_repeat(sel_rows):
    """The early blank and the blank at load are one denial of one frame: one block, one SEL row,
    and not counted as a second block of its host."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert [(b.host, b.repeats, b.blanked) for b in session.blocks] == [
        ("secret.allowed.example", 0, cdp.BLANKED_FRAME)
    ]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_counted_on_its_host_is_counted_once_for_its_early_and_load_blanks(sel_rows):
    """A second frame from a host the page already blocked is counted on that block once, though
    it is blanked both as it starts and when it loads."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED, fid="C0"), None)
    )
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("C2", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED, fid="C2"), None),
    )

    assert _frame_blanks(transport) == ["C0", "C2", "C2"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 1)]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_whose_blank_at_load_fails_after_its_early_blank_is_one_page_block(sel_rows):
    """The early blank landed, then the blank at load failed, so the whole page went: that is one
    denial, recorded once and told as the whole page."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    transport.frame_fail = ["the browser rejected the command: refused"]
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None))

    assert [(b.host, b.blanked) for b in session.blocks] == [
        ("secret.allowed.example", cdp.BLANKED_PAGE)
    ]
    assert len(sel_rows) == 1
    assert cdp.STOP_LOADING in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("page", [PAGE, cdp.BLANK_URL], ids=["another-site", "page-of-no-site"])
async def test_a_frame_not_from_the_pages_site_waits_for_its_attach_as_it_starts(page, sel_rows):
    """🔴 Red before: a frame from another site was blanked as its navigation started, which on a
    browser that gives each site its own process kept the browser from moving it into one: it was
    never attached, so never paused before its document loaded. It now gets no blank as it starts,
    nor does any frame of a page whose site is unknown; it is paused at its attach and blanked
    there, never let run, as it was without the early blank."""
    transport = FakeTransport(frames=(page,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session, transport, (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", DENIED), None)
    )
    assert _frame_blanks(transport) == [], "a frame from another site was blanked as it started"

    await _events(session, transport, (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1"), None))
    assert _frame_blanks(transport) == ["F1"]
    assert transport.child_sent == []
    assert _sel(sel_rows) == [(cdp.ATTACHED_TO_TARGET, "frame_attached")]


def test_a_frames_site_is_the_last_two_labels_of_its_documents_host():
    """The rule the early blank is given by, on the addresses it meets."""
    assert cdp._same_site("https://secret.allowed.example/f", "https://allowed.example/p")
    assert cdp._same_site("blob:https://allowed.example/1", "https://www.allowed.example/")
    assert cdp._same_site("https://ALLOWED.example.:8443/f", "https://allowed.example/p")
    assert not cdp._same_site("https://denied.example/f", "https://allowed.example/p")
    assert not cdp._same_site("https://allowedexample.com/f", "https://allowed.example/p")
    for unknown in (cdp.BLANK_URL, "about:srcdoc", "data:text/html,x", ""):
        assert not cdp._same_site("https://allowed.example/f", unknown)
        assert not cdp._same_site(unknown, "https://allowed.example/p")
        assert not cdp._same_site(unknown, cdp.BLANK_URL), "two unknown sites read as one"


def test_a_frames_site_counts_its_scheme_and_an_ip_addresss_whole_host():
    """🔴 Red before: ``http:`` and ``https:`` of one host, and two IP addresses that end in the
    same two numbers, read as one site, so a frame the browser puts in a process of its own was
    blanked as it started, which can keep it from that process and from the pause before its
    document loads. Each is now another site, as the browser has it; a port is no part of one."""
    assert not cdp._same_site("http://allowed.example/f", "https://allowed.example/p")
    assert not cdp._same_site("https://10.0.1.1/f", "https://93.184.1.1/p")
    assert not cdp._same_site("https://[::ffff:10.0.1.1]/f", "https://[::ffff:93.184.1.1]/p")
    assert not cdp._same_site("https://[::1]/f", "https://[::2]/p")
    assert cdp._same_site("https://93.184.1.1:8443/f", "https://93.184.1.1/p")
    assert cdp._same_site("http://localhost:9000/f", "http://localhost/p")
    assert not cdp._same_site("http://a.localhost/f", "http://b.localhost/p")


def test_a_frames_site_under_a_public_suffix_of_two_labels_reads_as_the_pages():
    """The rule's stated shortfall, pinned so the module's residual stays true: two labels is
    short of the browser's own rule under a public suffix of more than one label. Such a frame is
    blanked as it starts and judged at load as every frame is
    (:func:`test_a_frame_read_as_the_pages_site_is_judged_at_its_attach_and_at_its_commit`)."""
    assert cdp._same_site("https://other.co.uk/f", "https://example.co.uk/p")
    assert cdp._same_site("https://other.com.au/f", "https://example.com.au/p")
    assert "co.uk" in cdp.__doc__ and "com.au" in cdp.__doc__


@pytest.mark.security
@pytest.mark.asyncio
async def test_the_site_a_frame_is_told_against_is_the_page_that_committed_last(sel_rows):
    """The page the session started on is not the page it is on after a commit: a frame is told
    as the page's own site or another by the page's latest document, both ways."""
    transport = FakeTransport(frames=(cdp.BLANK_URL,))
    session = await _started(transport)

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": PAGE}}, None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1"], "the page's new site was not read"

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": cdp.BLANK_URL}}, None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD2", SAME_SITE_DENIED), None),
    )
    assert _frame_blanks(transport) == ["CHILD1"], "a frame was told against the page it left"


@pytest.mark.security
@pytest.mark.asyncio
async def test_the_pages_site_is_never_read_from_a_frames_commit():
    """The site a frame is told against is the page's: a frame of the page committing a document
    of another site, on the page's session or on a session of its own, changes nothing of it."""
    other = "https://redirected.example/frame"
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, _child_frame(other, fid="C0"), None),
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "OOP1", "url": other}}, "S1"),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1"], "the page's site was read from a frame"


class _ReportsItsBlanks(FakeTransport):
    """A browser that reports a frame's blank as a navigation of that frame starting, as it
    reports every navigation (``Page.frameStartedNavigating``), the session's own among them."""

    async def send(
        self, method: str, params: dict | None = None, *, session_id: str | None = None
    ) -> dict:
        params = dict(params or {})
        if session_id is None and method == cdp.NAVIGATE and "frameId" in params:
            if not self.frame_fail:
                start = _started_nav(params["frameId"], params["url"])
                await self.listener(cdp.FRAME_STARTED_NAVIGATING, start, None)
        return await super().send(method, params, session_id=session_id)


def _loaded(load: str, fid: str, url: str) -> tuple:
    """How the browser reports a frame's document: attached with its URL, attached paused with no
    URL of its own (judged by where its navigation started), a target change, or a commit."""
    if load == "attach":
        return (cdp.ATTACHED_TO_TARGET, _attached("S1", fid, url), None)
    if load == "attach-with-no-url":
        return (cdp.ATTACHED_TO_TARGET, _attached("S1", fid, ""), None)
    if load == "target-change":
        return (cdp.TARGET_INFO_CHANGED, _info(fid, url), None)
    return (cdp.FRAME_NAVIGATED, _child_frame(url, fid=fid), None)


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("load", ["attach", "attach-with-no-url", "target-change"])
async def test_a_frame_blanked_as_it_started_and_again_in_a_process_of_its_own_is_one_denial(
    load, sel_rows
):
    """🔴 Red before, attached with no URL: the browser reports the early blank as the frame's own
    navigation starting, and that replaced where the frame was going, so the paused frame was judged
    as ``about:blank`` and let run. A frame of the page's own site the browser still gives a process
    of its own (an origin it isolates, or another site two labels do not tell apart) is blanked as
    it starts and again when it is reported there, judged by where it was going, and never let run:
    one block, one SEL row, not a repeat of its host."""
    transport = _ReportsItsBlanks(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", SAME_SITE_DENIED), None),
        _loaded(load, "F1", SAME_SITE_DENIED),
    )

    assert _frame_blanks(transport) == ["F1", "F1"]
    assert transport.child_sent == [], "the denied frame was let run"
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 0)]
    assert len(sel_rows) == 1


#: A page and a denied frame from another site, by the browser's rule. The site rule reads the
#: first two as one site (two labels short of a public suffix of more), the last as two.
_OTHER_SITE_PAIRS = [
    ("https://example.co.uk/page", "https://other.co.uk/frame"),
    ("https://example.com.au/page", "https://other.com.au/frame"),
    ("https://93.184.1.1/page", "https://10.0.1.1/frame"),
]


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("load", ["attach", "attach-with-no-url", "commit"])
@pytest.mark.parametrize("page,frame", _OTHER_SITE_PAIRS, ids=["co-uk", "com-au", "ip-address"])
async def test_a_frame_read_as_the_pages_site_is_judged_at_its_attach_and_at_its_commit(
    page, frame, load, operator_egress, monkeypatch, sel_rows
):
    """Differential: the site rule chooses only how a denied frame is caught first, never whether
    it is judged. Read as the page's own site or as another, the frame is judged when the browser
    attaches it (paused, and never let run, also when it is attached with no URL of its own after
    the browser reported the early blank as the frame's navigation) and when it commits, and is
    one denial; the same holds without the early blank."""
    for host in ("example.co.uk", "other.co.uk", "example.com.au", "other.com.au"):
        monkeypatch.setitem(_DNS, host, ["93.184.216.40"])
    # An address is looked up as itself; the private one is denied by the guard's own stance.
    for address in ("93.184.1.1", "10.0.1.1"):
        monkeypatch.setitem(_DNS, address, [address])
    operator_egress.deny_hosts = [*operator_egress.deny_hosts, "other.co.uk", "other.com.au"]
    transport = _ReportsItsBlanks(frames=(page,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session, transport, (cdp.FRAME_STARTED_NAVIGATING, _started_nav("F1", frame), None)
    )
    early = len(_frame_blanks(transport))
    await _events(session, transport, _loaded(load, "F1", frame))

    assert _frame_blanks(transport)[early:] == ["F1"], "the frame was not judged when it loaded"
    assert transport.child_sent == [] and _top_blanks(transport) == 0
    assert [b.repeats for b in session.blocks] == [0] and len(sel_rows) == 1
    assert session.quarantine_reason == ""


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("load", ["commit", "attach"])
async def test_a_frame_that_loads_the_same_denied_url_again_is_a_second_denial(load, sel_rows):
    """🔴 Red before: the one-denial key was left by every load-time check, so a frame that loaded
    the same denied URL again on the page (re-pointed there by its page, or reloading itself) was
    handed the first block and counted nowhere. Only a navigation's start leaves the key now; a
    second load is counted on the first, as a second frame from that host is."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()
    if load == "commit":
        events = [
            (cdp.FRAME_NAVIGATED, _child_frame(url, fid="F1"), None)
            for url in (DENIED, cdp.BLANK_URL, DENIED)
        ]
    else:
        events = [
            (cdp.ATTACHED_TO_TARGET, _attached("S1", "F1", DENIED), None),
            (cdp.TARGET_INFO_CHANGED, _info("F1", cdp.BLANK_URL), None),
            (cdp.TARGET_INFO_CHANGED, _info("F1", DENIED), None),
        ]

    await _events(session, transport, *events)

    assert _frame_blanks(transport) == ["F1", "F1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("denied.example", 1)]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_whose_second_load_of_a_denied_url_costs_the_page_is_recorded_again(
    sel_rows,
):
    """🔴 Red before: the frame's second load was handed its first block, so when its blank failed
    and the whole page went, the audit log was told nothing more and the first block was rewritten
    as the page's. The second is recorded after all, as the whole page, beside the first."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="F1"), None),
        (cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL, fid="F1"), None),
    )
    transport.frame_fail = ["the browser rejected the command: refused"]

    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(DENIED, fid="F1"), None))

    assert [(b.host, b.blanked) for b in session.blocks] == [
        ("denied.example", cdp.BLANKED_FRAME),
        ("denied.example", cdp.BLANKED_PAGE),
    ]
    assert len(sel_rows) == 2 and cdp.STOP_LOADING in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_as_it_starts_twice_on_one_page_is_two_denials(sel_rows):
    """🔴 Red before: an early blank that landed leaves no load of the denied URL to take its key,
    and the frame's next start there was handed it and counted nowhere. Each start is a denial of
    its own; the second is counted on the first."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 1)]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_early_blank_on_one_document_is_not_the_denial_of_a_load_on_the_next(sel_rows):
    """A new document starts over: an early blank whose frame never loaded the denied URL on the
    page it left is not what a load of that URL on the next page is told as."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": PAGE}}, None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert len(session.blocks) == 2 and len(sel_rows) == 2


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_as_it_starts_while_a_restored_pages_frames_are_judged_is_one_denial(
    sel_rows,
):
    """A frame of a page restored from the back-forward cache can start navigating while the
    restored frames are read and judged: blanked as it starts and again as a restored frame, it is
    one denial, not a repeat of its host."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()
    transport.frames = (PAGE, SAME_SITE_DENIED)
    transport.events_on[cdp.GET_FRAME_TREE] = [
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD0", SAME_SITE_DENIED), None)
    ]
    judged: list[str] = []

    async def the_restored_frames_judgement_is_slower(fn, *args):
        if args and "secret.allowed.example" in str(args[0]):
            judged.append(str(args[0]))
            if len(judged) == 1:
                await asyncio.sleep(0.05)
        return fn(*args)

    session._blocking = the_restored_frames_judgement_is_slower
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _restore(PAGE), None))

    assert len(judged) == 2, "the frame was not judged both as it started and as restored"
    assert _frame_blanks(transport) == ["CHILD0", "CHILD0"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 0)]
    assert len(sel_rows) == 1


# ── a head start is one denial with the load it was for, and no other ─────────


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("again", ["mid-navigation", "redirected", "judged-first"])
async def test_a_later_load_of_the_url_a_landed_early_blank_was_for_is_a_second_denial(
    again, sel_rows
):
    """🔴 Red before: an early blank that landed left its block for the frame's load of that URL,
    and the frame committed only the blank, so the block stayed until the page's next document. A
    later load of the URL by the frame took it: re-pointed there while the session's navigation was
    in flight, redirected there from an allowed page of the site, or loaded again with its own head
    start judged after its commit. The document that loaded and ran was counted nowhere. The
    frame's next document settles its head start, so the later load is counted on the first block,
    as a second load of a denied URL always was."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    transport.replies[cdp.NAVIGATE] = {"frameId": "TOP", "loaderId": "L2"}
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None),
    )
    started = SAME_SITE_DENIED
    if again == "mid-navigation":
        assert (await session.navigate("https://allowed.example/next")).ok is True
    elif again == "redirected":
        started = ALLOWED
    else:
        calls = {"n": 0}

        async def the_start_is_judged_after_the_commit(fn, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(0.05)
            return fn(*args)

        session._blocking = the_start_is_judged_after_the_commit

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", started), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 1)]
    assert _sel(sel_rows) == [(cdp.FRAME_STARTED_NAVIGATING, "frame_started")]


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("again", ["mid-navigation", "redirected"])
async def test_a_later_load_of_the_url_a_landed_early_blank_was_for_that_costs_the_page_is_recorded(
    again, sel_rows
):
    """🔴 Red before: the later load took the early blank's block, so when its own blank failed and
    the whole page went, no row said so and the early blank's block was rewritten as the page's.
    The early blank stays the frame's block, and the load is recorded after all, as the whole page,
    with a row of its own."""
    transport = FakeTransport(frames=(PAGE,))
    transport.replies[cdp.NAVIGATE] = {"frameId": "TOP", "loaderId": "L2"}
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None),
    )
    started = SAME_SITE_DENIED
    if again == "mid-navigation":
        assert (await session.navigate("https://allowed.example/next")).ok is True
    else:
        started = ALLOWED
    await _events(
        session, transport, (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", started), None)
    )
    transport.frame_fail = ["the browser rejected the command: refused"]

    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None))

    assert [(b.host, b.blanked) for b in session.blocks] == [
        ("secret.allowed.example", cdp.BLANKED_FRAME),
        ("secret.allowed.example", cdp.BLANKED_PAGE),
    ]
    assert _sel(sel_rows) == [
        (cdp.FRAME_STARTED_NAVIGATING, "frame_started"),
        (cdp.FRAME_NAVIGATED, "frame_committed"),
    ]
    assert cdp.STOP_LOADING in transport.methods


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True], ids=["blanked", "costs-the-page"])
@pytest.mark.parametrize(
    "started",
    [SAME_SITE_DENIED + "#part", "https://secret.allowed.example/hop"],
    ids=["fragment", "redirect-on-the-host"],
)
async def test_one_navigation_named_differently_as_it_starts_and_commits_is_one_denial(
    started, fails, sel_rows
):
    """🔴 Red before: the early blank's block was kept under the URL the navigation started with,
    and its commit names the document's own address: without the fragment, or where a redirect on
    the same host ended. So one navigation was two denials, a repeat, or two blocks and two rows
    when its blank at load cost the page. A frame's head start is taken by the frame's next denial
    of the same host: one block, one row."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()
    await _events(
        session, transport, (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", started), None)
    )
    if fails:
        transport.frame_fail = ["the browser rejected the command: refused"]

    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None))

    blanked = cdp.BLANKED_PAGE if fails else cdp.BLANKED_FRAME
    assert [(b.host, b.repeats, b.blanked) for b in session.blocks] == [
        ("secret.allowed.example", 0, blanked)
    ]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_one_navigation_reported_as_started_twice_is_one_denial(sel_rows):
    """🔴 Red before: a frame's navigation reported as starting twice, each start judged while the
    other was, was recorded for each start: two denials of one navigation, one counted on the other.
    A start whose frame still has an unsettled head start for the same host is that denial."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    async def a_lookup_takes_a_moment(fn, *args):
        await asyncio.sleep(0.01)
        return fn(*args)

    session._blocking = a_lookup_takes_a_moment
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport)[-1] == "CHILD1"
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 0)]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frames_load_takes_its_head_starts_block_once(sel_rows):
    """The frame's load-time check of the navigation its early blank was for is that denial, once:
    a later denial of the same host by the frame is counted on it, as any is."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED + "?again"), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1", "CHILD1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 1)]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_start_while_the_frames_early_blank_is_unanswered_sends_no_second_blank(sel_rows):
    """Two blanks of one frame in flight make the browser answer one "aborted": a start of a frame
    whose early blank is still unanswered is left to that blank, and to the frame's check when it
    loads. Nothing more is sent or recorded."""
    import asyncio

    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()
    early = asyncio.get_running_loop().create_future()
    transport.frame_hold = early
    transport.dispatch(cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert _frame_blanks(transport) == ["CHILD1"]

    again = _started_nav("CHILD1", "https://secret.allowed.example/again")
    transport.dispatch(cdp.FRAME_STARTED_NAVIGATING, again, None)
    for _ in range(5):
        await asyncio.sleep(0)
    early.set_result(None)
    await session.wait_idle()

    assert _frame_blanks(transport) == ["CHILD1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 0)]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_when_it_loaded_is_still_blanked_as_it_starts_later():
    """An early blank is skipped only for a blank at load sent while its own start was judged: a
    frame blanked when it loaded earlier on the page is blanked as its next navigation starts."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]


@pytest.mark.security
@pytest.mark.asyncio
async def test_an_early_blank_past_its_bound_lifts_its_hold_once(monkeypatch):
    """The early blank lifts its hold when its bound passes, and not again when it is answered: a
    later block's hold must still hold page commands."""
    import asyncio

    monkeypatch.setattr(cdp, "EARLY_BLANK_HOLD", 0.02)
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    early = asyncio.get_running_loop().create_future()
    transport.frame_hold = early
    transport.dispatch(cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None)
    await asyncio.sleep(0.1)
    early.set_result(None)
    await session.wait_idle()

    session._hold()
    assert session.tearing_down, "a block's hold no longer holds page commands"
    session._release()
    assert not session.tearing_down


def test_an_early_blank_holds_the_page_two_seconds_at_most():
    """The documented bound, as a literal: the tests above shorten it, so only this pins it."""
    assert cdp.EARLY_BLANK_HOLD == 2.0


@pytest.mark.security
@pytest.mark.asyncio
async def test_the_pages_top_frame_is_the_one_that_committed_last():
    """A start of the frame that committed the page's latest document is the page's own navigation,
    judged when it commits, never blanked as a frame."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)

    await _events(
        session,
        transport,
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP2", "url": PAGE}}, None),
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("TOP2", SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == []


@pytest.mark.security
@pytest.mark.asyncio
async def test_going_back_whose_page_commit_comes_before_its_answer_ends_the_navigation():
    """Going back is in flight from its send: the browser can report the earlier page's commit
    before it answers, and that commit ends it, so a frame of the page is blanked as it starts."""
    page_a, page_b = "https://allowed.example/a", "https://allowed.example/b"
    transport = _History([(1, page_a), (2, page_b)], current=1)
    transport.frames = (page_b,)
    transport.events_on[cdp.NAVIGATE_TO_HISTORY_ENTRY] = [
        (cdp.FRAME_NAVIGATED, {"frame": {"id": "TOP", "url": page_a}}, None)
    ]
    session = await _started(transport)
    assert (await session.go_back()).ok is True

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1"], "going back left the page in flight for good"


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settled_by",
    [
        "blank-commit",
        "blank-commit-again",
        "allowed-commit",
        "blank-target-change",
        "allowed-target-change",
        "allowed-attach",
        "allowed-in-a-frame-tree",
        "blank-in-a-frame-tree",
    ],
)
async def test_a_frames_early_blank_is_settled_by_the_next_document_that_is_no_denial(
    settled_by, sel_rows
):
    """🔴 Red before: only a load of the very URL an early blank was for took its block, so the
    block outlived the blank landing, or the frame going elsewhere, and a later load of the URL by
    the frame took it and was counted nowhere. Any document the frame reports next that is no
    denial settles it, however it is reported: a commit (the blank's own, also when the frame
    commits the blank twice in a row and the browser reports it as the same document), a target
    change, an attach, or a frame tree read for a frame in a process of its own."""
    transport = FakeTransport(frames=(PAGE,))
    transport.replies[cdp.NAVIGATE] = {"frameId": "TOP", "loaderId": "L2"}
    session = await _started(transport)
    if settled_by == "blank-commit-again":
        await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None))
    sel_rows.clear()
    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
    )
    allowed_start = (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", ALLOWED), None)
    settle = {
        "blank-commit": [(cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None)],
        "blank-commit-again": [(cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None)],
        "allowed-commit": [(cdp.FRAME_NAVIGATED, _child_frame(ALLOWED), None)],
        "blank-target-change": [(cdp.TARGET_INFO_CHANGED, _info("CHILD1", cdp.BLANK_URL), None)],
        "allowed-target-change": [(cdp.TARGET_INFO_CHANGED, _info("CHILD1", ALLOWED), None)],
        # Re-pointed at an allowed page, which the browser attaches in a process of its own.
        "allowed-attach": [
            allowed_start,
            (cdp.ATTACHED_TO_TARGET, _attached("S1", "CHILD1", ALLOWED), None),
        ],
        "allowed-in-a-frame-tree": [
            (cdp.ATTACHED_TO_TARGET, _attached("R", "OOP1", ALLOWED, waiting=False), None)
        ],
        "blank-in-a-frame-tree": [
            (cdp.ATTACHED_TO_TARGET, _attached("R", "OOP1", ALLOWED, waiting=False), None)
        ],
    }[settled_by]
    nested = cdp.BLANK_URL if settled_by == "blank-in-a-frame-tree" else ALLOWED
    transport.child_replies[("R", cdp.GET_FRAME_TREE)] = _running_tree(
        "OOP1", ALLOWED, ("CHILD1", nested)
    )
    await _events(session, transport, *settle)
    assert (await session.navigate("https://allowed.example/next")).ok is True

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(SAME_SITE_DENIED), None),
    )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 1)]
    assert len(sel_rows) == 1


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frames_early_blank_is_not_the_denial_of_its_load_of_another_host(sel_rows):
    """Vacuity partner: the frame's next denial takes its early blank's block only for the same
    host. A frame blanked as it started for one denied host that loads another (its head start did
    not land, and the first host sent it on) is two denials, each recorded."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    await _events(
        session,
        transport,
        (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        (cdp.FRAME_NAVIGATED, _child_frame(DENIED), None),
    )

    assert [(b.host, b.repeats) for b in session.blocks] == [
        ("secret.allowed.example", 0),
        ("denied.example", 0),
    ]
    assert _sel(sel_rows) == [
        (cdp.FRAME_STARTED_NAVIGATING, "frame_started"),
        (cdp.FRAME_NAVIGATED, "frame_committed"),
    ]


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frame_blanked_as_it_starts_for_two_denied_hosts_is_two_denials(
    operator_egress, monkeypatch, sel_rows
):
    """Vacuity partner: a start of a frame whose early blank for another host was answered but
    not settled by a document yet is a denial of its own, blanked and recorded."""
    monkeypatch.setitem(_DNS, "vault.allowed.example", ["93.184.216.39"])
    operator_egress.deny_hosts = [*operator_egress.deny_hosts, "vault.allowed.example"]
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    for url in (SAME_SITE_DENIED, "https://vault.allowed.example/"):
        await _events(
            session, transport, (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", url), None)
        )

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [
        ("secret.allowed.example", 0),
        ("vault.allowed.example", 0),
    ]
    assert len(sel_rows) == 2


@pytest.mark.security
@pytest.mark.asyncio
async def test_a_frames_start_reported_again_before_any_document_of_it_is_the_same_denial(sel_rows):
    """🔴 Red before: a frame's navigation reported as starting again once its early blank was
    answered, before the frame reported any document, was recorded again and counted as a repeat.
    It is blanked again, and is the same denial: one block, one row. Once a document settles the
    early blank, the frame's next start is a denial of its own
    (:func:`test_a_frame_blanked_as_it_starts_twice_on_one_page_is_two_denials`)."""
    transport = FakeTransport(frames=(PAGE,))
    session = await _started(transport)
    sel_rows.clear()

    for _ in range(2):
        await _events(
            session,
            transport,
            (cdp.FRAME_STARTED_NAVIGATING, _started_nav("CHILD1", SAME_SITE_DENIED), None),
        )
    await _events(session, transport, (cdp.FRAME_NAVIGATED, _child_frame(cdp.BLANK_URL), None))

    assert _frame_blanks(transport) == ["CHILD1", "CHILD1"]
    assert [(b.host, b.repeats) for b in session.blocks] == [("secret.allowed.example", 0)]
    assert len(sel_rows) == 1
