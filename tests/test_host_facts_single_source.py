"""One host-fact collector, so the fit answer cannot disagree with itself.

The GPU/VRAM-total probe and the system-memory probe used to be collected in two unrelated
places each: :mod:`personalclaw.local_models.fit` for the fit budget and
:mod:`personalclaw.dashboard.handlers_system` for the system widget. Two collectors mean two
answers, and a fit chip that names a different VRAM total than the system card is worse than
no chip. These tests pin the single-source split:

* **capacity facts** (GPU vendor/model, VRAM *total*, unified-vs-discrete, memory total) are
  detected exactly once — in ``fit`` for the GPU, in ``local_models.residency`` for memory;
* **live telemetry** (utilisation, temperature, *used* VRAM, the disk gauge) stays in the
  metrics handler, because it changes every tick and is nobody's capacity input.

Every census here carries a **vacuity floor**: a pattern that matches nothing looks clean
while proving nothing, so each census asserts it found its subject before it asserts where
the subject lives.
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest
from host_facts_single_source_shared import _SRC

from personalclaw.dashboard import handlers_system
from personalclaw.local_models import fit, residency

_GB = 1024**3


def test_handlers_system_gpu_query_is_telemetry_only() -> None:
    """The metrics handler's own nvidia-smi read asks for live values, never capacity."""
    source = (_SRC / "dashboard" / "handlers_system.py").read_text(encoding="utf-8")
    queries = [line.strip() for line in source.splitlines() if "--query-gpu=" in line]
    # Vacuity floor: no query line means the assertions below inspect nothing.
    assert queries, "no --query-gpu= read found in handlers_system.py — census went stale"
    for query in queries:
        assert "utilization.gpu" in query, f"expected a live telemetry read, got: {query}"
        assert "memory.total" not in query, f"VRAM total belongs to fit._probe_gpu: {query}"
        assert "=name," not in query, f"the GPU name belongs to fit._probe_gpu: {query}"


def _stub_probe(monkeypatch: pytest.MonkeyPatch, vram_bytes: int) -> list[int]:
    """Point fit's ONE GPU probe at fake discrete-NVIDIA facts; return a call counter."""
    calls: list[int] = []

    def fake_probe() -> dict[str, object]:
        calls.append(1)
        return {
            "unified": False,
            "vram_bytes": vram_bytes,
            "vendor": "nvidia",
            "model": "Fake GPU 9000",
        }

    monkeypatch.setattr(fit, "_probe_gpu", fake_probe)
    return calls


def _stub_telemetry(monkeypatch: pytest.MonkeyPatch, csv: str) -> None:
    """Replace only handlers_system's view of subprocess, never the real module."""
    monkeypatch.setattr(
        handlers_system,
        "subprocess",
        types.SimpleNamespace(
            check_output=lambda *a, **k: csv.encode(), DEVNULL=subprocess.DEVNULL
        ),
    )


def test_gpu_capacity_facts_come_from_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The widget's VRAM total is fit's number — the same one the budget is built on."""
    calls = _stub_probe(monkeypatch, 8 * _GB)
    _stub_telemetry(monkeypatch, "42, 2048, 61\n")

    payload = handlers_system._collect_gpu_metrics()

    assert calls, "handlers_system did not consult fit._probe_gpu at all"
    assert payload["gpu_vendor"] == "nvidia"
    assert payload["gpu_model"] == "Fake GPU 9000"
    assert payload["vram_total_gb"] == 8.0
    # The invariant that matters: the widget and the fit budget cannot disagree, because
    # both read the same probe.
    host = fit.host_capacity()
    assert payload["vram_total_gb"] == round(host.discrete_vram_bytes / _GB, 1)


def test_gpu_telemetry_keys_survive_the_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Utilisation, temperature and USED VRAM are still collected locally."""
    _stub_probe(monkeypatch, 8 * _GB)
    _stub_telemetry(monkeypatch, "42, 2048, 61\n")

    payload = handlers_system._collect_gpu_metrics()

    assert payload["gpu_present"] is True
    assert payload["gpu_pct"] == 42.0
    assert payload["vram_used_gb"] == 2.0
    assert payload["gpu_temp_c"] == 61
    assert set(payload) == {
        "gpu_present",
        "gpu_vendor",
        "gpu_model",
        "gpu_pct",
        "gpu_temp_c",
        "vram_used_gb",
        "vram_total_gb",
    }


def test_system_metrics_payload_keeps_memory_and_disk_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The payload still publishes the memory keys and the local disk gauge."""
    monkeypatch.setattr(
        residency,
        "memory_pressure",
        lambda warn_pct=None: {
            "total_mb": 16384,
            "used_mb": 12288,
            "available_mb": 4096,
            "used_pct": 75.0,
            "warn_pct": 85,
            "warn": False,
            "source": "vm_stat",
        },
    )
    monkeypatch.setattr(handlers_system, "_STATIC_SYSTEM_INFO", None)

    data = handlers_system._collect_system_metrics()

    assert data["mem_total_gb"] == 16.0
    assert data["mem_free_gb"] == 4.0
    assert data["mem_used_gb"] == 12.0
    # The disk read stays local on purpose: it is a total/used/free GAUGE, not the
    # pre-download capacity question fit.disk_precheck answers.
    assert "disk_total_gb" in data
    assert "disk_free_gb" in data


def test_memory_total_matches_the_fit_budget_on_this_host() -> None:
    """Unpatched, the handler and the fit budget read the same total from residency."""
    host = fit.host_capacity()
    if sys.platform not in ("darwin", "linux"):
        pytest.skip(f"no memory probe for {sys.platform}")
    # A skip reads like a pass, so on a platform we DO probe, an unmeasured host is a
    # failure rather than a silent exemption.
    assert host.memory_measured, "residency could not measure memory on a supported platform"

    totals = handlers_system._memory_totals()
    assert totals is not None
    assert abs(totals[0] - host.total_ram_bytes / _GB) <= 0.1
