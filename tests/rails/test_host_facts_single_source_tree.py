"""The source-tree checks split from
``tests/test_host_facts_single_source.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from host_facts_single_source_shared import _SRC

#: The two strings that constitute GPU capacity DETECTION: the nvidia-smi VRAM-total query
#: and the macOS display probe. Utilisation/used-VRAM reads match neither.
_GPU_CAPACITY_MARKERS = ("memory.total", "SPDisplaysDataType")


#: How a memory TOTAL is read from this host. Both spellings belong to residency.
_MEMORY_TOTAL_MARKERS = ("hw.memsize", "MemTotal", "/proc/meminfo")


def _census(markers: tuple[str, ...]) -> dict[str, list[int]]:
    """``{relative path: [line numbers]}`` for every file under ``src/personalclaw``."""
    hits: dict[str, list[int]] = {}
    for path in source_index.package_files():
        lines = source_index.text(path).splitlines()
        found = [n for n, line in enumerate(lines, 1) if any(m in line for m in markers)]
        if found:
            hits[path.relative_to(_SRC).as_posix()] = found
    return hits


def test_gpu_capacity_detection_has_exactly_one_site() -> None:
    """VRAM total / GPU identity is detected in fit.py and nowhere else."""
    hits = _census(_GPU_CAPACITY_MARKERS)
    # Vacuity floor: if the probe were rewritten with different argv, this census would
    # match nothing and silently "pass" while every duplicate went unseen.
    assert hits, (
        "census found no GPU capacity probe at all — the markers "
        f"{_GPU_CAPACITY_MARKERS} went stale; re-derive them from fit._probe_gpu before "
        "trusting this test"
    )
    assert set(hits) == {"local_models/fit.py"}, (
        "GPU capacity (VRAM total, vendor, model) must be detected only by "
        f"fit._probe_gpu; also found in: {sorted(set(hits) - {'local_models/fit.py'})}"
    )


def test_handlers_system_reads_no_memory_total_itself() -> None:
    """The metrics handler routes memory through residency instead of probing again."""
    handler_hits = _census(_MEMORY_TOTAL_MARKERS).get("dashboard/handlers_system.py", [])
    # Vacuity floor: prove the markers still match the OWNER, otherwise "not in the
    # handler" is satisfied by a pattern that matches nothing anywhere.
    owner_hits = _census(_MEMORY_TOTAL_MARKERS).get("local_models/residency.py", [])
    assert owner_hits, (
        "census found no memory-total probe in residency.py — the markers "
        f"{_MEMORY_TOTAL_MARKERS} went stale and would no longer catch a duplicate"
    )
    assert not handler_hits, (
        "handlers_system.py must read memory through residency.memory_pressure (see "
        f"_memory_totals), not probe it; found at lines {handler_hits}"
    )
