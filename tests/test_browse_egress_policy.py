"""BROWSE-AUTOMATION §6.1 — the BROWSE egress profile.

The CDP pre-flight (§6.2, built alongside) is only as good as the policy it evaluates
against, and a policy that is not in ``_PROFILES`` fails INVISIBLY: ``get_policy`` returns
STRICT for an unknown name, so a missing registration looks like a working (merely tighter)
policy at every call site. That silent fallback is what most of this file is about.

All DNS is injected. No test here touches the resolver.
"""

from __future__ import annotations

import socket

import pytest

from personalclaw.net.guard import evaluate
from personalclaw.net.policy import BROWSE, STRICT, EgressPolicy, get_policy


def _resolver(mapping):
    """Fake DNS: host → [ips], gaierror for anything unmapped."""

    def _r(host):
        if host not in mapping:
            raise socket.gaierror(f"no such host {host}")
        return mapping[host]

    return _r


_PUBLIC = _resolver({"example.com": ["93.184.216.34"]})
_LAN = _resolver({"nas.local": ["192.168.1.50"]})
_LOOPBACK = _resolver({"localhost": ["127.0.0.1"]})


# ── registration: the assertion that catches the silent STRICT fallback ────────


def test_the_profile_is_registered_under_its_name():
    """Without a ``_PROFILES`` entry this returns STRICT and nothing else in the system
    complains — the browse surface would silently run on a 5 MB / 30 s / pinned-IP policy
    whose name never appears in an audit row."""
    assert get_policy("browse") is BROWSE
    assert BROWSE.name == "browse"


def test_the_lookup_actually_discriminates():
    """Vacuity check for the test above: ``get_policy`` always returns *something*, so the
    registration assertion only means anything if an unregistered name resolves elsewhere."""
    assert get_policy("definitely-not-a-profile") is STRICT
    assert get_policy("browse") is not STRICT


# ── the ceilings, each pinned at its exact number ──────────────────────────────


def test_every_raised_ceiling_differs_from_strict():
    """Asserted as inequalities AND at exact numbers below, so a drive-by retune reds and
    has to be argued for in review rather than drifting."""
    assert BROWSE.max_redirects > STRICT.max_redirects
    assert BROWSE.timeout_s > STRICT.timeout_s
    assert BROWSE.max_bytes > STRICT.max_bytes


def test_max_redirects_is_ten():
    """Consent walls / geo bounces / SSO hops exceed five on ordinary public sites, and
    every hop is re-evaluated by the guard (§6.2). Bounded, so a loop still terminates."""
    assert BROWSE.max_redirects == 10


def test_timeout_is_sixty_seconds():
    """A navigation is render + N subresource round-trips, not one request."""
    assert BROWSE.timeout_s == 60.0


def test_max_bytes_is_fifty_megabytes():
    """Sized for what our own code reads back over CDP (serialized DOM / text / screenshot).
    It does not meter Chrome's subresource bytes — see the §6.3 gap note in the profile."""
    assert BROWSE.max_bytes == 50_000_000


# ── pin_resolved_ip: the security judgement, pinned so a flip is deliberate ────


def test_pin_resolved_ip_is_false_because_chrome_owns_its_own_resolver():
    """DELIBERATE FALSE, not an oversight. ``pin_resolved_ip`` promises "the caller dials
    these exact validated IPs"; that promise is keepable only where we own the socket
    (``net/client``). Chrome re-resolves the hostname itself after the pre-flight and CDP
    has no connect-to-this-IP parameter, so the DNS-rebind window between our validation
    and Chrome's own resolution is real and this profile cannot close it. Declaring True
    would advertise a control the browse path does not implement. Flipping this to True
    must therefore come WITH a browser-level enforcement mechanism (request interception
    or a mandatory proxy) — this assertion exists to make that a conscious act."""
    assert BROWSE.pin_resolved_ip is False
    assert STRICT.pin_resolved_ip is True, "the contrast is the point"


def test_the_guard_still_returns_pinned_ips_as_evidence():
    """Independent of the flag: the pre-flight decision carries the addresses the host
    resolved to at check time. For BROWSE those are SEL evidence, not enforcement."""
    d = evaluate("https://example.com/page", BROWSE, resolver=_PUBLIC)
    assert d.allow is True
    assert d.pinned_ips == ["93.184.216.34"]


# ── stance: everything BROWSE deliberately did NOT relax ───────────────────────


def test_the_public_only_stance_is_unchanged():
    assert BROWSE.allow_private is False
    assert BROWSE.loopback_only is False
    assert BROWSE.allow_only is False
    assert BROWSE.on_violation == "deny"
    assert BROWSE.allow_hosts == ()
    assert BROWSE.deny_hosts == ()


def test_a_public_host_is_allowed_and_carries_the_sel_fields():
    d = evaluate("https://example.com/page", BROWSE, resolver=_PUBLIC)
    assert d.allow is True
    assert d.host == "example.com"
    assert d.url == "https://example.com/page"
    assert d.risk_level == "safe"


@pytest.mark.parametrize(
    "url,resolver",
    [
        ("http://nas.local/admin", _LAN),
        ("http://localhost:8080/", _LOOPBACK),
    ],
)
def test_a_private_or_loopback_host_is_denied_without_operator_opt_in(url, resolver):
    d = evaluate(url, BROWSE, resolver=resolver)
    assert d.allow is False
    assert d.risk_level == "destructive"
    assert "non-public address" in d.reason
    assert d.host in url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/html,<h1>x</h1>",
        "chrome://settings",
        "devtools://devtools/bundled/inspector.html",
        "view-source:https://example.com",
        "blob:https://example.com/abc",
    ],
)
def test_browser_only_schemes_are_refused_by_the_pre_flight(url):
    """``allow_schemes`` is inherited from the default but is load-bearing on this surface in
    a way it is not for a fetch profile: a browser understands all of these, and navigating
    to `file:` or `devtools:` is local-file read / debugger self-attach rather than egress.
    The pre-flight is therefore also the scheme gate."""
    d = evaluate(url, BROWSE, resolver=_resolver({}))
    assert d.allow is False
    assert "not allowed" in d.reason


def test_a_url_with_no_host_is_refused_before_any_resolution():
    def _explode(host):  # pragma: no cover - must never be called
        raise AssertionError("resolver must not be consulted for a hostless URL")

    assert evaluate("https://", BROWSE, resolver=_explode).allow is False


# ── operator control: BROWSE did not opt out of security.egress ────────────────
#
# ``egress_policy_for`` reads AppConfig lazily and best-effort, so these fake the config
# object rather than writing a config.json — the layering contract is what is under test.


class _FakeEgress:
    def __init__(self, allow_hosts=(), deny_hosts=(), allow_private=False):
        self.allow_hosts = list(allow_hosts)
        self.deny_hosts = list(deny_hosts)
        self.allow_private = allow_private


def _with_operator_egress(monkeypatch, eg: _FakeEgress):
    """Patch the loader ``egress_policy_for`` reaches for, not the function itself, so the
    real layering code (UNION of hosts, OR of allow_private) is the thing exercised."""

    class _Cfg:
        class security:  # noqa: N801 - mirrors the real attribute path
            egress = eg

        @staticmethod
        def load():
            return _Cfg

    monkeypatch.setattr("personalclaw.config.loader.AppConfig", _Cfg)


def test_an_operator_allow_host_makes_a_lan_host_reachable(monkeypatch):
    from personalclaw.net.policy import egress_policy_for

    _with_operator_egress(monkeypatch, _FakeEgress(allow_hosts=["nas.local"]))
    p = egress_policy_for(BROWSE)
    assert "nas.local" in p.allow_hosts
    assert p.name == "browse", "layering must not change which profile this is"
    assert p.max_bytes == BROWSE.max_bytes and p.pin_resolved_ip is False
    assert evaluate("http://nas.local/admin", p, resolver=_LAN).allow is True
    # ...and only that host: the rest of the private range is still blocked.
    other = evaluate(
        "http://other.local/x", p, resolver=_resolver({"other.local": ["192.168.1.51"]})
    )
    assert other.allow is False


def test_an_operator_deny_host_blocks_a_public_host(monkeypatch):
    from personalclaw.net.policy import egress_policy_for

    _with_operator_egress(monkeypatch, _FakeEgress(deny_hosts=["example.com"]))
    p = egress_policy_for(BROWSE)
    assert "example.com" in p.deny_hosts
    d = evaluate("https://example.com/page", p, resolver=_PUBLIC)
    assert d.allow is False
    assert "deny list" in d.reason


def test_an_operator_deny_outranks_their_own_allow(monkeypatch):
    from personalclaw.net.policy import egress_policy_for

    _with_operator_egress(
        monkeypatch, _FakeEgress(allow_hosts=["nas.local"], deny_hosts=["nas.local"])
    )
    p = egress_policy_for(BROWSE)
    assert evaluate("http://nas.local/admin", p, resolver=_LAN).allow is False


def test_an_operator_can_opt_the_whole_instance_into_private_egress(monkeypatch):
    from personalclaw.net.policy import egress_policy_for

    _with_operator_egress(monkeypatch, _FakeEgress(allow_private=True))
    p = egress_policy_for(BROWSE)
    assert p.allow_private is True
    assert evaluate("http://nas.local/admin", p, resolver=_LAN).allow is True


def test_with_no_operator_config_the_profile_is_unchanged(monkeypatch):
    """The fail-safe direction: an unreadable/absent config must not widen anything."""
    from personalclaw.net.policy import egress_policy_for

    _with_operator_egress(monkeypatch, _FakeEgress())
    assert egress_policy_for(BROWSE) == BROWSE


# ── the profile is a plain EgressPolicy, so the tier plane composes with it ────


def test_a_run_egress_tier_can_narrow_browse_but_never_widen_it():
    from personalclaw.net.policy import egress_policy_for_profile

    assert isinstance(BROWSE, EgressPolicy)
    off = egress_policy_for_profile(BROWSE, "off")
    assert off is None, "an egress-off run must not browse at all"
    listed = egress_policy_for_profile(BROWSE, "listed")
    assert listed is not None and listed.allow_only is True
    assert evaluate("https://example.com", listed, resolver=_PUBLIC).allow is False
    registry = egress_policy_for_profile(BROWSE, "registry")
    assert registry is not None
    assert registry.max_bytes <= BROWSE.max_bytes, "a tier must never raise a ceiling"
    assert egress_policy_for_profile(BROWSE, "all") is BROWSE
