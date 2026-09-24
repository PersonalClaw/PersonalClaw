"""A config-read failure must not un-deny a host the operator explicitly denied.

`egress_policy_for` layers `security.egress` onto a base profile behind a deliberately broad
`except` — the docstring says why, and that catch stays: `net` has to stay importable without a
loaded config (tests, early boot).

The defect was that it failed open in ONE direction. On exception it dropped `allow_hosts` and
`allow_private` (safe — the result is narrower than the operator asked for) *and* `deny_hosts`
(not safe — a host they explicitly denied becomes reachable). So a transient config error
silently un-denied a host, and nothing said so.

Fix under test: the last successfully-observed deny list is remembered and reused on a later
failure, and the failure is logged at WARNING. These tests assert the DENIAL still holds through
`guard.evaluate` — the call site an egress actually passes through — rather than only that the
policy object carries a tuple.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from personalclaw.net import policy as pol


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    """Each case starts with nothing remembered — otherwise one test's deny list
    proves another's."""
    monkeypatch.setattr(pol, "_LAST_DENY_HOSTS", (), raising=False)


class _Egress:
    def __init__(self, deny=(), allow=(), private=False):
        self.deny_hosts = tuple(deny)
        self.allow_hosts = tuple(allow)
        self.allow_private = private


def _seed(monkeypatch, egress):
    """Make one successful config read observable, then let the caller break the next one.

    `SimpleNamespace`, not a nested class: a class body does not close over the enclosing
    function's names, so `egress = egress` there reads the global and raises NameError — which
    it did, loudly, on the first run.
    """
    cfg = SimpleNamespace(security=SimpleNamespace(egress=egress))
    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", staticmethod(lambda: cfg), raising=False
    )


def _break(monkeypatch):
    def boom():
        raise RuntimeError("config.json is being rewritten")

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", staticmethod(boom), raising=False
    )


def test_a_denied_host_stays_denied_when_the_next_config_read_fails(monkeypatch):
    """The defect, directly: seed a denial, break the read, assert it still denies."""
    _seed(monkeypatch, _Egress(deny=("evil.test",)))
    first = pol.egress_policy_for(pol.STRICT)
    assert "evil.test" in first.deny_hosts, "the seeded deny list never landed (vacuous fixture)"

    _break(monkeypatch)
    after = pol.egress_policy_for(pol.STRICT)
    assert "evil.test" in after.deny_hosts, "a config error un-denied an explicitly denied host"


def test_the_denial_still_refuses_at_the_guard_after_a_config_error(monkeypatch):
    """Asserted at the CALL SITE: a policy tuple proves nothing if the guard never reads it."""
    from personalclaw.net import guard

    _seed(monkeypatch, _Egress(deny=("evil.test",)))
    pol.egress_policy_for(pol.STRICT)
    _break(monkeypatch)

    # An injected resolver so the assertion is about the DENY rule, not about DNS for a
    # .test domain — a NXDOMAIN would deny for the wrong reason and read as a pass.
    decision = guard.evaluate(
        "https://evil.test/x",
        pol.egress_policy_for(pol.STRICT),
        resolver=lambda host: ["93.184.216.34"],
    )
    assert not decision.allow, f"the guard allowed a denied host: {decision}"
    assert (
        "evil.test" in decision.reason or "deny" in decision.reason.lower()
    ), f"denied, but not for the deny-list reason: {decision.reason!r}"


def test_the_permissive_half_is_still_dropped_on_error(monkeypatch):
    """Allowances must NOT survive a failed read — narrower is the safe direction."""
    _seed(monkeypatch, _Egress(deny=("evil.test",), allow=("lan.test",), private=True))
    seeded = pol.egress_policy_for(pol.STRICT)
    assert "lan.test" in seeded.allow_hosts and seeded.allow_private

    _break(monkeypatch)
    after = pol.egress_policy_for(pol.STRICT)
    assert "lan.test" not in after.allow_hosts, "a failed read kept an allowance"
    assert after.allow_private == pol.STRICT.allow_private, "a failed read kept allow_private"
    assert "evil.test" in after.deny_hosts, "…but the denial must survive"


def test_a_failure_with_nothing_observed_yet_returns_the_base_profile(monkeypatch):
    """Early boot: no successful read has happened, so there is no operator denial to keep."""
    _break(monkeypatch)
    assert pol.egress_policy_for(pol.STRICT) == pol.STRICT


def test_the_fallback_is_logged_rather_than_swallowed(monkeypatch, caplog):
    """A control that stops applying must be visible — the bare `return base` was not."""
    _seed(monkeypatch, _Egress(deny=("evil.test",)))
    pol.egress_policy_for(pol.STRICT)
    _break(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="personalclaw.net.policy"):
        pol.egress_policy_for(pol.STRICT)
    assert any(
        "deny list" in r.message or "unreadable" in r.message for r in caplog.records
    ), f"no WARNING named the fallback: {[r.message for r in caplog.records]}"


def test_the_broad_catch_still_keeps_net_importable(monkeypatch):
    """The behaviour the catch exists for, pinned so a future fix cannot quietly remove it."""
    _break(monkeypatch)
    # No raise, and a usable policy back — that is the whole contract of the best-effort read.
    assert isinstance(pol.egress_policy_for(pol.STRICT), pol.EgressPolicy)
