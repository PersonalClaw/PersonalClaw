"""PHF-2 — the opt-in cgroup-scope enforcement tier and its unenforced-ceiling warning.

Three things are under test here, in increasing order of how easily they could rot:

1. ``probe_cgroup_scopes`` — never raises, on this host or under any simulated platform,
   and is answered from a cache after the first call.
2. ``cgroup_scope_argv`` / ``spawn_shim_argv`` — the scope is the OUTER layer: it wraps the
   NOFILE shim rather than replacing it, and ``MemorySwapMax=0`` always accompanies
   ``MemoryMax`` (a tree that can swap has escaped the memory cap).
3. The warning's *vacuity assertion* — a measurement proving macOS really cannot enforce
   the pids/RSS ceilings. Without it the warning could quietly become a lie the day Darwin
   starts honouring ``RLIMIT_AS``, and nothing would fail.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import pytest

from personalclaw import sandbox
from personalclaw.sandbox import (
    PROFILE_TOOL,
    ResourceCeilings,
    cgroup_scope_argv,
    probe_cgroup_scopes,
    spawn_shim_argv,
)

_SANDBOX_LOGGER = "personalclaw.sandbox"
_WARN_MARKER = "sandbox ceilings NOT enforced"


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """Clear both pieces of process-wide state this tier keeps.

    The probe is ``lru_cache``d and the warning is latched by a module global; either one
    leaking between tests would make an assertion depend on execution order.
    """
    probe_cgroup_scopes.cache_clear()
    monkeypatch.setattr(sandbox, "_UNENFORCED_CEILINGS_WARNED", False)
    yield
    probe_cgroup_scopes.cache_clear()


def _simulate_host(monkeypatch, tmp_path, *, cgroup2: bool, systemd: bool, bus: bool = True):
    """Point the probe's filesystem/PATH/env inputs at a simulated Linux host."""
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    controllers = tmp_path / "cgroup.controllers"
    if cgroup2:
        controllers.write_text("cpuset cpu io memory pids\n", encoding="ascii")
    monkeypatch.setattr(sandbox, "_CGROUP2_CONTROLLERS", str(controllers))
    monkeypatch.setattr(
        sandbox.shutil,
        "which",
        lambda name: "/usr/bin/systemd-run" if (systemd and name == "systemd-run") else None,
    )
    if bus:
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    else:
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    probe_cgroup_scopes.cache_clear()


def _force_probe(monkeypatch, available: bool):
    """Pin the probe's verdict so argv-shape tests do not depend on the running host."""
    monkeypatch.setattr(
        sandbox,
        "_cgroup_scopes_available",
        lambda: (available, "forced by test fixture"),
    )
    probe_cgroup_scopes.cache_clear()


# ── The probe ──


def test_probe_never_raises_on_this_host():
    result = probe_cgroup_scopes()
    assert isinstance(result, tuple)
    assert len(result) == 2
    available, detail = result
    assert isinstance(available, bool)
    assert isinstance(detail, str) and detail, "the probe must always explain its verdict"
    if sys.platform != "linux":
        assert available is False
        assert "not Linux" in detail


def test_probe_available_when_cgroup2_and_systemd_present(monkeypatch, tmp_path):
    _simulate_host(monkeypatch, tmp_path, cgroup2=True, systemd=True)
    available, detail = probe_cgroup_scopes()
    assert available is True
    assert "cgroup v2" in detail and "systemd" in detail
    # The delegated controllers are surfaced so the doctor line shows what the host has.
    assert "pids" in detail and "memory" in detail


def test_probe_unavailable_without_unified_hierarchy(monkeypatch, tmp_path):
    _simulate_host(monkeypatch, tmp_path, cgroup2=False, systemd=True)
    available, detail = probe_cgroup_scopes()
    assert available is False
    assert "no unified cgroup v2 hierarchy" in detail


def test_probe_unavailable_without_systemd(monkeypatch, tmp_path):
    _simulate_host(monkeypatch, tmp_path, cgroup2=True, systemd=False)
    available, detail = probe_cgroup_scopes()
    assert available is False
    assert "systemd-run is not on PATH" in detail


def test_probe_unavailable_without_a_user_bus(monkeypatch, tmp_path):
    _simulate_host(monkeypatch, tmp_path, cgroup2=True, systemd=True, bus=False)
    available, detail = probe_cgroup_scopes()
    assert available is False
    assert "no live systemd user bus" in detail


def test_probe_accepts_the_xdg_runtime_bus_socket(monkeypatch, tmp_path):
    _simulate_host(monkeypatch, tmp_path, cgroup2=True, systemd=True, bus=False)
    runtime = tmp_path / "run-user"
    runtime.mkdir()
    (runtime / "bus").write_bytes(b"")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    probe_cgroup_scopes.cache_clear()
    available, _detail = probe_cgroup_scopes()
    assert available is True, "$XDG_RUNTIME_DIR/bus is the fallback evidence of a user bus"


def test_probe_is_cached_after_the_first_call(monkeypatch):
    calls: list[int] = []

    def _counting():
        calls.append(1)
        return True, "counted"

    monkeypatch.setattr(sandbox, "_cgroup_scopes_available", _counting)
    probe_cgroup_scopes.cache_clear()
    first = probe_cgroup_scopes()
    second = probe_cgroup_scopes()
    assert first == second == (True, "counted")
    assert len(calls) == 1, "the probe sits on the spawn path; it must run at most once"


# ── cgroup_scope_argv ──

_ARGV = ["/bin/echo", "hi"]


def test_scope_argv_with_both_ceilings(monkeypatch):
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(nofile=4096, max_pids=64, max_rss_mb=512, cgroup_scopes=True)
    assert cgroup_scope_argv(_ARGV, ceilings) == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--property=TasksMax=64",
        "--property=MemoryMax=512M",
        "--property=MemorySwapMax=0",
        "--",
        "/bin/echo",
        "hi",
    ]


def test_scope_argv_with_pids_only(monkeypatch):
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(nofile=4096, max_pids=64, max_rss_mb=0, cgroup_scopes=True)
    assert cgroup_scope_argv(_ARGV, ceilings) == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--property=TasksMax=64",
        "--",
        "/bin/echo",
        "hi",
    ]


def test_scope_argv_with_rss_only(monkeypatch):
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(nofile=4096, max_pids=0, max_rss_mb=512, cgroup_scopes=True)
    out = cgroup_scope_argv(_ARGV, ceilings)
    assert out == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--property=MemoryMax=512M",
        "--property=MemorySwapMax=0",
        "--",
        "/bin/echo",
        "hi",
    ]
    # An unconfigured ceiling must not be emitted at all: TasksMax=0 is a total denial of
    # forking, not a disabled limit.
    assert not [tok for tok in out if "TasksMax" in tok]


@pytest.mark.parametrize("max_pids", [0, 64])
def test_memory_swap_max_is_always_emitted_with_memory_max(monkeypatch, max_pids):
    """A tree allowed to swap has escaped MemoryMax, so the two are inseparable."""
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(max_pids=max_pids, max_rss_mb=256, cgroup_scopes=True)
    out = cgroup_scope_argv(_ARGV, ceilings)
    assert "--property=MemoryMax=256M" in out
    assert "--property=MemorySwapMax=0" in out
    assert out.index("--property=MemorySwapMax=0") == out.index("--property=MemoryMax=256M") + 1


def test_scope_argv_unchanged_when_opted_out(monkeypatch):
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(max_pids=64, max_rss_mb=512, cgroup_scopes=False)
    assert cgroup_scope_argv(_ARGV, ceilings) == _ARGV


def test_scope_argv_unchanged_when_probe_says_unavailable(monkeypatch):
    _force_probe(monkeypatch, False)
    ceilings = ResourceCeilings(max_pids=64, max_rss_mb=512, cgroup_scopes=True)
    assert cgroup_scope_argv(_ARGV, ceilings) == _ARGV


def test_scope_argv_unchanged_when_no_ceiling_is_configured(monkeypatch):
    """A scope with no properties would cost a systemd round-trip and enforce nothing."""
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(nofile=4096, max_pids=0, max_rss_mb=0, cgroup_scopes=True)
    assert cgroup_scope_argv(_ARGV, ceilings) == _ARGV


def test_scope_argv_of_empty_argv_is_empty(monkeypatch):
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(max_pids=64, cgroup_scopes=True)
    assert cgroup_scope_argv([], ceilings) == []


# ── Layering: the scope wraps the shim, it does not replace it ──


def test_spawn_shim_argv_wraps_the_shim_in_the_scope(monkeypatch):
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(nofile=4096, max_pids=64, max_rss_mb=512, cgroup_scopes=True)
    out = spawn_shim_argv(list(_ARGV), PROFILE_TOOL, ceilings)

    # The scope tokens come FIRST — it is the outer layer.
    assert out[:4] == ["systemd-run", "--user", "--scope", "--quiet"]

    # The NOFILE shim is still there, intact, inside the scope — NOT replaced by it.
    sep = out.index("--")
    inner = out[sep + 1 :]
    assert inner[:3] == [sys.executable, "-m", sandbox._SHIM_MODULE]
    assert "RLIMIT_NOFILE" in inner[3], "the shim must still carry the NOFILE floor"
    assert inner[4] == "--"
    assert inner[5:] == list(_ARGV)
    assert out.index("systemd-run") < out.index(sys.executable)


def test_spawn_shim_argv_is_shim_only_without_the_tier(monkeypatch):
    """Opted out, the composition is byte-identical to the pre-tier behaviour."""
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(nofile=4096, max_pids=64, max_rss_mb=512, cgroup_scopes=False)
    out = spawn_shim_argv(list(_ARGV), PROFILE_TOOL, ceilings)
    assert out[:3] == [sys.executable, "-m", sandbox._SHIM_MODULE]
    assert "systemd-run" not in out


# ── The one loud warning ──


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if _WARN_MARKER in r.getMessage()]


def test_warning_fires_exactly_once_across_repeated_spawns(caplog):
    caplog.set_level(logging.WARNING, logger=_SANDBOX_LOGGER)
    ceilings = ResourceCeilings(nofile=4096, max_pids=64, max_rss_mb=512, cgroup_scopes=False)
    for _ in range(4):
        spawn_shim_argv(list(_ARGV), PROFILE_TOOL, ceilings)
    found = _warnings(caplog)
    assert len(found) == 1, f"expected exactly one warning per process, got {len(found)}"
    text = found[0]
    assert "pids" in text and "RSS" in text, "the warning must name what is not enforced"
    assert "NOFILE" in text, "and must say the NOFILE floor still applies"
    assert "cgroup_scopes" in text, "and must point at the remedy"


def test_no_warning_when_neither_ceiling_is_configured(caplog):
    """Nothing was asked for, so nothing is unenforced — silence is correct."""
    caplog.set_level(logging.WARNING, logger=_SANDBOX_LOGGER)
    ceilings = ResourceCeilings(nofile=4096, max_pids=0, max_rss_mb=0, cgroup_scopes=False)
    for _ in range(3):
        spawn_shim_argv(list(_ARGV), PROFILE_TOOL, ceilings)
    assert _warnings(caplog) == []


def test_no_warning_when_the_cgroup_tier_is_in_effect(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_SANDBOX_LOGGER)
    _force_probe(monkeypatch, True)
    ceilings = ResourceCeilings(nofile=4096, max_pids=64, max_rss_mb=512, cgroup_scopes=True)
    spawn_shim_argv(list(_ARGV), PROFILE_TOOL, ceilings)
    assert _warnings(caplog) == []


def test_warning_fires_when_opted_in_but_the_host_cannot_host_a_scope(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=_SANDBOX_LOGGER)
    _force_probe(monkeypatch, False)
    ceilings = ResourceCeilings(nofile=4096, max_pids=64, max_rss_mb=512, cgroup_scopes=True)
    spawn_shim_argv(list(_ARGV), PROFILE_TOOL, ceilings)
    assert len(_warnings(caplog)) == 1


# ── Vacuity assertions: the unenforced-ness the warning claims is REAL ──
#
# These are the reason the warning is allowed to exist. Each measures the platform in a
# fresh child rather than trusting folklore, and each fails LOUDLY (not silently passes) if
# the platform starts enforcing the ceiling — at which point the warning must be narrowed.


@pytest.mark.skipif(sys.platform != "darwin", reason="measures Darwin rlimit semantics")
def test_darwin_cannot_enforce_an_rss_ceiling():
    code = (
        "import resource, sys\n"
        "cap = 64 * 1024 * 1024\n"
        "try:\n"
        "    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))\n"
        "except (ValueError, OSError) as exc:\n"
        "    print('REJECTED', exc); sys.exit(7)\n"
        "try:\n"
        "    buf = bytearray(256 * 1024 * 1024); buf[0] = 1; buf[-1] = 1\n"
        "except MemoryError:\n"
        "    print('ENFORCED'); sys.exit(8)\n"
        "print('IGNORED'); sys.exit(9)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode != 8, (
        "RLIMIT_AS is now ENFORCED on this host: the unenforced-RSS half of the warning in "
        f"sandbox._warn_unenforced_ceilings has become a LIE and must be narrowed. {proc.stdout}"
    )
    assert proc.returncode in (7, 9), f"unexpected probe outcome: {proc.returncode} {proc.stderr}"
    # Measured on Darwin 26.6.1: the kernel rejects any finite RLIMIT_AS (it aliases
    # RLIMIT_RSS), so the ceiling is never even installed — exit 7, not 9.
    assert proc.returncode == 7 and "REJECTED" in proc.stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="measures Darwin rlimit semantics")
def test_darwin_nproc_counts_the_user_not_the_process_tree():
    listing = subprocess.run(
        ["ps", "-o", "pid=", "-u", str(os.getuid())],
        capture_output=True,
        text=True,
        timeout=60,
    )
    live = len([ln for ln in listing.stdout.splitlines() if ln.strip()])
    if live < 8:
        pytest.skip(f"only {live} processes for this uid — too idle to discriminate")
    # A cap comfortably ABOVE the two processes this tree will hold, and comfortably BELOW
    # the uid's live process count. A per-tree limit would permit the fork; a per-user one
    # cannot. That gap is what makes this measurement non-vacuous.
    cap = live // 2
    assert cap >= 2
    code = (
        "import os, resource, sys\n"
        f"cap = {cap}\n"
        "try:\n"
        "    resource.setrlimit(resource.RLIMIT_NPROC, (cap, cap))\n"
        "except (ValueError, OSError) as exc:\n"
        "    print('REJECTED', exc); sys.exit(7)\n"
        "try:\n"
        "    pid = os.fork()\n"
        "except OSError as exc:\n"
        "    print('DENIED', exc); sys.exit(8)\n"
        "if pid == 0:\n"
        "    os._exit(0)\n"
        "os.waitpid(pid, 0)\n"
        "print('FORKED'); sys.exit(9)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 8, (
        f"a fork from a ONE-process tree under RLIMIT_NPROC={cap} was permitted "
        f"(exit {proc.returncode}: {proc.stdout.strip()}). RLIMIT_NPROC may now be a "
        "per-tree bound on this host, which would make the unenforced-pids half of the "
        "warning in sandbox._warn_unenforced_ceilings a LIE — re-measure and narrow it."
    )
    assert "DENIED" in proc.stdout
