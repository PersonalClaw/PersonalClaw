"""PHF-7: a deliberately raw call that skips the enforced helper REDS the gate.

The enforced helper is ``atomic_write`` (``src/personalclaw/atomic_write.py``) — the one
implementation that keeps the post-write history seam and fsync durability intact. A handler
that rolls its own ``mkstemp`` + ``os.replace`` lands the bytes on disk and fires no hook, so
the write is swallowed with a green test on BOTH sides (DAS-9). ``structural-duplication``'s
``durable-write`` family is what makes that shape expensive.

This file exists because that clause is a **vacuity assertion about the gate**, and the gate's
own suite cannot make it. ``tests/test_structural_baseline.py`` pins the *detector* against
synthetic trees (``gen._durable_write_sites(tree) == [...]``) — one level too shallow. A
detector can be perfect while the gate reds on nothing.

MEASURED, not argued. Replacing the ``("durable-write", _durable_write_sites)`` tuple in
``scan_duplicates``'s dispatch with ``lambda tree: []`` and then regenerating
``structural-baseline.json`` — which that file's own instructions invite whenever a counter
"legitimately shrank" — leaves the whole 31-test structural suite **GREEN** while the durable-write
tally sits at 0 and any raw write sails through. The same tree reds 2 of the 5 legs here, by name.
Without the regeneration the existing suite does catch it (stale-high + byte-match), so the hole
is narrow — and narrow is exactly how a gate dies quietly. A red alone is not evidence either:
this repo has measured a runner reporting identical failures WITH and WITHOUT a planted mutation.
Hence three legs, run through the SAME entry point:

* the clean tree must be GREEN (without this, the red below proves nothing);
* a planted RAW durable write must be RED, and the failure must NAME the planted site;
* a planted wrapper that DELEGATES to ``atomic_write`` must stay GREEN — otherwise leg two is
  satisfied by a gate that reds on any new file, which measures nothing about the helper.

The plant is spliced into the census WALK (``_src_py_files``), not into an inventory dict, so
every real layer runs: the walk, ``ast`` parse, the detector, the per-file census, the vacuity
checks and ``regressions_duplication``. ``scan()``'s memo key deliberately carries ``_parse``
and ``_src_py_files`` for exactly this reason, so patching the walk invalidates the cache
instead of serving a stale clean result. Nothing is written into ``src/`` and no real home is
touched: the planted module lives in ``tmp_path`` and only its *claimed* repo-relative name is
injected, via ``monkeypatch.setitem`` on the rel-path memo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_structural_baseline as gen

RATCHET = gen.RATCHET_DUPLICATION
FAMILY = "durable-write"
ENFORCED_HELPER = "src/personalclaw/atomic_write.py"

# A top-level name, so the plant cannot invent a sub-package: ``census_packages()`` walks disk
# independently of the patched walk, and a planted path claiming ``src/personalclaw/newpkg/x.py``
# would red the package-coverage vacuity check for the wrong reason.
PLANTED_REL = "src/personalclaw/_phf7_planted_durable_write.py"

RAW_DURABLE_WRITE = '''\
"""A raw durable write: temp file + rename, skipping the enforced helper."""

import os
import tempfile


def _raw_durable_write(path, data):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    os.write(fd, data)
    os.close(fd)
    os.replace(tmp, path)
'''

DELEGATING_WRAPPER = '''\
"""A wrapper that DELEGATES to the enforced helper — the shape we want."""

import json

from personalclaw.atomic_write import atomic_write


def _delegating_durable_write(path, data):
    atomic_write(path, json.dumps(data), fsync=True)
'''


def _committed() -> dict:
    path = gen.baseline_path()
    assert path.is_file(), (
        "structural-baseline.json is missing — generate it with "
        "`python scripts/generate_structural_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _gate_failures(monkeypatch: pytest.MonkeyPatch, planted: Path | None = None) -> list[str]:
    """Every failure line the ``structural-duplication`` gate reports, with *planted* (if
    given) spliced into the census walk under :data:`PLANTED_REL`.

    The walk is patched in BOTH directions — the clean leg swaps in a lambda returning the
    same list — so all three legs re-scan for real instead of one of them silently reading
    ``scan()``'s memo from a sibling test.
    """
    real = gen._src_py_files()
    files = list(real)
    if planted is not None:
        monkeypatch.setitem(gen._REL_CACHE, planted, PLANTED_REL)
        files.append(planted)
    monkeypatch.setattr(gen, "_src_py_files", lambda: files)
    return gen.ratchet_failures(RATCHET, _committed(), {RATCHET: gen._duplication_block()})


def _plant(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "_phf7_planted_durable_write.py"
    path.write_text(source, encoding="utf-8")
    return path


# ── The three legs ───────────────────────────────────────────────────────────


def test_the_clean_tree_passes_this_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The GREEN direction, and it comes first on purpose. A gate that reds on the clean tree
    too is a broken runner, not enforcement — and it would make the planted red below
    meaningless. This is the assertion that makes the next test evidence."""
    assert _gate_failures(monkeypatch) == [], (
        "the structural-duplication gate is ALREADY red on an unmodified tree, so a red under "
        "a planted raw write would say nothing about the plant. Fix the tree (or the baseline) "
        "before reading the falsification legs in this file."
    )


def test_a_raw_durable_write_reds_this_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clause itself. A function that pairs ``tempfile.mkstemp`` with ``os.replace`` — a
    durable write that never reaches ``atomic_write`` and so never fires the post-write hook —
    must fail the gate, and the failure must name the site so the next reader knows what to fix
    rather than which suite to re-run.

    This is also the "would deleting the caller be caught?" assertion. Remove
    ``durable-write`` from ``scan_duplicates``'s dispatch, or narrow the walk, and this leg
    reds while every detector-level test in ``test_structural_baseline.py`` stays green.
    """
    failures = _gate_failures(monkeypatch, _plant(tmp_path, RAW_DURABLE_WRITE))
    assert failures, (
        "a raw durable write (mkstemp + os.replace, no atomic_write) did NOT red the "
        f"{RATCHET} gate. The enforced helper is unenforced: the gate is measuring nothing, "
        "so every clean run of it is a false clear."
    )
    named = [line for line in failures if PLANTED_REL in line]
    assert named, (
        f"the gate red, but no failure line names {PLANTED_REL} — so it red for some other "
        f"reason and this leg is not measuring the plant. Lines: {failures}"
    )
    assert any(f"{FAMILY}:_raw_durable_write" in line for line in named), (
        "the failure names the planted FILE but not the planted SYMBOL, so it cannot tell a "
        f"contributor which call to fix. Lines: {named}"
    )


def test_a_delegating_wrapper_does_not_red_this_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRECISION, without which the leg above is worthless: a gate that reds on ANY new file
    would satisfy it while saying nothing about the helper. Wrapping ``atomic_write`` is the
    shape we WANT — three such wrappers already ship — so it must stay green."""
    failures = _gate_failures(monkeypatch, _plant(tmp_path, DELEGATING_WRAPPER))
    assert failures == [], (
        "a wrapper that DELEGATES to atomic_write red the gate. That inverts the rail: it now "
        f"punishes reuse of the enforced helper. Lines: {failures}"
    )


# ── What the gate is enforcing, asserted rather than assumed ──────────────────


def test_the_enforced_helper_is_the_gates_declared_canonical() -> None:
    """The two halves must name the same file. If the family's canonical drifted off
    ``atomic_write.py``, the legs above would still pass while enforcing a different helper —
    and the DAS-9 post-write seam, which is the entire reason this convention exists, would go
    unguarded."""
    families = {f.name: f for f in gen.DUPLICATE_FAMILIES}
    assert FAMILY in families, (
        f"the {FAMILY!r} duplicate family is gone from the census, so nothing enforces the "
        "durable-write convention any more."
    )
    assert families[FAMILY].canonical == ENFORCED_HELPER
    helper = gen._repo_root() / ENFORCED_HELPER
    assert helper.is_file(), f"{ENFORCED_HELPER} does not exist"
    assert "def atomic_write(" in helper.read_text(encoding="utf-8"), (
        f"{ENFORCED_HELPER} no longer defines atomic_write, so the canonical the gate points "
        "at is not the helper anyone calls."
    )


def test_the_gate_is_registered_and_the_family_scans_a_real_population() -> None:
    """A family declared in ``DUPLICATE_FAMILIES`` but never dispatched by ``scan_duplicates``
    is a documented rail that scans nothing, and it reports ZERO sites — indistinguishable from
    a clean tree. So the committed census must show the family counting a real population, and
    the ratchet must be one the gate dispatch actually runs."""
    assert RATCHET in gen.RATCHETS, f"{RATCHET} is not a registered structural ratchet"
    by_family = _committed()[RATCHET]["totals"]["by_family"]
    assert FAMILY in by_family, f"the committed census has no {FAMILY!r} tally"
    assert by_family[FAMILY] > 0, (
        f"the committed census counts 0 {FAMILY} sites. Either the re-derivations were all "
        "folded into atomic_write (then say so in the plan log and this floor can go), or the "
        "finder stopped matching — and a finder that matches nothing looks exactly like a "
        "clean tree."
    )
