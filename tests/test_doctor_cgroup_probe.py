"""The doctor's sandbox pids/RSS enforcement row (PHF-2, doctor third).

PHF-2 adds an opt-in cgroup v2 tier (``systemd-run --user --scope`` with TasksMax /
MemoryMax / MemorySwapMax=0) above the NOFILE floor. Where that tier does not exist —
macOS, a non-systemd Linux, a container without a systemd user session — the two ceilings
it would enforce simply do not apply, and the atom's whole point is that we say so instead
of simulating a bound. Pinned here:

* the probe is REGISTERED (a probe function nothing registers is dead code that looks
  finished) and sits at tier ``CAPABILITY`` so it can never gate the core ladder;
* available → ``ok=True`` naming what IS enforced;
* unavailable → the ``detail`` names pids and RSS as unenforced and says NOFILE still
  applies (meaning asserted, not one exact wording);
* unavailable while an operator HAS configured a ceiling → ``ok=False``, because a green
  there would hide two configured controls doing nothing;
* **the probe never raises** — every failure mode of the underlying availability check
  degrades to a ``ProbeResult``;
* and the live, unpatched probe on this host returns a result (on darwin, the
  not-enforced row).
"""

from __future__ import annotations

import re
import sys

import pytest

from personalclaw import sandbox as sandbox_mod
from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import DoctorContext, ProbeResult, Tier

PROBE_ID = "sandbox.cgroup_scopes"


def _probe():
    """The registered probe object, looked up by its stable id."""
    return {p.id: p for p in doctor.all_probes()}[PROBE_ID]


def _fake_availability(monkeypatch, value):
    """Stand in for the sibling's ``sandbox.probe_cgroup_scopes``.

    ``value`` is either the ``(available, reason)`` tuple it returns or an exception
    instance to raise. Patched on ``personalclaw.sandbox`` (not on the doctor) because the
    probe imports it lazily at call time — that is what keeps the doctor and the spawn path
    reading the SAME detection.
    """

    def _fake():
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(sandbox_mod, "probe_cgroup_scopes", _fake, raising=False)


def _unenforced_clause(detail: str) -> str:
    """The clause of *detail* that makes the not-enforced claim, lowercased.

    Asserted separately from the whole paragraph because co-occurrence anywhere is far too
    weak a check: dropping "RSS" from the claim itself, while a later sentence still reads
    "no pids or RSS ceiling is configured", keeps a naive ``"rss" in detail`` green. Proven
    by mutation — the naive form did not red.
    """
    for clause in re.split(r"(?<=[.;])\s+", detail):
        if "not enforced" in clause.lower():
            return clause.lower()
    return ""


def _fake_ceilings(monkeypatch, *, nofile=4096, max_pids=0, max_rss_mb=0):
    monkeypatch.setattr(
        sandbox_mod.ResourceCeilings,
        "from_config",
        classmethod(
            lambda cls: sandbox_mod.ResourceCeilings(
                nofile=nofile, max_pids=max_pids, max_rss_mb=max_rss_mb
            )
        ),
    )


# ── registration (+ a vacuity check on the lookup itself) ────────────────────


def test_the_cgroup_probe_is_registered_at_the_CAPABILITY_tier():
    """Registered, not merely defined. ``CAPABILITY`` because a host without the cgroup
    tier is not a broken gateway: tiers 0-2 short-circuit everything above them, so a lower
    tier would make every Mac look down over an enforcement layer that never existed."""
    ids = {p.id: p for p in doctor.all_probes()}

    # Vacuity: the registry is populated and the lookup discriminates, so "found" means
    # something. A bogus id must NOT resolve.
    assert doctor.all_probes(), "the probe registry is empty — the lookup proves nothing"
    assert "sandbox.cgroup_scopes_that_does_not_exist" not in ids

    assert PROBE_ID in ids, "the probe must be registered, not just defined"
    probe = ids[PROBE_ID]
    assert probe.tier is Tier.CAPABILITY
    assert probe.capability == "sandbox"
    assert probe.title


# ── available: honest in the positive direction too ──────────────────────────


@pytest.mark.asyncio
async def test_available_tier_reports_pids_and_RSS_as_enforced(monkeypatch):
    _fake_availability(monkeypatch, (True, "cgroup2 unified hierarchy + systemd user session"))
    _fake_ceilings(monkeypatch, max_pids=200, max_rss_mb=2048)

    res = await _probe().run(DoctorContext())

    assert res.ok is True
    low = res.detail.lower()
    assert "pids" in low and "rss" in low and "enforced" in low
    assert res.evidence["cgroup_scope_tier_available"] is True
    assert res.evidence["enforced"] == ["NOFILE", "pids", "RSS"]
    assert res.evidence["unenforced"] == []
    assert res.evidence["configured_ceilings"]["max_pids"] == 200


# ── unavailable: the sentence an operator has to be able to act on ───────────


@pytest.mark.asyncio
async def test_unavailable_tier_names_pids_RSS_unenforced_and_NOFILE_still_applying(monkeypatch):
    """The user-facing clause of the atom. Asserted by MEANING (pids + RSS + not enforced +
    NOFILE still applies), never by an exact string, so the prose stays editable."""
    _fake_availability(monkeypatch, (False, "darwin: no cgroup v2 hierarchy, no systemd"))
    _fake_ceilings(monkeypatch)  # nothing configured → nothing silently dropped

    res = await _probe().run(DoctorContext())

    claim = _unenforced_clause(res.detail)
    assert claim, f"no clause makes the not-enforced claim: {res.detail!r}"
    assert "pids" in claim, "the claim must name pids"
    assert "rss" in claim, "the claim must name RSS"
    low = res.detail.lower()
    assert "nofile" in low and "still applies" in low
    assert res.ok is True, "a permanent red on every Mac trains operators to ignore the doctor"
    assert res.evidence["unenforced"] == ["pids", "RSS"]
    assert res.evidence["enforced"] == ["NOFILE"]
    assert res.evidence["cgroup_scope_tier_available"] is False
    assert res.evidence["platform"] == sys.platform


@pytest.mark.parametrize(
    "knob,value",
    [("max_pids", 200), ("max_rss_mb", 2048)],
)
@pytest.mark.asyncio
async def test_a_configured_ceiling_that_cannot_be_enforced_is_a_RED_row(monkeypatch, knob, value):
    """Honest in the other direction: a bare green would hide a configured ceiling that does
    nothing. Still tier 3, so this reds only the sandbox row."""
    _fake_availability(monkeypatch, (False, "linux: no systemd user session"))
    _fake_ceilings(monkeypatch, **{knob: value})

    res = await _probe().run(DoctorContext())

    assert res.ok is False
    claim = _unenforced_clause(res.detail)
    assert "pids" in claim and "rss" in claim
    low = res.detail.lower()
    assert "nofile" in low and "still applies" in low
    assert f"sandbox.{knob}={value}" in res.detail
    assert res.evidence["configured_ceilings"][knob] == value


# ── the never-raises clause, driven hard ────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        OSError("cgroup read failed"),
        PermissionError("/sys/fs/cgroup: permission denied"),
        FileNotFoundError("systemd-run: no such file"),
        Exception("anything at all"),
    ],
    ids=["oserror", "permissionerror", "filenotfound", "bare-exception"],
)
@pytest.mark.asyncio
async def test_the_probe_never_raises_whatever_the_availability_check_does(monkeypatch, exc):
    """An explicit clause of the atom, not a nicety. Every failure degrades toward
    "not enforced": a probe that cannot PROVE enforcement must never claim it."""
    _fake_availability(monkeypatch, exc)
    _fake_ceilings(monkeypatch)

    res = await _probe().run(DoctorContext())

    assert isinstance(res, ProbeResult)
    assert res.evidence["cgroup_scope_tier_available"] is False
    # the cause is diagnosable from the row without re-running anything
    assert type(exc).__name__ in res.evidence["availability_detail"]
    assert str(exc) in res.evidence["availability_error"]
    claim = _unenforced_clause(res.detail)
    assert "pids" in claim and "rss" in claim


@pytest.mark.asyncio
async def test_an_unreadable_sandbox_config_still_produces_a_row(monkeypatch):
    """The ceilings read is a second failure surface (config on disk). It degrades too."""

    def _boom(cls):
        raise PermissionError("config.json unreadable")

    monkeypatch.setattr(sandbox_mod.ResourceCeilings, "from_config", classmethod(_boom))
    _fake_availability(monkeypatch, (False, "darwin"))

    res = await _probe().run(DoctorContext())

    assert isinstance(res, ProbeResult)
    assert "unreadable" in res.evidence["ceilings_detail"]


# ── live on this host, nothing patched ──────────────────────────────────────


@pytest.mark.asyncio
async def test_the_real_probe_runs_on_this_host_without_raising():
    """Driven live, not simulated. On darwin this is the not-enforced row the atom's done
    criterion names; on a Linux CI runner it is whichever answer that host truthfully gives.
    Either way the contract is: a ProbeResult comes back and nothing propagates."""
    res = await _probe().run(DoctorContext())

    assert isinstance(res, ProbeResult)
    assert res.detail
    assert res.evidence["platform"] == sys.platform
    assert isinstance(res.evidence["cgroup_scope_tier_available"], bool)

    if sys.platform == "darwin":
        assert res.evidence["cgroup_scope_tier_available"] is False
        claim = _unenforced_clause(res.detail)
        assert claim, f"no clause makes the not-enforced claim: {res.detail!r}"
        assert "pids" in claim and "rss" in claim
        low = res.detail.lower()
        assert "nofile" in low and "still applies" in low
        assert res.evidence["unenforced"] == ["pids", "RSS"]
