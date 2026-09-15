"""WIN-1 — the POSIX-only ``resource`` import is guarded so gateway boot never crashes.

The bug (windows-native-audit ``WIN-1``): ``gateway.py`` did a bare ``import resource`` at boot.
``resource`` is POSIX-only, so a native-Windows process ``ImportError``ed before it could serve.
The fix moves the import behind :mod:`personalclaw.resource_limits`, mirroring the guard already
at ``_spawn_exec_shim.py``, and routes the facility's two consumers through it:

* the gateway's own ``RLIMIT_NOFILE`` raise at boot (``raise_fd_limit``), and
* the ``doctor`` resource-limits availability row (``resource_limits_available``).

These tests prove the DEGRADED branch actually runs (not merely that the import is wrapped):
with ``resource`` simulated-absent, both consumers degrade to the documented no-limit behaviour
and nothing raises; on a POSIX host the raise is byte-identical to the previous inline code.
"""

from __future__ import annotations

import ast
import importlib
import os
import sys

import pytest

from personalclaw import resource_limits as rl
from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import DoctorContext, Tier

PROBE_ID = "sandbox.resource_limits"


class _FakeResource:
    """A stand-in for the stdlib ``resource`` module that records ``setrlimit`` calls.

    Lets the POSIX-behaviour tests run identically on any host (some CI already carries a
    high soft cap) and lets us assert the exact ``(soft, hard)`` target the helper computes.
    """

    RLIMIT_NOFILE = 7  # the real constant's value is irrelevant; only identity matters

    def __init__(self, soft: int, hard: int):
        self._soft = soft
        self._hard = hard
        self.set_calls: list[tuple[int, tuple[int, int]]] = []

    def getrlimit(self, which):
        assert which == self.RLIMIT_NOFILE
        return (self._soft, self._hard)

    def setrlimit(self, which, pair):
        assert which == self.RLIMIT_NOFILE
        self.set_calls.append((which, pair))
        self._soft, self._hard = pair


# ── the degraded branch actually runs when ``resource`` is absent ──────────────


def test_resource_limits_available_is_true_on_this_posix_host():
    """This suite runs on macOS/Linux, so the facility is genuinely present — the positive
    direction, so ``False`` below means the absence path, not a broken import."""
    assert rl.resource_limits_available() is True


def test_raise_fd_limit_degrades_to_noop_when_resource_absent(monkeypatch):
    """With ``resource`` unavailable (Windows), the helper applies NO limit and returns the
    documented degraded result — it must not raise. Uses the ``_resource = None`` technique the
    spawn-shim degradation test already uses."""
    monkeypatch.setattr(rl, "_resource", None)
    assert rl.resource_limits_available() is False
    res = rl.raise_fd_limit()
    assert res.available is False
    assert res.raised is False
    # No attempt was made to read or set a limit — the whole rlimit path was skipped.
    assert res.soft is None and res.target is None


def test_import_guard_survives_an_absent_resource_module():
    """Exercise the real ``except ImportError`` branch (the Windows condition) by reimporting
    the module with ``resource`` blocked in ``sys.modules`` — the technique test_knowledge.py
    uses for pysqlite3. Restores the import system to exactly its prior state afterward so the
    module object other test modules hold a binding to is unchanged."""
    saved_resource = sys.modules.get("resource")
    saved_self = sys.modules.get("personalclaw.resource_limits")
    sys.modules.pop("personalclaw.resource_limits", None)
    sys.modules["resource"] = None  # type: ignore[assignment]  # makes `import resource` raise
    try:
        reloaded = importlib.import_module("personalclaw.resource_limits")
        # The guard swallowed the ImportError and the module still imports.
        assert reloaded.resource_limits_available() is False
        degraded = reloaded.raise_fd_limit()
        assert degraded.available is False and degraded.raised is False
    finally:
        if saved_resource is not None:
            sys.modules["resource"] = saved_resource
        else:
            sys.modules.pop("resource", None)
        sys.modules.pop("personalclaw.resource_limits", None)
        if saved_self is not None:
            sys.modules["personalclaw.resource_limits"] = saved_self


# ── POSIX behaviour is byte-identical to the previous inline code ──────────────


def test_raise_fd_limit_raises_soft_toward_target_when_below(monkeypatch):
    """The classic macOS case: soft 256 < target, hard high enough — raise to the target."""
    fake = _FakeResource(soft=256, hard=1_000_000)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()
    assert res.available is True and res.raised is True
    assert res.soft == 256 and res.target == rl.DEFAULT_FD_TARGET
    assert fake.set_calls == [(fake.RLIMIT_NOFILE, (rl.DEFAULT_FD_TARGET, 1_000_000))]


def test_raise_fd_limit_never_exceeds_the_inherited_hard_cap(monkeypatch):
    """An unprivileged process cannot raise the hard cap; the soft target is clamped to it."""
    fake = _FakeResource(soft=256, hard=1024)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()
    assert res.raised is True and res.target == 1024
    assert fake.set_calls == [(fake.RLIMIT_NOFILE, (1024, 1024))]


def test_raise_fd_limit_leaves_an_already_high_soft_alone(monkeypatch):
    """When the soft cap already meets/exceeds the target, nothing is set."""
    fake = _FakeResource(soft=rl.DEFAULT_FD_TARGET, hard=rl.DEFAULT_FD_TARGET)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()
    assert res.available is True and res.raised is False
    assert fake.set_calls == []


def test_raise_fd_limit_swallows_a_setrlimit_failure(monkeypatch):
    """A platform that has ``resource`` but refuses the ``setrlimit`` still boots — the helper
    never propagates the OSError (the ``except Exception: pass`` the inline code had)."""
    fake = _FakeResource(soft=256, hard=1_000_000)

    def _boom(_which, _pair):
        raise OSError("not permitted")

    monkeypatch.setattr(fake, "setrlimit", _boom)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()  # must not raise
    assert res.available is True and res.raised is False


# ── the second consumer: the doctor row ───────────────────────────────────────


def test_the_resource_limits_probe_is_registered_at_the_CAPABILITY_tier():
    """Registered, not merely defined — a host without ``resource`` is not a broken gateway,
    so the row is tier-3 and never gates the core."""
    ids = {p.id: p for p in doctor.all_probes()}
    assert doctor.all_probes(), "the probe registry is empty — the lookup proves nothing"
    assert "sandbox.resource_limits_that_does_not_exist" not in ids
    assert PROBE_ID in ids, "the probe must be registered, not just defined"
    probe = ids[PROBE_ID]
    assert probe.tier is Tier.CAPABILITY
    assert probe.capability == "sandbox"
    assert probe.title


@pytest.mark.asyncio
async def test_doctor_row_reports_available_on_a_posix_host():
    probe = {p.id: p for p in doctor.all_probes()}[PROBE_ID]
    res = await probe.run(DoctorContext())
    assert res.ok is True
    assert res.evidence["available"] is True
    assert "available" in res.detail.lower()


@pytest.mark.asyncio
async def test_doctor_row_degrades_loudly_when_resource_absent(monkeypatch):
    """The doctor consumes the SAME guarded helper; when it reports unavailable the row stays
    ok=True (no permanent red on Windows) but names the consequence loudly rather than hiding
    it behind a green."""
    monkeypatch.setattr(rl, "_resource", None)
    probe = {p.id: p for p in doctor.all_probes()}[PROBE_ID]
    res = await probe.run(DoctorContext())
    assert res.ok is True
    assert res.evidence["available"] is False
    assert "not available" in res.detail.lower()


# ── regression: the gateway boot path no longer carries a bare ``import resource`` ──


def test_gateway_boot_has_no_unguarded_import_resource():
    """The exact WIN-1 deliverable: ``gateway.py`` must not import ``resource`` directly —
    that bare import at boot was the crash. An AST scan (not a substring grep) so a
    re-introduced ``import resource`` / ``import resource as x`` reds, and it fails on the
    pre-fix tree for the right reason."""
    gw_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src",
        "personalclaw",
        "gateway.py",
    )
    tree = ast.parse(open(gw_path, encoding="utf-8").read())
    direct = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "resource"
    ]
    assert not direct, f"gateway.py imports the POSIX-only `resource` directly: {direct}"

    # And it DOES route through the guarded helper (the extraction is not cosmetic).
    src = open(gw_path, encoding="utf-8").read()
    assert "raise_fd_limit" in src, "gateway no longer calls the guarded raise_fd_limit helper"
