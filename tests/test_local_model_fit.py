"""Hardware-aware model fit — the budget arithmetic and the verdict (LMMV-8).

The defect this module exists to prevent is a budget that counts the same bytes twice. On
Apple Silicon (and any integrated GPU) the "VRAM" a probe reports IS system RAM, so adding
it produces a budget larger than the machine physically holds — which shows a green chip,
takes the user through a multi-gigabyte download, and then OOMs at load. Every arithmetic
test below therefore constructs :class:`HostCapacity` DIRECTLY: a test that probes the real
machine passes or fails by whichever laptop runs it, which is no assertion at all.

Three answers must stay distinct and every test here is ultimately about that distinction:

* ``None`` — the host could not be measured ("unknown").
* ``0`` — the host was measured and nothing fits ("no").
* ``> 0`` — a real budget.

Collapsing ``None`` into ``0`` hides every model from a user whose probe merely failed; the
reverse promises a fit on a machine that has none.

The verdict half carries a VACUITY assertion. ``fit_verdict`` could degenerate to one
constant answer — always green, or always unknown — and every "does it return a verdict"
test would still pass. So one test drives the real shipped whisper variant spread against
one synthetic host and asserts BOTH directions: at least one variant red AND at least one
green, with an explicit failure if all six ever agree.

Nothing here reads the real ``~/.personalclaw``: no test calls ``configured_reserve_gb()``
or ``hide_unrunnable_default()``, and every reserve is passed explicitly.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from personalclaw.local_models import fit

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024

#: The REAL shipped faster-whisper variant spread, in MB, read from
#: ``PersonalClawApps/faster-whisper/provider.py``: tiny=75, base=142, small=466,
#: medium=1500, turbo=1600, large-v3=2900. The vacuity assertion is only worth anything
#: against sizes a user can actually download — invented sizes prove the test, not the code.
WHISPER_VARIANT_SIZES_MB = [75.0, 142.0, 466.0, 1500.0, 1600.0, 2900.0]

#: A synthetic host small enough that the shipped spread straddles it: 4 GB of unified
#: memory minus the 3.0 GB default reserve leaves exactly 1 GB for models.
SMALL_HOST_GB = 4.0
SMALL_HOST_RESERVE_GB = 3.0


@pytest.fixture(autouse=True)
def _no_leaked_gpu_probe():
    """Drop the cached GPU facts around every test — a probe result must not leak."""
    fit.reset_gpu_probe_cache()
    yield
    fit.reset_gpu_probe_cache()


def _usage(free_bytes: int) -> SimpleNamespace:
    """A ``shutil.disk_usage`` stand-in — only ``.free`` is ever read."""
    return SimpleNamespace(total=free_bytes * 2, used=free_bytes, free=free_bytes)


def _host(
    *,
    ram_gb: float,
    measured: bool = True,
    unified: bool = True,
    vram_gb: float = 0.0,
    free_disk_gb: float = 100.0,
) -> fit.HostCapacity:
    """A host built from numbers, never from this machine."""
    return fit.HostCapacity(
        total_ram_bytes=int(ram_gb * _GB),
        memory_measured=measured,
        unified_memory=unified,
        discrete_vram_bytes=int(vram_gb * _GB),
        free_disk_bytes=int(free_disk_gb * _GB),
        disk_measured=True,
    )


# ── The budget: unified memory is counted exactly once ──────────────────────────


def test_unified_memory_vram_is_never_added_on_top_of_system_ram():
    """The double-count defect: a unified host's budget can never exceed its own RAM.

    A probe that reports an integrated GPU's memory as VRAM is reporting bytes that are
    ALREADY inside ``total_ram_bytes``. Adding them would claim 24 GB of room on a 16 GB
    machine. The populated ``discrete_vram_bytes`` here is deliberately wrong input — the
    budget must be identical to the same host with no VRAM at all.
    """
    wrongly_populated = _host(ram_gb=16.0, unified=True, vram_gb=8.0)
    clean = _host(ram_gb=16.0, unified=True, vram_gb=0.0)

    budget = fit.usable_memory_bytes(wrongly_populated, reserve_gb=3.0)

    assert budget is not None
    assert budget <= wrongly_populated.total_ram_bytes
    assert budget == int(16 * _GB) - int(3 * _GB)
    assert budget == fit.usable_memory_bytes(clean, reserve_gb=3.0)


def test_only_a_discrete_gpu_contributes_a_second_memory_pool():
    """``unified_memory=False`` plus real VRAM is the ONE case that adds two pools."""
    discrete = _host(ram_gb=16.0, unified=False, vram_gb=8.0)

    budget = fit.usable_memory_bytes(discrete, reserve_gb=3.0)

    assert budget == int(16 * _GB) - int(3 * _GB) + int(8 * _GB)
    # A genuinely separate pool is the only way a budget may exceed system RAM.
    assert budget > discrete.total_ram_bytes
    # And the reserve is taken from RAM only, never from the VRAM pool.
    assert budget - int(8 * _GB) == int(16 * _GB) - int(3 * _GB)


def test_a_discrete_gpu_with_no_vram_reported_adds_nothing():
    """Unknown hardware (vendor unidentified, 0 VRAM) must not inflate the budget."""
    unknown = _host(ram_gb=16.0, unified=False, vram_gb=0.0)

    assert fit.usable_memory_bytes(unknown, reserve_gb=3.0) == int(16 * _GB) - int(3 * _GB)


def test_the_fixed_reserve_is_subtracted_from_the_budget():
    """A budget equal to total RAM leaves nothing for the process doing the inference."""
    host = _host(ram_gb=16.0)

    assert fit.usable_memory_bytes(host, reserve_gb=4.0) == int(16 * _GB) - int(4 * _GB)
    # Monotonic: a bigger reserve is strictly less room, never the same number.
    assert fit.usable_memory_bytes(host, reserve_gb=6.0) < fit.usable_memory_bytes(
        host, reserve_gb=4.0
    )
    # An omitted reserve is the documented default, not zero.
    assert fit.usable_memory_bytes(host) == fit.usable_memory_bytes(
        host, reserve_gb=fit.DEFAULT_RESERVE_GB
    )
    assert fit.usable_memory_bytes(host) < host.total_ram_bytes


def test_a_negative_reserve_is_clamped_rather_than_inflating_the_budget():
    """A nonsense reserve from config must not become free memory."""
    host = _host(ram_gb=16.0)

    assert fit.usable_memory_bytes(host, reserve_gb=-8.0) == host.total_ram_bytes


def test_a_machine_smaller_than_the_reserve_yields_zero_not_none_and_not_negative():
    """A measured "nothing fits" is 0 — a real answer, never ``None`` and never negative."""
    tiny = _host(ram_gb=2.0)

    budget = fit.usable_memory_bytes(tiny, reserve_gb=3.0)

    assert budget is not None
    assert budget == 0
    assert budget >= 0


@pytest.mark.parametrize(
    ("ram_gb", "measured"),
    [
        (0.0, False),  # nothing probed at all
        (16.0, False),  # a plausible number the probe could not stand behind
        (0.0, True),  # measured, but the probe returned zero total RAM
    ],
)
def test_an_unmeasured_host_yields_none_so_unknown_never_reads_as_nothing_fits(ram_gb, measured):
    """``None`` and ``0`` are different answers; only one should hide models."""
    budget = fit.usable_memory_bytes(_host(ram_gb=ram_gb, measured=measured), reserve_gb=3.0)

    assert budget is None


# ── host_capacity(): the flags come from the probes, not from a guess ───────────


def test_host_capacity_treats_an_unavailable_memory_source_as_unmeasured(monkeypatch):
    """A total_mb that arrives with ``source="unavailable"`` is not a measurement.

    ``residency.memory_pressure`` returns zeros AND an ``unavailable`` source on a host it
    could not read. Trusting the number alone would turn an unknown host into a real
    budget, which is exactly the ``None``/``0`` collapse this module refuses.
    """
    monkeypatch.setattr(
        fit, "memory_pressure", lambda *a, **k: {"total_mb": 16384, "source": "unavailable"}
    )
    monkeypatch.setattr(fit, "_probe_gpu", lambda: {"unified": True, "vram_bytes": 0})

    host = fit.host_capacity()

    assert host.memory_measured is False
    assert fit.usable_memory_bytes(host, reserve_gb=3.0) is None


def test_host_capacity_carries_a_discrete_probe_through_to_a_two_pool_budget(monkeypatch):
    """An nvidia-smi host is the discrete case end to end, and the probe runs ONCE."""
    calls: list[list[str]] = []

    def _fake_check_output(cmd, **kwargs):
        calls.append(list(cmd))
        return b"NVIDIA GeForce RTX 4090, 24564\n"

    monkeypatch.setattr(fit.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(fit.subprocess, "check_output", _fake_check_output)
    monkeypatch.setattr(
        fit, "memory_pressure", lambda *a, **k: {"total_mb": 32768, "source": "meminfo"}
    )
    fit.reset_gpu_probe_cache()

    host = fit.host_capacity()

    assert host.unified_memory is False
    assert host.discrete_vram_bytes == int(24564 * _MB)
    assert host.gpu_vendor == "nvidia"
    budget = fit.usable_memory_bytes(host, reserve_gb=3.0)
    assert budget == int(32768 * _MB) - int(3 * _GB) + int(24564 * _MB)

    # Cached: a second capacity read must not re-shell-out.
    fit.host_capacity()
    assert len(calls) == 1


def test_an_unmeasurable_filesystem_leaves_disk_measured_false(monkeypatch):
    """0 free bytes and "could not measure" must not be told apart by the number."""

    def _boom(_path):
        raise OSError("no such filesystem")

    monkeypatch.setattr(
        fit, "memory_pressure", lambda *a, **k: {"total_mb": 16384, "source": "vm_stat"}
    )
    monkeypatch.setattr(fit, "_probe_gpu", lambda: {"unified": True, "vram_bytes": 0})
    monkeypatch.setattr(shutil, "disk_usage", _boom)

    host = fit.host_capacity()

    assert host.disk_measured is False
    assert host.free_disk_bytes == 0
    # The memory half is unaffected — one failed probe must not unmeasure the other.
    assert host.memory_measured is True


# ── kv_cache_bytes: an estimate that never guesses upward ───────────────────────


@pytest.mark.parametrize(
    ("context_tokens", "weights_bytes"),
    [(0, 4 * _GB), (8192, 0), (-1, 4 * _GB), (8192, -1)],
)
def test_kv_cache_is_zero_when_either_input_is_unknown(context_tokens, weights_bytes):
    """An unknown context window must not inflate the need and turn green into red."""
    assert fit.kv_cache_bytes(context_tokens, weights_bytes) == 0


def test_kv_cache_grows_with_both_the_context_window_and_the_weights():
    """The estimate scales on both axes; a constant would make context tokens inert."""
    assert fit.kv_cache_bytes(8192, 4 * _GB) > fit.kv_cache_bytes(4096, 4 * _GB)
    assert fit.kv_cache_bytes(4096, 8 * _GB) > fit.kv_cache_bytes(4096, 4 * _GB)
    # The documented rate: tokens x weight-GB x KV_BYTES_PER_TOKEN_PER_GB.
    assert fit.kv_cache_bytes(4096, _GB) == 4096 * fit.KV_BYTES_PER_TOKEN_PER_GB


def test_a_large_context_window_can_move_a_verdict_from_green_to_red():
    """The KV estimate reaches the verdict — otherwise it is a computed-but-inert number."""
    budget = 1 * _GB

    assert fit.fit_verdict(size_mb=466.0, context_tokens=0, budget_bytes=budget).verdict == "green"
    stretched = fit.fit_verdict(size_mb=466.0, context_tokens=65536, budget_bytes=budget)
    assert stretched.verdict == "red"
    assert stretched.need_bytes > int(466 * _MB)


# ── fit_verdict: four answers, and never only one of them ───────────────────────


def test_an_unmeasured_budget_is_unknown_rather_than_red():
    """A host that could not be measured must not read as "this model does not fit"."""
    assessment = fit.fit_verdict(size_mb=466.0, budget_bytes=None)

    assert assessment.verdict == "unknown"
    assert assessment.need_bytes == 0
    assert assessment.budget_bytes is None
    assert assessment.reason


@pytest.mark.parametrize("size_mb", [0.0, 0, None])
def test_a_model_with_no_declared_size_is_unknown_even_on_a_measured_host(size_mb):
    """A verdict needs two inputs; a card with no size is the second one missing."""
    assessment = fit.fit_verdict(size_mb=size_mb, budget_bytes=1 * _GB)

    assert assessment.verdict == "unknown"
    assert assessment.need_bytes == 0
    # The budget IS known here, so it is still reported — only the verdict is withheld.
    assert assessment.budget_bytes == 1 * _GB


def test_fit_verdict_spans_red_yellow_green_and_unknown():
    """All four answers are reachable, so no branch is dead."""
    budget = 1 * _GB
    verdicts = {
        fit.fit_verdict(size_mb=75.0, budget_bytes=budget).verdict,
        fit.fit_verdict(size_mb=800.0, budget_bytes=budget).verdict,
        fit.fit_verdict(size_mb=2900.0, budget_bytes=budget).verdict,
        fit.fit_verdict(size_mb=0.0, budget_bytes=budget).verdict,
    }

    assert verdicts == {"green", "yellow", "red", "unknown"}


def test_a_model_needing_more_than_the_budget_is_red_and_says_both_numbers():
    """The reason is the whole user-facing payload — it must be actionable alone."""
    assessment = fit.fit_verdict(size_mb=2900.0, budget_bytes=1 * _GB)

    assert assessment.verdict == "red"
    assert assessment.budget_bytes == 1 * _GB
    assert assessment.need_bytes == int(2900 * _MB)
    assert "2.8" in assessment.reason  # ~2.8 GB needed
    assert "1.0" in assessment.reason  # ~1.0 GB available


def test_a_model_inside_the_budget_but_over_the_headroom_is_yellow_not_green():
    """Green must mean room for a browser and a growing cache, not "technically fits"."""
    budget = 1 * _GB
    over_headroom = int(budget * fit.GREEN_HEADROOM) + 1 * _MB

    assessment = fit.fit_verdict(size_mb=over_headroom / _MB, budget_bytes=budget)

    assert assessment.verdict == "yellow"
    assert assessment.need_bytes <= budget


def test_at_least_one_shipped_model_is_red_and_at_least_one_is_green_on_a_small_host():
    """VACUITY: the verdict cannot silently degenerate to one constant answer.

    Sizes are the real shipped faster-whisper spread from
    ``PersonalClawApps/faster-whisper/provider.py`` (tiny=75, base=142, small=466,
    medium=1500, turbo=1600, large-v3=2900 MB). On 4 GB of unified memory with the 3.0 GB
    default reserve the budget is exactly 1 GB, which the spread straddles: the three small
    variants fit comfortably and the three large ones cannot load at all.

    A function that always returned green, always red, or always unknown would satisfy
    every other test in this file. This one fails if all six variants ever agree.
    """
    budget = fit.usable_memory_bytes(
        _host(ram_gb=SMALL_HOST_GB, unified=True), reserve_gb=SMALL_HOST_RESERVE_GB
    )
    assert budget == 1 * _GB, "the straddling host this assertion depends on has moved"

    verdicts = {
        size: fit.fit_verdict(size_mb=size, budget_bytes=budget).verdict
        for size in WHISPER_VARIANT_SIZES_MB
    }

    # Both directions, named — not just "more than one distinct value".
    assert {s for s, v in verdicts.items() if v == "green"} == {75.0, 142.0, 466.0}
    assert {s for s, v in verdicts.items() if v == "red"} == {1500.0, 1600.0, 2900.0}
    # And the degenerate-constant guard, stated separately so it fails on its own terms.
    assert len(set(verdicts.values())) > 1, f"every shipped variant returned {verdicts}"


def test_every_shipped_model_is_unknown_on_an_unmeasured_host():
    """The one case where all six SHOULD agree — and it must be unknown, not red."""
    verdicts = {
        fit.fit_verdict(size_mb=size, budget_bytes=None).verdict
        for size in WHISPER_VARIANT_SIZES_MB
    }

    assert verdicts == {"unknown"}


# ── Family quoting: the median variant, and stepping down ───────────────────────


def test_median_variant_size_is_the_median_and_never_the_smallest():
    """Quoting the smallest variant promises a fit the user's actual pick will not give."""
    median = fit.median_variant_size_mb(list(WHISPER_VARIANT_SIZES_MB))

    assert median == 1500.0
    assert median != min(WHISPER_VARIANT_SIZES_MB)
    assert median > min(WHISPER_VARIANT_SIZES_MB)
    # Order of the input must not change the answer.
    assert fit.median_variant_size_mb(list(reversed(WHISPER_VARIANT_SIZES_MB))) == median


def test_median_variant_size_takes_the_larger_of_two_middles_on_an_even_count():
    """A quote must not flatter the family: 466/1500 resolves upward, to 1500."""
    assert fit.median_variant_size_mb([75.0, 142.0, 466.0, 1500.0]) == 466.0
    assert fit.median_variant_size_mb([100.0, 200.0]) == 200.0
    # Odd counts are the plain middle.
    assert fit.median_variant_size_mb([75.0, 142.0, 466.0]) == 142.0
    # A family of one quotes itself.
    assert fit.median_variant_size_mb([466.0]) == 466.0


@pytest.mark.parametrize("sizes", [[], [0.0], [0.0, 0.0], [-1.0]])
def test_median_variant_size_is_zero_when_no_variant_declares_a_size(sizes):
    """No declared size feeds ``fit_verdict``'s unknown branch, not a made-up number."""
    assert fit.median_variant_size_mb(sizes) == 0.0


def test_largest_that_fits_steps_down_to_the_biggest_non_red_variant():
    """The download panel offers this instead of a variant that cannot load."""
    budget = 1 * _GB

    assert fit.largest_that_fits(list(WHISPER_VARIANT_SIZES_MB), budget) == 466.0
    # A generous budget steps down to nothing — the largest variant is offered as-is.
    assert fit.largest_that_fits(list(WHISPER_VARIANT_SIZES_MB), 64 * _GB) == 2900.0


def test_largest_that_fits_returns_none_when_not_even_the_smallest_variant_fits():
    """Distinct from ``None``-for-unknown by the budget the caller passed in."""
    assert fit.largest_that_fits(list(WHISPER_VARIANT_SIZES_MB), 8 * _MB) is None


def test_largest_that_fits_returns_none_on_an_unmeasured_budget():
    """With no budget there is nothing to step down to; keep the user's own choice."""
    assert fit.largest_that_fits(list(WHISPER_VARIANT_SIZES_MB), None) is None


def test_family_key_splits_an_ollama_style_id_and_leaves_a_bare_name_alone():
    """A colonless name is its own family of one, whose median is the model itself."""
    assert fit.family_key("qwen3:8b") == "qwen3"
    assert fit.family_key("large-v3") == "large-v3"
    assert fit.family_key("") == ""


# ── disk_precheck: refuse with both numbers, or skip with a warning ─────────────


def test_disk_precheck_refuses_with_a_typed_reason_naming_both_numbers(monkeypatch):
    """A refusal the user cannot act on without a second lookup is a bad refusal."""
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(1 * _GB))

    result = fit.disk_precheck(10240.0, "/tmp/does-not-matter")

    assert result.ok is False
    assert result.measured is True
    assert result.need_bytes == int(10240 * _MB)
    assert result.free_bytes == 1 * _GB
    assert result.reason.startswith("insufficient_disk_space")
    assert "10.0" in result.reason  # the need, in GB
    assert "1.0" in result.reason  # the free space, in GB
    assert result.warning == ""


def test_disk_precheck_allows_a_download_that_comfortably_fits(monkeypatch):
    """The ordinary case carries no reason and no warning to render."""
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(500 * _GB))

    result = fit.disk_precheck(2900.0)

    assert result.ok is True
    assert result.measured is True
    assert result.reason == ""
    assert result.warning == ""


def test_disk_precheck_skips_with_a_warning_when_the_filesystem_cannot_be_measured(
    monkeypatch,
):
    """Blocking a good download because a probe failed is the worse error.

    ``ok=True`` here means "not refused", NOT "verified to fit" — the two are told apart by
    ``measured``, and the unmeasured one must carry a warning the surface can show. An
    ``ok=True, measured=False`` result with an empty warning is a silent unchecked download.
    """

    def _boom(_path):
        raise OSError("filesystem went away")

    monkeypatch.setattr(shutil, "disk_usage", _boom)

    result = fit.disk_precheck(10240.0, "/tmp/unmeasurable")

    assert result.ok is True
    assert result.measured is False
    assert result.warning != ""
    assert result.reason == ""
    assert result.free_bytes == 0
    # The need is still reported, so the surface can say what it could not verify.
    assert result.need_bytes == int(10240 * _MB)


def test_disk_precheck_of_an_unknown_size_is_allowed_rather_than_refused(monkeypatch):
    """A card with no size cannot be refused for space it never claimed to need."""
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(0))

    result = fit.disk_precheck(0.0)

    assert result.ok is True
    assert result.measured is True
    assert result.need_bytes == 0


# ── assess(): the seam callers actually use ─────────────────────────────────────


def test_assess_takes_a_given_host_and_reserve_without_probing_the_machine():
    """A caller that already has host facts must not trigger a second collection."""
    small = _host(ram_gb=SMALL_HOST_GB, unified=True, vram_gb=8.0)

    green = fit.assess(size_mb=142.0, host=small, reserve_gb=SMALL_HOST_RESERVE_GB)
    red = fit.assess(size_mb=2900.0, host=small, reserve_gb=SMALL_HOST_RESERVE_GB)

    assert (green.verdict, red.verdict) == ("green", "red")
    # The unified host's wrongly-populated VRAM did not reach the budget here either.
    assert green.budget_bytes == 1 * _GB


def test_assess_on_an_unmeasured_host_is_unknown_for_a_model_that_would_otherwise_fit():
    """The unknown answer survives the convenience wrapper."""
    assessment = fit.assess(size_mb=75.0, host=_host(ram_gb=16.0, measured=False), reserve_gb=3.0)

    assert assessment.verdict == "unknown"
    assert assessment.budget_bytes is None
