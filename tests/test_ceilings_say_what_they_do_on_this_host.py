"""Settings › Security says when Max memory and Max processes do not contain a child.

🔴 On macOS ``RLIMIT_AS`` aliases ``RLIMIT_RSS`` and the kernel refuses any finite value, so
``sandbox.max_rss_mb`` is never applied, and ``RLIMIT_NPROC`` counts every process of the user
rather than the child's tree (both measured in ``tests/test_sandbox_cgroup_scopes.py``). The
gateway logged that once per process. ``GET /api/security/stats``, which the Security panel
reads, said nothing, so the panel offered both fields as if they held.

``child_ceilings_note`` is the panel's sentence. It answers from the same predicate as the log
warning (``_tree_ceilings_hold``), so the two cannot disagree about when the ceilings hold.
"""

from __future__ import annotations

import json
import logging

import pytest

from personalclaw import sandbox
from personalclaw.sandbox import ResourceCeilings, probe_cgroup_scopes


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    probe_cgroup_scopes.cache_clear()
    monkeypatch.setattr(sandbox, "_UNENFORCED_CEILINGS_WARNED", False)
    yield
    probe_cgroup_scopes.cache_clear()


def _host(monkeypatch, platform: str, *, scopes_available: bool = False, detail: str = "") -> None:
    monkeypatch.setattr(sandbox.sys, "platform", platform)
    monkeypatch.setattr(sandbox, "probe_cgroup_scopes", lambda: (scopes_available, detail))


def _stats(monkeypatch, ceilings: ResourceCeilings) -> dict:
    """``GET /api/security/stats`` as the panel reads it, for a config holding *ceilings*."""
    import asyncio

    from personalclaw.dashboard.handlers.core import api_security_stats

    monkeypatch.setattr(ResourceCeilings, "from_config", classmethod(lambda cls: ceilings))
    response = asyncio.run(api_security_stats(None))
    return json.loads(response.body)


# ── the defect: on a Mac the panel is told ────────────────────────────────────────────────────


def test_the_security_stats_tell_the_panel_on_a_mac(monkeypatch):
    _host(monkeypatch, "darwin", detail="darwin: no cgroup v2")

    stats = _stats(monkeypatch, ResourceCeilings(max_pids=256, max_rss_mb=2048))

    assert stats.get("child_ceilings", {}).get("contained") is False, stats
    note = stats["child_ceilings"]["note"]
    assert "Max memory is never applied" in note
    assert "Max processes counts every process you run" in note
    assert "Max open files is enforced" in note


def test_a_mac_is_told_before_any_ceiling_is_set(monkeypatch):
    """The warning waits for a configured ceiling; the panel is where one gets configured."""
    _host(monkeypatch, "darwin")

    assert sandbox.child_ceilings_note(ResourceCeilings(max_pids=0, max_rss_mb=0))


# ── other hosts ───────────────────────────────────────────────────────────────────────────────


def test_a_linux_host_running_cgroup_scopes_says_nothing(monkeypatch):
    _host(monkeypatch, "linux", scopes_available=True, detail="cgroup v2 + systemd --user")

    assert sandbox.child_ceilings_note(ResourceCeilings(max_pids=64, cgroup_scopes=True)) == ""
    stats = _stats(monkeypatch, ResourceCeilings(max_pids=64, cgroup_scopes=True))
    assert stats["child_ceilings"] == {"contained": True, "note": ""}


def test_a_linux_host_that_could_run_scopes_is_told_to_turn_them_on(monkeypatch):
    _host(monkeypatch, "linux", scopes_available=True, detail="cgroup v2 + systemd --user")

    note = sandbox.child_ceilings_note(ResourceCeilings(max_pids=64, cgroup_scopes=False))

    assert "neither contains a child's whole process tree" in note
    assert "Turn on Cgroup scopes below" in note


def test_a_linux_host_that_cannot_run_scopes_says_why(monkeypatch):
    _host(monkeypatch, "linux", detail="linux: no systemd-run on PATH")

    note = sandbox.child_ceilings_note(ResourceCeilings(max_pids=64, cgroup_scopes=True))

    assert "can't run (linux: no systemd-run on PATH)" in note
    assert "Turn on" not in note


# ── the log and the panel agree ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "platform, available, scopes",
    [
        ("darwin", False, False),
        ("darwin", False, True),
        ("linux", True, True),
        ("linux", True, False),
        ("linux", False, True),
    ],
)
def test_the_panel_speaks_exactly_when_the_log_warns(
    monkeypatch, caplog, platform, available, scopes
):
    _host(monkeypatch, platform, scopes_available=available, detail="probe detail")
    ceilings = ResourceCeilings(max_pids=64, max_rss_mb=512, cgroup_scopes=scopes)
    caplog.set_level(logging.WARNING, logger="personalclaw.sandbox")

    sandbox._warn_unenforced_ceilings(ceilings)

    warned = any("sandbox ceilings NOT enforced" in r.getMessage() for r in caplog.records)
    assert bool(sandbox.child_ceilings_note(ceilings)) is warned
